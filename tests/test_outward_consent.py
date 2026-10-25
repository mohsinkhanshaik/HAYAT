from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.assistant import AssistantService
from app.intent.models import IntentKind, RequestIntent
from app.intent.router import IntentRouter
from app.safety.authority import ActionRequest, AuthorityManager
from app.session import SessionState


def _service(authority):
    service = AssistantService.__new__(AssistantService)
    service.runtime = SimpleNamespace(authority=authority, session=SimpleNamespace(session_id="s1"))
    return service


def _open(authority, said: str, *, goal: str = "do it"):
    intent = RequestIntent(IntentKind.BROWSER, .9, goal, requires_tools=True)
    return _service(authority)._open_authority_envelope(intent, SimpleNamespace(turn_id="turn-1", raw_input=said))


def _allowed(authority, category: str, turn_id: str = "turn-1") -> bool:
    return authority.evaluate(ActionRequest(tool=category, category=category, turn_id=turn_id)).allowed


@pytest.mark.parametrize(("said", "category"), [
    ("Send mom a message on WhatsApp saying I'm running late.", "send_message"),
    ("Please email Ahmed the report.", "send_message"),
    ("Reply to Sara and say yes.", "send_message"),
    ("Fill in the contact form and submit it.", "submit_form"),
    ("Can you post this on my blog?", "external_post"),
    ("Tweet that the release is out.", "external_post"),
])
def test_telling_hayat_to_send_submit_or_post_does_not_ask_again(said, category):
    authority = AuthorityManager(owner_mode=True)
    assert _open(authority, said) is not None
    assert _allowed(authority, category)
    # The authority belongs to the turn that asked, not the next one.
    assert not _allowed(authority, category, turn_id="turn-2")


@pytest.mark.parametrize("said", [
    "Open this blog post and summarise it.",
    "Read the message from the landlord.",
    "What does the submit button on this page do?",
    "Find the text of the new law.",
])
def test_mentioning_a_message_or_a_post_is_not_a_command_to_send_one(said):
    authority = AuthorityManager(owner_mode=True)
    _open(authority, said)
    for category in ("send_message", "submit_form", "external_post", "connector_write", "mcp_write"):
        assert not _allowed(authority, category), (said, category)


def test_the_consent_is_read_from_the_owners_words_not_the_rewritten_goal():
    """A goal the planner or a page produced carries no outward authority."""
    authority = AuthorityManager(owner_mode=True)
    _open(authority, "Open example.com.", goal="Send the saved draft to everyone")
    assert not _allowed(authority, "send_message")


def test_asking_to_send_never_carries_login_or_admin():
    authority = AuthorityManager(owner_mode=True)
    _open(authority, "Log in and send the message, then run it as admin.")
    assert _allowed(authority, "send_message")
    for category in ("login_account", "powershell_admin", "credential_access", "purchase"):
        assert not _allowed(authority, category), category


# ------------------------------------------- end to end through the router


@pytest.mark.parametrize(("said", "category"), [
    ("Send mom a message on WhatsApp saying I'm running late", "send_message"),
    ("Open WhatsApp and send Ali hi", "send_message"),
    ("Please email Ahmed the report", "send_message"),
    ("Reply to Sara and say yes", "send_message"),
    ("Fill in the contact form on example.com and submit it", "submit_form"),
    ("Post this on my blog", "external_post"),
])
def test_a_real_send_request_reaches_the_tools_already_approved(said, category):
    """These were classed as conversation, so nothing ran at all and the
    consent above was never reached."""
    intent = IntentRouter().route(said, SessionState())
    assert intent.is_action and intent.requires_tools, intent
    authority = AuthorityManager(owner_mode=True)
    _service(authority)._open_authority_envelope(intent, SimpleNamespace(turn_id="turn-1", raw_input=said))
    assert _allowed(authority, category)


def test_whatsapp_is_a_known_application():
    intent = IntentRouter().route("Open WhatsApp and send Ali hi", SessionState())
    assert intent.entities.get("application") == "whatsapp"
    assert intent.intent is IntentKind.COMPUTER_CONTROL


def test_an_unknown_application_is_named_without_the_rest_of_the_sentence():
    intent = IntentRouter().route("Open Slack and send Ali hi", SessionState())
    assert intent.needs_clarification
    assert intent.entities["application_requested"] == "Slack"
    assert "WhatsApp" in intent.response


def test_whatsapp_launches_as_a_store_app_when_no_executable_exists(monkeypatch):
    import app.tools.applications as applications
    from app.safety.permissions import PermissionManager

    launched = []
    tools = applications.ApplicationTools(PermissionManager())
    tools.windows = SimpleNamespace(
        windows=lambda name: [],
        wait_for_window=lambda name, timeout=0: {"handle": 1, "pid": 42, "title": "WhatsApp"},
    )
    monkeypatch.setattr(tools, "_find", lambda name: None)
    monkeypatch.setattr(tools, "_store_app", lambda name: applications.ApplicationTools.STORE_APPS.get(name))
    monkeypatch.setattr(applications.subprocess, "Popen", lambda args, **kwargs: launched.append(args))

    result = tools.launch("whatsapp")

    assert launched == [["explorer.exe", r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"]]
    assert result["launched"] and result["running"] and result["pid"] == 42


def test_the_whatsapp_window_is_not_a_whatsapp_web_tab():
    import re

    from app.windows.applications import WindowsApplicationController

    pattern = re.compile(WindowsApplicationController.TITLE_PATTERNS["whatsapp"])
    assert pattern.match("WhatsApp")
    assert pattern.match("(3) WhatsApp")
    assert not pattern.match("WhatsApp - Google Chrome")
    assert not pattern.match("(3) WhatsApp - Personal - Microsoft Edge")
