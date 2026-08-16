"""Run Excephalon: `python -m excephalon` (speak to it), or double-click Excephalon.bat for the window.

  --gui         a window instead of the terminal: live transcript + a STOP button
  --text        type instead of speaking
  --mute        show replies as text, don't speak them
  --no-timings  hide the per-turn think/speak readout (shown by default)
"""

import signal
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from excephalon import machine
from excephalon.actions import fleet_actions
from excephalon.agent_desk import AgentDesk
from excephalon.brain_sdk import DEFAULT_PERSONA, SdkBrain
from excephalon.sdk_session import open_sign_in
from excephalon.console import Console
from excephalon.conversation import Conversation
from excephalon.errands import ErrandRunner, check_services, load_services, services_note
from excephalon.google_bridge import sign_in_fault
from excephalon.foreman import Foreman
from excephalon.inbox_watcher import InboxWatcher, QuietMonitor
from excephalon.mirror import TranscriptFeed
from excephalon.narrator import Narrator
from excephalon.memory import (
    DEFAULT_PROFILE_PATH,
    append_learned,
    complete_enhancement,
    compose_persona,
    lexicon_terms,
    load_learned,
    load_lexicon,
    load_persona_additions,
    load_profile,
    load_translations,
    number_enhancements,
    open_enhancements,
    open_projects,
    profile_without_project_tasks,
    StandingWatch,
    translation_pairs,
    user_name,
)
from excephalon.outbox import Outbox
from excephalon.polish import mend
from excephalon.relay import notice
from excephalon.shutdown import consolidate, leave_process
from excephalon.stt_console import ConsoleSTT
from excephalon.tailing import safe_name
from excephalon.homecoming import (
    STOCK_GREETING,
    changes_since,
    homecoming_note,
    last_boot,
    last_seen,
    record_boot,
)
from excephalon.transcript import MessageLog, Transcript, recent_turns
from excephalon.tts_cloud import CloudVoiceError, Failover, connect, settings_in
from excephalon.tts_neural import KokoroEngine, ensure_voice, voice_choice
from excephalon.tts_system import NullTTS, SystemTTS
from excephalon.voice import Speaker, play_stream
from excephalon.worktrees import head_commit

REPO = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO / "runtime"


def _live_instructions():
    """His standing instructions, read from the file THIS turn - an edit on the Config page is in
    force for the very next thing said, not the next launch. They are also composed into the boot
    persona; carrying them here too is what makes the edit immediate."""
    from excephalon.memory import DEFAULT_PERSONA_ADDITIONS_PATH

    try:
        told = DEFAULT_PERSONA_ADDITIONS_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not told:
        return ""
    return "\n\nHis standing instructions, live from the file - already in force:\n" + told
AGENT_INBOX = RUNTIME_DIR / "agent-inbox"  # agents drop questions/review-ready notes here, one per line
ACTIVE_AGENTS = RUNTIME_DIR / "active-agents.txt"  # who Excephalon has running, readable after a reset
AGENT_STATE = RUNTIME_DIR / "agents.json"  # the fleet's survival record: what a restart revives from
AGENT_LOGS = RUNTIME_DIR / "agent-logs"  # one timestamped exchange log per agent, written by the desk
TRANSCRIPTS = RUNTIME_DIR / "transcripts"  # one timestamped record per conversation, as it happens
MIC_OVERRIDE = RUNTIME_DIR / "mic.txt"  # optional: a device-name substring to force a specific mic
MIC_GAIN = RUNTIME_DIR / "mic-gain.txt"  # optional: a number to boost a quiet mic (e.g. 5)
SERVICES = RUNTIME_DIR / "services.json"  # his connected services (MCP servers), errand-hand reach
BOOT_RECORD = RUNTIME_DIR / "boot.json"  # where the last process stood: what the welcome-back reads
VOCAB_ROOTS = RUNTIME_DIR / "vocab-roots.txt"  # optional: extra dirs (one per line) to mine for project names
WORKSPACE = Path.home() / "workspace"  # default project tree; its folder names seed the custom vocabulary
AGENT_QUIET_AFTER = 20 * 60  # seconds of silence from an agent before Excephalon flags it to the user
FAILURE_LOG = RUNTIME_DIR / "launch-failure.log"  # why a launch didn't take - the same file launch.pyw writes


def note_failure(report=None, log=None, now=None):
    """Write a startup failure down and answer with where it went (None if even that failed).

    The same file launch.pyw writes, so a launch that did not take has ONE place to be read from
    - and deliberately its own few lines rather than an import of that launcher, which has to go
    on working when this package is exactly what cannot be imported."""
    log = FAILURE_LOG if log is None else Path(log)
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as out:
            out.write(f"\n===== {now or datetime.now():%Y-%m-%d %H:%M:%S} =====\n"
                      f"{report or traceback.format_exc()}")
        return log
    except OSError:
        return None


def _fresh_worktree_note():
    """Persona line: new work means a new worktree, and naming a new path to start_agent is all it
    takes - the tool cuts it from freshly-fetched origin/main itself, so the brain never runs git."""
    return (
        " Almost every agent you start is NEW work, which means a NEW worktree - don't resume an "
        "old one unless you are explicitly told to. Name a fresh worktree path to start_agent (a "
        "short kebab-case name for the work, under the project's .claude/worktrees/) and the tool "
        "cuts it from current origin/main itself."
    )


def _live_projects():
    """His project cards, live from the file this turn - the same treatment the Enhancements list
    gets, for the same reason. The boot persona's copy went stale the moment he made a card, and
    "take care of task #7 in the Highdeas Project" was answered with "I don't see a #7"."""
    open_now = open_projects()
    if not open_now:
        return ""
    return (
        "\n\nHis projects and their open tasks, live from the file this turn - the same cards his "
        "window's Projects tab shows, numbered per project. When he names a task by number, THIS "
        "is where to look it up; never say you cannot see it:\n" + open_now
    )


def _projects_note():
    """Persona line: where the user's projects live, so the brain never has to ask. It asked for
    the path to a repo whose name alone identified it; the directory listings already knew."""
    from excephalon.worktrees import projects

    homes = [(root, projects(root)) for root in _project_roots()]
    homes = [(root, known) for root, known in homes if known]
    if not homes:
        return ""
    listed = "; ".join(f"{root}: {', '.join(known)}" for root, known in homes)
    return (
        f" Their projects live one directory per project under these roots - {listed}. When they "
        "name one, that is the repo - never ask where it is. A new agent for a project works in "
        "<that project's directory>\\.claude\\worktrees\\<short-task-name>."
    )


def _mic_gain():
    """How much to amplify the mic. A quiet input - an onboard mic can peak around 0.009, under
    the 0.01 speech threshold - needs a boost or nothing registers as speech; loud mics leave
    this at 1."""
    try:
        return float(MIC_GAIN.read_text(encoding="utf-8").strip()) if MIC_GAIN.exists() else 1.0
    except ValueError:
        return 1.0


def _project_roots():
    """Everywhere the user's projects live: the workspace, plus each root listed in
    vocab-roots.txt - one file feeding both the transcription vocabulary and the brain's map,
    so a root added there fixes "it can't hear the name" and "it asked where the repo is" at once."""
    roots = [WORKSPACE]
    if VOCAB_ROOTS.exists():
        roots += [Path(line.strip()) for line in VOCAB_ROOTS.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    return roots


def _vocab_terms():
    """The terms Parakeet is biased toward, so a coined name like "Notecraft" stops coming back as
    "note craft". Two sources: project folder names (scanned off every project root), and the
    hand-kept lexicon - coined names and domain vocabulary alike, the same file the brain carries
    as standing context, so a term added in one place fixes both. The app's own name rides along,
    because "hey Excephalon" only wakes the mic if the transcriber can produce the word."""
    from excephalon.vocabulary import scan_terms

    return scan_terms(_project_roots()) | set(lexicon_terms(load_lexicon())) | {"Excephalon"}


def _other_apps():
    """His OTHER projects, by the folder names the vocabulary scan already finds. The Enhancements
    list is for changes to Excephalon itself; a feature request for one of these belongs to an
    agent in that project's own repo, and file_improvement refuses to file it (see
    actions.names_another_app) - twice it was filed anyway, and twice the remedy was a written
    instruction that held until it didn't."""
    from excephalon.vocabulary import scan_terms

    return sorted(name for name in scan_terms(_project_roots())
                  if name.lower() not in ("excephalon", "entity"))


def _agent_inbox_note(inbox):
    """Persona line telling Excephalon how its agents reach the user - the exact absolute path, since
    the agents run in other projects' worktrees and can't guess where Excephalon keeps its inbox."""
    return (
        " When you put a background agent on a task, tell that agent - in its own instructions - to "
        "write anything it needs from the user (a question, or that it's ready for review) as a single "
        f"line to {inbox}\\<a-short-agent-name>.txt. Nobody is watching the agents' screens, so that "
        "inbox is the only way they are heard from - always set it up when you delegate."
    )


def _window_note(logs):
    """Persona lines about the window the user is looking at - the part of the world the brain
    can't see but keeps getting asked about."""
    return (
        f" Every exchange with an agent is auto-written, timestamped, to {logs}\\<agent-name>.log "
        "- the window shows each of those as a live tab of its own, so a conversation is already "
        "watchable and you never open anything for them. Never hand-write your own log of an "
        "exchange; the desk keeps the real one. Their agents run on Opus 4.8 at high effort "
        "unless they choose otherwise - the fleet briefing says what a fresh agent starts on, so "
        "when they ask, answer from it; never say the choice isn't yours to make. When they ask "
        "you to file an enhancement, file EVERY item they named - one file_improvement call per "
        "item; filing one of two made them ask again for something they had already asked for."
    )


def _open_ears(announce):
    """The hardware half of listening - transcriber, mic, recorder - shared by both voice modes.

    Not "hearing", which is `excephalon.hearing`: that module is the live line, and one name for both
    would have the next reader looking for a screen in the microphone code."""
    import sounddevice as sd

    from excephalon.mic import BackgroundMicrophone, Microphone, choose_input_device, probe_input_device
    from excephalon.recorder import AudioRecorder
    from excephalon.transcribe import CorrectingTranscriber, ParakeetTranscriber

    # Bias transcription toward the user's own vocabulary, so their coined names survive it, and
    # swap outright the phrases that come back as ordinary English ("cloud agent"). The window's
    # Translations page shows both lists, so nothing here is applied unseen.
    terms = _vocab_terms()
    if terms:
        announce(f"(custom vocabulary: {len(terms)} of your terms, e.g. {', '.join(sorted(terms)[:3])})")
    transcriber = CorrectingTranscriber(ParakeetTranscriber(), terms,
                                        translations=translation_pairs(load_translations()))
    transcriber.warmup()  # load the 2.4 GB model now, not on the first spoken turn

    # Don't trust the OS default input - it is often an idle headset or a virtual device that
    # hands back silence. Pick the input that's actually hearing the room (or an override the user
    # drops in mic.txt), staying on the default's host API so the stream can actually be opened,
    # and say which mic won.
    override = MIC_OVERRIDE.read_text(encoding="utf-8").strip() if MIC_OVERRIDE.exists() else None
    default_input = sd.default.device[0]
    hostapi = sd.query_devices(default_input)["hostapi"] if default_input is not None else None
    device, device_name = choose_input_device(
        sd.query_devices(), probe_input_device, override=override, hostapi=hostapi
    )
    gain = _mic_gain()
    announce(f"(listening on mic: {device_name or 'system default'}{f', gain x{gain:g}' if gain != 1.0 else ''})")
    # Capture on a background thread: keep draining the mic even while Parakeet is transcribing, so
    # nothing they say mid-transcription is lost to a PortAudio overflow.
    mic = BackgroundMicrophone(Microphone(device=device, gain=gain))
    recorder = AudioRecorder(RUNTIME_DIR / "audio" / f"session-{datetime.now():%Y%m%d-%H%M%S}.wav")
    announce(f"(saving your audio to {recorder.path} - nothing you say gets lost, even on a crash)")
    return transcriber, mic, recorder


# Which of his configured services did not answer when the app came up ({name: why}). Probed once,
# on the way up, because a check costs a launch per server and the persona is recomposed every time
# his standing context moves; a fault that clears is picked up by the next restart, which is what
# fixing one of these takes anyway.
_SERVICE_FAULTS = {}


def _greeting(brain, booted_at, previous_boot, was_seen=0.0, waiting=(), note=None):
    """The first line of the session: a welcome back mid-conversation, or the stock greeting.

    "It shouldn't always say 'I'm ready. What can I do for you?' That should only be the default
    if we weren't in the middle of something when I restarted." A restart is his only way to pick
    up a fix, so most of them happen mid-thread - and the stock line greets him as a stranger
    about a conversation minutes old, with his own unanswered question still on the screen behind
    it. The brain does the wording; anything that goes wrong falls back to the stock line, because
    a greeting is not worth a launch."""
    if note is None:
        note = homecoming_note(
            turns=recent_turns(TRANSCRIPTS, keep=1),
            changes=changes_since(REPO, previous_boot.get("commit", "")),
            # How long he was WITHOUT it: from the last thing the old process wrote, not from
            # when that process started - which is the length of the conversation he just had.
            away=max(0.0, booted_at - max(was_seen, float(previous_boot.get("at") or booted_at))),
            waiting=waiting)
    if not note:
        return STOCK_GREETING
    try:
        said = brain.respond(note)
    except Exception:
        return STOCK_GREETING
    return said.strip() or STOCK_GREETING


def _google_faults(services):
    """{name: why} for his Google services when the sign-in behind them is dead.

    A launch check proves a server starts, and this bridge starts perfectly on a sign-in Google
    has revoked - startup announced Gmail and Calendar as reachable, and the first real errand
    hours later answered that they were never set up. The bridge is the app's own, so the app can
    ask it: one refresh, which doubles as the warm-up. Which servers are the bridge is read off
    the config he wrote, not assumed from their names."""
    ours = [name for name, config in services.items()
            if any("google_bridge" in str(part) for part in config.get("args") or ())]
    if not ours:
        return {}
    try:
        fault = sign_in_fault()
    except Exception as exc:  # a check that falls over must never stop the app from opening
        fault = f"its sign-in could not be checked: {exc}"
    return {name: fault for name in ours} if fault else {}


def _persona():
    """Everything Excephalon has been told about how to be - the standing rules, the user's own
    context, and every instruction added since (its own persona overlay). Composed in one place
    because the window shows this exact text, and a second copy would drift from the one the brain
    reads."""
    return (
        compose_persona(DEFAULT_PERSONA, profile_without_project_tasks(load_profile()),
                        load_learned(), load_lexicon(), additions=load_persona_additions())
        + _agent_inbox_note(AGENT_INBOX)
        + services_note(load_services(SERVICES)[0], broken=_SERVICE_FAULTS)
        + _fresh_worktree_note()
        + _projects_note()
        + _window_note(AGENT_LOGS)
    )


def _voice(announce):
    """The voice, fully settled BEFORE Excephalon says it is ready.

    It first shipped the other way - the robot System.Speech voice served while the neural model
    loaded in the background - and the first reply of every session came out robot-voiced. His
    call: "Just don't be ready until it loads. Time to start up is not precious; it's only time
    to respond while in session that matters." So startup blocks on the fetch (once ever), the
    load (~2s) and, when there is a cloud voice configured, the one request that proves its key.

    The cloud voice needs the local one behind it, so a machine that cannot have Kokoro does not
    get ElevenLabs either: without a fallback a dropped connection would be silence, and silence
    is the one thing this may never cost him."""
    local = _local_voice(announce)
    if local is None:
        return SystemTTS(rate=2)
    cloud = _cloud_voice(announce)
    if cloud is None:
        return Speaker(local, play=play_stream)
    return Speaker(Failover(cloud, local, announce=announce), play=play_stream)


def _local_voice(announce):
    """Kokoro, fetched, loaded and warmed - or None on a machine that genuinely can't have it,
    said out loud rather than discovered by ear."""
    paths = ensure_voice(RUNTIME_DIR / "tts", announce=announce)
    if paths is None:
        announce("(couldn't fetch the neural voice - the system voice will serve)")
        return None
    name, speed = voice_choice(RUNTIME_DIR / "tts")
    announce(f"(loading the voice: {name} - change it in runtime/tts/voice.txt)")
    engine = KokoroEngine(*paths, voice=name, speed=speed)
    try:
        engine.say("Ready.")  # the load and the warm-up, paid here rather than mid-conversation
    except Exception as exc:
        announce(f"(the neural voice failed to load: {exc!r} - the system voice will serve)")
        return None
    return engine


def _cloud_voice(announce):
    """ElevenLabs, once the ACCOUNT has answered for the key - or None, which is the ordinary
    case for a checkout that has never been given one and is announced as nothing at all.

    A configured service announced from its config alone is how this project once reported Gmail
    and Calendar reachable for a whole day against a server that never started; a key is spoken
    to before it is called connected, and a key that has expired is a thing to hear at startup
    rather than to discover as a voice that mysteriously changed mid-conversation."""
    settings = settings_in(RUNTIME_DIR / "tts")
    if settings is None:
        return None
    try:
        engine = connect(settings)
    except CloudVoiceError as fault:
        announce(f"({fault} - the local voice will speak)")
        return None
    announce(f"(the cloud voice answered: {settings['voice']} - edit runtime/tts/cloud.json)")
    return engine


def _build_ears(text_mode, stop, interrupt, announce=print):
    """Return (stt, mic, recorder) — mic/recorder are None in text mode; both close on exit.
    `interrupt` lets a quiet moment be broken off so Excephalon can pass on queued agent news."""
    if text_mode:
        return ConsoleSTT(), None, None
    from excephalon.stt_mic import MicSTT

    transcriber, mic, recorder = _open_ears(announce)
    cue = lambda: announce("  ✓ got it")  # visual "registered" the instant you say "over"
    stt = MicSTT(transcriber, mic, stop=stop, cue=cue, recorder=recorder, interrupt=interrupt)
    return stt, mic, recorder


def _session(*, announce, feed, gui, text_mode, muted, timings, stop, barge_in, attach=None,
             hooks=None):
    """Build everything and run the conversation to its end.

    Windowed, this runs on a worker while Tk owns the main thread - so the window is on screen
    within a moment of the click, and the model loading, the brain waking and the spoken greeting
    all happen where they can watch them. They were hearing "I'm ready" before any window appeared.
    """
    # Where the LAST process stood, read BEFORE this one records itself over it - that gap, and
    # the commits inside it, are the whole of what a welcome-back knows (see homecoming).
    booted_at = time.time()
    previous_boot = last_boot(BOOT_RECORD)
    # Read BEFORE this session's own transcript exists, or the newest write would be this one.
    was_seen = last_seen(TRANSCRIPTS)
    record_boot(BOOT_RECORD, head_commit(REPO), booted_at)
    # Word from the agents Excephalon drives lands in this inbox; the watcher tails it and the
    # Excephalon speaks each new line at the next lull (never cutting the user off).
    AGENT_INBOX.mkdir(parents=True, exist_ok=True)
    # Spooled, so news that has not reached the user yet survives a restart: three agents'
    # reports once lived only in a wedged process's memory, and died with it.
    outbox = Outbox(spool=RUNTIME_DIR / "outbox.json")

    # Every agent event - finished, died, wrote to its inbox, gone quiet - takes one trip through
    # the brain so what the user hears is the brain's own sentence, not a label read aloud. The
    # narrator needs the brain, which doesn't exist yet; until it does (a few seconds of startup),
    # the capped plain notice still carries any news, because news must never wait on wiring.
    newsroom = {}

    def agent_events(kind, agent, report):
        narrator = newsroom.get("narrator")
        if narrator is not None:
            narrator.tell(kind, agent, report)
        else:
            outbox.push(notice(agent, report), about=agent)

    # Don't just wait to be told - watch the agents. If one goes silent past the threshold, the
    # monitor surfaces a heads-up so the user isn't left in the dark by a hung or stalled agent.
    quiet_monitor = QuietMonitor(outbox, quiet_after=AGENT_QUIET_AFTER, events=agent_events)
    inbox_watcher = InboxWatcher(AGENT_INBOX, outbox, monitor=quiet_monitor, events=agent_events)
    inbox_watcher.start()

    announce("Excephalon is waking up...")
    # The chop mender: spurious sentence breaks healed instantly and deterministically (see
    # excephalon.polish - the model-based repairman is retired; it cost eight seconds a submit and
    # often did nothing).
    # The desk holds each agent as a live session on its own thread; the brain drives it through
    # typed in-process tools (start_agent, tell_agent, ...), so starting or messaging an agent
    # returns at once and whatever the agent says comes back through the outbox. Nothing the brain
    # does can block on agent work, and nothing it says doubles as a control channel.
    desk = AgentDesk(outbox, roster_path=ACTIVE_AGENTS, log_dir=AGENT_LOGS, monitor=quiet_monitor,
                     events=agent_events, state_path=AGENT_STATE,
                     # The machine-wide engineering law, split out of the user's personal config
                     # so working agents can be pointed at exactly it. Home-relative, so nothing
                     # personal enters the source and a machine without the split just skips it.
                     law_path=Path.home() / ".claude" / "engineering.md",
                     # Wrapping up an agent started for an Enhancements item ticks that item off
                     # the user's list (profile.md) - the pool they file into, self-draining as
                     # the work lands.
                     complete_enhancement=complete_enhancement)
    # The senior layer: engaged only when the brain hands it a stuck agent (ask_foreman), so its
    # bigger model is paid for per snag, never per turn.
    foreman = Foreman(desk, outbox)
    # The quiet errand hand: small local chores with no agent tab - "one agent per actual major
    # task", not one per little thing. Its outcomes take the news road like everything else. His
    # connected services (runtime/services.json) ride with it: "check my calendar" is a chore too,
    # and both what connected and a file that could not be read are SAID, since a config applied
    # unseen - or silently not applied - reads as a broken app.
    services, services_problem = load_services(SERVICES)
    if services_problem:
        announce(services_problem)
    if services:
        # Each one is actually SPOKEN TO before it is announced as reachable. Announced from the
        # config alone, a server that died the moment it launched still read as connected - his
        # Asana sat broken on a stale path for a day while the brain, told only the name, said the
        # service "isn't set up yet" and had no idea how to fix it.
        _SERVICE_FAULTS.clear()
        _SERVICE_FAULTS.update(check_services(services))
        _SERVICE_FAULTS.update(_google_faults(services))
        working = sorted(name for name in services if name not in _SERVICE_FAULTS)
        if working:
            announce(f"(errands can reach: {', '.join(working)})")
        for name, why in sorted(_SERVICE_FAULTS.items()):
            announce(f"({name} is set up but not answering: {why})")
    errands = ErrandRunner(RUNTIME_DIR, agent_events, services=services)
    if hooks is not None:
        # His name for an agent, from the page's own heading - the desk owns the key, the log and
        # the record, so the window asks it rather than moving files behind its back.
        hooks["rename_agent"] = desk.rename
    # The other apps he has, by their folder names - the same scan that teaches the ear his
    # project names. file_improvement refuses a feature request naming one of them: the
    # Enhancements list is for changes to Excephalon itself, and a request for another app
    # belongs to an agent in that app's own repo.
    actions_server, _ = fleet_actions(desk, foreman, errands, other_apps=_other_apps())
    # Seeded with the tail of the last session's transcript, so a restart - their only way of picking
    # up a fix - resumes the conversation instead of greeting them as a stranger.
    brain = SdkBrain(persona=_persona(), user=user_name(load_profile()), actions=actions_server,
                     seed_turns=recent_turns(TRANSCRIPTS))
    brain.warmup(announce=announce)
    # From here on, news arrives in the brain's own voice - and worded by where the work stands:
    # a finished turn is presentation news while building, wrap-up news while landing approved work.
    newsroom["narrator"] = Narrator(brain, outbox, stage_of=desk.delivery_stage)
    # "I close it and reopen it constantly": bring back every agent the last process recorded,
    # each resumed on its old session - one caught mid-task is told to pick back up. After the
    # narrator, so an instantly-finishing revival is narrated, not read out as a label.
    revived = desk.revive()
    if revived:
        announce(f"(reattached to last session's agents: {', '.join(revived)})")
    dictation = None
    hearing = None
    if gui:
        # The window's mic is a STATE, not a walkie-talkie: continuous dictation into the editable
        # draft, controlled by voice ("hey entity" / "stop listening"), the mic button, and Submit.
        from excephalon.dictation import Dictation
        from excephalon.hearing import Hearing

        transcriber, mic, recorder = _open_ears(announce)
        if hooks is not None:
            # The page's translation edits reach the running ear the moment they save.
            hooks["retune"] = getattr(transcriber, "retune", lambda **_: None)
        # Hearing only his voice is DROPPED by his call - "let's drop this feature. It's not
        # important right now and it seems like it's too difficult for you to accomplish
        # effectively." The measuring is what is switched off here: 3,258 scored chunks showed his
        # own speech and the app's own leaked voice sitting on top of each other (see CLAUDE.md),
        # so the scoring was paying a worker per chunk for evidence nobody was going to act on.
        # voiceprint.py and his enrollment stay where they are; nothing reads them.
        dictation = Dictation(
            transcriber, mic, recorder=recorder, stop=stop, interrupt=outbox.arrived,
            hearing=hearing,
            polish=mend,  # spurious sentence breaks healed on the way to the brain, instantly
            muted=True,  # the mic starts OFF; they turn it on when they're ready to talk
            on_draft=lambda t: feed.push("draft", t),
            on_state=lambda s: feed.push("state", s),
            on_level=lambda v: feed.push("level", v),
            on_submit_request=lambda: feed.push("submit", ""),
            on_retract=lambda: feed.push("retract", ""),
        )
        if attach is not None:
            attach(dictation)  # the window is already up, waiting to be wired to a mic
        dictation.start()
        stt = dictation
    else:
        stt, mic, recorder = _build_ears(text_mode, stop, outbox.arrived, announce)

    tts = NullTTS() if muted else _voice(announce)

    def watch_keys():
        for _ in sys.stdin:  # every Enter is a barge-in: shut the current reply up
            barge_in.set()

    if not text_mode and not gui:  # the window binds Enter itself, and pythonw has no stdin
        threading.Thread(target=watch_keys, daemon=True).start()

    if text_mode:
        announce("Excephalon is here. Type to talk; say 'quit' or 'goodbye Excephalon' to end.")
    elif gui:
        announce("Excephalon is here. Turn the mic on when you want to talk, or say 'hey Excephalon'.")
        announce("That same button stops it while it's speaking. Close the window to quit.")
    else:
        announce("Excephalon is here. Speak, and say 'over' when you finish each turn.")
        announce("Press Enter to cut it off. To quit, say 'goodbye Excephalon over' (or Ctrl-C).")
    if muted:
        announce("(muted: replies are shown, not spoken)")
    announce()

    had_conversation = []
    farewelled = []

    def show(turn):  # the terminal transcript itself is the Console's job now; this is just bookkeeping
        had_conversation.append(True)
        if turn.farewell:
            farewelled.append(True)  # the goodbye was already said this turn; don't repeat it below

    # A beat to read a reply before the mic reopens, but not in text mode (they set their own pace there).
    read_pause = 0.0 if text_mode else 1.2
    # Keep the same lines the terminal shows, timestamped, so a session that went wrong can be read
    # back afterwards instead of the user having to copy their scrollback out by hand.
    _session_name = f"session-{datetime.now():%Y%m%d-%H%M%S}"
    session_record = Transcript(TRANSCRIPTS / f"{_session_name}.log")
    # The same conversation as MESSAGES, written the moment each one is said - what the window
    # replays, so a reload cannot disagree with what he watched happen. The .log beside it stays
    # what it always was: prose for people.
    session_messages = MessageLog(TRANSCRIPTS / f"{_session_name}.jsonl")
    # The memory inbox's nudge: in genuine downtime - fleet idle, conversation quiet - one
    # remembered fact is raised for his verdict, worked toward inbox zero (see excephalon.review).
    from excephalon.memory import DEFAULT_LEARNED_PATH as LEARNED
    from excephalon.review import MemoryNudger

    def _memories():
        try:
            lines = LEARNED.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [line.lstrip("-* ").strip() for line in lines
                if line.strip() and not line.lstrip().startswith("#")]

    MemoryNudger(
        agent_events, memories=_memories,
        fleet_idle=lambda: not any(state in ("starting", "working")
                                   for _, state, _ in desk.roster()),
        # The transcript's own mtime is when the conversation last moved - the artifact, not a
        # parallel clock someone has to remember to update.
        quiet_for=lambda: max(0.0, time.time() - session_record.path.stat().st_mtime),
    ).start(stop)
    announce(f"(this conversation is being written to {session_record.path})\n")
    if gui:
        # The window renders a conversation, so it takes the Console's who-said-what seam rather
        # than its terminal lines - and no "(listening… say 'over')" notice, which is meaningless
        # next to a mic button and a level meter.
        console = Console(voice=True, record=session_record.write, listening_notice="",
                          echo=lambda t: None,
                          overwrite=lambda t: feed.push("overwrite", t),
                          messages=lambda role, text: (session_messages.keep(role, text),
                                                       feed.push("message", (role, text))))
    else:
        console = Console(voice=not text_mode, record=session_record.write,
                          messages=session_messages.keep)

    # The session's first line, handed to the conversation rather than spoken beside it: spoken
    # here, it was one of TWO messages he got thirteen seconds apart on opening the app - a
    # welcome asking about one update, then the app's own list offering all three ("it should
    # only have sent me one"). The loop says it through the one mouth everything else uses, with
    # the waiting list on the back of it when there is one.
    greeting = ("" if text_mode or muted
                else _greeting(brain, booted_at, previous_boot, was_seen,
                               waiting=sorted(name for name in outbox.owed_about() if name)))

    def converse():
        try:
            Conversation(
                stt, brain, tts, outbox=outbox, interrupt=barge_in,
                console=console, read_pause=read_pause, timings=timings, opening=greeting,
                # A dead sign-in answers with the door already open: a terminal at the claude
                # prompt, not just the steps to get one.
                sign_in_helper=open_sign_in,
                # The live truth about the fleet AND his Enhancements list, re-read from the file
                # every turn: the boot persona's copy of the list went stale and got DISBELIEVED
                # ("I can't see the Enhancements list"), while nothing carried in these per-turn
                # notes has ever faded or been denied.
                # Anything of his that has changed since the brain was told, whatever part of
                # his context it lives in - the guard against the whole stale-snapshot category.
                standing=StandingWatch(_persona, on_change=brain.refresh_persona).moved,
                briefing=lambda: (
                    f"{desk.digest()}\nFresh agents start on {desk.running_on()}."
                    "\n\nHis Enhancements list - the OPEN items, live from the file this turn. "
                    "You CAN see this list: it is right here, always current, and it is the same "
                    "list his window's tab shows. You file to it with file_improvement, rewrite "
                    "an item by number with revise_enhancement, tick one DONE with "
                    "check_off_enhancement the moment its ask is finished, and agents you start "
                    "on an item tick it off themselves when their work lands:\n"
                    + (open_enhancements() or "(nothing open)")
                    + _live_projects()
                    + _live_instructions()
                ),
            ).run(should_continue=lambda: not stop.is_set(), on_turn=show)
        except KeyboardInterrupt:
            stop.set()
        finally:
            inbox_watcher.stop()
            desk.close()
            if not farewelled:  # one goodbye: a spoken farewell already said it; only cover Ctrl-C/stop here
                console.reply("Be seeing you.")  # a spoken line renders as its bubble, and is recorded
                if not text_mode and not muted:
                    try:
                        tts.speak("Be seeing you.")
                    except Exception:
                        pass
            if had_conversation:  # remember what it learned - bounded so a slow model can't hang the exit
                try:
                    append_learned(consolidate(brain))
                except Exception:
                    pass
            for closer in (
                brain.close,
                foreman.close,
                errands.close,
                mic.close if mic is not None else None,
                recorder.close if recorder is not None else None,
                hearing.close if hearing is not None else None,
            ):
                try:
                    if closer is not None:
                        closer()
                except Exception:
                    pass

    converse()


def main(argv=None):
    # Give every enhancement a stable #id before anything reads the profile: the brain composes its
    # persona from this file and the window shows the same numbers, so "do twelve" points them both
    # at one task. Idempotent - it writes only the first time, when something is still unnumbered.
    number_enhancements(DEFAULT_PROFILE_PATH)
    argv = sys.argv[1:] if argv is None else argv
    text_mode = "--text" in argv
    muted = "--mute" in argv
    timings = "--no-timings" not in argv  # per-turn think/speak readout is on unless they opt out
    gui = "--gui" in argv and not text_mode  # a window instead of the terminal (voice runs only)

    # A hard fault should leave a trail: every thread's python stack lands in runtime/faults.log
    # the moment the interpreter dies of a real crash, so a vanished window comes with evidence.
    try:
        import faulthandler

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        faulthandler.enable(open(RUNTIME_DIR / "faults.log", "a", encoding="utf-8"))
    except Exception:
        pass  # the recorder must never be the thing that breaks the start

    # In a windowed run every startup line goes to the window's feed INSTEAD of stdout - launched
    # from the Start Menu there is no terminal at all, and launched from a command line they don't
    # want the window's contents spat out there too.
    feed = TranscriptFeed() if gui else None

    def announce(line=""):
        if feed is not None:
            feed.push("line", line)
        else:
            print(line, flush=True)

    # Shutdown is a spoken/typed farewell ("goodbye entity", "quit") or Ctrl-C. Enter is NOT quit -
    # it's the barge-in: press it to cut off whatever Excephalon is saying (they had a 15-minute
    # ramble they couldn't stop). Each Enter sets `barge_in`; the Conversation clears it per turn.
    stop = threading.Event()
    barge_in = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    running = dict(announce=announce, feed=feed, gui=gui, text_mode=text_mode, muted=muted,
                   timings=timings, stop=stop, barge_in=barge_in)
    if not gui:
        _session(**running)
        leave_process()  # same daemon threads, same finalization segfault - see the gui path's exit

    # Windowed: the window opens FIRST and the whole session runs on a worker, so a click puts
    # something on screen at once instead of after a 2.4 GB model has loaded. Closing the window
    # asks the loop to stop (the mic checks `stop` every frame), and once the worker has wound all
    # the way down - goodbye said, memory consolidated - `done` lets the window end itself.
    import anyio

    from excephalon.chord import ChordListener, SubmitChord, foreground_is_ours
    from excephalon.desktop import open_window
    from excephalon.memory import (
        DEFAULT_LEARNED_PATH,
        DEFAULT_PERSONA_ADDITIONS_PATH,
        DEFAULT_TRANSLATIONS_PATH,
    )
    from excephalon.no_console import silence_child_consoles
    from excephalon.transcript import past_messages
    from excephalon.mirror import Mirror
    from excephalon.web import create_app

    # With no console of its own to lend them, Windows gives each console child a new window: the
    # Claude CLI the brain runs was turning up as a second window on their desktop.
    silence_child_consoles(anyio)

    # Everything recorded so far. This session's own record does not exist yet - it is
    # opened with the session, further down - and its messages arrive live on the feed.
    for op, payload in past_messages(TRANSCRIPTS):
        feed.push(op, payload)  # yesterday's sessions, above the divider - no more amnesia
    feed.push("line", "───────  this session  ───────")

    mirror = Mirror(feed)
    # The window is up before the model has loaded, so the mic does not exist yet; whatever is
    # pressed in that gap is dropped rather than raising at a page that cannot know.
    mic = {}
    # What the session hands back for the page to drive live - today the transcriber's retune,
    # so a saved translation is in force for the very next chunk.
    hooks = running["hooks"] = {}

    def _rename_agent(name, to):
        renamed = hooks.get("rename_agent", lambda *_: False)(name, to)
        return safe_name(to) if renamed else ""

    booted_from = head_commit(REPO)
    from excephalon.memory import reconcile_lexicon
    from excephalon.vocabulary import scan_terms

    scanned_now = scan_terms(_project_roots()) | {"Excephalon"}

    def save_lexicon_rows(kept):
        reconcile_lexicon(kept, scanned_now)
        hooks.get("retune", lambda **_: None)(
            terms=sorted(scanned_now | set(lexicon_terms(load_lexicon()))))

    # The window, as the page may drive it: the styled close dialog's Close, and the Restart
    # button. Filled in once the window exists (hand_controls below); clicks before then no-op.
    window = {}

    def ask_quit():
        control = window.get("controls")
        if control is not None:
            control.quit()

    def ask_restart():
        control = window.get("controls")
        if control is not None:
            # The relaunch is a DETACHED helper spawned now, at the request - not the old
            # process's last act. It waits for this pid to die, however that goes (the one time
            # it mattered, teardown misbehaved, the spawn line never ran, and he reopened by
            # hand), then starts a fresh app on the current code. How a helper is detached, and
            # which interpreter brings the app back, are the desk's business and live with it.
            import os

            from excephalon.relauncher import spawn

            spawn(os.getpid(), REPO)
            control.restart()

    app = create_app(
        mirror.model, mirror=mirror,
        on_submit=lambda text: mic.get("submit", lambda _: None)(text),
        on_stop=barge_in.set,
        on_mic=lambda recording: mic.get("set_recording", lambda _: None)(recording),
        on_auto_listen=lambda on: mic.get("set_auto_listen", lambda _: None)(on),
        profile_path=DEFAULT_PROFILE_PATH, learned_path=DEFAULT_LEARNED_PATH,
        translations_path=DEFAULT_TRANSLATIONS_PATH,
        persona_additions_path=DEFAULT_PERSONA_ADDITIONS_PATH,
        # The same list the mic is about to be built with, so the page says what is in force
        # rather than what could be.
        terms=_vocab_terms(),
        agent_logs_dir=AGENT_LOGS,
        # The fleet's survival record, so a task an agent is on shows an indicator linking to its
        # log, and the log links back to the task - the desk keeps this current as agents come and
        # go (agents.json), and the two tabs read it to reach each other.
        agent_state_path=AGENT_STATE,
        on_quit=ask_quit, on_restart=ask_restart,
        # The page's rename: the desk does it, and the name it settled on comes back so the
        # heading shows what was actually taken rather than what was typed.
        on_rename=_rename_agent,
        # The (paraphone) rows: the live lexicon joins the folder-scanned terms on the page, his
        # edits reconcile back into the lexicon alone, and the running ear retunes at once.
        scanned_terms=sorted(scanned_now),
        lexicon_reader=lambda: lexicon_terms(load_lexicon()),
        on_lexicon_saved=save_lexicon_rows,
        # The Restart button shows only when there is genuinely something to restart INTO: the
        # checkout on disk has moved past the commit this process booted from.
        upgrade_ready=lambda: head_commit(REPO) not in ("", booted_from),
        # His Config edits take effect NOW, not at the next launch: new translations are swapped
        # into the running transcriber the moment they save.
        on_translations_saved=lambda own: hooks.get("retune", lambda **_: None)(translations=own),
    )
    # The modifier beside the spacebar + Enter submits the draft. On Windows that is Win+Enter,
    # which reaches no window on that machine, so it arrives by keyboard hook instead - and only
    # while Excephalon is in front. Held in a name for the app's lifetime, and asked whether it
    # took: a hook that fails to install is the one place this can die in silence, so it says so
    # on screen rather than the chord just quietly doing nothing. The Mac needs none of it - Cmd
    # reaches the page like any other modifier, and window.js binds the same gesture there - so
    # there is no hook to install and, more to the point, no failure to announce about one.
    if machine.WINDOWS:
        chord = ChordListener(SubmitChord(submit=lambda: feed.push("submit", ""),
                                          focused=foreground_is_ours))
        if not chord.start():
            feed.push("line",
                      "(Win+Enter to submit is unavailable - the keyboard hook didn't install)")

    def worker():
        try:
            _session(attach=lambda d: mic.update(submit=d.submit,
                                                 set_recording=d.set_recording,
                                                 set_auto_listen=d.set_auto_listen), **running)
        except Exception as exc:
            # The window is up by the time this runs, so a failure here HAS somewhere to be said -
            # and it has to be said, as the app's own aside rather than in Excephalon's voice. A
            # session that dies on the way in otherwise leaves a live-looking window that simply
            # never speaks, which reads as an app hanging rather than an app broken.
            where = note_failure()
            announce(f"(Excephalon's session failed to start - {type(exc).__name__}: {exc})")
            if where is not None:
                announce(f"(the whole traceback is in {where})")
        finally:
            stop.set()

    session = threading.Thread(target=worker, daemon=True)
    session.start()
    controls = open_window(
        app, icon=str(Path(__file__).resolve().parents[2] / "assets" / "excephalon.ico"),
        # Reopen where it was closed: "Entity window should remember where it was on the screen".
        position_path=RUNTIME_DIR / "window-position.json",
        hand_controls=lambda given: window.update(controls=given),
    )
    stop.set()  # the window was closed: ask the loop to wind down, as closing the Tk one did
    # Wait the wind-down out on EVERY close, not only a restart: the process used to exit while
    # the worker was still mid-goodbye, tearing native audio down under a live thread. The
    # restart's relaunch needs nothing here - a detached helper (excephalon.relauncher) is already
    # waiting for this pid to die, however the wind-down goes.
    session.join(timeout=30)
    # The wind-down is done and everything durable is on disk; interpreter finalization would
    # only tear native audio down under the daemon threads that are alive by design - which was
    # "Python quit unexpectedly" on every close (see shutdown.leave_process).
    leave_process()


if __name__ == "__main__":
    main()
