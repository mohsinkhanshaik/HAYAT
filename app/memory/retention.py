"""Spaced retention practice: can HAYAT still recall what it was told, from the words that matter in it?

A memory that is stored but no longer comes back when its own key words are asked for is, for every practical
purpose, forgotten. Nothing checked for that: recall was measured only when a question happened to need the memory.

Each review takes one durable memory, builds a cue from its rarest content words (the words that set it apart, as
a person would cue a memory -- "sister birthday", not the whole sentence), runs the same retrieval the assistant
uses, and records where the memory ranked:

* recalled in the top 5 -> the next review waits longer (1, 3, 7, 21, 60, 180 days);
* missed -> the interval resets to one day, and a memory missing the active model's vector is re-embedded;
* missed three reviews running, never used, low importance -> listed as a *forgetting candidate*. Nothing is
  deleted: forgetting the owner's memories is his decision (``memory_forget``), so the list is shown, not acted on.

Reviews run in the background learner's maintenance slot (idle machine, owner not waiting) and read only; the one
write to the memory database is the re-embedding of a missed memory. ``report()`` gives retention (share recalled
in the top 5) and retrieval relevance (mean reciprocal rank) over recent reviews -- both measured, never estimated.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

INTERVALS_DAYS = (1, 3, 7, 21, 60, 180)
TOP_K = 5
DURABLE_TYPES = ("semantic", "episodic", "procedural", "reflection", "project", "learning")
_STOP = frozenset("""
the a an and or but of to in on at by for from with about into is are was were be been being it its this that these
those i me my we our you your he she they them his her their what which who when where why how do does did have has
had will would can could should may might must not no yes so if then than there here just also very more most some
any all each other such only own same again still even now today task goal result results tool tools step steps
success failed failure true false none null user assistant hayat owner mohsin request reply answer
""".split())


def _words(text: str) -> list[str]:
    return [word for word in re.findall(r"[^\W\d_][\w'-]{2,}", str(text or "").lower()) if word not in _STOP]


def cue_for(content: str, document_frequency: Counter, documents: int, size: int = 4) -> str:
    """The memory's rarest content words, in the order they appear: what would make a person recall it."""
    words = list(dict.fromkeys(_words(content)))
    if not words:
        return ""
    rarity = {word: math.log((documents + 1) / (document_frequency.get(word, 0) + 1)) for word in words}
    chosen = set(sorted(words, key=lambda word: (-rarity[word], words.index(word)))[:size])
    return " ".join(word for word in words if word in chosen)


class RetentionPractice:
    SOURCE = "retention"

    def __init__(self, memory: Any, path: Path | str) -> None:
        self.memory = memory
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ready = False
        #: Set by the runtime: the event bus.
        self.events: Any = None

    def _emit(self, event_type: str, payload: dict[str, Any], **scope: Any) -> None:
        """Report activity on the event bus (ids and counts only, never text), so the Brain view shows it."""
        events = getattr(self, "events", None)
        if events is None:
            return
        try:
            events.publish(event_type, self.SOURCE, payload, **scope)
        except Exception:
            log_swallowed(_log, f"publishing {event_type}")

    def status(self) -> dict[str, Any]:
        return self.report()

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schedule(
                    memory_id INTEGER PRIMARY KEY, step INTEGER NOT NULL DEFAULT 0, due_at TEXT NOT NULL,
                    reviews INTEGER NOT NULL DEFAULT 0, recalled INTEGER NOT NULL DEFAULT 0,
                    misses_in_row INTEGER NOT NULL DEFAULT 0, last_rank INTEGER, last_at TEXT);
                CREATE TABLE IF NOT EXISTS reviews(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL, at TEXT NOT NULL,
                    cue TEXT NOT NULL, rank INTEGER, recalled INTEGER NOT NULL, milliseconds REAL NOT NULL);
            """)
            self._ready = True
        return db

    def _document_frequency(self, sample: int = 3000) -> tuple[Counter, int]:
        with self.memory.connect() as db:
            rows = db.execute("SELECT content FROM memories ORDER BY id DESC LIMIT ?", (sample,)).fetchall()
        frequency: Counter = Counter()
        for row in rows:
            frequency.update(set(_words(row[0])))
        return frequency, len(rows)

    def _due(self, limit: int, now: datetime) -> list[sqlite3.Row]:
        marks = ",".join("?" for _ in DURABLE_TYPES)
        with self.memory.connect() as db:
            candidates = db.execute(
                f"SELECT id, content, importance, memory_type FROM memories WHERE memory_type IN ({marks}) "  # noqa: S608
                "AND length(content) BETWEEN 12 AND 4000 ORDER BY importance DESC, id DESC LIMIT 2000",
                DURABLE_TYPES).fetchall()
        with self._lock, closing(self._db()) as db:
            scheduled = {row["memory_id"]: row["due_at"] for row in db.execute("SELECT memory_id, due_at FROM schedule")}
        due = [row for row in candidates if row["id"] in scheduled and scheduled[row["id"]] <= now.isoformat()]
        new = [row for row in candidates if row["id"] not in scheduled]
        return (due + new)[:limit]

    def review(self, limit: int = 12, *, now: datetime | None = None) -> dict[str, Any]:
        """Review up to ``limit`` memories that are due (or never reviewed); never raises."""
        now = now or datetime.now(UTC)
        try:
            items = self._due(limit, now)
            if not items:
                return {"reviewed": 0, "recalled": 0}
            frequency, documents = self._document_frequency()
        except sqlite3.Error:
            log_swallowed(_log, "choosing memories for retention practice")
            return {"reviewed": 0, "recalled": 0, "error": "memory database unavailable"}
        reviewed = recalled = 0
        missed_without_vector: list[int] = []
        for item in items:
            cue = cue_for(item["content"], frequency, documents)
            if not cue:
                continue
            started = time.perf_counter()
            try:
                found = self.memory.retrieve(cue, limit=TOP_K)
            except Exception:
                log_swallowed(_log, "retrieving during retention practice")
                continue
            milliseconds = (time.perf_counter() - started) * 1000
            # An identical copy coming back is the memory recalled: a duplicate outranking it is not a lapse.
            wanted = " ".join(str(item["content"]).split()).lower()
            rank = next((position for position, row in enumerate(found, start=1)
                         if int(row.get("id", 0)) == int(item["id"])
                         or " ".join(str(row.get("content", "")).split()).lower() == wanted), None)
            ok = rank is not None
            reviewed += 1
            recalled += ok
            if not ok:
                missed_without_vector.append(int(item["id"]))
            self._record(int(item["id"]), cue, rank, ok, milliseconds, now)
        if missed_without_vector:
            self._reembed(missed_without_vector)
        if reviewed:
            self._emit("MEMORY_REVIEWED", {"reviewed": reviewed, "recalled": recalled,
                                           "missed": reviewed - recalled})
        return {"reviewed": reviewed, "recalled": recalled}

    def _record(self, memory_id: int, cue: str, rank: int | None, ok: bool, milliseconds: float, now: datetime) -> None:
        with self._lock, closing(self._db()) as db, db:
            row = db.execute("SELECT * FROM schedule WHERE memory_id=?", (memory_id,)).fetchone()
            step = 0 if row is None else int(row["step"])
            step = min(step + 1, len(INTERVALS_DAYS) - 1) if ok and row is not None else (0 if not ok else step)
            due = now + timedelta(days=INTERVALS_DAYS[step])
            misses = 0 if ok else (0 if row is None else int(row["misses_in_row"])) + 1
            db.execute(
                "INSERT INTO schedule(memory_id,step,due_at,reviews,recalled,misses_in_row,last_rank,last_at) "
                "VALUES(?,?,?,1,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET step=excluded.step, due_at=excluded.due_at, "
                "reviews=reviews+1, recalled=recalled+excluded.recalled, misses_in_row=excluded.misses_in_row, "
                "last_rank=excluded.last_rank, last_at=excluded.last_at",
                (memory_id, step, due.isoformat(), int(ok), misses, rank, now.isoformat()))
            db.execute("INSERT INTO reviews(memory_id,at,cue,rank,recalled,milliseconds) VALUES(?,?,?,?,?,?)",
                       (memory_id, now.isoformat(), cue, rank, int(ok), round(milliseconds, 2)))

    def _reembed(self, memory_ids: list[int]) -> None:
        """A missed memory that has no vector from the active model gets one (the existing re-index path)."""
        reindex = getattr(self.memory, "reindex_embeddings", None)
        provider = getattr(self.memory, "embedding_provider", None)
        if reindex is None or provider is None or not getattr(provider, "available", False):
            return
        try:
            reindex(batch_size=min(32, max(1, len(memory_ids))))
        except Exception:
            log_swallowed(_log, "re-embedding memories missed in retention practice")

    def forgetting_candidates(self, limit: int = 20) -> list[dict[str, Any]]:
        """Missed three reviews running, low importance, never helped a recall: shown to the owner, never deleted."""
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT memory_id, reviews, misses_in_row FROM schedule WHERE misses_in_row>=3 "
                              "ORDER BY misses_in_row DESC LIMIT 200").fetchall()
        if not rows:
            return []
        ids = [int(row["memory_id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        with self.memory.connect() as db:
            details = {int(row["id"]): row for row in db.execute(
                f"SELECT id, content, importance, memory_type FROM memories WHERE id IN ({marks})", ids)}  # noqa: S608
        found = []
        for row in rows:
            memory = details.get(int(row["memory_id"]))
            if memory is None or float(memory["importance"]) >= 0.6:
                continue
            found.append({"memory_id": int(row["memory_id"]), "type": memory["memory_type"],
                          "importance": float(memory["importance"]), "misses_in_row": int(row["misses_in_row"]),
                          "text": str(memory["content"])[:140]})
        return found[:limit]

    def report(self, last: int = 300) -> dict[str, Any]:
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT rank, recalled, milliseconds FROM reviews ORDER BY id DESC LIMIT ?", (last,)).fetchall()
            scheduled = int(db.execute("SELECT COUNT(*) FROM schedule").fetchone()[0])
            mastered = int(db.execute("SELECT COUNT(*) FROM schedule WHERE step>=3").fetchone()[0])
            due = int(db.execute("SELECT COUNT(*) FROM schedule WHERE due_at<=?", (datetime.now(UTC).isoformat(),)).fetchone()[0])
        if not rows:
            return {"reviews": 0, "retention": None, "mean_reciprocal_rank": None, "scheduled": scheduled,
                    "due": due, "held_three_reviews": mastered, "median_ms": None}
        ranks = [row["rank"] for row in rows]
        times = sorted(float(row["milliseconds"]) for row in rows)
        return {"reviews": len(rows), "retention": round(sum(int(row["recalled"]) for row in rows) / len(rows), 3),
                "mean_reciprocal_rank": round(sum(1 / rank for rank in ranks if rank) / len(rows), 3),
                "scheduled": scheduled, "due": due, "held_three_reviews": mastered,
                "median_ms": round(times[len(times) // 2], 1),
                "forgetting_candidates": len(self.forgetting_candidates(200))}


def register_retention_tools(registry: Any, practice: RetentionPractice) -> None:
    def run(action: str = "report", limit: int = 12) -> dict[str, Any]:
        if action == "review":
            return {"review": practice.review(max(1, min(int(limit), 50))), "report": practice.report()}
        if action == "forgetting_candidates":
            return {"candidates": practice.forgetting_candidates(max(1, min(int(limit), 50))),
                    "note": "Nothing is deleted automatically; forget a memory with memory_forget if you agree."}
        return practice.report()

    registry.register(
        "memory_retention",
        "Spaced retention practice on HAYAT's own memories: whether each memory still comes back from its own key "
        "words (retention and mean reciprocal rank over recent reviews), what is due for review, and low-value "
        "memories it keeps failing to recall (listed, never deleted). action=review runs a few reviews now.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["report", "review", "forgetting_candidates"]},
            "limit": {"type": "integer"}}, "additionalProperties": False},
        "low", "system_read", run)
