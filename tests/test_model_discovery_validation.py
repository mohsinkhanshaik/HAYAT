"""Regression checks for discovery's held-out evidence and chronological boundaries."""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd
import pytest
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.linear_model import LogisticRegression, Ridge

import app.model_discovery as discovery


@pytest.fixture(autouse=True)
def small_search(monkeypatch):
    def candidates(task):
        if task == "regression":
            return [("baseline (always the average)", DummyRegressor(), [{}]),
                    ("linear (ridge)", Ridge(), [{}])]
        return [("baseline (always the most common)", DummyClassifier(strategy="most_frequent"), [{}]),
                ("linear (logistic)", LogisticRegression(), [{}])]

    monkeypatch.setattr(discovery, "_candidates", candidates)


def _table(rows=120):
    rng = np.random.default_rng(7)
    return pd.DataFrame({"x": rng.normal(size=rows), "z": rng.normal(size=rows), "y": rng.normal(size=rows)})


def test_holdout_labels_never_choose_feature_columns_or_validation_scores():
    table = _table()
    indices = np.random.default_rng(0).permutation(len(table))
    test_indices = indices[int(len(table) * 0.8):]
    # A large holdout-only relationship used to cause this feature to be excluded from every fold.
    table.loc[test_indices, "z"] *= 10000
    changed = table.copy()
    changed.loc[test_indices, "y"] = changed.loc[test_indices, "z"]
    first = discovery.discover(table, "y")
    second = discovery.discover(changed, "y")
    assert first["features"] == second["features"] == ["x", "z"]
    assert first["left_out"] == second["left_out"]
    assert [row["validation"] for row in first["candidates"]] == [
        row["validation"] for row in second["candidates"]]


def test_equal_timestamps_and_future_labels_never_cross_split_boundaries():
    times = pd.Series(pd.date_range("2025-01-01", periods=40).repeat(3))
    train, test, folds = discovery._chronological_partitions(times, gap=3)
    assert times.iloc[train].max() < times.iloc[test].min()
    assert train[-1] + 3 < test[0]
    for fit, check in folds:
        assert times.iloc[train[fit]].max() < times.iloc[train[check]].min()
        assert train[fit[-1]] + 3 < train[check[0]]


def test_csv_date_strings_are_parsed_before_temporal_sorting():
    table = _table()
    dates = pd.date_range("2025-01-01", periods=len(table))
    table["date"] = [f"{date.month}/{date.day}/{date.year}" for date in dates]
    table["sequence"] = np.arange(len(table))
    result = discovery.discover(table.sample(frac=1, random_state=3), "y", time_column="date", keep_fit=True)
    assert list(result["_fit"]["x_test"]["sequence"]) == list(range(96, 120))
    assert result["split"]["training_rows"] == 96
    table.loc[0, "date"] = "not a date"
    with pytest.raises(ValueError, match="invalid timestamps"):
        discovery.discover(table, "y", time_column="date")


def test_class_holdout_is_stratified_and_perfect_accuracy_has_nonzero_uncertainty():
    table = pd.DataFrame({"x": [0.0] * 90 + [1.0] * 10, "label": ["a"] * 90 + ["b"] * 10})
    table["row"] = np.arange(len(table))  # keep repeated observations distinct
    result = discovery.discover(table, "label", keep_fit=True)
    assert result["_fit"]["y_test"].value_counts().to_dict() == {"a": 18, "b": 2}
    assert result["uncertainty"]["accuracy_95_interval"][0] < 1
    assert "Wilson" in result["uncertainty"]["interval_method"]


def test_a_candidate_failing_its_final_fit_does_not_erase_the_baseline(monkeypatch):
    class FailsFinalFit(RegressorMixin, BaseEstimator):
        def fit(self, frame, labels):
            if len(frame) >= 96:
                raise ValueError("candidate cannot fit the full training set")
            self.mean_ = float(np.mean(labels))
            return self

        def predict(self, frame):
            return np.full(len(frame), self.mean_)

    monkeypatch.setattr(discovery, "_candidates", lambda task: [
        ("baseline (always the average)", DummyRegressor(), [{}]),
        ("fails at final fit", FailsFinalFit(), [{}])])
    result = discovery.discover(_table(), "y")
    assert result["best"].startswith("baseline")
    assert result["candidates"][1]["error"] == "ValueError"


@pytest.mark.parametrize("task", ["ranking", "forecast"])
def test_invalid_tasks_are_rejected(task):
    with pytest.raises(ValueError, match="classification or regression"):
        discovery.discover(_table(), "y", task=task)


def test_constant_targets_and_singleton_classes_are_rejected():
    table = _table()
    table["y"] = 1
    with pytest.raises(ValueError, match="never changes"):
        discovery.discover(table, "y")
    table["y"] = ["a"] * 119 + ["b"]
    with pytest.raises(ValueError, match="at least two rows"):
        discovery.discover(table, "y")


def test_recorded_experiments_reopen_and_totals_are_not_capped_at_200(tmp_path):
    instance = discovery.ModelDiscovery(tmp_path / "models.db")
    assert instance.detail(1) is None
    with sqlite3.connect(instance.path) as db:
        db.execute("CREATE TABLE experiments(id INTEGER PRIMARY KEY, at TEXT, source TEXT, target TEXT, "
                   "beats_baseline INTEGER, seconds REAL, result_json TEXT)")
        db.executemany("INSERT INTO experiments VALUES(?,?,?,?,?,?,?)", [
            (index, "2025-01-01", "sample.csv", "y", 1, 0.1,
             json.dumps({"verdict": f"Evidence {index}", "candidates": [{"model": "baseline"}]}))
            for index in range(1, 206)])
    assert instance.status()["experiments"] == 205
    assert instance.status()["found_something_predictable"] == 205
    assert instance.detail(1)["candidates"] == [{"model": "baseline"}]
    assert instance.detail(1)["source"] == "sample.csv"
    assert instance.detail(1000) is None


def test_market_horizon_is_positive_and_carried_to_validation():
    with pytest.raises(ValueError, match="positive"):
        discovery.market_table([], horizon=0)
    close = np.arange(100.0, 220.0)
    candles = [{"time": 1700000000 + 3600 * index, "close": price, "high": price + 1,
                "low": price - 1, "volume": 100 + index % 9} for index, price in enumerate(close)]
    table = discovery.market_table(candles, horizon=3)
    assert table.attrs["prediction_horizon_rows"] == 3
    with pytest.raises(ValueError, match="duplicate timestamps"):
        discovery.market_table(candles + candles[:1])


def test_csv_row_limit_reports_the_truncation_instead_of_silently_training_on_a_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "MAX_ROWS", 20)
    path = tmp_path / "large.csv"
    _table(21).to_csv(path, index=False)
    with pytest.raises(ValueError, match="more than 20 rows"):
        discovery.load_table(path)


def test_nonfinite_feature_values_are_imputed_and_nonfinite_targets_are_reported():
    table = _table()
    table.loc[0, "x"] = np.inf
    table.loc[1, "y"] = -np.inf
    result = discovery.discover(table, "y")
    assert result["rows_used"] == 119
    assert any("nonfinite targets" in warning for warning in result["integrity"])
