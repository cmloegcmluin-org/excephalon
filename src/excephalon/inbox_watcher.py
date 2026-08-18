"""Watch an inbox directory for word from the agents Excephalon is driving, and hand it to the
outbox so Excephalon can pass it on to the user.

This is how an agent reaches the user without them watching its screen: Excephalon tells each agent to
write any question or "ready for review" note as a line to `runtime/agent-inbox/<name>.txt`. A
background thread tails those files; each complete new line becomes an outbox message, spoken at the
next lull. Deliberately dumb - plain file polling, byte-offset per file, no OS-specific watching -
so it just works on Windows and can't corrupt the brain's own message stream.
"""

import threading

from excephalon.relay import notice
import time
from pathlib import Path


def _span(elapsed):
    minutes = round(elapsed / 60)
    return f"{minutes} {'minute' if minutes == 1 else 'minutes'}"


class QuietMonitor:
    """Watch the agents for silence instead of only waiting to be told.

    An agent reaches the user by writing to its inbox file; if it hangs or stalls it writes
    nothing, and they'd hear nothing (they once waited hours). So every check-in stamps the agent's
    last-heard time, and `tick()` — called on the same cheap poll the InboxWatcher already runs —
    surfaces one spoken heads-up once an agent has been silent past `quiet_after`. One warning per
    silence episode (no nagging); a later check-in re-arms it. Clock and threshold are injected so
    this is testable without real waiting.
    """

    def __init__(self, outbox, *, quiet_after, clock=time.monotonic, events=None):
        # Silence is reported as an event - (kind, agent, report) - for the narrator to word in
        # the brain's own voice; undirected, the old spoken line goes straight to the outbox.
        self._events = events or (lambda kind, agent, report: outbox.push(
            f"The {agent} agent hasn't checked in for {report.removeprefix('been silent for ')}.",
            about=agent))
        self._quiet_after = quiet_after
        self._clock = clock
        self._last_seen = {}  # agent -> clock() when we last heard from it
        self._warned = set()  # agents already flagged silent this episode

    def checked_in(self, agent):
        """The agent produced a line (or just appeared) — it's alive; reset its silence timer."""
        self._last_seen[agent] = self._clock()
        self._warned.discard(agent)

    def done(self, agent):
        """It finished, or died. Either way stop the clock: silence only means something while
        there is work in flight, and calling a finished agent quiet reports a problem that isn't
        there."""
        self._last_seen.pop(agent, None)
        self._warned.discard(agent)

    def tick(self):
        now = self._clock()
        for agent, last_seen in self._last_seen.items():
            if agent in self._warned:
                continue
            elapsed = now - last_seen
            if elapsed >= self._quiet_after:
                self._warned.add(agent)
                self._events("quiet", agent, f"been silent for {_span(elapsed)}")


class InboxWatcher:
    def __init__(self, directory, outbox, *, poll_interval=1.0, sleep=time.sleep, monitor=None,
                 events=None):
        # What an agent wrote goes to the events sink as ("wrote", agent, report) for the narrator
        # to word; undirected, the capped plain notice goes straight to the outbox as it always has.
        # Undirected (no narrator wired yet), the app's own plain sentence carries it - never a
        # word of what the agent wrote, which is a conversation he was not part of.
        self._events = events or (lambda kind, agent, report:
                                  outbox.push(notice(kind), about=agent, kind=kind))
        self._dir = Path(directory)
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._monitor = monitor  # optional QuietMonitor: flags agents that go silent
        self._offsets = {}  # file -> bytes already surfaced
        self._stop = threading.Event()
        # Seed offsets past whatever's already there, so a fresh start doesn't replay old questions.
        for path in self._files():
            self._offsets[path] = self._size(path)

    def _files(self):
        return sorted(self._dir.glob("*.txt")) if self._dir.exists() else []

    @staticmethod
    def _size(path):
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def poll_once(self):
        for path in self._files():
            self._read_new_lines(path)
        if self._monitor is not None:
            # Only the tick. Which agents exist, and when each last spoke, is the DESK's to say -
            # a filename here is not an agent, and treating it as one invented two of them and
            # announced both as having gone quiet.
            self._monitor.tick()

    def _read_new_lines(self, path):
        """Surface what an agent has newly written as ONE message; True if anything was pushed.

        One message, not one per line: an agent overwrote its file with a thirty-line report and
        every line became its own spoken heads-up, which the user had to stop one at a time.
        """
        size = self._size(path)
        start = self._offsets.get(path, 0)
        if size < start:  # file was truncated or rewritten smaller - resync from the top
            start = 0
        if size <= start:
            self._offsets[path] = size
            return False
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                chunk = handle.read(size - start)
        except OSError:
            return False
        newline = chunk.rfind(b"\n")
        if newline == -1:
            return False  # only a half-written line so far; wait for it to finish
        self._offsets[path] = start + newline + 1
        # splitlines/join rather than a raw decode: an agent writing from Windows leaves
        # carriage returns behind, and those have no business inside a spoken message.
        lines = chunk[: newline + 1].decode("utf-8", "replace").splitlines()
        report = "\n".join(lines).strip()
        if not report:
            return False
        # Never the file's contents raw: an agent overwrote this with thirty lines of its own
        # internals and every word of it was read out at them. The sink decides the wording -
        # the narrator's brain-composed sentence, or the capped notice by default.
        self._events("wrote", path.stem, report)
        return True

    def run(self):
        while not self._stop.is_set():
            self.poll_once()
            self._sleep(self._poll_interval)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self._stop.set()
