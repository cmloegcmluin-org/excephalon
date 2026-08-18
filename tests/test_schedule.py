import threading
from datetime import datetime

from excephalon.schedule import Schedule, resolve_when


def test_relative_minutes_add_to_now():
    now = datetime(2026, 8, 17, 16, 30)
    assert resolve_when("in 10 minutes", now) == datetime(2026, 8, 17, 16, 40)


def test_relative_variants_all_land_on_the_same_offset():
    now = datetime(2026, 8, 17, 16, 30)
    assert resolve_when("in 2 hours", now) == datetime(2026, 8, 17, 18, 30)
    assert resolve_when("+2h", now) == datetime(2026, 8, 17, 18, 30)
    assert resolve_when("in an hour", now) == datetime(2026, 8, 17, 17, 30)
    assert resolve_when("in 30 seconds", now) == datetime(2026, 8, 17, 16, 30, 30)


def test_clock_time_still_ahead_today_is_today():
    now = datetime(2026, 8, 17, 16, 30)
    assert resolve_when("17:15", now) == datetime(2026, 8, 17, 17, 15)


def test_clock_time_already_passed_rolls_to_tomorrow():
    now = datetime(2026, 8, 17, 16, 30)
    assert resolve_when("05:15", now) == datetime(2026, 8, 18, 5, 15)


def test_am_pm_clock_times():
    now = datetime(2026, 8, 17, 16, 30)
    assert resolve_when("5:15pm", now) == datetime(2026, 8, 17, 17, 15)
    assert resolve_when("5 pm", now) == datetime(2026, 8, 17, 17, 0)
    assert resolve_when("12am", now) == datetime(2026, 8, 18, 0, 0)
    assert resolve_when("12pm", now) == datetime(2026, 8, 18, 12, 0)


def test_absolute_datetime_in_the_future():
    now = datetime(2026, 8, 17, 16, 30)
    assert resolve_when("2026-08-18 09:00", now) == datetime(2026, 8, 18, 9, 0)
    assert resolve_when("2026-08-18T09:00", now) == datetime(2026, 8, 18, 9, 0)


def test_absolute_datetime_in_the_past_is_rejected():
    # A dated moment that has already gone by is a mistake, not a next-occurrence to roll forward.
    now = datetime(2026, 8, 17, 16, 30)
    assert resolve_when("2026-08-16 09:00", now) is None


def test_a_leading_at_or_on_is_ignored():
    now = datetime(2026, 8, 17, 16, 30)
    assert resolve_when("at 17:15", now) == datetime(2026, 8, 17, 17, 15)
    assert resolve_when("on 2026-08-18 09:00", now) == datetime(2026, 8, 18, 9, 0)


def test_unparseable_times_return_none():
    now = datetime(2026, 8, 17, 16, 30)
    for junk in ("", "sometime later", "banana", "at 5", "17:99"):
        assert resolve_when(junk, now) is None


def test_add_then_due_returns_and_removes_ready_items(tmp_path):
    sched = Schedule(tmp_path / "schedule.json", deliver=lambda m: None)
    sched.add(100.0, "dinner")
    sched.add(200.0, "later")

    assert sched.due(now=150.0) == [{"at": 100.0, "message": "dinner"}]
    # taken once, it is gone; the future one stays until its own time
    assert sched.due(now=150.0) == []
    assert sched.due(now=250.0) == [{"at": 200.0, "message": "later"}]


def test_poll_once_delivers_each_due_message_once(tmp_path):
    delivered = []
    sched = Schedule(tmp_path / "s.json", deliver=delivered.append, clock=lambda: 150.0)
    sched.add(100.0, "first")
    sched.add(120.0, "second")
    sched.add(200.0, "future")

    sched.poll_once()
    assert delivered == ["first", "second"]  # both past ones, earliest first

    sched.poll_once()
    assert delivered == ["first", "second"]  # the future one hasn't come due, nothing re-fires


def test_pending_messages_survive_a_restart(tmp_path):
    # The whole point: a message set before the app closed must still be there when it reopens.
    path = tmp_path / "s.json"
    Schedule(path, deliver=lambda m: None).add(500.0, "remember me")

    revived = Schedule(path, deliver=lambda m: None)
    assert revived.due(now=600.0) == [{"at": 500.0, "message": "remember me"}]


def test_run_polls_on_each_beat_until_stopped(tmp_path):
    delivered = []
    now = {"t": 100.0}
    sched = Schedule(tmp_path / "s.json", deliver=delivered.append, clock=lambda: now["t"])
    sched.add(150.0, "ping")
    stop = threading.Event()
    beats = {"n": 0}

    def fake_sleep(_seconds):
        beats["n"] += 1
        now["t"] = 200.0            # time moves on, so the message comes due on the next poll
        if beats["n"] >= 2:
            stop.set()

    sched.run(stop=stop, every=0.0, sleep=fake_sleep)

    assert delivered == ["ping"]
