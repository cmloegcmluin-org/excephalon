"""The Restart button's second half. Untested until the app gained a second desk, and every one
of these was red against the version that only knew the first: a `.venv\\Scripts\\pythonw.exe`
that is not there, `ctypes.windll` that does not exist, and creation flags that are a ValueError
rather than a nicety. A restart that cannot come back is the failure this whole helper exists to
prevent, so it is worth a test on the desk it runs on."""

import os
from pathlib import Path

import pytest

from excephalon import machine, relauncher


def test_a_fresh_app_starts_on_this_desk_s_own_interpreter(tmp_path):
    # The one thing the helper cannot get wrong: the path it relaunches through. Windows keeps a
    # windowless interpreter in Scripts; every other desk has bin/python.
    where = relauncher.app_python(tmp_path)

    expected = ("Scripts", "pythonw.exe") if machine.WINDOWS else ("bin", "python")
    assert where == Path(tmp_path).joinpath(".venv", *expected)


def test_the_helper_outlives_the_app_that_asked_for_it(tmp_path):
    # Detached is the whole point - the app is about to die, and a child that goes with it is no
    # relaunch at all. Windows says so in flags; POSIX says so with a session of its own.
    started = []

    relauncher.spawn(4321, tmp_path, start=lambda argv, **kw: started.append((argv, kw)))

    [(argv, kwargs)] = started
    assert argv == [str(relauncher.app_python(tmp_path)), "-m", "excephalon.relauncher",
                    "4321", str(tmp_path)]
    assert kwargs["cwd"] == str(tmp_path)
    assert ("creationflags" in kwargs) is machine.WINDOWS
    assert kwargs.get("start_new_session", False) is not machine.WINDOWS


def test_the_new_app_comes_up_once_the_old_one_is_gone(tmp_path):
    started, answers = [], [True, True, False]

    relauncher.wait_then_launch(4321, tmp_path, poll=0, alive=lambda: answers.pop(0),
                                start=lambda argv, **kw: started.append(argv),
                                bundled=lambda: None)  # the machine running the suite may have one

    assert answers == []  # it waited for every "still here" before giving up on it
    # Through launch.pyw, not `-m excephalon`: detached under pythonw there is no console, so the
    # one door that can report its own failure is the one a relaunch has to come back through.
    assert started == [[str(relauncher.app_python(tmp_path)), str(tmp_path / "launch.pyw")]]


def test_an_app_that_never_dies_still_gets_a_successor(tmp_path):
    # Timed out or died, the old window is not coming back either way, and a Restart that
    # silently declines to restart is the very hand-reopening this exists to spare him.
    started = []

    relauncher.wait_then_launch(4321, tmp_path, timeout=0, poll=0, alive=lambda: True,
                                start=lambda argv, **kw: started.append(argv),
                                bundled=lambda: None)

    assert len(started) == 1


@pytest.mark.skipif(machine.WINDOWS, reason="the reparenting signal is POSIX's")
def test_a_dead_parent_is_noticed_by_being_reparented_rather_than_by_a_signal():
    # A zombie answers the null signal exactly as a live process does, so a helper asking that way
    # would sit out its whole timeout after the window had already gone. Its parent dying reparents
    # it at once, which is the signal that is actually true.
    watch = relauncher.watcher_for(4321, parent=lambda: 4321)
    assert watch() is True

    reparented = iter([4321, 1])
    watch = relauncher.watcher_for(4321, parent=lambda: next(reparented))
    assert watch() is False


@pytest.mark.skipif(machine.WINDOWS, reason="POSIX's own answer for a process that is not ours")
def test_a_process_that_is_not_this_helper_s_parent_is_asked_directly():
    assert relauncher.watcher_for(os.getpid(), parent=lambda: 1)() is True
    # A pid nothing could hold: 0 is the whole process group, and no ordinary pid is negative.
    assert relauncher.watcher_for(2 ** 31 - 1, parent=lambda: 1)() is False


@pytest.mark.skipif(machine.WINDOWS, reason="the bundle is the Mac's launcher")
def test_a_mac_relaunch_goes_through_the_app_bundle_when_there_is_one(tmp_path):
    # The relaunch used to exec the venv python directly, and the app came back as "Python" with
    # the interpreter's own icon - a stranger in the Dock, beside the real pinned tile. The
    # bundle IS the app's identity on a Mac, so a restart that has one goes through it.
    started = []

    relauncher.wait_then_launch(4321, tmp_path, timeout=0, poll=0, alive=lambda: False,
                                start=lambda argv, **kw: started.append(argv),
                                bundled=lambda: "/Applications/Excephalon.app")

    assert started == [["open", "-n", "/Applications/Excephalon.app"]]


@pytest.mark.skipif(machine.WINDOWS, reason="the bundle is the Mac's launcher")
def test_a_mac_without_the_bundle_still_comes_back(tmp_path):
    started = []

    relauncher.wait_then_launch(4321, tmp_path, timeout=0, poll=0, alive=lambda: False,
                                start=lambda argv, **kw: started.append(argv),
                                bundled=lambda: None)

    assert started == [[str(relauncher.app_python(tmp_path)), str(tmp_path / "launch.pyw")]]
