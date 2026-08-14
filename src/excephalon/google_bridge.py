"""An MCP server for the user's Gmail and Calendar, over Google's classic APIs.

It BEGAN as a forwarder to Google's hosted MCP servers (gmailmcp.googleapis.com,
calendarmcp.googleapis.com), and every layer of that road was made to work - their one accepted
protocol version, the client name they don't hang up on, the CLI transport bug walked around by
speaking stdio - until the last: with a verified-perfect sign-in (web client, their own guides'
five scopes, every API enabled), every tools/call still came back "The caller does not have
permission", while the CLASSIC APIs answered the same token flawlessly - calendars, 99 labels.
So the bridge now IS the server: same registrations, same tools/call surface, but the work done
against the classic endpoints that provably answer. Do not point anything back at the hosted
servers without watching a tools/call actually succeed.

Auth is its own, which is the other half of why it exists: Google's sign-in refuses dynamic
client registration (the CLI's /mcp flow cannot start), and the CLI keeps what tokens it wins in
the macOS Keychain, where a headless session may not follow. The user's OAuth client
(runtime/google/client.json) and tokens (runtime/google/tokens.json) live in runtime/ - personal,
gitignored, readable by every session the app spawns, and copyable between machines: the PC needs
no browser dance at all. `--connect` is the one-time sign-in; `--serve <url>` is what the CLI
launches per server, the url naming which service's tools this instance offers.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

# Where the personal half lives, found from this file so the bridge works whatever cwd the CLI
# spawns it with - an errand session's, his interactive shell's, anyone's.
RUNTIME_GOOGLE = Path(__file__).resolve().parents[2] / "runtime" / "google"

TOKEN_URL = "https://oauth2.googleapis.com/token"

# EXACTLY the scopes Google's own MCP-server guides name, and no others - a token carrying the
# broad auth/calendar instead of the granular three got "The caller does not have permission",
# and gmail.modify in place of gmail.readonly read as "insufficient authentication scopes". The
# same five URLs are registered on the consent screen's Data Access page. They also bound what
# the classic-API tools below may do: read mail, draft mail, read the calendar.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
    "https://www.googleapis.com/auth/calendar.events.readonly",
)

def connect_hint(windows=None):
    """The one thing he can do, naming the door THIS desk actually has.

    The hint named only the Mac's `Connect Google.command` while the Windows desk had no such
    file - and no launcher of any kind, so when the sign-in died the only way back was a command
    line he does not work from. Each desk names its own door."""
    from excephalon import machine

    door = "Connect Google.bat" if (machine.WINDOWS if windows is None else windows) \
        else "Connect Google.command"
    return (f"Google's sign-in has expired or was never done - double-click {door} in the "
            "Excephalon folder and sign in once, and this works again.")

# The sign-in catcher's FIXED port: a Web-application OAuth client honors only redirect URIs
# registered in advance, exactly, so the port cannot be whatever the machine had free.
# http://127.0.0.1:8765 is the one URI registered, spelled with the number, because Google
# treats localhost and 127.0.0.1 as different strings.
CONNECT_PORT = 8765

PROTOCOL = "2025-06-18"


def load_client(path):
    """His OAuth client, from the file Google's console hands out, dropped in as it came.

    The download wraps the values in {"installed": {...}} (or {"web": ...}); asking him to
    rewrap them is asking a person to be a JSON parser. A flat file works too, and an absent or
    unreadable one is {} - the bridge still serves, and `connect_hint` is what says why
    nothing is signed in."""
    try:
        held = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(held, dict):
        return {}
    return held.get("installed") or held.get("web") or held


class FileTokens:
    """The tokens, in runtime/google/tokens.json: personal, gitignored, and readable by every
    session the app spawns - which the Keychain, where the CLI keeps its own, may not be."""

    def __init__(self, path):
        self._path = Path(path)

    def read(self):
        try:
            held = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return held if isinstance(held, dict) else {}

    def write(self, tokens):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(tokens), encoding="utf-8")
        os.chmod(self._path, 0o600)  # his sign-in; no other account on the machine needs it


def sign_in_fault(*, tokens=None, client=None, post=None):
    """"" when Google's sign-in is alive, or the one thing he can do when it is not.

    A launch check proves a server STARTS; this bridge starts perfectly with a sign-in Google has
    revoked, which is exactly how the failure hid - startup announced Gmail and Calendar as
    reachable, and the first real errand hours later answered that they were never set up. Trading
    the refresh token settles it in one request and leaves a fresh access token behind, so the
    check is also the warm-up.

    A request that cannot reach Google at all is NOT a fault: offline is not signed-out, and
    sending him to redo a good sign-in is the wrong fix confidently given."""
    tokens = tokens if tokens is not None else FileTokens(RUNTIME_GOOGLE / "tokens.json")
    client = client if client is not None else load_client(RUNTIME_GOOGLE / "client.json")
    post = post or _https_post
    held = tokens.read()
    if not (held.get("refresh_token") and client.get("client_id")):
        return connect_hint()
    try:
        status, _, body = post(
            TOKEN_URL,
            urlencode({"grant_type": "refresh_token",
                       "refresh_token": held["refresh_token"],
                       "client_id": client["client_id"],
                       "client_secret": client.get("client_secret", "")}).encode("utf-8"),
            {"Content-Type": "application/x-www-form-urlencoded"})
    except Exception:
        return ""  # unreachable, not refused - his sign-in is not what is wrong
    if status != 200:
        return connect_hint()
    tokens.write({**held, "access_token": json.loads(body)["access_token"]})
    return ""


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def auth_url(client, *, port):
    """Where the browser goes to sign in. access_type=offline + prompt=consent is what makes
    Google hand over a REFRESH token - without them the first sign-in yields an access token
    that dies in an hour with no way back, and he would be asked to sign in every hour."""
    return AUTH_URL + "?" + urlencode({
        "client_id": client.get("client_id", ""),
        "redirect_uri": f"http://127.0.0.1:{port}",
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    })


def exchange_code(code, client, *, port, tokens, post):
    """Trade the one-time code Google sent back for the tokens, and write them down."""
    status, _, body = post(
        TOKEN_URL,
        urlencode({"code": code,
                   "client_id": client.get("client_id", ""),
                   "client_secret": client.get("client_secret", ""),
                   "redirect_uri": f"http://127.0.0.1:{port}",
                   "grant_type": "authorization_code"}).encode("utf-8"),
        {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise RuntimeError(f"Google declined the sign-in code: {body[:200]}")
    fresh = json.loads(body)
    tokens.write({"access_token": fresh.get("access_token", ""),
                  "refresh_token": fresh.get("refresh_token", "")})


def _https_post(url, body, headers):
    """The real HTTPS side: (status, content_type, text). An HTTP error status is an answer here,
    not an exception - the caller's whole job is deciding what a 401 means."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return (response.status, response.headers.get("Content-Type", ""),
                    response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as denied:
        return (denied.code, denied.headers.get("Content-Type", ""),
                denied.read().decode("utf-8", errors="replace"))


def _https_json(method, url, token, body=None):
    """One classic-API request as JSON, or HTTPError for the caller to word."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "excephalon-google-bridge/1"},
        method=method)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------- the tools, per service, over the classic endpoints that provably answer ----------

CALENDAR = "https://www.googleapis.com/calendar/v3"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


def _trim_event(event):
    return {"summary": event.get("summary", ""), "start": event.get("start"),
            "end": event.get("end"), "location": event.get("location"),
            "status": event.get("status"), "id": event.get("id")}


def _list_events(ask, a, extra=None):
    calendar = a.get("calendar_id") or "primary"
    query = urlencode({k: v for k, v in {
        "timeMin": a.get("time_min"), "timeMax": a.get("time_max"),
        "maxResults": a.get("max_results") or 25,
        "singleEvents": "true", "orderBy": "startTime", **(extra or {})}.items() if v})
    held = ask("GET", f"{CALENDAR}/calendars/{calendar}/events?{query}")
    return [_trim_event(e) for e in held.get("items", [])]


def _headers_of(message):
    wanted = {"From", "To", "Subject", "Date"}
    held = {h["name"]: h["value"] for h in message.get("payload", {}).get("headers", [])
            if h.get("name") in wanted}
    held["snippet"] = message.get("snippet", "")
    held["id"] = message.get("id", "")
    return held


def _text_of(payload):
    """The text/plain body under a message payload, decoded; parts walked depth-first."""
    if payload.get("mimeType", "").startswith("text/plain"):
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
    for part in payload.get("parts", []) or []:
        text = _text_of(part)
        if text:
            return text
    return ""


def _draft_raw(a):
    mail = EmailMessage()
    mail["To"] = a["to"]
    mail["Subject"] = a.get("subject", "")
    mail.set_content(a.get("body", ""))
    return base64.urlsafe_b64encode(mail.as_bytes()).decode("ascii")


# name -> (description, {argument: (json_type, required, description)}, handler(ask, args))
CALENDAR_TOOLS = {
    "list_calendars": (
        "Every calendar on this account.",
        {},
        lambda ask, a: [{"id": c.get("id"), "summary": c.get("summary"),
                         "primary": c.get("primary", False)}
                        for c in ask("GET", f"{CALENDAR}/users/me/calendarList").get("items", [])]),
    "list_events": (
        "Events in a window, soonest first. Times are RFC3339 (2026-08-04T00:00:00Z).",
        {"calendar_id": ("string", False, "which calendar; the primary if omitted"),
         "time_min": ("string", False, "earliest start"),
         "time_max": ("string", False, "latest start"),
         "max_results": ("integer", False, "cap, default 25")},
        _list_events),
    "search_events": (
        "Events matching words, soonest first.",
        {"query": ("string", True, "words to match"),
         "calendar_id": ("string", False, "which calendar; the primary if omitted"),
         "time_min": ("string", False, "earliest start"),
         "max_results": ("integer", False, "cap, default 25")},
        lambda ask, a: _list_events(ask, a, extra={"q": a["query"]})),
}

GMAIL_TOOLS = {
    "list_labels": (
        "Every label on this mailbox.",
        {},
        lambda ask, a: [{"id": l.get("id"), "name": l.get("name")}
                        for l in ask("GET", f"{GMAIL}/labels").get("labels", [])]),
    "search_threads": (
        "Find mail threads with Gmail's own search syntax (from:, is:unread, newer_than:7d...).",
        {"query": ("string", True, "the Gmail search"),
         "max_results": ("integer", False, "cap, default 20")},
        lambda ask, a: ask("GET", f"{GMAIL}/threads?" + urlencode(
            {"q": a["query"], "maxResults": a.get("max_results") or 20})).get("threads", [])),
    "get_thread": (
        "One thread's messages: who, when, subject, snippet.",
        {"thread_id": ("string", True, "from search_threads")},
        lambda ask, a: [_headers_of(m) for m in ask(
            "GET", f"{GMAIL}/threads/{a['thread_id']}?format=metadata"
            "&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Subject"
            "&metadataHeaders=Date").get("messages", [])]),
    "get_message": (
        "One message in full: headers and its plain-text body.",
        {"message_id": ("string", True, "from get_thread or search_threads")},
        lambda ask, a: (lambda m: {**_headers_of(m), "body": _text_of(m.get("payload", {}))})(
            ask("GET", f"{GMAIL}/messages/{a['message_id']}?format=full"))),
    "create_draft": (
        "Write a DRAFT reply or email - saved to Drafts for his review, never sent.",
        {"to": ("string", True, "recipient address"),
         "subject": ("string", False, "the subject line"),
         "body": ("string", False, "the message text")},
        lambda ask, a: {"draft_id": ask("POST", f"{GMAIL}/drafts",
                                        {"message": {"raw": _draft_raw(a)}}).get("id", "")}),
}


def toolset_for(url):
    """Which service this instance serves, named by the registered URL - so every existing
    registration and services.json entry keeps meaning what it always did."""
    return CALENDAR_TOOLS if "calendar" in url else GMAIL_TOOLS


class Bridge:
    """One service's MCP server: initialize and tools answered locally, the work done against
    the classic endpoints with the user's token, an hourly-expired token refreshed invisibly."""

    def __init__(self, url, *, post=_https_post, ask=None, tokens=None, client=None):
        self._tools = toolset_for(url)
        self._name = "excephalon-calendar" if self._tools is CALENDAR_TOOLS else "excephalon-gmail"
        self._post = post
        self._ask_raw = ask or _https_json
        self._tokens = tokens if tokens is not None else FileTokens(RUNTIME_GOOGLE / "tokens.json")
        self._client = client if client is not None else load_client(RUNTIME_GOOGLE / "client.json")

    def handle(self, line):
        """One JSON-RPC line in, the answer line out - or None where none is owed."""
        request = json.loads(line)
        request_id = request.get("id")
        if request_id is None:
            return None
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL,
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": self._name, "version": "2"}}
        elif method == "tools/list":
            result = {"tools": [
                {"name": name, "description": described,
                 "inputSchema": {"type": "object",
                                 "properties": {arg: {"type": kind, "description": about}
                                                for arg, (kind, _, about) in arguments.items()},
                                 "required": [arg for arg, (_, need, _a) in arguments.items()
                                              if need]}}
                for name, (described, arguments, _) in self._tools.items()]}
        elif method == "tools/call":
            result = self._run_tool(request.get("params") or {})
        elif method == "ping":
            result = {}
        else:
            return json.dumps({"jsonrpc": "2.0", "id": request_id,
                               "error": {"code": -32601, "message": f"unknown method: {method}"}})
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _run_tool(self, params):
        """A tool call's result - and a failure is an ANSWER (isError content), never a dead
        server: no sign-in names the fix, and Google's own error words pass through whole,
        because a swallowed reason sends whoever asked off to fix the wrong thing."""
        tool = self._tools.get(params.get("name"))
        if tool is None:
            return {"content": [{"type": "text", "text": f"no such tool: {params.get('name')}"}],
                    "isError": True}
        if not self._tokens.read().get("access_token"):
            return {"content": [{"type": "text", "text": connect_hint()}], "isError": True}
        try:
            answer = tool[2](self._ask, params.get("arguments") or {})
        except urllib.error.HTTPError as denied:
            if denied.code == 401:
                # A 401 that survived `_ask`'s refresh is a sign-in that cannot be revived -
                # Google had expired or revoked the refresh token. Its raw error page ("Request
                # had invalid authentication credentials...") was relayed to the user as the
                # integration being unset-up with no idea how to fix it; a dead sign-in is a dead
                # sign-in however it died, and the answer is the door.
                return {"content": [{"type": "text", "text": connect_hint()}], "isError": True}
            return {"content": [{"type": "text",
                                 "text": denied.read().decode("utf-8", errors="replace")[:1000]}],
                    "isError": True}
        except Exception as broke:
            return {"content": [{"type": "text", "text": f"bridge error: {broke}"}],
                    "isError": True}
        return {"content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}]}

    def _ask(self, method, url, body=None):
        """One authed classic-API call. A 401 is an hourly-expired access token before it is
        anything else: the refresh token is traded, written down, and the request retried once,
        invisibly - the CLI just sees its answer."""
        try:
            return self._ask_raw(method, url, self._tokens.read().get("access_token", ""), body)
        except urllib.error.HTTPError as denied:
            if denied.code != 401 or not self._refresh():
                raise
            return self._ask_raw(method, url, self._tokens.read().get("access_token", ""), body)

    def _refresh(self):
        """Trade the refresh token for a fresh access token, and write it down. False when there
        is nothing to trade or Google declines - the caller's error then stands as it was."""
        held = self._tokens.read()
        if not (held.get("refresh_token") and self._client.get("client_id")):
            return False
        status, _, body = self._post(
            TOKEN_URL,
            urlencode({"grant_type": "refresh_token",
                       "refresh_token": held["refresh_token"],
                       "client_id": self._client["client_id"],
                       "client_secret": self._client.get("client_secret", "")}).encode("utf-8"),
            {"Content-Type": "application/x-www-form-urlencoded"})
        if status != 200:
            return False
        fresh = json.loads(body)
        self._tokens.write({**held, "access_token": fresh["access_token"]})
        return True


def serve(url, *, stdin=sys.stdin, stdout=sys.stdout):
    """The stdio loop the CLI runs: one JSON-RPC line in, one out, until stdin closes."""
    bridge = Bridge(url)
    for line in stdin:
        if not line.strip():
            continue
        try:
            answer = bridge.handle(line)
        except Exception as exc:  # one bad request must not kill the server for the session
            try:
                request_id = json.loads(line).get("id")
            except ValueError:
                continue
            if request_id is None:
                continue
            answer = json.dumps({"jsonrpc": "2.0", "id": request_id,
                                 "error": {"code": -32000, "message": f"bridge error: {exc}"}})
        if answer:
            stdout.write(answer.strip() + "\n")
            stdout.flush()


def connect():
    """The one-time browser sign-in: catch Google's redirect on the registered loopback port,
    trade the code, write the tokens. Run by a person, so it talks in plain sentences."""
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    client = load_client(RUNTIME_GOOGLE / "client.json")
    if not client.get("client_id"):
        print(f"No OAuth client found. Put the JSON file Google's console gave you at\n"
              f"  {RUNTIME_GOOGLE / 'client.json'}\nand run this again.")
        return 1

    port = CONNECT_PORT

    caught = {}
    done = threading.Event()

    class Catcher(BaseHTTPRequestHandler):
        def do_GET(self):
            caught.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Signed in - you can close this tab and go back to Excephalon.</h2>")
            done.set()

        def log_message(self, *args):
            pass  # the terminal is for the sentences below, not request logs

    server = HTTPServer(("127.0.0.1", port), Catcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    where = auth_url(client, port=port)
    print("Opening your browser for the Google sign-in...")
    webbrowser.open(where)
    print("(if nothing opened, paste this into your browser:)\n  " + where)
    if not done.wait(300):
        print("No sign-in arrived within five minutes - run this again when ready.")
        return 1
    server.shutdown()
    if "code" not in caught:
        print(f"Google sent back an error instead of a sign-in: {caught.get('error', 'unknown')}")
        return 1
    exchange_code(caught["code"], client, port=port,
                  tokens=FileTokens(RUNTIME_GOOGLE / "tokens.json"), post=_https_post)
    print("Connected. Gmail and Google Calendar are signed in; nothing more to do here.")
    return 0


if __name__ == "__main__":
    if "--connect" in sys.argv:
        raise SystemExit(connect())
    if "--serve" in sys.argv:
        serve(sys.argv[sys.argv.index("--serve") + 1])
    else:
        raise SystemExit("usage: python -m excephalon.google_bridge --connect | --serve <url>")
