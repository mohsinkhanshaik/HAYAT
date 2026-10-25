"""Run the opt-in checks skipped in an earlier HAYAT JUnit report.

The selected tests keep their assertions and fixture isolation. Live checks
start the existing folder runtime and stop only the server started here.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from defusedxml import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def selected_checks(baseline: Path, group: str, selection: str = "skipped") -> list[str]:
    checks = []
    for case in ET.parse(baseline).getroot().iter("testcase"):
        skipped = case.find("skipped")
        failed = case.find("failure") is not None or case.find("error") is not None
        if (selection == "skipped" and skipped is None) or (selection == "failed" and not failed):
            continue
        screenshot = ("screenshot" in skipped.attrib.get("message", "").lower()) if skipped is not None else "capture" in case.attrib["name"]
        if group != "all" and screenshot != (group == "screenshots"):
            continue
        module = case.attrib["classname"]
        path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
        if not path.is_file() or not path.resolve().is_relative_to((ROOT / "tests").resolve()):
            raise ValueError("Skipped check does not identify a local test module: " + module)
        checks.append(path.relative_to(ROOT).as_posix() + "::" + case.attrib["name"])
    return list(dict.fromkeys(checks))


def check_result(report: Path, expected: int) -> dict:
    """A rerun is complete only if every selected check actually ran and passed."""
    if not report.is_file():
        return {"complete": False, "error": "The selected run produced no JUnit report."}
    cases = list(ET.parse(report).getroot().iter("testcase"))
    failed = [case.attrib["name"] for case in cases if case.find("failure") is not None or case.find("error") is not None]
    skipped = [case.attrib["name"] for case in cases if case.find("skipped") is not None]
    return {"complete": len(cases) == expected and not failed and not skipped,
            "expected": expected, "ran": len(cases), "failed": failed, "skipped": skipped,
            "passed": len(cases) - len(failed) - len(skipped)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=ROOT / ".test-runs/job-hunting-full-suite.xml")
    parser.add_argument("--group", choices=("all", "live", "screenshots"), default="all")
    parser.add_argument("--selection", choices=("skipped", "failed"), default="skipped")
    parser.add_argument("--output", type=Path, default=ROOT / ".test-runs/pending-checks")
    args = parser.parse_args()
    checks = selected_checks(args.baseline, args.group, args.selection)
    args.output.mkdir(parents=True, exist_ok=True)
    inventory = {"baseline": str(args.baseline), "group": args.group, "selection": args.selection, "selected": checks}
    (args.output / (args.group + "-selected.json")).write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    if not checks:
        print(f"No matching {args.selection} checks in this report.")
        return 0
    command = [sys.executable, "-m", "pytest", *checks, "-v", "-s", "--tb=short",
               "--basetemp=" + str(args.output / (args.group + "-temp")),
               "--junitxml=" + str(args.output / (args.group + ".xml"))]
    flags = {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "HAYAT_SHOTS": "1",
             "HAYAT_LIVE": "1", "HAYAT_RUN_WORLD_LIVE": "1", "HAYAT_DISABLE_BACKGROUND": "1"}
    def run(env):
        print(f"Running {len(checks)} previously {args.selection} {args.group} checks", flush=True)
        result = subprocess.run(command, cwd=ROOT, env={**os.environ, **env, **flags}, check=False)
        summary = check_result(args.output / (args.group + ".xml"), len(checks))
        (args.output / (args.group + "-result.json")).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary), flush=True)
        return result.returncode or (0 if summary["complete"] else 1)
    if args.group == "screenshots":
        return run(os.environ)
    from app.launcher import folder_runtime
    with folder_runtime(lambda message: print(message, flush=True)) as environment_with_runtime:
        return run(environment_with_runtime)


if __name__ == "__main__":
    raise SystemExit(main())
