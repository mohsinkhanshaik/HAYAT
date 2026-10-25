"""Low-light enhancement brightens a dark frame on-device, and the tool writes the copy."""
from __future__ import annotations

import numpy as np

from app.perception.camera.lowlight import enhance
from app.safety.permissions import PermissionManager
from app.tools.image_enhance import register_image_enhance_tools
from app.tools.registry import ToolRegistry


def _dark_image():
    rng = np.random.default_rng(0)
    # A dim, noisy scene: mean well below mid-grey.
    base = rng.integers(5, 45, size=(120, 160, 3), dtype=np.uint8)
    return base


def test_enhance_raises_the_overall_brightness_of_a_dark_frame():
    out = enhance(_dark_image(), strength=1.0, denoise=False)
    assert out.mean_after > out.mean_before
    assert out.gamma < 1.0  # a dark frame gets a real lift
    assert out.image.shape == (120, 160, 3)


def test_an_already_bright_frame_gets_no_shadow_lift():
    rng = np.random.default_rng(1)
    bright = rng.integers(150, 230, size=(80, 80, 3), dtype=np.uint8)  # varied, already bright
    out = enhance(bright, strength=1.0, denoise=False)
    assert out.gamma >= 0.95  # the gamma shadow-lift stands down on an already-bright frame
    assert out.image.shape == bright.shape


def test_tool_writes_a_brightened_copy_next_to_the_source(tmp_path):
    import cv2

    source = tmp_path / "dark.png"
    cv2.imwrite(str(source), _dark_image())
    registry = ToolRegistry(PermissionManager({"default": "DENY", "permissions": {"filesystem_write_project": "ALLOW"}}, tmp_path))
    register_image_enhance_tools(registry)
    result = registry.execute("enhance_low_light", {"path": str(source), "denoise": False})
    assert result.success and result.output["ok"] is True
    assert result.output["brighter"] is True
    out_path = tmp_path / "dark.brightened.png"
    assert out_path.is_file()
    assert result.output["output"] == str(out_path)


def test_tool_reports_a_missing_or_non_image_file_cleanly(tmp_path):
    registry = ToolRegistry(PermissionManager({"default": "DENY", "permissions": {"filesystem_write_project": "ALLOW"}}, tmp_path))
    register_image_enhance_tools(registry)
    missing = registry.execute("enhance_low_light", {"path": str(tmp_path / "nope.png")})
    assert missing.success and missing.output["ok"] is False and "No file" in missing.output["reason"]
    note = tmp_path / "note.txt"
    note.write_text("x")
    bad = registry.execute("enhance_low_light", {"path": str(note)})
    assert bad.output["ok"] is False and "not a supported image" in bad.output["reason"]
