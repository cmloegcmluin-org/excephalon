import asyncio
import threading
import time

from excephalon.foreman import FOREMAN_MODEL, Foreman
from excephalon.outbox import Outbox


class FakeDesk:
    def __init__(self, known=("fixer",)):
        self._known = set(known)
        self.told = []

    def task_of(self, name):
        return "fix the drive link" if name in self._known else None

    def recent_log(self, name):
        return "[10:00:01] entity> get going\n[10:00:05] fixer> which auth library should I use?"

    def send(self, name, message):
        self.told.append((name, message))
        return name in self._known


class FakeSession:
    def __init__(self, reply):
        self._reply = reply
        self.asked = []

    def ask(self, prompt, on_message=None, on_text=None):
        self.asked.append(prompt)
        return self._reply


def _foreman(reply, desk=None, outbox=None):
    desk = desk or FakeDesk()
    outbox = outbox if outbox is not None else Outbox()
    session = FakeSession(reply)
    made = []

    def factory(options):
        made.append(options)
        return session

    return Foreman(desk, outbox, session_factory=factory), outbox, session, made


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _settled(session, count=1, timeout=2.0):
    return _wait_for(lambda: len(session.asked) >= count, timeout)


def test_the_foreman_reads_the_whole_situation():
    # Task, question, and the agent's own log tail: senior judgment needs what actually happened,
    # not a one-line summary of it.
    foreman, _, session, _ = _foreman("handled")

    foreman.consider("fixer", "It's asking which auth library to use.")

    assert _settled(session)
    [prompt] = session.asked
    assert "fixer" in prompt
    assert "fix the drive link" in prompt
    assert "which auth library should I use?" in prompt
    assert "It's asking which auth library to use." in prompt


def test_handled_means_the_user_never_hears_of_it():
    # The whole point of the layer: a snag settled agent-to-foreman is not news. Pushing
    # "Handled." at the user would be an interruption about nothing.
    foreman, outbox, session, _ = _foreman("Handled.")

    foreman.consider("fixer", "It paused for a go-ahead.")

    assert _settled(session)
    assert not _wait_for(lambda: bool(outbox), timeout=0.3)


def test_a_reply_that_ends_on_the_swallow_word_is_working_notes_and_never_news():
    # The contract is "settle it, then reply with the single word: handled" - and one settling
    # came back as three sentences of the foreman's own analysis with "handled" at the end. The
    # paragraphs before the swallow-word are its working notes, never user-addressed: queued
    # anyway, they sat as the agent's "update" in the roll call and were a jargon bomb waiting
    # for him to pick that number.
    foreman, outbox, session, _ = _foreman(
        "The named agent isn't at the desk - there's no process to prod. Nothing here needs "
        "the user.\n\nhandled")

    foreman.consider("fixer", "The monitor says it has gone quiet.")

    assert _settled(session)
    assert not _wait_for(lambda: bool(outbox), timeout=0.3)


def test_what_genuinely_needs_the_user_reaches_the_outbox_in_entitys_voice():
    foreman, outbox, session, _ = _foreman(
        "The fixer agent needs your Asana credentials to finish - nothing moves until then.")

    foreman.consider("fixer", "It says it's blocked on credentials.")

    assert _settled(session)
    assert _wait_for(lambda: bool(outbox))
    [news] = outbox.drain()
    assert "Asana credentials" in str(news)
    assert news.about == "fixer"
    # App-authored, not composed by the fast brain: the ledger must read it back to the brain, or
    # the user quotes a line at it that it has no record of saying.
    assert news.composed is False


def test_one_senior_session_serves_every_snag():
    # It remembers its past negotiations - and a session per snag would pay the model's cold
    # start every time.
    foreman, _, session, made = _foreman("handled")

    foreman.consider("fixer", "first snag")
    assert _settled(session, count=1)
    foreman.consider("fixer", "second snag")
    assert _settled(session, count=2)

    assert len(made) == 1


def test_the_foremans_session_is_a_senior_model_with_the_one_tool():
    foreman, _, session, made = _foreman("handled")

    foreman.consider("fixer", "anything")

    assert _settled(session)
    [options] = made
    assert options.model == FOREMAN_MODEL
    assert options.allowed_tools == ["mcp__foreman__tell_agent"]
    assert options.tools == []  # no built-ins: it judges, it does not investigate


def test_the_foremans_tell_reaches_the_agent_through_the_desk():
    desk = FakeDesk()
    foreman, _, _, _ = _foreman("handled", desk=desk)
    [tell] = foreman.tools()

    reply = asyncio.run(tell.handler({"name": "fixer", "message": "Use the existing auth module."}))

    assert desk.told == [("fixer", "Use the existing auth module.")]
    [content] = reply["content"]
    assert "Delivered" in content["text"]


def test_the_foreman_is_told_when_its_message_could_not_be_delivered():
    desk = FakeDesk(known=())
    foreman, _, _, _ = _foreman("handled", desk=desk)
    [tell] = foreman.tools()

    reply = asyncio.run(tell.handler({"name": "ghost", "message": "hello?"}))

    [content] = reply["content"]
    assert "No agent" in content["text"]


def test_a_dead_foreman_session_is_news_not_a_black_hole():
    # An agent silently stuck behind a foreman who never answered is the old lost-agent failure
    # with an extra layer of paint. The failed hand-off itself reaches the user.
    class BrokenSession:
        def ask(self, prompt, on_message=None, on_text=None):
            raise RuntimeError("session wedged")

    outbox = Outbox()
    foreman = Foreman(FakeDesk(), outbox, session_factory=lambda options: BrokenSession())

    foreman.consider("fixer", "anything")

    assert _wait_for(lambda: bool(outbox))
    [news] = outbox.drain()
    assert "foreman couldn't take" in str(news)
    assert news.about == "fixer"
