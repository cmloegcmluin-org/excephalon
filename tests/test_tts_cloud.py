import json
import struct

import numpy
import pytest

from excephalon.tts_cloud import (
    SAMPLE_RATE, CloudVoiceError, ElevenLabsEngine, Failover, connect, settings_in, setup,
)
from excephalon.tts_neural import KokoroEngine
from excephalon.voice import Speaker, play_stream


def pcm(*values):
    """Signed 16-bit little-endian samples, which is what `pcm_24000` is."""
    return struct.pack(f"<{len(values)}h", *values)


class FakeResponse:
    """An HTTP body handed over in pieces, the way a streamed one arrives."""

    def __init__(self, pieces):
        self._pieces = list(pieces)

    def read(self, size=None):
        return self._pieces.pop(0) if self._pieces else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTP:
    def __init__(self, pieces=(b"",)):
        self.requests = []
        self._pieces = pieces

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(self._pieces)


def test_the_engine_streams_the_line_as_pcm_and_hands_over_what_has_arrived():
    # Pieces out as pieces in: the point of the cloud voice streaming at all is that the first
    # sound does not wait on the last byte of the sentence.
    http = FakeHTTP([pcm(0, 16384), pcm(-16384)])
    engine = ElevenLabsEngine("sk-test", "voice-id", open_url=http)

    chunks, samplerate = engine.say("Both agents are green.")

    assert samplerate == SAMPLE_RATE
    assert [list(chunk) for chunk in chunks] == [[0.0, 0.5], [-0.5]]


def test_the_request_carries_the_key_the_voice_and_the_low_latency_model():
    http = FakeHTTP()
    engine = ElevenLabsEngine("sk-test", "voice-id", open_url=http)

    list(engine.say("Hey.")[0])

    request = http.requests[0]
    assert request.full_url.startswith(
        "https://api.elevenlabs.io/v1/text-to-speech/voice-id/stream?")
    assert "output_format=pcm_24000" in request.full_url  # no decoding, no extra dependency
    assert request.headers["Xi-api-key"] == "sk-test"
    assert json.loads(request.data) == {"text": "Hey.", "model_id": "eleven_flash_v2_5"}


class FakeLocal:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)
        return [f"<local {text}>"], SAMPLE_RATE


class Dead:
    """A cloud that cannot answer at all - no key, no network, no quota left."""

    def __init__(self, fault=None):
        self.asked = []
        self._fault = fault or OSError("connection refused")

    def say(self, text):
        self.asked.append(text)
        return self._raising(), SAMPLE_RATE

    def _raising(self):
        raise self._fault
        yield  # pragma: no cover - what makes the above a generator


def test_a_sentence_the_cloud_cannot_deliver_is_spoken_by_the_local_voice():
    # The network is the one part of this that is allowed to fail, and it is never allowed to be
    # the reason he hears nothing.
    local = FakeLocal()
    voice = Failover(Dead(), local)

    chunks, samplerate = voice.say("Both agents are green.")

    assert list(chunks) == ["<local Both agents are green.>"]
    assert local.said == ["Both agents are green."]
    assert samplerate == SAMPLE_RATE


def test_the_change_of_voice_is_said_out_loud_rather_than_left_to_be_noticed():
    # The same call the neural voice's own fallback makes: a voice that quietly becomes a
    # different voice reads as a broken app, and he is left guessing what changed.
    said = []
    voice = Failover(Dead(OSError("no route to host")), FakeLocal(), announce=said.append)

    list(voice.say("One.")[0])

    assert len(said) == 1
    assert "no route to host" in said[0]


def test_the_change_of_voice_is_said_once_and_not_once_a_sentence():
    # An aside per sentence would bury the conversation in the same line over and over.
    said = []
    voice = Failover(Dead(), FakeLocal(), announce=said.append, give_up_after=99)

    for line in ("One.", "Two.", "Three."):
        list(voice.say(line)[0])

    assert len(said) == 1


class DropsHalfway:
    """A connection that answers, then dies with the sentence half spoken."""

    def say(self, text):
        return self._pieces(), SAMPLE_RATE

    def _pieces(self):
        yield "first half"
        raise OSError("connection reset")


def test_a_connection_that_dies_mid_sentence_ends_the_sentence_not_the_voice():
    # This runs inside the pump that speaks every reply. A fault let out of here would take that
    # thread down and Excephalon would go silent for the rest of the session with its window
    # still saying it was talking - a helper killing its host, which is the failure this project
    # has already sat through once.
    said = []
    voice = Failover(DropsHalfway(), FakeLocal(), announce=said.append)

    chunks, _ = voice.say("Both agents are green.")

    assert list(chunks) == ["first half"]  # what got out, and nothing raised
    assert "connection reset" in said[0]


def test_a_cloud_that_keeps_failing_stops_being_asked():
    # Every failed sentence pays the network timeout before the local voice starts. A dead key
    # would put that pause in front of every sentence for the rest of the session, so after a
    # short run of failures the local voice simply takes over.
    said = []
    cloud = Dead()
    voice = Failover(cloud, FakeLocal(), announce=said.append, give_up_after=3)

    for line in ("One.", "Two.", "Three.", "Four.", "Five."):
        list(voice.say(line)[0])

    assert cloud.asked == ["One.", "Two.", "Three."]  # never asked again after the third
    assert any("local voice" in line for line in said[1:])


def test_a_cloud_that_answers_again_after_a_stumble_keeps_its_place():
    # A single dropped request is not a dead key; counting only CONSECUTIVE failures is what
    # keeps one bad moment from costing him the voice for the rest of the session.
    class FailsOnce:
        def __init__(self):
            self.asked = []

        def say(self, text):
            self.asked.append(text)
            return self._pieces(len(self.asked) == 1), SAMPLE_RATE

        def _pieces(self, fail):
            if fail:
                raise OSError("hiccup")
            yield f"<cloud {len(self.asked)}>"

    cloud = FailsOnce()
    voice = Failover(cloud, FakeLocal(), give_up_after=2)

    for line in ("One.", "Two.", "Three."):
        list(voice.say(line)[0])

    assert len(cloud.asked) == 3  # the run was broken, so nothing was given up on


def test_no_settings_file_means_the_local_voice_and_no_complaint(tmp_path):
    # Every checkout that has not been given a key is this case, including the gate's. It is not
    # a fault and must not be announced as one.
    assert settings_in(tmp_path) is None


def test_a_settings_file_without_a_key_or_a_voice_is_not_a_configuration(tmp_path):
    (tmp_path / "cloud.json").write_text('{"voice": "Adam"}', encoding="utf-8")

    assert settings_in(tmp_path) is None


def test_a_half_written_settings_file_costs_the_cloud_voice_and_nothing_else(tmp_path):
    # A file he edits by hand, so it will sometimes be mid-edit when the app starts. A broken
    # one loses the cloud voice for that launch; it must never lose the launch.
    (tmp_path / "cloud.json").write_text('{"key": "sk-test", "voi', encoding="utf-8")

    assert settings_in(tmp_path) is None


def test_the_settings_name_the_key_the_voice_and_the_model(tmp_path):
    (tmp_path / "cloud.json").write_text(
        '{"key": "sk-test", "voice": "Adam", "model": "eleven_multilingual_v2"}', encoding="utf-8")

    assert settings_in(tmp_path) == {"key": "sk-test", "voice": "Adam",
                                     "model": "eleven_multilingual_v2"}


class FakeVoices:
    """The account's voice list, which is what proves the key works and what a name resolves in."""

    def __init__(self, voices, status=None):
        self.requests = []
        self._body = json.dumps({"voices": voices}).encode("utf-8")
        self._status = status

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self._status is not None:
            raise self._status
        return FakeResponse([self._body])


def test_connecting_resolves_a_voice_by_name_so_he_never_pastes_an_id():
    # He picks a voice by listening to it on their site; its id is twenty characters of noise.
    # A name is a thing a person can type into a file and recognise again a month later.
    http = FakeVoices([{"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel"},
                       {"voice_id": "pNInz6obpgDQGcFmaJgB", "name": "Adam"}])

    engine = connect({"key": "sk-test", "voice": "adam"}, open_url=http)

    assert engine._voice_id == "pNInz6obpgDQGcFmaJgB"
    assert http.requests[0].headers["Xi-api-key"] == "sk-test"


def test_connecting_takes_a_voice_id_as_readily_as_a_name():
    http = FakeVoices([{"voice_id": "pNInz6obpgDQGcFmaJgB", "name": "Adam"}])

    engine = connect({"key": "sk-test", "voice": "pNInz6obpgDQGcFmaJgB"}, open_url=http)

    assert engine._voice_id == "pNInz6obpgDQGcFmaJgB"


def test_a_voice_the_account_does_not_have_names_the_ones_it_does():
    # The library is thousands of voices and only the ones he has ADDED are usable, so the useful
    # half of this fault is the list - not that the name was wrong.
    http = FakeVoices([{"voice_id": "a1", "name": "Rachel"}, {"voice_id": "a2", "name": "Adam"}])

    with pytest.raises(CloudVoiceError) as fault:
        connect({"key": "sk-test", "voice": "Brian"}, open_url=http)

    assert "Rachel" in str(fault.value) and "Adam" in str(fault.value)


def test_a_key_that_does_not_work_is_found_at_startup_not_at_the_first_reply():
    # The same call `check_services` makes of every service: speak to it before calling it
    # connected. A key that has expired otherwise reads as a working app until he asks it
    # something, and then as an app whose voice has mysteriously changed.
    http = FakeVoices([], status=OSError("HTTP Error 401: Unauthorized"))

    with pytest.raises(CloudVoiceError) as fault:
        connect({"key": "sk-bad", "voice": "Adam"}, open_url=http)

    assert "401" in str(fault.value)


class FakeOutput:
    def __init__(self):
        self.written = []

    def write(self, chunk):
        self.written.append(list(chunk))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_reply_reaches_the_sound_device_as_the_cloud_generates_it():
    # End to end through the real Speaker, the real player and the real engine, with only the
    # network and the sound device faked: it is not enough that each piece works: the point of
    # all of it is that samples come out of the device, and out of it EARLY.
    http = FakeHTTP([pcm(0, 16384), pcm(-16384, 0)])
    output = FakeOutput()
    speaker = Speaker(Failover(ElevenLabsEngine("sk-test", "voice-id", open_url=http), FakeLocal()),
                      play=lambda chunks, samplerate, interrupt=None: play_stream(
                          chunks, samplerate, interrupt=interrupt,
                          open_stream=lambda rate: output, prebuffer_seconds=0))

    speaker.speak("Both agents are green.")

    assert [sample for chunk in output.written for sample in chunk] == [0.0, 0.5, -0.5, 0.0]
    # Two pieces arrived and two went out - the second was never held behind the first's write.
    assert len(output.written) == 2


def test_a_dead_network_still_reaches_the_sound_device_in_the_local_voice():
    def unreachable(request, timeout=None):
        raise OSError("no route to host")

    output = FakeOutput()
    engine = ElevenLabsEngine("sk-test", "voice-id", open_url=unreachable)
    local = KokoroEngine("model.onnx", "voices.bin", kokoro_factory=_FakeKokoro)
    speaker = Speaker(Failover(engine, local),
                      play=lambda chunks, samplerate, interrupt=None: play_stream(
                          chunks, samplerate, interrupt=interrupt,
                          open_stream=lambda rate: output, prebuffer_seconds=0))

    speaker.speak("Both agents are green.")

    assert output.written  # the sentence was heard, in the other voice


class _FakeKokoro:
    def __init__(self, model_path, voices_path):
        pass

    def create(self, text, voice, speed, lang):
        return numpy.array([0.25, -0.25], dtype="float32"), SAMPLE_RATE


class Conversation:
    """The door's side of a double-click: what it printed, and what he typed back."""

    def __init__(self, *answers):
        self.said = []
        self._answers = list(answers)

    def ask(self, prompt):
        self.said.append(prompt)
        return self._answers.pop(0)

    def transcript(self):
        return "\n".join(self.said)


def test_the_setup_door_lists_his_voices_and_writes_the_one_he_picks(tmp_path):
    # He picks a voice by LISTENING to it on their site, then has to tell this app which one. A
    # numbered list of the voices his account actually has is the whole of that conversation -
    # the alternative is hand-writing JSON around a twenty-character id.
    http = FakeVoices([{"voice_id": "a1", "name": "Rachel"}, {"voice_id": "a2", "name": "Adam"}])
    door = Conversation("sk-test", "2")

    assert setup(tmp_path, ask=door.ask, say=door.said.append, open_url=http) is True

    assert settings_in(tmp_path) == {"key": "sk-test", "voice": "Adam"}
    assert "Rachel" in door.transcript() and "Adam" in door.transcript()


def test_the_setup_door_takes_a_name_as_readily_as_a_number(tmp_path):
    http = FakeVoices([{"voice_id": "a1", "name": "Rachel"}, {"voice_id": "a2", "name": "Adam"}])
    door = Conversation("sk-test", "rachel")

    setup(tmp_path, ask=door.ask, say=door.said.append, open_url=http)

    assert settings_in(tmp_path)["voice"] == "Rachel"


def test_a_key_the_account_rejects_is_said_at_the_door_and_changes_nothing(tmp_path):
    # Finding out at the door beats finding out at the next launch, when the only symptom is a
    # voice that isn't the one he chose.
    http = FakeVoices([], status=OSError("HTTP Error 401: Unauthorized"))
    door = Conversation("sk-wrong")

    assert setup(tmp_path, ask=door.ask, say=door.said.append, open_url=http) is False

    assert settings_in(tmp_path) is None
    assert "401" in door.transcript()


def test_the_setup_door_leaves_an_existing_choice_alone_when_he_answers_nothing(tmp_path):
    # A door opened by accident, or to look, must not be a way to lose the voice he set up.
    (tmp_path / "cloud.json").write_text('{"key": "sk-old", "voice": "Adam"}', encoding="utf-8")
    door = Conversation("")

    assert setup(tmp_path, ask=door.ask, say=door.said.append, open_url=FakeVoices([])) is False

    assert settings_in(tmp_path) == {"key": "sk-old", "voice": "Adam"}
