"""Small local chores, run quietly - no agent tab for every little thing.

"I just don't want my agents log tab to be cluttered with an agent for every little thing I ask
it to do, rather than more like one agent per actual major task." The fast brain deliberately has
no file tools - that is part of why it answers in a breath - so the little jobs go to another
part of the brain: one quiet helper session with file tools and nothing else, no desk entry, no
tab, no worktree. It does the chore, reports one sentence, and the narrator words the outcome in
Excephalon's own voice like any other news.

Real work still goes to real agents: this runs errands, it does not build features.

The errand hand is also where the user's own services plug in. runtime/services.json (standard
{"mcpServers": ...} shape, personal, never the source) hands this session their MCP servers -
Asana, Gmail, Google Calendar - so "what's on my calendar" is a chore like any other: done
off-turn, narrated back, and the fast brain stays tools=[], answering in a breath. Each server
authenticates once through the CLI's own OAuth (/mcp in an interactive `claude`), which is where
the tokens live - nothing secret is ever in this repo or its config.
"""

import json
import threading
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from excephalon.models import FAMILIES
from excephalon.sdk_session import SdkSession

ERRAND_MODEL = FAMILIES["haiku"]  # fetch-and-carry work: the smallest, fastest tier

PROMPT = (
    "[Errand from Excephalon on the user's behalf - no user is in this exchange. Do this small "
    "chore now, using your tools - files here, or the user's own connected services - and reply "
    "with a short plain report (a sentence or two) of what you found or did, or exactly what "
    "stopped you. The app's own records are under runtime/: live agent logs in "
    "runtime/agent-logs/, wrapped-up agents' logs in runtime/agent-logs-archive/, conversation "
    "transcripts in runtime/transcripts/.\n{chore}]"
)


def load_services(path):
    """The user's connected services - runtime/services.json - as (servers, problem).

    The file is the standard MCP `{"mcpServers": {name: config}}` shape, so a snippet from any
    service's docs pastes straight in. Personal, so it lives in runtime/ and never the source.
    Absent is ordinary (no services, no complaint); a file he EDITED that cannot be read must be
    said, because a config that silently does nothing reads as a broken app - so the problem comes
    back as a sentence for an aside, and a bad file refuses whole rather than guessing at halves."""
    path = Path(path)
    if not path.exists():
        return {}, ""
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"({path.name} could not be read - no services connected: {exc})"
    servers = held.get("mcpServers") if isinstance(held, dict) else None
    if not isinstance(servers, dict) or not all(isinstance(one, dict) for one in servers.values()):
        return {}, f'({path.name} is not the expected {{"mcpServers": {{...}}}} shape - no services connected)'
    return servers, ""


def services_note(services):
    """Persona line naming what errands can reach, or "" with nothing connected.

    A lever nobody mentions is a lever never pulled: without this the brain answers "I can't see
    your calendar" while the errand hand sits right there able to look. Empty when nothing is
    connected, because a brain told about absent services promises checks that can only fail."""
    if not services:
        return ""
    named = ", ".join(sorted(services))
    return (
        f" The errand hand is also connected to his own services - {named} - so checking or "
        "updating any of them ('what's on my calendar', 'any new email from X', 'what's due in "
        "Asana') is a run_errand job: dispatch it and the answer comes back as its own note. "
        "Never guess at what a service would say, and never claim you cannot see one of these."
    )


def _errand_options(cwd, services=None):
    services = services or {}
    return ClaudeAgentOptions(
        cwd=str(cwd),
        model=ERRAND_MODEL,
        # File tools and a shell: enough for moving, tidying, reading, renaming - plus every tool
        # of each connected service, allowed whole (mcp__<name>). The user runs whole coding
        # agents unattended by choice; a chore hand needs no more ceremony.
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
                      + [f"mcp__{name}" for name in services],
        mcp_servers=services,
        permission_mode="bypassPermissions",
        setting_sources=[],
        # Only the servers handed over above - account-level connectors (claude.ai Gmail and
        # friends) otherwise attach to any session the CLI opens, and a headless one that tries
        # to OAuth them has no browser and no user; sessions wedge on exactly that
        # (anthropics/claude-code#36060).
        extra_args={"strict-mcp-config": None},
    )


DEFAULT_DEADLINE = 180.0  # a chore is a small thing; one that outlives this is stuck, not slow


class ErrandRunner:
    """One quiet helper session, opened on first use, reused for every chore after - one chore
    AT A TIME. The first turn that dispatched two at once put both asks on the one session
    together; they collided on its stream and both wedged - "Both running—results in a moment,"
    then fifteen minutes of nothing, no answer and no error either."""

    def __init__(self, cwd, events, *, services=None, session_factory=SdkSession,
                 deadline=DEFAULT_DEADLINE):
        self._cwd = cwd
        self._events = events  # (kind, agent, report) - the same sink the desk's news takes
        self._services = services or {}
        self._session_factory = session_factory
        self._session = None
        self._deadline = deadline
        self._lock = threading.Lock()
        self._one_at_a_time = threading.Lock()

    def run(self, chore):
        """Take one chore. Returns at once; the outcome arrives as an "errand" event, worded by
        the narrator - so the user hears one sentence in Excephalon's voice, not a tool transcript."""
        threading.Thread(target=self._work, args=(chore,), daemon=True).start()

    def _work(self, chore):
        with self._one_at_a_time:
            try:
                said = self._bounded_ask(PROMPT.format(chore=chore))
            except Exception as exc:
                # A chore that silently evaporated would be the lost-agent failure in miniature.
                self._events("errand", "errands", f"the errand could not run: {exc}")
                return
        self._events("errand", "errands", said.strip() or "(finished without a word)")

    def _bounded_ask(self, prompt):
        """One ask that cannot vanish: past the deadline the session is closed - a dead session
        makes the stranded ask raise, the same recovery the brain uses - and the outcome SAYS the
        chore was given up on, because no answer and no error is the worst of the three."""
        outcome, answered = {}, threading.Event()
        session = self._ensure_session()

        def ask():
            try:
                outcome["said"] = session.ask(prompt)
            except Exception as exc:
                outcome["raised"] = exc
            finally:
                answered.set()

        threading.Thread(target=ask, daemon=True).start()
        if not answered.wait(self._deadline):
            with self._lock:
                self._session = None  # the next chore builds a fresh one
            try:
                session.close()
            except Exception:
                pass
            raise RuntimeError(f"it could not finish within {self._deadline:.0f}s and was given up on")
        if "raised" in outcome:
            raise outcome["raised"]
        return outcome["said"]

    def _ensure_session(self):
        with self._lock:
            if self._session is None:
                self._session = self._session_factory(_errand_options(self._cwd, self._services))
            return self._session

    def close(self):
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass  # a session already gone must not block shutdown
