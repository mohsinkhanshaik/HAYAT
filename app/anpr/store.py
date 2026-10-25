"""Recognition records, authorized vehicles, parking review events, exports, retention and deletion.

Everything stays in one local SQLite file (data/anpr/anpr.db) and a crops
folder beside it. Nothing here looks up who owns a vehicle, connects to any
government or police database, or issues a notice or fine: a parking rule
breach becomes an internal review event for a person to handle through their
own authorized process.
"""
from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS = {
    "region": "IN",                # plate format for repair and validation
    "custom_formats": [],          # [{"code", "label", "spec"}]
    "retention_days": 30,          # records and crops older than this are deleted
    "store_crops": True,           # keep the plate image for review
    "review_threshold": .75,       # below this confidence a record waits in the review queue
    "parking_mode": False,         # create review events for unregistered plates
    "max_stay_hours": 0,           # 0 = no overstay events
    "authorized_cameras": [],      # RTSP addresses the owner may read plates from
    "detector": "yolo",            # yolo | contour
    "ocr_engine": "auto",          # auto | fast-plate-ocr | easyocr | paddleocr
    "use_gpu": "auto",             # auto | on | off
    "frame_stride": 2,             # read every Nth video frame
}
STATUSES = ("new", "confirmed", "corrected", "rejected")


def now() -> datetime:
    return datetime.now(UTC)


class AnprStore:
    def __init__(self, folder: Path | str) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.crops = self.folder / "crops"
        self.path = self.folder / "anpr.db"
        self._lock = threading.RLock()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS recognitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, plate TEXT NOT NULL, raw_text TEXT, confidence REAL,
                    detection_confidence REAL, readings INTEGER DEFAULT 1, region TEXT, format_valid INTEGER,
                    source TEXT, source_kind TEXT, seen_at TEXT NOT NULL, video_offset REAL, bbox TEXT, crop_path TEXT,
                    status TEXT NOT NULL DEFAULT 'new', corrected_plate TEXT, reviewed_at TEXT, created_at TEXT NOT NULL,
                    engine TEXT);
                CREATE INDEX IF NOT EXISTS recognitions_plate ON recognitions(plate);
                CREATE INDEX IF NOT EXISTS recognitions_seen ON recognitions(seen_at);
                CREATE TABLE IF NOT EXISTS vehicles (
                    plate TEXT PRIMARY KEY, label TEXT, group_name TEXT, notes TEXT, added_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, recognition_id INTEGER, plate TEXT, kind TEXT NOT NULL,
                    note TEXT, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, resolved_at TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def _db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict[str, Any]:
        with self._db() as db:
            stored = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings")}
        return {**DEFAULT_SETTINGS, **{key: value for key, value in stored.items() if key in DEFAULT_SETTINGS}}

    def save_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError("Unknown settings: " + ", ".join(sorted(unknown)))
        if "retention_days" in changes and not 1 <= int(changes["retention_days"]) <= 3650:
            raise ValueError("Keep records for 1 to 3650 days.")
        if "review_threshold" in changes and not 0 <= float(changes["review_threshold"]) <= 1:
            raise ValueError("The review threshold is between 0 and 1.")
        with self._lock, self._db() as db:
            for key, value in changes.items():
                db.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, json.dumps(value)))
        return self.settings()

    # ------------------------------------------------------------------ recognitions
    def add(self, record: dict[str, Any], crop: Any = None) -> dict[str, Any]:
        """Store one recognition; returns it with its id and any parking events it raised."""
        settings = self.settings()
        crop_path = ""
        with self._lock, self._db() as db:
            cursor = db.execute(
                "INSERT INTO recognitions (plate, raw_text, confidence, detection_confidence, readings, region, format_valid, "
                "source, source_kind, seen_at, video_offset, bbox, status, created_at, engine) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)",
                (record["plate"], record.get("raw_text"), record.get("confidence"), record.get("detection_confidence"),
                 record.get("readings", 1), record.get("region"), int(bool(record.get("format_valid"))), record.get("source"),
                 record.get("source_kind"), record.get("seen_at") or now().isoformat(), record.get("video_offset"),
                 json.dumps(record.get("bbox")), now().isoformat(), record.get("engine")))
            identity = cursor.lastrowid
            if crop is not None and settings["store_crops"]:
                import cv2
                day = (record.get("seen_at") or now().isoformat())[:10]
                target = self.crops / day / f"{identity}.jpg"
                target.parent.mkdir(parents=True, exist_ok=True)
                if cv2.imwrite(str(target), crop):
                    crop_path = str(target)
                    db.execute("UPDATE recognitions SET crop_path=? WHERE id=?", (crop_path, identity))
        events = self._parking_events(identity, record, settings)
        return {**record, "id": identity, "crop_path": crop_path, "status": "new", "events": events}

    def _parking_events(self, identity: int, record: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]]:
        if not settings["parking_mode"] or not record.get("plate"):
            return []
        created = []
        plate = record["plate"]
        with self._lock, self._db() as db:
            if not db.execute("SELECT 1 FROM vehicles WHERE plate=?", (plate,)).fetchone():
                created.append(("not_registered", "Plate is not on the authorized vehicle list."))
            hours = float(settings["max_stay_hours"] or 0)
            if hours > 0:
                first = db.execute("SELECT MIN(seen_at) AS first FROM recognitions WHERE plate=? AND seen_at >= ?",
                                   (plate, (now() - timedelta(hours=24)).isoformat())).fetchone()["first"]
                if first and datetime.fromisoformat(record.get("seen_at") or now().isoformat()) - datetime.fromisoformat(first) \
                        > timedelta(hours=hours):
                    created.append(("overstay", f"Seen for longer than {hours:g} h (first today at {first[11:16]} UTC)."))
            rows = []
            for kind, note in created:
                open_already = db.execute("SELECT id FROM events WHERE plate=? AND kind=? AND status='open'", (plate, kind)).fetchone()
                if open_already:
                    continue
                cursor = db.execute("INSERT INTO events (recognition_id, plate, kind, note, created_at) VALUES (?, ?, ?, ?, ?)",
                                    (identity, plate, kind, note, now().isoformat()))
                rows.append({"id": cursor.lastrowid, "plate": plate, "kind": kind, "note": note})
        return rows

    def search(self, text: str = "", status: str = "", source: str = "", since: str = "", until: str = "",
               authorized: bool | None = None, limit: int = 200, offset: int = 0) -> dict[str, Any]:
        """Records matching every filter given, newest first, with the total."""
        clauses, values = [], []
        if text:
            clauses.append("(plate LIKE ? OR corrected_plate LIKE ?)")
            values += [f"%{text.upper()}%"] * 2
        if status:
            if status not in (*STATUSES, "review"):
                raise ValueError("Status is new, confirmed, corrected, rejected or review.")
            if status == "review":
                clauses.append("status='new' AND (confidence < ? OR format_valid = 0)")
                values.append(float(self.settings()["review_threshold"]))
            else:
                clauses.append("status=?")
                values.append(status)
        if source:
            clauses.append("source LIKE ?")
            values.append(f"%{source}%")
        if since:
            clauses.append("seen_at >= ?")
            values.append(since)
        if until:
            clauses.append("seen_at <= ?")
            values.append(until)
        if authorized is not None:
            clauses.append(("" if authorized else "NOT ") + "EXISTS (SELECT 1 FROM vehicles WHERE vehicles.plate = "
                           "COALESCE(recognitions.corrected_plate, recognitions.plate))")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._db() as db:
            total = db.execute("SELECT COUNT(*) FROM recognitions" + where, values).fetchone()[0]
            rows = [dict(row) for row in db.execute(
                "SELECT recognitions.*, (SELECT label FROM vehicles WHERE vehicles.plate = "
                "COALESCE(recognitions.corrected_plate, recognitions.plate)) AS vehicle_label FROM recognitions"
                + where + " ORDER BY seen_at DESC, id DESC LIMIT ? OFFSET ?", [*values, int(limit), int(offset)])]
        return {"total": total, "rows": rows}

    def review_queue(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.search(status="review", limit=limit)["rows"]

    def review(self, identity: int, action: str, plate: str = "") -> dict[str, Any]:
        """confirm, correct (with the right plate) or reject one record."""
        from app.anpr.formats import clean
        updates = {"confirm": ("confirmed", None), "reject": ("rejected", None), "correct": ("corrected", clean(plate))}
        if action not in updates:
            raise ValueError("Use confirm, correct or reject.")
        status, corrected = updates[action]
        if action == "correct" and not corrected:
            raise ValueError("Give the correct plate text.")
        with self._lock, self._db() as db:
            changed = db.execute("UPDATE recognitions SET status=?, corrected_plate=COALESCE(?, corrected_plate), reviewed_at=? "
                                 "WHERE id=?", (status, corrected, now().isoformat(), int(identity))).rowcount
            if not changed:
                raise ValueError(f"No record {identity}.")
            return dict(db.execute("SELECT * FROM recognitions WHERE id=?", (int(identity),)).fetchone())

    # ------------------------------------------------------------------ vehicles and events
    def vehicles(self) -> list[dict[str, Any]]:
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM vehicles ORDER BY plate")]

    def add_vehicle(self, plate: str, label: str = "", group: str = "", notes: str = "") -> dict[str, Any]:
        from app.anpr.formats import clean
        value = clean(plate)
        if not 2 <= len(value) <= 12:
            raise ValueError("A plate is 2 to 12 letters and digits.")
        with self._lock, self._db() as db:
            db.execute("INSERT INTO vehicles VALUES (?, ?, ?, ?, ?) ON CONFLICT(plate) DO UPDATE SET label=excluded.label, "
                       "group_name=excluded.group_name, notes=excluded.notes", (value, label, group, notes, now().isoformat()))
        return {"plate": value, "label": label, "group_name": group, "notes": notes}

    def remove_vehicle(self, plate: str) -> bool:
        from app.anpr.formats import clean
        with self._lock, self._db() as db:
            return bool(db.execute("DELETE FROM vehicles WHERE plate=?", (clean(plate),)).rowcount)

    def events(self, status: str = "open") -> list[dict[str, Any]]:
        with self._db() as db:
            query = "SELECT * FROM events" + (" WHERE status=?" if status else "") + " ORDER BY id DESC"
            return [dict(row) for row in db.execute(query, (status,) if status else ())]

    def resolve_event(self, identity: int, status: str = "resolved") -> None:
        if status not in ("resolved", "dismissed"):
            raise ValueError("An event is resolved or dismissed.")
        with self._lock, self._db() as db:
            if not db.execute("UPDATE events SET status=?, resolved_at=? WHERE id=?", (status, now().isoformat(), int(identity))).rowcount:
                raise ValueError(f"No event {identity}.")

    # ------------------------------------------------------------------ privacy
    def delete(self, identities: list[int]) -> int:
        """Delete records and their crops."""
        with self._lock, self._db() as db:
            rows = db.execute(f"SELECT id, crop_path FROM recognitions WHERE id IN ({','.join('?' * len(identities))})",
                              [int(item) for item in identities]).fetchall() if identities else []
            for row in rows:
                if row["crop_path"]:
                    Path(row["crop_path"]).unlink(missing_ok=True)
            if rows:
                marks, ids = ",".join("?" * len(rows)), [row["id"] for row in rows]
                db.execute(f"DELETE FROM events WHERE recognition_id IN ({marks})", ids)
                db.execute(f"DELETE FROM recognitions WHERE id IN ({marks})", ids)
        return len(rows)

    def delete_plate(self, plate: str) -> int:
        """Every record, crop and event of one plate."""
        from app.anpr.formats import clean
        value = clean(plate)
        with self._db() as db:
            identities = [row["id"] for row in db.execute("SELECT id FROM recognitions WHERE plate=? OR corrected_plate=?", (value, value))]
        count = self.delete(identities)
        with self._lock, self._db() as db:
            db.execute("DELETE FROM events WHERE plate=?", (value,))
        return count

    def purge(self, days: int | None = None) -> int:
        """Delete records older than the retention period (or `days`)."""
        keep = int(days if days is not None else self.settings()["retention_days"])
        cutoff = (now() - timedelta(days=keep)).isoformat()
        with self._db() as db:
            identities = [row["id"] for row in db.execute("SELECT id FROM recognitions WHERE seen_at < ?", (cutoff,))]
        count = self.delete(identities)
        # Empty day folders go too.
        if self.crops.exists():
            for folder in self.crops.iterdir():
                if folder.is_dir() and not any(folder.iterdir()):
                    shutil.rmtree(folder, ignore_errors=True)
        return count

    def delete_everything(self) -> None:
        with self._lock, self._db() as db:
            db.execute("DELETE FROM events")
            db.execute("DELETE FROM recognitions")
        shutil.rmtree(self.crops, ignore_errors=True)

    # ------------------------------------------------------------------ exports
    def export(self, target: Path | str, kind: str, **filters: Any) -> Path:
        """Matching records to JSON, CSV or a separate SQLite file."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = self.search(limit=1_000_000, **filters)["rows"]
        if kind == "json":
            target.write_text(json.dumps({"exported_at": now().isoformat(), "count": len(rows), "records": rows},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
        elif kind == "csv":
            columns = ["id", "plate", "corrected_plate", "status", "confidence", "detection_confidence", "readings", "region",
                       "format_valid", "source", "source_kind", "seen_at", "video_offset", "bbox", "vehicle_label", "crop_path"]
            with target.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        elif kind == "sqlite":
            target.unlink(missing_ok=True)
            with sqlite3.connect(target) as out:
                out.execute("CREATE TABLE recognitions (id INTEGER, plate TEXT, corrected_plate TEXT, status TEXT, confidence REAL, "
                            "detection_confidence REAL, readings INTEGER, region TEXT, format_valid INTEGER, source TEXT, "
                            "source_kind TEXT, seen_at TEXT, video_offset REAL, bbox TEXT, vehicle_label TEXT)")
                out.executemany("INSERT INTO recognitions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                [(row["id"], row["plate"], row["corrected_plate"], row["status"], row["confidence"],
                                  row["detection_confidence"], row["readings"], row["region"], row["format_valid"], row["source"],
                                  row["source_kind"], row["seen_at"], row["video_offset"], row["bbox"], row["vehicle_label"])
                                 for row in rows])
        else:
            raise ValueError("Export as json, csv or sqlite.")
        return target
