from excephalon.outbox import Outbox


def test_superseded_news_leaves_the_spool_so_a_restart_does_not_revive_it(tmp_path):
    # "This heads up makes no sense. It comes out of nowhere and provides no new information that
    # I didn't already have." An agent's older sentence, collapsed away in memory the moment its
    # newer one arrived, stayed in the spool - and the next process read it out as fresh news,
    # thirteen seconds after he had given his notes on that very work.
    spool = tmp_path / "outbox.json"
    outbox = Outbox(spool=spool)
    outbox.push("The split is ready for your eyes.", about="projects-tab")
    outbox.push("All twelve are cards now - which names need shortening?", about="projects-tab")
    older, newest = outbox.drain()

    outbox.superseded(older)  # newer news about the same agent replaced it: nobody will hear it
    outbox.spoken(newest)     # and the newest actually reached him

    assert Outbox(spool=spool).drain() == []


def test_pushed_messages_drain_in_order_then_the_outbox_is_empty():
    outbox = Outbox()
    outbox.push("agent 1 needs you")
    outbox.push("agent 2 is ready for review")

    assert outbox.drain() == ["agent 1 needs you", "agent 2 is ready for review"]
    assert outbox.drain() == []  # fully consumed


def test_arrived_is_set_on_push_and_cleared_on_drain():
    outbox = Outbox()
    assert not outbox.arrived.is_set()  # nothing waiting yet

    outbox.push("something to say")
    assert outbox.arrived.is_set()  # a lull can now be interrupted to speak it

    outbox.drain()
    assert not outbox.arrived.is_set()  # spoken, so the signal goes quiet again


def test_news_carries_which_agent_it_is_about_while_still_being_the_message():
    # Which agent a queued message is about has to survive the queue. Worked back out of the text
    # it would be reading the label to find the thing - and two of the four kinds of news the
    # Excephalon queues do not carry the name in any fixed place at all.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")

    [news] = outbox.drain()

    assert news == "fixer: the drive link is fixed"  # still only the message, to everything else
    assert news.about == "fixer"


def test_empty_outbox_is_falsy_and_a_pushed_one_is_truthy():
    outbox = Outbox()
    assert not outbox

    outbox.push("word from an agent")
    assert outbox


def test_undelivered_news_survives_the_process_and_delivered_news_does_not(tmp_path):
    # Three agents' reports once lived only in a wedged process's memory: the user restarted,
    # and the fresh app had "no trace" of the very updates it had just been offering. The spool
    # holds every pushed item until the conversation says it actually reached the user -
    # DRAINING is not delivery, because drained news waits in hand for a lull, sometimes for
    # minutes, and dies with the process there just the same.
    spool = tmp_path / "outbox.json"
    first = Outbox(spool=spool)
    first.push("the merge landed", about="lander", composed=True)
    first.push("the fix is ready to look at", about="fixer")
    first.drain()  # in hand, not yet spoken
    first.spoken("the merge landed")  # this one actually reached the user

    revived = Outbox(spool=spool)

    [held] = revived.drain()
    assert held == "the fix is ready to look at"  # still owed, back in the queue
    assert held.about == "fixer"
    assert revived.arrived.is_set() is False  # drained again; nothing else waiting


def test_a_spoolless_outbox_still_answers_spoken(tmp_path):
    outbox = Outbox()
    outbox.push("word")
    outbox.spoken("word")  # nothing durable to clear; must simply not raise


def test_news_about_a_finished_agent_can_be_dropped_from_the_queue_and_the_spool(tmp_path):
    # Its work is closed; an update about it lands as a surprise. And with the spool holding it,
    # it would come back after a restart to surprise him twice.
    spool = tmp_path / "outbox.json"
    outbox = Outbox(spool=spool)
    outbox.push("the copy fixes are ready to look at", about="copy-fixes")
    outbox.push("the other one needs a decision", about="other")

    outbox.drop("copy-fixes")

    assert [str(news) for news in outbox.drain()] == ["the other one needs a decision"]
    assert [str(news) for news in Outbox(spool=spool).drain()] == ["the other one needs a decision"]


def test_dropping_the_last_item_clears_the_waiting_signal(tmp_path):
    outbox = Outbox()
    outbox.push("only this", about="one")

    outbox.drop("one")

    assert not outbox and not outbox.arrived.is_set()


def test_a_drop_reaches_news_already_drained_into_someone_elses_hand():
    # The queue is only half of where news waits: the conversation drains items and holds them in
    # hand for a lull, and a drop that cleans the queue alone leaves the stale copy there. Held
    # smart-grouping news survived exactly that way - the user had already sent that agent new
    # instructions, and the "update" was still offered to him ("surely there's no update for
    # smart grouping. You just sent off the latest message to it."). The holder collects who was
    # dropped and prunes its own hand.
    outbox = Outbox()
    outbox.push("grouping: done", about="grouping")
    outbox.drain()  # in the conversation's hand now, not in the queue

    outbox.drop("grouping")

    assert outbox.take_dropped() == {"grouping"}
    assert outbox.take_dropped() == set()  # collected once; the next pass starts clean


def test_owed_about_sees_news_in_hand_not_only_news_in_the_queue(tmp_path):
    # "Presented, awaiting their verdict" was briefed about an agent whose walkthrough had never
    # reached him - drained, held in hand for over an hour, and the desk's view ("who is owed
    # news?") read only the queue. The spool is the record of everything still owed, in queue OR
    # in hand, so the question is answered from there.
    spool = tmp_path / "outbox.json"
    outbox = Outbox(spool=spool)
    outbox.push("linking: ready for your eyes", about="linking")
    outbox.push("fixer: merged", about="fixer")
    outbox.drain()  # both in hand
    outbox.spoken("fixer: merged")  # one actually reached him

    assert outbox.owed_about() == {"linking"}


def test_a_spoolless_outbox_answers_owed_about_from_its_queue():
    outbox = Outbox()
    outbox.push("fixer: merged", about="fixer")

    assert outbox.owed_about() == {"fixer"}


def test_news_can_arrive_unlisted_so_it_never_becomes_a_name_to_pick():
    # "I thought we're only working on one thing. I don't even know what errands would be." The
    # errand hand exists so a small chore is NOT a visible agent - and its result was read out
    # numbered, beside a real agent, under the internal word for the machinery. Some news is just
    # something to say; only an agent's news is an item on a list.
    outbox = Outbox()
    outbox.push("You're on italki as of this week.", about="errands", listed=False)
    outbox.push("fixer: the drive link is fixed", about="fixer")

    errand, agent = outbox.drain()

    assert errand.listed is False
    assert agent.listed is True  # an agent's news is a thing he chooses between, as before


def test_unlisted_survives_the_spool_like_everything_else_about_a_piece_of_news(tmp_path):
    spool = tmp_path / "outbox.json"
    first = Outbox(spool=spool)
    first.push("You're on italki as of this week.", about="errands", listed=False)

    [held] = Outbox(spool=spool).drain()

    assert held.listed is False  # a restart must not turn it back into a name to pick


def test_a_requested_hand_over_is_collected_once_by_the_holder():
    # The brain's way of DELIVERING a held update instead of retelling it: the request rides the
    # outbox to wherever the news is held, and the holder speaks that item word for word. Two
    # versions of the same update, 13 seconds apart, is what retelling produced.
    outbox = Outbox()
    outbox.request("fixer")

    assert outbox.take_requested() == {"fixer"}
    assert outbox.take_requested() == set()  # collected once; the next pass starts clean


def test_news_that_survived_a_restart_is_app_authored_to_the_new_brain(tmp_path):
    # `composed` means "the brain that will be asked about this wrote it" - and the brain that
    # wrote it died with the last process. Carried over as composed, a spooled line skipped the
    # unwritten-lines ledger and the new brain denied it to his face: "I don't see that statement
    # in our conversation - I didn't say the feature was already in Highdeas", about a line it had
    # spoken verbatim eighteen minutes earlier.
    spool = tmp_path / "outbox.json"
    first = Outbox(spool=spool)
    first.push("The feature should be there in Highdeas waiting.", about="toggle", composed=True)

    [restored] = Outbox(spool=spool).drain()

    assert restored.composed is False
    assert restored.about == "toggle"  # still known to be about that agent
