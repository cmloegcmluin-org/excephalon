import logging
import threading
import time
from pathlib import Path

import numpy as np
import pystray
import pytest

from excephalon.voice_log import Status
from excephalon.voice_log_icon import draw_icon
from excephalon.voice_log_tray import (WAIT_ABANDONED, WAIT_TIMEOUT, WM_ENDSESSION, WM_QUERYENDSESSION,
                                      StillRunning, VoiceLogTray, Win32Kernel, take_over,
                                      write_down_to)


class StubRecorder:
    def __init__(self, status):
        self.now = status
        self.stopped_because = None

    def status(self):
        return self.now

    def stop(self, because):
        self.stopped_because = because


class HeldIcon:
    def __init__(self, name, icon, title, menu):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu
        self._message_handlers = {}
        self.menu_updates = 0
        self.taken_down = False

    def update_menu(self):
        self.menu_updates += 1

    def stop(self):
        self.taken_down = True


def _shows(icon, picture):
    return np.array_equal(np.asarray(icon.icon), picture)


def _tray(tmp_path, status=Status(recording=True, says="Recording from Brio 101"), opened=None):
    recorder = StubRecorder(status)
    tray = VoiceLogTray(recorder, tmp_path / "voice-log", make_icon=HeldIcon,
                        open_folder=(opened if opened is not None else []).append)
    return tray, recorder


def test_the_menu_says_what_it_is_doing_then_offers_the_recordings_and_quit(tmp_path):
    tray, _ = _tray(tmp_path)

    doing, recordings, leave = tray.icon.menu.items

    assert (doing.text, doing.enabled) == ("Recording from Brio 101", False)
    assert (recordings.text, recordings.default) == ("Open recordings", True)
    assert leave.text == "Quit"


def test_open_recordings_opens_the_folder_even_before_the_first_recording_is_in_it(tmp_path):
    opened = []
    tray, _ = _tray(tmp_path, opened=opened)
    _, recordings, _ = tray.icon.menu.items

    recordings(tray.icon)

    assert opened == [tmp_path / "voice-log"] and (tmp_path / "voice-log").is_dir()


def test_quit_finishes_the_recording_and_takes_the_icon_down(tmp_path):
    tray, recorder = _tray(tmp_path)
    _, _, leave = tray.icon.menu.items

    leave(tray.icon)

    assert recorder.stopped_because == "Quit from the tray"
    assert tray.icon.taken_down


def test_the_icon_and_its_tooltip_follow_what_the_recorder_is_doing(tmp_path):
    tray, recorder = _tray(tmp_path, status=Status(recording=False,
                                                   says="Not recording yet: opening the microphone"))
    tray.refresh()
    assert tray.icon.title == "Voice Log\nNot recording yet: opening the microphone"
    assert _shows(tray.icon, draw_icon(64, recording=False))

    recorder.now = Status(recording=True, says="Recording from Brio 101")
    tray.refresh()

    assert tray.icon.title == "Voice Log\nRecording from Brio 101"
    assert _shows(tray.icon, draw_icon(64, recording=True))
    assert tray.icon.menu_updates == 2


def test_nothing_is_redrawn_while_nothing_changes(tmp_path):
    tray, _ = _tray(tmp_path)
    tray.refresh()

    tray.refresh()

    assert tray.icon.menu_updates == 1


def test_windows_asking_whether_it_may_log_off_or_shut_down_is_told_yes(tmp_path):
    tray, recorder = _tray(tmp_path)

    assert tray.icon._message_handlers[WM_QUERYENDSESSION](0, 0) == 1
    assert recorder.stopped_because is None


def test_when_windows_ends_the_session_the_recording_is_finished_first(tmp_path):
    tray, recorder = _tray(tmp_path)

    tray.icon._message_handlers[WM_ENDSESSION](1, 0)

    assert recorder.stopped_because == "Windows is ending the session"


def test_a_shutdown_that_is_called_off_leaves_it_recording(tmp_path):
    tray, recorder = _tray(tmp_path)

    tray.icon._message_handlers[WM_ENDSESSION](0, 0)

    assert recorder.stopped_because is None


def test_the_tray_library_still_routes_windows_messages_through_the_table_filled_in_here():
    windows_tray = (Path(pystray.__file__).parent / "_win32.py").read_text(encoding="utf-8")

    assert "self._message_handlers = {" in windows_tray
    assert "icon._message_handlers.get(" in windows_tray


class SharedKernel:
    def __init__(self):
        self._objects = {}
        self._guard = threading.Lock()

    def _named(self, name, make):
        with self._guard:
            return self._objects.setdefault(name, make())

    def mutex(self, name):
        return self._named(name, threading.Lock)

    def event(self, name):
        return self._named(name, threading.Event)

    def signal(self, event):
        event.set()

    def clear(self, event):
        event.clear()

    def wait(self, waitable, seconds):
        if isinstance(waitable, threading.Event):
            return waitable.wait(seconds)
        return waitable.acquire(timeout=-1 if seconds is None else seconds)


def test_a_new_launch_takes_over_from_the_copy_already_running():
    kernel = SharedKernel()
    first_told = threading.Event()
    first_holds = take_over(on_newer=first_told.set, kernel=kernel)
    second = threading.Thread(target=take_over, kwargs=dict(on_newer=lambda: None, kernel=kernel))

    second.start()
    assert first_told.wait(2.0)
    first_holds.release()
    second.join(2.0)

    assert not second.is_alive()


def test_a_launch_gives_up_when_the_running_copy_will_not_step_aside():
    kernel = SharedKernel()
    take_over(on_newer=lambda: None, kernel=kernel)

    with pytest.raises(StillRunning):
        take_over(on_newer=lambda: None, kernel=kernel, patience=0.05)


def test_a_launch_is_never_told_to_make_way_for_itself():
    told = threading.Event()

    take_over(on_newer=told.set, kernel=SharedKernel())

    assert not told.wait(0.2)


class Kernel32Calls:
    def __init__(self, waits_answer):
        self.calls = []
        self._waits_answer = waits_answer

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, *args))
            return self._waits_answer if name == "WaitForSingleObject" else f"handle:{args[-1]}"
        return call


def test_windows_is_asked_for_the_named_objects_and_waited_on_in_milliseconds():
    kernel32 = Kernel32Calls(waits_answer=WAIT_ABANDONED)
    kernel = Win32Kernel(kernel32)

    mutex, event = kernel.mutex("one-voice-log"), kernel.event("one-voice-log.Newer")
    kernel.signal(event)
    kernel.clear(event)
    took_it = kernel.wait(mutex, 1.5)
    kernel.wait(event, None)

    assert took_it
    assert kernel32.calls == [
        ("CreateMutexW", None, False, "one-voice-log"),
        ("CreateEventW", None, True, False, "one-voice-log.Newer"),
        ("SetEvent", "handle:one-voice-log.Newer"),
        ("ResetEvent", "handle:one-voice-log.Newer"),
        ("WaitForSingleObject", "handle:one-voice-log", 1500),
        ("WaitForSingleObject", "handle:one-voice-log.Newer", 0xFFFFFFFF),
    ]


def test_a_wait_that_runs_out_is_not_taken_for_the_object():
    kernel = Win32Kernel(Kernel32Calls(waits_answer=WAIT_TIMEOUT))

    assert not kernel.wait(kernel.mutex("one-voice-log"), 0.05)


def test_once_shown_the_icon_keeps_following_the_recorder_until_it_leaves(tmp_path):
    tray, recorder = _tray(tmp_path, status=Status(recording=False, says="Not recording yet: opening the microphone"))
    showing = threading.Thread(target=tray.show, kwargs=dict(every=0.01))

    showing.start()
    recorder.now = Status(recording=True, says="Recording from Brio 101")
    assert _wait_for(lambda: tray.icon.title == "Voice Log\nRecording from Brio 101")
    tray.leave(because="the test is over")
    showing.join(1.0)

    assert tray.icon.visible and not showing.is_alive()


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_what_happens_goes_into_the_voice_log_s_own_log_file(tmp_path):
    log_file = tmp_path / "logs" / "voice-log.log"
    handler = write_down_to(log_file)
    try:
        logging.getLogger("excephalon.voice_log").info("Listening through Brio 101")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()

    assert "INFO Listening through Brio 101" in log_file.read_text(encoding="utf-8")


def test_a_thread_that_dies_is_written_down_with_what_killed_it(tmp_path, monkeypatch):
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    log_file = tmp_path / "logs" / "voice-log.log"
    handler = write_down_to(log_file)
    try:
        dying = threading.Thread(target=lambda: 1 / 0, name="voice-log")
        dying.start()
        dying.join()
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()

    written = log_file.read_text(encoding="utf-8")
    assert "The voice-log thread died" in written and "ZeroDivisionError" in written


def test_a_tray_asked_to_leave_before_it_was_even_shown_takes_its_icon_down_once_shown(tmp_path):
    tray, _ = _tray(tmp_path)
    tray.leave(because="a newer Voice Log started")
    tray.icon.taken_down = False

    tray.show(every=0.01)

    assert tray.icon.taken_down


class IconElsewhere(HeldIcon):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self._message_handlers


def test_on_a_desk_whose_tray_has_no_windows_messages_it_still_comes_up(tmp_path):
    tray = VoiceLogTray(StubRecorder(Status(recording=True, says="Recording from Brio 101")),
                        tmp_path, make_icon=IconElsewhere, open_folder=[].append)

    assert tray.icon.title == "Voice Log"


def test_the_tooltip_never_runs_past_what_windows_can_show(tmp_path):
    tray, recorder = _tray(tmp_path)
    recorder.now = Status(recording=True, says="Recording from " + "a very long microphone name " * 9)

    tray.refresh()

    assert len(tray.icon.title) <= 127 and tray.icon.title.startswith("Voice Log\nRecording from")
