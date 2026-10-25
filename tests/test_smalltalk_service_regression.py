"""Real AssistantService turns must not turn social chat into external research."""
from types import SimpleNamespace

import pytest

from app.intent.models import IntentKind
from tests.test_assistant_service import runtime


@pytest.fixture
def chat(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append((args, kwargs))
        pytest.fail("Small talk must not schedule research, operate a camera, or invoke a model/planner")
    monkeypatch.setattr(rt.assistant, "_research_in_background", forbidden)
    monkeypatch.setattr(rt.assistant, "_conversation_reply", forbidden)
    rt.research = SimpleNamespace(recall_verified=forbidden)
    rt.registry = SimpleNamespace(execute=forbidden, names=lambda: [])
    rt.forbidden_calls = attempted
    return rt


@pytest.mark.parametrize("text", [
    "how are you today ?", "How are you today?", "how are you today", "how are you doing today?",
    "hey how are you today?", "how r u today ?", "how are u today?", "you good?",
])
@pytest.mark.parametrize("context", ["fresh", "active_research", "camera", "deep_research"])
def test_wellbeing_turn_never_crosses_the_assistant_web_invariant(chat, text, context):
    if context in {"active_research", "deep_research"}:
        chat.session.research_audit = {"query": "public employer job boards", "status": "running"}
        chat.session.conversation_topic = "job search"
    if context == "camera":
        chat.session.last_camera_observation = {"interpretation": "A generated test scene", "observed_at": "2026-10-25T00:00:00Z"}
    if context == "deep_research":
        chat.modes.set(["RESEARCH", "DEEP"])
    result = chat.assistant.handle(text)
    assert result.intent.intent == IntentKind.CONVERSATION and not result.intent.requires_tools
    assert result.display_response.strip() and len(result.display_response.split()) <= 60
    assert "searching" not in result.display_response.lower()
    assert not chat.forbidden_calls and chat.manager.calls == []


def test_reported_transcript_stays_conversational_without_background_work(chat):
    first = chat.assistant.handle("how are you today ?")
    second = chat.assistant.handle("ok")
    assert first.intent.intent == second.intent.intent == IntentKind.CONVERSATION
    assert not chat.forbidden_calls and chat.manager.calls == []
    assert not chat.session.research_audit


@pytest.mark.parametrize("query", ["What are today's Python releases?", "search the web for Python releases today"])
def test_fix_does_not_suppress_real_current_information_requests(tmp_path, monkeypatch, query):
    rt = runtime(tmp_path)
    calls = []
    rt.research = SimpleNamespace(recall_verified=lambda *args, **kwargs: None)
    monkeypatch.setattr(rt.assistant, "_research_in_background", lambda *args, **kwargs: calls.append((args, kwargs)) or "Queued public research.")
    result = rt.assistant.handle(query)
    assert result.intent.intent == IntentKind.RESEARCH and result.intent.requires_tools
    assert len(calls) == 1
