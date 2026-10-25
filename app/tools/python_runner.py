from __future__ import annotations

import sys
import uuid
from typing import Any

from app.safety.permissions import PermissionManager
from app.safety.sandbox import ProcessSandbox
from app.tools.registry import ToolRegistry


class PythonRunner:
    def __init__(self, permissions: PermissionManager) -> None:
        self.permissions = permissions
        self.work_dir = self.permissions.project_root / "data/work/python"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.processes = ProcessSandbox()

    def execute_code(self, code: str, timeout: int = 60, keep_file: bool = False) -> dict[str, Any]:
        script = self.work_dir / f"generated_{uuid.uuid4().hex}.py"
        script.write_text(code, encoding="utf-8")
        result = self.execute_file(str(script.relative_to(self.permissions.project_root)), timeout)
        if not keep_file:
            script.unlink(missing_ok=True)
            result["script"] = None
        return result

    def execute_file(self, path: str, timeout: int = 60, arguments: list[str] | None = None) -> dict[str, Any]:
        script = self.permissions.validate_project_path(path)
        root = self.permissions.project_root.resolve()
        resolved = script.resolve()
        try:
            resolved.relative_to(root)
            inside_project = True
        except ValueError:
            inside_project = False
        if not inside_project:
            raise PermissionError(f"Python execution is limited to the project directory: {resolved}")
        if script.suffix.lower() != ".py":
            raise ValueError("Only .py files can be executed by the Python tool.")
        result = self.processes.run(
            [sys.executable, "-s", "-P", str(script), *(arguments or [])],
            cwd=self.permissions.project_root,
            timeout=timeout,
            env=self.processes.environment({"PYTHONPATH": str(self.permissions.project_root)}),
        )
        return {**result, "script": str(script)}


def register_python_tools(registry: ToolRegistry) -> PythonRunner:
    runner = PythonRunner(registry.permissions)
    registry.register("python_execute_code", "Execute generated Python in the controlled project work folder.", {
        "type": "object", "properties": {"code": {"type": "string"}, "timeout": {"type": "integer", "minimum": 1, "maximum": 600}, "keep_file": {"type": "boolean"}}, "required": ["code"], "additionalProperties": False
    }, "medium", "python_project", runner.execute_code)
    registry.register("python_execute_file", "Execute an existing project Python file.", {
        "type": "object", "properties": {"path": {"type": "string"}, "timeout": {"type": "integer", "minimum": 1, "maximum": 600}, "arguments": {"type": "array", "items": {"type": "string"}}}, "required": ["path"], "additionalProperties": False
    }, "medium", "python_project", runner.execute_file)
    return runner
