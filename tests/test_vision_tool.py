from pathlib import Path

from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from app.tools.vision import register_vision_tools


class Computer:
    def __init__(self, path):
        self.path = path

    def screenshot(self, path):
        self.path.write_bytes(b"png")
        return {"path": str(self.path), "width": 10, "height": 10, "exists": True}


class Vision:
    def analyze(self, path, prompt):
        return {"path": path, "interpretation": "Notepad is visible.", "provider": "test", "model": "vision-test"}


def test_describe_screen_uses_registry_permission_and_returns_evidence(tmp_path):
    image = tmp_path / "screen.png"
    registry = ToolRegistry(PermissionManager({"default": "DENY", "permissions": {"computer_observe": "ALLOW"}}, tmp_path))
    register_vision_tools(registry, Computer(image), Vision())
    result = registry.execute("describe_screen")
    assert result.success
    assert result.output["interpretation"] == "Notepad is visible."
    assert Path(result.output["path"]).is_file()
