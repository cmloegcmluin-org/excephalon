from excephalon.threads import Ledger, News


def test_a_fact_stays_owed_until_it_is_settled_never_merely_looked_at(tmp_path):
    # News used to be DRAINED into the conversation's hand, and from that moment the store had no
    # idea whether it had reached him. Three agents' reports died in a wedged process's hand and
    # the restarted app had "no trace" of the updates it had been offering. Looking is not
    # delivery: the fact stays owed - in memory and on disk - until the mouth's receipt says it
    # actually went out.
    spool = tmp_path / "outbox.json"
    ledger = Ledger(spool=spool)
    ledger.owe("The fix is ready to look at.", about="fixer")

    [fact] = ledger.owed()
    ledger.seen()
    assert ledger.owed() == [fact]  # still owed after being looked at
    assert Ledger(spool=spool).owed() == ["The fix is ready to look at."]  # and after a restart

    ledger.settle(fact)

    assert ledger.owed() == []
    assert Ledger(spool=spool).owed() == []


def test_looking_clears_the_signal_and_a_new_fact_raises_it_again():
    ledger = Ledger()
    assert not ledger.arrived.is_set()

    ledger.owe("word", about="a")
    assert ledger.arrived.is_set()

    ledger.seen()
    assert not ledger.arrived.is_set()  # looked at; the loop will decide what to do with it
    assert ledger.owed()  # ...but it is still owed

    ledger.owe("more", about="b")
    assert ledger.arrived.is_set()


def test_one_fact_per_thread_and_the_newest_takes_the_threads_first_place():
    # Every turn-end while he was away queued its own sentence and the roll call read the same
    # name four times. And a refresh that moved a thread to the END had the same three names read
    # back re-numbered seconds apart ("Now I don't know what to tell you") - a thread keeps the
    # place its first news took.
    ledger = Ledger()
    ledger.owe("fixer: building", about="fixer")
    ledger.owe("docs: needs a call", about="docs")
    ledger.owe("fixer: ready for your eyes", about="fixer")

    assert [str(fact) for fact in ledger.owed()] == ["fixer: ready for your eyes",
                                                    "docs: needs a call"]


def test_an_alarm_never_displaces_a_report_and_a_report_displaces_an_alarm():
    # "Been silent for 20 minutes" arrived after its agent's merge report and, being newest,
    # destroyed it - the one thing he was waiting for, killed by a timer's guess.
    ledger = Ledger()
    ledger.owe("It merged.", about="lander", kind="landing")
    ledger.owe("been silent for 20 minutes", about="lander", kind="quiet")
    assert [str(fact) for fact in ledger.owed()] == ["It merged."]

    ledger.owe("been silent for 20 minutes", about="quiet-one", kind="quiet")
    ledger.owe("Ready for your eyes.", about="quiet-one", kind="finished")
    assert str(ledger.held("quiet-one")) == "Ready for your eyes."


def test_threadless_news_is_never_collapsed():
    ledger = Ledger()
    ledger.owe("one thing")
    ledger.owe("another thing")

    assert [str(fact) for fact in ledger.owed()] == ["one thing", "another thing"]


def test_settling_reaches_the_file_so_a_restart_cannot_revive_what_he_heard(tmp_path):
    # "This heads up makes no sense. It comes out of nowhere and provides no new information that
    # I didn't already have." - yesterday's sentence, kept on disk after its newer twin replaced
    # it in memory, read out as fresh news by the next process.
    spool = tmp_path / "outbox.json"
    ledger = Ledger(spool=spool)
    ledger.owe("The split is ready for your eyes.", about="projects-tab")
    ledger.owe("All twelve are cards now - which names need shortening?", about="projects-tab")

    assert Ledger(spool=spool).owed() == ["All twelve are cards now - which names need shortening?"]

    [newest] = ledger.owed()
    ledger.settle(newest)

    assert Ledger(spool=spool).owed() == []


def test_a_drop_reaches_everything_because_there_is_only_one_place():
    # A drop used to clean the queue while the copy already drained into the conversation's hand
    # was still offered ("surely there's no update for smart grouping. You just sent off the
    # latest message to it."). There is no hand; a drop is a drop.
    ledger = Ledger()
    ledger.owe("grouping: done", about="grouping")
    ledger.owe("other: needs a decision", about="other")
    ledger.seen()  # the loop has looked - which used to mean "taken into hand"

    ledger.drop("grouping")

    assert [fact.about for fact in ledger.owed()] == ["other"]
    assert ledger.take_dropped() == set()  # nothing anywhere else to prune


def test_a_threads_ending_survives_a_drop_that_asks_it_to():
    ledger = Ledger()
    ledger.owe("still checking the tab switch", about="scroller", kind="finished")
    ledger.owe("It merged - the tabs keep their scroll now.", about="scroller", kind="landing")

    ledger.drop("scroller", keep_conclusions=True)

    assert [str(fact) for fact in ledger.owed()] == ["It merged - the tabs keep their scroll now."]


def test_owed_about_and_held_answer_from_the_one_store(tmp_path):
    # The desk asked the queue who was owed news and could not see the walkthrough sitting in the
    # conversation's hand for an hour, so the brain told him it had presented work he had never
    # seen ("That's false. You never presented it to me."). One store, one answer.
    ledger = Ledger(spool=tmp_path / "outbox.json")
    ledger.owe("linking: ready for your eyes", about="linking", report="steps here")
    ledger.owe("fixer: merged", about="fixer")
    ledger.seen()
    ledger.settle(ledger.held("fixer"))

    assert ledger.owed_about() == {"linking"}
    assert ledger.held("linking").report == "steps here"
    assert ledger.held("fixer") is None


def test_what_is_restored_at_boot_is_app_authored_to_the_new_brain(tmp_path):
    # The brain that wrote it died with the last process; carried over as composed, a restored
    # line skipped the unwritten-lines ledger and the new brain denied saying it to his face.
    spool = tmp_path / "outbox.json"
    Ledger(spool=spool).owe("The feature should be there in Highdeas waiting.", about="toggle",
                            composed=True, work="the auto-play toggle", report="done", stage="ready")

    [restored] = Ledger(spool=spool).owed()

    assert restored.composed is False
    assert restored.about == "toggle" and restored.work == "the auto-play toggle"
    assert restored.report == "done" and restored.stage == "ready"


def test_a_renamed_thread_keeps_its_news_under_the_new_name():
    ledger = Ledger()
    ledger.owe("ready", about="auto-generated-name")

    ledger.retag("auto-generated-name", "the auto-play fix")

    assert ledger.held("the auto-play fix") is not None
    assert ledger.held("auto-generated-name") is None


def test_drain_takes_everything_as_heard_for_tests_that_mean_exactly_that():
    ledger = Ledger()
    ledger.owe("a", about="x")
    ledger.owe("b", about="y")

    assert [str(fact) for fact in ledger.drain()] == ["a", "b"]
    assert ledger.owed() == [] and not ledger and not ledger.arrived.is_set()


def test_his_attention_on_one_thread_is_a_bounded_hold_not_a_latch():
    # One thing at a time: while a walkthrough is in front of his eyes, other news waits. But a
    # verdict that never got RECORDED once held a merge report behind a review nobody could close,
    # so the hold is bounded by his own turns and a changed set is a fresh hold.
    from excephalon.threads import FOCUS_HOLDS_TURNS

    ledger = Ledger()
    assert ledger.focus(["spinner"]) == {"spinner"}
    for _ in range(FOCUS_HOLDS_TURNS):
        ledger.his_turn()
    assert ledger.focus(["spinner"]) == set()  # he is no longer looking, whatever the record says

    assert ledger.focus(["scroller"]) == {"scroller"}  # a different review: a fresh hold
    assert ledger.focus([]) == set()


def test_an_offer_stands_until_he_answers_and_knows_when_more_has_arrived():
    # "I never said I was ready for the update." An offer he has not answered holds everything;
    # the only thing that may change is the count ("I now have two updates for the
    # scheduled-message item").
    ledger = Ledger()
    assert ledger.offer == 0

    ledger.offered(1)
    assert ledger.offer == 1  # it stands, and this much was behind it

    ledger.spend_offer()
    assert ledger.offer == 0


def test_the_menu_as_read_out_survives_a_spent_offer_and_dies_with_the_last_fact():
    # "why did it just give me the same message twice in a row?" - the list is re-read only when
    # it would come out different, so what was last read out is remembered across his answers -
    # and forgotten only once nothing is owed at all.
    ledger = Ledger()
    fact = ledger.owe("fixer: ready", about="fixer")
    ledger.recited("Still waiting: fixer.")
    ledger.offered(1)

    ledger.spend_offer()
    assert ledger.recital == "Still waiting: fixer."

    ledger.settle(fact)
    assert ledger.recital == "" and ledger.offer == 0


def test_a_turn_of_his_spends_last_turns_request_too():
    ledger = Ledger()
    ledger.request("fixer")

    ledger.his_turn()

    assert ledger.take_requested() == set()  # an ask covers the moment it was made
