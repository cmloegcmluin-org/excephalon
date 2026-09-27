import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
TITLE = "Voice Log couldn't start"
FAILURE_LOG = REPO / "runtime" / "logs" / "voice-log-launch-failure.log"


def enter():
    from excephalon.voice_log_tray import main

    main(REPO / "runtime")


def _door():
    spec = importlib.util.spec_from_file_location("excephalon_door", REPO / "launch.pyw")
    door = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(door)
    return door


if __name__ == "__main__":
    door = _door()
    sys.path.insert(0, str(REPO / "src"))
    door.name_this_process("Voice Log", "VoiceLog", REPO / "assets" / "voice-log.ico")
    sys.exit(door.open_door(enter, title=TITLE, log=FAILURE_LOG))
