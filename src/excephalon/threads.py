"""What he is owed, in ONE place - the store the conversation reads instead of holding.

News used to wait in three places: the outbox's queue, the durable spool behind it, and the
conversation's own hand, which drained the queue and held items for a lull. Every cross-place
failure in this project's record lives in that split. A drop cleaned the queue while the copy in
hand was still offered ("surely there's no update for smart grouping. You just sent off the latest
message to it."). Newer news collapsed the older in memory while the spool kept it, and a restart
read yesterday's sentence out as fresh ("it comes out of nowhere and provides no new information").
The desk asked the queue who was owed news and could not see the walkthrough sitting in hand for
an hour, so the brain told him it had presented work he had never seen ("That's false. You never
presented it to me."). Three agents' reports died in a wedged process's hand, and the restarted
app had "no trace" of the updates it had been offering.

Here there is one store. A fact is owed from the moment it is recorded until the moment it is
SETTLED - spoken to him, on the mouth's own receipt - or dropped because he has moved past it.
Nothing is ever "drained" into anyone's keeping: the loop reads what is owed, speaks, and settles.
The rules that used to be applied in the hand are applied where the fact is written, once:

- One fact per thread. Newer news about a piece of work replaces the older, which nobody will
  now hear; every turn-end while he was away used to queue its own sentence and the roll call
  read the same name four times.
- A thread keeps its PLACE when its news is refreshed. Numbered, then refreshed, then re-read,
  the same three names once came back re-numbered seconds apart ("Now I don't know what to tell
  you") - so the newest fact sits where that thread's first one sat.
- An alarm never displaces a report. "Been silent for 20 minutes" is a timer's guess; one arrived
  after its agent's merge report and, being newest, destroyed it.
- What a thread ENDED as is never dropped as stale. A merge report is his last word on that work,
  and a boot sweep once threw one away unheard.

The store survives the process in the same file the outbox kept, so nothing owed is lost across
the upgrade - and a fact restored at boot is app-authored to whatever brain wakes up, because the
one that wrote it is gone.
"""

import json
import threading

# The kinds that END a work thread from where he sits: it merged, it came back from the landing
# saying what stopped it, or it died. These are the answers to "did my thing ship?", and they are
# the one class of news that may never be held back and may never be dropped unspoken.
CONCLUSIONS = frozenset(("landed", "landing", "died"))


class News(str):
    """One thing owed to him, and which piece of work it is about.

    A string, because everything downstream speaks it, joins it and matches on it - and the string
    is the app's own plain sentence, which is what is spoken if no better wording can be had and
    what survives a restart. What the one author actually needs rides beside it:

    `about` is the agent (a thread's key); `work` is the piece of work in HIS words, the only name
    he is ever told for it. `report` is the agent's own words - the author's INPUT at delivery,
    never its output. `stage` is where the work stood when the fact arrived. `kind` is the event
    it came from, because not every piece of news is worth the same. `listed` says this is an
    ITEM he may be asked to choose between by name - only an agent's news is; the errand hand and
    the memory inbox are the app's own machinery ("I don't even know what errands would be").
    `composed` says the brain wrote these exact words and remembers writing them; app-authored
    news stays False and is read back to it through the unwritten-lines ledger."""

    about = None
    work = ""
    report = ""
    stage = None
    composed = False
    listed = True
    kind = ""

    def __new__(cls, message, about=None, composed=False, listed=True, kind="", work="",
                report="", stage=None):
        news = super().__new__(cls, message)
        news.about = about
        news.work = work
        news.report = report
        news.stage = stage
        news.composed = composed
        news.listed = listed
        news.kind = kind
        return news

    @property
    def concluding(self):
        """Is this a thread's last word - it landed, it did not, or it died? Never held back and
        never dropped: holding one is the black hole this project has already sat through once."""
        return self.kind in CONCLUSIONS

    @property
    def alarm(self):
        """Is this only a timer's guess that something has gone quiet? Worth saying when there is
        nothing else to say about that agent, and worth nothing beside a real report."""
        return self.kind == "quiet"

    def _row(self):
        return {"message": str(self), "about": self.about, "composed": bool(self.composed),
                "listed": bool(self.listed), "kind": str(self.kind), "work": str(self.work),
                "report": str(self.report or ""), "stage": self.stage}

    @classmethod
    def _from_row(cls, row):
        # NOT composed, whoever wrote it: the brain that wrote it died with the last process, and
        # carried over as composed a restored line skipped the ledger and the new brain denied it
        # to his face ("I don't see that statement in our conversation").
        return cls(row["message"], row.get("about"), listed=row.get("listed", True),
                   kind=row.get("kind", ""), work=row.get("work", ""),
                   report=row.get("report", ""), stage=row.get("stage"))


class Ledger:
    """The one store of what he is owed. `spool` (a path) makes it survive the process."""

    def __init__(self, spool=None):
        self._facts = []
        self._lock = threading.Lock()
        self._spool = spool
        self._requested = set()  # threads whose held news the brain asked to deliver itself
        # Set when something NEW is owed; cleared when the loop has looked (`seen`). The window's
        # mic yields a delivery turn while this is set, so it must be cleared by looking, not by
        # speaking - a pass that decides not to speak yet clears it and, if it deferred for his
        # talking, puts it back up.
        self.arrived = threading.Event()
        for row in self._spooled():
            self._facts.append(News._from_row(row))
        if self._facts:
            self.arrived.set()

    # ---- writing -------------------------------------------------------------------------------

    def owe(self, message, about=None, composed=False, listed=True, kind="", work="", report="",
            stage=None):
        """Record one fact as owed, applying the rules above where the fact is written."""
        fact = (message if isinstance(message, News)
                else News(message, about, composed, listed, kind, work, report, stage))
        with self._lock:
            self._place(fact)
            self._write()
        self.arrived.set()
        return fact

    push = owe  # the name every producer has always called

    def _place(self, fact):
        """Put a fact in: threadless news simply joins the end; a thread's news takes that
        thread's existing place, and an alarm gives way to any real report already there."""
        about = fact.about
        if about is None:
            self._facts.append(fact)
            return
        at = next((i for i, held in enumerate(self._facts) if held.about == about), None)
        if at is None:
            self._facts.append(fact)
            return
        standing = self._facts[at]
        if fact.alarm and not standing.alarm:
            return  # a timer's guess never displaces a report
        self._facts[at] = fact
        # Only one fact per thread - drop any further copies an older process may have left.
        self._facts = [held for i, held in enumerate(self._facts)
                       if i == at or held.about != about]

    # ---- reading -------------------------------------------------------------------------------

    def owed(self):
        """Everything still owed, in first-arrival order per thread. A snapshot: settle or drop
        what is spoken or moved past; never edit this list."""
        with self._lock:
            return list(self._facts)

    def owed_about(self):
        """Every thread still owed news."""
        with self._lock:
            return {held.about for held in self._facts}

    def held(self, about):
        """The fact owed about this thread, or None."""
        with self._lock:
            return next((held for held in self._facts if held.about == about), None)

    def seen(self):
        """The loop has looked at what is owed; new arrivals will raise the signal again."""
        self.arrived.clear()

    def __bool__(self):
        with self._lock:
            return bool(self._facts)

    def drain(self):
        """Take EVERYTHING owed as spoken, and answer with it - a test's way of saying "he heard
        it all". The loop never calls this: it reads `owed` and settles each fact on the mouth's
        receipt, which is the whole reason a hand no longer exists."""
        with self._lock:
            taken, self._facts = list(self._facts), []
            self._write()
            self.arrived.clear()
        return taken

    # ---- settling ------------------------------------------------------------------------------

    def settle(self, news):
        """This fact reached him (or newer news about its thread replaced it): it is no longer
        owed, in memory and on disk alike."""
        with self._lock:
            self._facts = [held for held in self._facts if not self._same(held, news)]
            self._write()
            if not self._facts:
                self.arrived.clear()

    spoken = settle
    superseded = settle

    @staticmethod
    def _same(held, news):
        """The same fact: the same words about the same thread. A bare string names a fact by its
        words alone, which is how the older callers have always settled one."""
        if str(held) != str(news):
            return False
        about = getattr(news, "about", None)
        return about is None or held.about == about

    def drop(self, about, keep_conclusions=False):
        """Forget what is owed about one thread - it is finished with, or he has moved past it -
        sparing, when asked, the one class that is never stale: what the thread ENDED as."""
        with self._lock:
            self._facts = [held for held in self._facts
                           if held.about != about
                           or (keep_conclusions and held.concluding)]
            self._write()
            if not self._facts:
                self.arrived.clear()

    def take_dropped(self):
        """Nothing to collect: there is no hand to prune. Kept so an older loop still runs."""
        return set()

    def retag(self, about, to):
        """News about a renamed agent is about the same agent, under his name for it now."""
        with self._lock:
            self._facts = [News(str(held), to, held.composed, held.listed, held.kind, held.work,
                                held.report, held.stage) if held.about == about else held
                           for held in self._facts]
            self._write()

    def request(self, about):
        """The brain is delivering this thread's held news in its own reply; the loop checks
        that reply carried it, and settles it if so."""
        with self._lock:
            self._requested.add(about)

    def take_requested(self):
        with self._lock:
            taken, self._requested = self._requested, set()
            return taken

    # ---- the file ------------------------------------------------------------------------------

    def _spooled(self):
        if self._spool is None:
            return []
        try:
            return list(json.loads(self._spool.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return []  # an unreadable file must not stop news from flowing

    def _write(self):
        if self._spool is None:
            return
        try:
            self._spool.parent.mkdir(parents=True, exist_ok=True)
            self._spool.write_text(json.dumps([held._row() for held in self._facts], indent=2),
                                   encoding="utf-8")
        except OSError:
            pass  # a failed write costs durability, never the delivery itself
