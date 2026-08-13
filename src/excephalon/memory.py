"""Excephalon's memory of its user.

Three layers, all under the gitignored `runtime/` dir (private):
- `profile.md`  - the hand-written standing profile (goals, projects, life context). Its title
                  line is also what Excephalon calls its user - see `user_name`.
- `learned.md`  - facts Excephalon captured itself from past conversations.
- `lexicon.md`  - the user's working vocabulary: names they coined (Notecraft, WaveShaper) AND the
                  domain terms and proper nouns of the fields they work in (Bayesian inference,
                  the people they collaborate with) - one word or several. Triple duty: it is part
                  of the brain's standing context so it knows their words, transcription here is
                  biased toward the same list (see `vocabulary`), and another tool that
                  transcribes the same person can correct against it too - so teaching a term once
                  fixes all three. That last duty is why the file may live outside this repo
                  entirely; `lexicon_path` is how it says where.

All are folded into the brain's system prompt at startup, so it knows the user without being
re-told. At the end of a session the brain is asked what new, durable facts came up; those get
appended to `learned.md`, so next time it remembers them too - the auto-capture-and-remember loop.
"""

import re
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
DEFAULT_PROFILE_PATH = _RUNTIME / "profile.md"
DEFAULT_LEARNED_PATH = _RUNTIME / "learned.md"
DEFAULT_LEXICON_PATH = _RUNTIME / "lexicon.md"
# One line naming the lexicon file, when it isn't the one above - see `lexicon_path`.
LEXICON_POINTER = _RUNTIME / "lexicon-path.txt"
# Their own "heard -> said" list. Beside the lexicon rather than inside it: a lexicon entry is a real
# word to get right, a translation has a WRONG side, and the lexicon is shared with whatever else
# transcribes them - a syntax it doesn't know would read as a term they never uses.
DEFAULT_TRANSLATIONS_PATH = _RUNTIME / "translations.md"
# Excephalon's OWN standing instructions - how it has been told to behave, accreted over time by the
# user and by Excephalon itself when it is told to work differently from now on. This is the editable
# overlay layered on top of the shipped `DEFAULT_PERSONA` base (which stays in the source, nameless);
# it is NOT where `DEFAULT_PERSONA` lives. Gitignored like the rest of runtime, because how a
# companion behaves for one person is personal.
DEFAULT_PERSONA_ADDITIONS_PATH = _RUNTIME / "persona.md"

# `{user}` is filled in from the profile's own title line by `compose_persona` - see `user_name`.
USER_PLACEHOLDER = "{user}"

_PREAMBLE = (
    "Here is standing context about {user}'s life, for your awareness only. Do NOT raise any of "
    "it unprompted, and do not turn into a therapist or life-coach about it. "
    "Use it only to be more useful and less clueless when they bring something up themselves:"
)

_ADDITIONS_INTRO = (
    "More standing instructions on how to be, added since - by {user}, and by you yourself when "
    "they tell you to work differently from now on. Treat these as part of your core persona, every "
    "bit as binding as the rules above:"
)

_LEXICON_INTRO = (
    "This is {user}'s working vocabulary - not only names they coined, but the domain terms, proper "
    "nouns and terms of art of the fields they work in (their projects, the subjects they study, the "
    "people they work with). Recognize them when they use them, and get them "
    "right when you use them back - their speech-to-text is biased toward this same list. Don't force "
    "them into the conversation:"
)

# A gloss can follow the term after " - " / " — " / ": "; the term itself is the head of the line.
_GLOSS = re.compile(r"\s+[—–-]\s+|:\s+")

# What separates the two sides of a translation. "->" is what they wrote it as; "→" is what a page
# showing it back to them renders, and either has to read back in.
_ARROW = re.compile(r"\s*(?:->|→)\s*")

CONSOLIDATION_PROMPT = (
    "Our conversation is ending. List, as short bullet points (each starting with '-'), any NEW and "
    "durable facts about the user that came up and are worth remembering in future sessions - "
    "decisions, preferences, life updates, commitments - and that you didn't already know about them. "
    "Only things that will still matter later. If there is nothing new worth saving, reply with "
    "exactly: none"
)


ANONYMOUS_USER = "the user"


def user_name(profile, default=ANONYMOUS_USER):
    """What to call the person Excephalon is for, taken from the title line of their own profile
    ("# Ada - standing profile" -> "Ada"), with any gloss after the title dropped.

    The name belongs to the user, so it is read from the user's file rather than written into the
    source. A checkout with no profile yet still has to compose sentences, hence the neutral
    default: every persona line reads the same whether it says a name or "the user"."""
    for line in profile.splitlines():
        if line.startswith("# "):
            return _GLOSS.split(line[2:].strip(), maxsplit=1)[0].strip() or default
    return default


def load_profile(path=DEFAULT_PROFILE_PATH):
    return _read(path)


def load_learned(path=DEFAULT_LEARNED_PATH):
    return _read(path)


def lexicon_path(pointer=LEXICON_POINTER, default=DEFAULT_LEXICON_PATH):
    """Which file the lexicon is. Beside the rest of the runtime state, unless `lexicon-path.txt`
    names somewhere else.

    The point of the indirection is that this list is worth sharing: whatever else transcribes
    this user - a note-taker, a memo app, another machine - wants the same terms, and a term
    taught once should fix all of them. That shared copy lives wherever the tool that syncs it
    keeps it, which is nowhere this repo can guess, so the user writes the path down instead."""
    try:
        target = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return default
    return Path(target).expanduser() if target else default


def load_lexicon(path=None):
    return _read(lexicon_path() if path is None else path)


def load_persona_additions(path=DEFAULT_PERSONA_ADDITIONS_PATH):
    """Excephalon's own accreted standing instructions, or "" when none have been added yet."""
    return _read(path)


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def lexicon_terms(text):
    """The bare terms from a lexicon file, for biasing transcription - the head of each line (one
    word or a whole phrase), with any gloss, bullet, blank line or '#' comment stripped off."""
    terms = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line[0] in "-*•":
            line = line[1:].strip()
        term = _GLOSS.split(line, maxsplit=1)[0].strip()
        if term:
            terms.append(term)
    return terms


def load_translations(path=DEFAULT_TRANSLATIONS_PATH):
    return _read(path)


def translation_pairs(text):
    """Their own list of what it keeps mishearing: {what came back: what they said}.

    One per line, with the arrow they wrote in their own ticket - "cloud agent" -> "Claude agent". A
    line without one is not a translation and is left out rather than guessed at; the left side is
    lowercased because that is the side it gets looked up by."""
    pairs = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line[0] in "-*•":
            line = line[1:].strip()
        sides = _ARROW.split(line, maxsplit=1)
        if len(sides) != 2:
            continue
        heard, said = (side.strip() for side in sides)
        if heard and said:
            pairs[heard.lower()] = said
    return pairs


def save_translations(text, path=DEFAULT_TRANSLATIONS_PATH):
    """Write their list back. Their file, their wording - stored exactly as typed, so what they read next
    time is what they wrote rather than a normalised version of it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def compose_persona(base_persona, profile, learned="", lexicon="", additions=""):
    """Fold the user's standing context into the brain's system prompt: Excephalon's own accreted
    standing instructions right after the base rules (they are how-to-behave, like the base), then
    life context (profile + learned) under a do-not-play-therapist warning, then their lexicon under
    its own recognize-these framing.

    This is also where the persona learns whose companion it is: every `{user}` in the assembled
    text becomes the name from the profile. Substituting here rather than at each template keeps
    one place that can leave a placeholder showing - and the window renders this exact text."""
    life = "\n\n".join(section.strip() for section in (profile, learned) if section.strip())
    sections = [base_persona]
    if additions.strip():
        sections.append(f"{_ADDITIONS_INTRO}\n\n{additions.strip()}")
    if life:
        sections.append(f"{_PREAMBLE}\n\n{life}")
    if lexicon.strip():
        sections.append(f"{_LEXICON_INTRO}\n\n{lexicon.strip()}")
    return "\n\n".join(sections).replace(USER_PLACEHOLDER, user_name(profile))


def parse_facts(text):
    """Pull bullet-point facts out of the brain's end-of-session reply ('none' -> nothing)."""
    if text.strip().lower().rstrip(".") == "none":
        return []
    facts = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped[:1] in "-*•":
            fact = stripped[1:].strip()
            if fact:
                facts.append(fact)
    return facts


def save_learned(text, path=DEFAULT_LEARNED_PATH):
    """Write the user's edits to what Excephalon has learned. It is a memory OF them and it is
    theirs; when they cross something out it should stay crossed out."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def forget_learned(fact, path=DEFAULT_LEARNED_PATH):
    """Drop the remembered line closest to `fact` - the memory inbox's delete. Matched on folded
    words, containment either way, so the brain's paraphrase of a memory still lands on the line
    he meant. False when nothing matches; the caller owes him that plainly, never a silent no."""
    fold = lambda text: " ".join(text.casefold().split())
    wanted = fold(fact)
    if not wanted:
        return False
    lines = _read(path).splitlines()
    for at, line in enumerate(lines):
        said = fold(line.lstrip("-* ").strip())
        if said and (wanted in said or said in wanted):
            del lines[at]
            Path(path).write_text("\n".join(lines).strip() + ("\n" if any(lines) else ""),
                                  encoding="utf-8")
            return True
    return False


def reconcile_lexicon(kept_terms, scanned, path=None):
    """The Config page's paraphone rows, written back to the lexicon: terms he added join it,
    lexicon terms he removed from the page leave it, and everything else - glosses, the intro,
    terms that came from folder scans rather than this file - is left exactly as it stands."""
    where = Path(path) if path else lexicon_path()
    kept = {term.casefold() for term in kept_terms if term.strip()}
    scanned_fold = {term.casefold() for term in scanned}
    existing = _read(where)
    known = set()
    lines = []
    for line in existing.splitlines():
        match = _BULLET.match(line)
        if match and match.group("item").strip():
            term = _GLOSS.split(match.group("item").strip(), maxsplit=1)[0].strip()
            known.add(term.casefold())
            if term.casefold() not in kept:
                continue  # removed on the page; removed here
        lines.append(line)
    fresh = [term for term in kept_terms
             if term.strip() and term.casefold() not in known
             and term.casefold() not in scanned_fold]
    body = "\n".join(lines).rstrip()
    if fresh:
        body = (body + "\n" if body else "") + "\n".join(f"- {term.strip()}" for term in fresh)
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(body + "\n" if body else "", encoding="utf-8")


def append_learned(facts, path=DEFAULT_LEARNED_PATH):
    if not facts:
        return
    path = Path(path)
    existing = _read(path).rstrip() or "# Learned in past sessions"
    body = existing + "\n" + "\n".join(f"- {fact}" for fact in facts) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def save_persona_additions(text, path=DEFAULT_PERSONA_ADDITIONS_PATH):
    """Write Excephalon's standing instructions back. Its persona is theirs to shape, so what they
    cross out stays out and what they type is stored exactly as typed - the same contract as their
    learned facts and their translations."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def append_persona_addition(instruction, path=DEFAULT_PERSONA_ADDITIONS_PATH):
    """Add one standing instruction, the way Excephalon files one when it is told to change how it
    behaves from now on. Cumulative and bulleted - the mirror of `append_learned`, for how to be
    rather than for facts about the user - so a whole persona can grow one instruction at a time."""
    path = Path(path)
    existing = _read(path).rstrip()
    body = (existing + "\n" if existing else "") + f"- {instruction.strip()}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def profile_sections(text):
    """The profile split by its "## " headings - what the window's Goals/Projects/Enhancements tabs
    render. {heading: body}; text before the first heading is dropped (it's the file's preamble)."""
    sections = {}
    heading = None
    lines = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections[heading] = "\n".join(lines).strip()
            heading = line[3:].strip()
            lines = []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        sections[heading] = "\n".join(lines).strip()
    return sections


# A project is an ordinary checklist section whose heading opens with this tag - "## Project: RTT
# app". Nothing about it is special to the checklist machinery: it is numbered, filed into, ticked
# and saved exactly as the Enhancements list is. The tag is only how the Projects tab tells one of
# his projects apart from his life context or how-to-work-with-him, and it never reaches his eyes -
# the card shows the name after the tag. His project NAMES stay out of the source this way: the tag
# is the app's, the names are his, read from the profile at runtime.
PROJECT_PREFIX = "Project: "


def project_headings(text):
    """The profile's project sections, in file order - each "## Project: <name>" heading by its
    full heading text, ready to hand straight to the checklist readers and writers."""
    return [heading for heading in profile_sections(text) if heading.startswith(PROJECT_PREFIX)]


def project_title(heading):
    """The name a project's card shows - its heading with the "Project: " tag taken off. An
    untagged heading passes through unchanged, so the Excephalon card (drawn from the Enhancements
    section) can share this path."""
    return heading[len(PROJECT_PREFIX):] if heading.startswith(PROJECT_PREFIX) else heading


def _heading_blocks(lines):
    """The file split into blocks: the preamble first, then one block per "## heading" (its heading
    line and everything under it, up to the next heading). Rejoining the blocks in any order is a
    faithful rewrite of the file - which is what lets the cards be reordered without disturbing a
    line of anything else."""
    blocks, current = [], []
    for line in lines:
        if line.startswith("## "):
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    blocks.append(current)
    return blocks


def _block_heading(block):
    return block[0][3:].strip() if block and block[0].startswith("## ") else None


def reorder_projects(order, path=DEFAULT_PROFILE_PATH):
    """Rewrite the project cards in a new order - the sequence the tab draws them in is the order
    their "## Project:" sections stand in the file. Everything that is not a project keeps its place;
    the projects are re-laid where the first of them was. A name the order forgot is never dropped:
    it follows, in its old order, so a partial order can only move cards, never lose one."""
    path = Path(path)
    blocks = _heading_blocks(_read(path).splitlines())
    projects = {_block_heading(b): b for b in blocks
                if (_block_heading(b) or "").startswith(PROJECT_PREFIX)}
    wanted = [PROJECT_PREFIX + str(name) for name in order]
    new_order = ([head for head in wanted if head in projects]
                 + [head for head in projects if head not in wanted])
    out, placed = [], False
    for block in blocks:
        if (_block_heading(block) or "").startswith(PROJECT_PREFIX):
            if not placed:
                for head in new_order:
                    out.extend(projects[head])
                placed = True
        else:
            out.extend(block)
    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")


def rename_project(old, new, path=DEFAULT_PROFILE_PATH):
    """Rename a project's card - move its "## Project: <old>" heading to "## Project: <new>", its
    list riding along. Returns the new heading, or None when it cannot be made: no project by that
    old name, or the new name is already another project's (two cards must never share a heading, so
    the caller can say so where he typed it rather than silently restoring the old name). A blank
    new name is refused outright - a heading is one line with words on it."""
    new = " ".join(str(new).split())
    if not new:
        raise ValueError("a project needs a name")
    path = Path(path)
    sections = profile_sections(_read(path))
    old_heading, new_heading = PROJECT_PREFIX + old, PROJECT_PREFIX + new
    if old_heading not in sections:
        return None
    if new_heading == old_heading:
        return new_heading  # a no-op rename still succeeds
    if new_heading in sections:
        return None
    lines = _read(path).splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## ") and line[3:].strip() == old_heading:
            lines[index] = f"## {new_heading}"
            break
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return new_heading


def create_project(name, path=DEFAULT_PROFILE_PATH):
    """Start a new, empty project - "## Project: <name>" - and return its heading, so a card for
    it appears with a row to type into. A name that already names a project is left exactly as it
    is, its checklist intact, and its heading comes back unchanged: adding one twice must never
    wipe the list it already holds. A blank name is refused - a heading is one line with words on
    it."""
    name = " ".join(str(name).split())  # a heading is a single line; fold any stray whitespace
    if not name:
        raise ValueError("a project needs a name")
    heading = PROJECT_PREFIX + name
    path = Path(path)
    if heading in profile_sections(_read(path)):
        return heading
    save_section(path, heading, "")
    return heading


# Only the stem of the heading: a profile writes its own, and they run on ("Enhancements you want
# (roadmap, not now)"). Every reader matches on the stem - see `find_heading`.
ENHANCEMENTS_HEADING = "Enhancements"

# The enhancements list is a CHECKLIST: an item that gets done is ticked, never removed. "As you
# check items off from the enhancements list, I don't want them deleted forever." A struck-through
# item is also the only record that a complaint was heard and acted on - deleting it loses both the
# ask and the answer, and the same thing then gets filed again (five separate tickets in that list
# are one bug, refiled because nothing ever showed it had been dealt with).
#
# The list predates the boxes, so a plain "- item" is read as an unticked one and upgraded the first
# time anything writes it back. That migrates the file by use rather than by rewriting, under them,
# a personal file the running app may be autosaving at the same moment.
# A stable `#id` may ride between the box and the words - "add IDs to all of the enhancements so I
# can refer to them by ID". It is `#` and digits only, so a line whose words merely open with a `#`
# (a hashtag, "#3 of 5") is not mistaken for a numbered one.
_BULLET = re.compile(r"^(\s*)[-*]\s+(?:\[(?P<tick>[ xX])\]\s+)?(?:#(?P<id>\d+)\s+)?(?P<item>.*)$")
UNTICKED, TICKED = "- [ ] ", "- [x] "


def checklist_items(body):
    """A section's lines as the things the window ticks: whether each is done, and what it says.

    ANY line with words on it is an item - "the enhancements tab should simply assume that any
    newline is a checklist item" - because they type them in plain, and boxing only the ones already
    punctuated as bullets left their own additions sitting outside the list they were meant to join.
    A blank line is not an item; it is the gap between two."""
    items = []
    for line in body.splitlines():
        match = _BULLET.match(line)
        text = (match.group("item") if match else line).strip()
        if text:
            has_id = match is not None and match.group("id") is not None
            items.append({"done": match is not None and (match.group("tick") or " ") != " ",
                          "text": text,
                          "id": int(match.group("id")) if has_id else None})
    return items


def checklist_markdown(items, *, boxes=True):
    """The items back as markdown - the form the file keeps and the brain reads.

    Not quite the inverse of `checklist_items`: a bullet written before the boxes existed comes
    back as `- [ ]`, so the list upgrades itself the first time they touch it rather than needing a
    migration run over a personal file the app may be autosaving at that moment.

    A row with nothing typed into it yet is not an item. Enter is how every item is made, so the
    empty row is the normal state of the one they are about to fill in; storing it would leave a
    bullet with nothing after it sitting in their profile.

    A row holding several lines - a block pasted into one of them - becomes the items it reads as.
    Stored whole it would be one bullet with newlines inside it, and those lines come back as
    items that have lost their place in the list. Its `#id`, if it has one, stays on the first of
    those lines: the rest are fresh items and are numbered on the next pass, never handed a copy of
    an id already in use.

    `boxes=False` writes plain bullets instead: Life context is background, not work, and a box
    beside a life fact miscounts it as something left to do."""
    out = []
    for item in items:
        box = (TICKED if item["done"] else UNTICKED) if boxes else "- "
        tag = f"#{item['id']} " if item.get("id") is not None else ""
        for line in _lines(item["text"]):
            out.append(box + tag + line)
            tag = ""
    return "\n".join(out)


def _lines(text):
    """The lines a row is stored as: one per non-empty line, stripped. A block pasted into one row
    is kept as the several lines it reads as, so it is split the same way wherever the file's form
    of an item is what matters - writing it, and telling one already stored from a fresh edit."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _stored_lines(texts):
    return {line for text in texts for line in _lines(text)}


def find_heading(sections, stem):
    """Which of the profile's own headings this stem means, or the stem itself if it has none yet.

    The profile is hand-written, so its headings carry whatever gloss their author wanted. Matching
    a whole heading line would miss the section that is plainly right there - and a filing that
    misses doesn't fail, it starts a rival section beside the real one."""
    lowered = stem.lower()
    return next((h for h in sections if h.lower().startswith(lowered)), stem)


def _next_id(items):
    return max((item["id"] for item in items if item.get("id") is not None), default=0) + 1


def _assign_ids(items):
    """Give every item without one the next id after the highest in use, in order. Stable: an item
    that already has an id keeps it, so a number he has been told stays pointing at the same task."""
    next_id = _next_id(items)
    for item in items:
        if item.get("id") is None:
            item["id"] = next_id
            next_id += 1
    return items


def number_enhancements(path=DEFAULT_PROFILE_PATH, heading=ENHANCEMENTS_HEADING):
    """Ensure every enhancement carries a stable `#id` he can refer to it by. Idempotent, and it
    writes only when something was actually unnumbered - so it can run each time the page is opened
    without churning a personal file the running app may be autosaving at the same moment."""
    path = Path(path)
    resolved = find_heading(profile_sections(_read(path)), heading)
    items = checklist_items(profile_sections(_read(path)).get(resolved, ""))
    if all(item["id"] is not None for item in items):
        return
    save_section(path, resolved, checklist_markdown(_assign_ids(items)))


def save_checklist(path, heading, items, *, drawn, number=False, boxes=True):
    """Write one section's list back, as the markdown the file keeps and the brain reads.

    `drawn` is what the page believes the file holds - the words of every item it was drawn with,
    or last sent. Anything the section has gained since is carried over rather than overwritten:
    Excephalon files enhancements into this same list while the window sits open, and every keystroke
    writes the whole list back, so without this the next character they type deletes them.

    An item is told from a fresh one two ways. By `#id` when it has one (`number`): the same id is
    the same item, so editing it in place cannot fork it even when the page has lost track of what
    it last sent. And by its STORED lines otherwise: the file keeps one line per item, so a block
    pasted into a single row is stored split into its lines, and comparing the combined text the row
    still holds against those split lines would file every line a second time - one of the ways the
    same task piled up here in half-finished copies. Splitting both sides the way the file stores
    them also absorbs the `- x` -> `- [ ] x` upgrade, since the words are unchanged by it.

    With `number`, brand-new rows (no id yet) are handed the next number as they are written.

    The heading is resolved by STEM before anything is read or written, exactly as every reader
    resolves it (`find_heading`): the profile's own headings carry gloss ("Projects (long-term)"),
    and a write that matched exactly while the read matched by stem forked the file - the page
    read the glossed section, saved to the bare stem, and a rival "## Projects" appeared at the
    bottom holding the edit while the card kept showing the section it had always read."""
    resolved = find_heading(profile_sections(_read(path)), heading)
    seen = _stored_lines(item["text"] for item in items) | _stored_lines(drawn)
    sent_ids = {item["id"] for item in items if item.get("id") is not None}
    gained = [item for item in checklist_items(profile_sections(_read(path)).get(resolved, ""))
              if (item["id"] is None or item["id"] not in sent_ids) and item["text"] not in seen]
    merged = items + gained
    if number:
        _assign_ids(merged)
    save_section(path, resolved, checklist_markdown(merged, boxes=boxes))


def _same_ask(one, another):
    """The same words, give or take casing, spacing, and a filing stamp: the same ask."""
    strip = lambda text: re.sub(r"\s*\(filed [^)]*\)\s*$", "", text)
    fold = lambda text: " ".join(strip(text).casefold().split())
    return fold(one) == fold(another)


# A filed item carries "(filed 2026-07-28 02:23)" - the moment it was filed. The page shows that
# as a link to that point in the conversation, not as text in what he edits, so it is split off
# here. Narrower than the dedup strip above on purpose: only a real date-and-time is a moment to
# point at, so an older free-text note ("(filed 2026-07-27 by Claude directly...)") stays part of
# the words rather than becoming a dead link.
_FILED_STAMP = re.compile(r"\s*\(filed (\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)\)\s*$")


def split_filed(text):
    """An enhancement item as (the words he edits, the filing moment or None)."""
    match = _FILED_STAMP.search(text)
    if not match:
        return text, None
    return text[:match.start()].rstrip(), match.group(1)


def append_enhancement(item, path=DEFAULT_PROFILE_PATH, heading=ENHANCEMENTS_HEADING, *, stamp=None):
    """File one enhancement bullet INSIDE its section, so the window's tab (which re-reads this
    file) shows it the moment it lands - not at the end of the file under some other heading.

    `stamp` dates the filing in place - "include timestamps... so context is preserved even if we
    don't get to addressing them for many weeks". And the same words already OPEN on the list are
    the same ask, so they are not filed twice (False comes back instead): one evening's drive
    stacked the auto-listen bug and the grammar layer two copies each. A TICKED copy never blocks
    a refiling - the complaint coming back means it regressed, and that is news worth a fresh line.
    """
    path = Path(path)
    text = _read(path)
    heading = find_heading(profile_sections(text), heading)
    standing = checklist_items(profile_sections(text).get(heading, ""))
    if any(not existing["done"] and _same_ask(existing["text"], item) for existing in standing):
        return False
    if stamp:
        item = f"{item} (filed {stamp})"
    lines = text.splitlines()
    insert_at = None
    inside = False
    for index, line in enumerate(lines):
        if line.startswith("## "):
            if inside:  # the section ended - the bullet goes just before this next heading
                insert_at = index
                break
            inside = line[3:].strip() == heading
    if inside and insert_at is None:  # the section runs to the end of the file
        insert_at = len(lines)
    if insert_at is None:  # no such section yet - start it at the end
        lines += ["", f"## {heading}"]
        insert_at = len(lines)
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1  # tuck the bullet against the section's last line, not after its blank gap
    # With its number, from the moment it lands. Filed bare, the item had no #id until the page
    # happened to save the section - so the one he had just been told about was the one he could
    # not name back ("when Excephalon files an Enhancement ticket itself, it still has the bug
    # where the ID is missing from it initially").
    item = f"#{_next_id(standing)} {item}"
    lines.insert(insert_at, UNTICKED + item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def open_enhancements(path=DEFAULT_PROFILE_PATH, heading=ENHANCEMENTS_HEADING):
    """The still-open Enhancements as plain "#id words" lines - the live rendering the loop puts
    in front of the brain every turn. The boot-time persona already carries the whole profile, and
    the brain both let that copy go stale and flatly disbelieved it ("I can't see the Enhancements
    list - I have no tool to read your local files"). What rides in the per-turn notes has never
    faded or been denied; this is the list, there, fresh from the file each turn."""
    path = Path(path)
    resolved = find_heading(profile_sections(_read(path)), heading)
    items = checklist_items(profile_sections(_read(path)).get(resolved, ""))
    return "\n".join(
        (f"#{item['id']} " if item.get("id") is not None else "") + item["text"]
        for item in items if not item["done"]
    )


def revise_enhancement(item_id, text, path=DEFAULT_PROFILE_PATH, heading=ENHANCEMENTS_HEADING):
    """Rewrite the words of enhancement #id in place, keeping its number and its tick. False when
    no item carries that id. "Excephalon needs the ability to edit existing enhancement items after
    filing them, not just create new ones" - and the #id is how he names one."""
    path = Path(path)
    resolved = find_heading(profile_sections(_read(path)), heading)
    items = checklist_items(profile_sections(_read(path)).get(resolved, ""))
    for item in items:
        if item.get("id") == int(item_id):
            item["text"] = str(text).strip()
            save_section(path, resolved, checklist_markdown(items))
            return True
    return False


def complete_enhancement_by_id(item_id, path=DEFAULT_PROFILE_PATH, heading=ENHANCEMENTS_HEADING):
    """Tick enhancement #id done, keeping its number and its words exactly as they were. False
    when no item carries that id. The brain could file and rewrite but not FINISH - asked to
    check items off, it wrote a literal "[x]" into their words instead ("No you idiot... I'm
    saying to check them off!"). Done is a state, not a spelling; this flips the state."""
    path = Path(path)
    resolved = find_heading(profile_sections(_read(path)), heading)
    items = checklist_items(profile_sections(_read(path)).get(resolved, ""))
    for item in items:
        if item.get("id") == int(item_id):
            item["done"] = True
            save_section(path, resolved, checklist_markdown(items))
            return True
    return False


def complete_enhancement(item, path=DEFAULT_PROFILE_PATH, heading=ENHANCEMENTS_HEADING):
    """Tick the enhancement whose text contains `item`, in place. True if one was found.

    Matched loosely and on the first hit only, because the caller is quoting a fragment of a line
    the user wrote in their own words and at their own length. Reporting a miss matters: a tick that
    silently lands nowhere reads as done and isn't."""
    path = Path(path)
    text = _read(path)
    heading = find_heading(profile_sections(text), heading)
    wanted = item.strip().lower()
    lines = text.splitlines()
    inside = False
    for index, line in enumerate(lines):
        if line.startswith("## "):
            if inside:
                break  # the section ended; an item further down the file is not this list's
            inside = line[3:].strip() == heading
            continue
        match = _BULLET.match(line) if inside else None
        if match is None or (match.group("tick") or " ") != " ":
            continue  # not a bullet, or already ticked
        if wanted in match.group("item").strip().lower():
            tag = f"#{match.group('id')} " if match.group("id") else ""
            lines[index] = TICKED + tag + match.group("item").strip()
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def save_section(path, heading, body):
    """Replace one "## heading" section's body, leaving every other line of the file untouched.

    This is their own profile - the same file the brain loads as standing context - so an edit made
    in the window has to be surgical: rewriting the whole file from parsed sections would quietly
    drop the preamble and reflow everything they didn't touch.
    """
    path = Path(path)
    lines = _read(path).splitlines()
    start = end = None
    for index, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        if start is None and line[3:].strip() == heading:
            start = index + 1
        elif start is not None:
            end = index
            break
    body_lines = body.rstrip().splitlines()
    if start is None:  # no such section yet - start one at the end
        lines += ["", f"## {heading}"] + body_lines
    else:
        tail = lines[end:] if end is not None else []
        lines = lines[:start] + body_lines + ([""] if tail else []) + tail
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
