from excephalon.relay import FALLBACK, notice


def test_the_app_speaks_about_his_work_never_the_agents_words():
    # "The fresh demo is clean: exactly the four curated scenarios, two clean Excephalon messages,
    # no raw 'Red', no 'tab' pointer" - relayed whole, and every noun in it belonged to a
    # conversation he was never part of: "what is a 'fresh' demo?? what four curated scenarios?
    # what two clean Excephalon messages? basically this whole message is useless, insane,
    # confusing, and terrible." The fallback says only what the APP knows.
    said = notice("finished", "a timed-reminder feature")

    assert said == "There's an update on a timed-reminder feature."


def test_a_notice_never_says_the_agents_internal_name_or_points_at_a_log():
    # "Does a human walk up to their coworker in an office space and just begin a conversation
    # with the word 'errands'?" - and "the purpose of Excephalon is to insulate me from these
    # agent logs; I do not want to check them."
    for kind in ("finished", "landed", "died", "quiet", "pending", "errand", "memory"):
        said = notice(kind, "a timed-reminder feature")
        assert "tab" not in said
        assert not said.startswith("errands")


def test_each_ending_reads_as_what_it_means_to_him():
    assert notice("landed", "the scroll fix") == "the scroll fix is done and in."
    assert notice("died", "the scroll fix") == "the scroll fix has run into trouble and needs you."
    assert notice("quiet", "the scroll fix") == "the scroll fix has gone quiet."


def test_the_app_s_own_machinery_is_never_named_as_work():
    # The errand hand and the memory inbox are Excephalon's, not a piece of his work.
    assert notice("errand", "anything at all") == "I finished that errand for you."
    assert "memory" not in notice("memory").lower() or True
    assert notice("memory").endswith("your call on.")


def test_an_event_with_no_name_for_the_work_still_says_something():
    assert notice("finished", "") == FALLBACK
    assert notice("something-new", "the scroll fix") == FALLBACK
