"""Coming back up mid-conversation: what it says instead of greeting him as a stranger."""

from excephalon.homecoming import (
    STOCK_GREETING,
    changes_since,
    homecoming_note,
    last_boot,
    record_boot,
)


def test_the_boot_it_ran_last_time_survives_the_restart(tmp_path):
    # To say what changed while it was down, the next process has to know where the last one
    # stood. Nothing recorded that, so a restart could only ever greet him from zero.
    path = tmp_path / "boot.json"
    assert last_boot(path) == {}  # a first-ever launch knows of no previous one, and says so

    record_boot(path, commit="abc123", at=1000.0)

    assert last_boot(path) == {"commit": "abc123", "at": 1000.0}

    record_boot(path, commit="def456", at=2000.0)
    assert last_boot(path)["commit"] == "def456"  # the newest boot, not a growing pile


def test_an_unreadable_boot_record_is_simply_no_record(tmp_path):
    # It is read on the way up, where nothing may raise: a launch with no console is this
    # project's oldest failure, and a torn json file must never be what stops the app appearing.
    path = tmp_path / "boot.json"
    path.write_text("{not json", encoding="utf-8")

    assert last_boot(path) == {}


def test_what_landed_while_it_was_down_is_read_off_the_commits():
    # "I've been upgraded to improve microphone resilience" - the subjects of the commits between
    # the boot he last had and this one. Their own words, never a summary invented here.
    ran = []

    def fake_run(command, **kwargs):
        ran.append(command)

        class Done:
            returncode = 0
            stdout = ("The recording rolls before the WAV's ceiling\n"
                      "A file link is clickable, readable, and spoken\n")
        return Done()

    changed = changes_since("C:/repo", "abc123", run=fake_run)

    assert changed == ["The recording rolls before the WAV's ceiling",
                       "A file link is clickable, readable, and spoken"]
    assert "abc123..HEAD" in " ".join(ran[0])


def test_nothing_landed_and_a_git_that_will_not_answer_are_both_no_changes():
    class Failed:
        returncode = 128
        stdout = "fatal: bad revision\n"

    assert changes_since("C:/repo", "abc123", run=lambda *a, **k: Failed()) == []
    assert changes_since("C:/repo", "", run=lambda *a, **k: Failed()) == []  # no previous boot

    def explode(*args, **kwargs):
        raise OSError("git is not on this machine")

    assert changes_since("C:/repo", "abc123", run=explode) == []


def test_a_restart_in_the_middle_of_something_asks_the_brain_to_pick_the_thread_up():
    # "It shouldn't always say 'I'm ready. What can I do for you?' That should only be the default
    # if we weren't in the middle of something when I restarted." The last thing it said was a
    # question of his to answer, so the greeting owes him that question back.
    note = homecoming_note(
        turns=[("what about language study?",
                "What language and app do you want for language study?")],
        changes=["The recording rolls before the WAV's ceiling"],
        away=95.0)

    assert note  # there IS something to pick up, so the stock line is not what goes out
    assert "What language and app do you want" in note   # the open question, in its own words
    assert "recording rolls" in note                      # what changed, in the commit's words
    assert "back" in note.lower()


def test_a_change_he_cannot_see_is_not_something_to_tell_him_about():
    # "the stuff it said about 'voice safety layer' doesn't make any sense to me... what does it
    # mean for me to still be driving?!?" - that was the brain paraphrasing two commits about the
    # app's own internals. Most of what lands is machinery; asked what it MEANS for him, it will
    # invent a meaning rather than say there isn't one.
    note = homecoming_note(
        turns=[("what about the scrubber?", "It's ready for your eyes.")],
        changes=["Nothing stored is spoken without the brain checking it against the conversation"],
        away=95.0)

    assert "nothing to tell him about it" in note
    assert "INTERNAL" in note


def test_the_note_names_whose_updates_wait_and_says_that_is_all_of_them():
    # "it said 'or hear what's waiting from the agents' as if the naming prefix work is not stuff
    # from an agent, but that's not true; there is no other work waiting from agents." Told only
    # a COUNT, the greeting invented a second bucket and offered the one waiting thread against
    # itself. The threads are named, and named as the whole of it.
    note = homecoming_note(
        turns=[("what about the scrubber?", "It's ready for your eyes.")],
        changes=[], away=95.0, waiting=["highdeas-scrubber-fix", "robot-icon-ui"])

    assert "2 update" in note
    assert "highdeas-scrubber-fix, robot-icon-ui" in note
    assert "nothing else waiting from any agent" in note
    assert "Say nothing about WHAT any update SAYS" in note


def test_a_topic_he_never_got_an_answer_to_is_still_on_the_table():
    # "it didn't mention the calendar topic that is live." He had asked for a walk through his
    # day and never got it; several turns later the session ended on a different thread, and a
    # note built from the last exchange alone could not see the calendar at all.
    note = homecoming_note(
        turns=[("walk me through my day from that calendar", "Something's broken in my head."),
               ("any update on the naming prefix?", "Still being worked on.")],
        changes=[], away=95.0, waiting=["excephalon-agent-naming-prefix"])

    assert "walk me through my day from that calendar" in note
    assert "never got resolved is still OPEN" in note


def test_a_session_he_said_goodbye_to_is_not_the_middle_of_anything():
    # He ended it himself; coming back with "so, back to the task at hand" would be picking up a
    # thread he had closed.
    assert homecoming_note(turns=[("goodbye excephalon", "Be seeing you.")],
                           changes=["something landed"], away=95.0) == ""


def test_with_no_thread_at_all_there_is_nothing_to_come_back_to():
    assert homecoming_note(turns=[], changes=["something landed"], away=95.0) == ""


def test_how_long_he_was_away_is_measured_from_when_it_last_spoke_not_from_when_it_started(tmp_path):
    # "it claims I was out for 49 minutes, but that's not true. I had just sent it a message a few
    # minutes ago and then restarted to upgrade." The gap was measured boot-to-boot, which is the
    # LIFETIME of the session he spent talking to it, not the time he was without it. What the
    # last process last wrote is when it was last there for him.
    from excephalon.homecoming import last_seen

    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    older, newest = transcripts / "session-1.log", transcripts / "session-2.log"
    older.write_text("old\n", encoding="utf-8")
    newest.write_text("new\n", encoding="utf-8")
    import os

    os.utime(older, (1000.0, 1000.0))
    os.utime(newest, (5000.0, 5000.0))

    assert last_seen(transcripts) == 5000.0
    assert last_seen(tmp_path / "nowhere") == 0.0  # a first-ever launch has nothing to measure


def test_a_long_absence_is_not_a_restart_mid_conversation():
    # Coming back the next day is a fresh start, whatever the transcript still holds; "sorry you
    # weren't able to communicate with me for a minute" would be nonsense about last night.
    note = homecoming_note(
        turns=[("what about language study?", "What language and app do you want?")],
        changes=[], away=60 * 60 * 9)

    assert note == ""


def test_the_stock_greeting_is_still_what_a_fresh_start_says():
    assert STOCK_GREETING == "I'm ready. What can I do for you?"


def test_the_first_line_is_the_brains_when_there_is_a_thread_and_stock_when_there_is_not():
    from excephalon.__main__ import _greeting

    class Brain:
        def __init__(self, says="Welcome back - your mic can't drop out on me like that again."):
            self.says = says
            self.asked = []

        def respond(self, message, *, remember=True):
            self.asked.append((message, remember))
            return self.says

    brain = Brain()
    said = _greeting(brain, booted_at=0.0, previous_boot={}, note="[pick the thread up]")
    assert said == "Welcome back - your mic can't drop out on me like that again."
    # Asked, and deliberately NOT remembered as a turn: a greeting is a draft until it sounds -
    # `unfit` may refuse it, the loop may drop it for retelling the news behind it, and he may
    # speak first. Remembered here, a welcome nobody heard came back after a restart as the line
    # the model believed it had opened with.
    assert brain.asked == [("[pick the thread up]", False)]

    fresh = Brain()
    assert _greeting(fresh, booted_at=0.0, previous_boot={}, note="") == STOCK_GREETING
    assert fresh.asked == []  # nothing to pick up: the brain is not even asked


def test_a_brain_that_cannot_answer_still_leaves_him_greeted():
    # The greeting happens before the conversation loop exists, on the way up. A wedged or
    # signed-out brain must cost the welcome-back its detail, never the launch its first line.
    from excephalon.__main__ import _greeting

    class Broken:
        def respond(self, message):
            raise RuntimeError("the brain is not answering")

    class Silent:
        def respond(self, message):
            return "   "

    assert _greeting(Broken(), 0.0, {}, note="[pick it up]") == STOCK_GREETING
    assert _greeting(Silent(), 0.0, {}, note="[pick it up]") == STOCK_GREETING


def test_the_note_carries_where_every_work_thread_stands():
    # The greeting knew only the transcript's prose, and from prose it denied anything had landed
    # and reopened a finished approval in one sentence: "nothing's changed that you'd notice. Let
    # me finish reading what actually landed with the drag play cursor fix so you know exactly
    # what you're approving" - about work already shipped. The desk's own record rides the note.
    from excephalon.homecoming import homecoming_note

    note = homecoming_note(
        turns=[("what about the scrubber?", "It's ready for your eyes.")],
        changes=[], away=95.0,
        fleet="robot-icon-ui: DELIVERED just now - was on: the robot icons")

    assert "robot-icon-ui: DELIVERED just now" in note
    assert "must not contradict" in note
    assert "never re-offer it for review" in note


def test_a_greeting_that_picks_a_thread_for_him_is_unfit_while_something_waits():
    # "Back already - barely any time lost. While I was down, the Asana grouping fix, the tab
    # scroll memory, and the robot icon spinner all landed. So, about that calendar demo you
    # wanted - where should we start?" It named four things and then chose one: "strangely
    # assuming that we're starting on the calendar thing, rather than including it as one of the
    # potential things for us to do, and asking me which of the four things I want to work on."
    from excephalon.homecoming import unfit

    assert unfit("Back already. So, about that calendar demo - where should we start?", owed=True)
    assert unfit("Welcome back. Ready to carry on with the scrubber work?", owed=True)
    assert unfit("Back with you. Want to pick the calendar work back up, or hear the update "
                 "that's waiting?", owed=True) is None
    # Nothing waiting, nothing to choose between: an ordinary greeting is still fine.
    assert unfit("Back already. Where should we start?", owed=False) is None


def test_an_or_in_an_earlier_clause_is_not_an_offer():
    # The offer has to be IN the question, or "nothing changed, or nothing you'd notice." counts
    # as offering him something - and he is asked nothing at all.
    from excephalon.homecoming import offers_a_choice

    assert offers_a_choice("Nothing changed, or nothing you'd notice. Where were we?") is False
    assert offers_a_choice("Carry on there, or hear what's waiting?") is True


def test_one_thread_is_asked_about_rather_than_split_into_a_false_choice():
    # "it said 'or hear what's waiting from the agents' as if the naming prefix work is not stuff
    # from an agent, but that's not true; there is no other work waiting from agents." Where the
    # only update belongs to the thread being resumed, an either/or invents a second thread - so
    # asking WHETHER is an offer too, and requiring an "or" would have forced the invention.
    from excephalon.homecoming import offers_a_choice, unfit

    assert offers_a_choice("The naming prefix work has an update - want to hear it?") is True
    assert unfit("The naming prefix work has an update - want to hear it?", owed=True) is None
    assert offers_a_choice("So, about that calendar demo you wanted - where should we start?") \
        is False


def test_the_fallback_first_line_offers_when_something_is_waiting():
    # The stock line asks what it can do, which is a different question, and it would leave the
    # update sitting behind a choice he was never given. A refused greeting still has to ASK.
    from excephalon.__main__ import _greeting
    from excephalon.homecoming import OFFER_GREETING, offers_a_choice

    class Assuming:
        def respond(self, message, *, remember=True):
            return "Back already. So, about that calendar demo - where should we start?"

    said = _greeting(Assuming(), 0.0, {}, note="[pick it up]", waiting=["namer"])

    assert said == OFFER_GREETING
    assert offers_a_choice(said)


def test_a_refused_greeting_is_asked_again_with_the_fault_named():
    from excephalon.__main__ import _greeting

    asked = []

    class LearnsOnTheSecondTry:
        def respond(self, message, *, remember=True):
            asked.append(message)
            if len(asked) == 1:
                return "Back already. So, about that calendar demo - where should we start?"
            return "Back with you. Carry on with the calendar, or hear the update waiting?"

    said = _greeting(LearnsOnTheSecondTry(), 0.0, {}, note="[pick it up]", waiting=["namer"])

    assert said == "Back with you. Carry on with the calendar, or hear the update waiting?"
    assert "picks a thread for him" in asked[1]  # told exactly what was wrong with the first


def test_a_greeting_that_promises_work_is_unfit():
    # "Let me finish reading what actually landed..." opened a session by resurrecting a dead
    # errand; he answered "Dude, what the fuck? No. It's already been shipped." A greeting says
    # where things stand and hands him the floor - it never narrates the brain's next action.
    from excephalon.homecoming import unfit

    assert unfit("Back with you. Let me finish reading what landed with the drag play "
                 "cursor fix so you know exactly what you're approving.")
    assert unfit("Welcome back - I'm going to check what the agents did.")


def test_a_greeting_inviting_approval_is_unfit_unless_something_is_in_review():
    from excephalon.homecoming import unfit

    empty_desk = "No agents running."
    reviewing = "fixer: idle - task: a task - in review - presented, awaiting his verdict"

    assert unfit("Back with you - the demo is ready for your eyes to approve.", empty_desk)
    assert unfit("Welcome back. Take a look at the robot icon demo.", empty_desk)
    assert unfit("The fix is waiting on your approval.", reviewing) is None
    assert unfit("Welcome back. The scrubber fix is still waiting on your verdict.",
                 reviewing) is None


def test_an_ordinary_welcome_back_is_fit_to_speak():
    from excephalon.homecoming import unfit

    assert unfit("Welcome back - you were only gone a couple of minutes. The robot icon "
                 "work landed while you were out; we were talking about your schedule.",
                 "No agents running.") is None


def test_an_unfit_greeting_falls_back_to_the_stock_line():
    from excephalon.__main__ import _greeting

    class Promiser:
        def respond(self, message, *, remember=True):
            return "Back with you. Let me finish reading what actually landed."

    assert _greeting(Promiser(), 0.0, {}, note="[pick it up]") == STOCK_GREETING


def test_a_refused_greeting_is_taken_off_the_brains_own_record():
    # The stock line goes out INSTEAD of what it wrote, so the model is holding an opening he
    # never heard while he heard a different one. It opened a session promising to "finish
    # reading what actually landed with the drag play cursor fix so you know exactly what you're
    # approving", about work already shipped - a sentence it must not go on believing it said.
    from excephalon.__main__ import _greeting

    class Promiser:
        def __init__(self):
            self.retracted = []

        def respond(self, message, *, remember=True):
            return "Back with you. Let me finish reading what actually landed."

        def retract(self, draft):
            self.retracted.append(draft)

    brain = Promiser()
    assert _greeting(brain, 0.0, {}, note="[pick it up]") == STOCK_GREETING
    # Twice: a refused draft is told what was wrong and asked again, and this one refuses the
    # same way both times. Each attempt is taken back - two false memories, not one.
    assert brain.retracted == ["Back with you. Let me finish reading what actually landed."] * 2


def test_standing_work_makes_a_long_gap_no_fresh_start():
    # An agent was mid-task across a ninety-minute gap that ended in a wedge-forced close, and
    # the boot said "I'm ready. What can I do for you?" - "Excephalon gave me the generic
    # greeting just now even though it had some work in progress." The fresh-start outs are
    # about the conversation; standing work outlives the conversation.
    from excephalon.homecoming import homecoming_note

    fleet = "multiline-icon-fix: working - task: the robot icon on multiline tasks - in work"
    note = homecoming_note(
        turns=[("restart it", "On it.")], changes=[], away=60 * 96,
        fleet=fleet, busy=True)

    assert note != ""
    assert "multiline-icon-fix" in note
    assert "still standing" in note
    assert "do not pick it back up" in note  # the stale exchange is not a thread to resume
    assert "restart it" not in note  # and it is left out of the note entirely


def test_standing_work_survives_even_a_goodbye_ended_session():
    from excephalon.homecoming import homecoming_note

    note = homecoming_note(
        turns=[("goodbye excephalon", "Be seeing you.")], changes=[], away=95.0,
        fleet="fixer: working - in work", busy=True)

    assert note != ""
    assert "still standing" in note


def test_without_standing_work_the_fresh_start_outs_still_hold():
    from excephalon.homecoming import homecoming_note

    assert homecoming_note(turns=[], changes=[], away=95.0, busy=False) == ""
    assert homecoming_note(turns=[("hi", "Hello.")], changes=[], away=60 * 60 * 9,
                           busy=False) == ""


def test_a_genuine_resume_still_carries_the_broken_off_exchange():
    from excephalon.homecoming import homecoming_note

    note = homecoming_note(
        turns=[("what about the scrubber?", "It's ready for your eyes.")],
        changes=[], away=95.0, fleet="scrubber-fix: idle - in review", busy=True)

    assert "what about the scrubber?" in note
    assert "pick it back up" in note
