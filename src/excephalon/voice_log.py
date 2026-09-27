import re
from datetime import datetime, timedelta
from pathlib import Path

import soundfile

SAMPLE_RATE = 16_000
NAME_FORMAT = "%Y-%m-%d %H-%M-%S"
# libsndfile maps its 0-1 compression level onto LAME's bitrate table; at 16 kHz, 0.85 is 32 kbps.
THIRTY_TWO_KBPS = 0.85
LONGEST_UNBROKEN_GAP = 2.0
KEPT_FOR = timedelta(days=2)
A_RECORDING = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}( \(\d+\))?\.mp3")


def _hour_of(moment):
    return datetime.fromtimestamp(moment).replace(minute=0, second=0, microsecond=0)


class VoiceLog:
    def __init__(self, folder):
        self._folder = Path(folder)
        self._recording = None
        self._hour = None
        self._heard_until = None

    def write(self, block, at):
        start = at - len(block) / SAMPLE_RATE
        if not self._carries_on_from(start):
            self.close()
            self._begin(start)
        try:
            self._recording.write(block)
        except Exception:
            self._abandon()
            raise
        self._heard_until = at

    def close(self):
        if self._recording is not None:
            self._recording.close()
            self._recording = None

    def _abandon(self):
        try:
            self.close()
        except Exception:
            self._recording = None

    def _carries_on_from(self, start):
        return (self._recording is not None
                and _hour_of(start) == self._hour
                and start - self._heard_until <= LONGEST_UNBROKEN_GAP)

    def _begin(self, start):
        self._folder.mkdir(parents=True, exist_ok=True)
        self._forget_older_than(start - KEPT_FOR.total_seconds())
        self._recording = soundfile.SoundFile(
            self._unused_name(datetime.fromtimestamp(start)), "w", samplerate=SAMPLE_RATE,
            channels=1, format="MP3", subtype="MPEG_LAYER_III",
            bitrate_mode="CONSTANT", compression_level=THIRTY_TWO_KBPS)
        self._hour = _hour_of(start)

    def _unused_name(self, began):
        path = self._folder / f"{began:{NAME_FORMAT}}.mp3"
        copy = 2
        while path.exists():
            path = self._folder / f"{began:{NAME_FORMAT}} ({copy}).mp3"
            copy += 1
        return path

    def _forget_older_than(self, moment):
        for path in self._folder.iterdir():
            if A_RECORDING.fullmatch(path.name) and path.stat().st_mtime < moment:
                path.unlink()
