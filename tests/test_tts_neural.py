from excephalon.tts_neural import DEFAULT_SPEED, DEFAULT_VOICE, KokoroEngine, ensure_voice, voice_choice


def test_the_voice_choice_defaults_when_nothing_is_configured(tmp_path):
    assert voice_choice(tmp_path) == (DEFAULT_VOICE, DEFAULT_SPEED)


def test_voice_txt_beside_the_model_picks_the_voice_and_speed(tmp_path):
    (tmp_path / "voice.txt").write_text("am_fenrir 1.1", encoding="utf-8")

    assert voice_choice(tmp_path) == ("am_fenrir", 1.1)


def test_a_garbled_speed_keeps_the_voice_and_drops_only_the_speed(tmp_path):
    # A half-understood file must never cost the voice entirely.
    (tmp_path / "voice.txt").write_text("am_fenrir fastish", encoding="utf-8")

    assert voice_choice(tmp_path) == ("am_fenrir", DEFAULT_SPEED)


def test_ensure_voice_leaves_files_already_on_disk_alone(tmp_path):
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(b"model")
    (tmp_path / "voices-v1.0.bin").write_bytes(b"voices")
    fetched = []

    paths = ensure_voice(tmp_path, fetch=lambda url, into: fetched.append(url))

    assert fetched == []
    assert paths == (tmp_path / "kokoro-v1.0.onnx", tmp_path / "voices-v1.0.bin")


def test_ensure_voice_fetches_what_is_missing_and_says_so(tmp_path):
    # The voice is a third of a gigabyte the repo doesn't carry: fetched once, into runtime/,
    # announced so the window says why the first launch is busy - never fetched again.
    said = []

    def fetch(url, into):
        into.write_bytes(b"got " + url.encode()[-9:])

    paths = ensure_voice(tmp_path, fetch=fetch, announce=said.append)

    assert paths is not None
    assert all(path.exists() for path in paths)
    assert any("voice" in line.lower() for line in said)


def test_a_failed_fetch_reports_none_and_leaves_no_half_written_model(tmp_path):
    # A half-downloaded model that LOOKS present would fail every later launch; better nothing
    # and the robot voice than a file that exists and cannot load.
    def fetch(url, into):
        into.write_bytes(b"partial")
        raise OSError("connection dropped")

    assert ensure_voice(tmp_path, fetch=fetch) is None
    assert list(tmp_path.glob("*.onnx")) == []


class FakeKokoro:
    def __init__(self, model_path, voices_path):
        self.opened = (model_path, voices_path)

    def create(self, text, voice, speed, lang):
        return f"[{voice}@{speed}] {text}", 24000


def test_the_engine_synthesizes_with_its_configured_voice():
    engine = KokoroEngine("model.onnx", "voices.bin", voice="af_heart", speed=1.1,
                          kokoro_factory=FakeKokoro)

    chunks, samplerate = engine.say("Hey.")

    # The whole line at once is what a local engine has, and one piece is how it says so - the
    # same shape the player takes from a cloud engine that hands its audio over as it generates.
    assert list(chunks) == ["[af_heart@1.1] Hey."]
    assert samplerate == 24000


def test_the_engine_opens_the_model_once_however_much_it_says():
    opened = []

    class Counting(FakeKokoro):
        def __init__(self, model_path, voices_path):
            super().__init__(model_path, voices_path)
            opened.append(model_path)

    engine = KokoroEngine("model.onnx", "voices.bin", kokoro_factory=Counting)
    engine.say("One.")
    engine.say("Two.")

    assert opened == ["model.onnx"]


