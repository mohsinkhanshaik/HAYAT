"""Finding new public camera and data sources while HAYAT is idle.

Every camera network so far was found by a person searching, reading a
publisher's page and writing an adapter. This lets HAYAT do the first three
steps itself, in small bounded cycles, using only what publishers make public:

* discover -- web searches for open camera, webcam, weather and map catalogs,
  steered toward the regions HAYAT has no cameras for yet;
* probe -- fetch a candidate politely (robots.txt honoured, public addresses
  only, size and time capped, no logins) and recognise a camera catalog by its
  shape: records with a location and an image or stream address;
* explain access -- a source that answers 401/403, needs a key or sits behind a
  login is recorded with what access it requires. It is never retried around;
* health -- sample cameras of known networks and record whether frames come back;
* MCP -- look up relevant servers in the public MCP registry and suggest them,
  noting any credentials they need. Nothing is installed;
* activate -- a recognised catalog becomes a live camera network. An official
  government or university catalog whose media is on its own domain goes live
  by itself; anything else waits for the owner's "approve source N".

Hard rules, enforced by the fetcher and the checks below rather than by intent:
no private or local addresses (the fetcher refuses them), no authentication,
no key found inside someone else's URL, no directory of unsecured private
cameras, and nothing while the owner is busy, privacy mode is on, the learner
is paused or halted, or this module's own hourly request budget is spent.
"""

from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Never sources: indexes of unsecured private cameras and device search engines.
#: Watching a stranger's unprotected camera is a privacy violation, whatever the purpose.
FORBIDDEN_DOMAINS = frozenset({
    "insecam.org", "insecam.com", "shodan.io", "censys.io", "zoomeye.org", "fofa.info", "opentopia.com",
    "earthcam.net", "lookcam.com", "camhacker.com", "webcamhopper.com", "worldcam.eu",
})
#: Aggregators re-host other publishers' feeds; HAYAT goes to the publisher instead.
AGGREGATORS = frozenset({
    "argosatlas.com", "opencctv.org", "trafficvision.live", "trafficcameras.uk", "livetrafficcam.com",
    "opencam.live", "traffic-cams.com", "easeweather.com", "meteoblue.com", "windy.com", "skylinewebcams.com",
    "webcamtaxi.com", "fllc.net", "milecheckapp.com", "cctv-database.com", "youtube.com", "facebook.com",
    "pocketworld.org", "openwebcamdb.com", "publicapis.io", "webcamgalore.com", "webcams.travel", "camstreamer.com",
    "reddit.com", "wikipedia.org", "github.com", "medium.com", "x.com", "twitter.com",
})
#: Official publishers by domain shape: governments and universities.
OFFICIAL = re.compile(
    r"(?i)(?:\.gov|\.gov\.[a-z]{2}|\.govt\.[a-z]{2}|\.gc\.ca|\.gouv\.[a-z]{2}|\.gob\.[a-z]{2}|\.go\.[a-z]{2}|"
    r"\.gv\.at|\.admin\.ch|\.bund\.de|\.edu|\.edu\.[a-z]{2}|\.ac\.[a-z]{2}|\.int|\.mil)$")
DATA_HINT = re.compile(r"(?i)(?:api|json|geojson|xml|datex|opendata|open-data|dataset|featureserver|mapserver|wfs|"
                       r"cameras?|webcams?|cctv|kamera|camaras?|telecamere|kameras?)")
_KEY_IN_URL = re.compile(r"(?i)[?&](?:api_?key|key|token|access_?token|subscription-key|apikey)=")
_ACCESS_WORDS = re.compile(r"(?i)\b(?:api[ -]?key|apikey|access token|subscription key|register(?:ed)? (?:for|to get)|"
                           r"sign in|log ?in to|developer account|request access|credentials)\b")
_IMAGE = re.compile(r"(?i)^https://[^\s\"']+\.(?:jpe?g|png)(?:\?[^\s\"']*)?$")
_STREAM = re.compile(r"(?i)^https://[^\s\"']+\.m3u8(?:\?[^\s\"']*)?$")

TOPICS = ("traffic cameras open data api json", "road webcams open data", "highway cameras feed geojson",
          "weather webcams open data network", "public webcam network api", "camera locations open data portal",
          "official weather disaster news RSS data feed")
INDIA_REGIONS = ('Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh', 'Goa', 'Gujarat',
                 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka', 'Kerala', 'Madhya Pradesh', 'Maharashtra',
                 'Manipur', 'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim',
                 'Tamil Nadu', 'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
                 'Andaman and Nicobar Islands', 'Chandigarh', 'Dadra and Nagar Haveli and Daman and Diu',
                 'Delhi', 'Jammu and Kashmir', 'Ladakh', 'Lakshadweep', 'Puducherry')
#: MCP registry topics worth suggesting for vision and world awareness.
MCP_REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"
MCP_TOPICS = ("camera", "webcam", "traffic", "weather", "map", "geospatial", "satellite", "earthquake", "news", "flight")
#: Countries and regions searched first when HAYAT has no cameras there.
REGIONS = ("Canada", "Australia", "Germany", "France", "Italy", "Netherlands", "Belgium", "Austria", "Switzerland",
           "Poland", "Czech Republic", "Norway", "Sweden", "Denmark", "Estonia", "Latvia", "Portugal", "Ireland",
           "Scotland", "Wales", "Japan", "South Korea", "Taiwan", "Thailand", "Malaysia", "Philippines", "India",
           "Brazil", "Chile", "Mexico", "Argentina", "Colombia", "South Africa", "Kenya", "Israel", "Turkey",
           "United Arab Emirates", "New Zealand", "Greece", "Croatia", "Slovenia", "Hungary", "Romania")
#: Each region's government domain: half the searches look only there, where open-data catalogs live.
GOVERNMENT_DOMAINS = {
    "Canada": "gc.ca", "Australia": "gov.au", "Germany": "bund.de", "France": "gouv.fr", "Italy": "gov.it",
    "Netherlands": "overheid.nl", "Belgium": "belgium.be", "Austria": "gv.at", "Switzerland": "admin.ch",
    "Poland": "gov.pl", "Czech Republic": "gov.cz", "Norway": "vegvesen.no", "Sweden": "trafikverket.se",
    "Denmark": "vejdirektoratet.dk", "Estonia": "transpordiamet.ee", "Latvia": "lvceli.lv", "Portugal": "gov.pt",
    "Ireland": "gov.ie", "Scotland": "gov.scot", "Wales": "gov.wales", "Japan": "go.jp", "South Korea": "go.kr",
    "Taiwan": "gov.tw", "Thailand": "go.th", "Malaysia": "gov.my", "Philippines": "gov.ph", "India": "gov.in",
    "Brazil": "gov.br", "Chile": "gob.cl", "Mexico": "gob.mx", "Argentina": "gob.ar", "Colombia": "gov.co",
    "South Africa": "gov.za", "Kenya": "go.ke", "Israel": "gov.il", "Turkey": "gov.tr",
    "United Arab Emirates": "gov.ae", "New Zealand": "govt.nz", "Greece": "gov.gr", "Croatia": "gov.hr",
    "Slovenia": "gov.si", "Hungary": "gov.hu", "Romania": "gov.ro", 'Makkah': 'gov.sa',
}


def registered_domain(url_or_host: str) -> str:
    host = (urlsplit(url_or_host).hostname if "://" in url_or_host else url_or_host) or ""
    parts = host.lower().split(".")
    if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "gov", "govt", "go", "ac",
                                                                   "edu", "gob", "gouv", "gv", "or"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


#: Hosting platforms shared by many publishers: "known" is decided per publisher path, not per domain.
SHARED_PLATFORMS = frozenset({"arcgis.com", "arcgisonline.com"})


def shared_prefix(url: str) -> str:
    """'services2.arcgis.com/CcI36Pduqd0OR4W9/arcgis/rest/services/trafficCams' -> host + publisher + service."""
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    service = segments[segments.index("services") + 1] if "services" in segments[:-1] else ""
    return f"{parts.hostname}/{segments[0] if segments else ''}/{service}".lower()


def is_official(url: str) -> bool:
    return bool(OFFICIAL.search(urlsplit(url).hostname or ""))


def refused_domain(url: str) -> str | None:
    """Why a URL is never used, or None."""
    domain = registered_domain(url)
    if domain in FORBIDDEN_DOMAINS:
        return "a directory of unsecured private cameras or a device search engine; never used"
    if domain in AGGREGATORS:
        return "an aggregator or social site; HAYAT goes to the original publisher instead"
    return None


# ------------------------------------------------------------- recognising catalogs
def _records(data: Any) -> tuple[str, list[dict]]:
    """The largest list of objects in a JSON document, and where it was."""
    best: tuple[str, list[dict]] = ("", [])

    def visit(value: Any, path: str, depth: int) -> None:
        nonlocal best
        if depth > 4:
            return
        if isinstance(value, list):
            objects = [item for item in value if isinstance(item, dict)]
            if len(objects) > len(best[1]):
                best = (path, objects)
            if not objects:
                for item in value[:3]:
                    if isinstance(item, list):
                        visit(item, path, depth + 1)
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, (dict, list)):
                    visit(item, f"{path}.{key}" if path else key, depth + 1)

    visit(data, "", 0)
    return best


def _flatten(record: dict, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and depth < 3:
            flat.update(_flatten(value, name + ".", depth + 1))
        elif isinstance(value, list) and value and isinstance(value[0], dict) and depth < 3:
            flat.update(_flatten(value[0], name + "[0].", depth + 1))
        else:
            flat[name] = value
    return flat


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def analyse_catalog(data: Any) -> dict[str, Any] | None:
    """A field mapping when a JSON document is a catalog of located cameras, else None."""
    path, records = _records(data)
    if len(records) < 10:
        return None
    sample = [_flatten(record) for record in records[:200]]
    keys: dict[str, int] = {}
    for flat in sample:
        for key in flat:
            keys[key] = keys.get(key, 0) + 1

    def best(test: Callable[[str, Any], bool], names: tuple[str, ...] = ()) -> str | None:
        scores = []
        for key in keys:
            hits = sum(1 for flat in sample if key in flat and test(key, flat[key]))
            bonus = 1.2 if any(name in key.lower() for name in names) else 1.0
            if hits >= len(sample) * 0.5:
                scores.append((hits * bonus, key))
        return max(scores)[1] if scores else None

    coordinates = best(lambda key, value: isinstance(value, list) and len(value) >= 2
                       and all(_number(item) is not None for item in value[:2]), ("coordinates",))
    lat = lon = None
    if coordinates is None:
        lat = best(lambda key, value: _number(value) is not None and -90 <= _number(value) <= 90
                   and re.search(r"(?i)lat|(^|\.)y$", key) is not None, ("lat",))
        lon = best(lambda key, value: _number(value) is not None and -180 <= _number(value) <= 180
                   and re.search(r"(?i)lon|lng|(^|\.)x$", key) is not None, ("lon", "lng"))
        if not (lat and lon):
            return None
    image = best(lambda key, value: isinstance(value, str) and bool(_IMAGE.match(value)), ("image", "snapshot", "url"))
    video = best(lambda key, value: isinstance(value, str) and bool(_STREAM.match(value)), ("stream", "video", "hls"))
    if not image and not video:
        return None
    title = best(lambda key, value: isinstance(value, str) and 2 < len(value) < 200 and not value.startswith("http"),
                 ("name", "title", "description", "location", "caption"))
    identity = best(lambda key, value: isinstance(value, (str, int)) and not isinstance(value, bool),
                    ("id", "code", "number"))
    media = [flat.get(image) if image else None for flat in sample] + [flat.get(video) if video else None for flat in sample]
    hosts = sorted({urlsplit(url).hostname for url in media if isinstance(url, str) and url.startswith("https://")})
    return {"records_path": path, "coordinates": coordinates, "lat": lat, "lon": lon, "image": image, "video": video,
            "title": title, "id": identity, "count": len(records), "media_hosts": hosts[:20],
            "image_hosts": sorted({urlsplit(flat.get(image)).hostname for flat in sample if image and isinstance(flat.get(image), str)})[:20],
            "video_hosts": sorted({urlsplit(flat.get(video)).hostname for flat in sample if video and isinstance(flat.get(video), str)})[:20]}


def records_at(data: Any, path: str) -> list[dict]:
    value = data
    for part in [part for part in path.split(".") if part]:
        value = value.get(part) if isinstance(value, dict) else None
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def read_field(record: dict, key: str | None) -> Any:
    return _flatten(record).get(key) if key else None


# ------------------------------------------------------------------- store
class DiscoveryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ready = False

    def db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        if not self._ready:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL, found_by TEXT NOT NULL DEFAULT '', found_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'found', reason TEXT NOT NULL DEFAULT '',
                    access TEXT NOT NULL DEFAULT '', mapping TEXT NOT NULL DEFAULT '', checked_at TEXT);
                CREATE TABLE IF NOT EXISTS health (
                    feed TEXT PRIMARY KEY, checked_at TEXT NOT NULL, sampled INTEGER NOT NULL, working INTEGER NOT NULL,
                    detail TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS mcp (
                    name TEXT PRIMARY KEY, description TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
                    access TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL, found_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, job TEXT NOT NULL, detail TEXT NOT NULL);
            """)
            self._ready = True
        return connection

    def preference(self, key: str, default: Any = None) -> Any:
        with self._lock, self.db() as db:
            row = db.execute('SELECT value FROM preferences WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def save_preference(self, key: str, value: Any) -> None:
        with self._lock, self.db() as db:
            db.execute('INSERT INTO preferences VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, json.dumps(value)))

    def add(self, url: str, title: str, found_by: str) -> bool:
        with self._lock, self.db() as db:
            cursor = db.execute("INSERT OR IGNORE INTO sources (url, title, domain, found_by, found_at) VALUES (?,?,?,?,?)",
                                (url[:1000], title[:200], registered_domain(url), found_by[:200], now()))
            return cursor.rowcount > 0

    def update(self, source_id: int, **fields: Any) -> None:
        if not fields:
            return
        names = ", ".join(f"{key}=?" for key in fields)
        with self._lock, self.db() as db:
            db.execute(f"UPDATE sources SET {names}, checked_at=? WHERE id=?", (*fields.values(), now(), source_id))

    def get(self, source_id: int) -> dict[str, Any] | None:
        with self._lock, self.db() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def sources(self, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self.db() as db:
            rows = db.execute("SELECT * FROM sources" + (" WHERE status=?" if status else "") +
                              " ORDER BY id DESC LIMIT ?", ((status, limit) if status else (limit,))).fetchall()
        return [dict(row) for row in rows]

    def next_to_probe(self) -> dict[str, Any] | None:
        with self._lock, self.db() as db:
            cutoff = (datetime.now(UTC) - timedelta(hours=6)).isoformat(timespec='seconds')
            row = db.execute("SELECT * FROM sources WHERE status='found' OR (status='unreachable' AND checked_at<?) "
                             "ORDER BY CASE WHEN status='found' THEN 0 ELSE 1 END, id LIMIT 1", (cutoff,)).fetchone()
        return dict(row) if row else None

    def counts(self) -> dict[str, int]:
        with self._lock, self.db() as db:
            return {row["status"]: row["n"] for row in db.execute("SELECT status, COUNT(*) n FROM sources GROUP BY status")}

    def journal(self, job: str, detail: str) -> None:
        with self._lock, self.db() as db:
            db.execute("INSERT INTO journal (at, job, detail) VALUES (?,?,?)", (now(), job, detail[:500]))

    def recent_journal(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock, self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ----------------------------------------------------------- activation
DISCOVERED_MAPPINGS: dict[str, dict[str, Any]] = {}


def feed_for(source: dict[str, Any]):
    from app.intelligence.feeds import Feed

    mapping = json.loads(source.get('mapping') or '{}')
    rss = mapping.get('format') == 'rss'
    return Feed(f"news_discovered_{source['id']}" if rss else f"discovered_{source['id']}",
                f"{source['domain']} · discovered public news" if rss else f"{source['domain']} · discovered cameras",
                source['url'], 3600, source['url'], format='rss' if rss else 'json', max_bytes=8 * 1024 * 1024)


def register_discovered(source: dict[str, Any], manager: Any = None) -> str:
    """Make an active discovered catalog a camera network: feed, media allowlist and field mapping."""
    from app.intelligence.feeds import BY_KEY
    from app.intelligence.providers import MEDIA_HOSTS

    mapping = json.loads(source["mapping"])
    feed = feed_for(source)
    BY_KEY[feed.key] = feed
    MEDIA_HOSTS[feed.key] = (set(mapping.get("image_hosts") or []), set(mapping.get("video_hosts") or []), set())
    DISCOVERED_MAPPINGS[feed.key] = mapping
    if manager is not None and all(existing.key != feed.key for existing in manager.feeds):
        manager.feeds = tuple(manager.feeds) + (feed,)
    return feed.key


def restore_discovered(path: Path, manager: Any = None) -> list[str]:
    """At start-up: every catalog activated earlier becomes a network again."""
    store = DiscoveryStore(path)
    keys = []
    for source in store.sources("active", limit=500):
        try:
            keys.append(register_discovered(source, manager))
        except Exception:
            log_swallowed(_log, f"restoring discovered source {source.get('id')}", level=logging.WARNING)
    return keys


def discovered_camera(feed: Any, record: dict) -> dict[str, Any] | None:
    """One camera from a discovered catalog, through the same allowlisted builder as every network."""
    from app.intelligence.feeds import text
    from app.intelligence.providers import camera

    mapping = DISCOVERED_MAPPINGS.get(feed.key)
    if not mapping:
        raise ValueError("Unknown discovered catalog.")
    flat = _flatten(record)
    if mapping.get("coordinates"):
        point = flat.get(mapping["coordinates"])
        point = point[:2] if isinstance(point, list) else None
    else:
        point = [flat.get(mapping["lon"]), flat.get(mapping["lat"])]
    image = flat.get(mapping["image"]) if mapping.get("image") else ""
    video = flat.get(mapping["video"]) if mapping.get("video") else ""
    identity = flat.get(mapping["id"]) if mapping.get("id") else None
    title = text(flat.get(mapping["title"])) if mapping.get("title") else ""
    if identity in (None, ""):
        identity = image or video or title
    return camera(feed, identity, title or f"Camera {identity}", point, feed.title.split(" · ")[0], "",
                  image=image if isinstance(image, str) else "", video=video if isinstance(video, str) else "",
                  refresh=300, media_kind="discovered public catalog")


# ------------------------------------------------------------------ scout
class SourceScout:
    """One small job per cycle, only while the learner's gates say the machine is free."""

    def __init__(self, store: DiscoveryStore, *, search: Callable[[str, int], list[Any]], fetcher: Any,
                 manager: Any = None, learner: Any = None, authority: Any = None,
                 requests_per_hour: int = 30, interval_seconds: float = 600.0,
                 priority_regions: tuple[str, ...] | list[str] = ()) -> None:
        self.store, self.search, self.fetcher = store, search, fetcher
        self.manager, self.learner, self.authority = manager, learner, authority
        self.requests_per_hour = requests_per_hour
        self.interval_seconds = interval_seconds
        self._requests: list[float] = []
        self._budget_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._cycle = 0
        self.last: dict[str, Any] = {}
        self.priority_regions = tuple(str(region).strip()[:100] for region in priority_regions if str(region).strip())[:8]

    # ---- gates
    def _spend(self, n: int = 1) -> bool:
        with self._budget_lock:
            cutoff = time.time() - 3600
            self._requests = [stamp for stamp in self._requests if stamp > cutoff]
            if len(self._requests) + n > self.requests_per_hour:
                return False
            self._requests.extend([time.time()] * n)
            return True

    def may_run(self) -> tuple[bool, str]:
        if bool(getattr(self.authority, "privacy_mode", False)):
            return False, "privacy mode is on"
        if self.learner is not None:
            allowed, reason = self.learner.may_run()
            if not allowed:
                return False, reason
        cutoff = time.time() - 3600
        if len([stamp for stamp in self._requests if stamp > cutoff]) >= self.requests_per_hour:
            return False, f"discovery budget spent ({self.requests_per_hour} requests/hour)"
        return True, "ok"

    # ---- jobs
    def cycle(self, job: str = "") -> dict[str, Any]:
        """One bounded job: probe a waiting candidate, else discover, check health or look up MCP servers."""
        self._cycle += 1
        if not job:
            waiting = self.store.next_to_probe()
            job = "probe" if waiting else ("health", "mcp", "discover", "discover")[self._cycle % 4]
        try:
            result = {"probe": self.probe_next, "discover": self.discover, "health": self.health,
                      "mcp": self.mcp}[job]()
        except Exception as exc:
            log_swallowed(_log, f"discovery job {job}", level=logging.WARNING)
            result = {"job": job, "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:300]}
        result.setdefault("job", job)
        self.store.journal(job, json.dumps({key: value for key, value in result.items() if key != "job"},
                                           default=str)[:500])
        self.last = {"at": now(), **result}
        return result

    def query(self) -> tuple[str, list[str] | None]:
        """Search where HAYAT has no cameras yet, first; every other search only on that country's government domain."""
        covered = self.coverage().get("countries", {})
        global_regions = [region for region in REGIONS if region not in self.priority_regions]
        gaps = [region for region in global_regions if not covered.get(region)]
        index = self.store.preference('search_cursor', 0)
        self.store.save_preference('search_cursor', index + 1)
        # Two regional searches, then a global coverage gap. A few temple views
        # must not make HAYAT conclude that an entire country is covered.
        region = (self.priority_regions[(index - index // 3) % len(self.priority_regions)]
                  if self.priority_regions and index % 3 != 2 else random.choice(gaps or global_regions or list(REGIONS)))
        domain = GOVERNMENT_DOMAINS.get(region)
        location = region
        if region == 'India':
            cursor = self.store.preference('india_cursor', 0)
            location += ' ' + INDIA_REGIONS[cursor % len(INDIA_REGIONS)]
            self.store.save_preference('india_cursor', cursor + 1)
        if domain and self._cycle % 2 == 0:
            return f"{location} {random.choice(TOPICS)}", [domain]
        return f"{location} {random.choice(TOPICS)}", None

    def discover(self, query: str = "", domains: list[str] | None = None) -> dict[str, Any]:
        """One search. With no query, the strategy log picks how (stage 3: web, category gap or an open-data portal)."""
        if not self._spend():
            return {"ok": False, "detail": "budget spent"}
        if not query:
            from app.intelligence.discovery_strategies import SmartDiscovery
            return SmartDiscovery(self).run()
        added = [url for url, title in self.web_search(query, domains) if self.consider(url, title, f"search: {query}")]
        return {"ok": True, "query": query + (f" (on {', '.join(domains)})" if domains else ""),
                "candidates": len(added), "detail": ", ".join(added[:5])}

    def web_search(self, query: str, domains: list[str] | None = None) -> list[tuple[str, str]]:
        """(url, title) from a web search, kept on the given government domains when there are some."""
        found = []
        results = self.search(query, 10, domains) if domains else self.search(query, 10)
        for item in results:
            url = str(getattr(item, "url", "") or (item.get("url") if isinstance(item, dict) else "") or "")
            title = str(getattr(item, "title", "") or (item.get("title") if isinstance(item, dict) else "") or "")
            host = urlsplit(url).hostname or ""
            if domains and not any(host == domain or host.endswith("." + domain) for domain in domains):
                continue  # some search providers ignore the site: filter
            found.append((url, title))
        return found

    def consider(self, url: str, title: str, found_by: str) -> bool:
        """Queue an address to probe when it is public, new, and looks like data or an official source."""
        if not url.startswith("https://") or refused_domain(url) or self._known(url) or _KEY_IN_URL.search(url):
            return False
        if not (DATA_HINT.search(url) or DATA_HINT.search(title) or is_official(url) or found_by.split(":")[0] in ("ckan", "socrata", "arcgis")):
            return False
        return self.store.add(url, title, found_by[:300])

    def _known(self, url: str) -> bool:
        """Already a connected network (any of its media or catalog hosts)."""
        from app.intelligence.feeds import BY_KEY
        from app.intelligence.providers import MEDIA_HOSTS

        domain = registered_domain(url)
        if domain in SHARED_PLATFORMS:
            # One platform hosts thousands of publishers (services2.arcgis.com/<publisher>/...): compare the publisher's
            # own path, or every ArcGIS dataset would look "already connected" because one feed lives there.
            prefix = shared_prefix(url)
            return any(shared_prefix(feed.url) == prefix for feed in BY_KEY.values())
        hosts = {registered_domain(feed.url) for feed in BY_KEY.values()}
        for images, videos, pages in MEDIA_HOSTS.values():
            hosts.update(registered_domain(host) for host in (*images, *videos, *pages))
        return domain in hosts

    def probe_next(self) -> dict[str, Any]:
        source = self.store.next_to_probe()
        if source is None:
            return {"ok": True, "detail": "nothing waiting"}
        return self.probe(source)

    def probe(self, source: dict[str, Any]) -> dict[str, Any]:
        url, source_id = source["url"], source["id"]
        reason = refused_domain(url)
        if reason:
            self.store.update(source_id, status="rejected", reason=reason)
            return {"ok": True, "source": source_id, "status": "rejected", "detail": reason}
        if _KEY_IN_URL.search(url):
            access = "This address carries someone's access key. HAYAT does not use keys found in URLs; the publisher's own registration is needed."
            self.store.update(source_id, status="needs_access", access=access)
            return {"ok": True, "source": source_id, "status": "needs_access", "detail": access}
        if not self._spend():
            return {"ok": False, "detail": "budget spent"}
        try:
            resource = self.fetcher.fetch(url, 8 * 1024 * 1024, None, timeout=15)
        except PermissionError as exc:
            self.store.update(source_id, status="rejected", reason=f"robots.txt or the URL policy refuses it: {exc}"[:300])
            return {"ok": True, "source": source_id, "status": "rejected", "detail": "robots.txt or URL policy"}
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (401, 407):
                access = (f"The publisher answers HTTP {status_code}: it requires authorization. Register on its "
                          "developer page for a key or account; HAYAT will use it only once you add valid access.")
                self.store.update(source_id, status="needs_access", access=access)
                return {"ok": True, "source": source_id, "status": "needs_access", "detail": access}
            if status_code == 403:
                access = ("The publisher answers HTTP 403: it refuses automated access (a block on bots, or a login). "
                          "HAYAT does not try to get around it; its open-data or developer page may offer an official "
                          "feed or a key.")
                self.store.update(source_id, status="refused", access=access)
                return {"ok": True, "source": source_id, "status": "refused", "detail": access}
            self.store.update(source_id, status="unreachable", reason=f"{type(exc).__name__}: {exc}"[:300])
            return {"ok": True, "source": source_id, "status": "unreachable"}
        content_type = str(resource.headers.get("content-type", "")).lower()
        body = resource.content
        if ('xml' in content_type or body.lstrip().startswith((b'<?xml', b'<rss'))) and b'<rss' in body[:4096].lower():
            from xml.etree.ElementTree import ParseError
            from app.intelligence.reports import parse_rss, normalize_reports
            from app.intelligence.feeds import Feed
            try:
                items = parse_rss(body)
                valid, _rejected = normalize_reports(Feed('news_probe', source['domain'], url, 3600, url), items)
            except (ValueError, TypeError, ParseError):
                valid = {}
            if valid:
                return self._found_catalog(source, {'format': 'rss', 'count': len(valid), 'media_hosts': []})
        if "json" in content_type or body[:1] in (b"{", b"["):
            try:
                data = json.loads(body)
            except ValueError:
                data = None
            mapping = analyse_catalog(data) if data is not None else None
            if mapping:
                return self._found_catalog(source, mapping, data)
            self.store.update(source_id, status="not_a_camera_catalog", reason="JSON without located camera media")
            return {"ok": True, "source": source_id, "status": "not_a_camera_catalog"}
        if "html" in content_type:
            text = body[:600_000].decode("utf-8", errors="replace")
            links = self._data_links(resource.final_url or url, text)
            for link in links[:5]:
                self.store.add(link, f"linked from {source['domain']}", f"link: {url}")
            if not links and _ACCESS_WORDS.search(text):
                access = "The page describes access by key, account or registration. Register with the publisher; HAYAT will not get around it."
                self.store.update(source_id, status="needs_access", access=access)
                return {"ok": True, "source": source_id, "status": "needs_access", "detail": access}
            self.store.update(source_id, status="page_read", reason=f"{len(links)} data link(s) queued")
            return {"ok": True, "source": source_id, "status": "page_read", "links": len(links)}
        self.store.update(source_id, status="not_a_camera_catalog", reason=f"content type {content_type or 'unknown'}")
        return {"ok": True, "source": source_id, "status": "not_a_camera_catalog"}

    @staticmethod
    def _data_links(base: str, html: str) -> list[str]:
        found = []
        for href in re.findall(r"""href=["']([^"'#]+)["']""", html):
            link = urljoin(base, href)
            if (link.startswith("https://") and re.search(r"(?i)\.(?:geo)?json\b|/api/|featureserver|mapserver|datex|"
                                                          r"wfs|/data/|download|rss|/feed\b|\.xml\b", link)
                    and not refused_domain(link) and not _KEY_IN_URL.search(link)):
                found.append(link)
        return list(dict.fromkeys(found))

    def _found_catalog(self, source: dict[str, Any], mapping: dict[str, Any], data: Any = None) -> dict[str, Any]:
        unit = 'reports' if mapping.get('format') == 'rss' else 'cameras'
        if data is not None and unit == 'cameras':
            # The isolated trial: a few of the catalog's own images or streams must really answer before anything goes live.
            from app.intelligence.discovery_strategies import trial
            mapping["trial"] = trial(self.fetcher, data, mapping, self._spend)
            if mapping["trial"].get("waiting"):
                self.store.update(source["id"], reason="camera catalog found; its trial waits for the request budget")
                return {"ok": True, "source": source["id"], "status": "found", "detail": "trial waits for budget"}
            if not mapping["trial"]["passed"]:
                self.store.update(source["id"], status="trial_failed", mapping=json.dumps(mapping),
                                  reason=(f"{mapping['count']} cameras listed, but {mapping['trial']['working']} of "
                                          f"{mapping['trial']['sampled']} sampled images or streams answered: "
                                          + mapping["trial"]["detail"])[:300])
                return {"ok": True, "source": source["id"], "status": "trial_failed", "cameras": mapping["count"]}
            from app.intelligence.discovery_strategies import StrategyLog
            StrategyLog(self.store).credit(source["url"], mapping["count"])
        mapping_text = json.dumps(mapping)
        own = registered_domain(source["url"])
        same_publisher = all(registered_domain(host) == own for host in mapping["media_hosts"])
        if is_official(source["url"]) and same_publisher:
            self.store.update(source["id"], status="active", mapping=mapping_text,
                              reason=f"official catalog, {mapping['count']} {unit}, media on its own domain")
            active = self.store.get(source["id"])
            key = register_discovered(active, self.manager)
            if self.manager is not None:
                try:
                    self.manager.refresh(feeds=[key])
                except Exception:
                    log_swallowed(_log, f"first refresh of discovered network {key}", level=logging.WARNING)
            return {"ok": True, "source": source["id"], "status": "active", unit: mapping['count'], "feed": key}
        self.store.update(source["id"], status="proposed", mapping=mapping_text,
                          reason=(f"{mapping['count']} {unit}; media on {', '.join(mapping['media_hosts'][:3]) or 'publisher feed'}. "
                                  "Not an official domain or media elsewhere, so it waits for the owner's approval."))
        return {"ok": True, "source": source["id"], "status": "proposed", unit: mapping['count']}

    def approve(self, source_id: int) -> dict[str, Any]:
        """The owner's decision: a proposed catalog becomes a camera network."""
        source = self.store.get(int(source_id))
        if source is None:
            raise ValueError(f"No discovered source {source_id}.")
        if source["status"] != "proposed" or not source["mapping"]:
            raise ValueError(f"Source {source_id} is {source['status']}; only a proposed public catalog can be approved.")
        if refused_domain(source["url"]):
            raise ValueError("That source can never be used: " + str(refused_domain(source["url"])))
        self.store.update(source["id"], status="active", reason="approved by the owner")
        key = register_discovered(self.store.get(source["id"]), self.manager)
        refreshed = self.manager.refresh(feeds=[key]) if self.manager is not None else {}
        cameras = self.manager.cameras(feed=key, limit=1)["total_matching"] if self.manager is not None else 0
        return {"discovery": True, "approved": source["id"], "feed": key, "cameras": cameras,
                "refresh": [item.get("status") for item in refreshed.get("sources", [])]}

    def health(self, per_network: int = 3) -> dict[str, Any]:
        """Do known networks still serve frames? A few samples from the network checked longest ago."""
        if self.manager is None:
            return {"ok": True, "detail": "no camera manager"}
        with self.store.db() as db:
            checked = {row["feed"]: row["checked_at"] for row in db.execute("SELECT feed, checked_at FROM health")}
        networks = sorted({feed.key for feed in self.manager.feeds if feed.key.startswith(("cameras_", "discovered_"))},
                          key=lambda key: checked.get(key, ""))
        if not networks:
            return {"ok": True, "detail": "no networks"}
        feed = networks[0]
        rows = [row for row in self.manager.cameras(feed=feed, available_only=True, limit=200)["cameras"]
                if row["details"].get("image_url") or row["details"].get("video_url")]
        if not rows:
            # Viewer-only networks (Illinois, Michigan) have nothing HAYAT fetches; mark them checked and move on.
            with self.store.db() as db:
                db.execute("INSERT OR REPLACE INTO health (feed, checked_at, sampled, working, detail) VALUES (?,?,?,?,?)",
                           (feed, now(), 0, 0, "viewer links only, nothing to sample"))
            return {"ok": True, "feed": feed, "detail": "viewer links only"}
        sample = random.sample(rows, min(per_network, len(rows)))
        if not self._spend(len(sample)):
            return {"ok": False, "feed": feed, "detail": "budget spent"}
        working = 0
        for row in sample:
            details = row["details"]
            url = details.get("image_url") or details.get("video_url")
            try:
                self.fetcher.fetch(url, 3 * 1024 * 1024, None, timeout=10)
                working += 1
            except Exception:
                log_swallowed(_log, f"health sample from {feed}")
        with self.store.db() as db:
            db.execute("INSERT OR REPLACE INTO health (feed, checked_at, sampled, working, detail) VALUES (?,?,?,?,?)",
                       (feed, now(), len(sample), working, ""))
        return {"ok": True, "feed": feed, "sampled": len(sample), "working": working}

    def mcp(self, topic: str = "") -> dict[str, Any]:
        """Suggest public MCP servers for a topic, with the access each needs. Nothing is installed."""
        if not self._spend():
            return {"ok": False, "detail": "budget spent"}
        # The registry's search takes ~45 s server-side (past the fetch timeout: three failed jobs in the log); its
        # plain listing answers in under a second. So each job reads the next page of the listing and keeps the
        # servers whose name or description matches a topic.
        topics = (topic,) if topic else MCP_TOPICS
        cursor = self.store.preference("mcp_cursor", "")
        url = f"{MCP_REGISTRY}?limit=100" + (f"&cursor={quote(cursor, safe='')}" if cursor else "")
        data = self.fetcher.get_json(url)
        next_cursor = ((data.get("metadata") or {}).get("nextCursor") or "") if isinstance(data, dict) else ""
        self.store.save_preference("mcp_cursor", next_cursor)  # "" starts the listing again next time
        found = 0
        for entry in (data.get("servers") or []) if isinstance(data, dict) else []:
            server = entry.get("server") or {}
            name = str(server.get("name") or "")[:200]
            words = f"{name} {server.get('title') or ''} {server.get('description') or ''}".lower()
            topic = next((item for item in topics if re.search(rf"\b{re.escape(item)}s?\b", words)), "")
            if not name or not topic:
                continue
            remotes = server.get("remotes") or []
            needs = [header.get("description") or header.get("name") for remote in remotes
                     for header in remote.get("headers") or [] if header.get("isRequired")]
            access = ("needs " + "; ".join(str(item) for item in needs)[:200]) if needs else (
                "remote, no credentials listed" if remotes else "runs locally from its package")
            url = str((remotes[0].get("url") if remotes else "") or (server.get("repository") or {}).get("url") or "")
            with self.store.db() as db:
                cursor = db.execute("INSERT OR IGNORE INTO mcp (name, description, url, access, topic, found_at) "
                                    "VALUES (?,?,?,?,?,?)",
                                    (name, str(server.get("description") or "")[:300], url[:500], access, topic, now()))
                found += cursor.rowcount
        return {"ok": True, "topics": list(topics), "new_servers": found, "listing_continues": bool(next_cursor)}

    # ---- understanding
    def coverage(self) -> dict[str, Any]:
        """Cameras per country, from the connected networks: where HAYAT can and cannot see."""
        if self.manager is None:
            return {"countries": {}}
        counts: dict[str, int] = {}
        try:
            _sources, rows = self.manager._matching("", "", None, False, "cameras")
            for row in rows:
                country = str(row["details"].get("country") or "")
                if country:
                    counts[country] = counts.get(country, 0) + 1
        except Exception:
            log_swallowed(_log, "counting camera coverage by country")
        return {"countries": counts, "gaps": [region for region in REGIONS if not counts.get(region)]}

    def report(self) -> dict[str, Any]:
        with self.store.db() as db:
            health = [dict(row) for row in db.execute("SELECT * FROM health ORDER BY working * 1.0 / sampled, checked_at DESC")]
            mcp = [dict(row) for row in db.execute("SELECT * FROM mcp ORDER BY found_at DESC LIMIT 8")]
        allowed, reason = self.may_run()
        coverage = self.coverage()
        from app.intelligence.discovery_strategies import StrategyLog, category_counts
        return {"discovery": True, "running": self.running, "strategies": StrategyLog(self.store).summary(),
                "kind_scores": {kind: round(score, 2) for kind, score in StrategyLog(self.store).kind_scores().items()},
                "category_coverage": category_counts(self.manager), "trial_failed": self.store.sources("trial_failed", 8), "can_run_now": allowed, "waiting_because": "" if allowed else reason,
                "budget_per_hour": self.requests_per_hour, "counts": self.store.counts(),
                'priority_regions': list(self.priority_regions), 'india_regions_in_rotation': len(INDIA_REGIONS),
                "proposed": self.store.sources("proposed", 10), "active": self.store.sources("active", 10),
                "needs_access": self.store.sources("needs_access", 10) + self.store.sources("refused", 10),
                "health_problems": [row for row in health if row["sampled"] and row["working"] < row["sampled"]][:8],
                "health_checked": len(health), "mcp_suggestions": mcp,
                "coverage_gaps": coverage.get("gaps", [])[:12], "countries_covered": len(coverage.get("countries", {})),
                "recent": self.store.recent_journal(6), "last": self.last,
                "basis": ("Public sources only: robots.txt honoured, public addresses only, no logins, no keys taken "
                          "from URLs, no directories of unsecured cameras. Official catalogs with media on their own "
                          "domain go live by themselves; others wait for your approval.")}

    # ---- loop
    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hayat-source-discovery", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        self._stop.wait(120)  # never in the first minutes after start-up
        while not self._stop.is_set():
            allowed, reason = self.may_run()
            if allowed:
                self.cycle()
                self._stop.wait(self.interval_seconds)
            else:
                self.last = {"at": now(), "job": "waiting", "detail": reason}
                self._stop.wait(min(self.interval_seconds, 300))


# ------------------------------------------------------------------ routing
_REQUEST = re.compile(
    r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could)\s+(?:you|u)\s+)?(?:"
    r"(?P<status>(?:show\s+)?(?:the\s+)?discovery\s+(?:status|report)|what\s+(?:new\s+)?(?:sources|cameras|data\s+sources)\s+"
    r"(?:did|have)\s+you\s+(?:found|find|discovered|discover)|show\s+(?:me\s+)?(?:the\s+)?discovered\s+sources)"
    r"|(?P<run>(?:discover|find|look\s+for|search\s+for)\s+(?:new\s+|more\s+)?(?:public\s+)?(?:camera|webcam|data)\s+"
    r"(?:sources|feeds|catalogs|networks)(?:\s+now)?|run\s+(?:source\s+)?discovery(?:\s+now)?)"
    r"|(?P<approve>approve\s+(?:discovered\s+)?source\s+(?P<id>\d{1,6}))"
    r")[\s.!?]*$")


def discovery_request(text: str) -> dict[str, Any] | None:
    match = _REQUEST.match(" ".join(str(text or "").split()))
    if not match:
        return None
    if match["approve"]:
        return {"tool": "discovery_approve", "arguments": {"source_id": int(match["id"])}}
    if match["run"]:
        return {"tool": "discovery_run", "arguments": {}}
    return {"tool": "discovery_status", "arguments": {}}


def direct_discovery_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    request = discovery_request(text)
    if not request or request["tool"] not in set(names):
        return None
    return [{"description": "Use HAYAT's public-source discovery.", "tool": request["tool"],
             "arguments": request["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_discovery_tools(registry: Any, scout: SourceScout) -> None:
    obj = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register(
        'camera_api_search',
        'Search for official public camera APIs and catalogs for a region or query. Saves candidates for discovery_run to probe; reports access requirements without treating search hits as live cameras.',
        {'type': 'object', 'properties': {'query': {'type': 'string', 'minLength': 2, 'maxLength': 250}},
         'required': ['query'], 'additionalProperties': False}, 'low', 'web_search',
        lambda query: scout.discover(query=query.strip() + ' official public traffic camera API open data'))
    registry.register(
        "discovery_status",
        "Public camera and data source discovery: what HAYAT found, what is live, what waits for approval, what "
        "needs access (and which), network health samples, MCP server suggestions and coverage gaps.",
        obj, "low", "filesystem_read", scout.report)
    registry.register(
        "discovery_run",
        "Run discovery cycles now (probe a waiting candidate, else search for new public camera catalogs). Public "
        "sources only; robots.txt honoured; no logins.",
        {"type": "object", "properties": {"cycles": {"type": "integer", "minimum": 1, "maximum": 5}},
         "additionalProperties": False}, "low", "web_search",
        lambda cycles=3: {**scout.report(), "ran": [scout.cycle() for _ in range(int(cycles))]})
    registry.register(
        "discovery_approve",
        "Approve a proposed discovered camera catalog so it becomes a live camera network (owner decision).",
        {"type": "object", "properties": {"source_id": {"type": "integer", "minimum": 1}}, "required": ["source_id"],
         "additionalProperties": False}, "low", "public_api_get", scout.approve)
