from __future__ import annotations

import re
from typing import Any

from app.agent.planner import PlanStep
from app.tools.registry import ToolRegistry, ToolResult


class Executor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(self, step: PlanStep, history: list[dict[str, Any]]) -> ToolResult:
        arguments = self._resolve(step.arguments, history)
        return self.registry.execute(step.tool, arguments)

    def _resolve(self, value: Any, history: list[dict[str, Any]]) -> Any:
        if value == '{{desktop_observation_id}}':
            for event in reversed(history):
                if event.get('tool') != 'computer_observe':
                    continue
                result = event.get('result') or {}
                output = result.get('output') or {}
                token = output.get('observation_id') if isinstance(output, dict) else None
                if (result.get('success') and (event.get('verification') or {}).get('passed')
                        and isinstance(token, str) and token):
                    return token
                raise ValueError('The latest desktop observation is unverified; observe again before guarded input.')
            raise ValueError('Guarded input needs a verified computer_observe result; no observation token is available.')
        if value == "{{python_report_from_previous}}":
            matches: list[str] = []
            for event in reversed(history):
                output = event.get("result", {}).get("output") or {}
                if isinstance(output, dict) and isinstance(output.get("matches"), list):
                    matches = output["matches"]
                    break
            lines = ["# Python Files Report", "", f"Total Python files: **{len(matches)}**", "", "## Files", ""]
            lines.extend(f"- `{path}`" for path in matches)
            return "\n".join(lines) + "\n"
        if isinstance(value, dict):
            return {key: self._resolve(item, history) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve(item, history) for item in value]
        # A model plan wrote "{{steps.0.output.contacts[0].id}}" and the tool got it literally (live, 2026-10-24).
        # Only the two placeholders above are filled in; a reference to another step's output is refused here.
        # Ordinary braces in content ("{{ name }}" in a template the owner asked for) are left alone.
        if isinstance(value, str) and _STEP_REFERENCE.search(value):
            raise ValueError("This plan step refers to an earlier step's output, which HAYAT cannot fill in; "
                             "nothing was run with it. Ask again with the exact name or ID.")
        return value


_STEP_REFERENCE = re.compile(r"\{\{\s*(?:steps?|step_?\d+|previous|prev|results?|outputs?)\s*[.\[]", re.IGNORECASE)
