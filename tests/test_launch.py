"""The launcher, whose entire job is that a failed launch is never silent.

He pulled a week of work, double-clicked, and nothing happened: no window, no message, no log.
The shortcut still said `-m entity` after the package was renamed, and `pythonw.exe` has no
console for a ModuleNotFoundError to land in - so the app died in complete silence and the only
way to find out why was to go and read a .lnk. Every test here is about the two halves of that:
a launcher that names a FILE rather than a module, and a failure that puts its own reason up.
"""

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "launch.pyw"


def _launcher():
    """Load launch.pyw by path - it is not a module and must never become one: the failure it
    exists to report is the package failing to import."""
    spec = importlib.util.spec_from_file_location("excephalon_launcher", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def launch():
    return _launcher()


def test_a_launch_that_fails_puts_the_reason_on_screen(launch, tmp_path):
    told = []

    def boom(_argv):
        raise ModuleNotFoundError("No module named 'entity'")

    code = launch.run(enter=boom, log=tmp_path / "launch-failure.log",
                      tell=lambda title, body: told.append((title, body)))

    assert code == 1
    [(title, body)] = told
    assert "Excephalon" in title
    # The exception's own words, because that one line is usually the whole diagnosis.
    assert "No module named 'entity'" in body


def test_the_whole_traceback_is_written_down_where_it_can_be_read(launch, tmp_path):
    log = tmp_path / "launch-failure.log"

    launch.run(enter=lambda _argv: 1 / 0, log=log, tell=lambda *_: None)

    written = log.read_text(encoding="utf-8")
    assert "ZeroDivisionError" in written
    assert "Traceback" in written  # the frames, not just the last line the box shows


def test_a_second_failure_joins_the_first_rather_than_replacing_it(launch, tmp_path):
    # Two clicks, two failures: the log is the record of a bad evening, not of the last click.
    log = tmp_path / "launch-failure.log"

    launch.run(enter=lambda _argv: 1 / 0, log=log, tell=lambda *_: None)
    launch.run(enter=lambda _argv: [][1], log=log, tell=lambda *_: None)

    written = log.read_text(encoding="utf-8")
    assert "ZeroDivisionError" in written and "IndexError" in written


def test_the_box_says_where_the_rest_of_it_went(launch, tmp_path):
    told = []
    log = tmp_path / "launch-failure.log"

    launch.run(enter=lambda _argv: 1 / 0, log=log, tell=lambda title, body: told.append(body))

    assert str(log) in told[0]


def test_a_log_that_cannot_be_written_still_leaves_him_a_box(launch, tmp_path):
    # The reporting must not depend on the disk cooperating - the box is the part he actually sees.
    unwritable = tmp_path / "a-file" / "launch-failure.log"
    unwritable.parent.write_text("not a directory", encoding="utf-8")
    told = []

    launch.run(enter=lambda _argv: 1 / 0, log=unwritable, tell=lambda title, body: told.append(body))

    assert "ZeroDivisionError" in told[0]
    assert "all there is" in told[0]  # and it says the log isn't there rather than naming one


def test_an_import_failure_says_what_kind_of_thing_has_come_apart(launch, tmp_path):
    # His actual failure. "No module named 'entity'" alone does not tell him a shortcut is stale.
    told = []

    def renamed_out_from_under_it(_argv):
        raise ModuleNotFoundError("No module named 'entity'")

    launch.run(enter=renamed_out_from_under_it, log=tmp_path / "log",
               tell=lambda title, body: told.append(body))

    assert "pip install -e ." in told[0]


def test_a_launch_that_works_says_nothing_at_all(launch, tmp_path):
    told = []
    log = tmp_path / "launch-failure.log"

    code = launch.run(enter=lambda _argv: None, log=log, tell=lambda *said: told.append(said))

    assert code == 0
    assert told == []
    assert not log.exists()


def test_a_double_click_means_the_window(launch, tmp_path):
    # This file IS the windowed launcher: an installed shortcut that forgot the flag would
    # otherwise start the terminal app under pythonw - which is a process with no console, no
    # window, and no way to be talked to. Anything else passed still rides along.
    asked = []

    launch.run(["--mute"], enter=asked.append, log=tmp_path / "log", tell=lambda *_: None)

    assert asked == [["--gui", "--mute"]]


def test_quitting_normally_is_not_a_failure(launch, tmp_path):
    log = tmp_path / "launch-failure.log"

    def leaves(_argv):
        raise SystemExit(0)

    assert launch.run(enter=leaves, log=log, tell=lambda *_: None) == 0
    assert not log.exists()


def test_a_session_that_dies_behind_an_open_window_writes_to_the_same_log(tmp_path):
    """Past the launcher, the window is already up and a failure has somewhere to be SAID - but
    it still has to be written, and to one file, so "it didn't start" has a single place to look
    whichever half of the startup gave way."""
    from excephalon.__main__ import note_failure

    log = note_failure("Traceback (most recent call last):\n  ImportError: nope",
                       log=tmp_path / "launch-failure.log")

    assert "ImportError: nope" in log.read_text(encoding="utf-8")


def test_a_log_the_app_cannot_write_is_not_a_second_failure(tmp_path):
    from excephalon.__main__ import note_failure

    blocked = tmp_path / "a-file" / "launch-failure.log"
    blocked.parent.write_text("not a directory", encoding="utf-8")

    assert note_failure("whatever", log=blocked) is None


LAUNCHERS = (
    "Excephalon.bat",
    "tools/install-start-menu.ps1",
    "tools/install-app-bundle.sh",
    "src/excephalon/relauncher.py",
)


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_every_launcher_points_at_a_file_that_is_actually_there(launcher):
    """A .lnk installed once holds what it was given forever, and nothing in this repo can reach
    in and correct it - so what it is given must be a path rather than a module name, and that
    path must exist. This is the check that a rename here can never again leave every shortcut on
    the machine pointing at nothing."""
    text = (REPO / launcher).read_text(encoding="utf-8")
    # The basename, however each of the four syntaxes spells the folder in front of it - a batch
    # file's %~dp0 runs straight into the name with no separator at all.
    named = set(re.findall(r"(?:^|%~dp0|[\\/\"' ])([\w.-]+\.pyw)", text, re.MULTILINE))

    assert named, f"{launcher} launches nothing by file - a module name cannot be kept true"
    for name in named:
        assert (REPO / name).exists(), f"{launcher} launches {name}, which is not in the repo"


@pytest.mark.parametrize("door", ("Learn my voice", "Connect Google"))
def test_every_double_click_door_exists_for_both_desks(door):
    """A door built only for the Mac is no door at all on the desk he actually sits at. Google's
    sign-in had `Connect Google.command` and nothing else, so when the sign-in expired the only
    way back was a command line he does not work from - and the app told him to use one."""
    assert (REPO / f"{door}.bat").exists(), f"{door} has no Windows door"
    assert (REPO / f"{door}.command").exists(), f"{door} has no Mac door"


def test_the_start_menu_shortcut_is_installed_pointing_at_the_launcher():
    """The one file whose output becomes a .lnk on his machine. Whatever it writes there is what
    a click will go on asking for, long after the code has moved on."""
    text = (REPO / "tools/install-start-menu.ps1").read_text(encoding="utf-8")

    assert re.search(r"\$launcher\s*=\s*Join-Path\s+\$repo\s+\"launch\.pyw\"", text)
    assert re.search(r"\$link\.Arguments\s*=.*\$launcher", text)


def test_the_taskbar_pin_is_repointed_too():
    """A pin is a COPY, not a link to the menu entry - fixing only the Start Menu would leave the
    button he actually presses still asking for whatever it was pinned with."""
    text = (REPO / "tools/install-start-menu.ps1").read_text(encoding="utf-8")

    assert "User Pinned\\TaskBar\\Excephalon.lnk" in text
    assert re.search(r"foreach\s*\(\$shortcut\s+in\s+@\(\$menu,\s*\$pin\)\)", text)
    # ...but only updated. A .lnk written into that folder pins nothing.
    assert re.search(r"-not\s+\(Test-Path\s+\$pin\)", text)
