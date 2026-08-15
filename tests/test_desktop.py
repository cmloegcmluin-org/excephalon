import json
import socket
import threading

from excephalon.desktop import (
    WINDOW,
    Controls,
    free_port,
    open_window,
    restore_context_menus,
    restored_geometry,
    serve,
    set_app_id,
)
from excephalon.mirror import APP_ID


class _App:
    """Stands in for the Flask app: records how it was told to run, and returns at once - a test
    that binds a real port serves a page nobody reads and leaves a thread behind."""

    def __init__(self):
        self.ran = threading.Event()
        self.how = None

    def run(self, **how):
        self.how = how
        self.ran.set()


class _Hook:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _Shown:
    """A window as pywebview hands one back: events to hook, a spot on screen, a destroy."""

    def __init__(self):
        self.events = type("events", (), {"closing": _Hook(), "loaded": _Hook()})()
        self.x, self.y, self.width, self.height = 120, 80, 980, 760
        self.evaluated = []
        self.destroyed = False

    def evaluate_js(self, script):
        self.evaluated.append(script)

    def destroy(self):
        self.destroyed = True


class _Webview:
    """Stands in for pywebview, so the wiring can be checked without opening a window.

    Records the order things happened in as well as what they were: the taskbar identity has to be
    claimed BEFORE the window exists, and afterwards is the same as never."""

    def __init__(self):
        self.made = []
        self.started = []
        self.order = []
        self.window = _Shown()

    def create_window(self, title, url, **how):
        self.made.append((title, url, how))
        self.order.append("window")
        return self.window

    def start(self, **how):
        self.started.append(how)
        self.order.append("start")


def test_the_port_is_one_the_machine_says_is_free():
    port = free_port()

    with socket.socket() as sock:  # free means bindable; a fixed one collides with its holder
        sock.bind(("127.0.0.1", port))


def test_the_window_is_pointed_at_the_app_on_loopback_only():
    webview = _Webview()

    open_window(_App(), title="Excephalon", webview=webview, port=8123)

    title, url, how = webview.made[0]
    assert title == "Excephalon"
    assert url == "http://127.0.0.1:8123/"  # nothing off this machine can reach it
    assert how == WINDOW  # its own size, and a floor under it
    assert webview.started == [{}]


def test_the_window_lets_him_select_the_words_in_it():
    # pywebview turns text selection OFF unless asked (create_window's text_select defaults to
    # False), so every part of a message had to opt back in by hand - and anything nobody thought
    # to opt in could not be selected at all. The app aside was one: a startup failure printed
    # itself on screen and he could not drag over it to copy the error to anyone.
    assert WINDOW["text_select"] is True


def test_closing_the_window_asks_first_in_the_apps_own_styling(tmp_path):
    # "Godddamnit, I accidentally closed this app. There should definitely be an 'are you sure'
    # confirmation dialog!!" - and behind that X are a live conversation, a mic and running agents.
    # The asking is the page's own dialog now (the native confirm was a light-mode system box in
    # a dark app): the X saves where the window stands, hands the page the question, and keeps
    # the window - only the dialog's Close, through Controls.quit, actually closes.
    webview = _Webview()
    spot = tmp_path / "window-position.json"

    open_window(_App(), webview=webview, port=8123, position_path=spot)
    [asked] = webview.window.events.closing.handlers

    assert asked() is False                       # the native close is cancelled...
    assert "askToClose" in webview.window.evaluated[0]  # ...and the page asks instead
    assert json.loads(spot.read_text(encoding="utf-8"))["x"] == 120


def test_the_dialogs_close_answers_the_request_first_then_closes(tmp_path):
    # Destroying the window from inside a request handler of the very server it is showing
    # deadlocked the whole app on his first close (Windows logged pythonw as HUNG) - so the
    # close is deferred a beat, and the /quit response gets out before the window goes. The
    # position is saved by the closing event the close then fires, on the GUI thread, where
    # reading the window's geometry is unconditionally safe.
    window = _Shown()
    controls = Controls(window, tmp_path / "spot.json")

    controls.quit()

    assert not window.destroyed  # not yet - the request must be answered first
    assert controls.asked_to_close() is True  # the close's own closing event: saved and waved on
    assert json.loads((tmp_path / "spot.json").read_text(encoding="utf-8"))["width"] == 980
    for _ in range(100):
        if window.destroyed:  # the fake has no winforms form, so the fallback destroy closes it
            break
        threading.Event().wait(0.01)
    assert window.destroyed


def test_our_own_destroy_is_waved_through_the_closing_event(tmp_path):
    # destroy() fires the same closing event the X does. Answering our own destroy with the
    # dialog question - evaluate_js against a page mid-teardown - blocked the GUI thread forever:
    # the dialog's Close hung the whole app twice, and the thread dump showed this exact handler
    # inside the destroy. After quit(), the only answer is "go".
    window = _Shown()
    controls = Controls(window, tmp_path / "spot.json")

    controls.quit()

    assert controls.asked_to_close() is True   # the closing our destroy fires passes through...
    assert window.evaluated == []              # ...without ever asking the dying page anything


def test_restart_closes_marked_so_the_winddown_relaunches():
    window = _Shown()
    controls = Controls(window)

    controls.restart()

    assert controls.restart_asked is True
    for _ in range(100):
        if window.destroyed:
            break
        threading.Event().wait(0.01)
    assert window.destroyed


class _Screen:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


def test_the_window_reopens_where_it_was_closed_unless_that_screen_is_gone():
    # "Entity window should remember where it was on the screen when it was most recently closed,
    # and reopen there" - but a spot on an unplugged monitor would reopen it off-screen, which
    # reads as an app that vanished.
    saved = {"x": 2000, "y": 100, "width": 800, "height": 600}
    two_screens = [_Screen(0, 0, 1920, 1080), _Screen(1920, 0, 1920, 1080)]

    assert restored_geometry(saved, two_screens) == saved      # its monitor is there
    assert restored_geometry(saved, two_screens[:1]) == {}     # its monitor is gone: defaults
    assert restored_geometry(saved, None) == saved             # nothing known: trust the record
    assert restored_geometry({}, two_screens) == {}            # nothing saved: defaults
    assert restored_geometry({"x": 5}, two_screens) == {}      # a torn record: defaults


def test_the_taskbar_identity_is_claimed_before_the_window_exists(monkeypatch):
    # Windows groups taskbar buttons by AppUserModelID, and a process that declares none inherits
    # whatever other pythonw-hosted app already owns one - Excephalon turned up as another app. Claimed
    # after the window is made, it is the same as never claimed.
    claimed = []
    monkeypatch.setattr("excephalon.desktop.set_app_id", lambda app_id: claimed.append(app_id))
    webview = _Webview()

    open_window(_App(), webview=webview, port=8123)

    assert claimed == [APP_ID]
    assert webview.order == ["window", "start"]  # and the claim happened before either


def test_a_platform_without_the_taskbar_api_still_gets_a_window():
    # A cosmetic nicety must never keep the window from opening.
    def refuses(_):
        raise OSError("no shell32 here")

    set_app_id("Excephalon.VoiceCompanion", api=refuses)


def test_the_server_is_reachable_only_from_this_machine_and_cannot_outlive_the_window():
    app = _App()

    thread = serve(app, 8123)
    app.ran.wait(2)

    # A served page nobody can see must not keep the process alive after the window closes.
    assert thread.daemon
    assert app.how["host"] == "127.0.0.1"
    assert app.how["port"] == 8123
    assert app.how["threaded"] is True   # the page polls while a page is being saved
    assert app.how["use_reloader"] is False  # a reloader would fork a second conversation


class _Form:
    """The winforms form pywebview builds, and the WebView2 control it hosts."""

    def __init__(self, on_ui_thread=True):
        self.InvokeRequired = not on_ui_thread
        self.browser = type("browser", (), {"webview": "the control"})()


class _Window:
    def __init__(self, form):
        self.native = form


def test_the_page_gets_its_right_click_menu_back():
    # pywebview ties WebView2's default context menus to its debug flag, so an ordinary run has
    # none - and copying PART of a message is exactly what that menu is for. The hover button
    # copies a whole message; a selection needs this.
    turned_on = []

    restore_context_menus(_Window(_Form()), apply=turned_on.append)

    assert turned_on == ["the control"]


def test_a_menu_that_cannot_be_restored_costs_a_menu_and_nothing_else():
    # Best-effort: Ctrl+C keeps working either way, so a backend without a winforms control
    # underneath it must not take the window down with it.
    restore_context_menus(_Window(form=None))


def test_the_mac_dock_face_is_claimed_at_startup_and_a_failure_costs_only_cosmetics():
    # Launched outside its bundle (the old relauncher's bare python, a terminal run), the app sat
    # in the Dock as "Python" with the interpreter's icon - "it came back with a different icon
    # ... and is named Python". The window claims its own face at startup, the same shape as the
    # Windows AppUserModelID claim, and a platform where the claim fails just doesn't get one.
    from excephalon.desktop import set_mac_identity

    claimed = []
    set_mac_identity("/repo/assets/excephalon.png", api=claimed.append, bundled=lambda: None)
    assert claimed == ["/repo/assets/excephalon.png"]

    def refuses(icon):
        raise RuntimeError("no AppKit here")
    set_mac_identity("/repo/assets/excephalon.png", api=refuses,  # must not raise
                     bundled=lambda: None)


def test_a_bundle_launched_app_keeps_the_icon_the_system_gave_it():
    # "the Dock icon keeps losing its squircle" - this OS drapes every app icon on its rounded
    # plate, but only when the icon comes from the BUNDLE: an image set at runtime shows raw. So
    # the startup claim, added for bare-python launches, was stripping the plate off every proper
    # launch a moment after it appeared. Inside the bundle the system's icon is already right;
    # the claim is only for a process the bundle never dressed.
    from excephalon.desktop import set_mac_identity

    claimed = []
    set_mac_identity("/repo/assets/excephalon.png", api=claimed.append,
                     bundled=lambda: "Excephalon.VoiceCompanion")
    assert claimed == []

    set_mac_identity("/repo/assets/excephalon.png", api=claimed.append, bundled=lambda: None)
    assert claimed == ["/repo/assets/excephalon.png"]


def test_the_mac_never_hands_the_raw_icon_to_the_view_layer(monkeypatch):
    # pywebview's cocoa backend paints the Dock itself from start(icon=...) - raw file, no plate
    # - so every proper launch had its squircle stripped a moment after it appeared, whatever the
    # bundle's icns carried ("Excephalon icon still broken in the same way"). On a Mac the baked
    # bundle icns serves every Dock state; the view layer gets no icon to paint over it with. The
    # desk that needs the parameter (the Windows taskbar) still gets it.
    from excephalon import machine

    monkeypatch.setattr(machine, "MACOS", True)
    monkeypatch.setattr(machine, "WINDOWS", False)
    webview = _Webview()
    open_window(_App(), icon="/repo/assets/excephalon.ico", webview=webview, port=8123)
    assert webview.started == [{}]

    monkeypatch.setattr(machine, "MACOS", False)
    monkeypatch.setattr(machine, "WINDOWS", True)
    webview = _Webview()
    open_window(_App(), icon="/repo/assets/excephalon.ico", webview=webview, port=8123)
    assert webview.started == [{"icon": "/repo/assets/excephalon.ico"}]
