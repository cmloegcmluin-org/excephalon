from excephalon.inbox_watcher import InboxWatcher
from excephalon.threads import Ledger


class Heard:
    """The events sink the app wires in: (kind, agent, what the agent wrote).

    The watcher's job is to notice a complete line and say WHO wrote it and WHAT - never to word
    anything. What the user hears is composed downstream from his own name for the work, because
    an agent's own sentence reaching him is a conversation he was not part of.
    """

    def __init__(self):
        self.events = []

    def __call__(self, kind, agent, report):
        self.events.append((kind, agent, report))

    def reports(self):
        taken, self.events = [report for _, _, report in self.events], []
        return taken


class SpyMonitor:
    """Records the InboxWatcher's activity signals without any real timing."""

    def __init__(self):
        self.check_ins = []
        self.ticks = 0

    def checked_in(self, agent):
        self.check_ins.append(agent)

    def tick(self):
        self.ticks += 1


def test_a_new_complete_line_is_pushed_to_the_outbox(tmp_path):
    heard = Heard()
    watcher = InboxWatcher(tmp_path, Ledger(), events=heard)
    (tmp_path / "auth-agent.txt").write_text("I need your call: JWT or sessions?\n", encoding="utf-8")

    watcher.poll_once()

    # The event, not a spoken line: who wrote, and what. What he HEARS is composed downstream
    # from his own name for the work - an agent's own sentence reaching him is a conversation he
    # was not part of ("basically this whole message is useless, insane, confusing, and terrible").
    assert heard.events == [("wrote", "auth-agent", "I need your call: JWT or sessions?")]


def test_content_written_before_watching_is_not_replayed(tmp_path):
    (tmp_path / "old.txt").write_text("stale question from before startup\n", encoding="utf-8")
    heard = Heard()
    watcher = InboxWatcher(tmp_path, Ledger(), events=heard)  # seeds offsets past existing content

    watcher.poll_once()

    assert heard.reports() == []  # only news that arrives while watching surfaces


def test_a_partial_line_waits_until_its_newline_arrives(tmp_path):
    heard = Heard()
    watcher = InboxWatcher(tmp_path, Ledger(), events=heard)
    f = tmp_path / "agent.txt"
    f.write_text("still typing this th", encoding="utf-8")  # no newline yet

    watcher.poll_once()
    assert heard.reports() == []  # a half-written line isn't spoken

    with open(f, "a", encoding="utf-8") as fh:
        fh.write("ought\n")
    watcher.poll_once()
    assert heard.reports() == ["still typing this thought"]  # surfaces once complete


def test_lines_across_several_files_all_surface(tmp_path):
    heard = Heard()
    watcher = InboxWatcher(tmp_path, Ledger(), events=heard)
    (tmp_path / "a.txt").write_text("agent A is ready for review\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("agent B hit a failing test\n", encoding="utf-8")

    watcher.poll_once()

    assert set(heard.reports()) == {"agent A is ready for review", "agent B hit a failing test"}


def test_a_cleared_inbox_file_resyncs_from_the_top(tmp_path):
    # Inboxes are append-only in normal use, but if one is cleared and reused, a shrink below where
    # we'd read tells us to resync so the next line isn't lost.
    heard = Heard()
    watcher = InboxWatcher(tmp_path, Ledger(), events=heard)
    f = tmp_path / "agent.txt"
    f.write_text("first question\n", encoding="utf-8")
    watcher.poll_once()
    assert heard.reports() == ["first question"]

    f.write_text("", encoding="utf-8")  # cleared (shrinks below our offset)
    watcher.poll_once()
    f.write_text("second question\n", encoding="utf-8")
    watcher.poll_once()

    assert heard.reports() == ["second question"]


def test_blank_lines_are_ignored(tmp_path):
    heard = Heard()
    watcher = InboxWatcher(tmp_path, Ledger(), events=heard)
    (tmp_path / "agent.txt").write_text("\n  \nreal message\n\n", encoding="utf-8")

    watcher.poll_once()

    assert heard.reports() == ["real message"]


def test_each_poll_ticks_the_monitor(tmp_path):
    # The watcher's only remaining business with the monitor: it is the cheap poll the silence
    # check rides on. WHICH agents exist, and when each last spoke, comes from the desk - a
    # filename in here is not an agent, and reading it as one invented two of them.
    monitor = SpyMonitor()
    watcher = InboxWatcher(tmp_path, Ledger(), monitor=monitor)
    (tmp_path / "not-an-agent.txt").write_text("a note Excephalon wrote to itself\n", encoding="utf-8")

    watcher.poll_once()
    watcher.poll_once()

    assert monitor.ticks == 2
    assert monitor.check_ins == []  # no agent was conjured out of a file


def test_a_multi_line_report_arrives_as_one_event_not_line_by_line(tmp_path):
    # An agent overwrote its inbox file with a 30-line report; every line became its own spoken
    # heads-up, and they had to hit STOP for each one in turn. It is one event now, and what the
    # user hears is composed from his own name for the work - never from these lines.
    heard = Heard()
    watcher = InboxWatcher(tmp_path, Ledger(), events=heard)
    (tmp_path / "fixer.txt").write_text(
        "IN PROGRESS - backfill. Found a leaking test in your real state folder.\n"
        "Root cause: build_app calls load_dotenv.\nFixed and committed as 91459e5.\n",
        encoding="utf-8",
    )

    watcher.poll_once()

    assert len(heard.events) == 1  # one event, not one per line

def test_with_an_events_sink_a_written_line_reports_there_instead(tmp_path):
    # The narrator words agent news in the brain's own voice; the watcher's job shrinks to saying
    # what was written and by whom.
    from excephalon.threads import Ledger

    events = []
    outbox = Ledger()
    watcher = InboxWatcher(tmp_path, outbox, events=lambda *e: events.append(e))
    (tmp_path / "fixer.txt").write_text("Need your OAuth step before I can continue.\n",
                                        encoding="utf-8")

    watcher.poll_once()

    assert events == [("wrote", "fixer", "Need your OAuth step before I can continue.")]
    assert not outbox
