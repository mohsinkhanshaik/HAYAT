from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT


class BackupManager:
    """Consistent local SQLite backups; restore is deliberately manual/reviewed."""

    def __init__(self, storage, root: Path | None = None, sources: list[Path] | None = None) -> None:
        self.storage = storage
        self.root = root or PROJECT_ROOT / "data/backups"
        self.sources = sources or [
            PROJECT_ROOT / "data/memory/agent.db", PROJECT_ROOT / "data/memory/learning.db",
            PROJECT_ROOT / "data/memory/jobs.db", PROJECT_ROOT / "data/memory/events.db",
            PROJECT_ROOT / "data/knowledge/knowledge.db",
        ]
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        manifests = sorted(self.root.glob("*/manifest.json"), reverse=True)
        return {
            "directory": str(self.root), "backups": len(manifests),
            "latest": str(manifests[0].parent) if manifests else None,
            "automatic_restore": False, "includes_secrets": False,
        }

    def create(self, label: str = "manual") -> dict[str, Any]:
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")[:50] or "manual"
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = self.root / f"{stamp}-{safe_label}"
        destination.mkdir(parents=False, exist_ok=False)
        candidates = self.sources
        estimate = sum(path.stat().st_size for path in candidates if path.is_file())
        self.storage.ensure_capacity(estimate)
        files = []
        try:
            for source in candidates:
                if not source.is_file():
                    continue
                target = destination / source.name
                with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
                    source_db.backup(target_db)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                files.append({"source": str(source), "file": target.name, "bytes": target.stat().st_size, "sha256": digest})
            manifest = {
                "created_at": datetime.now(UTC).isoformat(), "label": safe_label,
                "files": files, "restore": "manual-review-required", "includes_env": False,
            }
            manifest_path = destination / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return {"path": str(destination), "manifest": str(manifest_path), "files": len(files), "bytes": sum(item["bytes"] for item in files), "restore": "manual-review-required"}
        except Exception:
            for path in destination.glob("*"):
                path.unlink(missing_ok=True)
            destination.rmdir()
            raise


def register_backup_tools(registry, manager: BackupManager) -> None:
    registry.register(
        "create_database_backup", "Create a consistent local backup of AI SQLite state without secrets or automatic restore.",
        {"type": "object", "properties": {"label": {"type": "string", "maxLength": 50}}, "additionalProperties": False},
        "low", "filesystem_write_project", manager.create,
    )
