"""Small, inspectable forecasts for evenly spaced numeric observations.

Candidate selection uses earlier rolling origins. Reported evaluation uses later
origins with a frozen method, never the future values being predicted.
"""
from __future__ import annotations

import math
import statistics


METHODS = ("last_value", "recent_mean", "local_linear_trend")


def _predict(values: list[float], steps: int, method: str) -> float:
    if method == "last_value":
        return values[-1]
    if method == "recent_mean":
        return statistics.fmean(values[-8:])
    sample = values[-20:]
    n = len(sample)
    mean = statistics.fmean(sample)
    midpoint = (n - 1) / 2
    denominator = sum((i - midpoint) ** 2 for i in range(n))
    slope = sum((i - midpoint) * (v - mean) for i, v in enumerate(sample)) / denominator
    return mean + slope * (n - 1 + steps - midpoint)


def forecast_series(values: list[float], horizon: int = 1) -> dict:
    if (not isinstance(values, list) or not 24 <= len(values) <= 2048
            or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e100 for v in values)):
        raise ValueError("Supply 24 to 2048 finite, evenly spaced observations with magnitude at most 1e100.")
    if type(horizon) is not int or not 1 <= horizon <= 12:
        raise ValueError("Horizon must be 1 to 12 observation steps.")
    values = [float(v) for v in values]
    predictions = []
    for step in range(1, horizon + 1):
        # Non-overlapping forecast outcomes for each horizon; keep processing bounded.
        origins = list(range(8, len(values) - step + 1, step))[-40:]
        if len(origins) < 8:
            raise ValueError("Too few observations for this horizon's independent backtest origins. Reduce the horizon or add data.")
        split = len(origins) // 2
        selection, evaluation = origins[:split], origins[split:]
        losses = {method: statistics.fmean(abs(_predict(values[:origin], step, method) - values[origin + step - 1])
                                          for origin in selection) for method in METHODS}
        # Stable tie-break prefers the simple last-value baseline.
        method = min(METHODS, key=lambda name: losses[name])
        trials = [{"trained_through_index": origin - 1, "target_index": origin + step - 1,
                   "prediction": _predict(values[:origin], step, method), "observed": values[origin + step - 1],
                   "baseline": values[origin - 1]} for origin in evaluation]
        errors = [abs(t["prediction"] - t["observed"]) for t in trials]
        mae = statistics.fmean(errors)
        baseline_mae = statistics.fmean(abs(t["baseline"] - t["observed"]) for t in trials)
        spread = sorted(errors)[math.ceil(len(errors) * .9) - 1]
        point = _predict(values, step, method)
        predictions.append({"step": step, "prediction": point, "method": method,
                            "baseline_prediction": values[-1], "empirical_error_band": [point - spread, point + spread],
                            "validation": {"selection_cases": len(selection), "evaluation_cases": len(evaluation),
                                           "selection_mae": losses, "evaluation_mae": mae, "baseline_mae": baseline_mae,
                                           "beats_baseline": mae < baseline_mae, "trials": trials}})
    return {"observations": len(values), "horizon": horizon, "predictions": predictions,
            "assumptions": "Observations are evenly spaced and in chronological order, with no missing periods.",
            "uncertainty": "Bands use the 90th percentile of held-out absolute errors. They are empirical error ranges, not guaranteed future coverage.",
            "limitations": "Simple numeric extrapolation only; no causal, seasonal or external-event model. Regime changes can invalidate past performance."}
