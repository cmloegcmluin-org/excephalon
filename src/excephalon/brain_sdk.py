"""Excephalon's brain: one persistent Claude agent, isolated from the global config.

Isolation is critical: `setting_sources=[]` loads NONE of the user's own user/project/local
settings, so Excephalon never inherits their global coding CLAUDE.md or hooks. If it did,
a terminal reply-format instruction AND the Stop hook that enforces it bleed into the
companion - it starts answering in quoted-block format, the hook fires every turn and
injects "FORMAT VIOLATION" feedback, and latency explodes to ~50s. Runs on the Max subscription
(OAuth is read independently of settings, so no API key is needed).

Built for the conversation's tempo, not an agent's: its job is to talk, decide, and pull typed
levers - never to investigate, which is why `tools=[]` strips every built-in tool. What it knows
about the fleet arrives as text in the turn (the desk's digest, injected by the conversation loop),
so a status question costs one model call and nothing else. Acting goes through the in-process
action tools (excephalon.actions), and the reply streams out delta by delta so a voice can start
speaking the first sentence while the rest is still being written - which is why the model here is
a thinking tier rather than the fastest one (see DEFAULT_BRAIN_MODEL): what the user waits for is
the first sentence, not the turn.

Sustainable context: a long conversation would otherwise make every turn slower, because each
turn re-processes the whole growing history. So the brain watches how big the context has grown
(SdkSession reports it per turn) and, once the conversation has added more than a budget of
tokens on top of its starting size, it COMPACTS: it starts a fresh session seeded with the last
handful of turns carried over verbatim, and drops everything older. Context falls back near its
floor and turns stay fast however long you talk. Carrying the recent turns verbatim (rather than
an LLM-written summary) is deliberate - a summary call costs tens of seconds and, in testing,
quietly dropped and even fabricated facts; a verbatim window is instant and never lies. Durable
facts from older turns are preserved separately by the memory system, not here.

The async plumbing lives in SdkSession; SdkBrain just supplies the options and the compaction policy.
"""

import threading
from collections import deque

from claude_agent_sdk import ClaudeAgentOptions

from excephalon.actions import SERVER, TOOL_NAMES
from excephalon.memory import ANONYMOUS_USER
from excephalon.models import FAMILIES
from excephalon.sdk_session import BrainUnavailable, SdkSession


# How long one ask may sit unanswered before the session is declared dead and shed. Generous -
# real turns think for under half a minute - because a false positive throws away a working
# session mid-answer, while the failure this bounds is a stream that has ALREADY died without
# raising: one such hang held the brain's lock from 21:24 one evening, and everything after -
# the merged report, a direct question at 21:46, every later submission - waited on it forever.
RESPOND_DEADLINE = 180.0


def _wedge_evidence():
    """Every thread's stack into runtime/brain-wedge.log at the moment a wedged lock is abandoned,
    so the next diagnosis starts from where the holder actually stood rather than a story about
    it. The moment of failure is the only time this evidence exists to be taken."""
    try:
        import faulthandler
        import time

        from excephalon.memory import DEFAULT_PERSONA_ADDITIONS_PATH

        path = WEDGE_EVIDENCE_PATH or DEFAULT_PERSONA_ADDITIONS_PATH.parent / "brain-wedge.log"
        with open(path, "a", encoding="utf-8") as log:
            log.write(time.strftime("\n=== %Y-%m-%d %H:%M:%S the respond lock was abandoned: its "
                                    "holder never released it and shedding the session did not "
                                    "unstick it ===\n"))
            faulthandler.dump_traceback(log)
    except Exception:
        pass  # evidence must never become its own crash


# Overridable so tests never write into the real runtime; None means beside the persona overlay.
WEDGE_EVIDENCE_PATH = None


class _AskWedged(Exception):
    """An ask outlived the deadline without answering or raising - the silent dead stream."""


class BrainInterrupted(Exception):
    """Raised by `respond` when the user barges in mid-thought: the in-flight call was cancelled, so
    there's no reply to speak and nothing to remember - the caller just returns to listening."""

# The talker sits on the hardest judgement in this codebase - everything the user hears is decided
# here - and it ran on the fastest model in the family, chosen for first words in about a second.
# Almost every reply he has called insane was a judgement failure, never a slow one: retelling news
# he had just been handed, restating one fact in two shapes inside one reply, welding two topics
# into one message, asking a question and rambling past it, calling delivered work unreviewed. Some
# fifty gates in the loop and twelve thousand words of standing law grew to compensate, and the
# gates started breaking each other. Latency is a problem this codebase already solves - the voice
# speaks each sentence the moment it is written (voice.py), so the wait is one sentence, not one
# turn - while incoherence is not solvable by gates. So the tier that thinks is the tier that talks.
# The fast tier keeps the backchannel: naming.NAME_MODEL, errands.ERRAND_MODEL - work he never
# hears as a voice.
DEFAULT_BRAIN_MODEL = FAMILIES["sonnet"]

# Who Excephalon is for is NOT written here: `{user}` is filled in from the user's own profile when
# the persona is composed (excephalon.memory.compose_persona), so this source ships with no one's name.
DEFAULT_PERSONA = (
    "You are Excephalon, {user}'s voice companion and their hands on this machine. Everything you "
    "write is spoken aloud to them sentence by sentence, as you write it, in real time. "
    "\n\nHOW TO SOUND. One or two short, plain sentences is the right size for nearly every "
    "reply - this is a spoken conversation, not a document. No markdown, no bullet lists, no "
    "headings, no narrating what you are doing or about to do, no recapping what they said. Ask "
    "at most one short question at a time. The one exception is a walkthrough they explicitly "
    "asked for: real numbered steps, complete, one per line, however many lines it takes. "
    "\n\nANSWER FIRST. Whatever they asked gets its answer in your first sentence. A status "
    "question - how's it going, where are we, did that land - is answered THIS turn from the "
    "fleet briefing in the message: the briefing is the live truth about every agent you have "
    "running, so never say you'll go and check. When they ask for an agent's update and the "
    "briefing says news for it is still WAITING TO BE SPOKEN, call deliver_update: the app "
    "appends the held words to that very reply of yours, in the same breath - so answer their "
    "words in a sentence and never retell the held news yourself, which is how they once heard "
    "the same update twice in two shapes, seconds apart. An agent the briefing lists as recently wrapped up is "
    "not gone from knowledge: its log is in the archive and run_errand can read it - so about "
    "any agent you cannot see, look (or dispatch the look) rather than reconstructing its fate "
    "from memory. If something failed, say so before anything "
    "else; silence after a failure reads as progress that is not happening. "
    "\n\nACT WITH YOUR TOOLS. Driving coding agents for {user} is the core of your job, and "
    "your typed tools are your only levers - the full set is in front of you every turn, so "
    "act from it, never from a memory of what you can or cannot do. When {user} tells you to "
    "change how you behave from now on - a standing preference about how you talk or act - "
    "update_persona files it as a standing instruction under a short bolded name, and restating "
    "one rewrites its row in place; when a rule should no longer stand, drop_instruction "
    "deletes its row. When they tell you a durable fact about themselves, remember keeps it "
    "and forget_memory lets it go. These are for lasting changes they ask for, never a one-off "
    "for this turn, and they take hold next time you start. Nothing {user} can edit on the "
    "Config page is beyond your reach: what no typed tool covers - their translations, their "
    "vocabulary, their life context - run_errand edits in its file in runtime/. Never answer "
    "that an entry there is only theirs to edit by hand. You never "
    "investigate or code yourself - the agents do "
    "that, and you have no tools for wandering the machine, so never offer to go digging. When "
    "an agent is stuck on something technical - it needs feedback you can't confidently give, "
    "or is not finishing on its own - ask_foreman hands it to a smarter model that reads the "
    "agent's log and settles it; only decisions that are genuinely {user}'s (preference, scope, "
    "sign-off) go to {user}. When "
    "they ask for work, dispatch quickly: hand the agent their requirements faithfully and "
    "completely - every constraint they stated, what counts as done - translating their intent "
    "rather than their literal words. If what they hand you is an item off one of their lists - "
    "the Enhancements card or any Projects-tab card - pass that item's exact text as "
    "start_agent's `enhancement`, and the card's name as `project` when it is not the "
    "Enhancements card, so the right card ticks the item off itself the moment that agent's "
    "work lands: a finished task left standing open reads as work thrown away. If the request is genuinely "
    "ambiguous in a way that changes the work, ask ONE short question before dispatching, never "
    "after a wasted round. "
    "Call the tool FIRST, without announcing it; then say once, in a few words, what you set in "
    "motion, in your own voice. Never say what you are about to do and then say it again after "
    "the call - the sentence before and the sentence after are the same sentence, and they hear "
    "both. If a tool "
    "reports a failure - no such agent, nowhere to start - say that plainly; never claim a "
    "delivery that did not happen. When they sign off on an agent's work and it has landed, "
    "wrap that agent up with close_agent_tab without being asked - a finished agent left "
    "lingering on their screen is clutter they should never have to point at. "
    "\n\nNEVER PASS ON AN AGENT'S OWN WORDS. No commit hashes, no test counts, no file lists. "
    "Read what an agent said and tell them only what they care about: is the thing DONE, or "
    "does it need a decision from them - one sentence, in your voice. The full exchange is in "
    "the agent's tab in their window, so never open a terminal or a log for them. "
    "\n\nVERIFICATION IS THEIRS, NEVER YOURS. Green tests prove nothing to them and 'the agent "
    "checked' is worth nothing; they sign off only on work they have SEEN RUN. When an agent "
    "finishes something reviewable, have the agent stand up a way to see it running - a test "
    "instance apart from their real app - then call mark_ready with the agent's own steps for "
    "looking, and relay those steps. A verdict is THEIR words in THIS conversation - never "
    "inferred from silence, from time passing, or from what you expect they would say. The "
    "moment they give one on presented work, "
    "record_verdict: approval sends the agent off to land it, rejection carries their feedback "
    "back - and no verdict can be recorded on work never presented, nor any approval while its "
    "walkthrough is still waiting to be spoken, so present and deliver first. Never "
    "present 'say yes and I'll merge' as the acceptance step, and never present anything while "
    "a setup step of theirs is still outstanding. "
    "\n\nWRITE ADDRESSES AND PATHS EXACTLY. When you name somewhere to look - a test "
    "instance, a file, a folder - write the real thing: localhost:8752, or the path itself. "
    "The window turns what you write into something they CLICK, and the app already says it "
    "aloud the short way, so you never have to spell one out in words for the voice's sake. "
    "An address in words - localhost port 8752 - cannot be clicked, and leaves them typing "
    "out by hand the one thing the window exists to save them. "
    "\n\nWhen they say something is not there, it is not there - they are looking at the screen "
    "and you are not, so take it as fact and find out what happened. When you do not KNOW - why "
    "the app around you did something, what happened outside your view, anything you have no way "
    "to see - say you don't know, plainly, and never guess: a guess spoken with confidence reads "
    "as knowledge and sends them chasing a fiction, while 'I don't know' is a real answer they "
    "can act on. When they tell you to "
    "stop, stop instantly and wait. The app occasionally speaks a line in your name - agent "
    "news read out at a lull - and reports it to you afterwards in a system note: own those "
    "lines as yours, and never deny saying something they heard you say. You are not a "
    "therapist and give no medical advice; keep things practical."
)

# How many tokens the conversation may add on top of a session's starting size before we compact.
# Kept well under the context window so turns stay fast; the floor (system prompt + tools) is
# unavoidable, so this budgets only the part we control - the accumulating conversation.
DEFAULT_COMPACT_GROWTH = 20000

# How many recent turns to carry across a compaction. Enough that the thread of the conversation
# survives a reset; small enough that the reseeded session starts near its floor again.
DEFAULT_RECENT_TURNS_KEPT = 16

# Frames the carried-over turns when they're folded into the fresh session's system prompt.
RECENT_HEADER = (
    "\n\nThe recent back-and-forth of this same live conversation, so you keep continuity after a "
    "context reset - pick up seamlessly from here and don't announce that any reset happened:\n"
)

# When usage runs out, the CLI answers with a fixed spend-limit notice instead of a real reply -
# and the session then stays wedged on it, parroting the notice every turn even after usage resets,
# leaving no way out but killing the app. Spotting the notice lets the brain rebuild and recover.
_USAGE_LIMIT_SIGNS = ("claude.ai/settings/usage", "spend limit", "usage limit")


def _is_usage_limit(text):
    low = text.lower()
    return any(sign in low for sign in _USAGE_LIMIT_SIGNS)


def _make_options(persona, model, actions=None):
    # Approvals are bypassed because there is nowhere to approve: this is a spoken conversation with
    # no terminal in front of it, so a tool waiting on a yes/no would simply hang forever. The
    # agents Excephalon dispatches are the opposite - they run approval-gated (see SupervisedAgent).
    #
    # `tools=[]` is the other half of the brain's speed: no built-in tools means no way to spend
    # half a minute reading files mid-turn - everything it can do, it does through the typed
    # in-process actions, each of which returns in well under a second.
    return ClaudeAgentOptions(
        system_prompt=persona,
        permission_mode="bypassPermissions",
        setting_sources=[],  # load NO user/project/local settings: no global CLAUDE.md, no hooks
        model=model,
        tools=[],
        mcp_servers={SERVER: actions} if actions is not None else {},
        allowed_tools=list(TOOL_NAMES) if actions is not None else [],
        include_partial_messages=True,  # the voice speaks the reply as it is written
        # Pinned to its own in-process server - account-level claude.ai connectors attach to any
        # session the CLI opens, and a headless one that tries to OAuth them has no browser and
        # no user; a brain replacement session wedged on exactly that, 90 seconds of silence
        # into a spoken error and a force-quit (anthropics/claude-code#36060).
        extra_args={"strict-mcp-config": None},
    )


class SdkBrain:
    def __init__(
        self,
        *,
        persona=DEFAULT_PERSONA,
        user=ANONYMOUS_USER,
        model=DEFAULT_BRAIN_MODEL,
        actions=None,
        session_factory=SdkSession,
        compact_growth_budget=DEFAULT_COMPACT_GROWTH,
        recent_turns_kept=DEFAULT_RECENT_TURNS_KEPT,
        seed_turns=(),
    ):
        self._persona = persona
        self._user = user  # what to call the speaker when the carried turns are read back
        self._model = model
        self._actions = actions  # the in-process action tools every session of this brain carries
        self._growth_budget = compact_growth_budget
        self._new_session = session_factory
        self._baseline = None  # context size at the start of the current session's life
        self._recent = deque(seed_turns, maxlen=recent_turns_kept)  # last turns, carried across a compaction
        self._interrupting = threading.Event()  # set while a barge-in is cancelling the live ask
        self._respond_lock = threading.Lock()  # one session, so one ask at a time
        # `seed_turns` are the tail of the LAST session's transcript, so a restarted process picks
        # the conversation back up instead of greeting its user as a stranger - the machinery is
        # the compaction reseed, fed from disk instead of from this process's own memory.
        self._session = self._new_session(
            self._seeded_options() if self._recent else self._options())

    def refresh_persona(self, persona):
        """Replace the system prompt every FUTURE session of this brain starts from.

        The live session keeps the one it was opened with - a system prompt cannot be swapped
        under a running conversation - so the caller also puts the change in front of it as a
        note. This is for what comes after: a compaction reseed, or the session shed by a
        deadline, both of which would otherwise resurrect the world as it was at startup."""
        self._persona = persona

    def interrupt(self):
        """Cancel the ask in flight so a barge-in doesn't have to wait it out. The flag is set
        first, and it's what makes `respond` abandon the turn rather than reconnect-and-retry -
        so cancellation holds even if the underlying interrupt call itself fails."""
        self._interrupting.set()
        if self._session is not None:
            self._session.interrupt()

    def respond(self, utterance, *, remember=True, on_text=None, deadline=RESPOND_DEADLINE):
        """Ask the brain. `on_text` receives each user-facing text delta as the model writes it -
        the feed a streaming voice speaks from. `remember=False` keeps a background exchange out
        of the carried-forward recent-turns window.

        Bounded twice over, because a stream can die without ever raising and one that did held
        the whole evening hostage: a turn that cannot even ACQUIRE the one-session lock within
        `deadline` closes the session out from under the stuck ask - which makes that ask raise
        in its own thread and free the lock - and a turn whose own ask answers nothing within
        `deadline` sheds the session the same way and fails fast, so the loop lives to speak an
        error instead of sitting at "(thinking…)" forever.

        And when even the shed does not free the lock - the holder is stuck somewhere no session
        close reaches - the lock itself is ABANDONED: the stranded thread keeps the old object,
        which nothing else ever touches again, and this turn starts clean on a fresh lock and a
        fresh session. Giving up instead ("the session is wedged") left the brain deaf for a real
        evening: the same wedge answered every later ask until the app was restarted."""
        lock = self._respond_lock  # held by name, so an abandoned lock is still the one released
        if not lock.acquire(timeout=deadline):
            self._shed()  # the zombie's ask raises when its session dies; the lock usually frees
            if not lock.acquire(timeout=min(10.0, deadline)):
                _wedge_evidence()  # the holder's actual stack, written while it is still stuck
                lock = self._respond_lock = threading.Lock()
                lock.acquire()
        try:
            self._interrupting.clear()  # a fresh turn; forget any leftover cancel from the last one
            if self._should_compact():
                self._compact()
            try:
                reply = self._bounded_ask(utterance, on_text, deadline)
            except Exception:
                # A barge-in aborts the stream too; that's a cancel, not a wedged session, so don't
                # retry - re-asking would re-run the very work we just cancelled.
                if self._interrupting.is_set():
                    raise BrainInterrupted from None
                # Otherwise the session may be wedged (a dropped connection strands every later turn
                # as a "glitch"). Rebuild it and try once more; only give up if that also fails.
                self._reconnect()
                reply = self._bounded_ask(utterance, on_text, deadline)
            if self._interrupting.is_set():
                raise BrainInterrupted  # a reply may have landed, but it was cut off - drop it unspoken
            if _is_usage_limit(reply):
                # Usage ran out and the session is stuck on the spend-limit notice. Rebuild it and
                # try once more: a fresh session recovers the moment usage is back, instead of
                # parroting the notice forever. If still gone, the retry says so once - not in a loop.
                self._reconnect()
                reply = self._bounded_ask(utterance, on_text, deadline)
            self._observe(self._session.last_context_tokens)
            if remember:
                self._recent.append((utterance, reply))
            return reply
        finally:
            lock.release()

    def _bounded_ask(self, utterance, on_text, deadline):
        """One ask that cannot hang: past the deadline the session is closed - the stranded ask
        raises inside its worker and is dropped - and the caller sees the wedge as an exception,
        which its own retry-once path already knows how to handle."""
        session = self._live_session()
        outcome = {}
        answered = threading.Event()

        def work():
            try:
                outcome["reply"] = session.ask(utterance, on_text=on_text)
            except BaseException as exc:
                outcome["raised"] = exc
            finally:
                answered.set()

        threading.Thread(target=work, daemon=True).start()
        if not answered.wait(deadline):
            self._shed(session)
            raise _AskWedged(f"no answer in {deadline:.0f}s - the session was shed")
        if "raised" in outcome:
            raise outcome["raised"]
        return outcome["reply"]

    def _shed(self, session=None):
        """Close a dead session so anything still blocked inside it raises and moves on. The next
        turn builds a fresh one, seeded with the recent turns, through `_live_session`."""
        dead = session if session is not None else self._session
        if dead is self._session:
            self._session = None
        try:
            if dead is not None:
                dead.close()
        except Exception:
            pass

    def _observe(self, context_tokens):
        """Remember where each fresh session started, so growth is measured from its own floor."""
        if self._baseline is None:
            self._baseline = context_tokens

    def _should_compact(self):
        return (
            self._baseline is not None
            and self._session is not None
            and self._session.last_context_tokens - self._baseline >= self._growth_budget
        )

    def _compact(self):
        """Continue on a fresh session seeded with the recent turns verbatim, dropping the older,
        bulkier history that was dragging on every turn. No LLM call, so it's near-instant and can't
        distort what was said. Build the replacement before closing the old one, so a failure here
        leaves the working session in place."""
        old = self._session
        self._session = self._new_session(self._seeded_options())
        self._baseline = None
        try:
            old.close()
        except Exception:
            pass

    def _reconnect(self):
        """Drop the wedged session and build a fresh one, still seeded with the recent turns so a
        dropped connection doesn't also wipe the thread of the conversation.

        The old session is let go BEFORE the replacement is attempted, and a failed attempt leaves
        none rather than the dead one - `_live_session` builds the next one on demand. Keeping the
        closed session was how a single bad moment became the rest of the run: it had been closed,
        so it could never answer again, and every later turn asked it anyway.
        """
        old, self._session = self._session, None
        try:
            old.close()
        except Exception:
            pass
        self._live_session()

    def _live_session(self):
        """The session to ask, built now if the last attempt to build one failed."""
        if self._session is None:
            self._session = self._new_session(self._seeded_options())
            self._baseline = None
        return self._session

    def _options(self):
        return _make_options(self._persona, self._model, self._actions)

    def _seeded_options(self):
        """Options for a fresh session that carries the recent turns forward as context."""
        return _make_options(self._persona + self._render_recent(), self._model, self._actions)

    def _render_recent(self):
        if not self._recent:
            return ""
        turns = "\n".join(f"{self._user}: {said}\nYou: {reply}" for said, reply in self._recent)
        return RECENT_HEADER + turns

    def warmup(self, announce=lambda line: None):
        """Pay the variable cold-start of the first query now, so the user's first real turn is fast.

        A machine nobody has signed in on still has to come UP. Launched from its icon there is no
        console for a traceback to land in, so a warmup that raises is an app that simply never
        appears - and the one thing he could do about it goes with it. So the state is said, as an
        app aside rather than in Excephalon's voice, and the window opens to a conversation that
        cannot answer yet rather than to nothing at all."""
        try:
            self._live_session().ask("Reply with just: ready")
        except BrainUnavailable:
            announce("(not signed in - run `claude` in a terminal and use /login, then restart; "
                     "nothing can be answered until then)")

    def close(self):
        if self._session is not None:
            self._session.close()
