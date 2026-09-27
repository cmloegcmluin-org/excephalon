import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile

from excephalon.mic import MicrophoneStopped
from excephalon.voice_log import SAMPLE_RATE, Recorder, Status, VoiceLog

SOURCE = Path(__file__).resolve().parents[1] / "src"


def _at(*when):
    return datetime(*when).timestamp()


def _tone(seconds, hz=440):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _recordings(folder):
    return sorted(path.name for path in folder.iterdir())


def test_the_first_sound_starts_a_file_named_for_when_it_was_heard(tmp_path):
    log = VoiceLog(tmp_path)

    log.write(_tone(1.0), at=_at(2026, 9, 26, 16, 53, 13))
    log.close()

    assert _recordings(tmp_path) == ["2026-09-26 16-53-12.mp3"]


def test_two_days_of_recording_fit_in_under_a_gigabyte(tmp_path):
    log = VoiceLog(tmp_path)

    log.write(_tone(10.0), at=_at(2026, 9, 26, 16, 0, 10))
    log.close()

    [recording] = tmp_path.iterdir()
    assert recording.stat().st_size / 10.0 * 2 * 24 * 3600 < 1e9


def test_a_recording_cut_off_by_a_crash_still_plays_to_where_it_stopped(tmp_path):
    crash_after_thirty_seconds = f"""
import os, sys
import numpy as np
sys.path.insert(0, {str(SOURCE)!r})
from excephalon.voice_log import SAMPLE_RATE, VoiceLog
log = VoiceLog({str(tmp_path)!r})
noise = np.random.default_rng(1)
for second in range(30):
    loud = 0.05 if second % 10 >= 5 else 0.002
    log.write(noise.normal(0, loud, SAMPLE_RATE).astype(np.float32), at=1_790_000_000 + second)
os._exit(1)
"""
    subprocess.run([sys.executable, "-c", crash_after_thirty_seconds], check=False, timeout=60)

    [recording] = tmp_path.iterdir()
    heard, rate = soundfile.read(recording)
    assert len(heard) / rate > 29.5


def test_it_keeps_enough_detail_for_speech_to_stay_clear(tmp_path):
    log = VoiceLog(tmp_path)

    log.write(_tone(10.0), at=_at(2026, 9, 26, 16, 0, 10))
    log.close()

    [recording] = tmp_path.iterdir()
    kilobits_per_second = recording.stat().st_size * 8 / 10.0 / 1000
    assert kilobits_per_second >= 24


def test_each_hour_starts_a_file_of_its_own(tmp_path):
    log = VoiceLog(tmp_path)

    log.write(_tone(0.1), at=_at(2026, 9, 26, 16, 59, 59, 950_000))
    log.write(_tone(0.1), at=_at(2026, 9, 26, 17, 0, 0, 50_000))
    log.write(_tone(0.1), at=_at(2026, 9, 26, 17, 0, 0, 150_000))
    log.close()

    assert _recordings(tmp_path) == ["2026-09-26 16-59-59.mp3", "2026-09-26 17-00-00.mp3"]


def test_a_gap_in_the_sound_starts_a_new_file_so_each_name_still_tells_the_time(tmp_path):
    log = VoiceLog(tmp_path)

    log.write(_tone(1.0), at=_at(2026, 9, 26, 16, 10, 1))
    log.write(_tone(1.0), at=_at(2026, 9, 26, 16, 10, 2))
    log.write(_tone(1.0), at=_at(2026, 9, 26, 16, 25, 31))
    log.close()

    assert _recordings(tmp_path) == ["2026-09-26 16-10-00.mp3", "2026-09-26 16-25-30.mp3"]


def _left_behind(folder, name, hours_before):
    recording = folder / name
    recording.write_bytes(b"\xff\xf3")
    os.utime(recording, (hours_before, hours_before))
    return recording


def test_recordings_older_than_two_days_are_deleted(tmp_path):
    now = _at(2026, 9, 26, 17, 0, 1)
    _left_behind(tmp_path, "2026-09-24 16-00-00.mp3", now - 49 * 3600)
    _left_behind(tmp_path, "2026-09-24 18-00-00.mp3", now - 47 * 3600)
    log = VoiceLog(tmp_path)

    log.write(_tone(1.0), at=now)
    log.close()

    assert _recordings(tmp_path) == ["2026-09-24 18-00-00.mp3", "2026-09-26 17-00-00.mp3"]


def test_nothing_but_its_own_recordings_is_ever_deleted(tmp_path):
    now = _at(2026, 9, 26, 17, 0, 1)
    _left_behind(tmp_path, "the idea I wanted to keep.mp3", now - 90 * 24 * 3600)
    _left_behind(tmp_path, "notes.txt", now - 90 * 24 * 3600)
    (tmp_path / "saved").mkdir()
    log = VoiceLog(tmp_path)

    log.write(_tone(1.0), at=now)
    log.close()

    assert _recordings(tmp_path) == ["2026-09-26 17-00-00.mp3", "notes.txt", "saved",
                                     "the idea I wanted to keep.mp3"]


def test_a_recording_is_never_written_over(tmp_path):
    first = VoiceLog(tmp_path)
    first.write(_tone(1.0), at=_at(2026, 9, 26, 17, 0, 1))
    first.close()
    again = VoiceLog(tmp_path)

    again.write(_tone(1.0), at=_at(2026, 9, 26, 17, 0, 1, 500_000))
    again.close()

    assert _recordings(tmp_path) == ["2026-09-26 17-00-00 (2).mp3", "2026-09-26 17-00-00.mp3"]


def test_a_second_recording_from_the_same_second_is_deleted_on_time_too(tmp_path):
    now = _at(2026, 9, 26, 17, 0, 1)
    _left_behind(tmp_path, "2026-09-24 16-00-00 (2).mp3", now - 49 * 3600)
    log = VoiceLog(tmp_path)

    log.write(_tone(1.0), at=now)
    log.close()

    assert _recordings(tmp_path) == ["2026-09-26 17-00-00.mp3"]


def test_the_folder_is_made_the_first_time_it_is_needed(tmp_path):
    log = VoiceLog(tmp_path / "runtime" / "voice-log")

    log.write(_tone(1.0), at=_at(2026, 9, 26, 17, 0, 1))
    log.close()

    assert _recordings(tmp_path / "runtime" / "voice-log") == ["2026-09-26 17-00-00.mp3"]


def test_after_a_failed_save_the_next_sound_goes_to_a_fresh_file(tmp_path, monkeypatch):
    log = VoiceLog(tmp_path)
    log.write(_tone(1.0), at=_at(2026, 9, 26, 17, 0, 1))
    saves = soundfile.SoundFile.write

    def disk_full(recording, block):
        monkeypatch.setattr(soundfile.SoundFile, "write", saves)
        raise OSError("No space left on device")

    monkeypatch.setattr(soundfile.SoundFile, "write", disk_full)
    with pytest.raises(OSError):
        log.write(_tone(1.0), at=_at(2026, 9, 26, 17, 0, 2))
    log.write(_tone(1.0), at=_at(2026, 9, 26, 17, 0, 3))
    log.close()

    assert _recordings(tmp_path) == ["2026-09-26 17-00-00.mp3", "2026-09-26 17-00-02.mp3"]


class SavedBlocks:
    def __init__(self):
        self.blocks = []
        self.closed = False

    def write(self, block, at):
        self.blocks.append(block)

    def close(self):
        self.closed = True


class Hearing:
    def __init__(self, *blocks):
        self._blocks = list(blocks)
        self.let_go = threading.Event()

    def blocks(self, stall_after):
        for at, block in enumerate(self._blocks):
            yield float(at), block
        if not self.let_go.wait(stall_after):
            raise MicrophoneStopped(f"no sound for {stall_after:g} seconds")

    def close(self):
        self.let_go.set()


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_what_the_microphone_hears_is_saved(tmp_path):
    log = SavedBlocks()
    recorder = Recorder(log, lambda: (Hearing("hello", "there"), "Brio 101"))

    recorder.start()
    assert _wait_for(lambda: log.blocks == ["hello", "there"])
    recorder.stop(because="the test is over")


def _one_after_another(*microphones):
    waiting = iter(microphones)
    return lambda: (next(waiting, None) or Hearing(), "Brio 101")


def test_a_microphone_that_stops_sending_sound_is_let_go_and_opened_again():
    log = SavedBlocks()
    first, second = Hearing("before"), Hearing("after")
    recorder = Recorder(log, _one_after_another(first, second), stall_after=0.01)

    recorder.start()
    assert _wait_for(lambda: log.blocks[:2] == ["before", "after"])
    recorder.stop(because="the test is over")

    assert first.let_go.is_set()


def test_while_no_microphone_can_be_opened_it_keeps_trying():
    log = SavedBlocks()
    tries = []

    def open_microphone():
        tries.append("try")
        if len(tries) < 3:
            raise OSError("Error opening InputStream: Invalid device [PaErrorCode -9996]")
        return Hearing("found it"), "Brio 101"

    recorder = Recorder(log, open_microphone, retry_after=0.01)

    recorder.start()
    assert _wait_for(lambda: log.blocks == ["found it"])
    recorder.stop(because="the test is over")


class FullDisk(SavedBlocks):
    def write(self, block, at):
        if block == "lost":
            raise OSError("No space left on device")
        super().write(block, at)


def test_a_block_that_cannot_be_saved_does_not_stop_the_recording():
    log = FullDisk()
    recorder = Recorder(log, _one_after_another(Hearing("lost", "kept")))

    recorder.start()
    assert _wait_for(lambda: log.blocks == ["kept"])
    recorder.stop(because="the test is over")


def test_stopping_finishes_the_file_at_once_even_while_the_microphone_is_silent():
    log = SavedBlocks()
    recorder = Recorder(log, _one_after_another(Hearing("last words")), stall_after=60)
    recorder.start()
    assert _wait_for(lambda: log.blocks == ["last words"])
    asked = time.monotonic()

    recorder.stop(because="the test is over")

    assert log.closed and time.monotonic() - asked < 0.5


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_while_sound_is_being_saved_it_says_so_and_names_the_microphone():
    log = SavedBlocks()
    recorder = Recorder(log, lambda: (Hearing("hello"), "Brio 101"), clock=Clock())

    recorder.start()
    assert _wait_for(lambda: log.blocks == ["hello"])

    assert recorder.status() == Status(recording=True, says="Recording from Brio 101")
    recorder.stop(because="the test is over")


def test_once_sound_stops_being_saved_it_says_it_is_not_recording():
    clock, log = Clock(), SavedBlocks()
    recorder = Recorder(log, _one_after_another(Hearing("hello")), clock=clock, stall_after=60)
    recorder.start()
    assert _wait_for(lambda: log.blocks == ["hello"])

    clock.now += 3.1

    assert recorder.status() == Status(recording=False,
                                       says="Not recording: no sound is coming from Brio 101")
    recorder.stop(because="the test is over")


def test_when_no_microphone_can_be_opened_it_says_so():
    def no_microphone():
        raise OSError("Error opening InputStream: Invalid device [PaErrorCode -9996]")

    recorder = Recorder(SavedBlocks(), no_microphone, retry_after=60)

    recorder.start()
    assert _wait_for(lambda: recorder.status() == Status(
        recording=False, says="Not recording: no microphone found"))
    recorder.stop(because="the test is over")


class NoRoom(SavedBlocks):
    def write(self, block, at):
        raise OSError("No space left on device")


def test_when_sound_cannot_be_saved_it_says_so():
    recorder = Recorder(NoRoom(), _one_after_another(Hearing("lost")), stall_after=60)

    recorder.start()
    assert _wait_for(lambda: recorder.status() == Status(
        recording=False, says="Not recording: can't save the recordings"))
    recorder.stop(because="the test is over")


def test_before_the_microphone_is_open_it_says_it_is_opening_it():
    recorder = Recorder(SavedBlocks(), _one_after_another())

    assert recorder.status() == Status(recording=False,
                                       says="Not recording yet: opening the microphone")


def test_which_microphone_it_listens_through_and_why_it_let_go_are_written_down(caplog):
    caplog.set_level(logging.INFO, logger="excephalon.voice_log")
    recorder = Recorder(SavedBlocks(), _one_after_another(Hearing("hello")), stall_after=0.01)

    recorder.start()
    assert _wait_for(lambda: "no sound for 0.01 seconds" in caplog.text)
    recorder.stop(because="the test is over")

    assert "Listening through Brio 101" in caplog.text
    assert "Let go of Brio 101: no sound for 0.01 seconds" in caplog.text


def test_why_no_microphone_could_be_opened_is_written_down_once_not_every_try(caplog):
    caplog.set_level(logging.INFO, logger="excephalon.voice_log")
    tries = []

    def no_microphone():
        tries.append("try")
        raise OSError("Invalid device [PaErrorCode -9996]")

    recorder = Recorder(SavedBlocks(), no_microphone, retry_after=0.001)

    recorder.start()
    assert _wait_for(lambda: len(tries) >= 5)
    recorder.stop(because="the test is over")

    assert caplog.text.count("No microphone: Invalid device [PaErrorCode -9996]") == 1


def test_why_the_recording_stopped_is_written_down(caplog):
    caplog.set_level(logging.INFO, logger="excephalon.voice_log")
    recorder = Recorder(SavedBlocks(), _one_after_another())

    recorder.stop(because="Windows is shutting down")

    assert "Stopped recording: Windows is shutting down" in caplog.text


class Broken(Hearing):
    def blocks(self, stall_after):
        yield 0.0, "before it broke"
        raise RuntimeError("Unanticipated host error [PaErrorCode -9999]")


def test_a_microphone_that_breaks_any_other_way_is_opened_again_too():
    log = SavedBlocks()
    recorder = Recorder(log, _one_after_another(Broken(), Hearing("after")), stall_after=0.01)

    recorder.start()
    assert _wait_for(lambda: log.blocks[:2] == ["before it broke", "after"])
    recorder.stop(because="the test is over")


class Unplugged(Hearing):
    def close(self):
        super().close()
        raise OSError("Error closing stream: Device unavailable [PaErrorCode -9985]")


def test_a_microphone_that_cannot_even_be_closed_does_not_stop_the_recording():
    log = SavedBlocks()
    recorder = Recorder(log, _one_after_another(Unplugged("before"), Hearing("after")),
                        stall_after=0.01)

    recorder.start()
    assert _wait_for(lambda: log.blocks[:2] == ["before", "after"])
    recorder.stop(because="the test is over")
