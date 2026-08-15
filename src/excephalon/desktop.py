"""The window as an application: a browser engine in an OS window of its own, not a browser tab.

A local Flask app on a loopback port nobody else can
reach, and pywebview holding a WebView2 view of it. What that buys over a tab is what a companion
needs: its own taskbar button and icon, no address bar, no other tabs, and a window that is closed
rather than navigated away from.

The server thread is a daemon: when the window closes the process ends, and a served page nobody
can see must not keep it alive.
"""

import json
import socket
import threading
from pathlib import Path

from excephalon import machine
from excephalon.mirror import APP_ID

# The X still asks first - "Godddamnit, I accidentally closed this app. There should definitely
# be an 'are you sure' confirmation dialog!!" - but the asking is the page's own styled dialog
# now, not the OS message box: the native one turned up in light mode with a system font inside
# an app that is neither. So the native confirm is off, the closing event hands the question to
# the page, and only the dialog's own Close button (through Controls.quit) actually closes.
# `text_select` because pywebview turns selection OFF by default: every part of a message had to
# opt back in with `user-select: text`, and whatever nobody thought to opt in could not be dragged
# over at all - a startup failure printed itself on screen and he could not copy the error out of
# it to send to anyone. Selection is the page's business, and the page says what must not be
# selected (its tags, its buttons) in its own stylesheet.
WINDOW = {"width": 980, "height": 760, "min_size": (620, 520), "text_select": True}


def _set_app_id_via_shell32(app_id):
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


def set_app_id(app_id, api=_set_app_id_via_shell32):
    """Claim a taskbar identity. Must happen before the window exists, and a platform without the
    API just doesn't get one - a cosmetic nicety must never keep the window from opening."""
    try:
        api(app_id)
    except Exception:
        pass


def _set_mac_identity_via_appkit(icon):
    from AppKit import NSApplication, NSImage  # noqa: PLC0415 - pyobjc, present under the cocoa backend

    image = NSImage.alloc().initWithContentsOfFile_(str(icon))
    if image is not None:
        NSApplication.sharedApplication().setApplicationIconImage_(image)


def _launching_bundle():
    """The bundle identity LaunchServices launched us under, or None for a bare process. Read
    from the environment because it survives the bundle script's exec into the venv python,
    while NSBundle.mainBundle follows the executable and answers "Python" either way."""
    import os

    return os.environ.get("__CFBundleIdentifier")


def set_mac_identity(icon, api=_set_mac_identity_via_appkit, bundled=_launching_bundle):
    """Claim the Dock face on a Mac - but ONLY outside the bundle. Launched bare - the old
    relauncher's venv python, a terminal run - the app sat in the Dock as "Python" with the
    interpreter's icon: "it came back with a different icon ... and is named Python". Launched
    properly, the system already shows the bundle's icon on its rounded plate, and an image set
    at runtime shows RAW - so claiming it anyway was "the Dock icon keeps losing its squircle",
    the plate stripped off every good launch a moment after it appeared. Best-effort, and a
    platform where the claim fails just doesn't get one."""
    try:
        if icon and not bundled():
            api(icon)
    except Exception:
        pass


def free_port(host="127.0.0.1"):
    """A port the machine says is free. Fixed ports collide with whatever already holds them,
    and this is loopback-only, so nothing needs to know it in advance."""
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def serve(app, port, host="127.0.0.1"):
    """Run the app on a thread that cannot outlive the window."""
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False,
                               threaded=True),
        daemon=True, name="excephalon-web")
    thread.start()
    return thread


def turn_on_context_menus(control):
    """Switch WebView2's default context menus back on, once its core is up.

    pywebview's WebView2 backend ties `AreDefaultContextMenusEnabled` to its debug flag, so an
    ordinary run has no right-click menu anywhere - and copying PART of a message is exactly what
    that menu is for. The hover button copies a whole message; a selection needs this. Called
    before the core has finished initialising the setting would have nothing to land on, so the
    caller fires and forgets."""
    core = control.CoreWebView2
    if core is not None:
        core.Settings.AreDefaultContextMenusEnabled = True


def restore_context_menus(window, apply=turn_on_context_menus):
    """Give the page back its right-click Cut/Copy/Paste.

    Windows only, and best-effort: Ctrl+C keeps working either way, so a failure here costs a
    menu and nothing else."""
    try:
        form = window.native            # the winforms form pywebview built
        control = form.browser.webview  # the WebView2 control it hosts
        # A WebView2 setting must be touched on the thread that created the control.
        if form.InvokeRequired:
            from System import Action  # noqa: PLC0415 - pythonnet, only under the winforms backend

            form.Invoke(Action(lambda: apply(control)))
        else:
            apply(control)
    except Exception:
        pass


def restored_geometry(saved, screens):
    """Where the window reopens: exactly where it was closed - "Excephalon window should remember
    where it was on the screen" - unless that spot no longer exists (a monitor unplugged), in
    which case the defaults, because a window restored off-screen reads as an app that vanished.

    `screens` is what the machine offers now; with none known the record is trusted as it stands.
    A small tolerance lets a window hugged against an edge still count as on that screen."""
    if not saved:
        return {}
    corners = tuple(saved.get(side) for side in ("x", "y", "width", "height"))
    if any(corner is None for corner in corners):
        return {}
    x, y, width, height = corners
    if screens is None:
        return {"x": x, "y": y, "width": width, "height": height}
    for screen in screens:
        if (x >= screen.x - 16 and y >= screen.y - 16
                and x + width <= screen.x + screen.width + 32
                and y + height <= screen.y + screen.height + 32):
            return {"x": x, "y": y, "width": width, "height": height}
    return {}


class Controls:
    """The window, as the page and the app may drive it: close it for real, or restart the whole
    process. The X never closes directly - it saves where the window stands and hands the page
    the question; these two are what the page's own dialog (and the Restart button) call."""

    def __init__(self, window, position_path=None):
        self._window = window
        self._path = Path(position_path) if position_path else None
        self.restart_asked = False
        self._leaving = False  # quit() was called; the next closing event is our own destroy

    def keep_position(self):
        """Write down where the window stands, so the next launch opens it right there."""
        if self._path is None:
            return
        try:
            stands = {side: int(getattr(self._window, side))
                      for side in ("x", "y", "width", "height")}
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(stands), encoding="utf-8")
        except Exception:
            pass  # a position that can't be kept costs the spot, never the close

    def asked_to_close(self):
        """The X was pressed: keep the spot, put the question to the page, keep the window.
        Returning False is what cancels the native close.

        UNLESS the close is our own: the wind-down fires this same closing event, and answering
        it with evaluate_js against the page being torn down blocked the GUI thread forever -
        the dialog's Close hung the whole app, twice, and the second time the thread dump showed
        exactly this handler inside the destroy. Once quit() has spoken, the answer is only ever
        "go" - and the position is saved HERE, because this handler runs on the GUI thread,
        where reading the window's geometry is unconditionally safe."""
        if self._leaving:
            self.keep_position()
            return True
        self.keep_position()
        # The ask goes to the page OFF this thread. This handler runs on the GUI thread, and
        # evaluate_js waits for the page's answer - plumbing that itself needs the GUI thread,
        # so asking inline deadlocked the X press on itself: press, freeze, Windows' hang
        # reporter, four times across one night. Fired from a worker, the dialog appears a beat
        # later and this handler has already returned False (window stays) either way.
        threading.Thread(target=self._ask_the_page, daemon=True).start()
        return False

    def _ask_the_page(self):
        try:
            self._window.evaluate_js("askToClose && askToClose()")
        except Exception:
            pass  # a page that cannot ask leaves the window open; the X still works next press

    def quit(self):
        """Actually close - the page's dialog said so, or a restart is tearing down.

        Three hard-won rules live here, each a hang he sat through. The close is deferred off
        this thread, because it is called from inside a request handler of the very server the
        window is showing, and tearing the window down under its own unanswered request
        deadlocked the app. The close goes through the NATIVE close on the GUI thread - the same
        road the X takes, the one path the toolkit exercises everywhere - not a cross-thread
        destroy, which still hung on his desk after the repro of it passed here. And a watchdog
        writes every thread's stack to runtime/close-stall.log if the app is somehow still alive
        well after the close was asked for, so the NEXT stall carries its own evidence instead
        of anyone's inference about his machine."""
        self._leaving = True  # before the close, so the closing event it fires is waved through
        threading.Timer(0.2, self._close_on_gui_thread).start()
        watchdog = threading.Timer(12.0, self._dump_stall)
        watchdog.daemon = True  # if the close succeeds, the process is gone before this fires
        watchdog.start()

    def _close_on_gui_thread(self):
        try:
            form = self._window.native  # the winforms form pywebview built
            from System import Action  # noqa: PLC0415 - pythonnet, only under the winforms backend

            if form.InvokeRequired:
                form.Invoke(Action(form.Close))
            else:
                form.Close()
        except Exception:
            try:
                self._window.destroy()  # a backend without winforms still gets a close
            except Exception:
                pass

    def _dump_stall(self):
        """The app should be gone by now; write down exactly where every thread is stuck."""
        try:
            import faulthandler

            where = (self._path.parent if self._path is not None else Path("runtime"))
            with open(where / "close-stall.log", "a", encoding="utf-8") as log:
                log.write("\n=== the close was asked for 12s ago and the app is still alive ===\n")
                faulthandler.dump_traceback(log)
        except Exception:
            pass  # the watchdog must never become its own crash

    def restart(self):
        """Close, marked so the winddown relaunches a fresh process - the reload button's whole
        point: a fix lands, one click, the same conversation resumes on the new code."""
        self.restart_asked = True
        self.quit()


def open_window(app, *, title="Excephalon", icon=None, webview=None, port=None,
                position_path=None, hand_controls=None):
    """Show the app in its own window, and return when that window is closed.

    `webview` is injected so the wiring can be exercised without a display; a machine without
    pywebview raises, rather than quietly opening a browser tab - a tab is the thing this exists
    not to be. `hand_controls` receives the Controls, so routes like /quit and /restart can drive
    the window they serve under. Returns the Controls once the window has closed."""
    if webview is None:
        import webview  # noqa: PLC0415 - optional at import time, required to show a window

    # Before the window exists, or the taskbar button is already grouped. Windows groups buttons by
    # AppUserModelID, and a process that declares none inherits the identity of whatever other
    # pythonw-hosted app already owns one - Excephalon window turned up under another app's icon,
    # wearing its name. It did so again the moment this call was dropped in a move.
    set_app_id(APP_ID)
    if machine.MACOS and icon:
        # The .png beside the .ico: NSImage reads it directly, and both are cut from one source.
        set_mac_identity(str(Path(icon).with_suffix(".png")))
    port = port or free_port()
    serve(app, port)
    saved = {}
    if position_path and Path(position_path).exists():
        try:
            saved = json.loads(Path(position_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
    where = restored_geometry(saved, getattr(webview, "screens", None))
    window = webview.create_window(title, f"http://127.0.0.1:{port}/", **(WINDOW | where))
    controls = Controls(window, position_path)
    if hand_controls is not None:
        hand_controls(controls)
    if hasattr(window, "events"):
        # Once the page is up, hand it back its right-click menu - pywebview switches it off, and
        # copying part of a message rather than all of it is what that menu is for.
        window.events.loaded += lambda: restore_context_menus(window)
        # The X asks, in the app's own styling, instead of closing: see Controls.asked_to_close.
        window.events.closing += controls.asked_to_close
    # pywebview's winforms backend applies the icon to the window, and so to the taskbar button;
    # without it Windows shows pythonw.exe's. (Its "GTK/QT only" docstring is stale - and stale
    # for cocoa too, which is why the Mac must NOT get it: that backend paints the Dock with the
    # raw file, stripping the system's rounded plate off every proper launch, whatever the
    # bundle's baked icns carried. On a Mac the bundle serves every Dock state by itself.)
    webview.start(icon=icon) if icon and machine.WINDOWS else webview.start()
    return controls
