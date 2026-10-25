"""Signal lab: which readings have actually told something about this market's next move, tested honestly.

Most market "signals" are noise that looks like a pattern. HAYAT tests them instead of trusting them.
For one coin or stock it reads up to two years of daily candles (the live price as today's close)
and walks through its past one day at a time, using only what was known on each day:

* **Ten candidate signals:** 5- and 20-day momentum, price against its 50- and 200-day averages,
  snap-back after a big day, RSI extremes, streak reversal, moves on heavy volume, the day of the
  week (learned only from earlier days) and following a leading market's move (Bitcoin for coins,
  the S&P 500 for shares and for Bitcoin).
* **Scored against the right yardstick:** each call is compared with the market's own up-rate as
  it was known then, so a market that usually rises does not make "always up" look clever.
  Next-week tests are a week apart so they never overlap. Days with no change are left out.
* **Corrected for luck:** trying ten signals over two horizons, one or two will look good by
  chance. A signal counts only if it passes a false-discovery test across *all* the tries
  (Benjamini-Hochberg, 10 %) *and* beat chance in both halves of its history.
* **Combined only after a later-period check:** choose and fit signals on the earlier two-thirds,
  then score that fixed combination on completed outcomes in the later third. Today's odds use
  that same fitted combination only if it improved Brier score there with at least ten tests.
  Correlated readings are averaged, with shrinkage and a cap on the adjustment.

The usual result for liquid markets is "no signal beats chance": that is a finding, not a failure.
Shares of past cases, never a forecast. Not advice; HAYAT never trades.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import UTC, datetime
from typing import Any, Callable

from app.logging import log_swallowed
from app.market_outlook import rsi
from app.market_periods import _live, trim
from app.market_scenarios import wilson

_log = logging.getLogger(__name__)

#: (key, label, days ahead, days between tests)
HORIZONS = (("1d", "next day", 1, 1), ("1w", "next week", 7, 7))
START = 60  # days of history before the first test
MIN_DAYS = 200
MIN_CALLS = {"1d": 40, "1w": 15}
FDR = .10  # false-discovery rate across every signal and horizon tried
SHRINK = 60  # pretend calls at the usual rate, so a short record cannot swing the odds far
CLAMP = .15  # the combined odds never move more than 15 points from the usual
#: Leading market per kind: Bitcoin leads coins; the S&P 500 leads shares, and Bitcoin itself.
LEADERS = {"crypto": ("BTC", "crypto", "Bitcoin"), "stock": ("SPY", "stock", "the S&P 500")}
STATUS_ORDER = {"works": 0, "luck": 1, "none": 2, "backwards": 3, "few": 4}


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _logit(share: float) -> float:
    share = min(.98, max(.02, share))
    return math.log(share / (1 - share))


def _expit(value: float) -> float:
    return 1 / (1 + math.exp(-value))


def one_sided_p(z: float) -> float:
    """Chance of a z-score at least this high from luck alone (normal approximation)."""
    return .5 * math.erfc(z / math.sqrt(2))


def benjamini_hochberg(p_values: list[float], rate: float = FDR) -> list[bool]:
    """Which tests survive when many were tried at once (False-discovery rate `rate`)."""
    order = sorted(range(len(p_values)), key=lambda index: p_values[index])
    count, cutoff = len(p_values), -1
    for rank, index in enumerate(order, 1):
        if p_values[index] <= rank / count * rate:
            cutoff = rank
    passed = [False] * count
    for rank, index in enumerate(order, 1):
        passed[index] = rank <= cutoff
    return passed


# ---------------------------------------------------------------------- the market's history
class History:
    """Daily closes, moves, volumes and the leading market's moves, aligned by day."""

    def __init__(self, closes: list[float], times: list[int], volumes: list[float], leader: dict[int, float] | None = None,
                 completed: int | None = None) -> None:
        self.closes, self.times, self.volumes = closes, times, volumes
        self.completed = len(closes) if completed is None else max(0, min(len(closes), completed))
        self.moves = [0.0] + [math.log(after / before) for before, after in zip(closes, closes[1:])]
        self.sigma = [0.0] * len(closes)  # 20-day swing up to and including each day
        for index in range(20, len(closes)):
            self.sigma[index] = statistics.pstdev(self.moves[index - 19:index + 1])
        self.leader_moves: list[float | None] = [None] * len(closes)
        if leader:
            for index in range(1, len(times)):
                before, after = leader.get(times[index - 1]), leader.get(times[index])
                if before and after and times[index] - times[index - 1] <= 86400:
                    self.leader_moves[index] = math.log(after / before)

    def __len__(self) -> int:
        return len(self.closes)

    def average(self, index: int, days: int) -> float | None:
        return sum(self.closes[index - days + 1:index + 1]) / days if index + 1 >= days else None


def _momentum(days: int) -> Callable[[History, int], int]:
    def call(history: History, index: int) -> int:
        return _sign(history.closes[index] / history.closes[index - days] - 1) if index >= days else 0
    return call


def _trend(days: int) -> Callable[[History, int], int]:
    def call(history: History, index: int) -> int:
        average = history.average(index, days)
        if average is None:
            return 0
        price = history.closes[index]
        return 1 if price > average * 1.01 else -1 if price < average * .99 else 0
    return call


def _reversal(history: History, index: int) -> int:
    usual = history.sigma[index - 1] if index >= 21 else 0.0
    if not usual:
        return 0
    size = history.moves[index] / usual
    return 1 if size <= -2 else -1 if size >= 2 else 0


def _rsi(history: History, index: int) -> int:
    strength = rsi(history.closes[max(0, index - 15):index + 1], 14)
    return 0 if strength is None else 1 if strength <= 30 else -1 if strength >= 70 else 0


def _streak(history: History, index: int) -> int:
    direction = _sign(history.moves[index])
    if not direction or index < 5:
        return 0
    run = 0
    while index - run >= 1 and _sign(history.moves[index - run]) == direction:
        run += 1
    return -direction if run >= 4 else 0


def _volume(history: History, index: int) -> int:
    if index < 21 or not history.moves[index]:
        return 0
    usual = statistics.median(history.volumes[index - 20:index])
    return _sign(history.moves[index]) if usual > 0 and history.volumes[index] >= 2 * usual else 0


def _lead(history: History, index: int) -> int:
    move = history.leader_moves[index]
    if move is None or index < 21:
        return 0
    recent = [value for value in history.leader_moves[index - 20:index] if value is not None]
    usual = statistics.pstdev(recent) if len(recent) >= 10 else 0.0
    return _sign(move) if usual and abs(move) >= .5 * usual else 0


def weekday_calls(history: History) -> list[int]:
    """Day-of-week calls for the next day, each learned only from days before it."""
    calls = [0] * len(history)
    ups: dict[int, list[int]] = {day: [0, 0] for day in range(7)}
    total = [0, 0]
    for index in range(len(history)):
        # Outcomes known by `index` end at it: day index-1 → index.
        if index >= 1 and history.closes[index] != history.closes[index - 1]:
            day = datetime.fromtimestamp(history.times[index], UTC).weekday()
            rose = int(history.closes[index] > history.closes[index - 1])
            ups[day][0] += rose
            ups[day][1] += 1
            total[0] += rose
            total[1] += 1
        coming = datetime.fromtimestamp(history.times[index] + 86400, UTC).weekday()
        seen, count = ups[coming]
        if count >= 30 and total[1]:
            gap = seen / count - total[0] / total[1]
            calls[index] = 1 if gap >= .04 else -1 if gap <= -.04 else 0
    return calls


def catalogue(leader_label: str | None) -> list[tuple[str, str, str, Callable[[History, int], int] | None]]:
    """(key, label, what it calls, call function); weekday is computed on its own."""
    rows = [("momentum_5", "5-day momentum", "the last 5 days' direction continues", _momentum(5)),
            ("momentum_20", "20-day momentum", "the last 20 days' direction continues", _momentum(20)),
            ("trend_50", "Against 50-day average", "above the 50-day average rises, below it falls", _trend(50)),
            ("trend_200", "Against 200-day average", "above the 200-day average rises, below it falls", _trend(200)),
            ("reversal", "Snap-back after a big day", "after a day over twice the usual swing, the opposite", _reversal),
            ("rsi", "RSI extremes", "RSI (14 days) under 30 rises, over 70 falls", _rsi),
            ("streak", "Streak reversal", "after 4 days in the same direction, the opposite", _streak),
            ("volume", "Heavy-volume moves", "a move on twice the usual volume continues", _volume),
            ("weekday", "Day of the week", "weekdays that rose more (or less) often before, learned from earlier days only", None)]
    if leader_label:
        rows.append(("lead", f"Follows {leader_label}", f"today's {leader_label} move carries into this market's next move", _lead))
    return rows


def all_calls(history: History, signals: list[tuple[str, str, str, Any]]) -> dict[str, list[int]]:
    calls = {}
    for key, _label, _about, function in signals:
        calls[key] = weekday_calls(history) if key == "weekday" else [function(history, index) for index in range(len(history))]
    return calls


# ---------------------------------------------------------------------- walk-forward scoring
def outcomes(history: History, ahead: int) -> list[int | None]:
    """1 if higher `ahead` days later, 0 if lower, None if unchanged or not yet known."""
    closes = history.closes
    return [None if index + ahead >= history.completed or closes[index + ahead] == closes[index] else int(closes[index + ahead] > closes[index])
            for index in range(len(closes))]


def usual_rates(results: list[int | None], ahead: int) -> list[float]:
    """The up-rate as known on each day: only outcomes that had finished by then (Laplace-smoothed)."""
    rates, ups, count = [], 0, 0
    for index in range(len(results)):
        finished = index - ahead
        if finished >= 0 and finished % ahead == 0 and results[finished] is not None:
            ups += results[finished]
            count += 1
        rates.append((ups + 1) / (count + 2))
    return rates


def score(calls: list[int], results: list[int | None], rates: list[float], days: list[int]) -> dict[str, Any]:
    """Hits against what the usual up-rate would have given the same calls."""
    hits = made = 0
    expected = variance = 0.0
    edges: list[float] = []
    for index in days:
        call, result = calls[index], results[index]
        if not call or result is None:
            continue
        chance = rates[index] if call > 0 else 1 - rates[index]
        hit = int((result == 1) == (call > 0))
        made += 1
        hits += hit
        expected += chance
        variance += chance * (1 - chance)
        edges.append(hit - chance)
    z = (hits - expected) / math.sqrt(variance) if variance else 0.0
    half = len(edges) // 2
    return {"calls": made, "hits": hits, "expected": expected, "z": z, "p_value": one_sided_p(z) if made else 1.0,
            "halves": [sum(edges[:half]), sum(edges[half:])] if half else [0.0, 0.0]}


def judge(stats: dict[str, Any], passed: bool, minimum: int, tried: int) -> tuple[str, str]:
    """(status, plain verdict) for one signal at one horizon."""
    if stats["calls"] < minimum:
        return "few", f"too few calls to judge ({stats['calls']}, at least {minimum} needed)"
    steady = all(edge > 0 for edge in stats["halves"])
    if passed and steady:
        return "works", "beat chance, held in both halves of its history and survived the luck test"
    if stats["z"] <= -2.5 and all(edge < 0 for edge in stats["halves"]):
        return "backwards", "did the opposite of its call more often than chance; HAYAT does not lean on it either way"
    if stats["p_value"] < .05:
        why = "it did not hold in both halves of its history" if not steady else f"with {tried} tries, luck alone makes some look this good"
        return "luck", f"looked better than chance, but {why}"
    return "none", "no better than chance"


def combine(usual: float, readings: list[tuple[int, dict[str, Any]]]) -> float:
    """Average shrunk log-odds shifts; correlated calls do not multiply the evidence."""
    shifts = []
    for call, stats in readings:
        if not call or not stats["calls"]:
            continue
        rate = (stats["hits"] + SHRINK * stats["expected"] / stats["calls"]) / (stats["calls"] + SHRINK)  # how often a call was right
        chance_up = rate if call > 0 else 1 - rate
        usual_for_call = stats["expected"] / stats["calls"]
        shifts.append(_logit(chance_up) - _logit(usual_for_call if call > 0 else 1 - usual_for_call))
    # Momentum, averages and RSI share the same prices. Repeating a correlated
    # reading must not multiply its evidence as if it were independent.
    if not shifts:
        return usual
    value = _logit(usual) + sum(shifts) / len(shifts)
    return min(usual + CLAMP, max(usual - CLAMP, _expit(value)))


def holdout(history: History, signals: list[tuple[str, str, str, Any]], calls: dict[str, list[int]],
            ahead: int, step: int, minimum: int) -> dict[str, Any]:
    """Choose signals on the earlier two-thirds only, then score the combined odds on the later third."""
    results = outcomes(history, ahead)
    rates = usual_rates(results, ahead)
    split = START + (len(history) - START) * 2 // 3
    train_days = [index for index in range(START, split - ahead, step)]
    trained = {key: score(calls[key], results, rates, train_days) for key, *_rest in signals}
    eligible = [key for key in trained if trained[key]["calls"] >= minimum]
    passed = dict(zip(eligible, benjamini_hochberg([trained[key]["p_value"] for key in eligible]))) if eligible else {}
    chosen = [key for key in eligible if passed[key] and all(edge > 0 for edge in trained[key]["halves"])]
    brier = plain = 0.0
    tested = 0
    for index in range(split, len(history) - ahead, step):
        if results[index] is None:
            continue
        odds = combine(rates[index], [(calls[key][index], trained[key]) for key in chosen])
        brier += (odds - results[index]) ** 2
        plain += (rates[index] - results[index]) ** 2
        tested += 1
    skill = (1 - brier / plain) * 100 if plain else 0.0
    short = ("not confirmed out of sample: nothing passed on the earlier two-thirds" if not chosen else "too few later days" if tested < 10
             else f"helped on the later third (skill {skill:+.1f}%)" if skill > 1
             else f"no better than usual on the later third ({skill:+.1f}%)" if skill >= -1
             else f"worse than usual on the later third ({skill:+.1f}%): do not lean on it")
    if not chosen:
        verdict = ("no signal beat chance on the earlier two-thirds of its history, so there was nothing to combine; "
                   "the usual odds are the honest answer")
    elif tested < 10:
        verdict = "too few later days to test the combination"
    elif skill > 1:
        verdict = f"chosen on the earlier two-thirds, the combination scored better than the usual odds on the later third (skill {skill:+.1f}%)"
    elif skill >= -1:
        verdict = f"chosen on the earlier two-thirds, the combination did about as well as the usual odds on the later third ({skill:+.1f}%)"
    else:
        verdict = (f"chosen on the earlier two-thirds, the combination did worse than the usual odds on the later third ({skill:+.1f}%): "
                   "do not lean on it")
    active = [key for key in chosen if calls[key][-1]]
    current = combine(rates[-1], [(calls[key][-1], trained[key]) for key in active])
    return {"chosen": chosen, "tested": tested, "skill_pct": round(skill, 1), "verdict": verdict, "short": short,
            "brier_score": brier / tested if tested else None, "baseline_brier_score": plain / tested if tested else None,
            "current_odds": current, "active": active, "training_end_index": split - ahead - 1}


def lab(history: History, leader_label: str | None) -> dict[str, Any]:
    """Every signal at every horizon, judged together; the combined odds now; the out-of-sample check."""
    signals = catalogue(leader_label)
    calls = all_calls(history, signals)
    tests: list[dict[str, Any]] = []
    for horizon, label, ahead, step in HORIZONS:
        results = outcomes(history, ahead)
        rates = usual_rates(results, ahead)
        days = list(range(START, len(history) - ahead, step))
        for key, name, about, _function in signals:
            if key == "weekday" and ahead != 1:
                continue
            stats = score(calls[key], results, rates, days)
            tests.append({"key": key, "label": name, "about": about, "horizon": horizon, "horizon_label": label, **stats})
    eligible = [test for test in tests if test["calls"] >= MIN_CALLS[test["horizon"]]]
    for test, passed in zip(eligible, benjamini_hochberg([test["p_value"] for test in eligible])):
        test["passed_luck_test"] = passed
    rows = []
    for test in tests:
        status, verdict = judge(test, test.get("passed_luck_test", False), MIN_CALLS[test["horizon"]], len(eligible))
        now = calls[test["key"]][-1]
        calls_made = test["calls"]
        rows.append({"key": test["key"], "label": test["label"], "about": test["about"], "horizon": test["horizon"],
                     "horizon_label": test["horizon_label"], "calls": calls_made, "hits": test["hits"],
                     "hit_pct": round(test["hits"] / calls_made * 100, 1) if calls_made else None,
                     "chance_pct": round(test["expected"] / calls_made * 100, 1) if calls_made else None,
                     "edge_pts": round((test["hits"] - test["expected"]) / calls_made * 100, 1) if calls_made else None,
                     "p_value": round(test["p_value"], 4), "status": status, "verdict": verdict,
                     "now": now, "now_text": {1: "leans up", -1: "leans down"}.get(now, "no call"),
                     "_stats": {"calls": calls_made, "hits": test["hits"], "expected": test["expected"]}})
    horizons = []
    for horizon, label, ahead, step in HORIZONS:
        results = outcomes(history, ahead)
        days = [results[index] for index in range(0, len(results), step) if results[index] is not None]
        ups = usual_rates(results, ahead)[-1]
        low, high = wilson(sum(days), len(days))
        working = [row for row in rows if row["horizon"] == horizon and row["status"] == "works"]
        check = holdout(history, [s for s in signals if s[0] != "weekday" or ahead == 1], calls, ahead, step, MIN_CALLS[horizon])
        validated = bool(check['chosen']) and check['tested'] >= 10 and check['skill_pct'] > 1
        odds = check['current_odds'] if validated else ups
        horizons.append({"key": horizon, "label": label, "usual_up_pct": round(ups * 100, 1),
                         "usual_interval_pct": [round(low * 100, 1), round(high * 100, 1)], "usual_samples": len(days),
                         "odds_up_pct": round(odds * 100, 1), "from_signals": check['active'] if validated else [],
                         "validation_passed": validated,
                         "withheld": '' if validated else 'Signal adjustments withheld: the later-third check did not establish an improvement.',
                         "working": [row["key"] for row in working],
                         "holdout": check})
    for row in rows:
        row.pop("_stats")
    rows.sort(key=lambda row: (STATUS_ORDER[row["status"]], row["horizon"], -(row["edge_pts"] or 0)))
    return {"signals": rows, "horizons": horizons, "tried": len(eligible)}


def signals(markets: Any, symbol: str, asset: str = "auto", live_price: float | None = None) -> dict[str, Any]:
    """The signal lab for one market, from Kraken daily candles and the live price."""
    name, kind, candles = markets.candles(symbol, 1440, asset)
    candles = trim(candles)
    if len(candles) < MIN_DAYS:
        raise ValueError(f"{name} has only {len(candles)} days of history; the signal lab needs about {MIN_DAYS}.")
    closes = [candle["close"] for candle in candles]
    completed = sum(candle['time'] + 86400 <= datetime.now(UTC).timestamp() for candle in candles)
    if live_price is None:
        live_price = _live(markets, name, kind)
    live = live_price is not None and live_price > 0 and completed < len(candles)
    if live:
        closes[-1] = float(live_price)  # today's candle is still forming: the live trade is its close so far
    leader = LEADERS.get(kind)
    if leader and leader[0] == name:
        leader = LEADERS["stock"] if kind == "crypto" else LEADERS["crypto"]
    by_time: dict[int, float] = {}
    if leader:
        try:
            _other, _kind, rows = markets.candles(leader[0], 1440, leader[1])
            by_time = {row["time"]: row["close"] for row in rows if row["close"] > 0}
        except Exception:  # the lab stands without the leading market
            log_swallowed(_log, "reading the leading market for the signal lab")
    history = History(closes, [candle["time"] for candle in candles], [candle.get("volume", 0.0) for candle in candles], by_time,
                      completed=completed)
    found = lab(history, leader[2] if by_time and leader else None)
    working = [row for row in found["signals"] if row["status"] == "works"]
    day = found["horizons"][0]
    summary = (f"{name}: {len(found['signals'])} signal tests over {len(closes)} days; "
               + (f"{len(working)} beat chance after allowing for luck ({', '.join(sorted({row['label'] for row in working}))}). "
                  if working else "none beat chance after allowing for luck. ")
               + f"Usual odds of a higher next day: {day['usual_up_pct']}%"
               + (f"; with the separately tested combination: {day['odds_up_pct']}%." if day["from_signals"] else ".")
               + (" Signal adjustments withheld by the later-third check." if day['withheld'] else ""))
    return {"markets": True, "signals_view": True, "symbol": name, "asset": kind, "price": closes[-1], "live": live,
            "computed_at": datetime.now(UTC).isoformat(timespec="seconds"), "history_days": len(closes),
            "closed_history_days": completed,
            "history_from": datetime.fromtimestamp(candles[0]["time"], UTC).date().isoformat(),
            "leader": leader[2] if by_time and leader else None, **found, "summary": summary,
            "source": "Kraken daily candles" + (" with the live streamed price as today's close" if live else ""),
            "basis": ("Each signal is tested on this market's own past using only what was known on each day, against its usual "
                      "up-rate. The table describes full-history tests corrected for multiple tries; current adjustments use only "
                      "the frozen earlier-period model if it improved Brier score on at least ten later tests. Unfinished candles "
                      "are not realized outcomes. Passing this one split is limited evidence, not proof of future accuracy. "
                      "Past edges fade and markets change: "
                      "shares of past cases, never a forecast. Not advice; HAYAT never trades.")}


def explain(model: Any, value: dict[str, Any]) -> str:
    """A plain summary by HAYAT's local model, from these numbers only."""
    import json
    from app.models.base import Message
    from app.models.streaming import generate_answer
    if model is None:
        raise RuntimeError("HAYAT's local model is not available in this session.")
    facts = {"symbol": value["symbol"], "summary": value["summary"], "horizons": value["horizons"],
             "signals": [{key: row[key] for key in ("label", "horizon_label", "hit_pct", "chance_pct", "status", "verdict", "now_text")}
                         for row in value["signals"]]}
    answer = generate_answer(model, [
        Message("system", "You explain a test of market signals to a non-expert in 4 to 6 short sentences. Use only the numbers "
                          "given. Say which signals held up after allowing for luck and which did not, what the usual and combined "
                          "odds mean, and what the later-third test showed. Never predict a price, never give advice to buy or "
                          "sell, and say plainly when nothing beat chance."),
        Message("user", json.dumps(facts, ensure_ascii=False))], temperature=.2, max_tokens=420)
    return str(getattr(answer, "content", answer)).strip()


def report_lines(value: dict[str, Any]) -> list[str]:
    from app.markets import money
    lines = [f"{value['symbol']} signal lab (tested on its own past, not a forecast) · now {money(value['price'])}"
             + (" live" if value["live"] else "") + f" · {value['history_days']} days since {value['history_from']}",
             value["summary"]]
    for horizon in value["horizons"]:
        low, high = horizon["usual_interval_pct"]
        line = f"{horizon['label'].capitalize()}: usually higher {horizon['usual_up_pct']}% ({low}–{high}%)"
        if horizon["from_signals"]:
            line += f"; with the tested combination {horizon['odds_up_pct']}%"
        lines.append(line + f". Later-third check: {horizon['holdout']['verdict']}.")
    shown = [row for row in value["signals"] if row["status"] in ("works", "luck", "backwards")] or value["signals"][:4]
    for row in shown[:8]:
        lines.append(f"  {row['label']} ({row['horizon_label']}): right {row['hit_pct']}% of {row['calls']} calls vs {row['chance_pct']}% "
                     f"by chance · {row['verdict']} · today: {row['now_text']}")
    rest = len(value["signals"]) - len(shown[:8])
    if rest > 0:
        lines.append(f"  {rest} more tests no better than chance or with too few calls (Trade Center → Signals for all).")
    lines.append(value["basis"])
    return lines
