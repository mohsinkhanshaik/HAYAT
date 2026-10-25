from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, load_settings
from app.training.readiness import checkpoint_report


TRAINING_PACKAGES = (
    "torch", "transformers", "peft", "trl", "bitsandbytes", "datasets",
    "accelerate", "safetensors",
)


class PrivateFineTuneLifecycle:
    """Candidate-only local adapter lifecycle with evaluation and rollback.

    Preparing a candidate never trains or promotes it.  The data gate,
    private-local boundary, explicit owner approval, baseline comparison, and
    rollback pointer remain separate so no unattended process can turn its own
    output into the production model.
    """

    # The checked-in local runner implements supervised adapter tuning through
    # LoRA/QLoRA. DPO remains a future pipeline profile, not a callable method.
    METHODS = {"LORA", "QLORA"}

    def __init__(self, pipeline, root: Path | None = None,
                 *, config: dict[str, Any] | None = None) -> None:
        self.pipeline = pipeline
        self.project_root = root or PROJECT_ROOT
        self.root = self.project_root / "data" / "training" / "candidates"
        self.root.mkdir(parents=True, exist_ok=True)
        settings = dict(config if config is not None else load_settings().get("training", {}))
        self.minimum = max(100, int(settings.get("minimum_validated_samples", 1000)))
        self.require_validation = bool(settings.get("require_validation_split", True))
        self.require_test = bool(settings.get("require_held_out_test", True))
        self.training_base = str(settings.get("training_base_checkpoint", "")).strip()
        self.active_path = self.root / "active.json"
        self.rollback_path = self.root / "rollback.json"

    def environment(self) -> dict[str, Any]:
        packages: dict[str, str | None] = {}
        for package in TRAINING_PACKAGES:
            try:
                packages[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                packages[package] = None
        checkpoint = checkpoint_report(self.training_base)
        return {
            "packages": packages,
            "dependencies_ready": all(packages.values()),
            "training_base_checkpoint": self.training_base or None,
            "training_base_local": checkpoint["ready"],
            "checkpoint": checkpoint,
            "network_download_allowed": False,
            "cloud_training_allowed": False,
        }

    def gate(self, *, profile: str = "conversation", method: str = "QLORA",
             private_only: bool = True) -> dict[str, Any]:
        if profile not in self.pipeline.CATEGORIES:
            raise ValueError(f"Unsupported training profile: {profile}")
        normalized = method.strip().upper()
        split_counts = self.pipeline.split_counts(
            privacy_scope="PRIVATE_LOCAL" if private_only else "STANDARD",
            category=profile,
        )
        total = sum(split_counts.values())
        validation = self.pipeline.validate_dataset()
        environment = self.environment()
        checks = {
            "supported_method": normalized in self.METHODS,
            "dataset_validation": bool(validation.get("passed")),
            "minimum_samples": total >= self.minimum,
            "training_split": split_counts.get("train", 0) > 0,
            "validation_split": (not self.require_validation) or split_counts.get("validation", 0) > 0,
            "held_out_test": (not self.require_test) or split_counts.get("test", 0) > 0,
            "dependencies": bool(environment["dependencies_ready"]),
            "local_trainable_checkpoint": bool(environment["training_base_local"]),
            "private_local_scope": private_only,
        }
        blockers = [name for name, passed in checks.items() if not passed]
        return {
            "ready": not blockers,
            "profile": profile,
            "method": normalized,
            "privacy_scope": "PRIVATE_LOCAL" if private_only else "STANDARD",
            "records": total,
            "split_counts": split_counts,
            "minimum_validated_samples": self.minimum,
            "checks": checks,
            "blockers": blockers,
            "environment": environment,
            "automatic_training": False,
            "automatic_promotion": False,
            "required_after_training": [
                "held-out task benchmark", "golden regression", "safety regression",
                "latency and memory comparison", "explicit owner promotion",
            ],
        }

    def prepare(self, *, profile: str = "conversation", method: str = "QLORA",
                private_only: bool = True) -> dict[str, Any]:
        gate = self.gate(profile=profile, method=method, private_only=private_only)
        dataset_version = self.pipeline.dataset()["version"]
        identity = json.dumps({
            "profile": profile, "method": gate["method"],
            "privacy_scope": gate["privacy_scope"], "dataset_version": dataset_version,
            "training_base": self.training_base,
        }, sort_keys=True)
        candidate_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        candidate_root = self.root / candidate_id
        candidate_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "created_at": datetime.now(UTC).isoformat(),
            "state": "READY_FOR_EXPLICIT_TRAINING" if gate["ready"] else "BLOCKED",
            "dataset_version": dataset_version,
            "profile": profile,
            "method": gate["method"],
            "privacy_scope": gate["privacy_scope"],
            "local_only": True,
            "training_script": str(self.project_root / "scripts" / "train_hayat_adapter.py"),
            "gate": gate,
            "evaluation": None,
            "adapter_path": None,
        }
        self._write(candidate_root / "manifest.json", manifest)
        return manifest

    def record_adapter(self, candidate_id: str, adapter_path: str | Path,
                       *, training_metrics: dict[str, Any]) -> dict[str, Any]:
        manifest, path = self._candidate(candidate_id)
        if manifest.get("state") != "READY_FOR_EXPLICIT_TRAINING":
            raise RuntimeError("Only a gate-passing candidate can register a trained adapter.")
        resolved = Path(adapter_path).expanduser().resolve()
        if not resolved.is_dir() or not (resolved / "adapter_config.json").is_file():
            raise ValueError("Adapter output must be a local directory containing adapter_config.json.")
        manifest.update({
            "state": "TRAINED_UNVERIFIED", "adapter_path": str(resolved),
            "training_metrics": dict(training_metrics),
            "trained_at": datetime.now(UTC).isoformat(),
        })
        self._write(path, manifest)
        return manifest

    def record_evaluation(self, candidate_id: str, *, baseline_score: float,
                          candidate_score: float, baseline_latency_ms: float,
                          candidate_latency_ms: float, golden_passed: bool,
                          safety_passed: bool) -> dict[str, Any]:
        manifest, path = self._candidate(candidate_id)
        if manifest.get("state") != "TRAINED_UNVERIFIED":
            raise RuntimeError("The adapter must be registered before evaluation.")
        for name, value in (("baseline_score", baseline_score), ("candidate_score", candidate_score),
                            ("baseline_latency_ms", baseline_latency_ms), ("candidate_latency_ms", candidate_latency_ms)):
            if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{name} must be a finite nonnegative measurement.")
        if type(golden_passed) is not bool or type(safety_passed) is not bool:
            raise ValueError("Golden and safety results must be booleans.")
        quality_ok = float(candidate_score) >= float(baseline_score)
        latency_ok = float(candidate_latency_ms) <= max(1.0, float(baseline_latency_ms)) * 1.25
        passed = quality_ok and latency_ok and bool(golden_passed) and bool(safety_passed)
        manifest["evaluation"] = {
            "baseline_score": float(baseline_score), "candidate_score": float(candidate_score),
            "baseline_latency_ms": float(baseline_latency_ms),
            "candidate_latency_ms": float(candidate_latency_ms),
            "quality_not_worse": quality_ok, "latency_within_25_percent": latency_ok,
            "golden_passed": bool(golden_passed), "safety_passed": bool(safety_passed),
            "passed": passed, "evaluated_at": datetime.now(UTC).isoformat(),
        }
        manifest["state"] = "EVALUATED_PASS" if passed else "REJECTED"
        self._write(path, manifest)
        return manifest

    def promote(self, candidate_id: str, *, owner_approved: bool) -> dict[str, Any]:
        if not owner_approved:
            raise PermissionError("Model promotion requires explicit owner approval.")
        manifest, _ = self._candidate(candidate_id)
        if manifest.get("state") != "EVALUATED_PASS":
            raise RuntimeError("Only an evaluation-passing candidate can be promoted.")
        previous = self._read(self.active_path)
        if previous:
            self._write(self.rollback_path, previous)
        active = {
            "candidate_id": candidate_id, "adapter_path": manifest.get("adapter_path"),
            "promoted_at": datetime.now(UTC).isoformat(), "owner_approved": True,
            "evaluation": manifest.get("evaluation"),
        }
        self._write(self.active_path, active)
        return active

    def rollback(self, *, owner_approved: bool) -> dict[str, Any]:
        if not owner_approved:
            raise PermissionError("Rollback requires explicit owner approval.")
        previous = self._read(self.rollback_path)
        if not previous:
            raise RuntimeError("No previous evaluated adapter is available for rollback.")
        current = self._read(self.active_path)
        self._write(self.active_path, previous)
        if current:
            self._write(self.rollback_path, current)
        return previous

    def status(self) -> dict[str, Any]:
        candidates = []
        for path in sorted(self.root.glob("*/manifest.json"), reverse=True):
            value = self._read(path)
            if value:
                candidates.append({
                    "candidate_id": value.get("candidate_id"), "state": value.get("state"),
                    "profile": value.get("profile"), "method": value.get("method"),
                    "records": (value.get("gate") or {}).get("records"),
                    "blockers": (value.get("gate") or {}).get("blockers", []),
                })
        return {
            "active": self._read(self.active_path) or None,
            "rollback_available": bool(self._read(self.rollback_path)),
            "candidates": candidates[:20],
            "environment": self.environment(),
            "automatic_training": False,
            "automatic_promotion": False,
        }

    def _candidate(self, candidate_id: str) -> tuple[dict[str, Any], Path]:
        if not candidate_id or any(character not in "0123456789abcdef" for character in candidate_id):
            raise ValueError("Invalid candidate id.")
        path = self.root / candidate_id / "manifest.json"
        manifest = self._read(path)
        if not manifest:
            raise KeyError(candidate_id)
        return manifest, path

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
