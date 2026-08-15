import wave

import numpy as np

from excephalon.recorder import AudioRecorder


def test_recorder_writes_a_readable_wav(tmp_path):
    path = tmp_path / "sub" / "session.wav"  # nested dir is created
    recorder = AudioRecorder(path, samplerate=16000)

    loud = np.full(480, 0.5, dtype=np.float32)
    recorder.write(loud)
    recorder.write(np.zeros(480, dtype=np.float32))
    recorder.close()

    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert len(samples) == 960
    assert abs(int(samples[0]) - round(0.5 * 32767)) <= 1  # first frame preserved


def test_a_long_session_rolls_into_a_new_file_instead_of_going_deaf(tmp_path):
    # A WAV counts its bytes in a 32-bit field, so past 4 GiB the header can no longer be written
    # and `wave` raises struct.error on EVERY write - which killed the mic pump outright: 37 hours
    # into a session the recorder hit the ceiling at 11:24 and Excephalon stopped hearing him, with
    # the window still saying it was recording. The recording rolls to a fresh file instead.
    path = tmp_path / "session.wav"
    recorder = AudioRecorder(path, samplerate=16000, limit=2000)  # bytes, so the roll is testable

    frame = np.full(480, 0.25, dtype=np.float32)  # 960 bytes of PCM
    for _ in range(5):  # 4800 bytes: past the limit twice
        recorder.write(frame)
    recorder.close()

    assert path.exists() and (tmp_path / "session-2.wav").exists()
    kept = 0
    for part in (path, tmp_path / "session-2.wav", tmp_path / "session-3.wav"):
        if not part.exists():
            continue
        with wave.open(str(part), "rb") as w:  # every part is a finished, readable WAV
            kept += w.getnframes()
    assert kept == 5 * 480  # not one frame of him was dropped in the roll


def test_the_recorder_says_which_file_it_is_writing_now(tmp_path):
    # The startup aside names the file; after a roll that name is stale, and the next diagnosis
    # would go looking in a file that stopped hours before the thing being diagnosed.
    path = tmp_path / "session.wav"
    recorder = AudioRecorder(path, limit=2000)

    for _ in range(5):
        recorder.write(np.full(480, 0.25, dtype=np.float32))

    assert recorder.path.endswith("session-3.wav")
    recorder.close()


def test_data_is_on_disk_before_close(tmp_path):
    # the whole point: a crash before close() must not lose what was already spoken
    path = tmp_path / "session.wav"
    recorder = AudioRecorder(path)

    recorder.write(np.full(480, 0.3, dtype=np.float32))
    size_after_one_frame = path.stat().st_size  # flushed, so already sized on disk

    assert size_after_one_frame >= 480 * 2  # at least the frame's PCM bytes are already written
    recorder.close()
