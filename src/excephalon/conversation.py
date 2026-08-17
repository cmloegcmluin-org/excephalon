import inspect
import re
import sys
import threading
import time
from dataclasses import dataclass

from excephalon.console import Console
from excephalon.homecoming import offers_a_choice
from excephalon.links import as_spoken
from excephalon.phrases import canonical as _canonical
from excephalon.phrases import ends_with_command as _ends_with_command
from excephalon.phrases import wakes as _wakes
from excephalon.sdk_session import needs_sign_in
from excephalon.voice import UNSAID, Receipt
from excephalon.waiting import chosen, roll_call

# Both names, as the window's wake phrases already carried both: the app says "Excephalon" every
# time it names itself, so that has to be the word that works - and the coined one is the word the
# transcriber least reliably lands, which is the whole reason the plain one stays beside it.
DEFAULT_FAREWELLS = (
    "goodbye excephalon",
    "goodnight excephalon",
    "goodbye entity",
    "goodnight entity",
    "that's all for now",
    "quit",
    "exit",
)
# "Stop listening" doesn't quit - it puts Excephalon to sleep so it stops responding; "hey
# excephalon" wakes it. (While asleep it still transcribes, only to catch the wake word - nothing
# reaches the brain.)
DEFAULT_SUSPENDS = ("suspend", "stop listening")
DEFAULT_RESUMES = ("resume", "hey excephalon", "hey entity")
DEFAULT_FAREWELL_REPLY = "Be seeing you."
# What they hear when the brain call fails: a plain sentence, no cause. The cause used to be IN
# the sentence (stderr goes nowhere under pythonw, and an unexplained failure "has never said
# that and recovered") - but that read "_AskWedged" to the user aloud, a code identifier straight
# through the insulation. The cause now goes to the session record instead (console.evidence):
# durable, diagnosable, and never spoken or shown.
DEFAULT_ERROR_REPLY = "Something's broken in my head - give me a moment, then ask me again."
# The one brain failure the user can fix and a restart cannot: the machine's Claude sign-in died.
# "Ask me again" was said about exactly this, he restarted on that advice, and met the same wall
# ("Something is broken in Excephalon's head right now, even after a restart"). Plain steps, no
# jargon: he is not technical outside code, and this is the whole of what there is to do.
DEFAULT_SIGN_IN_REPLY = (
    "I can't reach my mind, and a restart won't fix this one: this machine's Claude sign-in has "
    "expired. Open a terminal, type claude and press Enter, then type /login and press Enter, "
    "and sign in in the browser window that opens. Then restart me."
)
# When the app could open that terminal itself - "ideally Excephalon should do more than just
# tell me what to do, but pop open whatever I need to do it and run it itself if possible" -
# the reply matches what actually happened: the door is open, the signing in is his.
DEFAULT_SIGN_IN_OPENED_REPLY = (
    "I can't reach my mind: this machine's Claude sign-in has expired, and a restart won't fix "
    "it. I've opened a terminal for you - type /login there if it isn't already offering the "
    "sign-in, finish it in the browser window that opens, and then restart me."
)
# They ended a turn ("over") but said nothing in it. Rather than ignore them - which just makes them
# repeat "over" wondering if they were heard - acknowledge that the turn registered and invite them on.
DEFAULT_EMPTY_TURN_REPLY = "Go ahead."
DEFAULT_SUSPEND_REPLY = "Resting. Say 'hey Excephalon' when you want me back."
DEFAULT_RESUME_REPLY = "Back with you."

# Said back to the brain on the turn AFTER anything was spoken in its name that it did not write -
# an agent's notice, a roll call, a canned confirmation.
#
# The user hears ONE Excephalon. The brain only ever knew the half of it that it composed, so they could
# quote a line at it and be told, truthfully, "I have no record of typing that myself" - which from
# where they sit is a thing that said something and then denied saying it. Their words: "a basic
# principle of two people having a conversation is that each person is aware of the things that
# they've said. If what I consider to be one Entity is actually a bunch of disconnected fakers who
# aren't aware of each other, then the flimsy occasional illusion of you being a coherent Entity is
# worse than useless."
UNWRITTEN_NOTICE = (
    "[System note, not from the user: since your last reply, these lines were spoken to them in "
    "YOUR name by the app rather than composed by you. They experienced every one of them as you "
    "talking, and may refer to them as things you said - so own them and answer accordingly, and "
    "never tell them you have no record of saying something they heard you say:\n{lines}]\n\n"
)

# The live state of the fleet, put in front of the brain at the top of every turn by code. This is
# what lets "how's it going" be answered in the breath it was asked: the old brain went off to read
# the roster file with its own tools - thirty seconds to fifteen minutes of dead air for state the
# process already held.
STANDING_NOTICE = (
    "[System note, not from the user: their standing context has CHANGED since this conversation "
    "started. What follows is the current text and it replaces what you were told at the start - "
    "never answer from the older version, and never tell them you cannot see something that is "
    "here:\n\n{standing}]\n\n"
)

SILENT_TURN_NOTICE = (
    "[System note, not from the user: your last turn produced no words at all - whatever you "
    "did with your tools, they heard NOTHING back, which reads as the app being broken. Answer "
    "their words below now, in one short sentence, saying what you did or where it stands. Call "
    "no tools this turn; the work is already done.]\n\n"
)

BRIEFING_NOTICE = (
    "[Fleet briefing, from the app - the live state of your agents as of this turn. Where a "
    "piece of work stands comes from HERE, never from your memory of the conversation: work "
    "this briefing calls DELIVERED is finished - never re-offer it for review or approval; "
    "work it does not call delivered is never called shipped or done; an item on his lists "
    "with no agent on it is not started yet. When your memory and this briefing disagree, "
    "the briefing is what is true:\n{briefing}]\n\n"
)

# Conduct that rode only in the persona and lost to habit by mid-session - each line below was
# banned once, recurred live, and had to be corrected by the user again. A rule read at session
# start fades; this one is in front of the brain on every turn, where the failure happens.
CONDUCT_NOTICE = (
    "[Standing conduct, every turn: act first - never say what you are about to do with a tool; "
    "call it, then say ONCE what you did, and never restate the same fact in different words in "
    "one reply. Never open with 'You're right' or 'You're absolutely right' or any agreement "
    "reflex - answer the substance instead. Speak only of "
    "what happened THIS turn - no running totals that mix in old work. No internal vocabulary "
    "to them, ever: tool names, stage words, routing words - plain words or nothing. NAMED, "
    "because the category was not enough: never 'the desk', 'the fleet', 'the outbox', 'the "
    "roster', 'marked ready', 'the delivery stage', 'narrated'. Say 'your agents', 'the "
    "list', 'ready for you to look at'. If they ask what one of those words meant, say "
    "plainly that it was your jargon and what you actually meant - and if you do not know, "
    "say that instead of inventing a definition. An agent's own mechanics are internal too: "
    "'its tree was clean', branch names, rebases, worktrees - never relay them; say the "
    "OUTCOME in their terms. Crashes, restarts and retries the app already handled are "
    "machinery, not news - work in progress is 'still working on it', and what a crash cost "
    "is said only if they ask why something is slow. And a question they ASK is answered in "
    "your first sentence, before any fix or apology: an apology is not an answer, and they "
    "have had to ask the same question twice. Never send "
    "them to read an agent's log or tab: the logs are yours to read, not theirs - say the "
    "substance yourself, or have the agent present its work properly. And when "
    "you do not KNOW, say you don't know - never guess: a guess in knowledge's voice sends them "
    "chasing a fiction. And when they ask you to FIX something, the behavior they describe is "
    "the disease, not the wish - if a fix's direction is at all ambiguous, say the change back "
    "in one short sentence ('so same-named groups CAN combine - yes?') and wait for the yes "
    "before dispatching.]\n\n"
)

# While the brain thinks, re-check this often for a barge-in, so cutting a slow think off feels
# instant rather than waiting out the next check-in.
# How many of HIS turns the one-thing-at-a-time gate may hold everything else for. A review is
# closed by a verdict, and a verdict that never got recorded held every other thread's news
# forever - including a merge report he was waiting on. Two turns is generous for "he is looking
# at it right now" and short enough that a missed record can never bury the fleet.
REVIEW_HOLDS_TURNS = 2

DEFAULT_INTERRUPT_POLL = 0.05
# After telling the brain to cancel, wait up to this long for the call to actually unwind before
# moving on - so the loop never starts a second brain call overlapping a half-cancelled one.
DEFAULT_CANCEL_WAIT = 10.0

# The longest he may be left in SILENCE after speaking. The brain bounds each of its own asks at
# 180s, but one turn can spend several - lock, shed, reconnect, ask again - and his turn went
# twelve minutes with no word at all ("it seems to be stuck again"). This bound is his, not the
# brain's: past it the turn stops waiting and says so. It only ever fires while nothing has
# reached the air, so a reply already sounding is never cut off. 0 disables it.
DEFAULT_ANSWER_WITHIN = 90.0

# After a reply, wait this long before listening again, so they get a beat to read it rather than the
# mic reopening the instant the voice stops. 0 disables (default; the app turns it on for voice runs).
DEFAULT_READ_PAUSE = 0.0

# With no word from the user for this long, they are off doing something else - and news breaking
# in "out of nowhere" is a jolt. Dormant, the news is OFFERED instead of read: one line naming who
# it is about, and the content waits until they engage, so they decide when to stop and listen.
DEFAULT_DORMANT_AFTER = 180.0

# How the offer is worded. App-authored (the ledger reads it back to the brain), because it must
# be sayable even while the brain is mid-something-else.
UPDATE_OFFER = "I've got an update on {what} when you're ready."

# A bare go-ahead with SEVERAL updates held reads the numbered choice out. Exact matches only:
# "okay" mid-sentence is them talking, not them asking for the list. With ONE update held, no
# magic words are needed at all - whatever they say next carries the update into that reply.
_GO_AHEADS = frozenset((
    "ok", "okay", "yes", "yeah", "yep", "sure", "ready", "go ahead", "go for it", "alright",
    "hit me", "im ready", "i m ready", "lets hear it", "let s hear it", "go",
))

# The held update, put in front of the brain on the turn that answers the offer. Delivering the
# STORED line as well was the repeat he heard: "Yeah, let me know" missed the exact go-ahead list,
# the brain improvised the news from memory, and the stored sentence then played anyway.
OFFERED_NOTICE = (
    "[System note, not from the user: you told them an update on {about} was waiting, and they "
    "have now answered. Immediately after your reply, in the same breath, the app itself will "
    "speak the update word for word - so answer what they just said in a sentence, and do NOT "
    "restate, summarize, or answer the update's content yourself. For your awareness only, the "
    "words the app will add: {news}]\n\n"
)


def _goodbye_sentence(farewell):
    """The app's closing line as a STANDALONE sentence, wherever it lands in a reply - never the
    same words inside a longer one ("I'll be seeing you at the demo" is the brain talking).
    Substitute with the first group to keep the sentence boundary the match consumed."""
    core = re.escape(farewell.rstrip(".!?… ").strip())
    return re.compile(r"(^|[.!?…]\s+|\n\s*)" + core + r"(?:[.!?…]+\s*|\s*$)", re.IGNORECASE)


class _FarewellGate:
    """Forwards a streamed reply to the voice, dropping any sentence that is the goodbye.

    The goodbye is the app's own closing line; the brain writing it mid-conversation is a
    misfire the user had to correct out loud ("Wait, why did you say be seeing you? I thought
    you only say that when I'm closing you.") - and the standing instruction filed against it
    is exactly the duty-shaped rule the fast tier keeps missing. So the code holds the door.
    Text is released a completed sentence at a time - which costs the voice nothing, since it
    already synthesizes only completed sentences - and a sentence that IS the goodbye is
    dropped instead of fed to it. Mid-sentence uses ("be seeing you around") pass untouched:
    only the standalone closing line is the misfire."""

    _SENTENCE_END = re.compile(r"(?<=[.!?…])")

    def __init__(self, forward, farewell):
        self._forward = forward
        core = re.escape(farewell.rstrip(".!?… ").strip())
        self._goodbye = re.compile(r"\W*" + core + r"[.!?…]*\W*", re.IGNORECASE)
        self._held = ""

    def feed(self, piece):
        *done, self._held = self._SENTENCE_END.split(self._held + piece)
        for sentence in done:
            self._pass(sentence)

    def flush(self):
        """The reply is complete: whatever is still held is its last (unterminated) sentence."""
        held, self._held = self._held, ""
        self._pass(held)

    def _pass(self, sentence):
        if sentence and not self._goodbye.fullmatch(sentence):
            self._forward(sentence)


class _PastedReportGate:
    """Forwards a streamed reply onward, dropping any line that is pasted material.

    A reply is SPEECH, and speech has no blockquote: a line opening with ">" is a document
    quoted into the mouth. The brain once relayed an agent's whole markdown report inside a
    reply - launcher link, numbered steps, sign-off - and he stopped it mid-word: "Whoa whoa
    whoa... That's like ten times bigger than I ever want you to send a message to me." The
    held news the app welds on is composed by the narrator and carries no quote marks, so
    nothing legitimate is lost. Line-buffered, because the quote marker is a line's first
    character and a delta can end anywhere."""

    def __init__(self, forward):
        self._forward = forward
        self._held = ""

    def feed(self, piece):
        *done, self._held = (self._held + piece).split("\n")
        for line in done:
            self._pass(line + "\n")

    def flush(self):
        """The reply is complete: whatever is held is its last (unterminated) line."""
        held, self._held = self._held, ""
        if held:
            self._pass(held)

    def _pass(self, line):
        if not line.lstrip().startswith(">"):
            self._forward(line)


# The same rule for text already in hand: the record must match the ear, so what the stream
# gate drops is dropped from the kept reply too (and from a non-streamed one wholesale).
_PASTED_LINE = re.compile(r"(?m)^[ \t]*>[^\n]*\n?")

def _without_pasted_report(said):
    kept = _PASTED_LINE.sub("", said)
    return kept if kept.strip() else said  # a reply that was ALL paste still answers with itself


class _ThinkInterrupted(Exception):
    """Internal signal that a barge-in cancelled the brain call - the turn is abandoned and the
    loop goes straight back to listening, with no reply and no error spoken."""


class _ThinkTooSlow(Exception):
    """His wait ran out with nothing said. Deliberately an ordinary exception: the turn's existing
    brain-failure path already keeps the update owed, records the cause and speaks a plain line -
    which is exactly what a silent wedge deserves."""


def _cause(exc, depth=3):
    """What broke, and what broke underneath that.

    A library's top exception is often its GUESS at the cause: the agent SDK raises "Claude Code not
    found at <path>" for ANY FileNotFoundError while spawning, so it blamed the CLI while the CLI sat
    there untouched - and that guess was the whole of what there was to go on. Python chains the real
    error underneath, and it knows which file. Bounded, because past the first few links a chain is
    library plumbing rather than anything about what went wrong.
    """
    links, seen = [], set()
    while exc is not None and len(links) < depth and id(exc) not in seen:
        seen.add(id(exc))
        detail = f"{type(exc).__name__}: {exc}"
        missing = getattr(exc, "filename", None)
        if missing and str(missing) not in detail:
            detail += f" ({missing})"
        links.append(detail)
        exc = exc.__cause__ or exc.__context__
    return ", caused by ".join(links)


def _newest_per_agent(waiting):
    """`waiting` as (what to keep, what newer news about the same agent has superseded).

    Every turn-end while the user was away queued its own narration, and the roll call then read
    the same name four times - a list with no choice in it. The newest sentence about an agent
    already says where things stand; the ones they never heard are history. News with no agent
    on it (about=None) is never collapsed - those are not updates on one thing.

    The newest sentence takes the agent's EARLIEST place in the list, not its own arrival place.
    Kept where it arrived, a refresh moved that agent to the end and the numbered list came out
    reordered seconds after it was read - "Why did you give me two occurrences of three updates
    waiting, but order them differently? Now I don't know what to tell you." An agent holds its
    number for as long as it stays on the list.

    An ALARM never displaces a report. "Been silent for 20 minutes" is a timer's guess; it
    arrived twenty minutes after that agent's merge report and, being newest, superseded it -
    so the one thing he was waiting for was destroyed by a warning about the very agent that
    had already finished, and its durable copy went with it. Where an agent has real news, its
    alarms are dropped instead.

    What is superseded comes back to the caller because dropping it here is only half the job:
    its durable copy has to go too, or the next restart reads yesterday's sentence out as news."""
    real = {getattr(item, "about", None) for item in waiting
            if not getattr(item, "alarm", False)}
    waiting = [item for item in waiting
               if not (getattr(item, "alarm", False) and getattr(item, "about", None) in real)]
    newest = {}
    for item in waiting:
        about = getattr(item, "about", None)
        if about is not None:
            newest[about] = item
    keep, placed = [], set()
    for item in waiting:
        about = getattr(item, "about", None)
        if about is None:
            keep.append(item)
        elif about not in placed:
            placed.add(about)
            keep.append(newest[about])
    gone = [item for item in waiting
            if (about := getattr(item, "about", None)) is not None and item is not newest[about]]
    return keep, gone


def _receipt(answer, text="", cut=False):
    """A mouth's answer as a Receipt, whatever shape of mouth answered.

    Every voice this app ships receipts its own utterance now. A mouth written before the receipt
    existed answers None, and a streamed one answered the text that sounded; both still mean what
    they always meant, so both are read here rather than at five call sites each with its own
    guess. That spread is what let a barge-in-silenced line count as delivered: the question "did
    he hear it?" had five answers and the wrong one spent a merge report over zero audio.

    `cut` is the loop's own view of the interrupt, and stands in only for a mouth that does not
    report one - the answer this code has always used for the "(cut off mid-utterance)" note."""
    if isinstance(answer, Receipt):
        return answer
    said = str(answer if isinstance(answer, str) else text).strip()
    return Receipt(began=bool(said), said=said, cut=bool(cut))


def _accepts(brain, keyword):
    """Whether this brain's respond() takes `keyword`.

    Checked once rather than per call, so a brain fake with the plain signature runs the plain
    path instead of blowing up mid-turn."""
    try:
        return keyword in inspect.signature(brain.respond).parameters
    except (TypeError, ValueError):
        return False


def _accepts_streaming(brain):
    """Whether this brain can hand text out as it is written (an `on_text` keyword)."""
    return _accepts(brain, "on_text")


@dataclass(frozen=True)
class Turn:
    heard: str
    said: str
    farewell: bool = False
    error: bool = False


class Conversation:
    """Ties speech-to-text, a brain, and text-to-speech into a listen -> think -> speak loop.

    The shape of a turn is: their words go to the brain with the fleet briefing in front of them,
    and the reply is SPOKEN AS IT IS WRITTEN - each sentence sounding while the next is still
    forming - so the wait for first words is the model's first sentence, not the whole turn plus a
    stack of stock phrases. The stock phrases are gone: no acknowledgement line, no "I'll get back
    to you on that", no "ready for it?" gate, no cut at 260 characters. What remains unprompted is
    agent news at a lull, and a barge-in (Enter, or a spoken stop) that silences everything at once.
    """

    def __init__(
        self,
        stt,
        brain,
        tts,
        *,
        farewells=DEFAULT_FAREWELLS,
        suspends=DEFAULT_SUSPENDS,
        resumes=DEFAULT_RESUMES,
        farewell_reply=DEFAULT_FAREWELL_REPLY,
        error_reply=DEFAULT_ERROR_REPLY,
        sign_in_reply=DEFAULT_SIGN_IN_REPLY,
        sign_in_opened_reply=DEFAULT_SIGN_IN_OPENED_REPLY,
        sign_in_helper=None,
        suspend_reply=DEFAULT_SUSPEND_REPLY,
        resume_reply=DEFAULT_RESUME_REPLY,
        empty_turn_reply=DEFAULT_EMPTY_TURN_REPLY,
        interrupt_poll=DEFAULT_INTERRUPT_POLL,
        cancel_wait=DEFAULT_CANCEL_WAIT,
        read_pause=DEFAULT_READ_PAUSE,
        answer_within=DEFAULT_ANSWER_WITHIN,
        dormant_after=DEFAULT_DORMANT_AFTER,
        console=None,
        sleep=time.sleep,
        clock=time.monotonic,
        timings=False,
        outbox=None,
        interrupt=None,
        briefing=None,
        standing=None,
        opening="",
        in_review=None,
        review_opens=None,
    ):
        self._stt = stt
        self._brain = brain
        self._tts = tts
        self._farewells = frozenset(_canonical(f) for f in farewells)
        self._suspends = frozenset(_canonical(s) for s in suspends)
        self._resumes = frozenset(_canonical(r) for r in resumes)
        self.farewell_reply = farewell_reply
        # The closing line as a stray sentence in an ordinary reply - the misfire the gate and
        # the record-scrub both remove, so it is only ever heard when the app itself closes.
        self._stray_goodbye = _goodbye_sentence(farewell_reply)
        self.error_reply = error_reply
        self.sign_in_reply = sign_in_reply
        self.sign_in_opened_reply = sign_in_opened_reply
        # Opens a terminal at the claude prompt when the sign-in is dead (sdk_session.open_sign_in,
        # wired in __main__); None - the default, and every test - only recites the steps, so no
        # suite run ever pops a console.
        self._sign_in_helper = sign_in_helper
        self.suspend_reply = suspend_reply
        self.resume_reply = resume_reply
        self.empty_turn_reply = empty_turn_reply
        self._unwritten = []  # lines spoken in its name that it didn't compose; told to it next turn
        self._waiting = []  # news drained from the outbox and not delivered yet
        # The session's FIRST line, said through this one mouth rather than beside it. Spoken
        # outside the loop, it was one of two messages he got thirteen seconds apart at startup -
        # a welcome asking about one update, then the app offering all three ("I just opened
        # Excephalon and then it quickly sent me two messages. it should only have sent me one").
        self._opening = opening
        self._requested = set()  # agents whose held news the brain asked spoken (deliver_update)
        self._announced = ()  # the news the roll call last read out, so fresh news re-reads
        self._clock = clock
        self._dormant_after = dormant_after
        self._last_engaged = clock()  # startup counts: they just launched it, so they are here
        self._update_offered = False  # a dormant-lull offer stands; the news waits to be taken
        self._briefing = briefing  # callable: the live fleet state, put before the brain each turn
        # callable: his standing context, but only the parts that have CHANGED since the brain was
        # last told - "" on a turn where nothing of his has moved.
        self._standing = standing
        self._brain_streams = _accepts_streaming(brain)
        # Whether the brain can report that the model is still alive while it writes no words -
        # a tool call in flight. Without it, a turn spent doing what he asked looks exactly like
        # a turn that died, and his wait below cuts the live one off.
        self._brain_reports_work = _accepts(brain, "on_activity")
        self._interrupt_poll = interrupt_poll
        self._cancel_wait = cancel_wait
        self._read_pause = read_pause  # a beat after a reply so they can read it before listening resumes
        self._answer_within = answer_within  # how long he may be left in silence before the app speaks
        self._console = console or Console()
        self._sleep = sleep
        self._timings = timings  # --timings: show how long each turn spent thinking vs. speaking
        self._outbox = outbox
        self._interrupt = interrupt  # set (e.g. by a keypress) to cut off whatever it's saying
        self._paused = False
        self._floor_watched = False  # true while a stop-watcher already holds the mic (see _say)
        # One thing at a time - his standing instruction, made the loop's own rule. `in_review`
        # answers which agents' work is in front of his eyes RIGHT NOW (walkthrough spoken, no
        # verdict yet); while any is, other agents' news holds and no menu is read. `review_opens`
        # answers whether one piece of news IS a walkthrough - delivered, it opens a review, so
        # no menu rides its back ("Don't ask me about updates for other items when we've already
        # picked one of them to be working on").
        self._in_review = in_review or (lambda: ())
        self._review_opens = review_opens or (lambda name: False)
        self._reviewing = set()  # whose work the gate is currently holding everything else for
        self._review_turns = 0  # his turns since it opened - the bound on how long it may hold

    def _interrupted(self):
        return self._interrupt is not None and self._interrupt.is_set()

    def _remember_spoken(self, text):
        """Tell the brain this line of its own actually reached him.

        Composing is not delivering, so the lines it writes for the app to speak - agent news, the
        session's first line - are kept out of the window a compaction or a restart rebuilds the
        conversation from until they have sounded. This is what puts them in, and only what
        sounded goes in: what it remembers saying is then what he remembers hearing."""
        recorded = getattr(self._brain, "spoke", None)
        if recorded is not None and str(text or "").strip():
            recorded(text)

    def _retract(self, text):
        """Tell the brain this line of its own will never be spoken.

        The live session already holds every draft it wrote, where the only reading available is
        that it was said - so a line the app drops (overtaken, dropped as stale, a greeting that
        retold the news behind it) has to be taken back, or the model holds a sentence he never
        heard and reasons from it. That is the duplicate class from the inside: a draft dropped
        for a plain notice leaves it holding the draft AND hearing the notice."""
        take_back = getattr(self._brain, "retract", None)
        if take_back is not None and str(text or "").strip():
            take_back(text)

    def _say(self, text, *, record=True, known=False):
        """Speak, unless they've cut in. Once the interrupt is set, every later line this turn stays
        unsaid, and a line already in progress is killed by the TTS. While it speaks, a background
        watcher listens for them saying "stop", which trips the same interrupt - so they can cut it off
        by voice, not just the Enter key. When a watcher already holds the mic, we don't open a second
        one - two readers on one mic corrupt each other. A voice hiccup is logged, not fatal - a
        failed utterance must never crash the loop (it did, and they lost the whole run)."""
        if self._interrupted():
            self._console.aside("(left unsaid - they had cut in)")
            return UNSAID  # nothing sounded: the caller must not spend what it never said
        if not known:
            # They are about to hear this as Excephalon speaking, and the brain did not write it -
            # so the brain has to be told, or the two of them remember different conversations.
            self._unwritten.append(text)
        if record:  # a line already printed records itself; this is for the ones only they hear
            self._console.spoke(text)
        stop_watching = (None if self._floor_watched
                         else self._watch_for_spoken_stop(script=lambda: as_spoken(text)))
        try:
            # Said, not written: an address becomes "the link" and a path becomes its filename. The
            # line above already showed the real thing, which is what they read and clicks - this is
            # only the difference between what is on the screen and what a person would say aloud.
            sounded = _receipt(self._tts.speak(as_spoken(text), interrupt=self._interrupt),
                               text, cut=self._interrupted())
        except Exception as exc:  # a failed utterance must never crash the loop - but it IS evidence
            self._console.aside(f"(voice failed: {exc!r})")
            return UNSAID  # the hiccup, not the news, is what dies: nothing is spent on it
        else:
            if sounded.cut:  # the utterance was killed partway - the record must say so,
                self._console.aside("(cut off mid-utterance)")  # or a silenced line looks delivered
        finally:
            if stop_watching is not None:
                stop_watching()
        # The receipt is what anything owed is spent on: a mid-cut is his deliberate stop and still
        # counts as heard, while an utterance a barge-in beat to its first word counts as nothing.
        return sounded

    def _speak_reply(self, text, *, known=False):
        """Show the reply, then say it - the same words on screen as in their ear. Answers with
        the Receipt, so a delivery welded to it is only spent when it actually sounded."""
        self._console.reply(text)
        return self._say(text, record=False, known=known)

    def _pause_to_read(self):
        """A short beat after a reply before the mic reopens, so they aren't rushed off it - skipped if
        they've barged in (they're cutting in, not reading)."""
        if self._read_pause > 0 and not self._interrupted():
            self._sleep(self._read_pause)

    def _watch_for_spoken_stop(self, script=None, audio=None):
        """If the mic can catch a spoken stop word, listen for one for as long as we're speaking and
        set the interrupt when it lands. Returns a callable that stops and joins the watcher (so the
        mic is free again before the next listen), or None when voice-stop isn't available.

        A mic whose catch_stop can take them is also handed `script` (the words being spoken, so it
        can tell Excephalon's own leak from someone talking over it) and `audio` (whether sound is
        in the air at all - while the brain merely thinks, the ear stays open). A mic with the bare
        signature gets the bare call, unchanged."""
        catch_stop = getattr(self._stt, "catch_stop", None)
        if catch_stop is None or self._interrupt is None:
            return None
        extras = {}
        try:
            supported = inspect.signature(catch_stop).parameters
        except (TypeError, ValueError):
            supported = {}
        if script is not None and "script" in supported:
            extras["script"] = script
        if audio is not None and "audio" in supported:
            extras["audio"] = audio
        speaking = threading.Event()
        speaking.set()

        def watch():
            try:
                if catch_stop(speaking.is_set, **extras):  # they said "stop" while it was talking
                    self._interrupt.set()
            except Exception as exc:
                print(f"[voice-stop error] {exc!r}", file=sys.stderr)

        thread = threading.Thread(target=watch, daemon=True)
        thread.start()

        def stop():
            speaking.clear()  # the reply's done (or was cut) - let the watcher release the mic
            thread.join(timeout=1.5)

        return stop

    def _hold_the_floor(self, script=None, audio=None):
        """One stop-watcher held for a whole turn - think plus every sentence of streamed audio -
        so a spoken "stop" lands whenever it comes, without two readers ever sharing the mic.
        Returns a release callable; safe to call once whichever way the turn ends."""
        stop_watching = self._watch_for_spoken_stop(script=script, audio=audio)
        self._floor_watched = stop_watching is not None

        def release():
            self._floor_watched = False
            if stop_watching is not None:
                stop_watching()

        return release

    def _deliver_outbox(self):
        """Say what Excephalon has queued to say on its own - word from an agent.

        ONE agent's news is spoken as it lands. SEVERAL arriving together are read out numbered and
        then held, because run into one utterance they arrived as a wall. When several are ready,
        say which, and let the order be chosen; whichever is named is spoken then (see `_take_pick`).

        Whatever goes out goes out as ONE utterance, so a single stop silences all of it; they had
        to hit stop over and over while a report came at them line by line. It waits while they are
        recording, because it once broke in mid-sentence while they were talking.
        """
        if self._outbox is None:
            return self._say_opening()
        # A drop (the agent was retired, or the user just engaged it with new words) cleans the
        # queue and the spool, but not the news already drained into this hand - that copy was
        # still offered after he had sent the agent fresh instructions ("surely there's no update
        # for smart grouping. You just sent off the latest message to it."). Pruned BEFORE the
        # drain, so news arriving after a drop is never mistaken for what the drop meant.
        collect = getattr(self._outbox, "take_dropped", None)
        dropped = collect() if collect is not None else ()
        for stale in [held for held in self._waiting
                      if getattr(held, "about", None) in dropped]:
            self._waiting.remove(stale)
            self._console.evidence(f"(dropped as stale for {stale.about}: {stale})")
            if getattr(stale, "composed", False):
                self._retract(str(stale))  # its own sentence, now never to be spoken
        # ALWAYS drain, even when it can't be said yet. The queue's "something is waiting" flag is
        # what makes the window's mic yield an empty turn, and it is only cleared by draining - so
        # returning early with it still set spun the loop forever and swallowed every submission they
        # made. Held news waits here instead, in hand, and goes out at the next opportunity.
        fresh = self._outbox.drain()
        self._waiting.extend(fresh)
        for arrived in fresh:
            # Never shown or spoken - but a piece of news that goes wrong later is undiagnosable
            # without it: "why is this happening?" could not be answered from the record, because
            # news that is never spoken leaves no trace once its spool entry is gone.
            self._console.evidence(f"(holding for {getattr(arrived, 'about', None) or 'no agent'}: "
                                   f"{arrived})")
        self._waiting, superseded = _newest_per_agent(self._waiting)
        for stale in superseded:
            self._superseded(stale)  # its durable copy goes with it, or a restart revives it
        # What the brain asked spoken (deliver_update) - collected every pass, kept until its
        # item can go out, and pruned to what is actually held, so a request for news that has
        # since been dropped dies quietly instead of waiting forever.
        wanted = getattr(self._outbox, "take_requested", None)
        if wanted is not None:
            self._requested |= wanted()
        self._requested &= {getattr(held, "about", None) for held in self._waiting}
        if not self._waiting:
            self._announced = ()  # nothing outstanding, so the next single item is simply spoken
            self._update_offered = False
            if not self._they_are_talking():  # never break in mid-sentence, greeting included
                self._say_opening()
            return
        if self._they_are_talking():
            # Deferred, not dropped - and the flag goes back up, or the defer is forever: the
            # mic only yields a delivery turn when the outbox says something is waiting, this
            # pass already drained that flag, and news deferred here once sat silent for nine
            # minutes until he asked for it himself ("I keep having to prompt this thing for
            # updates. one of its main purposes is to share these updates with me ASAP").
            still_owed = getattr(self._outbox, "arrived", None)
            if still_owed is not None:
                still_owed.set()
            return
        if self._opening:
            # The first line is a thread of its own and it ends by ASKING him something. Welded to
            # a held update he got the question AND the other thread's whole walkthrough in one
            # breath, so neither could be answered: "it insanely asks me if I'd like to continue
            # with a calendar demo, then in the same breath tells me that a demo for a feature an
            # agent has been working on in the background is ready for my review, and moreover, it
            # just goes straight into the detailed information about that feature." What he asked
            # for instead is the choice - pick the thread back up, or hear the update - and the
            # greeting is composed knowing news waits and ends on exactly that question
            # (homecoming_note). So the first line goes out alone, and it IS the offer: what is
            # waiting stays waiting until he says which he wants.
            #
            # The latch is set only if the line actually ASKED. A first line composed before this
            # news arrived never offered anything, and latching on it would leave the update
            # sitting behind a choice he was never given - so that one is simply the first line,
            # and the news takes the next opening as its own utterance.
            asked = offers_a_choice(self._opening)
            if self._say_opening() and asked:
                self._update_offered = True
            return
        # Unlisted news is not an item to choose between - the errand hand is machinery, not an
        # agent with a tab and a verdict, and reading its tag out as a name beside a real agent
        # cost him five turns trying to close a "task" that never existed ("I don't even know
        # what errands would be"). It is simply said, at the first opening, and the list behind
        # it is whatever the AGENTS are still owed.
        loose = next((at for at, held in enumerate(self._waiting)
                      if not getattr(held, "listed", True)), None)
        if loose is not None:
            self._speak_held(loose, name_the_rest=False)
            return
        place = next((at for at, held in enumerate(self._waiting)
                      if getattr(held, "about", None) in self._requested), None)
        if place is not None:
            # The brain handed this update over rather than retelling it: the app speaks the held
            # copy word for word the moment the reply ends - one teller, the exact words, instead
            # of two versions of the same news 13 seconds apart.
            self._requested.discard(getattr(self._waiting[place], "about", None))
            self._speak_held(place)  # he asked for this one, by name, just now
            return
        reviewing = self._still_reviewing()
        if reviewing:
            # One thing at a time - his standing instruction, now the loop's own rule: his eyes
            # are on a piece of work, so no other agent's news breaks in and no menu is read
            # ("Don't ask me about updates for other items when we've already picked one of them
            # to be working on"). News about the work under review still flows - that is the
            # thread he is IN - plainly, with no roll call on its back. The instant his verdict
            # closes the review, this gate lifts and the held list is offered, which is exactly
            # the moment he had to ask for by hand ("Now would be a good time to ask about the
            # other two updates"). A thread's CONCLUSION is never held by it: a merge report is
            # the last word he is owed, and holding one is the black hole this project has
            # already sat through.
            place = next((at for at, held in enumerate(self._waiting)
                          if getattr(held, "about", None) in reviewing
                          or getattr(held, "concluding", False)), None)
            if place is not None:
                self._speak_held(place, name_the_rest=False)
            return
        if self._dormant():
            # They are off doing something else; news breaking in "out of nowhere" is a jolt.
            # One offer names who it is about, and the content waits for them to engage. The
            # offer counts as MADE only if it began sounding - latched on an utterance a
            # barge-in silenced, the offer was never heard and never repeated, and the news
            # behind it sat unreachable.
            if not self._update_offered:
                self._update_offered = bool(self._say(UPDATE_OFFER.format(what=self._whose_news())))
            return
        self._update_offered = False
        if self._announced:
            # A list has been read out and not worked through. Say it again only if it has changed,
            # or every trip round the loop would recite the same names at them - but changed means
            # the NEWS, not the count. Measured by count, an agent's fresh report replacing its own
            # older one left the tally at two and was never spoken: the presented work he was
            # waiting on sat silent for the rest of that session, and he closed the app still owed
            # it ("I never heard back again"). The re-read comes out with every agent still at the
            # number he first heard for it (see `_newest_per_agent`), so answering an older read-out
            # by number still names the agent he means.
            if self._announced != self._roll():
                self._announce()
            return
        if len(self._waiting) > 1:
            self._announce()
            return
        self._speak_held(0)  # the one item left: said as itself, through the one delivery path

    def _delivered(self, news):
        """Tell the outbox this news actually reached the user, so its durable copy is done.
        Draining is not delivery: three agents' reports once sat drained-in-hand when the brain
        wedged and the user restarted, and the restarted app had no trace of what it still owed."""
        spoken = getattr(self._outbox, "spoken", None)
        if spoken is not None:
            spoken(news)

    def _speak_held(self, place, name_the_rest=True):
        """Speak the held update at `place` word for word, then name any others still waiting -
        the one shape a held update ever reaches him in, whoever set it in motion (his pick, his
        go-ahead, or the brain handing it over with deliver_update).

        `name_the_rest=False` for news that is not an item on his list: an errand's result is
        something to say, and hanging a roll call off it turns the machinery he should never see
        into the thing he is answering about.

        Nothing held is ever withheld. A gate used to sit here asking the brain whether a stored
        line had been overtaken by the conversation; in two days it prevented nothing and twice
        destroyed what he was asking for - the update he had just said "Yes." to, and the demo
        link he had asked for twice. The stale-recording cases it was built for are stopped at
        their sources instead (an errand may not ask him questions; telling an agent, rejecting
        its work or retiring it drops its held news), and news never spoken stays the graver
        failure - which is also why nothing is SPENT here unless it began sounding: a barge-in
        already down when the words were about to start used to clear the spool over zero audio,
        and the news died in the black hole."""
        if self._interrupted():
            return ""  # nothing will sound; the news stays in hand, owed, for the next opening
        news = self._waiting.pop(place)
        listed = [held for held in self._waiting if getattr(held, "listed", True)]
        # A walkthrough OPENS a review: the moment it is spoken, his eyes are on that work, and
        # a menu of other items welded to its back is exactly the interruption he banned ("Don't
        # ask me about updates for other items when we've already picked one of them to be
        # working on"). The list is offered when his verdict closes the review, not before.
        about = getattr(news, "about", None)
        named = bool(name_the_rest and listed
                     and not (about is not None and self._review_opens(about)))
        said = f"{news}\n\n{roll_call(listed)}" if named else str(news)
        self._console.heads_up(said)
        # Known only when the whole utterance is the brain's own sentence; with a roll call
        # appended, part of what they hear is app-authored and the ledger must carry it.
        if not self._say(said, record=False,
                         known=getattr(news, "composed", False) and said == str(news)):
            self._waiting.insert(place, news)  # never sounded: still owed, back where it stood
            return ""
        # What has been READ OUT, which is only ever a roll call that actually went out. Recorded
        # after an errand's answer instead, it would mark the agents' list announced and that list
        # would then never be spoken, since it re-reads only when it has changed.
        self._announced = self._roll() if named else ()
        self._delivered(news)
        if getattr(news, "composed", False):
            self._remember_spoken(str(news))  # its own words, now actually in his ears
        return said

    def _hand_over(self, heard, place=0):
        """Say the update at `place`, then name any others still waiting. The one delivery path.

        Folded into a fresh brain turn instead, the content twice went missing - a "yes" answered
        with "Go check it out then", and the next one with "Checking if the Projects tab changes
        are actually live" - while the app marked the news delivered either way, so what the agent
        had actually reported never reached him at all ("that's not an update"). His go-ahead asks
        for that content; the content is the answer, and the app owes it rather than asking the
        brain to remember to include it. Anything more than a bare go-ahead is still a turn of
        his to answer, and rides into the reply as it always did.

        Never gated: this path is only ever reached because he ASKED - a go-ahead answering the
        offer, or a name off the roll call. The gate exists to stop the app volunteering
        something stale, never to overrule him. It once destroyed the update he had just said
        "Yes." to, two seconds later, because the brain had seen itself offer that update and
        read the offer as having delivered it."""
        return Turn(heard=heard, said=self._speak_held(place))

    def _superseded(self, news):
        """Tell the outbox this news will never be spoken - newer news about the same agent has
        replaced it. Collapsing the queue in memory alone left the old sentence spooled, and a
        restart delivered it as fresh: "it comes out of nowhere and provides no new information
        that I didn't already have"."""
        forget = getattr(self._outbox, "superseded", None)
        if forget is not None:
            forget(news)
        if getattr(news, "composed", False):
            # It wrote this sentence and nobody will ever hear it. Left on its record, the brain
            # holds the overtaken line AND hears the newer one, which is the same-thing-twice
            # reading manufactured from the inside.
            self._retract(str(news))

    def _roll(self):
        """The roll call as it would be SPOKEN right now - the sentence, which is what a re-read
        has to be compared against.

        Compared against the held NEWS instead, fresh news for an agent already on the list
        counted as a change while the sentence stayed identical, and he got the same words twice
        eight seconds apart ("why did it just give me the same message twice in a row?"). Compared
        by COUNT, an earlier version of this went the other way and never re-read a genuinely
        changed list at all. The sentence is the thing he hears; the sentence is the test."""
        return roll_call([held for held in self._waiting if getattr(held, "listed", True)])

    def _say_opening(self):
        """Say the session's first line, once - and answer whether it reached him.

        It is the whole of its own utterance: the one line at the boot boundary, never a header
        welded onto a piece of news. Its answer is what tells the caller an offer now stands,
        because with news waiting the greeting itself is what asked him to choose."""
        opening, self._opening = self._opening, ""
        if not opening:
            return UNSAID
        self._console.reply(opening)
        sounded = self._say(opening, record=False, known=True)
        if sounded:
            self._remember_spoken(opening)
        else:
            self._opening = opening  # never sounded: still unsaid, still owed
        return sounded

    def _announce(self):
        """Read out who is waiting, numbered, so one of them can be named.

        Never with the session's first line on the front of it: that line is its own thread and
        ends by asking him something, and welded to a menu he was asked two questions in one
        breath. The greeting goes out alone and IS the offer (see `_deliver_outbox`); this is what
        he hears once he has said he wants them."""
        roll = self._roll()
        self._console.heads_up(roll)
        if not self._say(roll, record=False):
            return
        self._announced = roll

    def _take_pick(self, heard):
        """They answered the roll call by naming one: say that one, and what is still waiting.

        A Turn if they were naming one, None if they were not - in which case this was an ordinary
        thing to say and the list simply stands. Only a terse answer counts as naming one (see
        `waiting.chosen`), so a sentence that happens to carry an agent's name is still their turn:
        answering it with a notice instead would lose the question.
        """
        listed = [held for held in self._waiting if getattr(held, "listed", True)]
        place = chosen(heard, listed)
        if place is None:
            return None
        # The pick ANSWERS the offer, so the offer is spent. Left standing, the leftover item
        # rode his NEXT words as if they had answered it: "The ship it still stands." - about a
        # different agent entirely - came back with the spinner walkthrough welded on.
        self._update_offered = False
        return self._hand_over(heard, self._waiting.index(listed[place]))

    def _still_reviewing(self):
        """Whose work is genuinely in front of him right now - and nothing once his attention has
        plainly moved on.

        The gate silences every other thread, so it may never outlive its own premise. A verdict
        that never got RECORDED (the brain answered his "ship it" with nothing at all, so no tool
        call was ever made) left a review open forever, and behind it a merge report he was
        waiting for sat unspeakable while he asked where it was. So the hold is bounded by his
        own turns: past a couple of them with no verdict, he is no longer looking at that work,
        whatever the record still says."""
        reviewing = set(self._in_review() or ())
        if reviewing != self._reviewing:
            self._reviewing, self._review_turns = reviewing, 0
        return reviewing if self._review_turns < REVIEW_HOLDS_TURNS else set()

    def _dormant(self):
        return (self._dormant_after is not None
                and self._clock() - self._last_engaged > self._dormant_after)

    def _whose_news(self):
        names = []
        for item in self._waiting:
            about = getattr(item, "about", None) or "your agents"
            if about not in names:
                names.append(about)
        return " and ".join(names)

    def _release_updates(self, heard):
        """They said the word and several are waiting: say the first, and name what is left.

        It used to answer with the numbered list and "Which first?" - a QUESTION, in reply to a
        "yes" that was itself the answer to one, about updates it had just named to him: "I
        already said yes to the Highdeas-submission-feedback one. Why would you ask me this? You
        sound insane." A go-ahead is a go-ahead. The list decides ORDER, not whether; the first
        is the one he was offered first, and naming the rest keeps the choice open without making
        him give it twice."""
        self._update_offered = False
        return self._hand_over(heard)

    def _they_are_talking(self):
        """Are they part-way through saying something? While they are, Excephalon says nothing of its
        own accord - it once broke in while they were mid-sentence.

        The question used to be "is their mic on", which was the same question when the mic was a
        walkie-talkie: it was only live while they held a turn. The window's mic is a STATE and stays
        armed for the whole conversation, so that reading answered yes forever and nothing unprompted
        could ever be said at all. A mic that can't report (the terminal's) never blocks: it only
        yields between turns anyway.
        """
        talking = getattr(self._stt, "is_mid_utterance", None)
        return bool(talking and talking())

    def _think(self, heard, on_text=None, progress=None, on_activity=None):
        """Ask the brain off the main thread so the interrupt stays answerable the whole time. If
        they barge in while it's thinking, the call is cancelled and `_ThinkInterrupted` is raised
        so the loop drops the turn. Re-raises whatever the brain raised, so the caller's error
        handling is unchanged. `on_text` streams the reply's text out as it is written.

        And HIS wait is bounded here, whatever the brain is doing underneath. The brain bounds
        each of its own asks, but it may spend several of them - lock, shed, reconnect, ask
        again - and a turn of his went twelve minutes with no word at all ("it seems to be stuck
        again. I said ship it then it never said anything"). The bound is on PROGRESS, not on the
        turn: `progress` answers when the turn last moved, so a reply still being written is never
        cut off, and a reply that stopped mid-way is not mistaken for one.

        Moving means WORKING, not merely speaking. Measured on words alone, a turn that went
        straight into a long tool call had never "progressed" at all, and the bound killed it at
        ninety seconds: he asked for a walk through his day from a calendar it was in the middle
        of reading, and was told something was broken in its head. Every message the model sends -
        a tool call, a tool's result, a word - is the turn moving, so only genuine silence ends
        the wait."""
        outcome = {}
        done = threading.Event()

        def work():
            try:
                extras = {}
                if on_text is not None:
                    extras["on_text"] = on_text
                if on_activity is not None and self._brain_reports_work:
                    extras["on_activity"] = on_activity
                outcome["reply"] = self._brain.respond(heard, **extras)
            except BaseException as exc:  # carry it back to the main thread to re-raise in context
                outcome["error"] = exc
            finally:
                done.set()

        threading.Thread(target=work, daemon=True).start()
        started = self._clock()
        while not done.wait(self._interrupt_poll):
            if self._interrupted():  # they cut in - cancel the call and abandon the turn
                self._cancel_think(done)
                raise _ThinkInterrupted
            # The bound is on PROGRESS, not on total silence. Measured on silence alone it
            # never fired for the turn that mattered: the brain wrote one clause, went into a
            # tool sequence and hung there, so "it has said something" stayed true while he sat
            # for twenty minutes ("I give my approval for a feature for the 4th time and
            # Excephalon is still not responding at all"). Anything that reaches the air resets
            # the clock; nothing new for the window ends the wait, whatever came before it.
            since = self._clock() - max(started, progress() if progress else started)
            if self._answer_within and since > self._answer_within:
                self._cancel_think(done)
                raise _ThinkTooSlow(f"nothing more said in {self._answer_within:.0f}s - the turn "
                                    "stopped waiting on the brain")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def _cancel_think(self, done):
        """Tell the brain to drop the in-flight call, then wait for the worker to unwind before
        returning - so the next turn never starts a second brain call overlapping this one. A brain
        with no `interrupt` (e.g. a fake) can't be cancelled; we still wait out the bounded window.

        The interrupt itself goes on a thread of its own and is never waited on. It reaches the
        CLI by scheduling a coroutine on the session's own loop, which runs it "at its next
        await" - and a session hung on a dead read never reaches one, so that call can block
        forever. Called inline, it defeated the very deadline that called it: the loop stopped
        waiting on the brain and then waited on the cancel instead."""
        interrupt = getattr(self._brain, "interrupt", None)
        if interrupt is not None:
            def cancel():
                try:
                    interrupt()
                except Exception as exc:
                    print(f"[interrupt error] {exc!r}", file=sys.stderr)

            threading.Thread(target=cancel, daemon=True).start()
        done.wait(self._cancel_wait)

    def turn(self):
        if self._interrupt is not None:
            self._interrupt.clear()  # a fresh turn; forget any leftover "stop" from the last one
        self._deliver_outbox()  # say any queued agent news before we start listening again
        if not self._paused:  # asleep it's only watching for the wake word - don't claim otherwise
            self._console.listening()
        heard = self._stt.listen()
        if not heard.strip():
            # An empty turn that still ended on "over" means they said only the terminator - let them
            # know it registered (the "✓ got it" cue already printed) instead of leaving dead air.
            if getattr(self._stt, "caught_terminator", False):
                self._say(self.empty_turn_reply)
            return None
        canonical = _canonical(heard)
        farewell = _ends_with_command(canonical, self._farewells)
        if self._paused and not farewell and not _wakes(canonical, self._resumes):
            # Asleep, and it's neither a wake word nor a goodbye - so it's the TV, or someone else in
            # the room. Don't transcribe it back at them; just show that it landed and was dropped.
            self._console.ignored()
            return None
        self._console.heard(heard)  # show what was transcribed before we act on it
        # An ask covers the moment it was made. One the app could not serve at once - he was
        # already mid-sentence - stayed pending across his next turn and then delivered its stale
        # "ready for your eyes" ungated, seconds after he had APPROVED that very work. Once he has
        # spoken again, whatever he asked for before is ordinary held news and faces the gate.
        self._requested.clear()
        self._review_turns += 1  # a turn of his against the one-thing-at-a-time gate's bound
        # The session's first line is a first line or nothing. One that missed its moment - he was
        # mid-sentence at boot, or the delivery that should have carried it never sounded - is no
        # longer a greeting once he has spoken: the boot welcome once surfaced seven minutes into
        # the conversation, inviting him to look at a demo he had already approved ("makes no
        # sense... Excephalon had been with me up until just before that").
        if self._opening:
            self._console.evidence(f"(opening dropped unspoken - he spoke first: {self._opening})")
            self._retract(self._opening)  # a first line it wrote and he never heard
            self._opening = ""
        self._last_engaged = self._clock()  # they spoke: present again, whatever the clock said
        if farewell:
            self._speak_reply(self.farewell_reply)
            return Turn(heard=heard, said=self.farewell_reply, farewell=True)
        if self._paused:  # a wake word - the only other thing that gets through
            self._paused = False
            self._speak_reply(self.resume_reply)
            return Turn(heard=heard, said=self.resume_reply)
        if _ends_with_command(canonical, self._suspends):  # "stop listening" puts it to sleep, doesn't quit
            self._paused = True
            self._speak_reply(self.suspend_reply)
            return Turn(heard=heard, said=self.suspend_reply)
        if self._update_offered and self._waiting and canonical in _GO_AHEADS:
            self._update_offered = False
            if len(self._waiting) == 1:
                return self._hand_over(heard)   # the one held update IS the answer
            return self._release_updates(heard)  # several are held; read out the choice
        if self._update_offered and len(self._waiting) == 1:
            # They answered with words of their own, so the held update rides into this turn's
            # prompt and the brain says it once, folded around what they asked. Speaking the stored
            # line as well - after the brain had already covered it - is how he heard it all twice.
            # Unless his eyes are on OTHER work: mid-review, news about a different thread never
            # rides his reply - one thing at a time - and waits for the gate instead.
            about = getattr(self._waiting[0], "about", None)
            reviewing = set(self._in_review() or ())
            if not reviewing or about in reviewing:
                self._update_offered = False
                self._announced = ()
                return self._answer(heard, offered=self._waiting.pop())
        if self._waiting and (self._announced or self._update_offered):
            # They may be naming one of the agents the roll call just read out - and ONLY then:
            # picking is answering a list, so with no list read out and no offer standing, a
            # short sentence that happens to contain "one" is his own words, not a choice off a
            # menu he never heard.
            picked = self._take_pick(heard)
            if picked is not None:
                return picked
        return self._answer(heard)

    def _with_system_notes(self, heard, offered=None):
        """Their words, with what the brain would otherwise have no way of knowing put in front:
        the live fleet briefing, everything said in its name since that it did not write, and -
        on the turn that answers an update offer - the held update itself, to deliver once."""
        notes = CONDUCT_NOTICE
        if offered is not None:
            notes += OFFERED_NOTICE.format(
                about=getattr(offered, "about", None) or "your agents", news=offered)
        if self._standing is not None:
            moved = str(self._standing()).strip()
            if moved:
                notes += STANDING_NOTICE.format(standing=moved)
        if self._briefing is not None:
            facts = str(self._briefing()).strip()
            if facts:
                notes += BRIEFING_NOTICE.format(briefing=facts)
        unwritten, self._unwritten = self._unwritten, []
        if unwritten:
            notes += UNWRITTEN_NOTICE.format(lines="\n".join(f"- {line}" for line in unwritten))
        return notes + heard

    def _answer(self, heard, offered=None):
        """Think, and speak the reply as it is written.

        With a streaming voice and a streaming brain, each sentence sounds while the next is still
        forming, and the loop waits out the audio before listening again. With either half unable
        to stream (the system voice, a plain fake), the reply is spoken whole once it lands - the
        same behavior this loop always had, minus the stock phrases around it."""
        self._console.thinking()  # a "(thinking…)" indicator so a pause doesn't read as a hang
        open_stream = getattr(self._tts, "stream", None)
        reply = None
        if open_stream is not None and self._brain_streams:
            # Sentences are synthesized in their spoken form (a path becomes its filename) while
            # the record keeps the real text - the screen shows what gets clicked.
            reply = open_stream(interrupt=self._interrupt, spoken_form=as_spoken)
        # What the voice is saying, as it accrues - the stop-watcher's measure of whether a chunk
        # heard mid-reply is Excephalon's own leak or someone talking over it. Spoken form, since
        # that is what is audible and therefore what a leak transcribes.
        spoken_parts = []
        # When the turn last MOVED - what his wait is measured against, so a reply still arriving
        # is never cut off and one that stopped part-way is not mistaken for it. Moved, not spoke:
        # a turn that goes straight into reading his calendar writes no words for minutes, and
        # measured on words it was killed as a wedge while doing exactly what he asked.
        last_progress = [0.0]

        def working(_message=None):
            last_progress[0] = self._clock()

        def audible(piece):
            spoken_parts.append(piece)
            working()
            reply.add(piece)

        # The gates between the brain and the voice: a pasted document line is dropped before it
        # can sound, then a stray goodbye sentence - so the closing line is only ever heard when
        # the app actually closes, and a quoted-in report is never read at him.
        gate = _FarewellGate(audible, self.farewell_reply) if reply is not None else None
        paste = _PastedReportGate(gate.feed) if gate is not None else None

        release_floor = self._hold_the_floor(
            script=lambda: as_spoken("".join(spoken_parts)),
            audio=(lambda: reply.sounding) if reply is not None else None)
        think_start = time.monotonic()
        try:
            said = self._think(self._with_system_notes(heard, offered),
                               on_text=paste.feed if paste is not None else None,
                               progress=lambda: last_progress[0],
                               on_activity=working)
        except _ThinkInterrupted:  # they cut the thinking off - no reply, straight back to listening
            self._keep_for_later(offered)  # nothing was said, so the update is still owed
            self._settle(reply)
            release_floor()
            return None
        except Exception as exc:  # the plain word to them; the cause to the durable record
            self._keep_for_later(offered)  # the delivery turn died; the update must survive it
            self._settle(reply)
            # The floor first: its leak-script is the streamed reply, and a wedge streamed
            # nothing - spoken under it, the error line's own audio came back through the mic
            # as the user's draft words. Released, _say opens a watcher scripted with exactly
            # the line it is about to speak, and the leak is dropped as Excephalon's own.
            release_floor()
            self._console.evidence(f"(brain error: {_cause(exc)})")
            # A dead sign-in is the one cause "ask me again" cannot outwait and a restart cannot
            # fix - he restarted on exactly that advice and met the same wall. The fix is his to
            # do, so the reply is the fix - and where the app can open the terminal itself, it
            # does, and says so instead of reciting steps for a door already standing open.
            if needs_sign_in(exc):
                opened = self._sign_in_helper is not None and self._sign_in_helper()
                said = self.sign_in_opened_reply if opened else self.sign_in_reply
            else:
                said = self.error_reply
            self._speak_reply(said)
            return Turn(heard=heard, said=said, error=True)
        think_time = time.monotonic() - think_start
        if paste is not None:
            paste.flush()  # the last line may have ended without a newline
        if gate is not None:
            gate.flush()  # the last sentence may have ended without punctuation
        # The record matches the ear: a stray goodbye the gate kept out of the voice comes off
        # the screen's copy too, and a reply that WAS only the goodbye becomes a silent turn -
        # and the pasted lines the paste gate never let sound come off it the same way.
        wrote_nothing = not str(said or "").strip()  # before the gates: did it write at all?
        said = _without_pasted_report(said)
        said = re.sub(r"[ \t]{2,}", " ", self._stray_goodbye.sub(r"\1", said)).strip()
        # Any held update this turn owes him - the one he was offered, and any the brain handed
        # over mid-think (deliver_update) - is appended to the reply BY CODE, word for word, one
        # utterance. Woven by the brain instead, the content twice went missing ("a 'Yes'
        # answered with 'Go check it out then'"); requested and served on a later loop pass
        # instead, the reply promised an update that never followed ("Hm, what do you mean? You
        # didn't get me anything.").
        served = self._requested_now()
        extras = ([offered] if offered is not None else []) + [news for _, news in served]
        if not said.strip() and not extras:
            self._settle(reply)
            release_floor()
            if not wrote_nothing:
                # It DID write, and a gate scrubbed the whole of it - a reply that was only the
                # stray goodbye. Deliberately silent: the misfire is what was dropped, and there
                # is nothing else it meant to say.
                return Turn(heard=heard, said="")
            # He SPOKE and the brain wrote nothing at all. It happens when a turn is spent on
            # tool calls and no words: he said "Yes, looks good. Ship it." and heard dead air,
            # then waited half an hour for an update on a landing nobody had recorded. Silence
            # is never an answer to his words, so the turn is asked once more, told plainly what
            # went wrong; only if THAT is silent too does the app say so in its own voice.
            said = self._say_something(heard)
            if not said:
                self._console.evidence("(the brain answered his words with nothing, twice)")
                said = self.error_reply
            self._speak_reply(said, known=True)
            return Turn(heard=heard, said=said)
        combined = "\n\n".join([part for part in [said] if part]
                               + [str(extra) for extra in extras])
        speak_start = time.monotonic()
        if reply is not None:
            for extra in extras:
                # Into the same stream, so it is one utterance and one stop silences all of it.
                spoken_parts.append(f"\n\n{extra}")
                reply.add(f"\n\n{extra}")
            # On screen the moment the text is complete - the audio is still going out, and
            # reading the whole of it beats being read to ("I want to see all the text
            # immediately up front and then hear it").
            self._console.reply(combined)
            # The receipt is what actually reached the air: an utterance drained whole by a
            # barge-in that beat its first word never sounded, and must spend nothing.
            sounded = _receipt(reply.done(), combined,  # then wait out the rest of the audio
                               cut=self._interrupted())
            if sounded.cut:  # the audio was cut partway - the record must say so
                self._console.aside("(cut off mid-utterance)")
            began = bool(sounded)
        else:
            spoken_parts.append(combined)  # the floor's script: about to be audible in full
            began = bool(self._speak_reply(combined, known=True))
        release_floor()
        if began:
            for extra in extras:
                self._delivered(extra)
                if getattr(extra, "composed", False):
                    self._remember_spoken(str(extra))  # its own words, now actually in his ears
                else:
                    # He heard it in Excephalon's voice and the brain did not write it - the same
                    # ledger every app-authored line rides, so it is never denied later.
                    self._unwritten.append(str(extra))
        else:
            # Never sounded: still owed, never spent - and each goes back where it STOOD, because
            # re-queued anywhere else the numbers he was read stop meaning what he heard ("Now I
            # don't know what to tell you").
            self._keep_for_later(offered)
            for place, news in served:
                self._waiting.insert(min(place, len(self._waiting)), news)
        if self._timings:
            self._console.timing(think=think_time, speak=time.monotonic() - speak_start)
        self._pause_to_read()
        return Turn(heard=heard, said=combined)

    def _say_something(self, heard):
        """One more ask, when the brain answered his words with no words at all - "" if it stays
        silent. Not the same turn again: it is told what happened, because a turn spent entirely
        on tool calls has already DONE the thing and only owes him the sentence about it."""
        try:
            said = self._think(SILENT_TURN_NOTICE + heard)
        except Exception:
            return ""
        return _without_pasted_report(str(said or "")).strip()

    def _settle(self, reply):
        """Let an open reply stream wind down (whatever was already spoken has been heard; the rest
        drains unspoken once the interrupt is set, or was never fed)."""
        if reply is not None:
            reply.done()

    def _keep_for_later(self, offered):
        """An update popped into a turn that never delivered it goes back to waiting - they were
        promised that line, and the failed turn must not be where it silently ends. Appended, not
        pushed to the front: the OFFERED update is taken from the tail of the hand, so the tail
        is where it stood - re-queued at the head it would reorder the list and a later roll call
        would come back renumbered, the failure he described as not knowing which numbering to
        answer. (A deliver_update extra, popped from an arbitrary place, is restored by place
        instead - see _answer's kept path.)"""
        if offered is not None:
            self._waiting.append(offered)

    def _requested_now(self):
        """The held items the brain handed over DURING this turn (deliver_update), popped to ride
        this very reply. Served on a later loop pass instead, anything in between could void the
        request, and a reply announcing an update was followed by nothing at all ("Hm, what do
        you mean? You didn't get me anything.")."""
        wanted = getattr(self._outbox, "take_requested", None)
        if wanted is None:
            return []
        asked = wanted()
        if not asked:
            return []
        # The request answers the outbox's WHOLE debt, and news that landed while he was talking
        # is still in the queue, not the hand - so the queue is drained into the hand first, the
        # same drain-and-collapse every delivery pass does, or a promise made about mid-turn news
        # would be another reply followed by nothing.
        self._waiting.extend(self._outbox.drain())
        self._waiting, superseded = _newest_per_agent(self._waiting)
        for stale in superseded:
            self._superseded(stale)
        served = []
        for about in asked:
            place = next((at for at, held in enumerate(self._waiting)
                          if getattr(held, "about", None) == about), None)
            if place is not None:
                # The place rides along so a weld that never sounds can put it back where it
                # stood - re-queued anywhere else, the numbers he was read stop being true.
                served.append((place, self._waiting.pop(place)))
        return sorted(served)

    def run(self, should_continue=lambda: True, on_turn=None):
        while should_continue():
            result = self.turn()
            if result is None:
                continue
            if on_turn is not None:
                on_turn(result)
            if result.farewell:
                break
