"""Microphone speech-to-text with a walkie-talkie end-of-turn keyword.

You end a turn by saying a terminator word ("over") - not by going silent, and not on any time
limit. The end is only checked when you actually PAUSE: `listen()` captures once you start
talking, and each time you stop for a moment it transcribes just the new stretch since your last
pause, tacks it onto the running transcript, and looks for a trailing "over". If it's there, the
turn is done (and a cue fires so you see it registered); if not, you just paused mid-thought and
it keeps listening, however long you take. Because each pause only transcribes the newest chunk -
never the whole turn again - the cost of a pause stays flat no matter how long you've been
talking, so a long turn can't slow to a crawl or run the machine out of memory. Saying "over" in
the middle of a sentence doesn't cut you off - there's no pause after it.

What counts as "speech" is judged against the ROOM, not a fixed number. A fixed loudness bar
failed both ways in practice: a quiet mic put the user's voice just under it (deaf), and boosting
the mic put the room's noise just over it (never a pause, also deaf). NoiseFloor keeps a running
measure of the room's quiet level; anything a few times louder is speech. That stays right
whatever the mic's level is.
"""

import re
from collections import deque

import numpy as np

from excephalon.recorder import record

FRAME = 480  # 30 ms at 16 kHz
PAUSE_FRAMES = 17  # ~0.5 s of quiet = you paused, so check whether you said "over"
MIN_VOICED_RUN = 4  # 120 ms - shorter than any syllable, so a burst under it holds no word
DELIBERATE_RUN = 6  # 180 ms - the measured floor of real speech; inventions sat at 1-4 frames
SPEECH_RATIO = 2.5  # this many times the room's quiet level counts as speech
FLOOR_MIN = 0.0008  # the floor never drops below this, so digital silence can't set an absurd bar
FLOOR_ADAPT = 0.1  # how fast the floor tracks quiet frames (EMA step)
RECENT_WINDOW = 100  # ~3 s of levels; their minimum pulls a stale floor back UP (see NoiseFloor)

# Parakeet hallucinates little backchannel words on near-silence - a quiet stretch comes back as
# "Mm-hmm. Yeah. Uh." though they said nothing. A chunk that's ONLY these (and has no terminator) is
# that noise, not a turn, so it's dropped.
_BACKCHANNEL = {
    "mm", "mmm", "mmhmm", "mhm", "hmm", "hm", "uh", "uhh", "um", "umm",
    "uhhuh", "yeah", "yep", "yup", "huh", "ah", "oh", "er", "erm",
    "okay", "ok", "kay", "alright", "aright",  # Parakeet fills their pauses with these too
}

# Not every invention is one word. Handed a stretch it can find no speech in, Parakeet answers with
# the likeliest thing anyone ever says, and "Thank you." is the one that kept landing in the draft
# box - five times in a replayed 20-minute session, not once actually said. The whole chunk has to BE
# the phrase; "thank you for doing that" is a sentence and stays.
_STOCK_PHRASES = {"thank you", "thanks"}

# Any of these, said aloud while Excephalon is talking, cuts it off (see MicSTT.catch_stop).
STOP_WORDS = ("stop", "shut up", "quiet", "enough", "wait")

# What fraction of the heard words must appear in the script before the chunk counts as the
# Excephalon's own voice arriving back through the mic. The measured leak (two captured incidents)
# transcribed near-verbatim - coverage ~1.0 - while someone else's words against an unrelated
# sentence sit near 0, so the middle is a wide gap, not a fitted bar.
_COVERED = 0.5


def covered_by(text, script):
    """Whether `text`, heard while Excephalon was speaking, is Excephalon's own voice - judged by
    its words being the words of `script`, the text actually being spoken.

    Containment is checked against the script with its spaces squashed out, because the
    transcriber splits and joins compounds freely ("dropdown" -> "drop down") and a word-set match
    would turn that drift into "someone else is talking". The failure direction is deliberate:
    a word wrongly counted as covered leans toward "its own voice", which is today's behavior -
    never toward eating something new."""
    words = re.findall(r"[a-z']+", text.lower())
    if not words:
        return True  # nothing heard is nothing new; treat as its own noise
    squashed = re.sub(r"[^a-z']", "", script.lower())
    if not squashed:
        return False
    hits = sum(1 for word in words if word in squashed)
    return hits / len(words) >= _COVERED


def rms(frame):
    frame = np.asarray(frame, dtype=np.float32)
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame * frame)))


def _is_stop_bark(text, words):
    """A deliberate stop is a BARK: one short burst that is essentially just the stop word
    ("stop", "okay stop stop"). A stop word buried in a flowing sentence is the TV or the room -
    matching those silently killed Excephalon's own speech mid-utterance, which read to the user as
    it never speaking at all. Whole words only, and no more than a few of them."""
    said = [w for w in re.findall(r"[a-z]+", text.lower())]
    if not said or len(said) > 3:
        return False
    canonical = " ".join(said)
    return any(re.search(rf"\b{word}\b", canonical) for word in words)


def carries_speech(voiced, min_run=MIN_VOICED_RUN):
    """Did this burst hold one UNBROKEN stretch of sound long enough to be a syllable?

    `voiced` is the per-frame speech/not-speech verdict for one burst. The energy gate only decides
    where a burst starts and ends; it says nothing about whether a word is inside it. A single tap,
    creak or breath clears the bar, and the burst then has to wait out a whole pause before it can
    end - so the model gets a second of near silence and, rather than nothing, returns the likeliest
    thing anyone ever says. Replaying their own sessions, that was some 90 chunks per 20 minutes, all
    of them invented: "Okay.", "Yeah.", "Thank you."

    Continuity is what separates the two, not loudness: speech carries syllables, ~150 ms of sound
    that does not let up, while noise hovering near the bar sputters - isolated frames scattered
    through silence. Across two replayed sessions nothing actually spoken (their voice, Excephalon's
    own through the room) ran under 6 frames, and the invented chunks sat at 1-4.
    """
    run = 0
    for speech in voiced:
        run = run + 1 if speech else 0
        if run >= min_run:
            return True
    return False


class Burst:
    """One stretch of sound between pauses, and what was true of it frame by frame.

    The frames are what gets transcribed; the rest is what decides whether transcribing it is worth
    doing at all. Both pumps keep exactly this bookkeeping, so it lives here once."""

    def __init__(self):
        self.frames = []
        self._voiced = []
        self._heard = []

    def __len__(self):
        return len(self.frames)

    def add(self, frame, *, speech, level):
        self.frames.append(frame)
        self._voiced.append(speech)
        self._heard.append(level)

    def audio(self):
        return np.concatenate(self.frames)

    def carries_speech(self):
        return carries_speech(self._voiced)

    def sounds_deliberate(self):
        """Voice that ran long enough to be someone actually talking, not a flicker the model will
        invent words over. The measured line (two replayed sessions): everything actually spoken
        ran 6+ voiced frames; every invented chunk sat at 1-4. A burst past that line is trusted
        even when its words are on the stock-phrase list - his real "thank you" sounds like this."""
        return carries_speech(self._voiced, min_run=DELIBERATE_RUN)


def _filler_segments(text):
    """How this chunk reads as punctuation-split segments, and whether every one is pure filler -
    a backchannel word or a stock phrase and nothing else."""
    segments = [seg for seg in re.split(r"[.!?,;]+", text) if seg.strip()]
    def is_filler(segment):
        words = [w for w in (re.sub(r"[^a-z]", "", part.lower()) for part in segment.split()) if w]
        return bool(words) and (all(w in _BACKCHANNEL for w in words)
                                or " ".join(words) in _STOCK_PHRASES)
    return len(segments), all(is_filler(seg) for seg in segments)


def _is_invented(text, terminator, *, deliberate=False):
    """True if the chunk is nothing the user said - filler the model hears in near-silence, or its
    stock answer to a stretch with no words in it. Never true of a chunk carrying the terminator, so
    a real 'yeah, over' still ends the turn.

    `deliberate` is the burst's own testimony (see Burst.sounds_deliberate): sound that ran long
    enough to be someone actually talking. A REAL "thank you" was being eaten by this filter -
    "it's more important for when I'm trying to actually say it that it can hear me" - and what
    separates his from the phantoms is the voice under it, which the text alone cannot show.
    But deliberate SOUND is not deliberate SPEECH: music and scraped chairs run past the line
    too, and the bypass waved through whole STRINGS of fillers invented over them ("Thank you.
    Mm-hmm. Yeah. Thank you." came back). Nobody's real turn is three-plus filler phrases and
    nothing else, so those read as invented whatever the sound under them; one or two ("yeah,
    thank you") stay his."""
    words = [w for w in (re.sub(r"[^a-z]", "", part.lower()) for part in text.split()) if w]
    if not words or terminator in words:
        return False
    segments, all_filler = _filler_segments(text)
    if deliberate:
        return all_filler and segments >= 3
    return all(w in _BACKCHANNEL for w in words) or " ".join(words) in _STOCK_PHRASES


class NoiseFloor:
    """The room's running quiet level, learned from the frames that aren't speech.

    The first frame calibrates it (never counted as speech - at that instant there's nothing to
    compare against). After that, a frame is speech if it's SPEECH_RATIO times the floor; every
    non-speech frame nudges the floor toward its level, so the bar follows the room - up when a fan
    kicks in, down when things settle - and never assumes anything about the mic's absolute level.

    One trap that adapting only on quiet frames sets: deep silence ratchets the floor to its
    minimum, and then the room's ordinary steady tone reads as endless "speech" - the floor can
    never climb back up, because "speech" frames don't feed it. That deafness is real (a whole
    session hung inside one turn, its pause never firing). So the QUIETEST level seen over the last
    few seconds also drags the floor up - gradually, at the same EMA pace it falls, never a jump:
    real speech always lets up somewhere in a few seconds, so its dips keep the minimum honest,
    while a tone that never once let up for that long isn't someone talking, it's the room.
    """

    def __init__(self, ratio=SPEECH_RATIO, adapt=FLOOR_ADAPT, floor_min=FLOOR_MIN, window=RECENT_WINDOW):
        self._ratio = ratio
        self._adapt = adapt
        self._floor_min = floor_min
        self._recent = deque(maxlen=window)  # every recent level, speech or not
        self._level = None

    def is_speech(self, level):
        if self._level is None:
            self._level = max(level, self._floor_min)
            return False
        self._recent.append(level)
        if len(self._recent) == self._recent.maxlen:
            quietest = min(self._recent)
            if quietest > self._level:  # even the window's quietest moment beats the floor: the
                # room itself got louder - drift up toward its quietest, at the usual EMA pace
                self._level = self._level + (quietest - self._level) * self._adapt
        if level >= self._level * self._ratio:
            return True
        self._level = max(self._level + (level - self._level) * self._adapt, self._floor_min)
        return False


def _strip_terminator(text, terminator):
    """Return the text minus a trailing terminator word, or None if it isn't there."""
    words = text.split()
    if words and words[-1].lower().strip(".,!?;:'\"") == terminator:
        return " ".join(words[:-1]).strip()
    return None


class MicSTT:
    def __init__(
        self,
        transcriber,
        mic,
        *,
        terminator="over",
        threshold=None,
        pause_frames=PAUSE_FRAMES,
        stop=None,
        cue=None,
        recorder=None,
        interrupt=None,
    ):
        self._transcriber = transcriber
        self._mic = mic
        self._terminator = terminator
        # A fixed threshold is for tests and odd setups; live use adapts to the room instead.
        self._is_speech = (lambda level: level >= threshold) if threshold is not None else NoiseFloor().is_speech
        self._pause_frames = pause_frames
        self._stop = stop
        self._cue = cue
        self._recorder = recorder
        self._interrupt = interrupt
        # Set once a turn ends on the terminator - even a bare "over" with nothing before it. Lets the
        # caller acknowledge an otherwise-empty turn instead of silently ignoring it (they'd repeat "over").
        self.caught_terminator = False

    def _flush_mic(self):
        """Drop whatever the background mic buffered between turns (Excephalon's own reply, room
        noise), so listening starts from now. Mics without a flush() - tests, console - just skip it."""
        flush = getattr(self._mic, "flush", None)
        if flush is not None:
            flush()

    def listen(self):
        self.caught_terminator = False  # about this turn only; forget the last one
        self._flush_mic()
        segments = []  # transcribed text so far, one entry per pause-delimited chunk
        burst = Burst()  # frames since the last pause - only this chunk gets transcribed
        silence_run = 0
        started = False
        for frame in self._mic.frames():
            record(self._recorder, frame)  # to disk first, so a crash can't lose what they said
            if self._stop is not None and self._stop.is_set():
                return ""  # a quit was requested while we were waiting for speech
            level = rms(frame)
            speech = self._is_speech(level)
            if not started:
                if self._interrupt is not None and self._interrupt.is_set():
                    return ""  # a lull, and Excephalon has something to say - yield so it can
                if speech:
                    started = True
                else:
                    continue
            burst.add(frame, speech=speech, level=level)
            silence_run = 0 if speech else silence_run + 1
            if silence_run == self._pause_frames:  # you paused - did you say "over"?
                done = self._absorb(segments, burst)
                burst = Burst()  # this chunk is now text; the next one starts fresh
                if done is not None:
                    return done
        if len(burst):  # a finite source ran out mid-chunk (a real mic never does)
            done = self._absorb(segments, burst)
            if done is not None:
                return done
        return " ".join(segments).strip()

    def _absorb(self, segments, burst):
        """Transcribe one pause-delimited chunk, append it to the running transcript, and return
        the finished turn if the transcript now ends with the terminator - else None to keep
        listening. Only this chunk is transcribed, never the whole turn, so the work per pause
        stays flat however long the turn runs.

        A chunk with no sustained sound in it holds no word (see carries_speech), so it isn't
        transcribed at all - the model would answer it with something invented."""
        if not burst.carries_speech():
            return None
        text = self._transcriber.transcribe(burst.audio()).strip()
        if text and not _is_invented(text, self._terminator, deliberate=burst.sounds_deliberate()):
            segments.append(text)  # drop pure "mm-hmm/yeah" hallucinations on near-silence
        without_terminator = _strip_terminator(" ".join(segments), self._terminator)
        if without_terminator is not None:
            self.caught_terminator = True
            if self._cue is not None:
                self._cue()
            return without_terminator
        return None

    def catch_stop(self, active, words=STOP_WORDS):
        """While `active()` is true - i.e. Excephalon is talking - listen for them barking a stop
        word and return True the moment one lands, so the caller can cut the voice off. Returns
        False when `active()` goes false (the reply finished on its own). Bark-only (see
        _is_stop_bark), so neither Excephalon's own voice bleeding into the mic nor a TV sentence
        that happens to contain "wait" can silence the reply."""
        floor = NoiseFloor()  # its own room-level, independent of a listen() in progress
        self._flush_mic()  # watch only what they say over the reply, not audio buffered before it
        chunk = []
        silence = 0
        started = False
        for frame in self._mic.frames():
            if not active():
                return False
            speech = floor.is_speech(rms(frame))
            if not started:
                if speech:
                    started = True
                else:
                    continue
            chunk.append(frame)
            silence = 0 if speech else silence + 1
            if silence >= self._pause_frames:  # a burst ended - was it a stop bark?
                if _is_stop_bark(self._transcriber.transcribe(np.concatenate(chunk)), words):
                    return True
                chunk, started, silence = [], False, 0  # not a stop; keep watching
        return False
