"""Durable event-to-goal rules with cooldowns and background authority."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from app.config import PROJECT_ROOT
from app.events import EVENT_TYPES
from app.execution import execution_scope
from app.safety.authority import Principal
from app.safety.sensitive import redact


@dataclass(slots=True)
class EventRule:
    event_type: str
    goal: str
    filters: dict[str, Any] = field(default_factory=dict)
    cooldown_seconds: int = 300
    maximum_runs_per_hour: int = 4
    enabled: bool = False
    action: str = "suggest"
    rule_id: str = field(default_factory=lambda: f"rule-{uuid.uuid4().hex[:10]}")

    def validate(self) -> None:
        self.event_type = self.event_type.upper()
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"Unsupported event type: {self.event_type}")
        if self.event_type in {"PROACTIVE_RULE_TRIGGERED", "PROACTIVE_RULE_SKIPPED"}:
            raise ValueError("A proactive rule cannot trigger on its own lifecycle events.")
        if self.action not in {"suggest", "run_goal"}:
            raise ValueError("Rule action must be suggest or run_goal.")
        if not self.goal.strip():
            raise ValueError("Rule goal is required.")
        self.cooldown_seconds = max(1, min(int(self.cooldown_seconds), 86400))
        self.maximum_runs_per_hour = max(1, min(int(self.maximum_runs_per_hour), 60))


class EventTriggerEngine:
    def __init__(self, bus: Any, *, path: Path | None = None,
                 run_goal: Callable[[str], Any] | None = None,
                 suggest: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.bus, self.run_goal, self.suggest = bus, run_goal, suggest
        self.path = path or PROJECT_ROOT / "data/automation/event_rules.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._unsubscribers: list[Callable[[], None]] = []
        self._recent: dict[str, list[float]] = {}
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS event_rules(
                rule_id TEXT PRIMARY KEY, rule_json TEXT NOT NULL, enabled INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_run_at TEXT,
                run_count INTEGER NOT NULL DEFAULT 0)"""
            )

    def add(self, rule: EventRule | dict[str, Any]) -> dict[str, Any]:
        item = rule if isinstance(rule, EventRule) else EventRule(**rule)
        item.validate()
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO event_rules(rule_id,rule_json,enabled,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(rule_id) DO UPDATE SET rule_json=excluded.rule_json,enabled=excluded.enabled,updated_at=excluded.updated_at",
                (item.rule_id, json.dumps(redact(asdict(item)), sort_keys=True), int(item.enabled), now, now),
            )
        if self._unsubscribers:
            self.stop(); self.start()
        return self.describe(item.rule_id)

    def get(self, rule_id: str) -> EventRule:
        with self._connect() as db:
            row = db.execute("SELECT rule_json,enabled FROM event_rules WHERE rule_id=?", (rule_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown event rule: {rule_id}")
        item = EventRule(**json.loads(row["rule_json"]))
        item.enabled = bool(row["enabled"])
        return item

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            ids = [row[0] for row in db.execute("SELECT rule_id FROM event_rules ORDER BY created_at")]
        return [self.describe(item) for item in ids]

    def describe(self, rule_id: str) -> dict[str, Any]:
        item = self.get(rule_id)
        with self._connect() as db:
            row = db.execute("SELECT last_run_at,run_count FROM event_rules WHERE rule_id=?", (rule_id,)).fetchone()
        return asdict(item) | {"last_run_at": row["last_run_at"], "run_count": row["run_count"]}

    def set_enabled(self, rule_id: str, enabled: bool) -> dict[str, Any]:
        self.get(rule_id)
        with self._connect() as db:
            db.execute("UPDATE event_rules SET enabled=?,updated_at=? WHERE rule_id=?",
                       (int(enabled), datetime.now(UTC).isoformat(), rule_id))
        if self._unsubscribers:
            self.stop(); self.start()
        return self.describe(rule_id)

    def start(self) -> None:
        if self._unsubscribers:
            return
        types = sorted({item["event_type"] for item in self.list() if item["enabled"]})
        self._unsubscribers = [self.bus.subscribe(event_type, self._on_event) for event_type in types]

    def stop(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def _on_event(self, event: dict[str, Any]) -> None:
        for raw in self.list():
            if not raw["enabled"] or raw["event_type"] != event["event_type"]:
                continue
            rule = self.get(raw["rule_id"])
            if not self._matches(rule.filters, event):
                continue
            allowed, reason = self._rate_allowed(rule)
            if not allowed:
                self.bus.publish("PROACTIVE_RULE_SKIPPED", "event_trigger_engine",
                                 {"rule_id": rule.rule_id, "reason": reason})
                continue
            self._record_run(rule.rule_id)
            self.bus.publish("PROACTIVE_RULE_TRIGGERED", "event_trigger_engine",
                             {"rule_id": rule.rule_id, "source_event": event["event_type"], "action": rule.action})
            if rule.action == "suggest":
                if self.suggest is not None:
                    self.suggest(rule.goal, event)
            elif self.run_goal is not None:
                threading.Thread(target=self._run, args=(rule, event),
                                 name=f"hayat-event-{rule.rule_id}", daemon=True).start()

    def _run(self, rule: EventRule, event: dict[str, Any]) -> None:
        # Proactive work has the background learner's narrow authority. It can
        # inspect and research; it cannot impersonate the owner or mutate an
        # external account merely because an event fired.
        with execution_scope(principal=Principal.BACKGROUND_LEARNER.value,
                             explicitly_requested=False, turn_id=f"event:{event['event_id']}"):
            self.run_goal(rule.goal)

    def _rate_allowed(self, rule: EventRule) -> tuple[bool, str]:
        now = time.monotonic()
        with self._lock:
            history = [item for item in self._recent.get(rule.rule_id, []) if now - item < 3600]
            if history and now - history[-1] < rule.cooldown_seconds:
                self._recent[rule.rule_id] = history
                return False, "cooldown"
            if len(history) >= rule.maximum_runs_per_hour:
                self._recent[rule.rule_id] = history
                return False, "hourly limit"
            history.append(now)
            self._recent[rule.rule_id] = history
        return True, ""

    def _record_run(self, rule_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE event_rules SET last_run_at=?,run_count=run_count+1 WHERE rule_id=?",
                       (datetime.now(UTC).isoformat(), rule_id))

    @staticmethod
    def _matches(filters: dict[str, Any], event: dict[str, Any]) -> bool:
        payload = event.get("payload") or {}
        return all(payload.get(key) == value for key, value in filters.items())

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def register_event_trigger_tools(registry: Any, engine: EventTriggerEngine) -> None:
    registry.register("proactive_rules", "List event-driven proactive rules and their bounded run counts.",
                      {"type": "object", "properties": {}}, "low", "system_read", engine.list)
    registry.register(
        "add_proactive_rule", "Create a disabled event-driven rule for owner review.",
        {"type": "object", "properties": {"event_type": {"type": "string"}, "goal": {"type": "string"},
         "filters": {"type": "object", "additionalProperties": True},
         "cooldown_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
         "maximum_runs_per_hour": {"type": "integer", "minimum": 1, "maximum": 60},
         "action": {"type": "string", "enum": ["suggest", "run_goal"]}}, "required": ["event_type", "goal"]},
        "high", "automation_manage",
        lambda event_type, goal, filters=None, cooldown_seconds=300, maximum_runs_per_hour=4, action="suggest":
        engine.add(EventRule(event_type, goal, filters or {}, cooldown_seconds,
                             maximum_runs_per_hour, False, action)),
    )
