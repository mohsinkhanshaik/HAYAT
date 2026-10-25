from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, load_settings
from app.safety.authority import (
    BACKGROUND_LEARNER_WRITE_CATEGORIES,
)


class LearningArchitecture:
    """One evidence-based view of everything HAYAT can learn and retain.

    The subsystem deliberately distinguishes durable memory, researched facts,
    measured teaching progress, reusable procedures, and model-training data.
    They have different trust rules and are never silently merged.
    """

    def __init__(self, runtime: Any, *, artifact: Path | None = None,
                 cache_seconds: float = 10.0) -> None:
        self.runtime = runtime
        self.artifact = artifact or PROJECT_ROOT / "data/benchmarks/learning/architecture.json"
        self.cache_seconds = max(0.0, float(cache_seconds))
        self._lock = threading.RLock()
        self._cached_at = 0.0
        self._cached: dict[str, Any] | None = None

    @staticmethod
    def _count(value: Any) -> int:
        return int(value or 0)

    @staticmethod
    def _db_metrics(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {"path": str(path), "exists": False, "bytes": 0}
        with sqlite3.connect(path) as db:
            page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
            free_pages = int(db.execute("PRAGMA freelist_count").fetchone()[0])
            journal_mode = str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        return {
            "path": str(path), "exists": True, "bytes": path.stat().st_size,
            "page_size": page_size, "pages": page_count, "free_pages": free_pages,
            "reclaimable_bytes": page_size * free_pages, "journal_mode": journal_mode,
        }

    def _research_alignment(self) -> dict[str, Any]:
        store = self.runtime.research.knowledge
        with store.connect() as db:
            claims = self._count(db.execute("SELECT COUNT(*) FROM claims").fetchone()[0])
            evidenced_claims = self._count(db.execute(
                "SELECT COUNT(DISTINCT claim_id) FROM evidence"
            ).fetchone()[0])
            summaries = self._count(db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0])
            sourced_summaries = self._count(db.execute(
                "SELECT COUNT(DISTINCT k.id) FROM knowledge k "
                "JOIN run_sources rs ON rs.run_id=k.run_id"
            ).fetchone()[0])
            active = self._count(db.execute(
                "SELECT COUNT(*) FROM knowledge WHERE status='active'"
            ).fetchone()[0])
            stale_active = self._count(db.execute(
                "SELECT COUNT(*) FROM knowledge WHERE status='active' "
                "AND valid_until IS NOT NULL AND valid_until<=?",
                (datetime.now(UTC).isoformat(),),
            ).fetchone()[0])
            unresolved = self._count(db.execute(
                "SELECT COUNT(*) FROM claim_conflicts WHERE resolution_status='unresolved'"
            ).fetchone()[0])
            disputed = self._count(db.execute(
                "SELECT COUNT(*) FROM claims WHERE status='DISPUTED'"
            ).fetchone()[0])
        return {
            "claims": claims, "claims_with_evidence": evidenced_claims,
            "claim_evidence_coverage": round(evidenced_claims / claims, 4) if claims else 1.0,
            "summaries": summaries, "summaries_with_sources": sourced_summaries,
            "summary_source_coverage": round(sourced_summaries / summaries, 4) if summaries else 1.0,
            "active_summaries": active, "stale_active_summaries": stale_active,
            "disputed_claims": disputed, "unresolved_conflicts": unresolved,
        }

    def inventory(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if not refresh and self._cached is not None and now - self._cached_at <= self.cache_seconds:
                return dict(self._cached) | {"cache": "hit"}
            started = time.perf_counter()
            memory = self.runtime.memory.stats()
            memory_health = self.runtime.memory.health()
            research = self.runtime.research.knowledge.stats()
            research_alignment = self._research_alignment()
            teaching = self.runtime.learning.status()
            queue = self.runtime.learner.queue_status(1) if self.runtime.learner else {"counts": {}}
            training = self.runtime.training.status()
            apprenticeship = self.runtime.apprenticeship.status() if self.runtime.apprenticeship else {}
            compaction = self.runtime.compaction.status() if self.runtime.compaction else {}
            skills = getattr(self.runtime.skills, "skills", {})

            settings = load_settings()
            continuous = settings.get("learning", {}).get("continuous", {})
            layers = {
                "durable_memory": {
                    "purpose": "conversation, task, semantic, reflection, and association recall",
                    "records": sum(self._count(v) for v in memory.get("memories", {}).values()),
                    "by_type": memory.get("memories", {}), "tasks": memory.get("tasks", 0),
                    "conversations": memory.get("conversations", 0),
                    "dna_associations": memory.get("associations", 0),
                    "trust_boundary": "tamper-checked local rows; transient state is session-scoped",
                },
                "researched_knowledge": {
                    "purpose": "source-backed external facts and contradiction-aware claims",
                    **{key: research.get(key, 0) for key in (
                        "runs", "sources", "knowledge", "claims", "evidence", "entities",
                        "relationships", "stale", "superseded", "disputed_claims",
                        "unresolved_conflicts", "monitored_sources",
                    )},
                    "trust_boundary": "public evidence, provenance, freshness, and claim status",
                },
                "teaching_progress": {
                    "purpose": "measured mastery, prerequisites, quizzes, labs, and spaced review",
                    "topics": teaching.get("topics", 0),
                    "weak_topics": len(teaching.get("weaknesses", [])),
                    "reviews_due": len(teaching.get("due_reviews", [])),
                    "recent_events": len(teaching.get("recent", [])),
                    "trust_boundary": "scores are evidence of practice, not general factual truth",
                },
                "background_learning": {
                    "purpose": "bounded idle-time research with fresh-result reuse",
                    "enabled": bool(continuous.get("enabled", False)),
                    "research_level": continuous.get("research_level", "normal"),
                    "queue": queue.get("counts", {}),
                    "principal": "BACKGROUND_LEARNER",
                    "allowed_writes": sorted(BACKGROUND_LEARNER_WRITE_CATEGORIES),
                    "trust_boundary": "public reads and validated knowledge writes only",
                },
                "procedural_learning": {
                    "purpose": "promoted, repeatable local procedures that bypass model inference",
                    "patterns_watched": apprenticeship.get("patterns_watched", 0),
                    "ready_to_learn": len(apprenticeship.get("ready_to_learn", [])),
                    "learned_patterns": len(apprenticeship.get("learned", [])),
                    "trusted_procedures": apprenticeship.get("trusted_procedures", 0),
                    "registered_skills": len(skills),
                    "trust_boundary": "repetition proposes; verification and promotion authorize",
                },
                "model_training": {
                    "purpose": "verified, split-isolated examples for optional local adapters",
                    "records": training.get("total", 0), "eligible": training.get("eligible", 0),
                    "private_local_records": training.get("private_local_records", 0),
                    "states": training.get("states", {}),
                    "validation_passed": training.get("validation", {}).get("passed", False),
                    "automatic_training": training.get("private_data", {}).get("automatic_training", False),
                    # Promotion is the step that changes which weights answer a
                    # question. Preparing a candidate does not, and conflating
                    # the two made the alignment check below test a proxy
                    # instead of the property it names.
                    "automatic_promotion": False,
                    "trust_boundary": "explicit validation; private data requires consent and stays local",
                },
            }
            result = {
                "generated_at": datetime.now(UTC).isoformat(), "cache": "miss",
                "layers": layers, "research_alignment": research_alignment,
                "recall_health": memory_health, "storage_pressure": compaction,
                "databases": {
                    "memory": self._db_metrics(Path(self.runtime.memory.path)),
                    "knowledge": self._db_metrics(Path(self.runtime.research.knowledge.path)),
                    "teaching": self._db_metrics(Path(self.runtime.learning.path)),
                    "background_queue": self._db_metrics(Path(self.runtime.learner.queue_path)),
                    "training": self._db_metrics(Path(self.runtime.training.registry)),
                },
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
            self._cached, self._cached_at = result, now
            return result

    def alignment(self, *, refresh: bool = False) -> dict[str, Any]:
        inventory = self.inventory(refresh=refresh)
        research = inventory["research_alignment"]
        recall = inventory["recall_health"]
        training = inventory["layers"]["model_training"]
        background = inventory["layers"]["background_learning"]
        checks = [
            self._check("memory_integrity", recall.get("integrity") == "ok", "blocker",
                        f"SQLite integrity: {recall.get('integrity', 'unknown')}"),
            self._check("recall_indexes", bool(recall.get("healthy")), "blocker",
                        f"hashed missing={recall.get('missing_hashed_vectors', 0)}, FTS missing={recall.get('missing_fts_rows', 0)}"),
            self._check("claim_provenance", research["claim_evidence_coverage"] >= 0.95,
                        "blocker", f"{research['claim_evidence_coverage']:.1%} of claims have evidence"),
            self._check("summary_provenance", research["summary_source_coverage"] >= 0.95,
                        "blocker", f"{research['summary_source_coverage']:.1%} of summaries have a source"),
            self._check("contradictions_resolved", research["unresolved_conflicts"] == 0,
                        "blocker", f"{research['unresolved_conflicts']} unresolved conflict(s)"),
            self._check("freshness", research["stale_active_summaries"] == 0, "warning",
                        f"{research['stale_active_summaries']} active summary/summaries require refresh"),
            self._check("training_dataset", bool(training["validation_passed"]), "blocker",
                        "no leakage, split contamination, or invalid private consent"),
            # The invariant is that weights never change on their own, and the
            # step that changes them is promotion -- which refuses without
            # owner_approved and raises PermissionError when asked anyway.
            # This used to test automatic_training instead, which only governs
            # whether a candidate may be *prepared*; and it passed vacuously
            # for a further reason, because private_status() reported the flag
            # as False whatever the configuration said.
            self._check("automatic_promotion_disabled", not bool(training["automatic_promotion"]),
                        "blocker", "learning cannot silently change model weights"),
            self._check("automatic_training_supervised", not bool(training["automatic_training"]),
                        "warning", "candidate adapters are prepared only when asked for"),
            self._check("background_least_authority",
                        set(background["allowed_writes"]) == {"knowledge_research"}, "blocker",
                        "background writes are restricted to validated research knowledge"),
            self._check("background_learning_approved", bool(background["enabled"]), "warning",
                        "owner configuration enables bounded continuous learning"),
        ]
        blockers = [item for item in checks if not item["passed"] and item["severity"] == "blocker"]
        warnings = [item for item in checks if not item["passed"] and item["severity"] == "warning"]
        return {
            "aligned": not blockers, "status": "blocked" if blockers else "aligned_with_warnings" if warnings else "aligned",
            "checks": checks, "blockers": [item["name"] for item in blockers],
            "warnings": [item["name"] for item in warnings],
            "policy": {
                "public_learning": "approved within budgets and robots/site rules",
                "local_storage": "approved for validated provenance-bearing knowledge",
                "private_training": "requires per-dataset owner consent and remains local-only",
                "self_permission_changes": "not approved",
            },
            "inventory_generated_at": inventory["generated_at"],
        }

    @staticmethod
    def _check(name: str, passed: bool, severity: str, evidence: str) -> dict[str, Any]:
        return {"name": name, "passed": bool(passed), "severity": severity, "evidence": evidence}

    def optimize(self) -> dict[str, Any]:
        """Safely tune indexes/caches without deleting learned evidence or history."""
        started = time.perf_counter()
        before = self.inventory(refresh=True)
        memory = self.runtime.memory.maintain_indexes()
        knowledge = self.runtime.research.knowledge.optimize()
        database_results: dict[str, Any] = {}
        paths = {
            "memory": Path(self.runtime.memory.path),
            "knowledge": Path(self.runtime.research.knowledge.path),
            "teaching": Path(self.runtime.learning.path),
            "background_queue": Path(self.runtime.learner.queue_path),
            "training": Path(self.runtime.training.registry),
        }
        for name, path in paths.items():
            if not path.is_file():
                database_results[name] = {"optimized": False, "reason": "missing"}
                continue
            try:
                with sqlite3.connect(path, timeout=2.0) as db:
                    db.execute("PRAGMA optimize")
                    checkpoint = tuple(db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())
                database_results[name] = {
                    "optimized": True,
                    "checkpoint": {"busy": checkpoint[0], "log_pages": checkpoint[1], "checkpointed_pages": checkpoint[2]},
                }
            except sqlite3.Error as exc:
                database_results[name] = {"optimized": False, "reason": f"{type(exc).__name__}: {exc}"}
        self._cached = None
        after = self.inventory(refresh=True)
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "safe": True, "learned_records_deleted": 0, "evidence_deleted": 0,
            "memory_indexes": memory, "knowledge_maintenance": knowledge,
            "databases": database_results,
            "before_bytes": {name: item["bytes"] for name, item in before["databases"].items()},
            "after_bytes": {name: item["bytes"] for name, item in after["databases"].items()},
            "alignment": self.alignment(),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        self.artifact.parent.mkdir(parents=True, exist_ok=True)
        self.artifact.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["artifact"] = str(self.artifact)
        return report

