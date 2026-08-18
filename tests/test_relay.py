from excephalon.relay import notice


def test_an_agents_report_arrives_as_a_notice_not_as_its_own_words():
    report = (
        "DONE. Google Drive per-memo folder links complete, tested, committed. Independently "
        "re-verified everything below myself just now, not just trusting the other instances. "
        "363 passed, 0 failed. Committed as 91459e5."
    )

    said = notice("hungry-neumann", report)

    assert said == "DONE."
    assert "91459e5" not in said and "363 passed" not in said  # its internals stay where they are


def test_a_notice_never_opens_with_the_agents_name_or_sends_him_to_a_tab():
    # "errands: The agent that fixed the proactive-notice bug is registered as
    # `excephalon-139-bug-excephalon-occasionally`... (the rest is in errands's tab)" - spoken in
    # Excephalon's own voice. "Does a human walk up to their coworker in an office space and just
    # begin a conversation with the word 'errands'? No, of course not." The name-tag is a label,
    # and the logs are Excephalon's to read, not his.
    said = notice("errands", "Checked it. Everything is where it should be. More detail follows.")

    assert not said.startswith("errands")
    assert "tab" not in said
    assert said == "Checked it."


def test_a_report_that_carries_its_own_name_tag_loses_it_too():
    # An agent's report often copies its log's own prefix; that is the same label by another road.
    assert notice("fixer", "fixer: Tests are green; needs your Cloud steps.") == (
        "Tests are green; needs your Cloud steps."
    )


def test_a_short_report_arrives_whole():
    assert notice("fixer", "Tests are green; needs your Cloud steps.") == (
        "Tests are green; needs your Cloud steps."
    )


def test_a_single_enormous_sentence_is_still_cut_short():
    said = notice("fixer", "and then " * 200)

    assert len(said) < 200
    assert said.endswith("…")


def test_an_empty_report_still_says_something_rather_than_nothing():
    said = notice("fixer", "   ")

    assert said and not said.startswith("fixer")
