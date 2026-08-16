"""The cloud voice: ElevenLabs, streamed, with the local voice standing behind it.

Kokoro sounds like a person; this sounds like a particular person, which is the whole of the
difference the user asked for. What it costs is that the sound now comes over a network, and a
network is a thing that fails - so nothing here is allowed to be the reason he hears nothing.
The engine hands its audio over in pieces as they arrive (see `voice.play_stream`), and
`Failover` decides at the FIRST piece of every sentence whether this line is the cloud's or the
local voice's, which is the last moment the choice is still free.

The format is `pcm_24000` - signed 16-bit little-endian at 24kHz, the rate Kokoro also speaks
at - so the samples go straight to the sound device with no decoder and no dependency for one.
"""

import itertools
import json
import urllib.request
from pathlib import Path

import numpy

API = "https://api.elevenlabs.io"

# What `pcm_24000` means, and the only sample rate this module deals in. 44.1k PCM is a paid
# tier's format; 24k is on every plan including the free one, and is what Kokoro produces too.
SAMPLE_RATE = 24000

# Flash is ElevenLabs' low-latency model (~75ms to first audio, and half the credits per
# character of the multilingual one). This voice interrupts and is interrupted; a model that
# sounds fractionally better a second later is the wrong trade here.
DEFAULT_MODEL = "eleven_flash_v2_5"

# How long to wait on the network before giving the sentence to the local voice instead. Long
# enough to cover a slow first byte, short enough that a dead connection is not a silence he
# sits through wondering whether the app has died.
TIMEOUT_SECONDS = 8

# How many sentences in a row may fail before the local voice simply takes over for the rest of
# the session. Each attempt costs the timeout above before any sound comes out, so against a dead
# key or a spent quota this is the difference between one bad pause and one before every sentence.
GIVE_UP_AFTER = 3

_FULL_SCALE = 32768.0  # what a signed 16-bit sample counts up to

SETTINGS_FILE = "cloud.json"


class CloudVoiceError(RuntimeError):
    """Something he can act on, worded for him - never a traceback and never a silence."""


def _urlopen(request, timeout=None):
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - a pinned https API


def settings_in(directory):
    """His cloud.json beside the local model, or None when there isn't a usable one.

    None is the ordinary case, not a fault: every checkout that has not been given a key is this,
    and so is a file caught mid-edit - which will happen, because he edits it by hand. A broken
    file costs the cloud voice for that launch and must never cost the launch."""
    try:
        held = json.loads((Path(directory) / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(held, dict) or not (held.get("key") and held.get("voice")):
        return None
    return held


def connect(settings, *, open_url=_urlopen):
    """The engine his settings name, once the account has actually answered for them.

    Announcing a service from its config alone is how this project last shipped a whole day of
    "connected" against a server that never started; the same rule holds here. Listing the voices
    proves the key in one cheap request AND resolves the name he wrote to the id the API wants,
    so he never has to paste twenty characters of noise into a file to pick a voice."""
    key = settings["key"]
    wanted = str(settings["voice"]).strip()
    request = urllib.request.Request(f"{API}/v2/voices?page_size=100",
                                     headers={"xi-api-key": key})
    try:
        with open_url(request, timeout=TIMEOUT_SECONDS) as response:
            voices = json.loads(response.read() or b"{}").get("voices") or []
    except Exception as fault:
        raise CloudVoiceError(f"ElevenLabs wouldn't answer for that key: {fault}") from fault
    for voice in voices:
        # A name is his to type, so it matches however he capitalized it; an id is the API's own
        # string and matches exactly.
        if wanted == voice.get("voice_id") or wanted.lower() == str(voice.get("name", "")).lower():
            return ElevenLabsEngine(key, voice["voice_id"],
                                    model=settings.get("model") or DEFAULT_MODEL,
                                    open_url=open_url)
    # The library is thousands of voices and only the ones he has added to the account are
    # usable, so the list is the actionable half of this - not that the name was wrong.
    have = ", ".join(str(voice.get("name")) for voice in voices) or "none at all"
    raise CloudVoiceError(f"no ElevenLabs voice called {wanted!r} on that account - it has: {have}")


class ElevenLabsEngine:
    """Synthesis: text in, (chunks, samplerate) out, in one configured voice."""

    def __init__(self, key, voice_id, *, model=DEFAULT_MODEL, open_url=_urlopen):
        self._key = key
        self._voice_id = voice_id
        self._model = model
        self._open_url = open_url

    def say(self, text):
        return self._pieces(text), SAMPLE_RATE

    def _pieces(self, text):
        """The line's audio as it is generated. Lazy on purpose: nothing is asked of the network
        until someone pulls the first piece, which is where `Failover` makes its decision."""
        request = urllib.request.Request(
            f"{API}/v1/text-to-speech/{self._voice_id}/stream?output_format=pcm_{SAMPLE_RATE}",
            data=json.dumps({"text": text, "model_id": self._model}).encode("utf-8"),
            headers={"xi-api-key": self._key, "content-type": "application/json",
                     "accept": "audio/pcm"},
        )
        with self._open_url(request, timeout=TIMEOUT_SECONDS) as response:
            remainder = b""
            while True:
                block = response.read(4096)
                if not block:
                    return
                block = remainder + block
                # A read can split a sample down the middle; its odd byte belongs to the next one.
                whole = len(block) - len(block) % 2
                block, remainder = block[:whole], block[whole:]
                if block:
                    yield numpy.frombuffer(block, dtype="<i2").astype("float32") / _FULL_SCALE


class Failover:
    """The cloud voice with the local one standing behind it, sentence by sentence.

    The decision is made at the FIRST piece of audio, which is the last moment it is still free:
    ask the cloud, and if nothing comes back - no key, no network, no quota - hand the same
    sentence to the local voice instead, so the failure costs a change of voice rather than a
    silence."""

    def __init__(self, cloud, local, *, announce=lambda line: None,
                 give_up_after=GIVE_UP_AFTER):
        self._cloud = cloud
        self._local = local
        self._announce = announce
        self._give_up_after = give_up_after
        self._failures = 0
        self._explained = False
        self._given_up = False

    def say(self, text):
        if self._given_up:
            return self._local.say(text)
        chunks, samplerate = self._cloud.say(text)
        pieces = iter(chunks)
        try:
            first = next(pieces)
        except Exception as fault:
            return self._fell_back(text, fault)
        self._failures = 0  # only a RUN of failures means the cloud is gone rather than stumbling
        return itertools.chain([first], self._rest(pieces)), samplerate

    def _rest(self, pieces):
        """The remainder of a sentence already sounding, and never a fault out of this module.

        This is drained inside the pump that speaks every reply, so anything let out of here
        takes that thread down and Excephalon goes silent with its window still saying it is
        talking. There is no re-speaking the tail either - the local voice would start the
        sentence again and he would hear its first half twice - so a sentence that loses its
        connection simply ends where it ended."""
        try:
            yield from pieces
        except Exception as fault:
            self._explain(f"(the cloud voice dropped mid-sentence: {fault})")

    def _fell_back(self, text, fault):
        """The local voice takes this sentence - and the first time it happens, says why."""
        self._failures += 1
        self._explain(f"(the cloud voice didn't answer: {fault} - the local voice is speaking)")
        if self._failures >= self._give_up_after:
            self._given_up = True
            # Every attempt costs the network timeout BEFORE the local voice starts. Against a
            # dead key that pause would sit in front of every sentence for the rest of the
            # session, which is worse than the voice he is falling back to.
            self._announce("(the cloud voice keeps failing - the local voice has it from here)")
        return self._local.say(text)

    def _explain(self, line):
        """Say what changed, once a session: a voice that has quietly become a different voice
        reads as a broken app, and the same aside every sentence buries the conversation."""
        if not self._explained:
            self._explained = True
            self._announce(line)
