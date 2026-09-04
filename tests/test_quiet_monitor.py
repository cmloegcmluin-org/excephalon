from excephalon.inbox_watcher import QuietMonitor
from excephalon.threads import Ledger


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def test_warns_when_an_agent_is_silent_past_the_threshold():
    outbox = Ledger()
    clock = FakeClock(0.0)
    monitor = QuietMonitor(outbox, quiet_after=1200, clock=clock)
    monitor.checked_in("drive-link")  # last heard from at t=0

    clock.now = 1200  # twenty minutes later, still nothing
    monitor.tick()

    [news] = outbox.drain()
    assert news == "The drive-link agent hasn't checked in for 20 minutes."
    assert news.about == "drive-link"  # silence is news about an agent, and reads out by name too


def test_stays_silent_before_the_threshold():
    outbox = Ledger()
    clock = FakeClock(0.0)
    monitor = QuietMonitor(outbox, quiet_after=1200, clock=clock)
    monitor.checked_in("a")

    clock.now = 1199
    monitor.tick()

    assert outbox.drain() == []


def test_an_agent_never_heard_from_is_not_monitored():
    outbox = Ledger()
    clock = FakeClock(0.0)
    monitor = QuietMonitor(outbox, quiet_after=1200, clock=clock)

    clock.now = 999_999
    monitor.tick()

    assert outbox.drain() == []


def test_warns_only_once_per_silence_episode():
    outbox = Ledger()
    clock = FakeClock(0.0)
    monitor = QuietMonitor(outbox, quiet_after=1200, clock=clock)
    monitor.checked_in("a")

    clock.now = 1200
    monitor.tick()
    assert outbox.drain() == ["The a agent hasn't checked in for 20 minutes."]

    clock.now = 5000  # still silent — but we already said so; no nagging
    monitor.tick()
    assert outbox.drain() == []


def test_a_check_in_rearms_the_warning():
    outbox = Ledger()
    clock = FakeClock(0.0)
    monitor = QuietMonitor(outbox, quiet_after=1200, clock=clock)
    monitor.checked_in("a")

    clock.now = 1200
    monitor.tick()
    outbox.drain()  # heard the first warning

    clock.now = 1300
    monitor.checked_in("a")  # the agent spoke up again
    clock.now = 2500  # ...then went quiet for another twenty minutes
    monitor.tick()

    assert outbox.drain() == ["The a agent hasn't checked in for 20 minutes."]


def test_an_agent_that_has_finished_is_not_reported_silent():
    # Silence only means something while there is work in flight. A finished agent isn't stalled,
    # and saying it has "gone quiet" reads as news about a problem that does not exist.
    outbox = Ledger()
    clock = FakeClock(0.0)
    monitor = QuietMonitor(outbox, quiet_after=1200, clock=clock)
    monitor.checked_in("a")
    monitor.done("a")

    clock.now = 5000
    monitor.tick()

    assert outbox.drain() == []


def test_each_agent_is_tracked_independently():
    outbox = Ledger()
    clock = FakeClock(0.0)
    monitor = QuietMonitor(outbox, quiet_after=1200, clock=clock)
    monitor.checked_in("a")
    clock.now = 600
    monitor.checked_in("b")  # b checked in ten minutes after a

    clock.now = 1200  # a is 20 min silent; b only 10
    monitor.tick()

    assert outbox.drain() == ["The a agent hasn't checked in for 20 minutes."]


def test_elapsed_time_is_reported_not_just_the_threshold():
    outbox = Ledger()
    clock = FakeClock(0.0)
    monitor = QuietMonitor(outbox, quiet_after=1200, clock=clock)
    monitor.checked_in("slow")

    clock.now = 1860  # a poll landed at 31 minutes, not right on the threshold
    monitor.tick()

    assert outbox.drain() == ["The slow agent hasn't checked in for 31 minutes."]


def test_with_an_events_sink_silence_reports_there_instead():
    from excephalon.threads import Ledger

    events = []
    outbox = Ledger()
    clock = FakeClock()
    monitor = QuietMonitor(outbox, quiet_after=60, clock=clock,
                           events=lambda *e: events.append(e))
    monitor.checked_in("fixer")
    clock.now += 61

    monitor.tick()

    assert events == [("quiet", "fixer", "been silent for 1 minute")]
    assert not outbox
