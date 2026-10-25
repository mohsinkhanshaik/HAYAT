"""What a price may do next, said as honestly as statistics allow, with HAYAT's own track record.

Nobody can predict prices reliably, so HAYAT does not pretend to. It gives:
* hour, day and week ranges from completed hourly candles, calibrated with earlier errors when supported;
* trend and momentum readings (moving averages, the last day's move, RSI);
* how good those have actually been for this market: tested walk-forward on its own past,
  how often the 68 % band really contained the price a day later, and how often momentum
  got the direction right against a past-only majority-direction baseline.
The track record is part of every answer, so a band that misses or a signal no better than
chance says so itself. Not advice; HAYAT never trades.
"""
from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime
from typing import Any

WINDOW = 168  # hours of history behind each volatility estimate (one week)


def returns(closes: list[float]) -> list[float]:
    return [math.log(after / before) for before, after in zip(closes, closes[1:]) if before > 0 and after > 0]


def ema(values: list[float], span: int) -> float:
    weight, value = 2 / (span + 1), values[0]
    for item in values[1:]:
        value = weight * item + (1 - weight) * value
    return value


def rsi(closes: list[float], period: int = 14) -> float | None:
    changes = [after - before for before, after in zip(closes[-period - 1:], closes[-period:])]
    if len(changes) < period:
        return None
    gains, losses = sum(change for change in changes if change > 0), -sum(change for change in changes if change < 0)
    return (50.0 if gains == 0 else 100.0) if losses == 0 else round(100 - 100 / (1 + gains / losses), 1)


def band(price: float, sigma: float, hours: float, z: float) -> tuple[float, float]:
    spread = z * sigma * math.sqrt(hours)
    return price * math.exp(-spread), price * math.exp(spread)


def track_record(closes: list[float], horizon: int = 24, step: int = 24) -> dict[str, Any]:
    """Non-overlapping range tests and paired momentum tests against a past-only majority baseline."""
    inside = tested = right = called = up_days = baseline_right = wins = losses = 0
    for t in range(WINDOW + 24, len(closes) - horizon, max(horizon, step)):
        sigma = statistics.pstdev(returns(closes[t - WINDOW:t + 1])) or 0.0
        low, high = band(closes[t], sigma, horizon, 1.0)
        later = closes[t + horizon]
        tested += 1
        inside += low <= later <= high
        up_days += later > closes[t]
        momentum = closes[t] - closes[t - 24]
        if momentum and later != closes[t]:
            previous = [closes[i + horizon] > closes[i] for i in range(0, t - horizon + 1, horizon)]
            majority_up = sum(previous) >= len(previous) / 2
            hit = (momentum > 0) == (later > closes[t])
            baseline_hit = majority_up == (later > closes[t])
            called += 1
            right += hit
            baseline_right += baseline_hit
            wins += hit and not baseline_hit
            losses += baseline_hit and not hit
    if not tested:
        return {"tested": 0}
    from app.market_scenarios import wilson
    coverage_interval = [round(value * 100, 1) for value in wilson(inside, tested)]
    paired = wins + losses
    p_value = sum(math.comb(paired, k) for k in range(wins, paired + 1)) / 2 ** paired if paired else 1.0
    improves = called >= 20 and paired >= 10 and p_value < .05
    return {"tested": tested, "non_overlapping": True, "baseline_right_pct": round(baseline_right / called * 100, 1) if called else None,
            "direction_improves_baseline": improves, "direction_edge_p_value": round(p_value, 5),
            "band_coverage_pct": round(inside / tested * 100, 1), "band_target_pct": 68.3,
            "band_coverage_interval_pct": coverage_interval,
            "momentum_calls": called, "momentum_right_pct": round(right / called * 100, 1) if called else None,
            "always_up_right_pct": round(up_days / tested * 100, 1),
            "verdict": verdict(inside / tested, (right / called) if called else None, called, improves, tested, coverage_interval)}


def verdict(coverage: float, momentum: float | None, calls: int, improves: bool = False,
            tested: int = 0, coverage_interval: list[float] | None = None) -> str:
    parts = []
    if tested < 20:
        parts.append("too few non-overlapping tests to establish range reliability")
    elif coverage_interval and coverage_interval[0] <= 68.3 <= coverage_interval[1]:
        parts.append("observed range coverage is compatible with 68.3%, with sampling uncertainty")
    elif coverage < .683:
        parts.append("the range has been too narrow: prices moved more than recent volatility suggested")
    else:
        parts.append("the range has been wider than needed")
    if momentum is None or calls < 20:
        parts.append("too few past days to judge momentum")
    elif improves:
        parts.append(f"momentum beat the past-only majority-direction baseline ({momentum:.0%} correct)")
    else:
        parts.append(f"momentum has no demonstrated edge over the past-only majority-direction baseline ({momentum:.0%} correct)")
    return "; ".join(parts) + "."


def outlook(markets: Any, symbol: str, asset: str = "auto") -> dict[str, Any]:
    name, kind, candles = markets.candles(symbol, 60, asset)
    from app.market_forecast import calibrated_range, closed_history
    candles, quality = closed_history(candles)
    closes = [candle['close'] for candle in candles]
    if len(closes) < WINDOW + 30:
        raise ValueError(f"Not enough price history for {name}: need {WINDOW + 30} completed, contiguous hourly candles; "
                         f"found {len(closes)} after excluding unfinished or invalid bars and splitting at gaps.")
    traded = sum(candle.get("trades", 0) for candle in candles[-WINDOW:])
    recent = returns(closes[-WINDOW - 1:])
    sigma = statistics.pstdev(recent)
    price = closes[-1]
    fast, slow = ema(closes[-96:], 12), ema(closes[-200:], 48)
    day_move = (price - closes[-25]) / closes[-25] * 100
    ranges = {label: calibrated_range(closes, hours)
              for label, hours in (("next_hour", 1), ("next_day", 24), ("next_week", 168))}
    trend = "up" if fast > slow * 1.002 else "down" if fast < slow * 0.998 else "flat"
    record = track_record(closes)
    thin = traded < 50
    return {"markets": True, "outlook": True, "symbol": name, "asset": kind, "price": price,
            "computed_at": datetime.now(UTC).isoformat(timespec="seconds"), "hourly_volatility_pct": round(sigma * 100, 3),
            "daily_volatility_pct": round(sigma * math.sqrt(24) * 100, 2), "ranges": ranges,
            "trend": {"direction": trend, "ema_12h": round(fast, 6), "ema_48h": round(slow, 6), "change_24h_pct": round(day_move, 2),
                      "rsi_14h": rsi(closes)},
            "track_record": record, "thin": thin,
            "data_quality": quality,
            "source": "Kraken hourly candles (last 30 days)",
            "basis": ("Ranges use completed, contiguous hourly candles and recent volatility, with past-error calibration where enough "
                      "non-overlapping cases exist. Each horizon has its own validation; limited tests are labelled. "
                      "Trend and RSI describe the past. Not a prediction of direction and not advice."
                      + (" History is stale: these ranges do not describe a current market price." if quality['stale'] else "")
                      + (" Few trades in this market: its candles are sparse, so the volatility, and the ranges, are understated."
                         if thin else ""))}
