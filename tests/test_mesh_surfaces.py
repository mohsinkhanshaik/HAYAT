import math

import pytest

from app.brain_mesh import BrainMesh
from app.gui_brain import BrainGraph, contract_curve, curve_point, standing_wave
from app.gui_theme import Palette


@pytest.fixture
def graph(shared_tk):
    from tests.conftest import open_window, close_window
    root = open_window(shared_tk, "720x480-4000-4000")
    widget = BrainGraph(root, Palette, lambda _node: None)
    widget.set_motion(False)
    widget.pack(fill="both", expand=True)
    root.update()
    yield widget
    close_window(root)


def test_mesh_detail_budget_and_surface_modes_preserve_real_topology(graph):
    original = dict(graph._positions)
    for detail, count in (("Low", 384), ("Normal", 1536), ("High", 2560)):
        graph.set_surface("Wireframe", detail=detail)
        assert len(graph._shell) == count
        assert all(graph.itemcget(item, "fill") == "" for item in graph.find_withtag("mesh"))
        assert graph._positions == original
        assert len(graph.find_withtag("contract")) == len(BrainMesh.EDGES)
    graph.set_surface("Hidden")
    assert not graph.find_withtag("mesh") and not graph.find_withtag("fissure")
    assert len(graph.find_withtag("contract")) == len(BrainMesh.EDGES)
    assert all(graph.itemcget(item, "arrow") == "last" for item in graph.find_withtag("contract"))
    with pytest.raises(ValueError):
        graph.set_surface("fake neurons")
    with pytest.raises(ValueError):
        graph.set_surface("Folded", detail="unlimited")
    with pytest.raises(ValueError):
        graph._shell_facets(1000, 1000)


def test_cymatics_is_static_illustration_not_fake_runtime_activity(graph):
    graph.set_surface("Standing waves", modes=(3, 5))
    assert graph.flows == [] and graph._edges == []
    assert not graph.find_withtag("flow")
    caption = graph.itemcget(graph.find_withtag("wave-caption")[0], "text")
    assert "not neural activity" in caption
    coordinates = [graph.coords(item) for item in graph.find_withtag("mesh")]
    graph.redraw()
    assert coordinates == [graph.coords(item) for item in graph.find_withtag("mesh")]
    graph.set_surface("Folded")
    assert not graph.find_withtag("wave-caption")


def test_wave_field_is_finite_bounded_and_mode_orders_are_validated():
    for x in range(-13, 14):
        for z in range(-12, 13):
            value = standing_wave((x / 10, 0, z / 10))
            assert math.isfinite(value) and -1 <= value <= 1
    for modes in ((2, 2), (3, 1), (0, 4), (3, 9), (True, 3)):
        with pytest.raises(ValueError):
            standing_wave((0, 0, 0), modes)


def test_directed_curves_leave_nodes_clear_and_split_opposite_contracts():
    forward = contract_curve((0, 0), (100, 0))
    backward = contract_curve((100, 0), (0, 0))
    assert forward[:2] == (20, 0) and forward[-2:] == (80, 0)
    assert curve_point(forward, 0) == forward[:2] and curve_point(forward, 1) == forward[-2:]
    assert curve_point(forward, .5)[1] > 0 > curve_point(backward, .5)[1]
    assert all(math.isfinite(x) for x in contract_curve((1, 1), (1, 1)))
