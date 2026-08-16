"""The neural voice: Kokoro on ONNX, local, and the machinery for acquiring it.

The robot voice (tts_system) spawned a PowerShell process per utterance - a second of wind-up
before any sound, and a voice nobody wants to listen to. Kokoro synthesizes on the CPU faster
than it speaks and sounds like a person. Its model is a third of a gigabyte the repo doesn't
carry: `ensure_voice` fetches it into runtime/ once, and startup waits for it - the user's call:
better a launch that takes as long as it takes than a first reply in the robot's voice. The
robot voice remains only for a machine where the neural one genuinely can't be had.
"""

import urllib.request
from pathlib import Path

# Pinned release files, not "latest": the voice bin's layout must match what kokoro-onnx expects,
# and a silent upgrade would be a different voice one morning with no explanation.
_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"

# The user asked for a male voice; am_michael is the strongest male in the v1.0 voice pack.
# voice.txt beside the model overrides it (see `voice_choice`), so trying another is an edit,
# not a build.
DEFAULT_VOICE = "am_michael"
DEFAULT_SPEED = 1.0


def voice_choice(directory):
    """(voice, speed) from `voice.txt` beside the model - "am_fenrir 1.1" - or the defaults.

    A file, because taste in voices is personal and runtime/ is where personal things live; and
    forgiving, because a half-understood file must never cost the voice entirely."""
    path = Path(directory) / "voice.txt"
    try:
        words = path.read_text(encoding="utf-8").split()
    except OSError:
        return DEFAULT_VOICE, DEFAULT_SPEED
    voice = words[0] if words else DEFAULT_VOICE
    try:
        speed = float(words[1]) if len(words) > 1 else DEFAULT_SPEED
    except ValueError:
        speed = DEFAULT_SPEED
    return voice, speed


def _download(url, into):
    urllib.request.urlretrieve(url, into)  # noqa: S310 - a pinned https release URL


def ensure_voice(directory, *, fetch=_download, announce=lambda line: None):
    """The model files, fetched if they aren't already on disk. None when they can't be had.

    Fetched to a working name and renamed only once whole: a half-downloaded model that LOOKS
    present would fail every later launch, and nothing would say why."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = (directory / MODEL_FILE, directory / VOICES_FILE)
    for path in paths:
        if path.exists():
            continue
        announce(f"(downloading the voice - a one-time {path.name} fetch)")
        partial = path.with_suffix(path.suffix + ".part")
        try:
            fetch(_RELEASE + path.name, partial)
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            return None
    return paths


class KokoroEngine:
    """Synthesis: text in, (chunks, samplerate) out, in one configured voice.

    The model is opened on first use, not at construction - construction happens on the startup
    path, and loading ~300MB there would push the window's first paint behind it."""

    def __init__(self, model_path, voices_path, *, voice=DEFAULT_VOICE, speed=DEFAULT_SPEED,
                 kokoro_factory=None):
        self._paths = (str(model_path), str(voices_path))
        self._voice = voice
        self._speed = speed
        self._factory = kokoro_factory or _real_kokoro
        self._kokoro = None

    def say(self, text):
        """The line as ONE piece: synthesis here is local and faster than speech, so there is
        nothing to be gained by handing it over in installments the way a cloud voice must."""
        if self._kokoro is None:
            self._kokoro = self._factory(*self._paths)
        samples, samplerate = self._kokoro.create(text, voice=self._voice, speed=self._speed,
                                                  lang="en-us")
        return [samples], samplerate


def _real_kokoro(model_path, voices_path):
    # Imported here so the engine can be exercised - and the app can run on the robot voice -
    # without the kokoro package or its model on disk.
    from kokoro_onnx import Kokoro

    return Kokoro(model_path, voices_path)


