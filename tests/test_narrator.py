import threading

from excephalon.narrator import Narrator
from excephalon.outbox import Outbox


class FakeBrain:
    """A brain whose triage answer is scripted. Its words are decisions, never speech."""

    def __init__(self, reply="news"):
        self._reply = reply
        self.asked = []
        self.retracted = []

    def respond(self, utterance, *, remember=True, on_text=None, background=False, deadline=None):
        self.asked.append((utterance, remember))
        return self._reply

    def retract(self, draft):
        self.retracted.append(draft)


def _wait_for(outbox, timeout=2.0):
    deadline = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if outbox:
            return True
        deadline.wait(0.01)
    return bool(outbox)


def _settled(brain, timeout=1.0):
    """Give a triage every chance to finish, so a wrong push would have happened by now."""
    tick = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if brain.retracted:
            break
        tick.wait(0.01)
    tick.wait(0.05)


def test_an_agents_event_becomes_a_fact_not_a_sentence():
    # It used to be worded HERE, at arrival, and stored as prose - a sentence written with no idea
    # of the moment it would be said in, and the app welding a roll call onto it later. Nothing
    # is worded here now: the fact carries the agent's report as INPUT for the one author, and the
    # only words on it are the app's own plain sentence, which is what survives a restart.
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox, work_of=lambda agent: "the drive link fix",
             stage_of=lambda agent: "ready").tell(
        "finished", "fixer", "All six tasks are done. Full suite green, pushed.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert str(news) == "There's an update on the drive link fix."
    assert news.report == "All six tasks are done. Full suite green, pushed."
    assert news.work == "the drive link fix"
    assert news.stage == "ready"
    assert news.about == "fixer"
    assert news.composed is False  # nobody has worded it yet; the delivery will, once


def test_an_errand_and_a_memory_nudge_are_never_items_on_his_list():
    # "Two updates waiting. One, weekly-schedule-builder. Two, errands." - "I thought we're only
    # working on one thing. I don't even know what errands would be." The errand hand and the
    # memory inbox are the app's own machinery; their news is something to say, never a name he
    # is asked to choose between beside a real agent.
    outbox = Outbox()
    narrator = Narrator(FakeBrain(), outbox)

    narrator.tell("errand", "errands", "he signed up for italki")
    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert news.listed is False

    narrator.tell("memory", "memory", "he takes his coffee black")
    assert _wait_for(outbox)
    [nudge] = outbox.drain()
    assert nudge.listed is False


def test_an_agents_own_news_stays_an_item_he_can_choose():
    outbox = Outbox()
    Narrator(FakeBrain(), outbox).tell("finished", "fixer", "done")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert news.listed is True


def test_a_finished_turn_is_judged_by_the_brain_before_it_is_news():
    # A finished turn is often not news - the agent pausing, or stuck on something technical -
    # and offering him "an update" that is nothing is its own failure: "Well then I don't think
    # you should Have Offered it as an option. If there's nothing actionable for it."
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "Done. 621 passing, not merged.")

    assert _wait_for(outbox)
    [(asked, remembered)] = brain.asked
    assert "fixer" in asked and "621 passing" in asked
    assert "tell_agent" in asked and "ask_foreman" in asked and "handled" in asked
    assert "do not word anything for the user" in asked
    assert remembered is False  # a decision, not a turn of the conversation


def test_the_triage_s_own_words_are_never_spoken_and_are_taken_back():
    # Whatever the brain answers here is a decision word. It is taken off the brain's own record,
    # or the model goes on believing it said something he never heard.
    brain, outbox = FakeBrain("news"), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "ready to look at")

    assert _wait_for(outbox)
    assert brain.retracted == ["news"]
    assert str(outbox.drain()[0]) == "There's an update on your work."  # never "news"


def test_a_handled_answer_is_swallowed_and_the_user_hears_nothing():
    # When the brain kicked the agent onward itself, there is no news: a fact about it would
    # interrupt him with a word about nothing.
    brain, outbox = FakeBrain("Handled."), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "Continuing shortly.")

    _settled(brain)
    assert not outbox
    assert brain.retracted == ["Handled."]  # the kick is off its record too


def test_handled_with_punctuation_is_still_swallowed_whole():
    brain, outbox = FakeBrain("Handled!"), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "continuing")

    _settled(brain)
    assert not outbox


def test_handled_in_front_of_real_words_is_still_news():
    # "Handled - <news>" once reached him verbatim and he asked what the word referred to. Alone
    # it means silence; leading real words it is only routing, and the event is still his news.
    brain, outbox = FakeBrain("Handled - it is waiting on the merge queue."), Outbox()
    Narrator(brain, outbox, work_of=lambda agent: "the self-edit work").tell(
        "finished", "entity-self-edit", "waiting on the queue")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert str(news) == "There's an update on the self-edit work."
    assert "handled" not in str(news).lower()


def test_a_quiet_agent_is_judged_with_the_foreman_offered_as_the_prod():
    brain, outbox = FakeBrain("handled"), Outbox()
    Narrator(brain, outbox).tell("quiet", "fixer", "been silent for 20 minutes")

    _settled(brain)
    [(asked, _)] = brain.asked
    assert "ask_foreman" in asked
    assert not outbox  # the prod settled it; nothing for him


def test_a_landing_agents_finished_turn_is_a_conclusion_never_triaged():
    # The loop's last leg: nothing to judge, and the brain is not even asked - the fact goes
    # straight in, kind "landing", so it is never held back and never dropped.
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox, stage_of=lambda name: "landing").tell(
        "finished", "fixer", "Merged. PR #12 is on main.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert news.kind == "landing"
    assert news.concluding is True
    assert brain.asked == []


def test_a_death_is_recorded_as_what_it_is_without_asking_anyone():
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox, work_of=lambda agent: "the drive link fix").tell(
        "died", "fixer", "RuntimeError: session lost")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert news.kind == "died"
    assert str(news) == "the drive link fix has run into trouble and needs you."
    assert brain.asked == []


def test_a_brain_failure_still_records_the_fact():
    # News must never die with the brain: a triage that cannot be had reads as news, and the fact
    # is recorded with the app's own plain sentence on it.
    class BrokenBrain:
        def respond(self, utterance, *, remember=True, on_text=None, background=False,
                    deadline=None):
            raise RuntimeError("session wedged")

    outbox = Outbox()
    Narrator(BrokenBrain(), outbox, work_of=lambda agent: "the drive link fix").tell(
        "finished", "fixer", "All done. Extra detail here.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert str(news) == "There's an update on the drive link fix."
    assert news.report == "All done. Extra detail here."


def test_a_triage_is_bounded_and_yields_to_his_own_turn():
    # A background ask that can outlast his patience is one his turn can lose to; three of his
    # turns in a row died waiting on one. The triage is bounded, and marked as the app's own.
    seen = {}

    class Brain:
        def respond(self, utterance, *, remember=True, background=False, deadline=None):
            seen.update(background=background, deadline=deadline)
            return "news"

    outbox = Outbox()
    Narrator(Brain(), outbox, deadline=7.0).tell("finished", "fixer", "done")

    assert _wait_for(outbox)
    assert seen == {"background": True, "deadline": 7.0}


def test_tell_returns_at_once_and_judges_off_thread():
    started = threading.Event()
    finished = threading.Event()

    class SlowBrain:
        def respond(self, utterance, *, remember=True, on_text=None, background=False,
                    deadline=None):
            started.set()
            finished.wait(2.0)
            return "news"

    outbox = Outbox()
    Narrator(SlowBrain(), outbox).tell("finished", "fixer", "report")

    assert started.wait(2.0)  # the triage is underway...
    assert not outbox  # ...but tell() already returned without blocking on it
    finished.set()
    assert _wait_for(outbox)


def test_the_fact_carries_his_own_name_for_the_work_and_never_the_agents():
    # "it says 'still waiting: ... scheduled-messages'. I think this is the same as the
    # 'timed-reminder feature', but it's weird and confusing that in the previous message it chose
    # a different name for the feature than its agent log's name." One thread, one name, his.
    outbox = Outbox()
    Narrator(FakeBrain(), outbox, work_of=lambda agent: "a timed-reminder feature").tell(
        "wrote", "excephalon-138-scheduled-messages", "Red.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert news.work == "a timed-reminder feature"
    assert str(news) == "There's an update on a timed-reminder feature."
    assert "excephalon-138" not in str(news)


def test_an_errands_outcome_is_a_fact_like_any_other():
    outbox = Outbox()
    Narrator(FakeBrain(), outbox).tell("errand", "errands", "moved the file")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert news.kind == "errand"
    assert news.report == "moved the file"
    assert str(news) == "I finished that errand for you."
