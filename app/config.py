from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def load_environment() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), match.group(2) or ""), value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def load_yaml(relative_path: str | Path) -> dict[str, Any]:
    load_environment()
    path = PROJECT_ROOT / relative_path
    with path.open("r", encoding="utf-8") as handle:
        return _expand(yaml.safe_load(handle) or {})


def load_settings() -> dict[str, Any]:
    return load_yaml("config/settings.yaml")


def ensure_data_directories() -> None:
    settings = load_settings()
    for key in ("log_directory", "artifact_directory", "work_directory", "skills_directory"):
        (PROJECT_ROOT / settings[key]).mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / Path(settings["memory_database"]).parent).mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data/tasks").mkdir(parents=True, exist_ok=True)
    for directory in ("sources", "extracted", "summaries", "indexes", "metadata"):
        (PROJECT_ROOT / "data/knowledge" / directory).mkdir(parents=True, exist_ok=True)
    for directory in ("downloads", "embeddings", "datasets", "cache", "tools", "backups", "camera"):
        (PROJECT_ROOT / "data" / directory).mkdir(parents=True, exist_ok=True)
