import asyncio
import threading
import time
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, StreamEvent

from excephalon.sdk_session import (BrainUnavailable, SdkSession, _context_tokens, extract_text,
                                needs_sign_in, open_sign_in)


class FakeBlock:
    def __init__(self, text):
        self.text = text


class FakeMsg:
    def __init__(self, content):
        self.content = content


def test_needs_sign_in_spots_a_dead_sign_in_anywhere_in_the_cause_chain():
    # The observed failure was two layers deep: the retry's "authentication_failed" wrapping the
    # first ask's "the turn failed: success". Either layer alone must be enough - and an ordinary
    # brain failure must NOT be, or every hiccup would send the user off to sign in again.
    outer = BrainUnavailable("authentication_failed")
    outer.__cause__ = BrainUnavailable("the turn failed: success")

    assert needs_sign_in(outer) is True
    assert needs_sign_in(BrainUnavailable("OAuth session expired and could not be refreshed")) is True
    assert needs_sign_in(BrainUnavailable("the turn failed: error_during_execution")) is False


def test_open_sign_in_walks_the_user_to_the_claude_prompt_and_reports_failure_plainly():
    # "ideally Excephalon should do more than just tell me what to do, but pop open whatever I
    # need to do it and run it itself if possible." The terminal opens at the claude prompt; the
    # signing in stays his. False on any failure, so the spoken line never claims an open door
    # that is not there.
    opened = []

    assert open_sign_in(spawn=lambda command, **kw: opened.append(command)) is True
    assert "claude" in " ".join(opened[0])

    def refuse(command, **kw):
        raise OSError("no console to be had")

    assert open_sign_in(spawn=refuse) is False


def test_extract_text_concatenates_text_blocks_within_a_message():
    msgs = [FakeMsg([FakeBlock("Hey "), FakeBlock("the user.")])]

    assert extract_text(msgs) == "Hey the user."


def test_extract_text_keeps_only_the_final_message_not_the_running_narration():
    # a tool-using turn narrates each step; only the last message is the answer the user should hear.
    msgs = [
        FakeMsg([FakeBlock("I'll read the worktree's CLAUDE.md first.")]),
        FakeMsg([FakeBlock("Now let me find where the link is built.")]),
        FakeMsg([FakeBlock("Found it. The agent is on it now, over.")]),
    ]

    assert extract_text(msgs) == "Found it. The agent is on it now, over."


def test_extract_text_ignores_non_text_blocks():
    class ThinkingBlock:
        thinking = "let me think"

    msgs = [FakeMsg([ThinkingBlock(), FakeBlock("Hi.")])]

    assert extract_text(msgs) == "Hi."


def test_extract_text_skips_messages_without_content():
    class ResultLike:
        pass

    msgs = [ResultLike(), FakeMsg([FakeBlock("Only this.")])]

    assert extract_text(msgs) == "Only this."


def test_context_tokens_sums_every_input_side_count():
    # the true size of the context the model just processed = fresh input + both cache tiers;
    # that's what governs how slow the turn was, so it's what we watch to decide on compaction.
    usage = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 576,
        "cache_read_input_tokens": 21319,
        "output_tokens": 4,  # output is NOT context the next turn re-processes
    }
    assert _context_tokens(usage) == 2 + 576 + 21319


def test_context_tokens_is_zero_when_usage_is_missing_or_empty():
    assert _context_tokens(None) == 0
    assert _context_tokens({}) == 0


class FakeClient:
    """A stand-in ClaudeSDKClient whose async methods just record that they ran, so the session's
    threading/loop plumbing can be tested without the real CLI."""

    def __init__(self, *, options=None):
        self.interrupted = threading.Event()
        self.disconnected = threading.Event()

    async def connect(self):
        pass

    async def interrupt(self):
        self.interrupted.set()

    async def disconnect(self):
        self.disconnected.set()


def _finished():
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id="s", usage={})


class StreamingClient(FakeClient):
    """A client that streams a turn back: a tool call, its output, then the agent's words."""

    STREAM = (
        FakeMsg([FakeBlock("Confirmed red.")]),
        FakeMsg([FakeBlock("Now the implementation:")]),
        _finished(),
    )

    async def query(self, prompt):
        self.asked = prompt

    async def receive_response(self):
        for message in self.STREAM:
            yield message


def test_every_message_reaches_the_caller_whole_as_it_streams():
    # It used to boil each message down to its text right here, so the tool calls, the diffs and
    # the command output were gone before anything downstream could write them anywhere.
    client = StreamingClient()
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: client)
    seen = []

    reply = session.ask("do the thing", on_message=seen.append)

    assert seen == list(StreamingClient.STREAM)  # every message, untouched
    assert reply == "Now the implementation:"  # the reply is still only its final word
    session.close()


def _delta(text, index=0):
    return StreamEvent(uuid="u", session_id="s",
                       event={"type": "content_block_delta", "index": index,
                              "delta": {"type": "text_delta", "text": text}})


class PartialStreamingClient(FakeClient):
    """A client running with partial messages on: deltas as the text is written, then the whole."""

    STREAM = (
        _delta("Star"),
        StreamEvent(uuid="u", session_id="s",
                    event={"type": "content_block_delta",
                           "delta": {"type": "thinking_delta", "thinking": "hmm"}}),
        _delta("ting now."),
        FakeMsg([FakeBlock("Starting now.")]),
        _finished(),
    )

    async def query(self, prompt):
        self.asked = prompt

    async def receive_response(self):
        for message in self.STREAM:
            yield message


def test_text_deltas_reach_the_caller_as_the_reply_is_being_written():
    # First words within a couple of seconds: a voice needs the reply as it is being written, not
    # after the turn has finished. Only the user-facing text streams - thinking stays out of it.
    client = PartialStreamingClient()
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: client)
    heard = []

    reply = session.ask("go", on_text=heard.append)

    assert heard == ["Star", "ting now."]
    assert reply == "Starting now."  # the settled reply is unchanged by having streamed
    session.close()


def _block_start():
    return StreamEvent(uuid="u", session_id="s",
                       event={"type": "content_block_start", "index": 0,
                              "content_block": {"type": "text", "text": ""}})


class TalksAroundAToolClient(FakeClient):
    """A tool-using turn: text, then a tool call, then more text - all of it spoken as it streams."""

    STREAM = (
        _block_start(),
        _delta("Right - that isn't real verification.", index=0),
        FakeMsg([FakeBlock("Right - that isn't real verification.")]),
        _block_start(),
        _delta("I'm having the agent stand up a test instance.", index=0),
        FakeMsg([FakeBlock("I'm having the agent stand up a test instance.")]),
        _finished(),
    )

    async def query(self, prompt):
        self.asked = prompt

    async def receive_response(self):
        for message in self.STREAM:
            yield message


def test_the_reply_is_everything_that_was_spoken_not_just_the_last_message():
    # "what it said aloud didn't always match what was printed... before that it spoke aloud
    # something that included 'you're absolutely right'". The voice speaks every text delta; the
    # record kept only the LAST message's text, so the bubble showed a fraction of what was heard.
    # The reply is now the same words the deltas carried - all of them.
    client = TalksAroundAToolClient()
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: client)
    heard = []

    reply = session.ask("go", on_text=heard.append)

    assert reply == ("Right - that isn't real verification.\n"
                     "I'm having the agent stand up a test instance.")
    assert "".join(heard) == reply  # the ear and the record got byte-identical words
    session.close()


def test_a_block_that_starts_flush_against_the_last_gets_a_seam():
    # Two text blocks can butt together with no whitespace between them; jammed, the sentence
    # splitter reads "...verification.I'm" as one word and the screen shows a run-on.
    client = TalksAroundAToolClient()
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: client)

    reply = session.ask("go")

    assert "verification.\nI'm having" in reply
    session.close()


class SignedOutClient(FakeClient):
    """The CLI with nobody signed in. Copied from a real capture: the refusal comes back dressed
    as the model's own reply, and the only things saying otherwise are `error` and the synthetic
    model name."""

    async def query(self, prompt, session_id=None):
        pass

    async def receive_response(self):
        message = FakeMsg([FakeBlock("Not logged in · Please run /login")])
        message.error = "authentication_failed"
        message.model = "<synthetic>"
        yield message
        yield ResultMessage(subtype="success", duration_ms=51, duration_api_ms=0, is_error=True,
                            num_turns=1, session_id="s", usage={})


def test_the_clis_own_refusal_never_becomes_something_Excephalon_said():
    # Launched from its icon the app inherits no shell, so a Mac that has not been signed in yet
    # got this back and SAID it: "entity> Not logged in - Please run /login". The CLI's words, in
    # Excephalon's voice, in its bubble - which is the one thing the whole insulation exists to
    # prevent, and worse than useless besides, since there is no /login to type at a microphone.
    # The turn did not fail from the app's side: `is_error` rides on a subtype of "success", and
    # the text arrives as an ordinary assistant reply. What gives it away is that the message
    # carries an `error` at all - so this catches a revoked token or an expired plan the same way,
    # without matching on any wording.
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: SignedOutClient())

    with pytest.raises(BrainUnavailable) as raised:
        session.ask("hi")

    assert "authentication_failed" in str(raised.value)  # the cause, for the durable record
    session.close()


class SilentlyFailingClient(FakeClient):
    """The same refusal on a REBUILT session: no assistant message this time, just the result
    saying the turn failed."""

    async def query(self, prompt, session_id=None):
        pass

    async def receive_response(self):
        yield ResultMessage(subtype="success", duration_ms=40, duration_api_ms=0, is_error=True,
                            num_turns=1, session_id="s", usage={})


def test_a_turn_that_failed_with_nothing_said_is_a_failure_not_an_empty_reply():
    # Catching only the assistant-shaped refusal fixed the first ask and not the second: the brain
    # retries once on a fresh session, and on that one the CLI skipped the synthetic message and
    # only flagged the result. The empty string came back as a reply, `_answer` found nothing to
    # say, and the turn passed in total silence - he types, it thinks, and nothing ever comes back.
    # A wedged brain that says nothing is the failure the error reply exists for.
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: SilentlyFailingClient())

    with pytest.raises(BrainUnavailable):
        session.ask("hi")

    session.close()


def test_interrupt_runs_the_clients_interrupt_on_the_session_loop():
    client = FakeClient()
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: client)

    session.interrupt()

    assert client.interrupted.is_set()  # the cancel was driven on the session's own event loop
    session.close()


def test_a_closed_session_refuses_work_instead_of_hanging_on_it_forever():
    # `close()` stops this session's private event loop, and a coroutine handed to a stopped loop is
    # queued and never run - so `.result()` waits for something that cannot happen. Anything holding
    # a closed session then stops answering ALTOGETHER rather than failing: no reply, no error, no
    # end to the wait. A brain rebuild that fails leaves exactly that session in place.
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: FakeClient())
    session.close()

    outcome = []
    asking = threading.Thread(target=lambda: outcome.append(_ask_quietly(session)), daemon=True)
    asking.start()
    asking.join(2.0)

    assert not asking.is_alive(), "asking a closed session never came back"
    assert isinstance(outcome[0], Exception)  # it fails, and a failure is something callers can see


def _ask_quietly(session):
    try:
        return session.ask("are you there")
    except Exception as exc:
        return exc


class RefusingClient(FakeClient):
    """A client whose CLI will not start - the failure `SdkSession.__init__` has to survive."""

    async def connect(self):
        raise RuntimeError("the CLI would not start")


def _capturing_factory(seen):
    def factory(*, options):
        seen.append(options)
        return FakeClient()
    return factory


def test_a_system_prompt_is_handed_over_as_a_file_rather_than_spelled_out():
    # The SDK writes the system prompt out on the CLI's command line, and Windows refuses a command
    # line over 32767 characters - as a FileNotFoundError, which the SDK reports as the CLI being
    # missing. So a persona that outgrew that budget made EVERY session fail to start, saying
    # "Claude Code not found at: ...claude.exe" about a file that was sitting right there. A path
    # is a few dozen characters however long the persona gets.
    persona = "x" * 40000
    seen = []
    session = SdkSession(ClaudeAgentOptions(system_prompt=persona),
                         client_factory=_capturing_factory(seen))

    handed = seen[0].system_prompt
    assert handed["type"] == "file"
    assert Path(handed["path"]).read_text(encoding="utf-8") == persona
    session.close()


def test_the_spilled_system_prompt_is_cleaned_up_when_the_session_ends():
    # The brain builds a fresh session on every compaction and every reconnect, so a file left
    # behind each time is an accumulating pile of copies of the user's own standing profile.
    seen = []
    session = SdkSession(ClaudeAgentOptions(system_prompt="who you are"),
                         client_factory=_capturing_factory(seen))
    spilled = Path(seen[0].system_prompt["path"])

    session.close()

    assert not spilled.exists()


def test_a_session_that_never_connects_takes_its_spilled_prompt_back_with_it():
    # The failure path is the one that repeats: a brain whose session died rebuilds on every turn,
    # and a rebuild that also fails never reaches close(). Thirty-four minutes of that is thirty-four
    # minutes of dropping copies of the user's profile into the temp directory.
    seen = []

    def factory(*, options):
        seen.append(options)
        return RefusingClient()

    with pytest.raises(RuntimeError):
        SdkSession(ClaudeAgentOptions(system_prompt="who you are"), client_factory=factory)

    assert not Path(seen[0].system_prompt["path"]).exists()


def _loops_built(monkeypatch):
    """Every event loop `SdkSession` opens while this is in place, so a test can look at one it
    never got a handle on - a session that fails to build never returns."""
    built = []
    opening = asyncio.new_event_loop

    def remember():
        loop = opening()
        built.append(loop)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", remember)
    return built


def _settled(loop, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not loop.is_closed() and time.monotonic() < deadline:
        time.sleep(0.01)
    return loop.is_closed()


def test_a_session_that_never_connects_shuts_down_the_loop_it_opened(monkeypatch):
    # The loop and its thread are started BEFORE the connect that can fail, and a failed build never
    # reaches close(). The brain rebuilds on every turn while it's broken, so that is one abandoned
    # event loop per turn, each still spinning, for as long as the trouble lasts - thirty-four
    # minutes of it the morning this was found.
    built = _loops_built(monkeypatch)

    with pytest.raises(RuntimeError):
        SdkSession(ClaudeAgentOptions(), client_factory=lambda options: RefusingClient())

    assert _settled(built[0]), "the failed session left its event loop running"


def test_closing_a_session_releases_its_loop_rather_than_only_stopping_it(monkeypatch):
    # A stopped loop still holds its selector and its thread. The brain opens a fresh session on
    # every compaction and every reconnect, so what is only stopped accumulates for the whole run.
    built = _loops_built(monkeypatch)
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: FakeClient())

    session.close()

    assert _settled(built[0]), "close() left the event loop open"


def test_the_sessions_id_is_kept_for_resuming_it_later():
    # An agent's whole memory lives in its CLI session; the id is what lets a restarted Excephalon
    # reattach instead of stranding the fleet - the old failure was "agents die when Excephalon dies".
    client = StreamingClient()
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: client)

    session.ask("do the thing")

    assert session.last_session_id == "s"  # from the turn's closing result message
    session.close()
