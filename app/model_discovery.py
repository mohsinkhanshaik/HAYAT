"""Model discovery: given a table and a column to predict, find out whether anything predicts it, and how well.

Derived from DARPA's Data-Driven Discovery of Models (D3M) programme. D3M itself ended in 2021 and its packages
pin Python 3.6-3.8 and long-superseded libraries, so none of it is installed here; what it automated is rebuilt on
scikit-learn, which is maintained: understand the dataset, clean it, build candidate pipelines, tune them a little,
compare them fairly, explain the winner and say how sure the answer is.

What makes the result trustworthy rather than flattering:

* **A baseline is always a candidate** (predict the mean / the most common class). A model "wins" only if it beats
  the baseline on data it never saw *and* in most validation folds. Otherwise the verdict is that nothing
  predictable was found -- which, for prices, is the usual and honest answer.
* **Time is respected.** With a time column the rows are sorted, validation folds always train on the past and
  test on the future (TimeSeriesSplit), and the final test is the most recent 20%. No shuffling, so no look-ahead.
* **Integrity checks run first**: duplicate rows, constant columns, and features that are the target in disguise
  (near-perfect correlation) are reported, and the disguised ones are left out.
* **Uncertainty is measured**, from the errors on held-out rows, not assumed.

Every run is kept (``model-discovery.db``) with its source, settings, scores and time taken.
"""
from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

MAX_ROWS = 200_000
MAX_BYTES = 80 * 1024**2
LEAK_CORRELATION = 0.98
HOLDOUT_SHARE = 0.2
FOLDS = 4
LIMITS = ("Found by comparing a few standard model families on this table only; a relationship here is a pattern "
          "in the data, not a cause. Scores describe the rows tested and can change on new data.")


def load_table(source: str | Path) -> Any:
    """A CSV file as a table (size-capped; nothing in the file is executed)."""
    import pandas as pd

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"No file at {path}")
    if path.suffix.lower() not in {".csv", ".tsv", ".txt"}:
        raise ValueError("Model discovery reads CSV or TSV tables.")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"That file is larger than {MAX_BYTES // 1024**2} MB; give a smaller extract.")
    table = pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else None, engine="python", nrows=MAX_ROWS + 1)
    if len(table) > MAX_ROWS:
        raise ValueError(f"That table has more than {MAX_ROWS:,} rows; give a smaller extract.")
    return table


def market_table(candles: list[dict[str, float]], horizon: int = 1) -> Any:
    """Features known at each candle's close, and the *next* period's return as the thing to predict.

    Every feature uses only that candle and earlier ones; the target is strictly later. Rows are oldest first.
    """
    import numpy as np
    import pandas as pd

    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError("The forecast horizon must be a positive number of candles.")
    frame = pd.DataFrame(candles).sort_values("time").reset_index(drop=True)
    if frame["time"].duplicated().any():
        raise ValueError("Market candles contain duplicate timestamps; resolve them before forecasting.")
    close = frame["close"].astype(float)
    change = close.pct_change()
    table = pd.DataFrame({
        "time": pd.to_datetime(frame["time"], unit="s", utc=True),
        "return_1": change, "return_3": close.pct_change(3), "return_12": close.pct_change(12),
        "volatility_12": change.rolling(12).std(), "volatility_48": change.rolling(48).std(),
        "range": (frame["high"] - frame["low"]) / close,
        "volume_vs_average": frame["volume"] / frame["volume"].rolling(24).mean(),
        "above_average_24": close / close.rolling(24).mean() - 1,
    })
    table["next_return"] = close.shift(-horizon) / close - 1
    table = table.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
    # Purge labels whose future candle could otherwise overlap a validation/test boundary.
    table.attrs["prediction_horizon_rows"] = horizon
    return table


def _feature_columns(frame: Any, target: Any = None) -> tuple[list[Any], list[Any], list[Any]]:
    """Learn feature eligibility using only the rows supplied to this fit."""
    import pandas as pd

    numeric, categorical, leaks = [], [], []
    for name in frame.columns:
        values = frame[name]
        distinct = values.nunique(dropna=True)
        if distinct <= 1:
            continue
        if pd.api.types.is_numeric_dtype(values):
            if target is not None and pd.api.types.is_numeric_dtype(target):
                correlation = values.corr(target)
                if correlation == correlation and abs(correlation) >= LEAK_CORRELATION:
                    leaks.append(name)
                    continue
            numeric.append(name)
        elif not pd.api.types.is_datetime64_any_dtype(values) and distinct <= 50:
            categorical.append(name)
    return numeric, categorical, leaks


def _chronological_partitions(times: Any, gap: int = 0) -> tuple[Any, Any, list[tuple[Any, Any]]]:
    """Keep equal timestamps together and purge future-label overlap at every boundary."""
    import numpy as np
    from sklearn.model_selection import TimeSeriesSplit

    groups = times.factorize(sort=False)[0]
    count = int(groups.max()) + 1
    cut = int(count * (1 - HOLDOUT_SHARE))
    if cut <= FOLDS:
        raise ValueError(f"At least {FOLDS + 2} distinct timestamps are needed for chronological validation.")
    test = np.flatnonzero(groups >= cut)
    train = np.flatnonzero(groups < cut)
    if gap:
        train = train[:-gap]
    if len(train) < FOLDS + 1:
        raise ValueError("Too few training rows remain after the forecast-horizon gap.")
    training_groups = groups[train]
    folds = []
    for fit_groups, check_groups in TimeSeriesSplit(n_splits=FOLDS).split(np.unique(training_groups)):
        fit_index = np.flatnonzero(training_groups <= fit_groups[-1])
        check_index = np.flatnonzero((training_groups >= check_groups[0]) & (training_groups <= check_groups[-1]))
        if gap:
            fit_index = fit_index[:-gap]
        if not len(fit_index):
            raise ValueError("Too few rows for a validation fold after the forecast-horizon gap.")
        folds.append((fit_index, check_index))
    return train, test, folds


def _accuracy_interval(hits: int, total: int) -> list[float]:
    """Wilson's 95% interval remains informative even when every test row is correct."""
    share, z = hits / total, 1.96
    denominator = 1 + z * z / total
    center = (share + z * z / (2 * total)) / denominator
    half = z * (share * (1 - share) / total + z * z / (4 * total * total)) ** 0.5 / denominator
    return [round(max(0.0, center - half), 3), round(min(1.0, center + half), 3)]


def profile(table: Any, target: str = "") -> dict[str, Any]:
    """What the table holds and what is wrong with it."""
    import pandas as pd

    columns = []
    for name in table.columns:
        series = table[name]
        kind = ("number" if pd.api.types.is_numeric_dtype(series) else
                "time" if pd.api.types.is_datetime64_any_dtype(series) else "text")
        columns.append({"name": str(name), "kind": kind, "missing": int(series.isna().sum()),
                        "distinct": int(series.nunique(dropna=True))})
    warnings: list[str] = []
    duplicates = int(table.duplicated().sum())
    if duplicates:
        warnings.append(f"{duplicates} duplicate rows (kept once).")
    constant = [column["name"] for column in columns if column["distinct"] <= 1]
    if constant:
        warnings.append("Columns that never change (left out): " + ", ".join(constant[:8]))
    leaks: list[str] = []
    if target and target in table.columns and pd.api.types.is_numeric_dtype(table[target]):
        numeric = table.select_dtypes("number")
        for name in numeric.columns:
            if name != target and numeric[name].nunique() > 1:
                correlation = numeric[name].corr(numeric[target])
                if correlation == correlation and abs(correlation) >= LEAK_CORRELATION:
                    leaks.append(str(name))
        if leaks:
            warnings.append("Columns that are the target in disguise (correlation ≥ "
                            f"{LEAK_CORRELATION}; left out): " + ", ".join(leaks[:8]))
    times = [column["name"] for column in columns if column["kind"] == "time"]
    return {"rows": int(len(table)), "columns": columns, "duplicate_rows": duplicates, "constant_columns": constant,
            "leaking_columns": leaks, "time_columns": times, "warnings": warnings}


def _candidates(task: str) -> list[tuple[str, Any, list[dict[str, Any]]]]:
    from sklearn.dummy import DummyClassifier, DummyRegressor
    from sklearn.ensemble import (HistGradientBoostingClassifier, HistGradientBoostingRegressor,
                                  RandomForestClassifier, RandomForestRegressor)
    from sklearn.linear_model import LogisticRegression, Ridge

    if task == "regression":
        return [("baseline (always the average)", DummyRegressor(strategy="mean"), [{}]),
                ("linear (ridge)", Ridge(), [{"alpha": 0.1}, {"alpha": 1.0}, {"alpha": 10.0}]),
                ("random forest", RandomForestRegressor(n_estimators=120, random_state=0, n_jobs=2),
                 [{"max_depth": 4}, {"max_depth": 8}, {"min_samples_leaf": 5}]),
                ("gradient boosting", HistGradientBoostingRegressor(random_state=0),
                 [{"learning_rate": 0.05, "max_depth": 3}, {"learning_rate": 0.1, "max_depth": 5}])]
    return [("baseline (always the most common)", DummyClassifier(strategy="most_frequent"), [{}]),
            ("linear (logistic)", LogisticRegression(max_iter=500), [{"C": 0.1}, {"C": 1.0}, {"C": 10.0}]),
            ("random forest", RandomForestClassifier(n_estimators=120, random_state=0, n_jobs=2),
             [{"max_depth": 4}, {"max_depth": 8}, {"min_samples_leaf": 5}]),
            ("gradient boosting", HistGradientBoostingClassifier(random_state=0),
             [{"learning_rate": 0.05, "max_depth": 3}, {"learning_rate": 0.1, "max_depth": 5}])]


def discover(table: Any, target: str, *, time_column: str = "", task: str = "", budget_seconds: float = 90.0,
             keep_fit: bool = False) -> dict[str, Any]:
    """Compare candidate pipelines for predicting ``target``; return scores, the verdict and its evidence."""
    import numpy as np
    import pandas as pd
    from sklearn.base import BaseEstimator, TransformerMixin, clone
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import balanced_accuracy_score, mean_absolute_error
    from sklearn.model_selection import KFold, StratifiedKFold, train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from sklearn.utils.multiclass import type_of_target

    started = time.perf_counter()
    if task not in {"", "classification", "regression"}:
        raise ValueError("Choose classification or regression for the prediction task.")
    if not np.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("The model search budget must be a positive number of seconds.")
    if not table.columns.is_unique:
        raise ValueError("Column names must be unique before a model can be built.")
    if len(table) > MAX_ROWS:
        raise ValueError(f"That table has more than {MAX_ROWS:,} rows; give a smaller extract.")
    if target not in table.columns:
        raise ValueError(f"There is no column called {target!r}. Columns: {', '.join(map(str, table.columns[:20]))}")
    gap = int(table.attrs.get("prediction_horizon_rows", 0)) if time_column else 0
    if gap < 0:
        raise ValueError("The forecast-horizon gap cannot be negative.")
    source_rows = len(table)
    duplicates = int(table.duplicated().sum())
    table = table.drop_duplicates().copy()
    number_columns = table.select_dtypes("number").columns
    table[number_columns] = table[number_columns].replace([np.inf, -np.inf], np.nan)
    if time_column:
        if time_column not in table.columns:
            raise ValueError(f"There is no time column called {time_column!r}.")
        if time_column == target:
            raise ValueError("The target cannot also be the time column.")
        if not pd.api.types.is_numeric_dtype(table[time_column]):
            table[time_column] = pd.to_datetime(table[time_column], errors="coerce", utc=True, format="mixed")
        if table[time_column].isna().any():
            raise ValueError("The time column contains missing or invalid timestamps; correct them before forecasting.")
        table = table.sort_values(time_column, kind="stable")
    missing_targets = int(table[target].isna().sum())
    table = table.dropna(subset=[target]).reset_index(drop=True)
    if len(table) < 60:
        raise ValueError(f"Only {len(table)} usable rows; at least 60 are needed to test a model fairly.")
    y = table[target]
    if y.nunique() < 2:
        raise ValueError("The target never changes; a predictive model cannot be evaluated.")
    if not task:
        task = ("regression" if pd.api.types.is_numeric_dtype(y)
                and (y.nunique() > 10 or type_of_target(y) == "continuous") else "classification")
    if task == "regression" and not pd.api.types.is_numeric_dtype(y):
        raise ValueError("Regression needs a numeric target column.")
    features = table.drop(columns=[name for name in (target, time_column) if name])
    if time_column:
        train, test, fold_indices = _chronological_partitions(table[time_column], gap)
        validation = "chronological: every fold trains on earlier rows and tests on later ones; final test = newest 20%"
        if gap:
            validation += f"; {gap}-row forecast-horizon gap at each boundary"
    else:
        if task == "classification":
            if y.value_counts().min() < 2:
                raise ValueError("Each class needs at least two rows for a stratified training/test split.")
            try:
                train, test = train_test_split(np.arange(len(table)), test_size=HOLDOUT_SHARE,
                                               random_state=0, stratify=y)
            except ValueError as error:
                raise ValueError("Too many classes for a fair held-out test; provide more rows per class.") from error
            if y.iloc[train].nunique() != y.nunique() or y.iloc[test].nunique() != y.nunique():
                raise ValueError("The held-out split cannot represent every class; provide more rows per rare class.")
            folds = min(FOLDS, int(y.iloc[train].value_counts().min()))
            if folds < 2:
                raise ValueError("Too few training rows per class for cross-validation; provide more rows.")
            splitter: Any = StratifiedKFold(folds, shuffle=True, random_state=0)
        else:
            order = np.random.default_rng(0).permutation(len(table))
            cut = int(len(table) * (1 - HOLDOUT_SHARE))
            train, test = order[:cut], order[cut:]
            splitter = KFold(FOLDS, shuffle=True, random_state=0)
        fold_indices = list(splitter.split(features.iloc[train], y.iloc[train]))
        validation = "shuffled folds; final test = a random 20% never used for choosing"
        if task == "classification":
            validation += "; stratified by class"
    x_train, y_train, x_test, y_test = features.iloc[train], y.iloc[train], features.iloc[test], y.iloc[test]
    from app.lab_evidence import digest, environment

    table_digest = hashlib.sha256(pd.util.hash_pandas_object(table, index=False).to_numpy().tobytes())
    table_digest.update(digest([(str(name), str(table[name].dtype)) for name in table.columns]).encode("ascii"))
    evaluation = {"protocol": "hayat-model-discovery-v2", "seed": 0, "dataset_sha256": table_digest.hexdigest(),
                  "split_sha256": digest({"train": train.tolist(), "test": test.tolist(),
                                          "folds": [(a.tolist(), b.tolist()) for a, b in fold_indices]}),
                  "task": task, "target": str(target), "time_column": time_column, "forecast_gap_rows": gap,
                  "environment": environment("numpy", "pandas", "scikit-learn"),
                  "search_budget_seconds": float(budget_seconds), "budget_scope": "soft search limit, not a per-fit timeout",
                  "scope": "Seeded local candidate search; records data and split digests, not raw rows or DARPA certification."}
    evaluation["comparison_key"] = digest({key: evaluation[key] for key in
                                           ("protocol", "seed", "dataset_sha256", "split_sha256", "task", "target",
                                            "time_column", "forecast_gap_rows", "environment")})
    # Reporting uses outer training data only; each CV pipeline repeats this selection on its own fit rows.
    facts = profile(table.iloc[train], target)
    if duplicates:
        facts["warnings"].insert(0, f"{duplicates} duplicate rows (kept once).")
    if missing_targets:
        facts["warnings"].append(f"{missing_targets} rows with missing or nonfinite targets left out.")
    numeric, categorical, _leaks = _feature_columns(x_train, y_train)
    if not numeric and not categorical:
        raise ValueError("No usable feature columns remain after the training-data integrity checks.")
    left_out = [str(name) for name in features.columns if name not in numeric + categorical]
    if time_column:
        left_out.append(time_column)

    class FoldFeatures(TransformerMixin, BaseEstimator):
        def fit(self, frame: Any, labels: Any = None) -> Any:
            numbers, categories, _leaks = _feature_columns(frame, labels)
            self.columns_ = numbers + categories
            if not self.columns_:
                raise ValueError("No usable features remain in this training fold.")
            return self

        def transform(self, frame: Any) -> Any:
            return frame[self.columns_]

    prepare = ColumnTransformer([
        ("numbers", Pipeline([("fill", SimpleImputer(strategy="median")), ("scale", StandardScaler())]),
         lambda frame: list(frame.select_dtypes("number").columns)),
        ("categories", Pipeline([("fill", SimpleImputer(strategy="most_frequent")),
                                 ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False))]),
         lambda frame: list(frame.select_dtypes(exclude="number").columns)),
    ])

    def score(truth: Any, predicted: Any) -> float:
        # Higher is better for both tasks.
        return (-float(mean_absolute_error(truth, predicted)) if task == "regression"
                else float(balanced_accuracy_score(truth, predicted)))

    rows: list[dict[str, Any]] = []
    for name, estimator, grid in _candidates(task):
        best: dict[str, Any] | None = None
        for settings in grid:
            if rows and time.perf_counter() - started > budget_seconds:
                break
            pipeline = Pipeline([("features", FoldFeatures()), ("prepare", clone(prepare)),
                                 ("model", clone(estimator).set_params(**settings))])
            folds = []
            try:
                for fit_index, check_index in fold_indices:
                    pipeline.fit(x_train.iloc[fit_index], y_train.iloc[fit_index])
                    folds.append(score(y_train.iloc[check_index], pipeline.predict(x_train.iloc[check_index])))
            except Exception as error:
                log_swallowed(_log, f"model discovery: fitting {name}")
                rows.append({"model": name, "settings": settings, "error": type(error).__name__})
                continue
            entry = {"model": name, "settings": settings, "folds": [round(value, 6) for value in folds],
                     "validation_score": round(float(np.mean(folds)), 6), "pipeline": pipeline}
            if best is None or entry["validation_score"] > best["validation_score"]:
                best = entry
        if best is not None:
            rows.append(best)
    scored = [row for row in rows if "validation_score" in row]
    if not scored or not scored[0]["model"].startswith("baseline"):
        raise ValueError("The baseline could not be validated; check the target and feature data.")
    baseline = scored[0]
    for row in scored:
        try:
            row["pipeline"].fit(x_train, y_train)
            row["test_score"] = round(score(y_test, row["pipeline"].predict(x_test)), 6)
        except Exception as error:
            log_swallowed(_log, f"model discovery: final fit of {row['model']}")
            row["error"] = type(error).__name__
            continue
        row["folds_beating_baseline"] = int(sum(a > b for a, b in zip(row["folds"], baseline["folds"])))
    scored = [row for row in scored if "test_score" in row]
    if not scored or baseline not in scored:
        raise ValueError("The baseline could not be tested; check the target and feature data.")
    winner = max(scored, key=lambda row: row["validation_score"])
    fold_count = len(fold_indices)
    unit = "mean absolute error" if task == "regression" else "balanced accuracy"
    shown = (lambda value: round(-value, 6)) if task == "regression" else (lambda value: round(value, 4))
    if task == "regression":
        skill = 1 - (winner["test_score"] / baseline["test_score"]) if baseline["test_score"] else 0.0
    else:
        skill = winner["test_score"] - baseline["test_score"]
    beats = (winner is not baseline and skill > (0.02 if task == "regression" else 0.03)
             and winner["folds_beating_baseline"] >= max(2, fold_count - 1))
    result: dict[str, Any] = {
        "task": task, "target": target, "rows_used": int(len(table)), "source_rows": int(source_rows),
        "features": numeric + categorical,
        "left_out": sorted(left_out), "validation_folds": fold_count,
        "split": {"training_rows": int(len(train)), "test_rows": int(len(test)), "gap_rows": gap,
                  "feature_selection": "fitted on each fold's training rows; holdout labels never select features"},
        "validation": validation, "metric": unit, "integrity": facts["warnings"],
        "candidates": [{"model": row["model"], "settings": row.get("settings"),
                        "validation": shown(row["validation_score"]) if "validation_score" in row else None,
                        "test": shown(row["test_score"]) if "test_score" in row else None,
                        "folds_beating_baseline": row.get("folds_beating_baseline"), "error": row.get("error")}
                       for row in rows],
        "best": winner["model"], "beats_baseline": bool(beats), "skill_over_baseline": round(float(skill), 4),
        "evaluation": evaluation, "heldout_score": shown(winner["test_score"]),
    }
    if beats:
        result["verdict"] = (f"{winner['model']} predicts {target} better than the baseline on rows it never saw "
                             f"({unit} {shown(winner['test_score'])} against {shown(baseline['test_score'])}), and in "
                             f"{winner['folds_beating_baseline']} of {fold_count} validation folds.")
        try:
            importance = permutation_importance(winner["pipeline"], x_test, y_test, n_repeats=5, random_state=0,
                                                scoring="neg_mean_absolute_error" if task == "regression"
                                                else "balanced_accuracy")
            ranked = sorted(zip(features.columns, importance.importances_mean, importance.importances_std),
                            key=lambda item: -item[1])
            result["what_matters"] = [{"feature": str(name), "importance": round(float(mean), 5),
                                       "spread": round(float(spread), 5)} for name, mean, spread in ranked[:8]
                                      if mean > 0]
        except Exception:
            log_swallowed(_log, "model discovery: permutation importance")
        result["next"] = "Check it on newer data before relying on it; test its robustness in the Robustness Laboratory."
    else:
        result["verdict"] = (f"Nothing predictable found: no model beat the baseline for {target} on unseen rows "
                             f"(best was {winner['model']}, {unit} {shown(winner['test_score'])} against "
                             f"{shown(baseline['test_score'])} for the baseline).")
        result["next"] = "Add features that could plausibly carry information, or more rows, and try again."
    if task == "regression":
        errors = np.abs(np.asarray(y_test, dtype=float) - winner["pipeline"].predict(x_test))
        result["uncertainty"] = {"typical_error": round(float(np.median(errors)), 6),
                                 "error_not_exceeded_80_percent_of_the_time": round(float(np.quantile(errors, 0.8)), 6),
                                 "measured_on": int(len(errors))}
    else:
        hits = int((winner["pipeline"].predict(x_test) == np.asarray(y_test)).sum())
        total = int(len(y_test))
        result["uncertainty"] = {"right_on_test": f"{hits} of {total}",
                                 "accuracy_95_interval": _accuracy_interval(hits, total),
                                 "interval_method": "Wilson score (ordinary accuracy, not balanced accuracy)"}
    if numeric:
        try:
            from sklearn.ensemble import IsolationForest

            filled = features[numeric].fillna(features[numeric].median())
            marks = IsolationForest(n_estimators=100, contamination=0.01, random_state=0).fit_predict(filled)
            odd = [int(index) for index in np.where(marks == -1)[0][:10]]
            result["unusual_rows"] = {"count": int((marks == -1).sum()), "first": odd,
                                      "note": "Rows unlike the rest (1% most isolated); worth a look, not errors by themselves."}
        except Exception:
            log_swallowed(_log, "model discovery: unusual rows")
    result["seconds"] = round(time.perf_counter() - started, 2)
    result["limits"] = LIMITS
    if keep_fit:
        # The training rows stay in memory with the fit (never saved): the label-corruption test refits on them.
        result["_fit"] = {"pipeline": winner["pipeline"], "x_test": x_test[numeric + categorical],
                          "y_test": y_test, "task": task, "x_train": x_train[numeric + categorical], "y_train": y_train}
    return result


class ModelDiscovery:
    """Runs discoveries for the owner, keeps every run, and remembers the last fitted model for robustness tests."""
    SOURCE = "model_discovery"

    def __init__(self, path: Path | str, runtime: Any = None) -> None:
        self.path = Path(path)
        self.runtime = runtime or (lambda: None)
        self._lock = threading.Lock()
        self.events: Any = None
        #: The last winning pipeline with its test rows, kept in memory only (never written to disk).
        self.last_fit: dict[str, Any] | None = None

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS experiments(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                   "source TEXT NOT NULL, target TEXT NOT NULL, beats_baseline INTEGER NOT NULL, seconds REAL NOT NULL, "
                   "result_json TEXT NOT NULL)")
        return db

    def table_for(self, source: str) -> tuple[Any, str, str]:
        """(table, default target, time column) for a CSV path or ``market:SYMBOL``."""
        value = str(source or "").strip()
        if value.lower().startswith("market:"):
            markets = getattr(self.runtime(), "markets", None)
            if markets is None:
                raise RuntimeError("Market data isn't available in this session.")
            symbol, _kind, candles = markets.candles(value.split(":", 1)[1].strip(), 60)
            if len(candles) < 200:
                raise ValueError(f"Only {len(candles)} hourly candles came back for {symbol}; too few to test.")
            return market_table(candles), "next_return", "time"
        return load_table(value), "", ""

    def run(self, source: str, target: str = "", time_column: str = "", task: str = "") -> dict[str, Any]:
        table, default_target, default_time = self.table_for(source)
        target = target or default_target
        if not target:
            raise ValueError("Say which column to predict (target).")
        result = discover(table, target, time_column=time_column or default_time, task=task, keep_fit=True)
        fit = result.pop("_fit", None)
        result["source"] = source
        result["tool"] = "scikit-learn pipelines (model discovery)"
        if fit is not None:
            self.last_fit = {**fit, "source": source, "target": target, "model": result["best"],
                             "beats_baseline": result["beats_baseline"], "evaluation": result["evaluation"]}
        with self._lock, closing(self._db()) as db, db:
            from app.lab_evidence import compare_metric

            previous = db.execute("SELECT id,result_json FROM experiments WHERE source=? AND target=? ORDER BY id DESC LIMIT 1",
                                  (source, target)).fetchone()
            result["comparison"] = compare_metric(result, json.loads(previous["result_json"]) if previous else None,
                                                   field="heldout_score", direction="lower" if result["task"] == "regression" else "higher")
            if previous:
                result["comparison"]["previous_experiment_id"] = previous["id"]
            cursor = db.execute("INSERT INTO experiments(at,source,target,beats_baseline,seconds,result_json) VALUES(?,?,?,?,?,?)",
                                (datetime.now(UTC).isoformat(timespec="seconds"), source, target,
                                 int(result["beats_baseline"]), result["seconds"], json.dumps(result, default=str)))
            result["experiment_id"] = int(cursor.lastrowid or 0)
        if self.events is not None:
            try:
                self.events.publish("LAB_RUN_COMPLETED", self.SOURCE, {"experiment_id": result["experiment_id"],
                                                                       "beats_baseline": result["beats_baseline"]})
            except Exception:
                log_swallowed(_log, "publishing a model discovery run")
        return result

    def describe(self, source: str, target: str = "") -> dict[str, Any]:
        table, default_target, _time = self.table_for(source)
        return {"source": source, **profile(table, target or default_target)}

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT * FROM experiments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": row["id"], "at": row["at"], "source": row["source"], "target": row["target"],
                 "beats_baseline": bool(row["beats_baseline"]), "seconds": row["seconds"],
                 **{key: json.loads(row["result_json"]).get(key) for key in ("best", "verdict", "metric")}}
                for row in rows]

    def detail(self, experiment_id: int) -> dict[str, Any] | None:
        """Reopen the complete recorded evidence without fitting another model."""
        if not self.path.is_file():
            return None
        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        if row is None:
            return None
        return {**json.loads(row["result_json"]), "experiment_id": row["id"], "at": row["at"],
                "source": row["source"], "target": row["target"]}

    def compare(self, experiment_id: int, baseline_id: int) -> dict[str, Any]:
        """Compare saved evidence only; never fit a model or read the original table again."""
        from app.lab_evidence import compare_metric

        current, previous = self.detail(experiment_id), self.detail(baseline_id)
        if current is None or previous is None:
            raise ValueError("Both experiment ids must name saved model-discovery runs.")
        return {"experiment_id": experiment_id, "baseline_id": baseline_id,
                **compare_metric(current, previous, field="heldout_score",
                                 direction="lower" if current["task"] == "regression" else "higher")}

    def status(self) -> dict[str, Any]:
        rows = self.recent(1)
        if not rows:
            return {"experiments": 0, "found_something_predictable": 0, "last": "none yet"}
        with self._lock, closing(self._db()) as db:
            count, predictable = db.execute("SELECT COUNT(*), SUM(beats_baseline) FROM experiments").fetchone()
        return {"experiments": count, "found_something_predictable": predictable, "last": rows[0]["verdict"]}


def register_model_discovery_tools(registry: Any, discovery: ModelDiscovery) -> None:
    def run(action: str = "discover", source: str = "", target: str = "", time_column: str = "",
            task: str = "", experiment_id: int = 0, baseline_id: int = 0) -> dict[str, Any]:
        if action == "recent":
            return {"experiments": discovery.recent()}
        if action == "profile":
            return discovery.describe(source, target)
        if action == "detail":
            return {"experiment": discovery.detail(experiment_id)}
        if action == "compare":
            return discovery.compare(experiment_id, baseline_id)
        return discovery.run(source, target, time_column, task)

    registry.register(
        "model_discover",
        "Find out whether a column in a table can be predicted, and how well: profiles the data, builds and compares "
        "candidate models against a baseline with leak-free validation (chronological when there is a time column), "
        "and reports the verdict, what matters, the measured uncertainty and unusual rows. source is a CSV path or "
        "market:SYMBOL (hourly candles; predicts the next hour's return). Says so when nothing predictable is found.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["discover", "profile", "recent", "detail", "compare"]},
            "experiment_id": {"type": "integer", "minimum": 1}, "baseline_id": {"type": "integer", "minimum": 1},
            "source": {"type": "string"}, "target": {"type": "string"}, "time_column": {"type": "string"},
            "task": {"type": "string", "enum": ["", "regression", "classification"]}},
         "additionalProperties": False},
        "low", "filesystem_read", run)
