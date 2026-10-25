"""The 3D brain mesh shows the real topology, real checks and real work only."""
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from app.brain_mesh import BrainMesh
from app.gui_brain import FLOWS, LAYERS, LAYOUT, BrainGraph, active_flows, describe, flow_edges
from app.gui_theme import Palette

EDGES = {(edge.source, edge.target) for edge in BrainMesh.EDGES}


@pytest.fixture(scope="module")
def root(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, "700x300-4000-4000")
    yield window
    close_window(window)


@pytest.fixture
def graph(root):
    opened = []
    widget = BrainGraph(root, Palette, opened.append)
    widget.pack(fill="both", expand=True)
    widget.set_motion(False)
    root.update()
    yield widget, opened
    widget.destroy()


def test_every_mesh_node_has_a_place_and_every_flow_names_real_nodes():
    assert set(LAYOUT) == {node.node_id for node in BrainMesh.NODES}
    assert len(set(LAYOUT.values())) == len(LAYOUT)
    for members in FLOWS.values():
        assert set(members) <= set(LAYOUT)


def test_path_between_follows_real_contracts():
    from app.gui_brain import path_between
    edges = {(edge.source, edge.target) for edge in BrainMesh.EDGES}
    chain = path_between("interface", "memory")
    assert chain and chain[0] == "interface" and chain[-1] == "memory"
    # Every step is a real directed contract, so the path is something work could actually follow.
    assert all((chain[i], chain[i + 1]) in edges for i in range(len(chain) - 1))
    assert path_between("memory", "memory") == ["memory"]
    assert path_between("interface", "not_a_node") == [] and path_between("nope", "memory") == []


def test_filtered_graph_hides_other_layers_and_their_hit_targets(graph):
    widget, opened = graph
    layer = LAYERS["memory"]
    widget.set_layer(layer)
    assert set(widget._positions) == {key for key, value in LAYERS.items() if value == layer}
    assert not widget._visible_edge("interface", "intent")
    widget.set_activity({"running": [{"kind": "model"}]}, {})
    widget.redraw()
    widget._step_key(1)
    assert widget._hover in widget._positions
    widget.set_layer("All layers")
    assert set(widget._positions) == set(LAYERS)


def test_flows_come_only_from_reported_work_and_light_only_real_edges():
    assert active_flows({}, {}) == []
    assert active_flows({"running": [{"kind": "tool"}, {"kind": "tool"}, {"kind": "unknown"}]}, {}) == ["tool"]
    # A disabled voice that last said "listening" is not hearing anyone.
    assert active_flows({}, {"state": "listening", "enabled": False}) == []
    assert active_flows({}, {"state": "listening", "enabled": True}) == ["hearing"]
    assert active_flows({}, {"state": "speaking"}) == ["speaking"]
    lit = flow_edges(["model", "tool", "research", "learning"])
    assert lit and set(lit) <= EDGES
    assert ("authority", "tools") in lit and ("interface", "intent") in lit


def test_description_uses_the_nodes_contracts_and_last_result():
    unknown = describe("tools", None)
    assert "Tool Bus" in unknown and "Not checked yet" in unknown
    assert "Receives from: Authority (scoped decision; model cannot grant it)" in unknown
    assert "Sends to: Truth Gate" in unknown
    failed = describe("network", {"passed": False, "latency_ms": 1.5, "detail": "offline"})
    assert "optional" in failed and "Needs attention · 1.50 ms · offline" in failed
    context = describe("context", {"passed": True, "evidence": {"last_shape": "deep", "last_context_tokens": 32768}})
    assert "Why it matters:" in context
    assert "last context tokens: 32768" in context
    assert "does not measure answer quality" in context


def test_colour_follows_the_last_self_check(graph):
    widget, _opened = graph
    assert widget._node_colour("authority") == Palette.ROYAL_SOFT
    widget.set_checks({"authority": {"passed": False}, "network": {"passed": False},
                       "memory": {"passed": True}, "not-a-node": {"passed": True}})
    assert widget._node_colour("authority") == Palette.ALERT     # critical
    assert widget._node_colour("network") == Palette.GOLD        # optional
    assert widget._node_colour("memory") == Palette.OLIVE_SOFT
    assert "not-a-node" not in widget.checks


def test_evidence_rings_are_live_only_and_details_expose_gaps(graph):
    widget, _ = graph
    layer = BrainMesh.evidence_layer({
        "research": {"passed": True, "evidence": {"claims": 2, "unresolved_conflicts": 1}},
    }, "2026-09-30T12:00:00+00:00")
    widget.set_evidence(layer)
    assert len(widget.find_withtag("evidence")) == len(BrainMesh.NODES)
    assert "1 unresolved source conflicts" in widget.evidence_detail("research")
    widget.show_moment(100, [])
    assert not widget.find_withtag("evidence")
    assert "Historical evidence was not recorded" in widget.evidence_detail("research")
    widget.show_moment(None)
    assert widget.find_withtag("evidence")
    widget.evidence_on = False
    widget.redraw()
    assert not widget.find_withtag("evidence")


def test_clicking_a_part_opens_its_details_and_turns_it_forward(graph):
    widget, opened = graph
    far = min(widget._positions, key=lambda key: widget._positions[key][2])
    x, y, _z = widget._positions[far]
    widget._press(SimpleNamespace(x=x, y=y))
    widget._release(SimpleNamespace(x=x, y=y))
    assert opened == [far]
    assert widget.active == far
    assert widget._positions[far][2] > .3


def test_coalesced_drag_and_escape_never_activate_a_brain_part(graph):
    widget, opened = graph
    key = max(widget._positions, key=lambda name: widget._positions[name][2])
    x, y, _ = widget._positions[key]
    widget._press(SimpleNamespace(x=x - 30, y=y))
    widget._release(SimpleNamespace(x=x, y=y))
    assert not opened
    widget._press(SimpleNamespace(x=x, y=y))
    assert widget._cancel_interaction() == "break"
    widget._release(SimpleNamespace(x=x, y=y))
    assert not opened


def test_auto_uses_small_mesh_during_idle_spin_and_restores_when_paused(graph):
    widget, opened = graph
    widget.mesh_detail = "Auto"
    widget._inside = False
    widget._idle_after = 0
    widget.set_motion(True)
    widget.redraw()
    assert widget.geometry_status()["render_detail"] == "Low"
    widget.set_motion(False)
    widget.redraw()
    assert widget.geometry_status()["render_detail"] in {"Normal", "High"}
    for char in ("", "²", "１２", "0"):
        assert widget._digit(SimpleNamespace(char=char, state=0)) is None
    assert not opened


def test_pulses_animate_on_their_own_layer_and_stop_with_the_work(graph):
    widget, _opened = graph
    widget.set_activity({"running": [{"kind": "tool"}]}, {})
    assert widget._edges and len(widget.find_withtag("flow")) == len(widget._edges)
    mesh = set(widget.find_withtag("node"))
    assert widget._overlay(redrawn=False) is True
    assert set(widget.find_withtag("node")) == mesh             # the mesh was left alone
    widget.set_activity({"running": []}, {})
    assert widget.find_withtag("flow") == ()
    assert widget._overlay(redrawn=False) is False


def test_names_never_overlap(graph):
    widget, _opened = graph
    for angle in (0, 1, 2, 3, 4, 5):
        widget.angle = angle
        widget.redraw()
        boxes = [widget.bbox(item) for item in widget.find_withtag("name")]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert not (a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1)


def test_layout_keeps_parts_apart_on_screen_at_home_and_nearby_angles():
    import itertools
    import math

    def screen(point, angle, pitch):
        x, y, z = point
        x, z = x * math.cos(angle) + z * math.sin(angle), z * math.cos(angle) - x * math.sin(angle)
        y, z = y * math.cos(pitch) - z * math.sin(pitch), y * math.sin(pitch) + z * math.cos(pitch)
        depth = 3.8 / (3.8 - z)
        return x * depth, y * depth

    # Home view and the angles a small drag reaches. The old hand layout let
    # parts land within 0.04 of each other here, so names and discs piled up.
    for angle, pitch in ((BrainGraph.HOME[0], BrainGraph.HOME[1]), (-.25, -.3), (-.75, -.3), (-.5, -.05)):
        points = {key: screen(value, angle, pitch) for key, value in LAYOUT.items()}
        closest = min(math.dist(points[a], points[b]) for a, b in itertools.combinations(points, 2))
        assert closest >= .25, (angle, pitch, closest)
    # Every part stays well inside the shell (semi-axes 1.22, 0.88, 1.08).
    for x, y, z in LAYOUT.values():
        assert (x / 1.22) ** 2 + (y / .88) ** 2 + (z / 1.08) ** 2 <= .9


@pytest.mark.parametrize("angle", [-.5, -1.4, .3, 2.2])
def test_names_never_cover_another_part_or_name(graph, angle):
    widget, _opened = graph
    widget.angle = angle
    widget.redraw()
    names = [widget.bbox(item) for item in widget.find_withtag("name")]
    assert len(names) >= 6
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not (a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1)
    for x, y, _z in widget._positions.values():
        for box in names:
            # A name may touch its own part's edge, never sit over a part's centre.
            assert not (box[0] < x < box[2] and box[1] < y < box[3])


def test_the_brain_fills_its_canvas_without_a_floor(graph):
    widget, _opened = graph
    widget.redraw()
    w, h = widget._size()
    xs = [x for x, _y, _z in widget._positions.values()]
    ys = [y for _x, y, _z in widget._positions.values()]
    assert max(xs) - min(xs) > w * .3 and max(ys) - min(ys) > h * .5
    mesh = [widget.bbox(item) for item in widget.find_withtag("mesh")]
    assert min(box[0] for box in mesh) >= 0 and max(box[2] for box in mesh) <= w
    # The backdrop is a glow only: no perspective floor lines under the brain.
    assert not [item for item in widget.find_withtag("backdrop") if widget.type(item) == "line"]


def test_filled_surface_has_directional_shading_and_wireframe_is_explicit(graph):
    widget, _opened = graph
    widget.redraw()
    facets = widget.find_withtag("mesh")
    wire = [item for item in facets if widget.itemcget(item, "outline") != widget.itemcget(item, "fill")]
    assert not wire
    assert len({widget.itemcget(item, "fill") for item in facets}) > 5
    widget.set_surface("Wireframe")
    assert all(widget.itemcget(item, "fill") == "" for item in widget.find_withtag("mesh"))


@pytest.mark.parametrize("width", [300, 420, 900])
def test_brain_captions_wrap_without_overlap_or_clipping(graph, monkeypatch, width):
    widget, _opened = graph
    monkeypatch.setattr(widget, "_size", lambda: (width, 480))
    widget.set_activity({"running": [{"kind": name} for name in ("model", "tool", "research", "learning")]}, {})
    widget.set_surface("Standing waves")
    widget.redraw()
    header = [widget.bbox(item) for item in widget.find_withtag("legend-header")]
    assert len(header) == 4
    for index, box in enumerate(header):
        assert 0 <= box[0] < box[2] <= width
        assert not widget._overlaps(box, header[index + 1:])
    footer = widget.bbox("legend-footer")
    assert 0 <= footer[0] < footer[2] <= width
    assert 0 <= footer[1] < footer[3] <= 480
    for item in widget.find_withtag("name"):
        assert not widget._overlaps(widget.bbox(item), header + [footer])
    widget._focused = True
    widget.redraw()
    box = widget.bbox("legend-footer")
    assert 0 <= box[0] < box[2] <= width


def test_unchecked_evidence_is_a_hairline_and_checked_evidence_stands_out(graph):
    widget, _opened = graph
    widget.set_evidence({"nodes": {"memory": {"state": "checked", "checked_at": "now", "scope": "", "gaps": []}}})
    rings = {item: widget.gettags(item) for item in widget.find_withtag("evidence")}
    widths = {next(tag for tag in tags if tag.startswith("node-")): float(widget.itemcget(item, "width"))
              for item, tags in rings.items()}
    assert widths["node-memory"] == 2 and widths["node-planner"] == 1


def test_trace_path_lights_the_contract_chain_between_two_subsystems(graph):
    widget, _opened = graph
    chain = widget.trace_path("interface", "memory")
    assert chain and chain[0] == "interface" and chain[-1] == "memory"
    assert all((chain[i], chain[i + 1]) in EDGES for i in range(len(chain) - 1))  # real contracts only
    assert widget._path_nodes == set(chain)
    assert widget._path_edges == {(chain[i], chain[i + 1]) for i in range(len(chain) - 1)}
    widget.clear_path()
    assert widget._path_nodes == set() and widget._path_edges == set()


def test_toggle_path_point_pins_then_traces(graph):
    widget, _opened = graph
    status = []
    widget.on_path_status = status.append
    widget.active = "interface"
    first = widget.toggle_path_point()          # pins the start
    assert "Path from" in first and widget._path_from == "interface" and not widget._path_edges
    widget.active = "memory"
    second = widget.toggle_path_point()          # traces to the new focus
    assert second.startswith("Path:") and widget._path_edges and widget._path_from is None
    # The key handler routes the status to the host callback.
    widget.active = ""
    widget._path_key()
    assert status and "Focus a subsystem first" in status[-1]
