import threading
import time
from types import SimpleNamespace

from excephalon.agent_desk import AgentDesk
from excephalon.outbox import Outbox


def said(text):
    """One streamed message carrying the agent's own words."""
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def called(tool, **tool_input):
    return SimpleNamespace(content=[SimpleNamespace(name=tool, input=tool_input)])


def came_back(output):
    return SimpleNamespace(content=[SimpleNamespace(tool_use_id="toolu_01", content=output)])


class FakeAgent:
    """Stands in for a real SupervisedAgent: a persistent session that remembers its messages."""

    def __init__(self, name, cwd, decide, hold=None):
        self.name = name
        self.cwd = cwd
        self.decide = decide
        self.session_id = f"sess-{name}"
        self.messages = []
        self.closed = False
        self._hold = hold

    def work(self, message, on_message=None):
        self.messages.append(message)
        if on_message is not None:
            on_message(said(f"[{self.name}] did: {message}"))
        if self._hold is not None:
            self._hold.wait(2.0)
        return f"[{self.name}] did: {message}"

    def close(self):
        self.closed = True


def _desk(outbox=None, made=None, hold=None, roster=None, monitor=None, log_dir=None, run=None,
          state=None, law=None, complete=None):
    outbox = Outbox() if outbox is None else outbox  # an empty one is falsy, not absent
    made = made if made is not None else []

    def factory(name, cwd, decide, **choice):
        agent = FakeAgent(name, cwd, decide, hold=hold)
        made.append(agent)
        return agent

    return (AgentDesk(outbox, agent_factory=factory, roster_path=roster, monitor=monitor,
                      log_dir=log_dir, run=run, state_path=state, law_path=law,
                      complete_enhancement=complete),
            outbox, made)


def _approved(desk, name, steps="look at it"):
    """Walk one agent through the review loop to an approved verdict, its news all spoken - the
    state a wrap-up is now only legal from, since a tab closed over unruled work delivered a
    feature behind his back, and a tab closed over an UNSPOKEN merge report dropped the one fact
    he was owed. Tests about the wrap-up ITSELF start here rather than restating the loop.

    The wait is for the landing instruction to have been DELIVERED and answered, not merely for
    an idle state: approval dispatches on a thread of its own, and retiring while that thread is
    still writing the agent's log cannot move the file on Windows."""
    desk.present(name, steps)
    desk._outbox.drain()  # its report reached him - approval is only legal on work he was shown
    desk.verdict(name, True)
    agent = desk._desked[name].agent
    _wait_for(lambda: len(agent.messages) >= 2 and desk._desked[name].state == "idle")
    desk._outbox.drain()  # the landing report reached him; nothing about the agent is still owed


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class SpyMonitor:
    def __init__(self):
        self.check_ins = []
        self.finished = []

    def checked_in(self, agent):
        self.check_ins.append(agent)

    def done(self, agent):
        self.finished.append(agent)


def test_the_desk_is_what_reports_whether_an_agent_is_alive():
    # "Again, you're lying about that. I can see that the agent just checked in two minutes ago."
    # Silence was measured off the inbox FILENAMES, so a file Excephalon had written itself became an
    # agent that then "went quiet", and a real agent that hadn't happened to write to its inbox
    # looked dead. The desk is the only thing that knows which agents exist and when each spoke.
    monitor = SpyMonitor()
    desk, _, _ = _desk(monitor=monitor)

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: monitor.finished == ["fixer"])  # the clock stops when it's done
    assert monitor.check_ins.count("fixer") >= 2  # dispatched, then again for what it narrated


def test_an_agent_that_dies_stops_its_silence_clock_too():
    # A dead agent is announced as dead; leaving its clock running would then also announce it as
    # quiet twenty minutes later, which is the same non-news twice.
    monitor = SpyMonitor()
    desk = AgentDesk(Outbox(), agent_factory=lambda *a, **k: _DyingAgent(), monitor=monitor)
    desk.start("doomed", "/tmp/wt", "try")

    assert _wait_for(lambda: monitor.finished == ["doomed"])


class _DyingAgent:
    def work(self, message, on_message=None):
        raise RuntimeError("session lost")

    def close(self):
        pass


def test_agents_start_on_the_model_he_chose_defaulting_to_opus_on_high():
    # "Sonnet's not so hot either. I usually use Opus... It should default to Opus 4.8 on High, but
    # I should be able to ask it for Fable Max for example if I want." It was hardcoded to Sonnet
    # and invisible - they asked what their agents were running and could not be told.
    started = []

    def factory(name, cwd, decide, *, model, effort):
        started.append((model, effort))
        return FakeAgent(name, cwd, decide)

    desk = AgentDesk(Outbox(), agent_factory=factory)

    desk.start("first", "/tmp/wt", "go")
    assert desk.choose("claude-fable-5", "max") == "Fable on max"  # and it says what it will be
    desk.start("second", "/tmp/wt2", "go")

    assert _wait_for(lambda: len(started) == 2)
    assert started == [("claude-opus-4-8", "high"), ("claude-fable-5", "max")]


def test_changing_the_model_leaves_an_agent_already_working_where_it_is():
    # A session's model is fixed when it opens, so a change can only govern the next agent. Saying
    # otherwise would be the kind of claim they check and find false.
    started = []
    desk = AgentDesk(Outbox(), agent_factory=lambda name, cwd, decide, *, model, effort:
                     started.append((name, model)) or FakeAgent(name, cwd, decide))

    desk.start("already-running", "/tmp/wt", "go")
    assert _wait_for(lambda: len(started) == 1)
    desk.choose("claude-fable-5", None)

    assert started == [("already-running", "claude-opus-4-8")]  # untouched by the later change
    assert desk.running_on() == "Fable on high"  # effort left alone, since they only named a model


def test_starting_an_agent_does_not_block_the_caller():
    # The conversation loop must never wait on agent work - that's what left them talking to a wall.
    hold = threading.Event()
    desk, outbox, _ = _desk(hold=hold)

    start = time.monotonic()
    desk.start("fixer", "/tmp/wt", "fix the drive link")
    elapsed = time.monotonic() - start

    assert elapsed < 0.5  # returned immediately, while the agent is still working
    assert not outbox  # and nothing is claimed to be finished yet
    hold.set()
    desk.close()


def test_the_agents_reply_arrives_in_the_outbox_when_it_lands():
    desk, outbox, _ = _desk()

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(outbox))
    assert any("did: fix the drive link" in message for message in outbox.drain())
    desk.close()


def test_the_news_an_agent_makes_says_which_agent_it_is_about():
    # Several ready at once are read out by name so one can be picked. The name has to travel with
    # the news: worked back out of the sentence it would be reading the label to find the thing.
    desk, outbox, _ = _desk()

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(outbox))
    [news] = outbox.drain()
    assert news.about == "fixer"
    desk.close()


def test_a_follow_up_reaches_the_same_agent_not_a_new_one():
    # Four agents in a row were lost because there was no live handle to talk back to.
    desk, outbox, made = _desk()
    desk.start("fixer", "/tmp/wt", "first task")
    assert _wait_for(lambda: bool(outbox))
    outbox.drain()

    assert desk.send("fixer", "now do the other half")

    assert _wait_for(lambda: bool(outbox))
    assert len(made) == 1  # the same agent, not a fresh one
    assert made[0].messages[0].startswith("first task")
    # And a follow-up is only the follow-up: the session already carries the standing rule the
    # desk attaches to a task, and repeating it every time would be most of what the tab holds.
    assert made[0].messages[1] == "now do the other half"
    desk.close()


def test_every_task_carries_the_standing_rule_to_rebase_before_showing_work():
    # "before presenting any agent branch/build to the user for verification, rebase it onto latest
    # origin/main first so nothing recently merged (e.g. other features) appears missing." The
    # brain wrote that into the dispatch on some days and not others. The desk attaches it to
    # every task, which makes it a mechanism rather than a reminder.
    desk, _, made = _desk()

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(made and made[0].messages))
    sent = made[0].messages[0]
    assert sent.startswith("fix the drive link")  # their ask first; the rule stands after it
    assert "rebase" in sent and "origin/main" in sent
    desk.close()


def test_a_follow_up_to_an_agent_that_was_never_started_says_so():
    desk, _, made = _desk()

    assert desk.send("ghost", "you there?") is False
    assert made == []


def test_an_agent_that_blows_up_is_reported_not_swallowed():
    outbox = Outbox()

    class Exploding:
        def work(self, message, on_message=None):
            raise RuntimeError("session died")

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice: Exploding())

    desk.start("doomed", "/tmp/wt", "do a thing")

    assert _wait_for(lambda: bool(outbox))
    said = outbox.drain()
    assert any("doomed" in m and "session died" in m for m in said)
    assert [news.about for news in said] == ["doomed"]  # a death is news about an agent too
    desk.close()


def test_the_roster_on_disk_says_who_is_live_and_what_they_are_doing(tmp_path):
    # Excephalon's own context resets kept stranding agents. The roster is a file, so it survives
    # a reset - the brain can just read it back with its ordinary tools.
    roster = tmp_path / "active-agents.txt"
    hold = threading.Event()
    desk, outbox, _ = _desk(hold=hold, roster=roster)

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: roster.exists() and "fixer" in roster.read_text(encoding="utf-8"))
    written = roster.read_text(encoding="utf-8")
    assert "working" in written and "fix the drive link" in written and "/tmp/wt" in written

    hold.set()
    assert _wait_for(lambda: "idle" in roster.read_text(encoding="utf-8"))  # its state moves on
    desk.close()


def test_every_exchange_is_written_to_a_timestamped_per_agent_log(tmp_path):
    # "still no timestamps in the logs": the tailable record of what Excephalon and an agent said
    # to each other, stamped, written by the desk itself as it happens - not left to the brain to
    # hand-author in whatever format it invents that day.
    outbox = Outbox()
    made = []

    def factory(name, cwd, decide, **choice):
        agent = FakeAgent(name, cwd, decide)
        made.append(agent)
        return agent

    desk = AgentDesk(outbox, agent_factory=factory, log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "fix the drive link")
    assert _wait_for(lambda: bool(outbox))
    desk.send("fixer", "only the subfolder")
    assert _wait_for(lambda: len(outbox.drain()) >= 0 and len(made[0].messages) == 2)
    assert _wait_for(lambda: "only the subfolder" in (tmp_path / "fixer.log").read_text(encoding="utf-8"))
    desk.close()

    log = (tmp_path / "fixer.log").read_text(encoding="utf-8")
    assert "EXCEPHALON> fix the drive link" in log
    assert "AGENT> [fixer] did: fix the drive link" in log
    assert "EXCEPHALON> only the subfolder" in log
    for line in log.splitlines():
        if line.startswith("====="):
            continue
        assert line.startswith("["), f"unstamped line: {line!r}"  # every line carries its time


def test_an_agent_log_line_carries_its_full_date_so_a_tail_tells_fresh_from_stale(tmp_path):
    # "still no timestamps" was answered with a clock time per line - but a time alone can't say
    # which DAY it was written. A log is read from its TAIL (the foreman's recent_log, a human's
    # `tail`, the window's newest lines), where the once-a-day date header sits far above and out of
    # reach, so a line an agent wrote yesterday reads exactly like one it wrote a minute ago. The
    # full date on every line is what lets a reader tell a working agent from a dead session at a
    # glance - without it there is no telling stale from fresh.
    import re

    desk, outbox, _ = _desk(log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "fix the drive link")
    assert _wait_for(lambda: bool(outbox))
    desk.close()

    stamped = [line for line in (tmp_path / "fixer.log").read_text(encoding="utf-8").splitlines()
               if line.startswith("[")]
    assert stamped, "no stamped lines to read"
    for line in stamped:
        assert re.match(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ", line), \
            f"line does not carry its date, so a tail can't date it: {line!r}"


def test_closing_the_desk_shuts_its_agents_down():
    desk, outbox, made = _desk()
    desk.start("fixer", "/tmp/wt", "a task")
    assert _wait_for(lambda: bool(outbox))

    desk.close()

    assert made[0].closed


def test_an_agents_steps_reach_its_log_as_it_works(tmp_path):
    # They watched an empty log for fourteen minutes while the agent was alive and working, and
    # Excephalon declared it dead one minute before it answered. Being able to SEE it work is the fix.
    outbox = Outbox()
    steps = ["Reading the router.", "Writing a failing test.", "Confirmed red."]

    class NarratingAgent:
        def work(self, message, on_message=None):
            for step in steps:
                on_message(said(step))
            return "done"

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice: NarratingAgent(),
                     log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "do the thing")
    assert _wait_for(lambda: bool(outbox))

    log = (tmp_path / "fixer.log").read_text(encoding="utf-8")
    for step in steps:
        assert step in log  # every step, as it happened
    assert log.index("Reading the router.") < log.index("Confirmed red.")  # in order
    desk.close()


def test_what_an_agent_ran_and_what_came_back_reach_its_log(tmp_path):
    # "no tool calls, diffs, or command/test output": the log held only the sentences the agent
    # narrated, so ten minutes of real work read back as ten minutes of silence. Asked for the
    # real exchange repeatedly, because it is what says whether these agents are being driven well.
    outbox = Outbox()

    class Working:
        def work(self, message, on_message=None):
            on_message(called("Bash", command="python -m pytest -q"))
            on_message(came_back("358 passed in 4.41s"))
            on_message(said("Green. Committing."))
            return "Green. Committing."

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice: Working(), log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "make it green")
    assert _wait_for(lambda: bool(outbox))

    log = (tmp_path / "fixer.log").read_text(encoding="utf-8")
    assert "WORK> Bash: python -m pytest -q" in log
    assert "WORK>     358 passed in 4.41s" in log
    assert "AGENT> Green. Committing." in log
    desk.close()


def test_the_digest_briefs_a_brain_on_the_fleet_without_a_file_read():
    # "How's it going?" used to send the brain off to read the roster file with its tools - thirty
    # seconds to fifteen minutes of silence for a question about state the process already held.
    # The digest is that state as a handful of lines, handed to the brain every turn by code.
    hold = threading.Event()
    desk, outbox, _ = _desk(hold=hold)
    desk.start("fixer", "/tmp/wt", "fix the drive link so it opens the memo's own subfolder")

    briefing = desk.digest()

    assert "fixer" in briefing
    assert "working" in briefing
    assert "fix the drive link" in briefing
    hold.set()
    desk.close()


def test_the_digest_with_nothing_running_says_so():
    desk, _, _ = _desk()

    assert "No agents running." in desk.digest()
    # And the ladder rides every briefing, so a stage word is never left to interpretation.
    assert "unstarted" in desk.digest() and "DELIVERED" in desk.digest()


def test_with_an_events_sink_the_desk_reports_there_instead_of_the_outbox():
    # The narrator words the news in the brain's own voice; the desk's job shrinks to saying WHAT
    # happened - kind, agent, report - and staying out of the wording business entirely.
    events = []
    outbox = Outbox()
    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice:
                     FakeAgent(name, cwd, decide), events=lambda *e: events.append(e))

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(events))
    kind, agent, report = events[0]
    assert (kind, agent) == ("finished", "fixer")
    assert "fix the drive link" in report
    assert not outbox  # the sink owns delivery now; nothing is pushed twice
    desk.close()


def test_a_death_reaches_the_events_sink_as_what_it_is():
    events = []
    desk = AgentDesk(Outbox(), agent_factory=lambda *a, **k: _DyingAgent(),
                     events=lambda *e: events.append(e))

    desk.start("doomed", "/tmp/wt", "try")

    assert _wait_for(lambda: bool(events))
    kind, agent, report = events[0]
    assert (kind, agent) == ("died", "doomed")
    assert "session lost" in report


def test_retiring_a_finished_agent_closes_its_tab_by_moving_its_log_to_the_archive(tmp_path):
    # A tab closes when its log leaves the folder the window watches. The brain used to do the
    # move with its own shell; it has no shell now, so the desk does it on the tool's behalf. The
    # log lands in the fleet's one archive - runtime/agent-logs-archive/, a SIBLING of the live
    # folder, named for what it is - so it is entirely outside what the roster globs.
    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs)
    desk.start("fixer", "/tmp/wt", "a task")
    assert _wait_for(lambda: bool(outbox))  # finished: its work() returned
    _approved(desk, "fixer")

    assert desk.retire("fixer") is True

    assert not (logs / "fixer.log").exists()
    assert (tmp_path / "agent-logs-archive" / "fixer.log").exists()
    assert desk.roster() == []  # and the desk lets go of the finished session
    desk.close()


def test_a_working_agent_cannot_be_retired_out_from_under_them(tmp_path):
    # Closing a live agent's tab would drop the user's view into work still happening.
    hold = threading.Event()
    desk, _, _ = _desk(hold=hold, log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "a task")
    assert _wait_for(lambda: (tmp_path / "fixer.log").exists())

    assert desk.retire("fixer") is False
    assert (tmp_path / "fixer.log").exists()

    hold.set()
    desk.close()


def test_retiring_an_agent_the_desk_never_had_still_moves_a_leftover_log(tmp_path):
    # After a restart the desk is empty but yesterday's logs still hold tabs open. Retiring one
    # is then purely the file move.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    desk, _, _ = _desk(log_dir=logs)
    (logs / "old-timer.log").write_text("x", encoding="utf-8")

    assert desk.retire("old-timer") is True
    assert (tmp_path / "agent-logs-archive" / "old-timer.log").exists()


def test_retiring_something_with_no_log_and_no_agent_says_no(tmp_path):
    desk, _, _ = _desk(log_dir=tmp_path)

    assert desk.retire("ghost") is False


def test_retiring_an_agent_ticks_off_the_enhancement_it_was_completing(tmp_path):
    # The enhancements list is the pool agents pick from; a finished agent's item should tick
    # itself off. The wrap-up is the moment: the work has landed and the tab is closing, so what
    # was an open ask is now done. The item rides with the agent from the start, never guessed
    # from its task, because a wrong tick corrupts the list's record of what was asked and answered.
    ticked = []
    desk, outbox, _ = _desk(log_dir=tmp_path / "agent-logs",
                            complete=lambda item: ticked.append(item) or True)
    desk.start("voice", "/tmp/wt", "wire up the better voice", enhancement="Better voice")
    assert _wait_for(lambda: bool(outbox))
    _approved(desk, "voice")

    assert desk.retire("voice") is True

    assert ticked == ["Better voice"]
    desk.close()


def test_retiring_ticks_the_item_on_the_project_card_it_rode_from(tmp_path):
    # "for the three tasks Excephalon took care of today, it did not check them off in the
    # Projects tab" - two of the three lived on the Highdeas card, and every tick could only ever
    # land on the Enhancements card. The card rides with the agent from the start, like the item.
    ticked = []
    desk, outbox, _ = _desk(log_dir=tmp_path / "agent-logs",
                            complete=lambda item, **where: ticked.append(
                                (item, where.get("heading"))) or True)
    desk.start("spinner", "/tmp/wt", "hold the spinner through the send",
               enhancement="#7 submitting to google drive", project="Highdeas")
    assert _wait_for(lambda: bool(outbox))
    _approved(desk, "spinner")

    assert desk.retire("spinner") is True

    assert ticked == [("#7 submitting to google drive", "Project: Highdeas")]
    desk.close()


def test_a_failed_agent_never_ticks_its_enhancement_done(tmp_path):
    # Retiring a DIED agent still wraps up its leftovers, but its ask is NOT answered - ticking it
    # off would record a failure as a completion, the checklist's worst possible lie.
    ticked = []
    outbox = Outbox()
    desk = AgentDesk(outbox, agent_factory=lambda *a, **k: _DyingAgent(),
                     log_dir=tmp_path / "agent-logs",
                     complete_enhancement=lambda item: ticked.append(item) or True)
    desk.start("doomed", "/tmp/wt", "attempt it", enhancement="Better voice")
    assert _wait_for(lambda: bool(desk._desked) and desk._desked["doomed"].state == "failed")

    outbox.drain()  # the death notice reached him - a wrap-up never buries unspoken news
    assert desk.retire("doomed") is True

    assert ticked == []
    desk.close()


def test_the_enhancement_tag_survives_a_restart_and_still_ticks(tmp_path):
    # "I close it and reopen it constantly." An agent dispatched for an enhancement often outlives
    # the process it was started in, so the tag has to live in the survival record - otherwise a
    # revived agent lands its work and the item it was for never ticks.
    import json

    state = tmp_path / "agents.json"
    logs = tmp_path / "agent-logs"
    first, outbox, _ = _desk(state=state, log_dir=logs)
    first.start("voice", "/wt/voice", "wire the voice", enhancement="Better voice")
    assert _wait_for(lambda: bool(outbox))
    _approved(first, "voice")  # he saw it and signed off; the landing is what the record carries

    [saved] = json.loads(state.read_text(encoding="utf-8"))
    assert saved["enhancement"] == "Better voice"  # the tag is in the survival record
    first.close()

    ticked = []
    revived_outbox = Outbox()
    revived = AgentDesk(revived_outbox,
                        agent_factory=lambda name, cwd, decide, **k: FakeAgent(name, cwd, decide),
                        state_path=state, log_dir=logs,
                        complete_enhancement=lambda item: ticked.append(item) or True)
    revived.revive()

    # The revived agent carries the approved verdict across the restart, so its wrap-up is legal.
    assert _wait_for(lambda: revived._desked["voice"].state == "idle")
    revived_outbox.drain()  # its picked-back-up report reached him
    assert revived.retire("voice") is True
    assert ticked == ["Better voice"]  # and the revived agent still ticks it


def test_a_tick_that_lands_nowhere_becomes_news_instead_of_a_silent_shrug(tmp_path):
    # Work merged, log archived, ticket still open, nobody told - "as far as I know it's still
    # open work." The tick's miss report used to be thrown away; now it is queued to be said.
    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs, complete=lambda item: False)
    desk.start("fixer", str(tmp_path / "wt"), "make it green", enhancement="Better voice")
    assert _wait_for(lambda: bool(outbox))
    _approved(desk, "fixer")
    outbox.drain()  # the landing report; what follows is the tick's own miss

    assert desk.retire("fixer") is True

    assert _wait_for(lambda: bool(outbox))
    [news] = outbox.drain()
    assert "did not get checked off" in news
    assert news.about == "fixer"


def test_an_agent_cannot_be_wrapped_up_over_work_he_has_not_ruled_on(tmp_path):
    # An agent built a feature, its tab was closed over the top, and he met the result as a fait
    # accompli: "are you saying you delivered a feature without me verifying it first? Have you
    # forgotten the absolute basics of how you are supposed to supervise new features?" A verdict
    # is the only thing that makes a wrap-up legal - the same gate that already stops the push.
    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs)
    desk.start("builder", str(tmp_path / "wt"), "add the checkbox")
    assert _wait_for(lambda: bool(outbox))
    outbox.drain()

    assert desk.retire("builder") is False  # never presented, never approved
    desk.present("builder", "open the app and look at the player bar")
    assert desk.retire("builder") is False  # presented, still waiting on him

    builder = desk._desked["builder"].agent
    desk.verdict("builder", True)
    assert _wait_for(lambda: len(builder.messages) >= 2
                     and desk._desked["builder"].state == "idle")

    outbox.drain()  # the landing report reached him
    assert desk.retire("builder") is True  # approved and told: the wrap-up is the last leg
    desk.close()


def test_no_approval_can_be_recorded_while_the_walkthrough_is_still_unspoken(tmp_path):
    # The submission-feedback breach: its ready-for-your-eyes walkthrough sat queued unspoken, an
    # ambiguous "yes" got recorded as approval, and the work merged without his eyes ever on it -
    # "I never even accepted it; it was never presented to me to be validated." A verdict on work
    # he has not been shown cannot exist, so approval is refused until the walkthrough is spoken.
    import pytest

    from excephalon.delivery import DeliveryError

    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs)
    desk.start("shipper", str(tmp_path / "wt"), "build the spinner")
    assert _wait_for(lambda: bool(outbox))  # its report is queued - and stays queued, unheard

    desk.present("shipper", "watch the spinner hold through the send")
    with pytest.raises(DeliveryError):
        desk.verdict("shipper", approved=True)

    outbox.drain()  # the walkthrough reached him
    desk.verdict("shipper", approved=True)  # now his yes can mean the work
    desk.close()


def test_a_rejection_stands_even_with_the_walkthrough_unspoken_and_drops_it(tmp_path):
    # He can reject from his own looking (he is often already at the test instance); holding his
    # rejection hostage to a walkthrough he no longer needs would be the app arguing with him.
    # The stale walkthrough is dropped instead - his feedback has moved past it.
    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs)
    desk.start("shipper", str(tmp_path / "wt"), "build the spinner")
    assert _wait_for(lambda: bool(outbox))  # the walkthrough is queued, unheard

    desk.present("shipper", "watch the spinner")
    desk.verdict("shipper", approved=False, feedback="the spinner still vanishes")

    assert "shipper" not in {getattr(news, "about", None) for news in outbox.drain()}
    desk.close()


def test_an_agent_cannot_be_wrapped_up_while_its_news_has_not_reached_him(tmp_path):
    # The submission-feedback agent merged its work, its "Merged." report was queued - and the
    # wrap-up dropped that report unheard, so the landed feature read as lost: "clearly my
    # feature just got dropped in a black hole and Excephalon somehow doesn't know anything
    # about it". The drop is for news he has moved past; a report he was never told is the
    # loop's last word, so the wrap-up waits for it to be spoken.
    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs)
    desk.start("lander", str(tmp_path / "wt"), "land the spinner fix")
    assert _wait_for(lambda: bool(outbox))
    outbox.drain()
    desk.present("lander", "watch the spinner hold through the whole send")
    desk.verdict("lander", True)
    agent = desk._desked["lander"].agent
    assert _wait_for(lambda: len(agent.messages) >= 2
                     and desk._desked["lander"].state == "idle")

    assert desk.retire("lander") is False       # its merge report is still waiting to be spoken
    assert (logs / "lander.log").exists()       # nothing was archived over the debt

    outbox.drain()                              # the report reached him
    assert desk.retire("lander") is True
    desk.close()


def test_wrapping_an_agent_up_takes_its_undelivered_news_with_it(tmp_path):
    # "I'm kind of surprised you have an update for that one, because that feature is already
    # done." News queued about an agent whose work is closed arrives as a surprise, not an
    # update - and after the spool it would survive a restart to do it again.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "lander.log").write_text("[10:00:00] ENTITY> land it" + chr(10), encoding="utf-8")
    desk, outbox, _ = _desk(log_dir=logs)
    outbox.push("lander has an update for you", about="lander")
    outbox.push("someone else entirely", about="other")

    assert desk.retire("lander") is True  # a leftover log with no agent behind it

    assert [str(news) for news in outbox.drain()] == ["someone else entirely"]


def test_the_roster_says_when_each_agent_was_last_heard_from(tmp_path):
    outbox = Outbox()
    roster = tmp_path / "active-agents.txt"

    class NarratingAgent:
        def work(self, message, on_message=None):
            on_message(said("Reading the router."))
            return "done"

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice: NarratingAgent(),
                     roster_path=roster, log_dir=tmp_path, clock=lambda fmt: "2026-07-19 08:20:15")
    desk.start("fixer", "/tmp/wt", "do the thing")
    assert _wait_for(lambda: bool(outbox))

    assert "last heard 2026-07-19 08:20:15" in roster.read_text(encoding="utf-8")
    desk.close()


def test_every_task_carries_the_standing_rule_that_review_means_their_eyes():
    # "When I say that I want to be able to verify a feature, I'm not satisfied with running a
    # test command." An agent handed back "run pytest" as the acceptance step; the rule that
    # review means a live instance and click-steps now rides with every task, like the rebase rule.
    desk, _, made = _desk()

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(made and made[0].messages))
    sent = made[0].messages[0]
    assert "own eyes" in sent and "live instance" in sent
    assert "Never offer 'run the tests'" in sent
    desk.close()


def test_retiring_a_finished_agent_also_removes_its_worktree(tmp_path):
    # "it should probably archive the agent log... and always do stuff like archive the Claude
    # session and worktree etc." - wrapping up is one gesture, not three chores.
    ran = []
    desk, outbox, _ = _desk(log_dir=tmp_path / "agent-logs",
                            run=lambda cmd, **kw: ran.append(cmd))
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))
    _approved(desk, "fixer")

    assert desk.retire("fixer") is True

    assert ["git", "-C", "/wt/fixer", "worktree", "remove", "/wt/fixer"] in ran
    desk.close()


def test_a_worktree_that_will_not_remove_does_not_block_the_retirement(tmp_path):
    # A dirty worktree is the sweep's business later; the tab and the session still wrap up now.
    def refuses(cmd, **kw):
        raise RuntimeError("worktree is dirty")

    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs, run=refuses)
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))
    _approved(desk, "fixer")

    assert desk.retire("fixer") is True
    assert not (logs / "fixer.log").exists()  # the tab still closed


def test_the_desks_state_survives_on_disk_for_the_next_process(tmp_path):
    # "Obviously the agent processes must be independent of Excephalon. I close it and reopen it
    # constantly." The state file is the fleet's survival record: who exists, where, on which
    # CLI session - everything a fresh process needs to reattach.
    import json

    state = tmp_path / "agents.json"
    desk, outbox, _ = _desk(state=state)

    desk.start("fixer", "/wt/fixer", "fix the drive link")
    assert _wait_for(lambda: bool(outbox))

    [entry] = json.loads(state.read_text(encoding="utf-8"))
    assert entry["name"] == "fixer"
    assert entry["cwd"] == "/wt/fixer"
    assert entry["session_id"] == "sess-fixer"
    assert entry["state"] == "idle"
    desk.close()


def test_retiring_prunes_the_state_file(tmp_path):
    import json

    state = tmp_path / "agents.json"
    desk, outbox, _ = _desk(state=state, log_dir=tmp_path / "agent-logs")
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))
    _approved(desk, "fixer")

    desk.retire("fixer")

    assert json.loads(state.read_text(encoding="utf-8")) == []


def test_revive_reopens_yesterdays_agents_on_their_old_sessions(tmp_path):
    # The whole point of Milestone 3: close Excephalon mid-task, reopen it, and the same agents are
    # there - reattached to sessions that remember everything, with in-flight work re-kicked.
    import json

    state = tmp_path / "agents.json"
    state.write_text(json.dumps([
        {"name": "fixer", "cwd": "/wt/fixer", "task": "fix the link",
         "session_id": "sess-1", "state": "idle",
         "delivery": "ready", "steps": "open the page and click the link",
         "model": "claude-opus-4-8", "effort": "high"},
        {"name": "builder", "cwd": "/wt/builder", "task": "build the thing",
         "session_id": "sess-2", "state": "working",
         "model": "claude-fable-5", "effort": "max"},
    ]), encoding="utf-8")
    revived = []

    def factory(name, cwd, decide, *, model, effort, resume=None):
        revived.append((name, model, effort, resume))
        return FakeAgent(name, cwd, decide)

    desk = AgentDesk(Outbox(), agent_factory=factory, state_path=state)

    names = desk.revive()

    assert sorted(names) == ["builder", "fixer"]
    assert ("fixer", "claude-opus-4-8", "high", "sess-1") in revived
    assert ("builder", "claude-fable-5", "max", "sess-2") in revived
    assert "fixer" in desk.digest() and "builder" in desk.digest()
    # The one that was mid-task is told to pick back up; the one at rest - work presented,
    # awaiting the user's verdict - is not disturbed.
    assert _wait_for(lambda: any("restarted" in m for a in desk._desked.values()
                                 for m in a.agent.messages))
    fixer = desk._desked["fixer"].agent
    assert fixer.messages == []
    desk.close()


def test_revive_sends_a_landing_agent_to_settle_its_merge(tmp_path):
    # The combine agent backgrounded its merge watch and went idle; the merge landed a minute
    # later with nobody listening, and its tab sat open all day. An agent recorded mid-landing
    # owes exactly one thing - the outcome - so revival demands it outright instead of leaving
    # an "idle" lander in peace.
    import json

    state = tmp_path / "agents.json"
    state.write_text(json.dumps([
        {"name": "lander", "cwd": "/wt/lander", "task": "land the fix",
         "session_id": "sess-9", "state": "idle", "delivery": "landing"},
    ]), encoding="utf-8")
    desk, _, _ = _desk(state=state)

    desk.revive()

    lander = desk._desked["lander"].agent
    assert _wait_for(lambda: any("merged" in m for m in lander.messages))
    desk.close()


def test_revive_asks_an_idle_agent_with_unpresented_work_to_present(tmp_path):
    # The black hole: an agent died four minutes into a feature when the app closed, came back
    # "idle", and nothing ever re-engaged it - the user learned the work was stranded only by
    # asking after it that night. Recorded idle with building work and nothing presented,
    # revival now asks for the one thing still owed: where the work stands, for his eyes.
    import json

    state = tmp_path / "agents.json"
    state.write_text(json.dumps([
        {"name": "stray", "cwd": "/wt/stray", "task": "expand the groups",
         "session_id": "sess-3", "state": "idle"},
    ]), encoding="utf-8")
    desk, _, _ = _desk(state=state)

    desk.revive()

    stray = desk._desked["stray"].agent
    assert _wait_for(lambda: any("never presented" in m for m in stray.messages))
    desk.close()


def test_the_digest_names_tabs_left_by_agents_the_desk_no_longer_knows(tmp_path):
    # He asked for a leftover tab to be closed and Excephalon answered that it had no agent's name
    # to go by: the window draws a tab for every log file, but the briefing listed only live
    # agents, so the two views never met. The digest now names the orphans, so "close that tab"
    # is one close_agent_tab call away.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "prevent-same-name-combine.log").write_text("old exchange", encoding="utf-8")
    desk, _, _ = _desk(log_dir=logs)

    briefing = desk.digest()

    assert "No agents running." in briefing
    assert "prevent-same-name-combine" in briefing
    assert "close_agent_tab" in briefing


def test_a_live_agents_log_is_not_called_a_leftover_tab(tmp_path):
    logs = tmp_path / "agent-logs"
    hold = threading.Event()
    desk, outbox, _ = _desk(hold=hold, log_dir=logs)
    desk.start("fixer", "/tmp/wt", "make it green")

    assert "Tabs still open" not in desk.digest()
    hold.set()
    assert _wait_for(lambda: bool(outbox))
    desk.close()


def test_a_revived_agents_recorded_session_survives_the_next_persist(tmp_path):
    # A freshly resumed agent reports no session id of its own until it first answers, and
    # persisting that None over the recorded id orphaned a whole fleet: the next restart found
    # nulls, skipped every revival, and the user was told there was "no trace" of agents he
    # had watched work all afternoon.
    import json

    state = tmp_path / "agents.json"
    state.write_text(json.dumps([
        {"name": "quiet", "cwd": "/wt/quiet", "task": "carry on", "session_id": "sess-q",
         "state": "idle", "delivery": "ready", "steps": "look at it"},
    ]), encoding="utf-8")

    class MuteAgent:  # resumed, and has not spoken this life - its own session_id is None
        session_id = None

        def work(self, message, on_message=None):
            return ""

        def close(self):
            pass

    desk = AgentDesk(Outbox(), agent_factory=lambda name, cwd, decide, **k: MuteAgent(),
                     state_path=state)
    desk.revive()  # revive persists the fleet immediately

    [kept] = json.loads(state.read_text(encoding="utf-8"))
    assert kept["session_id"] == "sess-q"  # the recorded id, not the mute agent's None
    desk.close()


def test_newest_session_for_reads_the_clis_per_cwd_store(tmp_path):
    # The CLI keeps one folder per working directory, a .jsonl per session; the newest session
    # that ever ran in an agent's own worktree is that agent, because nothing else runs there.
    import os

    from excephalon.agent_desk import newest_session_for

    folder = tmp_path / "C--wt-stray"
    folder.mkdir()
    (folder / "older.jsonl").write_text("{}", encoding="utf-8")
    (folder / "newest.jsonl").write_text("{}", encoding="utf-8")
    os.utime(folder / "older.jsonl", (100, 100))
    os.utime(folder / "newest.jsonl", (200, 200))

    assert newest_session_for("C:/wt/stray", store=tmp_path) == "newest"
    assert newest_session_for("C:/wt/gone", store=tmp_path) is None
    assert newest_session_for(None, store=tmp_path) is None


def test_revive_recovers_a_lost_session_id_from_the_store(tmp_path, monkeypatch):
    # The orphaned-fleet boot: one restart persisted nulls over the known ids, and the next
    # found "no trace" of agents the user had watched work all afternoon. The record heals
    # from the CLI's own store instead of skipping the fleet.
    import json

    from excephalon import agent_desk

    monkeypatch.setattr(agent_desk, "newest_session_for", lambda cwd, store=None: "sess-found")
    state = tmp_path / "agents.json"
    state.write_text(json.dumps([
        {"name": "stray", "cwd": "/wt/stray", "task": "carry on",
         "session_id": None, "state": "idle", "delivery": "ready", "steps": "look"},
    ]), encoding="utf-8")
    resumed = []

    class MuteAgent:  # resumed on the recovered id, and silent - no id of its own yet
        session_id = None

        def work(self, message, on_message=None):
            return ""

        def close(self):
            pass

    def factory(name, cwd, decide, *, model, effort, resume=None):
        resumed.append(resume)
        return MuteAgent()

    desk = AgentDesk(Outbox(), agent_factory=factory, state_path=state)

    assert desk.revive() == ["stray"]
    assert resumed == ["sess-found"]
    [kept] = json.loads(state.read_text(encoding="utf-8"))
    assert kept["session_id"] == "sess-found"  # and the record heals with it
    desk.close()


def test_revive_reminds_him_of_work_presented_that_he_never_ruled_on(tmp_path):
    # He rejected a round, the agent fixed everything and presented again into an app he had just
    # closed, and the next launch said nothing at all - nothing re-engages an idle agent, and the
    # review simply stopped existing. A revived agent holding presented work with no verdict is
    # news for HIM, so it is raised at startup rather than waiting for him to think to ask.
    import json

    state = tmp_path / "agents.json"
    state.write_text(json.dumps([
        {"name": "presenter", "cwd": "/wt/presenter", "task": "fix the copy buttons",
         "session_id": "sess-p", "state": "idle", "delivery": "ready",
         "steps": "open the page and click the link button"},
    ]), encoding="utf-8")
    seen = []
    desk, _, _ = _desk(state=state)
    desk._events = lambda kind, agent, report: seen.append((kind, agent, report))

    desk.revive()

    assert seen == [("pending", "presenter", "open the page and click the link button")]
    assert desk._desked["presenter"].agent.messages == []  # the agent itself is left in peace
    desk.close()


def test_an_agent_whose_log_is_archived_is_not_brought_back(tmp_path):
    # The survival record is written on the way DOWN, so an agent wrapped up from outside the app
    # comes back from the dead at the next launch - and every mechanism built to keep him informed
    # then re-raises work he has already ruled on: "can you get it to stop talking about this
    # thing, which I've already told it twice is finished? this is the third time it's pestered
    # me." The archive is the wrap-up's own record, and it outlives the file.
    import json

    logs = tmp_path / "agent-logs"
    logs.mkdir()
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    (archive / "settled.log").write_text("[10:00:00] ENTITY> done" + chr(10), encoding="utf-8")
    state = tmp_path / "agents.json"
    state.write_text(json.dumps([
        {"name": "settled", "cwd": "/wt/settled", "task": "the finished work",
         "session_id": "sess-s", "state": "idle", "delivery": "ready", "steps": "look at it"},
        {"name": "live", "cwd": "/wt/live", "task": "still going", "session_id": "sess-l",
         "state": "idle", "delivery": "landing"},
    ]), encoding="utf-8")
    (logs / "live.log").write_text("[10:00:00] ENTITY> carry on" + chr(10), encoding="utf-8")
    outbox = Outbox()
    outbox.push("the settled work is ready for your eyes", about="settled")
    desk, _, _ = _desk(outbox=outbox, state=state, log_dir=logs)

    assert desk.revive() == ["live"]  # the wrapped-up one stays wrapped up

    # And its news goes with it. Asked of the WHOLE queue this was a race the machine usually
    # won: the agent still running is told, on a thread, that the app restarted under its landing,
    # and on a loaded machine that true and wanted line arrives before the drain. What this is
    # about is the settled one - so it asks about the settled one.
    assert [str(news) for news in outbox.drain() if news.about == "settled"] == []
    desk.close()


def test_startup_forgets_held_news_about_agents_with_no_tab(tmp_path):
    # The fleet record can be empty while the spool still holds four reports about an agent that
    # was wrapped up - which is how work he had twice called finished came back a third time,
    # jargon and all, three seconds after a launch. No live log means no tab means it is over.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "still-going.log").write_text("[10:00:00] ENTITY> carry on" + chr(10), encoding="utf-8")
    outbox = Outbox()
    outbox.push("the finished one is ready for your eyes", about="all-done")
    outbox.push("the live one needs a decision", about="still-going")
    desk, _, _ = _desk(outbox=outbox, state=tmp_path / "agents.json", log_dir=logs)

    desk.revive()

    assert [str(news) for news in outbox.drain()] == ["the live one needs a decision"]


def test_revive_with_no_state_file_is_a_quiet_no_op(tmp_path):
    desk, _, _ = _desk(state=tmp_path / "missing.json")

    assert desk.revive() == []


def test_an_entry_with_no_session_id_cannot_be_revived_and_is_skipped(tmp_path):
    import json

    state = tmp_path / "agents.json"
    state.write_text(json.dumps([{"name": "ghost", "cwd": "/wt/g", "task": "?",
                                  "session_id": None, "state": "idle"}]), encoding="utf-8")
    desk, _, _ = _desk(state=state)

    assert desk.revive() == []


def _finished(desk, outbox, name="fixer", cwd="/wt/fixer", task="a task"):
    """Start one agent and wait until its first turn is done - the usual bench for delivery tests."""
    desk.start(name, cwd, task)
    assert _wait_for(lambda: bool(outbox))
    outbox.drain()
    return name


def test_presented_work_shows_in_the_digest_awaiting_a_verdict():
    desk, outbox, _ = _desk()
    _finished(desk, outbox)

    desk.present("fixer", "Open localhost:5300 and click Export.")

    assert "in review - presented, awaiting his verdict" in desk.digest()
    desk.close()


def test_the_digest_names_recently_wrapped_agents_so_their_names_still_resolve(tmp_path):
    # "it couldn't figure out which agent I was talking about even though I was using the same
    # name for it that it had been using" - briefed from the live fleet alone, the brain could
    # not see that a wrapped agent had ever existed, and reconstructed its fate from stale memory
    # ("That agent stalled on the merge") about work that had in fact merged.
    import os
    import time as _time

    logs = tmp_path / "agent-logs"
    logs.mkdir()
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    (archive / "submission-feedback.log").write_text("old\n", encoding="utf-8")
    (archive / "smart-grouping.log").write_text("new\n", encoding="utf-8")
    past = _time.time() - 3600
    os.utime(archive / "submission-feedback.log", (past, past))
    desk, _, _ = _desk(log_dir=logs)

    briefing = desk.digest()

    assert "Recently wrapped up" in briefing
    # Newest first, so the name he used a minute ago is the first one the brain sees.
    assert briefing.index("smart-grouping") < briefing.index("submission-feedback")
    assert "archive" in briefing
    desk.close()


def test_the_digest_says_when_presented_work_has_not_actually_reached_them_yet():
    # mark_ready fires when the announcement is COMPOSED; the announcement itself can then wait
    # in the queue for an hour. Briefed "presented, awaiting their verdict" across that gap, the
    # brain told him "I presented it earlier... no new update since then" about a walkthrough he
    # had never heard ("That's false. You never presented it to me."). While the outbox still
    # owes news about the agent, the briefing says the work has NOT been seen.
    desk, outbox, made = _desk()
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))  # its report is queued - and stays queued, unheard

    desk.present("fixer", "Open localhost:5300 and click Export.")

    briefing = desk.digest()
    assert "presented, awaiting their verdict" not in briefing
    assert "has NOT reached them yet" in briefing
    desk.close()


def test_hand_over_news_marks_held_news_to_be_spoken_and_says_when_there_is_none():
    # The brain's one honest way to answer "give me the update on X": the app speaks the held
    # copy word for word. Retold in the brain's own words instead, the app then delivered its
    # copy too - the same news twice, 13 seconds apart.
    desk, outbox, _ = _desk()
    outbox.push("fixer: ready for your eyes", about="fixer")

    assert desk.hand_over_news("fixer") is True
    assert outbox.take_requested() == {"fixer"}
    assert desk.hand_over_news("nobody") is False  # nothing held: the brain answers, not the app
    desk.close()


def test_drop_news_forgets_held_news_about_one_agent_and_only_that_agent():
    # The user's new words to an agent supersede whatever it was waiting to say - but a foreman
    # prod must never eat news owed to the user, so the drop is its own gesture, not part of send.
    desk, outbox, _ = _desk()
    outbox.push("fixer: done", about="fixer")
    outbox.push("other: needs a decision", about="other")

    desk.drop_news("fixer")

    assert [str(news) for news in outbox.drain()] == ["other: needs a decision"]
    desk.close()


def test_work_cannot_be_presented_while_the_agent_is_still_working():
    # The steps come from the agent's report; marking mid-turn would present a thing that does
    # not exist yet.
    import pytest

    from excephalon.delivery import DeliveryError

    hold = threading.Event()
    desk, _, made = _desk(hold=hold)
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: made and made[0].messages)

    with pytest.raises(DeliveryError):
        desk.present("fixer", "steps")
    hold.set()
    desk.close()


def test_presenting_an_agent_the_desk_does_not_have_refuses():
    import pytest

    from excephalon.delivery import DeliveryError

    desk, _, _ = _desk()

    with pytest.raises(DeliveryError):
        desk.present("nobody", "steps")


def test_an_approved_verdict_dispatches_the_landing():
    # After the user signs off, everything left is mechanical: the desk itself sends the agent
    # to land the work - no one has to remember to ask.
    desk, outbox, made = _desk()
    _finished(desk, outbox)
    desk.present("fixer", "steps")

    desk.verdict("fixer", approved=True)

    assert _wait_for(lambda: len(made[0].messages) == 2)
    assert "signed off" in made[0].messages[1]
    assert "landing - approved, being merged now" in desk.digest()
    desk.close()


def test_a_rejected_verdict_carries_the_feedback_back():
    desk, outbox, made = _desk()
    _finished(desk, outbox)
    desk.present("fixer", "steps")

    desk.verdict("fixer", approved=False, feedback="The button is on the wrong side.")

    assert _wait_for(lambda: len(made[0].messages) == 2)
    assert "The button is on the wrong side." in made[0].messages[1]
    assert "awaiting their verdict" not in desk.digest()  # back to plain being-built
    desk.close()


def test_a_verdict_with_no_presentation_is_refused_by_the_desk():
    import pytest

    from excephalon.delivery import DeliveryError

    desk, outbox, _ = _desk()
    _finished(desk, outbox)

    with pytest.raises(DeliveryError):
        desk.verdict("fixer", approved=True)
    desk.close()


def test_the_delivery_stage_survives_into_the_state_file_and_back(tmp_path):
    # A restart mid-loop must not lose where the work stood: presented work is still presented,
    # its steps still on file, when the next process revives the fleet.
    import json

    state = tmp_path / "agents.json"
    desk, outbox, _ = _desk(state=state)
    _finished(desk, outbox)
    desk.present("fixer", "Open localhost:5300.")
    desk.close()

    [entry] = json.loads(state.read_text(encoding="utf-8"))
    assert entry["delivery"] == "ready"
    assert entry["steps"] == "Open localhost:5300."

    reborn, reborn_outbox, _ = _desk(state=state)
    reborn.revive()
    # The revival reminder it just queued is still unspoken, so the briefing says the work has
    # not reached them; the moment that goes out, the plain presented line returns.
    assert "has NOT reached them yet" in reborn.digest()
    reborn_outbox.drain()
    assert "in review - presented, awaiting his verdict" in reborn.digest()
    assert reborn.delivery_stage("fixer") == "ready"
    reborn.close()


def test_the_narrator_can_ask_which_stage_an_agent_is_at():
    desk, outbox, _ = _desk()
    assert desk.delivery_stage("fixer") is None  # unknown agent: no stage, not an error
    _finished(desk, outbox)
    desk.present("fixer", "steps")
    desk.verdict("fixer", approved=True)

    assert desk.delivery_stage("fixer") == "landing"
    desk.close()


def test_the_desk_can_say_what_an_agent_is_working_on():
    desk, outbox, _ = _desk()
    _finished(desk, outbox, task="fix the drive link")

    assert desk.task_of("fixer") == "fix the drive link"
    assert desk.task_of("nobody") is None
    desk.close()


def test_the_desk_hands_over_an_agents_recent_log_for_a_senior_read(tmp_path):
    # The foreman judges from what actually happened, and the log is where that lives. The tail,
    # not the whole file: a day-long exchange would drown the situation it ends on.
    desk, outbox, _ = _desk(log_dir=tmp_path)
    _finished(desk, outbox)

    tail = desk.recent_log("fixer")

    assert "did: a task" in tail  # the exchange the fake agent streamed is in the tail
    assert desk.recent_log("nobody") == ""
    desk.close()


def test_the_standing_rule_carries_the_engineering_law_not_just_the_review_law():
    # "how to TDD etc. is ultra critical" - and an agent may work in a repo whose CLAUDE.md is
    # thin or missing, so the discipline rides with the task itself: test-driven, full suite
    # green, land through the repo's own process, leave it cleaner.
    desk, outbox, made = _desk()
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))

    [task] = made[0].messages
    assert "test-drive" in task
    assert "full test suite" in task
    assert "merge queue" in task
    assert "CLAUDE.md" in task
    desk.close()


def test_every_task_points_the_agent_at_the_machine_wide_engineering_law(tmp_path):
    # "why wouldn't that be in the global CLAUDE.md?" - it is, and agents can't load that file
    # (its conversation rules break them). The engineering half now lives in its own file, and
    # every task points there: one source, read fresh by each agent, never pasted stale.
    law = tmp_path / "engineering.md"
    law.write_text("# engineering law", encoding="utf-8")
    desk, outbox, made = _desk(law=law)

    desk.start("fixer", "/wt/fixer", "a task")

    assert _wait_for(lambda: bool(outbox))
    assert str(law) in made[0].messages[0]
    assert "engineering law" in made[0].messages[0]
    desk.close()


def test_a_law_file_that_is_not_there_adds_no_pointer(tmp_path):
    # A checkout without the split (another machine, a fresh clone) must not send agents chasing
    # a file that does not exist.
    desk, outbox, made = _desk(law=tmp_path / "missing.md")

    desk.start("fixer", "/wt/fixer", "a task")

    assert _wait_for(lambda: bool(outbox))
    assert "missing.md" not in made[0].messages[0]
    desk.close()


def test_a_merge_before_approval_is_refused_with_a_reason():
    # The code half of the review loop: an agent must not enqueue its own PR before the user has
    # seen the work. frozen-tabs and enhancements-tab-fixes both merged with no verdict on file
    # because the persona was only ASKED to wait - a gate that cannot forget is the fix.
    from excephalon.agent_desk import landing_block_reason

    reason = landing_block_reason("building", "Bash", {"command": "gh pr merge --auto"})

    assert reason  # a non-empty string the agent is shown, not a silent no
    assert "approved" in reason.lower()


def test_landing_is_allowed_once_the_verdict_is_in():
    # After an approved verdict the work's stage is "landing", and the mechanical push/PR/merge is
    # exactly what should run - the gate must not stand in the way of the step it was waiting for.
    from excephalon.agent_desk import landing_block_reason

    assert landing_block_reason("landing", "Bash", {"command": "gh pr merge --auto"}) is None
    assert landing_block_reason("landing", "Bash", {"command": "git push -u origin HEAD"}) is None


def test_push_and_opening_a_pr_are_landing_too_not_only_the_merge():
    # "pushing PRs, auto-merging, etc." - the whole outward-facing set is gated, not just the final
    # enqueue, because a pushed branch with auto-merge armed lands with no further command.
    from excephalon.agent_desk import landing_block_reason

    assert landing_block_reason("building", "Bash", {"command": "git push origin HEAD"})
    assert landing_block_reason("ready", "Bash", {"command": "gh pr create --fill --base main"})
    assert landing_block_reason("ready", "Bash", {"command": "gh pr ready 42"})


def test_local_work_and_presenting_never_trip_the_gate():
    # Rebasing onto latest main and standing the app up to present it are the steps the agent takes
    # BEFORE a verdict - blocking those would make presenting impossible and defeat the loop.
    from excephalon.agent_desk import landing_block_reason

    for command in ("git fetch origin", "git rebase origin/main", "git commit -m wip",
                    "python -m excephalon", "gh pr view 42", "pytest -q"):
        assert landing_block_reason("building", "Bash", {"command": command}) is None


def test_only_bash_commands_are_gated():
    # The gate reads shell commands; a read or an edit carries no way to ship work outward.
    from excephalon.agent_desk import landing_block_reason

    assert landing_block_reason("building", "Edit", {"file_path": "x", "new_string": "git push"}) is None
    assert landing_block_reason("building", "Read", {"file_path": "gh pr merge"}) is None


def test_the_desk_refuses_an_agents_landing_until_its_verdict_is_in():
    # End to end through the SAME callback a real agent's tools pass through (the desk hands it to
    # the factory): a landing command is denied while the work is unreviewed, and unblocked the
    # instant an approved verdict moves the work to "landing".
    import asyncio

    desk, outbox, made = _desk()
    name = _finished(desk, outbox)
    decide = made[0].decide  # exactly what SupervisedAgent's permission handler will consult

    blocked = asyncio.run(decide(name, "Bash", {"command": "gh pr merge --auto"}))
    assert isinstance(blocked, str) and "approved" in blocked.lower()

    desk.present(name, "steps")
    desk.verdict(name, approved=True)

    assert asyncio.run(decide(name, "Bash", {"command": "gh pr merge --auto"})) is True
    desk.close()


def test_the_desk_waves_ordinary_agent_work_through():
    # The gate is narrow: everything that is not an unapproved landing still passes, so the agents
    # the user runs unattended are not suddenly stopped at every step.
    import asyncio

    desk, outbox, made = _desk()
    name = _finished(desk, outbox)
    decide = made[0].decide

    assert asyncio.run(decide(name, "Bash", {"command": "pytest -q"})) is True
    assert asyncio.run(decide(name, "Edit", {"file_path": "x"})) is True
    desk.close()


def test_the_standing_rule_tells_the_agent_to_stop_at_presenting_and_wait_for_sign_off():
    # The instruction half of the landing gate: presenting is where the agent STOPS. It lands only
    # after the user has signed off, so a compliant agent never trips the code gate - and one that
    # would try is told, in the task itself, why the push will be refused.
    desk, outbox, made = _desk()
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))

    [task] = made[0].messages
    assert "signed off" in task
    assert "do NOT push" in task
    desk.close()


def test_a_landing_agents_silence_clock_keeps_running():
    # "Excephalon must proactively monitor agent progress and alert the user when progress stalls."
    # The overnight failure: agents told to land went idle, their merge-watchers fired into ended
    # turns, and nobody was counting their silence - so a stall was invisible until he asked. An
    # agent still owing a merge report is still ON the clock; only its retirement stops it.
    monitor = SpyMonitor()
    desk, outbox, _ = _desk(monitor=monitor)
    desk.start("lander", "/wt/lander", "build the thing")
    assert _wait_for(lambda: monitor.finished == ["lander"])
    outbox.drain()

    desk.present("lander", "1. open the page")
    desk.verdict("lander", approved=True)  # dispatches the landing; stage is now "landing"
    assert _wait_for(lambda: bool(outbox))

    # It finished its landing TURN but has not merged: the clock must still be running.
    assert monitor.finished == ["lander"]  # done() was NOT called a second time
    outbox.drain()  # its report reached him, so the wrap-up is legal
    assert desk.retire("lander")
    assert monitor.finished == ["lander", "lander"]  # retirement is what stops the clock
    desk.close()


def test_an_agent_keeps_its_log_when_only_the_capitals_of_its_name_change(tmp_path):
    # On Windows one file answers to its name in any case, so a case change is a rename of THIS
    # log, not a collision with another tab - and it is the rename an all-caps heading provoked.
    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs)
    desk.start("inbox-AUTO-play-toggle", "/wt/auto", "a task")
    assert _wait_for(lambda: bool(outbox))  # the log exists once it has spoken

    assert desk.rename("inbox-AUTO-play-toggle", "inbox-auto-play-toggle") is True

    assert [log.name for log in logs.glob("*.log")] == ["inbox-auto-play-toggle.log"]
    assert "inbox-auto-play-toggle" in desk.digest()
    desk.close()


def test_an_agent_can_be_renamed_and_the_app_uses_the_new_name(tmp_path):
    # "I should be able to rename these agents, and Excephalon should use the name I change them
    # to." A name is a label: the desk's key, the log the window draws a tab from, the record a
    # restart revives from, and the tag on news waiting to be spoken. All of it moves; the
    # worktree keeps its own name, which is git's business.
    logs = tmp_path / "agent-logs"
    state = tmp_path / "agents.json"
    desk, outbox, _ = _desk(log_dir=logs, state=state)
    desk.start("excephalon-link-copy-fixes", "/wt/copy", "fix the copy buttons")
    assert _wait_for(lambda: bool(outbox))

    assert desk.rename("excephalon-link-copy-fixes", "the copy fixes") is True

    import json

    assert (logs / "the-copy-fixes.log").exists()          # his name, filed
    assert not (logs / "entity-link-copy-fixes.log").exists()
    assert "the-copy-fixes" in desk.digest()               # and what the brain is told each turn
    [kept] = json.loads(state.read_text(encoding="utf-8"))
    assert (kept["name"], kept["cwd"]) == ("the-copy-fixes", "/wt/copy")
    assert [news.about for news in outbox.drain()] == ["the-copy-fixes"]  # held news follows
    desk.close()


def test_a_rename_refuses_a_name_already_taken_or_no_name_at_all(tmp_path):
    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs)
    desk.start("one", "/wt/one", "a task")
    desk.start("two", "/wt/two", "another")
    assert _wait_for(lambda: bool(outbox))

    assert desk.rename("one", "two") is False    # a name in use would collide two tabs into one
    assert desk.rename("one", "   ") is False    # nothing a file could carry
    assert desk.rename("nobody", "three") is False
    assert desk.rename("one", "one") is True     # the same name is not a failure, just a no-op
    desk.close()


def test_the_state_that_allows_a_wrap_up_is_the_state_that_ticks_its_item(tmp_path):
    # retire() used to read the agent's state twice - once to judge the wrap-up legal, and again,
    # a lock and a `git worktree remove` later, to decide whether the Enhancements item may be
    # ticked. A dispatch landing in that gap (a revived landing agent picking its merge back up
    # is dispatched on a thread, so the gap is real) left the two readings disagreeing: the
    # wrap-up went ahead on "idle" and the tick was withheld on "working", and he met a finished
    # feature whose ticket was still open with a "settle it by hand" notice for company. One
    # judgement, taken once, decides both.
    ticked = []
    entry = {}

    def run_and_move_the_state(*args, **kwargs):
        # The window itself: whatever the desk does between judging and ticking, the agent may
        # start working again under it.
        entry["desked"].state = "working"

    desk, outbox, _ = _desk(log_dir=tmp_path / "agent-logs", run=run_and_move_the_state,
                            complete=lambda item: ticked.append(item) or True)
    desk.start("voice", str(tmp_path / "wt"), "wire the voice", enhancement="Better voice")
    assert _wait_for(lambda: bool(outbox))
    _approved(desk, "voice")
    entry["desked"] = desk._desked["voice"]

    assert desk.retire("voice") is True
    assert ticked == ["Better voice"]


def test_a_wrap_up_writes_how_the_thread_ended_and_the_briefing_says_it(tmp_path):
    # The ladder's last rung. The archive alone keeps bare names, and a briefing of bare names
    # left "delivered" a fact nobody held: minutes after robot-icon-ui landed and wrapped up, the
    # brain told him "The robot icon UI work is done now; ready for you to look at" - reopening
    # review of finished work. The ending is a record now, and the digest reads it back.
    logs = tmp_path / "agent-logs"
    record = tmp_path / "wrapped.json"
    moment = [1000.0]
    desk, outbox, _ = _desk(log_dir=logs)
    desk._wrapped_path = record
    desk._now = lambda: moment[0]
    desk.start("robot-icon-ui", "/tmp/wt", "the robot icons on the Projects tab")
    assert _wait_for(lambda: bool(outbox))
    _approved(desk, "robot-icon-ui")

    assert desk.retire("robot-icon-ui") is True
    moment[0] = 1000.0 + 16 * 60

    briefing = desk.digest()
    assert "robot-icon-ui: DELIVERED 16 minutes ago" in briefing
    assert "was on: the robot icons on the Projects tab" in briefing
    desk.close()


def test_a_died_agent_is_recorded_as_died_never_delivered(tmp_path):
    logs = tmp_path / "agent-logs"
    outbox = Outbox()
    desk = AgentDesk(outbox, agent_factory=lambda *a, **k: _DyingAgent(), log_dir=logs)
    desk._wrapped_path = tmp_path / "wrapped.json"
    desk._now = lambda: 1000.0
    desk.start("doomed", "/tmp/wt", "a task")
    assert _wait_for(lambda: desk._desked["doomed"].state == "failed")
    outbox.drain()

    assert desk.retire("doomed") is True

    assert "doomed: DIED just now" in desk.digest()
    desk.close()


def test_a_crash_is_restarted_silently_and_never_reaches_him():
    # "I should never need to know that anything died. It's not relevant to me." Asked whether to
    # restart a dead agent, he answered "Yes, of course... your job is to insulate me from this
    # kind of Pointless delay." One crash gets a fresh session on the same history and a
    # pick-back-up - no event, no news, and the streak resets on the clean turn.
    events = []
    made = []

    class DiesOnce:
        session_id = "sess-fixer"

        def __init__(self, resume=None):
            self.resumed_from = resume
            self.messages = []

        def work(self, message, on_message=None):
            self.messages.append(message)
            if self is made[0]:
                raise RuntimeError("server error")
            return "picked back up and finished"

        def close(self):
            pass

    def factory(name, cwd, decide, resume=None, **choice):
        made.append(DiesOnce(resume))
        return made[-1]

    desk = AgentDesk(Outbox(), agent_factory=factory, events=lambda *e: events.append(e))
    desk.start("fixer", "/tmp/wt", "a task")

    assert _wait_for(lambda: any(e[0] == "finished" for e in events))
    assert not [e for e in events if e[0] == "died"]
    assert made[1].resumed_from == "sess-fixer"  # the same history: nothing of the task lost
    assert "pick up exactly where you left off" in made[1].messages[0].lower()
    assert desk._desked["fixer"].deaths == 0
    desk.close()


def test_a_task_that_keeps_killing_its_agents_finally_reaches_him():
    # Bounded: a third death in a row is not weather, it is a task that keeps killing its agents,
    # and silently feeding it new sessions forever would hide work that is genuinely stuck. The
    # digest holds the crash count as a quiet fact for "what's taking so long?".
    events = []
    desk = AgentDesk(Outbox(), agent_factory=lambda *a, **k: _DyingAgent(),
                     events=lambda *e: events.append(e))
    desk.start("doomed", "/tmp/wt", "try")

    assert _wait_for(lambda: any(e[0] == "died" for e in events))
    assert len([e for e in events if e[0] == "died"]) == 1  # two restarts were silent
    assert desk._desked["doomed"].deaths == 3
    desk.close()


def test_a_truncated_or_retyped_name_still_reaches_the_agent():
    # The desk names agents by truncating his words to a filename, then hands that name to the
    # brain and the foreman - who retype it. One retyping missed, the foreman answered "the
    # agent isn't reachable at the desk under the name I was given", and its failure report
    # reached the user as a heads-up about machinery: "it can't find the agent... it should be
    # smart enough to just look at open/recent agents or ones with names that are similar. this
    # is bullshit I shouldn't be pestered about."
    task = ("when a task is multiline here in the Projects tab, the robot icon link to the "
            "agent log is vertically centered")
    from excephalon.tailing import safe_name

    key = safe_name(task)  # the 60-char truncation the desk itself made
    desk, _, made = _desk()
    desk.start(key, "/tmp/wt", task)
    assert _wait_for(lambda: made and len(made[0].messages) == 1)

    assert desk.send(task, "the full sentence he actually said") is True  # untruncated
    assert desk.send(key.rstrip("-"), "the name minus its odd trailing dash") is True
    assert desk.send("the robot icon multiline fix", "words that share its tokens") is True
    assert _wait_for(lambda: len(made[0].messages) == 4)
    assert desk.resolve("no such agent at all, nothing shared") is None
    desk.close()


def test_an_ambiguous_name_is_never_guessed_between_two():
    desk, _, made = _desk()
    desk.start("alpha-scrubber-fix", "/tmp/a", "fix the scrubber")
    desk.start("alpha-scrubber-polish", "/tmp/b", "polish the scrubber")
    assert _wait_for(lambda: len(made) == 2 and all(a.messages for a in made))

    assert desk.resolve("alpha-scrubber") is None  # two candidates: refusing beats guessing
    assert desk.send("alpha-scrubber", "hello?") is False
    assert desk.resolve("alpha-scrubber-polish") == "alpha-scrubber-polish"
    desk.close()


def test_held_news_is_dropped_by_whatever_name_he_used():
    # tell_agent drops the agent's held news (his new words supersede it) - and the drop must
    # land however the name was said, or the stale update is still offered afterwards.
    desk, outbox, made = _desk()
    desk.start("robot-icon-alignment", "/tmp/wt", "align the robot icons")
    assert _wait_for(lambda: bool(outbox))

    desk.drop_news("the robot icon alignment work")

    assert not outbox.owed_about()
    desk.close()


def test_approved_work_that_reached_main_is_wrapped_up_by_the_desk_itself(tmp_path):
    # The loop's last leg used to hang on a narration commanding the brain to close the tab;
    # that narration failed once and the merged agent haunted the desk for fourteen hours -
    # revived every boot, re-presenting delivered work as new, refusing retirement - while the
    # ticket sat open ("make sure that tasks are designed to be automatically checked off when
    # the work gets finished"). The desk asks git whether the branch reached origin/main and
    # walks the wrap-up itself: item ticked, log archived, and only then the news, as "landed".
    events, ticked, made = [], [], []
    logs = tmp_path / "agent-logs"

    def factory(name, cwd, decide, **choice):
        made.append(FakeAgent(name, cwd, decide))
        return made[-1]

    desk = AgentDesk(Outbox(), agent_factory=factory, log_dir=logs,
                     run=lambda *a, **k: SimpleNamespace(returncode=0),
                     events=lambda *e: events.append(e),
                     complete_enhancement=lambda item, **where: ticked.append(item) or True)
    desk.start("lander", "/tmp/wt", "align the icons", enhancement="fix the icons")
    assert _wait_for(lambda: any(e[0] == "finished" for e in events))
    desk.present("lander", "open the demo")
    desk.verdict("lander", True)

    assert _wait_for(lambda: any(e[0] == "landed" for e in events))
    assert ticked == ["fix the icons"]
    assert (tmp_path / "agent-logs-archive" / "lander.log").exists()
    assert desk.roster() == []  # retired: nothing left to haunt the next boot
    desk.close()


def test_a_wrapped_agents_log_and_task_are_still_on_file(tmp_path):
    # "it says 'with no log I can't confirm' but that's fucking bullshit, the logs are right
    # there." The auto-wrap-up moved the log to the archive and the foreman's read then found
    # nothing: a wrapped agent's name still resolves, its log still reads, its task and its
    # ending are still answerable.
    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs)
    desk._wrapped_path = tmp_path / "wrapped.json"
    desk._now = lambda: 1000.0
    desk.start("autoplay-fix", "/tmp/wt", "make the auto-play choice stick")
    assert _wait_for(lambda: bool(outbox))
    _approved(desk, "autoplay-fix")
    assert desk.retire("autoplay-fix") is True

    assert desk.resolve("the autoplay fix") == "autoplay-fix"
    assert "make the auto-play choice stick" in desk.recent_log("autoplay-fix")
    assert desk.task_of("autoplay-fix") == "make the auto-play choice stick"
    assert desk.ended("autoplay-fix") == "delivered"
    desk.close()
