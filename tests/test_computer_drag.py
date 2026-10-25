"""Dragging and confirming where the pointer landed.

A click that releases where it pressed is not a drag, however many you chain,
so sliders, text selection, window resizing and moving a file into a folder
were all out of reach. And a plan that moved the mouse then clicked had no way
to confirm the move landed, so a move clamped by a display boundary was
followed by a click somewhere nobody intended.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.safety.permissions import PermissionManager
from app.tools.computer import ComputerTools, register_computer_tools
from app.tools.registry import ToolRegistry


class _FakeBackend:
    """Records the exact input sequence, which is the thing under test."""

    FAILSAFE = True

    def __init__(self, fail_on_move_to: tuple[int, int] | None = None) -> None:
        self.calls: list[tuple] = []
        self._position = (0, 0)
        self._fail_on_move_to = fail_on_move_to

    def moveTo(self, x, y, duration=0.0):
        self.calls.append(("moveTo", x, y))
        if self._fail_on_move_to == (x, y):
            raise RuntimeError("display boundary")
        self._position = (x, y)

    def mouseDown(self, button="left"):
        self.calls.append(("mouseDown", button))

    def mouseUp(self, button="left"):
        self.calls.append(("mouseUp", button))

    def position(self):
        return SimpleNamespace(x=self._position[0], y=self._position[1])

    def size(self):
        return SimpleNamespace(width=1920, height=1200)


def _tools(backend) -> ComputerTools:
    tools = ComputerTools.__new__(ComputerTools)
    tools.permissions = None
    tools._pyautogui = backend
    tools._error = ""
    return tools


def test_a_drag_presses_moves_and_releases_in_that_order():
    backend = _FakeBackend()
    result = _tools(backend).drag(100, 200, 400, 250)
    assert backend.calls == [
        ("moveTo", 100, 200), ("mouseDown", "left"),
        ("moveTo", 400, 250), ("mouseUp", "left"),
    ]
    assert result["start"] == {"x": 100, "y": 200}
    assert result["end"] == {"x": 400, "y": 250}
    assert result["released_at"] == {"x": 400, "y": 250}


def test_the_button_is_never_left_held_down_when_the_move_fails():
    """A held button makes every later observation a mid-drag observation."""
    backend = _FakeBackend(fail_on_move_to=(400, 250))
    with pytest.raises(RuntimeError):
        _tools(backend).drag(100, 200, 400, 250)
    assert ("mouseUp", "left") in backend.calls
    assert backend.calls[-1] == ("mouseUp", "left")


def test_the_drag_move_is_never_instantaneous():
    """Zero duration reads as a press and release in place, which does nothing."""
    recorded: list[float] = []

    class _TimedBackend(_FakeBackend):
        def moveTo(self, x, y, duration=0.0):
            recorded.append(duration)
            super().moveTo(x, y, duration=duration)

    _tools(_TimedBackend()).drag(0, 0, 10, 10, duration=0.0)
    assert recorded[-1] >= 0.05


def test_a_drag_duration_cannot_be_asked_to_run_forever():
    recorded: list[float] = []

    class _TimedBackend(_FakeBackend):
        def moveTo(self, x, y, duration=0.0):
            recorded.append(duration)
            super().moveTo(x, y, duration=duration)

    _tools(_TimedBackend()).drag(0, 0, 10, 10, duration=99.0)
    assert max(recorded) <= 5.0


def test_the_right_button_can_drag_too():
    backend = _FakeBackend()
    _tools(backend).drag(1, 1, 2, 2, button="right")
    assert ("mouseDown", "right") in backend.calls
    assert ("mouseUp", "right") in backend.calls


def test_mouse_position_reports_the_pointer_and_the_screen():
    backend = _FakeBackend()
    backend.moveTo(640, 480)
    state = _tools(backend).mouse_position()
    assert state == {"x": 640, "y": 480, "screen_width": 1920, "screen_height": 1200}


def test_desktop_input_is_unavailable_rather_than_silently_wrong():
    tools = ComputerTools.__new__(ComputerTools)
    tools.permissions = None
    tools._pyautogui = None
    tools._error = "DisplayError: no display"
    with pytest.raises(RuntimeError, match="no display"):
        tools.drag(0, 0, 1, 1)
    with pytest.raises(RuntimeError):
        tools.mouse_position()


def test_the_new_desktop_tools_keep_their_existing_permissions(tmp_path):
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_computer_tools(registry)
    drag = registry.get("computer_drag")
    assert drag.permission == "computer_input"
    assert drag.risk == "high"
    assert set(drag.input_schema["required"]) == {"start_x", "start_y", "end_x", "end_y"}
    position = registry.get("computer_mouse_position")
    assert position.permission == "computer_observe"
    assert position.risk == "low"


def test_out_of_bounds_pointer_never_sends_input():
    backend = _FakeBackend()
    tools = _tools(backend)
    for action in (lambda: tools.move_mouse(1920, 5), lambda: tools.click(-1, 2),
                   lambda: tools.drag(1, 2, 3, 1200)):
        with pytest.raises(ValueError, match='Coordinates'):
            action()
    assert backend.calls == []


def test_clamped_drag_start_never_presses_a_button():
    backend = _FakeBackend()
    backend.moveTo = lambda *_args, **_kwargs: None
    with pytest.raises(ValueError, match='instead'):
        _tools(backend).drag(1, 2, 3, 4)
    assert backend.calls == []


def test_unicode_typing_cannot_silently_drop_characters():
    backend = _FakeBackend()
    with pytest.raises(ValueError, match='Unicode'):
        _tools(backend).type_text('مرحبا')
    assert backend.calls == []


def test_exact_computer_requests_observe_act_and_observe_without_retries():
    from app.tools.computer import COMPUTER_TOOL_NAMES, direct_computer_plan
    plan = direct_computer_plan('Move the cursor to 200, 300', COMPUTER_TOOL_NAMES)
    assert [step['tool'] for step in plan] == ['computer_screenshot', 'computer_move_mouse', 'computer_screenshot']
    assert plan[1]['arguments'] == {'x': 200, 'y': 300}
    assert all(step['max_retries'] == 0 for step in plan)
    assert direct_computer_plan('Do not click at 200, 300', COMPUTER_TOOL_NAMES) is None
    assert direct_computer_plan('Click at 200, 300 and delete a file', COMPUTER_TOOL_NAMES) is None
