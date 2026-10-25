"""Explicit chat commands reach the existing permissioned PowerShell tool."""

import json

import pytest

from app.agent.planner import Planner
from app.agent.verifier import Verifier
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.models.base import ModelProvider, ModelResponse
from app.observed_reply import explain_failure, render
from app.safety.permissions import PermissionManager
from app.tools.powershell import register_powershell_tools
from app.tools.terminal import register_terminal_tools
from app.tools.registry import ToolRegistry, ToolResult


def test_explicit_powershell_command_keeps_exact_script_and_checks_exit(tmp_path):
    script = 'Write-Output "Hello  Mohsin"\nGet-Location'
    intent = IntentRouter().route("Run PowerShell: " + script)
    assert intent.intent is IntentKind.ACTION
    assert intent.entities["powershell_command"] == script

    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_powershell_tools(registry)
    step, = Planner(registry).create_plan(intent.goal, intent=intent)
    assert step.tool == "powershell"
    assert step.arguments == {"command": script}
    assert step.expected == {"type": "exit_code_zero"}
    assert step.max_retries == 0  # A command with side effects must not repeat itself.
    assert not Verifier().check(step.expected, ToolResult("powershell", True, {"exit_code": 1})).passed


def test_empty_or_capability_question_does_not_execute_powershell():
    router = IntentRouter()
    assert router.route("Run PowerShell: ").needs_clarification
    question = router.route("Can you use PowerShell?")
    assert "powershell_command" not in question.entities


@pytest.mark.parametrize("request_text, shell, tool, command", [
    ("Use PowerShell to show the current directory", "powershell", "powershell", "Get-Location"),
    ("Use CMD to list files", "cmd", "terminal", "dir"),
    ("Run PowerShell to show the current directory", "powershell", "powershell", "Get-Location"),
    ("Can you list running processes with PowerShell?", "powershell", "powershell", "Get-Process"),
    ("Show the current directory in CMD", "cmd", "terminal", "cd"),
    ("Run CMD: echo Hello  Mohsin", "cmd", "terminal", "echo Hello  Mohsin"),
])
def test_chat_tasks_use_the_requested_shell(tmp_path, request_text, shell, tool, command):
    intent = IntentRouter().route(request_text)
    assert intent.intent is IntentKind.ACTION
    assert intent.entities["preferred_shell"] == shell
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_powershell_tools(registry)
    register_terminal_tools(registry)
    step, = Planner(registry).create_plan(intent.goal, intent=intent)
    assert (step.tool, step.arguments["command"]) == (tool, command)
    assert step.expected == {"type": "exit_code_zero"}
    assert step.max_retries == 0
    if shell == "cmd":
        assert step.arguments["interpreter"] == "cmd"


def test_unmodelled_shell_task_never_falls_back_to_an_unrelated_listing(tmp_path):
    intent = IntentRouter().route("Use CMD to inspect the active network adapters")
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_terminal_tools(registry)
    with pytest.raises(ValueError, match="local planning model"):
        Planner(registry).create_plan(intent.goal, intent=intent)


@pytest.mark.parametrize("request_text, command", [
    ("Show running processes", "Get-Process"),
    ("What processes are running?", "Get-Process"),
    ("Check disk space", "Get-PSDrive -PSProvider FileSystem"),
    ("List services", "Get-Service"),
    ("Inspect network adapters", "Get-NetAdapter"),
])
def test_machine_requests_choose_a_live_shell_without_needing_shell_words(tmp_path, request_text, command):
    intent = IntentRouter().route(request_text)
    assert intent.intent is IntentKind.ACTION and intent.requires_tools
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_powershell_tools(registry)
    step, = Planner(registry).create_plan(intent.goal, intent=intent)
    assert step.tool == "powershell"
    assert step.arguments["command"] == command
    assert step.expected == {"type": "exit_code_zero"}


class ShellPlanningModel(ModelProvider):
    name = "shell-planner-test"

    def list_models(self):
        return [self.name]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        choices = response_schema["properties"]["steps"]["items"]["properties"]["tool"]["enum"]
        assert "powershell" in choices and "terminal" not in choices
        step = {"description": "Inspect adapters", "tool": "powershell",
                "arguments": {"command": "Get-NetAdapter"},
                "expected": {"type": "tool_success"}, "max_retries": 2}
        return ModelResponse(json.dumps({"steps": [step]}), self.name, self.name)


def test_open_ended_chat_task_plans_the_requested_shell_and_never_auto_retries(tmp_path):
    intent = IntentRouter().route("Use PowerShell to inspect active network adapters")
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_powershell_tools(registry)
    register_terminal_tools(registry)
    step, = Planner(registry, ShellPlanningModel()).create_plan(intent.goal, intent=intent)
    assert step.tool == "powershell"
    assert step.arguments == {"command": "Get-NetAdapter"}
    assert step.expected == {"type": "exit_code_zero"}
    assert step.max_retries == 0


class CmdPlanningModel(ShellPlanningModel):
    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        choices = response_schema["properties"]["steps"]["items"]["properties"]["tool"]["enum"]
        assert "terminal" in choices and "powershell" not in choices
        step = {"description": "Inspect adapters", "tool": "terminal",
                "arguments": {"command": "ipconfig"},
                "expected": {"type": "tool_success"}, "max_retries": 2}
        return ModelResponse(json.dumps({"steps": [step]}), self.name, self.name)


def test_open_ended_cmd_task_uses_cmd_and_checks_exit(tmp_path):
    intent = IntentRouter().route("Use CMD to inspect network adapters")
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_powershell_tools(registry)
    register_terminal_tools(registry)
    step, = Planner(registry, CmdPlanningModel()).create_plan(intent.goal, intent=intent)
    assert step.tool == "terminal"
    assert step.arguments == {"command": "ipconfig", "interpreter": "cmd"}
    assert step.expected == {"type": "exit_code_zero"}
    assert step.max_retries == 0


def test_powershell_tool_uses_bounded_process_and_reports_output(tmp_path):
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path),
                            approval_callback=lambda *_args: True)
    tools = register_powershell_tools(registry)
    calls = []

    def fake_run(argv, cwd, timeout):
        calls.append((argv, cwd, timeout))
        return {"stdout": "ready\n", "stderr": "", "exit_code": 0, "timed_out": False}

    tools.processes.run = fake_run
    result = registry.execute("powershell", {"command": "Get-Location", "timeout": 10})
    assert result.success
    assert calls == [(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                      "-Command", "Get-Location"], tmp_path.resolve(), 10)]
    assert result.output["shell"] == "PowerShell"
    assert "PowerShell command ran successfully" in render(result.output)


def test_failed_shell_command_reports_the_exit_error_in_chat():
    history = [{"tool": "powershell", "result": {"success": True, "output": {
        "shell": "PowerShell", "exit_code": 1, "stdout": "", "stderr": "Command failed",
    }}, "verification": {"passed": False}}]
    assert explain_failure("Run PowerShell: bad-command", history) == (
        "The PowerShell command exited with code 1:\nCommand failed"
    )
