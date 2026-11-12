"""Which public cameras actually work right now, from real checks rather than the publishers' own flags.

Added 2026-11-06 after a live audit of every network: a camera marked available can still answer 404, send a web
page instead of a picture (New South Wales), show the publisher's "Temporarily Unavailable" card (Caltrans, Austin,
Lyon, King County), stream a "LOADING VIDEO" slate (Indiana) or stream video only Apple devices can decrypt
(Alabama: FairPlay DRM). Every still HAYAT fetches and every stream it probes is recorded here, one row per camera.
A camera that failed is skipped for FAILURE_MEMORY and then tried again; a network where every recent check failed
is treated as down. `intelligence_camera_check` tests a sample of each network on request.
"""
from __future__ import annotations

import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from urllib.parse import urljoin

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: A failed camera is skipped this long, then tried again: cards and outages usually clear within hours.
FAILURE_MEMORY = timedelta(hours=6)
#: Failed checks, with none working, that mark a whole network as down.
NETWORK_DOWN = 5
#: A camera's stream is recorded as "<camera id>#stream", apart from its still (services.STREAM).
STREAM = "#stream"
WORDS = {"working": "working", "card": "showing the publisher's 'unavailable' card", "error": "not answering",
         "web_page": "answering with a web page instead of a picture", "drm": "encrypted video (DRM) no player here can show",
         "stream_dead": "stream not answering", "empty": "stream with no video segments"}


def now() -> datetime:
    return datetime.now(UTC)


def _moment(record: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(record.get("at")))
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    except ValueError:
        return None


def failing(record: dict[str, Any] | None, moment: datetime | None = None) -> bool:
    """True when the camera's last real check failed within FAILURE_MEMORY."""
    if not record or record.get("ok"):
        return False
    at = _moment(record)
    return at is not None and (moment or now()) - at <= FAILURE_MEMORY


def label(record: dict[str, Any] | None, moment: datetime | None = None) -> str:
    """'working, 12 min ago' / 'publisher card, 2 h ago' / '' for a camera never checked."""
    if not record:
        return ""
    at = _moment(record)
    minutes = int(((moment or now()) - at).total_seconds() // 60) if at else None
    age = "" if minutes is None else "just now" if minutes < 1 else f"{minutes} min ago" if minutes < 90 else f"{minutes // 60} h ago"
    word = {"working": "working", "card": "publisher card", "web_page": "web page, no picture", "error": "not answering",
            "drm": "encrypted video", "stream_dead": "stream down", "empty": "empty stream"}.get(record.get("verdict"), record.get("verdict"))
    return f"{word}, {age}" if age else str(word)


def network_states(store: Any, moment: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Per network, the checks within FAILURE_MEMORY: how many cameras worked, how many failed, and whether it is down."""
    moment = moment or now()
    states: dict[str, dict[str, Any]] = {}
    for record in store.camera_health().values():
        at = _moment(record)
        if at is None or moment - at > FAILURE_MEMORY:
            continue
        state = states.setdefault(record["feed"], {"working": 0, "failing": 0, "verdicts": {}, "last": record["at"]})
        state["working" if record["ok"] else "failing"] += 1
        if not record["ok"]:
            state["verdicts"][record["verdict"]] = state["verdicts"].get(record["verdict"], 0) + 1
        state["last"] = max(state["last"], record["at"])
    for state in states.values():
        state["down"] = state["working"] == 0 and state["failing"] >= NETWORK_DOWN
    return states


# ---------------------------------------------------------------------- HLS
KEY = re.compile(r"#EXT-X-(?:SESSION-)?KEY:([^\n]*)")


def hls_state(fetch: Callable[[str], bytes], url: str) -> tuple[str, str]:
    """Inspect bounded HLS variants. One broken rendition does not make a camera dead.

    Only URIs following STREAM-INF are video variants (an earlier URI may be an
    audio rendition). Every nested response is validated; cycles and large
    variant lists are bounded. Playlist availability is not proof of decoding.
    """
    visited: set[str] = set()

    def inspect(address: str, depth: int) -> tuple[str, str]:
        if address in visited or depth > 3 or len(visited) >= 8:
            return "stream_dead", "playlist nesting or request limit reached"
        visited.add(address)
        try:
            text = bytes(fetch(address)).decode("utf-8-sig", "replace")
        except Exception as exc:
            return "stream_dead", f"{type(exc).__name__}: {exc}"[:160]
        if not text.lstrip().startswith("#EXTM3U"):
            return "stream_dead", "the address did not return an HLS playlist"
        for key in KEY.findall(text):
            if drm(key):
                return "drm", key[:80]
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if any(line.startswith('#EXT-X-STREAM-INF:') for line in lines):
            variants, pending = [], False
            for line in lines:
                if line.startswith('#EXT-X-STREAM-INF:'):
                    pending = True
                elif pending and not line.startswith('#'):
                    variants.append(urljoin(address, line))
                    pending = False
            if not variants:
                return "empty", "master playlist lists no variant"
            results = []
            for variant in dict.fromkeys(variants):
                if len(results) >= 3:
                    break
                result = inspect(variant, depth + 1)
                if result[0] == 'live':
                    return result
                results.append(result)
            return next((result for result in results if result[0] == 'drm'), results[0])
        if not any(line.startswith('#EXTINF:') for line in lines) or not any(not line.startswith('#') for line in lines):
            return "empty", "playlist has no video segments"
        return "live", ""

    return inspect(url, 0)


def drm(key: str) -> bool:
    """Plain AES-128 with a key file is ordinary HLS; SAMPLE-AES with FairPlay, Widevine or PlayReady key formats is DRM."""
    upper = key.upper()
    return "SAMPLE-AES" in upper and ("SKD://" in upper or "KEYFORMAT" in upper) or "COM.APPLE.STREAMINGKEYDELIVERY" in upper \
        or "URN:UUID:EDEF8BA9" in upper or "URN:UUID:9A04F079" in upper


# ---------------------------------------------------------------------- the check
def _networks(manager: Any, feed: str, query: str) -> dict[str, list[dict[str, Any]]]:
    """network → the cameras to sample from: a whole network, a network family ("cameras_caltrans"), or the cameras
    whose names and places match a query ("Texas", "London"), grouped by network."""
    from app.intelligence.feeds import ALL_FEEDS
    if query and not feed:
        # A network named for the place ("Transport for London", "Iowa DOT") is that place's cameras; otherwise the
        # cameras whose names and places mention it (a "London Rd" in Indiana only counts when no network matches).
        from app.intelligence.camera_chat import PLACE_FEEDS
        words = query.lower().split()
        named = [item.key for item in ALL_FEEDS if item.key.startswith("cameras_") and manager.store.latest(item.key)
                 and (item.key in PLACE_FEEDS.get(" ".join(words), ()) or all(word in (item.key + " " + item.title).lower() for word in words))]
        if named:
            return {key: list(((manager.store.latest(key) or {}).get("events") or {}).values()) for key in named}
        _sources, rows = manager._matching(query, "", None, False, "cameras")
        needle = " ".join(words)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            details = row.get("details") or {}
            place = " ".join(str(details.get(key) or "") for key in ("city", "county", "state", "region", "country")).lower()
            if row.get("kind") == "public_camera" and row["feed"].startswith("cameras_") and needle in place:
                grouped.setdefault(row["feed"], []).append(row)
        if grouped:
            return grouped
        # No camera is placed there by name: fall back to cameras whose names mention it ("I-35 @ Corral Ln").
        for row in rows:
            if row.get("kind") == "public_camera" and row["feed"].startswith("cameras_") and needle in str(row.get("title", "")).lower():
                grouped.setdefault(row["feed"], []).append(row)
        return grouped
    keys = [item.key for item in ALL_FEEDS if item.key.startswith("cameras_") and (not feed or item.key == feed
                                                                                  or item.key.startswith(feed.rstrip("_") + "_"))]
    return {key: list(((manager.store.latest(key) or {}).get("events") or {}).values()) for key in keys if manager.store.latest(key)}


def check_cameras(manager: Any, feed: str = "", query: str = "", sample: int = 4, streams: int = 2, decode: bool = True) -> dict[str, Any]:
    """Fetch real stills and probe real streams (decoding one frame each with FFmpeg when `decode`) from a sample of each
    matching network, recording every result."""
    networks_rows = _networks(manager, feed, query)
    keys = list(networks_rows)
    if not keys:
        raise ValueError(f"No connected camera network matches {feed or query!r}." if feed or query else "No camera network is loaded yet.")
    sample, streams = max(1, min(int(sample), 10)), max(0, min(int(streams), 5))
    checked_before = manager.store.camera_health()
    rng = random.Random()

    def pick(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        # Cameras never checked first, then the ones checked longest ago.
        rng.shuffle(rows)
        rows.sort(key=lambda row: str((checked_before.get(row["id"]) or {}).get("at") or ""))
        return rows[:count]

    def one(key: str) -> dict[str, Any]:
        rows = networks_rows[key]
        stills = pick([row for row in rows if row["details"].get("image_url")], sample)
        videos = pick([row for row in rows if row["details"].get("video_url")], streams)
        for row in stills:
            try:
                manager.camera_frame(row["id"], fresh=True)
            except Exception:  # camera_frame recorded the verdict; the check goes on
                log_swallowed(_log, f"checking camera {row['id']}")
        for row in videos:
            manager.stream_health(row, decode=decode, timeout=10)
        # A camera's still and its stream are separate records (STREAM suffix): both are counted.
        checked = [(row, row["id"]) for row in stills] + [(row, row["id"] + STREAM) for row in videos]
        results = manager.store.camera_health([key for _row, key in checked])
        verdicts: dict[str, int] = {}
        for record in results.values():
            verdicts[record["verdict"]] = verdicts.get(record["verdict"], 0) + 1
        examples = list(dict.fromkeys(
            f"{row['title'][:60]}{' (video)' if key.endswith(STREAM) else ''}: {WORDS.get(results[key]['verdict'], results[key]['verdict'])}"
            for row, key in checked if key in results and not results[key]["ok"]))[:3]
        return {"feed": key, "cameras": len(rows), "stills_checked": len(stills), "streams_checked": len(videos),
                "working": sum(record["ok"] for record in results.values()), "checked": len(results),
                "verdicts": verdicts, "examples": examples}

    started = now()
    with ThreadPoolExecutor(max_workers=8) as pool:
        networks = list(pool.map(one, keys))
    from app.intelligence.feeds import BY_KEY
    for item in networks:
        item["title"] = BY_KEY[item["feed"]].title if item["feed"] in BY_KEY else item["feed"]
        item["state"] = ("nothing to check" if not item["checked"] else "working" if item["working"] == item["checked"] else "down" if item["working"] == 0
                         else "partly working" if item["checked"] else "nothing to check")
    networks.sort(key=lambda item: ({"down": 0, "partly working": 1, "working": 2}.get(item["state"], 3), item["feed"]))
    return {"intelligence": True, "explore": "camera_check", "networks": networks,
            "checked": sum(item["checked"] for item in networks), "working": sum(item["working"] for item in networks),
            "seconds": round((now() - started).total_seconds(), 1),
            "source": "the publishers' own stills and stream playlists, fetched now",
            "basis": "A sample, not every camera. A still counts as working when a real picture arrives that is not the "
                     "publisher's 'unavailable' card; a stream when its playlist lists video segments without DRM. Cameras "
                     "that failed are skipped for six hours when HAYAT picks a camera to show, then tried again."}


def report_lines(value: dict[str, Any]) -> list[str]:
    lines = [f"Camera check: {value['working']} of {value['checked']} sampled cameras working across "
             f"{len(value['networks'])} network{'s' if len(value['networks']) != 1 else ''} ({value['seconds']:g} s)."]
    for item in value["networks"][:40]:
        if not item["checked"]:
            continue
        problems = ", ".join(f"{count} {WORDS.get(verdict, verdict)}" for verdict, count in item["verdicts"].items() if verdict != "working")
        lines.append(f"{'✓' if item['state'] == 'working' else '✗' if item['state'] == 'down' else '◐'} {item['title']}: "
                     f"{item['working']}/{item['checked']} working" + (f" — {problems}" if problems else ""))
        lines.extend(f"    {example}" for example in item["examples"][:2] if item["state"] != "working")
    return lines


def register_camera_check_tools(registry: Any, manager: Any) -> None:
    registry.register(
        "intelligence_camera_check",
        "Test whether public cameras actually work now: fetch real stills (refusing the publisher's 'unavailable' cards) and "
        "probe real streams (refusing DRM, and decoding one frame each to catch slates) from a sample of each network, "
        "optionally one network or a place. Results steer which cameras HAYAT shows.",
        {"type": "object", "properties": {"feed": {"type": "string", "maxLength": 60}, "query": {"type": "string", "maxLength": 80},
                                          "sample": {"type": "integer", "minimum": 1, "maximum": 10},
                                          "streams": {"type": "integer", "minimum": 0, "maximum": 5},
                                          "decode": {"type": "boolean"}},
         "required": [], "additionalProperties": False},
        "low", "public_web_read",
        lambda feed="", query="", sample=4, streams=2, decode=True: check_cameras(manager, feed, query, sample, streams, decode))
