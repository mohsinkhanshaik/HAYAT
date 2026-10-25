from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.config import PROJECT_ROOT, load_settings
from app.research.knowledge import KnowledgeStore
from app.research.text import tokens

# Queries naming a person's contact details are one-off lookups, not reusable
# skills, and the candidate file would keep the address on disk.
_EMAIL = re.compile(r"(?i)\b[a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9.-]+\.[a-z]{2,24}\b")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")


def _names_a_person(query: str) -> bool:
    # Ten or more digits keeps dates ("2026-10-09") and versions out.
    return bool(_EMAIL.search(query)) or any(
        sum(char.isdigit() for char in match.group()) >= 10 for match in _PHONE.finditer(query)
    )


class ResearchSkillLearner:
    """Creates review-only declarative candidates after repeated successful research patterns."""

    def __init__(self, knowledge: KnowledgeStore, directory: str | Path | None = None) -> None:
        configured = directory or load_settings().get("skills_directory", "data/skills")
        root = Path(configured)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        self.candidates = root / "candidates"
        self.candidates.mkdir(parents=True, exist_ok=True)
        self.knowledge = knowledge

    def consider(self, query: str, mode: str) -> Path | None:
        if _names_a_person(query):
            return None
        if self.knowledge.similar_run_count(query) < 3:
            return None
        key_words = [word for word in tokens(query) if word not in {"using", "official", "primary", "current"}][:5]
        name = "research_" + "_".join(key_words or ["topic"])
        name = re.sub(r"[^a-z0-9_]+", "_", name)[:64].strip("_")
        path = self.candidates / f"{name}.yaml"
        if path.exists():
            return path
        definition = {
            "name": name,
            "description": f"Reviewable candidate inferred from repeated successful research similar to: {query[:180]}",
            "inputs": ["query"],
            "tools": ["research_topic"],
            "steps": [{"tool": "research_topic", "arguments": {"query": "{{query}}", "level": mode.lower(), "official_only": mode == "PRIMARY_ONLY", "remember": True}}],
            "success_criteria": ["research returns validated claims with retrieved citation evidence"],
            "trusted": False,
        }
        path.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
        return path

    def status(self) -> dict[str, object]:
        rows = sorted(self.candidates.glob("*.yaml"))
        return {
            "candidates": len(rows),
            "directory": str(self.candidates),
            "latest": [path.stem for path in rows[-5:]],
            "promotion": "explicit_owner_review",
            "automatic_execution": False,
        }
