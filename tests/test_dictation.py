import threading
import time

import numpy as np

from excephalon.dictation import Dictation
from excephalon.hearing import Hearing

LOUD = 0.05
QUIET = 0.001


def _sp(level=LOUD):
    return np.full(480, level, dtype=np.float32)


def _sil():
    return _sp(QUIET)


class FakeMic:
    """A scripted mic. A callable in the script is an event mid-stream - Excephalon starting or
    finishing a reply while the pump is running - executed in sequence, never yielded."""

    def __init__(self, frames):
        self._frames = list(frames)

    def frames(self):
        for item in self._frames:
            if callable(item):
                item()
            else:
                yield item


class FakeTranscriber:
    """Hands out the scripted texts, one per transcribed chunk."""

    def __init__(self, *texts):
        self._texts = list(texts)
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self._texts.pop(0) if self._texts else ""


class Ears:
    """Collects everything the dictation reports, the way the window would."""

    def __init__(self):
        self.drafted = []
        self.states = []
        self.levels = []
        self.submits = 0
        self.retracted = 0

    def kwargs(self):
        return dict(
            on_draft=self.drafted.append,
            on_state=self.states.append,
            on_level=self.levels.append,
            on_submit_request=self._submit,
            on_retract=self._retract,
        )

    def _submit(self):
        self.submits += 1

    def _retract(self):
        self.retracted += 1


def _burst_then_pause():
    # Quiet first: the floor calibrates on the opening frame, so a stream that STARTS loud would
    # set the bar at voice level and hear nothing at all.
    return [_sil()] * 2 + [_sp()] * 4 + [_sil()] * 4


def test_speech_while_recording_lands_in_the_draft():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("add eggs to the list"), FakeMic(_burst_then_pause()),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["add eggs to the list"]
    assert ears.submits == 0  # nothing submitted - the draft just accumulates


def test_every_worded_chunk_is_handed_to_the_scorekeeper():
    # The against-his-voice score is measured for everything the mic turns into words - measured
    # only: the pipeline's decisions are untouched, and the scores go to a log the dropping
    # threshold will one day be chosen from.
    class KeeperSpy:
        def __init__(self):
            self.noted = []

        def note(self, audio, text):
            self.noted.append((len(audio), text))

    keeper = KeeperSpy()
    ears = Ears()
    dictation = Dictation(FakeTranscriber("add eggs to the list"), FakeMic(_burst_then_pause()),
                          pause_frames=3, scorekeeper=keeper, **ears.kwargs())

    dictation.pump()

    [(frames, text)] = keeper.noted
    assert frames > 0
    assert text == "add eggs to the list"
    assert ears.drafted == ["add eggs to the list"]  # noting changed nothing about the draft


class MicWithAWorkerBeside:
    """A mic whose frames arrive while the hearing worker reads the snapshots they leave - which is
    what its own thread does in the app, without a thread to go wrong in a test."""

    def __init__(self, frames, hearing):
        self._frames = list(frames)
        self._hearing = hearing

    def frames(self):
        for frame in self._frames:
            yield frame
            self._hearing.step()


def test_words_reach_the_window_while_they_are_still_being_said():
    # The complaint itself: "it doesn't actually print out what it's hearing me say until I stop
    # speaking". The burst is only transcribed once a pause ends it, so the wait they saw was the
    # pause. Now the burst so far is read as it grows, and the settled words go up as they settle.
    ears = Ears()
    heard = []
    hearing = Hearing(
        FakeTranscriber("Pick up the", "Pick up the drive", "Pick up the drive work",
                        "Pick up the drive work."),
        heard.append, every=2)
    frames = [_sil()] * 2 + [_sp()] * 6 + [_sil()] * 4
    dictation = Dictation(FakeTranscriber("Pick up the drive work."),
                          MicWithAWorkerBeside(frames, hearing), pause_frames=3,
                          hearing=hearing, **ears.kwargs())

    dictation.pump()

    assert heard == ["Pick up the", "Pick up the drive", "Pick up the drive work.", ""]
    # ...and the finished sentence still lands in the draft box, which the live line makes way for.
    assert ears.drafted == ["Pick up the drive work."]


def test_the_live_line_never_puts_the_entitys_own_voice_up_as_the_users_words():
    # Their draft box once opened with "I do for you" - the tail of Excephalon's own greeting, heard back
    # through their speakers. A line that printed as it listened would put that on screen a word at a
    # time, and faster.
    ears = Ears()
    heard = []
    hearing = Hearing(FakeTranscriber("I'm ready. What can", "I'm ready. What can I do"),
                      heard.append, every=2)
    frames = [_sil()] * 2 + [_sp()] * 6 + [_sil()] * 4
    dictation = Dictation(FakeTranscriber("I'm ready. What can I do for you?"),
                          MicWithAWorkerBeside(frames, hearing), pause_frames=3,
                          hearing=hearing, **ears.kwargs())
    dictation.begin_speaking()

    dictation.pump()

    assert heard == []
    assert ears.drafted == []


def test_stop_listening_mutes_and_keeps_the_words_before_it():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("add eggs, stop listening", "invisible while muted"),
                          FakeMic(_burst_then_pause() * 2), pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["add eggs"]  # the phrase (and its comma) never lands in the draft
    assert ears.states[-1] == "muted"  # and the mic went off


def test_scratch_that_takes_back_what_he_said_before_it_and_never_lands_in_the_draft():
    # The other half of what they asked for: a spoken way to rewind and say it again. They says a
    # sentence, sees it come out wrong, and takes it back without reaching for the keyboard.
    ears = Ears()
    dictation = Dictation(
        FakeTranscriber("pick up the drive subfolder work", "scratch that",
                        "pick up the Notecraft work"),
        FakeMic(_burst_then_pause() * 3), pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["pick up the drive subfolder work", "pick up the Notecraft work"]
    assert ears.retracted == 1  # and the first of those is taken back off the box between them


def test_scratch_that_in_the_same_breath_takes_back_the_words_in_that_breath():
    # They catches it before they have even paused. What they are taking back is right there in the chunk,
    # so none of it lands - and the sentence already in the box, which they did not object to, stays.
    ears = Ears()
    dictation = Dictation(
        FakeTranscriber("pick up the Notecraft work",
                        "and then merge to main, scratch that",
                        "scratch that, ask me first"),
        FakeMic(_burst_then_pause() * 3), pause_frames=3, **ears.kwargs())

    dictation.pump()

    # The last one is the whole gesture in one breath: take back the sentence before, say the new
    # one. Taking a sentence back and starting the next is a single thing people say.
    assert ears.drafted == ["pick up the Notecraft work", "ask me first"]
    assert ears.retracted == 1


def test_muted_speech_is_dropped_until_hey_entity():
    ears = Ears()
    dictation = Dictation(
        FakeTranscriber("just the TV talking", "hey entity add milk", "and bread"),
        FakeMic(_burst_then_pause() * 3), pause_frames=3, muted=True, **ears.kwargs(),
    )

    dictation.pump()

    assert ears.drafted == ["add milk", "and bread"]  # the wake phrase carried its first words
    assert ears.states[-1] == "recording"


def test_over_still_submits_for_the_old_muscle_memory():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("send the report over"),
                          FakeMic(_burst_then_pause()), pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["send the report"]
    assert ears.submits == 1


def test_over_carries_a_short_answer_through_instead_of_reading_it_as_filler():
    # "Yeah, over" is the exact case the backchannel filter is written to let through - it refuses
    # to call anything filler if the terminator is in it. Stripping the terminator FIRST and then
    # asking took that protection away: the answer was dropped, the submit found an empty draft
    # box, and saying "over" did nothing whatsoever. Half their answers are one of these words.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("Yeah, over."), FakeMic(_burst_then_pause()),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["Yeah,"]
    assert ears.submits == 1


def test_over_ends_the_recording_as_well_as_submitting():
    # Both halves of what they asked for: "over" is the whole gesture for "I'm done talking", so it
    # hands the turn over AND puts the mic down, rather than leaving it live on the room.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("send the report over"), FakeMic(_burst_then_pause()),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.submits == 1
    assert ears.states[-1] == "muted"


def test_the_level_meter_sees_the_mic_only_while_recording():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(""), FakeMic([_sp(0.04), _sp(0.04)]),
                          pause_frames=3, muted=True, **ears.kwargs())

    dictation.pump()

    assert ears.levels == [0.0, 0.0]  # muted: the meter shows nothing, whatever the room does

    ears2 = Ears()
    Dictation(FakeTranscriber(""), FakeMic([_sp(0.04)]), pause_frames=3, **ears2.kwargs()).pump()

    assert ears2.levels and ears2.levels[0] > 0.01  # recording: the real level


def test_a_burst_with_no_sustained_sound_is_never_even_transcribed():
    # Replayed from their own session audio: a single tap or creak clears the speech bar, the burst
    # then has to wait out a whole pause before it ends, and Parakeet - handed a second of near
    # silence - answers with the likeliest thing anyone ever says ("Thank you.", "Okay."). Some 90
    # times in 20 minutes. Nothing a person says is that brief, so the burst never goes to the model.
    ears = Ears()
    transcriber = FakeTranscriber("Thank you.")
    dictation = Dictation(transcriber, FakeMic([_sil()] * 2 + [_sp()] + [_sil()] * 4),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == []
    assert transcriber.calls == 0  # not transcribed-then-dropped: never asked in the first place


def test_hallucinated_backchannel_chunks_stay_out_of_the_draft():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("Mm-hmm. Yeah."), FakeMic(_burst_then_pause()),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == []


def test_the_button_toggle_flips_state_and_reports_it():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), **ears.kwargs())

    dictation.set_recording(False)
    dictation.set_recording(True)

    assert ears.states == ["muted", "recording"]


def test_listen_hands_back_what_the_window_submits():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), **ears.kwargs())
    heard = {}

    def listener():
        heard["text"] = dictation.listen()

    thread = threading.Thread(target=listener)
    thread.start()
    dictation.submit("the edited draft, as they corrected it")
    thread.join(2.0)

    assert heard["text"] == "the edited draft, as they corrected it"


def test_listen_yields_empty_when_interrupted_so_agent_news_can_speak():
    interrupt = threading.Event()
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), interrupt=interrupt, **ears.kwargs())
    heard = {}

    def listener():
        heard["text"] = dictation.listen()

    thread = threading.Thread(target=listener)
    thread.start()
    interrupt.set()
    thread.join(2.0)

    assert heard["text"] == ""  # a lull broken for the outbox, not a real turn


def test_listen_does_not_break_for_news_while_they_are_mid_sentence():
    # Yielded mid-sentence, the delivery pass found him talking, deferred, and had no way back
    # until his next words - so the yield waits for an actual lull. His submission still lands:
    # a standing news flag must never eat a turn he is composing.
    interrupt = threading.Event()
    interrupt.set()
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), interrupt=interrupt, **ears.kwargs())
    dictation.is_mid_utterance = lambda: True
    heard = {}

    def listener():
        heard["text"] = dictation.listen()

    thread = threading.Thread(target=listener)
    thread.start()
    dictation.submit("still my turn")
    thread.join(2.0)

    assert heard["text"] == "still my turn"


def test_a_stop_event_ends_the_pump_mid_stream():
    stop = threading.Event()
    stop.set()
    ears = Ears()
    dictation = Dictation(FakeTranscriber("never"), FakeMic([_sp()] * 50), stop=stop,
                          pause_frames=3, **ears.kwargs())

    dictation.pump()  # returns promptly instead of consuming the stream

    assert ears.drafted == []


def test_catch_stop_hears_a_bark_and_keeps_it_out_of_the_draft():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("stop"), FakeMic(_burst_then_pause() + [_sil()] * 20),
                          pause_frames=3, **ears.kwargs())
    caught = {}

    def watcher():
        caught["stopped"] = dictation.catch_stop(lambda: caught.get("stopped") is None)

    thread = threading.Thread(target=watcher, daemon=True)  # daemon: a hang can't wedge the suite
    thread.start()
    deadline = time.monotonic() + 2.0
    while dictation._bark is None and time.monotonic() < deadline:
        time.sleep(0.005)  # the pump must not outrun the watcher installing its bark event
    dictation.pump()
    thread.join(2.0)

    assert caught.get("stopped") is True  # the bark cut the voice
    assert ears.drafted == []  # and never became draft text


def test_every_frame_reaches_the_recorder_even_while_muted():
    # The crash-proof audio capture must not depend on the mic state - their words are only
    # recoverable if they were written before anything else happened to them.
    written = []

    class FakeRecorder:
        def write(self, frame):
            written.append(frame)

    ears = Ears()
    dictation = Dictation(FakeTranscriber(""), FakeMic([_sil(), _sp(), _sil()]),
                          pause_frames=3, muted=True, recorder=FakeRecorder(), **ears.kwargs())

    dictation.pump()

    assert len(written) == 3


def test_a_recorder_that_fails_never_deafens_the_pump():
    # The recording is a safety net, not the ear. When the WAV hit its 4 GiB ceiling the write
    # raised inside the pump loop, the pump thread died, and Excephalon went silently deaf mid-
    # session with the window still saying it was recording. A failed recording costs the net.
    ears = Ears()

    class BrokenRecorder:
        def __init__(self):
            self.tried = 0

        def write(self, frame):
            self.tried += 1
            raise OSError("the disk said no")

    recorder = BrokenRecorder()
    frames = _burst_then_pause()
    dictation = Dictation(FakeTranscriber("add eggs to the list"), FakeMic(frames),
                          pause_frames=3, recorder=recorder, **ears.kwargs())

    dictation.pump()

    assert recorder.tried == len(frames)             # offered every frame, never switched off
    assert ears.drafted == ["add eggs to the list"]  # and the words still reached him


def _speaking_scripted(texts, script, **kwargs):
    """A Dictation whose mic script can reference the dictation itself (begin/end_speaking events
    mid-stream) - resolved through a late-bound holder, since the mic exists before it does."""
    holder = {}
    frames = [item if not isinstance(item, str) else
              (lambda name=item: getattr(holder["dictation"], name)())
              for item in script]
    dictation = Dictation(FakeTranscriber(*texts), FakeMic(frames), pause_frames=3, **kwargs)
    holder["dictation"] = dictation
    return dictation


def test_its_voice_is_not_drafted_even_when_the_burst_outlives_the_reply():
    # THE LEAK he hit live: Excephalon's reply started a burst, the burst's closing pause came after
    # end_speaking - and the whether-to-draft decision looked at the CURRENT state, so its own
    # sentence became his draft: "you said: Here, ready to go. That doesn't really sound like...".
    # A burst is judged by the state it was captured in, not the state at its closing pause.
    ears = Ears()
    dictation = _speaking_scripted(
        ["Here, ready to go."],
        [_sil()] * 2 + ["begin_speaking"] + [_sp()] * 6 + ["end_speaking"] + [_sil()] * 6,
        **ears.kwargs())

    dictation.pump()

    assert ears.drafted == []  # its words never became his


def test_the_tail_of_its_voice_just_after_it_finishes_is_not_drafted_either():
    # Speaker to mic is ~90ms on this desk, and the audio stream drains a beat after end_speaking -
    # so the last word of a reply lands in a mic that has just been handed back. A short grace
    # window after end_speaking is still "it talking".
    ears = Ears()
    dictation = _speaking_scripted(
        ["go."],
        [_sil()] * 2 + ["begin_speaking", "end_speaking"] + [_sp()] * 5 + [_sil()] * 6,
        **ears.kwargs())

    dictation.pump()

    assert ears.drafted == []


def test_what_he_says_after_the_reply_still_lands():
    # The cut must not eat HIM: once the grace window passes, the next burst is his again.
    ears = Ears()
    dictation = _speaking_scripted(
        ["Here, ready to go.", "sounds good"],
        [_sil()] * 2 + ["begin_speaking"] + [_sp()] * 4 + ["end_speaking"]
        + [_sil()] * 14 + [_sp()] * 4 + [_sil()] * 6,
        **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["sounds good"]


def test_his_words_from_before_the_reply_are_kept_when_it_starts_talking():
    # He was mid-sentence when Excephalon opened its mouth (news at a lull): what he had said is
    # his, finished as dictation - only what comes after is Excephalon's sound.
    ears = Ears()
    dictation = _speaking_scripted(
        ["as I was saying", "Heads up from the fixer agent."],
        [_sil()] * 2 + [_sp()] * 4 + ["begin_speaking"] + [_sp()] * 4 + ["end_speaking"]
        + [_sil()] * 6,
        **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["as I was saying"]


def test_nothing_is_drafted_while_the_entity_is_speaking():
    # Their draft box opened with "I do for you" - the tail of Excephalon's own spoken greeting, heard
    # through their speakers. Its own voice must never become their words.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("I'm ready. What can I do for you?"),
                          FakeMic(_burst_then_pause()), pause_frames=3, **ears.kwargs())
    dictation.begin_speaking()

    dictation.pump()

    assert ears.drafted == []
    assert ears.states[-1] == "speaking"  # and the window can say so on its button
    assert ears.levels[-1] == 0.0  # the meter shows nothing: it isn't listening to them


def test_when_it_stops_speaking_the_mic_returns_to_how_he_left_it():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), **ears.kwargs())

    dictation.begin_speaking()
    dictation.end_speaking()

    assert ears.states == ["speaking", "recording"]  # they were recording before, so they still are


def test_cutting_it_off_leaves_the_mic_off_rather_than_recording_the_next_breath():
    # "stopping shouldn't immediately turn on record".
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), **ears.kwargs())

    dictation.begin_speaking()
    dictation.set_recording(False)  # what the button does when it's showing STOP
    dictation.end_speaking()

    assert ears.states[-1] == "muted"


def test_auto_listening_opens_the_mic_when_it_finishes_speaking():
    # "an auto listening mode, where it will default to turning the mic on when it finishes
    # speaking" - so answering back costs nothing, not even the button.
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), muted=True, **ears.kwargs())
    dictation.set_auto_listen(True)

    dictation.begin_speaking()
    dictation.end_speaking()

    assert ears.states[-1] == "recording"


def test_auto_listening_leaves_the_mic_he_used_to_cut_it_off_alone():
    # They silenced it mid-sentence: that is not an invitation to record their next breath, whatever
    # the mode says. The button and the reply ending race each other, so both orders have to hold.
    for silence_first in (True, False):
        ears = Ears()
        dictation = Dictation(FakeTranscriber(), FakeMic([]), **ears.kwargs())
        dictation.set_auto_listen(True)
        dictation.begin_speaking()

        if silence_first:
            dictation.set_recording(False)  # what the button does while it's showing STOP
            dictation.end_speaking()
        else:
            dictation.end_speaking()
            dictation.set_recording(False)

        assert ears.states[-1] == "muted"


def test_a_muted_mic_stays_muted_through_a_reply():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), muted=True, **ears.kwargs())

    dictation.begin_speaking()
    dictation.end_speaking()

    assert ears.states[-1] == "muted"


def test_starting_the_pump_announces_the_state_it_was_built_in():
    # The window opens before the mic exists, so it has to be told - otherwise a mic that starts
    # off is drawn as listening until something happens to change it.
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), muted=True, **ears.kwargs())

    dictation.start().join(2.0)

    assert ears.states[0] == "muted"


def test_turning_the_mic_off_keeps_the_sentence_he_had_just_finished_saying():
    # They spoke a whole sentence, then hit mic-off, and the words never appeared: the burst was
    # still buffered, waiting for a pause that muting made irrelevant. Muting is not "forget the
    # part you hadn't transcribed yet".
    held = []

    class MicHePressesMuteDuring:
        """They are mid-sentence when they reach for the button - the burst has started and no pause
        has ended it yet."""

        def frames(self):
            for index, frame in enumerate([_sil()] * 2 + [_sp()] * 6):
                if index == 5:
                    held[0].set_recording(False)
                yield frame

    ears = Ears()
    dictation = Dictation(FakeTranscriber("the whole sentence I just said"),
                          MicHePressesMuteDuring(), pause_frames=3, **ears.kwargs())
    held.append(dictation)

    dictation.pump()

    assert ears.drafted == ["the whole sentence I just said"]
    assert ears.states[-1] == "muted"  # and it did go quiet, as they asked


def test_it_reports_whether_they_are_part_way_through_a_sentence():
    # The loop asks this before ever speaking up on its own. Being ARMED must not read as talking:
    # they leave the mic armed for a whole conversation, and taking that for "they are speaking" left
    # Excephalon unable to say anything unprompted for the entire session.
    ears = Ears()
    held = []
    seen = []

    class MicThatWatches:
        def frames(self):
            for frame in _burst_then_pause():
                seen.append(held[0].is_mid_utterance())
                yield frame

    dictation = Dictation(FakeTranscriber("a sentence"), MicThatWatches(),
                          pause_frames=3, **ears.kwargs())
    held.append(dictation)

    assert dictation.is_mid_utterance() is False  # armed from the start, but they haven't spoken yet

    dictation.pump()

    assert True in seen  # it did say so while a burst was still in the air
    # A pause no longer reads as done - his pauses are where the news offer once barged in - but
    # handing the turn over does: the submit closes the thought along with the mic.
    assert dictation.is_mid_utterance() is True
    dictation.submit("a sentence")
    assert dictation.is_mid_utterance() is False


def test_the_sounds_he_makes_while_thinking_never_reach_the_draft():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("Um, so uh the drive link is wrong"),
                          FakeMic(_burst_then_pause()), pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["So the drive link is wrong"]


def test_a_thank_you_he_really_said_lands_in_the_draft():
    # "it apparently can't hear me when I say 'thank you'... it's more important for when I'm
    # trying to actually say it that it can hear me." The stock-phrase filter exists for phantom
    # thank-yous invented over near-silence - but his real one carries real voice: the measured
    # line is that nothing actually spoken ran under 6 voiced frames and inventions sat at 1-4.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("Thank you."),
                          FakeMic([_sil()] * 2 + [_sp()] * 8 + [_sil()] * 4),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["Thank you."]


def test_a_phantom_thank_you_over_a_weak_flicker_is_still_dropped():
    # The same words over a marginal flicker of sound - the shape every invented chunk had - stay
    # filtered, so ambient recording doesn't fill the draft with courtesies nobody said.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("Thank you."),
                          FakeMic([_sil()] * 2 + [_sp()] * 4 + [_sil()] * 4),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == []


def _watched(dictation, caught, **kwargs):
    """The stop-watcher, run the way conversation runs it: on a thread beside the pump, released
    by flipping caught["done"] once the pump has drained the scripted mic."""
    def watcher():
        caught["stopped"] = dictation.catch_stop(lambda: caught.get("done") is None, **kwargs)

    thread = threading.Thread(target=watcher, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2.0
    while dictation._bark is None and time.monotonic() < deadline:
        time.sleep(0.005)  # the pump must not outrun the watcher installing its bark event
    return thread


def test_the_ear_stays_open_while_the_entity_merely_thinks():
    # Full duplex, first half. Between handing a turn over and the first sound there is nothing
    # coming out of the speakers - no leak to fear - so words said then are the user's, kept as
    # draft. They used to become bark-checks and vanish for the whole think.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("and one more thing"),
                          FakeMic(_burst_then_pause() + [_sil()] * 8),
                          pause_frames=3, **ears.kwargs())
    caught = {}
    thread = _watched(dictation, caught, script=lambda: "", audio=lambda: False)

    dictation.pump()
    caught["done"] = True
    thread.join(2.0)

    assert caught.get("stopped") is False
    assert ears.drafted == ["and one more thing"]  # heard, kept - the reply had made no sound yet


def test_its_own_voice_while_sounding_is_dropped_not_drafted():
    # The leak's measured mark: it transcribes near-verbatim. Words the script covers are its own
    # voice arriving back through the mic - never draft text.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("the dropdown is reordered"),
                          FakeMic([_sil()] * 2 + [_sp()] * 7 + [_sil()] * 8),
                          pause_frames=3, **ears.kwargs())
    caught = {}
    thread = _watched(dictation, caught,
                      script=lambda: "The dropdown is reordered and live at localhost.",
                      audio=lambda: True)

    dictation.pump()
    caught["done"] = True
    thread.join(2.0)

    assert caught.get("stopped") is False
    assert ears.drafted == []


def test_talking_over_the_reply_is_heard_and_kept_but_does_not_cut_it():
    # Full duplex, second half: their own words over its voice land in the draft - talking over
    # the reply must not mean being unheard. And no cut: only a stop bark silences the voice, so
    # the TV (whose sentence once killed an utterance) can never kill a reply.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("actually put it under settings"),
                          FakeMic([_sil()] * 2 + [_sp()] * 7 + [_sil()] * 8),
                          pause_frames=3, **ears.kwargs())
    caught = {}
    thread = _watched(dictation, caught,
                      script=lambda: "The dropdown is reordered and live at localhost.",
                      audio=lambda: True)

    dictation.pump()
    caught["done"] = True
    thread.join(2.0)

    assert caught.get("stopped") is False  # words alone never cut the voice
    assert ears.drafted == ["actually put it under settings"]


def test_a_flicker_of_sound_while_it_speaks_never_reaches_the_draft():
    # Four loud frames is under the measured line of real speech (DELIBERATE_RUN=6): whatever the
    # model reads into it is an invention, and inventions do not get to talk over the reply.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("thank you"),
                          FakeMic(_burst_then_pause() + [_sil()] * 8),
                          pause_frames=3, **ears.kwargs())
    caught = {}
    thread = _watched(dictation, caught, script=lambda: "Something else entirely.",
                      audio=lambda: True)

    dictation.pump()
    caught["done"] = True
    thread.join(2.0)

    assert caught.get("stopped") is False
    assert ears.drafted == []


def test_a_stop_bark_while_it_merely_thinks_still_cuts_the_turn():
    # The open ear must not cost the voice-stop: a bark mid-think cancelled the brain before, and
    # still must - it fires on its own now instead of riding on "the whole watch is speaking".
    ears = Ears()
    dictation = Dictation(FakeTranscriber("stop"),
                          FakeMic(_burst_then_pause() + [_sil()] * 20),
                          pause_frames=3, **ears.kwargs())
    caught = {}
    thread = _watched(dictation, caught, script=lambda: "", audio=lambda: False)

    dictation.pump()
    caught["done"] = True
    thread.join(2.0)

    assert caught.get("stopped") is True  # the bark cut the think
    assert ears.drafted == []  # and never became draft text


def test_submitting_puts_the_mic_down_like_saying_over_does():
    # "Auto-listen bug: Excephalon drops to listening mode after speaking even when auto-listen is
    # unchecked." The mic survived a button/chord submit, so the next reply ended with the ear
    # already open and he read it as auto-listen. A submit is the whole gesture, whichever way
    # it is made: turn handed over, mic down. Auto-listening re-arms at end_speaking, as built.
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), pause_frames=3, **ears.kwargs())
    assert dictation.taking_dictation()

    dictation.submit("the drafted turn")

    assert not dictation.taking_dictation()
    assert ears.states[-1] == "muted"
    dictation.set_auto_listen(True)
    dictation.begin_speaking()
    dictation.end_speaking()
    assert dictation.taking_dictation()  # auto-listen, and only auto-listen, reopens it


def test_a_pause_in_his_dictation_still_counts_as_him_mid_thought():
    # "Entity interrupted me while I was talking... it should never do that." His natural pauses
    # end bursts, so the old is-he-talking check (mid-burst only) said no exactly then, and the
    # news offer barged into his pause. While the mic is armed and words landed recently, he is
    # still composing - unprompted speech holds.
    ears = Ears()
    clock = {"now": 100.0}
    dictation = Dictation(FakeTranscriber("the first half of a thought"),
                          FakeMic(_burst_then_pause()), pause_frames=3,
                          clock=lambda: clock["now"], **ears.kwargs())

    dictation.pump()  # the burst landed in the draft; the burst itself is over

    assert ears.drafted == ["the first half of a thought"]
    assert dictation.is_mid_utterance()  # he paused; he did not finish
    clock["now"] += 60.0
    assert not dictation.is_mid_utterance()  # a real lull, not a breath


def test_a_muted_mic_is_never_mid_utterance():
    ears = Ears()
    clock = {"now": 100.0}
    dictation = Dictation(FakeTranscriber("words"), FakeMic(_burst_then_pause()),
                          pause_frames=3, clock=lambda: clock["now"], **ears.kwargs())
    dictation.pump()
    dictation.set_recording(False)

    assert not dictation.is_mid_utterance()  # mic down means the floor is open


def test_a_submitted_draft_passes_through_the_polisher_on_its_way_to_the_loop():
    # His call on where the punctuation repair runs: "before submitting to it... hopefully only a
    # second of wait time" - the agent should read sentences, not pause-chopped fragments.
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), pause_frames=3,
                          polish=lambda text: text.replace(". As", " as"), **ears.kwargs())

    dictation.submit("do it. As we discussed")

    assert dictation.listen() == "do it as we discussed"


def test_spoken_formatting_commands_become_formatting_not_words():
    # "I should be able to speak commands like 'paragraph break' and have them become formatting."
    ears = Ears()
    dictation = Dictation(
        FakeTranscriber("first point paragraph break second point new line third"),
        FakeMic(_burst_then_pause()), pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["first point\n\nsecond point\nthird"]


def test_a_formatting_command_alone_is_just_its_formatting():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("new paragraph"),
                          FakeMic(_burst_then_pause()), pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["\n\n"]  # the command IS its formatting - spoken alone,
    # with a pause either side, is exactly how he says it, and it must still land


def _mouth(*, terminator="over"):
    """A pump with no mic behind it: chunks go straight to the path where the terminator - said
    or misheard - is recognised. `said` marks a chunk as deliberate, which is what a real burst
    of his voice carries; without it the invention filter eats short filler like a bare "okay",
    and this is about the ones he really said."""
    ears = Ears()
    pump = Dictation(FakeTranscriber(), FakeMic([]), terminator=terminator, **ears.kwargs())
    said = lambda text: pump._take_dictation(text, deliberate=True)
    return pump, ears, said


def test_the_terminator_still_lands_when_it_is_heard_as_okay():
    # "'Over' keeps getting misheard as 'Okay'. Can we do anything about that?" The record shows
    # exactly that shape - a turn ending "...Surely you can figure it out. Okay." - so a trailing
    # "okay" standing as its own sentence, with dictated words already in the box, is taken as the
    # gesture it was: the turn goes over, the word never lands, and the mic goes down with it.
    pump, ears, said = _mouth()
    said("The link and copy work is ready.")
    said("Okay.")

    assert ears.drafted == ["The link and copy work is ready."]  # the stray word never arrives
    assert ears.submits == 1
    assert ears.states[-1] == "muted"  # the whole gesture, exactly as "over" is


def test_okay_by_itself_with_an_empty_box_is_just_him_saying_okay():
    # He answers questions with it all day. With nothing dictated to end, it is a word like any
    # other - the failure this must never become is a turn sent while he is only agreeing.
    pump, ears, said = _mouth()
    said("Okay.")

    assert ears.drafted == ["Okay."]
    assert ears.submits == 0


def test_okay_mid_clause_is_his_word_and_stays_put():
    # "...that's fine, okay" is speech, not a sign-off: only a standalone sentence counts.
    pump, ears, said = _mouth()
    said("The gym thing is settled.")
    said("And that's fine, okay")

    assert ears.drafted == ["The gym thing is settled.", "And that's fine, okay"]
    assert ears.submits == 0


def test_a_submitted_box_needs_new_words_before_okay_ends_a_turn_again():
    # The box empties with the turn, so the next lone "okay" is an answer again, not a gesture.
    pump, ears, said = _mouth()
    said("Send this one.")
    said("Okay.")
    pump.submit("Send this one.")
    said("Okay.")

    assert ears.submits == 1  # the second one drafted rather than submitting
    assert ears.drafted[-1] == "Okay."
