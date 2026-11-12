"""Laboratory results carry a reproducibility record, compare only like with like, and never overstate."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from app.lab_evidence import compare_metric, digest, environment
from app.model_discovery import ModelDiscovery
from app.research_lab import PROGRAM_REFERENCES, evidence_lines, ghidra_status
from app.robustness import POISON_LIMITS, RobustnessLab, poisoning_suite


@pytest.fixture(autouse=True)
def small_search(monkeypatch):
    """A baseline and one quick candidate: these tests are about the records, not the search."""
    from sklearn.dummy import DummyClassifier, DummyRegressor
    from sklearn.linear_model import LogisticRegression, Ridge

    import app.model_discovery as module

    def candidates(task):
        if task == "regression":
            return [("baseline (always the average)", DummyRegressor(), [{}]), ("linear (ridge)", Ridge(), [{}])]
        return [("baseline (always the most common)", DummyClassifier(strategy="most_frequent"), [{}]),
                ("linear (logistic)", LogisticRegression(max_iter=300), [{}])]
    monkeypatch.setattr(module, "_candidates", candidates, raising=True)


def _labels(rows: int = 240, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    table = pd.DataFrame({"a": rng.normal(size=rows), "b": rng.normal(size=rows)})
    table["label"] = np.where(2 * table["a"] - table["b"] + rng.normal(scale=0.3, size=rows) > 0, "up", "down")
    return table


def _numbers(rows: int = 240, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    table = pd.DataFrame({"a": rng.normal(size=rows), "b": rng.normal(size=rows)})
    table["y"] = 3 * table["a"] - 2 * table["b"] + rng.normal(scale=0.2, size=rows)
    return table


# ---------------------------------------------------------------------------------------------- the record itself

def test_a_digest_ignores_key_order_and_refuses_values_that_cannot_be_reproduced():
    assert digest({"a": 1, "b": [1, 2]}) == digest({"b": [1, 2], "a": 1})
    assert digest({"a": 1}) != digest({"a": 2}) and len(digest([])) == 64
    with pytest.raises(ValueError):
        digest({"score": float("nan")})


def test_the_environment_names_what_is_and_is_not_installed():
    found = environment("numpy", "a-package-that-does-not-exist")
    assert found["packages"]["a-package-that-does-not-exist"] == "not installed"
    assert found["packages"]["numpy"][0].isdigit() and found["python"].count(".") == 2


def _run(metric, key="same", field="metric"):
    return {field: metric, "evaluation": {"comparison_key": key}}


@pytest.mark.parametrize("current,previous,why", [
    (_run(0.9), None, "No earlier run"),
    (_run(0.9), _run(0.8, key="other"), "differs or was not recorded"),
    ({"metric": 0.9}, {"metric": 0.8}, "differs or was not recorded"),          # runs from before records existed
    (_run(0.9, key=""), _run(0.8, key=""), "differs or was not recorded"),
    (_run(None), _run(0.8), "finite measured score"),
    (_run(0.9), _run(math.inf), "finite measured score"),
    (_run(True), _run(0.8), None),                                              # see the note in the test body
])
def test_runs_are_compared_only_when_the_same_thing_was_measured(current, previous, why):
    result = compare_metric(current, previous)
    if why is None:
        # A bool is an int in Python; it is at least finite, and the recorded values are shown as they are.
        assert result["comparable"] and result["after"] is True
        return
    assert not result["comparable"] and why in result["reason"]
    assert "before" not in result and "change" not in result


@pytest.mark.parametrize("before,after,direction,change", [
    (0.8, 0.9, "higher", "improved"), (0.9, 0.8, "higher", "worse"), (0.5, 0.5, "higher", "unchanged"),
    (0.8, 0.9, "lower", "worse"), (0.9, 0.8, "lower", "improved"),
])
def test_better_or_worse_follows_the_direction_of_the_score(before, after, direction, change):
    result = compare_metric(_run(after), _run(before), direction=direction)
    assert result["comparable"] and result["change"] == change and result["delta"] == pytest.approx(after - before)
    assert "not statistical significance" in result["limits"]


def test_the_window_lines_state_the_record_without_claiming_more_than_it_shows():
    lines = evidence_lines({
        "evaluation": {"protocol": "hayat-model-discovery-v2", "dataset_sha256": "d" * 64, "split_sha256": "5" * 64,
                       "seed": 0, "environment": {"python": "3.14.0", "packages": {"numpy": "2.3.0"}}},
        "comparison": {"comparable": True, "before": 0.8, "after": 0.9, "delta": 0.1, "direction": "higher"}})
    text = "\n".join(lines)
    assert "Evaluation: hayat-model-discovery-v2" in text and "Dataset: " + "d" * 16 + "…" in text
    assert "Validation split: " + "5" * 16 + "…" in text and "Random seed: 0" in text
    assert "Environment: Python 3.14.0; numpy 2.3.0" in text
    assert "0.8 → 0.9; change +0.1 (higher is better)" in text and "not independent confirmation" in text
    assert "d" * 17 not in text                               # a short fingerprint, not the full digest
    unavailable = evidence_lines({"comparison": {"comparable": False, "reason": "No earlier run for this input."}})
    assert unavailable == ["Run comparison unavailable: No earlier run for this input."]
    tests = evidence_lines({"baseline_tests_passed": False, "tests_passed": None})
    assert tests == ["Named tests: before failed; after not measured."]
    assert evidence_lines({}) == []
    assert all(address.startswith("https://") for address in PROGRAM_REFERENCES.values())
    assert set(PROGRAM_REFERENCES) == {"GARD", "AIxCC", "D3M", "Ghidra"}


# ---------------------------------------------------------------------------------------------- model discovery

def test_a_repeated_discovery_is_comparable_and_a_changed_table_is_not(tmp_path):
    path = tmp_path / "numbers.csv"
    _numbers().to_csv(path, index=False)
    discovery = ModelDiscovery(tmp_path / "md.db")
    first = discovery.run(str(path), "y")
    record = first["evaluation"]
    assert record["protocol"] == "hayat-model-discovery-v2" and len(record["dataset_sha256"]) == 64
    assert len(record["split_sha256"]) == 64 and len(record["comparison_key"]) == 64
    assert "not raw rows" in record["scope"] and str(path) not in json.dumps(record)
    assert first["comparison"] == {"comparable": False, "reason": "No earlier run for this input."}
    second = discovery.run(str(path), "y")
    assert second["evaluation"]["comparison_key"] == record["comparison_key"]
    assert second["comparison"]["comparable"] and second["comparison"]["change"] == "unchanged"
    assert second["comparison"]["previous_experiment_id"] == first["experiment_id"]
    assert second["comparison"]["direction"] == "lower"        # a regression is scored by its error
    # One changed cell is a different table: the two scores are not put side by side.
    changed = _numbers()
    changed.loc[0, "a"] += 1.0
    changed.to_csv(path, index=False)
    third = discovery.run(str(path), "y")
    assert third["evaluation"]["dataset_sha256"] != record["dataset_sha256"]
    assert not third["comparison"]["comparable"] and "differs" in third["comparison"]["reason"]
    # Saved evidence is compared without fitting anything or reading the table again.
    path.unlink()
    again = discovery.compare(second["experiment_id"], first["experiment_id"])
    assert again["comparable"] and again["change"] == "unchanged" and again["baseline_id"] == first["experiment_id"]
    assert not discovery.compare(third["experiment_id"], first["experiment_id"])["comparable"]
    with pytest.raises(ValueError, match="saved model-discovery runs"):
        discovery.compare(second["experiment_id"], 9999)
    reopened = discovery.detail(second["experiment_id"])
    assert reopened["evaluation"] == second["evaluation"] and reopened["comparison"]["comparable"]
    assert "_fit" not in reopened and "x_train" not in json.dumps(reopened)   # rows stay in memory, never in the record


# ---------------------------------------------------------------------------------------------- robustness

def test_a_repeated_regression_suite_is_comparable_and_its_scope_is_stated(tmp_path):
    lab = RobustnessLab(tmp_path / "rob.db")
    first = lab.run("injection")["results"][0]
    record = first["evaluation"]
    assert record["protocol"] == "hayat-injection-regression-v2" and len(record["corpus_sha256"]) == 64
    assert "not unseen accuracy" in record["scope"] and not first["comparison"]["comparable"]
    second = lab.run("injection")["results"][0]
    assert second["comparison"]["comparable"] and second["comparison"]["change"] == "unchanged"
    assert second["previous"]["id"] == first["run_id"]
    saved = lab.detail(second["run_id"])
    assert saved["evaluation"] == record and saved["comparison"]["comparable"]
    with pytest.raises(ValueError, match="poisoning or all"):
        lab.run("everything")


def test_wrong_training_labels_cost_a_classifier_accuracy_and_the_cost_is_measured_on_clean_rows(tmp_path):
    path = tmp_path / "labels.csv"
    _labels().to_csv(path, index=False)
    discovery = ModelDiscovery(tmp_path / "md.db")
    assert discovery.run(str(path), "label")["task"] == "classification"
    before = discovery.last_fit["y_train"].copy()
    result = poisoning_suite(discovery.last_fit, rates=(0.10, 0.45))
    assert result["suite"] == "poisoning" and result["tool"].startswith("scikit-learn refit")
    light, heavy = result["levels"]
    assert light["corrupted_share"] == 0.10 and heavy["corrupted_share"] == 0.45 and light["refits"] == 2
    assert result["clean_score"] > 0.9
    # Nearly half the labels wrong costs accuracy on the clean rows; one in ten costs less. (A linear model
    # tolerates evenly spread wrong labels well, so the loss is real but modest: the test measures, it does not assume.)
    assert heavy["score"] < result["clean_score"] and heavy["cost"] > light["cost"] and heavy["cost"] > 0.02
    assert heavy["cost"] == pytest.approx(result["clean_score"] - heavy["score"], abs=1e-4)
    assert result["metric"] == light["cost"]                    # the headline is the share nearest one in ten
    assert "10% of training labels are wrong" in result["headline"]
    assert result["limits"] == POISON_LIMITS and "not a targeted or backdoor attack" in POISON_LIMITS
    record = result["evaluation"]
    assert record["protocol"] == "hayat-label-corruption-v1" and record["score"] == "balanced accuracy"
    assert record["seeds"] == [0, 1] and not record["training_rows_sampled"]
    assert record["discovery"]["dataset_sha256"] == discovery.last_fit["evaluation"]["dataset_sha256"]
    # The model that was discovered and its rows are left exactly as they were.
    assert discovery.last_fit["y_train"].equals(before)
    assert poisoning_suite(discovery.last_fit, rates=(0.10, 0.45))["levels"] == result["levels"]   # seeded


def test_wrong_training_values_grow_a_regressor_s_error(tmp_path):
    path = tmp_path / "numbers.csv"
    _numbers().to_csv(path, index=False)
    discovery = ModelDiscovery(tmp_path / "md.db")
    discovery.run(str(path), "y")
    lab = RobustnessLab(tmp_path / "rob.db", discovery)
    result = lab.run("poisoning")["results"][0]
    assert result["evaluation"]["score"] == "mean absolute error"
    assert [level["corrupted_share"] for level in result["levels"]] == [0.05, 0.10, 0.20]
    assert result["levels"][-1]["score"] > result["clean_score"] and result["levels"][-1]["cost"] > 0
    assert "of training values are wrong" in result["headline"]
    assert not result["comparison"]["comparable"]
    again = lab.run("poisoning")["results"][0]
    assert again["comparison"]["comparable"] and again["comparison"]["direction"] == "lower"
    assert again["comparison"]["change"] == "unchanged"
    assert lab.history()[0]["suite"] == "poisoning" and "poisoning" in lab.report()["suites_run"]
    json.dumps(lab.detail(again["run_id"]))                    # the saved record holds no rows or model objects


def test_the_label_test_says_plainly_when_it_has_nothing_to_measure(tmp_path):
    assert poisoning_suite(None)["headline"].startswith("No model to test yet")
    older = {"pipeline": object(), "x_test": [1], "y_test": [1], "task": "regression", "model": "linear"}
    assert "before training rows were kept" in poisoning_suite(older)["headline"]
    path = tmp_path / "labels.csv"
    _labels().to_csv(path, index=False)
    discovery = ModelDiscovery(tmp_path / "md.db")
    discovery.run(str(path), "label")
    with pytest.raises(ValueError, match="at most one half"):
        poisoning_suite(discovery.last_fit, rates=(0.8,))
    # No time at all: the clean refit is reported, the corrupted ones are named as not measured, nothing is invented.
    rushed = poisoning_suite(discovery.last_fit, budget_seconds=-1)
    assert rushed["metric"] is None and rushed["levels"] == [] and rushed["not_measured_shares"] == [0.05, 0.10, 0.20]
    assert "time budget ran out" in rushed["headline"] and not rushed["failure_category"]


# ---------------------------------------------------------------------------------------------- software analysis

def _engine(root, version="12.2"):
    (root / "runtime/ghidra/Ghidra").mkdir(parents=True)
    (root / "runtime/ghidra/support").mkdir()
    (root / "runtime/ghidra/support/analyzeHeadless.bat").write_text("", encoding="utf-8")
    (root / "runtime/ghidra/Ghidra/application.properties").write_text(
        f"application.name=Ghidra\napplication.version={version}\napplication.java.min=21\n", encoding="utf-8")
    (root / "runtime/java/bin").mkdir(parents=True)
    (root / "runtime/java/bin/java.exe").write_text("", encoding="utf-8")
    (root / "runtime/java/release").write_text('JAVA_VERSION="25.0.5"\n', encoding="utf-8")


def _saved(root, **changes):
    report = {"passed": True, "source_unchanged": True, "decompiled_return_42": True, "executed": False,
              "ghidra_version": "12.2", "java_version": "25.0.5", "rea_version": "6.8.0",
              "checked_at": "2026-11-10T08:00:00+00:00", "qualification_limits": "Generated x64 PE fixture only."} | changes
    folder = root / ".test-runs/native-analysis"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "verification.json").write_text(json.dumps(report), encoding="utf-8")


def test_an_installed_engine_is_not_called_tested_until_a_matching_check_is_on_record(tmp_path, monkeypatch):
    import app.research_lab as module
    import app.reverse_engineer as engineer

    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engineer, "installed_version", lambda: "6.8.0")
    _engine(tmp_path)
    found = ghidra_status(None)
    assert found["version"] == "12.2" and found["java_version"] == "25.0.5" and found["required_java"] == "21"
    assert "advisory_warning" not in found and found["reference"] == PROGRAM_REFERENCES["Ghidra"]
    assert found["qualification"] == {"available": False, "current_installation_retested": False,
                                      "scope": "No saved generated-x64 qualification was found; installation is not an analysis test."}
    _saved(tmp_path)
    saved = ghidra_status(None)["qualification"]
    assert saved["available"] and saved["recorded_success"] and saved["versions_match"]
    assert saved["current_installation_retested"] is False      # reading the record is not running the check
    assert "do not prove current file integrity" in saved["limits"]
    # A record made with another engine version, or one in which the file was run, is shown as exactly that.
    _saved(tmp_path, ghidra_version="12.1.4")
    assert ghidra_status(None)["qualification"]["versions_match"] is False
    _saved(tmp_path, executed=True)
    assert ghidra_status(None)["qualification"]["recorded_success"] is False
    _saved(tmp_path, decompiled_return_42="yes")
    assert ghidra_status(None)["qualification"]["recorded_success"] is False
    (tmp_path / ".test-runs/native-analysis/verification.json").write_text("[1, 2]", encoding="utf-8")
    assert ghidra_status(None)["qualification"]["available"] is False


def test_an_engine_older_than_the_fix_is_warned_about_from_its_own_properties(tmp_path, monkeypatch):
    import app.research_lab as module

    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    _engine(tmp_path, version="12.1.4")
    found = ghidra_status(None)
    assert "older than 12.2" in found["advisory_warning"] and "analyse only files you trust" in found["advisory_warning"]
    assert "setup_native_analysis all --upgrade" in found["upgrade_next"]
