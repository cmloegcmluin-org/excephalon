"""The window, as a local web app - Flask behind a loopback port, shown in its own desktop window.

The pages live in `templates/`, their look in `static/app.css`, their behaviour in the scripts
beside it; this module is the routes between them and the conversation. Nothing here draws: it
hands over entries that already know who said them, and takes back what was typed.

Why a browser engine rather than Tk: half of what a message thread wants - a box that hugs its
words, a hover that answers, a column beside the conversation - is a line of CSS and a fight with
a text widget. The measuring that Tk needed is simply gone.

There is no tab strip. What were tabs are pages with a bar above them: the conversation, the
profile's four sections down one page, the persona, what has been learned, and the agents.
"""

import re
from datetime import datetime
from pathlib import Path

from flask import Flask, redirect, render_template, request

from urllib.parse import quote

from excephalon.memory import (
    ENHANCEMENTS_HEADING,
    PROJECT_PREFIX,
    checklist_items,
    create_project,
    profile_sections,
    project_headings,
    project_title,
    rename_project,
    reorder_projects,
    save_checklist,
    save_learned,
    save_persona_additions,
    save_translations,
    split_filed,
    translation_pairs,
)
from excephalon.links import link_parts, offers, open_link
from excephalon.mirror import SIDES, TranscriptModel, sessions
from excephalon.transcript import SELF
from excephalon.tailing import LogTail, archive_dir, discover, safe_name
from excephalon.vocabulary import translations_in_force

# The role keys are the transcript's own line format and never change; the NAMES are what the
# window shows, and the window shows Excephalon now.
SPEAKERS = {"you": "You", SELF: "Excephalon", "heads-up": "Excephalon · heads-up"}

# The profile's own categories - each names only the stem of its heading, because a profile
# glosses its headings however it likes ("Enhancements they want for you (roadmap, not now)").
# Context reads last ("Context should be moved below Enhancements, Goals, and Projects") and as
# plain bullets: it is standing background, not a list of things to do, so boxes and an open
# count would miscount it as work. `subtitle` is the one-line explanation under a Config card's
# title; the Projects tab's cards carry none - an empty subtitle draws no note - so the Excephalon
# card there reads exactly like every other project card ("remove any special styling from the
# Excephalon card - it should look the same as the other project cards").

# The Projects list is gone from Config too, by his call: "remove the Projects list from Config
# entirely, and on the Projects tab create an individual card for each of those items." So every
# long-term item is its own project card on the Projects tab now, and Config is left with the one
# thing that was never a list of work - the life context it should hold in the background.
SECTIONS = (
    ("Context", "Life context", "bullets",
     "Background it should always hold about your life - facts, not tasks."),
)


def _said(entry, label="", day="", speakers=SPEAKERS):
    """One entry as the page needs it: who said it, when, and whether it is a message at all.

    `label` is a session's name, which the break itself does not carry - it is worked out from
    the day above it and the first thing said inside it. The break shows exactly what the row in
    the contents shows, so the two read as the same thing rather than as a rule and some dots.

    `day` is the date of the break above this entry. A message keeps only the time on screen, but
    it carries two dated pointers: `reference`, the readable "You · 2026-07-18 05:01:59" he copies
    to paste back at Excephalon, and `moment`, the bare "date time" the link button encodes into a
    URL's #at= hash to reopen the conversation at this turn. A bare "05:01:59" could be any day, so
    both carry the date. A break is a place, not a moment, so it carries neither."""
    bubble = entry["role"] in SIDES
    name = speakers.get(entry["role"], "")
    dated = f"{day} {entry['stamp']}".strip()  # "date time", or just the time before the first break
    return {
        "role": entry["role"],
        "name": name,
        "stamp": entry["stamp"],
        "text": entry["text"],
        "label": label,
        "historical": entry["historical"],
        "bubble": bubble,
        "side": SIDES.get(entry["role"], ""),
        "reference": f"{name} · {dated}" if bubble else "",
        "moment": dated if bubble else "",
        # What in it can be opened, worked out here so the page only draws it. Space-aware, so a
        # path with a folder like "Field Notes" in it is one link, not one broken one.
        "parts": link_parts(entry["text"]) if entry["role"] in SIDES else [],
    }


def _thread(entries, since, speakers=SPEAKERS):
    """A stretch of conversation as the page draws it, from `since` on."""
    found = sessions(entries)
    # By position, never by value: every session break is the same dict as every other, so
    # looking one up by equality sent all of them to the first one in the thread.
    named = {at: label for label, at in found}
    # The date in force at each position, from the whole thread - so a message's dated reference is
    # right even when the poll starts past the day break that dates it.
    day_at, day = [], ""
    for entry in entries:
        if entry["role"] == "day":
            day = entry["stamp"]
        day_at.append(day)
    return {
        "entries": [_said(entry, named.get(since + offset, ""), day_at[since + offset], speakers)
                    for offset, entry in enumerate(entries[since:])],
        "at": since,
        "total": len(entries),
        "sessions": [{"label": label, "at": at} for label, at in found],
    }


def _with_filed(item):
    """A checklist item split into the words he edits and the filing moment shown beside them as a
    link. The stamp stays out of the editable words - the page carries it in `data-filed` and puts
    it back on save (see writing.js), so the file keeps the exact "(filed …)" it always did."""
    words, filed = split_filed(item["text"])
    return {**item, "text": words, "filed": filed}


def _heading(found, stem):
    """Which of the profile's own headings this section means. Matched on the stem, because a
    profile glosses its headings however it likes ("Enhancements they want for you (roadmap, not
    now)")."""
    return next((head for head in found if head.lower().startswith(stem.lower())), None)


def _card(found, title, heading, kind, subtitle):
    """One card as its page draws it: the section's items split into what is still open and what is
    done, so the done ones fold away. Shared by Config and the Projects tab, which draw the same
    card - a project's is just an ordinary checklist section tagged "Project: <name>"."""
    items = [_with_filed(item) for item in checklist_items(found.get(heading, ""))]
    return {"title": title, "heading": heading, "kind": kind, "subtitle": subtitle,
            "active": [item for item in items if not item["done"]],
            "done": [item for item in items if item["done"]]}


# The bullet marker and nothing else: stripping "-* " as a character set ate the leading `**`
# of the name itself, so every instruction lost its bold on the way to the page.
_BULLET = re.compile(r"^\s*[-*+]\s+")
_NAMED = re.compile(r"^\*\*(?P<lede>[^*]+)\*\*\s*(?P<rest>.*)$", re.S)


def _named(line):
    """One instruction as a name and the rule under it - {"lede": ..., "text": ...}.

    "modify each Instruction so that it begins with a bolded name - 3 words tops": the name is
    stored in the file as markdown, and shown as the bold word it is rather than as asterisks he
    has to read past. A line with no name is all rule, and shows exactly as it always did."""
    found = _NAMED.match(line)
    return {"lede": found["lede"].strip(), "text": found["rest"].strip()} if found else {"text": line}


def _started(log):
    """The day this agent's log was opened, read off its own first date header.

    The day the agent was STARTED, not the day it stopped - "the date should be when the agent was
    started, not when it finished" - which is the first line the desk ever wrote to it. A log with
    no header falls back to the file's own last write, the only other date there is."""
    from excephalon.transcript import day_of

    try:
        with open(log, "r", encoding="utf-8", errors="replace") as lines:
            for line in lines:
                day = day_of(line.rstrip())
                if day is not None:
                    return datetime.strptime(day, "%Y-%m-%d")
                break  # the header is the first line the desk writes, or there is none
    except OSError:
        return None
    try:
        return datetime.fromtimestamp(log.stat().st_mtime)
    except OSError:
        return None


class Agents:
    """Every agent's log, read back as the exchange it is rather than as lines.

    An agent's thread is the same shape as the conversation, with the speakers swapped: the
    Excephalon is the one asking, and the agent answers."""

    def __init__(self, directory, clock):
        self._directory = Path(directory) if directory else None
        # The fleet's one archive, shared with the desk's own wrap-up (see tailing.archive_dir), so
        # a log closed here and one the desk retires land in the same place.
        self._archive = archive_dir(self._directory) if self._directory else None
        self._clock = clock
        self._read = {}  # name -> (LogTail, TranscriptModel)

    def names(self):
        return sorted(discover(self._directory)) if self._directory else []

    def live_names(self):
        """Every open tab, newest first, with the day its agent was STARTED - (name, when)."""
        return self._dated(self._directory)

    def archived_names(self):
        """Every retired agent's log, reopenable, newest first and dated - (name, when).

        Alphabetical, an archive reads as a filing cabinet: "archived agent logs should be sorted
        by date, not alphabetically, jesus... and show the timestamp for them too"."""
        return self._dated(self._archive)

    def _dated(self, folder):
        """The logs in `folder` as (name, day started), newest first.

        The day the agent was STARTED, not the day it stopped - "the date should be when the agent
        was started, not when it finished" - which is the first line the desk ever wrote to it, and
        every log opens with its own date header. A log with no header falls back to the file's
        own last write, which is the only other date there is."""
        if not folder:
            return []
        dated = []
        for name in discover(folder):
            log = folder / f"{name}.log"
            dated.append((name, _started(log)))
        dated.sort(key=lambda entry: (entry[1] is not None, entry[1]), reverse=True)
        # The day, not the minute: it buys room for the names beside it, and a run is remembered
        # by the day it happened on.
        return [(name, when.strftime("%Y-%m-%d") if when else "") for name, when in dated]

    def rename_live(self, name, to):
        """Rename a tab whose log is all there is - a leftover from an agent the desk let go.

        "I can edit the name, but the changes don't persist; they simply get silently rejected":
        the desk owns the agents it is holding, and it rightly refused a name it had never heard
        of. But the window draws a tab per LOG file, so a log outliving its agent is still a tab
        he is looking at, and its name is still his to change."""
        return self._move(self._directory, name, to)

    def rename_archived(self, name, to):
        """An archived exchange under the name he gives it. The live ones are the desk's to
        rename (it holds the session too); this is the same move for a log with no agent left."""
        return self._move(self._archive, name, to)

    def _move(self, folder, name, to):
        """One log renamed inside its folder - (new name, "") - or ("", why it could not be).

        The refusal comes back as a sentence because a rename that quietly puts the old name back
        is indistinguishable from a broken app: "the changes are back to failing to persist"."""
        wanted = safe_name(to)
        if not wanted or folder is None:
            return "", "that leaves nothing a file can be named"
        log = folder / f"{name}.log"
        if not log.exists():
            return "", "there is no log under that name any more"
        if wanted == name:
            return "", "that is the name it already has"
        # Windows keeps one file under one name in ANY case, so a name differing from this log's
        # only in case IS this log: "inbox-AUTO-play-toggle" -> "inbox-auto-play-toggle" read as a
        # collision with itself and was refused, and fixing the capitals an all-caps heading had
        # led him to type was exactly the rename he tried. The move itself changes the case fine.
        if (folder / f"{wanted}.log").exists() and wanted.lower() != name.lower():
            return "", "another log is already called that"
        self._read.pop(name, None)
        self._read.pop(("archived", name), None)
        log.replace(folder / f"{wanted}.log")
        return wanted, ""

    def restore(self, name):
        """Bring an archived log back: moved into the live folder, it IS a tab again - the
        roster is the folder. The one road back out of the archive, and it is the same road in,
        reversed, so a log can shuttle between the lists without ever being copied or lost."""
        log = self._archive / f"{name}.log" if self._archive is not None else None
        if log is not None and log.exists() and self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)
            log.replace(self._directory / log.name)

    def close(self, name):
        """Take an agent away and archive its log, so it stays closed. Moving the log aside is
        what makes that stick - the roster is the folder, so a log still in it comes straight
        back on the next poll."""
        self._read.pop(name, None)
        log = self._directory / f"{name}.log" if self._directory else None
        if log is not None and log.exists() and self._archive is not None:
            self._archive.mkdir(parents=True, exist_ok=True)
            log.replace(self._archive / log.name)

    def entries(self, name):
        if name not in self._read:
            self._read[name] = (LogTail(self._directory / f"{name}.log"),
                                TranscriptModel(clock=self._clock))
        tail, model = self._read[name]
        for line in tail.poll().splitlines():
            model.apply("log", line)
        return model.entries


def create_app(model, *, on_submit, on_stop=None, on_mic=None, on_auto_listen=None,
               opener=open_link, mirror=None,
               profile_path=None, learned_path=None, translations_path=None, terms=(),
               persona_additions_path=None, agent_logs_dir=None, clock=None,
               on_quit=None, on_restart=None, upgrade_ready=None, on_translations_saved=None,
               scanned_terms=(), lexicon_reader=None, on_lexicon_saved=None, on_rename=None):
    """`model` is the conversation to show. `mirror` is what fills it from the feed, when there
    is a live session behind it - without one the model is whatever was put in it.

    `terms` is the vocabulary transcription is biased toward, as it stood when the session opened -
    shown, not used, so they can see what it is snapping their words to. `on_quit` and
    `on_restart` drive the window this app is shown in: the page's own close dialog and the
    Restart button land here."""
    app = Flask(__name__)
    profile_path = Path(profile_path) if profile_path else None
    learned_path = Path(learned_path) if learned_path else None
    translations_path = Path(translations_path) if translations_path else None
    persona_additions_path = Path(persona_additions_path) if persona_additions_path else None
    agents = Agents(agent_logs_dir, clock)

    def _profile_raw():
        if profile_path is None or not profile_path.exists():
            return ""
        return profile_path.read_text(encoding="utf-8")

    def _profile_text():
        return profile_sections(_profile_raw())

    @app.get("/")
    def window():
        return render_template("window.html", here="/")

    @app.get("/messages")
    def messages():
        """What the page has not drawn yet, and where each session starts.

        `since` is how much it already holds, so a poll four times a second carries a few bytes
        rather than every session ever recorded. The contents list is small and its numbering
        shifts as the conversation grows, so that goes whole each time."""
        if mirror is not None:
            mirror.drain()  # the page's poll is the pump; nothing runs while nothing is looking
        entries = model.entries
        since = min(request.args.get("since", 0, type=int), len(entries))
        retract, typed, send = mirror.dictated() if mirror is not None else (0, [], False)
        return _thread(entries, since) | {
            "state": mirror.state if mirror is not None else "waking",
            "level": mirror.level if mirror is not None else 0.0,
            "hearing": mirror.hearing if mirror is not None else "",
            "retract": retract,
            "dictated": typed,
            "send": send,
        }

    @app.post("/submit")
    def submit():
        on_submit(request.form["text"])
        return ("", 204)

    @app.post("/mic")
    def mic():
        if on_mic is not None:
            on_mic(request.form["recording"] == "true")
        return ("", 204)

    @app.post("/stop")
    def stop():
        if on_stop is not None:
            on_stop()
        return ("", 204)

    @app.post("/auto-listen")
    def auto_listen():
        """Whether the mic re-arms itself after each reply, rather than being pressed each time."""
        if on_auto_listen is not None:
            on_auto_listen(request.form["on"] == "true")
        return ("", 204)

    @app.post("/open")
    def open_what_was_clicked():
        """Open something a message named - an address in the browser, a path on this machine.

        Only what this same server offered as a link: the page can ask for anything, and a POST
        that opens whatever string it is handed is a way to run things by talking to the port."""
        target = request.form["target"]
        if not offers(target):  # the same rule that offered it, so a spaced path still opens
            return ("", 400)
        opener(target)
        return ("", 204)

    # ---- the one page that was five tabs ------------------------------------------------------

    @app.get("/config")
    def config():
        """Everything he tunes, down one page with a contents column beside it: the profile's four
        checklists, what Excephalon has learned, its standing instructions, the words it swaps, the
        vocabulary it snaps to, and the credit line it warns from. These were five tabs
        ("'Profile' doesn't make sense as a title to me. please change it to 'Config', and
        consolidate in all the contents"), and the composed-persona dump is deliberately not
        carried over - it duplicated, worse, what this page already shows.

        Each checklist is split into what is still open and what is done: the done ones fold into
        a collapsible section at its foot, so what he still has to act on is what he sees."""
        found = _profile_text()
        sections = [_card(found, title, heading, kind, subtitle)
                    for title, heading, kind, subtitle in
                    ((title, _heading(found, stem), kind, subtitle)
                     for title, stem, kind, subtitle in SECTIONS)
                    if heading is not None]
        own = translation_pairs(_own_translations())
        stock = translations_in_force({})
        in_force = translations_in_force(own)
        learned = learned_path.read_text(encoding="utf-8") if (
            learned_path is not None and learned_path.exists()) else ""
        # The file's own "# Learned..." heading is bookkeeping, not a memory - and shown, it read
        # as one. The rows are the memories; the heading is re-derived on save.
        memories = [line.lstrip("-* ").strip() for line in learned.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        lexicon_now = sorted(set(lexicon_reader() if lexicon_reader is not None else ()),
                             key=str.lower)
        return render_template(
            "config.html", here="/config", sections=sections,
            # Each row remembers what the stock rule for its "heard" says, so a save can tell an
            # override worth writing from a built-in left exactly as it was - without the page
            # ever LABELLING which is which ("it doesn't matter whether a translation is
            # 'built-in' or not; don't display that").
            # One list, sorted by the RIGHT side: "for a given destination word, all the
            # existing translations into it" at a glance - nobody rallies around a mishearing.
            # The (circasonant) rows are the old Vocabulary card: the live lexicon plus what
            # the project folders contribute.
            swaps=sorted(
                [{"heard": heard, "said": in_force[heard], "stock": stock.get(heard, "")}
                 for heard in in_force]
                + [{"heard": "(circasonant)", "said": term, "stock": ""}
                   for term in set(scanned_terms) | set(lexicon_now)],
                key=lambda swap: (swap["said"].casefold(), swap["heard"].casefold())),
            memories=memories,
            instructions=[_named(_BULLET.sub("", line).strip())
                          for line in _persona_additions().splitlines() if line.strip()],
        )

    # The tabs this page replaced still answer, so a window standing open across the update lands
    # here rather than on a 404.
    for old in ("/profile", "/persona", "/memory", "/translations"):
        app.add_url_rule(old, f"was_{old.strip('/')}", lambda: redirect("/config"))

    # ---- the Projects tab: a card per project -------------------------------------------------

    @app.get("/projects")
    def projects():
        """A card for each of his projects. #128: "the Projects section should become its own tab,
        with a card for each project (RTT app, Highdeas, etc.) and this Enhancements section just
        becomes the Project card for Excephalon itself." Excephalon leads - its roadmap IS the
        Enhancements section, so nothing about the brain's filing or ticking moves - and each app
        follows as its own "## Project: <name>" checklist, read and saved by the same machinery."""
        text = _profile_raw()
        found = profile_sections(text)
        cards = []
        enhancements = _heading(found, ENHANCEMENTS_HEADING)
        if enhancements is not None:
            # Excephalon is not a "## Project:" section - it is the Enhancements roadmap - so its
            # rail entry neither renames (that would move the heading the brain files into) nor
            # drags: it always leads. `movable` is what tells the two apart on the page. It carries
            # no subtitle, so its card reads exactly like every other project card.
            cards.append({**_card(found, "Excephalon", enhancements, "checklist", ""),
                          "movable": False})
        cards += [{**_card(found, project_title(heading), heading, "checklist", ""), "movable": True}
                  for heading in project_headings(text)]
        return render_template("projects.html", here="/projects", sections=cards)

    @app.post("/project/new")
    def new_project():
        """The + button starts a new card already in edit mode - there is no name field to fill.
        A placeholder project is created and the tab comes back with `?editing=<name>`, which the
        page uses to focus that card's name for typing over. He names it by renaming it (below), so
        his project names still reach only his profile, never the source."""
        if profile_path is None:
            return redirect("/projects")
        taken = set(project_headings(_profile_raw()))
        name, nth = "New project", 2
        while PROJECT_PREFIX + name in taken:  # a second + is a second card, not the same one again
            name, nth = f"New project {nth}", nth + 1
        create_project(name, path=profile_path)
        return redirect("/projects?editing=" + quote(name))

    @app.post("/project/rename")
    def rename_project_route():
        """Rename a project card from the sidebar, the way an agent is renamed. A name another card
        already has is refused with a reason to show where he typed it - a quiet restore of the old
        name reads as a broken app."""
        if profile_path is not None:
            try:
                moved = rename_project(request.form["from"], request.form["to"], path=profile_path)
            except ValueError:
                return {"why": "a project needs a name"}, 400
            if moved is None:
                return {"why": "another project is already called that"}, 409
        return ("", 204)

    @app.post("/project/reorder")
    def reorder_projects_route():
        """Save the order the sidebar was dragged into - the sequence the cards stand in the file is
        the sequence the tab draws them."""
        if profile_path is not None:
            reorder_projects(request.get_json()["order"], path=profile_path)
        return ("", 204)

    @app.post("/profile")
    def write_profile():
        """Save one section's list back, as the markdown the file keeps and the brain reads -
        never as the boxes drawn from it.

        `drawn` is what the page believes the file holds, so an enhancement Excephalon filed into the
        same section while the window sat open is carried over rather than overwritten by the next
        character they type."""
        if profile_path is not None:
            sent = request.get_json()
            # The lists he refers to items of by number carry ids, so those number a new row on the
            # way in: the Excephalon roadmap (the Enhancements section) and every project card (a
            # "Project: <name>" section is the same checklist). The other panes stay plain lists.
            head = sent["heading"].lower()
            numbered = (head.startswith(ENHANCEMENTS_HEADING.lower())
                        or head.startswith(PROJECT_PREFIX.lower()))
            # A bullets section (Life context) writes plain bullets back: it is background, not
            # work, and boxes in the file would draw boxes on the page again.
            plain = any(sent["heading"].lower().startswith(stem.lower())
                        for _, stem, kind, _ in SECTIONS if kind == "bullets")
            save_checklist(profile_path, sent["heading"], sent["items"], drawn=sent["drawn"],
                           number=numbered, boxes=not plain)
            # The numbering mutates the sent items in place, so this is each row's id in the order
            # the page sent them - what lets a new row show its number the moment it first saves.
            return {"ids": [item.get("id") for item in sent["items"]]}
        return ("", 204)

    def _persona_additions():
        if persona_additions_path is None or not persona_additions_path.exists():
            return ""
        return persona_additions_path.read_text(encoding="utf-8")

    @app.post("/persona")
    def write_persona():
        if persona_additions_path is not None:
            save_persona_additions(request.form["body"], persona_additions_path)
        return ("", 204)

    def _own_translations():
        if translations_path is None or not translations_path.exists():
            return ""
        return translations_path.read_text(encoding="utf-8")

    @app.post("/translations")
    def write_translations():
        if translations_path is not None:
            save_translations(request.form["body"], translations_path)
            if on_translations_saved is not None:
                # In force NOW: the running ear swaps to the saved rules for the very next chunk.
                on_translations_saved(translation_pairs(request.form["body"]))
        return ("", 204)

    @app.post("/lexicon")
    def write_lexicon():
        """The (circasonant) rows written back: additions join his lexicon, removals leave it, and
        the folder-scanned terms pass through untouched - a folder's name is not this page's to
        delete. In force immediately, like every other row on the card."""
        if on_lexicon_saved is not None:
            kept = [line.strip() for line in request.form["terms"].splitlines() if line.strip()]
            on_lexicon_saved(kept)
        return ("", 204)

    @app.post("/memory")
    def write_memory():
        if learned_path is not None:
            save_learned(request.form["body"], learned_path)
        return ("", 204)

    # ---- the window itself --------------------------------------------------------------------

    @app.post("/quit")
    def quit_window():
        """The page's own close dialog said Close - the one way the window actually goes."""
        if on_quit is not None:
            on_quit()
        return ("", 204)

    @app.get("/upgrade")
    def upgrade():
        """Whether a restart has anything to restart INTO: the checkout on disk has moved past
        the commit this process booted from. The bar's Restart button shows only then."""
        return {"ready": bool(upgrade_ready()) if upgrade_ready is not None else False}

    @app.post("/restart")
    def restart():
        """The Restart button: wind this process down and bring up a fresh one on the current
        code - how a landed fix reaches the app without him hunting down the .bat."""
        if on_restart is not None:
            on_restart()
        return ("", 204)

    @app.get("/agents")
    def show_agents():
        return render_template("agents.html", here="/agents", names=agents.live_names(),
                               archived=agents.archived_names())

    @app.post("/agents/archived/<name>/restore")
    def restore_agent(name):
        # An archived log is not read in place - restoring moves it back to the live folder,
        # where it is an ordinary tab again. The static /archived/ segment outranks the live
        # route's converter, so the two namespaces never collide.
        if name not in [kept for kept, _ in agents.archived_names()]:  # only a name from the archive
            return ("", 404)
        agents.restore(name)
        return ("", 204)

    @app.get("/agents/<name>")
    def agent_thread(name):
        if name not in agents.names():  # never read a path that did not come from the log folder
            return ({"entries": [], "at": 0, "total": 0, "sessions": []}, 404)
        # In an agent's thread Excephalon is the one asking and the agent answers.
        return _thread(agents.entries(name), request.args.get("since", 0, type=int),
                       {"you": "Excephalon", SELF: name, "heads-up": "Excephalon · heads-up"})

    @app.post("/agents/<name>/rename")
    def rename_agent(name):
        """His name for a running agent. The desk owns the live ones - it holds the session, the
        log and the record - so the app hands the ask over rather than moving files behind it."""
        wanted = request.form.get("to", "")
        if name not in agents.names():
            return ("", 404)
        # The desk first: it holds the session, the record and the news, so a live agent is its
        # rename to make. A tab whose agent it no longer holds is a log and nothing else, and
        # moving that log is the whole job - which is why the ask never fails silently now.
        renamed = on_rename(name, wanted) if on_rename is not None else ""
        if renamed:
            return ({"name": renamed}, 200)
        renamed, why = agents.rename_live(name, wanted)
        return ({"name": renamed}, 200) if renamed else ({"why": why}, 409)

    @app.post("/agents/archived/<name>/rename")
    def rename_archived_agent(name):
        if name not in [kept for kept, _ in agents.archived_names()]:
            return ("", 404)
        renamed, why = agents.rename_archived(name, request.form.get("to", ""))
        return ({"name": renamed}, 200) if renamed else ({"why": why}, 409)

    @app.post("/agents/<name>/close")
    def close_agent(name):
        if name not in agents.names():  # never touch a path that did not come from the log folder
            return ("", 404)
        agents.close(name)
        return ("", 204)

    return app
