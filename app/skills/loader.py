from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from jsonschema import validate

from app.config import PROJECT_ROOT, load_settings


SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]{2,63}$"},
        "description": {"type": "string", "minLength": 10},
        "inputs": {"type": "array", "items": {"type": "string"}},
        "tools": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}},
                "required": ["tool", "arguments"],
                "additionalProperties": False,
            },
            "minItems": 1,
        },
        "success_criteria": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "trusted": {"type": "boolean"},
    },
    "required": ["name", "description", "inputs", "tools", "steps", "success_criteria", "trusted"],
    "additionalProperties": False,
}


class SkillLoader:
    def __init__(self, directory: str | Path | None = None) -> None:
        configured = directory or load_settings()["skills_directory"]
        self.directory = Path(configured)
        if not self.directory.is_absolute():
            self.directory = PROJECT_ROOT / self.directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def load(self, path: str | Path) -> dict[str, Any]:
        candidate = Path(path)
        candidate = candidate if candidate.is_absolute() else self.directory / candidate
        resolved = candidate.resolve()
        if self.directory.resolve() not in resolved.parents:
            raise PermissionError("Skill must be stored inside the configured skills directory.")
        if resolved.suffix.lower() not in {".yaml", ".yml"}:
            raise ValueError("Skills must be declarative YAML files.")
        data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        validate(data, SKILL_SCHEMA)
        declared = set(data["tools"])
        used = {step["tool"] for step in data["steps"]}
        if not used.issubset(declared):
            raise ValueError("Every step tool must be declared in the skill tools list.")
        return data | {"path": str(resolved)}

    def discover(self) -> list[dict[str, Any]]:
        skills = []
        for path in sorted([*self.directory.glob("*.yaml"), *self.directory.glob("*.yml")]):
            try:
                skills.append(self.load(path))
            except Exception as exc:
                skills.append({"name": path.stem, "path": str(path), "valid": False, "error": str(exc)})
        return skills
