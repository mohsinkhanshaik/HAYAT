from types import SimpleNamespace

from app.agent.planner import Planner
from app.safety.permissions import PermissionManager
from app.tools.applications import ApplicationTools, register_application_tools
from app.tools.registry import ToolRegistry


def test_application_tool_is_allowlisted_and_reports_process(tmp_path, monkeypatch):
    executable = tmp_path / "notepad.exe"
    executable.write_bytes(b"")
    permissions = PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path)
    applications = ApplicationTools(permissions)
    monkeypatch.setattr(applications, "_find", lambda name: executable)
    monkeypatch.setattr(applications.windows, "windows", lambda name: [])
    monkeypatch.setattr(applications.windows, "wait_for_window", lambda name: None)
    monkeypatch.setattr("app.tools.applications.subprocess.Popen", lambda *args, **kwargs: SimpleNamespace(pid=123, poll=lambda: None))
    result = applications.launch("notepad")
    assert result["running"] and result["pid"] == 123
    try:
        applications.launch("arbitrary.exe")
        assert False, "arbitrary applications must be rejected"
    except ValueError:
        pass


def test_planner_has_fast_safe_application_and_file_paths(tmp_path):
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_application_tools(registry)
    registry.register("write_file", "write", {"type": "object"}, "low", "write", lambda **kwargs: {})
    registry.register("read_file", "read", {"type": "object"}, "low", "read", lambda **kwargs: {})
    planner = Planner(registry)
    assert planner.create_plan("Can you open Chrome?")[0].tool == "launch_application"
    steps = planner.create_plan("Create a text file called voice-test.txt containing hello from my AI.")
    assert [step.tool for step in steps] == ["write_file", "read_file"]
    # A bare spoken filename is stored under data/artifacts/ so generated files
    # do not pile up in the project root; the spelled-out name still resolves.
    spoken = planner.create_plan("Create a text file called voice test dot T X T containing hello.")
    assert spoken[0].arguments["path"] == "data/artifacts/voice-test.txt"
    recognized = planner.create_plan("Create a text file called voice dash test.text containing hello.")
    assert recognized[0].arguments["path"] == "data/artifacts/voice-test.txt"


import pytest as _pytest

from app.intent.router import IntentRouter as _Router


@_pytest.mark.parametrize(("said", "application"), [
    ("open word", "word"), ("open excel and make a budget", "excel"), ("launch powerpoint", "powerpoint"),
    ("open outlook", "outlook"), ("open onenote", "onenote"), ("start teams", "teams"),
    ("open discord", "discord"), ("open zoom", "zoom"), ("open telegram", "telegram"),
    ("open signal", "signal"), ("open spotify", "spotify"), ("open microsoft edge", "edge"),
])
def test_everyday_desktop_apps_are_recognised(said, application):
    assert _Router.application_in(said) == application


@_pytest.mark.parametrize("said", [
    "zoom in on the screen", "I excel at chess", "change my password", "research knowledge graphs",
    "look for edge cases in the parser", "signal strength is weak", "two teams played tonight",
])
def test_an_app_name_that_is_also_a_word_needs_something_pointing_at_the_app(said):
    """Substring matching found "edge" in "knowledge" and would have found
    "word" in "password"."""
    assert _Router.application_in(said) is None


def test_edge_cases_are_not_a_browser_request():
    from app.intent.models import IntentKind
    from app.session import SessionState

    assert _Router().route("look for edge cases in the parser", SessionState()).intent is not IntentKind.BROWSER


def test_every_launchable_app_has_a_window_pattern_that_ignores_browser_tabs():
    import re

    from app.tools.applications import ApplicationTools
    from app.windows.applications import WindowsApplicationController

    patterns = WindowsApplicationController.TITLE_PATTERNS
    assert set(ApplicationTools.ALIASES) <= set(patterns)
    for app in ("teams", "discord", "spotify", "telegram", "whatsapp", "outlook"):
        assert not re.match(patterns[app], f"{app.title()} - Google Chrome"), app
    assert re.match(patterns["excel"], "Budget.xlsx - Excel")
    assert re.match(patterns["word"], "Report.docx - Word")
    assert not re.match(patterns["word"], "WordPad")


def test_an_app_without_an_executable_is_found_in_the_start_menu(monkeypatch):
    import app.tools.applications as applications
    from app.safety.permissions import PermissionManager

    monkeypatch.setattr(applications.os, "name", "nt")
    monkeypatch.setattr(applications.ApplicationTools, "_start_menu_cache",
                        {"discord": "com.squirrel.Discord.Discord", "zoom workplace": "zoom.us.Zoom Video Meetings"})
    tools = applications.ApplicationTools.__new__(applications.ApplicationTools)
    tools.permissions = PermissionManager()
    assert tools._store_app("discord") == "com.squirrel.Discord.Discord"
    assert tools._store_app("zoom") == "zoom.us.Zoom Video Meetings"
    assert tools._store_app("spotify") is None, "not installed here, so nothing to launch"
