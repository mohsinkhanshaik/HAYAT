"""Publish, register and submit only after the owner checks and confirms; then verify."""
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from app.safety.confirmations import ConfirmationGate


def _gate(tmp_path, executor=None):
    gate = ConfirmationGate(log_path=tmp_path / "actions.jsonl")
    calls = []

    def run(secrets):
        calls.append(dict(secrets))
        return {"verdict": "looks sent", "final_url": "https://example.com/thanks"}
    summary = gate.propose("register", "Register an account on example.com", "https://example.com/join",
                           [("Email", "me@example.com"), ("Password", "(you type it in the confirmation window)")],
                           executor or run, secrets_needed=["Password"], notes=["Nothing sent yet."])
    return gate, summary, calls


def test_nothing_runs_until_the_owner_confirms_once_with_the_password(tmp_path):
    gate, summary, calls = _gate(tmp_path)
    assert summary["status"] == "awaiting confirmation" and "Nothing has been sent yet" in summary["how_to_confirm"]
    assert calls == []
    with pytest.raises(PermissionError):
        gate.confirm(summary["action_id"], by_owner=False, secrets={"Password": "x"})
    with pytest.raises(ValueError, match="never taken from chat"):
        gate.confirm(summary["action_id"], by_owner=True)
    result = gate.confirm(summary["action_id"], by_owner=True, secrets={"Password": "s3cret"})
    assert calls == [{"Password": "s3cret"}] and result["status"] == "done, please verify"
    with pytest.raises(ValueError, match="only be confirmed once"):
        gate.confirm(summary["action_id"], by_owner=True, secrets={"Password": "s3cret"})
    assert gate.verify(summary["action_id"], True)["status"] == "verified by you"
    log = (tmp_path / "actions.jsonl").read_text(encoding="utf-8")
    assert "s3cret" not in log and "me@example.com" not in log  # field names only, never values or secrets
    assert [json.loads(line)["event"] for line in log.splitlines()] == ["proposed", "confirmed", "executed",
                                                                         "verified by you"]


def test_cancel_expire_failures_and_listeners(tmp_path, monkeypatch):
    seen = []
    gate = ConfirmationGate()
    gate.listeners.append(lambda action: seen.append(action.id))
    first = gate.propose("form", "Contact form", "https://example.com", [("Name", "M")], lambda _s: {})
    assert seen == [first["action_id"]]
    gate.cancel(first["action_id"])
    with pytest.raises(ValueError):
        gate.confirm(first["action_id"], by_owner=True)
    second = gate.propose("form", "Contact form", "https://example.com", [("Name", "M")], lambda _s: {})
    monkeypatch.setattr(ConfirmationGate, "TTL_SECONDS", -1)
    assert gate.pending() == []
    with pytest.raises(ValueError, match="expired"):
        gate.confirm(second["action_id"], by_owner=True)
    monkeypatch.setattr(ConfirmationGate, "TTL_SECONDS", 600)

    def boom(_secrets):
        raise RuntimeError("the page changed")
    third = gate.propose("form", "Contact form", "https://example.com", [("Name", "M")], boom)
    failed = gate.confirm(third["action_id"], by_owner=True)
    assert failed["status"] == "failed" and "page changed" in failed["result"]["error"]
    with pytest.raises(ValueError, match="nothing to verify"):
        gate.verify(third["action_id"], True)
    with pytest.raises(ValueError):
        gate.propose("purchase", "Buy", "x", [], lambda _s: {})


# ------------------------------------------------------------------ the real browser

FORM = """<html><body><h1>Join</h1>
<form onsubmit="event.preventDefault(); document.body.innerHTML='<h1>Thank you, account created</h1>'; history.pushState({}, '', '#done')">
<label for=n>Full name</label><input id=n name=name>
<label for=e>Email</label><input id=e name=email type=email>
<label for=p>Password</label><input id=p name=password type=password>
<label><input type=checkbox name=terms> I agree to the terms</label>
<button type=submit>Sign up</button></form>
<form role=search><input type=search name=q><button>Search</button></form></body></html>"""


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    pytest.importorskip("playwright")
    from app.safety.permissions import PermissionManager
    from app.tools.browser import BrowserTools

    root = tmp_path_factory.mktemp("browser")
    tools = BrowserTools(PermissionManager({"default": "ALLOW"}, root))
    try:
        tools.navigate("about:blank")
    except Exception as exc:  # pragma: no cover - no browser installed
        pytest.skip(f"Chromium is not available: {exc}")
    tools.root = root
    yield tools
    tools.close()


def _load(browser, html=FORM):
    browser.navigate("about:blank")
    browser.host.run(lambda: browser._page().set_content(html))


def test_plain_browser_tools_refuse_to_submit_but_still_search(browser):
    from app.tools.browser import SubmissionNeedsConfirmation

    _load(browser)
    with pytest.raises(SubmissionNeedsConfirmation, match="after you confirm"):
        browser.click("button[type=submit]")
    with pytest.raises(SubmissionNeedsConfirmation):
        browser.click_by_role("button", "Sign up")
    with pytest.raises(SubmissionNeedsConfirmation):
        browser.type_text("#e", "me@example.com", press_enter=True)
    with pytest.raises(SubmissionNeedsConfirmation):
        browser.press_key("Enter", "#n")
    assert browser.host.run(lambda: browser._page().locator("h1").inner_text()) == "Join"  # nothing was sent
    browser.click("form[role=search] button")  # searching is reading


def test_prepare_never_fills_a_password_and_submit_checks_nothing_changed(browser):
    _load(browser)
    evidence = str(browser.root / "evidence")
    plan = browser.prepare_form("", {"Full name": "Mohsin Khan", "Email": "me@example.com", "Password": "from-model",
                                     "I agree to the terms": "yes"}, "", evidence)
    assert plan["kind"] == "register" and plan["secrets"] == ["Password"] and plan["evidence"].endswith("-before.png")
    assert browser.host.run(lambda: browser._page().locator("#p").input_value()) == ""
    assert [(f["label"], f["value"]) for f in plan["fields"] if not f["secret"]] == [
        ("Full name", "Mohsin Khan"), ("Email", "me@example.com"), ("I agree to the terms", "ticked")]
    # Someone (or a script on the page) changes a value after the owner checked it: nothing is sent.
    browser.host.run(lambda: browser._page().locator("#e").fill("attacker@example.com"))
    with pytest.raises(RuntimeError, match="changed since you checked"):
        browser.submit_prepared(plan, {"Password": "pw"}, evidence)
    browser.host.run(lambda: browser._page().locator("#e").fill("me@example.com"))
    result = browser.submit_prepared(plan, {"Password": "pw"}, evidence)
    assert result["verdict"] == "looks sent" and "thank you" in result["success_words"]
    assert result["after_screenshot"].endswith("-after.png")


@pytest.mark.parametrize("html, message", [
    ("<form><label for=c>Card number</label><input id=c name=cardnumber><button>Pay now</button></form>", "payment"),
    ("<form><label for=e>Email</label><input id=e><div class='g-recaptcha' data-sitekey=x></div>"
     "<button>Send</button></form>", "CAPTCHA"),
])
def test_payments_and_captchas_are_refused(browser, html, message):
    _load(browser, f"<html><body>{html}</body></html>")
    with pytest.raises(PermissionError, match=message):
        browser.prepare_form("", {"Card number" if "Card" in html else "Email": "4111"}, "")


# ------------------------------------------------------------------ preparing and publishing

def test_form_preparer_shows_every_value_and_hides_secrets(tmp_path):
    from app.tools.actions import ActionPreparer

    plan = {"token": "t", "url": "https://shop.example/join", "title": "Join", "kind": "register",
            "fields": [{"mark": "t-0", "label": "Email", "secret": False, "value": "me@example.com"},
                       {"mark": "t-1", "label": "Password", "secret": True, "value": ""}],
            "secrets": ["Password"], "submit_label": "sign up", "evidence": ""}
    sent = []
    fake = SimpleNamespace(prepare_form=lambda *a: plan, submit_prepared=lambda p, s, e: sent.append(dict(s)) or {"verdict": "ok"})
    gate = ConfirmationGate()
    summary = ActionPreparer(gate, fake).form("https://shop.example/join", {"Email": "me@example.com"})
    values = {item["field"]: item["value"] for item in summary["details"]}
    assert values["Email"] == "me@example.com" and "you type it" in values["Password"]
    assert values["Button HAYAT will press"] == "sign up" and summary["you_will_type"] == ["Password"]
    assert summary["title"] == "Register an account or a business on shop.example"
    gate.confirm(summary["action_id"], by_owner=True, secrets={"Password": "pw"})
    assert sent == [{"Password": "pw"}]


def _netlify(tmp_path, html="<html><body>Hello</body></html>", live=None):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("content-type", "")))
        assert request.headers.get("authorization", "").startswith("Bearer tok") or request.url.host == "studio.netlify.app"
        if request.url.path == "/api/v1/sites" and request.method == "POST":
            return httpx.Response(201, json={"id": "site1", "url": "http://studio.netlify.app"})
        if request.url.path.endswith("/deploys") and request.method == "POST":
            return httpx.Response(200, json={"id": "dep1", "state": "processing"})
        if request.url.path == "/api/v1/deploys/dep1":
            return httpx.Response(200, json={"id": "dep1", "state": "ready"})
        if request.url.path == "/api/v1/sites/site1":
            return httpx.Response(200, json={"id": "site1", "ssl_url": "https://studio.netlify.app"})
        if request.url.host == "studio.netlify.app":
            return httpx.Response(200, text=live if live is not None else html)
        return httpx.Response(404)

    class Secrets:
        def list(self):
            return [{"name": "netlify_token"}]

        def issue_lease(self, *a, **k):
            return "lease"

        def redeem(self, *a):
            return "tok" + "x" * 30

    from app.tools.publish import NetlifyPublisher

    publisher = NetlifyPublisher(Secrets(), client=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
                                 poll_seconds=0)
    folder = tmp_path / "site"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text(html, encoding="utf-8")
    return publisher, folder, calls


def test_publishing_deploys_once_and_checks_the_live_page(tmp_path):
    publisher, folder, calls = _netlify(tmp_path)
    result = publisher.publish(folder, "Studio")
    assert result["matches"] and result["site_url"] == "https://studio.netlify.app"
    assert result["verdict"] == "live and matches your file"
    assert ("POST", "/api/v1/sites/site1/deploys", "application/zip") in calls
    publisher, folder, _ = _netlify(tmp_path / "b", live="<html>someone else's page</html>")
    assert not publisher.publish(folder, "Studio")["matches"]


def test_publishing_needs_setup_and_a_real_token(tmp_path):
    from app.tools.publish import NetlifyPublisher

    publisher = NetlifyPublisher(None)
    with pytest.raises(PermissionError, match="Publishing setup"):
        publisher.publish(tmp_path, "x") if (tmp_path / "index.html").write_text("x") or True else None
    stored = {}
    secrets = SimpleNamespace(store=lambda name, value, scopes: stored.update({name: (value, scopes)}), list=lambda: [])
    with pytest.raises(ValueError, match="personal access token"):
        NetlifyPublisher(secrets).store_token("short")
    NetlifyPublisher(secrets).store_token("nfp_" + "a" * 36)
    assert stored["netlify_token"][1] == ["publish:netlify"]


def test_publish_preparer_shows_the_files_and_remembers_the_site(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.actions import ActionPreparer
    from app.tools.business import BusinessLedger
    from app.tools.launch import Launchpad

    ledger = BusinessLedger(tmp_path / "b.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
    launchpad = Launchpad(ledger)
    launchpad.start("Studio", "Logos")
    publisher, _folder, _calls = _netlify(tmp_path)
    gate = ConfirmationGate()
    summary = ActionPreparer(gate, None, launchpad, publisher).publish()
    values = {item["field"]: item["value"] for item in summary["details"]}
    assert "index.html" in values["Files"] and "new address" in values["Address"]
    assert any("Anyone on the internet" in note for note in summary["notes"])
    # The preview was made from the real website file; publishing serves that same file.
    publisher_html = (tmp_path / "out" / "site" / "studio" / "index.html").read_text(encoding="utf-8")
    publisher.client = _netlify(tmp_path / "live", html=publisher_html)[0].client
    result = gate.confirm(summary["action_id"], by_owner=True)
    assert result["result"]["matches"] and launchpad.published()["site"]["url"] == "https://studio.netlify.app"
    second = ActionPreparer(gate, None, launchpad, publisher).publish()
    assert "(update)" in {item["field"]: item["value"] for item in second["details"]}["Address"]


# ------------------------------------------------------------------ chat decisions

def test_chat_confirms_only_from_this_computer_and_never_takes_passwords(tmp_path):
    from app.assistant import AssistantService
    from app.execution import execution_scope

    gate = ConfirmationGate()
    service = SimpleNamespace(runtime=SimpleNamespace(confirmations=gate))
    decide = AssistantService._owner_action_decision.__get__(service)
    ran = []
    plain = gate.propose("form", "Contact form on example.com", "https://example.com", [("Name", "M")],
                         lambda _s: ran.append(1) or {"verdict": "looks sent", "final_url": "https://example.com/ok",
                                                      "success_words": ["thank you"]})
    with execution_scope(principal="REMOTE_OWNER"):
        assert "not from a remote device" in decide("CONFIRM_ACTION", {"action_id": plain["action_id"]})
    assert ran == []
    reply = decide("CONFIRM_ACTION", {"action_id": plain["action_id"]})
    assert ran == [1] and "looks sent" in reply.lower() and "looks right" in reply
    assert "verified" in decide("VERIFY_ACTION", {"action_id": plain["action_id"], "ok": True})
    secret = gate.propose("login", "Sign in", "https://example.com", [("Email", "m")], lambda _s: {},
                          secrets_needed=["Password"])
    assert "never taken from chat" in decide("CONFIRM_ACTION", {"action_id": secret["action_id"]})
    assert "confirm action" in decide("PENDING_ACTIONS", {})
    assert "Nothing was sent" in decide("CANCEL_ACTION", {"action_id": secret["action_id"]})


def test_no_tool_can_confirm_and_submissions_are_routed_to_preparation(tmp_path):
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        names = runtime.registry.names()
        assert {"form_fill_prepare", "publish_website_prepare", "pending_actions"} <= set(names)
        assert not [name for name in names if "confirm" in name and name != "pending_actions"]
        assert runtime.confirmations is not None and runtime.publisher is not None
        from app.config import PROJECT_ROOT

        # A test runtime never writes the owner's real audit log.
        assert (PROJECT_ROOT / "data") not in runtime.confirmations.log_path.resolve().parents
    finally:
        runtime.close()


# ------------------------------------------------------------------ the confirmation window

def test_the_window_needs_the_tick_and_the_password_then_shows_proof(shared_tk, tmp_path):
    from app.gui_confirm import ConfirmDialog
    from app.gui_theme import Palette

    gate, summary, calls = _gate(tmp_path)

    class GUI(Palette):
        root = shared_tk
        runtime = SimpleNamespace(confirmations=gate)
        notes = []

        def _append(self, tag, text):
            self.notes.append(text)

    dialog = ConfirmDialog(GUI(), summary["action_id"])
    try:
        dialog.withdraw()
        assert str(dialog.confirm_button.cget("state")) == "disabled"
        dialog.checked.set(True)
        dialog._sync()
        dialog.confirm()
        assert "Type Password first" in dialog.status_var.get() and calls == []
        dialog.secret_vars["Password"].set("pw")
        dialog.confirm()
        assert dialog.secret_vars["Password"].get() == ""  # cleared from the window at once
        until = time.monotonic() + 5
        while dialog._outcome is None and time.monotonic() < until:
            dialog.update()
            time.sleep(.02)
        dialog.update()
        dialog._poll()
        assert calls == [{"Password": "pw"}]
        assert "Please check it yourself" in dialog.status_var.get()
        dialog.verify(True)
        assert gate.get(summary["action_id"]).status == "verified by you"
        assert any("verified by you" in note for note in GUI.notes)
    finally:
        if dialog.winfo_exists():
            dialog.destroy()
