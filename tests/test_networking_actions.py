from __future__ import annotations

from functools import partial
import json
from types import SimpleNamespace

import pytest

from app.agent.planner import Planner
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.networking_actions import plan, report, request
from app.session import SessionState
from app.tools.networking import TOOL_NAMES


CONTACT_ID = "ab" * 16


@pytest.mark.parametrize("text,tool,args", [
    ("Show my private networking contacts", "networking_list", {}),
    ("List my saved contacts", "networking_list", {}),
    ("Please show me my private network", "networking_list", {}),
    ("Find saved contacts who can help with Python projects", "networking_match", {"goal": "Python projects", "limit": 20}),
    ("Who in my private network can help with optimization?", "networking_match", {"goal": "optimization", "limit": 20}),
    ("Which saved contacts could help me with open research?", "networking_match", {"goal": "open research", "limit": 20}),
    ("Search my saved contacts for Ada Example", "networking_list", {"query": "Ada Example"}),
    (f"Show saved contact {CONTACT_ID}", "networking_detail", {"contact_id": CONTACT_ID}),
    (f"Draft an introduction to saved contact {CONTACT_ID} about Python projects", "networking_draft",
     {"contact_id": CONTACT_ID, "purpose": "Python projects"}),
    (f"Draft an introduction to saved contact {CONTACT_ID}", "networking_detail", {"contact_id": CONTACT_ID}),
    ("Draft an introduction to a saved contact", "networking_list", {}),
    # Since 2026-10-24 a name or a short ID chooses the contact; the book resolves it and lists namesakes.
    ("Draft an introduction to saved contact Ada Example about Python projects", "networking_draft",
     {"contact_id": "Ada Example", "purpose": "Python projects"}),
    ("Draft an introduction to my contact 3f9a1c2b about documentation", "networking_draft",
     {"contact_id": "3f9a1c2b", "purpose": "documentation"}),
    ("Show my contact Ada Example", "networking_detail", {"contact_id": "Ada Example"}),
    ("Restore my networking contacts from C:/Users/me/contacts-export.json", "networking_restore",
     {"path": "C:/Users/me/contacts-export.json"}),
])
def test_supported_commands_use_explicit_local_tools(text, tool, args):
    assert request(text) == (tool, args)
    step = plan(text, TOOL_NAMES)
    assert step["tool"] == tool and step["arguments"] == args
    intent = IntentRouter().route(text, SessionState())
    assert intent.intent is IntentKind.ACTION
    assert intent.entities.get("modality") == "private_networking"
    registry = SimpleNamespace(names=lambda: list(TOOL_NAMES))
    actual = Planner(registry)._deterministic_plan(intent.goal, intent, SessionState())
    assert len(actual) == 1 and actual[0].tool == tool and actual[0].arguments == args


@pytest.mark.parametrize("text", [
    "Show my contacts", "Find people online who can help with Python projects", "Send Ada a message",
    "Find private contact details for a stranger", "Identify this person from their photo",
    "Who is the nicest person based on nationality?", "Draft an introduction to Ada",
    "Delete all my saved contacts", "Show private social media profiles", "Export all contacts and send them",
])
def test_routes_do_not_hijack_messaging_public_research_or_identity_inference(text):
    assert request(text) is None


def test_missing_tool_does_not_create_a_fake_plan():
    assert plan("Show my private networking contacts", []) is None


def test_empty_results_and_namesake_choice_reports_are_honest():
    empty = report([{"tool": "networking_match", "result": {"success": True, "output": {"contacts": []}}}])
    assert "No saved contacts match" in empty and "only your active local" in empty
    rows = [{"id": "aa" * 16, "name": "Alex Example", "organization": "First Lab"},
            {"id": "bb" * 16, "name": "Alex Example", "organization": "Second Lab"}]
    chosen = report([{"tool": "networking_list", "result": {"success": True, "output": {"contacts": rows}}}])
    assert all(row["id"] in chosen and row["organization"] in chosen for row in rows)
    assert "first 8 characters of the ID" in chosen and "not a prediction" in chosen
    asked = report([{"tool": "networking_draft", "result": {"success": False, "error":
             "ValueError: More than one saved contact is named “Alex Example”: Alex Example | First Lab (ID aaaaaaaa)"}}])
    assert asked.startswith("More than one saved contact") and "failed" not in asked  # a question back, not a failure


def test_report_delivers_only_valid_unsent_draft_and_real_failure():
    draft = report([{"tool": "networking_draft", "result": {"success": True, "output": {
        "subject": "Research conversation", "body": "Hello Ada", "sent": False}}}])
    assert "Hello Ada" in draft and "Nothing was sent" in draft
    malformed = report([{"tool": "networking_draft", "result": {"success": True, "output": {"sent": True}}}])
    assert "No valid unsent" in malformed
    failed = report([{"tool": "networking_detail", "result": {"success": False, "error": "Access revoked"}}])
    assert "failed: Access revoked" in failed


def test_mixed_task_reporting_cannot_claim_nothing_sent_after_another_tool_sent_a_message():
    draft = {"tool": "networking_draft", "result": {"success": True, "output": {
        "subject": "Research conversation", "body": "Hello Ada", "sent": False}}}
    sent = {"tool": "email_send", "result": {"success": True, "output": {"sent": True}}}
    assert report([draft, sent]) is None
    assert report([sent, draft]) is None
    assert "Nothing was sent" in report([draft])


def test_default_live_networking_directory_is_redirected_to_test_sandbox():
    from app.networking import NetworkingBook
    from tests.conftest import LIVE_DATA, SANDBOX
    book = NetworkingBook(LIVE_DATA / "networking")
    assert book.directory == SANDBOX / "networking"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from app import runtime as runtime_module
    from app.memory.database import MemoryDatabase
    from app.modes import ModeManager
    monkeypatch.setattr(runtime_module, "MemoryDatabase", partial(MemoryDatabase, tmp_path / "data/memory/agent.db"))
    monkeypatch.setattr(runtime_module, "ModeManager", partial(ModeManager, tmp_path / "data/memory/modes.json"))
    built = runtime_module.build_runtime(use_model=False, start_background_services=False, safe_mode=True)
    built.conversation.clear()
    built.session.session_id = built.conversation.session_id
    yield built
    built.close()


def test_full_assistant_lists_matches_and_drafts_real_saved_records_without_a_model(runtime):
    saved = runtime.networking.save("Ada Example", organization="Example Lab", expertise=["Python projects"],
                                    goals=["open research"], source_url="https://example.org/team/ada")
    unrelated = runtime.networking.save("Unrelated Person", expertise=["civil engineering"])
    listed = runtime.assistant.handle("Show my private networking contacts")
    assert listed.complete and "Ada Example" in listed.display_response and "Unrelated Person" in listed.display_response
    matches = runtime.assistant.handle("Find saved contacts who can help with Python projects")
    assert matches.complete and "Ada Example" in matches.display_response and "Unrelated Person" not in matches.display_response
    assert saved["id"] in matches.display_response and unrelated["id"] not in matches.display_response
    drafted = runtime.assistant.handle(f"Draft an introduction to saved contact {saved['id']} about Python projects")
    assert drafted.complete and "Unsent draft" in drafted.display_response and "Nothing was sent" in drafted.display_response
    for reply in [listed, matches, drafted]:
        assert all(event["tool"].startswith("networking_") for event in reply.outcome.history)
    runtime.events.flush()
    durable = json.dumps([row for row in runtime.events.recent(500) if row["event_type"] in {
        "TASK_STARTED", "TASK_CREATED", "TASK_PROGRESS", "TASK_COMPLETED", "TOOL_STARTED", "TOOL_COMPLETED"}])
    assert "Ada Example" not in durable and "Python projects" not in durable


def test_private_networking_outcomes_cannot_enter_apprenticeship_or_correction_training():
    from app.assistant import AssistantService
    def forbidden(*args, **kwargs):
        pytest.fail("Private networking must not enter learning/training")
    service = SimpleNamespace(runtime=SimpleNamespace(apprenticeship=SimpleNamespace(observe=forbidden),
                                                      session=SimpleNamespace(last_correction="private correction")))
    outcome = SimpleNamespace(plan=[{"tool": "networking_draft"}], history=[{"tool": "networking_draft"}])
    intent = SimpleNamespace(entities={"modality": "private_networking"})
    AssistantService._observe_for_learning(service, outcome)
    AssistantService._learn_from_repair(service, intent, outcome)
    assert service.runtime.session.last_correction == ""
    service.runtime.situation = SimpleNamespace(observe_turn=forbidden)
    AssistantService._observe_situation(service, "private user words", SimpleNamespace(intent=intent, outcome=outcome))


def test_namesakes_come_back_as_a_question_through_the_registry(tmp_path):
    # Live 2026-10-24: two "Sam Example" contacts made the agent replan for 111 s and lose the question.
    from app.networking import NetworkingBook
    from app.safety.permissions import PermissionManager
    from app.tools.networking import register_networking_tools
    from app.tools.registry import ToolRegistry

    def codec(data, *, decrypt=False):
        return data
    book = NetworkingBook(tmp_path / "book", codec=codec)
    book.save("Sam Example", organization="First Lab")
    book.save("Sam Example", organization="Second Lab")
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    register_networking_tools(registry, book)
    result = registry.execute("networking_draft", {"contact_id": "Sam Example", "purpose": "a python project"})
    assert result.success and result.output["choice_needed"] is True
    text = report([{"tool": "networking_draft", "result": {"success": True, "output": result.output}}])
    assert text.startswith("More than one saved contact is named") and "First Lab" in text and "Second Lab" in text
    assert "Nothing was drafted or sent" in text
