from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PlanCritique:
    approved: bool
    issues: list[str]


class PlanCritic:
    """Fast structural critic. It runs only for standard/complex plans."""

    def check(self, plan: list[Any], intent: Any) -> PlanCritique:
        tools = [step.tool for step in plan]
        issues: list[str] = []
        criteria = getattr(intent, "success_criteria", [])
        if any(item.type == "ui_text_contains" for item in criteria) and "notepad_edit" not in tools:
            issues.append("The plan does not edit and read back the requested Notepad text.")
        if any(item.type == "target_window_absent" for item in criteria) and "close_application" not in tools:
            issues.append("The plan does not close and verify the intended application window.")
        if any(item.type == "browser_destination" for item in criteria) and not any(tool in {"browser_navigate", "browser_read_page"} for tool in tools):
            issues.append("The plan does not navigate to and observe the requested browser destination.")
        if not plan:
            issues.append("An action intent has no executable steps.")
        return PlanCritique(not issues, issues)
