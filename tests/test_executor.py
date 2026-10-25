from app.agent.executor import Executor
from app.agent.planner import PlanStep
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


def test_executor_resolves_python_report(tmp_path):
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    registry.register("capture", "capture", {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}, "low", "any", lambda content: content)
    history = [{"result": {"output": {"matches": ["a.py", "b.py"]}}}]
    result = Executor(registry).execute(PlanStep("report", "capture", {"content": "{{python_report_from_previous}}"}), history)
    assert result.success
    assert "Total Python files: **2**" in result.output
    assert "`a.py`" in result.output
