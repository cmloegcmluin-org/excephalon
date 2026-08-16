"""The layer that reads what is about to be said against what has already been said."""

from excephalon.coherence import GATE_PROMPT, overtaken


class Brain:
    def __init__(self, reply="say"):
        self.reply = reply
        self.asked = []

    def respond(self, utterance, *, remember=True, on_text=None):
        self.asked.append((utterance, remember))
        return self.reply


def test_the_line_about_to_be_spoken_is_put_in_front_of_the_brain_verbatim():
    # It is the brain that holds the conversation, so the check is one question to the brain with
    # the exact words attached - not a second copy of the conversation kept somewhere else, which
    # would be one more part that does not know what the others know.
    brain = Brain()

    overtaken(brain, "You're on italki - which language are you learning there?")

    [(asked, remember)] = brain.asked
    assert "which language are you learning there?" in asked
    assert remember is False  # a check is not a turn of the conversation


def test_only_an_explicit_skip_holds_a_line_back():
    # News that is never spoken is this project's worst failure - a merged feature once died
    # unheard in a queue. So the gate opens on anything but a clear answer that this has been
    # overtaken, and every uncertainty falls toward saying it.
    assert overtaken(Brain("skip"), "line") is True
    assert overtaken(Brain("SKIP - he answered this two turns ago"), "line") is True
    assert overtaken(Brain("say"), "line") is False
    assert overtaken(Brain(""), "line") is False
    assert overtaken(Brain("I'm not sure"), "line") is False


def test_a_brain_that_cannot_answer_lets_the_line_through():
    class Wedged:
        def respond(self, utterance, *, remember=True, on_text=None):
            raise RuntimeError("the brain is not answering")

    assert overtaken(Wedged(), "line") is False


def test_no_brain_at_all_is_not_a_gate():
    assert overtaken(None, "line") is False


def test_the_prompt_forbids_rewording_it():
    # The one thing this must never do is answer with a better version of the line. The app speaks
    # held news word for word precisely because the brain retelling it in its own words put two
    # versions of the same update in front of him thirteen seconds apart.
    assert "SAY" in GATE_PROMPT and "SKIP" in GATE_PROMPT
    assert "reword" in GATE_PROMPT.lower() or "rewrite" in GATE_PROMPT.lower()
