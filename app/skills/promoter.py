from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.memory.database import MemoryDatabase
from app.skills.loader import SkillLoader


class SkillPromoter:
    """Creates reviewable YAML candidates; trust is granted only by explicit promotion."""

    def __init__(self, memory: MemoryDatabase, loader: SkillLoader) -> None:
        self.memory = memory
        self.loader = loader
        self.candidate_dir = self.loader.directory / "candidates"
        self.candidate_dir.mkdir(parents=True, exist_ok=True)

    def propose_from_task(self, task_id: str, name: str | None = None) -> Path:
        with self.memory.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE id=? AND status='complete'", (task_id,)).fetchone()
            actions = db.execute("SELECT * FROM actions WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        if task is None or not actions:
            raise ValueError("Only completed tasks with recorded actions can become skill candidates.")
        skill_name = sanitize_name(name or task["goal"])
        tools = []
        steps = []
        for action in actions:
            verification = yaml.safe_load(action["verification_json"])
            if not verification.get("passed"):
                continue
            tool = action["tool"]
            if tool not in tools:
                tools.append(tool)
            steps.append({"tool": tool, "arguments": yaml.safe_load(action["parameters_json"])})
        definition: dict[str, Any] = {
            "name": skill_name,
            "description": f"Reviewable procedure learned from completed task: {task['goal']}",
            "inputs": [],
            "tools": tools,
            "steps": steps,
            "success_criteria": ["every recorded tool step succeeds under current permissions"],
            "trusted": False,
        }
        path = self.candidate_dir / f"{skill_name}.yaml"
        path.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
        return path

    def promote(self, name: str) -> Path:
        candidate = self.candidate_dir / f"{sanitize_name(name)}.yaml"
        definition = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        definition["trusted"] = True
        destination = self.loader.directory / candidate.name
        destination.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
        self.loader.load(destination)
        return destination


def sanitize_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:64]
    if len(name) < 3:
        name = f"skill_{name or 'task'}"
    if not name[0].isalpha():
        name = f"skill_{name}"
    return name
