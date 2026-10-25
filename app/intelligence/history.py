"""Where moving things were: a time-indexed history of every aircraft and ship position HAYAT observed.

Every position that passes through the live providers (a map layer, a question about a
place, or a military/emergency query) is kept for seven days. That is what
lets HAYAT answer "what aircraft were near here at 14:20?", draw a track, or notice a
holding pattern -- but only from what it actually observed. Gaps are gaps: an aircraft
HAYAT never looked at is not in this history, and the answer says so.
"""

from __future__ import annotations

import math
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.intelligence.live import bearing, distance_km

RETENTION = timedelta(days=7)


class PositionHistory:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_prune = 0.0
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS positions(
                    kind TEXT NOT NULL, ident TEXT NOT NULL, label TEXT, at TEXT NOT NULL,
                    latitude REAL NOT NULL, longitude REAL NOT NULL, altitude_ft REAL, speed REAL, heading REAL,
                    detail TEXT, source TEXT,
                    PRIMARY KEY(kind, ident, at));
                CREATE INDEX IF NOT EXISTS positions_time ON positions(at);
                CREATE INDEX IF NOT EXISTS positions_place ON positions(latitude, longitude);
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    # ------------------------------------------------------------- write
    def record(self, kind: str, items: list[dict[str, Any]], source: str, at: datetime | None = None) -> int:
        """Store positions, one per craft per ~30 seconds (repeats inside that window are dropped)."""
        moment = at or datetime.now(UTC)
        moment = (moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)).replace(microsecond=0)
        stamp = moment.replace(second=moment.second - moment.second % 30).isoformat()
        rows = []
        for item in items:
            ident = str(item.get("hex") or item.get("mmsi") or item.get("id") or "").lower()
            if not ident or item.get("latitude") is None or item.get("longitude") is None:
                continue
            latitude, longitude = float(item["latitude"]), float(item["longitude"])
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                continue
            label = item.get("callsign") or item.get("flight") or item.get("registration") or item.get("name") or ""
            detail = "|".join(str(item.get(key) or "").replace("|", " ") for key in ("registration", "type", "kind", "squawk", "operator"))
            rows.append((kind, ident, str(label).upper()[:40], stamp, latitude, longitude,
                         item.get("altitude_ft"), item.get("speed_kt", item.get("speed_kn")),
                         item.get("track", item.get("course")), detail, source))
        if not rows:
            return 0
        with self._lock, self._connect() as db:
            db.executemany("INSERT OR IGNORE INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            inserted = db.total_changes
            if moment.timestamp() - self._last_prune > 3600:
                db.execute("DELETE FROM positions WHERE at < ?", ((moment - RETENTION).isoformat(),))
                db.execute("DELETE FROM positions WHERE rowid IN (SELECT rowid FROM positions ORDER BY at DESC LIMIT -1 OFFSET 500000)")
                self._last_prune = moment.timestamp()
        return inserted

    # -------------------------------------------------------------- read
    def near(self, latitude: float, longitude: float, radius_km: float, at: datetime,
             window_minutes: int = 10, kind: str = "aircraft") -> dict[str, Any]:
        """Craft observed within a radius around a moment (± the window), closest observation per craft."""
        radius_km = max(0.5, min(float(radius_km), 1000.0))
        at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
        degrees = radius_km / 111.0
        span = degrees / max(0.05, math.cos(math.radians(latitude)))
        west, east = (longitude - span + 180) % 360 - 180, (longitude + span + 180) % 360 - 180
        start, end = at - timedelta(minutes=window_minutes), at + timedelta(minutes=window_minutes)
        with self._connect() as db:
            rows = db.execute(
                "SELECT ident,label,at,latitude,longitude,altitude_ft,speed,heading,detail,source FROM positions "
                "WHERE kind=? AND at BETWEEN ? AND ? AND latitude BETWEEN ? AND ? "
                "AND (? >= 180 OR (? <= ? AND longitude BETWEEN ? AND ?) OR (? > ? AND (longitude >= ? OR longitude <= ?)))",
                (kind, start.isoformat(), end.isoformat(), latitude - degrees, latitude + degrees,
                 span, west, east, west, east, west, east, west, east)).fetchall()
            observed = db.execute("SELECT MIN(at), MAX(at), COUNT(*) FROM positions WHERE kind=? AND at BETWEEN ? AND ?",
                                  (kind, start.isoformat(), end.isoformat())).fetchone()
        best: dict[str, dict[str, Any]] = {}
        for ident, label, stamp, lat, lon, altitude, speed, heading, detail, source in rows:
            distance = distance_km(latitude, longitude, lat, lon)
            if distance > radius_km:
                continue
            gap = abs((datetime.fromisoformat(stamp) - at).total_seconds())
            if ident not in best or gap < best[ident]["seconds_from_moment"]:
                registration, aircraft_type, craft_kind, squawk, operator = (detail or "||||").split("|")[:5]
                best[ident] = {"ident": ident, "label": label, "at": stamp, "latitude": lat, "longitude": lon,
                               "distance_km": round(distance, 1), "direction": bearing(latitude, longitude, lat, lon),
                               "altitude_ft": altitude, "speed": speed, "heading": heading,
                               "registration": registration or None, "type": aircraft_type or None,
                               "kind": craft_kind or None, "squawk": squawk or None, "operator": operator or None,
                               "source": source, "seconds_from_moment": gap}
        found = sorted(best.values(), key=lambda item: item["distance_km"])
        coverage = {"positions_recorded_in_window_anywhere": observed[2], "first": observed[0], "last": observed[1]}
        return {"moment": at.isoformat(timespec="seconds"), "window_minutes": window_minutes, "radius_km": radius_km,
                "found": found, "coverage": coverage,
                "basis": ("Only positions HAYAT itself observed are kept (seven days). "
                          + ("Nothing was being recorded around then, so absence here proves nothing."
                             if not observed[2] else "A craft HAYAT was not watching at that time is not listed."))}

    def track(self, ident: str, hours: float = 6.0, kind: str = "aircraft") -> list[dict[str, Any]]:
        since = (datetime.now(UTC) - timedelta(hours=max(0.1, min(hours, 168)))).isoformat()
        with self._connect() as db:
            rows = db.execute("SELECT at,latitude,longitude,altitude_ft,speed,heading,label FROM positions "
                              "WHERE kind=? AND (ident=? OR label=?) AND at>=? ORDER BY at", (kind, ident.lower(), ident.upper(), since)).fetchall()
        return [{"at": at, "latitude": lat, "longitude": lon, "altitude_ft": altitude, "speed": speed, "heading": heading,
                 "label": label} for at, lat, lon, altitude, speed, heading, label in rows]

    def counts(self, south: float, west: float, north: float, east: float, hours: int = 6,
               kind: str = "aircraft") -> list[dict[str, Any]]:
        """Distinct craft per hour inside a box: how activity changed (only where HAYAT was recording)."""
        since = (datetime.now(UTC) - timedelta(hours=max(1, min(hours, 168)))).isoformat()
        with self._connect() as db:
            rows = db.execute("SELECT substr(at,1,13) AS hour, COUNT(DISTINCT ident) FROM positions WHERE kind=? AND at>=? "
                              "AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? GROUP BY hour ORDER BY hour",
                              (kind, since, south, north, west, east)).fetchall()
        return [{"hour": hour + ":00", "distinct": count} for hour, count in rows]

    def stats(self) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT kind, COUNT(*), COUNT(DISTINCT ident), MIN(at), MAX(at) FROM positions GROUP BY kind").fetchall()
        return {kind: {"positions": count, "craft": craft, "from": first, "to": last} for kind, count, craft, first, last in rows}


def holding_patterns(track: list[dict[str, Any]], minutes: int = 30) -> dict[str, Any] | None:
    """A craft turning through more than one and a half circles inside ~25 km in `minutes`: an anomaly, not a conclusion."""
    if len(track) < 6:
        return None
    end = datetime.fromisoformat(track[-1]["at"])
    recent = [point for point in track if datetime.fromisoformat(point["at"]) >= end - timedelta(minutes=minutes)
              and point.get("heading") is not None]
    if len(recent) < 6:
        return None
    turned = 0.0
    for earlier, later in zip(recent, recent[1:]):
        change = (later["heading"] - earlier["heading"] + 540) % 360 - 180
        turned += change
    spread = max(distance_km(recent[0]["latitude"], recent[0]["longitude"], point["latitude"], point["longitude"]) for point in recent)
    if abs(turned) >= 540 and spread <= 25:
        return {"anomaly": "holding or circling pattern", "turned_degrees": round(abs(turned)), "within_km": round(spread, 1),
                "minutes": minutes, "label": "ANOMALY -- not a conclusion",
                "alternatives": "Routine holding for traffic or weather, training, survey or police/medical work, "
                                "or a sightseeing flight."}
    return None
