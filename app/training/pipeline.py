from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import subprocess
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, load_settings
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


SECRET_KEY = re.compile(r"(?i)(api[_-]?key|password|passwd|secret|token|authorization|private[_-]?key|credential)")
SECRET_VALUE = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|(?:api[_-]?key|password|token|secret)\s*[:=]\s*[\"']?(?!\[REDACTED(?::|\])|<redacted>)[^\s\"']+|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----)")


def _legacy_secret(value: Any) -> bool:
    """Check decoded values: JSON escaping must not disguise a redaction marker."""
    if isinstance(value, dict):
        return any(_legacy_secret(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_legacy_secret(item) for item in value)
    return isinstance(value, str) and bool(SECRET_VALUE.search(value))


class TrainingDataPipeline:
    CATEGORIES = {"conversation", "routing", "planning", "tool_use", "coding", "research", "vision", "teaching", "preference", "rejected"}
    STATES = {"RAW", "VALIDATED", "REJECTED", "TRAINING_READY", "HELD_OUT"}
    PROFILES = {"router": {"methods": ["SFT", "LoRA"], "recommended": True}, "planner": {"methods": ["SFT", "LoRA"]}, "conversation": {"methods": ["SFT", "DPO"]}, "coding": {"methods": ["SFT", "QLoRA"]}, "teaching": {"methods": ["SFT", "DPO"]}}

    PRIVACY_SCOPES = {"STANDARD", "PRIVATE_LOCAL"}

    def __init__(self, root: Path | None = None, *, config: dict[str, Any] | None = None) -> None:
        self.root = root or PROJECT_ROOT / "data/training"; self.root.mkdir(parents=True, exist_ok=True)
        self.config = dict(config if config is not None else load_settings().get("training", {}))
        private = dict(self.config.get("private_data", {}))
        self.private_data_enabled = bool(private.get("enabled", False))
        self.private_local_only = bool(private.get("local_only", True))
        self.private_requires_consent = bool(private.get("explicit_consent_required", True))
        self.private_cloud_export_allowed = bool(private.get("cloud_export_allowed", False))
        # Read, not assumed. private_status() used to report both of these as
        # False whatever the configuration said, so switching them on in
        # settings.yaml changed the file and nothing else -- the status kept
        # answering "off" and every caller believed it.
        self.automatic_collection = bool(private.get("automatic_collection", False))
        self.automatic_training = bool(self.config.get("automatic_training", False))
        self.registry = self.root / "registry.db"
        for category in self.CATEGORIES:
            (self.root / category).mkdir(parents=True, exist_ok=True)
            (self.root / category / "private").mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS training_records(
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                category TEXT NOT NULL, state TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
                semantic_hash TEXT NOT NULL, sample_json TEXT NOT NULL, provenance_json TEXT NOT NULL,
                validation_json TEXT NOT NULL DEFAULT '{}', split TEXT, redactions INTEGER NOT NULL DEFAULT 0,
                privacy_scope TEXT NOT NULL DEFAULT 'STANDARD', local_only INTEGER NOT NULL DEFAULT 1,
                consent_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_training_state ON training_records(state,category,split);
            CREATE TABLE IF NOT EXISTS model_versions(
                id INTEGER PRIMARY KEY AUTOINCREMENT, base_model TEXT NOT NULL, adapter_version TEXT,
                dataset_version TEXT NOT NULL, training_config_json TEXT NOT NULL,
                evaluation_json TEXT NOT NULL, deployment_date TEXT, status TEXT NOT NULL
            );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(training_records)")}
            for name, declaration in {
                "privacy_scope": "TEXT NOT NULL DEFAULT 'STANDARD'",
                "local_only": "INTEGER NOT NULL DEFAULT 1",
                "consent_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE training_records ADD COLUMN {name} {declaration}")

    def ingest(self, category: str, sample: dict[str, Any], *, provenance: dict[str, Any],
               privacy_scope: str = "STANDARD", owner_consent: bool = False) -> dict[str, Any]:
        if category not in self.CATEGORIES: raise ValueError(f"Unsupported training category: {category}")
        if not provenance.get("source"): raise ValueError("Training samples require provenance.source.")
        scope = str(privacy_scope or "STANDARD").strip().upper()
        if scope not in self.PRIVACY_SCOPES:
            raise ValueError(f"Unsupported training privacy scope: {privacy_scope}")
        if scope == "PRIVATE_LOCAL":
            if not self.private_data_enabled:
                raise PermissionError("Private training-data collection is disabled.")
            if self.private_requires_consent and not owner_consent:
                raise PermissionError("Private training data requires explicit owner consent.")
            if not self.private_local_only or self.private_cloud_export_allowed:
                raise PermissionError("Private training is restricted to local-only storage and execution.")
        redacted, count = self._redact(sample); canonical = json.dumps(redacted, sort_keys=True, default=str)
        from app.evaluation.holdout import is_holdout
        if is_holdout(json.dumps(sample, ensure_ascii=False, default=str)):
            raise ValueError("This example contains a held-out evaluation question; it is never used for training.")
        clean_provenance, provenance_redactions = self._redact(provenance)
        count += provenance_redactions
        digest = hashlib.sha256(canonical.encode()).hexdigest(); identifier = digest[:20]; semantic = self._semantic_hash(canonical)
        now = datetime.now(UTC).isoformat()
        consent = {
            "owner_consented": bool(owner_consent),
            "scope": scope,
            "local_only": True,
            "cloud_export_allowed": False,
        }
        with self._connect() as db:
            duplicate = db.execute("SELECT id,state,split,privacy_scope FROM training_records WHERE content_hash=? OR semantic_hash=?", (digest, semantic)).fetchone()
            if duplicate:
                return {"id": duplicate["id"], "state": duplicate["state"], "split": duplicate["split"],
                        "privacy_scope": duplicate["privacy_scope"], "duplicate": True, "redactions": count}
            db.execute(
                "INSERT INTO training_records(id,created_at,updated_at,category,state,content_hash,semantic_hash,"
                "sample_json,provenance_json,redactions,privacy_scope,local_only,consent_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, now, now, category, "RAW", digest, semantic, canonical,
                 json.dumps(clean_provenance, default=str), count, scope, 1, json.dumps(consent)),
            )
        return {"id": identifier, "state": "RAW", "privacy_scope": scope,
                "local_only": True, "duplicate": False, "redactions": count}

    def add_private(self, category: str, sample: dict[str, Any], *, provenance: dict[str, Any],
                    verified: bool, owner_consent: bool) -> Path:
        """Stage one explicitly consented private example for local training only."""
        if not verified:
            raise ValueError("Only evaluated, verified private examples may enter the training dataset.")
        record = self.ingest(category, sample, provenance=provenance,
                             privacy_scope="PRIVATE_LOCAL", owner_consent=owner_consent)
        if record["duplicate"]:
            if record.get("privacy_scope") != "PRIVATE_LOCAL":
                raise ValueError("An identical standard example already exists; privacy scopes are not merged.")
            split = record["split"] or "train"
            return self.root / category / "private" / f"{split}.jsonl"
        self.validate(record["id"], verification={"passed": True, "source": "verified_private_add"})
        promoted = self.promote(record["id"])
        return self.root / category / "private" / f"{promoted['split']}.jsonl"

    def validate(self, identifier: str, *, verification: dict[str, Any] | None = None, human_approved: bool = False, deterministic_correct: bool = False, reject_reason: str | None = None) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM training_records WHERE id=?", (identifier,)).fetchone()
            if row is None: raise KeyError(identifier)
            if reject_reason:
                state, split = "REJECTED", None
            elif human_approved is True or deterministic_correct is True or (verification or {}).get("passed") is True:
                split = None; state = "VALIDATED"
            else:
                raise ValueError("Validation requires human approval, deterministic correctness, or passed verification.")
            validation = {"human_approved": human_approved, "deterministic_correct": deterministic_correct, "verification": verification or {}, "reject_reason": reject_reason}
            validation, _ = self._redact(validation)
            db.execute("UPDATE training_records SET state=?,split=?,validation_json=?,updated_at=? WHERE id=?", (state, split, json.dumps(validation, default=str), datetime.now(UTC).isoformat(), identifier))
        # A revoked or re-reviewed record must also leave the derived JSONL
        # corpus. Otherwise the training runner can still read old approval.
        if row["split"]:
            self._export(dict(row))
        return {"id": identifier, "state": state, "split": split}

    def promote(self, identifier: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM training_records WHERE id=?", (identifier,)).fetchone()
            if row is None: raise KeyError(identifier)
            if row["state"] != "VALIDATED": raise ValueError("Only VALIDATED records can become training ready.")
            from app.safety.sensitive import contains_secret
            try:
                sample = json.loads(row["sample_json"])
                provenance = json.loads(row["provenance_json"])
                validation = json.loads(row["validation_json"] or "{}")
                consent = json.loads(row["consent_json"] or "{}")
                if not all(isinstance(value, dict) for value in (sample, provenance, validation, consent)):
                    raise ValueError("Record fields must be objects.")
                proof = validation.get("verification") or {}
                approved = (validation.get("human_approved") is True or validation.get("deterministic_correct") is True
                            or isinstance(proof, dict) and proof.get("passed") is True)
                digest = hashlib.sha256(json.dumps(sample, sort_keys=True, default=str).encode()).hexdigest()
                if (not approved or validation.get("reject_reason") or not provenance.get("source")
                        or digest != row["content_hash"] or contains_secret([sample, provenance, validation, consent])):
                    raise ValueError("Approval, provenance and untampered content are required.")
                if row["privacy_scope"] == "PRIVATE_LOCAL" and (not row["local_only"] or consent.get("owner_consented") is not True):
                    raise ValueError("Private training needs recorded consent and local storage.")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Cannot promote invalid training record: {exc}") from exc
            split = self._split(identifier); state = "HELD_OUT" if split == "test" else "TRAINING_READY"
            db.execute("UPDATE training_records SET state=?,split=?,updated_at=? WHERE id=?", (state, split, datetime.now(UTC).isoformat(), identifier))
            payload = dict(row) | {"state": state, "split": split}
        self._export(payload)
        return {"id": identifier, "state": state, "split": split}

    def pending_review(self, limit: int = 20, offset: int = 0, *, category: str = "", privacy_scope: str = "",
                       query: str = "", order: str = "oldest") -> dict[str, Any]:
        """Collected records waiting for the owner to say keep or drop.

        Collection gathers RAW records automatically; nothing moves one toward
        training without a decision. 45 records had waited with no way to be
        reviewed, against a gate of 1000.
        """
        if category and category not in self.CATEGORIES:
            raise ValueError("Unsupported training category.")
        if privacy_scope and privacy_scope not in self.PRIVACY_SCOPES:
            raise ValueError("Unsupported training privacy scope.")
        if order not in {"oldest", "newest"} or not isinstance(query, str) or len(query) > 500:
            raise ValueError("Use oldest/newest ordering and a search of up to 500 characters.")
        where, parameters = ["state='RAW'"], []
        for field, value in (("category", category), ("privacy_scope", privacy_scope)):
            if value:
                where.append(f"{field}=?"); parameters.append(value)
        if query.strip():
            where.append("(instr(lower(sample_json),lower(?))>0 OR instr(lower(provenance_json),lower(?))>0)")
            parameters.extend([query.strip(), query.strip()])
        condition = " AND ".join(where)
        direction = "ASC" if order == "oldest" else "DESC"
        with self._connect() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM training_records WHERE {condition}", parameters).fetchone()[0])
            rows = db.execute(
                "SELECT id,created_at,category,privacy_scope,sample_json,provenance_json FROM training_records "
                f"WHERE {condition} ORDER BY created_at {direction},id {direction} LIMIT ? OFFSET ?",
                (*parameters, max(1, min(int(limit), 100)), max(0, int(offset))),
            ).fetchall()
        records = []
        for row in rows:
            try:
                sample = json.loads(row["sample_json"])
            except ValueError:
                sample = {"raw": row["sample_json"]}
            try:
                provenance = json.loads(row["provenance_json"] or "{}")
                source = provenance.get("source", "") if isinstance(provenance, dict) else "invalid provenance"
            except ValueError:
                source = "invalid provenance"
            records.append({"id": row["id"], "created_at": row["created_at"], "category": row["category"],
                            "privacy_scope": row["privacy_scope"], "sample": sample,
                            "source": source})
        return {"total": total, "records": records}

    def review(self, identifier: str, *, approved: bool, reason: str = "") -> dict[str, Any]:
        """The owner's decision on one record: keep it for training or drop it.

        Approval is human approval in validate()'s sense, then promotion into
        a deterministic train/validation/test split. A rejection keeps the row
        as REJECTED, so the same example is not collected again.
        """
        if not approved:
            return self.validate(identifier, reject_reason=reason or "rejected by the owner in review")
        self.validate(identifier, human_approved=True)
        return self.promote(identifier)

    def add(self, category: str, sample: dict[str, Any], *, provenance: dict[str, Any], verified: bool) -> Path:
        if not verified: raise ValueError("Only evaluated, verified examples may enter the training dataset.")
        record = self.ingest(category, sample, provenance=provenance)
        if record["duplicate"]:
            if record.get("privacy_scope") == "PRIVATE_LOCAL":
                raise ValueError("An identical private example already exists; privacy scopes are not merged.")
            split = record["split"] or "train"; return self.root / category / f"{split}.jsonl"
        self.validate(record["id"], verification={"passed": True, "source": "verified_add"})
        promoted = self.promote(record["id"])
        return self.root / category / f"{promoted['split']}.jsonl"

    def dataset(self) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT category,state,split,privacy_scope,COUNT(*) count FROM training_records GROUP BY category,state,split,privacy_scope").fetchall()
            version_source = "|".join(row[0] for row in db.execute("SELECT content_hash FROM training_records WHERE state IN ('TRAINING_READY','HELD_OUT') ORDER BY content_hash"))
        return {"version": hashlib.sha256(version_source.encode()).hexdigest()[:16], "records": [dict(row) for row in rows], "profiles": self.PROFILES}

    def split_counts(self, *, privacy_scope: str | None = None,
                     category: str | None = None) -> dict[str, int]:
        """Count only evaluated corpus rows for a candidate's exact scope.

        Training gates must not accidentally add standard and private rows or
        unrelated task profiles together just to reach a minimum count.
        """
        where = ["state IN ('TRAINING_READY','HELD_OUT')", "split IS NOT NULL"]
        parameters: list[Any] = []
        if privacy_scope:
            scope = str(privacy_scope).upper()
            if scope not in self.PRIVACY_SCOPES:
                raise ValueError(f"Unsupported training privacy scope: {privacy_scope}")
            where.append("privacy_scope=?"); parameters.append(scope)
        if category:
            if category not in self.CATEGORIES:
                raise ValueError(f"Unsupported training category: {category}")
            where.append("category=?"); parameters.append(category)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT split,COUNT(*) count FROM training_records WHERE {' AND '.join(where)} GROUP BY split",
                parameters,
            ).fetchall()
        counts = {"train": 0, "validation": 0, "test": 0}
        for row in rows:
            counts[str(row["split"])] = int(row["count"])
        return counts

    def validate_dataset(self) -> dict[str, Any]:
        from app.safety.sensitive import contains_secret
        with self._connect() as db:
            rows = db.execute("SELECT * FROM training_records").fetchall()
        hashes: dict[str, set[str]] = {}; semantic: dict[str, set[str]] = {}; secrets = []
        malformed, integrity, unverified, invalid_splits = [], [], [], []
        invalid_private = []
        for row in rows:
            if row["split"]: hashes.setdefault(row["content_hash"], set()).add(row["split"]); semantic.setdefault(row["semantic_hash"], set()).add(row["split"])
            try:
                sample = json.loads(row["sample_json"])
                provenance = json.loads(row["provenance_json"])
                validation = json.loads(row["validation_json"] or "{}")
                consent = json.loads(row["consent_json"] or "{}")
                if not all(isinstance(item, dict) for item in (sample, provenance, validation, consent)):
                    raise ValueError("record fields must be objects")
            except (ValueError, TypeError):
                malformed.append(row["id"])
                continue
            if _legacy_secret([sample, provenance, validation, consent]) or contains_secret([sample, provenance, validation, consent]):
                secrets.append(row['id'])
            canonical = json.dumps(sample, sort_keys=True, default=str)
            if hashlib.sha256(canonical.encode()).hexdigest() != row["content_hash"]:
                integrity.append(row["id"])
            eligible = row["state"] in {"TRAINING_READY", "HELD_OUT"}
            proof = validation.get("verification") or {}
            proven = (validation.get("human_approved") is True
                      or validation.get("deterministic_correct") is True
                      or isinstance(proof, dict) and proof.get("passed") is True)
            if eligible and (not proven or not provenance.get("source") or validation.get("reject_reason")):
                unverified.append(row["id"])
            if (eligible and (row["split"] not in {"train", "validation", "test"}
                              or (row["state"] == "HELD_OUT") != (row["split"] == "test"))
                    or not eligible and row["split"] is not None):
                invalid_splits.append(row["id"])
            if row["privacy_scope"] == "PRIVATE_LOCAL" and (
                    not row["local_only"] or consent.get("owner_consented") is not True):
                invalid_private.append(row["id"])
        contamination = [key for key, splits in hashes.items() if len(splits) > 1] + [key for key, splits in semantic.items() if len(splits) > 1]
        return {"passed": not any((contamination, secrets, invalid_private, malformed, integrity, unverified, invalid_splits)),
                "records": len(rows), "cross_split_contamination": contamination,
                "secret_leaks": secrets, "invalid_private_records": invalid_private,
                "malformed_records": malformed, "content_mismatches": integrity,
                "unverified_eligible_records": unverified, "invalid_split_records": invalid_splits,
                "duplicate_policy": "exact and normalized-semantic hash before split"}

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            states = {row["state"]: row["count"] for row in db.execute("SELECT state,COUNT(*) count FROM training_records GROUP BY state")}
            total = int(db.execute("SELECT COUNT(*) FROM training_records").fetchone()[0])
            eligible = int(db.execute("SELECT COUNT(*) FROM training_records WHERE state IN ('TRAINING_READY','HELD_OUT')").fetchone()[0])
            private = int(db.execute("SELECT COUNT(*) FROM training_records WHERE privacy_scope='PRIVATE_LOCAL'").fetchone()[0])
        return {"root": str(self.root), "total": total, "eligible": eligible,
                "private_local_records": private,
                "private_data": self.private_status(),
                "states": {state: states.get(state, 0) for state in sorted(self.STATES)},
                "dataset": self.dataset(), "validation": self.validate_dataset(),
                "training_gate": self.training_gate(eligible)}

    def private_status(self) -> dict[str, Any]:
        return {
            "enabled": self.private_data_enabled,
            "local_only": self.private_local_only,
            "explicit_consent_required": self.private_requires_consent,
            "cloud_export_allowed": self.private_cloud_export_allowed,
            "credentials_redacted": True,
            "automatic_collection": self.automatic_collection,
            "automatic_training": self.automatic_training,
        }

    def estimate(self, profile: str = "router") -> dict[str, Any]:
        if profile not in self.PROFILES: raise ValueError(f"Unknown training profile: {profile}")
        gpu = {"name": None, "vram_total_mb": None, "vram_free_mb": None}
        try:
            output = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, check=True).stdout.strip().split(",")
            gpu = {"name": output[0].strip(), "vram_total_mb": int(output[1]), "vram_free_mb": int(output[2])}
        except Exception:
            log_swallowed(_log, "querying the GPU with nvidia-smi")
        status = self.status(); ready = status["states"]["TRAINING_READY"]
        return {"profile": profile, "hardware": gpu, "dataset_ready_records": ready, "recommended_methods": self.PROFILES[profile]["methods"], "safe_to_start": False, "reason": "Training execution is intentionally disabled until dataset, base-model fit, and baseline/candidate evaluation gates pass.", "first_target_recommendation": "small specialized intent/router model"}

    def training_gate(self, total: int) -> dict[str, Any]:
        """Readiness, reported from the configuration rather than from a constant.

        `automatic_training` was a literal False in this dict, and this dict is
        what the GUI's training panel reads. Turning the setting on in
        settings.yaml therefore changed the pipeline's behaviour and not the
        number on screen, so the panel kept reporting "automatic training
        False" over a config that said true. A status display that cannot
        report a change is worse than no display.
        """
        minimum = max(100, int(self.config.get("minimum_validated_samples", 1000)))
        splits = self.split_counts()
        checks = {
            "minimum_samples": int(total) >= minimum,
            "training_split": splits.get("train", 0) > 0,
            "validation_split": not self.config.get("require_validation_split", True) or splits.get("validation", 0) > 0,
            "held_out_test": not self.config.get("require_held_out_test", True) or splits.get("test", 0) > 0,
            "dataset_integrity": self.validate_dataset()["passed"],
        }
        return {"ready": all(checks.values()), "minimum_validated_samples": minimum,
                "eligible_records": int(total), "checks": checks,
                "blockers": [key for key, passed in checks.items() if not passed],
                "scope": "Dataset readiness only; candidate preparation also checks local checkpoint and training dependencies.",
                "requirements": ["quality threshold", "validation split", "held-out test", "trainable base model", "hardware estimate", "baseline benchmark", "no golden or safety regression"],
                "automatic_training": self.automatic_training, "supported_future_methods": ["SFT", "LoRA", "QLoRA", "DPO"],
                "promotion": "explicit evaluation-backed approval only", "rollback": ["base model", "previous production adapter", "candidate adapter"]}

    def _export(self, row: dict[str, Any]) -> None:
        """Rebuild one derived split atomically from current approved rows."""
        if row["category"] not in self.CATEGORIES or row["split"] not in {"train", "validation", "test"}:
            raise ValueError("Invalid category or split for training export.")
        private = row.get("privacy_scope") == "PRIVATE_LOCAL"
        destination = self.root / row["category"] / ("private" if private else "") / f"{row['split']}.jsonl"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM training_records WHERE category=? AND split=? AND privacy_scope=? "
                "AND state IN ('TRAINING_READY','HELD_OUT') ORDER BY id",
                (row["category"], row["split"], row.get("privacy_scope", "STANDARD")),
            ).fetchall()
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                                 prefix=".export-", suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    for record in rows:
                        payload = {"id": record["id"], "created_at": record["created_at"],
                                   "category": record["category"], "state": record["state"], "split": record["split"],
                                   "privacy_scope": record["privacy_scope"], "local_only": bool(record["local_only"]),
                                   "provenance": json.loads(record["provenance_json"]),
                                   "content_hash": record["content_hash"],
                                   "validation": json.loads(record["validation_json"] or "{}"),
                                   "consent": json.loads(record["consent_json"] or "{}"),
                                   "sample": json.loads(record["sample_json"])}
                        handle.write(json.dumps(payload, default=str) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    #: What SQLite says when a database file is no longer one. "unsupported
    #: file format" belongs here as much as the other two: that is what it
    #: reported when registry.db was found with Tk's error output written over
    #: its header, and the learner's existing quarantine check -- which looked
    #: only for "malformed" and "not a database" -- would have let it through.
    _CORRUPT = ("malformed", "not a database", "unsupported file format",
                "file is encrypted", "database disk image")

    @contextmanager
    def _connect(self):
        try:
            connection = self._open()
        except sqlite3.DatabaseError as error:
            if not self._quarantine(error):
                raise
            connection = self._open()
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.registry)
        # connect() is lazy: a destroyed file opens without complaint and only
        # fails on first use. Reading the schema table is the probe, because it
        # is what every later call needs and it actually parses page one --
        # `PRAGMA schema_version` returned 1768846945 quite happily from a
        # header that had the ASCII for "iela" sitting in that field.
        try:
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception:
            # Windows will not rename a file that still has an open handle, so
            # the quarantine below would silently fail and the error would
            # propagate as if nothing could be done about it.
            connection.close()
            raise
        return connection

    def _quarantine(self, error: Exception) -> bool:
        """Move an unreadable registry aside and rebuild an empty one.

        The registry indexes training samples whose contents live in the
        per-category JSONL files beside it. Losing the index costs the records
        that had not been exported yet; keeping an unreadable file in place
        costs every future call, which is what happened -- the whole training
        subsystem stopped, and thirteen tests with it.

        Renamed rather than deleted, so the damage can still be examined.
        """
        if not any(marker in str(error).lower() for marker in self._CORRUPT):
            return False
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        try:
            self.registry.rename(self.registry.with_name(f"registry.corrupt.{stamp}.db"))
        except OSError:
            return False
        for suffix in ("-wal", "-shm"):
            side = self.registry.with_name(self.registry.name + suffix)
            side.unlink(missing_ok=True)
        self.initialize()
        return True

    @classmethod
    def _redact(cls, value: Any) -> tuple[Any, int]:
        """Shared value-level redaction. Training data must never carry credentials."""
        from app.safety.sensitive import Redactor

        # Value-independent placeholders: a digest is still derived from the
        # secret, and training corpora may be exported. It also keeps two samples
        # that differ only by their secret deduplicating correctly.
        redactor = Redactor(include_digest=False)
        cleaned = redactor.value(value)
        keyed = cls._count_keyed(value)
        return cleaned, max(1, len(redactor.findings) + keyed) if (redactor.findings or keyed) else 0

    @classmethod
    def _count_keyed(cls, value: Any) -> int:
        from app.safety.sensitive import SECRET_KEY_NAMES

        if isinstance(value, dict):
            return sum(
                1 if SECRET_KEY_NAMES.search(str(key)) else cls._count_keyed(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return sum(cls._count_keyed(item) for item in value)
        return 0

    @staticmethod
    def _semantic_hash(text: str) -> str:
        normalized = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
        return hashlib.sha256(normalized.encode()).hexdigest()

    @staticmethod
    def _split(identifier: str) -> str:
        value = int(identifier[:8], 16) % 100
        return "train" if value < 80 else "validation" if value < 90 else "test"
