import pytest

from excephalon.brain_sdk import (
    DEFAULT_BRAIN_MODEL,
    DEFAULT_PERSONA,
    BrainInterrupted,
    SdkBrain,
    _is_usage_limit,
    _make_options,
)
from excephalon.errands import ERRAND_MODEL
from excephalon.memory import compose_persona
from excephalon.models import FAMILIES
from excephalon.naming import NAME_MODEL

_LIMIT = "You've hit your monthly spend limit - raise it at claude.ai/settings/usage"


def test_a_refreshed_persona_is_what_the_next_session_starts_from():
    # A system prompt cannot be swapped under a running conversation, so the live session keeps
    # the one it opened with and the change rides in as a note. This is for what comes after: a
    # compaction reseed would otherwise resurrect the world as it was when the app booted.
    made = []

    class _Session:
        def __init__(self, options):
            made.append(options)

        def close(self):
            pass

    brain = SdkBrain(persona="the world at boot", session_factory=_Session)

    brain.refresh_persona("the world as he has since edited it")
    brain._session = brain._new_session(brain._options())  # what a reseed does

    assert "the world as he has since edited it" in str(made[-1])


def test_the_shipped_persona_is_personalised_by_the_profile_not_by_the_source():
    # An edit that drops the placeholder would leave every user addressed as "the user" - and a
    # name written in its place would be wrong for everyone but its author.
    persona = compose_persona(DEFAULT_PERSONA, "# Ada - standing profile\n\nintro\n")

    assert "Ada's voice companion" in persona


def test_the_persona_teaches_editing_standing_instructions_in_both_directions():
    # "I don't have a way to remove standing instructions - I can only add new ones. You'll need
    # to manually remove the old one" - spoken about the user's own Config card. The levers exist
    # now; this pins the sentence that tells the brain so, since a lever it is never told about
    # is a lever it will deny having.
    assert "update_persona" in DEFAULT_PERSONA
    assert "drop_instruction" in DEFAULT_PERSONA


def test_is_usage_limit_spots_the_cli_spend_notice():
    assert _is_usage_limit(_LIMIT) is True
    assert _is_usage_limit("You've hit your usage limit.") is True
    assert _is_usage_limit("Merged it - the drive icon opens the folder now.") is False


def test_a_remember_false_turn_stays_out_of_the_recent_window():
    # the heartbeat's silent "any agent news?" polls must not crowd out the real conversation.
    class Session:
        def __init__(self, options):
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            return f"reply to {message}"

        def close(self):
            pass

    brain = SdkBrain(session_factory=Session)
    brain.respond("what's the plan for today")
    brain.respond("HEARTBEAT poll", remember=False)

    carried = [utterance for utterance, _ in brain._recent]
    assert "what's the plan for today" in carried
    assert "HEARTBEAT poll" not in carried  # the poll didn't enter the carried-forward memory


class _Echoing:
    """A session that answers, and keeps every prompt it was asked."""

    def __init__(self, options):
        self.asked = []
        self.last_context_tokens = 0

    def ask(self, message, on_text=None):
        self.asked.append(message)
        return "sure"

    def close(self):
        pass


def test_an_ask_busy_with_tool_calls_is_not_shed_as_a_wedge():
    # The shed exists for a stream that has ALREADY died without raising. Bounded on elapsed time
    # instead, it also killed the asks that were doing real work: reading his calendar is minutes
    # of tool calls with no words in them, and the app declared the session dead mid-errand. Every
    # message the model sends resets the clock, so only silence is read as death.
    import time as _time

    class Working:
        def __init__(self, options):
            self.last_context_tokens = 0

        def ask(self, message, on_message=None, on_text=None):
            for _ in range(8):  # eight round-trips, each well inside the quiet bound
                _time.sleep(0.02)
                on_message("a tool call")
            return "You've got three things today."

        def close(self):
            raise AssertionError("a working session must never be shed")

    brain = SdkBrain(session_factory=Working)

    assert brain.respond("walk me through my day", deadline=0.1) == "You've got three things today."


def test_a_session_that_says_nothing_at_all_is_still_shed():
    # The other half of the same rule: a stream that sends NOTHING - no words, no tool calls - has
    # died without raising, and one of those held the brain's lock for a whole evening. Silence is
    # still the test, and the session is still let go.
    import threading as _threading

    let_go = _threading.Event()
    built, closed = [], []

    class Dead:
        def __init__(self, options):
            built.append(True)
            self.last_context_tokens = 0

        def ask(self, message, on_message=None, on_text=None):
            let_go.wait(2.0)
            return "an answer at last"

        def close(self):
            closed.append(True)
            let_go.set()

    brain = SdkBrain(session_factory=Dead)

    assert brain.respond("ship it", deadline=0.1) == "an answer at last"
    assert closed        # the silent session was let go rather than waited on
    assert len(built) == 2  # ...and the answer came from the fresh one built in its place


def test_a_draft_that_was_never_spoken_is_taken_back_on_the_next_ask():
    # Composing is not delivering, and the live session cannot tell the difference: every line it
    # writes sits in its own history, where the only reading available is that it was said. The
    # app throws finished lines away routinely - a narration that claimed unlanded work was live,
    # one the deadline gave up waiting for, a greeting `unfit` refused - and each one left the
    # model holding a sentence he never heard. Worse where it counts: a draft dropped FOR a plain
    # notice leaves it holding the draft and hearing the notice, which is "it repeats it twice in
    # a row like an insane person" manufactured inside one turn's memory.
    sessions = []
    brain = SdkBrain(session_factory=lambda options: sessions.append(_Echoing(options)) or sessions[-1])

    brain.retract("The spinner fix is live in Highdeas now.")
    brain.respond("how's it going")

    [asked] = sessions[-1].asked
    assert "The spinner fix is live in Highdeas now." in asked
    assert "never reached the user" in asked
    assert asked.endswith("how's it going")


def test_a_retraction_is_made_once_and_not_again():
    sessions = []
    brain = SdkBrain(session_factory=lambda options: sessions.append(_Echoing(options)) or sessions[-1])

    brain.retract("a line nobody heard")
    brain.respond("first")
    brain.respond("second")

    first, second = sessions[-1].asked
    assert "a line nobody heard" in first
    assert "a line nobody heard" not in second  # said once; a standing note would be its own noise


def test_what_it_remembers_saying_is_what_actually_sounded():
    # The window a compaction or a restart rebuilds the conversation from used to be filled at
    # COMPOSITION. Agent news is composed minutes before it is spoken and sometimes never spoken
    # at all, so that window carried lines he never heard across the reset - and the model went on
    # reasoning from them. It is filled from the delivery now, and an app-authored turn has no
    # words of his in front of it, because he did not ask for it.
    brain = SdkBrain(session_factory=_Echoing, user="Ada")

    brain.spoke("The drive link is fixed - want to look?")

    assert brain._recent[-1] == (None, "The drive link is fixed - want to look?")
    assert "Ada:" not in brain._render_recent()
    assert "You: The drive link is fixed - want to look?" in brain._render_recent()


def test_nothing_is_remembered_or_retracted_for_an_empty_line():
    brain = SdkBrain(session_factory=_Echoing)

    brain.spoke("   ")
    brain.retract("")

    assert list(brain._recent) == []
    assert brain._with_retractions("his words") == "his words"


def test_a_brain_seeded_with_past_turns_starts_mid_conversation():
    # A restart used to greet them as a stranger five minutes after they'd been mid-task - that is
    # the "breaking the current session" half of their reload ticket. Seeded, the FIRST session opens
    # already carrying the recent back-and-forth, exactly as a compaction reseed does.
    made = []

    class Session:
        def __init__(self, options):
            made.append(options)
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            return "reply"

        def close(self):
            pass

    SdkBrain(session_factory=Session, user="Ada",
             seed_turns=[("how is the agent doing", "Still working.")])

    prompt = made[0].system_prompt
    assert "Ada: how is the agent doing" in prompt and "You: Still working." in prompt
    assert "continuity" in prompt  # the same framing a compaction reseed carries


def test_an_unseeded_brain_starts_clean():
    made = []

    class Session:
        def __init__(self, options):
            made.append(options)
            self.last_context_tokens = 0

        def close(self):
            pass

    SdkBrain(session_factory=Session)

    assert "continuity" not in made[0].system_prompt  # no seed, no invented history


def test_a_rebuild_that_fails_leaves_no_session_rather_than_a_dead_one():
    # "It has never said that and recovered." A rebuild that itself failed left `self._session`
    # pointing at the session it had just CLOSED, and a closed session cannot answer again - so one
    # bad moment (this morning it was a CLINotFoundError) became the rest of the run. Nothing is
    # kept now; the next turn builds a fresh one and gets on with it.
    made = []

    class RefusesToBeBuiltOnce:
        def __init__(self, options):
            made.append(self)
            self.closed = False
            self.last_context_tokens = 0
            if len(made) == 2:  # the rebuild can't reach the CLI either
                raise RuntimeError("CLI not found")

        def ask(self, message, on_text=None):
            if self.closed or self is made[0]:
                raise RuntimeError("this session is gone")
            return "back with you"

        def close(self):
            self.closed = True

    brain = SdkBrain(session_factory=RefusesToBeBuiltOnce)

    with pytest.raises(RuntimeError):
        brain.respond("are you there")  # this turn is lost: the rebuild failed too

    assert brain._session is None  # and nothing dead was left behind to be asked again
    assert brain.respond("are you there") == "back with you"  # so the next turn simply works


def test_respond_rebuilds_the_session_when_it_hits_a_usage_limit_then_recovers():
    made = []

    class LimitedThenBackSession:
        def __init__(self, options):
            made.append(self)
            self.closed = False
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            if made.index(self) == 0:  # the wedged session parrots the spend-limit notice
                return _LIMIT
            return "Merged. The drive icon opens the folder now."  # a fresh session, usage back

        def close(self):
            self.closed = True

    brain = SdkBrain(session_factory=LimitedThenBackSession)

    assert brain.respond("merge it") == "Merged. The drive icon opens the folder now."
    assert len(made) == 2 and made[0].closed  # it rebuilt past the wedged session, didn't loop


def test_a_persistent_usage_limit_is_surfaced_once_not_looped_forever():
    made = []

    class StillLimitedSession:
        def __init__(self, options):
            made.append(self)
            self.closed = False
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            return _LIMIT  # usage genuinely still gone on every session

        def close(self):
            self.closed = True

    brain = SdkBrain(session_factory=StillLimitedSession)

    assert _is_usage_limit(brain.respond("hi"))  # says it once
    assert len(made) == 2  # exactly one rebuild+retry, not an unbounded loop


def test_interrupt_cancels_the_current_session():
    made = []

    class InterruptibleSession:
        def __init__(self, options):
            made.append(self)
            self.interrupted = False
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            return "hi"

        def interrupt(self):
            self.interrupted = True

        def close(self):
            pass

    brain = SdkBrain(session_factory=InterruptibleSession)
    brain.interrupt()

    assert made[0].interrupted is True  # the barge-in was forwarded to the live session


def test_respond_does_not_retry_after_an_interrupt():
    # A barge-in lands mid-ask and the stream aborts. respond must NOT reconnect-and-re-ask
    # (that would re-run the very work we cancelled) - it surfaces the cancellation instead.
    made = []

    class AbortedSession:
        def __init__(self, options):
            made.append(self)
            self.asks = 0
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            self.asks += 1
            brain.interrupt()  # they barge in while we're waiting on the model
            raise RuntimeError("stream aborted by interrupt")

        def interrupt(self):
            pass

        def close(self):
            pass

    brain = SdkBrain(session_factory=AbortedSession)

    with pytest.raises(BrainInterrupted):
        brain.respond("a big job")
    assert made[0].asks == 1  # asked once
    assert len(made) == 1  # and did NOT reconnect a fresh session to retry


def test_respond_discards_a_partial_reply_after_an_interrupt():
    # When the interrupt lands, the CLI may still return a half-finished reply. respond must drop
    # it - not speak it, and not seed it into the history carried across a compaction.
    class PartialSession:
        def __init__(self, options):
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            brain.interrupt()
            return "half a sentence they never asked to h"

        def interrupt(self):
            pass

        def close(self):
            pass

    brain = SdkBrain(session_factory=PartialSession)

    with pytest.raises(BrainInterrupted):
        brain.respond("x")
    assert list(brain._recent) == []  # the abandoned partial was not remembered


def test_a_fresh_respond_after_an_interrupt_works_normally():
    # The cancel flag from one turn must not gag the next turn.
    class Session:
        def __init__(self, options):
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            return f"reply to {message}"

        def interrupt(self):
            pass

        def close(self):
            pass

    brain = SdkBrain(session_factory=Session)
    brain.interrupt()  # a leftover cancel from a previous turn

    assert brain.respond("hello") == "reply to hello"  # the new turn is not cancelled


def test_brain_is_isolated_from_user_settings_and_hooks():
    # The fix for the leak: load NO user/project/local settings, so Excephalon never
    # inherits the global coding CLAUDE.md or the Stop hook that enforces a quoted-block
    # reply format (which otherwise bleeds that format in and explodes latency).
    opts = _make_options("PERSONA", "sonnet")

    assert list(opts.setting_sources) == []
    assert opts.system_prompt == "PERSONA"


def test_the_brain_has_no_built_in_tools_only_the_typed_actions():
    # Its old Bash/Read tools are how a status question became fifteen minutes of digging. The
    # fast brain talks and pulls levers; investigation belongs to the agents it starts.
    actions = object()
    opts = _make_options("PERSONA", "haiku", actions)

    assert opts.tools == []  # every built-in tool is off
    assert opts.mcp_servers == {"excephalon": actions}
    assert all(name.startswith("mcp__excephalon__") for name in opts.allowed_tools)
    assert opts.include_partial_messages is True  # the voice speaks the reply as it is written


def test_the_brain_runs_on_a_capable_model():
    # The seam between the app and the user is the hardest judgement in this codebase, and it was
    # given the fastest model in the family. Nearly every reply he has called insane was a
    # judgement failure rather than a slow one: retelling news he had just been given, restating
    # one fact in two shapes inside a single reply, welding two topics together, asking a question
    # and then rambling past it, calling delivered work unreviewed. Some fifty code gates and
    # twelve thousand words of standing law grew here to compensate, and the gates began breaking
    # each other. Latency is the problem this codebase already solved - the voice speaks each
    # sentence as it is written - so the tier that thinks is the one that talks.
    made = []

    class Session:
        def __init__(self, options):
            made.append(options)
            self.last_context_tokens = 0

        def close(self):
            pass

    SdkBrain(session_factory=Session)

    assert made[0].model == FAMILIES["sonnet"]


def test_the_fast_tier_is_kept_for_the_backchannel():
    # The fast model still has its jobs - the ones the user never hears as a voice: distilling an
    # agent's task down to a name, and the errand hand's fetch-and-carry. Pinned together with the
    # brain's own tier so a future edit cannot quietly put the talker back on the label model, nor
    # spend a thinking model on a three-word filename.
    assert NAME_MODEL == FAMILIES["haiku"]
    assert ERRAND_MODEL == FAMILIES["haiku"]
    assert DEFAULT_BRAIN_MODEL != NAME_MODEL


def test_text_deltas_stream_through_respond_to_the_caller():
    class Session:
        def __init__(self, options):
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            for piece in ("Star", "ting now."):
                on_text(piece)
            return "Starting now."

        def close(self):
            pass

    brain = SdkBrain(session_factory=Session)
    heard = []

    assert brain.respond("go", on_text=heard.append) == "Starting now."
    assert heard == ["Star", "ting now."]


def test_respond_rebuilds_a_wedged_session_and_retries_once():
    made = []

    class FlakySession:
        def __init__(self, options):
            made.append(self)
            self.closed = False
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            if made.index(self) == 0:  # the first session is wedged
                raise RuntimeError("connection dropped")
            return "recovered reply"

        def close(self):
            self.closed = True

    brain = SdkBrain(session_factory=FlakySession)

    assert brain.respond("hi") == "recovered reply"
    assert len(made) == 2  # rebuilt the session after the error
    assert made[0].closed  # and closed the wedged one


class GrowingSession:
    """A fake whose context grows by `per_turn` tokens each ask and echoes the message, so a test
    can watch context climb and then check what the reseeded session was handed."""

    def __init__(self, options, *, per_turn=20000):
        self.options = options
        self.asks = []
        self.closed = False
        self.last_context_tokens = 0
        self._per_turn = per_turn

    def ask(self, message, on_text=None):
        self.asks.append(message)
        self.last_context_tokens += self._per_turn
        return f"reply to {message}"

    def close(self):
        self.closed = True


def _growing_factory(sessions, *, per_turn=20000):
    def factory(options):
        s = GrowingSession(options, per_turn=per_turn)
        sessions.append(s)
        return s

    return factory


def test_stays_on_one_session_while_context_stays_small():
    sessions = []
    brain = SdkBrain(session_factory=_growing_factory(sessions, per_turn=1000), compact_growth_budget=30000)
    for _ in range(6):
        brain.respond("hi")

    assert len(sessions) == 1  # 6 small turns never crossed the budget, so no compaction


def test_compacts_onto_a_fresh_session_when_context_grows_past_budget():
    sessions = []
    brain = SdkBrain(
        persona="BASE PERSONA", session_factory=_growing_factory(sessions), compact_growth_budget=30000
    )
    # turn1 -> 20k (baseline). turn2 -> 40k. turn3 -> 60k. turn4 sees 60k-20k=40k >= 30k -> compact.
    replies = [brain.respond(f"q{i}") for i in range(4)]

    assert len(sessions) == 2  # compacted exactly once
    assert sessions[0].closed  # the bloated session was closed
    assert replies[-1] == "reply to q3"  # the caller got its real reply, uninterrupted
    # compaction is a cheap reseed, NOT an expensive summary call - the old session got no extra ask
    assert sessions[0].asks == ["q0", "q1", "q2"]


def test_the_reseeded_session_carries_the_base_persona_plus_the_recent_turns_verbatim():
    sessions = []
    brain = SdkBrain(
        persona="BASE PERSONA", session_factory=_growing_factory(sessions), compact_growth_budget=30000
    )
    for i in range(4):
        brain.respond(f"q{i}")

    seeded = sessions[1].options.system_prompt
    assert seeded.startswith("BASE PERSONA")  # the base persona is preserved, not lost
    # the turns that happened before the reset are carried forward verbatim, so nothing is fabricated
    assert "q0" in seeded and "q2" in seeded
    assert "reply to q1" in seeded


def test_the_carried_turns_are_labelled_with_the_users_own_name():
    # The reseed reads the recent turns back as a dialogue, so the label has to be who the user
    # actually is - and that name reaches the brain from the caller, never from the source.
    sessions = []
    brain = SdkBrain(
        user="Ada", session_factory=_growing_factory(sessions), compact_growth_budget=30000
    )
    for i in range(4):
        brain.respond(f"q{i}")

    assert "Ada: q2" in sessions[1].options.system_prompt


def test_only_the_most_recent_turns_are_carried_across_a_reset():
    sessions = []
    brain = SdkBrain(
        session_factory=_growing_factory(sessions),
        compact_growth_budget=30000,
        recent_turns_kept=2,
    )
    for i in range(4):
        brain.respond(f"q{i}")

    seeded = sessions[1].options.system_prompt
    assert "q2" in seeded  # kept (recent)
    assert "q0" not in seeded  # dropped - only the last 2 turns are carried, bounding the reseed size


def test_a_compacted_session_does_not_immediately_compact_again():
    sessions = []
    brain = SdkBrain(session_factory=_growing_factory(sessions), compact_growth_budget=30000)
    for _ in range(7):  # enough to cross the budget a second time if the baseline reset works
        brain.respond("hi")

    # first epoch: turns 1-3 on session0, compact at turn4 -> session1 re-baselines at 20k;
    # session1 grows 20k,40k,60k over turns 4-6, compact again at turn7 -> session2. Two compactions.
    assert len(sessions) == 3
    assert sessions[1].closed


def test_a_hung_ask_is_shed_within_the_deadline_and_the_next_turn_gets_a_fresh_session():
    # The silent wedge: an ask that never raises and never returns. One froze a whole evening -
    # the landing narration hung at 21:24 holding the one session, his 21:46 question sat at
    # "(thinking…)" forever, and every submission after that was never even read. Bounded now:
    # past the deadline the dead session is closed and the turn fails fast, and the turn after
    # that runs on a fresh session.
    import threading

    sessions = []

    class SessionThatHangsOnceThenAnswers:
        def __init__(self, options):
            self.last_context_tokens = 0
            self.released = threading.Event()
            self.closed = False
            sessions.append(self)

        def ask(self, message, on_text=None):
            if len(sessions) == 1:  # only the first session is the dead one
                self.released.wait(5.0)
                raise RuntimeError("stream torn down")
            return f"reply to {message}"

        def close(self):
            self.closed = True
            self.released.set()  # closing the dead session is what makes its ask finally raise

        def interrupt(self):
            pass

    brain = SdkBrain(session_factory=SessionThatHangsOnceThenAnswers)

    # The wedge is healed INSIDE the turn: the dead session is shed at the deadline and the
    # existing retry-once path answers on a fresh one - so the user gets their reply, not an
    # error, and only a wedge that survives the retry too surfaces at all.
    assert brain.respond("are you there", deadline=0.2) == "reply to are you there"

    assert sessions[0].closed  # the wedged session was shed, not kept
    assert len(sessions) == 2  # ...and the answer came from its replacement


def test_a_turn_stuck_behind_a_wedged_ask_frees_the_lock_by_shedding_the_session():
    # The lock half of the same failure: the hung ask HOLDS the brain's one-at-a-time lock, so
    # every later turn blocked before any recovery could run. A turn that cannot even acquire
    # the lock within its deadline closes the session out from under the zombie - whose ask then
    # raises and releases the lock - and proceeds.
    import threading

    sessions = []

    class SessionThatHangsUntilClosed:
        def __init__(self, options):
            self.last_context_tokens = 0
            self.released = threading.Event()
            sessions.append(self)

        def ask(self, message, on_text=None):
            if len(sessions) == 1 and message == "the one that hangs":
                self.released.wait(5.0)
                raise RuntimeError("stream torn down")
            return f"reply to {message}"

        def close(self):
            self.released.set()

        def interrupt(self):
            pass

    brain = SdkBrain(session_factory=SessionThatHangsUntilClosed)

    def swallow():
        try:
            brain.respond("the one that hangs")
        except Exception:
            pass

    stuck = threading.Thread(target=swallow, daemon=True)
    stuck.start()
    for _ in range(200):  # wait until the zombie actually holds the lock
        if sessions and not sessions[0].released.is_set() and brain._respond_lock.locked():
            break
        threading.Event().wait(0.01)

    assert brain.respond("hello?", deadline=0.3) == "reply to hello?"
    stuck.join(timeout=2.0)


def test_a_lock_whose_holder_survives_the_shed_is_abandoned_not_waited_out(tmp_path, monkeypatch):
    # The wedge that outlived every remedy: the holder was stuck somewhere no session close
    # reaches, so "the session is wedged" answered every later ask until the app was restarted -
    # the brain deaf for a real evening. The lock is a means, not a principal: when shedding
    # does not free it, it is ABANDONED - the stranded thread keeps the old object, which
    # nothing else ever touches again - and the turn proceeds on a fresh lock and session,
    # writing the holder's stack down at the moment of failure.
    from excephalon import brain_sdk

    monkeypatch.setattr(brain_sdk, "WEDGE_EVIDENCE_PATH", tmp_path / "brain-wedge.log")

    class FineSession:
        def __init__(self, options):
            self.last_context_tokens = 0

        def ask(self, message, on_text=None):
            return f"reply to {message}"

        def close(self):
            pass

        def interrupt(self):
            pass

    brain = SdkBrain(session_factory=FineSession)
    brain._respond_lock.acquire()  # a holder no shed can unstick; it will never release

    assert brain.respond("hello?", deadline=0.2) == "reply to hello?"
    assert "abandoned" in (tmp_path / "brain-wedge.log").read_text(encoding="utf-8")
    assert brain.respond("again?", deadline=0.2) == "reply to again?"  # the fresh lock is free


def test_a_machine_that_is_not_signed_in_still_opens_and_says_so():
    # Nothing can be answered until he signs in - but the app crashing on the way up is not how he
    # finds that out. Launched from its icon there is no console for a traceback to land in, so a
    # warmup that raises is an app that simply never appears. It says the one thing he can act on
    # instead, as an app aside: this is the machine reporting its own state, not Excephalon
    # talking, and there is no /login to type at a microphone anyway.
    from excephalon.sdk_session import BrainUnavailable

    class SignedOutSession:
        def ask(self, prompt, **kwargs):
            raise BrainUnavailable("authentication_failed")

    brain = SdkBrain(persona="p", session_factory=lambda options: SignedOutSession())
    said = []

    brain.warmup(announce=said.append)

    assert len(said) == 1
    assert "sign" in said[0].lower() and "claude" in said[0].lower()


def test_every_session_the_app_spawns_is_pinned_to_its_own_servers():
    # Account-level claude.ai connectors attach to ANY session the CLI opens, and a headless one
    # that tries to OAuth them has no browser and no user (anthropics/claude-code#36060). It
    # wedged a real evening: the brain's replacement session hung on initialize for 90 seconds
    # ("Control request timeout: initialize"), the error reply was spoken, and his quit stalled
    # into a force-quit. Every session this app spawns is headless, so every one is pinned.
    from excephalon.brain_sdk import _make_options
    from excephalon.foreman import _foreman_options
    from excephalon.supervised_agent import _agent_options

    assert _make_options("p", "m").extra_args == {"strict-mcp-config": None}
    assert _foreman_options("/wt").extra_args == {"strict-mcp-config": None}
    assert _agent_options("/wt", "m", "high", lambda *a: None).extra_args == {"strict-mcp-config": None}
