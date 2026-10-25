"""Scheduled events that move markets, and how much they have moved them before.

* **Fed rate decisions:** read from the Federal Reserve's own FOMC calendar page (published
  dates, past and coming). The decision is on a meeting's last day.
* **US jobs report:** usually the first Friday of the month at 8:30 ET. The Bureau of Labor
  Statistics refuses automated reading of its schedule, so this is the usual pattern, labelled as
  such, not the published date (holidays move it).
* **Company earnings:** from Finnhub's earnings calendar when the owner has added a free Finnhub key
  (Studios → Keys); the key travels in a header, never in an address.

**What it did before.** For each kind of event, HAYAT looks at the market's own daily history:
the move from the close before the event day to the close after it, on every past event day,
against the same two-day move on all days. A ratio above 1 means those days moved it more than
usual. Direction is shown too, but past direction says little about the next one.
"""
from __future__ import annotations

import logging
import os
import re
import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FINNHUB = "https://finnhub.io/api/v1/"
MONTHS = {name: index for index, name in enumerate(("January", "February", "March", "April", "May", "June", "July", "August",
                                                    "September", "October", "November", "December"), 1)}
#: Markets each kind of event usually bears on.
EVENT_MARKETS = {"fed": (("SPY", "stock"), ("QQQ", "stock"), ("GLD", "stock"), ("BTC", "crypto")),
                 "jobs": (("SPY", "stock"), ("GLD", "stock"), ("BTC", "crypto"))}
EARNINGS_WATCH = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX", "COIN", "MSTR", "TSM")


def parse_fomc(html: str) -> list[date]:
    """Decision days (each meeting's last day) from the Fed's calendar page; notation votes are left out."""
    days: list[date] = []
    for section in re.split(r"(?=(\d{4}) FOMC Meetings)", html):
        year_match = re.match(r"(\d{4}) FOMC Meetings", section)
        if not year_match:
            continue
        year = int(year_match[1])
        for months, dates in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<',
                                        section, re.S):
            text = dates.strip()
            if "notation" in text.lower() or "unscheduled" in text.lower():
                continue
            numbers = re.findall(r"\d{1,2}", text)
            names = months.split("/")
            if not numbers or names[-1] not in MONTHS:
                continue
            try:
                days.append(date(year, MONTHS[names[-1]], int(numbers[-1])))
            except ValueError:
                continue
    return sorted(set(days))


def first_fridays(start: date, end: date) -> list[date]:
    """The usual US jobs-report days (first Friday of each month) between two dates."""
    result, cursor = [], date(start.year, start.month, 1)
    while cursor <= end:
        friday = cursor + timedelta(days=(4 - cursor.weekday()) % 7)
        if start <= friday <= end:
            result.append(friday)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return result


def reaction(candles: list[dict[str, Any]], days: list[date]) -> dict[str, Any] | None:
    """Two-day moves (close before → close after) on event days against all days, from daily candles."""
    closes = {datetime.fromtimestamp(candle["time"], UTC).date(): candle["close"] for candle in candles if candle["close"] > 0}
    ordered = sorted(closes)
    if len(ordered) < 60:
        return None
    position = {day: index for index, day in enumerate(ordered)}
    all_moves = [closes[ordered[index + 1]] / closes[ordered[index - 1]] - 1 for index in range(1, len(ordered) - 1)]
    event_moves = []
    for day in days:
        index = position.get(day)
        if index is None or index == 0 or index >= len(ordered) - 1:
            continue
        event_moves.append(closes[ordered[index + 1]] / closes[ordered[index - 1]] - 1)
    if len(event_moves) < 3:
        return None
    usual = statistics.mean(abs(move) for move in all_moves)
    typical = statistics.mean(abs(move) for move in event_moves)
    return {"events": len(event_moves), "event_move_pct": round(typical * 100, 2), "usual_move_pct": round(usual * 100, 2),
            "ratio": round(typical / usual, 2) if usual else None, "up_share_pct": round(sum(move > 0 for move in event_moves) / len(event_moves) * 100),
            "largest_pct": round(max(event_moves, key=abs) * 100, 2)}


class MarketCalendar:
    def __init__(self, fetcher: Any, markets: Any = None) -> None:
        self.fetcher, self.markets = fetcher, markets
        self._fomc: tuple[float, list[date]] | None = None

    def fomc(self) -> list[date]:
        import time
        if self._fomc and time.monotonic() - self._fomc[0] < 6 * 3600:
            return self._fomc[1]
        html = self.fetcher.fetch(FOMC_URL, 2 * 1024 * 1024).content.decode("utf-8", "replace")
        days = parse_fomc(html)
        if not days:
            raise ValueError("The Fed's calendar page had no meeting dates HAYAT could read.")
        self._fomc = (time.monotonic(), days)
        return days

    def earnings(self, start: date, end: date) -> list[dict[str, Any]]:
        key = os.environ.get("HAYAT_KEY_FINNHUB", "").strip()
        if not key:
            return []
        import json
        url = FINNHUB + f"calendar/earnings?from={start.isoformat()}&to={end.isoformat()}"
        data = json.loads(self.fetcher.fetch(url, 4 * 1024 * 1024, request_headers={"X-Finnhub-Token": key}).content)
        return [row for row in data.get("earningsCalendar") or [] if row.get("symbol") in EARNINGS_WATCH]

    def upcoming(self, days_ahead: int = 45) -> dict[str, Any]:
        """Coming events with countdowns and how the markets they touch reacted before."""
        today = datetime.now(UTC).date()
        until = today + timedelta(days=days_ahead)
        items: list[dict[str, Any]] = []
        gaps: list[str] = []
        fed: list[date] = []
        try:
            fed = self.fomc()
            items += [{"kind": "fed", "title": "Fed interest-rate decision (FOMC)", "date": day.isoformat(), "time": "14:00 ET",
                       "basis": "published by the Federal Reserve", "source": FOMC_URL} for day in fed if today <= day <= until]
        except Exception as exc:
            log_swallowed(_log, "reading the Fed's meeting calendar")
            gaps.append(f"Fed calendar: {type(exc).__name__}")
        items += [{"kind": "jobs", "title": "US jobs report (nonfarm payrolls)", "date": day.isoformat(), "time": "08:30 ET",
                   "basis": "usual pattern (first Friday); the BLS schedule refuses automated reading, so check bls.gov",
                   "source": "pattern"} for day in first_fridays(today, until)]
        try:
            for row in self.earnings(today, until):
                items.append({"kind": "earnings", "symbol": row["symbol"], "title": f"{row['symbol']} earnings", "date": row["date"],
                              "time": {"bmo": "before the open", "amc": "after the close"}.get(row.get("hour", ""), ""),
                              "basis": "Finnhub earnings calendar", "source": "finnhub"})
        except Exception as exc:
            log_swallowed(_log, "reading the earnings calendar")
            gaps.append(f"Earnings (Finnhub): {type(exc).__name__}")
        if not os.environ.get("HAYAT_KEY_FINNHUB"):
            gaps.append("Company earnings: add a free Finnhub key in Studios → Keys to see them.")
        for item in items:
            item["days_until"] = (date.fromisoformat(item["date"]) - today).days
        items.sort(key=lambda item: (item["date"], item["kind"]))
        effects = self.effects(fed) if self.markets is not None else {}
        for item in items:
            item["effects"] = effects.get(item["kind"], [])
        return {"markets": True, "calendar_view": True, "events": items, "effects": effects, "gaps": gaps,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "basis": ("Event-day moves are two-day moves (close before to close after) on past event days, against all two-day "
                          "moves, from Kraken daily prices. Bigger past moves mean bigger swings were common, not which way the "
                          "next one goes.")}

    def effects(self, fed: list[date]) -> dict[str, list[dict[str, Any]]]:
        """For each kind of event, how each market it touches moved on past event days."""
        today = datetime.now(UTC).date()
        past = {"fed": [day for day in fed if day < today],
                "jobs": first_fridays(today - timedelta(days=730), today - timedelta(days=1))}
        result: dict[str, list[dict[str, Any]]] = {}
        for kind, markets in EVENT_MARKETS.items():
            rows = []
            for symbol, asset in markets:
                try:
                    _name, _kind, candles = self.markets.candles(symbol, 1440, asset)
                    measured = reaction(candles, past[kind])
                except Exception:  # a market without history is left out, not guessed
                    log_swallowed(_log, "measuring an event-day reaction")
                    measured = None
                if measured:
                    rows.append({"symbol": symbol, **measured})
            result[kind] = rows
        return result


def report_lines(value: dict[str, Any]) -> list[str]:
    lines = ["Scheduled events that can move markets:"]
    for item in value["events"][:10]:
        when = "today" if item["days_until"] == 0 else "tomorrow" if item["days_until"] == 1 else f"in {item['days_until']} days"
        lines.append(f"  {item['date']} ({when}) {item.get('time', '')}: {item['title']}"
                     + ("" if item["source"] != "pattern" else " · usual pattern, check bls.gov"))
    for kind, label in (("fed", "Fed decisions"), ("jobs", "Jobs reports")):
        rows = value["effects"].get(kind) or []
        if rows:
            lines.append(f"On past {label.lower()}: " + "; ".join(
                f"{row['symbol']} moved {row['event_move_pct']}% over two days vs {row['usual_move_pct']}% usually ({row['ratio']}×, {row['events']} events)"
                for row in rows[:4]) + ".")
    lines += [f"  Not available: {gap}" for gap in value["gaps"]]
    lines.append(value["basis"])
    return lines
