"""Durable storage for owner mode, capability grants, and the authority audit.

Grants live in SQLite rather than a config file for two reasons. A grant is
security state, not a preference: it should not be hand-editable alongside
cosmetic settings, and every mutation should be timestamped and auditable. And
the model has file tools -- keeping authority in a structured store that only
AuthorityManager writes to removes any path where generated text could widen
its own permissions by editing YAML.

Only enough is persisted to rebuild the grant set honestly on startup. Routine
allows are deliberately not written here; they are high-volume telemetry, and
recording every mouse click would bury the decisions that matter.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import PROJECT_ROOT

# Bumped when the meaning of stored authority changes. A grant recorded under an
# older policy is not silently honoured under a newer, differently-shaped one.
POLICY_VERSION = 1
GRANT_SCHEMA_VERSION = 1


class AuthorityStore:
    """SQLite-backed persistence for authority state."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else PROJECT_ROOT / "data/memory/authority.db"
        if not self.path.is_absolute():
            self.path = PROJECT_ROOT / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS authority_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS grants (
                    grant_id TEXT PRIMARY KEY,
                    capability TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '*',
                    risk_tier INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at REAL,
                    persistent INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'owner',
                    principal TEXT NOT NULL DEFAULT 'LOCAL_OWNER',
                    conditions_json TEXT NOT NULL DEFAULT '{}',
                    revoked_at TEXT,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    policy_version INTEGER NOT NULL DEFAULT 1,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_grants_capability ON grants(capability, revoked_at);
                CREATE TABLE IF NOT EXISTS authority_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    capability TEXT,
                    scope TEXT,
                    decision TEXT,
                    tier INTEGER,
                    source TEXT,
                    reason TEXT,
                    grant_id TEXT,
                    detail_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_audit_at ON authority_audit(at);
                """
            )

    # --------------------------------------------------------------- settings

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT value FROM authority_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                "INSERT INTO authority_settings(key,value,updated_at) VALUES(?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value), datetime.now(UTC).isoformat()),
            )

    # ----------------------------------------------------------------- grants

    def save_grant(self, record: dict[str, Any]) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                "INSERT INTO grants(grant_id,capability,scope,risk_tier,created_at,expires_at,persistent,"
                "source,principal,conditions_json,revoked_at,last_used_at,use_count,policy_version,schema_version)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(grant_id) DO UPDATE SET revoked_at=excluded.revoked_at,"
                " last_used_at=excluded.last_used_at, use_count=excluded.use_count",
                (
                    record["grant_id"], record["capability"], record.get("scope", "*"),
                    int(record.get("risk_tier", 1)), record["created_at"], record.get("expires_at"),
                    int(bool(record.get("persistent"))), record.get("source", "owner"),
                    record.get("principal", "LOCAL_OWNER"), json.dumps(record.get("conditions", {})),
                    record.get("revoked_at"), record.get("last_used_at"),
                    int(record.get("use_count", 0)), POLICY_VERSION, GRANT_SCHEMA_VERSION,
                ),
            )

    def load_grants(self) -> list[dict[str, Any]]:
        """Return persistent, unrevoked grants written under the current policy.

        A grant recorded under an older policy version is deliberately not
        loaded: tier definitions may have changed underneath it, and silently
        honouring it could grant more authority than the owner agreed to. It
        stays in the table as audit history and simply stops being active.
        """
        with self._lock, self.connect() as db:
            rows = db.execute(
                "SELECT * FROM grants WHERE persistent=1 AND revoked_at IS NULL"
                " AND policy_version=? AND schema_version=?",
                (POLICY_VERSION, GRANT_SCHEMA_VERSION),
            ).fetchall()
        return [dict(row) for row in rows]

    def stale_grant_count(self) -> int:
        """Persistent grants held back because they predate the current policy."""
        with self._lock, self.connect() as db:
            row = db.execute(
                "SELECT COUNT(*) FROM grants WHERE persistent=1 AND revoked_at IS NULL"
                " AND (policy_version<>? OR schema_version<>?)",
                (POLICY_VERSION, GRANT_SCHEMA_VERSION),
            ).fetchone()
        return int(row[0])

    def mark_revoked(self, grant_id: str) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                "UPDATE grants SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL",
                (datetime.now(UTC).isoformat(), grant_id),
            )

    # ------------------------------------------------------------------ audit

    def record_audit(self, kind: str, **fields: Any) -> None:
        """Durably record a decision that matters. Routine allows do not come here."""
        with self._lock, self.connect() as db:
            db.execute(
                "INSERT INTO authority_audit(at,kind,capability,scope,decision,tier,source,reason,grant_id,detail_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(UTC).isoformat(), kind, fields.get("capability"), fields.get("scope"),
                    fields.get("decision"), fields.get("tier"), fields.get("source"),
                    str(fields.get("reason", ""))[:1000], fields.get("grant_id"),
                    json.dumps(fields.get("detail", {}), default=str),
                ),
            )

    def audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self.connect() as db:
            rows = db.execute(
                "SELECT * FROM authority_audit ORDER BY id DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [dict(row) for row in rows][::-1]
