import logging
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import soundfile

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
NAME_FORMAT = "%Y-%m-%d %H-%M-%S"
# libsndfile maps its 0-1 compression level onto LAME's bitrate table; at 16 kHz, 0.85 is 32 kbps.
THIRTY_TWO_KBPS = 0.85
LONGEST_UNBROKEN_GAP = 2.0
STILL_RECORDING_WITHIN = 3.0
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


class Status(NamedTuple):
    recording: bool
    says: str


class Recorder:
    def __init__(self, log, open_microphone, *, stall_after=5.0, retry_after=5.0,
                 clock=time.monotonic):
        self._log = log
        self._open_microphone = open_microphone
        self._stall_after = stall_after
        self._retry_after = retry_after
        self._clock = clock
        self._microphone = None
        self._saved_at = None
        self._trouble = None
        self._stopping = threading.Event()
        self._saving = threading.Lock()
        self._thread = threading.Thread(target=self._keep_recording, name="voice-log", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self, because):
        self._stopping.set()
        with self._saving:
            self._log.close()
        logger.info("Stopped recording: %s", because)

    def status(self):
        if self._saved_at is not None and self._clock() - self._saved_at <= STILL_RECORDING_WITHIN:
            return Status(recording=True, says=f"Recording from {self._microphone}")
        if self._trouble:
            return Status(recording=False, says=f"Not recording: {self._trouble}")
        if self._microphone is None:
            return Status(recording=False, says="Not recording yet: opening the microphone")
        return Status(recording=False,
                      says=f"Not recording: no sound is coming from {self._microphone}")

    def _keep_recording(self):
        while not self._stopping.is_set():
            try:
                microphone, self._microphone = self._open_microphone()
            except Exception as missing:
                self._in_trouble("no microphone found", "No microphone", missing)
                self._stopping.wait(self._retry_after)
                continue
            self._trouble = None
            logger.info("Listening through %s", self._microphone)
            try:
                self._save_what_it_hears(microphone)
            except Exception as stopped:
                logger.warning("Let go of %s: %s", self._microphone, stopped)
            finally:
                self._let_go_of(microphone)

    def _save_what_it_hears(self, microphone):
        for at, block in microphone.blocks(self._stall_after):
            with self._saving:
                if self._stopping.is_set():
                    return
                try:
                    self._log.write(block, at)
                except Exception as unsaved:
                    self._in_trouble("can't save the recordings", "Can't save", unsaved)
                    continue
                self._saved_at = self._clock()
                self._trouble = None

    def _let_go_of(self, microphone):
        try:
            microphone.close()
        except Exception as stuck:
            logger.warning("Could not close %s: %s", self._microphone, stuck)

    def _in_trouble(self, trouble, written_down_as, error):
        if trouble != self._trouble:
            logger.warning("%s: %s", written_down_as, error)
        self._trouble = trouble
