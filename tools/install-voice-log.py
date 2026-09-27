import os
import sys
from pathlib import Path
from typing import NamedTuple


class Shortcut(NamedTuple):
    path: Path
    target: Path
    arguments: str
    working_directory: Path
    icon: Path


def shortcuts(repo, *, appdata, interpreter):
    programs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return tuple(
        Shortcut(path=place / "Voice Log.lnk", target=interpreter,
                 arguments=f'"{repo / "voice_log.pyw"}"', working_directory=repo,
                 icon=repo / "assets" / "voice-log.ico")
        for place in (programs / "Startup", programs))


def named_interpreter(repo):
    pythonw = repo / ".venv" / "Scripts" / "pythonw.exe"
    try:
        from app_support.process_identity import ProcessNamer

        return Path(ProcessNamer("Voice Log", icon=repo / "assets" / "voice-log.ico")
                    .named_exe(pythonw, "VoiceLog"))
    except Exception:
        return pythonw


def save(shortcut):
    import win32com.client

    link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(shortcut.path))
    link.TargetPath = str(shortcut.target)
    link.Arguments = shortcut.arguments
    link.WorkingDirectory = str(shortcut.working_directory)
    link.IconLocation = str(shortcut.icon)
    link.Description = "Voice Log - records the microphone all the time and keeps two days"
    link.Save()


def main():
    repo = Path(__file__).resolve().parents[1]
    for shortcut in shortcuts(repo, appdata=os.environ["APPDATA"],
                              interpreter=named_interpreter(repo)):
        shortcut.path.parent.mkdir(parents=True, exist_ok=True)
        save(shortcut)
        print(f"installed {shortcut.path}")


if __name__ == "__main__":
    sys.exit(main())
