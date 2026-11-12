"""The research-to-invention hand-off is listed as a connection only when it is really wired in this runtime."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.brain_components as components
import app.system_insight as insight
from app.brain_mesh import BrainMesh
from app.invention_handoff import completed_research_result
from app.safety.permissions import PermissionManager
from app.tools.invention import register_invention_tools
from app.tools.registry import ToolRegistry
from tests.test_executive_invention import bench

LINK = ("cap:research", "tool:invention_from_research")


def _runtime(tmp_path, *, wire=True, jobs=True):
    workbench = bench(tmp_path / "invention", None)
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    # research_search stands for the Research area's own tools, so that area exists in the map.
    registry.register("research_search", "Search the web.", {"type": "object", "properties": {}}, "low", "network_read",
                      lambda: pytest.fail("an inventory must not execute tools"))
    runtime = SimpleNamespace(registry=registry, invention=workbench,
                              research_jobs=SimpleNamespace(get=lambda job_id: pytest.fail("an inventory must not read jobs"))
                              if jobs else None)
    if wire:  # exactly what app/runtime.py installs
        workbench.research_result_supplier = lambda job_id: completed_research_result(runtime, job_id)
    runtime.brain_mesh = BrainMesh(runtime)
    return runtime


def _found(runtime):
    return {(link["a"], link["b"]): link for link in insight.build_snapshot(runtime)["connections"]}


def test_the_hand_off_is_a_verified_connection_from_research_to_the_draft_tool(tmp_path):
    runtime = _runtime(tmp_path)
    link = _found(runtime)[LINK]
    assert link["kind"] == "integration" and "execution not tested" in link["basis"]
    assert "finished searches" in link["basis"]
    # Listing it ran nothing: no project exists and the job store was never read (it would have failed the test).
    assert not runtime.invention.list()


@pytest.mark.parametrize("break_it", ["no supplier", "no job store", "another runtime", "another workbench", "tool missing"])
def test_a_look_alike_is_not_reported_as_wired(tmp_path, break_it):
    runtime = _runtime(tmp_path, wire=break_it != "no supplier", jobs=break_it != "no job store")
    if break_it == "another runtime":
        # A supplier built over some other runtime reads some other job store: not this runtime's hand-off.
        other = SimpleNamespace(research_jobs=SimpleNamespace(get=lambda job_id: None))
        runtime.invention.research_result_supplier = lambda job_id: completed_research_result(other, job_id)
    elif break_it == "another workbench":
        # The registered tool still belongs to the first workbench; swapping the attribute does not rewire it.
        runtime.invention = bench(tmp_path / "second", None)
        runtime.invention.research_result_supplier = lambda job_id: completed_research_result(runtime, job_id)
    elif break_it == "tool missing":
        registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
        registry.register("research_search", "Search the web.", {"type": "object", "properties": {}}, "low", "network_read",
                          lambda: None)
        runtime.registry = registry
    assert LINK not in _found(runtime)


def test_the_link_needs_both_ends_in_the_component_map(tmp_path):
    runtime = _runtime(tmp_path)
    everything = components.from_runtime(runtime)
    assert any((link.a, link.b) == LINK for link in components.integration_links(everything, runtime))
    without_research = [item for item in everything if item.component_id != "cap:research"]
    assert not any((link.a, link.b) == LINK for link in components.integration_links(without_research, runtime))
