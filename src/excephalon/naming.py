"""Naming a fresh agent - a short, thought-through label, not the task slugified.

An agent's name is the most user-facing string the fleet has: the tab in the window, the line in
the roll call when several finish at once, the word Excephalon speaks when it has news about the
work. It used to be the task text with its spaces turned to hyphens -
"agent-names-shouldn-t-be-the-name-of-the-task-with-hyphens" - unreadable on a tab and unspeakable
in a sentence.

A name here is DISTILLED instead. A small, fast model reads the task, understands it, and hands
back one to three words for it (`AgentNamer`); the project it belongs to is prefixed, so a Highdeas
task is "highdeas-<words>" and Excephalon's own work is "excephalon-<words>" (SELF_PROJECT, for a
task with no project card of its own), so the roll call groups by project at a glance. When the task
is a numbered item off one of his cards, that number rides between the project and the words -
"highdeas-7-<words>" - so the label ties the agent to the exact card item at a glance too. The
distillation is the one part that can be slow or fail, so it is bounded and ALWAYS has a mechanical
fallback - the task's own first few meaningful words (`distill_name`). The app never blocks forever
on a name, and never fails to start an agent because a name could not be thought up.

`unique_name` is the other half: distilled names are short enough to collide, and a collision on
the desk's key silently REPLACED a running agent, so a taken name is bumped rather than reused.

The worktree and the branch keep their own (git's) names; this is only the LABEL the user sees.
"""

import re
import threading

from claude_agent_sdk import ClaudeAgentOptions

from excephalon.models import FAMILIES
from excephalon.sdk_session import SdkSession
from excephalon.tailing import safe_name

MAX_NAME_WORDS = 3  # "distills it down to 1-3 words"

# What a task with no project card of its own belongs to: Excephalon itself. Its own roadmap items
# (the Enhancements card) and any ad-hoc self-work carry project=None for ticking purposes, but the
# NAME still wants a prefix - "excephalon-distill-names", never a bare "distill-names" - so the roll
# call groups its own work by project too, exactly like Highdeas's.
SELF_PROJECT = "excephalon"

# Words that carry no identity in a short label - dropped so "fix the drive link" becomes
# "fix-drive-link", not "fix-the-drive". Kept to the truly contentless connectives (articles,
# prepositions, conjunctions, copulas, pronouns, and bare politeness), so a real word - a verb or
# a noun that names the work - is never mistaken for filler.
_FILLER = frozenset((
    "a", "an", "the", "of", "to", "for", "and", "or", "in", "on", "at", "with", "by", "from",
    "into", "onto", "as", "is", "are", "be", "it", "its", "that", "this", "these", "those",
    "please", "should",
))


def _words(phrase):
    """The up-to-three meaningful words of a phrase, lowercased. Filler is dropped, but never down
    to nothing: an all-filler phrase keeps its own words rather than distilling to emptiness. Empty
    only when the phrase held no letters or digits at all."""
    tokens = [token for token in re.split(r"[^a-z0-9]+", str(phrase).lower()) if token]
    meaningful = [token for token in tokens if token not in _FILLER]
    return (meaningful or tokens)[:MAX_NAME_WORDS]


def compose(project, phrase, item_id=None):
    """The final agent name: "<project>-<item#>-<1-3 words>", trimmed to what a filename and a URL
    segment can carry. A missing or unusable `project` is Excephalon's own work, prefixed
    "excephalon-" (SELF_PROJECT). `item_id`, when the task is a numbered item off one of his cards,
    rides between the project and the words - so a tab reads "highdeas-7-smart-grouping" and the
    roll call ties the agent to the exact card item; work that is on no list omits it. Never returns
    "": an empty phrase still yields the prefix, so an agent always has a name."""
    prefix = (safe_name(project).lower() if project else "") or SELF_PROJECT
    if item_id is not None:
        prefix = f"{prefix}-{item_id}"
    body = "-".join(_words(phrase))
    name = f"{prefix}-{body}" if body else prefix
    return safe_name(name) or SELF_PROJECT


def distill_name(task, project=None, item_id=None):
    """A name WITHOUT the model: the task's own first few meaningful words, prefixed by the project
    (or "excephalon-" when it has none of its own) and by its card number when it has one. This is
    the default namer the tools carry, and the fallback the thinking namer drops to when the model
    cannot be reached. Better than the whole task hyphenated; a plain string, no I/O."""
    return compose(project, task, item_id)


def unique_name(candidate, taken):
    """`candidate` if it is free, else the first "candidate-2", "candidate-3", ... not in `taken`.

    Case-insensitive, because a name IS a filename and Windows would fold "Fix" and "fix" onto one
    log. `candidate`'s own case is preserved in what is returned."""
    folded = {str(name).lower() for name in taken}
    if candidate.lower() not in folded:
        return candidate
    number = 2
    while f"{candidate}-{number}".lower() in folded:
        number += 1
    return f"{candidate}-{number}"


NAME_MODEL = FAMILIES["haiku"]  # a label is fetch-and-carry thinking: the smallest, fastest tier
NAME_DEADLINE = 20.0  # a name is never worth blocking an agent-start longer than this

PROMPT = (
    "[Name a coding task, for a label on a tab and a word said aloud. Reply with ONE to THREE "
    "plain words and NOTHING else - no punctuation, no quotes, no explanation - the words a person "
    "would use to refer to this work (like 'auto-play toggle', 'voice fallback', 'drive link'). "
    "Not a sentence, not the whole task, just the words. Task:\n{task}]"
)


def _name_options():
    return ClaudeAgentOptions(
        model=NAME_MODEL,
        tools=[],  # it only has to think of words: no files, no shell, nothing to wait on
        permission_mode="bypassPermissions",
        setting_sources=[],  # no user/project CLAUDE.md, no hooks - just the model
        # Pinned against account-level claude.ai connectors, which attach to any session the CLI
        # opens and wedge a headless one on a browserless OAuth (anthropics/claude-code#36060).
        extra_args={"strict-mcp-config": None},
    )


class AgentNamer:
    """The thinking namer: a small model reads the task and gives back the words for it.

    A fresh one-off session per name (naming is infrequent, so no session is held open for it), and
    the ask is bounded - past the deadline the session is shed and the mechanical `distill_name`
    stands in. Every failure path lands on that same fallback, so `name` cannot raise and cannot
    hang: an agent-start is never held up, and never blocked, by the naming step."""

    def __init__(self, *, session_factory=SdkSession, deadline=NAME_DEADLINE, options=None):
        self._session_factory = session_factory
        self._deadline = deadline
        self._options = options if options is not None else _name_options()

    def name(self, task, project=None, item_id=None):
        """A distilled "<project>-<item#>-<1-3 words>" label for an agent about to work on `task`.
        `item_id`, when the task is a numbered card item, ties the label to it on both paths."""
        try:
            phrase = self._distilled(str(task or ""))
        except Exception:
            phrase = ""
        # Only trust the model's words if there are any; otherwise the task's own words distill it.
        if _words(phrase):
            return compose(project, phrase, item_id)
        return distill_name(task, project, item_id)

    def _distilled(self, task):
        """The model's one-to-three words for the task, or "" - bounded so a hung session cannot
        wait forever. The session is closed either way; on timeout that close is what makes the
        stranded ask raise inside its daemon thread and unwind."""
        if not task.strip():
            return ""
        session = self._session_factory(self._options)
        outcome, answered = {}, threading.Event()

        def ask():
            try:
                outcome["said"] = session.ask(PROMPT.format(task=task))
            except Exception as exc:  # noqa: BLE001 - the caller's fallback covers every failure
                outcome["raised"] = exc
            finally:
                answered.set()

        threading.Thread(target=ask, daemon=True).start()
        try:
            if not answered.wait(self._deadline):
                raise RuntimeError(f"no name in {self._deadline:.0f}s - the session was shed")
            if "raised" in outcome:
                raise outcome["raised"]
            return outcome["said"]
        finally:
            try:
                session.close()
            except Exception:
                pass
