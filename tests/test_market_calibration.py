"""Conformal range calibration improves coverage and can never worsen the chosen ranges."""
from __future__ import annotations

import math

import numpy as np

from app import market_periods as mp


def _series_from_returns(returns):
    price = 100.0
    out = [{"close": price, "high": price, "low": price}]
    for r in returns:
        price *= math.exp(r)
        out.append({"close": price, "high": price, "low": price})
    return out


def _heavy_tailed_series(n=900, seed=7):
    """A stationary but heavy-tailed, regime-shifting return stream: the kind whose
    simple quantile bands miscalibrate, so there is something for calibration to fix."""
    rng = np.random.default_rng(seed)
    returns = []
    for i in range(n):
        vol = 0.004 if (i // 40) % 2 == 0 else 0.016  # alternating calm/volatile regimes
        step = rng.standard_t(df=3) * vol  # fat tails
        returns.append(float(step))
    return _series_from_returns(returns)


def test_calibrate_ranges_widens_and_narrows_around_the_median():
    ranges = {"p05": -2.0, "p25": -1.0, "p50": 0.0, "p75": 1.0, "p95": 2.0}
    wider = mp._calibrate_ranges(ranges, 1.5, 1.5)
    assert wider["p25"] == -1.5 and wider["p75"] == 1.5
    assert wider["p05"] == -3.0 and wider["p95"] == 3.0
    narrower = mp._calibrate_ranges(ranges, 0.5, 0.5)
    assert narrower["p95"] == 1.0 and narrower["p50"] == 0.0  # median is the anchor


def test_needed_factor_is_one_at_the_edge_and_larger_beyond_it():
    ranges = {"p05": -2.0, "p25": -1.0, "p50": 0.0, "p75": 1.0, "p95": 2.0}
    assert mp._needed_factor(1.0, ranges, "p25", "p75") == 1.0   # exactly at p75
    assert mp._needed_factor(2.0, ranges, "p25", "p75") == 2.0   # twice the half-width out
    assert mp._needed_factor(-0.5, ranges, "p25", "p75") == 0.5  # inside, below the median


def test_measure_never_picks_ranges_worse_than_the_uncalibrated_bases():
    series = _heavy_tailed_series()
    result = mp.measure(series, length=5, label="week")
    assert result["available"] is True
    chosen = mp._error(result["track_record"])
    plain = mp._error(mp.track_record([math.log(series[i + 5]["close"] / series[i]["close"])
                                       for i in range(len(series) - 5)],
                                      mp._sigmas([math.log(series[i + 1]["close"] / series[i]["close"])
                                                  for i in range(len(series) - 1)], len(series)),
                                      len(series), 5, False))
    # The selected ranges are at least as well-calibrated as the plain baseline.
    assert chosen <= plain + 1e-9


def test_calibration_is_selected_and_improves_coverage_on_a_miscalibrated_market():
    series = _heavy_tailed_series()
    closes = [c["close"] for c in series]
    count = len(closes)
    length = 5
    moves = [math.log(closes[i + length] / closes[i]) for i in range(count - length)]
    logs = [math.log(closes[i + 1] / closes[i]) for i in range(count - 1)]
    sigmas = mp._sigmas(logs, count)
    base = mp.track_record(moves, sigmas, count, length, False)
    cal = mp.calibrated_record(moves, sigmas, count, length, False)
    assert cal["tested"] >= mp.MIN_TESTS
    # On this deliberately miscalibrated stream, calibration brings the wide band's realized
    # coverage closer to the stated 90% than the raw quantile band does.
    assert abs(cal["inside_wide_pct"] - 90) <= abs(base["inside_wide_pct"] - 90)
