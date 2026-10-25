from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_settings
from app.policy.cost import CostPolicy
from app.release_evidence import EVIDENCE_DIR, build_manifest, run_check, source_state, verify_manifest

LATEST = PROJECT_ROOT / EVIDENCE_DIR / "latest.json"
# Give pytest its own temp base under the project instead of the shared
# per-user one. On Windows the shared base keeps a `pytest-current` symlink
# that a still-open handle from a concurrent run can lock; pytest then raises
# PermissionError in session teardown -- after every test has passed -- and the
# gate reported a false "blocked". It must not be under data/: the test
# sandbox moves anything written there to one shared sandbox folder, so tests'
# own files vanished and their databases mixed (the gate failed that way from
# 2026-09 until 2026-10-14).
PYTEST_BASETEMP = PROJECT_ROOT / ".test-runs" / "release-pytest"


def gate(root: Path = PROJECT_ROOT) -> dict:
    python = sys.executable
    run_id = datetime.now(UTC).strftime("release-%Y%m%d-%H%M%S")
    run_dir = root / EVIDENCE_DIR / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    junit = run_dir / "pytest.xml"
    before = source_state(root)
    checks = [
        run_check("compile", [python, "-m", "compileall", "-q", "app", "scripts", "tests"], root=root, timeout=180),
        run_check("tests", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--basetemp={PYTEST_BASETEMP}",
                            f"--junitxml={junit}"], root=root, timeout=1800, junit=junit),
        run_check("generated_docs", [python, "scripts/generate_reference.py", "--check"], root=root, timeout=180),
        run_check("doctor", [python, "scripts/doctor.py"], root=root, timeout=300),
    ]
    policy = CostPolicy.from_settings(load_settings().get("cost_policy", {}))
    try:
        policy.assert_zero_cost(); policy_passed = True; policy_error = ""
    except Exception as exc:
        policy_passed = False; policy_error = str(exc)
    checks.append({"name": "zero_cost", "command": ["in-process CostPolicy.assert_zero_cost()"],
                   "in_process_passed": policy_passed, "passed": policy_passed,
                   "detail": policy.status(), "error": policy_error})
    manifest = build_manifest(checks, before, source_state(root)) | {"id": run_id}
    text = json.dumps(manifest, indent=2, default=str)
    (run_dir / "manifest.json").write_text(text, encoding="utf-8")
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(text, encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the release gate, or re-verify a recorded manifest.")
    parser.add_argument("--verify", nargs="?", const=str(LATEST), metavar="MANIFEST",
                        help="Re-check a manifest (default: the latest) against the current tree and files.")
    args = parser.parse_args(argv)
    if args.verify:
        try:
            manifest = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(json.dumps({"passed": False, "promotion": "blocked", "problems": [f"No readable manifest: {exc}"]}))
            return 1
        result = verify_manifest(manifest, PROJECT_ROOT)
        print(json.dumps({key: result[key] for key in ("passed", "promotion", "problems")} |
                         {"manifest": args.verify}, indent=2))
        return 0 if result["passed"] else 1
    manifest = gate()
    print(json.dumps({"passed": manifest["passed"], "promotion": manifest["promotion"], "artifact": str(LATEST),
                      "source": manifest["source"], "problems": manifest["problems"],
                      "checks": [{"name": row["name"], "passed": row["passed"]} for row in manifest["checks"]]},
                     indent=2))
    return 0 if manifest["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
