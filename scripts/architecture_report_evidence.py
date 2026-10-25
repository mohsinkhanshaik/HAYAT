from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from xml.etree.ElementTree import ParseError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.runtime import build_runtime
from app.config import load_yaml
from app.release_evidence import junit_outcomes as regression_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect live evidence for the HAYAT architecture report.")
    parser.add_argument("--junitxml", type=Path, required=True,
                        help="Completed pytest JUnit evidence; counts are never supplied by hand.")
    parser.add_argument("--output", type=Path,
                        default=PROJECT_ROOT / "data/benchmarks/architecture/latest.json")
    args = parser.parse_args()

    try:
        release = regression_evidence(args.junitxml)
    except (OSError, ValueError, ParseError) as exc:
        parser.error(str(exc))

    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        brain = runtime.brain_mesh.self_check(deep=True)
        brain_benchmark = runtime.brain_mesh.benchmark(iterations=20)
        learning = runtime.learning_architecture.inventory(refresh=True)
        alignment = runtime.learning_architecture.alignment()
        memory_integrity = runtime.memory.verify_memory_integrity(limit=100_000)
        security = runtime.security.audit()
        knowledge = runtime.research.knowledge.stats()
        evidence = {
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": str(PROJECT_ROOT),
            "release": release,
            "brain_mesh": brain,
            "brain_benchmark": brain_benchmark,
            "learning": {
                "layers": learning.get("layers", {}),
                "research_alignment": learning.get("research_alignment", {}),
                "recall_health": learning.get("recall_health", {}),
                "databases": learning.get("databases", {}),
                "alignment": alignment,
            },
            "memory_integrity": memory_integrity,
            "knowledge": knowledge,
            "security": {
                "passed": bool(security["pip_check"]["passed"] and security["secret_scan"]["passed"]),
                "pip_check": security["pip_check"],
                "secret_scan": security["secret_scan"],
                "download_defense": security["download_defense"],
                "prohibited_capabilities": security["prohibited_capabilities"],
            },
            "runtime": {
                "identity_model": load_yaml("config/models.yaml").get("identity_model", {}),
                "tools": len(runtime.registry.names()),
                "models": runtime.router.available(),
                "zero_cost": runtime.cost_policy.status(),
                "voice": runtime.voice.status() if runtime.voice else {},
                "camera": runtime.camera.status(),
            },
        }
    finally:
        runtime.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"written": str(args.output), "passed": evidence["brain_mesh"]["passed"],
                      "tests": evidence["release"]["tests_passed"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
