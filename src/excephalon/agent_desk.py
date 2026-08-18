"""The agents Excephalon has started, and can still talk to.

Excephalon used to fire agents off and lose them: it spawned them as detached background tasks,
kept only an id, and then couldn't reach them again - four in a row went unreachable, and its own
context resets stranded the rest. It also drove them from inside a conversational turn, so while
an agent worked, the user was talking to a wall.

The desk fixes both. Each agent is a persistent session held HERE, in the process, so the handle
can't be lost to a context reset - a follow-up goes to the same agent, which remembers. And every
message is sent on a worker thread: starting an agent, or sending it one, returns at once, and
whatever the agent says back is pushed to the Outbox for the conversation to deliver at its next
natural moment. The conversation never waits on an agent again.

The roster is written to a file as agents come and go, so the brain - which can read files but
can't reach into this process - can always see who it has running. And every exchange goes into a
timestamped per-agent log the desk writes itself (log_dir/<name>.log) - the file the user tails to
watch a conversation happen, which used to be hand-authored by the brain in whatever format it
invented that day, with no timestamps.
"""

import json
import re
import threading
import time
from pathlib import Path

from excephalon.delivery import LADDER, Delivery, DeliveryError
from excephalon.memory import PROJECT_PREFIX
from excephalon.models import DEFAULT_EFFORT, DEFAULT_MODEL, describe
from excephalon.naming import unique_name
from excephalon.relay import notice
from excephalon.steps import SAID, render
from excephalon.tailing import archive_dir, safe_name
from excephalon.transcript import AGENT_DID, AGENT_SAID, ENTITY_SAID, Transcript


# Attached to every task the desk hands out, because asking for it each time did not hold: the
# brain wrote it into some dispatches and not others, and the round it forgot cost a whole review -
# work shown off a stale branch reads as though features that had already merged were missing. The
# second rule earned its place the same way: "verification" came back as "run pytest", and the
# user had to say - again - that green tests are not their eyes.
STANDING_RULE = (
    "\n\nStanding rules, from the person this work is for, and they hold however the task above "
    "is worded. One: before you present ANY branch, build or running instance for them to look "
    "at, first `git fetch origin` and rebase your branch onto the latest `origin/main`, then "
    "re-run the tests on the rebased commit - shown off a stale branch, work that other people "
    "have already merged looks to them like it has gone missing. Two: when your work is done, "
    "'ready for review' means THEY can SEE it working with their own eyes and mouse - stand up a "
    "live instance of the app on its own port with its own scratch data, apart from their real "
    "one, and report the exact click-by-click steps to watch the new behavior happen. Never "
    "offer 'run the tests' as their verification: green tests are your evidence, not theirs, "
    "and they will send it back. Presenting is where you STOP: do NOT push, open a PR, enqueue, "
    "or merge - a live instance for their eyes is not a landed change. Wait. The user's sign-off "
    "reaches you in so many words; only then do you land it, and until then the desk itself "
    "refuses the push, the PR and the merge. Three: engineering discipline is not optional - "
    "read the repo's CLAUDE.md before you start and follow it; test-drive every change (one "
    "failing test, the minimum code to pass it, refactor, again); run the project's full test "
    "suite green before anything lands; once they have signed off, land through the repo's own "
    "process - a repo with a PR merge queue means push a branch, open a PR, enqueue it, and watch "
    "it to actually merged, never a direct push to a shared main - and leave everything you "
    "touched cleaner than you found it."
)


def _one_line(text, limit=160):
    """A task or a last word as one digest-sized line: its first line, capped."""
    line = str(text).strip().splitlines()[0] if str(text).strip() else ""
    return line if len(line) <= limit else line[:limit].rstrip() + "…"


def _ago(seconds):
    """An age as a person says one - the briefing reads better as "16 minutes ago" than as an
    epoch nobody can subtract in their head."""
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} minutes ago"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} hours ago"
    return f"{round(seconds / 86400)} days ago"


# Words that carry no identity when names are matched loosely: shared by half the fleet, they
# would make "the agent" match everything.
_NAME_NOISE = frozenset(("the", "and", "for", "its", "with", "that", "this", "agent", "one"))


def _name_tokens(name):
    return {piece for piece in re.split(r"[^a-z0-9]+", str(name).lower())
            if len(piece) >= 3 and piece not in _NAME_NOISE}


# Commands that ship work OUT to the shared remote - the point past which it is no longer just this
# agent's private branch. An agent must not run one until the user has SEEN the work and approved
# it. This is the code half of the review loop: the persona is only ASKED to wait for a verdict,
# and a persona that forgets landed unreviewed PRs anyway (frozen-tabs and enhancements-tab-fixes
# both merged with no verdict on file). git fetch/rebase and running the app to present it are NOT
# here - they stay local, so presenting never trips the gate.
_LANDING_COMMAND = re.compile(r"\bgit\s+push\b|\bgh\s+pr\s+(?:create|merge|ready)\b", re.IGNORECASE)

LANDING_NOT_APPROVED = (
    "Landing is blocked: the user has not approved this work yet, so pushing, opening a PR, "
    "enqueuing, or merging is refused. Present it for their EYES first - stand up a live instance "
    "and give the click-by-click steps - then stop and wait. When they sign off you will be told "
    "to land it in so many words, and only then will these commands run."
)


def landing_block_reason(stage, tool_name, tool_input):
    """Why a tool call must be refused, or None to allow it - the ONLY refusal the desk makes on
    its own. A command that ships work to the shared remote (push, open a PR, enqueue, merge) is
    denied unless the work's delivery has reached "landing", i.e. an approved verdict is on record.
    Everything else passes: reads, edits, local git, running the app - the user runs these agents
    unattended by choice. The reason is returned as text so a denied landing teaches the flow."""
    if stage == "landing":
        return None  # the user signed off; the mechanical landing is exactly what should run now
    if tool_name != "Bash":
        return None
    command = str((tool_input or {}).get("command", ""))
    return LANDING_NOT_APPROVED if _LANDING_COMMAND.search(command) else None


def newest_session_for(cwd, store=None):
    """The id of the newest CLI session that ever ran in `cwd`, or None.

    The fleet record is the desk's memory of which session is which agent - but the CLI keeps
    its own store, one folder per working directory, a .jsonl per session. When the record
    loses an id (see `revive`), the newest session in the agent's own worktree folder IS that
    agent: nothing else ever runs there. `store` overrides the store's location for tests."""
    if not cwd:
        return None
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    folder = (Path(store) if store is not None else Path.home() / ".claude" / "projects") / slug
    try:
        sessions = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return sessions[-1].stem if sessions else None


# What a restarted Excephalon says to an agent it found recorded mid-task. The resumed session
# remembers everything, so the message is a nudge, not a re-briefing.
CONTINUE_AFTER_RESTART = (
    "Excephalon restarted while you were mid-task. Your session was resumed, so everything you knew "
    "still holds. Pick up exactly where you left off and finish; if you had in fact finished, "
    "report where things stand."
)

# How many silent restarts a crashing agent gets before its deaths become the user's business.
# One crash is infrastructure - a server error, a dropped connection - and he has ruled on it:
# told an agent had "hit a server error and died - should I restart it?", he answered "Yes, of
# course... your job is to insulate me from this kind of Pointless delay." But a THIRD death in
# a row is not weather, it is a task that keeps killing its agent, and silently feeding it new
# sessions forever would hide work that is genuinely stuck.
DEATH_LIMIT = 2

RESUME_AFTER_CRASH = (
    "Your previous session crashed mid-task and has been resumed. Pick up exactly where you "
    "left off and finish; if you had in fact finished, report where things stand."
)

# A revived agent recorded mid-landing owes exactly one thing: the outcome. Left "idle" in
# peace, one sat on a merge that had landed within a minute while its tab stayed open all day.
RESUME_LANDING = (
    "Excephalon restarted while you were landing approved work. Check the PR's real state right now "
    "- gh pr view --json state,mergedAt - and make this reply the outcome: merged, or exactly "
    "what stands in the way. Still queued? Watch it in the foreground of this same turn until "
    "it lands or fails; never hand the watch to a background task and end your turn."
)

# A revived agent recorded idle with building work never presented is a black hole: nothing
# re-engages an idle agent, so its task - however far it got - sat invisible for hours while
# the user was told the agent "finished" with nothing to show. One died four minutes into a
# feature when the app closed, came back idle, and the user only learned the work was stranded
# by asking after it that night. Presenting is the one duty such an agent still owes.
PRESENT_AFTER_RESTART = (
    "Excephalon restarted and found you idle on work that was never presented for the user's eyes. "
    "Say plainly where the task stands: if the work is ready, present it now with the exact "
    "steps for the user to see it running; if it is unfinished, pick it back up and finish, "
    "then present; if nothing of it survives, say that outright so it can be restarted."
)

# Sent by the desk itself the moment a verdict is recorded - after the user has spoken, what
# remains is mechanical, and mechanical steps are not left to anyone's memory. The watch is
# commanded in the FOREGROUND because a landing agent once handed it to a background task and
# ended its turn: the merge landed one minute later, nothing re-engages an idle agent when a
# background task finishes, and the "it merged" the user was owed never existed - its tab sat
# open as a ghost all day.
APPROVED_LAND_IT = (
    "The user looked at what you presented and signed off. Land it now: push your branch, open "
    "the PR, enqueue it on the merge queue, and watch it in the FOREGROUND of this same turn - "
    "one command that polls until the PR is merged or fails, however long that takes. Never hand "
    "the watch to a background task and end your turn: nothing re-engages you when a background "
    "watcher fires, and the merge report is the one thing still owed. Your reply is the outcome: "
    "it merged, or exactly what stopped it."
)
# His sign-off, carrying one change to fold in on the way. A separate tell-then-land lost the
# sign-off half: "ship it with that small tweak" went to the agent as words alone, no verdict
# reached the record, the landing gate refused the push, and he was asked to approve AGAIN work
# he had approved in no uncertain terms.
APPROVED_WITH_CHANGE = (
    "The user looked at what you presented and signed off, with one change to fold in first: "
    "{feedback}\nMake that change, run the full suite green on it, and then land it now - no "
    "second presentation: push your branch, open the PR, enqueue it on the merge queue, and "
    "watch it in the FOREGROUND of this same turn, one command that polls until the PR is merged "
    "or fails. Never hand the watch to a background task and end your turn. Your reply is the "
    "outcome: it merged, or exactly what stopped it."
)
REJECTED_TRY_AGAIN = (
    "The user looked at what you presented and rejected it: {feedback}\n"
    "Address their feedback and present again when it is ready for their eyes."
)


class _Desked:
    """One agent and what it's doing, so the roster can say more than just a name."""

    def __init__(self, agent, cwd, task, log, *, model, effort, delivery=None, enhancement=None,
                 project=None, item_id=None, landed=False, ticked=False):
        self.agent = agent
        self.cwd = cwd
        self.task = task
        self.log = log  # the timestamped exchange log the user can tail, or None
        self.model = model  # what it was started on, so a revival can put it back on the same
        self.effort = effort
        self.delivery = delivery or Delivery()  # where this work stands in the review loop
        # The list item this agent is here to complete, verbatim, or None. Carried from the start
        # rather than matched from the task later, so the tick lands on exactly the line the user
        # wrote - a wrong tick would corrupt the list's record of ask and answer. `project` names
        # the Projects-tab card the item lives on ("Highdeas"); None means his Enhancements card -
        # the three tasks a whole afternoon delivered were left unticked on their cards because
        # only the Enhancements card could ever be ticked.
        self.enhancement = enhancement
        self.project = project
        # The item's NUMBER, when it could be resolved at start. A number does not drift the way
        # the brain's retyping of his sentence does, and the tick goes by it first.
        self.item_id = item_id
        # Whether GIT has confirmed this agent's branch actually reached origin/main. Set only
        # where the desk asks git (`_carry`), never from anybody's claim - "landing" is an order
        # and this is the outcome, and a wrap-up that trusted the order raced the very thread
        # carrying it: an agent was recorded DELIVERED two seconds after his "ship it", its item
        # ticked off his list, while its work sat unpushed on his machine.
        self.landed = landed
        # Whether its list item has been checked off. The tick belongs to the MERGE, not to the
        # tab closing: it used to be a step inside the wrap-up, so every other door that ends an
        # agent - the window's own close button, a wrap-up refused, a process that went down
        # first - left his ask standing open with the work already shipped. Recorded so the doors
        # that come later cannot tick it twice either.
        self.ticked = ticked
        self.state = "starting"
        self.last_heard = None  # when it last said anything at all, step or reply
        self.last_word = None  # the last thing it said back, trimmed for the roster
        # Crashes in a row on the CURRENT effort, reset by any clean turn. Counted so the desk
        # can restart a crashed session silently a bounded number of times - the user has ruled
        # that deaths are machinery, not news - without a task that keeps killing its agents
        # churning new sessions forever.
        self.deaths = 0
        # The session id this agent was REVIVED on. A freshly resumed agent reports no id of its
        # own until it first answers, and persisting that None over the recorded id orphaned a
        # whole fleet: the next restart found nulls, skipped every revival, and the user was told
        # there was "no trace" of agents he had watched work all afternoon.
        self.recorded_session = None


class AgentDesk:
    def __init__(self, outbox, *, agent_factory=None, roster_path=None, log_dir=None,
                 monitor=None, clock=time.strftime, events=None, run=None, state_path=None,
                 law_path=None, complete_enhancement=None, tick_by_id=None, tick_anywhere=None,
                 resolve_item=None, wrapped_path=None, now=time.time):
        from excephalon.worktrees import run_hidden

        self._run = run or run_hidden  # how retire removes a finished agent's worktree
        # How a finished agent's Enhancements-list item gets ticked off (memory.complete_enhancement),
        # or None to skip it. Injected so the desk needn't know where the profile lives, and so a
        # desk with no profile behind it (most tests) simply does not tick.
        self._complete_enhancement = complete_enhancement
        # The same tick, by the item's NUMBER (memory.complete_enhancement_by_id) and by number
        # on whichever card holds it (complete_enhancement_anywhere). A number is what survives
        # the brain's retyping of his sentence, which is what the text match depended on.
        self._tick_by_id = tick_by_id
        self._tick_anywhere = tick_anywhere
        # What turns an item's WORDS into its number (memory.enhancement_id). It lives here, at
        # the one place every agent start passes through, rather than at each door: resolved in
        # the brain's tool alone, an agent started by a Projects-tab robot click carried no
        # number at all, and the delivered work left its item open exactly as before.
        self._resolve_item = resolve_item
        # What happened - finished, died - goes to the events sink as (kind, agent, report); the
        # narrator words it in the brain's own voice. Undirected, the desk speaks the old way:
        # a capped notice (or the death line) straight to the outbox.
        self._events = events or self._plain_notices
        self._outbox = outbox
        self._factory = agent_factory or _real_agent
        self._roster_path = Path(roster_path) if roster_path else None
        self._state_path = Path(state_path) if state_path else None  # the fleet's survival record
        self._law_path = Path(law_path) if law_path else None  # the machine-wide engineering law
        self._log_dir = Path(log_dir) if log_dir else None
        # Where a finished agent's log goes to rest - the fleet's one archive (see tailing).
        self._archive_dir = archive_dir(self._log_dir) if self._log_dir else None
        # The durable record of how each wrapped agent ENDED - delivered, or died. The archive
        # alone keeps only names, and a briefing of bare names left "delivered" a fact nobody
        # held: the brain re-offered review of work that had landed and been wrapped up minutes
        # earlier ("The robot icon UI work is done now; ready for you to look at").
        self._wrapped_path = Path(wrapped_path) if wrapped_path else None
        self._now = now
        # Who is actually alive. Silence used to be measured off the agent-inbox FILENAMES, which
        # know nothing about agents: a note Excephalon wrote itself became an "agent" that then went
        # quiet, and a working agent that hadn't written to its inbox looked dead. Both were
        # reported to the user as fact, and both were denied on the spot by someone reading the log.
        self._monitor = monitor
        # Which model their agents run on, and how hard they are told to think. Held HERE because
        # they change it by asking - and because an agent's session is fixed at birth, so a change
        # governs the next one started, never one already working. It was hardcoded and invisible;
        # they had to ask what their agents were running and could not be told (see excephalon.models).
        self._model, self._effort = DEFAULT_MODEL, DEFAULT_EFFORT
        self._clock = clock
        self._desked = {}
        # Names a start has settled on but not yet inserted (the agent's session is still
        # connecting, which happens off the lock so the digest stays snappy). Held so a concurrent
        # start cannot hand out the same name in that window and clobber the one being born.
        self._reserved = set()
        self._lock = threading.Lock()
        self._threads = []

    def start(self, name, cwd, task, enhancement=None, project=None, item_id=None):
        """Put a fresh agent on `task` in `cwd`. Returns the name the agent was actually given -
        `name` unless it collided with a running agent or an open tab and had to be bumped. The
        reply arrives in the Outbox when it lands, so this still returns without waiting on work.

        `enhancement`, when given, is the list item this agent is completing - ticked off its card
        when the agent is retired - and `project` names the Projects-tab card that item lives on
        ("Highdeas"), with None meaning his Enhancements card. `item_id` is that item's NUMBER,
        resolved while it was in front of whoever started the agent: the tick goes by number
        because the text is whatever the brain retyped, and a retyping that drifted left two
        finished features with their items still open and their robots simply grey again.

        The name is made unique HERE because the caller's namer distills it to a short, project-
        prefixed label (see excephalon.naming), and short labels collide where the old task-length
        names never did - a collision on this key used to silently REPLACE a live agent. The
        session is connected off the lock (a connect takes real time, and holding the lock would
        stall the digest), so the settled name is RESERVED across that gap.

        The standing rule rides along with the task itself - not with every later message, since
        the session keeps it, and repeating it would be most of what the agent's tab is made of."""
        item_id = item_id if item_id else self.item_number(enhancement, task)
        with self._lock:
            name = unique_name(name, self._taken_names())
            self._reserved.add(name)  # held from now until it is desked, so no concurrent start takes it
        try:
            agent = self._factory(name, cwd, self._decide, model=self._model, effort=self._effort)
            with self._lock:
                self._desked[name] = _Desked(agent, cwd, task, self._open_log(name),
                                             model=self._model, effort=self._effort,
                                             enhancement=enhancement, project=project,
                                             item_id=item_id)
        finally:
            with self._lock:
                self._reserved.discard(name)  # now it lives in _desked, or the start failed
        self._dispatch(name, task + STANDING_RULE + self._law_note())
        return name

    def item_number(self, enhancement, task):
        """The list item's NUMBER from whatever words this start was given - the item text as it
        was retyped, and failing that his own task text, which is passed on faithfully. Resolved
        HERE so no door can forget it: with the resolution living in the brain's tool alone, an
        agent started by a Projects-tab robot click carried no number, and the finished work left
        its item open again ("Excephalon still failed to check off the task in the Projects tab").
        Never a guess - the resolver answers only on a unique match.

        Public because the doors read it too, before the start: the agent's NAME carries the number
        ("highdeas-7-...") so a tab and the roll call tie it to the exact card item, and reading it
        off this one resolver keeps a second, drifting copy of the resolve logic out of the doors."""
        if self._resolve_item is None:
            return None
        for words in (enhancement, task):
            if not words:
                continue
            try:
                found, _ = self._resolve_item(words)
            except Exception:
                return None  # an unreadable list costs the tick its number, never the start
            if found:
                return found
        return None

    def _taken_names(self):
        """Names not free for a newcomer - read under the lock: the agents at the desk, the tabs
        still open in the log folder (the window draws one per file), and any name a concurrent
        start has reserved but not yet inserted."""
        names = set(self._desked) | self._reserved
        if self._log_dir is not None and self._log_dir.exists():
            names |= {path.stem for path in self._log_dir.glob("*.log")}
        return names

    def _law_note(self):
        """The machine-wide engineering law, pointed at rather than pasted: one source, no size
        ceiling, and each agent reads the CURRENT text, never a copy staled by Excephalon's uptime.
        Silent when the file isn't there - a fresh machine must not send agents chasing it."""
        if self._law_path is None or not self._law_path.exists():
            return ""
        return (f"\n\nThe user's machine-wide engineering law is in {self._law_path} - read "
                "that file before you begin, and follow it as strictly as this repo's own "
                "CLAUDE.md.")

    def rename(self, name, to):
        """Call an agent something else - his word for it, everywhere the app uses a name.

        A name is a LABEL: the desk's key, the log file the window draws a tab from, the roster,
        the survival record, and the tag on any news waiting to be spoken. The worktree and the
        branch keep their own names, which are git's business and not his.

        False when there is no such agent, when the new name is already taken, or when it is not
        a name a file can carry - a rename that half-lands would leave a tab pointing at nothing.
        """
        name = self.resolve(name) or name
        wanted = safe_name(to)
        if not wanted:
            return False
        with self._lock:
            if name not in self._desked:
                return False
            if wanted == name:
                return True  # the name it already has: nothing to move, and no failure either
            # Its own name in another case is not a collision - it is this same agent, and the
            # rename is the case change. Another agent's name in any case IS one, because on
            # Windows both tabs would want the one log file.
            if any(wanted.lower() == held.lower() for held in self._desked
                   if held.lower() != name.lower()):
                return False  # a name in use would collide two agents into one tab
            entry = self._desked.pop(name)
            self._desked[wanted] = entry
        if self._log_dir is not None:
            log = self._log_dir / f"{name}.log"
            if log.exists():
                log.replace(self._log_dir / f"{wanted}.log")
            entry.log = self._open_log(wanted)  # further lines go to the file under the new name
        retag = getattr(self._outbox, "retag", None)
        if retag is not None:
            retag(name, wanted)  # news already queued is about the same agent, by its new name
        self._persist()
        return True

    def revive(self):
        """Reopen every agent the last process recorded, each resumed on its old session.

        "Obviously the agent processes must be independent of Excephalon. I close it and reopen it
        constantly" - and a restart used to strand the whole fleet. An agent recorded mid-task is
        told to pick back up; one that was idle is reattached and left in peace. An entry with no
        session id was never heard from, so there is nothing to resume - it is skipped. Returns
        the names brought back."""
        # Before the record is even read: held news is judged by the tabs, not by the fleet
        # file, which can be empty while four stale reports are still queued.
        self._forget_finished_news()
        if self._state_path is None or not self._state_path.exists():
            return []
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []  # an unreadable record must not stop the app from starting
        revived = []
        for entry in saved:
            name, session = entry.get("name"), entry.get("session_id")
            if name and self._already_retired(name):
                # Its log is in the archive: this agent was wrapped up, whatever the record still
                # says. The record is written on the way down, so an agent wrapped up from outside
                # the app (or after the last persist) comes back from the dead - and everything
                # built to keep him informed then dutifully re-raises work he has already ruled
                # on: "can you get it to stop talking about this thing, which I've already told
                # it twice is finished? this is the third time it's pestered me." News about it
                # goes the same way.
                drop = getattr(self._outbox, "drop", None)
                if drop is not None:
                    drop(name)
                continue
            if name and not session:
                # The record can lose an id the CLI's own session store still knows: one boot
                # persisted freshly-resumed agents before they had spoken - null over the known
                # ids - and the next boot skipped the whole fleet ("no trace" of agents the user
                # had watched work all afternoon). The store is per-cwd, so the newest session
                # that ever ran in this agent's worktree is this agent's.
                session = newest_session_for(entry.get("cwd"))
            if not name or not session:
                continue
            model = entry.get("model") or self._model
            effort = entry.get("effort") or self._effort
            agent = self._factory(name, entry.get("cwd"), self._decide,
                                  model=model, effort=effort, resume=session)
            with self._lock:
                desked = _Desked(agent, entry.get("cwd"), entry.get("task", ""),
                                 self._open_log(name), model=model, effort=effort,
                                 delivery=Delivery(entry.get("delivery") or "building",
                                                   entry.get("steps"),
                                                   entry.get("rejections") or 0),
                                 enhancement=entry.get("enhancement"),
                                 project=entry.get("project"),
                                 item_id=entry.get("item_id"),
                                 landed=bool(entry.get("landed")),
                                 ticked=bool(entry.get("ticked")))
                desked.recorded_session = session  # what the next persist writes until it speaks
                desked.state = "idle"
                # A crash streak survives the app's own restart, or a task that kills its agents
                # would get a fresh silent allowance every boot.
                desked.deaths = entry.get("deaths") or 0
                self._desked[name] = desked
            revived.append(name)
            if entry.get("delivery") == "landing":
                self._dispatch(name, RESUME_LANDING)
            elif entry.get("state") in ("starting", "working"):
                self._dispatch(name, CONTINUE_AFTER_RESTART)
            elif entry.get("delivery") == "ready":
                # Presented, and no verdict on record: the user is the one holding this up, and
                # nothing re-engages an idle agent - so the reminder is raised at startup rather
                # than waiting for him to think to ask.
                self._events("pending", name, entry.get("steps") or "")
            elif (entry.get("delivery") or "building") == "building" and not entry.get("steps"):
                # Recorded idle (or dead) with work never presented: the black hole. Idle it
                # stays idle forever, and what it built goes unseen unless it is asked to show.
                self._dispatch(name, PRESENT_AFTER_RESTART)
        self._persist()
        return revived

    def _forget_finished_news(self):
        """Drop held news about any agent with no live log - it has been wrapped up, whatever the
        fleet record says, and an update about closed work arrives as a surprise. The record can
        be empty while the spool still holds four reports, which is how a wrap-up he had twice
        called finished came back a third time, jargon and all, three seconds after a launch.

        What a thread ENDED as is spared, because that is never stale and he is owed it exactly
        once: the scroll-position fix merged, its report was offered at a lull he never answered,
        the session closed with it still in the spool - and this line threw it away on the next
        launch. He was never told the feature had shipped; it reached him days later as a clause
        inside a greeting."""
        held = getattr(self._outbox, "owed_about", None)
        drop = getattr(self._outbox, "drop", None)
        if held is None or drop is None or self._log_dir is None:
            return
        for name in held():
            if name and not (self._log_dir / f"{name}.log").exists():
                drop(name, keep_conclusions=True)

    def _already_retired(self, name):
        """Has this agent's log been moved to the archive? That move IS the wrap-up's record, and
        it outlives any fleet file: a live agent always has its log in the live folder."""
        if self._log_dir is None or self._archive_dir is None:
            return False
        return ((self._archive_dir / f"{name}.log").exists()
                and not (self._log_dir / f"{name}.log").exists())

    def choose(self, model=None, effort=None):
        """Put the NEXT agent on this model, at this effort, and say what it will be. Either half
        left out keeps what was there, because they say one or the other as often as both."""
        self._model = model or self._model
        self._effort = effort or self._effort
        return describe(self._model, self._effort)

    def running_on(self):
        """What a fresh agent would be started on right now."""
        return describe(self._model, self._effort)

    def resolve(self, name):
        """The agent this name MEANS, or None: his words, a tool's retyping, or a truncation,
        resolved against who is actually here - and never a guess between two candidates.

        The desk names agents by truncating his words to a filename, then hands that name to
        the brain and the foreman - who retype it. One retyping missed, the foreman answered
        "the agent isn't reachable at the desk under the name I was given", and its failure
        report reached the user as a heads-up about machinery ("it can't find the agent... it
        should be smart enough to just look at open/recent agents or ones with names that are
        similar. this is bullshit I shouldn't be pestered about"). Exact first; then the name
        as `safe_name` writes one, prefix in either direction, which is what a truncation is;
        then shared words, taken only when exactly one candidate wins."""
        wanted = str(name or "").strip()
        with self._lock:
            held = list(self._desked)
        if self._log_dir is not None and self._log_dir.exists():
            # Tabs still open without an agent behind them answer to their names too - the
            # window shows them, so he and the tools speak of them.
            held += [path.stem for path in self._log_dir.glob("*.log") if path.stem not in held]
        if self._archive_dir is not None and self._archive_dir.exists():
            # And so do the wrapped-up: their logs are the record of finished work, and a name
            # that resolves only while its agent is alive is how the foreman answered "with no
            # log I can't confirm" about logs sitting whole in the archive.
            held += [path.stem for path in self._archive_dir.glob("*.log")
                     if path.stem not in held]
        if not wanted or not held:
            return None
        if wanted in held:
            return wanted
        folded = wanted.lower()
        matched = [name for name in held if name.lower() == folded]
        if len(matched) == 1:
            return matched[0]
        tidy = safe_name(wanted).lower()
        if tidy:
            matched = [name for name in held
                       if name.lower().startswith(tidy) or tidy.startswith(name.lower())]
            if len(matched) == 1:
                return matched[0]
        words = _name_tokens(wanted)
        if words:
            shared = {name: len(words & _name_tokens(name)) for name in held}
            best = max(shared.values())
            matched = [name for name, count in shared.items() if count == best and count]
            if len(matched) == 1:
                return matched[0]
        return None

    def send(self, name, message):
        """Say something more to an agent already at the desk. False if there's no such agent -
        the caller must not be told a message was delivered when it wasn't."""
        name = self.resolve(name) or name
        with self._lock:
            if name not in self._desked:
                return False
        self._dispatch(name, message)
        return True

    def drop_news(self, name):
        """Forget the held news about one agent - for when the user has moved past it (their new
        words to the agent supersede whatever it was waiting to say). NOT part of `send`, because
        the foreman also sends: a technical prod settles a snag, not the user's business, and must
        never eat news still owed to them."""
        name = self.resolve(name) or name
        drop = getattr(self._outbox, "drop", None)
        if drop is not None:
            drop(name)

    def hand_over_news(self, name):
        """Ask that this agent's held news be spoken at the next opening - True when there was
        news to hand over. The brain calls this instead of retelling a held update in its own
        words: retold, the app then delivered its held copy too, and the user heard two versions
        of the same news 13 seconds apart."""
        name = self.resolve(name) or name
        held = getattr(self._outbox, "owed_about", None)
        request = getattr(self._outbox, "request", None)
        if held is None or request is None or name not in held():
            return False
        request(name)
        return True

    def roster(self):
        """(name, state, task) for each agent, newest state - what the roster file is written from."""
        with self._lock:
            return [(name, desked.state, desked.task) for name, desked in self._desked.items()]

    def present(self, name, steps):
        """Record that `name`'s work is standing up for the user's eyes, with the steps to see it.

        Refused for an agent mid-turn: the steps come from its report, so marking before it has
        reported would present a thing that does not exist yet. Raises DeliveryError with the
        reason - the caller owes the brain that sentence, not a silent no."""
        name = self.resolve(name) or name
        with self._lock:
            entry = self._desked.get(name)
            if entry is None:
                raise DeliveryError(f"no agent called {name} is at the desk")
            if entry.state in ("starting", "working"):
                raise DeliveryError(f"{name} hasn't finished its turn yet - wait for its report")
            entry.delivery.present(steps)
        self._persist()

    def verdict(self, name, approved, feedback=""):
        """Record the user's verdict on presented work, and set the mechanical consequence going:
        approval sends the agent to land it, rejection carries the feedback back. The Delivery
        refuses a verdict on work never presented - the loop's whole point.

        And no APPROVAL can be recorded while the agent's walkthrough is still waiting to be
        spoken: an ambiguous "yes" was once recorded as approval of work whose ready-for-your-eyes
        steps had never reached the user, and it merged without his eyes ever on it - "I never
        even accepted it; it was never presented to me to be validated." A rejection stands either
        way - he often judges from his own looking - and it drops the now-stale walkthrough, since
        his feedback has moved past it."""
        name = self.resolve(name) or name
        held = getattr(self._outbox, "owed_about", None)
        owed = held() if held is not None else set()
        with self._lock:
            entry = self._desked.get(name)
            if entry is None:
                raise DeliveryError(f"no agent called {name} is at the desk")
            if approved and entry.delivery.stage == "landing" and not feedback:
                return  # "The ship it still stands." - agreement with a landing already ordered
            if approved and name in owed:
                raise DeliveryError(
                    f"{name}'s walkthrough is still waiting to be spoken - the user cannot have "
                    "approved work they were never shown, so deliver its update first")
            entry.delivery.verdict(approved)
        self._persist()
        if approved:
            # An approval may carry one change to fold in on the way - "ship it with that small
            # tweak" is a sign-off, not feedback to re-present over. Routed as words alone, no
            # verdict reached the record and the landing gate then refused the very landing he
            # had ordered.
            self._dispatch(name, APPROVED_WITH_CHANGE.format(feedback=feedback) if feedback
                           else APPROVED_LAND_IT)
        else:
            self.drop_news(name)
            self._dispatch(name, REJECTED_TRY_AGAIN.format(feedback=feedback))

    def in_review(self):
        """The agents whose work is in front of the user's eyes RIGHT NOW: presented, the
        walkthrough actually spoken (nothing about them still owed), verdict pending. The
        conversation holds other news while this is non-empty - one thing at a time, his own
        standing instruction, enforced by the loop rather than asked of the persona."""
        held = getattr(self._outbox, "owed_about", None)
        owed = held() if held is not None else set()
        with self._lock:
            return [name for name, entry in self._desked.items()
                    if entry.delivery.stage == "ready" and name not in owed]

    def delivery_stage(self, name):
        """Where `name`'s work stands - what the narrator asks before wording a finished turn."""
        name = self.resolve(name) or name
        with self._lock:
            entry = self._desked.get(name)
            return entry.delivery.stage if entry is not None else None

    def task_of(self, name):
        """What `name` was put on - the first thing a senior read of its situation needs. A
        wrapped agent's task is still on file (the endings record), because a question about
        finished work deserves better than "(unknown)"."""
        name = self.resolve(name) or name
        with self._lock:
            entry = self._desked.get(name)
            if entry is not None:
                return entry.task
        ended = [record for record in self._wrapped_records() if record.get("name") == name]
        return ended[-1].get("task") if ended else None

    def ended(self, name):
        """How this agent's thread ENDED - "delivered" or "died" - or None for one still going
        (or never recorded). The foreman asks before treating a missing agent as a mystery: a
        landing agent's auto-wrap-up can beat the quiet alarm's question, and the answer then
        is on file, not a snag to investigate."""
        name = self.resolve(name) or name
        with self._lock:
            if name in self._desked:
                return None  # still at the desk: its thread has not ended
        ended = [record for record in self._wrapped_records() if record.get("name") == name]
        return ended[-1].get("outcome") if ended else None

    def recent_log(self, name, limit=3000):
        """The tail of an agent's exchange log - the situation as it actually unfolded, for the
        foreman's senior read. The tail and not the whole file, because a day-long exchange would
        drown the situation it ends on. A wrapped agent's log is read from the archive - the
        record of finished work is still the record ("that's fucking bullshit, the logs are
        right there"). Empty when there is nothing to read."""
        name = self.resolve(name) or name
        if self._log_dir is None:
            return ""
        log = self._log_dir / f"{name}.log"
        if not log.exists() and self._archive_dir is not None:
            log = self._archive_dir / f"{name}.log"
        try:
            text = log.read_text(encoding="utf-8")
        except OSError:
            return ""
        return text[-limit:]

    def digest(self):
        """The fleet as a few plain lines, for handing to a brain at the top of a turn.

        "How's it going?" used to send the brain off to read the roster file with its own tools -
        half a minute of dead air for state this process already held in memory. The digest is
        that state as text, so a status question is answerable in the breath it was asked.

        "Presented" is claimed only once the user could actually have seen it: mark_ready fires
        when the announcement is COMPOSED, and the announcement can then wait in the queue for an
        hour - briefed "awaiting their verdict" across that gap, the brain told him "I presented
        it earlier... no new update since then" about a walkthrough he had never heard ("That's
        false. You never presented it to me."). So while the outbox still owes news about a
        presented agent, the briefing says the work has NOT reached them."""
        held = getattr(self._outbox, "owed_about", None)
        owed = held() if held is not None else set()
        with self._lock:
            lines = [
                f"{name}: {entry.state}"
                + (f", last heard {entry.last_heard}" if entry.last_heard else "")
                + f" - task: {_one_line(entry.task)}"
                + (f" - {self._delivery_truth(name, entry, owed)}"
                   if self._delivery_truth(name, entry, owed) else "")
                # A quiet fact, held for "what's taking so long?" - never volunteered: he has
                # ruled that crashes and restarts are machinery, and the answer to work in
                # progress is "still working on it".
                + (f" - (crashed and was restarted, {entry.deaths}x - mention only if he asks "
                   "why it is slow)" if entry.deaths else "")
                + (f" - last said: {_one_line(entry.last_word)}" if entry.last_word else "")
                for name, entry in self._desked.items()
            ]
        fleet = "\n".join(lines) or "No agents running."
        # The ladder rides with the stages so a stage word is never left to interpretation, and
        # the briefing claims its own authority: a stage it does not state is a stage the brain
        # must not invent.
        fleet = (f"Every work thread climbs: {LADDER}. This is the app's own record of where "
                 f"each one stands - the one truth, never your memory of it.\n{fleet}")
        orphans = self._orphan_tabs()
        if orphans:
            fleet += ("\nTabs still open from agents no longer at the desk - close_agent_tab "
                      "closes one: " + ", ".join(orphans))
        recently = self._recently_wrapped()
        if recently:
            fleet += ("\nRecently wrapped up, each log in the archive (run_errand can read "
                      "one): " + "; ".join(recently))
        return fleet

    def _recently_wrapped(self, count=3):
        """The newest wrapped-up agents WITH how each one ended - so a name the user is still
        using resolves even after its tab closed, and its outcome is a fact on file rather than
        a memory. Briefed from the live fleet alone, the brain could not even see that a wrapped
        agent had existed ("That agent stalled on the merge" - about work that had merged); briefed
        bare names, it re-invented their endings ("done now; ready for you to look at" - about
        work delivered and wrapped up minutes earlier). Newest first and a few only."""
        ended = {record["name"]: record for record in self._wrapped_records()}
        if self._archive_dir is None or not self._archive_dir.exists():
            logs = []
        else:
            logs = sorted(self._archive_dir.glob("*.log"),
                          key=lambda log: log.stat().st_mtime, reverse=True)[:count]
        lines = []
        for log in logs:
            record = ended.get(log.stem)
            if record is None:
                lines.append(log.stem)  # wrapped before endings were recorded: the name still counts
            else:
                lines.append(f"{log.stem}: {record['outcome'].upper()} "
                             f"{_ago(self._now() - record.get('at', 0))}"
                             + (f" - was on: {_one_line(record['task'])}"
                                if record.get("task") else ""))
        return lines

    def _wrapped_records(self):
        """Every recorded ending, oldest first - [] when there is no record or it will not read."""
        if self._wrapped_path is None or not self._wrapped_path.exists():
            return []
        try:
            held = json.loads(self._wrapped_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return held if isinstance(held, list) else []

    def _record_wrapped(self, name, outcome, task):
        """Write one agent's ending down. Append-and-cap, and never a reason a wrap-up fails -
        a retirement that could not note itself must still retire."""
        if self._wrapped_path is None:
            return
        try:
            records = self._wrapped_records()
            records.append({"name": name, "outcome": outcome, "at": self._now(),
                            "task": _one_line(task)})
            self._wrapped_path.parent.mkdir(parents=True, exist_ok=True)
            self._wrapped_path.write_text(json.dumps(records[-50:], indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _delivery_truth(name, entry, owed):
        """The delivery stage as the user has actually experienced it: presented-and-awaiting only
        holds once nothing about the agent is still waiting to be spoken."""
        described = entry.delivery.describe()
        if described and entry.delivery.stage == "ready" and name in owed:
            return ("finished and ready to show, but the announcement has NOT reached them yet - "
                    "it is still waiting to be spoken, so they have not seen this work")
        return described

    def _orphan_tabs(self):
        """Log files still in the live folder with no agent behind them: tabs the user can see
        that the fleet lines would never mention. The window draws a tab per log file, the desk
        knows only its agents - and when the two views split, he asked for a leftover tab to be
        closed and the brain, briefed from the desk alone, could not even see its name."""
        if self._log_dir is None or not self._log_dir.exists():
            return []
        with self._lock:
            known = set(self._desked)
        return sorted(path.stem for path in self._log_dir.glob("*.log") if path.stem not in known)

    def retire(self, name, report=None):
        """Wrap a finished agent up in one gesture: close its tab (the log moves into the archive),
        tick off the Enhancements item it was completing, let the session go, and remove its
        worktree.

        "It should probably archive the agent log... and always do stuff like archive the Claude
        session and worktree" - chores nobody should have to name separately. False when there is
        nothing to retire, or the agent is still live - closing a live agent's tab would drop the
        user's view into work still happening. An agent the desk never had (yesterday's, before a
        restart) is just its leftover log, and the move alone closes it. A worktree that refuses
        removal (dirty, locked) is left for a maintenance sweep - the wrap-up itself never fails
        over it. Only a cleanly finished agent ticks its item: a DIED one never marks its ask done.

        `report` takes a tick that could not land, instead of it being pushed as news of its own -
        for the caller that is already sending one utterance about this wrap-up, since news of
        the landing would otherwise supersede the miss on arrival and the item would stay open
        with nobody told."""
        name = self.resolve(name) or name
        held = getattr(self._outbox, "owed_about", None)
        owed = held() if held is not None else set()
        with self._lock:
            entry = self._desked.get(name)
            if entry is not None and entry.state not in ("idle", "failed"):
                return False  # starting or working - live either way, and a live tab stays up
            # Work he has not ruled on cannot be wrapped up. An agent built a feature, the tab was
            # closed over it, and he met the result as a fait accompli: "are you saying you
            # delivered a feature without me verifying it first? Have you forgotten the absolute
            # basics of how you are supposed to supervise new features?" A DIED agent is another
            # matter - there is no verdict to wait for - and a leftover log with no agent behind
            # it is just a file to file away.
            if (entry is not None and entry.state == "idle"
                    and entry.delivery.stage != "landing"):
                return False
            # And "landing" is an ORDER, never an outcome. The order goes out on a thread of its
            # own, and a wrap-up that trusted the stage raced that very thread: two seconds after
            # his "ship it" an agent was recorded DELIVERED, its item ticked off his list and its
            # tab closed, while its work sat unpushed on his machine - and he was then twice told
            # the feature had landed and was live. Only git saying the branch reached origin/main
            # makes a wrap-up legal (`entry.landed`, set where the desk asks); a DIED agent has no
            # merge to wait for and is not held here.
            if entry is not None and entry.state == "idle" and not entry.landed:
                return False
            # And a wrap-up is illegal while anything about this agent is still waiting to be
            # spoken. The drop further down is for news he has MOVED PAST - but a merged report
            # he was never told is the loop's last word, and the wrap-up once dropped exactly
            # that unheard: the landed feature then read as lost ("clearly my feature just got
            # dropped in a black hole and Excephalon somehow doesn't know anything about it").
            # Scoped to desked agents: a leftover log with no agent behind it still closes, its
            # stale news still dropped.
            if entry is not None and name in owed:
                return False
            # Judged and taken off the desk in ONE hold, and the judgement carried out rather than
            # re-read. The state was read twice - here, and again after a `git worktree remove` -
            # and a dispatch landing in that gap (a revived landing agent picks its merge back up
            # on a thread) left the two readings disagreeing: the wrap-up went ahead on "idle" and
            # the tick was withheld on "working". Off the desk, a dispatch that has not started
            # finds nothing and stands down, so nothing can move under the rest of this.
            finished_cleanly = entry is not None and entry.state == "idle"
            if entry is not None:
                self._desked.pop(name, None)
        # How this thread ENDED, written down before anything else can fail: the ladder's last
        # rung is a fact the fleet record forgets the moment the agent leaves the desk, and a
        # wrap-up nobody could look up is how "delivered" got re-invented as "ready for review".
        if entry is not None:
            self._record_wrapped(name, "delivered" if finished_cleanly else "died", entry.task)
        log = self._log_dir / f"{name}.log" if self._log_dir is not None else None
        if entry is None and (log is None or not log.exists()):
            return False
        if log is not None and log.exists() and self._archive_dir is not None:
            self._archive_dir.mkdir(parents=True, exist_ok=True)
            log.replace(self._archive_dir / log.name)
        # Anything queued about this agent is news about work that is over: undelivered, it would
        # arrive as an update on a closed feature - "I'm kind of surprised you have an update for
        # that one, because that feature is already done." Dropped BEFORE the tick below, whose
        # own miss report is the one thing about this agent he does still need to hear.
        drop = getattr(self._outbox, "drop", None)
        if drop is not None:
            drop(name)
        if entry is not None:
            try:
                entry.agent.close()  # the session first: nothing may hold the worktree open
            except Exception:
                pass  # the session may already be gone; the wrap-up carries on
            try:
                self._run(["git", "-C", entry.cwd, "worktree", "remove", entry.cwd], check=True)
            except Exception:
                pass  # dirty or locked: the sweep's business later, not a failed retirement
            if entry.enhancement or entry.item_id:
                # The tick's own miss report used to be thrown away, and the user met the result
                # cold: work merged, log archived, and the ticket still open with nobody told -
                # "as far as I know it's still open work." A tick that cannot land (or is
                # rightly withheld from a died agent) is NEWS, not a silent shrug. The tick goes
                # to the card the item RIDES FROM - a Projects-tab card as readily as the
                # Enhancements card, since a whole afternoon's Highdeas tasks were delivered and
                # left standing open ("it did not check them off in the Projects tab").
                ticked = finished_cleanly and self._tick_once(entry)
                if not ticked:
                    missed = (f"{name} is wrapped up, but its list item did not get checked "
                              "off - settle that item by hand (check_off_enhancement by its "
                              "number and card, or tell the user why it stays open).")
                    if report is not None:
                        report(missed)  # the caller folds it into the one utterance it is sending
                    else:
                        self._outbox.push(missed, about=name)
            self._finished(name)  # a landing agent's clock runs until here; a retired one is off it
            self._persist()
        return True

    def _tick_once(self, entry):
        """Check this agent's item off, once, however many doors ask.

        The merge asks first, because that is the fact a tick means; the wrap-up asks after, and
        finds it already done. A tick that MISSED is not recorded, so the next door tries again -
        an item left open is the failure this exists to end."""
        if entry.ticked:
            return True
        if self._tick(entry):
            entry.ticked = True
            return True
        return False

    def _tick(self, entry):
        """Check this agent's list item off - by its NUMBER first, which is what it was resolved
        to when the agent started. The tick used to be a substring match of the item text as the
        BRAIN had retyped it, so a paraphrase, a dropped backtick or a guessed card silently
        missed: two delivered features left their items open and their robots simply went grey
        again. The text match stays as the fallback for an agent started before a number could
        be found."""
        if self._complete_enhancement is None:
            return False
        where = {"heading": PROJECT_PREFIX + entry.project} if entry.project else {}
        if entry.item_id and self._tick_by_id is not None:
            if self._tick_by_id(entry.item_id, **where):
                return True
            if self._tick_anywhere is not None and self._tick_anywhere(entry.item_id):
                return True  # the card it was filed under moved; the number did not
        return bool(entry.enhancement) and bool(self._complete_enhancement(entry.enhancement,
                                                                          **where))

    def close(self):
        # The survival record is written BEFORE the fleet is let go: it is what the next process
        # revives from, so shutdown must leave it showing the fleet as it stood, not empty.
        self._write_state()
        with self._lock:
            desked = list(self._desked.values())
            self._desked.clear()
        for entry in desked:
            try:
                entry.agent.close()
            except Exception:
                pass  # a session that's already gone shouldn't block the rest of shutdown
        self._write_roster()

    async def _decide(self, agent, tool_name, tool_input):
        """Every tool an agent wants to use passes through here. Reads, edits, local git and running
        the app to present it are waved through - the user runs these agents unattended by choice.
        The one refusal is mechanical and cannot be forgotten: a command that ships work to the
        shared remote (push, open a PR, enqueue, merge) is denied until an approved verdict is on
        record (delivery stage "landing"), so an agent can never land what the user has not seen.
        Returns True to allow, or the reason string the agent is shown when the landing is refused."""
        with self._lock:
            entry = self._desked.get(agent)
            stage = entry.delivery.stage if entry is not None else None
        reason = landing_block_reason(stage, tool_name, tool_input)
        return True if reason is None else reason

    def _open_log(self, name):
        # A full date+time on every line, not just the clock time the conversation transcript uses.
        # An agent log is read from its TAIL - the foreman's recent_log, a human's `tail`, the
        # window's newest lines - where the once-a-day date header sits far above and out of reach,
        # so a time-only stamp leaves a line the agent wrote yesterday reading exactly like one it
        # wrote a minute ago. The date on each line is what lets a reader tell a working agent from a
        # dead session at a glance.
        if self._log_dir is None:
            return None
        log = Transcript(self._log_dir / f"{name}.log", timefmt="%Y-%m-%d %H:%M:%S")
        # The file exists from the moment the agent is desked, so its tab is there to open before the
        # first line lands. The Projects-tab robot goes green on start()'s return, and green is a
        # promise the log is there to navigate to - written first by the work thread instead, it
        # raced the click and all but always lost.
        log.ensure()
        return log

    def _dispatch(self, name, message):
        thread = threading.Thread(target=self._carry, args=(name, message), daemon=True)
        self._threads.append(thread)
        thread.start()

    def _carry(self, name, message):
        """Deliver one message to an agent and put whatever comes back where the user will hear it."""
        with self._lock:
            entry = self._desked.get(name)
        if entry is None:  # closed out from under us
            return
        self._log(entry, message, prefix=ENTITY_SAID)
        self._set_state(name, "working")
        self._alive(name)  # it has work in flight from this moment; the silence clock starts here
        try:
            # Everything the agent streams back goes to the log as it happens, so their tab shows
            # the agent working rather than an empty file that reads exactly like a dead one.
            reply = entry.agent.work(message, on_message=lambda msg: self._heard(name, msg))
        except Exception as exc:
            # A crash is infrastructure, not his news: "I should never need to know that
            # anything died. It's not relevant to me" - asked whether to restart a dead agent,
            # he answered "Yes, of course... your job is to insulate me from this kind of
            # Pointless delay." So the desk restarts it itself, silently, up to DEATH_LIMIT
            # times; only a task that keeps killing its agents becomes something to say.
            self._log(entry, f"(died: {exc})", prefix=AGENT_SAID)
            if self._restart_dead(name, entry, message):
                return
            self._set_state(name, "failed")
            self._finished(name)  # already announced as dead; don't also announce it as quiet later
            self._events("died", name, str(exc))
            return
        with self._lock:
            entry.deaths = 0  # a clean turn: earlier crashes were weather, not a pattern
        # Asked BEFORE the state flips to idle: the git round-trip takes real time, and spent
        # between idle and the finished event it opens a window where the agent reads as done
        # with nothing owed about it yet.
        landed = entry.delivery.stage == "landing" and self._merged(entry)
        if landed:
            with self._lock:
                entry.landed = True  # git said so; from here a wrap-up is legal, and only from here
            # And the tick happens HERE, at the merge, not as a step inside the wrap-up. Every
            # other door that ends an agent - the window's own close button on its tab, a wrap-up
            # this desk refuses, a process that goes down before it runs - used to leave his ask
            # standing open over work that had already shipped, and he has asked for this five
            # times: "when an agent is done with its task, Excephalon needs to check off the task
            # it was working on!!!!!!!". The merge is the fact the tick MEANS, so the tick is
            # taken the moment the fact is.
            self._tick_once(entry)
        self._set_state(name, "idle", last_word=reply)
        if landed:
            # The loop's last leg is mechanical, so the desk walks it itself: approved work
            # that has actually reached origin/main is wrapped up NOW - log archived, list
            # item ticked, session closed - and only then told to him, composed from the fact.
            # It used to hang on a narration commanding the brain to close the tab; that
            # narration failed once and the merged agent haunted the desk for fourteen hours -
            # revived every boot, re-presenting delivered work as new, refusing retirement -
            # while his ticket sat open ("make sure that tasks are designed to be
            # automatically checked off when the work gets finished").
            self.drop_news(name)  # its own prose reports are superseded by the fact of landing
            missed = []
            if self.retire(name, report=missed.append):
                # A tick that could not land rides the SAME utterance as the landing. Pushed as
                # news of its own it was destroyed on arrival: the landed narration is newer
                # news about the same agent, so the collapse ate the miss and the item stayed
                # open with nobody told - "instead of the tasks getting checked off at the end,
                # the robot icons just went grey again".
                self._events("landed", name, reply + ("\n\n" + missed[0] if missed else ""))
                return
        if entry.delivery.stage != "landing":
            # A landing agent still owes a merge report, so its silence clock keeps running - the
            # overnight stall was invisible precisely because idle stopped the count. Retirement
            # is what finally stops it.
            self._finished(name)
        self._events("finished", name, reply)

    def _merged(self, entry):
        """Has this agent's branch actually reached origin/main? Git is the one holder of that
        truth, so it is asked directly - a fetch first, since the merge queue lands on the
        remote and a minutes-old merge is invisible to a stale clone. Any failure to answer
        reads as not-merged: a wrap-up on a guess would close work still in flight, and the
        normal narration path still covers it."""
        try:
            self._run(["git", "-C", entry.cwd, "fetch", "origin", "--quiet"],
                      check=False, timeout=60)
            asked = self._run(["git", "-C", entry.cwd, "merge-base", "--is-ancestor",
                               "HEAD", "origin/main"], check=False, timeout=30)
            return getattr(asked, "returncode", 1) == 0
        except Exception:
            return False

    def _restart_dead(self, name, entry, message):
        """Give a crashed agent a fresh session on its own history and tell it to pick back up -
        True when the restart is in motion, False when its deaths have hit the limit and the
        crash is now genuinely the user's business. The resumed session remembers everything the
        dead one knew (same CLI session id), so nothing of the task is lost with the process; an
        agent that crashed before it ever spoke has no history to resume, and is simply handed
        the message it died on, from the top."""
        with self._lock:
            if self._desked.get(name) is not entry:
                return True  # retired or renamed out from under the crash; nothing to report
            entry.deaths += 1
            if entry.deaths > DEATH_LIMIT:
                return False
            session = (getattr(entry.agent, "session_id", None) or entry.recorded_session
                       or newest_session_for(entry.cwd))
            old = entry.agent
            entry.agent = self._factory(name, entry.cwd, self._decide,
                                        model=entry.model, effort=entry.effort, resume=session)
            entry.recorded_session = session
        try:
            old.close()
        except Exception:
            pass  # the session is already dead; the restart carries on
        self._log(entry, f"(restarted on a fresh session after the crash - attempt "
                         f"{entry.deaths} of {DEATH_LIMIT})", prefix=AGENT_SAID)
        self._persist()
        self._dispatch(name, RESUME_AFTER_CRASH if session else message)
        return True

    def _plain_notices(self, kind, agent, report):
        """The undirected default: what the desk always said, straight to the outbox. A notice,
        never the agent's own words - the full reply is in the log its tab reads. Named, so that
        several landing together can be read out by name for one of them to be picked."""
        if kind == "died":
            self._outbox.push(f"The {agent} agent died: {report}", about=agent)
        else:
            self._outbox.push(notice(agent, report), about=agent)

    def _heard(self, name, message):
        """One message back from an agent - what it said AND what it did - logged as it arrives."""
        with self._lock:
            entry = self._desked.get(name)
        if entry is not None:
            for kind, text in render(message):
                self._log(entry, text, prefix=AGENT_SAID if kind == SAID else AGENT_DID)
                if kind == SAID:
                    entry.last_word = text[:120]  # the roster carries its words, not its machinery
            entry.last_heard = self._clock("%Y-%m-%d %H:%M:%S")
            self._alive(name)  # the same signal the roster records - any message is a sign of life

    def _alive(self, name):
        if self._monitor is not None:
            self._monitor.checked_in(name)

    def _finished(self, name):
        if self._monitor is not None:
            self._monitor.done(name)

    def _log(self, entry, text, *, prefix):
        if entry.log is not None:
            entry.log.write(text, prefix=prefix)

    def _set_state(self, name, state, *, last_word=None):
        with self._lock:
            entry = self._desked.get(name)
            if entry is None:
                return
            entry.state = state
            if last_word is not None:
                entry.last_word = last_word
        self._persist()

    def _persist(self):
        self._write_roster()
        self._write_state()

    def _write_state(self):
        """Everything a fresh process needs to reattach: who, where, on which session. JSON,
        because this one is read back by code (`revive`), not by the brain."""
        if self._state_path is None:
            return
        with self._lock:
            record = [
                {"name": name, "cwd": entry.cwd, "task": entry.task,
                 # The agent's own id once it has spoken this life; until then, the id it was
                 # revived on - never None over a known id, which orphaned a whole fleet.
                 "session_id": getattr(entry.agent, "session_id", None) or entry.recorded_session,
                 "state": entry.state, "model": entry.model, "effort": entry.effort,
                 "delivery": entry.delivery.stage, "steps": entry.delivery.steps,
                 "rejections": entry.delivery.rejections, "deaths": entry.deaths,
                 "enhancement": entry.enhancement, "item_id": entry.item_id,
                 "project": entry.project, "landed": entry.landed,
                 "ticked": entry.ticked}
                for name, entry in self._desked.items()
            ]
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    def _write_roster(self):
        """The roster is a file because the brain's memory isn't reliable across a context reset,
        but a file it can read is."""
        if self._roster_path is None:
            return
        with self._lock:
            lines = [
                f"{name} | {entry.state} | last heard {entry.last_heard or 'not yet'} | "
                f"{entry.cwd} | {entry.task}"
                + (f" | last: {entry.last_word[:120]}" if entry.last_word else "")
                for name, entry in self._desked.items()
            ]
        self._roster_path.parent.mkdir(parents=True, exist_ok=True)
        header = f"# agents Excephalon has running, as of {self._clock('%Y-%m-%d %H:%M:%S')}\n"
        self._roster_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _real_agent(name, cwd, decide, *, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT, resume=None):
    # Imported here so the desk can be exercised without the SDK (and without a real agent).
    from excephalon.supervised_agent import SupervisedAgent

    return SupervisedAgent(name, cwd, decide, model=model, effort=effort, resume=resume)
