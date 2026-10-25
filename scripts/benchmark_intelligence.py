from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.core import TaskOutcome
from app.agent.verifier import Verifier
from app.intent.models import IntentKind
from app.runtime import build_runtime
from app.session import SessionState
from app.tools.registry import ToolResult


def main() -> int:
    runtime = build_runtime(use_model=False)
    state = SessionState()
    checks: list[tuple[str, bool]] = []
    try:
        router = runtime.intent_router
        planner = runtime.agent.planner
        greeting = router.route("Hi", state)
        checks.append(("instruction_understanding", greeting.intent == IntentKind.CONVERSATION and not greeting.requires_tools))
        opened = router.route("Open Notepad", state)
        checks.append(("action_understanding", opened.intent == IntentKind.COMPUTER_CONTROL and opened.requires_tools))
        state.update_from_outcome(TaskOutcome("x", "Open Notepad", True, "", [], [{"result": {"success": True, "output": {"application": "notepad", "window": {"handle": 77}}}}]))
        referenced = router.route("Write hello in it", state)
        checks.append(("reference_resolution", referenced.entities.get("application") == "notepad" and referenced.entities.get("reference_resolved") is True))
        plan = planner.create_plan(referenced.goal, intent=referenced, session=state)
        checks.append(("planning", [step.tool for step in plan] == ["notepad_edit"] and plan[0].arguments.get("handle") == 77))
        close = router.route("Close it", state)
        checks.append(("logical_consistency", close.success_criteria[0].type == "target_window_absent"))
        feedback = router.route("That didn't work; you only opened Notepad", state)
        checks.append(("error_correction", feedback.intent == IntentKind.DIAGNOSTIC and not feedback.requires_tools))
        research = router.route("Research the latest Python release", state)
        checks.append(("research_use", research.intent == IntentKind.RESEARCH and research.requires_research))
        memory = router.route("Remember that Atlas is my main project", state)
        checks.append(("memory_use", memory.intent == IntentKind.MEMORY and not memory.requires_tools))
        research_plan = planner.create_plan(research.goal, intent=research, session=state)
        checks.append(("tool_selection", [step.tool for step in research_plan] == ["research_topic"]))
        contradiction = Verifier().check({"type": "target_window_absent", "application": "notepad"}, ToolResult("close", True, {"application": "notepad", "closed": True, "target_window_exists": True}))
        checks.append(("verification", not contradiction.passed))
    finally:
        runtime.close()
    details = [{"capability": name, "passed": passed} for name, passed in checks]
    report = {"cases": len(details), "behavior_accuracy": sum(passed for _, passed in checks) / len(details), "details": details}
    destination = PROJECT_ROOT / "data/benchmarks/intelligence/after.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "details"}, indent=2))
    print(destination)
    return 0 if report["behavior_accuracy"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
