from __future__ import annotations

import inspect
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema.validators import validator_for

from app.config import PROJECT_ROOT


SAFE_DEEP_PROBES: dict[str, dict[str, Any]] = {
    "system_status": {},
    "display_status": {},
    "clipboard_status": {},
    "camera_status": {},
    "secret_broker_status": {},
    "browser_bridge_status": {},
    "proactive_rules": {},
    "lab_catalog": {},
    "discover_applications": {},
    "get_agent_state": {},
    "public_document_learning_status": {},
}
VALID_RISKS = {"low", "medium", "high", "critical"}


class ToolBenchmark:
    """Audit every registered tool without pretending every tool is safe to run.

    Every tool receives schema/handler/permission coverage. Historical calls
    supply real latency and reliability. A small explicit allowlist of passive,
    non-content probes may run in deep mode; mutating and private-read tools are
    never invoked merely for a benchmark.
    """

    def __init__(self, registry: Any, understanding: Any,
                 *, artifact: Path | None = None) -> None:
        self.registry = registry
        self.understanding = understanding
        self.artifact = artifact or PROJECT_ROOT / "data/benchmarks/tools/latest.json"

    def run(self, *, deep: bool = False) -> dict[str, Any]:
        started = time.perf_counter()
        probe_results: dict[str, dict[str, Any]] = {}
        if deep:
            for name, arguments in SAFE_DEEP_PROBES.items():
                if name not in self.registry.names():
                    continue
                self.registry.reset_budget()
                result = self.registry.execute(name, arguments)
                probe_results[name] = {
                    "success": result.success, "duration_ms": result.duration_ms,
                    "error_kind": str(result.error).split(":", 1)[0] if result.error else "",
                }

        rows = []
        for name in self.registry.names():
            spec = self.registry.get(name)
            static = self._static(spec)
            observed = self.understanding.record_for(name) if self.understanding else {}
            probe = probe_results.get(name)
            recommendation = self._recommendation(static, observed, probe)
            rows.append({
                "name": name, "description": spec.description,
                "risk": spec.risk, "permission": spec.permission,
                "static": static, "historical": observed or None,
                "live_probe": probe,
                "execution_policy": (
                    "passive explicit probe" if name in SAFE_DEEP_PROBES
                    else "not executed by benchmark: may mutate state, expose private content, require a target, or cause external effects"
                ),
                "recommendation": recommendation,
            })

        observed_rows = [row for row in rows if row["historical"]]
        sufficient = [row for row in observed_rows if row["historical"].get("enough_evidence")]
        unreliable = [row for row in sufficient if row["historical"].get("failure_rate", 0) >= .34]
        slow = [row for row in observed_rows if row["historical"].get("avg_ms", 0) >= 3000]
        static_failed = [row for row in rows if not row["static"]["passed"]]
        probed = [row for row in rows if row["live_probe"] is not None]
        report = {
            "generated_at": datetime.now(UTC).isoformat(), "deep": bool(deep),
            "scope": "every registered tool",
            "summary": {
                "registered": len(rows), "static_passed": len(rows) - len(static_failed),
                "static_failed": len(static_failed), "historically_observed": len(observed_rows),
                "sufficient_runtime_evidence": len(sufficient),
                "unobserved": len(rows) - len(observed_rows),
                "live_probed": len(probed),
                "live_probe_passed": sum(bool(row["live_probe"]["success"]) for row in probed),
                "unreliable": len(unreliable), "slow": len(slow),
            },
            "static_failures": [row["name"] for row in static_failed],
            "unreliable_tools": [row["name"] for row in unreliable],
            "slow_tools": [row["name"] for row in slow],
            "optimization_queue": [
                {"tool": row["name"], "recommendation": row["recommendation"]}
                for row in rows if row["recommendation"] != "No measured optimization needed."
            ],
            "tools": rows,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "safety": "Static coverage for all tools; only explicit passive probes execute. No mutation, private reads, credentials, downloads, messages, or external writes are benchmark-triggered.",
        }
        self.artifact.parent.mkdir(parents=True, exist_ok=True)
        self.artifact.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report | {"artifact": str(self.artifact)}

    def status(self) -> dict[str, Any]:
        if not self.artifact.is_file():
            return {"available": False, "artifact": str(self.artifact)}
        try:
            report = json.loads(self.artifact.read_text(encoding="utf-8"))
            return {"available": True, "artifact": str(self.artifact),
                    "generated_at": report.get("generated_at"), "summary": report.get("summary", {})}
        except Exception as exc:
            return {"available": False, "artifact": str(self.artifact),
                    "error": f"{type(exc).__name__}: {exc}"}

    def _static(self, spec: Any) -> dict[str, Any]:
        issues: list[str] = []
        try:
            validator_for(spec.input_schema).check_schema(spec.input_schema)
            schema_valid = True
        except Exception as exc:
            schema_valid = False
            issues.append(f"invalid JSON schema: {type(exc).__name__}")
        callable_handler = callable(spec.handler)
        if not callable_handler:
            issues.append("handler is not callable")
        if str(spec.risk).lower() not in VALID_RISKS:
            issues.append("unknown risk label")
        if not str(spec.permission).strip():
            issues.append("missing permission category")
        signature_compatible = True
        try:
            signature = inspect.signature(spec.handler)
            parameters = signature.parameters
            accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
            schema_properties = set(spec.input_schema.get("properties", {}))
            unknown = schema_properties - set(parameters)
            if unknown and not accepts_kwargs:
                signature_compatible = False
                issues.append("schema fields absent from handler: " + ", ".join(sorted(unknown)))
        except (TypeError, ValueError):
            signature_compatible = True
        return {
            "passed": not issues, "schema_valid": schema_valid,
            "callable": callable_handler, "signature_compatible": signature_compatible,
            "permission_declared": bool(str(spec.permission).strip()), "issues": issues,
        }

    @staticmethod
    def _recommendation(static: dict[str, Any], observed: dict[str, Any],
                        probe: dict[str, Any] | None) -> str:
        if not static["passed"]:
            return "Repair static contract: " + "; ".join(static["issues"])
        if probe is not None and not probe["success"]:
            return f"Investigate passive probe failure ({probe['error_kind'] or 'unknown'})."
        if not observed:
            return "Add a deterministic isolated fixture benchmark; never exercise side effects on live user state."
        if observed.get("enough_evidence") and observed.get("failure_rate", 0) >= .34:
            return "Reliability is below threshold; inspect common failure class, reproduce in isolation, patch, and regress."
        if observed.get("avg_ms", 0) >= 3000:
            return "Latency exceeds 3 seconds; profile I/O/model waits and add caching or bounded concurrency where semantics permit."
        if not observed.get("enough_evidence"):
            return "Collect at least five non-refused executions before drawing a reliability conclusion."
        return "No measured optimization needed."
