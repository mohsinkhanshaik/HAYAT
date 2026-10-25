"""Accounts & sign-in: the three ways to sign in, token care, reads, confirmed writes, chat and the window."""
import base64
import hashlib
import json
import threading
import time
import urllib.request
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.accounts import PROVIDERS, AccountManager, AccountTools, Loopback, register_account_tools, when
from app.accounts_chat import TOOL_NAMES, account_request, direct_plan, report
from app.safety.confirmations import ConfirmationGate


class Broker:
    """The secret broker's contract, in memory: values only through single-use, scoped leases."""

    def __init__(self):
        self.values, self.scopes, self.leases = {}, {}, {}

    def store(self, name, value, *, scopes, expires_at=None):
        self.values[name], self.scopes[name] = value, set(scopes)
        return {"name": name, "stored": True}

    def issue_lease(self, name, scope, ttl_seconds=30):
        if name not in self.values:
            raise KeyError(name)
        if scope not in self.scopes[name]:
            raise PermissionError("wrong scope")
        token = f"lease-{len(self.leases)}"
        self.leases[token] = (name, scope)
        return token

    def redeem(self, token, scope):
        name, granted = self.leases.pop(token)
        assert granted == scope
        return self.values[name]

    def delete(self, name):
        return self.values.pop(name, None) is not None

    def list(self):
        return [{"name": name} for name in self.values]

    def status(self):
        return {"backend": "KeyringBackend", "stored": len(self.values)}


class Response:
    def __init__(self, status=200, body=None):
        self.status_code, self.body = status, body if body is not None else {}
        self.content = json.dumps(self.body).encode()

    def json(self):
        return self.body


class Provider:
    """Fake provider endpoints; records every request."""

    def __init__(self):
        self.calls, self.device_answers, self.issued = [], [], {}
        self.challenge = None

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        auth = (kwargs.get("headers") or {}).get("Authorization", "")
        if url == "https://github.com/login/device/code":
            return Response(body={"device_code": "dev-123", "user_code": "WDJB-MJHT", "verification_uri": "https://github.com/login/device",
                                  "interval": 5, "expires_in": 900})
        if url == "https://github.com/login/oauth/access_token":
            return Response(body=self.device_answers.pop(0))
        if url == "https://api.github.com/user":
            return Response(401, {"message": "Bad credentials"}) if auth != "Bearer gh-token" else Response(body={"login": "owner", "name": "Owner"})
        if url == "https://api.github.com/user/repos":
            return Response(body=[{"full_name": "owner/hayat", "private": True, "language": "Python", "updated_at": "2026-10-11T10:00:00Z",
                                   "html_url": "https://github.com/owner/hayat", "description": "Assistant"}])
        if url == "https://api.github.com/repos/owner/hayat/issues":
            return Response(201, {"number": 7, "html_url": "https://github.com/owner/hayat/issues/7"})
        if url == "https://oauth2.googleapis.com/token":
            form = kwargs["data"]
            if form["grant_type"] == "authorization_code":
                assert form["client_secret"] == "google-secret"
                digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                assert digest == self.challenge and form["code"] == "auth-code"
                return Response(body={"access_token": "g-token", "refresh_token": "g-refresh", "expires_in": 3600})
            assert form["grant_type"] == "refresh_token" and form["client_secret"] == "google-secret"
            return Response(body={"access_token": "g-token-2", "expires_in": 3600})
        if url == "https://openidconnect.googleapis.com/v1/userinfo":
            assert auth in ("Bearer g-token", "Bearer g-token-2")
            return Response(body={"email": "owner@gmail.com", "name": "Owner"})
        if url == "https://gmail.googleapis.com/gmail/v1/users/me/messages":
            return Response(body={"messages": [{"id": "m1"}]})
        if url == "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1":
            return Response(body={"snippet": "See you at 3", "labelIds": ["UNREAD", "INBOX"], "payload": {"headers": [
                {"name": "Subject", "value": "Meeting"}, {"name": "From", "value": "Sam <sam@example.com>"}, {"name": "Date", "value": "Sat, 11 Oct"}]}})
        if url == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send":
            self.issued["sent"] = kwargs["json"]["raw"]
            return Response(body={"id": "sent-1"})
        if url == "https://oauth2.googleapis.com/revoke":
            self.issued["revoked"] = kwargs["data"]["token"]
            return Response()
        if url == "https://api.notion.com/v1/users/me":
            assert (kwargs.get("headers") or {}).get("Notion-Version")
            return Response(body={"name": "HAYAT reader", "bot": {"workspace_name": "Home"}}) if auth == "Bearer secret_notion" else Response(401, {})
        if url == "https://slack.com/api/conversations.list":
            return Response(body={"ok": True, "channels": [{"name": "general", "num_members": 4, "is_member": True}]})
        raise AssertionError("unexpected " + url)


@pytest.fixture
def setup(tmp_path):
    fake, broker = Provider(), Broker()
    opened = []
    manager = AccountManager(broker, tmp_path / "accounts.json", request=fake, open_browser=opened.append)
    return SimpleNamespace(fake=fake, broker=broker, manager=manager, opened=opened, path=tmp_path / "accounts.json")


def test_every_provider_lists_its_ways_to_sign_in_and_what_it_can_do(setup):
    status = setup.manager.status()
    assert [row["key"] for row in status["accounts"]] == ["google", "microsoft", "github", "linkedin", "notion", "slack", "bluesky",
                                                          "mastodon", "reddit", "x", "telegram", "discord", "facebook", "threads"]
    assert status["connected"] == []
    github = setup.manager.status("github")
    assert [(method["key"], method["ready"]) for method in github["methods"]] == [("device", False), ("browser", False), ("token", True)]
    assert github["needs_app"] and github["app_steps"] and "Enable Device Flow" in " ".join(github["app_steps"])
    assert [op["key"] for op in github["operations"] if op["write"]] == ["create_issue"]
    assert not setup.manager.status("notion")["needs_app"] and "never sees your password" in status["basis"]
    with pytest.raises(ValueError):
        setup.manager.set_app("github", "has spaces in it")
    setup.manager.set_app("github", "Iv1.abcdef123456")
    assert setup.manager.status("github")["methods"][0]["ready"] and not setup.manager.status("github")["methods"][1]["ready"]  # needs the secret
    with pytest.raises(ValueError):
        setup.manager.start_device("google")  # Google does not offer codes for mail scopes


def test_sign_in_with_a_code_waits_slows_down_and_connects(setup):
    setup.manager.set_app("github", "Iv1.abcdef123456")
    started = setup.manager.start_device("github")
    assert started["user_code"] == "WDJB-MJHT" and started["verification_uri"] == "https://github.com/login/device"
    request = setup.fake.calls[-1]
    assert request[2]["data"] == {"client_id": "Iv1.abcdef123456", "scope": "read:user repo notifications"}
    setup.fake.device_answers += [{"error": "authorization_pending"}, {"error": "slow_down"},
                                  {"access_token": "gh-token", "token_type": "bearer", "scope": "repo"}]
    assert setup.manager.poll_device("github") == {"state": "waiting", "interval": 5}
    assert setup.manager.poll_device("github") == {"state": "waiting", "interval": 10}
    done = setup.manager.poll_device("github")
    assert done["state"] == "connected" and done["connected_as"] == "owner" and done["method"] == "device"
    stored = json.loads(setup.broker.values["account.github"])
    assert stored["access_token"] == "gh-token" and setup.broker.scopes["account.github"] == {"account:github"}
    assert "gh-token" not in setup.path.read_text(encoding="utf-8")  # the record holds no secret
    setup.manager.start_device("github")
    setup.fake.device_answers.append({"error": "access_denied"})
    with pytest.raises(PermissionError, match="declined"):
        setup.manager.poll_device("github")


def test_sign_in_with_the_browser_uses_pkce_state_and_a_loopback(setup):
    setup.manager.set_app("google", "1234567890-abc.apps.googleusercontent.com", "google-secret")

    def browser(url):
        query = parse_qs(urlsplit(url).query)
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?") and query["code_challenge_method"] == ["S256"]
        assert query["access_type"] == ["offline"] and "gmail.send" in query["scope"][0]
        setup.fake.challenge = query["code_challenge"][0]
        redirect = query["redirect_uri"][0]
        assert redirect.startswith("http://127.0.0.1:")
        threading.Thread(target=lambda: urllib.request.urlopen(f"{redirect}?code=auth-code&state={query['state'][0]}", timeout=5).read(),
                         daemon=True).start()
    setup.manager.open_browser = browser
    done = setup.manager.sign_in_browser("google", timeout=10)
    assert done["connected"] and done["connected_as"] == "owner@gmail.com" and done["method"] == "browser"
    assert json.loads(setup.broker.values["account.google"])["refresh_token"] == "g-refresh"


def test_the_loopback_ignores_a_wrong_state():
    listener = Loopback("right-state")
    try:
        threading.Thread(target=lambda: urllib.request.urlopen(f"http://127.0.0.1:{listener.port}/?code=x&state=forged", timeout=5).read(),
                         daemon=True).start()
        with pytest.raises(PermissionError, match="wrong state"):
            listener.wait(5)
    finally:
        listener.close()


def test_a_pasted_token_is_checked_before_it_is_kept(setup):
    with pytest.raises(RuntimeError):
        setup.manager.save_token("notion", "secret_wrong_value")
    assert "account.notion" not in setup.broker.values and not setup.manager.status("notion")["connected"]
    done = setup.manager.save_token("notion", "secret_notion")
    assert done["connected_as"] == "HAYAT reader" and done["method"] == "token"
    with pytest.raises(ValueError):
        setup.manager.save_token("google", "a-google-token-value")  # Google is signed into, not pasted


def test_tokens_refresh_before_they_expire_and_sign_out_revokes(setup):
    setup.manager.set_app("google", "1234567890-abc.apps.googleusercontent.com", "google-secret")
    setup.broker.store("account.google", json.dumps({"access_token": "g-token", "refresh_token": "g-refresh", "expires_at": time.time() + 10,
                                                     "method": "browser"}), scopes=["account:google"])
    records = json.loads(setup.path.read_text(encoding="utf-8"))
    records["google"].update(connected=True, connected_as="owner@gmail.com")
    setup.path.write_text(json.dumps(records), encoding="utf-8")
    rows = setup.manager.read("google", "profile")["rows"]
    assert rows == [{"title": "Owner", "detail": "owner@gmail.com"}]
    assert json.loads(setup.broker.values["account.google"])["access_token"] == "g-token-2"
    setup.manager.sign_out("google")
    assert setup.fake.issued["revoked"] == "g-refresh" and "account.google" not in setup.broker.values
    assert not setup.manager.status("google")["connected"]
    with pytest.raises(PermissionError, match="not connected"):
        setup.manager.read("google", "gmail_inbox")


def connect(setup, key, token):
    setup.broker.store(f"account.{key}", json.dumps({"access_token": token, "method": "token"}), scopes=[f"account:{key}"])
    records = json.loads(setup.path.read_text(encoding="utf-8")) if setup.path.exists() else {}
    records[key] = {"connected": True, "connected_as": "owner", "method": "token", "connected_at": "2026-10-11T00:00:00+00:00"}
    setup.path.write_text(json.dumps(records), encoding="utf-8")


def test_reads_come_back_as_plain_rows(setup):
    connect(setup, "google", "g-token")
    connect(setup, "github", "gh-token")
    connect(setup, "slack", "xoxb-token")
    inbox = setup.manager.read("google", "gmail_inbox")["rows"]
    assert inbox == [{"title": "Meeting", "detail": "Sam <sam@example.com>", "when": "Sat, 11 Oct", "extra": "See you at 3", "unread": True}]
    assert setup.manager.read("github", "repos")["rows"][0]["title"] == "owner/hayat"
    assert setup.manager.read("slack", "channels")["rows"][0]["title"] == "#general"
    with pytest.raises(PermissionError):
        setup.manager.read("google", "gmail_send")  # a write is never a read


def test_sending_is_prepared_and_happens_only_after_the_owner_confirms(setup):
    connect(setup, "google", "g-token")
    connect(setup, "github", "gh-token")
    gate = ConfirmationGate()
    tools = AccountTools(setup.manager, gate)
    proposal = tools.prepare("google", "gmail_send", {"to": "sam@example.com", "subject": "Hello", "body": "See you at 3."})
    assert proposal["kind"] == "account" and {"field": "To", "value": "sam@example.com"} in proposal["details"]
    assert "sent" not in setup.fake.issued and "Nothing has been sent yet" in proposal["how_to_confirm"]
    with pytest.raises(PermissionError):
        gate.confirm(proposal["action_id"], by_owner=False)
    done = gate.confirm(proposal["action_id"], by_owner=True)
    raw = base64.urlsafe_b64decode(setup.fake.issued["sent"]).decode()
    assert done["status"] == "done, please verify" and "To: sam@example.com" in raw and "Subject: Hello" in raw
    issue = tools.prepare("github", "create_issue", {"repo": "owner/hayat", "title": "Bug"})
    assert gate.confirm(issue["action_id"], by_owner=True)["result"]["number"] == 7
    with pytest.raises(ValueError, match="Fill in"):
        tools.prepare("github", "create_issue", {"repo": "owner/hayat"})
    with pytest.raises(ValueError):
        tools.prepare("github", "repos", {})


def test_times_are_read_as_local_and_tools_are_registered(setup, tmp_path):
    assert when("2026-10-14 15:00").startswith("2026-10-14T15:00:00")
    with pytest.raises(ValueError):
        when("next tuesday")
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_account_tools(registry, setup.manager, ConfirmationGate())
    assert {"account_status", "account_read", "account_prepare"} <= set(registry.names())


def test_chat_routes_and_replies():
    assert account_request("check my gmail") == {"tool": "account_read", "arguments": {"provider": "google", "operation": "gmail_inbox"}}
    assert account_request("my outlook calendar")["arguments"] == {"provider": "microsoft", "operation": "calendar_upcoming"}
    assert account_request("search notion for invoices")["arguments"]["params"] == {"query": "invoices"}
    assert account_request("connect my gmail account") == {"tool": "account_status", "arguments": {"provider": "google"}}
    assert account_request("which accounts are connected?")["tool"] == "account_status"
    for text in ("what is gmail?", "send an email to bob", "check my email"):
        assert account_request(text) is None
    assert direct_plan("my github notifications", TOOL_NAMES)["tool"] == "account_read"
    rows = {"read_view": True, "name": "Google", "label": "Latest Gmail messages", "rows": [{"title": "Meeting", "detail": "Sam", "unread": True}]}
    text = report([{"tool": "account_read", "result": {"success": True, "output": rows}, "verification": {"passed": True}}])
    assert text.startswith("Google · Latest Gmail messages: 1 item.") and "● Meeting · Sam" in text
    status = AccountManager(None, "unused.json").status("github")
    text = report([{"tool": "account_status", "result": {"success": True, "output": status}, "verification": {"passed": True}}])
    assert "GitHub is not connected yet" in text and "never sees your password" in text


def test_the_window_connects_with_a_code_reads_and_prepares(shared_tk, setup, monkeypatch):
    import app.gui_accounts as window_module
    from app.gui_theme import Palette
    opened = []
    monkeypatch.setattr(window_module.webbrowser, "open", opened.append)
    # Assert the copied value without competing for the desktop's global clipboard.
    # Focus/clipboard ownership can change while the full GUI suite is running.
    clipboard = []
    monkeypatch.setattr(window_module.AccountsCenter, 'clipboard_clear', lambda _self: clipboard.clear())
    monkeypatch.setattr(window_module.AccountsCenter, 'clipboard_append', lambda _self, text: clipboard.append(text))
    gate = ConfirmationGate()

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(accounts=setup.manager, confirmations=gate, email=None, secret_broker=setup.broker)
    window = window_module.AccountsCenter(gui)

    def wait(ready, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            shared_tk.update()
            if ready():
                return True
            time.sleep(.02)
        return ready()
    try:
        assert wait(lambda: window.winfo_ismapped()), 'Accounts window did not map'
        assert set(window.card_widgets) == set(PROVIDERS) and f"0 of {len(PROVIDERS)}" in window.status.get()
        window.select("github")
        window.client_id.set("Iv1.abcdef123456")
        window.save_app()
        assert window.row()["client_id_set"]
        window.start_device()
        assert wait(lambda: window.device and 'device' not in window._busy and opened and clipboard)
        assert opened == ["https://github.com/login/device"] and clipboard == ["WDJB-MJHT"]
        setup.fake.device_answers += [{"access_token": "gh-token"}]
        window.next_poll = 0
        assert wait(lambda: window.row()["connected"] and window.device is None and 'poll' not in window._busy)
        assert "owner" in window.card_widgets["github"]["state"].cget("text") and window.device is None
        window.tabs.select(1)
        window.read("repos")
        assert wait(lambda: window.results.get_children())
        assert window.results.item("0", "text") == "owner/hayat"
        window.results.selection_set("0")
        window.open_result()
        assert opened[-1] == "https://github.com/owner/hayat"
        window.write_op.set("Open an issue")
        window.build_form()
        window.field_vars["github:create_issue:repo"].set("owner/hayat")
        window.field_vars["github:create_issue:title"].set("Bug")
        window.prepare()
        assert "Prepared as action #1" in window.write_note.cget("text") and gate.pending()[0]["title"] == "Open an issue (GitHub)"
        assert not any(url.endswith("/issues") for _method, url, _kwargs in setup.fake.calls)  # nothing sent before confirming
    finally:
        window.close()
