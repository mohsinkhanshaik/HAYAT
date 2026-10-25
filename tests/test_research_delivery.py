from __future__ import annotations

from types import SimpleNamespace

from app.assistant import AssistantService
from app.intent.models import IntentKind, RequestIntent, SuccessCriterion, TaskComplexity
from app.voice.manager import SpokenResponseFormatter

ANSWER = "Answer:\n- Python 3.13 was released on October 7, 2024. [1]\n\nSources:\n[1] https://example.com"


def _research_outcome(answer: str = ANSWER, confidence: float = 0.82):
    return SimpleNamespace(
        complete=True,
        needs_user_input=False,
        summary="Task completed.",
        task_id="task-1",
        history=[{
            "tool": "research_topic",
            "result": {"success": True, "output": {"answer": answer, "sources": [{"url": "https://example.com"}], "confidence": confidence}},
        }],
    )


def _research_intent():
    return RequestIntent(
        IntentKind.RESEARCH, .97, "Python 3.13 release date", True, True, True, TaskComplexity.RESEARCH,
        {"query": "Python 3.13 release date"},
        [SuccessCriterion("research_complete", "A research answer with sources and citations was produced.")],
    )


def test_display_response_delivers_the_research_answer_not_a_generic_confirmation():
    """Regression test: research_complete verified only that the tool produced
    a cited answer -- the answer was never surfaced, so the user saw
    "Done. I verified the requested result." with the actual findings sitting
    unread in the tool output. Research's deliverable IS the answer."""
    service = AssistantService.__new__(AssistantService)
    service.runtime = SimpleNamespace(proactive_mode="SUGGEST")
    response = service._outcome_response(_research_intent(), _research_outcome())
    assert "October 7, 2024" in response
    assert response != "Done. I verified the requested result."


def test_rendered_browser_capture_delivers_its_answer_not_a_generic_confirmation():
    intent = RequestIntent(
        IntentKind.BROWSER, .995, "example.com summarize it", True, True, True,
        TaskComplexity.STANDARD, {"url": "https://example.com", "web_capture": True},
        [
            SuccessCriterion("browser_destination", "The page was rendered.", value="https://example.com"),
            SuccessCriterion("research_complete", "The page produced a sourced answer."),
        ],
    )
    outcome = _research_outcome()
    outcome.history[0]["tool"] = "capture_web_page"
    response = AssistantService.__new__(AssistantService)
    response.runtime = SimpleNamespace(proactive_mode="SUGGEST")
    delivered = response._outcome_response(intent, outcome)
    assert "October 7, 2024" in delivered
    assert delivered != "Done. I verified the requested result."


def test_spoken_response_summarizes_the_answer_for_search_phrasing():
    """The voice layer had its own separate keyword list ("research", "look
    up", "check whether", "learn ") that omitted "search", so "Search the
    internet for X" fell through to "Done. The task completed successfully."
    Matching on the tool actually used is robust to phrasing."""
    response = SpokenResponseFormatter.format(_research_outcome(), "Search the internet for Python 3.13 release date.")
    assert "October 7, 2024" in response.spoken_response
    assert response.spoken_response != "Done. The task completed successfully."


def test_low_confidence_research_is_flagged_in_speech():
    response = SpokenResponseFormatter.format(_research_outcome(confidence=0.31), "Search the internet for something obscure.")
    assert "uncertain" in response.spoken_response.lower()


def test_research_without_an_answer_falls_back_to_source_count():
    response = SpokenResponseFormatter.format(_research_outcome(answer=""), "Look up something.")
    assert "1 source" in response.spoken_response
