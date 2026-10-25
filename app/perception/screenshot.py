from __future__ import annotations

from typing import Any

from app.tools.computer import ComputerTools


class ScreenshotPerception:
    """Captures real Windows screen state; it does not infer visual meaning by itself."""

    def __init__(self, computer: ComputerTools) -> None:
        self.computer = computer

    def capture(self, path: str = "data/artifacts/screen_observation.png") -> dict[str, Any]:
        return self.computer.screenshot(path)
