"""Numbers behind the Trade Center's 3D views, kept apart from Tk so they can be tested.

* **Market space:** many coins and shares placed by what they did over the last month — return,
  swing (annualised volatility) and how closely they move with Bitcoin and the S&P 500 —
  sized by trading volume and coloured by today's move.
* **Odds surface:** for the market on screen, the share of past periods that ended at or above
  each move, for every period from an hour to a year (from app/market_periods.py's measured
  5th–95th percentiles; beyond them the surface only fades to 0 or 100 % and says so).
* **Link web:** the market on screen at the centre and the markets that move with it around it
  (app/market_influences.py), nearer and higher the tighter the link.
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

#: Markets placed when the watchlist adds nothing: big coins, the market funds, gold, sectors and big shares.
SPACE_BOARD = (("BTC", "crypto"), ("ETH", "crypto"), ("SOL", "crypto"), ("XRP", "crypto"), ("DOGE", "crypto"), ("ADA", "crypto"),
               ("LINK", "crypto"), ("AVAX", "crypto"), ("SPY", "stock"), ("QQQ", "stock"), ("GLD", "stock"), ("SMH", "stock"),
               ("XLE", "stock"), ("NVDA", "stock"), ("AAPL", "stock"), ("MSFT", "stock"), ("TSLA", "stock"), ("COIN", "stock"))
MAX_MARKETS = 22
DAYS = {"1w": 7, "1m": 30, "3m": 90}
SURFACE_COLUMNS = 21  # moves across the surface


def _moves(rows: list[dict[str, float]]) -> dict[int, float]:
    return {after["time"]: math.log(after["close"] / before["close"]) for before, after in zip(rows, rows[1:])
            if before["close"] > 0 and after["close"] > 0}


def _corr(first: dict[int, float], second: dict[int, float], days: int = 90) -> float | None:
    shared = sorted(set(first) & set(second))[-days:]
    if len(shared) < 20:
        return None
    try:
        return round(statistics.correlation([first[day] for day in shared], [second[day] for day in shared]), 2)
    except statistics.StatisticsError:
        return None


def market_space(markets: Any, symbols: list[tuple[str, str]] | None = None, span: str = "1m") -> dict[str, Any]:
    """Each market's return, swing and links over `span`, for the 3D market space."""
    if span not in DAYS:
        raise ValueError("Choose a span: " + ", ".join(DAYS) + ".")
    wanted = list(symbols or [])
    if not wanted:
        if getattr(markets, "path", None):
            try:
                wanted = [(symbol, "auto") for symbol in markets.watchlist("list")["symbols"]]
            except Exception:  # the board stands in for an unreadable watchlist
                log_swallowed(_log, "reading the watchlist for the market space")
        wanted += [item for item in SPACE_BOARD if item[0] not in {symbol for symbol, _asset in wanted}]
    wanted = wanted[:MAX_MARKETS]
    days = DAYS[span]
    found: dict[str, tuple[str, list[dict[str, float]]]] = {}
    gaps = []
    for symbol, asset in wanted + [("BTC", "crypto"), ("SPY", "stock")]:
        if symbol in found:
            continue
        try:
            name, kind, rows = markets.candles(symbol, 1440, asset)
            rows = [dict(row) for row in trim(rows)]
            price = _live(markets, name, kind)
            if price and rows:
                rows[-1]["close"] = float(price)
            found[name] = (kind, rows)
        except Exception as exc:  # one unknown market does not empty the space
            gaps.append(f"{symbol}: {str(exc)[:80]}")
    bitcoin, market = _moves(found["BTC"][1]) if "BTC" in found else {}, _moves(found["SPY"][1]) if "SPY" in found else {}
    points = []
    for symbol, _asset in wanted:
        entry = found.get(symbol) or found.get(str(symbol).upper())
        if entry is None:
            continue
        kind, rows = entry
        if len(rows) < days + 2:
            gaps.append(f"{symbol}: under {days} days of history")
            continue
        recent = rows[-days - 1:]
        logs = [math.log(after["close"] / before["close"]) for before, after in zip(recent, recent[1:]) if before["close"] > 0]
        moves = _moves(rows)
        name = str(symbol).upper()
        points.append({"symbol": name, "asset": kind, "price": rows[-1]["close"],
                       "return_pct": round((rows[-1]["close"] / recent[0]["close"] - 1) * 100, 2),
                       "volatility_pct": round(statistics.pstdev(logs) * math.sqrt(365) * 100, 1) if len(logs) > 2 else None,
                       "change_today_pct": round((rows[-1]["close"] / rows[-2]["close"] - 1) * 100, 2),
                       "corr_btc": 1.0 if name == "BTC" else _corr(moves, bitcoin),
                       "corr_spy": 1.0 if name == "SPY" else _corr(moves, market),
                       "volume": sum(row.get("volume", 0.0) * row["close"] for row in recent),
                       "thin": sum(row.get("trades", 0) for row in rows[-5:]) < 25})
    if not points:
        raise ValueError("No market could be placed: " + "; ".join(gaps[:3]))
    return {"markets": True, "space_view": True, "span": span, "days": days, "points": points, "gaps": gaps,
            "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": "Kraken daily candles with live streamed prices where followed",
            "basis": "What each market did over the span, not what it will do. Not advice; HAYAT never trades."}


def squash(move_pct: float) -> float:
    """Signed square root of the log move: an hour's and a year's moves fit on one axis."""
    value = math.log(max(1e-9, 1 + move_pct / 100))
    return math.copysign(math.sqrt(abs(value)), value)


def unsquash(value: float) -> float:
    return (math.exp(math.copysign(value * value, value)) - 1) * 100


def share_above(levels: dict[str, dict[str, float]], move_pct: float) -> tuple[float, bool]:
    """(share of past periods ending at or above `move_pct`, measured?) from the 5th–95th percentile moves."""
    known = [(levels[key]["move_pct"], share) for key, share in (("p05", 95.0), ("p25", 75.0), ("p50", 50.0), ("p75", 25.0), ("p95", 5.0))]
    low, high = known[0][0], known[-1][0]
    if move_pct <= low:  # below the 5th percentile: fade to 100 % over one more lower gap, unmeasured
        gap = max(1e-9, known[1][0] - low)
        return min(100.0, 95.0 + 5.0 * (low - move_pct) / gap), False
    if move_pct >= high:
        gap = max(1e-9, high - known[-2][0])
        return max(0.0, 5.0 - 5.0 * (move_pct - high) / gap), False
    for (left, left_share), (right, right_share) in zip(known, known[1:]):
        if left <= move_pct <= right:
            part = (move_pct - left) / (right - left) if right > left else 0.0
            return left_share + (right_share - left_share) * part, True
    return 50.0, True


def odds_surface(periods_value: dict[str, Any]) -> dict[str, Any]:
    """A grid: one row per measured period, one column per move, each the share ending at or above it."""
    rows = [row for row in periods_value.get("periods", []) if row.get("available")]
    if not rows:
        raise ValueError("No measured period to draw.")
    reach = max(abs(squash(row["range"][key]["move_pct"])) for row in rows for key in ("p05", "p95")) * 1.15 or .01
    columns = [unsquash(reach * (2 * index / (SURFACE_COLUMNS - 1) - 1)) for index in range(SURFACE_COLUMNS)]
    grid = []
    for row in rows:
        cells = [share_above(row["range"], move) for move in columns]
        grid.append({"key": row["key"], "label": row["label"], "chance_higher_pct": row["chance_higher"]["up_pct"],
                     "shares": [round(share, 1) for share, _measured in cells], "measured": [measured for _share, measured in cells],
                     "middle_pct": row["range"]["p50"]["move_pct"]})
    return {"symbol": periods_value["symbol"], "price": periods_value["price"], "moves_pct": [round(move, 2) for move in columns],
            "reach": reach, "rows": grid}


def link_web(value: dict[str, Any]) -> dict[str, Any]:
    """Nodes around the market on screen: angle by order, distance shorter and height greater the tighter the link."""
    nodes = []
    drivers = [row for row in value.get("drivers", []) if row.get("corr_90") is not None]
    for index, row in enumerate(drivers):
        angle = index / max(1, len(drivers)) * math.tau
        link = abs(row["corr_90"])
        distance = .35 + .6 * (1 - link)
        nodes.append({"symbol": row["symbol"], "asset": row["asset"], "label": row["label"], "corr": row["corr_90"],
                      "beta": row["beta_90"], "lead": row["lead"]["significant"], "steady": row["steady"],
                      "x": round(math.cos(angle) * distance, 3), "z": round(math.sin(angle) * distance, 3),
                      "y": round(max(-1.0, min(1.0, (row["beta_90"] or 0) / 2)), 3), "text": row["text"],
                      "today_pct": row["today"]["driver_pct"]})
    return {"symbol": value["symbol"], "nodes": nodes, "model": value.get("model")}
