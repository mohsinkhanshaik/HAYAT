"""Smarter discovery (live network stage 3): learn which searches pay off, aim at gaps, read open-data portals, trial first.

The scout in discovery.py found sources with web searches aimed at countries without cameras.
This adds four things:

* a strategy log: every way of searching (a web search for a country or a category, a CKAN,
  Socrata or ArcGIS portal search) records what it yielded: candidates, catalogs and cameras.
  The next search favours what has worked (epsilon-greedy, so new ways still get tried) and
  rests a combination that found nothing for two weeks;
* category gaps: weather, volcano, coast, mountain and city cameras are searched for when
  HAYAT has few of them, not only countries with none;
* open-data portals: dataset searches on government CKAN portals, Socrata's public catalog and
  ArcGIS Online, which list camera datasets that web search rarely surfaces;
* an isolated trial: before a found catalog goes live or is proposed, a few of its own images or
  streams are fetched and checked (a real JPEG/PNG, a real HLS playlist). A catalog whose media
  does not answer is recorded as failing its trial and never activated.

Everything still goes through HAYAT's public fetcher: robots.txt, public addresses only, no
keys from URLs, the scout's hourly request budget.
"""
from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Kinds of camera, the words people publish them under, and words that mark one in a title.
CATEGORIES = {
    "traffic": (("traffic cameras", "road webcams", "highway cameras"), ("traffic", "road", "highway", "i-", "route", "motorway")),
    "weather": (("weather webcams", "road weather cameras", "weather station cameras"), ("weather", "rwis", "meteo", "snow")),
    "volcano": (("volcano webcams", "volcano observatory cameras"), ("volcan", "crater", "ashcam", "avo")),
    "coast": (("beach webcams", "harbour webcam", "port cameras", "coastal cameras"), ("beach", "harbour", "harbor", "port", "coast", "pier")),
    "mountain": (("mountain webcams", "avalanche cameras", "ski area webcam"), ("mountain", "pass", "summit", "avalanche", "peak")),
    "city": (("city webcams", "public square webcam", "city live camera"), ("square", "city", "plaza", "downtown", "street")),
}
#: Government open-data portals that run CKAN and allow automated searches (checked 2026-10-11 through
#: HAYAT's fetcher). Left out because their robots.txt refuses it: the UK, Australia, Ireland, Germany, British
#: Columbia, Ontario, Alberta and Mexico; Switzerland blocks bots (403); Brazil needs an account (401); the US
#: (catalog.data.gov) and Austria moved their APIs (404). The key's first word is the country, for coverage gaps.
CKAN_PORTALS = {
    "Canada": "https://open.canada.ca/data/api/3/action/package_search",
    "Canada (Toronto)": "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_search",
    "Japan": "https://www.data.go.jp/data/api/3/action/package_search",
    "Netherlands": "https://data.overheid.nl/data/api/3/action/package_search",
    "Israel": "https://data.gov.il/api/3/action/package_search",
}
#: Countries the category searches rotate through (web search, any domain).
CATEGORY_REGIONS = ("United States", "Canada", "United Kingdom", "Australia", "Japan", "New Zealand", "Norway", "Spain", "Italy",
                    "Chile", "Germany", "France", "India", "Brazil", "South Africa")
SOCRATA = "https://api.us.socrata.com/api/catalog/v1"
ARCGIS = "https://www.arcgis.com/sharing/rest/search"
KINDS = ("web", "category", "ckan", "socrata", "arcgis")
DATA_FORMATS = {"geojson", "json", "api", "esri rest", "arcgis geoservices rest api", "csv", "kml", "xml", "datex"}
REST_DAYS = 14
EXPLORE = .25


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StrategyLog:
    """What each way of searching has yielded, kept beside the scout's sources."""

    def __init__(self, store: Any) -> None:
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS strategies (id TEXT PRIMARY KEY, kind TEXT NOT NULL, query TEXT NOT NULL,
                    runs INTEGER NOT NULL DEFAULT 0, candidates INTEGER NOT NULL DEFAULT 0, catalogs INTEGER NOT NULL DEFAULT 0,
                    cameras INTEGER NOT NULL DEFAULT 0, last_run TEXT, last_yield TEXT);
                CREATE TABLE IF NOT EXISTS strategy_sources (url TEXT PRIMARY KEY, strategy TEXT NOT NULL);
            """)

    def record(self, strategy: str, kind: str, query: str, urls: list[str]) -> None:
        with self.store.db() as db:
            db.execute("INSERT INTO strategies (id, kind, query) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING", (strategy, kind, query[:300]))
            db.execute("UPDATE strategies SET runs = runs + 1, candidates = candidates + ?, last_run = ?, last_yield = "
                       "CASE WHEN ? > 0 THEN ? ELSE last_yield END WHERE id = ?", (len(urls), now(), len(urls), now(), strategy))
            db.executemany("INSERT OR IGNORE INTO strategy_sources (url, strategy) VALUES (?,?)", [(url, strategy) for url in urls])

    def credit(self, url: str, cameras: int) -> None:
        """A source this strategy found became a working catalog."""
        with self.store.db() as db:
            row = db.execute("SELECT strategy FROM strategy_sources WHERE url = ?", (url,)).fetchone()
            if row:
                db.execute("UPDATE strategies SET catalogs = catalogs + 1, cameras = cameras + ?, last_yield = ? WHERE id = ?",
                           (int(cameras), now(), row[0]))

    def rested(self, strategy: str) -> bool:
        """Run before, found nothing, and not long ago: give it a rest."""
        with self.store.db() as db:
            row = db.execute("SELECT runs, candidates, last_run FROM strategies WHERE id = ?", (strategy,)).fetchone()
        if not row or not row[0] or row[1]:
            return False
        try:
            return datetime.fromisoformat(row[2]) > datetime.now(UTC) - timedelta(days=REST_DAYS)
        except (TypeError, ValueError):
            return False

    def kind_scores(self) -> dict[str, float]:
        """Smoothed yield per kind of search: catalogs count most, candidates a little."""
        scores = {kind: 1.0 / 2 for kind in KINDS}
        with self.store.db() as db:
            for kind, runs, candidates, catalogs in db.execute(
                    "SELECT kind, SUM(runs), SUM(candidates), SUM(catalogs) FROM strategies GROUP BY kind"):
                scores[kind] = (catalogs * 10 + candidates * .5 + 1) / ((runs or 0) + 2)
        return scores

    def choose(self, rng: random.Random, available: tuple[str, ...] = KINDS) -> str:
        if rng.random() < EXPLORE:
            return rng.choice(available)
        scores = self.kind_scores()
        return max(available, key=lambda kind: (scores.get(kind, .5), rng.random()))

    def summary(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.store.db() as db:
            rows = db.execute("SELECT id, kind, query, runs, candidates, catalogs, cameras, last_run FROM strategies "
                              "ORDER BY cameras DESC, catalogs DESC, candidates DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("id", "kind", "query", "runs", "candidates", "catalogs", "cameras", "last_run"), row)) for row in rows]


def category_counts(manager: Any) -> dict[str, int]:
    """Cameras per kind, guessed from titles, providers and feed names (a camera can count as more than one kind)."""
    counts = {category: 0 for category in CATEGORIES}
    if manager is None:
        return counts
    try:
        _sources, rows = manager._matching("", "", None, False, "cameras")
    except Exception:
        log_swallowed(_log, "counting cameras by kind")
        return counts
    for row in rows:
        text = " ".join(str(value).lower() for value in (row.get("title"), row.get("feed"), (row.get("details") or {}).get("provider")))
        for category, (_phrases, marks) in CATEGORIES.items():
            if any(mark in text for mark in marks):
                counts[category] += 1
    return counts


def portal_query(category: str) -> str:
    return {"traffic": "traffic camera", "weather": "webcam weather", "volcano": "volcano webcam", "coast": "webcam beach harbour",
            "mountain": "webcam mountain", "city": "webcam"}[category]


def ckan_candidates(fetcher: Any, portal: str, text: str) -> list[tuple[str, str]]:
    data = fetcher.get_json(f"{portal}?q={quote(text)}&rows=20")
    found = []
    for package in ((data or {}).get("result") or {}).get("results") or []:
        title = str(package.get("title") or "")[:200]
        for resource in package.get("resources") or []:
            url = str(resource.get("url") or "")
            fmt = str(resource.get("format") or "").strip().lower()
            if url.startswith("https://") and (fmt in DATA_FORMATS or "featureserver" in url.lower() or url.lower().endswith((".json", ".geojson"))):
                found.append((url, f"{title} ({fmt or 'data'})"))
    return found


def socrata_candidates(fetcher: Any, text: str) -> list[tuple[str, str]]:
    data = fetcher.get_json(f"{SOCRATA}?q={quote(text)}&only=dataset&limit=20")
    found = []
    for item in (data or {}).get("results") or []:
        resource, metadata = item.get("resource") or {}, item.get("metadata") or {}
        domain, identifier = metadata.get("domain"), resource.get("id")
        if domain and identifier:
            found.append((f"https://{domain}/resource/{identifier}.json?$limit=5000", str(resource.get("name") or "")[:200]))
    return found


def arcgis_candidates(fetcher: Any, text: str) -> list[tuple[str, str]]:
    query = quote(f'({text}) AND type:"Feature Service"')
    data = fetcher.get_json(f"{ARCGIS}?q={query}&num=20&f=json")
    found = []
    for item in (data or {}).get("results") or []:
        url = str(item.get("url") or "")
        if url.startswith("https://") and "featureserver" in url.lower():
            found.append((url.rstrip("/") + "/0/query?where=1%3D1&outFields=*&f=geojson&resultRecordCount=2000",
                          str(item.get("title") or "")[:200]))
    return found


IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG")


def trial(fetcher: Any, data: Any, mapping: dict[str, Any], spend: Callable[[int], bool], samples: int = 3) -> dict[str, Any]:
    """Fetch a few of the catalog's own images or streams and check they are real. Run before any activation."""
    from app.intelligence.discovery import read_field, records_at
    records = records_at(data, mapping.get("records_path", ""))
    media = []
    for record in records:
        url = read_field(record, mapping.get("image")) or read_field(record, mapping.get("video"))
        if isinstance(url, str) and url.startswith("https://"):
            media.append(url)
    if not media:
        return {"sampled": 0, "working": 0, "passed": False, "detail": "no media addresses to try", "at": now()}
    chosen = random.Random(len(media)).sample(media, min(samples, len(media)))
    if not spend(len(chosen)):
        return {"sampled": 0, "working": 0, "passed": False, "detail": "budget spent; trial waits", "at": now(), "waiting": True}
    working, problems = 0, []
    for url in chosen:
        try:
            resource = fetcher.fetch(url, 3 * 1024 * 1024, None, timeout=12)
            body = bytes(resource.content[:64])
            if body.startswith(IMAGE_MAGIC) or body.lstrip().startswith(b"#EXTM3U"):
                working += 1
            else:
                problems.append(f"{urlsplit(url).hostname}: not an image or playlist")
        except Exception as error:  # a dead camera is the trial's answer, not an error
            problems.append(f"{urlsplit(url).hostname}: {type(error).__name__}")
    needed = 1 if len(chosen) == 1 else 2
    return {"sampled": len(chosen), "working": working, "passed": working >= needed, "detail": "; ".join(problems)[:300], "at": now()}


class SmartDiscovery:
    """Picks how to search next and runs it, for SourceScout.discover."""

    def __init__(self, scout: Any, rng: random.Random | None = None) -> None:
        self.scout, self.log, self.rng = scout, StrategyLog(scout.store), rng or random.Random()

    def plan(self) -> tuple[str, str, str, Any]:
        """(strategy id, kind, description, run) for the next search."""
        for _attempt in range(6):
            kind = self.log.choose(self.rng)
            coverage = self.scout.coverage().get("countries", {})
            if kind == "web":
                query, domains = self.scout.query()
                return (f"web:{query}", kind, query, lambda query=query, domains=domains: self.scout.web_search(query, domains))
            counts = category_counts(self.scout.manager)
            category = min(counts, key=lambda name: (counts[name], self.rng.random()))
            if kind == "category":
                region = self.rng.choice(CATEGORY_REGIONS)
                query = f"{region} {self.rng.choice(CATEGORIES[category][0])} open data"
                strategy = f"category:{category}:{region}"
                if not self.log.rested(strategy):
                    return strategy, kind, query, lambda query=query: self.scout.web_search(query, None)
                continue
            text = portal_query(category)
            if kind == "ckan":
                gaps = [name for name in CKAN_PORTALS if not coverage.get(name.split(" (")[0])] or list(CKAN_PORTALS)
                country = self.rng.choice(gaps)
                strategy = f"ckan:{country}:{category}"
                if not self.log.rested(strategy):
                    return (strategy, kind, f"{country} open-data portal: {text}",
                            lambda country=country, text=text: ckan_candidates(self.scout.fetcher, CKAN_PORTALS[country], text))
                continue
            strategy = f"{kind}:{category}"
            if not self.log.rested(strategy):
                finder = socrata_candidates if kind == "socrata" else arcgis_candidates
                return strategy, kind, f"{kind} catalog: {text}", lambda finder=finder, text=text: finder(self.scout.fetcher, text)
        query, domains = self.scout.query()
        return f"web:{query}", "web", query, lambda: self.scout.web_search(query, domains)

    def run(self) -> dict[str, Any]:
        strategy, kind, description, search = self.plan()
        found = search()
        added = []
        for url, title in found:
            if self.scout.consider(url, title, f"{kind}: {description}"):
                added.append(url)
        self.log.record(strategy, kind, description, added)
        return {"ok": True, "strategy": strategy, "kind": kind, "query": description, "found": len(found), "candidates": len(added),
                "detail": ", ".join(added[:5])}
