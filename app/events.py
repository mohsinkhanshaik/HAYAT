from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


LIFECYCLE_EVENT_TYPES = {
    # Runtime and services
    "RUNTIME_STARTING", "RUNTIME_READY", "RUNTIME_DEGRADED", "RUNTIME_STOPPING",
    "SERVICE_STARTING", "SERVICE_HEALTHY", "SERVICE_DEGRADED", "SERVICE_FAILED",
    "SERVICE_RESTARTING", "SERVICE_RECOVERED",
    # Sessions and turns
    "SESSION_STARTED", "SESSION_ENDED",
    "TURN_RECEIVED", "TURN_NORMALIZED", "TURN_ROUTED", "TURN_RUNNING",
    "TURN_WAITING_APPROVAL", "TURN_COMPLETED", "TURN_FAILED", "TURN_CANCELLED",
    "TURN_SUPERSEDED",
    # Input and output
    "USER_TEXT", "USER_VOICE_FINAL", "USER_CORRECTION",
    "ASSISTANT_STARTED", "ASSISTANT_FINAL",
    # Work
    "TASK_CREATED", "TASK_PROGRESS", "TASK_WAITING_APPROVAL", "TASK_CANCELLED",
    "TASK_SUPERSEDED",
    "TOOL_STARTED", "TOOL_COMPLETED", "TOOL_FAILED",
    'GUI_TOOL_REQUESTED', 'GUI_TOOL_REFUSED',
    "MODEL_STARTED", "MODEL_COMPLETED",
    "RESEARCH_STARTED", "RESEARCH_RESULT", 'INTELLIGENCE_ALERT',
    # Perception
    "CAMERA_OBSERVATION", "CAMERA_FAILED", "VOICE_STATE", "VOICE_FAILED",
    # Wi-Fi sensing (app/sensing.py): the movement level changed, or sensing paused for privacy.
    "SENSING_MOTION",
    # Governance and health
    "APPROVAL_REQUIRED", "APPROVAL_ACCEPTED", "APPROVAL_DENIED", "APPROVAL_EXPIRED",
    "APPROVAL_CANCELLED",
    "MEMORY_CONFLICT", "FAILURE_RECORDED", "NETWORK_CHANGED",
    "PROJECT_CODE_CHANGED", "RESTART_REQUIRED",
    # Answer integrity. The model produced a reply that misrepresented the
    # system -- claimed to be a different assistant, disowned a real
    # capability, or declined a subject it should answer -- and a guard caught
    # it and asked again. Publishing these was crashing the very turn the
    # guard was correcting, because the events were never in this taxonomy and
    # publish() raises on an unknown type. Recording them is also how the count
    # of such corrections becomes visible instead of invisible.
    "IDENTITY_LEAK", "CAPABILITY_DENIAL", "TOPIC_REFUSAL",
    # Authority. Routine allows are not published -- ordinary local work is the
    # common case and would drown the bus -- but anything withheld, and every
    # change to what is permitted, is recorded.
    "AUTHORITY_DENIED", "AUTHORITY_APPROVAL_REQUIRED",
    "GRANT_CREATED", "GRANT_REVOKED", "GRANT_EXPIRED", "PRIVACY_MODE_CHANGED",
    # Integrations and proactive automation. Secret values are never payloads;
    # these events carry identifiers, scopes and outcomes only.
    "SECRET_STORED", "SECRET_DELETED", "SECRET_LEASE_ISSUED", "SECRET_LEASE_USED",
    "CONNECTOR_INSTALLED", "CONNECTOR_ENABLED", "CONNECTOR_DISABLED", "CONNECTOR_CALLED",
    "MCP_SERVER_REGISTERED", "MCP_TOOL_CALLED",
    "BROWSER_BRIDGE_STARTED", "BROWSER_BRIDGE_CONNECTED", "BROWSER_BRIDGE_DISCONNECTED",
    "BROWSER_PAGE_SNAPSHOT", "PROACTIVE_RULE_TRIGGERED", "PROACTIVE_RULE_SKIPPED",
}

EVENT_TYPES = {
    "TASK_STARTED", "TASK_FAILED", "TASK_COMPLETED", "MODEL_LOADED", "MODEL_FAILED",
    "CAMERA_CHANGED", "SCREEN_CHANGED", "FILE_CHANGED", "RESOURCE_ALERT",
    "KNOWLEDGE_STALE", "VOICE_COMMAND",
} | LIFECYCLE_EVENT_TYPES

# Losing one of these would misrepresent what the system actually did, so a
# full queue blocks the publisher rather than dropping them. Everything else
# is telemetry and may be shed under sustained overload.
CRITICAL_EVENT_TYPES = {
    "TURN_COMPLETED", "TURN_FAILED", "TURN_CANCELLED", "TURN_SUPERSEDED",
    "TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED", "TASK_SUPERSEDED",
    "APPROVAL_REQUIRED", "APPROVAL_ACCEPTED", "APPROVAL_DENIED", "APPROVAL_EXPIRED",
    "APPROVAL_CANCELLED", "FAILURE_RECORDED", "ASSISTANT_FINAL", "SERVICE_FAILED", "RESTART_REQUIRED",
    # Losing a record of what authority was granted, revoked, or withheld would
    # make the audit trail lie by omission.
    "AUTHORITY_DENIED", "AUTHORITY_APPROVAL_REQUIRED", "GRANT_CREATED",
    "GRANT_REVOKED", "PRIVACY_MODE_CHANGED",
    "SECRET_STORED", "SECRET_DELETED", "SECRET_LEASE_ISSUED", "SECRET_LEASE_USED",
    "CONNECTOR_INSTALLED", "CONNECTOR_ENABLED", "CONNECTOR_DISABLED",
    "MCP_SERVER_REGISTERED", "PROACTIVE_RULE_TRIGGERED",
}


class EventBus:
    """Durable local event bus. Payloads contain operational data, never chain-of-thought.

    Ordering and subscriber dispatch happen synchronously on the publishing
    thread; the SQLite write is handed to a single background writer. The
    previous design wrote synchronously with a fresh connection per publish,
    which measured 230 events/sec with a p95 of 77ms and a worst case of
    4.24 SECONDS under five concurrent producers -- a camera or voice thread
    could stall for seconds on lock contention. Sequencing stays on the
    publishing thread so ordering is still assigned deterministically at
    publish time rather than at flush time.
    """

    def __init__(self, path: Path | None = None, *, runtime_id: str | None = None, queue_size: int = 10000, durable: bool = True) -> None:
        self.path = path or PROJECT_ROOT / "data/memory/events.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_id = runtime_id or f"runtime-{uuid.uuid4().hex[:12]}"
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._lock = threading.RLock()
        self._sequence = 0
        self._durable = durable
        self._dropped = 0
        self._queue: queue.Queue = queue.Queue(maxsize=max(16, queue_size))
        self._stopped = threading.Event()
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp TEXT NOT NULL,event_type TEXT NOT NULL,source TEXT NOT NULL,payload_json TEXT NOT NULL,handled INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type,timestamp)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(events)").fetchall()}
            for name, declaration in {
                "sequence": "INTEGER", "runtime_id": "TEXT", "session_id": "TEXT",
                "turn_id": "TEXT", "task_id": "TEXT", "final": "INTEGER NOT NULL DEFAULT 0",
                "supersedes": "TEXT",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE events ADD COLUMN {name} {declaration}")
        self._writer = threading.Thread(target=self._drain, name="ai-event-writer", daemon=True)
        self._writer.start()

    def publish(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        final: bool = False,
        supersedes: str | None = None,
    ) -> int:
        name = event_type.upper()
        if name not in EVENT_TYPES:
            raise ValueError(f"Unsupported event type: {event_type}")
        safe_payload = self._sanitize(payload or {})
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            callbacks = list(self._subscribers.get(name, []))
        record = {
            "id": sequence, "sequence": sequence, "event_id": f"evt-{uuid.uuid4().hex[:12]}",
            "timestamp": datetime.now(UTC).isoformat(), "monotonic_time": time.monotonic(),
            "event_type": name, "source": source, "payload": safe_payload,
            "runtime_id": self.runtime_id, "session_id": session_id,
            "turn_id": turn_id, "task_id": task_id, "final": bool(final),
            "supersedes": supersedes,
        }
        if self._durable:
            self._enqueue(record, critical=name in CRITICAL_EVENT_TYPES)
        for callback in callbacks:
            # One broken subscriber (a closing UI, a crashed renderer) must not
            # stop the others or the publisher.
            try:
                callback(record)
            except Exception:
                log_swallowed(_log, "event subscriber callback")
        return sequence

    def _enqueue(self, record: dict[str, Any], *, critical: bool) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            if critical:
                self._queue.put(record)  # block rather than lose a decision-bearing event
            else:
                with self._lock:
                    self._dropped += 1

    def _drain(self) -> None:
        while True:
            record = self._queue.get()
            if record is None:
                self._queue.task_done()
                return
            batch = [record]
            while len(batch) < 256:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    # Shutdown sentinel mid-batch: still persist what we hold,
                    # and account for every item so flush() cannot hang.
                    self._write(batch)
                    for _ in batch:
                        self._queue.task_done()
                    self._queue.task_done()
                    return
                batch.append(item)
            self._write(batch)
            for _ in batch:
                self._queue.task_done()

    def _write(self, batch: list[dict[str, Any]]) -> None:
        try:
            with self._connect() as db:
                db.executemany(
                    "INSERT INTO events(timestamp,event_type,source,payload_json,sequence,runtime_id,session_id,turn_id,task_id,final,supersedes)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            item["timestamp"], item["event_type"], item["source"],
                            json.dumps(item["payload"], default=str), item["sequence"],
                            item["runtime_id"], item["session_id"], item["turn_id"],
                            item["task_id"], int(item["final"]), item["supersedes"],
                        )
                        for item in batch
                    ],
                )
        except Exception:
            # Durability is best-effort; losing the log must never take down
            # the runtime that produced it.
            log_swallowed(_log, "writing events to the durable event log", level=logging.WARNING)

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until queued events are actually persisted.

        Waiting on queue.empty() is not enough: the queue drains the moment the
        writer picks a batch up, which is before that batch reaches SQLite.
        unfinished_tasks only returns to zero once task_done() has run for
        every item, which happens after the write.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.002)
        return self._queue.unfinished_tasks == 0

    def close(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        self.flush()
        self._queue.put(None)
        self._writer.join(timeout=3)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "runtime_id": self.runtime_id, "sequence": self._sequence,
                "queue_depth": self._queue.qsize(), "dropped_non_critical": self._dropped,
                "subscribers": sum(len(items) for items in self._subscribers.values()),
            }

    def subscribe(self, event_type: str, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Subscribe and return an unsubscribe callable."""
        name = event_type.upper()
        if name not in EVENT_TYPES: raise ValueError(name)
        with self._lock: self._subscribers.setdefault(name, []).append(callback)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._subscribers.get(name, [])
                if callback in handlers:
                    handlers.remove(callback)

        return unsubscribe

    def recent(self, limit: int = 50, event_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM events"; parameters: tuple[Any, ...] = ()
        if event_type: query += " WHERE event_type=?"; parameters = (event_type.upper(),)
        with self._connect() as db: rows = db.execute(query + " ORDER BY id DESC LIMIT ?", parameters + (max(1, min(limit, 500)),)).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    @staticmethod
    def _sanitize(value: Any) -> Any:
        """Key-aware and value-aware redaction before an event is persisted."""
        from app.safety.sensitive import redact
        return redact(value)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path); connection.row_factory = sqlite3.Row
        try:
            with connection: yield connection
        finally: connection.close()
