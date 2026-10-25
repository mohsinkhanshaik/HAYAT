from __future__ import annotations

import os
import platform
import shutil
from typing import Any

from app.tools.registry import ToolRegistry


def system_status() -> dict[str, Any]:
    disk = shutil.disk_usage(os.getcwd())
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "disk_total": disk.total,
        "disk_free": disk.free,
    }


def register_system_tools(registry: ToolRegistry) -> None:
    registry.register("system_status", "Read basic operating-system, Python, and disk status.", {"type": "object", "properties": {}, "additionalProperties": False}, "low", "filesystem_read", system_status)
