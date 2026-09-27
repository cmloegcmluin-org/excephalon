"""The real microphone: a 16 kHz mono float32 input stream, read in fixed-size frames.

Picking the RIGHT input device matters: Windows often defaults to a dead virtual mic (an idle
headset's silent input, "Sound Mapper", etc.), which hands back pure silence - so Excephalon hears
nothing and just sits there. `choose_input_device` avoids that by taking the liveliest real input,
or an explicit override. Hardware I/O only; the segmentation/transcription it feeds is tested
without a mic.

`BackgroundMicrophone` wraps a mic to drain it on its own thread, so the stream keeps being read
even while the main thread is stuck transcribing - the gap where PortAudio used to overflow and
silently drop whatever was said mid-transcription.
"""

import math
import queue
import threading
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
FRAME = 480  # 30 ms at 16 kHz
MAX_BUFFERED_FRAMES = 2000  # ~60 s; a cap on backlog that piles up between turns (drop the oldest)


def _turned_up(frame, gain):
    if gain == 1.0:
        return frame
    return np.clip(frame * gain, -1.0, 1.0).astype("float32")


class Microphone:
    def __init__(self, *, device=None, gain=1.0, samplerate=SAMPLE_RATE, blocksize=FRAME):
        self._blocksize = blocksize
        self._gain = gain  # boost a quiet mic so speech clears the speech threshold and transcribes cleanly
        self._stream = sd.InputStream(
            device=device, samplerate=samplerate, channels=1, dtype="float32", blocksize=blocksize
        )
        self._stream.start()

    def read(self):
        data, _ = self._stream.read(self._blocksize)
        return _turned_up(data[:, 0].copy(), self._gain)

    def frames(self):
        while True:
            yield self.read()

    def close(self):
        self._stream.stop()
        self._stream.close()


class BackgroundMicrophone:
    """Reads a frame source continuously on a background thread into a queue.

    The main loop reads the mic only between transcriptions; while Parakeet chews on a chunk (a
    second or more) nothing was draining PortAudio, so it overflowed and dropped whatever they said in
    that window. Here a dedicated thread keeps reading no matter what the main thread is doing, and
    `frames()` hands over the buffered audio. `flush()` throws away audio captured between turns (the
    Excephalon's own spoken reply, room noise) so it isn't replayed as their next turn.
    """

    def __init__(self, source, *, max_frames=MAX_BUFFERED_FRAMES):
        self._source = source
        self._queue = queue.Queue()
        self._max_frames = max_frames
        self._running = True
        self._exhausted = False  # the source ran out (only a finite/test source does; a real mic never)
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _capture(self):
        while self._running:
            try:
                frame = self._source.read()
            except Exception:
                self._exhausted = True  # source closed or ran dry - stop feeding the queue
                return
            self._queue.put(frame)
            while self._queue.qsize() > self._max_frames:
                try:
                    self._queue.get_nowait()  # drop the oldest; stale between-turn audio isn't worth keeping
                except queue.Empty:
                    break

    def flush(self):
        """Discard everything buffered so far, so the next frames() starts from now."""
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def frames(self):
        while self._running:
            try:
                yield self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._exhausted:
                    return  # a finite source ran dry; a real mic just keeps the loop waiting

    def close(self):
        self._running = False
        try:
            self._source.close()  # unblock a blocking read() so the thread notices we've stopped
        except Exception:
            pass
        self._thread.join(timeout=1.0)


class MicrophoneStopped(Exception):
    pass


class LiveMicrophone:
    def __init__(self, device=None, *, gain=1.0, samplerate=SAMPLE_RATE,
                 blocksize=SAMPLE_RATE // 10, open_stream=sd.InputStream, clock=time.time):
        self._gain = gain
        self._clock = clock
        self._heard = queue.Queue()
        self._stream = open_stream(device=device, samplerate=samplerate, channels=1,
                                   dtype="float32", blocksize=blocksize, callback=self._arrived)
        self._stream.start()

    def _arrived(self, block, frames, when, status):
        self._heard.put((self._clock(), _turned_up(block[:, 0].copy(), self._gain)))

    def blocks(self, stall_after):
        while True:
            try:
                yield self._heard.get(timeout=stall_after)
            except queue.Empty:
                raise MicrophoneStopped(f"no sound for {stall_after:g} seconds") from None

    def close(self):
        self._stream.stop()
        self._stream.close()


def choose_input_device(devices, probe, *, override=None, hostapi=None):
    """Pick an input device index from a `sd.query_devices()` list.

    Only devices on `hostapi` are considered (when given) - on Windows the same mic is listed under
    several host APIs and some (WDM-KS) can't be opened for blocking reads, so we stick to the API
    the OS default uses. With `override` (a device-name substring), take the first such input whose
    name contains it. Otherwise probe each distinct input's live level via `probe(index) -> rms` and
    take the LIVELIEST - a real mic's self-noise always beats a disconnected virtual device's ~0, so
    this reliably avoids a dead default (an idle headset, a virtual device) even in a silent room, where an
    absolute-threshold check would find nothing and fall back to that very dead default. Returns
    (index, name), or (None, None) only when there's no input device we could probe at all.
    """
    inputs = [
        (i, d)
        for i, d in enumerate(devices)
        if d.get("max_input_channels", 0) > 0 and (hostapi is None or d.get("hostapi") == hostapi)
    ]
    if override:
        want = override.strip().lower()
        for index, device in inputs:
            if want and want in device["name"].lower():
                return index, device["name"]
    # A phone offering itself as a mic (macOS lists an iPhone or iPad via Continuity) is not a
    # candidate: PROBING it is what makes the phone ring itself awake trying to serve - "that's
    # annoying and I won't ever want to do that" - so it is dropped before the probe, however
    # lively it would read. The override above runs first: asked for BY NAME, it still works.
    inputs = [(i, d) for i, d in inputs
              if not any(phone in d["name"].lower() for phone in ("iphone", "ipad"))]
    best_index, best_name, best_level = None, None, None
    seen = set()
    for index, device in inputs:
        if device["name"] in seen:
            continue  # same physical mic, different host API - probe it once
        seen.add(device["name"])
        try:
            level = probe(index)
        except Exception:
            continue
        if level is None or not math.isfinite(level):
            continue
        if best_level is None or level > best_level:
            best_index, best_name, best_level = index, device["name"], level
    return best_index, best_name


def probe_input_device(index, *, seconds=0.4):
    """Measure a device's live RMS by briefly recording from it (real hardware; the pure selection
    logic in `choose_input_device` takes this as an injected callable). Values are clipped to the
    valid audio range first, so a glitchy device returning garbage can't overflow or masquerade as
    the loudest mic."""
    info = sd.query_devices(index)
    samplerate = int(info["default_samplerate"])
    recording = sd.rec(
        int(seconds * samplerate), samplerate=samplerate, channels=1, dtype="float32", device=index
    )
    sd.wait()
    clean = np.clip(np.nan_to_num(recording[:, 0]), -1.0, 1.0)
    return float(np.sqrt(np.mean(clean**2)))


def named_microphone(mic_txt):
    return mic_txt.read_text(encoding="utf-8").strip() if mic_txt.exists() else None


def microphone_gain(mic_gain_txt):
    try:
        return float(mic_gain_txt.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 1.0


def pick_input_device(override=None):
    default_input = sd.default.device[0]
    hostapi = sd.query_devices(default_input)["hostapi"] if default_input is not None else None
    return choose_input_device(sd.query_devices(), probe_input_device, override=override,
                               hostapi=hostapi)


def open_live_microphone(mic_txt, mic_gain_txt):
    # PortAudio lists the devices once, as it starts, so only a restart sees a mic plugged in since.
    sd._terminate()
    sd._initialize()
    device, name = pick_input_device(named_microphone(mic_txt))
    microphone = LiveMicrophone(device, gain=microphone_gain(mic_gain_txt))
    return microphone, name or "the default microphone"
