from excephalon.console import Console
from excephalon.transcript import SELF


def _recording():
    # One list for both seams, so the assertions see the real interleaving of whole lines and
    # in-place overwrites. An overwrite is recognisable by its leading carriage return.
    lines = []
    return lines, Console(echo=lines.append, overwrite=lines.append)


def test_an_address_it_said_in_words_is_recorded_as_an_address():
    # "It says 'click through at localhost port 8752' rather than ... in a clickable form." The
    # repair belongs where its words become the record: the voice had already said the words, and
    # the screen is the half that has to offer something to click.
    said = []
    console = Console(echo=lambda line: None, messages=lambda role, text: said.append((role, text)))

    console.reply("Ready - click through at localhost port 8752 and look.")
    console.heads_up("The test instance is up at localhost port 5200.")

    assert said == [(SELF, "Ready - click through at localhost:8752 and look."),
                    ("heads-up", "The test instance is up at localhost:5200.")]


def test_the_persona_tells_it_to_write_the_address_rather_than_spell_it_out():
    # The repair above is the net; this is the rule it nets. Both, because a rule only the persona
    # carries is a known weakness, and a net with no rule behind it invites the same sentence.
    from excephalon.brain_sdk import DEFAULT_PERSONA

    assert "WRITE ADDRESSES AND PATHS EXACTLY" in DEFAULT_PERSONA
    assert "localhost port 8752" in DEFAULT_PERSONA  # named as the thing not to write


def test_heard_shows_what_he_said():
    lines, console = _recording()

    console.heard("turn on the lights")

    assert lines == ["you said: turn on the lights"]


def test_a_typed_run_narrates_neither_the_mic_nor_the_users_own_words():
    lines = []
    console = Console(echo=lines.append, voice=False)  # they have their own prompt and their words on screen

    console.listening()
    console.heard("typed input")

    assert lines == []


def test_thinking_shows_the_indicator():
    lines, console = _recording()

    console.thinking()

    assert lines == ["(thinking…)"]


def test_reply_is_shown_prefixed_so_he_can_read_it():
    lines, console = _recording()

    console.reply("the lights are on")

    assert any("the lights are on" in line for line in lines)
    assert lines[0].startswith("excephalon>")


def test_heads_up_is_marked_as_unprompted():
    lines, console = _recording()

    console.heads_up("the deploy agent needs your call")

    assert any("the deploy agent needs your call" in line for line in lines)
    assert any("heads-up" in line for line in lines)


def test_timing_shows_the_think_and_speak_durations():
    lines, console = _recording()

    console.timing(think=2.34, speak=1.51)

    assert any("think 2.3s" in line and "speak 1.5s" in line for line in lines)


def test_ignoring_says_it_heard_something_and_dropped_it():
    lines, console = _recording()

    console.ignored()

    assert lines == ["\r(ignoring…)"]


def test_what_is_printed_is_also_written_to_the_session_record():
    # The terminal scrolls away, and it was the only record of what they actually saw.
    recorded = []
    console = Console(echo=lambda _: None, record=recorded.append)

    console.heard("pick up the drive work")
    console.reply("on it")

    assert recorded == ["you said: pick up the drive work", "excephalon> on it\n"]


def test_a_typed_run_still_records_what_he_said_even_though_it_is_not_echoed():
    recorded, lines = [], []
    console = Console(echo=lines.append, record=recorded.append, voice=False)

    console.heard("typed input")

    assert lines == []  # their own typing isn't echoed back at them
    assert recorded == ["you said: typed input"]  # but the record still has their side of it


def test_a_run_of_ignores_is_recorded_once_as_a_tally_not_line_by_line():
    recorded = []
    console = Console(echo=lambda _: None, overwrite=lambda _: None, record=recorded.append)

    for _ in range(16):
        console.ignored()
    console.reply("back with you")

    assert recorded == ["(ignored 16 while asleep)", "excephalon> back with you\n"]


def test_a_run_of_ignores_collapses_onto_one_line_with_a_tally():
    lines, console = _recording()

    for _ in range(3):
        console.ignored()

    assert lines == ["\r(ignoring…)", "\r(ignoring… 2x)", "\r(ignoring… 3x)"]  # each rewrites the last


def test_a_reply_closes_the_ignore_run_so_it_does_not_land_on_the_counter():
    lines, console = _recording()

    console.ignored()
    console.reply("back with you")

    assert lines[1] == "\n"  # the counter line is terminated first
    assert lines[2].startswith("excephalon>")


def test_a_later_ignore_starts_a_fresh_count():
    lines, console = _recording()

    console.ignored()
    console.reply("back with you")
    console.ignored()

    assert lines[-1] == "\r(ignoring…)"  # not "2x" - that run ended when they were answered


def test_messages_are_reported_with_who_said_them():
    # The window renders a conversation, not a log: it needs to know who spoke, not to re-parse
    # the prefixes this module just wrote.
    said = []
    console = Console(echo=lambda _: None, messages=lambda role, text: said.append((role, text)))

    console.heard("pick up the drive work")
    console.reply("on it")
    console.spoke("Message received.")
    console.heads_up("the fixer agent is done")
    console.thinking()

    assert said == [
        ("you", "pick up the drive work"),
        ("excephalon", "on it"),
        ("excephalon", "Message received."),
        ("heads-up", "the fixer agent is done"),
        ("status", "(thinking…)"),
    ]


def test_an_empty_listening_notice_says_nothing_at_all():
    # In the window there is a mic button and a level meter; "(listening… say 'over' when you're
    # done)" is both wrong and noise there.
    lines, recorded = [], []
    console = Console(echo=lines.append, record=recorded.append, listening_notice="")

    console.listening()

    assert lines == [] and recorded == []


def test_an_apps_own_aside_is_not_a_message_from_excephalon():
    # ""(cut off mid-utterance)" should not appear in a blue word bubble, because it's not
    # something Excephalon says. it can be grey text in the middle." A note the app made about
    # the turn is a status line; only what he HEARS is Excephalon's own message.
    said, kept = [], []
    console = Console(echo=lambda line: None, record=kept.append,
                      messages=lambda role, text: said.append((role, text)))

    console.spoke("Both agents are green.")
    console.aside("(cut off mid-utterance)")

    assert said == [("excephalon", "Both agents are green."), ("status", "(cut off mid-utterance)")]
    assert kept == ["Both agents are green.", "(cut off mid-utterance)"]  # both kept, as always
