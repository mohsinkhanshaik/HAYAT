from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.memory.database import MemoryDatabase, now_iso


@dataclass(slots=True)
class ScheduledJob:
    id: str
    goal: str
    schedule_type: str
    schedule: dict[str, Any]
    enabled: bool = True


@dataclass(slots=True)
class ApprovalRequest:
    id: int
    job_id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    status: str
    created_at: str


class JobRepository:
    STATES = {"QUEUED", "RUNNING", "PAUSED", "WAITING", "COMPLETED", "FAILED", "CANCELLED"}

    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database
        with self.database.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY, goal TEXT NOT NULL, schedule_type TEXT NOT NULL,
                schedule_json TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, last_run_at TEXT, last_task_id TEXT, last_status TEXT)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, tool TEXT NOT NULL,
                arguments_json TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL, decided_at TEXT, task_id TEXT)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS job_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, task_id TEXT,
                state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                checkpoint_json TEXT NOT NULL DEFAULT '{}',
                resume_policy TEXT NOT NULL DEFAULT 'manual_review_required')"""
            )

    def save(self, job: ScheduledJob) -> None:
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO scheduled_jobs(id,goal,schedule_type,schedule_json,enabled,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET goal=excluded.goal,schedule_type=excluded.schedule_type,schedule_json=excluded.schedule_json,enabled=excluded.enabled",
                (job.id, job.goal, job.schedule_type, json.dumps(job.schedule), int(job.enabled), now_iso()),
            )

    def list(self, enabled_only: bool = False) -> list[ScheduledJob]:
        query = "SELECT * FROM scheduled_jobs" + (" WHERE enabled=1" if enabled_only else "")
        with self.database.connect() as db:
            rows = db.execute(query + " ORDER BY created_at").fetchall()
        return [ScheduledJob(row["id"], row["goal"], row["schedule_type"], json.loads(row["schedule_json"]), bool(row["enabled"])) for row in rows]

    def get(self, job_id: str) -> ScheduledJob:
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown scheduled job: {job_id}")
        return ScheduledJob(row["id"], row["goal"], row["schedule_type"], json.loads(row["schedule_json"]), bool(row["enabled"]))

    def set_enabled(self, job_id: str, enabled: bool) -> None:
        with self.database.connect() as db:
            cursor = db.execute("UPDATE scheduled_jobs SET enabled=? WHERE id=?", (int(enabled), job_id))
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown scheduled job: {job_id}")

    def delete(self, job_id: str) -> None:
        with self.database.connect() as db:
            cursor = db.execute("DELETE FROM scheduled_jobs WHERE id=?", (job_id,))
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown scheduled job: {job_id}")

    def record_run(self, job_id: str, task_id: str, status: str) -> None:
        with self.database.connect() as db:
            db.execute("UPDATE scheduled_jobs SET last_run_at=?,last_task_id=?,last_status=? WHERE id=?", (now_iso(), task_id, status, job_id))

    def request_approval(self, job_id: str, tool: str, arguments: dict[str, Any], reason: str, task_id: str) -> int:
        with self.database.connect() as db:
            existing = db.execute(
                "SELECT id FROM approval_requests WHERE job_id=? AND tool=? AND arguments_json=? AND status='pending'",
                (job_id, tool, json.dumps(arguments, sort_keys=True, default=str)),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cursor = db.execute(
                "INSERT INTO approval_requests(job_id,tool,arguments_json,reason,status,created_at,task_id) VALUES(?,?,?,?,?,?,?)",
                (job_id, tool, json.dumps(arguments, sort_keys=True, default=str), reason, "pending", now_iso(), task_id),
            )
            return int(cursor.lastrowid)

    def approvals(self, status: str | None = "pending") -> list[ApprovalRequest]:
        query = "SELECT * FROM approval_requests"
        parameters: tuple[Any, ...] = ()
        if status:
            query += " WHERE status=?"
            parameters = (status,)
        with self.database.connect() as db:
            rows = db.execute(query + " ORDER BY id", parameters).fetchall()
        return [ApprovalRequest(row["id"], row["job_id"], row["tool"], json.loads(row["arguments_json"]), row["reason"], row["status"], row["created_at"]) for row in rows]

    def decide_approval(self, request_id: int, status: str) -> ApprovalRequest:
        if status not in {"approved", "denied", "consumed"}:
            raise ValueError(status)
        with self.database.connect() as db:
            db.execute("UPDATE approval_requests SET status=?,decided_at=? WHERE id=?", (status, now_iso(), request_id))
            row = db.execute("SELECT * FROM approval_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown approval request: {request_id}")
        return ApprovalRequest(row["id"], row["job_id"], row["tool"], json.loads(row["arguments_json"]), row["reason"], row["status"], row["created_at"])

    def begin_execution(self, job_id: str) -> int:
        now = now_iso()
        with self.database.connect() as db:
            cursor = db.execute("INSERT INTO job_executions(job_id,state,created_at,updated_at) VALUES(?,?,?,?)", (job_id, "QUEUED", now, now))
            return int(cursor.lastrowid)

    def update_execution(self, execution_id: int, state: str, *, task_id: str | None = None, checkpoint: dict[str, Any] | None = None) -> None:
        name = state.upper()
        if name not in self.STATES:
            raise ValueError(state)
        with self.database.connect() as db:
            cursor = db.execute("UPDATE job_executions SET state=?,task_id=COALESCE(?,task_id),checkpoint_json=COALESCE(?,checkpoint_json),updated_at=? WHERE id=?", (name, task_id, json.dumps(checkpoint, default=str) if checkpoint is not None else None, now_iso(), execution_id))
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown job execution: {execution_id}")

    def executions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            rows = db.execute("SELECT * FROM job_executions ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        return [dict(row) | {"checkpoint": json.loads(row["checkpoint_json"])} for row in rows]
