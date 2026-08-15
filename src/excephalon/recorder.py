"""Continuously write mic frames to a WAV on disk, so nothing the user says is ever lost.

Every frame is flushed to disk the instant it's captured. If the process crashes mid-turn (it
has, losing minutes of their ideas), the audio is already on disk - recoverable even if the WAV
header never got finalized (the raw 16-bit PCM sits right after the 44-byte header).

A WAV counts its bytes in a 32-bit field, so one file cannot hold more than 4 GiB - about 37
hours at this rate. Past that, `wave` raises on every write while patching the header, and since
the write happens inside the mic pump, the exception KILLED THE PUMP: 37 hours into one session
the ceiling was reached at 11:24 and Excephalon simply stopped hearing him, window still saying
it was recording. So the recording rolls into a fresh file well before the ceiling, and the pumps
that call this treat a recording failure as a lost safety net rather than a reason to go deaf.
"""

import wave
from pathlib import Path

import numpy as np

# Where one file gives way to the next. Under the format's own 4 GiB ceiling by a wide margin,
# because the header is patched on every write and the failure mode at the ceiling is silence.
DEFAULT_LIMIT = 3 * 1024**3


def record(recorder, frame):
    """Hand one frame to the recording, and carry on whatever it says.

    Called from inside the mic pumps, which is why it swallows: the recording is a safety net,
    not the ear, and an exception raised here killed the pump thread outright - Excephalon went
    deaf mid-session with its window still saying it was recording. A lost recording costs the
    net; a lost pump costs everything he says from then on."""
    if recorder is None:
        return
    try:
        recorder.write(frame)
    except Exception:
        pass


class AudioRecorder:
    def __init__(self, path, samplerate=16000, limit=DEFAULT_LIMIT):
        self._first = Path(path)
        self._first.parent.mkdir(parents=True, exist_ok=True)
        self._samplerate = samplerate
        self._limit = limit
        self._part = 1
        self._written = 0
        self._open(self._first)

    def _open(self, path):
        self.path = str(path)
        self._file = open(path, "wb")
        self._wav = wave.open(self._file, "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self._samplerate)

    def _roll(self):
        """Finish this file and start the next - `session.wav`, `session-2.wav`, and so on. The
        finished part keeps a valid header, so every part plays; `path` names the live one, since
        an aside pointing at a file that stopped hours ago sends the next diagnosis nowhere."""
        self.close()
        self._part += 1
        self._written = 0
        self._open(self._first.with_name(f"{self._first.stem}-{self._part}{self._first.suffix}"))

    def write(self, frame):
        pcm = np.clip(np.asarray(frame, dtype=np.float32), -1.0, 1.0)
        data = (pcm * 32767).astype("<i2").tobytes()
        if self._written and self._written + len(data) > self._limit:
            self._roll()
        self._wav.writeframes(data)
        self._written += len(data)
        self._file.flush()  # onto disk every frame - a crash then loses nothing

    def close(self):
        try:
            self._wav.close()  # finalizes the header; does not close the underlying file object
        finally:
            self._file.close()
