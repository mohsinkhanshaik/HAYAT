from __future__ import annotations

from typing import Any

from app.safety.permissions import PermissionManager
from app.safety.elevation import preflight
from app.safety.sandbox import ProcessSandbox
from app.tools.registry import ToolRegistry


class PowerShellTools:
    def __init__(self, permissions: PermissionManager) -> None:
        self.permissions = permissions
        self.processes = ProcessSandbox()

    def run(self, command: str, working_directory: str = ".", timeout: int = 60) -> dict[str, Any]:
        runnable, explanation = preflight(command)
        if not runnable:
            raise PermissionError(explanation)
        cwd = self.permissions.validate_project_path(working_directory)
        result = self.processes.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=cwd,
            timeout=timeout,
        )
        return {**result, "shell": "PowerShell", "working_directory": str(cwd)}


def register_powershell_tools(registry: ToolRegistry) -> PowerShellTools:
    tools = PowerShellTools(registry.permissions)
    schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}, "working_directory": {"type": "string"}, "timeout": {"type": "integer", "minimum": 1, "maximum": 600}},
        "required": ["command"], "additionalProperties": False,
    }
    registry.register("powershell", "Run non-elevated Windows PowerShell in the project and capture its result.", schema, "high", "powershell_unknown", tools.run)
    return tools
