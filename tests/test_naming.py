import threading

from excephalon.naming import AgentNamer, compose, distill_name, unique_name


def test_compose_prefixes_the_project_and_hyphenates_the_words():
    # "if it's a Highdeas task then the agent name must begin with `highdeas-`" - the project is
    # the first word, lowercased, and the distilled words follow.
    assert compose("Highdeas", "smart grouping of ideas") == "highdeas-smart-grouping-ideas"


def test_compose_without_a_project_is_just_the_distilled_words():
    # Excephalon's own work has no project card (project=None), so no prefix - just the words.
    assert compose(None, "voice fallback layer") == "voice-fallback-layer"
    assert compose("", "voice fallback") == "voice-fallback"


def test_compose_caps_the_words_at_three():
    # "distills it down to 1-3 words" - a model that returns more is trimmed, not obeyed.
    assert compose("Highdeas", "one two three four five") == "highdeas-one-two-three"


def test_compose_drops_filler_words_but_never_down_to_nothing():
    # A short label reads better without the connectives, but an all-filler phrase must still
    # yield something rather than vanishing.
    assert compose(None, "fix the drive link") == "fix-drive-link"
    assert compose(None, "of the to for") != ""


def test_compose_always_yields_a_name_even_from_nothing():
    # An agent must always have a name: an empty distillation falls back to the project, or to a
    # last-resort word, never to "".
    assert compose(None, "") == "agent"
    assert compose("Highdeas", "") == "highdeas"


def test_distill_name_is_the_mechanical_prefixed_fallback():
    # No model in the loop: the task's own first meaningful words, still prefixed by the project.
    assert distill_name("smart grouping of ideas", "Highdeas") == "highdeas-smart-grouping-ideas"
    assert distill_name("wire the neural voice fallback") == "wire-neural-voice"


def test_unique_name_passes_a_free_name_through():
    assert unique_name("highdeas-audio", set()) == "highdeas-audio"


def test_unique_name_bumps_a_name_already_taken():
    # Short distilled names collide far more readily than the old task-length ones, and a collision
    # on the desk's key silently REPLACED a running agent - so a taken name is bumped, not reused.
    assert unique_name("highdeas-audio", {"highdeas-audio"}) == "highdeas-audio-2"
    assert unique_name("highdeas-audio", {"highdeas-audio", "highdeas-audio-2"}) == "highdeas-audio-3"


def test_unique_name_is_case_insensitive_like_the_log_files_it_guards():
    # A name IS a filename; Windows would fold "Fix" and "fix" onto one log, so the guard folds too.
    assert unique_name("Highdeas-Audio", {"highdeas-audio"}) == "Highdeas-Audio-2"


class _FakeSession:
    """Stands in for an SdkSession: records the prompt, answers with a canned reply (or raises, or
    blocks forever), and notes that it was closed."""

    def __init__(self, options, *, reply="", boom=None, block=None):
        self.options = options
        self._reply = reply
        self._boom = boom
        self._block = block
        self.closed = False
        self.asked = None

    def ask(self, prompt, on_message=None, on_text=None):
        self.asked = prompt
        if self._block is not None:
            self._block.wait()
        if self._boom is not None:
            raise self._boom
        return self._reply

    def close(self):
        self.closed = True


def test_the_namer_distills_the_task_through_the_model_and_prefixes_the_project():
    # The "layer of Excephalon which reads and understands the task": a small model reads the task
    # and hands back the words; the project is prefixed onto them.
    made = []

    def factory(options):
        session = _FakeSession(options, reply="Auto-play toggle")
        made.append(session)
        return session

    namer = AgentNamer(session_factory=factory)
    name = namer.name("add a checkbox in the modal that disables audio auto-play", "Highdeas")

    assert name == "highdeas-auto-play-toggle"
    assert made[0].closed  # the one-off naming session is let go after use


def test_the_namer_falls_back_to_the_task_words_when_the_model_errors():
    # A name is never worth failing an agent-start over: a model that cannot be reached drops to
    # the task's own first meaningful words, still prefixed.
    def factory(options):
        return _FakeSession(options, boom=RuntimeError("no model"))

    namer = AgentNamer(session_factory=factory)

    assert namer.name("wire the neural voice fallback", "Highdeas") == "highdeas-wire-neural-voice"


def test_the_namer_gives_up_on_a_hung_model_and_still_returns_a_name():
    # The distillation is bounded: past the deadline the session is shed and the mechanical name
    # stands in, so a hung model can never block an agent-start forever.
    stuck = threading.Event()

    def factory(options):
        return _FakeSession(options, block=stuck)

    namer = AgentNamer(session_factory=factory, deadline=0.05)
    try:
        assert namer.name("polish the audio scrubber cursor", "Highdeas") == "highdeas-polish-audio-scrubber"
    finally:
        stuck.set()  # let the daemon ask-thread unwind
