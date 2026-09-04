from excephalon.threads import News
from excephalon.speaker import Speaker, Worded, anchors, claims_deployed

LAUNCHER = "[▶ Launch the demo](http://127.0.0.1:41777/launch?t=abc&p=C%3A%5Cdemo.vbs)"


class FakeBrain:
    def __init__(self, *replies):
        self._replies = list(replies)
        self.asked = []
        self.retracted = []

    def respond(self, prompt, *, remember=True, deadline=None):
        self.asked.append((prompt, remember, deadline))
        return self._replies.pop(0) if self._replies else ""

    def retract(self, draft):
        self.retracted.append(draft)


def _fact(report, kind="finished", work="the timed-reminder feature", stage="ready", **more):
    return News("There's an update on the timed-reminder feature.", about="excephalon-138",
                kind=kind, work=work, report=report, stage=stage, **more)


def test_news_is_worded_once_at_the_moment_it_is_spoken_by_one_author():
    # It used to be worded when it ARRIVED, stored as prose, and spoken minutes later with the
    # app's roll call welded on - two authors, one utterance, and every gate at that seam existed
    # to police the splice. Now the fact goes in and one whole message comes out.
    brain = FakeBrain(f"The timed-reminder feature is ready for you to try - click {LAUNCHER} "
                      "and ask it to remind you in two minutes.")
    fact = _fact(f"Done. Click {LAUNCHER} to open the demo. 1226 tests pass, commit 62283598b.")

    worded = Speaker(brain).word([fact])

    assert worded.composed is True
    assert LAUNCHER in worded.text
    assert "62283598b" not in worded.text
    [(prompt, remembered, _)] = brain.asked
    assert remembered is False  # composing is not delivering; the delivery writes its memory
    assert "the timed-reminder feature" in prompt and "never to relay" in prompt


def test_what_else_waits_is_written_into_the_same_composition_never_welded_on():
    brain = FakeBrain("The timed-reminder feature is ready to try. Also waiting: the scroll fix "
                      "and the Asana grouping fix - which do you want next?")
    others = [News("x", about="a", work="the scroll fix"),
              News("y", about="b", work="the Asana grouping fix")]

    worded = Speaker(brain).word([_fact("Ready. Steps: open the app.")], waiting=others)

    [(prompt, _, _)] = brain.asked
    assert "the scroll fix, the Asana grouping fix" in prompt
    assert "END your message by naming them" in prompt
    assert worded.composed is True
    assert "Which first?" not in worded.text  # no app-authored menu on the back of it


def test_a_draft_that_drops_the_door_is_asked_again_with_the_fault_named():
    # "What launch link? You didn't give me one." The door is what the message exists to hand
    # over; a draft without it is retracted and asked again, told exactly what it dropped.
    brain = FakeBrain("The demo is ready - open the launch link and try it.",
                      f"The timed-reminder feature is ready - open {LAUNCHER} and try it.")
    fact = _fact(f"Ready: {LAUNCHER}")

    worded = Speaker(brain).word([fact])

    assert LAUNCHER in worded.text and worded.composed is True
    first, second = brain.asked
    assert "drops the link http://127.0.0.1:41777/launch?t=abc&p=C%3A%5Cdemo.vbs" in second[0]
    assert brain.retracted == ["The demo is ready - open the launch link and try it."]


def test_two_failed_drafts_give_way_to_the_apps_own_whole_sentence_with_the_door_in_it():
    brain = FakeBrain("Ready, go look.", "Still no link here.")
    fact = _fact(f"Ready: {LAUNCHER}")

    worded = Speaker(brain).word([fact])

    assert worded.composed is False
    assert worded.text.startswith("There's an update on the timed-reminder feature")
    assert "http://127.0.0.1:41777/launch?t=abc&p=C%3A%5Cdemo.vbs" in worded.text
    assert len(brain.retracted) == 2  # neither draft stays on its record as something he heard


def test_unlanded_work_is_never_worded_as_shipped():
    # "The feature should be there in Highdeas waiting" - said about work still being built.
    brain = FakeBrain("The auto-play toggle is deployed - go try it in Highdeas.",
                      "The auto-play toggle is ready for your eyes in the demo.")

    worded = Speaker(brain).word([_fact("built the toggle", work="the auto-play toggle",
                                        stage="building")])

    assert "deployed" not in worded.text
    assert "deployed or shipped" in brain.asked[1][0]


def test_the_same_words_stand_once_the_work_has_landed():
    brain = FakeBrain("The auto-play toggle is in and done.")

    worded = Speaker(brain).word([_fact("merged", kind="landed", work="the auto-play toggle",
                                        stage="landing")])

    assert worded.text == "The auto-play toggle is in and done."


def test_a_brain_that_cannot_answer_still_leaves_him_told():
    class Broken:
        def respond(self, prompt, *, remember=True, deadline=None):
            raise RuntimeError("session wedged")

    worded = Speaker(Broken()).word([_fact("Ready.")])

    assert worded.text == "There's an update on the timed-reminder feature."
    assert worded.composed is False


def test_without_a_brain_the_app_speaks_for_itself_and_prose_passes_through_unchanged():
    # News composed before facts existed - a restart's spool, a test's fake - is already words.
    already = News("The drive link is fixed.", about="fixer", composed=True)
    others = [News("n", about="docs", work="the sidebar width")]

    alone = Speaker().word([already])
    with_menu = Speaker().word([already], waiting=others)

    assert alone == Worded("The drive link is fixed.", composed=True)
    assert with_menu.text == "The drive link is fixed.\n\nStill waiting: the sidebar width."
    assert with_menu.composed is False  # the menu is the app's, so the whole is app-authored


def test_the_brains_wait_is_bounded_and_it_is_a_foreground_ask():
    brain = FakeBrain("The timed-reminder feature is ready for your eyes.")

    Speaker(brain, deadline=12.0).word([_fact("Ready.")])

    [(_, _, deadline)] = brain.asked
    assert deadline == 12.0


def test_anchors_are_what_the_window_would_draw_as_links():
    report = (f"Open {LAUNCHER}, or http://localhost:5199/ - the file is "
              "C:\\Users\\ada\\demo\\Try-it.bat and nothing at src/excephalon/x.py matters.")

    assert anchors(report) == ["http://127.0.0.1:41777/launch?t=abc&p=C%3A%5Cdemo.vbs",
                               "http://localhost:5199/", "C:\\Users\\ada\\demo\\Try-it.bat"]


def test_claims_deployed_only_bites_before_the_work_has_landed():
    assert claims_deployed("it's shipped", "building") is True
    assert claims_deployed("it's shipped", "ready") is True
    assert claims_deployed("it's shipped", "landing") is False
    assert claims_deployed("the demo is live on port 5199", "ready") is False


def test_a_draft_that_never_names_the_work_is_not_a_delivery_of_it():
    # A reply that ignored the news entirely passed every other check, and the news was marked
    # delivered unsaid. Naming the work - two of his own words for it - is the floor.
    brain = FakeBrain("Sure thing, go ahead.", "The timed-reminder feature is ready to try.")

    worded = Speaker(brain).word([_fact("Ready to try.")])

    assert worded.text == "The timed-reminder feature is ready to try."
    assert "never so much as names the work" in brain.asked[1][0]


def test_the_reply_brief_hands_the_brain_the_fact_and_makes_it_the_only_author():
    from excephalon.speaker import brief

    note = brief([_fact(f"Ready: {LAUNCHER}")])

    assert "OWED to him and you are its only author" in note
    assert "the timed-reminder feature" in note
    assert LAUNCHER in note  # the door, for the brain to carry verbatim
    assert "app appends nothing" in note
