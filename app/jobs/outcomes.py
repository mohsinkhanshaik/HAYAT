"""Observed application milestones and a bounded, conditional reply forecast.

The forecast models reaching an interview/offer within a fixed observation
window. It is not a probability of being hired by an individual employer.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any


def timestamp(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def observations(applications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    sent_states = {"applied", "interview", "offer", "rejected"}
    for row in applications:
        events = row.get("events") or []
        states = {event.get("status") for event in events} | {row.get("status")}
        if not row.get("applied_at") and not (states & sent_states):
            continue
        applied = timestamp(row.get("applied_at"))
        if applied is None:
            dates = [stamp for event in events if event.get("status") == "applied"
                     and (stamp := timestamp(event.get("at"))) is not None]
            applied = min(dates) if dates else None
        result.append({**row, "submitted_at": applied,
                       "interview_reached": bool(states & {"interview", "offer"}),
                       "offer_reached": "offer" in states})
    return result


def predictive_counts(successes: int, trials: int, size: int = 10) -> dict[str, Any]:
    """Beta-binomial posterior predictive counts with a uniform Beta(1,1) prior."""
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (successes, trials, size)) or not 0 <= successes <= trials or trials < 0 or not 1 <= size <= 50:
        raise ValueError("Invalid observed successes, trials or forecast batch size")
    a, b = successes + 1, trials - successes + 1
    def log_beta(x, y):
        return math.lgamma(x) + math.lgamma(y) - math.lgamma(x + y)
    weights = [math.exp(math.lgamma(size + 1) - math.lgamma(k + 1) - math.lgamma(size - k + 1)
                        + log_beta(k + a, size - k + b) - log_beta(a, b)) for k in range(size + 1)]
    total = sum(weights)
    probabilities = [weight / total for weight in weights]
    def quantile(q):
        cumulative = 0.0
        for k, probability in enumerate(probabilities):
            cumulative += probability
            if cumulative >= q:
                return k
        return size
    return {"applications": size, "expected_interview_reaching": round(size * a / (a + b), 2),
            "predictive_interval": [quantile(.05), quantile(.95)], "interval_mass": .90,
            "probabilities": probabilities, "model": "beta-binomial", "prior": "Beta(1,1)"}


def outcome_summary(applications: list[dict[str, Any]], now: datetime | None = None,
                    window_days: int = 14) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if not 1 <= window_days <= 90:
        raise ValueError("Observation window must be 1–90 days")
    rows = observations(applications)
    mature, recent, unknown, successes = [], 0, 0, 0
    for row in rows:
        applied = row["submitted_at"]
        if applied is None or applied > now:
            unknown += 1
            continue
        if now - applied < timedelta(days=window_days):
            recent += 1
            continue
        dates = [stamp for event in row.get("events") or [] if event.get("status") in {"interview", "offer"}
                 and (stamp := timestamp(event.get("at"))) is not None and applied <= stamp <= now]
        if row["interview_reached"] and not dates:
            unknown += 1
            continue
        mature.append(row)
        successes += int(any(stamp - applied <= timedelta(days=window_days) for stamp in dates))
    forecast = predictive_counts(successes, len(mature)) if len(mature) >= 10 else None
    return {"measured_at": now.isoformat(), "window_days": window_days, "submitted": len(rows),
            "interview_reached": sum(row["interview_reached"] for row in rows),
            "offer_reached": sum(row["offer_reached"] for row in rows),
            "matured": len(mature), "within_window": successes, "recent_excluded": recent,
            "unknown_timing_excluded": unknown, "forecast": forecast,
            "reason": "Conditional forecast available" if forecast else
                      "At least 10 applications with complete, matured observation windows are needed",
            "assumptions": "Self-reported milestones must be complete. The model assumes future applications are comparable "
                           "and share a stable response rate; roles, employers, search quality and market conditions may change. "
                           "The 90% range is a model-based predictive interval, not independently calibrated accuracy, "
                           "an offer probability or a prediction for a specific employer."}
