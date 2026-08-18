"""One-off scheduled messages: hold a line and speak it at a real wall-clock time."""

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# How many seconds one unit of a spoken delay is worth. The brain hears "in ten minutes" and passes
# it on; the app owns the arithmetic, because the brain is never told the current time to the minute.
_UNIT_SECONDS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
                 "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
                 "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}

# "in 10 minutes", "10 min", "+2h", "in an hour" (a/an -> 1). A leading "in "/"+" and a written "a"
# are all how the same delay reaches here.
_RELATIVE = re.compile(r"(?:in\s+|\+\s*)?(\d+|an?)\s*(" + "|".join(_UNIT_SECONDS) + r")$")

# A full date and time: "2026-08-18 09:00", or with a "T". The brain reaches for this when the user
# names a day ("tomorrow", "the 20th"), resolving the date itself against the time in its briefing.
_ABSOLUTE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})[ t](\d{1,2}):(\d{2})$")

# A 12-hour clock time with a meridiem: "5:15pm", "5 pm", "12am". The minute is optional.
_CLOCK_12 = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])m$")

# A bare clock time, 24-hour: "17:15", "9:00". Both clock forms name a time of day, not a date, so
# they fire at the NEXT time that reading comes round - today if still ahead, tomorrow if it passed.
_CLOCK_24 = re.compile(r"(\d{1,2}):(\d{2})$")


def _next_occurrence(now, hour, minute):
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def resolve_when(when, now):
    """The wall-clock moment a spoken time refers to, or None if it is not one this understands.

    `now` is the current local time (a naive datetime); the return is the same kind, so the caller
    turns it into an epoch with `.timestamp()`.
    """
    text = " ".join(str(when).strip().lower().split())
    for prefix in ("at ", "on "):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break

    absolute = _ABSOLUTE.match(text)
    if absolute:
        try:
            moment = datetime(*(int(part) for part in absolute.groups()))
        except ValueError:
            return None  # a real-looking date that isn't one (month 13, day 40)
        return moment if moment > now else None  # a dated moment in the past is a mistake

    relative = _RELATIVE.match(text)
    if relative:
        count = 1 if relative.group(1).startswith("a") else int(relative.group(1))
        return now + timedelta(seconds=count * _UNIT_SECONDS[relative.group(2)])

    twelve = _CLOCK_12.match(text)
    if twelve:
        hour = int(twelve.group(1))
        minute = int(twelve.group(2)) if twelve.group(2) else 0
        if 1 <= hour <= 12 and minute <= 59:
            hour = hour % 12 + (12 if twelve.group(3) == "p" else 0)
            return _next_occurrence(now, hour, minute)

    clock = _CLOCK_24.match(text)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if hour <= 23 and minute <= 59:
            return _next_occurrence(now, hour, minute)

    return None


class Schedule:
    """The pending one-off messages, and the clock that fires them.

    A message the user asked for at 5:15 has to reach him at 5:15 whether or not a conversation is
    in progress, and even if the app was closed when the time came round - so the pending list is a
    file (`path`), read and rewritten under a lock so the tool that adds and the loop that fires
    never see a torn list. `deliver` is how a due message reaches him: in the running app, a push
    onto the outbox, which already carries proactive news to a lull and survives a restart until it
    is actually spoken. This owns only the WAIT; the outbox owns the delivery."""

    def __init__(self, path, deliver, *, clock=time.time):
        self._path = Path(path) if path is not None else None
        self._deliver = deliver
        self._clock = clock
        self._lock = threading.Lock()

    def add(self, at, message):
        """Hold `message` until wall-clock epoch `at`. Returns the stored item."""
        item = {"at": float(at), "message": str(message)}
        with self._lock:
            pending = self._load()
            pending.append(item)
            self._save(pending)
        return item

    def due(self, now=None):
        """Take (and forget) every message whose time has come, earliest first. Removing them here
        is what stops a fired message from firing again on the next poll or the next launch."""
        moment = self._clock() if now is None else now
        with self._lock:
            pending = self._load()
            ready = sorted((item for item in pending if item["at"] <= moment),
                           key=lambda item: item["at"])
            if ready:
                self._save([item for item in pending if item["at"] > moment])
            return ready

    def poll_once(self):
        """Deliver every message whose time has come. Called on a timer by `run`."""
        for item in self.due():
            self._deliver(item["message"])

    def run(self, *, stop, every=15.0, sleep=None):
        """Fire due messages every `every` seconds until `stop` is set. A message set for 5:15
        should land near 5:15, so the beat is short - a person accepts a few seconds' lag, not a
        few minutes'. On the FIRST beat this also catches anything that came due while the app was
        closed, which is how a reminder set before a restart still reaches him after it."""
        wait = sleep or (lambda seconds: stop.wait(seconds))
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass  # a broken poll must never take the session down
            wait(every)

    def start(self, stop):
        thread = threading.Thread(target=self.run, kwargs={"stop": stop}, daemon=True)
        thread.start()
        return thread

    def _load(self):
        if self._path is None:
            return []
        try:
            return list(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return []  # an unreadable schedule must not stop new messages from being scheduled

    def _save(self, pending):
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(pending, indent=2), encoding="utf-8")
        except OSError:
            pass  # a failed write costs durability, never the scheduling itself
