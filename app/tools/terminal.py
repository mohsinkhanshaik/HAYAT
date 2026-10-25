from __future__ import annotations

import os
from typing import Any

from app.safety.permissions import PermissionManager
from app.safety.sandbox import ProcessSandbox
from app.tools.registry import ToolRegistry


class TerminalTools:
    def __init__(self, permissions: PermissionManager) -> None:
        self.permissions = permissions
        self.processes = ProcessSandbox()

    def run(self, command: str, working_directory: str = ".", timeout: int = 60,
            interpreter: str | None = None) -> dict[str, Any]:
        if interpreter == "cmd" and os.name != "nt":
            raise OSError("Windows CMD is unavailable on this operating system.")
        cwd = self.permissions.validate_project_path(working_directory)
        return {**self.processes.run(command, cwd, timeout, shell=True),
                "shell": "CMD" if os.name == "nt" else "Shell", "working_directory": str(cwd)}


def register_terminal_tools(registry: ToolRegistry) -> TerminalTools:
    tools = TerminalTools(registry.permissions)
    schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}, "working_directory": {"type": "string"},
                       "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
                       "interpreter": {"type": "string", "enum": ["cmd"]}},
        "required": ["command"],
        "additionalProperties": False,
    }
    registry.register("terminal", "Run a non-administrator command in the project (using CMD on Windows) and capture stdout, stderr, and exit code.", schema, "high", "terminal", tools.run)
    return tools
