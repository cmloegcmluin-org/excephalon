"""The Gmail/Calendar MCP server, serving over Google's classic APIs.

It began as a forwarder to Google's hosted MCP servers, and every layer of that road was made to
work except the last: with a verified-perfect sign-in, every tools/call still answered "The
caller does not have permission" - while the classic APIs answered the same token flawlessly.
These tests pin the server it became; the OAuth half (the sign-in, the tokens, the refresh) is
what survived the pivot unchanged.
"""

import json
import urllib.error

from excephalon.google_bridge import Bridge, FileTokens


class FakeAsk:
    """The classic-API side, scripted: url fragment -> the JSON Google would answer."""

    def __init__(self, answers=None, denies=None):
        self.answers = answers or {}
        self.denies = denies  # an HTTPError to raise, once, then answer normally
        self.calls = []  # (method, url, token, body)

    def __call__(self, method, url, token, body=None):
        self.calls.append((method, url, token, body))
        if self.denies is not None:
            raised, self.denies = self.denies, None
            raise raised
    # falls through to the scripted answer
        for match, data in self.answers.items():
            if match in url:
                return data
        raise AssertionError(f"unexpected url: {url}")


class Tokens:
    def __init__(self, access=None, refresh=None):
        self.held = {k: v for k, v in (("access_token", access), ("refresh_token", refresh)) if v}

    def read(self):
        return dict(self.held)

    def write(self, tokens):
        self.held = dict(tokens)


GMAIL_URL = "https://gmailmcp.googleapis.com/mcp/v1"
CALENDAR_URL = "https://calendarmcp.googleapis.com/mcp/v1"


def _bridge(url=GMAIL_URL, *, ask=None, tokens=None, post=None, client=None):
    return Bridge(url, ask=ask or FakeAsk(), tokens=tokens or Tokens(access="tok-1"),
                  post=post or (lambda *a: (500, "", "")), client=client or {})


def _call_line(name, arguments=None, request_id=2):
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments or {}}})


def test_initialize_and_listing_are_the_bridges_own_and_name_the_service():
    # The hosted servers' initialize was a minefield (one accepted protocol version, a client
    # name they hang up on); answered locally there is no field to step on. The url in the
    # registration says which service this instance is, so nothing already registered changes.
    ask = FakeAsk()
    answer = json.loads(_bridge(CALENDAR_URL, ask=ask).handle(json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})))
    assert answer["result"]["serverInfo"]["name"] == "excephalon-calendar"

    listed = json.loads(_bridge(CALENDAR_URL, ask=ask).handle(json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})))
    assert {t["name"] for t in listed["result"]["tools"]} == {
        "list_calendars", "list_events", "search_events"}
    listed = json.loads(_bridge(GMAIL_URL, ask=ask).handle(json.dumps(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})))
    assert "search_threads" in {t["name"] for t in listed["result"]["tools"]}
    assert ask.calls == []  # its own knowledge, no network trip


def test_a_tool_call_reaches_the_classic_api_with_the_token_and_trims_the_answer():
    ask = FakeAsk(answers={"/users/me/calendarList": {"items": [
        {"id": "c1", "summary": "Personal", "primary": True, "etag": "noise",
         "conferenceProperties": {"noise": True}}]}})

    answer = json.loads(_bridge(CALENDAR_URL, ask=ask).handle(_call_line("list_calendars")))

    held = json.loads(answer["result"]["content"][0]["text"])
    assert held == [{"id": "c1", "summary": "Personal", "primary": True}]  # the facts, not the noise
    [(method, url, token, body)] = ask.calls
    assert method == "GET" and token == "tok-1" and body is None


def test_an_expired_token_is_refreshed_once_and_the_call_retried():
    # Google access tokens die hourly. The 401 is traded for a fresh token, written down, and
    # the call retried - invisible to the CLI, which just sees its answer.
    denied = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
    ask = FakeAsk(answers={"/labels": {"labels": [{"id": "L1", "name": "guitar"}]}},
                  denies=denied)
    tokens = Tokens(access="tok-stale", refresh="refresh-1")
    post = lambda url, body, headers: (200, "application/json",
                                       json.dumps({"access_token": "tok-new"}))

    answer = json.loads(_bridge(ask=ask, tokens=tokens, post=post,
                                client={"client_id": "id-1"}).handle(_call_line("list_labels")))

    assert json.loads(answer["result"]["content"][0]["text"]) == [{"id": "L1", "name": "guitar"}]
    assert tokens.held["access_token"] == "tok-new"  # written down, not just used once
    assert ask.calls[1][2] == "tok-new"  # the retry wears the fresh token


def test_no_sign_in_answers_with_the_one_thing_he_can_do():
    answer = json.loads(_bridge(tokens=Tokens()).handle(_call_line("list_labels")))
    held = answer["result"]
    assert held["isError"] and "Connect Google" in held["content"][0]["text"]


def test_a_sign_in_that_cannot_be_refreshed_answers_with_the_same_one_thing_he_can_do():
    # A refresh token Google has expired or revoked answered with its raw 401 JSON - "Request had
    # invalid authentication credentials... Expected OAuth 2 access token" - and Excephalon relayed
    # that as the integration being unset-up with no idea how to fix it. A dead sign-in is a dead
    # sign-in however it died: the answer is the door, not Google's error page.
    import io

    def denied_401():
        return urllib.error.HTTPError(
            "url", 401, "Unauthorized", {},
            io.BytesIO(b'{"error": {"message": "Invalid Credentials"}}'))

    ask = FakeAsk(denies=denied_401())
    ask.answers["/labels"] = None
    tokens = Tokens(access="tok-dead", refresh="refresh-revoked")
    post = lambda url, body, headers: (400, "application/json",
                                       '{"error": "invalid_grant", '
                                       '"error_description": "Token has been expired or revoked."}')

    answer = json.loads(_bridge(ask=ask, tokens=tokens, post=post,
                                client={"client_id": "id-1"}).handle(_call_line("list_labels")))

    held = answer["result"]
    assert held["isError"]
    assert "Connect Google" in held["content"][0]["text"]  # the door, named
    assert "Invalid Credentials" not in held["content"][0]["text"]  # not Google's error page


def test_the_sign_in_can_be_checked_without_calling_a_single_tool(tmp_path):
    # A launch check proves a server STARTS; the Google bridge starts perfectly with a sign-in
    # Google has revoked, which is exactly how the failure hid - "(errands can reach: gmail,
    # google-calendar)" announced at startup, and the first real errand hours later answering
    # that the service was never set up. Trading the refresh token settles it in one request, and
    # a success leaves a fresh access token behind rather than throwing the work away.
    from excephalon.google_bridge import sign_in_fault

    tokens = Tokens(access="tok-stale", refresh="refresh-1")
    alive = lambda url, body, headers: (200, "application/json",
                                        json.dumps({"access_token": "tok-new"}))
    assert sign_in_fault(tokens=tokens, client={"client_id": "id-1"}, post=alive) == ""
    assert tokens.held["access_token"] == "tok-new"  # kept, so the next call is already warm

    revoked = lambda url, body, headers: (400, "application/json",
                                          '{"error": "invalid_grant", "error_description": '
                                          '"Token has been expired or revoked."}')
    fault = sign_in_fault(tokens=Tokens(access="tok", refresh="dead"),
                          client={"client_id": "id-1"}, post=revoked)
    assert "Connect Google" in fault

    assert "Connect Google" in sign_in_fault(tokens=Tokens(), client={}, post=revoked)


def test_a_sign_in_check_that_cannot_reach_google_is_not_a_fault(tmp_path):
    # Offline is not signed-out. Sending him to redo a sign-in that is perfectly good - because
    # the wifi was down when the app started - is the wrong fix confidently given.
    from excephalon.google_bridge import sign_in_fault

    def unreachable(url, body, headers):
        raise OSError("getaddrinfo failed")

    assert sign_in_fault(tokens=Tokens(access="tok", refresh="r"),
                         client={"client_id": "id-1"}, post=unreachable) == ""


def test_the_door_named_is_the_one_this_desk_actually_has():
    # The Mac has "Connect Google.command" and the hint named only that - on the Windows desk the
    # file does not exist, and for months there was no door at all: the sign-in expired and the
    # only way back was a command line he does not work from.
    from excephalon.google_bridge import connect_hint

    assert "Connect Google.bat" in connect_hint(windows=True)
    assert "Connect Google.command" in connect_hint(windows=False)


def test_googles_own_error_words_pass_through_as_the_answer():
    # "insufficient authentication scopes", "API has not been used in project..." - the denial's
    # words ARE the answer; a swallowed reason sends whoever asked off to fix the wrong thing.
    import io

    denied = urllib.error.HTTPError("url", 403, "Forbidden", {},
                                    io.BytesIO(b'{"error": {"message": "Gmail API has not been used in project 42"}}'))
    ask = FakeAsk(denies=denied)
    ask.answers["/labels"] = None  # the retry path must not be taken for a 403

    answer = json.loads(_bridge(ask=ask).handle(_call_line("list_labels")))

    held = answer["result"]
    assert held["isError"] and "has not been used in project" in held["content"][0]["text"]


def test_a_notification_owes_no_answer_line():
    assert _bridge().handle(json.dumps(
        {"jsonrpc": "2.0", "method": "notifications/initialized"})) is None


def test_a_draft_is_a_real_rfc822_message_saved_to_drafts_never_sent():
    ask = FakeAsk(answers={"/drafts": {"id": "d-9", "message": {"id": "m-1"}}})

    answer = json.loads(_bridge(ask=ask).handle(_call_line(
        "create_draft", {"to": "ada@example.com", "subject": "strings", "body": "two dozen"})))

    assert json.loads(answer["result"]["content"][0]["text"]) == {"draft_id": "d-9"}
    [(method, url, _, body)] = ask.calls
    assert method == "POST" and url.endswith("/drafts")
    import base64
    raw = base64.urlsafe_b64decode(body["message"]["raw"] + "==").decode("utf-8")
    assert "To: ada@example.com" in raw and "two dozen" in raw


def test_the_client_file_google_hands_out_is_read_as_it_comes(tmp_path):
    # The console's download wraps the values in {"installed": {...}} (or {"web": ...}); asking
    # him to rewrap them is asking a person to be a JSON parser. Drop the file in as it came.
    from excephalon.google_bridge import load_client

    downloaded = tmp_path / "client.json"
    downloaded.write_text(json.dumps({"installed": {
        "client_id": "id-9", "client_secret": "secret-9",
        "token_uri": "https://oauth2.googleapis.com/token"}}), encoding="utf-8")
    assert load_client(downloaded)["client_id"] == "id-9"

    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"client_id": "id-3"}), encoding="utf-8")
    assert load_client(flat)["client_id"] == "id-3"

    assert load_client(tmp_path / "absent.json") == {}


def test_tokens_survive_the_trip_to_disk(tmp_path):
    store = FileTokens(tmp_path / "google" / "tokens.json")
    assert store.read() == {}  # never signed in: empty, not an error

    store.write({"access_token": "a", "refresh_token": "r"})
    again = FileTokens(tmp_path / "google" / "tokens.json")
    assert again.read() == {"access_token": "a", "refresh_token": "r"}


def test_the_sign_in_url_asks_for_a_refresh_token_and_only_the_needed_scopes():
    # access_type=offline + prompt=consent is what makes Google hand over a REFRESH token - the
    # first sign-in without them yields an access token that dies in an hour with no way back,
    # and the bridge would ask him to sign in again every hour of his life.
    from excephalon.google_bridge import SCOPES, auth_url

    url = auth_url({"client_id": "id-7"}, port=8765)

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in url and "prompt=consent" in url
    assert "client_id=id-7" in url
    assert "127.0.0.1%3A8765" in url  # the loopback catcher the browser is sent back to
    for scope in SCOPES:
        from urllib.parse import quote
        assert quote(scope, safe="") in url


def test_the_code_google_sends_back_is_traded_for_tokens_and_written_down(tmp_path):
    from excephalon.google_bridge import exchange_code

    calls = []

    def post(url, body, headers):
        calls.append((url, body.decode("utf-8")))
        return (200, "application/json", json.dumps(
            {"access_token": "tok-1", "refresh_token": "refresh-1"}))

    store = FileTokens(tmp_path / "tokens.json")
    exchange_code("the-code", {"client_id": "id-7", "client_secret": "s-7"},
                  port=8765, tokens=store, post=post)

    assert store.read()["refresh_token"] == "refresh-1"
    [(url, payload)] = calls
    assert url == "https://oauth2.googleapis.com/token"
    assert "code=the-code" in payload and "grant_type=authorization_code" in payload
