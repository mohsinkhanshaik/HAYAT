"""What moves a market: how it moves with the wider markets, measured, and how much of today they explain.

For one coin or stock HAYAT lines up its daily moves with eight markets that often carry others
(Bitcoin, Ethereum, the S&P 500, the Nasdaq-100, gold, chip stocks, energy stocks and the euro
against the dollar), day by day, and measures for each:

* **Moves with:** correlation over the last 90 days and the last year, and whether the link has
  stayed steady or changed (a link that came and went is said to be unreliable).
* **Per 1 %:** how far this market usually moved for each 1 % the other moved (beta, 90 days).
* **In a sell-off:** its average move on the other market's worst tenth of days, next to its own
  usual swing: links often tighten when markets fall.
* **Leads?:** whether the other market's move on one day told anything about this market's next
  day. Most never do; a lead counts only past a strict bar, since eight markets are tried.
* **Today:** the wider markets' moves today and what the links imply here, next to what happened.
  A combined fit over 90 days says how much of the daily swings these markets explain together,
  and splits today's move into "with the markets" and "its own" (news, flows, anything else).

World events in its industries, scheduled events and unconfirmed reports are added from the
scenario readings. Nearness and co-movement, never proof of cause. Not advice; HAYAT never trades.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import UTC, datetime
from typing import Any

from app.logging import log_swallowed
from app.market_periods import _live, trim

_log = logging.getLogger(__name__)

#: (symbol, asset, label) of the markets that often carry others.
DRIVERS = (("BTC", "crypto", "Bitcoin"), ("ETH", "crypto", "Ethereum"), ("SPY", "stock", "S&P 500"), ("QQQ", "stock", "Nasdaq-100"),
           ("GLD", "stock", "Gold"), ("SMH", "stock", "Chip stocks"), ("XLE", "stock", "Energy stocks"),
           ("EUR", "crypto", "Euro vs dollar"))
LEAD_Z = 2.8  # about 1 % two-sided, after trying eight markets
MIN_DAYS = 40
#: Share of unchanged days (last 90) above which a market is too thinly traded to measure: its price goes stale,
#: so links come out weak when they are not (Kraken's chip and energy funds were unchanged on 40-60 % of days).
THIN = .2
RIDGE = 1e-6  # keeps the combined fit stable when two drivers move almost alike (S&P 500 and Nasdaq)
OTHER_FACTORS = ("World events", "Scheduled", "Unconfirmed report", "Thin trading")


def daily_moves(rows: list[dict[str, float]], live_price: float | None = None) -> dict[int, float]:
    """{day: log move from the day before} for consecutive daily candles; the live price closes today."""
    rows = [dict(row) for row in rows]
    if live_price and rows:
        rows[-1]["close"] = float(live_price)
    return {after["time"]: math.log(after["close"] / before["close"]) for before, after in zip(rows, rows[1:])
            if after["time"] - before["time"] <= 86400 and before["close"] > 0 and after["close"] > 0}


def stale_share(rows: list[dict[str, float]], days: int = 90) -> tuple[int, int]:
    """(unchanged days, days) among the last `days` daily closes."""
    recent = rows[-days - 1:]
    pairs = list(zip(recent, recent[1:]))
    return sum(before["close"] == after["close"] for before, after in pairs), len(pairs)


def correlation(first: list[float], second: list[float]) -> float | None:
    if len(first) < MIN_DAYS // 2 or len(first) != len(second):
        return None
    try:
        return statistics.correlation(first, second)
    except statistics.StatisticsError:
        return None


def beta(target: list[float], driver: list[float]) -> float | None:
    if len(driver) < MIN_DAYS // 2:
        return None
    spread = statistics.pvariance(driver)
    if spread < 1e-12:  # a market that did not move has no slope to measure
        return None
    mean_t, mean_d = statistics.fmean(target), statistics.fmean(driver)
    return sum((t - mean_t) * (d - mean_d) for t, d in zip(target, driver)) / len(driver) / spread


def strength(value: float | None) -> str:
    if value is None:
        return "unknown"
    size = abs(value)
    return "strong" if size >= .6 else "moderate" if size >= .35 else "weak" if size >= .15 else "none"


def solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when the system is singular."""
    size = len(vector)
    rows = [row[:] + [value] for row, value in zip(matrix, vector)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(rows[index][column]))
        if abs(rows[pivot][column]) < 1e-15:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for index in range(size):
            if index != column:
                factor = rows[index][column] / rows[column][column]
                rows[index] = [a - factor * b for a, b in zip(rows[index], rows[column])]
    return [rows[index][size] / rows[index][index] for index in range(size)]


def fit(target: list[float], drivers: list[list[float]]) -> tuple[list[float], float, float] | None:
    """(slopes, intercept, R²) of target on drivers, by least squares with a small ridge."""
    count, width = len(target), len(drivers)
    if count < MIN_DAYS or not width:
        return None
    means = [statistics.fmean(column) for column in drivers]
    mean_t = statistics.fmean(target)
    centred = [[value - mean for value in column] for column, mean in zip(drivers, means)]
    centred_t = [value - mean_t for value in target]
    scale = sum(value * value for column in centred for value in column) / width or 1.0
    gram = [[sum(a * b for a, b in zip(centred[i], centred[j])) + (RIDGE * scale if i == j else 0.0) for j in range(width)]
            for i in range(width)]
    slopes = solve(gram, [sum(a * b for a, b in zip(column, centred_t)) for column in centred])
    if slopes is None:
        return None
    intercept = mean_t - sum(slope * mean for slope, mean in zip(slopes, means))
    total = sum(value * value for value in centred_t)
    left = sum((t - intercept - sum(s * column[k] for s, column in zip(slopes, drivers))) ** 2 for k, t in enumerate(target))
    return slopes, intercept, (1 - left / total) if total else 0.0


def measure(target: dict[int, float], driver: dict[int, float]) -> dict[str, Any] | None:
    """Every link measurement between two markets' daily moves."""
    days = sorted(set(target) & set(driver))
    if len(days) < MIN_DAYS:
        return None
    year, quarter = days[-365:], days[-90:]
    mine, theirs = [target[day] for day in quarter], [driver[day] for day in quarter]
    near, far = correlation(mine, theirs), correlation([target[day] for day in year], [driver[day] for day in year])
    slope = beta(mine, theirs)
    # Its average move on the other market's worst tenth of days, against its own usual swing.
    worst = sorted(year, key=lambda day: driver[day])[:max(5, len(year) // 10)]
    usual = statistics.median([abs(target[day]) for day in year])
    stress = {"days": len(worst), "driver_move_pct": round((math.exp(statistics.fmean(driver[day] for day in worst)) - 1) * 100, 2),
              "move_pct": round((math.exp(statistics.fmean(target[day] for day in worst)) - 1) * 100, 2),
              "usual_abs_pct": round((math.exp(usual) - 1) * 100, 2)}
    # Does yesterday's move there tell anything about today here? Only consecutive days count.
    pairs = [(driver[before], target[after]) for before, after in zip(year, year[1:]) if after - before <= 86400]
    lead = correlation([a for a, _b in pairs], [b for _a, b in pairs]) if len(pairs) >= MIN_DAYS else None
    z = lead * math.sqrt(len(pairs) - 3) if lead is not None and len(pairs) > 3 else 0.0  # Fisher's approximation
    steady = near is not None and far is not None and abs(near - far) < .25
    return {"days": len(year), "corr_90": None if near is None else round(near, 2), "corr_365": None if far is None else round(far, 2),
            "steady": steady, "beta_90": None if slope is None else round(slope, 2), "strength": strength(near), "stress": stress,
            "lead": {"corr": None if lead is None else round(lead, 3), "days": len(pairs), "z": round(z, 2),
                     "significant": abs(z) >= LEAD_Z}}


def describe(row: dict[str, Any], name: str) -> str:
    """One plain sentence for a link."""
    label, near = row["label"], row["corr_90"]
    if near is None:
        return f"Too little shared history with {label}."
    direction = "with" if near > 0 else "against"
    text = {"strong": f"Moves closely {direction} {label}", "moderate": f"Often moves {direction} {label}",
            "weak": f"Moves a little {direction} {label}", "none": f"Moves on its own, not with {label}"}[row["strength"]]
    if row["strength"] in ("strong", "moderate") and row["beta_90"] is not None:
        text += f": about {row['beta_90']:+.2f}% for each 1% {label} moves"
    if not row["steady"] and row["corr_365"] is not None:
        text += f" (over a year {row['corr_365']:+.2f}: the link has changed)"
    if row["lead"]["significant"]:
        text += f". {label}'s move one day has told something about {name}'s next day"
    return text + "."


def influences(markets: Any, symbol: str, asset: str = "auto", events_data: dict | None = None,
               live_price: float | None = None) -> dict[str, Any]:
    """How one market moves with eight others, today's split, and the other readings that might matter."""
    name, kind, candles = markets.candles(symbol, 1440, asset)
    candles = trim(candles)
    if live_price is None:
        live_price = _live(markets, name, kind)
    target = daily_moves(candles, live_price)
    if len(target) < MIN_DAYS:
        raise ValueError(f"{name} has only {len(target)} days of moves; links need at least {MIN_DAYS}.")
    rows, series, gaps = [], {}, []
    for other, other_kind, label in DRIVERS:
        if other == name:
            continue
        try:
            _found, _kind, raw = markets.candles(other, 1440, other_kind)
            raw = trim(raw)
            moves = daily_moves(raw, _live(markets, other, other_kind))
        except Exception as exc:  # one missing market leaves the others standing
            log_swallowed(_log, f"reading {other} for market influences")
            gaps.append(f"{label}: {str(exc)[:80]}")
            continue
        unchanged, counted = stale_share(raw)
        if counted and unchanged / counted > THIN:
            gaps.append(f"{label}: thinly traded on Kraken ({unchanged} of the last {counted} days unchanged), so its prices lag "
                        "and no link is measured")
            continue
        found = measure(target, moves)
        if found is None:
            gaps.append(f"{label}: too little shared history")
            continue
        last = max(moves)
        today = moves[last] if last == max(target) else None
        row = {"symbol": other, "asset": other_kind, "label": label, **found,
               "today": {"driver_pct": None if today is None else round((math.exp(today) - 1) * 100, 2),
                         "implied_pct": None if today is None or found["beta_90"] is None
                         else round((math.exp(found["beta_90"] * today) - 1) * 100, 2)}}
        row["text"] = describe(row, name)
        rows.append(row)
        series[other] = moves
    rows.sort(key=lambda row: -abs(row["corr_90"] or 0))
    model = combined(target, series, rows)
    today_move = target[max(target)]
    unchanged, counted = stale_share(candles)
    thin = bool(counted) and unchanged / counted > THIN
    leads = [row["label"] for row in rows if row["lead"]["significant"]]
    top = rows[0] if rows else None
    summary = (f"{name}: " + (describe(top, name) if top else "no other market to compare with.")
               + (f" Together these markets explain about {model['r2_pct']:.0f}% of its daily swings over {model['days']} days; "
                  f"the rest is its own." if model else "")
               + (f" Leading: {', '.join(leads)}." if leads else " None of them has led it from one day to the next.")
               + (f" Few trades in {name} on Kraken ({unchanged} of the last {counted} days unchanged): every link is understated."
                  if thin else ""))
    return {"markets": True, "influences_view": True, "symbol": name, "asset": kind, "price": candles[-1]["close"] if not live_price
            else float(live_price), "live": bool(live_price), "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "history_days": len(target), "thin": thin, "today_pct": round((math.exp(today_move) - 1) * 100, 2), "drivers": rows, "model": model,
            "other_factors": other_factors(markets, name, kind, candles, events_data), "gaps": gaps, "summary": summary,
            "source": "Kraken daily candles (crypto, tokenized shares and EUR/USD)" + (" with live streamed prices" if live_price else ""),
            "basis": ("Co-movement measured on daily moves, not cause: two markets can move together because a third thing moves "
                      "both. Links change, especially in crises. Not advice; HAYAT never trades.")}


def combined(target: dict[int, float], series: dict[str, dict[int, float]], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One fit on the last 90 shared days: how much the drivers explain together, and today's split."""
    usable = [row["symbol"] for row in rows if row["symbol"] in series]
    days = sorted(set(target).intersection(*(set(series[key]) for key in usable)))[-90:] if usable else []
    # A driver with a short history (a new tokenized fund) must not shrink the window for all.
    while usable and len(days) < MIN_DAYS:
        usable.pop()
        days = sorted(set(target).intersection(*(set(series[key]) for key in usable)))[-90:] if usable else []
    if not usable:
        return None
    found = fit([target[day] for day in days], [[series[key][day] for day in days] for key in usable])
    if found is None:
        return None
    slopes, intercept, r2 = found
    today = max(target)
    result = {"r2_pct": round(max(0.0, r2) * 100, 1), "days": len(days), "drivers": usable}
    if all(today in series[key] for key in usable):
        explained = intercept + sum(slope * series[key][today] for slope, key in zip(slopes, usable))
        actual = target[today]
        result["today"] = {"actual_pct": round((math.exp(actual) - 1) * 100, 2),
                           "with_markets_pct": round((math.exp(explained) - 1) * 100, 2),
                           "own_pct": round((math.exp(actual - explained) - 1) * 100, 2)}
    return result


def other_factors(markets: Any, name: str, kind: str, candles: list[dict[str, float]], events_data: dict | None) -> list[dict[str, str]]:
    """World events, scheduled events, unconfirmed reports and thin trading, from the scenario readings."""
    closes = [candle["close"] for candle in candles]
    if len(closes) < 140:
        return []
    try:
        from app.market_scenarios import drivers, situations
        found = drivers(markets, name, kind, closes, candles, situations(closes)[-1], events_data)
    except Exception:  # the links stand without the other readings
        log_swallowed(_log, "reading other factors for market influences")
        return []
    return [item for item in found if item["factor"] in OTHER_FACTORS]


def report_lines(value: dict[str, Any]) -> list[str]:
    from app.markets import money, signed_pct
    lines = [f"What moves {value['symbol']} (co-movement measured on daily moves, not cause) · now {money(value['price'])}"
             + (" live" if value["live"] else "") + f" · today {signed_pct(value['today_pct'])}", value["summary"]]
    for row in value["drivers"][:8]:
        today = row["today"]
        lines.append(f"  {row['text']}"
                     + (f" Today {row['label']} {signed_pct(today['driver_pct'])}, which goes with {signed_pct(today['implied_pct'])} here."
                        if today["driver_pct"] is not None and row["strength"] in ("strong", "moderate") else "")
                     + f" On {row['label']}'s worst tenth of days it moved {signed_pct(row['stress']['move_pct'])} on average "
                       f"(usual swing ±{row['stress']['usual_abs_pct']}%).")
    model = value.get("model") or {}
    if model.get("today"):
        split = model["today"]
        lines.append(f"Today's {signed_pct(split['actual_pct'])}: about {signed_pct(split['with_markets_pct'])} goes with the wider "
                     f"markets and {signed_pct(split['own_pct'])} is its own (news, flows, anything else).")
    for item in value.get("other_factors") or []:
        lines.append(f"  {item['factor']}: {item['reading']}")
    lines.extend("Unavailable: " + gap for gap in value.get("gaps") or [])
    lines.append(value["basis"])
    return lines
