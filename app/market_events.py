"""World events lined up with the markets they usually touch: industries, funds, shares and crypto.

Each rule says which kind of event in which part of the world bears on which industry, and
why, in one sentence a person can check. For an event HAYAT then reads how those markets
moved since it was reported, next to the whole market (S&P 500) over the same time, so a
fall shared by everything is not mistaken for the event's effect. A move after an event
does not show the event caused it, and every answer says so.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: (south, west, north, east) boxes for the parts of the world the rules mention.
REGIONS = {
    "us_gulf": (24.0, -98.5, 31.5, -80.0), "us_west": (32.0, -125.0, 49.0, -114.0), "us": (24.0, -125.0, 49.5, -66.5),
    "middle_east": (12.0, 32.0, 38.0, 63.0), "taiwan": (21.5, 119.5, 25.5, 122.5), "japan": (30.0, 129.0, 46.0, 146.0),
    "korea": (33.0, 124.5, 38.7, 130.0), "china": (18.0, 73.0, 53.5, 135.0), "east_asia": (18.0, 100.0, 46.0, 146.0),
    "europe": (35.0, -11.0, 71.0, 40.0), "ukraine_russia": (44.0, 22.0, 60.0, 50.0), "chile": (-56.0, -76.0, -17.5, -67.5), "peru": (-18.5, -81.5, -3.0, -68.7),
    "red_sea": (11.0, 32.0, 30.5, 44.0), "north_sea": (51.0, -4.0, 62.0, 9.0), "world": (-90.0, -180.0, 90.0, 180.0),
}

#: Industry -> the tradeable funds or shares that stand for it (tokenized on Kraken) and crypto.
SECTORS = {
    "Energy (oil and gas)": ["XLE", "XOP", "XOM", "CVX"], "Natural gas / LNG": ["LNG"],
    "Semiconductors": ["SMH", "TSM", "NVDA", "ASML"], "South Korea": ["EWY"], "Germany": ["EWG"], "Europe": ["VGK", "FEZ"],
    "Defence": ["ITA"], "Gold (safe haven)": ["GLD"], "Copper": ["COPX"], "Uranium / nuclear": ["URA", "NLR", "CEG"],
    "Agriculture": ["MOO"], "Cybersecurity": ["CRWD", "PANW", "NET"], "Satellites and space": ["ASTS"],
    "US market (benchmark)": ["SPY"], "Crypto": ["BTC", "ETH"], "Crypto companies": ["COIN", "MSTR", "HOOD"],
    "Emerging markets": ["IEMG"],
}
CRYPTO = {"BTC", "ETH", "SOL"}

RULES: tuple[dict[str, Any], ...] = (
    {"id": "gulf-storm", "categories": {"severe_weather", "floods"}, "regions": ["us_gulf"],
     "words": r"hurricane|tropical storm|storm surge|flood", "sectors": ["Energy (oil and gas)", "Natural gas / LNG"],
     "why": "The US Gulf Coast holds much of US refining and LNG export capacity; storms there can shut plants and platforms."},
    {"id": "asia-quake-chips", "categories": {"earthquakes", "volcanoes"}, "regions": ["taiwan", "japan", "korea"],
     "min_magnitude": 6.0, "sectors": ["Semiconductors", "South Korea"],
     "why": "Taiwan, Japan and Korea make most of the world's advanced chips and chip materials; a strong quake can halt fabs."},
    {"id": "middle-east-conflict", "categories": {"conflict", "attack", "emergency"}, "regions": ["middle_east", "red_sea"],
     "words": r"wars?|strikes?|airstrikes?|missiles?|drones?|bomb(?:s|ing)?|shelling|troops|invasion|military|offensive|houthis?|iran|tankers?|strait|oil|refinery|pipeline|attacks? on (?:ships?|shipping|tankers?|airports?|infrastructure)|airport attack",
     "sectors": ["Energy (oil and gas)", "Gold (safe haven)", "Defence"],
     "why": "The region ships a large share of the world's oil; conflict there tends to lift oil, gold and defence shares."},
    {"id": "europe-war", "categories": {"conflict", "attack"}, "regions": ["ukraine_russia", "europe"],
     "words": r"wars?|strikes?|airstrikes?|missiles?|drones?|bomb(?:s|ing)?|shelling|troops|invasion|military|offensive|houthis?|iran|tankers?|strait|oil|refinery|pipeline|attacks? on (?:ships?|shipping|tankers?|airports?|infrastructure)|airport attack",
     "sectors": ["Defence", "Natural gas / LNG", "Europe", "Germany"],
     "why": "War in Europe affects European gas supply, defence spending and European shares."},
    {"id": "copper-supply", "categories": {"earthquakes", "conflict", "emergency", "floods"}, "regions": ["chile", "peru"],
     "min_magnitude": 6.0, "sectors": ["Copper"], "why": "Chile and Peru mine about a third of the world's copper; disruption there tightens supply."},
    # A published, exploited flaw is routine (CISA lists hundreds); a breach, ransomware or outage is news.
    {"id": "cyber", "categories": {"cyber", "news"}, "regions": ["world"], "sectors": ["Cybersecurity"],
     "words": r"ransomware|data breach|breached|cyber ?attack|outage|zero-day|supply[- ]chain attack|wiper",
     "why": "Large attacks and new exploited flaws raise demand for security products."},
    {"id": "crypto-incident", "categories": {"cyber", "news"}, "regions": ["world"],
     "words": r"crypto|bitcoin|exchange hack|stablecoin|defi|blockchain", "sectors": ["Crypto", "Crypto companies"],
     "why": "Exchange hacks and crypto news move coin prices and crypto-linked shares."},
    {"id": "space-weather", "categories": {"space_weather"}, "regions": ["world"], "words": r"G[3-5]|severe|extreme|X\d",
     "sectors": ["Satellites and space"], "why": "Strong geomagnetic storms and flares can disturb satellites and radio links."},
    {"id": "nuclear", "categories": {"emergency", "news", "attack"}, "regions": ["world"], "words": r"nuclear|reactor|uranium",
     "sectors": ["Uranium / nuclear"], "why": "Nuclear incidents and policy news move uranium and nuclear-power shares."},
    {"id": "farm-weather", "categories": {"floods", "severe_weather", "wildfires"}, "regions": ["us", "europe", "china"],
     "words": r"drought|flood|heat|frost|crop", "sectors": ["Agriculture"],
     "why": "Floods, drought and heat in big farming regions affect crops and agricultural companies."},
    {"id": "west-fire-utilities", "categories": {"wildfires"}, "regions": ["us_west"], "min_acres": 10000,
     "sectors": ["US market (benchmark)"], "why": "Large western wildfires matter to utilities and insurers; HAYAT has no tradeable fund for "
     "those, so only the market benchmark is shown."},
)


def in_region(latitude: Any, longitude: Any, region: str) -> bool:
    if latitude is None or longitude is None:
        return region == "world"
    south, west, north, east = REGIONS[region]
    return south <= float(latitude) <= north and west <= float(longitude) <= east


def magnitude(row: dict[str, Any]) -> float | None:
    details = row.get("details") or {}
    for value in (details.get("magnitude"), details.get("mag")):
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    match = re.search(r"\bM\s?(\d+(?:\.\d)?)", str(row.get("title") or ""))
    return float(match[1]) if match else None


def matching_rules(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The rules an event meets: its category, where it is, and any words or size the rule needs."""
    category = row.get("category") or ""
    text = " ".join(str(row.get(key) or "") for key in ("title", "headline", "subtitle")) + " " + str((row.get("details") or {}).get("description") or "")
    found = []
    for rule in RULES:
        if category not in rule["categories"]:
            continue
        if not any(in_region(row.get("latitude"), row.get("longitude"), region) for region in rule["regions"]):
            continue
        if rule.get("words") and not re.search(rf"\b(?:{rule['words']})\b", text, re.I):
            continue
        if rule.get("min_magnitude") and category == "earthquakes" and (magnitude(row) or 0) < rule["min_magnitude"]:
            continue
        if rule.get("min_acres"):
            try:
                if float((row.get("details") or {}).get("acres") or 0) < rule["min_acres"]:
                    continue
            except (TypeError, ValueError):
                continue
        found.append(rule)
    return found


def _since(markets: Any, symbol: str, since: float) -> dict[str, Any]:
    """Price now and at `since` (the first hourly candle at or after it), for a share or a coin."""
    asset = "crypto" if symbol in CRYPTO else "stock"
    name, kind, candles = markets.candles(symbol, 60, asset)
    if not candles:
        raise ValueError(f"No candles for {symbol}.")
    before = next((candle for candle in candles if candle["time"] >= since - 3600), candles[0])
    now = candles[-1]
    traded = sum(candle["trades"] for candle in candles if candle["time"] >= before["time"])
    return {"symbol": name, "asset": kind, "then": before["open"], "now": now["close"],
            "change_pct": round((now["close"] - before["open"]) / before["open"] * 100, 2) if before["open"] else None,
            "from": datetime.fromtimestamp(before["time"], UTC).isoformat(timespec="minutes"), "trades": traded,
            "thin": traded < 5, "covers_event": before["time"] <= since + 3600}


def event_impact(manager: Any, markets: Any, event_id: str) -> dict[str, Any]:
    """The markets an event usually bears on, and how they moved since it was reported, next to the whole market."""
    record = manager.event_record(event_id)
    row = record["event"]
    rules = matching_rules(row)
    observed = row.get("observed_at") or row.get("retrieved_at")
    try:
        since = datetime.fromisoformat(str(observed).replace("Z", "+00:00")).timestamp()
    except ValueError:
        since = datetime.now(UTC).timestamp() - 86400
    moves, gaps = [], []
    symbols = list(dict.fromkeys([symbol for rule in rules for sector in rule["sectors"] for symbol in SECTORS[sector]] + ["SPY"]))
    for symbol in symbols:
        try:
            moves.append(_since(markets, symbol, since))
        except Exception as error:  # one missing market is a gap in the answer, not a failure of it
            gaps.append(f"{symbol}: {str(error)[:100]}")
            log_swallowed(_log, f"reading {symbol} for an event")
    benchmark = next((move for move in moves if move["symbol"] == "SPY"), None)
    quiet = bool(moves) and all(move["thin"] for move in moves if move["asset"] == "stock")
    for move in moves:
        if benchmark and move is not benchmark and move["change_pct"] is not None and benchmark["change_pct"] is not None:
            move["vs_market_pct"] = round(move["change_pct"] - benchmark["change_pct"], 2)
    return {"markets": True, "event_impact": True, "event": {"id": row["id"], "title": row.get("headline") or row.get("title"),
            "category": row.get("category"), "observed_at": observed, "latitude": row.get("latitude"), "longitude": row.get("longitude")},
            "rules": [{"id": rule["id"], "sectors": rule["sectors"], "why": rule["why"]} for rule in rules], "moves": moves, "gaps": gaps,
            "quiet": quiet, "note": ("Little or no trading in these shares since the event (US markets are closed at weekends and "
                                     "overnight, and tokenized shares trade thinly then): the changes are not yet a reaction.") if quiet else "",
            "source": "Kraken (tokenized shares and crypto), hourly candles",
            "basis": "Which industries an event usually touches is a written rule with its reason. A move after an event does not show "
                     "the event caused it; compare it with the whole market (S&P 500) over the same time. Tokenized shares can drift from "
                     "exchange prices; 'thin' means few trades."}


def events_and_markets(manager: Any, markets: Any, hours: float = 48, limit: int = 12) -> dict[str, Any]:
    """Today's significant events grouped by the industries they bear on, with each industry's move today."""
    # One read per kind of event the rules know: an all-kinds read is filled by road events first (thousands of them),
    # and the conflicts and quakes the rules are about never arrived. Severity is not a filter: the rules ask for wording.
    rows = []
    for category in sorted({category for rule in RULES for category in rule["categories"]}):
        try:
            rows += manager.dashboard(category=category, since_hours=hours, limit=1000, located_only=False)["events"]
        except ValueError:
            continue  # a category this HAYAT has no feed for
    rank = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda row: str(row.get("observed_at") or ""), reverse=True)  # newest first,
    rows.sort(key=lambda row: rank.get(row.get("severity"), 3))  # then the most severe first (a stable sort keeps both)
    by_sector: dict[str, dict[str, Any]] = {}
    for row in rows:
        for rule in matching_rules(row):
            for sector in rule["sectors"]:
                entry = by_sector.setdefault(sector, {"sector": sector, "why": rule["why"], "events": []})
                if len(entry["events"]) < 5 and all(item["id"] != row["id"] for item in entry["events"]):
                    entry["events"].append({"id": row["id"], "title": row.get("headline") or row.get("title"),
                                            "severity": row.get("severity"), "observed_at": row.get("observed_at")})
    tickers: dict[str, dict[str, Any]] = {}
    try:
        tickers.update({row["symbol"]: row for row in markets.stocks(limit=1000)["rows"]})
    except Exception:
        log_swallowed(_log, "reading tokenized share prices")
    try:
        tickers.update({row["symbol"]: row for row in markets.overview("USD", limit=1000)["rows"] if row["symbol"] in CRYPTO})
    except Exception:
        log_swallowed(_log, "reading crypto prices")
    sectors = []
    for entry in by_sector.values():
        entry["markets"] = [{"symbol": symbol, "price": tickers[symbol]["price"], "change_today_pct": tickers[symbol]["change_today_pct"],
                             "thin": tickers[symbol].get("thin", False)} for symbol in SECTORS[entry["sector"]] if symbol in tickers]
        sectors.append(entry)
    sectors.sort(key=lambda entry: (-sum(item["severity"] == "high" for item in entry["events"]), -len(entry["events"])))
    spy = tickers.get("SPY")
    return {"markets": True, "events_markets": True, "hours": hours, "sectors": sectors[:limit],
            "benchmark": {"symbol": "SPY", "change_today_pct": spy["change_today_pct"]} if spy else None,
            "events_considered": len(rows), "source": "HAYAT's public event feeds + Kraken prices",
            "basis": "Events are matched to industries by written rules with reasons. Today's moves are since 00:00 UTC and are not "
                     "caused by the events shown unless something else shows it."}
