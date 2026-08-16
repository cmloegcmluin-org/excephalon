import threading
from contextlib import contextmanager

from excephalon.voice import SentenceStream, Speaker, play_stream


class FakeEngine:
    """Synthesis as a marker: the "samples" for a text are just the text, tagged.

    An engine hands over its audio in pieces; this one always has the whole line at once, which
    is one piece - the shape a local engine has and a cloud one grows into."""

    def __init__(self):
        self.synthesized = []

    def say(self, text):
        self.synthesized.append(text)
        return [f"<{text}>"], 24000


class FakePlayer:
    def __init__(self, hold=None):
        self.played = []
        self._hold = hold  # set to an Event to make playback take real time

    def __call__(self, chunks, samplerate, interrupt=None):
        if interrupt is not None and interrupt.is_set():
            return
        self.played.extend(chunks)
        if self._hold is not None:
            self._hold.wait(2.0)


def test_a_sentence_is_handed_over_the_moment_its_end_arrives():
    # The voice speaks sentence by sentence while the rest of the reply is still being written -
    # waiting for the whole reply is the old world, where nothing was heard for half a minute.
    out = []
    stream = SentenceStream(out.append)

    stream.feed("Both agents are ")
    assert out == []  # mid-sentence: nothing worth saying yet
    stream.feed("green. The drive one ")

    assert out == ["Both agents are green."]


def test_the_tail_of_a_reply_is_spoken_when_the_reply_ends():
    # A reply that ends without terminal punctuation still ends; its last words are not optional.
    out = []
    stream = SentenceStream(out.append)

    stream.feed("Done. Want the steps")
    stream.flush()

    assert out == ["Done.", "Want the steps"]


def test_numbered_steps_come_out_a_line_at_a_time():
    # A walkthrough arrives as lines; each is a thing to say whole, not held for a period.
    out = []
    stream = SentenceStream(out.append)

    stream.feed("1. Open the console\n2. Enable the API\n")
    stream.flush()

    assert out == ["1. Open the console", "2. Enable the API"]


def test_a_one_shot_line_is_synthesized_and_played_whole():
    engine, player = FakeEngine(), FakePlayer()
    speaker = Speaker(engine, play=player)

    speaker.speak("Be seeing you.")

    assert engine.synthesized == ["Be seeing you."]
    assert player.played == ["<Be seeing you.>"]


def test_a_streamed_reply_is_spoken_sentence_by_sentence_in_order():
    engine, player = FakeEngine(), FakePlayer()
    speaker = Speaker(engine, play=player)

    reply = speaker.stream()
    reply.add("Both agents are green. ")
    reply.add("The drive one wants a decision")
    spoken = reply.done()

    assert player.played == ["<Both agents are green.>", "<The drive one wants a decision>"]
    assert spoken == "Both agents are green. The drive one wants a decision"


def test_sentences_are_synthesized_in_their_spoken_form_but_recorded_raw():
    # The screen shows the real path - it is what gets clicked - and nobody wants a minute of
    # "see colon backslash users" out of the speaker. The transform feeds the engine only; what
    # was said (for the record) stays the real text.
    engine, player = FakeEngine(), FakePlayer()
    speaker = Speaker(engine, play=player)

    reply = speaker.stream(spoken_form=lambda text: text.replace("C:/deep/path.md", "path.md"))
    reply.add("It's in C:/deep/path.md. ")
    spoken = reply.done()

    assert engine.synthesized == ["It's in path.md."]
    assert spoken == "It's in C:/deep/path.md."


def test_a_reply_reports_when_its_sound_is_actually_in_the_air():
    # The mic needs to know the difference between Excephalon THINKING (no sound - the user's
    # words are theirs to keep) and its voice actually sounding (what the mic hears now is mostly
    # Excephalon itself). One flag spanning first sound to drained, not flapping per sentence.
    playing = threading.Event()
    hold = threading.Event()

    class HoldingPlayer(FakePlayer):
        def __call__(self, samples, samplerate, interrupt=None):
            playing.set()
            super().__call__(samples, samplerate, interrupt)

    engine, player = FakeEngine(), HoldingPlayer(hold=hold)
    speaker = Speaker(engine, play=player)

    reply = speaker.stream()
    assert reply.sounding is False  # the brain is still writing; nothing is in the air

    reply.add("First thing. ")
    assert playing.wait(2.0)
    assert reply.sounding is True  # audio is out of the speaker right now

    hold.set()
    reply.done()
    assert reply.sounding is False  # drained: the air is clear again


def test_a_barge_in_cuts_the_reply_and_the_rest_stays_unspoken():
    # One stop silences all of it: the queued sentences drain unspoken, and done() reports only
    # what actually got out - the record must never claim words were heard that weren't.
    playing = threading.Event()
    hold = threading.Event()

    class HoldingPlayer(FakePlayer):
        def __call__(self, samples, samplerate, interrupt=None):
            playing.set()
            super().__call__(samples, samplerate, interrupt)

    engine, player = FakeEngine(), HoldingPlayer(hold=hold)
    interrupt = threading.Event()
    speaker = Speaker(engine, play=player)

    reply = speaker.stream(interrupt=interrupt)
    reply.add("First thing. Second thing. Third thing. ")
    assert playing.wait(2.0)  # the first sentence is sounding
    interrupt.set()  # they cut in
    hold.set()  # ...which is what makes the in-flight playback return
    spoken = reply.done()

    assert player.played == ["<First thing.>"]
    assert spoken == "First thing."


class FakeOutput:
    def __init__(self):
        self.written = []

    def write(self, chunk):
        self.written.append(list(chunk))


@contextmanager
def _fake_stream(output):
    yield output


def test_playback_writes_the_whole_clip_in_small_pieces():
    # Small pieces are what make a cut-off feel instant: the check between writes is the only
    # moment playback can stop, so a piece is at most a tenth of a second of sound.
    output = FakeOutput()

    play_stream([[0.1] * 5], samplerate=20, interrupt=None,
                open_stream=lambda samplerate: _fake_stream(output), chunk_seconds=0.1,
                prebuffer_seconds=0)

    assert [len(chunk) for chunk in output.written] == [2, 2, 1]  # 0.1s at 20Hz = 2 samples
    assert [sample for chunk in output.written for sample in chunk] == [0.1] * 5


def test_playback_stops_at_the_first_check_after_a_cut_off():
    interrupt = threading.Event()

    class CutsAfterFirstWrite(FakeOutput):
        def write(self, chunk):
            super().write(chunk)
            interrupt.set()

    output = CutsAfterFirstWrite()
    play_stream([[0.1] * 6], samplerate=20, interrupt=interrupt,
                open_stream=lambda samplerate: _fake_stream(output), chunk_seconds=0.1,
                prebuffer_seconds=0)

    assert len(output.written) == 1  # the rest of the clip was never written


def test_playback_starts_before_the_clip_has_finished_arriving():
    # A cloud voice hands over its audio in pieces as it is generated; waiting for the last piece
    # would put the whole synthesis between the question and the first sound, which is the world
    # sentence-by-sentence speaking exists to escape.
    output = FakeOutput()
    arrived = []

    def pieces():
        for value in (0.1, 0.2, 0.3):
            arrived.append(value)
            yield [value] * 2

    play_stream(pieces(), samplerate=20, interrupt=None,
                open_stream=lambda samplerate: _fake_stream(output), chunk_seconds=0.1,
                prebuffer_seconds=0)

    # Three pieces in, three pieces out - none of them held back for the ones behind it.
    assert [sample for chunk in output.written for sample in chunk] == [0.1, 0.1, 0.2, 0.2, 0.3, 0.3]


def test_playback_holds_the_first_pieces_back_until_there_is_a_cushion():
    # Writing the very first bytes the instant they land leaves the device with nothing queued, so
    # any hesitation in the network is heard as a gap or a click mid-word. A short cushion of
    # sound is banked first; from then on the device's own buffer paces the writes.
    output = FakeOutput()

    play_stream([[0.1]] * 6, samplerate=20, interrupt=None,
                open_stream=lambda samplerate: _fake_stream(output), chunk_seconds=0.1,
                prebuffer_seconds=0.2)

    # 0.2s at 20Hz is 4 samples: nothing is written until four have arrived, and they go out
    # together rather than dribbling.
    assert [len(chunk) for chunk in output.written] == [2, 2, 2]
    assert [sample for chunk in output.written for sample in chunk] == [0.1] * 6
