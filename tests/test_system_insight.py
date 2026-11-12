"""Connected-tool inventory must report real configuration without exercising it."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from types import SimpleNamespace

import pytest

import app.brain_components as components
import app.system_insight as insight
from app.brain_mesh import BrainMesh
from app.capabilities import BY_KEY, readiness
from app.tools.brain_mesh import register_brain_mesh_tools
from app.tools.registry import ToolRegistry


class PassiveService:
    def status(self, *args, **kwargs):
        pytest.fail("an inventory must not probe a service")

    def ready(self, *args, **kwargs):
        pytest.fail("an inventory must not start or probe a service")


def _bind(registry, name, service):
    def handler():
        pytest.fail("an inventory must not execute tools")
        return service  # pragma: no cover -- retained closure is the wiring being inspected

    registry.register(name, f"Description of {name}.", {"type": "object", "properties": {}},
                      "low", "system_read", handler)


def _runtime():
    registry = ToolRegistry()
    discovery, robustness, defense = PassiveService(), PassiveService(), PassiveService()
    robustness.discovery = discovery
    runtime = SimpleNamespace(registry=registry, model_discovery=discovery, robustness=robustness,
                              code_defense=defense, reverse_engineer=PassiveService(),
                              model=SimpleNamespace(model="configured-only"), email=PassiveService())
    for name, service in (("model_discover", discovery), ("robustness_lab", robustness), ("code_defense", defense),
                          ("future_unmapped_probe", object())):
        _bind(registry, name, service)
    runtime.brain_mesh = BrainMesh(runtime)
    return runtime


def test_every_registry_tool_has_a_component_even_if_a_new_tool_is_not_classified():
    built = components.build(tool_names=["model_discover", "future_unmapped_probe"],
                             include_tools=True, include_intents=False, include_policy=False)
    neurons = {component.component_id: component for component in built}
    assert neurons["tool:future_unmapped_probe"].parent == "tools"
    assert "not mapped" in neurons["tool:future_unmapped_probe"].detail
    assert neurons["cap:research_lab"].parent == "research"
    assert len([item for item in built if item.component_id == "tool:model_discover"]) == 1


def test_snapshot_maps_tools_to_capabilities_and_existing_mesh_nodes_without_executing(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(runtime.brain_mesh, "self_check", lambda **kwargs: pytest.fail("no implicit self-check"))
    result = insight.build_snapshot(runtime)
    assert result["summary"]["registered_tools"] == 4
    assert result["summary"]["tool_components"] == 4
    assert result["summary"]["mapped_tools"] == 3 and result["summary"]["unmatched_tools"] == 1
    tools = {row["name"]: row for row in result["tools"]}
    assert tools["model_discover"]["capabilities"] == ["research_lab"]
    assert tools["model_discover"]["mesh_nodes"] == ["research"]
    assert tools["future_unmapped_probe"]["capabilities"] == []
    assert tools["future_unmapped_probe"]["mesh_nodes"] == ["tools"]
    assert all(row["handler_callable"] for row in tools.values())
    assert result["nodes"] == [asdict(node) for node in BrainMesh.NODES]
    assert result["edges"] == [asdict(edge) for edge in BrainMesh.EDGES]
    assert result["health"]["state"] == "unknown" and result["health"]["checked_at"] is None
    assert all(row["state"] != "ready" for row in result["capabilities"])
    assert any(gap["kind"] == "unmapped_tools" for gap in result["gaps"])
    json.dumps(result, allow_nan=False)


def test_connections_require_exact_handler_and_service_identity():
    runtime = _runtime()
    found = insight.build_snapshot(runtime)["connections"]
    assert len(found) == 4
    assert all(link["kind"] == "integration" for link in found)
    assert any(link["a"] == "svc:model_discovery" and link["b"] == "svc:robustness" for link in found)
    # Merely replacing a similarly named service does not create a true wire.
    runtime.model_discovery = PassiveService()
    found = insight.build_snapshot(runtime)["connections"]
    assert not any("model_discovery" in link["a"] or "model_discovery" in link["b"] for link in found)
    assert not any(link["a"] == "tool:model_discover" for link in found)


def test_recorded_usage_is_bounded_read_only_and_distinguishes_refusal_from_failure(tmp_path, monkeypatch):
    runtime = _runtime()
    path = tmp_path / "calls.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tool_calls(id INTEGER PRIMARY KEY,tool TEXT,ok INTEGER,refused INTEGER,at TEXT)")
        db.executemany("INSERT INTO tool_calls VALUES(?,?,?,?,?)", [
            (1, "model_discover", 0, 0, "2026-11-10T01:00:00+00:00"),
            (2, "model_discover", 1, 0, "2026-11-10T02:00:00+00:00"),
            (3, "model_discover", 0, 1, "2026-11-10T03:00:00+00:00"),
            (4, "code_defense", 0, 0, "2026-11-10T04:00:00+00:00")])
    runtime.tool_understanding = SimpleNamespace(path=path)
    before = path.stat().st_mtime_ns
    monkeypatch.setattr(insight, "USAGE_ROW_LIMIT", 3)
    result = insight.build_snapshot(runtime)
    tools = {row["name"]: row for row in result["tools"]}
    model = tools["model_discover"]["observed"]
    assert model["calls"] == 2 and model["failures"] == 0 and model["refused"] == 1
    assert model["last_at"] == "2026-11-10T03:00:00+00:00" and model["last_refused"] is True
    assert tools["code_defense"]["observed"]["failures"] == 1
    assert result["observations"]["rows_read"] == 3 and result["observations"]["row_limit"] == 3
    assert result["observations"]["since"] == "2026-11-10T02:00:00+00:00"
    assert path.stat().st_mtime_ns == before


def test_missing_or_invalid_usage_store_is_unknown_and_is_never_created(tmp_path):
    runtime = _runtime()
    path = tmp_path / "missing.db"
    runtime.tool_understanding = SimpleNamespace(path=path)
    assert not insight.build_snapshot(runtime)["observations"]["available"]
    assert not path.exists()
    path.write_text("not a sqlite database", encoding="utf-8")
    result = insight.build_snapshot(runtime)
    assert not result["observations"]["available"] and "error" in result["observations"]


def test_cached_health_keeps_its_timestamp_and_does_not_rerun_or_share_mutable_data(monkeypatch):
    runtime = _runtime()
    checked_at = "2026-11-10T02:30:00+00:00"
    checks = {"passed": True, "critical_failures": [], "nodes": {
        "research": {"passed": True, "detail": "recorded check", "evidence": {"claims": 4}}}}
    monkeypatch.setattr(runtime.brain_mesh, "independent_checks", lambda: checks)
    monkeypatch.setattr(runtime.brain_mesh, "team_checks", lambda: {"passed": True, "checks": {}})
    original = runtime.brain_mesh.self_check()
    original["generated_at"] = checked_at
    original["independent"]["nodes"]["research"]["passed"] = False
    # The returned self-check can be edited without rewriting cached evidence.
    assert runtime.brain_mesh.last_check()["independent"]["nodes"]["research"]["passed"]
    cached_at = runtime.brain_mesh.last_check()["generated_at"]
    monkeypatch.setattr(runtime.brain_mesh, "self_check", lambda **kwargs: pytest.fail("no health probe"))
    result = insight.build_snapshot(runtime)
    assert result["health"]["checked_at"] == cached_at
    assert result["health"]["state"] == "checked"
    result["health"]["nodes"]["research"]["passed"] = False
    assert runtime.brain_mesh.last_check()["independent"]["nodes"]["research"]["passed"]


def test_missing_laboratories_and_analysis_service_are_not_reported_ready():
    runtime = SimpleNamespace()
    assert readiness(BY_KEY["research_lab"], runtime)[0] == "off"
    assert readiness(BY_KEY["reverse_engineer"], runtime)[0] == "off"
    runtime.robustness = PassiveService()
    assert readiness(BY_KEY["research_lab"], runtime)[0] == "setup"
    runtime.model_discovery, runtime.code_defense = PassiveService(), PassiveService()
    assert readiness(BY_KEY["research_lab"], runtime)[0] == "ready"


def test_missing_registry_and_unregistered_capabilities_remain_explicitly_unavailable():
    runtime = _runtime()
    runtime.registry = ToolRegistry()
    result = insight.build_snapshot(runtime)
    areas = {row["key"]: row for row in result["capabilities"]}
    assert areas["research_lab"]["state"] == "off"
    del runtime.registry
    result = insight.build_snapshot(runtime)
    assert result["summary"]["registry_available"] is False
    assert any(gap["kind"] == "registry" for gap in result["gaps"])


def test_insight_tool_is_registered_on_the_shared_read_only_bus():
    runtime = _runtime()
    register_brain_mesh_tools(runtime.registry, runtime.brain_mesh)
    spec = runtime.registry.get("brain_mesh_insight")
    assert spec.permission == "system_read" and spec.risk == "low"
    assert spec.handler()["summary"]["registered_tools"] == 8


def test_insight_tool_pagination_covers_each_tool_once_and_leaves_gui_snapshot_complete():
    runtime = _runtime()
    for index in range(23):
        _bind(runtime.registry, f"extra_unmapped_{index:02}", object())
    first = runtime.brain_mesh.insight()
    assert len(first["tools"]) == 10 and first["total"] == 27
    assert first["offset"] == 0 and first["next_offset"] == 10 and first["truncated"]
    pages = [first]
    while pages[-1]["next_offset"] is not None:
        pages.append(runtime.brain_mesh.insight(offset=pages[-1]["next_offset"]))
    names = [row["name"] for page in pages for row in page["tools"]]
    assert names == runtime.registry.names() and len(names) == len(set(names))
    assert pages[-1]["next_offset"] is None
    assert all("tools" not in area and isinstance(area["tool_count"], int)
               for page in pages for area in page["capabilities"])
    assert len(insight.build_snapshot(runtime)["tools"]) == 27


def test_insight_filters_preserve_usage_and_relevant_wiring_contracts():
    runtime = _runtime()
    result = runtime.brain_mesh.insight(area="research_lab", tool="MODEL")
    assert result["total"] == 1 and not result["truncated"]
    assert [row["name"] for row in result["tools"]] == ["model_discover"]
    assert "observed" in result["tools"][0]
    assert {area["key"] for area in result["capabilities"]} == {"research_lab"}
    assert any(link["a"] == "tool:model_discover" for link in result["connections"])
    assert any(edge["source"] == "research" or edge["target"] == "research" for edge in result["edges"])
    assert runtime.brain_mesh.insight(tool="no_such_tool")["tools"] == []
    assert runtime.brain_mesh.insight(offset=100)["next_offset"] is None


def test_insight_pagination_rejects_invalid_inputs_before_building_snapshot(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(insight, "build_snapshot", lambda runtime: pytest.fail("invalid inputs should be rejected first"))
    for arguments in ({"offset": -1}, {"offset": True}, {"offset": 1.5}, {"limit": 0}, {"limit": 51},
                      {"limit": True}, {"area": "not_an_area"}, {"area": []}, {"tool": None}):
        with pytest.raises(ValueError):
            runtime.brain_mesh.insight(**arguments)
