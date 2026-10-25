"""Scenarios for a market, from live data: how often it rose in situations like now, and what might matter.

HAYAT does not predict prices. It measures. For the market on screen it reads up to 720 daily
candles (Kraken), takes the price streaming live this second as today's close, and describes
the situation now:
* trend: price against its 20-day average;
* volatility: the last 10 days' swings against the last 120 days';
* RSI (14 days): overbought, oversold or neutral.

Then it looks back for every past day in the same situation and counts how often the market was
higher one day and one week later, next to how often it was higher on all days. Each share comes
with its sample size and a 95 % interval (Wilson), and a plain verdict: a situation that changes
nothing says so. Week-ahead samples are taken a week apart so they do not overlap.

Around that:
* scenario prices for the coming week (the 10th, 50th and 90th percentile of past weekly moves in
  the same volatility state, applied to the live price);
* the chance of an unusually large move tomorrow, now and normally;
* factors that might matter, each with its reading: momentum, volatility, RSI, the last day's
  move, world events in its industries (market_events rules), how it moves with the S&P 500 or
  Bitcoin, and thin trading. Nearness in time is shown, never cause.

"Explain with HAYAT" hands only these numbers to the local model for a plain summary. Not advice;
HAYAT never trades.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import UTC, datetime
from typing import Any

from app.logging import log_swallowed
from app.market_outlook import rsi

_log = logging.getLogger(__name__)

DAY = 1
MIN_MATCHES = 15


def wilson(hits: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if not total:
        return 0.0, 1.0
    share = hits / total
    centre = (share + z * z / (2 * total)) / (1 + z * z / total)
    spread = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return max(0.0, centre - spread), min(1.0, centre + spread)


def _std(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def situations(closes: list[float]) -> list[dict[str, str] | None]:
    """The situation on each day (None while there is too little history before it)."""
    moves = [math.log(after / before) for before, after in zip(closes, closes[1:])]
    swings = [None] * len(closes)
    for index in range(11, len(closes)):
        swings[index] = _std(moves[index - 10:index])
    result: list[dict[str, str] | None] = []
    for index, price in enumerate(closes):
        if index < 130:
            result.append(None)
            continue
        average = sum(closes[index - 19:index + 1]) / 20
        trend = "up" if price > average * 1.01 else "down" if price < average * .99 else "flat"
        usual = statistics.median([value for value in swings[index - 120:index] if value])
        now = swings[index] or 0.0
        volatility = "high" if usual and now > usual * 1.3 else "low" if usual and now < usual * .75 else "normal"
        strength = rsi(closes[:index + 1], 14)
        zone = "overbought" if strength is not None and strength >= 70 else "oversold" if strength is not None and strength <= 30 else "neutral"
        result.append({"trend": trend, "volatility": volatility, "rsi": zone})
    return result


def share_up(closes: list[float], days: list[int], ahead: int) -> dict[str, Any]:
    ups = sum(closes[day + ahead] > closes[day] for day in days)
    low, high = wilson(ups, len(days))
    return {"up_pct": round(ups / len(days) * 100, 1) if days else None, "samples": len(days),
            "interval_pct": [round(low * 100, 1), round(high * 100, 1)]}


def judge(now: dict[str, Any], usual: dict[str, Any]) -> str:
    if now["samples"] < MIN_MATCHES:
        return "too few past cases to tell"
    low, high = now["interval_pct"]
    if low > usual["up_pct"]:
        return "rose more often than usual in situations like this"
    if high < usual["up_pct"]:
        return "rose less often than usual in situations like this"
    return "within the noise: this situation has not changed the odds"


def correlation(first: list[float], second: list[float]) -> float | None:
    if len(first) < 20 or len(first) != len(second):
        return None
    try:
        return round(statistics.correlation(first, second), 2)
    except statistics.StatisticsError:
        return None


def scenarios(markets: Any, symbol: str, asset: str = "auto", events_data: dict | None = None,
              live_price: float | None = None) -> dict[str, Any]:
    name, kind, candles = markets.candles(symbol, 1440, asset)
    candles = [candle for candle in candles if candle["close"] > 0]
    if len(candles) < 160:
        raise ValueError(f"{name} has only {len(candles)} days of history; scenarios need about 160.")
    closes = [candle["close"] for candle in candles]
    if live_price is None:
        stream = getattr(markets, "live", None)
        if stream is not None:
            from app.markets_live import ws_symbol
            item = (stream.snapshot([ws_symbol(name, kind)])["prices"] or {}).get(ws_symbol(name, kind)) or {}
            live_price = item.get("price")
    live = live_price is not None and live_price > 0
    if live:
        closes[-1] = float(live_price)  # today's candle is still forming: the live trade is its close so far
    week = 7  # Kraken trades crypto and tokenized shares every day, so a week is 7 daily candles
    states = situations(closes)
    now = states[-1]
    days_day = [index for index in range(130, len(closes) - DAY) if states[index]]
    days_week = [index for index in range(130, len(closes) - week, week) if states[index]]

    def matching(days: list[int], keys: tuple[str, ...]) -> list[int]:
        return [index for index in days if all(states[index][key] == now[key] for key in keys)]
    # The most specific match with enough past cases; the broadest one (trend alone) otherwise.
    conditions: tuple[str, ...] = ("trend",)
    for keys in (("trend", "volatility", "rsi"), ("trend", "volatility"), ("trend",)):
        if len(matching(days_week, keys)) >= MIN_MATCHES:
            conditions = keys
            break
    usual_day, usual_week = share_up(closes, days_day, DAY), share_up(closes, days_week, week)
    now_day, now_week = share_up(closes, matching(days_day, conditions), DAY), share_up(closes, matching(days_week, conditions), week)
    # The week ahead, from weekly moves in the same volatility state.
    weekly = [closes[index + week] / closes[index] - 1 for index in days_week if states[index]["volatility"] == now["volatility"]]
    if len(weekly) < MIN_MATCHES:  # too few weeks in this volatility state for a spread: use every week
        weekly = [closes[index + week] / closes[index] - 1 for index in days_week]
    weekly.sort()
    price = closes[-1]

    def percentile(values: list[float], share: float) -> float:
        return values[min(len(values) - 1, max(0, int(share * (len(values) - 1))))]
    paths = {label: {"price": round(price * (1 + percentile(weekly, share)), 6), "move_pct": round(percentile(weekly, share) * 100, 2)}
             for label, share in (("weak", .1), ("middle", .5), ("strong", .9))}
    moves = [abs(closes[index + 1] / closes[index] - 1) for index in range(len(closes) - 1)]
    typical = statistics.median([move for move in moves if move > 0] or [0.0]) * 100  # quiet days with no trade do not count
    big = typical * 2.5
    big_all = [abs(closes[index + 1] / closes[index] - 1) * 100 > big for index in days_day]
    big_now = [abs(closes[index + 1] / closes[index] - 1) * 100 > big for index in days_day if states[index]["volatility"] == now["volatility"]]
    factors = drivers(markets, name, kind, closes, candles, now, events_data)
    summary = (f"In situations like now ({', '.join(f'{key} {now[key]}' for key in conditions)}), {name} was higher a "
               f"week later in {now_week['up_pct']}% of {now_week['samples']} past cases "
               f"(95% range {now_week['interval_pct'][0]}–{now_week['interval_pct'][1]}%), against {usual_week['up_pct']}% on all weeks: "
               f"{judge(now_week, usual_week)}.")
    return {"markets": True, "scenarios_view": True, "symbol": name, "asset": kind, "price": price, "live": live,
            "computed_at": datetime.now(UTC).isoformat(timespec="seconds"), "history_days": len(closes),
            "situation": now, "conditions": list(conditions),
            "next_day": {"now": now_day, "usual": usual_day, "verdict": judge(now_day, usual_day)},
            "next_week": {"now": now_week, "usual": usual_week, "verdict": judge(now_week, usual_week), "days": week},
            "paths": paths, "typical_day_move_pct": round(typical, 2),
            "big_move": {"threshold_pct": round(big, 2), "now_pct": round(sum(big_now) / len(big_now) * 100, 1) if big_now and big else None,
                         "usual_pct": round(sum(big_all) / len(big_all) * 100, 1) if big_all and big else None},
            "factors": factors, "summary": summary,
            "source": "Kraken daily candles" + (" with the live streamed price as today's close" if live else ""),
            "basis": ("Shares of past cases, not forecasts: markets change, and similar situations are not the same situation. "
                      "Factors are readings that may matter, not causes. Not advice; HAYAT never trades.")}


def drivers(markets: Any, name: str, kind: str, closes: list[float], candles: list[dict], now: dict[str, str],
            events_data: dict | None) -> list[dict[str, str]]:
    """Readings that might matter now, each leaning up, down or adding risk; never a claim of cause."""
    price = closes[-1]
    average = sum(closes[-20:]) / 20
    factors = [{"factor": "Trend", "reading": f"{(price / average - 1) * 100:+.1f}% against its 20-day average",
                "leaning": {"up": "up", "down": "down"}.get(now["trend"], "neutral"),
                "note": "Momentum describes the past; check the situation odds above before leaning on it."}]
    moves = [math.log(after / before) for before, after in zip(closes, closes[1:])]
    recent, usual = _std(moves[-10:]), statistics.median([_std(moves[index - 10:index]) for index in range(len(moves) - 120, len(moves))
                                                          if index >= 10] or [0.0])
    factors.append({"factor": "Volatility", "reading": f"swings {recent / usual:.1f}× its usual size" if usual else "not enough history",
                    "leaning": "risk" if now["volatility"] == "high" else "neutral",
                    "note": {"high": "Bigger moves either way are more likely than usual.", "low": "Quiet markets can stay quiet, or break out.",
                             "normal": "Swings are about their usual size."}[now["volatility"]]})
    strength = rsi(closes, 14)
    if strength is not None:
        factors.append({"factor": "RSI (14 days)", "reading": f"{strength:.0f} · {now['rsi']}",
                        "leaning": "down" if now["rsi"] == "overbought" else "up" if now["rsi"] == "oversold" else "neutral",
                        "note": "Extremes sometimes pull back; strong trends can stay extreme for weeks."})
    factors.append({"factor": "Last day", "reading": f"{(price / closes[-2] - 1) * 100:+.2f}%", "leaning": "neutral",
                    "note": "One day's move says little about the next on its own."})
    from app.gui_markets_views import events_for
    events = events_for(events_data, name)
    if events:
        top = events[0]
        factors.append({"factor": "World events", "reading": f"{len(events)} in its industr{'ies' if len({e['sector'] for e in events}) > 1 else 'y'}: "
                        f"{top['title'][:80]}", "leaning": "risk", "note": f"{top['sector']} · nearness in time, not proof of cause."})
    benchmark = ("SPY", "stock") if kind == "stock" and name != "SPY" else ("BTC", "crypto") if kind == "crypto" and name != "BTC" else \
        ("SPY", "stock") if name == "BTC" else None
    if benchmark:
        try:
            _other, _kind, other = markets.candles(benchmark[0], 1440, benchmark[1])
            by_time = {candle["time"]: candle["close"] for candle in other if candle["close"] > 0}
            pairs = [(candle["close"], by_time[candle["time"]]) for candle in candles[-91:] if candle["time"] in by_time]
            mine = [after[0] / before[0] - 1 for before, after in zip(pairs, pairs[1:])]
            theirs = [after[1] / before[1] - 1 for before, after in zip(pairs, pairs[1:])]
            linked = correlation(mine, theirs)
            if linked is not None:
                label = "S&P 500" if benchmark[0] == "SPY" else "Bitcoin"
                today = (other[-1]["close"] / other[-2]["close"] - 1) * 100 if len(other) > 1 else 0.0
                factors.append({"factor": f"Moves with {label}", "reading": f"correlation {linked:+.2f} over 90 days · {label} {today:+.2f}% today",
                                "leaning": ("up" if today > 0 else "down") if abs(linked) >= .5 and abs(today) >= .3 else "neutral",
                                "note": "Closely linked: the wider market's direction tends to carry over." if abs(linked) >= .5
                                else "Loosely linked: it mostly moves on its own."})
        except Exception:  # a missing benchmark leaves the other readings standing
            log_swallowed(_log, "reading the benchmark for a scenario")
    calendar = getattr(markets, "calendar", None)
    if calendar is not None:
        try:
            from app.market_calendar import EVENT_MARKETS
            coming = calendar.upcoming(10)
            for item in coming["events"]:
                touched = [symbol for symbol, _asset in EVENT_MARKETS.get(item["kind"], ())] + ([item["symbol"]] if item.get("symbol") else [])
                if name not in touched:
                    continue
                past = next((row for row in coming["effects"].get(item["kind"], []) if row["symbol"] == name), None)
                factors.append({"factor": "Scheduled", "reading": f"{item['title']} in {item['days_until']} day{'s' if item['days_until'] != 1 else ''}"
                                + (f" · past ones moved it {past['ratio']}× its usual" if past and past.get("ratio") else ""),
                                "leaning": "risk", "note": "Scheduled events bring bigger swings when they move markets; direction is "
                                "not known in advance."})
                break
        except Exception:  # scenarios stand without the calendar
            log_swallowed(_log, "reading the calendar for a scenario")
    news = getattr(markets, "news", None)
    if news is not None:
        try:
            heard = news.news(name, 48, True, 3)["items"]
            if heard:
                factors.append({"factor": "Unconfirmed report", "reading": f"{heard[0]['title'][:80]} ({heard[0]['outlet']})", "leaning": "risk",
                                "note": "A rumour, not a fact: it may prove wrong, and markets often move on it before it is settled."})
        except Exception:  # scenarios stand without the news
            log_swallowed(_log, "reading rumours for a scenario")
    traded = sum(candle.get("trades", 0) for candle in candles[-5:])
    if traded < 25:
        factors.append({"factor": "Thin trading", "reading": f"{traded} trades in 5 days", "leaning": "risk",
                        "note": "Few trades: prices can jump and the history is less reliable."})
    return factors


def explain(model: Any, value: dict[str, Any]) -> str:
    """A plain summary by HAYAT's local model, from these numbers only."""
    import json
    from app.models.base import Message
    from app.models.streaming import generate_answer
    if model is None:
        raise RuntimeError("HAYAT's local model is not available in this session.")
    facts = {key: value[key] for key in ("symbol", "price", "live", "situation", "next_day", "next_week", "paths", "big_move", "factors",
                                         "summary")}
    answer = generate_answer(model, [
        Message("system", "You explain market statistics to a non-expert in 4 to 6 short sentences. Use only the numbers given. "
                          "Say what the odds and ranges mean and which factors might matter. Never predict a price, never claim a "
                          "cause, never give advice to buy or sell, and say plainly when the odds are no better than usual."),
        Message("user", json.dumps(facts, ensure_ascii=False))], temperature=.2, max_tokens=420)
    return str(getattr(answer, "content", answer)).strip()
