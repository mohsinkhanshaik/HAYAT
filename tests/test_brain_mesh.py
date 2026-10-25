from __future__ import annotations

import json

import pytest

from app.brain_mesh import BrainMesh


@pytest.fixture(scope="module")
def runtime():
    from app.runtime import build_runtime

    value = build_runtime(use_model=False, start_background_services=False)
    try:
        yield value
    finally:
        value.close()


def test_topology_names_separate_nodes_and_explicit_trust_contracts():
    mesh = BrainMesh.__new__(BrainMesh)
    topology = mesh.topology()
    node_ids = {node["node_id"] for node in topology["nodes"]}

    assert len(node_ids) == len(topology["nodes"]) == 21
    assert {'cortex', 'dual_brain', 'invention', 'inquiry'} <= node_ids
    assert {"authority", "verifier", "memory", "dna", "research", "network",
            "context", "helpers", "tool_judgment"} <= node_ids
    assert topology["model_can_authorize"] is False
    assert topology["truth_order"][0] == "live observation"
    assert topology["view_dimensions"] == ["x", "y", "z", "time", "evidence"]


def test_fifth_dimension_distinguishes_runtime_health_from_evidence_gaps():
    layer = BrainMesh.evidence_layer({
        "research": {"passed": True, "evidence": {"claims": 4, "unresolved_conflicts": 2}},
        "memory": {"passed": True, "evidence": {"checked": 12}},
        "router": {"passed": True, "evidence": {"verified_outcomes": 0}},
    }, "2026-09-30T12:00:00+00:00")
    assert layer["nodes"]["research"]["state"] == "attention"
    assert layer["nodes"]["memory"]["state"] == "checked"
    assert layer["nodes"]["router"]["state"] == "attention"
    assert layer["nodes"]["authority"]["state"] == "unknown"
    assert "not a measure of intelligence" in layer["nodes"]["memory"]["scope"]


def test_each_brain_is_checked_independently_and_as_one_team(runtime):
    report = runtime.brain_mesh.self_check(deep=True)

    assert report["passed"] is True
    assert report["independent"]["critical_failures"] == []
    assert report["team"]["passed"] is True
    assert all(report["team"]["checks"].values())
    assert report["independent"]["nodes"]["context"]["passed"] is True
    assert report["independent"]["nodes"]["helpers"]["evidence"]["shared_core"] is True
    assert report["independent"]["nodes"]["tool_judgment"]["passed"] is True
    assert report["unsafe_capabilities"] == {
        "software_cracking": False,
        "unauthorized_access": False,
        "satellite_control_without_authorized_api": False,
        "counterattack": False,
    }


def test_benchmark_measures_coordination_without_claiming_generation_quality(runtime, tmp_path):
    artifact = tmp_path / "brain-mesh.json"
    result = BrainMesh(runtime, artifact=artifact).benchmark(iterations=3)

    assert result["passed"] is True
    assert result["independent_passed"] is True and result["team_passed"] is True
    assert set(result["benchmarks"]) == {
        "intent", "model_router", "memory_health", "tool_inventory", "team_contract",
    }
    assert all(row["mean_ms"] >= 0 for row in result["benchmarks"].values())
    assert "no generation quality claim" in result["scope"]
    assert json.loads(artifact.read_text(encoding="utf-8"))["passed"] is True


def test_operator_tools_are_registered_on_the_shared_bus(runtime):
    names = set(runtime.registry.names())
    assert {"brain_mesh_status", "brain_mesh_self_check", "brain_mesh_benchmark"} <= names



def test_sources_disagreeing_is_not_a_broken_research_brain(runtime):
    """Recording a contradiction is the behaviour working, not failing.

    The probe failed the whole research node whenever any conflict sat
    unresolved -- so a store that had correctly noticed two sources disagree
    reported itself as broken, while never checking the property it names.
    An unverifiable claim is the real failure.
    """
    mesh = BrainMesh(runtime)

    class _Knowledge:
        def __init__(self, **stats): self._stats = stats
        def stats(self): return self._stats

    original = runtime.research.knowledge
    try:
        runtime.research.knowledge = _Knowledge(
            claims=1574, evidence=1626, unresolved_conflicts=6)
        healthy = mesh._probe_research()
        assert healthy["passed"] is True
        assert healthy["evidence"]["unresolved_conflicts"] == 6

        # A claim with nothing behind it still fails.
        runtime.research.knowledge = _Knowledge(
            claims=100, evidence=12, unresolved_conflicts=0)
        assert mesh._probe_research()["passed"] is False
        runtime.research.knowledge = _Knowledge(
            claims=2, evidence=20, unsupported_claims=1, unresolved_conflicts=0)
        assert mesh._probe_research()["passed"] is False, "many citations for one claim cannot cover another claim"
    finally:
        runtime.research.knowledge = original
