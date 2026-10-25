from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from app.skills.loader import SkillLoader
from app.tools.registry import ToolRegistry, ToolResult


class SkillRegistry:
    def __init__(self, tools: ToolRegistry, loader: SkillLoader | None = None) -> None:
        self.tools = tools
        self.loader = loader or SkillLoader()
        self.skills: dict[str, dict[str, Any]] = {}

    def reload(self) -> None:
        self.skills.clear()
        builtin = SkillLoader(Path(__file__).parent / 'builtin')
        for skill in [*builtin.discover(), *self.loader.discover()]:
            if skill.get("valid") is False:
                continue
            unknown = set(skill["tools"]) - set(self.tools.names())
            if unknown:
                continue
            self.skills[skill["name"]] = skill

    def execute(self, name: str, inputs: dict[str, Any]) -> list[ToolResult]:
        skill = self.skills[name]
        if not skill.get("trusted", False):
            raise PermissionError("Untrusted skills may be inspected but cannot be activated.")
        missing = set(skill["inputs"]) - set(inputs)
        if missing:
            raise ValueError(f"Missing skill inputs: {sorted(missing)}")
        results = []
        for step in skill["steps"]:
            arguments = substitute(deepcopy(step["arguments"]), inputs)
            result = self.tools.execute(step["tool"], arguments)
            results.append(result)
            if not result.success:
                break
        return results


def substitute(value: Any, inputs: dict[str, Any]) -> Any:
    if isinstance(value, str):
        exact = re.fullmatch(r'\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}', value)
        if exact and exact[1] in inputs:
            return deepcopy(inputs[exact[1]])
        for key, replacement in inputs.items():
            value = value.replace("{{" + key + "}}", str(replacement))
        return value
    if isinstance(value, dict):
        return {key: substitute(item, inputs) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, inputs) for item in value]
    return value
