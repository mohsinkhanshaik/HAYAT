from __future__ import annotations

from typing import Any

from app.config import load_settings
from app.perception.screenshot import ScreenshotPerception
from app.perception.vision import VisionAnalyzer


class ScreenPerception:
    def __init__(self, screenshots: ScreenshotPerception, vision: VisionAnalyzer | None = None) -> None:
        self.screenshots = screenshots
        self.vision = vision
        self.settings = load_settings().get("vision", {})

    def observe(self, path: str = "data/artifacts/screen_observation.png") -> dict[str, Any]:
        capture = self.screenshots.capture(path)
        capture["vision_enabled"] = bool(self.settings.get("enabled", False) and self.vision)
        capture["interpretation"] = None
        if not capture["vision_enabled"]:
            capture["note"] = "Screenshot captured. Visual interpretation is disabled until a vision model is configured."
        else:
            analysis = self.vision.analyze(capture["path"])
            capture.update({key: value for key, value in analysis.items() if key != "path"})
        return capture
