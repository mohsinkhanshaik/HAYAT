"""World trade chokepoints: the narrow sea lanes most trade passes through, with what is happening near each.

For each chokepoint: what usually passes (in plain words, from public shipping and energy
references such as the US EIA's "World Oil Transit Chokepoints"), the industries and funds it
links to (the market_events sectors), and the events HAYAT's public feeds report within a few
hundred kilometres in the last days. Status is a reading of those reports, not of ship traffic:
HAYAT has no keyless live shipping-volume source, and says so.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

#: (key, name, latitude, longitude, what passes, sectors)
CHOKEPOINTS = (
    ("hormuz", "Strait of Hormuz", 26.57, 56.25, "Crude oil and LNG from the Persian Gulf (Saudi Arabia, Iraq, UAE, Kuwait, Qatar, Iran)",
     ("Energy (oil and gas)", "Natural gas / LNG")),
    ("malacca", "Strait of Malacca", 2.5, 101.3, "The main lane between the Indian Ocean and East Asia: oil to China, Japan and Korea, and "
     "Asia–Europe containers", ("Energy (oil and gas)", "Emerging markets", "Semiconductors")),
    ("suez", "Suez Canal", 30.6, 32.33, "Europe–Asia container ships, oil and LNG", ("Europe", "Energy (oil and gas)")),
    ("bab", "Bab-el-Mandeb", 12.6, 43.3, "The Red Sea's southern gate, on the Suez route between Europe and Asia",
     ("Energy (oil and gas)", "Europe")),
    ("panama", "Panama Canal", 9.1, -79.7, "US East Coast–Asia trade, US grain and LNG exports", ("Agriculture", "Natural gas / LNG")),
    ("taiwan", "Taiwan Strait", 24.5, 119.5, "East Asian container traffic past the world's main chip-making island", ("Semiconductors",)),
    ("bosporus", "Turkish Straits (Bosporus)", 41.1, 29.06, "Black Sea grain and oil (Ukraine, Russia, Kazakhstan)",
     ("Agriculture", "Energy (oil and gas)", "Europe")),
    ("gibraltar", "Strait of Gibraltar", 35.95, -5.6, "The Atlantic–Mediterranean gate", ("Europe",)),
    ("danish", "Danish Straits", 55.7, 11.0, "Baltic Sea oil and goods (Russia, Scandinavia, Germany's Baltic ports)",
     ("Energy (oil and gas)", "Europe")),
    ("cape", "Cape of Good Hope", -34.36, 18.47, "The long way round Africa when the Red Sea is unsafe", ("Energy (oil and gas)",)),
    ("dover", "Strait of Dover", 51.0, 1.5, "Europe's busiest shipping lane, into the North Sea ports", ("Europe",)),
)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1.0, a)))


def chokepoints(manager: Any, markets: Any = None, radius_km: float = 350, hours: float = 72) -> dict[str, Any]:
    from app.market_events import SECTORS
    moves: dict[str, Any] = {}
    if markets is not None:
        try:
            moves = {row["symbol"]: row for row in markets.stocks(limit=1000)["rows"]}
        except Exception:  # the events stand without today's moves
            moves = {}
    rows = []
    for key, name, latitude, longitude, passes, sectors in CHOKEPOINTS:
        span_lat = radius_km / 111.0
        span_lon = radius_km / (111.0 * max(.2, math.cos(math.radians(latitude))))
        bbox = [max(-180, longitude - span_lon), max(-90, latitude - span_lat), min(180, longitude + span_lon), min(90, latitude + span_lat)]
        events = []
        if manager is not None:
            found = manager.dashboard(bbox=bbox, located_only=True, since_hours=hours, limit=200)["events"]
            for event in found:
                if event.get("category") == "cameras" or str(event.get("feed", "")).startswith(("cameras", "livestreams")):
                    continue
                away = distance_km(latitude, longitude, event["latitude"], event["longitude"])
                if away <= radius_km:
                    events.append({"id": event.get("id"), "title": event.get("title", ""), "severity": event.get("severity", ""),
                                   "category": event.get("category", ""), "observed_at": event.get("observed_at", ""),
                                   "distance_km": round(away)})
        events.sort(key=lambda item: ({"high": 0, "medium": 1}.get(item["severity"], 2), item["distance_km"]))
        high = sum(event["severity"] == "high" for event in events)
        status = "alert" if high else "watch" if events else "calm"
        markets_linked = []
        for sector in sectors:
            for symbol in SECTORS.get(sector, []):
                row = moves.get(symbol)
                markets_linked.append({"symbol": symbol, "sector": sector,
                                       "change_today_pct": row.get("change_today_pct") if row else None})
        rows.append({"key": key, "name": name, "latitude": latitude, "longitude": longitude, "passes": passes, "sectors": list(sectors),
                     "status": status, "events": events[:6], "event_count": len(events), "high": high, "markets": markets_linked})
    order = {"alert": 0, "watch": 1, "calm": 2}
    rows.sort(key=lambda row: (order[row["status"]], -row["event_count"]))
    return {"trade_view": True, "chokepoints": rows, "radius_km": radius_km, "hours": hours,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "basis": (f"Events HAYAT's public feeds reported within {radius_km:g} km in the last {hours:g} hours, not ship traffic: there "
                      "is no keyless live shipping-volume source. What passes each chokepoint is from public shipping and energy "
                      "references. An event nearby does not mean trade was disrupted.")}


def report_lines(value: dict[str, Any]) -> list[str]:
    def signed(change: Any) -> str:
        return "—" if change is None else f"{change:+.2f}%"
    lines = [f"World trade chokepoints · events within {value['radius_km']:g} km, last {value['hours']:g} h:"]
    for row in value["chokepoints"]:
        mark = {"alert": "▲ alert", "watch": "● watch", "calm": "○ calm"}[row["status"]]
        lines.append(f"  {row['name']}: {mark} · {row['event_count']} event{'s' if row['event_count'] != 1 else ''}"
                     + (f" (e.g. {row['events'][0]['title'][:70]})" if row["events"] else ""))
        if row["status"] != "calm":
            lines.append("    linked: " + ", ".join(f"{item['symbol']} {signed(item['change_today_pct'])}" for item in row["markets"][:5]))
    lines.append(value["basis"])
    return lines
