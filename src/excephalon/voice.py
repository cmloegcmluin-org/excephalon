"""How a streamed reply becomes audible speech, sentence by sentence.

The old voice spoke a reply only once the whole of it existed, which put the entire brain latency
between the question and the first sound. Here the reply's text arrives as deltas while the model
is still writing, a sentence is cut the moment its end appears, and each finished sentence is
synthesized and played while the next is still forming - so the wait to first words is the time to
the first sentence END, not to the end of the whole turn.
"""

import queue
import re
import threading
from dataclasses import dataclass

import numpy

# The seam a spoken sentence ends on: closing punctuation followed by whitespace (or a newline,
# which ends a line of a list the same way). A period right after a digit is an enumerator
# ("1. Open the console") - the number belongs to its step, so it is no seam. Abbreviations
# ("e.g. ") occasionally split early; a spurious beat between two fragments costs less than
# holding real sentences back.
_SENTENCE_END = re.compile(r"(?<=[.!?:])(?<![0-9]\.)\s+|\n+")


class SentenceStream:
    """Cuts a stream of text deltas into sentences, handing each over the moment it is complete."""

    def __init__(self, on_sentence):
        self._on_sentence = on_sentence
        self._pending = ""

    def feed(self, delta):
        self._pending += delta
        parts = _SENTENCE_END.split(self._pending)
        for done in parts[:-1]:
            if done.strip():
                self._on_sentence(done.strip())
        self._pending = parts[-1]

    def flush(self):
        """The reply is over: whatever is still pending is its last words, spoken as they stand."""
        tail, self._pending = self._pending.strip(), ""
        if tail:
            self._on_sentence(tail)


_END = object()  # closes a Reply's queue; never spoken


@dataclass(frozen=True)
class Receipt:
    """Whether an utterance actually reached the air - the one answer to "did he hear it?".

    Everything the app owes him is spent on this: news leaves the durable spool, an offer counts as
    made, a first line counts as said. The question was being answered five different ways in the
    loop - a bare True from a speak that returns nothing, a truthy string from a drained stream, a
    flag latched before the audio started - and each one that guessed wrong spent something over
    zero sound. A merge report died in exactly that gap: the barge-in was already down when the
    words were about to start, and the spool was cleared as if he had heard them.

    `began` is the only thing anyone may spend on. `cut` says he stopped it partway, which is his
    deliberate act and never an undelivery - he heard the start and chose to stop it - but the
    record must still say so, or a silenced line reads as fully spoken. `said` is what actually
    sounded, which after a cut is the head of it that got out.
    """

    began: bool
    said: str = ""
    cut: bool = False

    def __bool__(self):
        return self.began

    def __str__(self):
        return self.said


UNSAID = Receipt(began=False)  # nothing sounded, so nothing may be spent


class Speaker:
    """The voice: one engine, one way audio goes out, for one-shot lines and streamed replies alike.

    `engine.say(text) -> (chunks, samplerate)` synthesizes, where chunks is an ITERABLE of sample
    pieces rather than one clip; `play(chunks, samplerate, interrupt)` makes them audible as they
    arrive and returns early when the interrupt fires. A local engine that has the whole line at
    once yields a single piece and loses nothing; a cloud engine yields the line as it is
    generated, so the first sound is not held behind the last byte. Both are injected, so every
    behavior here is tested without an audio device."""

    def __init__(self, engine, *, play):
        self._engine = engine
        self._play = play

    def speak(self, text, *, interrupt=None):
        """One whole utterance - a greeting, a piece of news - and a Receipt for it.

        The interrupt is read again after synthesis, immediately before the audio goes out, which
        is the same discipline the streamed pump keeps: a barge-in landing while the engine works
        means nothing sounded, and nothing owed may be spent on it."""
        said = str(text).strip()
        if not said or _fired(interrupt):
            return UNSAID
        chunks, samplerate = self._engine.say(said)
        if _fired(interrupt):
            return UNSAID
        self._play(chunks, samplerate, interrupt=interrupt)
        return Receipt(began=True, said=said, cut=_fired(interrupt))

    def stream(self, *, interrupt=None, spoken_form=None):
        """A reply about to arrive as text deltas: speak it sentence by sentence as it forms.

        `spoken_form` maps a sentence to what the voice should SAY for it (a path to its
        filename, a URL to "the link") while the record keeps the real text - the screen shows
        what gets clicked; the speaker says what a person would."""
        return Reply(self._engine, self._play, interrupt, spoken_form)


class Reply:
    """One streamed reply being spoken while it is still being written.

    Deltas go in on whatever thread the brain streams from; a worker of its own synthesizes and
    plays each finished sentence, so the next sentence forms while the last one sounds. `done()`
    waits for the audio to run out and returns the Receipt for it - what was actually spoken,
    which after a barge-in is the head of the reply that got out before the cut."""

    def __init__(self, engine, play, interrupt, spoken_form=None):
        self._engine = engine
        self._play = play
        self._interrupt = interrupt
        self._spoken_form = spoken_form or (lambda sentence: sentence)
        self._queue = queue.SimpleQueue()
        self._spoken = []
        # Whether this reply's sound is in the air RIGHT NOW - one span from the first play to
        # drained, not flapping between sentences. The mic asks: while the brain merely thinks,
        # what it hears is the user (keep it); while this is True, it is mostly Excephalon itself.
        self.sounding = False
        self._sentences = SentenceStream(self._queue.put)
        self._worker = threading.Thread(target=self._pump, daemon=True)
        self._worker.start()

    def add(self, delta):
        self._sentences.feed(delta)

    def done(self):
        """The reply's text is complete; wait out the audio and receipt what of it was spoken.

        An utterance a barge-in drained whole - the cut was already down when its first word was
        about to go out - sounded nothing, and its receipt says so: whatever it was carrying is
        still owed."""
        self._sentences.flush()
        self._queue.put(_END)
        self._worker.join()
        said = " ".join(self._spoken)
        return Receipt(began=bool(said.strip()), said=said, cut=_fired(self._interrupt))

    def _pump(self):
        try:
            while True:
                sentence = self._queue.get()
                if sentence is _END:
                    return
                if _fired(self._interrupt):
                    continue  # cut off: drain the rest unspoken, so done() comes straight back
                chunks, samplerate = self._engine.say(self._spoken_form(sentence))
                if _fired(self._interrupt):
                    continue
                self.sounding = True
                self._play(chunks, samplerate, interrupt=self._interrupt)
                self._spoken.append(sentence)  # heard - at least its start, if the cut came mid-word
        finally:
            self.sounding = False  # however the reply ends, the air is clear once the pump is


def _fired(interrupt):
    return interrupt is not None and interrupt.is_set()


def _sounddevice_stream(samplerate):
    import sounddevice

    return sounddevice.OutputStream(samplerate=samplerate, channels=1, dtype="float32")


# How much sound is banked before the first write. A local engine hands over the whole line at
# once and clears this on its first piece, so it costs nothing there; a cloud engine's pieces
# arrive over a network, and writing the first of them the instant it lands leaves the device
# with nothing queued behind it - any hesitation upstream is then heard as a gap mid-word. A
# fifth of a second is a cushion the ear does not notice being given.
PREBUFFER_SECONDS = 0.2


def play_stream(chunks, samplerate, *, interrupt=None, open_stream=_sounddevice_stream,
                chunk_seconds=0.1, prebuffer_seconds=PREBUFFER_SECONDS):
    """Make synthesized audio audible as its pieces arrive, in writes small enough to cut short.

    The interrupt is checked between writes, so a write is the longest a reply can keep sounding
    after they say stop - a tenth of a second, not the rest of the sentence."""
    step = max(1, int(samplerate * chunk_seconds))
    cushion = int(samplerate * prebuffer_seconds)
    with open_stream(samplerate) as stream:
        banked, banked_length, playing = [], 0, False
        for piece in chunks:
            if _fired(interrupt):
                return
            banked.append(piece)
            banked_length += len(piece)
            if banked_length < (step if playing else cushion):
                continue  # too little to be worth a write; let it gather
            playing = True
            if not _write(stream, _joined(banked), step, interrupt):
                return
            banked, banked_length = [], 0
        if banked:
            _write(stream, _joined(banked), step, interrupt)


def _joined(pieces):
    """One array of the banked pieces - and the piece itself when there is only one of them,
    which is every utterance of a local engine and must not cost a copy of the whole clip."""
    return pieces[0] if len(pieces) == 1 else numpy.concatenate(pieces)


def _write(stream, samples, step, interrupt):
    """Write one span of samples; False once the interrupt has cut it short."""
    for start in range(0, len(samples), step):
        if _fired(interrupt):
            return False
        stream.write(samples[start:start + step])
    return True
