import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pystray
from PIL import Image

from excephalon import machine
from excephalon.links import open_on_this_machine
from excephalon.mic import open_live_microphone
from excephalon.voice_log import Recorder, VoiceLog
from excephalon.voice_log_icon import draw_icon

logger = logging.getLogger(__name__)

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
LONGEST_TOOLTIP = 127


def _picture(recording):
    return Image.fromarray(draw_icon(64, recording=recording), "RGBA")


class VoiceLogTray:
    def __init__(self, recorder, folder, *, make_icon=pystray.Icon,
                 open_folder=open_on_this_machine):
        self._recorder = recorder
        self._folder = Path(folder)
        self._open_folder = open_folder
        self._shown = None
        self._gone = threading.Event()
        self.icon = make_icon("Voice Log", _picture(recording=False), "Voice Log",
                              menu=pystray.Menu(
                                  pystray.MenuItem(lambda item: self._recorder.status().says, None,
                                                   enabled=False),
                                  pystray.MenuItem("Open recordings", self._open_recordings,
                                                   default=True),
                                  pystray.MenuItem("Quit", self._quit)))
        # pystray answers any message it has no handler for with 0, which to WM_QUERYENDSESSION
        # means "do not end the session": Windows then holds shutdown and lists this app as the
        # one preventing it.
        windows_messages = getattr(self.icon, "_message_handlers", None)
        if windows_messages is not None:
            windows_messages[WM_QUERYENDSESSION] = lambda wparam, lparam: 1
            windows_messages[WM_ENDSESSION] = self._session_ends

    def show(self, icon=None, *, every=1.0):
        self.icon.visible = True
        self.refresh()
        while not self._gone.wait(every):
            self.refresh()
        self.icon.stop()

    def refresh(self):
        status = self._recorder.status()
        if status == self._shown:
            return
        self._shown = status
        self.icon.icon = _picture(recording=status.recording)
        self.icon.title = f"Voice Log\n{status.says}"[:LONGEST_TOOLTIP]
        self.icon.update_menu()

    def _session_ends(self, ending, detail):
        if ending:
            self._recorder.stop(because="Windows is ending the session")
        return 0

    def _open_recordings(self):
        self._folder.mkdir(parents=True, exist_ok=True)
        self._open_folder(self._folder)

    def _quit(self):
        self.leave(because="Quit from the tray")

    def leave(self, because):
        self._gone.set()
        self._recorder.stop(because=because)
        self.icon.stop()


ONE_COPY = r"Local\Excephalon.VoiceLog"


class StillRunning(Exception):
    pass


def take_over(on_newer, *, kernel, patience=10.0):
    ownership = kernel.mutex(ONE_COPY)
    newer_launch = kernel.event(f"{ONE_COPY}.NewerLaunch")
    kernel.signal(newer_launch)
    if not kernel.wait(ownership, patience):
        raise StillRunning(f"The Voice Log already running did not stop within {patience:g} "
                           "seconds of being asked to make way")
    kernel.clear(newer_launch)
    threading.Thread(target=lambda: kernel.wait(newer_launch, None) and on_newer(),
                     name="voice-log-newer-launch", daemon=True).start()
    return ownership


WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF


class Win32Kernel:
    def __init__(self, kernel32=None):
        self._kernel32 = kernel32 or _load_kernel32()

    def mutex(self, name):
        return self._kernel32.CreateMutexW(None, False, name)

    def event(self, name):
        return self._kernel32.CreateEventW(None, True, False, name)

    def signal(self, event):
        self._kernel32.SetEvent(event)

    def clear(self, event):
        self._kernel32.ResetEvent(event)

    def wait(self, handle, seconds):
        milliseconds = INFINITE if seconds is None else int(seconds * 1000)
        return self._kernel32.WaitForSingleObject(handle, milliseconds) in (WAIT_OBJECT_0,
                                                                          WAIT_ABANDONED)


def _load_kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return kernel32


def write_down_to(log_file):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    everything = logging.getLogger()
    everything.addHandler(handler)
    everything.setLevel(logging.INFO)
    threading.excepthook = _write_down_how_a_thread_died
    return handler


def _write_down_how_a_thread_died(died):
    logging.getLogger(__name__).error(
        "The %s thread died", died.thread.name if died.thread else "unnamed",
        exc_info=(died.exc_type, died.exc_value, died.exc_traceback))


def main(runtime):
    runtime = Path(runtime)
    write_down_to(runtime / "logs" / "voice-log.log")
    folder = runtime / "voice-log"
    recorder = Recorder(VoiceLog(folder), lambda: open_live_microphone(runtime / "mic.txt",
                                                                       runtime / "mic-gain.txt"))
    tray = VoiceLogTray(recorder, folder)
    if machine.WINDOWS:
        take_over(on_newer=lambda: tray.leave(because="a newer Voice Log started"),
                  kernel=Win32Kernel())
    logger.info("Voice Log started, saving into %s", folder)
    recorder.start()
    tray.icon.run(setup=tray.show)
    logger.info("Voice Log closed")
