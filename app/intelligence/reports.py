"""Reports about the world: news, conflict and security reporting, storms, airport disruptions, quakes.

News items are read from public RSS feeds of established outlets and the GDELT news
index. Each is classified by its own words into conflict, attack, emergency or plain
news, and placed with the gazetteer at the precision the text allows. A single report is
never treated as established fact: objects.py merges reports of the same event and
decides CONFIRMED / LIKELY / UNVERIFIED / CONTRADICTED from independent sources.
"""

from __future__ import annotations

import hashlib
import re
from email.utils import parsedate_to_datetime
from typing import Any

from app.intelligence.feeds import event, finite, text, timestamp
from app.intelligence.gazetteer import locate

#: Words that put a report in a security or emergency category, most specific first.
TAXONOMY: tuple[tuple[str, str, str], ...] = (
    ("attack", "airstrike", r"air ?strikes?|bombing raids?"),
    ("attack", "missile strike", r"missiles?(?: strikes?| attacks?| barrage)?|ballistic"),
    ("attack", "drone attack", r"drones? (?:attacks?|strikes?)|kamikaze drones?|shahed"),
    ("attack", "rocket fire", r"rockets? (?:fire|attacks?|barrage)|rockets? fired"),
    ("attack", "explosion", r"explosions?|blasts?|explosives?|car bomb|ied\b|suicide bomb\w*"),
    ("attack", "shelling", r"shelling|artillery"),
    ("attack", "shooting", r"shootings?|gunmen|gunfire|opened fire|massacre"),
    ("attack", "attack", r"terror\w*|attacks?|ambush\w*|assassinat\w*|killed in (?:an? )?(?:strike|raid)"),
    ("conflict", "clashes", r"clashes|clashed|fighting|firefights?|skirmish\w*"),
    ("conflict", "offensive", r"offensive|invasion|incursion|advance[sd]? on|siege|front ?line"),
    ("conflict", "military activity", r"troops|military exercise|naval drills?|warships?|deploy\w*|mobilis\w*|mobiliz\w*"),
    ("conflict", "war", r"\bwar\b|ceasefire|truce|hostilities|militia|insurgen\w*|rebels?|armed groups?|hostages?"),
    ("emergency", "evacuation", r"evacuat\w*|state of emergency|emergency declared"),
    ("emergency", "disaster", r"earthquake|tsunami|floods?|flooding|cyclone|hurricane|typhoon|wildfires?|landslides?|volcan\w*"),
    ("emergency", "accident", r"plane crash|air crash|derail\w*|ferry (?:sank|capsized)|collapse[sd]?|stampede"),
    ("emergency", "humanitarian", r"famine|cholera|outbreak|epidemic|displaced|refugees?|humanitarian|aid convoy"),
)
_TAXONOMY = [(category, kind, re.compile(rf"(?i)\b(?:{pattern})")) for category, kind, pattern in TAXONOMY]
#: Language that disputes a claim; a report saying so contradicts what it disputes.
DENIAL = re.compile(r"(?i)\b(?:denies|denied|false claims?|fake|hoax|debunk\w*|no evidence|fabricated|misinformation|disinformation)\b")

#: Outlets and agencies: authority (0-1) and a plain note on ownership or bias risk.
OUTLETS: dict[str, tuple[float, str]] = {
    "news.un.org": (0.9, "United Nations news service"), "bbc.co.uk": (0.8, "public broadcaster (UK)"),
    "bbc.com": (0.8, "public broadcaster (UK)"), "aljazeera.com": (0.75, "state-funded broadcaster (Qatar)"),
    "reuters.com": (0.85, "news agency"), "apnews.com": (0.85, "news agency"), "afp.com": (0.85, "news agency"),
    "theguardian.com": (0.75, "newspaper (UK)"), "nytimes.com": (0.75, "newspaper (US)"), "france24.com": (0.75, "state-funded broadcaster (France)"),
    "dw.com": (0.75, "state-funded broadcaster (Germany)"), "rt.com": (0.3, "Russian state media; high propaganda risk"),
    "tass.com": (0.35, "Russian state news agency"), "cgtn.com": (0.35, "Chinese state media"), "presstv.ir": (0.3, "Iranian state media"),
}


def outlet(url: str) -> tuple[str, float, str]:
    host = re.sub(r"^www\.", "", (re.match(r"https?://([^/]+)", url or "") or [None, ""])[1].lower())
    for domain, (authority, note) in OUTLETS.items():
        if host == domain or host.endswith("." + domain):
            return domain, authority, note
    return host, 0.5, "outlet not rated; treated as moderate"


def classify(words: str) -> tuple[str, str]:
    for category, kind, pattern in _TAXONOMY:
        if pattern.search(words):
            return category, kind
    return "news", "news report"


def parse_rss(content: bytes) -> list[dict[str, Any]]:
    """Items of an RSS 2.0 feed. Document types and entities are refused before parsing."""
    from app.security import xml as ElementTree

    root = ElementTree.fromstring(content)
    items = []
    for item in root.iter("item"):
        values = {child.tag.split("}")[-1]: (child.text or "").strip() for child in item}
        items.append(values)
    return items[:500]


def _iso(value: str) -> str | None:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError):
        return timestamp(value)


def news_item(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    title = text(item.get("title"), 400)
    summary = re.sub(r"<[^>]+>", " ", text(item.get("description"), 1500))
    link = text(item.get("link") or item.get("url"), 600)
    if not title or not link:
        raise ValueError("A news item needs a title and a link.")
    words = f"{title}. {summary}"
    category, kind = classify(words)
    place = locate(title) or locate(summary)
    domain, authority, note = outlet(link)
    identifier = hashlib.sha256(link.encode()).hexdigest()[:20]
    observed = _iso(item.get("pubDate") or item.get("seendate") or "")
    return event(feed, identifier, title, kind, observed, link,
                 [place["longitude"], place["latitude"]] if place else None,
                 provider=feed.title.split(" · ")[0], outlet=domain, authority=authority, bias_note=note,
                 security_category=category, summary=summary[:600], place=(place or {}).get("place"),
                 country=(place or {}).get("country"), location_precision=(place or {}).get("precision", "unknown"),
                 disputes=bool(DENIAL.search(words)))


def gdelt_items(payload: Any) -> list[dict[str, Any]]:
    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        raise ValueError("GDELT returned no article list.")
    return [{"title": item.get("title"), "link": item.get("url"), "seendate": item.get("seendate"),
             "description": item.get("sourcecountry") or ""} for item in articles]


def storm(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    names = {"HU": "Hurricane", "TS": "Tropical Storm", "TD": "Tropical Depression", "STS": "Subtropical Storm",
             "PTC": "Potential Tropical Cyclone", "PC": "Post-tropical Cyclone"}
    classification = str(item.get("classification") or "")
    wind = finite(item.get("intensity"))
    title = f"{names.get(classification, classification)} {text(item.get('name'), 60)}".strip()
    advisory = (item.get("publicAdvisory") or {}).get("url") or feed.documentation
    return event(feed, item.get("id"), title, "tropical cyclone", timestamp(item.get("lastUpdate")), advisory,
                 [item.get("longitudeNumeric"), item.get("latitudeNumeric")], provider="NOAA National Hurricane Center",
                 classification=classification, wind_kt=wind, pressure_mb=finite(item.get("pressure")),
                 moving_toward_deg=item.get("movementDir"), moving_kt=item.get("movementSpeed"))


def airport_disruption(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    code = text(item.get("code"), 8)
    airport = item.get("airport") or {}
    title = f"{item.get('type', 'Disruption')} at {airport.get('name') or code}"
    point = [airport.get("longitude"), airport.get("latitude")] if airport else None
    return event(feed, f"{item.get('type', '')}:{code}", title, str(item.get("type", "disruption")).lower(), None,
                 "https://nasstatus.faa.gov/", point, provider="FAA Air Traffic Control System Command Center",
                 airport_code=code, reason=text(item.get("reason"), 500), starts=text(item.get("start"), 60),
                 ends=text(item.get("end"), 60), country="United States")


def faa_records(content: bytes, codes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Closures, ground stops, ground delays and arrival/departure delays from the FAA status XML."""
    from app.security import xml as ElementTree

    root = ElementTree.fromstring(content)
    found = []
    for delay in root.iter("Delay_type"):
        kind = (delay.findtext("Name") or "Delay").strip().rstrip("s").replace("Program", "").strip() or "Delay"
        for entry in delay.iter():
            code = entry.findtext("ARPT")
            if not code or entry.tag in ("Delay_type",):
                continue
            reason = entry.findtext("Reason") or ""
            found.append({"type": kind, "code": code.strip(), "reason": reason.strip(),
                          "start": entry.findtext("Start") or "", "end": entry.findtext("Reopen") or entry.findtext("End_Time") or "",
                          "airport": codes.get(code.strip().upper()) or codes.get("K" + code.strip().upper())})
    unique = {(item["type"], item["code"]): item for item in found}
    return list(unique.values())


def emsc_quake(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    props = item.get("properties") or {}
    magnitude = finite(props.get("mag"))
    return event(feed, props.get("unid") or item.get("id"), f"M{magnitude:.1f} - {text(props.get('flynn_region'), 120).title()}"
                 if magnitude is not None else text(props.get("flynn_region"), 120), "earthquake",
                 timestamp(props.get("time")), f"https://www.seismicportal.eu/eventdetails.html?unid={props.get('unid')}",
                 (item.get("geometry") or {}).get("coordinates"), provider="EMSC", magnitude=magnitude,
                 depth_km=finite(props.get("depth")), region=props.get("flynn_region"))


def normalize_reports(feed: Any, payload: Any) -> tuple[dict[str, Any], int]:
    if feed.key.startswith("news_"):
        items, builder = payload, news_item
    elif feed.key == "gdelt_security":
        items, builder = gdelt_items(payload), news_item
    elif feed.key == "storms_nhc":
        items, builder = (payload or {}).get("activeStorms"), storm
    elif feed.key == "airports_faa":
        items, builder = payload, airport_disruption
    elif feed.key == "earthquakes_emsc":
        items, builder = (payload or {}).get("features"), emsc_quake
    else:
        raise ValueError("Unknown report feed.")
    if not isinstance(items, list):
        raise ValueError("Provider response must contain a bounded array.")
    rows, rejected = {}, 0
    for item in items[:2000]:
        try:
            row = builder(feed, item)
            rows[row["id"]] = row
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            rejected += 1
    if items and not rows:
        raise ValueError("All provider records failed validation; the prior snapshot is retained.")
    return rows, rejected


REPORT_FEEDS = ("news_bbc_world", "news_aljazeera", "news_un", "gdelt_security", "storms_nhc", "airports_faa", "earthquakes_emsc")
