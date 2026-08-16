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


def test_the_note_says_what_is_already_waiting_so_the_greeting_does_not_ask_about_one():
    # He opened it and got two messages thirteen seconds apart: a welcome asking about the
    # scrubber fix, then "Three updates waiting. One... Two... Three... Which first?" The
    # greeting must not pick one out - the list is the app's to read.
    note = homecoming_note(
        turns=[("what about the scrubber?", "It's ready for your eyes.")],
        changes=[], away=95.0, waiting=["highdeas-scrubber-fix", "robot-icon-ui"])

    assert "2 update" in note
    assert "do NOT ask" in note and "do not list them" in note


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

        def respond(self, message):
            self.asked.append(message)
            return self.says

    brain = Brain()
    said = _greeting(brain, booted_at=0.0, previous_boot={}, note="[pick the thread up]")
    assert said == "Welcome back - your mic can't drop out on me like that again."
    assert brain.asked == ["[pick the thread up]"]

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
