"""The cheapest robot voice each desk already has: Windows System.Speech through PowerShell, and
macOS's own `say`.

No pip dependencies on either. The text to speak is never interpolated into a command string - it
goes through the environment on Windows and down stdin on the Mac - so there is no quoting,
escaping, or injection surface regardless of what the brain says, and the line does not turn up in
a process list either. Speaking is interruptible: pass an `interrupt` Event and the voice is killed
the moment it fires, so the user can cut off a runaway reply instead of sitting through it.

This is the fallback, reached only when the neural voice cannot be had - which is exactly when it
has to work, so it knows both desks rather than one.
"""

import os
import shutil
import subprocess

from excephalon import machine
from excephalon.voice import UNSAID, Receipt

_SPEAK_SCRIPT = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.Rate = {rate}; "
    "$s.Speak($env:ENTITY_TTS_TEXT)"
)

# `rate` is System.Speech's scale, roughly -10 to 10 around an ordinary speaking pace. `say` wants
# words per minute instead, so the same number has to mean the same briskness on both desks: 175
# is its own default, and a step of System.Speech's is about twenty words of it.
_SAY_BASE_WPM, _SAY_WPM_PER_STEP = 175, 20


class TTSError(RuntimeError):
    pass


def command_for(text, rate=0):
    """How this desk says one line: (argv, what to add to the environment, what to feed stdin).

    Raises TTSError when the machine has no voice program at all, which is a thing to SAY rather
    than to discover by silence."""
    if machine.WINDOWS:
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if exe is None:
            raise TTSError("could not find PowerShell to drive System.Speech")
        return ([exe, "-NoProfile", "-NonInteractive", "-Command", _SPEAK_SCRIPT.format(rate=rate)],
                {"ENTITY_TTS_TEXT": text}, None)
    exe = shutil.which("say")
    if exe is None:
        raise TTSError("could not find `say` to speak with")
    words_per_minute = _SAY_BASE_WPM + _SAY_WPM_PER_STEP * rate
    return ([exe, "-r", str(words_per_minute), "-f", "-"], {}, text)


def _default_run(rate, text, interrupt=None):
    argv, extra_env, feed = command_for(text, rate)
    proc = subprocess.Popen(
        argv,
        env={**os.environ, **extra_env},
        stdin=subprocess.PIPE if feed is not None else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        # Launched from a window (or Git Bash) there's no console to inherit, so every utterance
        # was popping its own PowerShell window onto their monitors. The voice needs no window.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if machine.WINDOWS else 0,
    )
    if feed is not None:
        proc.stdin.write(feed)
        proc.stdin.close()
    while True:
        try:
            proc.wait(timeout=0.05)  # check often enough to fall silent within a breath of a cut-in
            break
        except subprocess.TimeoutExpired:
            if interrupt is not None and interrupt.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return  # they cut it off - not a failure
    if proc.returncode != 0:
        said = " ".join(argv[:1])
        raise TTSError(f"{said} failed: {(proc.stderr.read() if proc.stderr else '').strip()}")


class NullTTS:
    """Speaks nothing - for muted / text-only runs.

    It still RECEIPTS the line, because in those runs the screen is the mouth: the reply is
    printed and the user reads it, so what it carried has been delivered. Receipting nothing here
    would leave every piece of news owed forever in a text-mode run."""

    def speak(self, text, *, interrupt=None):
        said = str(text).strip()
        return Receipt(began=bool(said), said=said)


class SystemTTS:
    def __init__(self, *, rate=0, run=_default_run):
        self._rate = rate
        self._run = run

    def speak(self, text, *, interrupt=None):
        said = str(text).strip()
        if not said:
            return UNSAID
        self._run(self._rate, said, interrupt)
        fired = getattr(interrupt, "is_set", None)
        return Receipt(began=True, said=said, cut=bool(fired and fired()))
