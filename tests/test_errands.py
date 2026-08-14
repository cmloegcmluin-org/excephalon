import time

from excephalon.errands import ERRAND_MODEL, ErrandRunner


class FakeSession:
    def __init__(self, reply="Moved the log into the archive."):
        self._reply = reply
        self.asked = []

    def ask(self, prompt, on_message=None, on_text=None):
        self.asked.append(prompt)
        return self._reply


def _runner(reply="Moved the log into the archive."):
    session = FakeSession(reply)
    events = []
    made = []

    def factory(options):
        made.append(options)
        return session

    runner = ErrandRunner("C:/runtime", lambda *event: events.append(event),
                          session_factory=factory)
    return runner, session, events, made


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_chore_runs_quietly_and_its_outcome_becomes_an_event():
    # "I just don't want my agents log tab to be cluttered with an agent for every little thing" -
    # no desk entry, no tab: one helper session does it and the outcome takes the news road.
    runner, session, events, _ = _runner()

    runner.run("move runtime/agent-logs/old.log into the archive folder")

    assert _wait_for(lambda: bool(events))
    assert "move runtime/agent-logs/old.log" in session.asked[0]
    [(kind, agent, report)] = events
    assert kind == "errand"
    assert report == "Moved the log into the archive."


def test_the_errand_prompt_maps_where_the_apps_own_records_live():
    # Asked to read "the most recently archived agent logs", the errand hand looked in the live
    # folder, found only the one live log there, and answered "there are no archived logs" while
    # three sat in runtime/agent-logs-archive/. A helper with file tools gets told where the
    # app's records actually are, so a lookup fails only when the record is truly absent.
    from excephalon.errands import PROMPT

    assert "runtime/agent-logs-archive/" in PROMPT
    assert "runtime/agent-logs/" in PROMPT
    assert "runtime/transcripts/" in PROMPT


def test_a_failed_errand_is_news_not_silence():
    class BrokenSession:
        def ask(self, prompt, on_message=None, on_text=None):
            raise RuntimeError("session wedged")

    events = []
    runner = ErrandRunner("C:/runtime", lambda *event: events.append(event),
                          session_factory=lambda options: BrokenSession())

    runner.run("tidy the folder")

    assert _wait_for(lambda: bool(events))
    [(kind, _, report)] = events
    assert kind == "errand"
    assert "could not run" in report


def test_the_errand_hand_is_a_small_model_with_file_tools_and_no_tab():
    runner, session, events, made = _runner()

    runner.run("anything")

    assert _wait_for(lambda: bool(events))
    [options] = made
    assert options.model == ERRAND_MODEL
    assert "Bash" in options.allowed_tools and "Write" in options.allowed_tools
    assert options.cwd == "C:/runtime"


def test_one_helper_session_serves_every_chore():
    runner, session, events, made = _runner()

    runner.run("first chore")
    assert _wait_for(lambda: len(events) == 1)
    runner.run("second chore")
    assert _wait_for(lambda: len(events) == 2)

    assert len(made) == 1


def test_no_services_file_means_no_services_and_no_complaint(tmp_path):
    from excephalon.errands import load_services

    assert load_services(tmp_path / "services.json") == ({}, "")


def test_a_services_file_is_read_and_a_broken_one_is_said_not_swallowed(tmp_path):
    # The standard {"mcpServers": ...} shape, so any service's docs paste straight in. A file he
    # edited that silently does nothing reads as a broken app, so the problem comes back as a
    # sentence - and a bad file refuses WHOLE rather than connecting half of what he wrote.
    from excephalon.errands import load_services

    good = tmp_path / "services.json"
    good.write_text('{"mcpServers": {"asana": {"type": "sse", "url": "https://mcp.asana.com/sse"}}}',
                    encoding="utf-8")
    assert load_services(good) == ({"asana": {"type": "sse", "url": "https://mcp.asana.com/sse"}}, "")

    torn = tmp_path / "torn.json"
    torn.write_text('{"mcpServers": {', encoding="utf-8")
    servers, problem = load_services(torn)
    assert servers == {} and "torn.json" in problem

    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"asana": "https://mcp.asana.com/sse"}', encoding="utf-8")
    servers, problem = load_services(wrong)
    assert servers == {} and "mcpServers" in problem


def test_connected_services_reach_the_errand_session():
    # The fast brain stays tools=[] - nothing mid-turn may outlast a breath - so his services
    # belong to the errand hand: "check my calendar" is a chore like any other, done off-turn in
    # the helper session and narrated back. The session gets the servers, and each is allowed
    # whole (mcp__<name> covers every tool it offers).
    session, events, made = FakeSession(), [], []
    services = {"asana": {"type": "sse", "url": "https://mcp.asana.com/sse"}}
    runner = ErrandRunner("C:/runtime", lambda *event: events.append(event),
                          services=services, session_factory=lambda o: made.append(o) or session)

    runner.run("what's due in Asana this week?")

    assert _wait_for(lambda: bool(made))
    assert made[0].mcp_servers == services
    assert "mcp__asana" in made[0].allowed_tools
    assert "Read" in made[0].allowed_tools  # the file tools stay - it is still the chore hand


def test_without_services_the_errand_session_is_unchanged():
    runner, session, events, made = _runner()

    runner.run("tidy the archive")

    assert _wait_for(lambda: bool(made))
    assert made[0].mcp_servers == {}
    assert not any(name.startswith("mcp__") for name in made[0].allowed_tools)


def test_the_brain_is_told_what_errands_can_reach_and_told_nothing_when_nothing():
    # A lever nobody mentions is a lever never pulled: without this note the brain answers "I
    # can't see your calendar" while the errand hand sits right there able to look. And when
    # nothing is connected the note is EMPTY - a brain told about services that are not there
    # would promise checks that can only fail.
    from excephalon.errands import services_note

    note = services_note({"asana": {}, "gmail": {}})
    assert "asana" in note and "gmail" in note and "run_errand" in note
    assert services_note({}) == ""


def test_an_errand_session_sees_only_the_servers_it_was_given():
    # Account-level connectors (claude.ai Gmail and friends) attach themselves to ANY session the
    # CLI opens, and a headless one that tries to OAuth them has no browser and no user - sessions
    # wedge on exactly that (anthropics/claude-code#36060). --strict-mcp-config pins the errand
    # session to the servers the app handed it, and nothing else.
    session, events, made = FakeSession(), [], []
    runner = ErrandRunner("C:/runtime", lambda *event: events.append(event),
                          session_factory=lambda o: made.append(o) or session)

    runner.run("tidy the archive")

    assert _wait_for(lambda: bool(made))
    assert made[0].extra_args == {"strict-mcp-config": None}


def test_two_chores_at_once_take_turns_on_the_one_session():
    # "Both running—results in a moment," then fifteen minutes of nothing: the first time the
    # brain dispatched two errands in one turn, both asks hit the one session together, collided
    # on its stream, and BOTH wedged - no answer, no error, no event. One session means one
    # chore at a time; the second waits its turn instead of destroying the first.
    import threading

    inside, overlapped = [], []

    class SlowSession:
        def ask(self, prompt, on_message=None, on_text=None):
            if inside:
                overlapped.append(prompt)
            inside.append(prompt)
            time.sleep(0.05)
            inside.pop()
            return f"did: {prompt[-20:]}"

    events = []
    runner = ErrandRunner("C:/runtime", lambda *event: events.append(event),
                          session_factory=lambda o: SlowSession())

    runner.run("check the calendar")
    runner.run("check the mail")

    assert _wait_for(lambda: len(events) == 2)
    assert overlapped == []  # never two asks inside the session at once
    runner.close()


def test_a_chore_that_never_answers_is_reported_not_vanished():
    # An ask with no deadline is a chore that can disappear: the thread blocks forever and the
    # user hears nothing at all - not even a failure. Past the deadline the session is shed (a
    # dead session makes the stranded ask raise, same as the brain's own recovery) and the
    # outcome says what happened.
    import threading

    hang = threading.Event()

    class WedgedSession:
        def ask(self, prompt, on_message=None, on_text=None):
            hang.wait(5.0)
            raise RuntimeError("session closed under the ask")

        def close(self):
            hang.set()  # closing is what frees the stranded ask, as with the real session

    events = []
    runner = ErrandRunner("C:/runtime", lambda *event: events.append(event),
                          session_factory=lambda o: WedgedSession(), deadline=0.1)

    runner.run("check the mail")

    assert _wait_for(lambda: bool(events), timeout=3.0)
    assert "could not" in events[0][2]
    runner.close()
