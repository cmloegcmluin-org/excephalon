import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile

from excephalon.voice_log import SAMPLE_RATE, VoiceLog

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
