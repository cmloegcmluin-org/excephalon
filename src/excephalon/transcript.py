"""A timestamped, durable record of one session, written as it happens.

The terminal is where the conversation actually appears, and it scrolls away - so when something
goes wrong the only record of what the user saw was whatever they copied out of the terminal by hand.
This writes the same lines to a file as they're printed, stamped with the time, so a session can be
read back afterwards. The clock is injected so tests are deterministic; writes are locked because
background workers and the conversation loop both log.
"""

import json
import re
import threading
from datetime import datetime
from pathlib import Path


class Transcript:
    def __init__(self, path, *, clock=datetime.now, timefmt="%H:%M:%S"):
        self.path = Path(path)
        self._clock = clock
        self._timefmt = timefmt
        self._lock = threading.Lock()
        self._last_day = None  # the date the last line was written under, to mark day rollovers
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def ensure(self):
        """Create the file now, empty, if nothing is there yet - so a reader that lists the folder
        of logs sees it before the first line is written. An agent's tab is drawn one per *.log, so
        this is what puts the tab there the instant the agent is desked, rather than a beat later
        when its first line lands."""
        self.path.touch(exist_ok=True)

    def write(self, text, *, prefix=""):
        # The date lives in the filename and in a header written once per day; the lines themselves
        # carry only the time, and a fresh header marks a session that runs past midnight.
        now = self._clock()
        stamp = now.strftime(self._timefmt)
        lines = str(text).splitlines() or [""]
        body = "".join(f"[{stamp}] {prefix}{line}\n" for line in lines)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as handle:
                if now.date() != self._last_day:
                    handle.write(f"===== {now.strftime('%Y-%m-%d')} =====\n")
                    self._last_day = now.date()
                handle.write(body)


SESSION_MARK = "===== session ====="  # emitted between files; no log ever contains one


class MessageLog:
    """The conversation as MESSAGES - one JSON line each, beside the human-readable .log.

    The .log is prose for people: prefixes, wrapped lines, a date header. Reading it back into
    messages meant guessing - which prefix, whose line, is this bare line a continuation or
    something the app spoke - and every guess was a rule that eventually rewrote his history in
    front of him ("the conversation history had been rewritten. this is terrifying"). What the
    window draws now is not a guess: the same role the live view already gets is written down
    the moment it is said, and read straight back.

    One object per message: {"at": "<date time>", "role": ..., "text": ...}. Roles are the
    conversation's own - "you", "excephalon", "heads-up", "status" - and the text is exact, newlines
    and all."""

    def __init__(self, path, *, clock=datetime.now):
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def keep(self, role, text):
        line = json.dumps({"at": self._clock().strftime("%Y-%m-%d %H:%M:%S"),
                           "role": role, "text": str(text)}, ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def messages_in(path):
    """The messages one .jsonl record holds, as (role, date, time, text). A torn last line (the
    process died mid-write) is skipped rather than taking the session down with it."""
    kept = []
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return kept
    for line in raw:
        if not line.strip():
            continue
        try:
            held = json.loads(line)
        except ValueError:
            continue
        at = str(held.get("at", ""))
        date, _, clock = at.partition(" ")
        role = held.get("role", "status")
        kept.append((SELF if role == WAS_SELF else role, date, clock, held.get("text", "")))
    return kept


def messages_from_log(path):
    """One old .log, read back as messages - the guesswork, done ONCE.

    Sessions recorded before the message log existed are all there is for those days, so they are
    converted rather than lost, and the result is written down so no rule ever has to run over
    them again. Three things the format lets us know: a line carrying a prefix opens a message; a
    bare line in the SAME second is that message continuing (one message is written in one call,
    so it carries one stamp); and a bare line of its own is something the app spoke aloud without
    printing - an update offer, an acknowledgement - which is Excephalon talking, not an aside.
    Only the console's own asides, which open with "(" or "[", are asides."""
    kept = []
    date = ""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return kept
    for line in raw:
        day = day_of(line)
        if day is not None:
            date = day
            continue
        parsed = parse_line(line)
        if parsed is None:
            if kept and re.fullmatch(r"\[[^\]]*\]\s*", line.strip()):
                kept[-1] = kept[-1][:3] + (kept[-1][3] + "\n",)  # a blank line inside a message
            continue
        role, clock, text = parsed
        if role in ("day", "session"):
            continue
        if (kept and role == "status" and clock == kept[-1][2]
                and kept[-1][0] in ("you", SELF, "heads-up")
                and not text.startswith(("(", "["))):
            joined = kept[-1][3].rstrip("\n") + "\n" + text
            kept[-1] = kept[-1][:3] + (joined,)
            continue
        if role == "status" and not text.startswith(("(", "[")):
            role = SELF  # spoken, never printed: the app talking in its own voice
        kept.append((role, date, clock, text))
    return kept


def past_messages(directory, *, current=None, convert=True):
    """Every session ever recorded, oldest first, as one op per thing to draw:
    ("history", ("day", date)), ("history", ("session", "")) and
    ("history", ("message", role, stamp, text)).

    Each .log gets a .jsonl beside it the first time it is read, so the old format is converted
    once and never guessed at again. `current` is this session's own record, which is live and
    excluded."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    current = Path(current).stem if current else None
    # By session NAME, from either half: the record is what is read, and a log is what it is
    # converted from - a session that has only one of the two is still a session.
    names = sorted({path.stem for path in directory.glob("*.log")}
                   | {path.stem for path in directory.glob("*.jsonl")})
    ops, dated, first = [], None, True
    for name in names:
        if name == current:
            continue
        kept, log = directory / f"{name}.jsonl", directory / f"{name}.log"
        if convert and not kept.exists() and log.exists():
            _write_messages(kept, messages_from_log(log))
        messages = messages_in(kept) if kept.exists() else messages_from_log(log)
        if not messages:
            continue
        if not first:
            ops.append(("history", ("session",)))
        first = False
        for role, date, clock, text in messages:
            if date and date != dated:
                dated = date
                ops.append(("history", ("day", date)))
            ops.append(("history", ("message", role, clock, text)))
    return ops


def _write_messages(path, messages):
    """The converted session, written down beside its log so the conversion happens once."""
    try:
        with open(path, "w", encoding="utf-8") as handle:
            for role, date, clock, text in messages:
                handle.write(json.dumps({"at": (date + " " + clock).strip(), "role": role,
                                         "text": text}, ensure_ascii=False) + "\n")
    except OSError:
        pass  # a read-only archive is still readable; the conversion simply runs again


def past_lines(directory, *, current):
    """Every session ever recorded, oldest first - the whole thread above the live conversation,
    so scrolling back reaches the start rather than a cut with nothing on screen to explain it.
    `current` is this session's own file, which is live and excluded.

    A file writes its date at the top, so several sessions in one day used to read as the same
    date printed over and over. The two facts are separated here: the date is emitted only when
    it changes, and a session mark goes between files - one says which day, the other says a new
    conversation began.

    Unbounded on purpose: the whole archive is a few hundred kilobytes of text, and the page draws
    only the part of it that is scrolled to.
    The filenames sort chronologically, so sorting them is sorting the history."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    lines, dated = [], None
    for path in sorted(path for path in directory.glob("*.log")
                       if current is None or path != Path(current)):
        try:
            session = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if not session:
            continue  # nothing was said, so there is no session to divide off
        if lines:
            lines.append(SESSION_MARK)
        for line in session:
            day = day_of(line)
            if day is not None:
                if day == dated:
                    continue  # that date already stands above; repeating it reads as a glitch
                dated = day
            lines.append(line)
    return lines


def day_of(line):
    """The date on a file's day header, or None if the line is not one."""
    if line.startswith("===== ") and line.endswith(" =====") and line != SESSION_MARK:
        return line.strip("= ")
    return None


# What every archive calls Excephalon's own side, and what it is stored as from here on. The
# displayed name was never in these files - it is looked up from the role when the page draws -
# so this string is a STORAGE format, and everything ever written carries the old spelling of it.
SELF = "excephalon"
WAS_SELF = "entity"  # what the same side is called in everything recorded before the rename

# The prefixes an agent's exchange is written under. Named here because the desk writes them and
# `parse_line` reads them back, and two spellings of one format is a bug nothing would catch.
ENTITY_SAID = "EXCEPHALON> "
AGENT_SAID = "AGENT> "
AGENT_DID = "WORK> "  # what it ran, and what came back - the machinery under its words

# What Console writes, and what an agent's desk used to. Both spellings are read; only the first
# of each pair is ever written. Dropping the old ones would not lose a setting - it would lose his
# history, since a line whose role nothing recognises stops being a message at all: no name, no
# side, not a bubble.
SELF_SAID = f"{SELF}> "
SELF_HEADS_UP = f"{SELF} (heads-up)> "


# Both archives this reads: their own conversation (Console's prefixes) and an agent exchange (the
# desk's). "you" is whoever opened the exchange - them in their own thread, Excephalon in an agent's.
# Longest first within a spelling: "excephalon (heads-up)> " starts with "excephalon", so tried the
# other way round an unprompted line comes back as an ordinary reply and stops being marked as one.
_ROLE_PREFIXES = (
    ("you said: ", "you"),
    (SELF_HEADS_UP, "heads-up"),
    (SELF_SAID, SELF),
    (f"{WAS_SELF} (heads-up)> ", "heads-up"),
    (f"{WAS_SELF}> ", SELF),
    (ENTITY_SAID, "you"),
    ("ENTITY> ", "you"),
    (AGENT_SAID, SELF),
    (AGENT_DID, "work"),
)


# The two kinds of break, deliberately unalike: a dated rule for the day, and a quiet caesura for
# one conversation ending and the next beginning. Made to look the same they read as one repeated
# thing, which is the confusion this pair exists to end.
DAY_BREAK = "───────  {}  ───────"
SESSION_BREAK = "•   •   •"  # filled, not middle dots: at this size those are three faint specks


def recent_turns(directory, keep=16):
    """The tail of the recorded conversation, as (their words, the reply) pairs - the seed that
    lets a restarted process pick the thread back up instead of greeting them as a stranger.

    "There should be a way to reload Excephalon so that it gets any fixes but without breaking the
    current session." The half of a restart that breaks the session is the lost thread; the
    transcript already holds it. The tail walks BACK across session files until it is full,
    because the boundary that matters is his ("the stuff we were just talking about a few minutes
    ago"), not the process's: a window opened, never spoken into, and closed used to be the whole
    seed - one unanswered greeting - and the next boot answered "I don't have access to previous
    sessions" about a conversation minutes old. A question with no reply under it (the line a
    session died on) is skipped, never stitched to the next answer - a seed where answers sit
    under the wrong questions is worse than no seed at all.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    turns = []
    for record in sorted(directory.glob("*.log"), reverse=True):  # filenames sort chronologically
        turns = _session_turns(record) + turns
        if len(turns) >= keep:
            break
    return turns[-keep:]


def _session_turns(record):
    """One session's (question, reply) pairs, oldest first."""
    try:
        lines = record.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    turns, question = [], None
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            continue
        role, _, text = parsed
        if role == "you":
            question = text  # a question already waiting is the one the session died on - dropped
        elif role == SELF and question is not None:
            turns.append((question, text))
            question = None
    return turns


def parse_line(line):
    """Read one recorded line back as (role, time, text), or None if it isn't conversation.

    The prefixes are the ones Console writes; reading its own archive back is what lets past
    sessions appear in the window as the conversation they were, not as log lines.
    """
    line = line.rstrip()
    if line == SESSION_MARK:
        # Its own role, not "status": the window offers to copy a whole session from this line,
        # and recognising it by its display text would be reading the label to find the thing.
        return "session", "", SESSION_BREAK
    day = day_of(line)
    if day is not None:
        # The date each file writes once a day. Scrolling back through every session ever is a
        # wall without it, so it comes back as the break it marks rather than as nothing. Its own
        # role, so the date is read off the entry rather than back out of the line it draws.
        return "day", day, DAY_BREAK.format(day)
    if not line.startswith("[") or "] " not in line:
        return None
    stamp, _, body = line[1:].partition("] ")
    body = body.strip()
    if not body:
        return None
    for prefix, role in _ROLE_PREFIXES:
        if body.startswith(prefix):
            return role, stamp, body[len(prefix):]
        if body == prefix.rstrip():
            # The marker with nothing after it - a blank line in what was said. A written line
            # keeps no trailing space by the time it is read back, so without this the marker
            # itself was drawn, centred, in the middle of the tab: "AGENT>", "WORK>".
            return None
    return "status", stamp, body
