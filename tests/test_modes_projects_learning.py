from __future__ import annotations

from pathlib import Path

from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.learning.manager import LearningManager
from app.modes import ExecutionMode, ModeManager
from app.projects import ProjectManager
from app.session import SessionState
from app.agent.conversation import ConversationService, naturalize


def test_mode_composition_and_auto_resolution(tmp_path: Path):
    modes = ModeManager(tmp_path / "modes.json")
    assert modes.configured == [ExecutionMode.AUTO]
    research = IntentRouter().route("What's new in CUDA?", SessionState())
    assert research.intent == IntentKind.RESEARCH
    assert {mode.value for mode in modes.resolve(research)} == {"RESEARCH", "DEEP"}
    modes.set("deep + researcher")
    assert modes.snapshot()["configured"] == ["DEEP", "RESEARCH"]


def test_auto_mode_routes_complex_questions_to_deep(tmp_path: Path):
    from app.intent.models import RequestIntent, TaskComplexity

    modes = ModeManager(tmp_path / "modes.json")
    intent = RequestIntent(IntentKind.QUESTION, .9, "Explain a hard distributed systems failure",
                           complexity=TaskComplexity.COMPLEX)
    assert modes.resolve(intent) == [ExecutionMode.DEEP]


def test_active_project_persists(tmp_path: Path):
    path = tmp_path / "projects.json"
    first = ProjectManager(path)
    assert first.active and first.active.name == "AI"
    first.clear()
    assert ProjectManager(path).active is None
    ProjectManager(path).use("AI")
    assert ProjectManager(path).active.name == "AI"


def test_a_finished_answer_is_never_cut_short():
    """FAST mode used to chop the finished reply at a hundred words.

    Asked how Rust's ownership, borrowing and lifetimes fit together, HAYAT
    explained ownership, began on borrowing, and stopped -- never reaching
    lifetimes. It read as losing the thread, because the end had been cut off
    after the model wrote it. Brevity belongs in the request to the model, not
    in a pair of scissors applied to its answer, so nothing that shortens a
    complete reply may exist on this path.
    """
    assert not hasattr(ConversationService, "_limit_words")

    long_answer = " ".join(f"Sentence number {n} of a thorough answer." for n in range(80))
    assert len(long_answer.split()) > 400
    assert naturalize(long_answer).split()[-6:] == long_answer.split()[-6:]


def test_teach_quiz_weakness_and_lab_persist(tmp_path: Path, monkeypatch):
    learning = LearningManager(tmp_path / "learning.db")
    session = SessionState()
    lesson = learning.handle("Teach me GPU scheduling", session)
    assert "actively" in lesson and learning.topic("GPU scheduling")["level"] == "LEARNING"
    question = learning.handle("Quiz me", session)
    assert "answer" in question.lower() and session.pending_learning
    result = learning.handle("I am not sure", session)
    assert "weak concepts" in result and learning.weaknesses()[0]["topic"] == "GPU scheduling"
    monkeypatch.setattr("app.learning.manager.PROJECT_ROOT", tmp_path)
    lab = learning.create_lab("GPU scheduling")
    assert "lab is ready" in lab
