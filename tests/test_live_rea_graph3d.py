"""Opt-in: real REA/Ghidra, generated call fixture and native 3D graph window."""
import hashlib
import json
import os
from pathlib import Path
import struct
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE_REA_GRAPH") != "1", reason="live REA graph: set HAYAT_LIVE_REA_GRAPH=1")


def test_real_native_call_graph_renders_selects_and_decompiles(shared_tk, tmp_path):
    from app.gui_rea_graph3d import ReaGraph3DWindow, engine_loader
    from app.gui_theme import Palette, style_controls
    from app.reverse_engineer import ReverseEngineer
    from scripts.verify_native_analysis import fixture
    from tests.conftest import close_window, open_window

    target = tmp_path / "owned-call-fixture.exe"
    fixture(target)
    image = bytearray(target.read_bytes())
    struct.pack_into("<I", image, 0x98 + 240 + 8, 0x30)
    image[512:526] = b"\x48\x83\xec\x28\xe8\x17\x00\x00\x00\x48\x83\xc4\x28\xc3"
    image[544:550] = b"\xb8\x2a\x00\x00\x00\xc3"
    target.write_bytes(image)
    before = hashlib.sha256(image).hexdigest()
    engine = ReverseEngineer()
    root = open_window(shared_tk, "1100x800+30+30")
    palette = Palette()
    palette.root = root
    style_controls(root, palette)
    window = None
    opened = []
    try:
        engine.act("open_binary", {"path": str(target), "provider_id": "ghidra"})
        loader = engine_loader(engine)
        graph = loader()
        assert len(graph["nodes"]) == 2 and len(graph["edges"]) == 1
        assert graph["edges"][0] == ("entry", "FUN_140001020")
        assert graph["coverage"]["checked"] >= 2 and not graph["coverage"]["failed"]

        def decompile(name):
            result = engine.read("procedure_pseudo_code", {"procedure": name})
            assert result["ok"] and result["structured"]
            opened.append(name)

        window = ReaGraph3DWindow(palette, lambda: graph, decompile)
        deadline = time.monotonic() + 10
        while not window.scene.points and time.monotonic() < deadline:
            root.update()
            time.sleep(.02)
        assert len(window.scene.points) == 2 and len(window.scene.segments) == 1
        window.scene.set_spin(False)
        window.scene.select("entry")
        window.scene.focus_force()
        root.update()
        window.scene.event_generate("<Return>")
        root.update()
        assert opened == ["entry"]
        if os.environ.get("HAYAT_SHOTS") == "1":
            from tests.test_gui_screenshots import _grab
            from types import SimpleNamespace

            _grab(SimpleNamespace(root=root), "76-rea-live-native-call-graph", window)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == before
        report = {"fixture_sha256": before, "executed": False, "nodes": graph["nodes"],
                  "edges": graph["edges"], "coverage": graph["coverage"], "opened_and_decompiled": opened,
                  "source_unchanged": True}
        (Path(__file__).resolve().parents[1] / ".test-runs/live-rea-graph-verification.json").write_text(json.dumps(report, indent=2))
    finally:
        if window is not None:
            window.close()
        close_window(root)
        engine.close()
