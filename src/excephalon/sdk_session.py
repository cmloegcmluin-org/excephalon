"""A persistent Claude session held open on a private background event loop.

The SDK is async; this wraps one `ClaudeSDKClient` so the rest of the app can talk to it with
a plain synchronous `ask(prompt) -> text`. Shared by the companion brain (`SdkBrain`) and the
supervised coding agents (`SupervisedAgent`) - they differ only in the options they pass in.

It is also where a session's system prompt is kept off the command line - see
`_spill_system_prompt`, which exists because Windows put a ceiling on how long a persona could get.
"""

import asyncio
import os
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient, ResultMessage


class BrainUnavailable(RuntimeError):
    """The CLI answered instead of the model - it could not reach one on our behalf.

    Raised rather than returned, because everything downstream treats a returned string as words
    Excephalon said: the conversation speaks it, the record files it in its bubble, and the ledger
    reads it back next turn as its own. Raising puts it on the path already built for a brain that
    failed - the cause to the durable record, a plain sentence to the user, nothing leaked.
    """


def open_sign_in(spawn=None):
    """Open a terminal sitting at the `claude` prompt - the door to the sign-in - instead of only
    reciting steps: "ideally Excephalon should do more than just tell me what to do, but pop open
    whatever I need to do it and run it itself if possible." The signing in itself stays the
    user's act (their account, approved in their browser); this walks them to the prompt, where
    the signed-out CLI offers the login flow itself. True when the terminal opened, so the spoken
    line can match what actually happened - a failed opener must never eat the reply."""
    import subprocess

    from excephalon import machine

    try:
        if machine.WINDOWS:
            (spawn or subprocess.Popen)(["cmd", "/k", "claude"],
                                        creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            (spawn or subprocess.Popen)(
                ["osascript", "-e", 'tell application "Terminal" to do script "claude"',
                 "-e", 'tell application "Terminal" to activate'])
        return True
    except Exception:
        return False


def needs_sign_in(error):
    """Whether this brain failure is the machine's Claude sign-in being dead - the one failure a
    restart cannot fix and the user can, so the reply must say the fix rather than "ask me again"
    (he restarted on that advice and met the same wall). Keyed on the tokens every shape of that
    failure has carried - the authentication_failed subtype, the OAuth-expiry sentence, the
    signed-out /login notice - across the whole cause chain, since the retry wraps the original."""
    seen = []
    while error is not None and error not in seen:
        seen.append(error)
        error = getattr(error, "__cause__", None)
    text = " ".join(str(err) for err in seen).casefold()
    return any(token in text for token in
               ("authentication", "oauth", "/login", "logged in", "signed out"))


def _cli_refusal(message):
    """The CLI's own refusal, or None when this is really the model talking.

    It arrives wearing the model's clothes: an ordinary assistant message, its text ready to be
    spoken, under a ResultMessage whose subtype is "success" - so nothing about the turn's SHAPE
    says anything went wrong. What gives it away is that the message carries an `error` at all,
    and that its model is the synthetic stand-in rather than one that could have written the
    words. Asked structurally like this, a revoked token, an expired plan and a signed-out machine
    are all caught by the same line, and no wording anyone might change is being matched.
    """
    error = getattr(message, "error", None)
    return str(error) if error else None


def extract_text(messages):
    """The spoken reply is the FINAL thing Excephalon says, not its running narration.

    A tool-using turn emits text between every step ("Now let me read that file...", "Found it,
    let me check..."); only the last message is the actual answer. Reading all of it aloud dumps the
    play-by-play the user is supposed to be insulated from, so we keep just the last message that has
    text and drop the narration before it."""
    latest = ""
    for message in messages:
        text = ""
        for block in getattr(message, "content", ()) or ():
            value = getattr(block, "text", None)
            if isinstance(value, str):
                text += value
        if text.strip():
            latest = text
    return latest.strip()


def _text_delta(message):
    """The user-facing text a partial-message event carries, or "" when it carries none.

    A stream interleaves text deltas with thinking and tool-input deltas; only the text is the
    reply being written, so only the text is worth interrupting anyone with."""
    event = getattr(message, "event", None)
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta") or {}
    return delta.get("text", "") if delta.get("type") == "text_delta" else ""


def _opens_text_block(message):
    """Whether this event begins a fresh text block - the seam between what the model said before
    a tool call and what it says after."""
    event = getattr(message, "event", None)
    if not isinstance(event, dict) or event.get("type") != "content_block_start":
        return False
    return (event.get("content_block") or {}).get("type") == "text"


def _context_tokens(usage):
    """How many tokens the model just processed as input = fresh input + both cache tiers. This
    is what grows as a conversation runs on and what makes each turn slower, so it's the number
    the brain watches to decide when to compact. Output tokens are excluded - they aren't context
    the next turn re-reads."""
    if not usage:
        return 0
    return sum(
        int(usage.get(key, 0) or 0)
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )


def _spill_system_prompt(options):
    """Put the system prompt in a file and hand the SDK that path instead of the text.

    The SDK spells the system prompt out on the CLI's command line, and Windows refuses a command
    line longer than 32767 characters. It refuses it as a FileNotFoundError, which the SDK reports
    as `CLINotFoundError: Claude Code not found at: ...claude.exe` - so a persona that outgrew that
    budget failed EVERY session with a complaint about a 252MB file that was sitting right there,
    and the conversation could not recover, because each rebuild had the same prompt to pass.

    A path costs a few dozen characters however long the persona grows, which it does on its own:
    the profile the persona is composed from gains a line every time an enhancement is filed.

    Returns the options to use and the file to delete when the session is done with it.
    """
    if not isinstance(options.system_prompt, str):  # an agent carries no persona of its own
        return options, None
    handle, path = tempfile.mkstemp(prefix="excephalon-persona-", suffix=".txt", text=True)
    with os.fdopen(handle, "w", encoding="utf-8") as spilled:
        spilled.write(options.system_prompt)
    return replace(options, system_prompt={"type": "file", "path": path}), path


class SdkSession:
    _LOOP_STOP_WAIT = 5.0  # seconds to give the loop's thread to unwind before letting it go

    def __init__(self, options, *, client_factory=ClaudeSDKClient):
        options, self._prompt_file = _spill_system_prompt(options)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._closed = False
        self._client = client_factory(options=options)
        try:
            self._submit(self._client.connect())
        except BaseException:
            # A session that never opened is never closed, so this is its only chance to tidy up -
            # and it's the path that REPEATS, since a brain whose session died rebuilds every turn.
            self._discard_spilled_prompt()
            self._shutdown_loop()
            raise
        self.last_context_tokens = 0  # size of the context the most recent ask processed
        self.last_session_id = None  # the CLI session's id, for resuming it after a restart

    def _submit(self, coro):
        """Run a coroutine on this session's loop and wait for it.

        Refused once closed, because closing SHUTS that loop down: a coroutine handed to a stopped
        loop is queued and never run, so the wait is for something that cannot happen. Whoever still
        holds a closed session would then not fail - it would stop answering altogether, with no
        reply, no error and no end to the wait. A brain whose rebuild failed holds exactly that.
        """
        if self._closed:
            coro.close()  # it will never be awaited; closing it keeps the warning out of the log
            raise RuntimeError("this session is closed")
        return self._run(coro)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def interrupt(self):
        """Cancel the ask currently in flight. Safe to call from another thread while `ask` is
        blocked: the interrupt coroutine is scheduled on this session's loop, where it interleaves
        with the streaming receive at its next await and stops the turn. The CLI then closes the
        turn out with a result message, so the blocked `ask` returns of its own accord."""
        self._submit(self._client.interrupt())

    async def _ask(self, prompt, on_message, on_text):
        await self._client.query(prompt)
        messages = []
        spoken = []  # every text delta, in order - exactly what a listening voice was handed

        def carry(piece):
            spoken.append(piece)
            if on_text is not None:
                on_text(piece)

        async for message in self._client.receive_response():
            refusal = _cli_refusal(message)
            if refusal is not None:
                raise BrainUnavailable(refusal)
            messages.append(message)
            if on_message is not None:
                on_message(message)
            if _opens_text_block(message) and spoken and not spoken[-1][-1:].isspace():
                # Text blocks on either side of a tool call can butt together with no whitespace;
                # jammed, "...verification.I'm" defeats the sentence splitter and the screen shows
                # a run-on. The seam goes to the voice AND the record, so they stay identical.
                carry("\n")
            delta = _text_delta(message)
            if delta:
                carry(delta)
            if isinstance(message, ResultMessage):
                # The same refusal on a REBUILT session drops the synthetic message and only flags
                # the result, so there is nothing above to have caught. With not one word said,
                # returning the empty string reads downstream as a reply that happened to be
                # blank: the turn passes in total silence, he having typed and waited for nothing.
                # A turn that says something and then flags an error keeps what it said - that is
                # an agent whose run failed partway, and its words are still what it did.
                if message.is_error and not spoken and not extract_text(messages[:-1]):
                    raise BrainUnavailable(f"the turn failed: {message.subtype}")
                self.last_context_tokens = _context_tokens(message.usage)
                # The id is the session's whole memory made durable: a restarted process resumes
                # it instead of stranding the conversation - the old failure was agents dying
                # whenever the app did.
                self.last_session_id = message.session_id or self.last_session_id
                break
        # A session streaming partial messages heard the whole reply go past as deltas: THAT is
        # the reply, all of it. Keeping only the final message's text left the record showing a
        # fraction of what a voice had already spoken - "what it said aloud didn't always match
        # what was printed". A session without partials (an agent's) keeps the final-text rule:
        # its narration between tool calls is machinery, not the report.
        return "".join(spoken).strip() if spoken else extract_text(messages)

    def ask(self, prompt, on_message=None, on_text=None):
        """Ask, and hand each message to `on_message`, whole, as it arrives.

        A real task takes many minutes, and nothing at all used to be visible until the very end -
        so an agent hard at work and an agent that had died looked exactly the same, and the user
        sat watching an empty log for fourteen minutes while Excephalon declared it dead one minute
        before it answered.

        Whole, and not boiled down to its text first: a message carries what the agent RAN as well
        as what it said, and reducing it here is what left the logs with the narration and none of
        the work. What to keep is the caller's decision - see `excephalon.steps`.

        `on_text` is the other tempo: each user-facing text delta the moment it is written, for a
        session opened with `include_partial_messages=True` - what lets a voice start speaking a
        reply while the rest of it is still being composed. Thinking and tool-input deltas are not
        text and never reach it."""
        return self._submit(self._ask(prompt, on_message, on_text))

    def close(self):
        """Disconnect and stop the loop. Idempotent: closing twice happens on every failure path,
        and the second call must not wait on a loop the first one already stopped."""
        if self._closed:
            return
        self._closed = True
        try:
            self._run(self._client.disconnect())
        finally:
            self._shutdown_loop()
            self._discard_spilled_prompt()

    def _shutdown_loop(self):
        """Stop this session's loop, wait for its thread to come out of `run_forever`, and close it.

        Closing is the part that gives the handles back; a loop that is merely stopped keeps them,
        and its thread stays alive. Bounded, because a loop that will not stop must not take the
        conversation down with it - better a leaked thread than a wedged app.
        """
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=self._LOOP_STOP_WAIT)
        if not self._thread.is_alive():
            self._loop.close()

    def _discard_spilled_prompt(self):
        """Take the persona's copy back off disk. A fresh session is built on every compaction and
        every reconnect, so one left behind each time is a growing pile of the user's own profile."""
        if self._prompt_file is not None:
            Path(self._prompt_file).unlink(missing_ok=True)
            self._prompt_file = None
