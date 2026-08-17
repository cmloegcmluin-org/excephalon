"""The door every click comes through, and the one place a failed launch can still speak.

The Start Menu entry, the taskbar pin and Excephalon.bat all run this under `pythonw.exe`, which
has no console at all: a traceback on the way up goes nowhere, so the app just does not appear.
Clicking and getting nothing - no window, no message, no log - has now happened three times, most
recently for a week after the package was renamed, while every shortcut on the machine still said
`-m entity`. Three things here are what make that not repeatable.

It is a FILE, not a module name. A shortcut installed once holds whatever it was given forever,
and nothing in this repo can reach into a .lnk to correct a rename - but a path into the checkout
goes on meaning the same thing whatever the package is called.

It puts `src` on the path itself, so a checkout whose venv lost its editable install still starts
instead of dying on the first import.

And whatever goes wrong, it SAYS so: the traceback is appended to runtime/launch-failure.log and
its first line goes up in a box he cannot miss. Nothing above `run` imports the package - the
failure this exists to report is the package failing to import - and every part of the reporting
is wrapped, because a reporter that raises is just the silence again.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent
FAILURE_LOG = REPO / "runtime" / "launch-failure.log"
TITLE = "Excephalon couldn't start"


def _box_on_windows(title, body):
    import ctypes

    # MB_ICONERROR | MB_SETFOREGROUND: an error, and in front of whatever he is looking at
    # rather than blinking somewhere in the taskbar behind it.
    ctypes.windll.user32.MessageBoxW(None, body, title, 0x10 | 0x10000)


def _box_on_mac(title, body):
    import json
    import subprocess

    # json.dumps writes exactly the quoting and escaping AppleScript wants from a string literal.
    subprocess.run(
        ["osascript", "-e", f"display dialog {json.dumps(body)} with title {json.dumps(title)}"
                            ' with icon stop buttons {"OK"}'],
        check=False,
    )


def show(title, body):
    """Put it in front of him. A dialog rather than a print, because there is no console to print
    to and no window to write into - the app never got far enough to have either."""
    try:
        if sys.platform.startswith("win"):
            _box_on_windows(title, body)
        elif sys.platform == "darwin":
            _box_on_mac(title, body)
        else:
            print(f"{title}\n\n{body}", file=sys.stderr)
    except Exception:
        pass  # the reporter must never become the thing that fails


def write_failure(report, log=FAILURE_LOG, now=None):
    """Append the whole traceback where it can be read afterwards, and answer with where it went
    (or None, if even that could not be written - then the box is all there is)."""
    try:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        stamp = now or datetime.now()
        with open(log, "a", encoding="utf-8") as out:
            out.write(f"\n===== {stamp:%Y-%m-%d %H:%M:%S} =====\n{report}")
        return log
    except OSError:
        return None


def failure_message(exc, log=None):
    """What the box says: the fault in the exception's own words first, since that one line is
    usually the whole diagnosis, then where to find the rest of it."""
    said = [f"{type(exc).__name__}: {exc}"]
    if isinstance(exc, ImportError):
        said.append("The code and the interpreter running it have come apart - usually a "
                    "shortcut still naming something the code no longer has, or a virtualenv "
                    "that needs `pip install -e .` again.")
    said.append(f"The full details are in {log}" if log is not None
                else "It could not be written to a log, so this box is all there is.")
    return "\n\n".join(said)


def name_this_process():
    """Leave the shortcut an interpreter that says "Excephalon" next time.

    Windows takes what it shows about a process from the file it was started
    from - the Details tab's name, the Processes tab's description, the icon
    beside it - so a plain pythonw.exe puts Excephalon in the task list as one
    more anonymous "Python", beside every other Python app on the machine. That
    costs nothing until something strands a process, and then the task list is
    the only way back and cannot say which row is safe to end.

    This process cannot be named on the way in: writing the copy takes the very
    interpreter being named. So each run makes it for the run after and the
    shortcut points at it once it exists.

    Wrapped like everything else above it: naming a process must never become
    the thing that stops the app from starting.
    """
    try:
        from app_support.process_identity import ProcessNamer

        ProcessNamer("Excephalon", icon=REPO / "assets" / "excephalon.ico").prepare_launcher(
            "Excephalon")
    except Exception:
        pass


def run(argv=(), *, enter=None, log=FAILURE_LOG, tell=show):
    """Start the app in its window and answer with an exit code. `enter` and `tell` are injected
    so the reporting can be exercised without launching anything or putting a box on a screen."""
    if enter is None:
        sys.path.insert(0, str(REPO / "src"))  # a checkout with no editable install still runs
        name_this_process()
    try:
        if enter is None:
            from excephalon.__main__ import main as enter
        # This file IS the windowed launcher - a double-click means the window, whether or not
        # anything thought to pass the flag. Extra arguments still ride along (--mute, --text).
        enter(["--gui", *argv])
    except SystemExit as leaving:
        return leaving.code or 0
    except BaseException as exc:  # noqa: BLE001 - anything at all, or it dies in silence
        tell(TITLE, failure_message(exc, write_failure(traceback.format_exc(), log)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
