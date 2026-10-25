from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT


@dataclass(slots=True)
class ProjectContext:
    name: str
    root: str
    goals: list[str]
    architecture: str
    known_issues: list[str]


class ProjectManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PROJECT_ROOT / "data/memory/projects.json"
        existed = self.path.is_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.projects: dict[str, ProjectContext] = {}
        self.active_name: str | None = None
        self._load()
        if "AI" not in self.projects:
            self.projects["AI"] = ProjectContext("AI", str(PROJECT_ROOT), ["Build a reliable personal Windows AI operating layer."], "See docs/architecture.md", [])
        if self.active_name is None and not existed:
            self.active_name = "AI"
        self._save()

    @property
    def active(self) -> ProjectContext | None:
        return self.projects.get(self.active_name or "")

    def use(self, name: str) -> ProjectContext:
        match = next((key for key in self.projects if key.lower() == name.strip().lower()), None)
        if match is None:
            raise KeyError(f"Unknown project {name!r}. Registered: {', '.join(sorted(self.projects))}")
        self.active_name = match
        self._save()
        return self.projects[match]

    def register(self, name: str, root: str) -> ProjectContext:
        name = name.strip()
        if not name or len(name) > 80 or any(char in name for char in "\\/\r\n\t"):
            raise ValueError("Choose a project name of 1 to 80 characters without path separators.")
        folder = Path(root.strip().strip('"')).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError(f"Project folder does not exist: {folder}")
        match = next((key for key in self.projects if key.casefold() == name.casefold()), None)
        if match:
            raise ValueError(f"Project {match!r} is already registered.")
        project = ProjectContext(name, str(folder), [], "", [])
        self.projects[name] = project
        self._save()
        return project

    def clear(self) -> None:
        self.active_name = None
        self._save()

    def status(self) -> dict[str, Any]:
        project = self.active
        if project is None:
            return {"active": None, "projects": sorted(self.projects)}
        root = Path(project.root)
        recent = []
        if root.is_dir():
            candidates: list[Path] = []
            for current, folders, files in os.walk(root):
                folders[:] = [name for name in folders if name not in {".git", ".venv", "__pycache__", "browser-profiles", "models"}]
                candidates.extend(Path(current) / name for name in files)
            recent = [str(path.relative_to(root)) for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:10]]
        git = self._git(root)
        return {"active": asdict(project), "recent_files": recent, "git": git, "projects": sorted(self.projects)}

    def quick_status(self) -> dict[str, Any]:
        """Project identity without recursively walking the tree or invoking git."""
        project = self.active
        return {
            "active": asdict(project) if project else None,
            "projects": sorted(self.projects), "recent_files": [],
            "git": {"state": "not_scanned_in_quick_snapshot"},
        }

    @staticmethod
    def _git(root: Path) -> dict[str, Any]:
        if not (root / ".git").exists():
            return {"repository": False}
        try:
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True, timeout=3, check=True).stdout.strip()
            changes = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=3, check=True).stdout.splitlines()
            return {"repository": True, "branch": branch, "clean": not changes, "changes": len(changes)}
        except Exception as exc:
            return {"repository": True, "error": f"{type(exc).__name__}: {exc}"}

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.projects = {name: ProjectContext(**value) for name, value in data.get("projects", {}).items()}
            self.active_name = data.get("active")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self.projects = {}
            self.active_name = None

    def _save(self) -> None:
        self.path.write_text(json.dumps({"active": self.active_name, "projects": {name: asdict(value) for name, value in self.projects.items()}}, indent=2), encoding="utf-8")
