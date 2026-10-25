from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import PROJECT_ROOT, ensure_data_directories, load_settings, load_yaml
from app.memory.database import MemoryDatabase
from app.doctor import inspect_runtime
from app.runtime import build_runtime
from app.portable import activate


def main() -> int:
    failures: list[str] = []
    for module in ("httpx", "bs4", "pypdf", "yaml", "dotenv", "playwright", "PIL", "pyautogui", "pywinauto", "apscheduler", "jsonschema"):
        try:
            importlib.import_module(module)
        except Exception as exc:
            failures.append(f"{module}: {exc}")
    try:
        load_settings()
        load_yaml("config/models.yaml")
        load_yaml("config/permissions.yaml")
        ensure_data_directories()
        database = MemoryDatabase()
        database.initialize()
    except Exception as exc:
        failures.append(f"configuration/database: {exc}")
    if failures:
        print("Doctor found problems:")
        print("\n".join(f"- {item}" for item in failures))
        return 1
    # Inspection must not mutate the learning queue or spend network budget.
    # Match the desktop launcher's in-folder provider rather than inspecting a
    # different, stopped system Ollama on port 11434.
    activate()
    runtime = build_runtime(use_model=True, start_background_services=False)
    try:
        report = inspect_runtime(runtime)
    finally:
        runtime.close()
    print(json.dumps(report, indent=2, default=str))
    if not report["ready"]:
        return 1
    print(f"Doctor passed with Python {sys.version.split()[0]} at {PROJECT_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
