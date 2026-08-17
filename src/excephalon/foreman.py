"""The senior half of the fleet: a smarter model that unsticks working agents.

The fast brain is a talker - Haiku, tuned for the breath-quick turn - and the working agents are
where the real work happens. Between them sat a gap the user kept having to fill himself: an
agent pauses on a technical question, or stops short of done, and the judgment needed to push it
through is more than the talker has but less than the user should be bothered for. His ask: "a
smarter Claude agent would take care of negotiating issues that come up with the working agents."

The foreman is that layer, engaged only when the brain asks (the ask_foreman tool), so the bigger
model is paid for per snag rather than per turn. It reads the agent's task, the situation, and
the tail of the agent's own log, then either settles it - sends the agent what it needs through
its one typed tool and answers "handled", and the user never hears of it - or writes the one or
two sentences the user genuinely must see, which go to the outbox like any other news. Those go
out app-authored (not composed by the fast brain), so the unwritten-lines ledger reads them back
to the brain and one Excephalon remembers everything said in its name.
"""

import re
import threading

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from excephalon.models import FAMILIES
from excephalon.narrator import _HANDLED_LEAD
from excephalon.sdk_session import SdkSession

# The swallow-word at the END of a longer reply: the contract is "settle it, then reply with the
# single word: handled", and one settling came back as three sentences of the foreman's own
# analysis with "handled" on the last line. Everything before a closing swallow-word is working
# notes, never user-addressed - queued anyway, that analysis sat as the agent's "update" in the
# roll call, a jargon bomb waiting for him to pick its number.
_HANDLED_CLOSE = re.compile(r"(?i)\bhandled[.!]?\s*$")

# Smarter than the talker by definition, whatever the working agents happen to run on - the whole
# point of the layer is senior judgment, so it does not follow the next-agent model choice.
FOREMAN_MODEL = FAMILIES["opus"]
FOREMAN_EFFORT = "high"

PROMPT = (
    "[Foreman turn, from the app - no user is in this exchange. You are the senior half of "
    "Excephalon: the fast brain handed you a working agent's situation instead of bothering the "
    "user.\nAgent: {agent}\nIts task: {task}\nThe situation: {question}\n"
    "The tail of its log:\n{log}\n\n"
    "If a competent technical lead could settle this - answer the question, correct the course, "
    "tell it plainly to finish - settle it: send the agent what it needs with tell_agent, then "
    "reply with the single word: handled. Only if it genuinely needs the user - a preference, "
    "scope, spending, sign-off - reply instead with one or two short sentences addressed to "
    "them, in Excephalon's voice, saying what is needed and what you recommend.]"
)


def _foreman_options(server):
    return ClaudeAgentOptions(
        tools=[],  # no built-ins: investigation stays with the agents; its lever is the one tool
        mcp_servers={"foreman": server},
        allowed_tools=["mcp__foreman__tell_agent"],
        model=FOREMAN_MODEL,
        effort=FOREMAN_EFFORT,
        permission_mode="bypassPermissions",  # an in-process tool with nowhere to ask a yes/no
        setting_sources=[],
        # Pinned to the servers named here - account-level claude.ai connectors attach to any
        # session the CLI opens, and a headless one that tries to OAuth them has no browser and
        # no user; a brain replacement session wedged on exactly that, 90 seconds of silence
        # into a spoken error and a force-quit (anthropics/claude-code#36060).
        extra_args={"strict-mcp-config": None},
    )


class Foreman:
    """One persistent senior session, built the first time it is needed - it remembers its past
    negotiations, so the second snag with the same agent starts from context, not from zero."""

    def __init__(self, desk, outbox, *, session_factory=SdkSession):
        self._desk = desk
        self._outbox = outbox
        self._session_factory = session_factory
        self._session = None
        self._lock = threading.Lock()

    def consider(self, agent, question):
        """Take one stuck agent's situation. Returns at once; the settling happens off-thread,
        because the brain's tool call must never wait on a senior model's think."""
        threading.Thread(target=self._work, args=(agent, question), daemon=True).start()

    def _work(self, agent, question):
        # A question about an agent whose thread already ENDED has its answer on file, not in a
        # senior model. A landing agent's auto-wrap-up beat the quiet alarm's question here: the
        # foreman found the desk empty, reasoned "with no log I can't confirm which" about logs
        # sitting whole in the archive, and its shrug reached the user as a heads-up ("that's
        # fucking bullshit, the logs are right there"). Delivered work needs no investigation
        # and no news - the landed narration already said it.
        over = getattr(self._desk, "ended", None)
        if over is not None and over(agent) == "delivered":
            return
        prompt = PROMPT.format(
            agent=agent,
            task=self._desk.task_of(agent) or "(unknown)",
            question=question,
            log=self._desk.recent_log(agent) or "(no log yet)",
        )
        try:
            said = self._ensure_session().ask(prompt)
        except Exception as exc:
            # The problem must not vanish into a dead session: the user hears that the hand-off
            # failed, which beats an agent silently stuck behind a foreman who never answered.
            self._outbox.push(f"The foreman couldn't take {agent}'s problem: {exc}", about=agent)
            return
        said = said.strip()
        if said and _HANDLED_CLOSE.search(said):
            return  # settled, however much working-notes preamble came with the swallow-word
        # The same strip the narrator uses: "handled" is the swallow-word, and it has reached him
        # at the head of a longer sentence - "Handled." and then a paragraph of its own,
        # which is how the swallow-word reached him three seconds after a launch.
        said = _HANDLED_LEAD.sub("", said) if said else ""
        if not said.strip():
            return  # settled with the agent directly; there is no news to interrupt anyone with
        self._outbox.push(said.strip(), about=agent)  # app-authored: the ledger informs the brain

    def _ensure_session(self):
        with self._lock:
            if self._session is None:
                server = create_sdk_mcp_server(name="foreman", tools=self.tools())
                self._session = self._session_factory(_foreman_options(server))
            return self._session

    def tools(self):
        """The foreman's one lever, built against this desk. Public so tests can drive the
        handler without a real session."""

        @tool("tell_agent", "Send the working agent the answer, correction or push it needs. "
              "`name` is the agent's name; `message` is what to tell it.",
              {"name": str, "message": str})
        async def tell_agent(args):
            delivered = self._desk.send(str(args["name"]).strip(), str(args["message"]))
            text = "Delivered." if delivered else "No agent by that name is at the desk."
            return {"content": [{"type": "text", "text": text}]}

        return [tell_agent]

    def close(self):
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass  # a session already gone must not block shutdown
