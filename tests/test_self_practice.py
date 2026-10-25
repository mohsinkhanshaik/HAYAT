import json
import threading

import pytest

from app.learning.self_practice import SelfPractice, task_for, parse_answer, verifies


def test_independent_oracles_and_strict_answers():
    for family in ("arithmetic", "statistics", "code_trace", "sequence", "evidence", "impartiality"):
        task = task_for(family, 17)
        assert verifies(parse_answer(json.dumps({"answer": task["expected"]})), task["expected"])
    assert not verifies(True, 1)
    assert not verifies([True, 2], [1, 2])
    assert verifies(3.0, 3)
    assert not verifies(float("nan"), 3)
    assert verifies("3", 3)
    assert not verifies("the answer is 3", 3)
    assert not verifies([2, 1], [1, 2])
    with pytest.raises(ValueError):
        parse_answer('The answer is 42')


def test_same_model_agreement_cannot_create_verified_success(tmp_path):
    calls = []
    def generate(prompt, **kwargs):
        calls.append(prompt)
        return '{"answer": -999, "critique": "I am confident this is correct"}'
    engine = SelfPractice(tmp_path)
    report = engine.run(generate, cycles=2)
    assert report["model_calls"] == 4 and len(calls) == 4
    assert report["verified_correct"] == 0 and not engine.state["verified_cases"]
    assert report["weights_updated"] is False
    assert all(row["selected"] is None for row in report["cycles"])
    restored = SelfPractice(tmp_path)
    assert restored.notes("arithmetic")
    assert any("Verified oracle correction" in note for note in restored.notes("arithmetic"))
    assert len(restored.state["verified_corrections"]) == 2


def test_review_recovery_is_verified_and_memory_is_reused(tmp_path):
    engine = SelfPractice(tmp_path)
    expected = task_for("arithmetic", 42)["expected"]
    responses = iter(['{"answer": -1}', json.dumps({"answer": expected, "critique": "Rechecked the product"})])
    report = engine.run(lambda *a, **k: next(responses), cycles=1, seed=42)
    assert report["draft_correct"] == 0 and report["verified_correct"] == 1
    assert report["cycles"][0]["selected"] == "review"
    restored = SelfPractice(tmp_path)
    assert any("Verified practice example" in note for note in restored.notes("arithmetic"))
    assert restored.state["sessions"] == 1
    restored.state["verified_cases"][0]["answer"] = -1
    assert not any("Verified practice example" in note for note in restored.notes("arithmetic"))


def test_correct_draft_survives_an_incorrect_self_review(tmp_path):
    expected = task_for("arithmetic", 42)["expected"]
    answers = iter([json.dumps({"answer": expected}), '{"answer": -1}'])
    report = SelfPractice(tmp_path).run(lambda *a, **k: next(answers), cycles=1)
    assert report["cycles"][0]["selected"] == "draft" and report["verified_correct"] == 1


def test_cancellation_and_failure_have_explicit_outcomes(tmp_path):
    cancel = threading.Event()
    cancel.set()
    report = SelfPractice(tmp_path / "cancelled").run(lambda *a, **k: pytest.fail("Must not generate"), cancel=cancel)
    assert report["state"] == "cancelled" and report["model_calls"] == 0
    def fails(*a, **k):
        raise RuntimeError("Model unavailable")
    failed = SelfPractice(tmp_path / "failed").run(fails, cycles=1)
    assert failed["state"] == "inference_failed" and failed["verified_correct"] == 0
    with pytest.raises(ValueError):
        SelfPractice(tmp_path).run(fails, cycles=100)


def test_adaptive_selection_returns_to_weakest_practiced_family(tmp_path):
    engine = SelfPractice(tmp_path)
    for name in ("arithmetic", "statistics", "code_trace", "sequence", "evidence", "impartiality"):
        engine.state["families"][name] = {"attempts": 10, "draft_correct": 9, "verified_correct": 10}
    engine.state["families"]["statistics"]["draft_correct"] = 1
    assert engine._next_family(0) == "statistics"


def test_chat_calculation_is_complete_and_does_not_execute_arbitrary_input():
    from app.models.hayat_open import verified_calculation
    assert verified_calculation("Calculate 17 * 23. Return only the number.") == "391"
    assert verified_calculation("Calculate 144 / 12 + 7. Return only the number.") == "19"
    assert verified_calculation("Calculate 0.1 + 0.2. Return only the number.") == "0.3"
    for text in ("Calculate 1 / 0", "Calculate __import__('os').system('echo test')",
                 "Calculate 17 * 23 and explain tax implications.", "Calculate 17 * 23 without multiplying."):
        assert verified_calculation(text) is None


def test_atomic_memory_save_retries_transient_windows_file_lock(tmp_path, monkeypatch):
    from pathlib import Path
    from app.learning import self_practice
    original = Path.replace
    attempts = []
    def replace(source, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError("Reader temporarily holds the report")
        return original(source, target)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(self_practice.time, "sleep", lambda seconds: None)
    engine = SelfPractice(tmp_path)
    engine._save(tmp_path / "report.json", {"success": True})
    assert len(attempts) == 3
    assert json.loads((tmp_path / "report.json").read_text())["success"] is True
