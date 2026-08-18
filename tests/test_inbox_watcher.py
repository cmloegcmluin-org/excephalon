from excephalon.inbox_watcher import InboxWatcher
from excephalon.outbox import Outbox


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
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "auth-agent.txt").write_text("I need your call: JWT or sessions?\n", encoding="utf-8")

    watcher.poll_once()

    [news] = outbox.drain()
    assert news == "I need your call: JWT or sessions?"  # his news, not a name-tag
    # Named, so several landing together can be read out by name for one of them to be picked -
    # rather than the name being worked back out of the sentence the agent happened to write.
    assert news.about == "auth-agent"


def test_content_written_before_watching_is_not_replayed(tmp_path):
    (tmp_path / "old.txt").write_text("stale question from before startup\n", encoding="utf-8")
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)  # seeds offsets past existing content

    watcher.poll_once()

    assert outbox.drain() == []  # only news that arrives while watching surfaces


def test_a_partial_line_waits_until_its_newline_arrives(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    f = tmp_path / "agent.txt"
    f.write_text("still typing this th", encoding="utf-8")  # no newline yet

    watcher.poll_once()
    assert outbox.drain() == []  # a half-written line isn't spoken

    with open(f, "a", encoding="utf-8") as fh:
        fh.write("ought\n")
    watcher.poll_once()
    assert outbox.drain() == ["still typing this thought"]  # surfaces once complete


def test_lines_across_several_files_all_surface(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "a.txt").write_text("agent A is ready for review\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("agent B hit a failing test\n", encoding="utf-8")

    watcher.poll_once()

    assert set(outbox.drain()) == {"agent A is ready for review", "agent B hit a failing test"}


def test_a_cleared_inbox_file_resyncs_from_the_top(tmp_path):
    # Inboxes are append-only in normal use, but if one is cleared and reused, a shrink below where
    # we'd read tells us to resync so the next line isn't lost.
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    f = tmp_path / "agent.txt"
    f.write_text("first question\n", encoding="utf-8")
    watcher.poll_once()
    assert outbox.drain() == ["first question"]

    f.write_text("", encoding="utf-8")  # cleared (shrinks below our offset)
    watcher.poll_once()
    f.write_text("second question\n", encoding="utf-8")
    watcher.poll_once()

    assert outbox.drain() == ["second question"]


def test_blank_lines_are_ignored(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "agent.txt").write_text("\n  \nreal message\n\n", encoding="utf-8")

    watcher.poll_once()

    assert outbox.drain() == ["real message"]


def test_each_poll_ticks_the_monitor(tmp_path):
    # The watcher's only remaining business with the monitor: it is the cheap poll the silence
    # check rides on. WHICH agents exist, and when each last spoke, comes from the desk - a
    # filename in here is not an agent, and reading it as one invented two of them.
    monitor = SpyMonitor()
    watcher = InboxWatcher(tmp_path, Outbox(), monitor=monitor)
    (tmp_path / "not-an-agent.txt").write_text("a note Excephalon wrote to itself\n", encoding="utf-8")

    watcher.poll_once()
    watcher.poll_once()

    assert monitor.ticks == 2
    assert monitor.check_ins == []  # no agent was conjured out of a file


def test_a_multi_line_report_arrives_as_one_notice_not_line_by_line(tmp_path):
    # An agent overwrote its inbox file with a 30-line report; every line became its own spoken
    # heads-up, and they had to hit STOP for each one in turn. Now it is one notice, and the report
    # itself stays in that agent's tab.
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "fixer.txt").write_text(
        "IN PROGRESS - backfill. Found a leaking test that wrote into your real state folder.\n"
        "Root cause: build_app calls load_dotenv internally.\nFixed and committed as 91459e5.\n",
        encoding="utf-8",
    )

    watcher.poll_once()

    said = outbox.drain()
    assert said == ["IN PROGRESS - backfill."]
    assert "91459e5" not in said[0]  # its internals never reach them


def test_with_an_events_sink_a_written_line_reports_there_instead(tmp_path):
    # The narrator words agent news in the brain's own voice; the watcher's job shrinks to saying
    # what was written and by whom.
    from excephalon.outbox import Outbox

    events = []
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox, events=lambda *e: events.append(e))
    (tmp_path / "fixer.txt").write_text("Need your OAuth step before I can continue.\n",
                                        encoding="utf-8")

    watcher.poll_once()

    assert events == [("wrote", "fixer", "Need your OAuth step before I can continue.")]
    assert not outbox
