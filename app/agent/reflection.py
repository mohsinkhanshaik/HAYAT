from __future__ import annotations

import json
from typing import Any

from app.memory.database import MemoryDatabase


class Reflector:
    def __init__(self, memory: MemoryDatabase) -> None:
        self.memory = memory

    def reflect(self, task_id: str, goal: str, history: list[dict[str, Any]], succeeded: bool) -> dict[str, Any]:
        failures = [event for event in history if not event.get("verification", {}).get("passed", False)]
        successful_tools = [event.get("tool") for event in history if event.get("verification", {}).get("passed")]
        failed_steps = []
        for event in failures:
            result = event.get("result") or {}
            verification = event.get("verification") or {}
            reason = str(result.get("error") or verification.get("reason") or "No observed failure reason")[:300]
            failed_steps.append({
                "tool": event.get("tool"),
                "reason": reason,
                "expected": event.get("expected") or {},
                "observed": {
                    "success": result.get("success"),
                    "exit_code": (result.get("output") or {}).get("exit_code")
                    if isinstance(result.get("output"), dict) else None,
                    "permission": result.get("permission"),
                },
            })
        category = self._category(history, succeeded)
        next_time = (
            f"Inspect the {failed_steps[0]['tool']} failure and its expected check before retrying: "
            f"{failed_steps[0]['reason']}" if failed_steps else
            "Reuse the verified steps when a matching task occurs."
        )
        reflection = {
            "what_worked": successful_tools,
            "what_failed": [step["reason"] for step in failed_steps],
            "why": "Tool evidence and explicit verification determined the outcome.",
            "reusable_skill_candidate": succeeded and len(successful_tools) >= 3,
            "next_time": next_time,
            "failure_category": category,
            "intent": next((event.get("intent") for event in history if event.get("intent")), None),
            "plan": [{"tool": event.get("tool"), "verified": bool(event.get("verification", {}).get("passed"))}
                     for event in history],
            "expected_vs_actual": failed_steps,
        }
        self.memory.store_memory("reflection", json.dumps(reflection, ensure_ascii=False, default=str),
                                 0.7 if succeeded else 0.8, "reflection", task_id,
                                 {"goal": goal, "succeeded": succeeded, "failure_category": category,
                                  "failed_tool": failed_steps[0]["tool"] if failed_steps else None})
        return reflection

    @staticmethod
    def _category(history: list[dict[str, Any]], succeeded: bool) -> str | None:
        if succeeded:
            return None
        if not history:
            return "PLANNING"
        if any(event.get("result", {}).get("permission") == "DENY" for event in history):
            return "EXECUTION"
        if any(not event.get("result", {}).get("success", False) for event in history):
            return "EXECUTION"
        if any(not event.get("verification", {}).get("passed", False) for event in history):
            return "VERIFICATION"
        return "TOOL_SELECTION"
