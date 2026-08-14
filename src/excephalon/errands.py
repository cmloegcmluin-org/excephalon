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


def services_note(services, broken=None):
    """Persona line naming what errands can reach, or "" with nothing connected.

    A lever nobody mentions is a lever never pulled: without this the brain answers "I can't see
    your calendar" while the errand hand sits right there able to look. Empty when nothing is
    connected, because a brain told about absent services promises checks that can only fail.

    `broken` is {name: why} for services that are configured but do not answer (see
    `check_services`). Those are named as FAULTY with their reason rather than left out or
    passed off as working: told only the configured names, the brain answered that services set
    up weeks ago "aren't set up yet" and that it had no idea how to fix them - while the fault
    was a stale path in his own config file, sitting in the failure's own words."""
    if not services:
        return ""
    broken = broken or {}
    healthy = sorted(name for name in services if name not in broken)
    note = ""
    if healthy:
        note = (
            f" The errand hand is also connected to his own services - {', '.join(healthy)} - so "
            "checking or updating any of them ('what's on my calendar', 'any new email from X', "
            "'what's due in Asana') is a run_errand job: dispatch it and the answer comes back as "
            "its own note. Never guess at what a service would say, and never claim you cannot "
            "see one of these."
        )
    if broken:
        faults = "; ".join(f"{name} ({why})" for name, why in sorted(broken.items()))
        note += (
            f" These of his services are SET UP but not answering right now: {faults}. They are "
            "connected and broken, never missing - so never say one is not set up. If he asks "
            "about one, say plainly that it is failing and what the fault says; the reason above "
            "usually names the fix, and a run_errand can look closer at the app's own files."
        )
    return note


def _probe_stdio(name, config, *, timeout=20.0):
    """Speak MCP at one configured server and return "" when it answers, or why it did not.

    Launch-and-initialize only: no tool is called, so nothing of his is touched and a check
    costs a second. It catches exactly the failure that hid for weeks - a server that dies the
    moment it starts (a stale path, a missing interpreter) while the app went on announcing it
    as connected."""
    import json as _json
    import subprocess

    command = [config.get("command")] + list(config.get("args") or [])
    if not config.get("command"):
        return "no command to launch (only stdio servers can be checked)"
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", **_no_console())
    hello = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "excephalon-check", "version": "1"}}})
    try:
        answer, complaint = proc.communicate(hello + "\n", timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return f"it did not answer within {timeout:.0f}s"
    finally:
        if proc.poll() is None:
            proc.kill()
    if '"result"' in (answer or ""):
        return ""
    trouble = " ".join((complaint or "").split())[-300:]
    return trouble or "it started and said nothing"


def _no_console():
    """Keep a probe from flashing a console window on his screen - the app runs under pythonw,
    where every child would otherwise conjure one."""
    from excephalon import machine

    if not machine.WINDOWS:
        return {}
    import subprocess

    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def check_services(services, *, probe=_probe_stdio):
    """{name: why} for every configured service that does not answer - {} when all of them do.

    Run on the way up, so a service that is set up but broken is SAID rather than announced as
    connected and then silently absent: "it's claiming that the Asana and Google integrations
    that we worked so fucking hard for aren't working now, and that it has no idea how to fix
    them" - Asana's server had been crashing at launch on a stale path in his own config. A probe
    that itself falls over is that service's fault, never the app's: this runs before the window
    exists, and a launch with no mouth is this project's oldest failure."""
    broken = {}
    for name, config in services.items():
        try:
            why = probe(name, config)
        except Exception as exc:
            why = f"the check could not run: {exc}"
        if why:
            broken[name] = why
    return broken


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
