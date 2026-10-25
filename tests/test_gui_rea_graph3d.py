import time

import pytest
from tkinter import ttk

from app.gui_rea_graph3d import ReaGraph3DWindow, engine_loader, graph_scene, normalise_graph
from app.gui_theme import Palette, style_controls
from tests.conftest import open_window


def test_module_colouring_filter_and_legend():
    from app.gui_rea_graph3d import module_of

    class P:
        GOLD = "#D4AF37"
    assert module_of("ns::func") == "ns" and module_of("mod!fn") == "mod"
    assert module_of("a/b/m.js") == "a/b" and module_of("pkg.sub.fn") == "pkg.sub" and module_of("bare") == "·"
    nodes = ["app::main", "app::helper", "net::send", "net::recv", "bare"]
    edges = [("app::main", "app::helper"), ("app::main", "net::send"), ("net::send", "net::recv")]
    scene = graph_scene(nodes, edges, P, colour_by="module")
    colours = {p["id"]: p["colour"] for p in scene["points"]}
    assert colours["net::send"] == colours["net::recv"]  # one module, one colour
    assert colours["app::main"] == P.GOLD  # the entry point stays gold regardless of module
    assert {item["label"] for item in scene["legend"]} >= {"app", "net"}
    filtered = graph_scene(nodes, edges, P, name_filter="net")
    assert sorted(p["id"] for p in filtered["points"]) == ["net::recv", "net::send"] and "filtered" in filtered["note"]
    assert graph_scene(nodes, edges, P, name_filter="zzz")["points"] == []
    assert graph_scene(nodes, edges, P, colour_by="depth")["legend"] == []


def test_call_path_and_highlighting():
    from app.gui_rea_graph3d import call_path

    class P:
        GOLD = "#D4AF37"
        GOLD_SOFT = "#F0D899"
    nodes = ["main", "a", "b", "c", "d", "x"]
    edges = [("main", "a"), ("a", "b"), ("b", "d"), ("main", "c"), ("c", "d"), ("x", "main")]
    assert call_path(nodes, edges, "main", "d") == ["main", "c", "d"]  # shortest directed chain
    assert call_path(nodes, edges, "d", "main") == []  # no path against edge direction
    assert call_path(nodes, edges, "main", "main") == ["main"]
    scene = graph_scene(nodes, edges, P, path=("main", "d"))
    lit = {p["id"] for p in scene["points"] if p["colour"] == "#FF5D73"}
    assert lit == {"main", "c", "d"}
    assert sum(1 for seg in scene["segments"] if seg["colour"] == "#FF5D73") == 2 and "3 steps" in scene["note"]
    none = graph_scene(nodes, edges, P, path=("d", "main"))
    assert not any(p["colour"] == "#FF5D73" for p in none["points"]) and "no call path" in none["note"]


def test_focus_isolates_a_procedures_neighbourhood():
    class P:
        GOLD = "#D4AF37"
        GOLD_SOFT = "#F0D899"
    nodes = ["main", "a", "b", "c", "d", "leaf"]
    edges = [("main", "a"), ("main", "b"), ("a", "c"), ("b", "c"), ("c", "d")]
    scene = graph_scene(nodes, edges, P, focus="c")
    ids = {p["id"] for p in scene["points"]}
    assert ids == {"a", "b", "c", "d"}  # c plus its callers (a, b) and callee (d) only
    focused = next(p for p in scene["points"] if p["id"] == "c")
    assert focused["size"] >= 1.5 and focused["colour"] == P.GOLD_SOFT and "focused on c" in scene["note"]
    # An unknown focus is ignored (not a false claim), and the full graph is shown.
    whole = graph_scene(nodes, edges, P, focus="missing")
    assert len(whole["points"]) == len(nodes) and "focused on" not in whole["note"]


def test_normalise_accepts_loose_shapes_and_bounds_size():
    graph = normalise_graph(["main", {"name": "helper"}, "main"],
                            [("main", "helper"), {"from": "main", "to": "calc"}, "bad", ("x", "x")])
    assert "main" in graph["nodes"] and "helper" in graph["nodes"] and "calc" in graph["nodes"]
    assert ("main", "helper") in graph["edges"] and ("main", "calc") in graph["edges"]
    assert ("x", "x") not in graph["edges"]  # self-calls dropped
    big = normalise_graph([f"f{i}" for i in range(400)], [(f"f{i}", f"f{i+1}") for i in range(399)], limit=50)
    assert len(big["nodes"]) == 50


def test_scene_places_entries_high_and_leaves_low_inside_the_cube():
    class P:
        GOLD = "#D4AF37"
    scene = graph_scene(["main", "a", "b", "leaf"],
                        [("main", "a"), ("a", "b"), ("b", "leaf"), ("main", "leaf")], P)
    assert len(scene["points"]) == 4 and len(scene["segments"]) == 4
    by_id = {p["id"]: p for p in scene["points"]}
    assert by_id["main"]["y"] > 0.9 and by_id["main"]["colour"] == P.GOLD  # entry at the top, gold
    assert by_id["leaf"]["y"] < by_id["main"]["y"]
    assert all(-1.001 <= p[axis] <= 1.001 for p in scene["points"] for axis in ("x", "y", "z"))
    # The busiest node is drawn largest.
    assert by_id["main"]["size"] >= max(p["size"] for p in scene["points"]) - 1e-9
    assert graph_scene([], [], P)["points"] == [] and "Open a target" in graph_scene([], [], P)["note"]


def test_scene_is_deterministic():
    class P:
        GOLD = "#D4AF37"
    args = (["a", "b", "c"], [("a", "b"), ("b", "c")], P)
    assert graph_scene(*args)["points"] == graph_scene(*args)["points"]


def test_engine_loader_falls_back_to_callees_when_no_whole_graph():
    class Engine:
        def __init__(self):
            self.calls = []

        def read(self, tool, arguments=None):
            self.calls.append(tool)
            if tool == "list_procedures":
                return {"structured": {"rows": [{"name": "main"}, {"name": "a"}]}, "report": ""}
            if tool == "get_call_graph":
                raise RuntimeError("this REA has no whole-graph tool")
            if tool == "procedure_callees":
                return {"structured": (["a"] if arguments["procedure"] == "main" else []), "report": ""}
            return {}
    engine = Engine()
    graph = engine_loader(engine)()
    assert graph["nodes"] and ("main", "a") in [tuple(edge) for edge in graph["edges"]]
    assert "procedure_callees" in engine.calls  # used the fallback


def test_current_ghidra_evidence_envelopes_are_rows_not_json_named_procedures():
    class Engine:
        def read(self, tool, arguments=None):
            if tool == "list_procedures":
                return {"structured": {"evidence_id": "test-only", "normalized_result": [
                    {"address": "0x1000", "value": "entry"}, {"address": "0x1020", "value": "helper"}]}}
            assert tool == "procedure_callees"  # no invalid get_call_graph({}) call
            return {"structured": {"normalized_result": ([{"address": "0x1020", "value": "0x1020"}]
                                                            if arguments["procedure"] == "entry" else [])}}

    graph = engine_loader(Engine())()
    assert normalise_graph(graph["nodes"], graph["edges"]) == {"nodes": ["entry", "helper"], "edges": [("entry", "helper")]}
    assert graph["coverage"]["checked"] == 2 and not graph["coverage"]["limited"]


def test_graph_read_failures_and_bounds_remain_visible():
    class Engine:
        def read(self, tool, arguments=None):
            if tool == "list_procedures":
                return {"structured": ["one", "two", "three"]}
            raise RuntimeError("provider unreadable")

    graph = engine_loader(Engine(), limit=1)()
    assert not graph["edges"] and graph["coverage"]["failed"] == ["one"]
    assert graph["coverage"]["limited"]


def test_target_switch_during_graph_read_is_not_a_mixed_graph():
    class Engine:
        def read(self, tool, arguments=None):
            if tool == "list_procedures":
                return {"open_target": "first.exe", "structured": ["entry"]}
            return {"open_target": "second.exe", "structured": ["other"]}

    with pytest.raises(RuntimeError, match="target changed"):
        engine_loader(Engine())()


@pytest.fixture
def gui(shared_tk):
    root = open_window(shared_tk, "1100x800-4000-4000")
    panel = Palette()
    panel.root = root
    style = ttk.Style(root)
    theme = style.theme_use()
    style.theme_use("clam")
    style_controls(root, panel)
    try:
        yield panel
    finally:
        style.theme_use(theme)
        root.destroy()


def test_window_renders_the_graph_and_opens_a_procedure(gui):
    opened = []

    def load():
        return {"nodes": ["main", "a", "b"], "edges": [("main", "a"), ("a", "b")]}
    window = ReaGraph3DWindow(gui, load, decompile=opened.append)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not window.scene.points:
        window.update()
        time.sleep(0.02)
    try:
        assert len(window.scene.points) == 3
        assert "procedures" in window.status.get()
        window._open("main")
        assert opened == ["main"]
        # Focus and path controls re-render from the cached graph without reloading.
        window._selected("a")
        window._focus_selected()
        window.update()
        assert "focused on a" in window.status.get()
        window._selected("main")
        window._set_path_from()
        window._selected("b")
        window._set_path_to()
        window.update()
        assert any(p["colour"] == "#FF5D73" for p in window.scene.points)  # the path is lit
        window._show_all()
        window.update()
        assert window._path is None and window._focus == ""
    finally:
        window.close()


def test_window_shows_a_clean_message_when_loading_fails(gui):
    def boom():
        raise RuntimeError("REA is not running")
    window = ReaGraph3DWindow(gui, boom)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and "Could not load" not in window.status.get():
        window.update()
        time.sleep(0.02)
    try:
        assert "Could not load" in window.status.get() and "REA is not running" in window.status.get()
    finally:
        window.close()
