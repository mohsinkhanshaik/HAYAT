"""Market intelligence: why markets move, how events, positioning and news tone relate to them, and how
good HAYAT's own odds have really been (2026-11-09).

Builds on the Trade Center's live prices, scenarios (situation odds), periods (ranges), signal lab and
influences. Every number here comes from public data with its sample size, and every relationship is
tested against chance before HAYAT says it exists:

* **Forecast scorecard.** Each scenario HAYAT gives is recorded (odds of being higher in a day and a week,
  and the weak-to-strong range). When the time is up it is scored against the real price: Brier score
  against simply quoting the usual rate, and how often the price landed inside the range. A model that
  has done worse than the usual rate over enough cases is distrusted: its odds are replaced by the usual
  rate and HAYAT says why. That is learning from outcomes, measured.
* **Event impact.** For a kind of world event (earthquakes, conflict, cyber attacks, storms…) recorded by
  Global Intelligence, how the asset moved over the next day or week after those events, against all days,
  with a permutation test: "no measurable effect" unless chance rarely produces such a gap.
* **Institutional positioning.** The CFTC's weekly Commitments of Traders (Traders in Financial Futures):
  how asset managers, leveraged funds and dealers are positioned in Bitcoin, Ether, S&P 500, Nasdaq-100,
  euro and yen futures, where that stands against three years, and whether past extremes or changes
  preceded the next week's move.
* **News tone.** GDELT's average tone of worldwide coverage of a subject, day by day, and whether a change
  in tone ever preceded the next day's move.

None of it is a forecast or advice, and HAYAT never trades.
"""
from __future__ import annotations

import json
import logging
import math
import random
import sqlite3
import statistics
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.logging import log_swallowed

_log = logging.getLogger(__name__)
DAY = 86400
#: A model needs this many scored forecasts before its record can overrule it.
MIN_SCORED = 20
#: Fewer event days than this and an event study says "too few".
MIN_EVENTS = 8


# ------------------------------------------------------------------------------------- statistics

def pearson(xs: list[float], ys: list[float]) -> tuple[float | None, float | None]:
    """(r, two-sided p) for paired values; p from the t distribution's normal approximation."""
    pairs = [(x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]
    n = len(pairs)
    if n < 8:
        return None, None
    mx, my = statistics.fmean(x for x, _ in pairs), statistics.fmean(y for _, y in pairs)
    sx = math.sqrt(sum((x - mx) ** 2 for x, _ in pairs))
    sy = math.sqrt(sum((y - my) ** 2 for _, y in pairs))
    if not sx or not sy:
        return None, None
    r = sum((x - mx) * (y - my) for x, y in pairs) / (sx * sy)
    r = max(-.999999, min(.999999, r))
    t = r * math.sqrt((n - 2) / (1 - r * r))
    p = math.erfc(abs(t) / math.sqrt(2))
    return round(r, 3), round(p, 4)


def permutation_p(sample: list[float], population: list[float], rounds: int = 2000, seed: int = 7) -> float:
    """Two-sided p that a random draw of the same size from `population` has a mean at least as far off."""
    if not sample or len(population) < len(sample):
        return 1.0
    centre = statistics.fmean(population)
    gap = abs(statistics.fmean(sample) - centre)
    generator = random.Random(seed)
    hits = sum(abs(statistics.fmean(generator.sample(population, len(sample))) - centre) >= gap for _ in range(rounds))
    return (hits + 1) / (rounds + 1)


def plainly(r: float | None, p: float | None, n: int, what: str) -> str:
    if r is None:
        return f"Too few cases to test whether {what} ({n})."
    if p is not None and p < .05 and abs(r) >= .1:
        direction = "went with" if r > 0 else "went against"
        return (f"Measurable but small: {what} {direction} the next move (r = {r:+.2f}, {n} cases, p = {p:.3f}). "
                "A pattern in the past, not a promise.")
    return f"No measurable link: {what} did not predict the next move better than chance (r = {r:+.2f}, {n} cases)."


# ---------------------------------------------------------------------------------- forecast scorecard

class ForecastLedger:
    """Every scenario forecast HAYAT gave, scored against what the price really did."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("CREATE TABLE IF NOT EXISTS forecasts (id INTEGER PRIMARY KEY, issued_at REAL, day TEXT, symbol TEXT, "
                   "asset TEXT, model TEXT, horizon_days INTEGER, price REAL, p_up REAL, base_up REAL, low REAL, high REAL, "
                   "nominal REAL, due_at REAL, resolved_at REAL, outcome REAL, up INTEGER, inside INTEGER, "
                   "UNIQUE(symbol, model, horizon_days, day))")
        return db

    def record(self, symbol: str, asset: str, model: str, horizon_days: int, price: float, p_up: float | None,
               base_up: float | None = None, low: float | None = None, high: float | None = None,
               nominal: float | None = None, now: float | None = None) -> bool:
        """One forecast per market, model and horizon per day (asking twice in a day is one forecast)."""
        now = time.time() if now is None else now
        day = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO forecasts (issued_at, day, symbol, asset, model, horizon_days, price, p_up, base_up, "
                "low, high, nominal, due_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, day, symbol, asset, model, horizon_days, price, p_up, base_up, low, high, nominal, now + horizon_days * DAY))
            return cursor.rowcount == 1

    def record_scenarios(self, value: dict[str, Any], now: float | None = None) -> int:
        """The odds and the weak-to-strong range of a market_scenarios result."""
        symbol, asset, price = value["symbol"], value.get("asset", ""), float(value["price"])
        count = 0
        for key, days in (("next_day", 1), ("next_week", int(value.get("next_week", {}).get("days", 7)))):
            part = value.get(key) or {}
            now_odds, usual = part.get("now") or {}, part.get("usual") or {}
            if now_odds.get("up_pct") is None:
                continue
            paths = value.get("paths") or {} if key == "next_week" else {}
            count += self.record(symbol, asset, "situation odds", days, price, now_odds["up_pct"] / 100,
                                 (usual.get("up_pct") or 50) / 100,
                                 (paths.get("weak") or {}).get("price"), (paths.get("strong") or {}).get("price"),
                                 .8 if paths else None, now)
        return count

    def resolve(self, markets: Any, now: float | None = None) -> int:
        """Score every forecast whose time is up, from Kraken daily closes (one read per market)."""
        now = time.time() if now is None else now
        with self._lock, self._connect() as db:
            due = db.execute("SELECT id, symbol, asset, due_at, price, low, high FROM forecasts "
                             "WHERE resolved_at IS NULL AND due_at <= ?", (now,)).fetchall()
        if not due:
            return 0
        closes: dict[tuple[str, str], list[dict]] = {}
        done = 0
        for row_id, symbol, asset, due_at, price, low, high in due:
            key = (symbol, asset or "auto")
            if key not in closes:
                try:
                    closes[key] = markets.candles(symbol, 1440, asset or "auto")[2]
                except Exception:  # offline or delisted: try again next time
                    log_swallowed(_log, f"reading {symbol} to score its forecasts")
                    closes[key] = []
            # The first daily close at or after the due moment.
            later = [candle for candle in closes[key] if candle["time"] >= due_at - DAY / 2 and candle["close"] > 0]
            if not later or later[0]["time"] > now - DAY / 2:
                continue
            outcome = float(later[0]["close"])
            inside = None if low is None or high is None else int(low <= outcome <= high)
            with self._lock, self._connect() as db:
                db.execute("UPDATE forecasts SET resolved_at=?, outcome=?, up=?, inside=? WHERE id=?",
                           (now, outcome, int(outcome > price), inside, row_id))
            done += 1
        return done

    def record_card(self, symbol: str = "") -> dict[str, Any]:
        """Per model and horizon: how many scored, Brier score against the usual rate, skill, range hit-rate."""
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT symbol, model, horizon_days, p_up, base_up, up, inside, nominal FROM forecasts "
                              "WHERE resolved_at IS NOT NULL" + (" AND symbol = ?" if symbol else ""),
                              (symbol,) if symbol else ()).fetchall()
            pending = db.execute("SELECT COUNT(*) FROM forecasts WHERE resolved_at IS NULL"
                                 + (" AND symbol = ?" if symbol else ""), (symbol,) if symbol else ()).fetchone()[0]
        groups: dict[tuple[str, int], list[tuple]] = {}
        for row in rows:
            groups.setdefault((row[1], int(row[2])), []).append(row)
        cards = []
        for (model, horizon), items in sorted(groups.items()):
            brier = statistics.fmean((p - up) ** 2 for _s, _m, _h, p, _b, up, _i, _n in items)
            base = statistics.fmean(((b if b is not None else .5) - up) ** 2 for _s, _m, _h, _p, b, up, _i, _n in items)
            ranged = [inside for *_rest, inside, _nominal in items if inside is not None]
            skill = 1 - brier / base if base else 0.0
            cards.append({"model": model, "horizon_days": horizon, "scored": len(items), "brier": round(brier, 4),
                          "usual_rate_brier": round(base, 4), "skill": round(skill, 3),
                          "range_hits_pct": round(sum(ranged) / len(ranged) * 100, 1) if ranged else None,
                          "range_nominal_pct": 80 if ranged else None,
                          "trusted": len(items) < MIN_SCORED or skill >= 0,
                          "verdict": self._verdict(len(items), skill, ranged)})
        return {"symbol": symbol or "all markets", "cards": cards, "pending": pending,
                "summary": self._summary(cards, pending)}

    @staticmethod
    def _verdict(scored: int, skill: float, ranged: list[int]) -> str:
        if scored < MIN_SCORED:
            return f"Only {scored} scored so far; {MIN_SCORED} are needed before the record can judge it."
        text = (f"Beat simply quoting the usual rate (skill {skill:+.0%})." if skill > .02 else
                f"No better than the usual rate (skill {skill:+.0%})." if skill >= -.02 else
                f"Worse than the usual rate (skill {skill:+.0%}): HAYAT now shows the usual rate instead.")
        if ranged:
            hit = sum(ranged) / len(ranged) * 100
            text += f" Its 80 % range held {hit:.0f} % of the time" + (" (too narrow)." if hit < 70 else
                                                                       " (too wide)." if hit > 90 else ".")
        return text

    @staticmethod
    def _summary(cards: list[dict], pending: int) -> str:
        if not cards:
            return (f"No forecast has come due yet ({pending} waiting). Each scenario HAYAT gives is recorded and scored "
                    "when its day or week is up.")
        return " ".join(f"{card['model']}, {card['horizon_days']} day{'s' if card['horizon_days'] != 1 else ''} "
                        f"({card['scored']} scored): {card['verdict']}" for card in cards) + f" {pending} still waiting."

    def trust(self, symbol: str, model: str, horizon_days: int) -> dict[str, Any] | None:
        """The learned correction: None while the record is thin, else whether to keep the model's odds."""
        card = next((card for card in self.record_card(symbol)["cards"]
                     if card["model"] == model and card["horizon_days"] == horizon_days), None)
        if card is None or card["scored"] < MIN_SCORED:
            return None
        return {"keep": card["skill"] >= 0, "skill": card["skill"], "scored": card["scored"], "verdict": card["verdict"]}


def apply_track_record(value: dict[str, Any], ledger: ForecastLedger) -> dict[str, Any]:
    """Attach the scorecard to a scenarios result, and replace odds the record says to distrust."""
    lessons = []
    for key, days in (("next_day", 1), ("next_week", int((value.get("next_week") or {}).get("days", 7)))):
        trust = ledger.trust(value["symbol"], "situation odds", days)
        part = value.get(key) or {}
        if trust is None:
            continue
        part["track_record"] = trust
        if not trust["keep"]:
            part["replaced_by_usual_rate"] = True
            part["now"] = dict(part.get("usual") or {})
            lessons.append(f"{'Next-day' if days == 1 else 'Next-week'} situation odds for {value['symbol']} did worse than the "
                           f"usual rate over {trust['scored']} scored forecasts, so the usual rate is shown instead.")
    if lessons:
        value["lessons"] = lessons
        value["summary"] = " ".join(lessons) + " " + str(value.get("summary") or "")
    return value


# --------------------------------------------------------------------------------------- event impact

def event_study(manager: Any, markets: Any, symbol: str, category: str, horizon_days: int = 1, asset: str = "auto",
                min_severity: str = "medium") -> dict[str, Any]:
    """How the asset moved after recorded events of one kind, against all days, with a permutation test."""
    from app.intelligence.classify import CATEGORIES

    if category not in CATEGORIES:
        raise ValueError("Choose an event kind: " + ", ".join(sorted(CATEGORIES)) + ".")
    if horizon_days not in (1, 3, 7):
        raise ValueError("Use a horizon of 1, 3 or 7 days.")
    name, kind, candles = markets.candles(symbol, 1440, asset)
    candles = [candle for candle in candles if candle["close"] > 0]
    by_day = {datetime.fromtimestamp(candle["time"], UTC).strftime("%Y-%m-%d"): index for index, candle in enumerate(candles)}
    closes = [candle["close"] for candle in candles]
    _sources, rows = manager._matching("", "", None, False, category, min_severity, None)
    days = sorted({str(row.get("observed_at") or "")[:10] for row in rows if row.get("observed_at")})
    moves, used = [], []
    for day in days:
        index = by_day.get(day)
        # From the close before the event's day to the close `horizon` days later: the event day's own reaction counts.
        if index is None or index < 1 or index - 1 + horizon_days >= len(closes):
            continue
        moves.append(closes[index - 1 + horizon_days] / closes[index - 1] - 1)
        used.append(day)
    every = [closes[i + horizon_days] / closes[i] - 1 for i in range(len(closes) - horizon_days)]
    title = CATEGORIES[category][0]
    result = {"markets": True, "event_study": True, "symbol": name, "asset": kind, "category": category,
              "event_kind": title, "horizon_days": horizon_days, "event_days": len(used), "events_recorded": len(rows),
              "first_day": used[0] if used else None, "last_day": used[-1] if used else None,
              "source": "Global Intelligence event record and Kraken daily closes",
              "basis": "Moves after past events, compared with all days. Co-occurrence, not proof that the events caused them."}
    if len(used) < MIN_EVENTS:
        result["verdict"] = (f"Too few to measure: {len(used)} day{'s' if len(used) != 1 else ''} with recorded {title.lower()} "
                             f"events overlap {name}'s price history (at least {MIN_EVENTS} needed). The event record "
                             "grows every day.")
        return result
    p = permutation_p(moves, every)
    after, usual = statistics.fmean(moves) * 100, statistics.fmean(every) * 100
    up_after = sum(move > 0 for move in moves) / len(moves) * 100
    up_usual = sum(move > 0 for move in every) / len(every) * 100
    result.update({"mean_move_after_pct": round(after, 2), "mean_move_usual_pct": round(usual, 2),
                   "up_after_pct": round(up_after, 1), "up_usual_pct": round(up_usual, 1), "p_value": round(p, 4),
                   "spread_after_pct": round(statistics.pstdev(moves) * 100, 2),
                   "spread_usual_pct": round(statistics.pstdev(every) * 100, 2)})
    window = f"over {horizon_days} day{'s' if horizon_days != 1 else ''}"
    if p < .05:
        result["verdict"] = (f"After {len(used)} days with recorded {title.lower()}, {name} moved {after:+.2f} % on average {window}, "
                             f"against {usual:+.2f} % on all days; chance alone gives a gap that large only {p:.1%} of the time. "
                             "A measured pattern, not a rule.")
    else:
        result["verdict"] = (f"No measurable effect: after {len(used)} days with recorded {title.lower()}, {name} moved {after:+.2f} % "
                             f"{window}, against {usual:+.2f} % on all days, a gap chance produces {p:.0%} of the time.")
    return result


# --------------------------------------------------------------------------------- institutional positions

#: Market → CFTC contract code in the Traders in Financial Futures report (publicreporting.cftc.gov, dataset gpe5-46if).
CONTRACTS = {"BTC": ("133741", "Bitcoin futures, CME"), "ETH": ("146021", "Ether futures, CME"),
             "SPX": ("13874A", "E-mini S&P 500, CME"), "NDX": ("209742", "Nasdaq-100 mini, CME"),
             "EUR": ("099741", "Euro FX, CME"), "JPY": ("097741", "Japanese yen, CME"), "VIX": ("1170E1", "VIX futures, CBOE")}
ALIASES = {"BITCOIN": "BTC", "XBT": "BTC", "ETHER": "ETH", "ETHEREUM": "ETH", "S&P": "SPX", "S&P 500": "SPX", "SP500": "SPX",
           "SPY": "SPX", "NASDAQ": "NDX", "QQQ": "NDX", "EURO": "EUR", "YEN": "JPY"}
GROUPS = (("asset_mgr", "asset managers"), ("lev_money", "leveraged funds"), ("dealer", "dealers"))
CFTC = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def positioning_rows(fetcher: Any, code: str, weeks: int = 156) -> list[dict[str, Any]]:
    query = (f"$where=cftc_contract_market_code='{code}'&$order=report_date_as_yyyy_mm_dd%20DESC&$limit={int(weeks)}")
    resource = fetcher.fetch(CFTC + "?" + query.replace("'", "%27"), 4 * 1024 * 1024, {"application/json", "text/json"})
    rows = json.loads(bytes(resource.content))
    return sorted(rows, key=lambda row: row.get("report_date_as_yyyy_mm_dd") or "")


def _long_short(row: dict[str, Any], group: str) -> tuple[float, float]:
    suffix = "_all" if group == "dealer" else ""
    return _number(row.get(f"{group}_positions_long{suffix}")), _number(row.get(f"{group}_positions_short{suffix}"))


def positioning(fetcher: Any, market: str, markets: Any = None, rows: list[dict] | None = None) -> dict[str, Any]:
    """Who holds what in the futures market, against three years, and whether it ever led the next week."""
    key = ALIASES.get(market.strip().upper(), market.strip().upper())
    if key not in CONTRACTS:
        raise ValueError("Positioning is published for: " + ", ".join(f"{name} ({label})" for name, (_c, label) in CONTRACTS.items()) + ".")
    code, label = CONTRACTS[key]
    rows = rows if rows is not None else positioning_rows(fetcher, code)
    if len(rows) < 8:
        raise ValueError(f"The CFTC returned only {len(rows)} weeks for {label}.")
    latest = rows[-1]
    groups = {}
    for group, title in GROUPS:
        nets = [(lambda pair: pair[0] - pair[1])(_long_short(row, group)) for row in rows]
        opens = [_number(row.get("open_interest_all")) or 1.0 for row in rows]
        shares = [net / open_ * 100 for net, open_ in zip(nets, opens)]
        now = shares[-1]
        rank = sum(value <= now for value in shares) / len(shares) * 100
        # Relative to its own three years: a group that is usually very short can be "near its least short" while
        # still net short (live, 2026-11-09: leveraged funds -14.8 % of open interest, the least short in 3 years).
        if rank >= 90:
            reading = "near its most long in three years" if nets[-1] > 0 else "net short, but near its least short in three years"
        elif rank <= 10:
            reading = "near its most short in three years" if nets[-1] < 0 else "net long, but near its least long in three years"
        else:
            reading = "within its usual range"
        groups[group] = {"who": title, "net_contracts": int(nets[-1]), "net_share_of_open_interest_pct": round(now, 1),
                         "weekly_change_contracts": int(nets[-1] - nets[-2]),
                         "percentile_3y": round(rank, 0), "extreme": rank >= 90 or rank <= 10, "reading": reading}
    test = None
    if markets is not None and key in ("BTC", "ETH"):
        test = _positioning_test(markets, key, rows)
    report_day = str(latest.get("report_date_as_yyyy_mm_dd") or "")[:10]
    lev = groups["lev_money"]
    return {"markets": True, "positioning_view": True, "market": key, "contract": label, "report_date": report_day,
            "weeks": len(rows), "open_interest": int(_number(latest.get("open_interest_all"))), "groups": groups,
            "leads_next_week": test,
            "summary": (f"{label}, report of {report_day}: leveraged funds are net {lev['net_contracts']:+,} contracts "
                        f"({lev['net_share_of_open_interest_pct']:+.1f} % of open interest, {lev['reading']}); asset managers "
                        f"{groups['asset_mgr']['net_contracts']:+,} ({groups['asset_mgr']['reading']})."
                        + (" " + test["verdict"] if test else "")),
            "source": "CFTC Commitments of Traders, Traders in Financial Futures (weekly, positions as of Tuesday)",
            "basis": ("Positions are a week old when published. Crowded positioning is a risk to note, not a signal to "
                      "act on; HAYAT tests whether it ever preceded moves and says so.")}


def _positioning_test(markets: Any, key: str, rows: list[dict]) -> dict[str, Any] | None:
    """Did the change in leveraged funds' net position predict the next week's return?"""
    try:
        _name, _kind, weekly = markets.candles(key, 10080, "crypto")
    except Exception:
        log_swallowed(_log, f"reading weekly {key} for the positioning test")
        return None
    closes = {datetime.fromtimestamp(candle["time"], UTC).strftime("%G-%V"): candle["close"] for candle in weekly if candle["close"] > 0}
    changes, returns = [], []
    nets = [(lambda pair: pair[0] - pair[1])(_long_short(row, "lev_money")) for row in rows]
    for index in range(1, len(rows)):
        moment = datetime.fromisoformat(str(rows[index]["report_date_as_yyyy_mm_dd"])[:10]).replace(tzinfo=UTC)
        week = moment.strftime("%G-%V")
        following = datetime.fromtimestamp(moment.timestamp() + 7 * DAY, UTC).strftime("%G-%V")
        if week in closes and following in closes:
            changes.append(nets[index] - nets[index - 1])
            returns.append(closes[following] / closes[week] - 1)
    r, p = pearson(changes, returns)
    return {"r": r, "p": p, "weeks": len(changes),
            "verdict": plainly(r, p, len(changes), "a change in leveraged funds' position")}


# -------------------------------------------------------------------------------------------- news tone

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
SUBJECTS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple xrp", "DOGE": "dogecoin",
            "AAPL": "apple iphone", "NVDA": "nvidia", "TSLA": "tesla", "MSFT": "microsoft", "SPX": "stock market",
            "GOLD": "gold price", "OIL": "oil price"}
_tone_cache: dict[str, tuple[float, dict]] = {}
_tone_lock = threading.Lock()
TONE_CACHE_SECONDS = 3 * 3600


def tone_series(fetcher: Any, subject: str) -> list[tuple[str, float]]:
    """(day, average tone) for three months of worldwide coverage, from GDELT (cached; GDELT asks one request per 5 s)."""
    with _tone_lock:
        cached = _tone_cache.get(subject)
        if cached and time.time() - cached[0] < TONE_CACHE_SECONDS:
            return cached[1]["series"]
    url = f"{GDELT}?query={quote(subject)}&mode=timelinetone&timespan=3months&format=json"
    resource = fetcher.fetch(url, 2 * 1024 * 1024, {"application/json", "text/json", "text/plain", "text/html"})
    text = bytes(resource.content).decode("utf-8", errors="replace").strip()
    if not text.startswith("{"):
        raise ValueError("GDELT asked for fewer requests right now (it allows one every five seconds); try again shortly.")
    data = json.loads(text)
    series = []
    for timeline in data.get("timeline", []):
        for point in timeline.get("data", []):
            stamp, value = str(point.get("date") or ""), point.get("value")
            if len(stamp) >= 8 and isinstance(value, (int, float)):
                series.append((f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", float(value)))
    daily: dict[str, list[float]] = {}
    for day, value in series:
        daily.setdefault(day, []).append(value)
    result = sorted((day, statistics.fmean(values)) for day, values in daily.items())
    if not result:  # an empty answer is usually throttling: never keep it in place of real data
        raise ValueError("GDELT returned no tone data just now (it may be limiting requests); try again shortly.")
    with _tone_lock:
        _tone_cache[subject] = (time.time(), {"series": result})
    return result


def sentiment(fetcher: Any, markets: Any, symbol: str, subject: str = "", series: list[tuple[str, float]] | None = None) -> dict[str, Any]:
    """The tone of worldwide news about a market, recent against usual, and whether tone changes led the next day."""
    key = symbol.strip().upper()
    subject = subject or SUBJECTS.get(key, symbol.strip().lower())
    series = series if series is not None else tone_series(fetcher, subject)
    if len(series) < 14:
        raise ValueError(f"GDELT returned only {len(series)} days of tone for “{subject}”.")
    values = [value for _day, value in series]
    recent, usual = statistics.fmean(values[-7:]), statistics.fmean(values)
    spread = statistics.pstdev(values) or 1.0
    result = {"markets": True, "sentiment_view": True, "symbol": key, "subject": subject, "days": len(series),
              "tone_last_7_days": round(recent, 2), "tone_3_months": round(usual, 2),
              "difference_in_spreads": round((recent - usual) / spread, 2),
              "reading": ("more negative than usual" if recent < usual - .5 * spread else
                          "more positive than usual" if recent > usual + .5 * spread else "about usual"),
              "source": "GDELT 2.0 DOC API: average tone of worldwide online news coverage, by day",
              "basis": "Tone measures wording, not facts or future prices. A link to the next day's move is reported only "
                       "if it beats chance."}
    test = None
    try:
        name, _kind, candles = markets.candles(key, 1440, "auto")
        closes = {datetime.fromtimestamp(candle["time"], UTC).strftime("%Y-%m-%d"): candle["close"] for candle in candles if candle["close"] > 0}
        changes, nexts = [], []
        for (day_a, tone_a), (day_b, tone_b) in zip(series, series[1:]):
            following = datetime.fromtimestamp(datetime.fromisoformat(day_b).replace(tzinfo=UTC).timestamp() + DAY, UTC).strftime("%Y-%m-%d")
            if day_b in closes and following in closes:
                changes.append(tone_b - tone_a)
                nexts.append(closes[following] / closes[day_b] - 1)
        r, p = pearson(changes, nexts)
        test = {"r": r, "p": p, "days": len(changes), "verdict": plainly(r, p, len(changes), "a change in news tone")}
    except Exception:
        log_swallowed(_log, f"testing news tone against {key}")
    result["leads_next_day"] = test
    result["summary"] = (f"News about {subject} over the last week reads {result['reading']} (tone {recent:+.2f} against "
                         f"{usual:+.2f} over three months)." + (" " + test["verdict"] if test else ""))
    return result


# -------------------------------------------------------------------------------------------- the brief

def brief(markets: Any, manager: Any, ledger: ForecastLedger, symbol: str, asset: str = "auto") -> dict[str, Any]:
    """Everything HAYAT can say about one market, in plain words: what is happening, why it might be moving,
    what could happen with odds, how good those odds have been, and the risks. Each part says where it came from."""
    from app.market_scenarios import scenarios as compute

    sections: dict[str, Any] = {}
    gaps: list[str] = []
    ledger.resolve(markets)
    try:
        events = None
        if manager is not None:
            from app.market_events import events_and_markets
            events = events_and_markets(manager, markets)
        value = compute(markets, symbol, asset, events)
        ledger.record_scenarios(value)
        value = apply_track_record(value, ledger)
        sections["scenarios"] = value
    except Exception as exc:
        gaps.append(f"scenarios: {exc}")
    try:
        from app.market_influences import influences

        sections["influences"] = influences(markets, symbol, asset, None)
    except Exception as exc:
        gaps.append(f"what moves it: {exc}")
    key = ALIASES.get(symbol.strip().upper(), symbol.strip().upper())
    if key in CONTRACTS:
        try:
            sections["positioning"] = positioning(markets.fetcher, key, markets)
        except Exception as exc:
            gaps.append(f"positioning: {exc}")
    try:
        sections["sentiment"] = sentiment(markets.fetcher, markets, symbol)
    except Exception as exc:
        gaps.append(f"news tone: {exc}")
    sections["track_record"] = ledger.record_card((sections.get("scenarios") or {}).get("symbol", symbol))
    return {"markets": True, "brief_view": True, "symbol": symbol, "sections": sections, "gaps": gaps,
            "text": brief_text(symbol, sections, gaps),
            "basis": "Measured from public data with sample sizes; possibilities with odds, never promises or advice."}


def brief_text(symbol: str, sections: dict[str, Any], gaps: list[str]) -> str:
    lines = [f"{symbol.upper()} — what the evidence says"]
    scenarios = sections.get("scenarios")
    if scenarios:
        lines.append(f"Now: {scenarios['price']:,.6g} ({scenarios['source']}). Situation: "
                     + ", ".join(f"{key} {value}" for key, value in (scenarios.get("situation") or {}).items()) + ".")
        week = scenarios.get("next_week") or {}
        now, usual = week.get("now") or {}, week.get("usual") or {}
        lines.append(f"What could happen this week: higher in {now.get('up_pct')} % of {now.get('samples')} similar past cases "
                     f"(usual {usual.get('up_pct')} %), {week.get('verdict')}. Weak / middle / strong: "
                     + " / ".join(f"{(scenarios['paths'][label]['move_pct']):+.1f} %" for label in ("weak", "middle", "strong")) + ".")
        for lesson in scenarios.get("lessons") or []:
            lines.append("Learned: " + lesson)
    influences = sections.get("influences")
    if influences and influences.get("summary"):
        lines.append("Why it moves: " + str(influences["summary"]))
    if sections.get("positioning"):
        lines.append("Big players: " + sections["positioning"]["summary"])
    if sections.get("sentiment"):
        lines.append("News tone: " + sections["sentiment"]["summary"])
    card = sections.get("track_record") or {}
    lines.append("How good HAYAT's odds have been: " + str(card.get("summary") or "no record yet."))
    risks = []
    if scenarios and (scenarios.get("big_move") or {}).get("now_pct"):
        risks.append(f"big daily moves (over {scenarios['big_move']['threshold_pct']} %) happened on "
                     f"{scenarios['big_move']['now_pct']} % of days in this volatility state")
    position = sections.get("positioning") or {}
    crowded = [group["who"] for group in (position.get("groups") or {}).values()
               if group.get("extreme", "unusually" in str(group.get("reading")))]
    if crowded:
        risks.append("positioning at a three-year extreme among " + ", ".join(crowded) + " (it can unwind sharply)")
    if risks:
        lines.append("Risks to keep in mind: " + "; ".join(risks) + ".")
    if gaps:
        lines.append("Not available this time: " + "; ".join(gap.rstrip(". ") for gap in gaps) + ".")
    lines.append("These are odds from the past and readings of public data, not predictions or advice. HAYAT never trades.")
    return "\n".join(lines)
