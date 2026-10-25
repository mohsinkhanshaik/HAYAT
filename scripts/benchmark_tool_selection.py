from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.planner import Planner
from app.intent.router import IntentRouter
from app.runtime import build_runtime


CASES = [
    ("Open Notepad.", ["launch_application"]),
    ("Close Notepad.", ["close_application"]),
    ("Write hello in Notepad.", ["launch_application", "notepad_edit"]),
    ("Create a folder called Research.", ["create_folder"]),
    ("Create a text file called note.txt containing hello.", ["write_file", "read_file"]),
    ("Inspect this project and list the files.", ["list_folder"]),
    ("Open my AI project folder.", ["open_project_folder"]),
    ("Create a Python script that prints Hello from my AI and execute it.", ["write_file", "python_execute_file"]),
    ("Research the current Python release using official sources.", ["research_topic"]),
    ("Look this up quickly: current Ollama documentation.", ["research_topic"]),
    ("Why do you believe that? What evidence supports that?", ["explain_knowledge"]),
    ("Find all Python files and create data/artifacts/python_report.md.", ["search_files", "write_file", "file_exists", "read_file"]),
]


def main() -> int:
    runtime = build_runtime(use_model=False)
    details = []
    try:
        planner: Planner = runtime.agent.planner
        router: IntentRouter = runtime.intent_router
        for utterance, expected in CASES:
            intent = router.route(utterance, runtime.session)
            plan = planner.create_plan(intent.goal, intent=intent, session=runtime.session)
            actual = [step.tool for step in plan]
            missing = [tool for tool in expected if tool not in actual]
            wrong = [tool for tool in actual if tool not in expected]
            details.append({"utterance": utterance, "expected": expected, "actual": actual, "missing": missing, "wrong": wrong, "passed": not missing and not wrong})
    finally:
        runtime.close()
    total = len(details)
    report = {
        "cases": total,
        "correct_tool_rate": sum(item["passed"] for item in details) / total,
        "wrong_tool_rate": sum(bool(item["wrong"]) for item in details) / total,
        "missing_tool_rate": sum(bool(item["missing"]) for item in details) / total,
        "details": details,
    }
    destination = PROJECT_ROOT / "data/benchmarks/tool_selection/after.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "details"}, indent=2))
    print(destination.resolve())
    return 0 if report["correct_tool_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
