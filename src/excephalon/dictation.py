"""The window's microphone: continuous dictation into an editable draft, no "over" required.

The walkie-talkie model (talk, say "over", the turn is sent) made them repeat themselves and forced
every thought to be final. In the window the mic is a STATE, not a turn: while it's on, everything
they say is transcribed chunk-by-chunk into the draft box - which they can edit - and nothing is
sent until they click Submit, or until they say "over", which is still the whole gesture for "I'm
done": it hands the turn over AND puts the mic down. "Stop listening" turns the mic off mid-stream
and keeps the words before it; "hey Excephalon" turns it back on and keeps the words after it; the
window's button does the same by hand. Muted, the room is heard but dropped - only the wake phrase
gets through. "Scratch that" rewinds: it takes back the words immediately before it, whether those
are the chunk already sitting in the box or the ones they said in the same breath, so a sentence that
came out wrong is re-said rather than typed over.

A chunk only reaches the draft box once a PAUSE has ended it, which is the wait they complained of -
nothing printed until they stopped talking. So while a burst is still growing it is also handed to
`hearing`, which reads it over and over on a worker of its own and puts the settled words on a live
line as they settle. That line is a preview and nothing more: the draft box is still filled by the
one authoritative reading taken when the burst ends, and the line comes down as it lands.

The pump runs on its own thread for the whole session. It reports through callbacks (draft text,
mic state, level for the meter, submit requests) so the window can mirror it without this module
knowing anything about Tk. `listen()` is the Conversation-facing half: it blocks until the window
hands over the (possibly edited) draft via `submit`, so the loop's think/speak flow is unchanged.

Two things decide whether they are being heard: whether the mic is ARMED (their button, their spoken
phrases) and whether Excephalon's voice is actually SOUNDING. While the brain merely thinks, nothing
is coming out of the speakers, so the ear stays open and words said then are drafted normally. While
sound IS in the air, the mic mostly hears Excephalon itself - their very first draft box opened with
"I do for you", the tail of its own spoken greeting - so each chunk is judged against the script
being spoken: its own words arriving back are dropped, someone ELSE'S words are kept as draft
(talking over the reply must not mean being unheard), and only a stop BARK cuts the voice - so the
TV, whose sentence once killed an utterance, can never kill a reply. Arming survives a reply, so a conversation flows
without touching the button; only they can end it - by saying "over" or "stop listening", or by
cutting Excephalon off mid-sentence, since a stop should not turn straight around and start
recording their next breath. Auto-listening goes one further and opens the mic each time a reply
ends, so answering costs nothing - except after that same cut-off, for the same reason.
"""

import queue
import re
import threading
import time

from excephalon.hesitation import without_hesitations
from excephalon.phrases import canonical, ends_with_command, strip_leading_command, wakes
from excephalon.recorder import record
from excephalon.stt_mic import (
    PAUSE_FRAMES,
    STOP_WORDS,
    Burst,
    NoiseFloor,
    _is_invented,
    _is_stop_bark,
    _strip_terminator,
    covered_by,
    rms,
)

# How long after end_speaking a frame still counts as Excephalon's sound: its last word is in the
# air when the audio call returns (speaker to mic is ~90ms on the measured desk), and the output
# stream drains a beat after the last write. 10 frames = 300ms at the 30ms mic frame.
SPEAK_TAIL_FRAMES = 10

# How long after his last drafted words he still counts as mid-thought. His natural pauses end
# bursts, so "is a burst open" said no exactly in those pauses - and the news offer barged into
# one ("Entity interrupted me while I was talking... it should never do that"). Not measured off
# recordings, so it errs long: held news costs seconds, an interruption costs the thought.
DICTATION_LULL = 12.0

DEFAULT_MUTE_PHRASES = ("stop listening", "suspend")
# "hey entity" stays alongside the new name: the transcriber knows the plain word cold, and a
# wake phrase that only sometimes transcribes is a mic that only sometimes answers.
DEFAULT_WAKE_PHRASES = ("hey excephalon", "hey entity", "resume")
# Rewind and say it again. Both are stock dictation idioms rather than anything they say about code,
# and both are already what a person says when taking a sentence back mid-thought ("the blue one,
# scratch that, the red one") - so the usage that would be a false alarm IS the one it is for.
DEFAULT_RETRACT_PHRASES = ("scratch that", "strike that")

# What the transcriber writes down when he says the terminator and it does not land. "Over" is a
# short common word with a shorter commoner neighbour, and the model reaches for the neighbour:
# turns end "...Surely you can figure it out. Okay." in the record, which is his "over" wearing
# someone else's spelling ("'Over' keeps getting misheard as 'Okay'. Can we do anything about
# that?"). Accepted only where it CANNOT be him saying okay for real: the draft must already hold
# dictated words, and the word must stand as its own sentence - so "okay" alone answers a question
# as it always did, and "...that's fine, okay" mid-clause is left where it lies. The cost of a
# false positive is a turn sent a beat early; the cost of the miss is a gesture that does nothing.
MISHEARD_TERMINATORS = ("okay",)

# Spoken formatting: said aloud, these become the formatting they name, never words in the draft
# ("I should be able to speak commands like 'paragraph break'"). Stock dictation idioms, so the
# utterance that would be a false alarm is the one they exist for. Order matters: the two-word
# paragraph forms go before "new line", or "new paragraph" would half-match nothing.
_FORMATTING = (
    (re.compile(r"[,.;:]?\s*\b(?:paragraph break|new paragraph)\b[,.;:]?\s*", re.IGNORECASE), "\n\n"),
    (re.compile(r"[,.;:]?\s*\b(?:new line|line break)\b[,.;:]?\s*", re.IGNORECASE), "\n"),
)


def _spoken_formatting(text):
    for phrase, becomes in _FORMATTING:
        text = phrase.sub(becomes, text)
    return text


class Dictation:
    def __init__(
        self,
        transcriber,
        mic,
        *,
        on_draft,
        on_state,
        on_level,
        on_submit_request,
        on_retract,
        muted=False,
        terminator="over",
        mute_phrases=DEFAULT_MUTE_PHRASES,
        wake_phrases=DEFAULT_WAKE_PHRASES,
        retract_phrases=DEFAULT_RETRACT_PHRASES,
        pause_frames=PAUSE_FRAMES,
        stop=None,
        interrupt=None,
        recorder=None,
        hearing=None,
        clock=time.monotonic,
        polish=None,
        scorekeeper=None,
    ):
        self._transcriber = transcriber
        self._mic = mic
        self._on_draft = on_draft
        self._on_state = on_state
        self._on_level = on_level
        self._on_submit_request = on_submit_request
        self._on_retract = on_retract
        self._armed = not muted
        self._auto_listen = False  # off until they ask for it; the button is how the mic opens
        self._silenced = False  # they put the mic down ON the reply - auto-listening must not undo it
        self._speaking = False
        self._terminator = terminator
        self._mutes = tuple(canonical(p) for p in mute_phrases)
        self._wakes = tuple(canonical(p) for p in wake_phrases)
        self._retracts = tuple(canonical(p) for p in retract_phrases)
        self._pause_frames = pause_frames
        self._stop = stop
        self._interrupt = interrupt
        self._recorder = recorder
        # The live line: the burst so far, read over and over on its own worker, so words appear
        # while they are still talking rather than only once a pause ends the sentence.
        self._hearing = hearing
        self._submitted = queue.SimpleQueue()  # the window hands finished turns over here
        self._drafted = False  # dictated words are sitting in the box, unsubmitted
        self._mid_burst = False  # they are talking right now: a burst has started and not yet ended
        self._finish_burst = False  # muted mid-sentence: take down what's still in the air
        self._bark = None  # while Excephalon speaks: an Event a stop bark should fire
        self._stop_words = STOP_WORDS  # what counts as a bark, per the watcher that installed _bark
        self._script = None  # while Excephalon speaks: a callable for the words it is saying
        self._tail_pending = False  # end_speaking happened; the pump owes a grace window for the tail
        self._clock = clock
        self._last_worded = None  # when words last landed in the draft: the mid-thought clock
        self._polish = polish  # heals a submitted draft's spurious sentence breaks, instantly
        # Watch-only voice measuring (see voiceprint.Scorekeeper): every worded chunk is scored
        # against the learned voice on the keeper's own worker. Measured, never acted on - the
        # threshold that will one day act is chosen from these logs, not fitted blind.
        self._scores = scorekeeper

    # ---- the Conversation-facing half ----------------------------------------------------------

    def listen(self):
        """Block until the window submits a turn (Submit button, or a spoken "over"). An interrupt
        (queued agent news during a lull) yields "" so the loop can go deliver it."""
        while True:
            if self._stop is not None and self._stop.is_set():
                return ""
            if self._interrupt is not None and self._interrupt.is_set():
                return ""
            try:
                return self._submitted.get(timeout=0.1)
            except queue.Empty:
                continue

    def submit(self, text):
        """The window hands over the draft - as edited, which is the whole point of the box.

        And the mic goes down with it, exactly as a spoken "over" puts it down: the turn is
        handed over, the composing is finished. Leaving it armed made every reply end with the
        ear already open, which read as auto-listen firing while unchecked - auto-listening
        (and only it) reopens the mic when the reply ends.

        The draft's pause-chopped punctuation is repaired on the way through (`polish`) - his
        call: the brain "will be able to make more sense of what I'm saying if the fix is done
        before submitting". The repair is deterministic and instant (excephalon.polish.mend), so
        the submit costs nothing - the model that once sat here added eight seconds."""
        text = text.strip()
        if self._polish is not None and text:
            text = self._polish(text)
        self._submitted.put(text)
        self._drafted = False  # the box is empty again; the next terminator needs new words
        self._last_worded = None  # the thought was handed over; he is not mid-anything now
        if self._armed:
            self.set_recording(False)

    def catch_stop(self, active, words=STOP_WORDS, script=None, audio=None):
        """The duplex watch, held for a whole reply turn. True the moment they bark a stop; False
        once `active()` goes false (the reply finished on its own).

        `audio` reports whether sound is actually in the air. While the brain merely THINKS,
        nothing is - so the ear stays open and words said then are drafted normally; only the
        span when the voice is sounding treats chunks as its own leak. Without `audio` (the
        terminal path, one-shot lines) the whole watch counts as sounding, which is what it was.
        `script` is the words being spoken - how a chunk heard mid-sound is told apart: its own
        voice arriving back (covered by the script, dropped) or someone talking over it (kept as
        draft, never lost - though only a stop BARK cuts the audio, so a TV sentence can't)."""
        bark = threading.Event()
        self._bark = bark
        self._stop_words = words
        self._script = script
        sounding = False
        try:
            if audio is None:
                self.begin_speaking()
                sounding = True
            while active():
                if audio is not None:
                    now = bool(audio())
                    if now and not sounding:
                        self.begin_speaking()
                        sounding = True
                    elif sounding and not now:
                        self.end_speaking()
                        sounding = False
                if bark.wait(0.05):
                    return True
            return False
        finally:
            self._bark = None
            self._script = None
            self._stop_words = STOP_WORDS
            if sounding:
                self.end_speaking()

    # ---- the window-facing half ----------------------------------------------------------------

    def set_recording(self, recording):
        """The mic button (and the spoken phrases) - arms or disarms and tells the window.

        Disarming mid-sentence keeps that sentence: they said a whole one, pressed mic-off, and the
        words never arrived, because the burst was still buffered waiting for a pause. Turning the
        mic off means "stop listening from here", not "throw away the part you hadn't written down".
        """
        if not recording and self._armed and self._mid_burst:
            self._finish_burst = True
        if not recording and self._speaking:
            self._silenced = True  # cutting it off is not an invitation to auto-listen after
        self._armed = recording
        self._announce_state()

    def is_mid_utterance(self):
        """Are they part-way through saying something right now? The loop asks before speaking up on
        its own, because it once broke in mid-sentence.

        Being ARMED is not the answer: the mic here is a state they leave on for the whole
        conversation, so "is it armed" is yes from the moment they start until they stop, and taking
        that for "they are talking" left Excephalon unable to say anything unprompted ever again.
        But an open burst alone is not the answer either: his natural pauses END bursts, and the
        news offer barged into one of those pauses mid-thought. Mid-utterance is an open burst, or
        an armed mic whose draft gained words moments ago - a thought still being composed. A
        submit or a disarm closes it at once.
        """
        composing = (self._armed and self._last_worded is not None
                     and self._clock() - self._last_worded < DICTATION_LULL)
        return self._mid_burst or composing

    def begin_speaking(self):
        """Excephalon has started talking: stop taking dictation until it's done."""
        self._speaking = True
        self._announce_state()

    def set_auto_listen(self, on):
        """Auto-listening: the mic opens by itself each time Excephalon stops talking, so answering
        back costs nothing at all."""
        self._auto_listen = on

    def end_speaking(self):
        """It has finished (or been cut off) - back to however they had left the mic. The pump is
        told explicitly (`_tail_pending`) rather than left to sample the flag, because a reply can
        begin and end between two mic frames and the tail grace must still happen."""
        self._speaking = False
        self._tail_pending = True
        if self._auto_listen and not self._silenced:
            self._armed = True
        self._silenced = False
        self._announce_state()

    def taking_dictation(self):
        return self._armed and not self._speaking

    def _announce_state(self):
        self._on_state("speaking" if self._speaking else ("recording" if self._armed else "muted"))

    def start(self):
        self._announce_state()  # the window opened before this existed; tell it how the mic stands
        thread = threading.Thread(target=self.pump, daemon=True)
        thread.start()
        return thread

    def pump(self):
        """The forever loop: frames in, draft text / state changes / levels / submits out. Runs on
        its own thread against a real mic; tests run it inline against a finite one.

        A burst is judged by the state it was CAPTURED in, never the state at its closing pause.
        Excephalon's reply used to start a burst whose pause came after end_speaking, and the
        whether-to-draft check looked at the current state - so its own sentence became the user's
        draft, word for word. Every speaking transition now cuts the burst in flight: what came
        before Excephalon opened its mouth is the user's, what its voice made is a bark-check and
        nothing more, and a short grace window after end_speaking still counts as it talking,
        because its last word is in the air (speaker to mic is ~90ms) after the audio call returns.
        """
        floor = NoiseFloor()
        burst = Burst()
        silence = 0
        started = False
        was_speaking = self._speaking
        speech_tail = 0  # frames of grace after end_speaking that are still "it talking"
        for frame in self._mic.frames():
            record(self._recorder, frame)  # to disk first, so a crash can't lose what they said
            if self._stop is not None and self._stop.is_set():
                return
            speaking_now = self._speaking
            if speaking_now and not was_speaking:  # it opened its mouth mid-burst
                if len(burst):  # what they had said so far is theirs - finish it as dictation
                    self._end_burst(burst, heard_while_speaking=speech_tail > 0)
                    burst, silence, started = Burst(), 0, False
                speech_tail = 0
            was_speaking = speaking_now
            if self._tail_pending:  # it just finished a reply - maybe between two frames
                self._tail_pending = False
                if len(burst):  # the burst its voice made is a bark-check, never a draft
                    self._end_burst(burst, heard_while_speaking=True)
                    burst, silence, started = Burst(), 0, False
                speech_tail = SPEAK_TAIL_FRAMES
            entity_sounding = speaking_now or speech_tail > 0
            if speech_tail:
                speech_tail -= 1
            level = rms(frame)
            self._on_level(level if self.taking_dictation() else 0.0)
            speech = floor.is_speech(level)
            if not started:
                if not speech:
                    continue
                started = True
            burst.add(frame, speech=speech, level=level)
            if self._hearing is not None and self._armed and not entity_sounding:
                # Only what they are being heard saying: the room while the mic is off, and the
                # Excephalon's own voice through their speakers, have no business on screen as their words.
                self._hearing.follow(burst)
            silence = 0 if speech else silence + 1
            ended = silence >= self._pause_frames  # they paused: that burst is over
            if ended or self._finish_burst:  # ...or they muted while still mid-sentence
                self._end_burst(burst, heard_while_speaking=entity_sounding)
                burst, silence, started = Burst(), 0, False
            self._mid_burst = started
        if len(burst):  # a finite source ran out mid-burst (a real mic never does)
            self._end_burst(burst, heard_while_speaking=self._speaking or speech_tail > 0)
        self._mid_burst = False

    def _end_burst(self, burst, *, heard_while_speaking):
        """Hand one finished burst to the transcriber - unless there was no word in it. A burst
        with no sustained sound is a tap or a creak, and the model answers those with invented
        words (see carries_speech), so it never gets asked. `heard_while_speaking` is the state
        the burst was captured in - the pump's call, since only it knows when the state flipped."""
        if self._hearing is not None:
            self._hearing.rest()  # the finished sentence is about to land; the live line makes way
        if burst.carries_speech():
            self._absorb(burst.audio(), armed=self._armed or self._finish_burst,
                         speaking=heard_while_speaking, deliberate=burst.sounds_deliberate())
        self._finish_burst = False

    def _absorb(self, audio, *, armed=None, speaking=None, deliberate=False):
        armed = self._armed if armed is None else armed
        speaking = self._speaking if speaking is None else speaking
        # The "um"s and "uh"s go before anything reads the text, so no reader downstream has to
        # know they were ever there.
        text = without_hesitations(self._transcriber.transcribe(audio).strip())
        if not text:
            return
        if self._scores is not None:
            self._scores.note(audio, text)  # measured whatever happens to it below; never acted on
        if self._bark is not None and _is_stop_bark(text, self._stop_words):
            # A bark cuts the turn whenever a watch is up - sounding OR still thinking. The think
            # phase used to be covered only because the whole watch counted as "speaking"; with
            # the ear now open during it, the bark must fire on its own.
            self._bark.set()
            return
        if speaking:  # sound is in the air: what the mic hears now is mostly its own voice
            if (deliberate and self._script is not None and self._armed
                    and not covered_by(text, self._script())
                    and not _is_invented(text, self._terminator)):
                # Words its own script does not contain: someone ELSE is talking over it. Kept as
                # draft - talking over the reply must not mean being unheard - but never a cut:
                # only a stop bark silences the voice, so the TV can pollute a draft box (as it
                # always could when the mic was armed) yet can never kill a reply mid-sentence.
                self._take_dictation(text, deliberate=True)
            return
        if armed:
            self._take_dictation(text, deliberate=deliberate)
        else:
            self._maybe_wake(text)

    def _take_dictation(self, text, *, deliberate=False):
        self._last_worded = self._clock()  # words are landing: he is mid-thought from here
        spoken = canonical(text)
        if ends_with_command(spoken, self._mutes):
            self._draft_before_mute(text, spoken, deliberate=deliberate)
            self.set_recording(False)
            return
        if self._retract_what_he_took_back(spoken):
            return
        without_over = _strip_terminator(text, self._terminator)
        if without_over is None:
            without_over = self._misheard_over(text)
        if without_over is not None:
            # Whatever came before "over" is kept as said. It is NOT run past the invention
            # filter: a chunk carrying the terminator is someone deliberately ending a turn,
            # never something the model made up out of near-silence - which is why _is_invented
            # refuses to call one invented in the first place. Asking it after the terminator
            # had been taken off threw away exactly the answers that filter exists to protect
            # ("yeah, over"), and the submit then found an empty draft box, so saying "over" did
            # nothing at all.
            if without_over:
                self._on_draft(without_over)
            self._on_submit_request()  # "over" still submits - old muscle memory, same meaning
            self.set_recording(False)  # ...and it is the whole gesture: turn handed over, mic down
            return
        if _is_invented(text, self._terminator, deliberate=deliberate):
            return  # Parakeet's hallucinated filler on near-silence, not them
        text = _spoken_formatting(text)
        # A chunk that IS a formatting command comes out as pure whitespace - his natural way to
        # say "paragraph break" is as its own utterance, with a pause either side, and dropping
        # the whitespace-only chunk made exactly that case do nothing at all.
        if text.strip() or "\n" in text:
            self._on_draft(text)
            self._drafted = True  # there is something in the box for a terminator to end

    def _misheard_over(self, text):
        """The chunk minus a trailing word the transcriber wrote where he said the terminator, or
        None when this is not that. Deliberately narrow - see MISHEARD_TERMINATORS."""
        words = text.split()
        if not words or not self._drafted:
            return None
        if words[-1].lower().strip(".,!?;:'\"") not in MISHEARD_TERMINATORS:
            return None
        if len(words) > 1 and not words[-2].endswith((".", "!", "?", "…")):
            return None  # mid-clause ("that's fine, okay") - his word, not the gesture
        return " ".join(words[:-1]).strip()

    def _retract_what_he_took_back(self, spoken):
        """"Scratch that" - rewind, and say it again. True if this chunk was them doing that.

        What it takes back is the words immediately BEFORE it, wherever they are: said on their own
        after a pause ("...the drive work." / "scratch that"), that is the chunk already sitting in
        the box; said in the same breath ("...the drive work, scratch that"), it is those very words,
        which then simply never land. One rule, because it is one gesture. The phrase itself never
        lands either way, and anything they go straight on to say does - taking a sentence back and
        starting the new one is a single thing people say."""
        rest = strip_leading_command(spoken, self._retracts)
        if rest is not None:
            self._on_retract()
            if rest:
                self._on_draft(rest)
            return True
        if ends_with_command(spoken, self._retracts):
            return True  # what they took back is in this chunk; none of it goes in the box
        return False

    def _draft_before_mute(self, text, spoken, *, deliberate=False):
        """They said something and THEN the mute phrase ("add eggs, stop listening") - keep the
        something; the phrase itself never belongs in the draft."""
        for phrase in self._mutes:
            if spoken != phrase and spoken.endswith(" " + phrase):
                kept = text.strip()[: -len(phrase)].rstrip(" ,.;:!?-")
                if kept and not _is_invented(kept, self._terminator, deliberate=deliberate):
                    self._on_draft(kept)
                return

    def _maybe_wake(self, text):
        spoken = canonical(text)
        if not wakes(spoken, self._wakes):
            return  # muted: the room talks, nothing gets through but the wake phrase
        self.set_recording(True)
        rest = strip_leading_command(spoken, self._wakes)
        if rest:  # "hey entity add milk" - the wake phrase carried their first real words
            self._on_draft(rest)
