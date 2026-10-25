"""Possibilities by time period: what a market did over every past period from an hour to a year, measured.

Nobody can say where a price will be, so HAYAT does not. For each period it looks at every past
stretch of the same length in this market's own history and counts what happened:

* **Periods:** 1 hour and 4 hours (30 days of hourly candles); 1 day, 1 week and 1 month (two
  years of daily candles); 3 months, 6 months and 1 year (weekly candles: back to 2013 for
  Bitcoin). Kraken's tokenized stocks only began trading in 2025, so their long periods have too
  little history, and the answer says so instead of guessing.
* **Chance higher** at the end of the period. Its 95 % interval counts *separate* periods: a
  year of overlapping daily windows is still only one year of evidence.
* **After a move like the last one:** how often it was higher after past periods that followed a
  rise (or a fall) over the same length, and whether that changed anything ("within the noise").
* **Price ranges:** the 5th, 25th, 50th, 75th and 95th percentile of past moves, applied to the
  live price. Each range is computed two ways, from all past periods as they were and scaled to
  today's volatility, and HAYAT uses whichever held better in its own walk-forward test.
* **Possibilities:** how often the move was a big fall, a fall, about flat, a rise or a big rise
  (steps sized to the period), and how often the price *touched* a step up or down on the way,
  even when it ended elsewhere.
* **Track record:** each range tested on the market's past using only data from before each
  test. The middle range should hold about half the time and the wide one about 9 in 10.
* **Scheduled:** Fed decisions, jobs reports and earnings that fall inside the period.
* **Calm or volatile times like now:** the chance higher, and how wide the moves were, in past periods
  that began as calm (or as volatile) as today, against all periods.
* **Do the "like now" odds help?** Each test point uses only earlier data: the odds after a like move,
  and the plain odds, are scored against what happened (Brier score; lower is better). If the
  conditional odds were no better, the answer says so.

Shares of past periods, never a forecast. Not advice; HAYAT never trades.
"""
from __future__ import annotations

import logging
import math
import re
import statistics
from datetime import UTC, datetime
from typing import Any

from app.logging import log_swallowed
from app.market_scenarios import wilson

_log = logging.getLogger(__name__)

#: (key, label, candle minutes, candles per period)
PERIODS = (("1h", "1 hour", 60, 1), ("4h", "4 hours", 60, 4), ("1d", "1 day", 1440, 1), ("1w", "1 week", 1440, 7),
           ("1m", "1 month", 1440, 30), ("3m", "3 months", 10080, 13), ("6m", "6 months", 10080, 26), ("1y", "1 year", 10080, 52))
LABELS = {key: label for key, label, _minutes, _count in PERIODS}
DAYS = {"1h": 1 / 24, "4h": 1 / 6, "1d": 1, "1w": 7, "1m": 30, "3m": 91, "6m": 182, "1y": 365}
CANDLES = {60: "hourly", 1440: "daily", 10080: "weekly"}
MIN_PERIODS = 10  # separate past periods needed before any share is shown
MIN_CASES = 15  # separate past periods after a like move needed before a verdict
MIN_TESTS = 8  # walk-forward tests needed before a range's track record is judged
VOL_WINDOW = 20  # candles behind each volatility reading
STEPS = (.1, .2, .25, .5, 1, 2, 2.5, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100)  # possible step sizes, in percent
SHARES = (("p05", .05), ("p25", .25), ("p50", .5), ("p75", .75), ("p95", .95))
#: Markets compared when no list is given and the watchlist is empty.
BOARD = (("BTC", "crypto"), ("ETH", "crypto"), ("SOL", "crypto"), ("SPY", "stock"), ("QQQ", "stock"), ("GLD", "stock"),
         ("NVDA", "stock"), ("AAPL", "stock"))


def quantile(values: list[float], share: float) -> float:
    """Linear-interpolated quantile of sorted values."""
    if not values:
        return 0.0
    position = share * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def nice_step(typical_pct: float) -> float:
    """The round step size (in percent) nearest the typical move."""
    if typical_pct <= 0:
        return STEPS[0]
    return min(STEPS, key=lambda step: abs(math.log(step / typical_pct)))


def period_key(text: str) -> str | None:
    """'next 3 months' → '3m', 'in a year' → '1y', 'long term' → '1y': the measured period nearest what was asked."""
    value = " ".join(str(text or "").lower().split())
    if re.search(r"long[\s-]term", value):
        return "1y"
    if re.search(r"short[\s-]term|tomorrow", value):
        return "1d"
    unit = re.search(r"\b(hour|day|week|month|quarter|year)s?\b", value)
    if not unit:
        return None
    words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "nine": 9, "twelve": 12, "half a": .5}
    count = re.search(r"\b(\d{1,2}|half a|one|two|three|four|five|six|nine|twelve|a|an)\s+" + unit.group(1), value)
    raw = count.group(1) if count else "1"
    number = float(words[raw]) if raw in words else float(raw)
    days = number * {"hour": 1 / 24, "day": 1, "week": 7, "month": 30, "quarter": 91, "year": 365}[unit.group(1)]
    return min(DAYS, key=lambda key: abs(math.log(DAYS[key] / days))) if days > 0 else None


def _sigmas(logs: list[float], count: int) -> list[float | None]:
    """Volatility at each candle: the spread of the VOL_WINDOW one-candle moves that ended there."""
    result: list[float | None] = [None] * count
    for index in range(VOL_WINDOW, count):
        result[index] = statistics.pstdev(logs[index - VOL_WINDOW:index]) or None
    return result


def _ranges(moves: list[float], sigmas: list[float | None], upto: int, scaled: bool, sigma_now: float | None) -> dict[str, float] | None:
    """Quantiles of log moves for periods that started before `upto` (exclusive), plain or scaled to sigma_now."""
    if scaled:
        if not sigma_now:
            return None
        values = sorted(moves[start] / sigmas[start] for start in range(VOL_WINDOW, upto) if sigmas[start])
        factor = sigma_now
    else:
        values = sorted(moves[:upto])
        factor = 1.0
    if len(values) < 30:
        return None
    return {key: quantile(values, share) * factor for key, share in SHARES}


def track_record(moves: list[float], sigmas: list[float | None], count: int, length: int, scaled: bool) -> dict[str, Any]:
    """Walk-forward: at each past test point, ranges from earlier periods only; did the outcome land inside?"""
    first = VOL_WINDOW + max(MIN_PERIODS * length, 30) + length
    tested = middle = wide = 0
    for start in range(first, count - length, length):
        ranges = _ranges(moves, sigmas, start - length + 1, scaled, sigmas[start])
        if ranges is None:
            continue
        outcome = moves[start]
        tested += 1
        middle += ranges["p25"] <= outcome <= ranges["p75"]
        wide += ranges["p05"] <= outcome <= ranges["p95"]
    if not tested:
        return {"tested": 0, "verdict": "too few past tests to judge these ranges"}
    inside_middle, inside_wide = round(middle / tested * 100, 1), round(wide / tested * 100, 1)
    return {"tested": tested, "inside_middle_pct": inside_middle, "inside_wide_pct": inside_wide,
            "verdict": record_verdict(tested, inside_middle, inside_wide)}


def record_verdict(tested: int, inside_middle: float, inside_wide: float) -> str:
    if tested < MIN_TESTS:
        return "too few past tests to judge these ranges"
    if abs(inside_wide - 90) <= 7 and abs(inside_middle - 50) <= 12:
        return "the ranges held about as often as stated"
    if inside_wide < 83 or inside_middle < 38:
        return "the ranges were too narrow: prices moved more than this market's past suggested"
    return "the ranges were wider than needed"


def _error(record: dict[str, Any]) -> float:
    if record.get("tested", 0) < MIN_TESTS:
        return math.inf
    return abs(record["inside_middle_pct"] - 50) + abs(record["inside_wide_pct"] - 90)


def _needed_factor(outcome: float, ranges: dict[str, float], lo_key: str, hi_key: str) -> float:
    """The band-width factor at which this outcome first falls inside [lo, hi] around the median.

    Scaling the median-to-edge distance by this factor makes a band that exactly reaches the
    outcome. The q-th percentile of these over the past is the factor whose band held q of them --
    which is how the calibrated ranges are sized.
    """
    center = ranges["p50"]
    half = (ranges[hi_key] - center) if outcome >= center else (center - ranges[lo_key])
    if half <= 0:
        return math.inf
    return abs(outcome - center) / half


def calibrated_record(moves: list[float], sigmas: list[float | None], count: int, length: int, scaled: bool) -> dict[str, Any]:
    """Walk-forward coverage of conformally-calibrated ranges, honest: each test's width factor
    comes only from earlier tests, never from the point being scored."""
    first = VOL_WINDOW + max(MIN_PERIODS * length, 30) + length
    mid_scores: list[float] = []
    wide_scores: list[float] = []
    tested = middle = wide = 0
    for start in range(first, count - length, length):
        ranges = _ranges(moves, sigmas, start - length + 1, scaled, sigmas[start])
        if ranges is None:
            continue
        outcome = moves[start]
        need_mid = _needed_factor(outcome, ranges, "p25", "p75")
        need_wide = _needed_factor(outcome, ranges, "p05", "p95")
        usable_mid = [value for value in mid_scores if math.isfinite(value)]
        usable_wide = [value for value in wide_scores if math.isfinite(value)]
        if len(usable_mid) >= MIN_TESTS and len(usable_wide) >= MIN_TESTS:
            factor_mid = quantile(sorted(usable_mid), 0.50)
            factor_wide = quantile(sorted(usable_wide), 0.90)
            tested += 1
            middle += need_mid <= factor_mid
            wide += need_wide <= factor_wide
        mid_scores.append(need_mid)
        wide_scores.append(need_wide)
    if not tested:
        return {"tested": 0, "verdict": "too few past tests to judge these ranges"}
    inside_middle, inside_wide = round(middle / tested * 100, 1), round(wide / tested * 100, 1)
    return {"tested": tested, "inside_middle_pct": inside_middle, "inside_wide_pct": inside_wide,
            "verdict": record_verdict(tested, inside_middle, inside_wide)}


def _calibration_factors(moves: list[float], sigmas: list[float | None], count: int, length: int, scaled: bool) -> tuple[float, float] | None:
    """The final width factors from every walk-forward test, for sizing the ranges that get reported."""
    first = VOL_WINDOW + max(MIN_PERIODS * length, 30) + length
    mid_scores: list[float] = []
    wide_scores: list[float] = []
    for start in range(first, count - length, length):
        ranges = _ranges(moves, sigmas, start - length + 1, scaled, sigmas[start])
        if ranges is None:
            continue
        mid = _needed_factor(moves[start], ranges, "p25", "p75")
        wide = _needed_factor(moves[start], ranges, "p05", "p95")
        if math.isfinite(mid):
            mid_scores.append(mid)
        if math.isfinite(wide):
            wide_scores.append(wide)
    if len(mid_scores) < MIN_TESTS or len(wide_scores) < MIN_TESTS:
        return None
    return quantile(sorted(mid_scores), 0.50), quantile(sorted(wide_scores), 0.90)


def _calibrate_ranges(ranges: dict[str, float], factor_mid: float, factor_wide: float) -> dict[str, float]:
    """Resize the middle and wide bands around the median by the calibrated factors."""
    center = ranges["p50"]
    return {
        "p05": center - factor_wide * (center - ranges["p05"]),
        "p25": center - factor_mid * (center - ranges["p25"]),
        "p50": center,
        "p75": center + factor_mid * (ranges["p75"] - center),
        "p95": center + factor_wide * (ranges["p95"] - center),
    }


def like_now_verdict(cases: int, low: float, high: float, usual: float, direction: str, label: str) -> str:
    if cases < MIN_CASES:
        return "too few past cases to tell"
    if low > usual:
        return f"rose more often than usual after a {direction}"
    if high < usual:
        return f"rose less often than usual after a {direction}"
    return f"within the noise: a {direction} over the last {label} has not changed the odds"


def volatility_state(sigma: float | None, usual: float | None) -> str:
    if not sigma or not usual:
        return "normal"
    return "volatile" if sigma > usual * 1.3 else "calm" if sigma < usual * .75 else "normal"


def calm_or_volatile(moves: list[float], sigmas: list[float | None], length: int, chance: dict[str, Any], label: str) -> dict[str, Any] | None:
    """How periods that began in today's volatility state ended, against all periods."""
    known = [value for value in sigmas if value]
    if len(known) < 30 or not sigmas[-1]:
        return None
    usual = statistics.median(known)
    state = volatility_state(sigmas[-1], usual)
    matched = [start for start in range(len(moves)) if sigmas[start] and volatility_state(sigmas[start], usual) == state]
    if not matched:
        return None
    share = sum(moves[start] > 0 for start in matched) / len(matched)
    cases = max(1, round(len(matched) / length))
    low, high = wilson(share * cases, cases)
    spread = lambda values: quantile(sorted(values), .95) - quantile(sorted(values), .05)
    wide_all, wide_now = spread(moves), spread([moves[start] for start in matched])
    if cases < MIN_CASES:
        verdict = "too few past cases to tell"
    elif low * 100 > chance["up_pct"]:
        verdict = f"rose more often than usual in {state} times"
    elif high * 100 < chance["up_pct"]:
        verdict = f"rose less often than usual in {state} times"
    else:
        verdict = f"within the noise: {state} times have not changed the odds over {label}"
    return {"state": state, "now_vs_usual": round(sigmas[-1] / usual, 2), "up_pct": round(share * 100, 1),
            "interval_pct": [round(low * 100, 1), round(high * 100, 1)], "separate_periods": cases,
            "width_ratio": round(wide_now / wide_all, 2) if wide_all else None, "verdict": verdict}


def odds_test(moves: list[float], closes: list[float], length: int) -> dict[str, Any]:
    """Walk-forward Brier scores: the odds after a like move against the plain odds, each from earlier data only."""
    first = max(MIN_PERIODS * length, 30) + 2 * length
    plain_score = like_score = 0.0
    tested = 0
    for start in range(first, len(moves), length):
        earlier = range(length, start - length + 1)
        if not len(earlier):
            continue
        outcome = 1.0 if moves[start] > 0 else 0.0
        base = sum(moves[index] > 0 for index in earlier) / len(earlier)
        rising = closes[start] > closes[start - length]
        same = [index for index in earlier if (closes[index] > closes[index - length]) == rising]
        like = sum(moves[index] > 0 for index in same) / len(same) if len(same) >= 10 else base
        plain_score += (base - outcome) ** 2
        like_score += (like - outcome) ** 2
        tested += 1
    if tested < MIN_TESTS:
        return {"tested": tested, "verdict": "too few past tests to judge the like-now odds"}
    plain_score, like_score = plain_score / tested, like_score / tested
    better = plain_score - like_score
    verdict = ("the like-now odds called it better than the plain odds" if better > .005 else
               "the like-now odds did worse than the plain odds: ignore them" if better < -.005 else
               "the like-now odds were no better than the plain odds")
    return {"tested": tested, "brier_like_now": round(like_score, 4), "brier_plain": round(plain_score, 4), "verdict": verdict}


def measure(series: list[dict[str, float]], length: int, label: str) -> dict[str, Any]:
    """Every measurement for one period from candles (oldest first), the last one carrying the live price."""
    closes = [candle["close"] for candle in series]
    highs = [max(candle.get("high", candle["close"]), candle["close"]) for candle in series]
    lows = [min(candle.get("low", candle["close"]) or candle["close"], candle["close"]) for candle in series]
    count = len(closes)
    separate = (count - 1) // length if count > 1 else 0
    if separate < MIN_PERIODS:
        return {"available": False, "separate_periods": separate,
                "reason": f"too little history: only {separate} separate period{'s' if separate != 1 else ''} of {label} "
                          f"(at least {MIN_PERIODS} needed)"}
    price = closes[-1]
    starts = range(count - length)
    moves = [math.log(closes[start + length] / closes[start]) for start in starts]
    simple = [math.exp(move) - 1 for move in moves]
    up_share = sum(move > 0 for move in simple) / len(simple)
    low, high = wilson(up_share * separate, separate)
    chance = {"up_pct": round(up_share * 100, 1), "interval_pct": [round(low * 100, 1), round(high * 100, 1)], "separate_periods": separate}
    flat_pct = round(sum(move == 0 for move in simple) / len(simple) * 100, 1)
    typical = statistics.median([abs(move) for move in simple if move] or [0.0]) * 100
    step = nice_step(typical) / 100
    edges = (("big_fall", f"fell over {step * 200:g}%", lambda move: move <= -2 * step),
             ("fall", f"fell {step * 100:g}–{step * 200:g}%", lambda move: -2 * step < move <= -step),
             ("flat", f"within ±{step * 100:g}%", lambda move: -step < move < step),
             ("rise", f"rose {step * 100:g}–{step * 200:g}%", lambda move: step <= move < 2 * step),
             ("big_rise", f"rose over {step * 200:g}%", lambda move: move >= 2 * step))
    possibilities = [{"key": key, "label": text, "share_pct": round(sum(map(test, simple)) / len(simple) * 100, 1)}
                     for key, text, test in edges]
    touched_up = sum(max(highs[start + 1:start + length + 1]) / closes[start] - 1 >= step for start in starts)
    touched_down = sum(min(lows[start + 1:start + length + 1]) / closes[start] - 1 <= -step for start in starts)
    touch = {"step_pct": round(step * 100, 2), "rise_pct": round(touched_up / len(simple) * 100, 1),
             "fall_pct": round(touched_down / len(simple) * 100, 1)}
    # After a move like the last one: periods that followed a move in the same direction over the same length.
    like_now = None
    last = closes[-1] / closes[-1 - length] - 1
    if last:
        direction = "rise" if last > 0 else "fall"
        matched = [start for start in starts if start >= length and (closes[start] / closes[start - length] - 1 > 0) == (last > 0)
                   and closes[start] != closes[start - length]]
        if matched:
            share = sum(simple[start] > 0 for start in matched) / len(matched)
            cases = max(1, round(len(matched) / length))
            low_now, high_now = wilson(share * cases, cases)
            like_now = {"direction": direction, "past_move_pct": round(last * 100, 2), "up_pct": round(share * 100, 1),
                        "interval_pct": [round(low_now * 100, 1), round(high_now * 100, 1)], "separate_periods": cases,
                        "verdict": like_now_verdict(cases, low_now * 100, high_now * 100, chance["up_pct"], direction, label)}
    # Ranges: plain or scaled to today's volatility, whichever held better on this market's own past.
    logs = [math.log(after / before) for before, after in zip(closes, closes[1:])]
    sigmas = _sigmas(logs, count)
    plain, scaled = track_record(moves, sigmas, count, length, False), track_record(moves, sigmas, count, length, True)
    # Four candidates, each scored on this market's own past: plain or volatility-scaled ranges,
    # each as-is or conformally recalibrated so the stated 50%/90% ranges held that often. The
    # calibrated one is used only when it scored better, so it can never make the ranges worse.
    plain_cal = calibrated_record(moves, sigmas, count, length, False)
    scaled_cal = calibrated_record(moves, sigmas, count, length, True)
    candidates = [
        (_error(plain), False, False, plain),
        (_error(scaled), True, False, scaled),
        (_error(plain_cal), False, True, plain_cal),
        (_error(scaled_cal), True, True, scaled_cal),
    ]
    best_error, use_scaled, use_calibrated, record = min(candidates, key=lambda item: item[0])
    other = scaled if not use_scaled else plain
    ranges = _ranges(moves, sigmas, len(moves), use_scaled, sigmas[-1]) or _ranges(moves, sigmas, len(moves), False, None)
    if ranges is None:  # fewer than 30 windows: quantiles of what there is
        values = sorted(moves)
        ranges = {key: quantile(values, share) for key, share in SHARES}
    if use_calibrated and math.isfinite(best_error):
        factors = _calibration_factors(moves, sigmas, count, length, use_scaled)
        if factors is not None:
            ranges = _calibrate_ranges(ranges, *factors)
    levels = {key: {"price": round(price * math.exp(value), 6), "move_pct": round((math.exp(value) - 1) * 100, 2)}
              for key, value in ranges.items()}
    return {"available": True, "separate_periods": separate, "chance_higher": chance, "flat_pct": flat_pct, "like_now": like_now,
            "volatility": calm_or_volatile(moves, sigmas, length, chance, label), "odds_test": odds_test(moves, closes, length),
            "range": levels,
            "method": ("scaled to today's volatility" if use_scaled else "all past periods as they were")
                      + (", recalibrated so the ranges held as stated" if use_calibrated else ""),
            "calibrated": use_calibrated,
            "typical_move_pct": round(typical, 2), "possibilities": possibilities, "touch": touch, "track_record": record,
            "track_record_other": other}


def trim(rows: list[dict[str, float]]) -> list[dict[str, float]]:
    """Candles with a price, without the untraded run before a market opened (tokenized shares repeat one price there)."""
    rows = [row for row in rows if row["close"] > 0]
    first = next((index for index, row in enumerate(rows) if row.get("trades", 1)), len(rows))
    return rows[first:]


def _live(markets: Any, name: str, kind: str) -> float | None:
    stream = getattr(markets, "live", None)
    if stream is None:
        return None
    try:
        from app.markets_live import ws_symbol
        item = (stream.snapshot([ws_symbol(name, kind)])["prices"] or {}).get(ws_symbol(name, kind)) or {}
        return item.get("price")
    except Exception:  # the last close stands in for the live price
        log_swallowed(_log, "reading the live price for periods")
        return None


def _scheduled(markets: Any, name: str) -> list[dict[str, Any]]:
    calendar = getattr(markets, "calendar", None)
    if calendar is None:
        return []
    try:
        from app.market_calendar import EVENT_MARKETS
        coming = calendar.upcoming(120)
    except Exception:  # periods stand without the calendar
        log_swallowed(_log, "reading the calendar for periods")
        return []
    found = []
    for item in coming.get("events", []):
        touched = [symbol for symbol, _asset in EVENT_MARKETS.get(item.get("kind"), ())] + ([item["symbol"]] if item.get("symbol") else [])
        if name in touched:
            found.append({"title": item["title"], "days_until": item["days_until"]})
    return found


def periods(markets: Any, symbol: str, asset: str = "auto", period: str = "all", live_price: float | None = None) -> dict[str, Any]:
    """Every period's measurements for one market; `period` picks the one to dwell on ('all' for none)."""
    if period != "all" and period not in LABELS:
        raise ValueError("Choose a period: " + ", ".join(LABELS) + " or all.")
    by_interval: dict[int, tuple[str, str, list[dict[str, float]]] | Exception] = {}
    for interval in sorted({minutes for _key, _label, minutes, _count in PERIODS}):
        try:
            name, kind, rows = markets.candles(symbol, interval, asset)
            by_interval[interval] = (name, kind, trim(rows))
        except Exception as exc:  # one missing candle size leaves the other periods standing
            if interval == 1440:
                raise
            log_swallowed(_log, f"reading {CANDLES[interval]} candles for periods")
            by_interval[interval] = exc
    name, kind, _daily = by_interval[1440]
    if live_price is None:
        live_price = _live(markets, name, kind)
    live = live_price is not None and live_price > 0
    price = float(live_price) if live else by_interval[1440][2][-1]["close"]
    scheduled = _scheduled(markets, name)
    rows = []
    for key, label, minutes, length in PERIODS:
        found = by_interval[minutes]
        base = {"key": key, "label": label, "candles": CANDLES[minutes]}
        if isinstance(found, Exception) or not found[2]:
            rows.append({**base, "available": False, "separate_periods": 0,
                         "reason": f"Kraken returned no {CANDLES[minutes]} candles for {name}"})
            continue
        series = [dict(row) for row in found[2]]
        series[-1].update(close=price, high=max(series[-1].get("high", price), price), low=min(series[-1].get("low", price), price))
        row = {**base, "history_from": datetime.fromtimestamp(series[0]["time"], UTC).date().isoformat(),
               **measure(series, length, label)}
        days = DAYS[key]
        if row["available"] and days >= 1:
            row["scheduled"] = [item for item in scheduled if item["days_until"] <= days]
        rows.append(row)
    available = [row for row in rows if row["available"]]
    if not available:
        raise ValueError(f"{name} has too little history for any period.")
    picks = [row for row in available if row["key"] in ("1d", "1m")] + ([available[-1]] if available[-1]["key"] not in ("1d", "1m") else [])
    summary = (f"{name}: " + "; ".join(f"higher after {row['label']} in {row['chance_higher']['up_pct']}% of past periods "
                                        f"({row['chance_higher']['interval_pct'][0]}–{row['chance_higher']['interval_pct'][1]}%)"
                                        for row in picks) + ".")
    longest = available[-1]["label"]
    missing = [row["label"] for row in rows if not row["available"]]
    return {"markets": True, "periods_view": True, "symbol": name, "asset": kind, "price": price, "live": live, "focus": period,
            "computed_at": datetime.now(UTC).isoformat(timespec="seconds"), "periods": rows, "summary": summary,
            "longest_measured": longest, "missing": missing,
            "source": "Kraken hourly, daily and weekly candles" + (" with the live streamed price as the latest close" if live else ""),
            "basis": ("Shares of past periods, not forecasts. Long periods mirror this market's own past growth or decline, which "
                      "need not repeat. Ranges are tested on its own past; news can break them. Not advice; HAYAT never trades."
                      + (f" Too little history for {', '.join(missing)}." if missing else ""))}


def compare(markets: Any, symbols: list[str] | None = None, period: str = "1m") -> dict[str, Any]:
    """One period across several markets: chance higher, middle move, wide range and how well the ranges held."""
    if period not in LABELS:
        raise ValueError("Choose a period: " + ", ".join(LABELS) + ".")
    wanted: list[tuple[str, str]] = [(symbol, "auto") for symbol in symbols or [] if str(symbol).strip()][:12]
    origin = "given"
    if not wanted and getattr(markets, "path", None):
        try:
            wanted = [(symbol, "auto") for symbol in markets.watchlist("list")["symbols"]][:12]
            origin = "watchlist"
        except Exception:  # the default board stands in for an unreadable watchlist
            log_swallowed(_log, "reading the watchlist for a period comparison")
    if not wanted:
        wanted, origin = list(BOARD), "default"
    minutes, length, label = next((minutes, count, text) for key, text, minutes, count in PERIODS if key == period)
    rows = []
    for symbol, asset in wanted:
        try:
            name, kind, series = markets.candles(symbol, minutes, asset)
            series = [dict(row) for row in trim(series)]
            price = _live(markets, name, kind)
            if price and series:
                series[-1].update(close=float(price), high=max(series[-1]["high"], price), low=min(series[-1]["low"], price))
            value = measure(series, length, label)
            rows.append({"symbol": name, "asset": kind, "price": series[-1]["close"] if series else None, **value})
        except Exception as exc:  # one unknown market does not stop the comparison
            rows.append({"symbol": str(symbol).upper(), "available": False, "reason": str(exc)[:160]})
    rows.sort(key=lambda row: (not row["available"], -(row.get("chance_higher") or {}).get("up_pct", 0)))
    return {"markets": True, "periods_compare": True, "period": period, "label": label, "origin": origin, "rows": rows,
            "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": f"Kraken {CANDLES[minutes]} candles",
            "basis": ("Ranked by how often each was higher after past periods of this length. The 95 % intervals usually overlap: "
                      "a few points' difference is noise, not an edge. Not advice; HAYAT never trades.")}


def explain(model: Any, value: dict[str, Any]) -> str:
    """A plain summary by HAYAT's local model, from these numbers only."""
    import json
    from app.models.base import Message
    from app.models.streaming import generate_answer
    if model is None:
        raise RuntimeError("HAYAT's local model is not available in this session.")
    rows = [{key: row.get(key) for key in ("label", "chance_higher", "range", "like_now", "volatility", "odds_test", "track_record")}
            for row in value["periods"] if row.get("available")]
    facts = {"symbol": value["symbol"], "price": value["price"], "periods": rows, "missing": value.get("missing", [])}
    answer = generate_answer(model, [
        Message("system", "You explain market statistics to a non-expert in 5 to 7 short sentences. Use only the numbers given. Say what "
                          "the chance of ending higher and the ranges mean for short and long periods, whether the like-now odds and calm or "
                          "volatile odds changed anything, and where the ranges were too narrow. Never predict a price, never give advice "
                          "to buy or sell, and say plainly when the odds are no better than a coin flip."),
        Message("user", json.dumps(facts, ensure_ascii=False, default=str)[:12000])], temperature=.2, max_tokens=480)
    return str(getattr(answer, "content", answer)).strip()


def _money(value: float) -> str:
    from app.markets import money
    return money(value)


def _range_text(row: dict[str, Any]) -> str:
    levels = row["range"]
    return (f"middle half {_money(levels['p25']['price'])} – {_money(levels['p75']['price'])}, "
            f"9 in 10 {_money(levels['p05']['price'])} – {_money(levels['p95']['price'])}")


def plain_odds(up_pct: float, interval: list[float] | tuple[float, float]) -> str:
    """What a "higher in N% (low–high%)" share means, in words: a coin flip unless the history clearly says more."""
    low, high = interval
    if low <= 50 <= high:
        return "about a coin flip: the past does not lean either way"
    return "more often up than down in the past" if up_pct > 50 else "more often down than up in the past"


def plain_lead(value: dict[str, Any]) -> str | None:
    """The one sentence a person needs before the table (owner asked for answers that are easy to follow, 2026-10-24)."""
    focus = value.get("focus", "all")
    rows = [row for row in value.get("periods", []) if row.get("available")]
    row = next((row for row in rows if row["key"] == focus), None) or next((row for row in rows if row["key"] == "1m"), None)
    if row is None:
        return None
    chance, levels = row["chance_higher"], row["range"]
    return (f"💬 In plain words: nobody can know. Over past {row['label']} periods {value['symbol']} ended higher "
            f"{chance['up_pct']}% of the time, {plain_odds(chance['up_pct'], chance['interval_pct'])}. "
            f"Half of them ended between {_money(levels['p25']['price'])} and {_money(levels['p75']['price'])}.")


def report_lines(value: dict[str, Any]) -> list[str]:
    """Chat lines for a periods view or a comparison, written from the data."""
    if value.get("periods_compare"):
        lines = [f"Chance higher after {value['label']}, across markets ({'your watchlist' if value['origin'] == 'watchlist' else 'a standard set' if value['origin'] == 'default' else 'as asked'}):"]
        for row in value["rows"]:
            if not row["available"]:
                lines.append(f"  {row['symbol']}: {row['reason']}")
                continue
            chance, levels, record = row["chance_higher"], row["range"], row["track_record"]
            lines.append(f"  {row['symbol']}: higher in {chance['up_pct']}% ({chance['interval_pct'][0]}–{chance['interval_pct'][1]}%, "
                         f"{chance['separate_periods']} separate periods) · middle {levels['p50']['move_pct']:+.1f}% · 9 in 10 "
                         f"{levels['p05']['move_pct']:+.1f}% to {levels['p95']['move_pct']:+.1f}%"
                         + (f" · ranges held {record['inside_wide_pct']:.0f}% of {record['tested']} tests" if record.get("tested", 0) >= MIN_TESTS
                            else " · too few tests to check its ranges"))
        return lines + [value["basis"]]
    lead = plain_lead(value)
    lines = ([lead] if lead else []) + [
        f"{value['symbol']} by time period (how often each outcome happened before; not a forecast) · now {_money(value['price'])}"
        + (" live" if value["live"] else "")]
    focus = value.get("focus", "all")
    for row in value["periods"]:
        if not row["available"]:
            lines.append(f"  {row['label']}: {row['reason']}.")
            continue
        chance, record = row["chance_higher"], row["track_record"]
        held = (f"ranges held {record['inside_middle_pct']:.0f}%/{record['inside_wide_pct']:.0f}% of {record['tested']} tests"
                if record.get("tested", 0) >= MIN_TESTS else "too few tests to check its ranges")
        lines.append(f"  {row['label']}: higher {chance['up_pct']}% ({chance['interval_pct'][0]}–{chance['interval_pct'][1]}%, "
                     f"{chance['separate_periods']} separate) · {_range_text(row)} · {held}"
                     + (f" · unchanged in {row['flat_pct']:g}% (thin trading)" if row["flat_pct"] >= 10 else ""))
    detail = [row for row in value["periods"] if row["available"] and (row["key"] == focus or focus == "all" and row["key"] == "1m")]
    if not detail and focus != "all":
        lines.append(f"Too little history for {LABELS[focus]} in this market.")
    for row in detail:
        lines.append(f"Over {row['label']} in detail:")
        lines += ["  " + line for line in detail_lines(row)]
    lines.append(value["basis"])
    return lines


def detail_lines(row: dict[str, Any]) -> list[str]:
    """One period in detail: the possibilities, levels touched on the way, the odds after a like move, the ranges' record."""
    if not row.get("available"):
        return [f"{row['label']}: {row['reason']}."]
    lines = ["Possibilities: " + " · ".join(f"{item['label']} {item['share_pct']}%" for item in row["possibilities"])]
    touch = row["touch"]
    lines.append(f"On the way: touched +{touch['step_pct']:g}% in {touch['rise_pct']}% of periods and −{touch['step_pct']:g}% in "
                 f"{touch['fall_pct']}%, even when it ended elsewhere.")
    like = row["like_now"]
    if like:
        lines.append(f"After a {like['direction']} like the last {row['label']} ({like['past_move_pct']:+.1f}%): higher in "
                     f"{like['up_pct']}% of {like['separate_periods']} separate cases ({like['interval_pct'][0]}–{like['interval_pct'][1]}%) "
                     f"against {row['chance_higher']['up_pct']}% usually: {like['verdict']}.")
    calm = row.get("volatility")
    if calm:
        lines.append(f"In {calm['state']} times like now (swings {calm['now_vs_usual']:g}× usual): higher in {calm['up_pct']}% of "
                     f"{calm['separate_periods']} separate cases ({calm['interval_pct'][0]}–{calm['interval_pct'][1]}%)"
                     + (f", moves {calm['width_ratio']:g}× as wide" if calm.get("width_ratio") else "") + f": {calm['verdict']}.")
    test = row.get("odds_test") or {}
    if test.get("tested"):
        lines.append(f"Odds test ({test['tested']} past tests): " + test["verdict"]
                     + (f" (Brier {test['brier_like_now']} vs {test['brier_plain']}; lower is better)." if "brier_plain" in test else "."))
    lines.append(f"Ranges: {row['method']}; {row['track_record']['verdict']}.")
    if row.get("scheduled"):
        lines.append("Scheduled inside it: " + ", ".join(f"{item['title']} in {item['days_until']} days" for item in row["scheduled"][:4]) + ".")
    return lines
