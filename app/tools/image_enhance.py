"""Tool: brighten a dark image or frame (low-light / "night vision"), on this machine.

Over app/perception/camera/lowlight.py. It reads a local image the owner points
at, lifts the shadows and cleans up the noise, and writes a brightened copy next
to it (or to a path the owner gives). No model, no network. It cannot see through
objects or invent detail the sensor never captured.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.registry import ToolRegistry

_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def register_image_enhance_tools(registry: ToolRegistry) -> None:
    def enhance_low_light(path: str, output_path: str = "", strength: float = 1.0, denoise: bool = True) -> dict[str, Any]:
        import cv2

        source = Path(path).expanduser()
        if not source.is_file():
            return {"ok": False, "reason": f"No file at {source}.", "path": str(source)}
        if source.suffix.lower() not in _SUFFIXES:
            return {"ok": False, "reason": f"{source.suffix or 'That file'} is not a supported image.", "path": str(source)}
        image = cv2.imread(str(source))
        if image is None:
            return {"ok": False, "reason": f"{source} could not be read as an image.", "path": str(source)}

        from app.perception.camera.lowlight import enhance

        result = enhance(image, strength=strength, denoise=denoise)
        target = Path(output_path).expanduser() if output_path else source.with_name(f"{source.stem}.brightened{source.suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(target), result.image):
            return {"ok": False, "reason": f"Could not write {target}.", "path": str(source)}
        return {
            "ok": True, "path": str(source), "output": str(target),
            "mean_before": round(result.mean_before, 1), "mean_after": round(result.mean_after, 1),
            "gamma": result.gamma, "denoised": result.denoised,
            "brighter": result.mean_after > result.mean_before,
        }

    registry.register(
        "enhance_low_light",
        "Brighten a dark local image or camera frame (low-light / night-vision style): lift the shadows, even out "
        "local contrast and reduce noise, writing a brightened copy. On-device, no network. 'strength' 0-2 "
        "(default 1). It cannot see through objects or add detail the camera never captured.",
        {"type": "object",
         "properties": {"path": {"type": "string"}, "output_path": {"type": "string"},
                        "strength": {"type": "number"}, "denoise": {"type": "boolean"}},
         "required": ["path"], "additionalProperties": False},
        "low",
        "filesystem_write_project",
        enhance_low_light,
    )
