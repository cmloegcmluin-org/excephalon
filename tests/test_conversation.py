import threading
import time

from excephalon.console import Console
from excephalon.conversation import Conversation, Turn
from excephalon.outbox import Outbox


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
    convo.turn()  # fresh news: the list is re-read, every agent still at its old number
    convo.turn()  # so "one" still means fixer - and yields its newest sentence

    spoken = "\n".join(tts.spoken)
    assert len([line for line in tts.spoken
                if line == "Two updates waiting. One, fixer. Two, docs-sidebar. Which first?"]) == 2
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


def test_answering_the_offer_hands_the_update_to_the_brain_to_deliver_once():
    # "Yeah, let me know." was answered twice: it missed the exact go-ahead list, so the brain
    # improvised the news from memory as an ordinary turn - and the stored line then played at the
    # next lull anyway. The answer to the offer IS the delivery: with one update held, whatever
    # they say next carries it into the brain's prompt, the brain says it once, and nothing is
    # left behind to repeat.
    clock = FakeClock()
    outbox = Outbox()
    tts = FakeTTS()
    brain = FakeBrain()
    convo = Conversation(FakeSTT(["", "yeah, let me know", ""]), brain, tts, outbox=outbox,
                         dormant_after=180, clock=clock)
    clock.now = 600
    outbox.push("The fix is ready to look at on localhost:5200.", about="asana-submit-fix",
                composed=True)

    convo.turn()  # the offer goes out
    turn = convo.turn()  # their answer, magic words or not - the reply carries the news
    convo.turn()  # a later pass finds nothing left to deliver

    assert "The fix is ready to look at on localhost:5200." in brain.heard[-1]
    assert _words(brain.heard[-1]) == "yeah, let me know"
    assert turn.said.startswith("reply to")
    # Never spoken as its own stored line - once through the brain's reply is the whole delivery.
    # (The fake echoes only his words, so the whole spoken record is: the offer, then the reply.)
    assert tts.spoken == ["I've got an update on asana-submit-fix when you're ready.",
                          "reply to yeah, let me know"]


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
    assert not [item for item in outbox.drain()]  # and the one he heard is not owed again


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


def test_engaging_with_something_else_delivers_the_news_in_that_same_reply():
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
    convo.turn()  # his question - the reply carries the update with it
    convo.turn()  # the goodbye; nothing further arrives

    assert "news about the fix" in brain.heard[-1]
    assert _words(brain.heard[-1]) == "what time is it"
    assert "news about the fix" not in tts.spoken  # only inside the brain's one reply


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


def test_news_that_cannot_be_delivered_yet_does_not_wedge_the_loop():
    # THE FREEZE: an undeliverable message left outbox.arrived latched, and the window's mic yields
    # an empty turn whenever that flag is set - so the loop spun, their submissions were never read,
    # and only a restart got them out. Declining to deliver must never leave the flag standing.
    outbox = Outbox()

    class MidSentenceSTT(FakeSTT):
        def is_mid_utterance(self):
            return True  # they are talking the whole time

    convo = Conversation(MidSentenceSTT(["never read"]), FakeBrain(), FakeTTS(), outbox=outbox)
    outbox.push("the fixer agent has news")

    convo._deliver_outbox()  # can't speak over them - but must still drain

    assert not outbox.arrived.is_set()  # nothing latched, so listening works normally


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


def test_fresh_news_about_an_agent_already_listed_is_read_out_again():
    # The roll call had been read out with two agents waiting. One of them then reported the very
    # thing he was waiting for - its work presented for his eyes - which SUPERSEDED its own older
    # item, leaving the tally at two. Measured by count, "has the list changed" said no, and the
    # presentation was never spoken: he closed the app still owed it ("I never heard back
    # again"). The roll call is remembered by its news now, not by its length.
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["", "", ""]), FakeBrain(), tts, outbox=outbox)
    outbox.push("fixer is still going", about="fixer")
    outbox.push("builder is still going", about="builder")

    convo.turn()  # the roll call names both
    assert any("fixer" in line and "builder" in line for line in tts.spoken)
    spoken_so_far = len(tts.spoken)

    convo.turn()  # nothing has changed - and nothing is said again
    assert len(tts.spoken) == spoken_so_far

    outbox.push("fixer presented its work for your eyes", about="fixer")
    convo.turn()

    assert len(tts.spoken) > spoken_so_far  # the fresh report re-opens the roll call
