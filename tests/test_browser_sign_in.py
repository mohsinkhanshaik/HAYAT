"""Browser sign-in for every platform: open a tab, sign in, come back to HAYAT, keep the session safely."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import app.accounts as accounts
from app.accounts import AccountManager
from tests.test_accounts import Broker, Provider, Response


class Mastodon:
    """A Mastodon server: registers apps, issues a token for the code, answers who the token belongs to."""

    def __init__(self):
        self.calls, self.apps = [], []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url == "https://social.example/api/v1/apps":
            self.apps.append(kwargs["data"])
            return Response(body={"client_id": "dyn-id", "client_secret": "dyn-secret"})
        if url == "https://social.example/oauth/token":
            form = kwargs["data"]
            assert form["client_id"] == "dyn-id" and form["client_secret"] == "dyn-secret" and form["code"] == "the-code"
            assert form["redirect_uri"] == self.apps[-1]["redirect_uris"] and form["scope"] == "read write:statuses"
            return Response(body={"access_token": "masto-token", "token_type": "Bearer", "scope": "read write:statuses"})
        if url == "https://social.example/api/v1/accounts/verify_credentials":
            auth = (kwargs.get("headers") or {}).get("Authorization", "")
            return Response(body={"acct": "owner", "username": "owner"}) if auth == "Bearer masto-token" else Response(401, {})
        raise AssertionError("unexpected " + url)


def approve(link):
    """The browser: the provider sends the tab back to HAYAT's address on this computer with a code."""
    query = parse_qs(urlsplit(link).query)
    redirect, state = query["redirect_uri"][0], query["state"][0]
    pages = []
    threading.Thread(target=lambda: pages.append(urllib.request.urlopen(f"{redirect}?code=the-code&state={state}",
                                                                        timeout=20).read().decode()), daemon=True).start()
    return pages


@pytest.fixture
def mastodon(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "public_server", lambda host, resolve=None: host)
    server, broker, opened = Mastodon(), Broker(), []
    manager = AccountManager(broker, tmp_path / "accounts.json", request=server, open_browser=lambda link: opened.append(approve(link)))
    return SimpleNamespace(server=server, broker=broker, manager=manager, opened=opened, path=tmp_path / "accounts.json")


def test_mastodon_signs_in_with_the_browser_and_no_setup(mastodon):
    status = mastodon.manager.status("mastodon")
    assert status["methods"][0] == {"key": "browser", "label": "Sign in with your browser", "ready": True}
    assert status["dynamic_app"] and not status["needs_app"]
    done = mastodon.manager.sign_in_browser("mastodon", timeout=10, server="https://Social.Example/")
    assert done["connected"] and done["connected_as"] == "@owner@social.example" and done["method"] == "browser"
    app = mastodon.server.apps[0]
    assert app["client_name"] == "HAYAT" and app["redirect_uris"].startswith("http://127.0.0.1:")  # registered by HAYAT itself
    deadline = time.monotonic() + 10
    while not mastodon.opened[0] and time.monotonic() < deadline:
        time.sleep(.02)
    assert "connected to Mastodon as @owner@social.example" in mastodon.opened[0][0]  # what the tab showed
    # Kept safely: the token in the credential store only, the record without it.
    assert json.loads(mastodon.broker.values["account.mastodon"])["access_token"] == "masto-token"
    assert "masto-token" not in mastodon.path.read_text(encoding="utf-8")


def test_the_session_survives_a_restart_and_is_checked_live(mastodon):
    mastodon.manager.sign_in_browser("mastodon", timeout=10, server="social.example")
    # A new HAYAT process: same credential store, same records file.
    again = AccountManager(mastodon.broker, mastodon.path, request=mastodon.server, open_browser=lambda link: None)
    assert again.status("mastodon")["connected"]
    checked = again.check("mastodon")
    assert checked["check_ok"] and checked["connected_as"] == "@owner@social.example"
    assert again.read("mastodon", "profile")["rows"]  # and it works


def test_mastodon_needs_the_server_first(mastodon):
    with pytest.raises(ValueError, match="server"):
        mastodon.manager.sign_in_browser("mastodon", timeout=1)


def test_a_builtin_app_makes_sign_in_one_click_and_an_own_app_wins(tmp_path):
    fake, broker = Provider(), Broker()
    manager = AccountManager(broker, tmp_path / "accounts.json", request=fake, open_browser=lambda link: None)
    manager._builtin_apps = {"github": {"client_id": "Iv1.builtin123"}}
    github = manager.status("github")
    assert github["builtin_app"] and not github["needs_app"]
    assert [(method["key"], method["ready"]) for method in github["methods"]] == [("device", True), ("browser", False), ("token", True)]
    manager.start_device("github")
    assert fake.calls[-1][2]["data"]["client_id"] == "Iv1.builtin123"
    manager.set_app("github", "Iv1.ownapp45678")
    manager.start_device("github")
    assert fake.calls[-1][2]["data"]["client_id"] == "Iv1.ownapp45678" and not manager.status("github")["builtin_app"]


def test_builtin_apps_are_read_from_the_project_file_and_the_local_one(tmp_path):
    local = tmp_path / "oauth-apps.json"
    local.write_text(json.dumps({"google": {"client_id": "g-id.apps.googleusercontent.com", "client_secret": "g-secret"},
                                 "empty": {"client_id": ""}}), encoding="utf-8")
    apps = accounts.read_builtin_apps(local)
    assert apps["google"] == {"client_id": "g-id.apps.googleusercontent.com", "client_secret": "g-secret"} and "empty" not in apps
    template = (accounts.Path(__file__).resolve().parents[1] / "config" / "oauth_apps.yaml").read_text(encoding="utf-8")
    assert "client_secret:" not in template.replace("client SECRET", "")  # secrets never go in the repository file


def test_google_signs_in_and_refreshes_with_a_builtin_desktop_app(tmp_path):
    fake, broker = Provider(), Broker()
    manager = AccountManager(broker, tmp_path / "accounts.json", request=fake,
                             open_browser=lambda link: (setattr(fake, "challenge", parse_qs(urlsplit(link).query)["code_challenge"][0]),
                                                        approve_google(link)))
    manager._builtin_apps = {"google": {"client_id": "1-abc.apps.googleusercontent.com", "client_secret": "google-secret"}}
    assert manager.status("google")["methods"][0]["ready"]
    done = manager.sign_in_browser("google", timeout=10)
    assert done["connected_as"] == "owner@gmail.com"
    value = json.loads(broker.values["account.google"])
    value["expires_at"] = time.time()  # expired: the next use refreshes with the built-in app's secret
    broker.values["account.google"] = json.dumps(value)
    assert manager._token("google") == "g-token-2"


def approve_google(link):
    query = parse_qs(urlsplit(link).query)
    threading.Thread(target=lambda: urllib.request.urlopen(f"{query['redirect_uri'][0]}?code=auth-code&state={query['state'][0]}",
                                                           timeout=20).read(), daemon=True).start()


def test_the_window_sets_up_then_signs_in_with_one_press(shared_tk, tmp_path, monkeypatch):
    import app.gui_accounts as window_module
    from app.safety.confirmations import ConfirmationGate
    from app.gui_theme import Palette
    monkeypatch.setattr(window_module.webbrowser, "open", lambda url: None)
    fake, broker = Provider(), Broker()
    started = []
    manager = AccountManager(broker, tmp_path / "accounts.json", request=fake, open_browser=lambda link: None)
    manager.sign_in_browser = lambda key, timeout=300, server="": started.append((key, server)) or manager.status(key)

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(accounts=manager, confirmations=ConfirmationGate(), email=None,
                                                       secret_broker=broker)
    window = window_module.AccountsCenter(gui)

    def wait(ready, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            shared_tk.update()
            if ready():
                return True
            time.sleep(.02)
        return ready()

    def buttons(widget):
        for child in widget.winfo_children():
            if isinstance(child, window_module.ttk.Button):
                yield child
            yield from buttons(child)
    try:
        window.select("google")
        browser = next(button for button in buttons(window.connect_tab) if button.cget("text") == "Set up, then sign in")
        assert "disabled" not in browser.state()  # never a dead button
        browser.invoke()
        assert started == [] and "one-time app registration" in window.connect_note.cget("text")
        window.client_id.set("1234567890-abc.apps.googleusercontent.com")
        window.client_secret.set("google-secret")
        window.save_app()
        assert wait(lambda: started == [("google", "")])  # the sign-in opened by itself after Save app
        window.select("mastodon")
        assert any(button.cget("text") == "Open the sign-in page" for button in buttons(window.connect_tab))
        window.token_values["server"].set("social.example")
        window.start_browser()
        assert wait(lambda: started[-1] == ("mastodon", "social.example"))
    finally:
        window.close()
