r"""Screenshots of Global intelligence on the 3D globe and the flat map, for a visual review. On request only.

    $env:HAYAT_SHOTS = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_gui_globe_shots.py

Writes PNGs to .test-runs/gui-shots/ from the recorded test feeds (no network, no owner data).
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_SHOTS") != "1", reason="screenshots on request")
OUT = Path(__file__).resolve().parents[1] / ".test-runs" / "gui-shots"


def _pump(window, seconds):
    end = time.time() + seconds
    while time.time() < end:
        window.update()
        time.sleep(.03)


def _grab(window, name):
    from PIL import ImageGrab

    window.update()
    captured = ImageGrab.grab(window=int(window.wm_frame(), 16))
    assert captured.width > 100 and captured.getextrema() != ((0, 0), (0, 0), (0, 0))
    OUT.mkdir(parents=True, exist_ok=True)
    captured.save(OUT / f"{name}.png")


def test_capture_globe_and_flat_map(shared_tk, tmp_path):
    from app.gui_intelligence import IntelligenceWindow
    from app.gui_theme import Palette
    from app.intelligence.manager import IntelligenceManager
    from app.intelligence.tools import register_intelligence_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    from tests.test_public_intelligence import Fetcher

    shared_tk.deiconify()
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    manager = IntelligenceManager(tmp_path / "intelligence.db", Fetcher(), registry)
    register_intelligence_tools(registry, manager)
    manager.refresh()

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(intelligence=manager, registry=registry)
    window = IntelligenceWindow(gui)
    try:
        window.geometry("1536x900+0+0")
        window.attributes("-topmost", True)
        window.map_mode.set("flat")
        window.map_mode_changed()
        _pump(window, 2)
        _grab(window, "50-intelligence-flat-before")
        window.map_mode.set("globe")
        window.map_mode_changed()
        window.toggle_map_focus()
        _pump(window, 2.5)
        _grab(window, "51-globe-expanded")
        chad = window._globe_earth.find("Chad")[0]
        window.select_country(chad, fly=False)
        window.globe_focus(19, 12, 3.2, remember=True)
        _pump(window, 2)
        _grab(window, "52-globe-country")
        window.clear_country()
        window.globe_layer.set("density")
        window.globe_option_changed()
        window.globe_focus(78, 22, 1.5, remember=False)
        _pump(window, 2)
        _grab(window, "53-globe-density")
        window.globe_layer.set("")
        window.globe_option_changed()
        window.toggle_map_focus()
        window.globe_focus(20, 25, 1.0, remember=False)
        _pump(window, 2)
        _grab(window, "54-globe-workspace")
    finally:
        window.close()
        manager.close()
        shared_tk.withdraw()
