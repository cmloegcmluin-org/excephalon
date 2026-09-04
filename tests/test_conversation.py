import threading
import time

from excephalon.console import Console
from excephalon.conversation import Conversation, Turn
from excephalon.outbox import Outbox
from excephalon.voice import UNSAID


class FakeSTT:
    def __init__(self, utterances):
        self._utterances = list(utterances)
        self.calls = 0

    def listen(self):
        self.calls += 1
        if not self._utterances:
            raise AssertionError("STT exhausted - the loop failed to stop")
        return self._utterances.pop(0)


class FakeBrain:
    def __init__(self):
        self.heard = []

    def respond(self, utterance):
        self.heard.append(utterance)
        # Echo the USER'S words, not the system notes the loop prefixed - a real brain answers
        # the person, and tests about replies must not also be tests of the annotation.
        return f"reply to {_words(utterance)}"


class CarryingBrain(FakeBrain):
    """A brain that does what the reply brief asks: it carries the owed news in its own reply."""

    def __init__(self, reply):
        super().__init__()
        self._reply = reply

    def respond(self, utterance):
        super().respond(utterance)
        return self._reply


class FakeTTS:
    """A one-shot voice with no stream(): the fallback shape (system voice, muted runs)."""

    def __init__(self):
        self.spoken = []

    def speak(self, text, *, interrupt=None):
        self.spoken.append(text)


def _words(utterance):
    """Just the user's own words, with any system note the loop prefixed stripped back off - so a
    test about WHICH turns reached the brain isn't also a test of how they were annotated."""
    return utterance.rpartition("]\n\n")[2]


def test_a_barge_in_before_the_reply_leaves_it_unspoken():
    interrupt = threading.Event()

    class InterruptingBrain:  # they hit Enter while it's thinking
        def respond(self, utterance):
            interrupt.set()
            return "a fifteen-minute novella they never wanted"

    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), InterruptingBrain(), tts, interrupt=interrupt)
    convo.turn()

    assert tts.spoken == []  # the reply is silenced, and no canned line takes its place


def test_the_reply_is_printed_to_the_terminal_before_it_is_spoken():
    events = []

    class RecordingTTS:
        def speak(self, text, *, interrupt=None):
            events.append(f"say:{text}")

    console = Console(echo=lambda line: events.append(f"print:{line}"))
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), RecordingTTS(), console=console)

    convo.turn()

    printed = next(e for e in events if e.startswith("print:excephalon>") and "reply to hi" in e)
    assert events.index(printed) < events.index("say:reply to hi")  # they can read it before/while it speaks


def test_a_path_is_shown_whole_and_spoken_the_way_a_person_would_say_it():
    # They want the real path on screen - it is what they click - and they do not want a minute of
    # "see colon backslash users backslash" out of the speaker. The two are not the same words.
    class PathBrain:
        def respond(self, utterance):
            return r"It's in C:\Users\ada\workspace\entity\runtime\profile.md."

    tts, shown = FakeTTS(), []
    convo = Conversation(FakeSTT(["where"]), PathBrain(), tts, console=Console(echo=shown.append))

    convo.turn()

    assert any(r"C:\Users\ada\workspace\entity\runtime\profile.md" in line for line in shown)
    assert tts.spoken == ["It's in profile.md."]


def test_timings_prints_a_think_and_speak_readout_when_enabled():
    lines = []
    convo = Conversation(
        FakeSTT(["hi"]), FakeBrain(), FakeTTS(),
        timings=True, console=Console(echo=lines.append),
    )

    convo.turn()

    assert any("· speak" in line for line in lines)  # the per-turn think/speak readout showed


def test_no_timings_readout_when_disabled():
    lines = []
    convo = Conversation(
        FakeSTT(["hi"]), FakeBrain(), FakeTTS(),
        timings=False, console=Console(echo=lines.append),
    )

    convo.turn()

    assert not any("· speak" in line for line in lines)


def test_a_thinking_indicator_is_shown_while_it_thinks():
    shown = []
    console = Console(echo=shown.append)
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), FakeTTS(), console=console)

    convo.turn()

    assert any("thinking" in line.lower() for line in shown)


def test_it_pauses_after_a_reply_to_give_a_beat_to_read():
    slept = []
    convo = Conversation(
        FakeSTT(["hi"]), FakeBrain(), FakeTTS(),
        read_pause=1.5, sleep=slept.append,
    )

    convo.turn()

    assert slept == [1.5]  # a beat to read the reply before listening starts again


def test_no_read_pause_after_a_control_phrase():
    slept = []
    convo = Conversation(FakeSTT(["suspend"]), FakeBrain(), FakeTTS(), read_pause=1.5, sleep=slept.append)

    convo.turn()

    assert slept == []  # nothing substantive to read, so no beat


def test_read_pause_is_skipped_when_he_barges_in():
    slept = []
    interrupt = threading.Event()

    class InterruptingBrain:
        def respond(self, utterance):
            interrupt.set()  # they cut in as the reply lands
            return "reply"

    convo = Conversation(
        FakeSTT(["hi"]), InterruptingBrain(), FakeTTS(),
        read_pause=1.5, sleep=slept.append, interrupt=interrupt,
    )

    convo.turn()

    assert slept == []  # they're cutting in - don't make them wait out a read pause


def test_a_barge_in_while_thinking_cancels_the_brain_and_returns_to_listening():
    interrupt = threading.Event()
    thinking = threading.Event()
    release = threading.Event()

    class SlowInterruptibleBrain:
        def __init__(self):
            self.interrupted = False

        def respond(self, utterance):
            thinking.set()  # we're now inside the brain call
            release.wait(2.0)  # block until cancelled (safety timeout so a bug can't hang the suite)
            return "an essay they never wanted to sit through"

        def interrupt(self):
            self.interrupted = True
            release.set()  # cancelling unblocks the call, as the real SDK interrupt does

    brain = SlowInterruptibleBrain()
    tts = FakeTTS()

    def barge():
        thinking.wait(2.0)
        interrupt.set()  # they hit Enter / say "stop" while it's still thinking

    threading.Thread(target=barge, daemon=True).start()
    convo = Conversation(FakeSTT(["do the big thing"]), brain, tts, interrupt=interrupt)
    turn = convo.turn()

    assert turn is None  # the turn was abandoned - the loop is free to listen again
    assert brain.interrupted is True  # the brain was told to drop the in-flight call
    assert tts.spoken == []  # the cancelled reply stayed unsaid


def test_a_barge_in_while_thinking_does_not_start_the_next_brain_call_until_the_last_unwinds():
    # The cancel must WAIT for the cancelled call to finish unwinding, so the loop never runs two
    # overlapping brain calls on the one session.
    interrupt = threading.Event()
    thinking = threading.Event()
    release = threading.Event()
    order = []

    class SlowInterruptibleBrain:
        def respond(self, utterance):
            thinking.set()
            release.wait(2.0)
            order.append("unwound")
            return "late reply"

        def interrupt(self):
            release.set()

    brain = SlowInterruptibleBrain()

    def barge():
        thinking.wait(2.0)
        interrupt.set()

    threading.Thread(target=barge, daemon=True).start()
    convo = Conversation(FakeSTT(["go"]), brain, FakeTTS(), interrupt=interrupt)
    convo.turn()
    order.append("turn_returned")

    assert order == ["unwound", "turn_returned"]  # the worker finished before turn() handed control back


def test_a_spoken_stop_word_while_thinking_cancels_the_brain():
    interrupt = threading.Event()
    thinking = threading.Event()
    release = threading.Event()

    class SlowInterruptibleBrain:
        def __init__(self):
            self.interrupted = False

        def respond(self, utterance):
            thinking.set()
            release.wait(2.0)
            return "a monologue they tried to stop"

        def interrupt(self):
            self.interrupted = True
            release.set()

    class SpokenStopSTT:
        def listen(self):
            return "do the big thing"

        def catch_stop(self, active):
            # honour the active window like the real mic; report a spoken "stop" once it's thinking
            while active():
                if thinking.is_set():
                    return True
                time.sleep(0.005)
            return False

    brain = SlowInterruptibleBrain()
    tts = FakeTTS()
    convo = Conversation(SpokenStopSTT(), brain, tts, interrupt=interrupt)
    turn = convo.turn()

    assert turn is None
    assert brain.interrupted is True  # a spoken "stop" mid-think cancelled it, not only the Enter key
    assert "a monologue they tried to stop" not in tts.spoken


def test_a_stale_interrupt_is_cleared_at_the_start_of_a_turn():
    interrupt = threading.Event()
    interrupt.set()  # left over from cutting off the previous turn
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), tts, interrupt=interrupt)

    convo.turn()

    assert tts.spoken == ["reply to hi"]  # the stale flag didn't gag this fresh turn


def test_the_interrupt_is_forwarded_to_the_tts_so_a_reply_in_progress_can_be_killed():
    interrupt = threading.Event()
    passed = []

    class CapturingTTS:
        def speak(self, text, *, interrupt=None):
            passed.append(interrupt)

    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), CapturingTTS(), interrupt=interrupt)
    convo.turn()

    assert interrupt in passed  # the reply's speak got the interrupt, so a keypress can cut it mid-word


def test_a_spoken_stop_word_cuts_the_voice_off():
    interrupt = threading.Event()

    class StopHearingSTT:  # they say "stop" the instant it starts talking
        def listen(self):
            return "tell me a long story"

        def catch_stop(self, active):
            return True

    convo = Conversation(StopHearingSTT(), FakeBrain(), FakeTTS(), interrupt=interrupt)
    convo.turn()

    assert interrupt.is_set()  # the spoken "stop" tripped the same interrupt the Enter key does


def test_the_stop_watcher_is_handed_the_script_and_the_audio_state():
    # Full duplex needs the mic to know two things the bare stop-watch never carried: WHAT is
    # being spoken (to tell Excephalon's own leak from someone talking over it) and WHETHER sound
    # is in the air at all (while the brain merely thinks, the ear stays open). Both ride in on
    # catch_stop for a mic whose catch_stop can take them.
    seen = {}
    done_speaking = threading.Event()

    class DuplexSTT:
        def listen(self):
            return "make it so"

        def catch_stop(self, active, script=None, audio=None):
            seen["script"] = script
            seen["audio"] = audio
            while active():
                time.sleep(0.005)
            return False

    class FakeReply:
        def __init__(self):
            self.sounding = False
            self.text = []

        def add(self, piece):
            self.text.append(piece)

        def done(self):
            return "".join(self.text)

    class StreamingTTS:
        def __init__(self):
            self.reply = FakeReply()

        def stream(self, *, interrupt=None, spoken_form=None):
            return self.reply

    class StreamingBrain:
        def respond(self, utterance, *, on_text=None):
            for piece in ("All three are ", "green."):
                on_text(piece)
            return "All three are green."

    tts = StreamingTTS()
    convo = Conversation(DuplexSTT(), StreamingBrain(), tts, interrupt=threading.Event())

    convo.turn()

    assert seen["script"]() == "All three are green."  # the words being spoken, as they stand
    assert seen["audio"]() is tts.reply.sounding is False  # and the live is-sound-out state


def test_stop_listening_sleeps_the_entity_and_hey_entity_wakes_it():
    brain = FakeBrain()
    tts = FakeTTS()
    stt = FakeSTT(["stop listening", "are you there", "hey Excephalon", "hi again", "goodbye entity"])
    convo = Conversation(stt, brain, tts)

    convo.run()

    assert [_words(u) for u in brain.heard] == ["hi again"]  # nothing reached it while asleep
    assert convo.suspend_reply in tts.spoken and convo.resume_reply in tts.spoken


def test_stop_listening_does_not_quit():
    convo = Conversation(FakeSTT(["stop listening"]), FakeBrain(), FakeTTS())

    turn = convo.turn()

    assert turn.farewell is False  # it sleeps, it doesn't end the conversation


def test_a_command_with_a_stray_word_in_front_still_fires():
    # transcription tacks words on ("okay, stop listening"); exact-match used to miss that
    convo = Conversation(FakeSTT(["okay stop listening"]), FakeBrain(), FakeTTS())
    assert convo.turn().said == convo.suspend_reply


def test_a_trailing_farewell_still_ends_the_conversation():
    convo = Conversation(FakeSTT(["alright well goodbye entity"]), FakeBrain(), FakeTTS())
    assert convo.turn().farewell is True


def test_it_answers_to_the_name_it_calls_itself():
    # It says "Excephalon is here" on the way up and "say 'hey Excephalon' when you want me back"
    # on the way to sleep - and then only ever listened for "entity". The app naming itself one
    # thing and answering to another is the rename left half-done; the coined word being the one
    # the transcriber least reliably lands is exactly why the old name stays alongside it.
    assert Conversation(FakeSTT(["goodbye excephalon"]), FakeBrain(), FakeTTS()).turn().farewell
    assert Conversation(FakeSTT(["goodnight excephalon"]), FakeBrain(), FakeTTS()).turn().farewell

    convo = Conversation(FakeSTT(["stop listening", "hey excephalon"]), FakeBrain(), FakeTTS())
    assert convo.turn().said == convo.suspend_reply
    assert convo.turn().said == convo.resume_reply


def test_a_plain_sentence_is_not_mistaken_for_a_command():
    convo = Conversation(FakeSTT(["tell me about the weather"]), FakeBrain(), FakeTTS())
    turn = convo.turn()
    assert turn.farewell is False and turn.said == "reply to tell me about the weather"


def test_turn_transcribes_thinks_and_speaks():
    stt = FakeSTT(["hello"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    turn = convo.turn()

    assert [_words(u) for u in brain.heard] == ["hello"]
    assert tts.spoken == ["reply to hello"]  # the reply itself is the whole exchange - no stock lines
    assert turn == Turn(heard="hello", said="reply to hello")


def test_queued_agent_news_is_spoken_when_it_is_the_entitys_turn():
    outbox = Outbox()
    outbox.push("Heads up - the auth agent is ready for your review.")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert tts.spoken[0] == "Heads up - the auth agent is ready for your review."  # before we listened


def test_an_unprompted_message_is_printed_to_the_terminal_not_only_spoken(capsys):
    outbox = Outbox()
    outbox.push("the deploy agent needs your call")
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), FakeTTS(), outbox=outbox)

    convo.turn()

    assert "the deploy agent needs your call" in capsys.readouterr().out  # visible, not just audio


def test_several_ready_at_once_are_read_out_numbered_and_held_until_one_is_named():
    # "when several are ready, tell them which and let them choose the order." Run together they
    # arrived as a wall nobody could take one piece at a time.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert tts.spoken[0] == "Two updates waiting. One, fixer. Two, docs-sidebar. Which first?"
    assert "the drive link is fixed" not in " ".join(tts.spoken)  # neither is read out unasked


def test_naming_one_of_them_speaks_that_one_and_says_what_is_still_waiting():
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["two"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()  # the roll call goes out, then they answer it

    spoken = "\n".join(tts.spoken)
    assert "docs-sidebar: needs your call on the width" in spoken
    assert "Still waiting: fixer." in spoken
    assert "the drive link is fixed" not in spoken  # the one they didn't pick keeps waiting


def test_an_agents_older_news_is_forgotten_durably_when_newer_news_replaces_it(tmp_path):
    # The collapse used to be in memory only, so the older sentence sat on in the spool and the
    # next process spoke it: work announced as "ready for your eyes" moments after he reviewed it.
    spool = tmp_path / "outbox.json"
    outbox = Outbox(spool=spool)
    outbox.push("The split is ready for your eyes.", about="projects-tab")
    outbox.push("All twelve are cards now.", about="projects-tab")
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), FakeTTS(), outbox=outbox)

    convo.turn()

    assert Outbox(spool=spool).drain() == []  # both are finished with; neither comes back


def test_a_list_already_read_out_is_not_recited_every_turn():
    # It is checked before every listen. Announcing the same names each time round would be the
    # nagging that made periodic progress updates worse than silence.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)

    convo.turn()  # the roll call, then a question that names none of them
    convo.turn()

    # Counted as announcements, not substrings: the fake brain echoes its whole prompt back, and
    # the system note in it legitimately quotes the roll call it was told about.
    assert len([line for line in tts.spoken if line.startswith("Two updates waiting.")]) == 1


def test_once_the_list_is_worked_through_the_next_single_notice_is_simply_spoken():
    # The reset that matters. Left standing, the roll-call state would hold every later notice
    # back waiting for a name that is never coming - news they are never told at all.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["one", "sidebar", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)

    convo.turn()  # the roll call, then they take the first
    convo.turn()  # and then the last one, by name
    outbox.push("drive-export: green and pushed", about="drive-export")
    convo.turn()

    assert "drive-export: green and pushed" in tts.spoken  # straight out, with nothing to choose


def test_fresh_news_from_a_listed_agent_keeps_its_place_so_the_numbers_he_heard_stay_true():
    # "Why did you give me two occurrences of three updates waiting, but order them differently?
    # Now I don't know what to tell you." Superseding used to leave the newest item at its ARRIVAL
    # position, so a refresh moved that agent to the end and the re-read came out re-numbered.
    # The refresh keeps the agent's place: the re-read says the same names at the same numbers,
    # and picking one yields that agent's NEWEST sentence.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "one", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)

    convo.turn()  # a roll call of two, then a question that names none of them
    outbox.push("fixer: green now, and pushed", about="fixer")
    convo.turn()  # fresh news, same names: the list stands as read, not repeated at him
    convo.turn()  # so "one" still means fixer - and yields its newest sentence

    spoken = "\n".join(tts.spoken)
    assert len([line for line in tts.spoken
                if line == "Two updates waiting. One, fixer. Two, docs-sidebar. Which first?"]) == 1
    assert "fixer: green now, and pushed" in spoken
    assert "fixer: the drive link is fixed" not in spoken


def test_a_refreshed_agent_keeps_its_number_even_as_the_list_grows():
    # The same failure with a new arrival in the mix: the re-read is right (the list truly
    # changed), but the refreshed agent must hold its old place in it, so the numbers he already
    # heard stay true.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)

    convo.turn()  # a roll call of two
    outbox.push("fixer: green now, and pushed", about="fixer")
    outbox.push("drive-export: green and pushed", about="drive-export")
    convo.turn()

    assert ("Three updates waiting. One, fixer. Two, docs-sidebar. Three, drive-export. Which first?"
            in tts.spoken)


def test_the_offered_update_is_carried_by_the_reply_itself_and_checked():
    # "Folded into a fresh brain turn the content twice went missing - a 'Yes' answered with 'Go
    # check it out then'" - so for a while the app appended the stored words to the reply by
    # code. That made two authors of one utterance, and the seam between them is where doubled
    # sentences and welded topics came from. The brain is the one author now, handed the fact to
    # carry in its own reply - and the loop CHECKS that the reply carried it before anything is
    # spent. One utterance, one author, nothing riding on trust.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    brain = CarryingBrain("Sure - the scrubber drag is ready for your eyes now.")
    convo = Conversation(FakeSTT(["", "sure, go ahead with it", "goodbye entity"]), brain,
                         tts, outbox=outbox, dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("The scrubber drag is ready for your eyes.", about="scrubber")

    convo.turn()  # the offer
    convo.turn()  # more than a bare go-ahead: a real turn, with the update owed into it

    assert "OWED to him and you are its only author" in brain.heard[-1]
    assert "Sure - the scrubber drag is ready for your eyes now." in tts.spoken
    assert not outbox  # carried, so delivered - and durably so
    assert "The scrubber drag is ready for your eyes." not in tts.spoken  # never a second copy


def test_a_reply_that_drops_the_owed_update_leaves_it_owed_and_it_is_spoken_next():
    # The other half of the same rule, and the one the weld existed to guarantee: a reply that
    # did not carry the news has not delivered it. It stays owed - back where it stood - and goes
    # out whole at the next opening. Loss becomes repeat, never silence.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["", "sure, go ahead with it", "", "goodbye entity"]),
                         FakeBrain(), tts, outbox=outbox, dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("The scrubber drag is ready for your eyes.", about="scrubber")

    convo.turn()  # the offer
    convo.turn()  # "reply to sure, go ahead with it" - the news is nowhere in it
    assert convo._outbox.owed() and str(convo._outbox.owed()[0]) == "The scrubber drag is ready for your eyes."

    convo.turn()  # the next opening: the news itself, whole

    assert "The scrubber drag is ready for your eyes." in tts.spoken
    assert not outbox


def test_a_reply_the_brain_leaves_empty_leaves_the_update_owed_for_the_next_opening():
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()

    class SilentBrain(FakeBrain):
        def respond(self, utterance):
            super().respond(utterance)
            return ""

    convo = Conversation(FakeSTT(["", "tell me about it", "", "goodbye entity"]), SilentBrain(),
                         tts, outbox=outbox, dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("The scrubber drag is ready for your eyes.", about="scrubber")

    convo.turn()
    convo.turn()  # nothing said, so nothing delivered: the update goes back, still owed
    assert convo._outbox.owed()

    convo.turn()  # and the next opening speaks it whole

    assert "The scrubber drag is ready for your eyes." in tts.spoken


def test_deliver_update_lands_in_the_same_utterance_as_the_reply_that_announces_it():
    # "Here's what the scrubber fix agent has for you." - and nothing followed ("Hm, what do you
    # mean? You didn't get me anything."): the tool recorded a request that a LATER loop pass was
    # supposed to serve, and anything between the two could void it. The request is now served on
    # the very turn that made it: the app appends the held words to that reply, one utterance.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()

    class DeliveringBrain(FakeBrain):
        """A brain that calls deliver_update mid-think, the way the real tool call lands."""

        def __init__(self, request):
            super().__init__()
            self._request = request

        def respond(self, utterance):
            super().respond(utterance)
            if "fixer thing" in utterance:
                self._request("fixer")
                return "Here it is - the drive link is fixed."
            return "reply"

    convo = Conversation(FakeSTT(["give me the update on the fixer thing", "goodbye entity"]),
                         DeliveringBrain(outbox.request), tts, outbox=outbox)

    convo.turn()  # the roll call of two goes out, then his full-sentence ask

    assert "Here it is - the drive link is fixed." in tts.spoken  # the reply itself is the update
    assert "fixer: the drive link is fixed" not in tts.spoken  # nothing appended behind it
    assert [held.about for held in convo._outbox.owed()] == ["docs-sidebar"]  # settled; the other held


def test_a_delivery_that_never_began_sounding_is_still_owed():
    # The black-hole class: _say drops the line whole when a barge-in is already set, and the
    # bookkeeping used to mark it delivered anyway - spool cleared, zero audio. Nothing is marked
    # spoken now unless its utterance actually began; the news stays owed and comes back.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    interrupt = threading.Event()

    class EnterAfterSpeaking(FakeSTT):
        """His pick, with Enter landing right behind it - the interrupt is set by the time the
        delivery tries to speak."""

        def __init__(self, texts, stop, barge_on):
            super().__init__(texts)
            self._stop = stop
            self._barge_on = barge_on

        def listen(self):
            heard = super().listen()
            if heard == self._barge_on:
                self._stop.set()
            return heard

    stt = EnterAfterSpeaking(["what time is it", "one", "one", "goodbye entity"], interrupt, "one")
    convo = Conversation(stt, FakeBrain(), tts, outbox=outbox, interrupt=interrupt)

    convo.turn()  # the roll call of two
    convo.turn()  # his pick, but Enter is already down: nothing sounds, nothing is spent
    assert "fixer: the drive link is fixed" not in "\n".join(tts.spoken)

    stt._barge_on = None
    convo.turn()  # he picks again; the news was still owed, and now it goes out

    assert any("fixer: the drive link is fixed" in line for line in tts.spoken)


class RememberingBrain(FakeBrain):
    """A brain that keeps its own record of what it said and what it took back."""

    def __init__(self):
        super().__init__()
        self.said = []
        self.retracted = []

    def spoke(self, text):
        self.said.append(str(text))

    def retract(self, draft):
        self.retracted.append(str(draft))


def test_its_own_news_enters_its_memory_only_once_he_has_heard_it():
    # Agent news is composed minutes before it is spoken, and the composing call used to write the
    # model's memory then and there. So the window a compaction or a restart rebuilds from carried
    # lines he had never heard, and the model reasoned from them as things it had told him. It is
    # written from the delivery now.
    outbox = Outbox()
    outbox.push("The drive link is fixed.", about="fixer", composed=True)
    brain = RememberingBrain()
    convo = Conversation(FakeSTT(["what time is it"]), brain, FakeTTS(), outbox=outbox)

    convo.turn()

    assert brain.said == ["The drive link is fixed."]


def test_a_line_overtaken_before_it_was_ever_spoken_is_taken_back():
    # Newer news about the same agent replaces the older, which is right - but the older sentence
    # was the model's own and is still sitting in its session history, where the only reading is
    # that it was said. Holding the overtaken line AND speaking the newer one is the same news
    # twice from the inside.
    outbox = Outbox()
    outbox.push("The drive work is being built.", about="fixer", composed=True)
    outbox.push("The drive work is ready for your eyes.", about="fixer", composed=True)
    brain = RememberingBrain()
    convo = Conversation(FakeSTT(["what time is it"]), brain, FakeTTS(), outbox=outbox)

    convo.turn()

    # The older sentence never reaches this loop at all now: the store keeps one fact per thread
    # and the newer replaced it the moment it was written - and since news arrives as facts
    # rather than as the brain's own prose, there is nothing of the brain's to take back.
    assert brain.retracted == []
    assert brain.said == ["The drive work is ready for your eyes."]
    assert not outbox


def test_a_first_line_he_never_heard_is_taken_back_rather_than_remembered():
    # A first line is a first line or nothing: he spoke before it could be said, so it is dropped.
    # Remembered anyway, a welcome nobody heard came back after the next restart as the line the
    # model believed it had opened with.
    class TalkingSTT(FakeSTT):
        def is_mid_utterance(self):
            return True  # he is already speaking, so nothing unprompted may break in

    brain = RememberingBrain()
    convo = Conversation(TalkingSTT(["what time is it"]), brain, FakeTTS(), outbox=Outbox(),
                         opening="Back with you - where were we?")

    convo.turn()

    assert brain.retracted == ["Back with you - where were we?"]
    assert brain.said == []


def test_a_first_line_that_did_sound_is_remembered_as_said():
    outbox = Outbox()
    outbox.push("The drive link is fixed.", about="fixer", composed=True)
    brain = RememberingBrain()
    convo = Conversation(FakeSTT(["what time is it"]), brain, FakeTTS(), outbox=outbox,
                         opening="Back with you.")

    convo.turn()  # the opening rides the front of the one held update

    assert "Back with you." in brain.said


def test_an_offer_he_has_not_answered_is_never_delivered_at_him_anyway():
    # "I never said I was ready for the update." He was offered one at a lull, said nothing, and
    # five minutes later the whole walkthrough was read out unasked - it had arrived by another
    # road (an errand's report), which bypassed the offer entirely. An offer he did not take is
    # not a licence to deliver.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["", ""]), FakeBrain(), tts, outbox=outbox,
                         dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("The scheduled-message feature is ready to try.", about="scheduler")

    convo.turn()  # the lull: he is offered it
    assert tts.spoken == ["I've got an update on scheduler when you're ready."]

    outbox.push("Here are the exact steps to launch it.", about="errands", listed=False)
    convo.turn()  # more arrives, by another road, while the offer stands

    assert not any("exact steps" in line for line in tts.spoken)  # nothing read at him


def test_more_arriving_under_a_standing_offer_says_so_by_the_count():
    # "if it now had another update, it should have said something like 'I now have two updates
    # for the scheduled-message item'." The offer stands; what changes is how much is behind it.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["", "", ""]), FakeBrain(), tts, outbox=outbox,
                         dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("Built and ready to try.", about="scheduler")

    convo.turn()
    # By another road, as it was in his transcript: the same work reported under a second name.
    outbox.push("And here is how to launch it.", about="errands", listed=False)
    convo.turn()

    assert tts.spoken[-1].startswith("I've got two updates on ")

    convo.turn()  # nothing new since: a standing question is not repeated

    assert len(tts.spoken) == 2


def test_a_mouth_that_receipts_its_own_silence_is_believed(tmp_path):
    # The gap the loop could not see. Its own interrupt was never set, so every check it makes
    # says the line went out - but the voice knows better: a barge-in landed while the engine was
    # synthesizing, and not one sample reached the air. Answered True regardless, the news was
    # spent over zero audio, which is how a merge report died. The mouth's receipt is now the
    # answer, and a mouth that says nothing sounded is believed over the loop's own view.
    outbox = Outbox(spool=tmp_path / "spool.json")
    outbox.push("fixer: the drive link is fixed", about="fixer")

    class SilentMouth(FakeTTS):
        def speak(self, text, *, interrupt=None):
            self.spoken.append(text)  # it was asked for - and it never made a sound
            return UNSAID

    tts = SilentMouth()
    convo = Conversation(FakeSTT(["what time is it"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert convo._outbox.owed()  # nothing was spent: the news is still owed and comes back
    assert outbox.owed_about() == {"fixer"}


def test_the_streamed_reply_carries_the_news_itself_on_the_path_the_real_app_runs():
    # The production shape is a streaming brain into a streaming voice: the extras must enter the
    # SAME reply stream (one utterance, one stop), and a stream that never got a word into the
    # air spends nothing.
    clock = FakeClock()
    outbox = Outbox()
    tts = StreamingTTS()
    convo = Conversation(FakeSTT(["", "yeah, let me know", ""]),
                         StreamingBrain("Right - the fix is ready to look at on localhost:5200."),
                         tts, outbox=outbox, dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("The fix is ready to look at on localhost:5200.", about="asana-submit-fix",
                composed=True)

    convo.turn()  # the offer
    convo.turn()  # his answer: the reply streams, carrying the update in its own words

    reply = tts.replies[-1]
    assert "".join(reply.deltas) == "Right - the fix is ready to look at on localhost:5200."
    assert not convo._outbox.owed()  # carried by the streamed reply, so delivered


def test_a_streamed_utterance_that_never_sounded_spends_nothing():
    # Reply.done() answers with what actually reached the air; drained whole by a barge-in that
    # beat the first word, it answers empty - and the update must still be owed.
    clock = FakeClock()
    outbox = Outbox()
    interrupt = threading.Event()

    class DrainedTTS(StreamingTTS):
        class Reply(StreamingTTS.Reply):
            def done(self):
                self.finished = True
                self._tts.spoken.append("")
                return ""  # the whole utterance drained unspoken

        def stream(self, *, interrupt=None, spoken_form=None):
            reply = self.Reply(self, interrupt)
            self.replies.append(reply)
            return reply

    convo = Conversation(FakeSTT(["", "yeah, let me know", ""]),
                         StreamingBrain("Right, here it is."), DrainedTTS(), outbox=outbox,
                         dormant_after=180, clock=clock, interrupt=interrupt)
    clock.now = 600
    outbox.push("The fix is ready to look at on localhost:5200.", about="asana-submit-fix",
                composed=True)

    convo.turn()
    convo.turn()

    assert convo._outbox.owed()  # never sounded: the update is still owed
    assert str(convo._outbox.owed()[0]) == "The fix is ready to look at on localhost:5200."


def test_a_welded_update_is_kept_when_a_barge_in_beats_the_reply():
    # The mutation this pins: removing the began-sounding guard must fail this test. His Enter
    # lands at the very end of the think; nothing sounds; the update survives to the next turn.
    clock = FakeClock()
    outbox = Outbox()
    interrupt = threading.Event()
    tts = FakeTTS()

    class BargedBrain(FakeBrain):
        def __init__(self, stop):
            super().__init__()
            self._stop = stop
            self.first = True

        def respond(self, utterance):
            super().respond(utterance)
            if self.first:
                self.first = False
                self._stop.set()  # Enter lands just as the reply finishes composing
            return "reply"

    convo = Conversation(FakeSTT(["", "yeah, let me know", "and again", ""]), BargedBrain(interrupt),
                         tts, outbox=outbox, dormant_after=180, clock=clock, interrupt=interrupt)
    clock.now = 600
    outbox.push("The fix is ready to look at on localhost:5200.", about="asana-submit-fix",
                composed=True)

    convo.turn()  # the offer
    convo.turn()  # his answer, but Enter beat the audio: nothing sounds, nothing is spent
    assert all("ready to look at" not in line for line in tts.spoken)

    convo.turn()  # the next turn still owes it, and now it goes out (in its spoken form)

    assert any("The fix is ready to look at on localhost port 5200." in line
               for line in tts.spoken)


def test_a_dying_voice_spends_nothing_either():
    # tts.speak raising before any audio used to count as spoken; the news died with the hiccup.
    outbox = Outbox()

    class DyingTTS(FakeTTS):
        def __init__(self):
            super().__init__()
            self.failures = 1

        def speak(self, text, interrupt=None):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("audio device vanished")
            super().speak(text, interrupt=interrupt)

    tts = DyingTTS()
    convo = Conversation(FakeSTT(["what time is it", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)
    outbox.push("fixer: the drive link is fixed", about="fixer")

    convo.turn()  # the voice dies on the delivery: the news must survive it
    convo.turn()  # and the next pass, voice recovered, actually says it

    assert any("fixer: the drive link is fixed" in line for line in tts.spoken)


def test_deliver_update_reaches_news_that_arrived_mid_turn():
    # hand_over_news answers from the outbox's whole debt - queue included - so a request for news
    # that landed WHILE he was talking must be served from the queue too, or the reply promises an
    # update that never follows (the 20:42 failure shape, reachable by another door).
    outbox = Outbox()
    tts = FakeTTS()

    class MidTurnBrain(FakeBrain):
        def __init__(self, outbox):
            super().__init__()
            self._outbox = outbox

        def respond(self, utterance):
            super().respond(utterance)
            # The agent reports while the brain is mid-think, and the brain hands it straight over.
            self._outbox.push("fixer: the drive link is fixed", about="fixer")
            self._outbox.request("fixer")
            return "Just came in - the drive link is fixed."

    convo = Conversation(FakeSTT(["did the fixer finish", "goodbye entity"]), MidTurnBrain(outbox),
                         tts, outbox=outbox)

    convo.turn()

    assert "Just came in - the drive link is fixed." in tts.spoken
    assert not convo._outbox.owed() and not outbox  # carried by the reply, so settled


def test_a_weld_that_never_sounds_puts_the_update_back_where_it_stood():
    # [fixer, docs-sidebar, exporter] was read out numbered; he asks for docs-sidebar in a full
    # sentence and Enter beats the audio. Re-queued anywhere but its own place, "two" would now
    # name a different agent than the one he heard under that number - the renumbering failure.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    outbox.push("exporter: green and pushed", about="exporter")
    interrupt = threading.Event()
    tts = FakeTTS()

    class BargedDeliveringBrain(FakeBrain):
        def __init__(self, request, stop):
            super().__init__()
            self._request = request
            self._stop = stop

        def respond(self, utterance):
            super().respond(utterance)
            if "sidebar thing" in utterance:
                self._request("docs-sidebar")
                self._stop.set()  # Enter lands as the reply finishes composing
                return "Here it is."
            return "reply"

    convo = Conversation(
        FakeSTT(["can you tell me all about the sidebar thing", "two", "goodbye entity"]),
        BargedDeliveringBrain(outbox.request, interrupt), tts, outbox=outbox,
        interrupt=interrupt)

    convo.turn()  # roll call of three, his ask, the barged weld: nothing sounds, nothing is spent
    assert [getattr(held, "about", None) for held in convo._outbox.owed()] == [
        "fixer", "docs-sidebar", "exporter"]  # back where it stood, numbers still true

    convo.turn()  # "two" still means docs-sidebar

    assert any("docs-sidebar: needs your call on the width" in line for line in tts.spoken)


def test_the_offered_update_is_durably_delivered(tmp_path):
    # Not just gone from the in-memory queue: the spool copy is spent too, so a restart cannot
    # resurrect an update he already heard welded to a reply.
    clock = FakeClock()
    spool = tmp_path / "outbox.json"
    outbox = Outbox(spool=spool)
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["", "yeah, let me know", ""]),
                         CarryingBrain("Sure - the fix is ready to look at on localhost:5200."),
                         tts, outbox=outbox, dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("The fix is ready to look at on localhost:5200.", about="asana-submit-fix")

    convo.turn()
    convo.turn()

    assert Outbox(spool=spool).drain() == []  # nothing survives to a next life


def test_no_stored_line_is_ever_withheld_from_him():
    # A gate used to sit here judging whether a queued line had been overtaken, and twice it
    # destroyed something he was asking for: the update he had just said "Yes." to, and the demo
    # link he had asked for twice - "(overtaken, never spoken, for errands: The play cursor drag
    # fix demo is at ...)". It prevented nothing in return; the stale-recording cases it was built
    # for are stopped at their sources. News never spoken is the graver failure, always.
    outbox = Outbox()
    outbox.push("The play cursor drag fix demo is at localhost:5223.", about="errands",
                listed=False)
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert "The play cursor drag fix demo is at localhost port 5223." in tts.spoken


def test_the_list_is_read_again_only_when_it_would_come_out_different():
    # "why did it just give me the same message twice in a row?" - 22:22:59 and 22:23:07, the same
    # sentence word for word. Fresh news had arrived for an agent already ON the list, which
    # changed the news but not one word of the roll call, since a roll call says only names. What
    # decides a re-read is what would be SAID, not what is held behind it.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "what time is it", "goodbye entity"]),
                         FakeBrain(), tts, outbox=outbox)

    convo.turn()  # the roll call
    outbox.push("fixer: green now, and pushed", about="fixer")  # same names, newer news
    convo.turn()

    assert len([line for line in tts.spoken if line.startswith("Two updates waiting.")]) == 1


def test_the_boot_says_one_thing_and_the_list_waits_for_his_answer():
    # "I just opened Excephalon and then it quickly sent me two messages. it should only have sent
    # me one." That was fixed by welding them - and the weld was the next failure: a greeting that
    # asked him something with an unrelated menu on its back is two questions in one breath. The
    # first line goes out alone and IS the offer; the list is what he hears once he says yes.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["yes", "goodbye entity"]), FakeBrain(), tts, outbox=outbox,
                         opening="Welcome back. Want to pick the drive work back up, or hear "
                                 "what's waiting?")

    convo.turn()  # the first line goes out alone; his "yes" then brings the news

    first, *rest = tts.spoken
    assert first == ("Welcome back. Want to pick the drive work back up, or hear "
                     "what's waiting?")
    assert "fixer" not in first and "docs-sidebar" not in first  # nothing rode its back
    assert any("the drive link is fixed" in line for line in rest)
    assert len([line for line in tts.spoken if "Welcome back" in line]) == 1


def test_the_opening_is_said_on_its_own_when_nothing_is_waiting():
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=Outbox(),
                         opening="I'm ready. What can I do for you?")

    convo.turn()

    assert tts.spoken[0] == "I'm ready. What can I do for you?"


def test_the_opening_is_said_once_and_never_again():
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "goodbye entity"]), FakeBrain(), tts,
                         outbox=Outbox(), opening="I'm ready. What can I do for you?")

    convo.turn()
    convo.turn()

    assert len([line for line in tts.spoken if "I'm ready" in line]) == 1


def test_the_first_line_goes_out_first_and_never_waits_behind_the_news():
    # A boot with exactly one update once spoke the news alone and left the welcome pending; it
    # surfaced seven minutes later, inviting him to look at a demo he had already approved ("this
    # message makes no sense. why was this sent?"). Riding the news fixed that and broke the other
    # side - the walkthrough arrived welded to a question about something else. The first line is
    # simply FIRST, and alone, so it can never be left behind and never carries another thread.
    outbox = Outbox()
    outbox.push("robot-icon-ui: the demo is ready - open localhost:8770", about="robot-icon-ui")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["yeah", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox,
                         opening="Back with you. Carry on where we were, or hear the update?")

    convo.turn()  # the first line alone, then his "yeah" brings the one held update

    first, *rest = tts.spoken
    assert first == "Back with you. Carry on where we were, or hear the update?"
    assert "8770" not in first  # the walkthrough did not ride the greeting
    assert any("localhost port 8770" in line for line in rest)
    assert len([line for line in tts.spoken if "Back with you" in line]) == 1


def test_an_opening_that_missed_its_moment_dies_when_he_speaks():
    # He was mid-sentence as the app came up, so the boot pass rightly held its first line - and
    # once he has spoken, that line is no longer a greeting. Spoken at the next lull anyway, the
    # welcome arrived deep into the conversation ("Excephalon had been with me up until just
    # before that so 'back with you' makes no sense").
    class MidSentenceSTT(FakeSTT):
        talking = True

        def is_mid_utterance(self):
            return self.talking

    stt = MidSentenceSTT(["what time is it", "goodbye entity"])
    tts = FakeTTS()
    convo = Conversation(stt, FakeBrain(), tts, outbox=Outbox(),
                         opening="Welcome back. Where were we?")

    convo.turn()  # the boot pass holds the greeting: he is mid-sentence, and then he speaks
    stt.talking = False
    convo.turn()  # the next lull would have been its old chance - it must not take it

    assert not [line for line in tts.spoken if "Welcome back" in line]


def test_unlisted_news_is_simply_said_and_never_joins_the_numbered_list():
    # "Two updates waiting. One, weekly-schedule-builder. Two, errands." - "I thought we're only
    # working on one thing. I don't even know what errands would be." The errand hand is
    # machinery, not an agent with a tab and a verdict; its result is something to say, and it
    # cost him five turns trying to close a "task" that never existed.
    outbox = Outbox()
    outbox.push("You're on italki as of this week.", about="errands", listed=False)
    outbox.push("fixer: the drive link is fixed", about="fixer")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)

    convo.turn()  # the errand's answer, said as itself
    convo.turn()  # then the one agent's news, on its own

    spoken = "\n".join(tts.spoken)
    assert "You're on italki as of this week." in spoken   # said, in its own words
    assert "errands" not in spoken                          # never named as a thing to pick
    assert "Which first?" not in spoken                     # one agent left: no menu at all
    assert "the drive link is fixed" in spoken


def test_unlisted_news_does_not_hold_back_a_real_roll_call():
    outbox = Outbox()
    outbox.push("You're on italki as of this week.", about="errands", listed=False)
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)

    convo.turn()  # the errand's answer first, as itself
    convo.turn()

    assert "Two updates waiting. One, fixer. Two, docs-sidebar. Which first?" in tts.spoken


def test_news_already_in_hand_is_pruned_when_its_agent_is_dropped():
    # A drop cleans the queue and the spool, but drained news waits in the conversation's hand -
    # and that copy was still offered after the user had sent the agent new instructions: "surely
    # there's no update for smart grouping. You just sent off the latest message to it." The hand
    # prunes itself with what the outbox collected.
    outbox = Outbox()
    outbox.push("grouping: done", about="grouping")
    outbox.push("fixer: ready for your eyes", about="fixer")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)

    convo.turn()  # a roll call of two; both now held in hand
    outbox.drop("grouping")  # the user just engaged that agent; its held news is stale
    convo.turn()

    spoken = "\n".join(tts.spoken)
    assert "Still waiting: fixer." in spoken  # the list shrank to the one still owed
    assert "grouping: done" not in spoken


def test_an_agent_finishing_while_they_are_choosing_joins_the_list_and_is_said():
    # Otherwise it sits silent behind a list that was read out before it existed, and the only
    # sign of it is a tab they had no reason to open.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)

    convo.turn()  # a roll call of two, then a question that names none of them
    outbox.push("drive-export: green and pushed", about="drive-export")
    convo.turn()

    assert ("Three updates waiting. One, fixer. Two, docs-sidebar. Three, drive-export. Which first?"
            in tts.spoken)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_news_after_a_long_lull_is_offered_not_dumped():
    # "instead of just suddenly start talking at me it would say 'I have an update if you're
    # ready'" - dormant, he is mid-something-else; the announcement lets him decide when to
    # stop, get comfortable, and take it.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT([""]), FakeBrain(), tts, outbox=outbox,
                         dormant_after=180, clock=clock)
    clock.now = 600  # ten minutes with no word from him
    outbox.push("The fix is ready to look at on localhost:5200.", about="asana-submit-fix",
                composed=True)

    convo.turn()

    assert tts.spoken == ["I've got an update on asana-submit-fix when you're ready."]


def test_a_change_to_his_standing_context_is_put_in_front_of_the_brain():
    # "Is stupid shit like this going to keep happening?" Everything of his in the persona is a
    # snapshot from startup, and each time one went stale the brain answered from it and told him
    # what he was looking at did not exist. Whatever has moved rides in the turn's own notes.
    moved = ["## Life context\n- he keeps bees now", "", ""]
    brain = FakeBrain()
    convo = Conversation(FakeSTT(["what do I keep", "and now", "goodbye entity"]), brain, FakeTTS(),
                         standing=lambda: moved.pop(0))

    convo.turn()
    convo.turn()

    assert "he keeps bees now" in brain.heard[0]
    assert "replaces what you were told at the start" in brain.heard[0]
    assert "he keeps bees now" not in brain.heard[1]  # said once, not every turn after


def test_a_bare_go_ahead_is_answered_with_the_held_update_itself():
    # "Excephalon keeps sending me updates that aren't updates." Twice a bare "Yes" was answered
    # by a fresh brain turn that never said what the update was - "Go check it out then", then
    # "Checking if the Projects tab changes are actually live" - while the app marked the news
    # delivered either way, so what the agent had reported reached him not at all. A bare
    # go-ahead asks for that content and nothing else, so the app says the content.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    brain = FakeBrain()
    convo = Conversation(FakeSTT(["", "yes", ""]), brain, tts, outbox=outbox,
                         dormant_after=180, clock=clock)
    clock.now = 600
    news = "Long names ellipsize now, and the Excephalon card looks like the others."
    outbox.push(news, about="projects-tab-refactor", composed=True)

    convo.turn()          # the offer goes out
    turn = convo.turn()   # his bare "yes"
    convo.turn()          # and nothing is left over to say again

    assert turn.said == news
    assert tts.spoken == ["I've got an update on projects-tab-refactor when you're ready.", news]
    assert brain.heard == []  # no turn for the brain to improvise around it and drop it


def test_answering_the_offer_is_answered_by_one_reply_that_carries_the_update():
    # "Yeah, let me know." was answered twice: it missed the exact go-ahead list, so the brain
    # improvised the news from memory - and the stored line then played at the next lull anyway.
    # The answer to the offer IS the delivery, and the delivery is mechanical now: the brain
    # answers his words, the app appends the stored update to that same utterance word for word,
    # and nothing is left behind to repeat or rides on the brain remembering to include it.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    brain = CarryingBrain("Sure - the fix is ready to look at on localhost:5200.")
    convo = Conversation(FakeSTT(["", "yeah, let me know", ""]), brain, tts, outbox=outbox,
                         dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("The fix is ready to look at on localhost:5200.", about="asana-submit-fix",
                composed=True)

    convo.turn()  # the offer goes out
    turn = convo.turn()  # their answer, magic words or not - the reply carries the news
    convo.turn()  # a later pass finds nothing left to deliver

    assert "The fix is ready to look at on localhost:5200." in brain.heard[-1]
    assert "OWED to him and you are its only author" in brain.heard[-1]
    assert _words(brain.heard[-1]) == "yeah, let me know"
    assert turn.said == "Sure - the fix is ready to look at on localhost:5200."
    # Spoken once: the offer, then the one reply that IS the update - never a third line.
    assert tts.spoken == [
        "I've got an update on asana-submit-fix when you're ready.",
        "Sure - the fix is ready to look at on localhost port 5200.",
    ]


def test_a_go_ahead_with_several_held_says_the_first_and_names_the_rest():
    # "I already said yes to the Highdeas-submission-feedback one. Why would you ask me this? You
    # sound insane." His yes was answered with the numbered list and "Which first?" - a question,
    # in reply to the answer to one, about updates it had just named to him. A go-ahead is a
    # go-ahead: the first one is said, and the rest are named so the choice stays open.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["", "okay"]), FakeBrain(), tts, outbox=outbox,
                         dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("fixer is ready", about="fixer", composed=True)
    outbox.push("docs-sidebar needs a call", about="docs-sidebar", composed=True)

    convo.turn()  # one offer, naming both
    turn = convo.turn()  # the go-ahead

    assert turn.said.startswith("fixer is ready")  # the update, not a question
    assert "Still waiting: docs-sidebar." in turn.said
    assert "Which first?" not in turn.said
    # The one he heard is settled; the one only NAMED is still owed, in the one store.
    assert [held.about for held in outbox.owed()] == ["docs-sidebar"]


def test_a_brain_failure_on_the_offered_turn_does_not_lose_the_news():
    # The update was popped into the turn that died; it must come back, or the one line he was
    # promised evaporates with the error.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()

    class BrokenBrain:
        def respond(self, utterance):
            raise RuntimeError("session wedged")

    convo = Conversation(FakeSTT(["", "yeah, let me know", "goodbye entity"]), BrokenBrain(), tts,
                         outbox=outbox, dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("The fix is ready to look at.", about="asana-submit-fix", composed=True)

    convo.turn()  # the offer
    convo.turn()  # the brain dies on the delivery turn
    convo.turn()  # active again: the surviving update goes out ahead of the goodbye

    assert "The fix is ready to look at." in tts.spoken


def test_the_offer_is_made_once_not_every_pass_round_the_loop():
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["", "", ""]), FakeBrain(), tts, outbox=outbox,
                         dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("news", about="fixer", composed=True)

    convo.turn()
    convo.turn()
    convo.turn()

    assert tts.spoken.count("I've got an update on fixer when you're ready.") == 1


def test_engaging_with_something_else_hands_the_reply_the_news_or_it_is_spoken_next():
    # He answered the offer with a question of his own. He is present, and the one reply folds
    # the update in around his question - a separate stored line arriving afterwards is how he
    # heard everything twice.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    brain = FakeBrain()
    convo = Conversation(FakeSTT(["", "what time is it", "goodbye entity"]), brain, tts,
                         outbox=outbox, dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("news about the fix", about="fixer", composed=True)

    convo.turn()  # the offer
    convo.turn()  # his question - the brain is handed the update to carry in that reply
    convo.turn()  # the goodbye pass: a reply that did not carry it left it owed, so it goes now

    assert "news about the fix" in brain.heard[-1]  # the goodbye never reaches the brain
    assert _words(brain.heard[-1]) == "what time is it"
    # This fake never carries anything, so the update was still owed after its reply - and the
    # next opening spoke it whole rather than losing it. Never inside a reply AND on its own.
    assert tts.spoken.count("news about the fix") == 1


def test_news_while_he_is_active_is_not_gated_behind_an_offer():
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox,
                         dormant_after=180, clock=clock)
    clock.now = 60  # he spoke recently
    outbox.push("straight news", about="fixer", composed=True)

    convo.turn()

    assert tts.spoken[0] == "straight news"


def test_stacked_news_about_one_agent_collapses_to_the_newest():
    # "Four waiting. One, asana-submit-fix. Two, asana-submit-fix..." - every turn-end while he
    # was away queued its own narration about the SAME agent, and the roll call read the same name
    # four times. Undelivered news about an agent is superseded by newer news about it: the newest
    # sentence already describes where things stand.
    outbox = Outbox()
    for stale in ("asana-submit-fix is on it.", "asana-submit-fix hit a snag but recovered.",
                  "asana-submit-fix is testing now."):
        outbox.push(stale, about="asana-submit-fix", composed=True)
    outbox.push("The fix is ready to look at on localhost:5200.", about="asana-submit-fix",
                composed=True)
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    # One line, the truth - spoken with the address the natural way ("localhost port 5200").
    assert tts.spoken[0] == "The fix is ready to look at on localhost port 5200."
    assert not any("Which first?" in line for line in tts.spoken)  # no roll call of one name


def test_news_about_different_agents_still_all_arrives():
    outbox = Outbox()
    outbox.push("fixer: done", about="fixer")
    outbox.push("fixer: really done", about="fixer")
    outbox.push("docs-sidebar: needs your call", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert tts.spoken[0] == "Two updates waiting. One, fixer. Two, docs-sidebar. Which first?"


def test_without_an_outbox_the_loop_is_unchanged():
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), tts)

    convo.turn()

    assert tts.spoken == ["reply to hi"]  # outbox=None interposes nothing


def test_a_message_arriving_during_a_lull_is_spoken_on_the_next_pass():
    outbox = Outbox()

    class LullSTT:
        # the real MicSTT yields "" when its interrupt fires during a lull; mimic that here by
        # having a message land mid-lull and the listen break off empty.
        def __init__(self):
            self.n = 0

        def listen(self):
            self.n += 1
            if self.n == 1:
                outbox.push("the deploy agent hit an error")
                return ""
            return "goodbye entity"

    tts = FakeTTS()
    convo = Conversation(LullSTT(), FakeBrain(), tts, outbox=outbox)
    convo.run()

    assert tts.spoken == ["the deploy agent hit an error", convo.farewell_reply]


class TerminatedEmptySTT:
    """Reports, per listen(), whether the (possibly empty) turn ended on the terminator - like MicSTT."""

    def __init__(self, results):
        self._results = list(results)  # (text, caught_terminator) per call
        self.caught_terminator = False

    def listen(self):
        text, self.caught_terminator = self._results.pop(0)
        return text


def test_a_bare_over_gets_a_brief_ack_so_he_knows_it_registered():
    # they said only "over"; the turn is empty but the terminator registered, so acknowledge it out
    # loud instead of ignoring them - otherwise they just repeat "over" wondering if they were heard.
    tts = FakeTTS()
    convo = Conversation(TerminatedEmptySTT([("", True)]), FakeBrain(), tts)

    result = convo.turn()

    assert result is None  # nothing to think about, so no brain call and no real turn
    assert tts.spoken == [convo.empty_turn_reply]


def test_an_empty_turn_without_a_terminator_stays_silent():
    # a lull yield (queued agent news) also returns "" - but no terminator was caught, so no ack.
    tts = FakeTTS()
    convo = Conversation(TerminatedEmptySTT([("", False)]), FakeBrain(), tts)

    assert convo.turn() is None
    assert tts.spoken == []


def test_a_blank_line_from_an_stt_without_the_flag_stays_silent():
    # ConsoleSTT has no caught_terminator; a blank typed line must not trigger the spoken ack.
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["   "]), FakeBrain(), tts)

    assert convo.turn() is None
    assert tts.spoken == []


def test_blank_utterance_is_skipped():
    stt = FakeSTT(["   "])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    turn = convo.turn()

    assert turn is None
    assert [_words(u) for u in brain.heard] == []
    assert tts.spoken == []


def test_run_loops_until_should_continue_is_false():
    stt = FakeSTT(["one", "two", "three"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    checks = {"n": 0}

    def should_continue():
        checks["n"] += 1
        return checks["n"] <= 2

    convo.run(should_continue=should_continue)

    assert [_words(u) for u in brain.heard] == ["one", "two"]
    assert tts.spoken == ["reply to one", "reply to two"]


def test_farewell_ends_the_conversation_without_asking_the_brain():
    stt = FakeSTT(["hi", "Goodbye, Excephalon.", "unreachable"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    convo.run()

    assert [_words(u) for u in brain.heard] == ["hi"]
    assert "Goodbye, Excephalon." not in brain.heard
    assert tts.spoken[-1] == convo.farewell_reply


def test_quit_and_exit_are_also_farewells():
    for word in ["quit", "Exit.", "goodbye entity"]:
        convo = Conversation(FakeSTT([word]), FakeBrain(), FakeTTS())
        assert convo.turn().farewell is True


def test_brain_failure_is_spoken_and_loop_survives():
    class BoomBrain:
        def respond(self, utterance):
            raise RuntimeError("network hiccup")

    stt = FakeSTT(["hello", "goodbye entity"])
    tts = FakeTTS()
    convo = Conversation(stt, BoomBrain(), tts)

    convo.run()

    assert any(convo.error_reply in line for line in tts.spoken)  # plainly told, not left hanging
    assert tts.spoken[-1] == convo.farewell_reply  # and the loop lived on to say goodbye


def test_a_slow_brain_failure_still_surfaces_as_the_error_reply():
    class SlowBoom:
        def respond(self, utterance):
            time.sleep(0.05)
            raise RuntimeError("hiccup after a pause")

    tts = FakeTTS()
    record = []
    convo = Conversation(FakeSTT(["hello"]), SlowBoom(), tts,
                         console=Console(record=record.append))
    turn = convo.turn()

    assert turn.error is True
    assert convo.error_reply in tts.spoken[-1]  # the plain line reached the voice
    assert any("hiccup after a pause" in line for line in record)  # the off-thread cause, kept


def test_delivered_news_leaves_the_durable_spool(tmp_path):
    # The wedge evening: reports drained into the conversation's hands died with the process,
    # and the restarted app had no trace of what it still owed. Only actual delivery - the
    # words reaching the user - clears the spool; until then a fresh outbox re-owes them.
    spool = tmp_path / "outbox.json"
    outbox = Outbox(spool=spool)
    outbox.push("fixer finished the drive link", about="fixer", composed=True)
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hello", "goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.run()

    assert any("fixer finished" in line for line in tts.spoken)
    assert not Outbox(spool=spool)  # delivered, so a restarted outbox owes nothing


def test_run_reports_each_completed_turn_to_on_turn():
    stt = FakeSTT(["a", "goodbye entity"])
    convo = Conversation(stt, FakeBrain(), FakeTTS())

    seen = []
    convo.run(on_turn=seen.append)

    assert [t.heard for t in seen] == ["a", "goodbye entity"]
    assert seen[-1].farewell is True


def test_suspend_pauses_the_brain_until_resume():
    stt = FakeSTT(["suspend", "what's the weather", "resume", "hi there", "goodbye entity"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    convo.run()

    assert [_words(u) for u in brain.heard] == ["hi there"]  # nothing reached it while paused
    assert convo.suspend_reply in tts.spoken
    assert convo.resume_reply in tts.spoken


def test_a_wake_word_with_words_after_it_still_wakes_it():
    # "Hey Excephalon. Can you hear me?" ENDS on "hear me", so an ends-with check ignored them and they had
    # to keep repeating themselves until they said the bare phrase alone.
    brain = FakeBrain()
    stt = FakeSTT(["stop listening", "hey Excephalon, can you hear me?", "so about that bug", "goodbye entity"])
    convo = Conversation(stt, brain, FakeTTS())

    convo.run()

    assert [_words(u) for u in brain.heard] == ["so about that bug"]  # woke first try, took the turn


def test_agent_news_still_arrives_while_it_is_asleep():
    # They asked outright: if they say "stop listening" and then it has something to relay from an
    # agent, does it speak up or wait for them? Sleep silences the user's turns, not the agents' news.
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["stop listening", "anything?", "goodbye entity"]),
                         FakeBrain(), tts, outbox=outbox)

    convo.turn()  # "stop listening" - now asleep
    outbox.push("the auth agent is blocked on you")
    convo.turn()  # their words are ignored while asleep, but the news is not

    assert "the auth agent is blocked on you" in tts.spoken


def test_what_it_hears_while_asleep_is_counted_not_transcribed_back():
    # Asleep it still transcribes, but only to catch the wake word. Echoing a TV's dialogue back at
    # them all evening is noise; a collapsing count says "heard you, ignoring you" without the scroll.
    lines = []
    console = Console(echo=lines.append, overwrite=lines.append)
    convo = Conversation(FakeSTT(["stop listening", "some TV dialogue", "more TV dialogue", "goodbye entity"]),
                         FakeBrain(), FakeTTS(), console=console)

    convo.turn()  # "stop listening" - now asleep
    convo.turn()
    convo.turn()

    assert not any("TV dialogue" in line for line in lines)  # never echoed back at them
    assert lines[-1] == "\r(ignoring… 2x)"  # just a tally that ticks up in place


def test_it_says_it_is_listening_before_it_listens():
    lines = []
    console = Console(echo=lines.append, overwrite=lines.append)
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), FakeTTS(), console=console)

    convo.turn()

    assert lines[0].startswith("(listening")  # before anything they said could be echoed


def test_it_does_not_claim_to_be_listening_while_it_is_asleep():
    # "(listening…)" while asleep is a flat lie - it's transcribing to catch the wake word and
    # throwing the rest away.
    lines = []
    console = Console(echo=lines.append, overwrite=lines.append)
    convo = Conversation(FakeSTT(["stop listening", "some TV dialogue", "goodbye entity"]),
                         FakeBrain(), FakeTTS(), console=console)

    convo.turn()  # "stop listening" - now asleep
    lines.clear()
    convo.turn()

    assert not any("(listening" in line for line in lines)


def test_what_he_hears_is_in_the_record_even_when_the_terminal_does_not_show_it():
    # Reading a session back has to hold everything they heard, printed or not - the farewell here
    # is spoken and recorded even though the terminal shows its own goodbye elsewhere.
    recorded = []
    console = Console(echo=lambda _: None, overwrite=lambda _: None, record=recorded.append)
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), FakeTTS(), console=console)

    convo.turn()

    assert recorded.count("excephalon> reply to hi\n") == 1  # a printed reply isn't recorded twice


def test_a_reply_cut_off_mid_utterance_is_noted_in_the_record():
    # "You didn't say that aloud. You only wrote it on the screen." - the record showed the line as
    # delivered, with nothing to say the voice was killed partway. Now the cut is on the record.
    recorded = []
    console = Console(echo=lambda _: None, overwrite=lambda _: None, record=recorded.append)
    interrupt = threading.Event()

    class CutOffTTS:
        def speak(self, text, *, interrupt=None):
            interrupt.set()  # the watcher (or Enter) kills the utterance partway through

    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), CutOffTTS(),
                         interrupt=interrupt, console=console)

    convo.turn()

    assert any("cut off" in line for line in recorded)


def test_a_voice_failure_is_noted_in_the_record_not_lost_to_stderr():
    recorded = []
    console = Console(echo=lambda _: None, overwrite=lambda _: None, record=recorded.append)

    class BrokenTTS:
        def speak(self, text, *, interrupt=None):
            raise RuntimeError("powershell exploded")

    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), BrokenTTS(), console=console)

    convo.turn()  # must not crash the loop

    assert any("voice failed" in line and "powershell exploded" in line for line in recorded)


def test_whatever_the_outbox_has_to_say_is_one_utterance_to_stop():
    # They had to hit stop over and over while a report came at them line by line. Whatever goes
    # out - the roll call naming several, or one agent's news - is one utterance, so one stop ends
    # it. An agent with no name to it falls back to its own words, which is what these are.
    outbox = Outbox()
    outbox.push("first agent finished")
    outbox.push("second agent finished")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    spoken = [line for line in tts.spoken if "agent finished" in line]
    assert len(spoken) == 1  # one utterance, so one STOP silences all of it
    assert "first agent finished" in spoken[0] and "second agent finished" in spoken[0]


def test_news_deferred_mid_sentence_is_retried_at_the_next_pause():
    # Two failure modes share this moment, and both are pinned. THE FREEZE: the mic used to
    # yield an empty turn whenever outbox.arrived was set, talking or not, so a latched flag
    # spun the loop and ate every submission - the mic now yields on it only at a PAUSE
    # (dictation.listen), so a standing flag can never wedge dictation. THE SILENT STALL: this
    # pass drains the flag, and deferring with it down left no way back until his next words -
    # held news sat unspoken for nine minutes and he had to ask ("I keep having to prompt this
    # thing for updates"). So a defer puts the flag back up: still waiting to be spoken is
    # exactly what the flag means.
    outbox = Outbox()

    class MidSentenceSTT(FakeSTT):
        def is_mid_utterance(self):
            return True  # they are talking the whole time

    convo = Conversation(MidSentenceSTT(["never read"]), FakeBrain(), FakeTTS(), outbox=outbox)
    outbox.push("the fixer agent has news")

    convo._deliver_outbox()  # can't speak over them - but must still drain

    assert [str(held) for held in convo._outbox.owed()] == ["the fixer agent has news"]  # in hand
    assert outbox.arrived.is_set()  # and flagged, so the next pause yields a delivery turn


def test_it_stays_quiet_while_they_are_mid_sentence_then_delivers():
    outbox = Outbox()
    outbox.push("the fixer agent has news")
    tts = FakeTTS()

    class MicSTT(FakeSTT):
        talking = True

        def is_mid_utterance(self):
            return self.talking

    stt = MicSTT(["", "goodbye entity"])
    convo = Conversation(stt, FakeBrain(), tts, outbox=outbox)

    convo.turn()
    assert not any("fixer" in line for line in tts.spoken)  # they're talking; it does not break in

    stt.talking = False
    convo.turn()
    assert any("fixer" in line for line in tts.spoken)  # they stopped - held news goes out


def test_a_brain_failure_speaks_plainly_and_keeps_the_cause_in_the_record():
    # Two of his requirements meet here. "I would prefer at that point that the underlying error
    # just be leaked" - the cause must land somewhere USEFUL, because stderr under pythonw goes
    # nowhere and an unexplained failure "has never said that and recovered". But spoken, the
    # cause read "_AskWedged" to him aloud - a code identifier straight through the insulation.
    # So the voice gets the plain sentence, and the durable session record gets the cause.
    class BrokenBrain:
        def respond(self, utterance):
            raise RuntimeError("the CLI exited with code 1")

    tts = FakeTTS()
    record = []
    convo = Conversation(FakeSTT(["hi"]), BrokenBrain(), tts,
                         console=Console(record=record.append))

    turn = convo.turn()

    assert turn.error is True
    assert "RuntimeError" not in turn.said  # nothing technical is spoken or shown as the reply
    assert convo.error_reply in tts.spoken
    kept = " ".join(record)
    assert "the CLI exited with code 1" in kept  # the real cause, durable
    assert "RuntimeError" in kept  # and what kind, so a repeat is recognisable


def test_a_dead_sign_in_is_answered_with_the_fix_not_with_try_again():
    # "Something's broken in my head - give me a moment, then ask me again" was said about an
    # expired Claude sign-in - a failure no moment and no restart fixes, so he restarted on that
    # advice and met the same wall ("Something is broken in Excephalon's head right now, even
    # after a restart"). The one brain failure the user can fix is answered with the fix.
    from excephalon.sdk_session import BrainUnavailable

    class SignedOutBrain:
        def respond(self, utterance):
            raise BrainUnavailable("authentication_failed")

    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), SignedOutBrain(), tts)

    turn = convo.turn()

    assert turn.error is True
    assert "/login" in " ".join(tts.spoken)
    assert convo.error_reply not in tts.spoken  # "ask me again" would be a lie here


def test_a_dead_sign_in_opens_the_terminal_itself_when_it_can_and_says_so():
    # "ideally Excephalon should do more than just tell me what to do, but pop open whatever I
    # need to do it and run it itself if possible." With the helper wired (as __main__ wires it),
    # the terminal opens and the reply matches the open door; if opening fails, the steps are
    # spoken instead - never a claim of a door that is not there.
    from excephalon.sdk_session import BrainUnavailable

    class SignedOutBrain:
        def respond(self, utterance):
            raise BrainUnavailable("authentication_failed")

    opened = []
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), SignedOutBrain(), tts,
                         sign_in_helper=lambda: opened.append(True) or True)

    convo.turn()

    assert opened == [True]
    assert any("opened a terminal" in line for line in tts.spoken)

    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), SignedOutBrain(), tts, sign_in_helper=lambda: False)
    convo.turn()
    assert any("Open a terminal" in line for line in tts.spoken)  # the steps, since nothing opened


def test_a_requested_update_is_spoken_by_the_app_word_for_word_after_the_turn():
    # The deep fix for one piece of news in two mouths: asked for an agent's update in a full
    # sentence, the brain hands it over (deliver_update) instead of retelling it, and the app
    # speaks the held copy word for word at the first opening - one teller, the exact words.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")
    outbox.push("docs-sidebar: needs your call on the width", about="docs-sidebar")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["what time is it", "goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox)

    convo.turn()  # a roll call of two; both held in hand
    outbox.request("fixer")  # what the brain's deliver_update tool records mid-turn
    convo.turn()

    spoken = "\n".join(tts.spoken)
    assert "fixer: the drive link is fixed" in spoken  # word for word, from the app
    assert "Still waiting: docs-sidebar." in spoken    # and what remains is named


def test_the_error_line_is_spoken_under_its_own_leak_script_not_the_turns():
    # The wedge reply was once spoken while the turn's floor still held the mic, scripted with
    # the streamed reply - of which a wedge never streamed a word. Judged against that empty
    # script, the error line's own audio came back through the mic as the user's draft words.
    # The floor is released first now, so the line is watched with its own words as the script.
    scripts = []

    class DuplexSTT:
        def listen(self):
            return "hello"

        def catch_stop(self, active, script=None, audio=None):
            scripts.append(script)
            while active():
                time.sleep(0.005)
            return False

    class BrokenBrain:
        def respond(self, utterance):
            raise RuntimeError("session wedged")

    convo = Conversation(DuplexSTT(), BrokenBrain(), FakeTTS(), interrupt=threading.Event())

    turn = convo.turn()

    assert turn.error is True
    error_watch = scripts[-1]  # the watcher opened for the error line itself
    assert error_watch is not None
    assert "broken in my head" in error_watch()


def test_a_failure_names_the_error_underneath_the_librarys_guess():
    # The SDK raises "Claude Code not found at <path>" for ANY FileNotFoundError while spawning, so
    # it blamed the CLI while the CLI sat there, 252MB, untouched - and that guess was the whole of
    # what there was to go on. The real error is chained underneath it and says which file.
    class BrokenBrain:
        def respond(self, utterance):
            try:
                raise FileNotFoundError(2, "No such file or directory", "C:/gone/config.json")
            except FileNotFoundError as underneath:
                raise RuntimeError("Claude Code not found at: C:/present/claude.exe") from underneath

    tts = FakeTTS()
    record = []
    convo = Conversation(FakeSTT(["hi"]), BrokenBrain(), tts,
                         console=Console(record=record.append))

    convo.turn()

    kept = " ".join(record)
    assert "Claude Code not found" in kept  # what the library claimed
    assert "C:/gone/config.json" in kept  # and what had actually gone missing


def test_it_is_told_what_was_said_in_its_name_that_it_did_not_write():
    # "If what I consider to be one Entity is actually a bunch of disconnected fakers who aren't
    # aware of each other, then the flimsy occasional illusion of you being a coherent Entity is
    # worse than useless." They kept quoting lines back that the brain had no record of, because the
    # app speaks in its name - an agent's notice, a held roll call - and none of it ever reached the
    # brain. Asked about one, it said "I have no record of typing that myself", which was true, and
    # read as gaslighting.
    heard = []

    class NotingBrain:
        def respond(self, utterance):
            heard.append(utterance)
            return "a reply"

    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed")
    convo = Conversation(FakeSTT(["", "what did you just say to me"]), NotingBrain(), FakeTTS(),
                         outbox=outbox)

    convo.turn()  # a lull: the agent's notice goes out in its name, unwritten by it
    convo.turn()  # and now they ask about it

    assert "fixer: the drive link is fixed" in heard[0]  # it knows what they heard
    assert "what did you just say to me" in heard[0]  # and their words are still in there


def test_news_the_brain_composed_is_not_read_back_to_it_as_someone_elses():
    # The narrator asks the brain to word each interjection, so the brain REMEMBERS saying it -
    # feeding it back through the unwritten-lines ledger would tell it about its own sentence.
    heard = []

    class NotingBrain:
        def respond(self, utterance):
            heard.append(utterance)
            return "a reply"

    outbox = Outbox()
    outbox.push("The drive work's done - it just needs your eyes.", about="fixer", composed=True)
    convo = Conversation(FakeSTT(["", "what needs my eyes exactly"]), NotingBrain(), FakeTTS(),
                         outbox=outbox)

    convo.turn()  # the composed line goes out at the lull
    convo.turn()

    # No unwritten-lines note: it already knows it said it. (The standing conduct note rides
    # on every turn and is not about lines said in its name.)
    assert [_words(u) for u in heard] == ["what needs my eyes exactly"]
    assert "spoken to them in YOUR name" not in heard[0]


def test_its_own_answers_are_not_read_back_to_it_as_someone_elses():
    # Only lines it is NOT already aware of. Its own reply is in its context already.
    heard = []

    class NotingBrain:
        def respond(self, utterance):
            heard.append(utterance)
            return "the drive icon opens the folder now"

    convo = Conversation(FakeSTT(["did it work", "thanks"]), NotingBrain(), FakeTTS())

    convo.turn()
    convo.turn()

    assert _words(heard[1]) == "thanks"  # nothing to report: it wrote its own last line itself
    assert "spoken to them in YOUR name" not in heard[1]


class StreamingTTS(FakeTTS):
    """A voice with stream(): sentences sound as the reply is written, like the neural Speaker."""

    def __init__(self):
        super().__init__()
        self.replies = []
        self.spoken_forms = []

    def stream(self, *, interrupt=None, spoken_form=None):
        self.spoken_forms.append(spoken_form)
        reply = self.Reply(self, interrupt)
        self.replies.append(reply)
        return reply

    class Reply:
        def __init__(self, tts, interrupt):
            self._tts = tts
            self._interrupt = interrupt
            self.deltas = []
            self.finished = False

        def add(self, delta):
            self.deltas.append(delta)

        def done(self):
            self.finished = True
            heard = "".join(self.deltas)
            if self._interrupt is not None and self._interrupt.is_set():
                heard = heard[: len(heard) // 2]  # the cut came partway through the audio
            self._tts.spoken.append(heard)
            return heard


class StreamingBrain:
    """A brain that streams its reply out in deltas before returning it whole."""

    def __init__(self, text="Both are green. The drive one wants a decision."):
        self._text = text
        self.heard = []

    def respond(self, utterance, *, on_text=None):
        self.heard.append(utterance)
        if on_text is not None:
            middle = len(self._text) // 2
            on_text(self._text[:middle])
            on_text(self._text[middle:])
        return self._text


def test_a_streaming_voice_speaks_the_reply_as_the_brain_writes_it():
    # The whole point of the rebuild: the deltas reach the voice DURING the think, so first words
    # are heard while the rest of the reply is still being written - no ack, no handoff line.
    tts = StreamingTTS()
    brain = StreamingBrain()
    convo = Conversation(FakeSTT(["how's it going"]), brain, tts)

    turn = convo.turn()

    [reply] = tts.replies
    assert "".join(reply.deltas) == "Both are green. The drive one wants a decision."
    assert reply.finished  # the loop waited out the audio before listening again
    assert turn.said == "Both are green. The drive one wants a decision."


def test_a_stray_goodbye_in_a_streamed_reply_never_reaches_the_voice_or_the_screen():
    # "Wait, why did you say be seeing you? I thought you only say that when I'm closing you."
    # The instruction filed against it is exactly the duty-shaped rule the fast tier keeps
    # missing, so the code holds the door: the goodbye sentence is dropped from the audio as
    # it streams and from the record after, and is only ever heard when the app closes.
    tts = StreamingTTS()
    brain = StreamingBrain("Both agents are green. Be seeing you.")
    convo = Conversation(FakeSTT(["how's it going"]), brain, tts)

    turn = convo.turn()

    [reply] = tts.replies
    assert "".join(reply.deltas) == "Both agents are green."
    assert turn.said == "Both agents are green."


def test_a_reply_that_is_only_the_stray_goodbye_becomes_a_silent_turn():
    # The incident's exact shape: mid-conversation, the whole reply was "Be seeing you."
    tts = StreamingTTS()
    convo = Conversation(FakeSTT(["thanks"]), StreamingBrain("Be seeing you."), tts)

    turn = convo.turn()

    assert turn.said == ""
    assert not any(tts.spoken)  # nothing sounded, and no goodbye reached the record


def test_the_goodbye_mid_sentence_is_not_the_misfire_and_passes(): 
    # Only the standalone closing line is the misfire - the words inside a real sentence are
    # the brain talking, and eating them would garble it.
    tts = StreamingTTS()
    brain = StreamingBrain("I'll be seeing you at the demo tomorrow.")
    convo = Conversation(FakeSTT(["ok"]), brain, tts)

    turn = convo.turn()

    assert turn.said == "I'll be seeing you at the demo tomorrow."
    [reply] = tts.replies
    assert "".join(reply.deltas) == turn.said


def test_the_real_farewell_still_says_its_line():
    # The gate is for STRAY goodbyes; the app's own closing line on a real goodbye is untouched.
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts)

    turn = convo.turn()

    assert turn.farewell is True
    assert convo.farewell_reply in tts.spoken


def test_a_streamed_reply_speaks_paths_the_way_a_person_would():
    # The one sanctioned difference between ear and screen, carried over from the one-shot path:
    # the screen shows the real path (it is what gets clicked); the voice says its filename.
    from excephalon.links import as_spoken

    tts = StreamingTTS()
    convo = Conversation(FakeSTT(["where"]), StreamingBrain(), tts)

    convo.turn()

    assert tts.spoken_forms == [as_spoken]


def test_the_bubble_appears_while_the_voice_is_still_speaking():
    # "the text bubble doesn't appear until after it's been read aloud, but it should definitely
    # happen in the opposite order, so if I'm there I can read along with it speaking."
    events = []

    class WatchedTTS(StreamingTTS):
        class Reply(StreamingTTS.Reply):
            def done(self):
                events.append("audio finished")
                return super().done()

    console = Console(echo=lambda line: events.append(f"print:{line}"))
    convo = Conversation(FakeSTT(["status"]), StreamingBrain(), WatchedTTS(), console=console)

    convo.turn()

    printed = next(e for e in events if e.startswith("print:excephalon>"))
    assert events.index(printed) < events.index("audio finished")  # read along, not read after


def test_a_streamed_reply_reaches_the_screen_and_the_record_whole():
    lines, recorded = [], []
    console = Console(echo=lines.append, record=recorded.append)
    convo = Conversation(FakeSTT(["status"]), StreamingBrain(), StreamingTTS(), console=console)

    convo.turn()

    assert any("Both are green." in line for line in lines)  # on screen
    assert any("Both are green." in line for line in recorded)  # and on the record


def test_a_streamed_reply_cut_off_partway_is_recorded_as_cut():
    # What was heard and what is on the record must agree: after a barge-in the record says the
    # voice was killed partway, instead of showing the reply as delivered.
    recorded = []
    console = Console(echo=lambda _: None, overwrite=lambda _: None, record=recorded.append)
    interrupt = threading.Event()

    class BargedBrain(StreamingBrain):
        def respond(self, utterance, *, on_text=None):
            said = super().respond(utterance, on_text=on_text)
            interrupt.set()  # they cut in while the audio is still going out
            return said

    convo = Conversation(FakeSTT(["status"]), BargedBrain(), StreamingTTS(),
                         interrupt=interrupt, console=console)

    convo.turn()

    assert any("cut off" in line for line in recorded)


def test_a_brain_without_streaming_still_works_with_a_streaming_voice():
    # ConsoleSTT runs the same loop with the same voice; a brain fake with the plain respond()
    # signature must not be forced to grow one.
    tts = StreamingTTS()
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), tts)

    turn = convo.turn()

    assert turn.said == "reply to hi"
    assert "reply to hi" in tts.spoken  # spoken whole through speak(), not streamed


def test_the_fleet_briefing_is_in_front_of_the_brain_every_turn():
    # "How's it going" must be answerable THIS turn: the state of every agent rides into the brain
    # as text the app composed, so no tool call and no file read stands between the question and
    # its answer.
    heard = []

    class NotingBrain:
        def respond(self, utterance):
            heard.append(utterance)
            return "reply"

    convo = Conversation(FakeSTT(["how's it going"]), NotingBrain(), FakeTTS(),
                         briefing=lambda: "fixer: working - task: fix the drive link")

    convo.turn()

    assert "[Fleet briefing" in heard[0]
    assert "fixer: working" in heard[0]
    assert heard[0].rstrip().endswith("how's it going")  # their words close the message


def test_an_empty_briefing_adds_nothing():
    heard = []

    class NotingBrain:
        def respond(self, utterance):
            heard.append(utterance)
            return "reply"

    convo = Conversation(FakeSTT(["hi"]), NotingBrain(), FakeTTS(), briefing=lambda: "")

    convo.turn()

    assert _words(heard[0]) == "hi"
    assert "[Fleet briefing" not in heard[0]  # an empty briefing earns no empty box


def test_every_turn_carries_the_standing_conduct_note():
    # Three behaviors survived their persona bans and had to be re-corrected live: announcing a
    # tool call before making it and again after (the same sentence twice in one bubble), the
    # "You're absolutely right" reflex, and running counts that mix old work into new ("all six
    # items filed" when three were). A rule read once at session start loses to habit by
    # mid-session; this one rides in front of the brain on every single turn.
    brain = FakeBrain()
    convo = Conversation(FakeSTT(["hi"]), brain, FakeTTS())

    convo.turn()

    [prompt] = brain.heard
    assert "act first" in prompt.lower()
    assert "absolutely right" in prompt.lower()
    assert "this turn" in prompt.lower()


def test_fresh_news_about_an_agent_already_listed_does_not_repeat_the_same_sentence():
    # This used to re-read the roll call whenever the NEWS behind it changed, so that a fresh
    # report could not sit silent. But a roll call says only names: with the same agents waiting
    # it comes out word for word identical, and re-reading it delivers nothing while sounding
    # broken - "why did it just give me the same message twice in a row?" (22:22:59 and 22:23:07).
    # The list stands, his numbers still hold, and picking one is what delivers its newest news.
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["", "", "", "one"]), FakeBrain(), tts, outbox=outbox)
    outbox.push("fixer is still going", about="fixer")
    outbox.push("builder is still going", about="builder")

    convo.turn()  # the roll call names both
    assert any("fixer" in line and "builder" in line for line in tts.spoken)
    spoken_so_far = len(tts.spoken)

    convo.turn()  # nothing has changed - and nothing is said again
    assert len(tts.spoken) == spoken_so_far

    outbox.push("fixer presented its work for your eyes", about="fixer")
    convo.turn()
    assert len(tts.spoken) == spoken_so_far  # same names, same sentence: not said twice

    convo.turn()  # and when he takes number one, what he gets is the NEWEST of fixer's news
    assert "fixer presented its work for your eyes" in "\n".join(tts.spoken)


def test_a_pasted_report_never_reaches_the_voice_or_the_record():
    # "Whoa whoa whoa whoa whoa. That message is huge. That's like ten times bigger than I ever
    # want you to send a message to me." The brain relayed an agent's whole markdown report -
    # blockquote lines, launcher link, sign-off - inside its reply. A reply is speech, and
    # speech has no blockquote: a ">"-led line is a document quoted into the mouth, dropped
    # before it can sound and off the record's copy alike.
    tts = FakeTTS()

    class PastingBrain(FakeBrain):
        def respond(self, utterance):
            super().respond(utterance)
            return ("The fix is ready for your eyes.\n\n"
                    "> Done - the choice is now saved on the server in preferences.json.\n"
                    "> 1. Click it - a window opens with two demo notes.\n"
                    "> Nothing is pushed or merged - say the word and I'll land it.\n\n"
                    "Say the word when you've looked.")

    convo = Conversation(FakeSTT(["how's the autoplay fix", "goodbye entity"]), PastingBrain(),
                         tts, outbox=Outbox())
    convo.turn()
    convo.turn()

    said = tts.spoken[0]
    assert "ready for your eyes" in said
    assert "Say the word when you've looked." in said
    assert "preferences.json" not in said  # not one pasted line sounded
    assert ">" not in said


def test_news_about_other_work_holds_while_his_eyes_are_on_one_thing():
    # "Don't ask me about updates for other items when we've already picked one of them to be
    # working on." - and then, because nothing offered the held items when the review DID close,
    # "Now would be a good time to ask about the other two updates," which he should never have
    # had to say. While a review is open, other news holds; the moment it closes, the list is
    # offered on its own.
    outbox = Outbox()
    outbox.push("names: the naming layer is ready for your eyes", about="names")
    outbox.push("autoplay: the auto-play fix is ready for your eyes", about="autoplay")
    reviewing = {"spinner"}
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["looks good, one note though", "goodbye entity"]), FakeBrain(),
                         tts, outbox=outbox, in_review=lambda: reviewing)

    convo.turn()  # the review is open: the two held updates stay held, no menu, no offer
    assert not [line for line in tts.spoken if "updates waiting" in line]

    reviewing.clear()  # his verdict closed the review
    convo.turn()

    assert any("Two updates waiting" in line for line in tts.spoken)


def test_a_walkthrough_carries_no_menu_on_its_back():
    # His "Yes." at 19:56 was answered with the spinner walkthrough AND "Two updates waiting...
    # Which first?" welded to it - a menu read out at the exact moment his eyes went onto one
    # thing. Delivering a walkthrough OPENS a review, so the list waits for his verdict.
    outbox = Outbox()
    outbox.push("spinner: ready for your eyes - open localhost:5599", about="spinner")
    outbox.push("names: the naming layer is ready too", about="names")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["one", "goodbye entity"]), FakeBrain(), tts, outbox=outbox,
                         review_opens=lambda name: name == "spinner")

    convo.turn()  # the roll call goes out; he picks the spinner by number
    picked = next(line for line in tts.spoken if "localhost port 5599" in line)

    assert "updates waiting" not in picked  # the walkthrough went out alone, review now open
    convo.turn()


def test_a_pick_spends_the_offer_so_the_leftover_never_rides_unrelated_words():
    # He picked one of two offered updates; the offer stayed latched, and his NEXT words - "The
    # ship it still stands.", about a different agent entirely - came back with the leftover
    # spinner walkthrough welded on ("it shouldn't be providing information about more than one
    # different task in a single message").
    outbox = Outbox()
    outbox.push("names: the naming layer is ready", about="names")
    outbox.push("spinner: the spinner is ready", about="spinner")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["one", "the ship it still stands", "goodbye entity"]),
                         FakeBrain(), tts, outbox=outbox)
    convo._update_offered = True  # the offer that read both names out is standing

    convo.turn()  # "one" picks the names update
    convo.turn()  # unrelated words: the spinner must NOT ride this reply

    reply = next(line for line in tts.spoken if "reply to" in line)
    assert "spinner" not in reply
    assert [getattr(h, "about", None) for h in convo._outbox.owed()] == ["spinner"]  # still held
    convo.turn()


def test_a_held_update_about_other_work_does_not_ride_his_reply_mid_review():
    # One thing at a time holds for the offered-rides-the-turn path too: with his eyes on one
    # piece of work, a leftover update about another waits for the gate, never welds itself to
    # whatever he says next.
    outbox = Outbox()
    outbox.push("spinner: the spinner is ready", about="spinner")
    tts = FakeTTS()
    reviewing = {"names"}
    convo = Conversation(FakeSTT(["the ship it still stands", "goodbye entity"]), FakeBrain(),
                         tts, outbox=outbox, in_review=lambda: reviewing)
    convo._update_offered = True

    convo.turn()

    reply = next(line for line in tts.spoken if "reply to" in line)
    assert "spinner" not in reply
    assert [getattr(h, "about", None) for h in convo._outbox.owed()] == ["spinner"]
    convo.turn()


def test_a_greeting_can_no_longer_say_the_same_thing_as_the_news_behind_it():
    # "Agent naming is still waiting for your verdict-ready to look at it?" welded straight onto a
    # walkthrough opening "Agent naming is waiting for your verdict" - the same sentence twice in
    # one message ("it repeats ... twice in a row like an insane person"). A similarity check used
    # to drop the greeting when it happened. Nothing to check now: the greeting and the news are
    # never in one utterance at all, so a repeat cannot be built.
    outbox = Outbox()
    outbox.push("Agent naming is waiting for your verdict - names now get the project prefix.",
                about="namer")
    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox,
        opening="Back with you. Agent naming is still waiting for your verdict.")

    convo.turn()

    assert tts.spoken[0] == "Back with you. Agent naming is still waiting for your verdict."
    assert not any("project prefix" in line for line in tts.spoken)  # the news is still owed


def test_a_brain_that_answers_his_words_with_nothing_is_asked_again():
    # "Yes, looks good. Ship it." - and the reply was dead air: the turn was spent on tool calls
    # and wrote no words, so he heard nothing, assumed nothing had happened, and waited half an
    # hour for an update on a landing nobody had recorded. Silence is never an answer to his
    # words, and the second ask is told what went wrong rather than repeating the turn blind.
    asked = []

    class MuteThenSpeaking(FakeBrain):
        def respond(self, utterance):
            asked.append(utterance)
            return "" if len(asked) == 1 else "Recorded - it's landing now."

    tts = FakeTTS()
    convo = Conversation(FakeSTT(["yes looks good ship it", "goodbye entity"]),
                         MuteThenSpeaking(), tts)

    turn = convo.turn()

    assert turn.said == "Recorded - it's landing now."
    assert tts.spoken == ["Recorded - it's landing now."]
    assert "produced no words" in asked[1]  # told plainly, not asked the same thing twice
    convo.turn()


def test_two_silent_turns_running_are_said_to_be_a_fault_never_dead_air():
    tts = FakeTTS()

    class Mute(FakeBrain):
        def respond(self, utterance):
            super().respond(utterance)
            return ""

    convo = Conversation(FakeSTT(["ship it", "goodbye entity"]), Mute(), tts)

    turn = convo.turn()

    assert turn.said and turn.said == tts.spoken[0]  # he hears SOMETHING, never nothing
    convo.turn()


def test_a_silence_alarm_never_destroys_a_report_from_the_same_agent():
    # "been silent for 20 minutes" arrived twenty minutes after that agent's merge report and,
    # being the newest news about it, superseded it - the one thing he was waiting for was
    # destroyed by a timer's guess about an agent that had already finished.
    outbox = Outbox()
    outbox.push("fixer: landed and wrapped up", about="fixer", kind="landed")
    outbox.push("fixer: been silent for 20 minutes", about="fixer", kind="quiet")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert tts.spoken[0] == "fixer: landed and wrapped up"
    assert not outbox.owed_about()  # and the alarm did not linger as news of its own


def test_a_landing_report_is_never_held_behind_a_review():
    # One thing at a time holds other threads' chatter - never a thread's LAST WORD. Held, a
    # merge report is the black hole this project has already sat through once.
    outbox = Outbox()
    outbox.push("fixer: it merged and is wrapped up", about="fixer", kind="landed")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox,
                         in_review=lambda: {"spinner"})

    convo.turn()

    assert tts.spoken[0] == "fixer: it merged and is wrapped up"


def test_a_review_nobody_ever_ruled_on_stops_holding_the_fleet():
    # The gate silences every other thread, so it may never outlive its premise: a verdict that
    # never got RECORDED left a review open forever, and behind it he had a merge report he was
    # asking for. Past a couple of his turns with no verdict, his attention has plainly moved.
    outbox = Outbox()
    outbox.push("names: the naming layer is ready for your eyes", about="names")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["ship it", "what about the names", "and now", "goodbye entity"]),
                         FakeBrain(), tts, outbox=outbox, in_review=lambda: {"spinner"})

    convo.turn()
    assert not [line for line in tts.spoken if "naming layer" in line]  # held: he is reviewing
    convo.turn()
    convo.turn()

    assert any("naming layer" in line for line in tts.spoken)
    convo.turn()


def test_his_wait_is_bounded_even_when_the_brain_never_answers():
    # "it seems to be stuck again. I said ship it then it never said anything" - the turn sat
    # twelve minutes with no word. The brain bounds each of its own asks, but one turn can spend
    # several (lock, shed, reconnect, ask again); HIS silence is bounded here, and the turn's
    # existing brain-failure path keeps the update owed and says a plain line.
    import threading as _threading

    let_go = _threading.Event()
    clock = [0.0]

    class Hanging(FakeBrain):
        def respond(self, utterance):
            super().respond(utterance)
            let_go.wait(2.0)
            return "far too late to be an answer"

        def interrupt(self):
            let_go.set()

    tts = FakeTTS()
    convo = Conversation(FakeSTT(["ship it", "goodbye entity"]), Hanging(), tts,
                         answer_within=30.0, clock=lambda: clock[0], sleep=lambda s: None)
    original = convo._interrupted

    def tick():  # the wall clock runs while the brain does not
        clock[0] += 20.0
        return original()

    convo._interrupted = tick

    turn = convo.turn()

    assert turn.error is True
    assert tts.spoken and tts.spoken[0] == convo.error_reply  # he hears something, not silence
    convo._interrupted = original
    convo.turn()


def test_a_reply_still_arriving_is_never_cut_off_by_that_bound():
    # A brain that keeps writing is a brain doing its job, however long the whole answer takes:
    # each delta resets his wait, so only a STALL ends the turn.
    import threading as _threading

    clock = [0.0]
    tts = StreamingTTS()
    keep_going = _threading.Event()

    class SlowButWriting(StreamingBrain):
        def respond(self, utterance, *, on_text=None):
            for piece in ("Working on it - ", "still going - ", "here is the answer."):
                clock[0] += 20.0  # under the 30s bound each time, so the wait keeps resetting
                on_text(piece)
            keep_going.set()
            return "Working on it - still going - here is the answer."

    convo = Conversation(FakeSTT(["ship it"]), SlowButWriting(""), tts,
                         answer_within=30.0, clock=lambda: clock[0], sleep=lambda s: None)

    turn = convo.turn()

    assert turn.said == "Working on it - still going - here is the answer."
    assert not turn.error


def test_a_turn_busy_with_his_own_errand_is_never_mistaken_for_a_wedge():
    # "Can we go through a demo of your ability to manage my day based on a calendar that you've
    # prepared." Ninety seconds later: "Something's broken in my head." Nothing was broken - it
    # was reading his calendar, which is minutes of tool calls and not one word until the answer.
    # Measured on WORDS, doing what he asked looked exactly like having died. Every message the
    # model sends is the turn moving, so only real silence ends the wait.
    clock = [0.0]
    tts = StreamingTTS()

    class ReadingHisCalendar(StreamingBrain):
        def respond(self, utterance, *, on_text=None, on_activity=None):
            for _ in range(6):  # six tool round-trips, 20s apart, no words at all
                clock[0] += 20.0
                on_activity("a tool call and its result")
            on_text("You've got three things today.")
            return "You've got three things today."

    convo = Conversation(FakeSTT(["walk me through my day"]), ReadingHisCalendar(""), tts,
                         answer_within=30.0, clock=lambda: clock[0], sleep=lambda s: None)

    turn = convo.turn()

    assert turn.said == "You've got three things today."
    assert not turn.error


def test_a_reply_that_stops_part_way_ends_the_wait_like_any_other_silence():
    # "I give my approval for a feature for the 4th time and Excephalon is still not responding
    # at all" - measured on total silence, the bound never fired: the brain had written one
    # clause before it hung, so "it has said something" stayed true while he sat for twenty
    # minutes. Progress is what the wait is measured against, not whether anything was ever said.
    import threading as _threading

    let_go = _threading.Event()
    clock = [0.0]
    tts = StreamingTTS()

    class StopsPartWay(StreamingBrain):
        def respond(self, utterance, *, on_text=None):
            on_text("Recording that - ")
            let_go.wait(2.0)  # and then nothing, ever
            return "Recording that - too late."

        def interrupt(self):
            let_go.set()

    convo = Conversation(FakeSTT(["ship it", "goodbye entity"]), StopsPartWay(""), tts,
                         answer_within=30.0, clock=lambda: clock[0], sleep=lambda s: None)
    original = convo._interrupted

    def tick():  # the wall clock runs while the brain does not
        clock[0] += 20.0
        return original()

    convo._interrupted = tick

    turn = convo.turn()

    assert turn.error is True
    assert convo.error_reply in tts.spoken  # he hears something, not a stalled half-sentence
    convo._interrupted = original
    convo.turn()
