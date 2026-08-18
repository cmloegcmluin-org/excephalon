import threading

from excephalon.narrator import Narrator
from excephalon.outbox import Outbox


class FakeBrain:
    def __init__(self, reply="The drive work's done - it just needs your eyes."):
        self._reply = reply
        self.asked = []
        self.retracted = []

    def respond(self, utterance, *, remember=True, on_text=None):
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


def test_news_reaches_the_outbox_in_the_brains_own_words():
    # "gdoc-export: HIGHDEAS: native Google Doc export (branch ...) — update: ..." is a log line
    # wearing a voice, and he said so. The brain reads the agent's report and composes the one or
    # two sentences he actually hears - so the interjection is the same voice he talks to.
    brain, outbox = FakeBrain(), Outbox()
    narrator = Narrator(brain, outbox)

    narrator.tell("finished", "fixer", "All six tasks are done. Full suite green, pushed.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert str(news) == "The drive work's done - it just needs your eyes."
    assert news.about == "fixer"
    assert news.composed is True  # the brain wrote it, so nothing need be read back to it


def test_an_errand_and_a_memory_nudge_are_never_items_on_his_list():
    # "Two updates waiting. One, weekly-schedule-builder. Two, errands." - "I thought we're only
    # working on one thing. I don't even know what errands would be." The errand hand and the
    # memory inbox are the app's own machinery; their news is something to say, never a name he
    # is asked to choose between beside a real agent.
    outbox = Outbox()
    narrator = Narrator(FakeBrain("Found your italki signup from this week."), outbox)

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
    narrator = Narrator(FakeBrain("The drive link is fixed."), outbox)

    narrator.tell("finished", "fixer", "done")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert news.listed is True


def test_the_brain_is_told_which_agent_and_what_it_reported():
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "Done. 621 passing, not merged.")

    assert _wait_for(outbox)
    [(asked, remembered)] = brain.asked
    assert "fixer" in asked
    assert "621 passing" in asked
    # Composed, not yet delivered - and this is the one place in the app that routinely throws a
    # finished line away: swallowed as a kick to the agent, dropped for over-claiming, beaten by
    # the deadline. Remembered here, every one of those drafts survived a compaction or a restart
    # as something the model believed it had told him. What it said is written from the DELIVERY
    # instead (SdkBrain.spoke, called when the utterance actually sounds).
    assert remembered is False


def test_the_line_is_told_his_own_name_for_the_work_and_never_the_agents():
    # "it says 'still waiting: ... scheduled-messages'. I think this is the same as the
    # 'timed-reminder feature', but it's weird and confusing that in the previous message it chose
    # a different name for the feature than its agent log's name." One thread, one name, and the
    # name is his - the agent's is a filename.
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox, work_of=lambda agent: "a timed-reminder feature").tell(
        "finished", "excephalon-138-scheduled-messages", "Red.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "a timed-reminder feature" in asked
    assert "Never say the agent's internal name" in asked
    assert outbox.drain()[0].work == "a timed-reminder feature"


def test_a_fallback_says_what_the_app_knows_never_what_the_agent_wrote():
    # "The fresh demo is clean: exactly the four curated scenarios, two clean Excephalon
    # messages..." reached him verbatim: "what is a 'fresh' demo?? what four curated scenarios?
    # what two clean Excephalon messages? basically this whole message is useless, insane,
    # confusing, and terrible."
    class BrokenBrain:
        def respond(self, utterance, *, remember=True, on_text=None):
            raise RuntimeError("session wedged")

    outbox = Outbox()
    Narrator(BrokenBrain(), outbox, work_of=lambda agent: "a timed-reminder feature").tell(
        "finished", "excephalon-139", "The fresh demo is clean: exactly the four curated scenarios.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert str(news) == "There's an update on a timed-reminder feature."


def test_a_death_is_narrated_as_what_it_is():
    brain, outbox = FakeBrain("The fixer agent died mid-task - want me to start a fresh one?"), Outbox()
    Narrator(brain, outbox).tell("died", "fixer", "RuntimeError: session lost")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "died" in asked.lower()


def test_a_brain_failure_falls_back_to_the_plain_notice():
    # News must never die with the brain: a narration that cannot be composed is still delivered,
    # as the capped first-sentence notice the relay has always made.
    class BrokenBrain:
        def respond(self, utterance, *, remember=True, on_text=None):
            raise RuntimeError("session wedged")

    outbox = Outbox()
    Narrator(BrokenBrain(), outbox).tell("finished", "fixer", "All done. Extra detail here.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    # The news itself, in plain words - never opening with the agent's internal name, which is a
    # label and reached him as one ("Does a human walk up to their coworker ... and just begin a
    # conversation with the word 'errands'?"). Which agent it is about travels with it instead.
    # The app's own sentence about HIS work - never a word of what the agent wrote.
    assert str(news) == "There's an update on your work."
    assert news.about == "fixer"
    assert news.composed is False  # app-authored after all, so the ledger treats it as unwritten


def test_tell_returns_at_once_and_narrates_off_thread():
    started = threading.Event()
    finished = threading.Event()

    class SlowBrain:
        def respond(self, utterance, *, remember=True, on_text=None):
            started.set()
            finished.wait(2.0)
            return "done now"

    outbox = Outbox()
    Narrator(SlowBrain(), outbox).tell("finished", "fixer", "report")

    assert started.wait(2.0)  # the narration is underway...
    assert not outbox  # ...but tell() already returned without blocking on it
    finished.set()
    assert _wait_for(outbox)


def test_a_finished_narration_is_told_that_tests_are_never_his_verification():
    # The narrated heads-up once told him to "run pytest in the worktree to verify" - the exact
    # thing his standing profile forbids. The prompt now carries the law where the wording is made.
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "Done, PR open, 621 tests passing.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "see-it-running" in asked
    assert "never their verification" in asked


def test_a_finished_narration_may_kick_the_agent_itself_instead_of_interrupting():
    # An agent pausing to narrate an unactionable step is not news. The prompt offers the brain a
    # third way: nudge the agent onward with tell_agent and answer only "handled".
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "I'll now run the tests, then continue.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "tell_agent" in asked
    assert "handled" in asked


def test_a_handled_reply_is_swallowed_and_the_user_hears_nothing():
    # When the brain kicked the agent onward itself, there is no news: pushing "Handled." to the
    # outbox would interrupt the user with a word about nothing.
    responded = threading.Event()

    class KickingBrain(FakeBrain):
        def respond(self, utterance, *, remember=True, on_text=None):
            try:
                return super().respond(utterance, remember=remember, on_text=on_text)
            finally:
                responded.set()

    outbox = Outbox()
    Narrator(KickingBrain("Handled."), outbox).tell("finished", "fixer", "Continuing shortly.")

    assert responded.wait(2.0)
    settled = threading.Event()
    for _ in range(20):  # give the push after respond() every chance to happen if it wrongly would
        if outbox:
            break
        settled.wait(0.01)
    assert not outbox


def test_a_finished_agent_that_was_landing_approved_work_gets_the_wrap_up_prompt():
    # After the user approves, the rest is mechanical: the agent lands it, and the brain is told
    # to wrap the agent up itself the moment the report says it merged - not to hand the user
    # another chore.
    brain, outbox = FakeBrain(), Outbox()
    narrator = Narrator(brain, outbox, stage_of=lambda name: "landing")

    narrator.tell("finished", "fixer", "Merged - the queue took it, main has the work.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "close_agent_tab" in asked
    assert "approved" in asked


def test_a_finished_agent_still_building_keeps_the_presentation_prompt():
    brain, outbox = FakeBrain(), Outbox()
    narrator = Narrator(brain, outbox, stage_of=lambda name: "building")

    narrator.tell("finished", "fixer", "Done with the first pass.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "see-it-running" in asked
    assert "close_agent_tab" not in asked


def test_a_finished_narration_knows_the_foreman_exists_for_technical_snags():
    # "a smarter Claude agent would take care of negotiating issues that come up with the working
    # agents" - the brain is the router, so the option has to be in front of it where the wording
    # is made, or every snag still lands on the user.
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "I need to know which auth library to use.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "ask_foreman" in asked


def test_a_quiet_narration_offers_the_foreman_as_the_prod():
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("quiet", "fixer", "been silent for 25 minutes")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "ask_foreman" in asked


def test_a_handled_prefix_is_stripped_and_never_reaches_his_ears():
    # "The word 'handled' doesn't appear to refer to anything in my previous message... What are
    # you talking about?" The brain wrote "Handled - <news>" and the exact-match swallow let the
    # whole thing through, protocol word first. The word is routing, never speech: alone it is
    # swallowed, and in front of real news the news goes out without it.
    brain, outbox = FakeBrain("Handled - entity-self-edit is waiting on the merge queue."), Outbox()
    Narrator(brain, outbox).tell("finished", "entity-self-edit", "waiting on the queue")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert str(news) == "entity-self-edit is waiting on the merge queue."
    assert "handled" not in str(news).lower()


def test_handled_with_punctuation_is_still_swallowed_whole():
    responded = threading.Event()

    class KickingBrain(FakeBrain):
        def respond(self, utterance, *, remember=True, on_text=None):
            try:
                return super().respond(utterance, remember=remember, on_text=on_text)
            finally:
                responded.set()

    outbox = Outbox()
    Narrator(KickingBrain("Handled!"), outbox).tell("finished", "fixer", "continuing")

    assert responded.wait(2.0)
    settled = threading.Event()
    for _ in range(20):
        if outbox:
            break
        settled.wait(0.01)
    assert not outbox


def test_news_survives_a_brain_that_hangs():
    # After 04:43 one morning, everything that needed the brain simply never reached the screen:
    # one narration hanging inside a wedged session holds the lock, and every later one - the
    # agent's merge report, the twenty-minute quiet warning - queues behind it until the app
    # closes and all of it dies unspoken. The wait is bounded now: past the deadline the plain
    # notice carries the news.
    release = threading.Event()

    class WedgedBrain:
        def respond(self, utterance, *, remember=True, on_text=None):
            release.wait(2.0)
            return "A late answer."

    outbox = Outbox()
    Narrator(WedgedBrain(), outbox, deadline=0.05).tell("finished", "fixer", "It merged. Details.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert str(news) == "There's an update on your work."  # never the agent's own words
    assert news.composed is False  # app-authored: the ledger must read it back to the brain

    # The brain's answer, when it finally comes, is dropped - the news must not be told twice.
    release.set()
    settled = threading.Event()
    settled.wait(0.2)
    assert not outbox


def test_an_errands_outcome_is_worded_like_any_other_news():
    brain, outbox = FakeBrain("Done - that old log is tucked into the archive."), Outbox()
    Narrator(brain, outbox).tell("errand", "errands", "Moved the log into the archive.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "chore" in asked
    assert "Moved the log into the archive." in asked


def test_every_narration_carries_the_same_conduct_a_reply_does():
    # "The agent reported the feature is working... but it's already wrapped up at the desk" was a
    # narration, and the standing conduct that bans internal words reaches only replies - so he
    # got jargon he had to ask about twice, in a sentence that named no feature.
    asked = []

    class Brain:
        def respond(self, prompt, remember=True):
            asked.append(prompt)
            return "The copy fixes are ready to look at."

    outbox = Outbox()
    Narrator(Brain(), outbox).tell("finished", "copy-fixes", "done")

    assert _wait_for(outbox)
    assert "the desk" in asked[0] and "never" in asked[0]  # named, not merely a category
    assert "the feature" in asked[0]  # and told to name the work in his own words


def test_work_that_has_not_landed_is_never_narrated_as_deployed():
    # "The feature should be there in Highdeas waiting" - said about work still being built, and
    # he went looking for it. The stage rides in as a fact, and a line that claims deployment
    # anyway is dropped for the plain notice, which claims nothing.
    class Brain:
        def respond(self, prompt, remember=True):
            return "The auto-play checkbox is deployed - go and try it in Highdeas."

    outbox = Outbox()
    Narrator(Brain(), outbox, stage_of=lambda agent: "building").tell(
        "finished", "toggle", "built the checkbox")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert "deployed" not in str(news)
    assert news.composed is False  # app-authored, so the ledger reads it back to the brain


def test_the_same_words_stand_once_the_work_has_landed():
    class Brain:
        def respond(self, prompt, remember=True):
            return "The auto-play checkbox is deployed - it is in Highdeas now."

    outbox = Outbox()
    Narrator(Brain(), outbox, stage_of=lambda agent: "landing").tell(
        "died", "toggle", "landed it")

    assert _wait_for(outbox)
    assert "deployed" in str(outbox.drain()[0])


def test_a_line_dropped_for_over_claiming_is_taken_off_the_brains_own_record():
    # The plain notice goes out INSTEAD of what it wrote - so it is holding a sentence he never
    # heard, beside one he did, about the same work. That is the same-thing-twice reading built
    # from the inside, and it is why the draft is retracted rather than merely unused.
    brain, outbox = FakeBrain("The auto-play checkbox is deployed - try it in Highdeas."), Outbox()
    Narrator(brain, outbox, stage_of=lambda agent: "building").tell(
        "finished", "toggle", "built the checkbox")

    assert _wait_for(outbox)
    assert brain.retracted == ["The auto-play checkbox is deployed - try it in Highdeas."]


def test_a_swallowed_kick_to_the_agent_is_taken_back_too():
    # "Handled." is protocol, not speech: nothing is pushed and nothing is heard. Left on the
    # record it is a reply the model believes it gave him about an agent he was never told about.
    responded = threading.Event()

    class KickingBrain(FakeBrain):
        def respond(self, utterance, *, remember=True, on_text=None):
            try:
                return super().respond(utterance, remember=remember, on_text=on_text)
            finally:
                responded.set()

    brain = KickingBrain("Handled.")
    Narrator(brain, Outbox()).tell("finished", "fixer", "Continuing shortly.")

    assert responded.wait(2.0)
    for _ in range(50):
        if brain.retracted:
            break
        threading.Event().wait(0.01)
    assert brain.retracted == ["Handled."]


def test_a_draft_the_deadline_beat_is_taken_back_rather_than_left_standing():
    # One hung narration once held the brain's lock while a merge report queued behind it, so the
    # wait is bounded and the plain notice ships. The late answer is dropped - and has to be taken
    # back, or the model holds the sentence it wrote beside the notice he actually heard.
    started, let_go = threading.Event(), threading.Event()

    class SlowBrain(FakeBrain):
        def respond(self, utterance, *, remember=True, on_text=None):
            started.set()
            let_go.wait(2.0)
            return "The drive work is ready for your eyes."

    brain, outbox = SlowBrain(), Outbox()
    Narrator(brain, outbox, deadline=0.05).tell("finished", "fixer", "It merged. Details.")

    assert _wait_for(outbox)          # the plain notice shipped on the deadline
    assert not outbox.drain()[0].composed
    let_go.set()
    for _ in range(200):
        if brain.retracted:
            break
        threading.Event().wait(0.01)
    assert brain.retracted == ["The drive work is ready for your eyes."]
