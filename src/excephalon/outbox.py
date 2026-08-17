"""A little queue for things Excephalon wants to say on its own - chiefly word from an agent it's
driving. A background producer pushes; the conversation loop drains and speaks the messages the
next time it's Excephalon's turn to talk. `arrived` lets a quiet moment (a lull while it's waiting
for the user to start speaking) be interrupted so the message goes out promptly, rather than sitting
until they happen to say something.
"""

import threading
from collections import deque


class News(str):
    """One thing waiting to be said, and which agent it is about.

    A string, because everything downstream speaks it, joins it and matches on it. But the agent's
    name has to survive the queue too: when several agents are ready at once they are read out
    numbered so one can be picked, and working the name back out of the message text would be
    reading the label to find the thing - two of the four kinds of news Excephalon queues do not
    carry it in any fixed place at all.

    `composed` says the BRAIN wrote these words (the narrator asked it to): spoken as its own,
    they need no unwritten-lines ledger entry - it remembers saying them the way it remembers any
    reply. App-authored news stays composed=False and is read back to it next turn.

    `listed` says this news is an ITEM - one of several he may be asked to choose between by
    name. Only an agent's news is: the errand hand exists so a small chore is not a visible
    agent, and its result was read out numbered beside a real one, under the internal word for
    the machinery ("I don't even know what errands would be"). Unlisted news is simply something
    to say.

    `kind` is the event it came from ("landed", "quiet", "finished"...), because not every piece
    of news is worth the same. A merge report is a thread's LAST WORD and outranks everything;
    a silence alarm is a guess by a timer, and one of those replaced a merge report in the queue
    and left him waiting on news that had already been written."""

    about = None  # the agent, when there is one
    composed = False  # whether the brain itself wrote the words
    listed = True  # whether it is an item on the numbered list, or just a thing to say
    kind = ""  # the event behind it, for the few places where the KIND decides

    def __new__(cls, message, about=None, composed=False, listed=True, kind=""):
        news = super().__new__(cls, message)
        news.about = about
        news.composed = composed
        news.listed = listed
        news.kind = kind
        return news

    @property
    def concluding(self):
        """Is this a thread's last word - it landed, or it died? Never held back for anything:
        holding one is the black hole this project has already sat through once."""
        return self.kind in ("landed", "died")

    @property
    def alarm(self):
        """Is this only a timer's guess that something has gone quiet? It is worth saying when
        there is nothing else to say about that agent, and worth nothing at all beside a real
        report - which one of them destroyed."""
        return self.kind == "quiet"


class Outbox:
    """`spool` (a path) makes undelivered news survive the process. Draining is NOT delivery -
    the conversation holds drained news in hand, sometimes for minutes, until a lull lets it
    speak - so the spool keeps every pushed item until `spoken` says it actually went out. On a
    real evening the brain wedged with three agents' reports in hand and the user restarted:
    the reports lived only in that process's memory, and the restarted app had "no trace" of
    the very updates it had just been offering. What is owed to the user must not be a casualty
    of the process that owed it."""

    def __init__(self, spool=None):
        self._items = deque()
        self._lock = threading.Lock()
        self._spool = spool
        self._dropped = set()  # agents whose news was dropped since last collected - see drop()
        self._requested = set()  # agents whose held news was asked spoken - see request()
        self.arrived = threading.Event()  # set while something is waiting to be spoken
        for held in self._spooled():  # last life's undelivered news, back in the queue
            # NOT composed, whoever wrote it: `composed` means "the brain that will be asked about
            # this wrote it", and the brain that wrote it died with the last process. Carried over
            # as composed, a spooled line skipped the unwritten-lines ledger and the new brain
            # denied saying it - to his face, about a line he had watched it say ("I don't see
            # that statement in our conversation - I didn't say the feature was already in
            # Highdeas", about a heads-up it had spoken verbatim 18 minutes earlier).
            self._items.append(News(held["message"], held.get("about"),
                                    listed=held.get("listed", True),
                                    kind=held.get("kind", "")))
        if self._items:
            self.arrived.set()

    def push(self, message, about=None, composed=False, listed=True, kind=""):
        with self._lock:
            self._items.append(News(message, about, composed, listed, kind))
            self._keep(message, about, composed, listed, kind)
        self.arrived.set()

    def drain(self):
        """Take everything queued (in arrival order) and clear the waiting signal."""
        with self._lock:
            items = list(self._items)
            self._items.clear()
            self.arrived.clear()
        return items

    def owed_about(self):
        """Every agent still owed news - queued here, OR drained into the conversation's hand and
        not yet spoken. The spool is the record of that whole debt, so it answers when there is
        one; asked from the queue alone, an agent whose walkthrough sat in hand for an hour read
        as all caught up, and the brain told the user it had presented work he had never seen."""
        with self._lock:
            if self._spool is not None:
                return {held.get("about") for held in self._spooled()}
            return {getattr(item, "about", None) for item in self._items}

    def retag(self, about, to):
        """Held news about a renamed agent is about the same agent - under his name for it now, so
        a roll call reads out what he called it rather than what the app happened to name it."""
        with self._lock:
            self._items = deque(News(str(item), to, item.composed, item.listed, item.kind)
                                if getattr(item, "about", None) == about else item
                                for item in self._items)
            kept = self._spooled()
            for held in kept:
                if held.get("about") == about:
                    held["about"] = to
            self._write(kept)

    def drop(self, about):
        """Forget every item about one agent - it is finished with, or the user has moved past it,
        and news about work already closed lands as a surprise rather than an update.

        The queue and the spool are only two of the three places news waits: the conversation
        drains items and holds them in hand for a lull. Those are out of reach from here, so the
        drop is also NOTED, and the holder collects the notes (`take_dropped`) and prunes its own
        hand - without that, a drop cleaned the queue while the stale copy in hand was still
        offered ("surely there's no update for smart grouping. You just sent off the latest
        message to it.")."""
        with self._lock:
            self._items = deque(item for item in self._items
                                if getattr(item, "about", None) != about)
            if not self._items:
                self.arrived.clear()
            self._dropped.add(about)
            self._write([held for held in self._spooled() if held.get("about") != about])

    def take_dropped(self):
        """Collect (and clear) the agents dropped since last asked - the holder of drained news
        prunes its hand with these on its next pass."""
        with self._lock:
            taken, self._dropped = self._dropped, set()
            return taken

    def request(self, about):
        """Ask that this agent's held news be SPOKEN at the next opening - the brain's way of
        delivering a held update instead of retelling it in its own words, which produced two
        versions of the same news 13 seconds apart. The request rides here because the news may
        be in the queue or already drained into the conversation's hand; the holder collects
        (`take_requested`) and speaks the item word for word."""
        with self._lock:
            self._requested.add(about)

    def take_requested(self):
        """Collect (and clear) the agents whose held news was requested spoken."""
        with self._lock:
            taken, self._requested = self._requested, set()
            return taken

    def spoken(self, news):
        """The conversation reports that this news actually reached the user - only then does it
        leave the spool. News merely drained is still owed."""
        with self._lock:
            self._forget(news)

    def superseded(self, news):
        """This news will never be spoken: newer news about the same agent has replaced it.

        Not "spoken" - it never reached anyone - but just as finished, and its durable copy has to
        go with it. Collapsing only the in-memory queue left the older sentence in the spool, and a
        restart hours later read it out as if it were new: he was told work was "ready for your
        eyes" thirteen seconds after giving his notes on that very work, "out of nowhere", with
        nothing in it he did not already know."""
        with self._lock:
            self._forget(news)

    def _forget(self, news):
        """Drop one durable copy - the caller holds the lock and decides what it means."""
        kept = self._spooled()
        for i, held in enumerate(kept):
            if held["message"] == str(news):
                del kept[i]
                break
        self._write(kept)

    def _keep(self, message, about, composed, listed=True, kind=""):
        kept = self._spooled()
        kept.append({"message": str(message), "about": about, "composed": bool(composed),
                     "listed": bool(listed), "kind": str(kind)})
        self._write(kept)

    def _spooled(self):
        if self._spool is None:
            return []
        try:
            import json

            return list(json.loads(self._spool.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return []  # an unreadable spool must not stop news from flowing

    def _write(self, kept):
        if self._spool is None:
            return
        try:
            import json

            self._spool.parent.mkdir(parents=True, exist_ok=True)
            self._spool.write_text(json.dumps(kept, indent=2), encoding="utf-8")
        except OSError:
            pass  # a failed spool write costs durability, never the delivery itself

    def __bool__(self):
        with self._lock:
            return bool(self._items)
