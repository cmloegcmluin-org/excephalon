"""The restart's second half, as its own process: wait for the old app to die, start a new one.

The Restart button's relaunch used to be the OLD process's last act - which meant it only
happened if the wind-down reached that line, and the one time it mattered most (teardown
misbehaving, the very reason he pressed Restart) the app closed and nothing came back; he had
to reopen it by hand. Spawned as a detached helper at the moment restart is REQUESTED, this
outlives whatever the old process does: it waits for that pid to disappear - however the app
goes, clean or crashed - then launches a fresh one on the current code.

Both halves - how a process is watched, and how one is started so it survives its parent - are
the desk's business rather than the button's, so both live here and the window just asks.

Run as: pythonw -m excephalon.relauncher <old-pid> <repo-root>   (the Mac's own python, on a Mac)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from excephalon import machine


def app_python(repo):
    """The interpreter a fresh app is started with. Windows has a windowless one and puts it in
    Scripts; every other desk has bin/python and no console to hide it from."""
    venv = Path(repo) / ".venv"
    return venv / "Scripts" / "pythonw.exe" if machine.WINDOWS else venv / "bin" / "python"


def _detached():
    """How a process is started so it outlives the one starting it. Windows wants the flags said
    out loud; POSIX wants a session of its own, so whatever closes over the parent - a terminal,
    a launcher - cannot take the child with it."""
    if machine.WINDOWS:
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def spawn(pid, repo, *, start=subprocess.Popen):
    """Set the helper going: from here on the relaunch is out of the old process's hands."""
    start([str(app_python(repo)), "-m", "excephalon.relauncher", str(pid), str(repo)],
          cwd=str(repo), close_fds=True, **_detached())


def _alive_via_kernel32(pid):
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _signalable(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # not this helper's to signal, but it is plainly there
    return True


def watcher_for(pid, *, parent=os.getppid):
    """How to ask whether the old app is still up, on this desk.

    On POSIX that app is this helper's own parent, and a dead parent reparents its children away
    at once - even before anyone has reaped it. That reparenting is the honest signal: sending
    the null signal keeps SUCCEEDING against an unreaped zombie, so the relaunch would sit out
    the whole timeout instead of coming back the moment the window went."""
    if machine.WINDOWS:
        return lambda: _alive_via_kernel32(pid)
    if parent() == pid:
        return lambda: parent() == pid
    return lambda: _signalable(pid)


MAC_BUNDLE = Path("/Applications/Excephalon.app")


def _installed_bundle():
    """The Mac app bundle to relaunch through, or None off-Mac or before it is installed."""
    return str(MAC_BUNDLE) if machine.MACOS and MAC_BUNDLE.exists() else None


def wait_then_launch(pid, repo, *, timeout=120.0, poll=0.5, alive=None, start=subprocess.Popen,
                     bundled=_installed_bundle):
    alive = watcher_for(pid) if alive is None else alive
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and alive():
        time.sleep(poll)
    # Timed out or died - either way the old window is not coming back; bring up the new one.
    # On a Mac that means the BUNDLE when there is one: relaunching the venv python directly
    # brought the app back as "Python" with the interpreter's own icon - a stranger in the Dock
    # beside the real pinned tile - because the bundle is the app's identity there.
    bundle = bundled()
    if bundle is not None:
        start(["open", "-n", bundle], close_fds=True)
        return
    # Through launch.pyw, the same door a click comes in by - detached under pythonw there is no
    # console here either, so a relaunch that fails has to be one that can say why. "Relaunch to
    # upgrade didn't work this time, though actually I can't even open Excephalon" was this path
    # coming back with nothing and no reason.
    start([str(app_python(repo)), str(Path(repo) / "launch.pyw")], cwd=str(repo), close_fds=True,
          **_detached())


if __name__ == "__main__":
    wait_then_launch(int(sys.argv[1]), sys.argv[2])
