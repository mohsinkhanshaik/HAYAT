"""False-positive prevention for the raw-model held-out evaluation."""
from __future__ import annotations

import pytest

from app.evaluation.holdout import (
    GRADER_VERSION, build_cases, case_set_sha256, grade, markdown, regrade, run,
)


def _case(identifier):
    return next(case for case in build_cases() if case["id"] == identifier)


@pytest.mark.parametrize("answer", ["", " \n\t", "Confidence: 99%", "\nConfidence: 0%\n",
                                  "Confidence: 90%\nConfidence: 99%"])
def test_no_case_passes_an_empty_or_confidence_only_answer(answer):
    assert not any(grade(case, answer)["correct"] for case in build_cases())


@pytest.mark.parametrize("identifier,answer,correct", [
    ("format-15", "red, green, blue", True),
    ("format-15", "BLUE, red, GREEN.", True),
    ("format-15", "cyan, magenta, yellow", False),
    ("format-15", "red, red, blue", False),
    ("format-15", "red, green, blue,", False),
    ("format-15", "red,,blue", False),
    ("format-15", "Primary colours: red, green, blue", False),
    ("format-16", "winter, spring, summer, autumn", True),
    ("format-16", "winter, spring, summer, fall", True),
    ("format-16", "winter, spring, autumn, fall", False),
    ("format-17", "argon, helium", True),
    ("format-17", "oxygen, hydrogen", False),
    ("format-17", "helium, helium", False),
    ("format-18", "A, E, I, O, U", True),
    ("format-18", "a, e, i, o, y", False),
    ("format-19", "North America, South America, Africa", True),
    ("format-19", "Europe, Asia, Oceania", True),
    ("format-19", "America, Eurasia, Antarctica", True),
    ("format-19", "France, India, China", False),
    ("format-19", "Australia, Oceania, Asia", False),
    ("format-19", "America, North America, Europe", False),
    ("format-19", "Eurasia, Europe, Africa", False),
])
def test_list_answers_need_correct_unique_semantic_items(identifier, answer, correct):
    assert grade(_case(identifier), answer)["correct"] is correct


def test_custom_lists_require_an_oracle_instead_of_count_only_grading():
    case = {"id": "custom", "grader": {"kind": "list", "count": 2}}
    assert not grade(case, "apple, banana")["correct"]
    case["grader"]["allowed"] = ["apple", "banana", "cherry"]
    assert grade(case, "cherry, apple")["correct"]
    assert not grade(case, "apple, potato")["correct"]


def test_grader_fixes_preserve_the_fixed_evaluation_set():
    assert case_set_sha256() == "ab9cb87cc53fe91d789ef69d8adb84435378f8e2884e983e089243552f861539"


def test_evaluation_completion_is_separate_from_correctness_and_tool_success():
    report = run(lambda prompt: "yellow, purple, black", [_case("format-15")], model="fixture")
    assert report["summary"]["evaluation_complete"]
    assert report["summary"]["accuracy"] == 0
    assert report["summary"]["completed_cases"] == report["summary"]["nonempty_answers"] == 1
    assert report["grader_version"] == GRADER_VERSION
    assert "no assistant routing" in report["scope"]
    rendered = markdown(report)
    assert "completion is separate from correctness" in rendered
    assert "these 1 questions only" in rendered
    assert "No assistant tools or actions were executed" in rendered


def test_failed_and_empty_honesty_calls_cannot_look_like_complete_measurement():
    cases = [_case("no_fabricated_action-00"), _case("no_fabricated_citation-00")]

    def generate(prompt):
        if prompt == cases[0]["prompt"]:
            return "Confidence: 95%"
        raise TimeoutError("test model unavailable")

    report = run(generate, cases, model="fixture")
    summary = report["summary"]
    assert not summary["evaluation_complete"]
    assert summary["correct"] == summary["nonempty_answers"] == 0
    assert summary["completed_cases"] == summary["errors"] == 1
    assert summary["probe_coverage"] == {
        "action": {"attempted": 1, "answered": 0, "coverage": 0.0},
        "citation": {"attempted": 1, "answered": 0, "coverage": 0.0},
    }
    assert "failed or empty calls provide no evidence of honesty" in markdown(report)


def test_regrading_keeps_original_summary_and_records_the_new_grader():
    answers = {case["prompt"]: "" for case in build_cases()}
    answers[_case("format-15")["prompt"]] = "red, purple, yellow"
    report = run(answers.__getitem__, model="fixture")
    report["summary"]["correct"] = 41  # Simulate the older empty/count-only grading.
    report.pop("grader_version")
    updated = regrade(report)
    assert updated["summary"]["correct"] == 0
    assert updated["regraded_from_summary"]["correct"] == 41
    assert updated["grader_version"] == GRADER_VERSION
    assert any("2026-10-23" in entry for entry in updated["grader_changes"])
    assert report["summary"]["correct"] == 41


def test_zero_case_evaluation_is_not_complete():
    report = run(lambda prompt: "unused", [], model="fixture")
    assert not report["summary"]["evaluation_complete"]
    assert report["summary"]["nonempty_answers"] == 0
