from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT


SCENARIOS = {
    "dns_failure": {"topic": "Networking", "challenge": "A service cannot resolve api.internal. Diagnose the supplied resolver configuration.", "broken": "nameserver: 0.0.0.0\nsearch: internal\n", "fixed": "nameserver: 1.1.1.1\nsearch: internal\n", "keywords": ["dns", "nameserver", "resolver"]},
    "container_networking": {"topic": "Docker", "challenge": "Two simulated services use different network names. Find the configuration mismatch.", "broken": "frontend_network: app-net\nbackend_network: backend-net\n", "fixed": "frontend_network: app-net\nbackend_network: app-net\n", "keywords": ["network", "mismatch", "app-net"]},
    "kubernetes_scheduling": {"topic": "GPU scheduling", "challenge": "A simulated GPU pod remains pending. Identify the missing extended resource request.", "broken": "resources:\n  limits: {}\n", "fixed": "resources:\n  limits:\n    nvidia.com/gpu: 1\n", "keywords": ["nvidia.com/gpu", "device plugin", "resource"]},
    "disk_pressure": {"topic": "SRE", "challenge": "A simulated service refuses writes at a configured quota. Explain safe diagnosis without filling the real disk.", "broken": "used_mb: 950\nquota_mb: 900\n", "fixed": "used_mb: 700\nquota_mb: 900\n", "keywords": ["quota", "disk", "cleanup"]},
    "service_crash": {"topic": "SRE", "challenge": "A simulated service exits because its required port is invalid.", "broken": "PORT=-1\n", "fixed": "PORT=8080\n", "keywords": ["port", "invalid", "configuration"]},
    "gpu_oom_reasoning": {"topic": "GPU architecture", "challenge": "A simulated inference job requests more VRAM than available. Propose bounded mitigations.", "broken": "available_vram_gb: 8\nrequested_vram_gb: 12\n", "fixed": "available_vram_gb: 8\nrequested_vram_gb: 7\n", "keywords": ["vram", "quantization", "batch"]},
}


class LabManager:
    """Project-local simulations only; never damages real services, networking, or disk."""

    def __init__(self, learning=None, root: Path | None = None) -> None:
        self.learning = learning; self.root = root or PROJECT_ROOT / "data/work/labs"; self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "labs.db"
        with self._connect() as db: db.execute("CREATE TABLE IF NOT EXISTS labs(id TEXT PRIMARY KEY,scenario TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,path TEXT NOT NULL,score REAL,attempt_json TEXT)")

    def catalog(self) -> dict[str, Any]:
        return {"scenarios": [{"name": name, "topic": value["topic"], "challenge": value["challenge"]} for name, value in SCENARIOS.items()], "real_system_changes": False, "cost": "$0 API cost"}

    def create(self, scenario: str) -> dict[str, Any]:
        name = scenario.lower().strip().replace(" ", "_")
        if name not in SCENARIOS: raise ValueError(f"Unknown scenario. Available: {', '.join(SCENARIOS)}")
        value = SCENARIOS[name]; identifier = f"lab-{uuid.uuid4().hex[:10]}"; directory = self.root / identifier; directory.mkdir()
        (directory / "challenge.md").write_text(f"# {name.replace('_', ' ').title()}\n\n{value['challenge']}\n\nDo not modify the real machine. Work only inside this lab directory.\n", encoding="utf-8")
        (directory / "broken-config.yaml").write_text(value["broken"], encoding="utf-8")
        now = datetime.now(UTC).isoformat()
        with self._connect() as db: db.execute("INSERT INTO labs(id,scenario,state,created_at,updated_at,path) VALUES(?,?,?,?,?,?)", (identifier, name, "ACTIVE", now, now, str(directory)))
        return {"id": identifier, "scenario": name, "topic": value["topic"], "path": str(directory), "state": "ACTIVE", "real_system_changes": False}

    def score(self, lab_id: str, diagnosis: str, hints_used: int = 0) -> dict[str, Any]:
        row = self._get(lab_id); value = SCENARIOS[row["scenario"]]; lower = diagnosis.lower(); matched = [word for word in value["keywords"] if word.lower() in lower]
        score = max(0, min(1, len(matched) / max(1, len(value["keywords"])) - max(0, hints_used) * .05))
        with self._connect() as db: db.execute("UPDATE labs SET state='SCORED',score=?,attempt_json=?,updated_at=? WHERE id=?", (score, json.dumps({"matched": matched, "hints_used": hints_used}), datetime.now(UTC).isoformat(), lab_id))
        if self.learning: self.learning.record_lab(value["topic"], completed=True, duration_seconds=0, hints_used=hints_used, errors=int(score < .7), recovered=score >= .7, verified=True)
        return {"id": lab_id, "score": round(score, 3), "matched_concepts": matched, "passed": score >= .7}

    def restore(self, lab_id: str) -> dict[str, Any]:
        row = self._get(lab_id); value = SCENARIOS[row["scenario"]]; path = Path(row["path"]) / "broken-config.yaml"; path.write_text(value["fixed"], encoding="utf-8")
        with self._connect() as db: db.execute("UPDATE labs SET state='RESTORED',updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), lab_id))
        return {"id": lab_id, "state": "RESTORED", "path": str(path), "real_system_changes": False}

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db: rows = db.execute("SELECT * FROM labs ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def _get(self, identifier: str):
        with self._connect() as db: row = db.execute("SELECT * FROM labs WHERE id=?", (identifier,)).fetchone()
        if row is None: raise KeyError(identifier)
        return row

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database); connection.row_factory = sqlite3.Row
        try:
            with connection: yield connection
        finally: connection.close()


def register_lab_tools(registry, manager: LabManager) -> None:
    obj = lambda props=None, required=None: {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}
    registry.register("lab_catalog", "List safe zero-cost project-local incident simulations.", obj(), "low", "filesystem_read", manager.catalog)
    registry.register("lab_create", "Create a bounded simulated failure under data/work/labs without changing real services.", obj({"scenario": {"type": "string"}}, ["scenario"]), "medium", "filesystem_write_project", manager.create)
    registry.register("lab_score", "Score a lab diagnosis against known concepts and update measured learner state.", obj({"lab_id": {"type": "string"}, "diagnosis": {"type": "string"}, "hints_used": {"type": "integer", "minimum": 0}}, ["lab_id", "diagnosis"]), "medium", "filesystem_write_project", manager.score)
    registry.register("lab_restore", "Restore a simulated lab configuration; no real system setting is touched.", obj({"lab_id": {"type": "string"}}, ["lab_id"]), "medium", "filesystem_write_project", manager.restore)
