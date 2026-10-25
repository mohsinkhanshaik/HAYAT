"""Jobs, their readings and matches, and the owner's applications: data/jobs/jobs.db.

Each job gets a short number the owner can say ("job 12"); it never changes. Applications move
through saved → prepared → applied → interview → offer, or rejected / withdrawn, and every change
is kept with its time so the tracker shows the whole story.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

STATUSES = ("saved", "prepared", "opened", "applied", "interview", "offer", "rejected", "withdrawn")
FOLLOW_UP_DAYS = 7


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (number INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, source TEXT NOT NULL,
                    title TEXT NOT NULL, company TEXT, posted_at TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    hidden INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS matches (number INTEGER PRIMARY KEY, score INTEGER NOT NULL, verdict TEXT NOT NULL,
                    reading TEXT NOT NULL, match TEXT NOT NULL, computed_at TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS applications (number INTEGER PRIMARY KEY, status TEXT NOT NULL, folder TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, applied_at TEXT, channel TEXT, follow_up_at TEXT,
                    followed_up INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS application_events (id INTEGER PRIMARY KEY AUTOINCREMENT, number INTEGER NOT NULL,
                    at TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, fetched_at TEXT, read INTEGER, kept INTEGER, error TEXT);
                CREATE TABLE IF NOT EXISTS source_cooldowns (name TEXT PRIMARY KEY, retry_at TEXT NOT NULL, reason TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS matches_score ON matches(score DESC);
            """)

    def _db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ------------------------------------------------------------------ jobs
    def upsert(self, rows: list[dict[str, Any]]) -> list[int]:
        """Store jobs; returns the numbers of jobs seen for the first time."""
        new, stamp = [], now_iso()
        with self._lock, self._db() as db:
            for row in rows:
                found = db.execute("SELECT number FROM jobs WHERE key=?", (row["key"],)).fetchone()
                if found:
                    db.execute("UPDATE jobs SET last_seen=?, data=?, title=?, company=?, posted_at=? WHERE number=?",
                               (stamp, json.dumps(row, ensure_ascii=False), row["title"], row["company"], row["posted_at"], found["number"]))
                else:
                    cursor = db.execute("INSERT INTO jobs (key, source, title, company, posted_at, first_seen, last_seen, data) "
                                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (row["key"], row["source"], row["title"], row["company"],
                                                                           row["posted_at"], stamp, stamp, json.dumps(row, ensure_ascii=False)))
                    new.append(int(cursor.lastrowid))
        return new

    def duplicate(self, row: dict[str, Any]) -> bool:
        """True only for a different board with enough evidence to represent the same posting."""
        from app.jobs.sources import same_posting
        with self._db() as db:
            # A known native key must refresh its own posting even if an older duplicate exists.
            if db.execute("SELECT 1 FROM jobs WHERE key=?", (row["key"],)).fetchone():
                return False
            candidates = db.execute("SELECT data FROM jobs WHERE lower(title)=lower(?) AND lower(company)=lower(?)",
                                    (row["title"], row["company"])).fetchall()
        return any(same_posting(row, json.loads(candidate["data"])) for candidate in candidates)

    def job(self, number: int) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT j.*, m.score, m.verdict, m.reading, m.match, a.status AS application FROM jobs j "
                             "LEFT JOIN matches m ON m.number=j.number LEFT JOIN applications a ON a.number=j.number "
                             "WHERE j.number=?", (int(number),)).fetchone()
        if row is None:
            raise KeyError(f"There is no job {number}. Say “show my job matches” to see the numbers.")
        return self._row(row)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        value = {**json.loads(row["data"]), "number": row["number"], "first_seen": row["first_seen"], "hidden": bool(row["hidden"])}
        keys = row.keys()
        if "score" in keys and row["score"] is not None:
            value.update(score=row["score"], verdict=row["verdict"], reading=json.loads(row["reading"]), match=json.loads(row["match"]))
        if "application" in keys:
            value["application"] = row["application"]
        return value

    def numbers(self, include_hidden: bool = False) -> list[int]:
        with self._db() as db:
            return [row[0] for row in db.execute("SELECT number FROM jobs" + ("" if include_hidden else " WHERE hidden=0"))]

    def set_match(self, number: int, reading: dict[str, Any], match: dict[str, Any]) -> None:
        with self._lock, self._db() as db:
            db.execute("INSERT INTO matches (number, score, verdict, reading, match, computed_at) VALUES (?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT(number) DO UPDATE SET score=excluded.score, verdict=excluded.verdict, reading=excluded.reading, "
                       "match=excluded.match, computed_at=excluded.computed_at",
                       (number, match["score"], match["verdict"], json.dumps(reading, ensure_ascii=False),
                        json.dumps(match, ensure_ascii=False), now_iso()))

    def matches(self, min_score: int = 0, limit: int = 30, include_blocked: bool = False, include_hidden: bool = False,
                query: str = "") -> list[dict[str, Any]]:
        from itertools import islice
        return list(islice(self.iter_matches(min_score, include_blocked, include_hidden, query), max(1, min(int(limit), 500))))

    def iter_matches(self, min_score: int = 0, include_blocked: bool = False, include_hidden: bool = False,
                     query: str = "") -> Iterator[dict[str, Any]]:
        """Stream all saved matches so filters and pay ranking precede the display limit."""
        sql = ("SELECT j.*, m.score, m.verdict, m.reading, m.match, a.status AS application FROM matches m JOIN jobs j ON j.number=m.number "
               "LEFT JOIN applications a ON a.number=j.number WHERE m.score>=?")
        values: list[Any] = [int(min_score)]
        if not include_blocked:
            sql += " AND m.verdict!='not eligible'"
        if not include_hidden:
            sql += " AND j.hidden=0"
        if query:
            sql += " AND (j.title LIKE ? OR j.company LIKE ?)"
            values += [f"%{query}%", f"%{query}%"]
        sql += " ORDER BY m.score DESC, j.posted_at DESC, j.number DESC"
        with closing(self._db()) as db:
            cursor = db.execute(sql, values)
            while batch := cursor.fetchmany(200):
                for row in batch:
                    yield self._row(row)

    def unnotified(self, min_score: int) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT j.*, m.score, m.verdict, m.reading, m.match FROM matches m JOIN jobs j ON j.number=m.number "
                              "WHERE m.notified=0 AND m.score>=? AND m.verdict!='not eligible' AND j.hidden=0 ORDER BY m.score DESC",
                              (int(min_score),)).fetchall()
        return [self._row(row) for row in rows]

    def mark_notified(self, numbers: list[int]) -> None:
        with self._lock, self._db() as db:
            db.executemany("UPDATE matches SET notified=1 WHERE number=?", [(number,) for number in numbers])

    def hide(self, number: int, hidden: bool = True) -> None:
        self.job(number)
        with self._lock, self._db() as db:
            db.execute("UPDATE jobs SET hidden=? WHERE number=?", (int(hidden), int(number)))

    def counts(self) -> dict[str, int]:
        with self._db() as db:
            jobs = db.execute("SELECT COUNT(*) FROM jobs WHERE hidden=0").fetchone()[0]
            verdicts = dict(db.execute("SELECT verdict, COUNT(*) FROM matches GROUP BY verdict").fetchall())
            applications = dict(db.execute("SELECT status, COUNT(*) FROM applications GROUP BY status").fetchall())
        return {"jobs": jobs, **{f"verdict_{key}": value for key, value in verdicts.items()},
                **{f"applications_{key}": value for key, value in applications.items()}}

    def forget_old(self, days: int = 60) -> int:
        """Jobs not seen for `days` and never applied to are dropped; applications keep their job."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock, self._db() as db:
            old = [row[0] for row in db.execute("SELECT number FROM jobs WHERE last_seen<? AND number NOT IN (SELECT number FROM applications)",
                                                (cutoff,))]
            db.executemany("DELETE FROM matches WHERE number=?", [(number,) for number in old])
            db.executemany("DELETE FROM jobs WHERE number=?", [(number,) for number in old])
        return len(old)

    # ------------------------------------------------------------------ sources
    def defer_source(self, name: str, retry_at: str, reason: str) -> None:
        # ATS boards share a provider's request allowance.
        name = name.split(":", 1)[0]
        with self._lock, self._db() as db:
            db.execute("INSERT INTO source_cooldowns VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                       "retry_at=MAX(source_cooldowns.retry_at, excluded.retry_at), reason=excluded.reason", (name, retry_at, reason))

    def source_cooldowns(self) -> dict[str, dict[str, Any]]:
        with self._db() as db:
            return {row["name"]: dict(row) for row in db.execute("SELECT * FROM source_cooldowns WHERE retry_at>?", (now_iso(),))}

    def source_state(self) -> dict[str, dict[str, Any]]:
        with self._db() as db:
            return {row["name"]: dict(row) for row in db.execute("SELECT * FROM sources")}

    def record_source(self, name: str, read: int, kept: int, error: str = "") -> None:
        with self._lock, self._db() as db:
            db.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET fetched_at=excluded.fetched_at, "
                       "read=excluded.read, kept=excluded.kept, error=excluded.error", (name, now_iso(), read, kept, error or None))

    # ------------------------------------------------------------------ applications
    def application(self, number: int) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM applications WHERE number=?", (int(number),)).fetchone()
            if row is None:
                return None
            events = [dict(item) for item in db.execute("SELECT at, status, note FROM application_events WHERE number=? ORDER BY id",
                                                         (int(number),))]
        return {**dict(row), "events": events}

    def set_status(self, number: int, status: str, note: str = "", folder: str | None = None, channel: str | None = None) -> dict[str, Any]:
        if status not in STATUSES:
            raise ValueError("Status is one of: " + ", ".join(STATUSES) + ".")
        self.job(number)
        stamp = now_iso()
        with self._lock, self._db() as db:
            found = db.execute("SELECT * FROM applications WHERE number=?", (int(number),)).fetchone()
            if found is None:
                db.execute("INSERT INTO applications (number, status, created_at, updated_at) VALUES (?, ?, ?, ?)", (int(number), status, stamp, stamp))
            db.execute("UPDATE applications SET status=?, updated_at=? WHERE number=?", (status, stamp, int(number)))
            if folder is not None:
                db.execute("UPDATE applications SET folder=? WHERE number=?", (folder, int(number)))
            if channel is not None:
                db.execute("UPDATE applications SET channel=? WHERE number=?", (channel, int(number)))
            if status == "applied":
                follow = (datetime.now(UTC) + timedelta(days=FOLLOW_UP_DAYS)).isoformat(timespec="seconds")
                db.execute("UPDATE applications SET applied_at=COALESCE(applied_at, ?), follow_up_at=COALESCE(follow_up_at, ?) WHERE number=?",
                           (stamp, follow, int(number)))
            if note:
                db.execute("UPDATE applications SET notes=TRIM(notes || char(10) || ?) WHERE number=?", (note[:500], int(number)))
            db.execute("INSERT INTO application_events (number, at, status, note) VALUES (?, ?, ?, ?)", (int(number), stamp, status, note[:500]))
        return self.application(number)

    def applications(self, status: str = "") -> list[dict[str, Any]]:
        sql = ("SELECT a.*, j.title, j.company, j.data, m.score FROM applications a JOIN jobs j ON j.number=a.number "
               "LEFT JOIN matches m ON m.number=a.number")
        values: list[Any] = []
        if status:
            sql += " WHERE a.status=?"
            values.append(status)
        sql += " ORDER BY a.updated_at DESC"
        with self._db() as db:
            rows = db.execute(sql, values).fetchall()
        out = []
        for row in rows:
            data = json.loads(row["data"])
            out.append({key: row[key] for key in row.keys() if key != "data"} | {"url": data.get("url"), "source_name": data.get("source_name")})
        return out

    def due_follow_ups(self) -> list[dict[str, Any]]:
        stamp = now_iso()
        return [row for row in self.applications("applied") if row["follow_up_at"] and row["follow_up_at"] <= stamp and not row["followed_up"]]

    def mark_followed_up(self, number: int) -> None:
        with self._lock, self._db() as db:
            db.execute("UPDATE applications SET followed_up=1 WHERE number=?", (int(number),))
