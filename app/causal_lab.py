"""Cause and effect from a table: what the data can support about "does X change Y?", and what it cannot.

Model discovery answers "can Y be predicted?". This answers a different question: how much Y changes when X is
changed, after allowing for the other columns that influence both. It is the standard observational toolkit, built
on scikit-learn and numpy:

* **Naive difference**: what a plain comparison shows (the figure confounding distorts).
* **Regression adjustment**: a linear model of the outcome on the treatment and the confounders.
* **Doubly robust estimate**: for a yes/no treatment, augmented inverse-propensity weighting (AIPW); for a numeric
  treatment, the partially linear "double machine learning" estimate. Both fit their helper models on other rows
  than the ones they score (cross-fitting) and carry a standard error from the influence function.
* **Refutation tests**: the treatment shuffled (the effect should vanish), a random extra confounder added (the
  estimate should not move), and random 80% subsets (it should be stable).
* **Overlap and balance**: whether treated and untreated rows are comparable at all, column by column.
* **E-value**: how strong an unmeasured common cause would have to be to explain the effect away.

No table can prove causation on its own. The estimate is causal only if the adjusted columns include every common
cause of treatment and outcome and none that the treatment itself changes. Every result states that, and the verdict
is "unreliable" rather than a number when the refutation tests or overlap fail.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

MIN_ROWS, MAX_CONFOUNDERS, MAX_LEVELS, FOLDS = 60, 40, 20, 5
CLIP = (0.02, 0.98)
PLACEBO_RUNS, SUBSET_RUNS = 20, 10
LIMITS = ("An observational estimate. It is the causal effect only if the adjusted columns include every common "
          "cause of the treatment and the outcome, and none that the treatment itself changes. No analysis of a "
          "table can confirm that. Estimates also require treatment overlap, a consistent treatment and no "
          "interference between rows; these confidence intervals assume independent observations. A well-designed "
          "controlled experiment can address confounding.")


def _columns(table: Any, treatment: str, outcome: str, confounders: list[str] | None) -> list[str]:
    for name, role in ((treatment, "treatment"), (outcome, "outcome")):
        if name not in table.columns:
            raise ValueError(f"There is no column named {name!r} to use as the {role}.")
    if treatment == outcome:
        raise ValueError("The treatment and the outcome must be different columns.")
    if confounders is None:
        chosen = [str(name) for name in table.columns if name not in (treatment, outcome)]
    else:
        chosen = list(dict.fromkeys(str(name) for name in confounders))
        missing = [name for name in chosen if name not in table.columns]
        if missing:
            raise ValueError("No such column to adjust for: " + ", ".join(missing))
        if treatment in chosen or outcome in chosen:
            raise ValueError("The treatment and the outcome cannot also be adjusted for.")
    if len(chosen) > MAX_CONFOUNDERS:
        raise ValueError(f"Adjust for at most {MAX_CONFOUNDERS} columns; name the ones that matter.")
    return chosen


def _design(table: Any, confounders: list[str]) -> tuple[Any, list[str], list[str]]:
    """Numeric confounder matrix (medians for gaps, one column per category level) and what was left out."""
    import numpy as np
    import pandas as pd

    parts, names, dropped = [], [], []
    for name in confounders:
        column = table[name]
        if pd.api.types.is_bool_dtype(column) or pd.api.types.is_numeric_dtype(column):
            values = pd.to_numeric(column, errors="coerce").astype(float)
            values = values.replace([np.inf, -np.inf], np.nan)
            if values.notna().sum() == 0 or values.nunique(dropna=True) < 2:
                dropped.append(f"{name} (constant or empty)")
                continue
            parts.append(values.fillna(values.median()).to_numpy()[:, None])
            names.append(name)
        else:
            levels = column.astype("string").fillna("(missing)")
            counts = levels.value_counts()
            if len(counts) < 2:
                dropped.append(f"{name} (one value)")
                continue
            if len(counts) > MAX_LEVELS:
                dropped.append(f"{name} ({len(counts)} different values: too many to adjust for)")
                continue
            for level in counts.index[1:]:  # the most common level is the reference
                parts.append((levels == level).to_numpy(dtype=float)[:, None])
                names.append(f"{name}={level}")
    matrix = np.hstack(parts) if parts else np.zeros((len(table), 0))
    return matrix, names, dropped


def _treatment(column: Any) -> tuple[Any, str, dict[str, Any]]:
    """(values, kind, description). Two distinct values are a yes/no treatment; a numeric column is a dose."""
    import numpy as np
    import pandas as pd

    present = column.dropna()
    if pd.api.types.is_numeric_dtype(column) and not np.isfinite(pd.to_numeric(present).astype(float)).all():
        raise ValueError("The treatment column has values that are not finite numbers.")
    levels = sorted(present.unique().tolist(), key=str)
    if len(levels) < 2:
        raise ValueError("The treatment column has only one value, so there is nothing to compare.")
    if len(levels) == 2:
        low, high = levels
        if pd.api.types.is_bool_dtype(column) or pd.api.types.is_numeric_dtype(column):
            low, high = sorted(levels)
        else:  # words: the level that reads as "yes" is the treated one, otherwise the later in order
            yes = {"yes", "true", "y", "treated", "treatment", "1", "on", "after", "new"}
            if str(low).strip().lower() in yes and str(high).strip().lower() not in yes:
                low, high = high, low
        return (column == high).to_numpy(dtype=float), "binary", {"treated": str(high), "untreated": str(low)}
    if not pd.api.types.is_numeric_dtype(column):
        raise ValueError(f"The treatment column has {len(levels)} different text values. Use a yes/no column or a "
                         "numeric amount.")
    values = pd.to_numeric(column, errors="coerce").astype(float).to_numpy()
    if not np.isfinite(values).all():
        raise ValueError("The treatment column has values that are not finite numbers.")
    return values, "continuous", {"unit": "one unit of the treatment column"}


def _nuisance(rows: int, regression: bool, seed: int) -> Any:
    """Helper model for the outcome or the propensity: linear for small tables, boosted trees for larger ones."""
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.linear_model import LogisticRegression, RidgeCV
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if rows < 2000:
        return make_pipeline(StandardScaler(), RidgeCV(alphas=(0.1, 1.0, 10.0)) if regression
                             else LogisticRegression(C=1.0, max_iter=500))
    return (HistGradientBoostingRegressor(max_iter=150, random_state=seed) if regression
            else HistGradientBoostingClassifier(max_iter=150, random_state=seed))


def _folds(rows: int, seed: int, strata: Any = None) -> list[tuple[Any, Any]]:
    from sklearn.model_selection import KFold, StratifiedKFold

    import numpy as np

    index = np.arange(rows)
    if strata is not None and min(int(strata.sum()), int(len(strata) - strata.sum())) >= FOLDS:
        return list(StratifiedKFold(FOLDS, shuffle=True, random_state=seed).split(index, strata))
    return list(KFold(FOLDS, shuffle=True, random_state=seed).split(index))


def _predict(model: Any, features: Any, labels: Any, score_on: Any, *, probability: bool = False) -> Any:
    import numpy as np

    if features.shape[1] == 0:  # nothing to adjust for: the helper model is the average
        return np.full(len(score_on), float(np.mean(labels)))
    fitted = model.fit(features, labels)
    return fitted.predict_proba(score_on)[:, 1] if probability else fitted.predict(score_on)


def _aipw(y: Any, t: Any, x: Any, seed: int) -> dict[str, Any]:
    """Cross-fitted augmented inverse-propensity estimate of the average effect of a yes/no treatment."""
    import numpy as np
    from sklearn.base import clone

    rows = len(y)
    propensity, mu1, mu0 = np.zeros(rows), np.zeros(rows), np.zeros(rows)
    if x.shape[1] == 0:
        # With no adjustment, use the exact difference in means and its usual
        # sampling error rather than injecting random fold-mean differences.
        effect, error = _naive(y, t, "binary")
        probability = float(np.mean(t))
        propensity.fill(probability)
        return {"effect": effect, "standard_error": error, "propensity": propensity,
                "weights": t / probability + (1 - t) / (1 - probability)}
    for fit, score in _folds(rows, seed, t):
        treated, control = fit[t[fit] == 1], fit[t[fit] == 0]
        propensity[score] = _predict(clone(_nuisance(rows, False, seed)), x[fit], t[fit], x[score], probability=True)
        mu1[score] = _predict(clone(_nuisance(rows, True, seed)), x[treated], y[treated], x[score])
        mu0[score] = _predict(clone(_nuisance(rows, True, seed)), x[control], y[control], x[score])
    raw = propensity.copy()
    propensity = np.clip(propensity, *CLIP)
    influence = mu1 - mu0 + t * (y - mu1) / propensity - (1 - t) * (y - mu0) / (1 - propensity)
    effect = float(influence.mean())
    error = float(influence.std(ddof=1) / math.sqrt(rows))
    weights = t / propensity + (1 - t) / (1 - propensity)
    return {"effect": effect, "standard_error": error, "propensity": raw, "weights": weights}


def _partial(y: Any, t: Any, x: Any, seed: int) -> dict[str, Any]:
    """Cross-fitted partially linear estimate: the change in the outcome per unit of a numeric treatment."""
    import numpy as np
    from sklearn.base import clone

    rows = len(y)
    y_left, t_left = np.zeros(rows), np.zeros(rows)
    if x.shape[1] == 0:
        y_left, t_left = y - y.mean(), t - t.mean()
    else:
        for fit, score in _folds(rows, seed):
            y_left[score] = y[score] - _predict(clone(_nuisance(rows, True, seed)), x[fit], y[fit], x[score])
            t_left[score] = t[score] - _predict(clone(_nuisance(rows, True, seed)), x[fit], t[fit], x[score])
    spread = float(np.mean(t_left ** 2))
    if spread <= 1e-12:
        raise ValueError("The adjusted columns determine the treatment completely, so its own effect cannot be "
                         "separated from theirs.")
    effect = float(np.mean(t_left * y_left) / spread)
    influence = t_left * (y_left - effect * t_left) / spread
    return {"effect": effect, "standard_error": float(influence.std(ddof=1) / math.sqrt(rows)),
            "treatment_unexplained_share": round(spread / max(float(np.var(t)), 1e-12), 4)}


def _adjusted(y: Any, t: Any, x: Any) -> float:
    """Coefficient of the treatment in a linear model of the outcome on treatment and confounders."""
    import numpy as np

    design = np.column_stack([np.ones(len(y)), t, x])
    solution, *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(solution[1])


def _naive(y: Any, t: Any, kind: str) -> tuple[float, float]:
    import numpy as np

    if kind == "binary":
        a, b = y[t == 1], y[t == 0]
        return float(a.mean() - b.mean()), float(math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))
    centred = t - t.mean()
    slope = float(np.sum(centred * (y - y.mean())) / np.sum(centred ** 2))
    residual = y - y.mean() - slope * centred
    return slope, float(math.sqrt(np.sum(residual ** 2) / (len(y) - 2) / np.sum(centred ** 2)))


def _interval(effect: float, error: float) -> list[float]:
    return [round(effect - 1.96 * error, 6), round(effect + 1.96 * error, 6)]


def e_value(effect: float, outcome_sd: float) -> float | None:
    """Approximate E-value for a continuous outcome, through the standardised difference.

    The minimum strength (as a risk ratio) of association an unmeasured common cause would need with both the
    treatment and the outcome to fully explain an effect of this size. 1.0 means none is needed.
    """
    if not math.isfinite(effect) or not math.isfinite(outcome_sd) or outcome_sd <= 0:
        return None
    log_ratio = 0.91 * abs(effect) / outcome_sd
    try:
        ratio = math.exp(log_ratio)
        value = ratio * (1 + math.sqrt(-math.expm1(-log_ratio)))
    except OverflowError:
        return None
    return round(value, 3) if math.isfinite(value) else None


def _balance(x: Any, names: list[str], t: Any, weights: Any) -> list[dict[str, Any]]:
    """Standardised mean difference of each adjusted column between treated and untreated rows, before and after
    weighting. Below 0.1 is the usual mark of "comparable"."""
    import numpy as np

    rows = []
    for index, name in enumerate(names):
        column = x[:, index]
        a, b = column[t == 1], column[t == 0]
        pooled = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2) if len(a) > 1 and len(b) > 1 else 0.0
        if pooled <= 0:
            continue
        wa, wb = weights[t == 1], weights[t == 0]
        after = (np.average(a, weights=wa) - np.average(b, weights=wb)) / pooled
        rows.append({"column": name, "difference_before": round(float((a.mean() - b.mean()) / pooled), 4),
                     "difference_after_weighting": round(float(after), 4)})
    return sorted(rows, key=lambda row: -abs(row["difference_before"]))


def estimate(table: Any, treatment: str, outcome: str, confounders: list[str] | None = None, *,
             seed: int = 0, refute: bool = True) -> dict[str, Any]:
    """Estimate the effect of ``treatment`` on ``outcome`` in a pandas table, with its checks and its limits."""
    import numpy as np
    import pandas as pd

    from app.lab_evidence import digest, environment

    started = time.perf_counter()
    if not isinstance(table, pd.DataFrame):
        raise ValueError("A table is required.")
    if not table.columns.is_unique:
        raise ValueError("Column names must be unique before estimating an effect.")
    if any(not isinstance(name, str) for name in table.columns):
        raise ValueError("Column names must be strings before estimating an effect.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("The random seed must be an integer between 0 and 2**32 - 1.")
    treatment, outcome = str(treatment or "").strip(), str(outcome or "").strip()
    if not treatment or not outcome:
        raise ValueError("Name the treatment column (what is changed) and the outcome column (what it may change).")
    chosen = _columns(table, treatment, outcome, confounders)
    work = table[[treatment, outcome, *chosen]].copy()
    outcome_values = work[outcome]
    notes: list[str] = []
    if pd.api.types.is_bool_dtype(outcome_values):
        outcome_values = outcome_values.astype(float)
    elif not pd.api.types.is_numeric_dtype(outcome_values):
        levels = sorted(outcome_values.dropna().unique().tolist(), key=str)
        if len(levels) != 2:
            raise ValueError("The outcome must be a number or a yes/no column.")
        outcome_values = (outcome_values == levels[1]).astype(float).where(work[outcome].notna())
        notes.append(f"The outcome was read as the share of rows where {outcome} is {levels[1]!r}.")
    outcome_values = pd.to_numeric(outcome_values, errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)
    keep = outcome_values.notna() & work[treatment].notna()
    left_out = int((~keep).sum())
    work, outcome_values = work.loc[keep], outcome_values.loc[keep]
    if left_out:
        notes.append(f"{left_out} rows without a treatment or an outcome value were left out.")
    if len(work) < MIN_ROWS:
        raise ValueError(f"Only {len(work)} usable rows; at least {MIN_ROWS} are needed for an estimate.")
    y = outcome_values.to_numpy()
    with np.errstate(over="ignore", invalid="ignore"):
        outcome_variance = float(np.var(y))
    if not math.isfinite(outcome_variance):
        raise ValueError("The outcome's numeric range overflows the estimator; rescale the column before estimating an effect.")
    binary_outcome = set(np.unique(y)) <= {0.0, 1.0}
    if float(np.std(y)) == 0:
        raise ValueError("The outcome has the same value in every row, so nothing can change it.")
    t, kind, described = _treatment(work[treatment])
    x, names, dropped = _design(work, chosen)
    with np.errstate(over="ignore", invalid="ignore"):
        valid_scale = math.isfinite(float(np.var(t))) and np.isfinite(np.var(x, axis=0)).all()
    if not valid_scale:
        raise ValueError("Treatment or adjustment values overflow the estimator; rescale the numeric columns.")
    if kind == "binary" and min(int(t.sum()), int(len(t) - t.sum())) < 20:
        raise ValueError("Fewer than 20 rows in one of the two treatment groups; too few to compare.")
    if confounders is None and chosen:
        notes.append("Every other column was adjusted for. A column the treatment itself changes (a step on the "
                     "way to the outcome) must not be adjusted for: name the columns yourself if one is.")
    if not names:
        notes.append("Nothing was adjusted for, so this is the plain comparison: any common cause distorts it.")
    if kind == "continuous":
        notes.append("The numeric-treatment model assumes a linear, additive treatment effect. If effects vary "
                     "across rows, this coefficient need not be the population average causal effect.")
    if not refute:
        notes.append("Refutation checks were not run; this estimate has not passed those checks.")

    naive, naive_error = _naive(y, t, kind)
    adjusted = _adjusted(y, t, x)
    robust = _aipw(y, t, x, seed) if kind == "binary" else _partial(y, t, x, seed)
    effect, error = robust["effect"], robust["standard_error"]
    if not all(math.isfinite(value) for value in (naive, naive_error, adjusted, effect, error)):
        raise ValueError("The estimator produced nonfinite results; check and rescale the input columns.")
    interval = _interval(effect, error)
    result: dict[str, Any] = {
        "treatment": treatment, "outcome": outcome, "treatment_kind": kind, "treatment_levels": described,
        "rows": int(len(y)), "adjusted_for": names, "left_out_columns": dropped, "notes": notes,
        "method": ("augmented inverse-propensity weighting (doubly robust), cross-fitted" if kind == "binary"
                   else "partially linear double machine learning, cross-fitted"),
        "effect": round(effect, 6), "standard_error": round(error, 6), "interval_95": interval,
        "naive": {"effect": round(naive, 6), "interval_95": _interval(naive, naive_error)},
        "regression_adjusted": round(adjusted, 6),
        "confounding_shift": round(naive - effect, 6),
        "unit": (f"difference in average {outcome} between {described['treated']} and {described['untreated']}"
                 if kind == "binary" else f"change in {outcome} per one unit of {treatment}"),
        "tool": "scikit-learn + numpy (causal lab)", "limits": LIMITS,
    }
    omitted = [item for item in dropped if "too many to adjust for" in item]
    problems: list[str] = (["could not adjust for nonconstant columns: " + ", ".join(omitted[:4])] if omitted else [])
    if kind == "binary":
        propensity, weights = robust["propensity"], robust["weights"]
        extreme = float(np.mean((propensity < 0.05) | (propensity > 0.95)))
        effective = float(weights.sum() ** 2 / np.sum(weights ** 2))
        result["overlap"] = {"share_with_extreme_propensity": round(extreme, 4),
                             "effective_sample_size": round(effective, 1),
                             "treated": int(t.sum()), "untreated": int(len(t) - t.sum()),
                             "note": "Rows whose chance of treatment is under 5% or over 95% have no comparable "
                                     "counterpart; a large share means the groups are too different to compare."}
        result["balance"] = _balance(x, names, t, weights)
        separated = [name for index, name in enumerate(names)
                     if np.ptp(x[t == 1, index]) == 0 and np.ptp(x[t == 0, index]) == 0
                     and x[t == 1, index][0] != x[t == 0, index][0]]
        if separated:
            problems.append("adjusted columns perfectly separate the treatment groups, so treatment overlap is absent: "
                            + ", ".join(separated[:4]))
        if extreme > 0.2:
            problems.append(f"{extreme:.0%} of rows have almost no comparable counterpart in the other group")
        unbalanced = [row["column"] for row in result["balance"] if abs(row["difference_after_weighting"]) > 0.25]
        if unbalanced:
            problems.append("still unbalanced after weighting: " + ", ".join(unbalanced[:4]))
        result["e_value"] = {"estimate": e_value(effect, float(np.std(y, ddof=1))) if not binary_outcome else None,
                             "interval_bound": (1.0 if interval[0] <= 0 <= interval[1] else
                                                e_value(min(abs(interval[0]), abs(interval[1])), float(np.std(y, ddof=1)))),
                             "approximate": True,
                             "meaning": "Approximation using a continuous-outcome standardized difference, not an exact risk-ratio bound. "
                                        "How strongly an unmeasured common cause would have to be tied to both the "
                                        "treatment and the outcome (as a risk ratio) to explain the effect away. "
                                        "Near 1 means a weak one would do."}
        if binary_outcome:
            result["e_value"].update(interval_bound=None, meaning="Not computed for a yes/no outcome: a valid adjusted "
                                    "risk ratio and its uncertainty are needed; the continuous-outcome conversion does not apply.")
    else:
        result["treatment_unexplained_share"] = robust["treatment_unexplained_share"]
        if robust["treatment_unexplained_share"] < 0.02:
            problems.append("the adjusted columns explain over 98% of the treatment, leaving almost nothing to learn from")

    if refute:
        rng = np.random.default_rng(seed + 1)
        runner = (lambda yy, tt, xx, s: _aipw(yy, tt, xx, s)["effect"]) if kind == "binary" else \
                 (lambda yy, tt, xx, s: _partial(yy, tt, xx, s)["effect"])
        placebo = np.array([runner(y, rng.permutation(t), x, seed) for _ in range(PLACEBO_RUNS)])
        beyond = float(np.mean(np.abs(placebo) >= abs(effect)))
        with_noise = runner(y, t, np.column_stack([x, rng.normal(size=len(y))]), seed)
        subsets = []
        for _ in range(SUBSET_RUNS):
            pick = np.sort(rng.choice(len(y), int(len(y) * 0.8), replace=False))
            if kind == "binary" and min(int(t[pick].sum()), int(len(pick) - t[pick].sum())) < 10:
                continue
            subsets.append(runner(y[pick], t[pick], x[pick], seed))
        spread = float(np.std(subsets)) if len(subsets) > 1 else None
        significant = not interval[0] <= 0 <= interval[1]
        checks = {
            "placebo_treatment": {
                "what": f"The treatment column shuffled {PLACEBO_RUNS} times: a real effect should disappear.",
                "average_effect": round(float(placebo.mean()), 6), "largest": round(float(np.abs(placebo).max()), 6),
                "share_as_large_as_estimate": round(beyond, 3),
                "monte_carlo_pvalue": round((int(np.sum(np.abs(placebo) >= abs(effect))) + 1) / (len(placebo) + 1), 4),
                # Only a claimed effect has to beat its placebos; "no effect" is not refuted by them.
                "passed": bool((int(np.sum(np.abs(placebo) >= abs(effect))) + 1) / (len(placebo) + 1) <= 0.05) if significant else True},
            "random_common_cause": {
                "what": "A column of pure noise added to the adjustment: the estimate should not move.",
                "effect_with_noise_column": round(with_noise, 6),
                "passed": bool(abs(with_noise - effect) <= max(2 * error, 1e-9))},
            "subset_stability": {
                "what": f"The estimate on {SUBSET_RUNS} random 80% subsets: it should stay close.",
                "spread": round(spread, 6) if spread is not None else None, "subsets": len(subsets),
                "passed": bool(spread is not None and spread <= max(3 * error, 1e-9))},
        }
        result["refutation"] = checks
        problems += [name.replace("_", " ") + " check failed" for name, check in checks.items() if not check["passed"]]
    result["problems"] = problems
    excludes_zero = not interval[0] <= 0 <= interval[1]
    result["verdict_kind"] = "unreliable" if problems else "effect" if excludes_zero else "no_effect_detected"
    size = f"{effect:+.4g} (95% interval {interval[0]:.4g} to {interval[1]:.4g})"
    if problems:
        result["verdict"] = (f"Not reliable enough to state an effect of {treatment} on {outcome}: "
                             + "; ".join(problems) + f". The figure it produced was {size}.")
    elif excludes_zero:
        result["verdict"] = (f"After adjusting for {len(names)} column{'s' if len(names) != 1 else ''}, "
                             f"{treatment} goes with a change of {size} in {outcome}; the plain comparison shows "
                             f"{naive:+.4g}. This is the causal effect only under the assumption stated in the limits.")
    else:
        result["verdict"] = (f"No effect of {treatment} on {outcome} can be told apart from zero: {size}. "
                             f"The plain comparison shows {naive:+.4g}. An effect smaller than the interval's width "
                             "would not be seen in this many rows.")
    result["next"] = ("Collect the missing common causes, or restrict to rows where the two groups overlap, and run "
                      "it again." if problems else
                      "Ask what else could cause both; if you can change the treatment yourself, a small controlled "
                      "trial settles it." if excludes_zero else
                      "More rows narrow the interval; a controlled trial is the direct test.")
    table_digest = pd.util.hash_pandas_object(work, index=False).to_numpy().tobytes()
    import hashlib

    evaluation = {"protocol": "hayat-causal-estimate-v1", "seed": seed,
                  "dataset_sha256": hashlib.sha256(table_digest).hexdigest(), "treatment": treatment,
                  "outcome": outcome, "adjusted_for": names, "folds": FOLDS, "propensity_clip": list(CLIP),
                  "refutation_enabled": bool(refute), "placebo_runs": PLACEBO_RUNS if refute else 0,
                  "subset_runs": SUBSET_RUNS if refute else 0,
                  "environment": environment("numpy", "pandas", "scikit-learn"),
                  "scope": "Records the data fingerprint and settings, not raw rows. Not a proof of causation."}
    evaluation["comparison_key"] = digest(evaluation)
    result["evaluation"] = evaluation
    result["seconds"] = round(time.perf_counter() - started, 2)
    return result


class CausalLab:
    """Runs cause-and-effect estimates for the owner and keeps every run with its checks."""
    SOURCE = "causal_lab"

    def __init__(self, path: Path | str, tables: Any = None) -> None:
        self.path = Path(path)
        #: source -> pandas table; the model-discovery loader, so the same size and path rules apply.
        self.tables = tables
        self._lock = threading.Lock()
        self.events: Any = None

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS estimates(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                   "source TEXT NOT NULL, treatment TEXT NOT NULL, outcome TEXT NOT NULL, verdict_kind TEXT NOT NULL, "
                   "effect REAL, seconds REAL NOT NULL, result_json TEXT NOT NULL)")
        return db

    def _table(self, source: str) -> Any:
        if not callable(self.tables):
            from app.model_discovery import load_table

            return load_table(str(source or "").strip())
        loaded = self.tables(source)
        return loaded[0] if isinstance(loaded, tuple) else loaded

    def run(self, source: str, treatment: str, outcome: str, confounders: list[str] | None = None) -> dict[str, Any]:
        from app.lab_evidence import compare_metric

        result = estimate(self._table(source), treatment, outcome, confounders)
        result["source"] = source
        with self._lock, closing(self._db()) as db, db:
            previous = db.execute("SELECT id,result_json FROM estimates WHERE source=? AND treatment=? AND outcome=? "
                                  "ORDER BY id DESC LIMIT 1", (source, treatment, outcome)).fetchone()
            result["comparison"] = compare_metric(result, json.loads(previous["result_json"]) if previous else None,
                                                   field="effect")
            if result["comparison"].get("comparable"):
                # An effect is not a score: a larger one is not "better".
                result["comparison"]["change"] = ("unchanged" if result["comparison"]["delta"] == 0 else "different")
                result["comparison"]["direction"] = "neither"
                result["comparison"]["previous_estimate_id"] = previous["id"]
            cursor = db.execute("INSERT INTO estimates(at,source,treatment,outcome,verdict_kind,effect,seconds,result_json) "
                                "VALUES(?,?,?,?,?,?,?,?)",
                                (datetime.now(UTC).isoformat(timespec="seconds"), source, treatment, outcome,
                                 result["verdict_kind"], result["effect"], result["seconds"],
                                 json.dumps(result, default=str)))
            result["estimate_id"] = int(cursor.lastrowid or 0)
        if self.events is not None:
            try:
                self.events.publish("LAB_RUN_COMPLETED", self.SOURCE, {"estimate_id": result["estimate_id"],
                                                                       "verdict_kind": result["verdict_kind"]})
            except Exception:
                log_swallowed(_log, "publishing a causal estimate")
        return result

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT id,at,source,treatment,outcome,verdict_kind,effect FROM estimates "
                              "ORDER BY id DESC LIMIT ?", (max(1, min(100, int(limit))),)).fetchall()
        return [dict(row) for row in rows]

    def detail(self, estimate_id: int) -> dict[str, Any] | None:
        """The complete saved estimate; nothing is fitted and the table is not read again."""
        if not self.path.is_file():
            return None
        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT id,at,result_json FROM estimates WHERE id=?", (int(estimate_id),)).fetchone()
        return {**json.loads(row["result_json"]), "estimate_id": row["id"], "at": row["at"]} if row else None

    def status(self) -> dict[str, Any]:
        rows = self.recent(1)
        if not rows:
            return {"estimates": 0, "last": "none yet"}
        with self._lock, closing(self._db()) as db:
            counts = dict(db.execute("SELECT verdict_kind, COUNT(*) FROM estimates GROUP BY verdict_kind").fetchall())
        return {"estimates": sum(counts.values()), "effects_found": counts.get("effect", 0),
                "no_effect_detected": counts.get("no_effect_detected", 0), "unreliable": counts.get("unreliable", 0),
                "last": f"{rows[0]['treatment']} → {rows[0]['outcome']}: {rows[0]['verdict_kind'].replace('_', ' ')}"}


def register_causal_tools(registry: Any, lab: CausalLab) -> None:
    def run(action: str = "estimate", source: str = "", treatment: str = "", outcome: str = "",
            confounders: list[str] | None = None, estimate_id: int = 0) -> dict[str, Any]:
        if action == "recent":
            return {"estimates": lab.recent()}
        if action == "detail":
            return {"estimate": lab.detail(estimate_id)}
        return lab.run(source, treatment, outcome, confounders)

    registry.register(
        "causal_analyze",
        "Estimate how much an outcome column changes when a treatment column is changed, from a table (CSV path): "
        "compares the plain difference with a doubly robust estimate adjusted for the other columns, gives a 95% "
        "interval, runs refutation tests (shuffled treatment, random common cause, subsets), reports overlap, balance "
        "and an E-value for unmeasured causes. Says 'unreliable' or 'no effect detected' when that is the result. "
        "An observational estimate, never a proof of causation. Use for 'does X cause Y', 'effect of X on Y'.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["estimate", "recent", "detail"]},
            "source": {"type": "string"}, "treatment": {"type": "string"}, "outcome": {"type": "string"},
            "confounders": {"type": "array", "maxItems": MAX_CONFOUNDERS, "items": {"type": "string", "maxLength": 200}},
            "estimate_id": {"type": "integer", "minimum": 1}},
         "additionalProperties": False},
        "low", "filesystem_read", run)
