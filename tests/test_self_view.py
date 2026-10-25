from types import SimpleNamespace

from app.self_view import describe_self
from app.tools.computer import ComputerTools


def test_self_description_keeps_unknown_distinct_from_measured_success():
    text = describe_self({"tool_count": 200, "self_change": {"restart_required": True}})
    assert "No active model" in text
    assert "No tool-use evidence" in text
    assert "Registered tools: 200" in text
    assert "code on disk differs" in text


def test_computer_readiness_observes_without_sending_input():
    computer = object.__new__(ComputerTools)
    computer._pyautogui = SimpleNamespace(FAILSAFE=True,
        size=lambda: SimpleNamespace(width=1920, height=1080),
        position=lambda: SimpleNamespace(x=450, y=320))
    status = computer.status()
    assert status["available"] and status["failsafe"]
    assert status["pointer"] == {"x": 450, "y": 320}
    assert status["input_sent"] is False
    assert "has not been tested" in status["verification"]
    computer._pyautogui = None
    computer._error = "No display"
    assert computer.status()["available"] is False
