"""Read-only tool and subsystem inventory, with bounded recorded evidence.

Registration, object wiring and past outcomes are distinct facts. This module
never executes a tool, probes a provider, starts an engine, or loads the large
brain population to make the inventory look healthier than the evidence says.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.brain_components import CAPABILITY_BACKING, CAPABILITY_HOME, from_runtime, integration_links
from app.brain_mesh import BrainMesh
from app.capabilities import CATALOG, classify, readiness

USAGE_ROW_LIMIT = 10_000
_LOCAL_READINESS = frozenset({"chat", "voice", "senses", "invention", "replica", "research_lab", "reverse_engineer"})


def _recorded_usage(runtime: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Only the newest indexed tool-call rows; never scan the full event log."""
    source = {"source": "tool-understanding records", "scope": f"newest {USAGE_ROW_LIMIT:,} recorded tool calls",
              "row_limit": USAGE_ROW_LIMIT, "rows_read": 0, "available": False, "since": None, "until": None}
    value = getattr(getattr(runtime, "tool_understanding", None), "path", None)
    if value is None:
        return {}, source
    path = Path(value)
    if not path.is_file():
        return {}, source
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.25)) as db:
            db.execute("PRAGMA query_only=ON")
            rows = db.execute("SELECT tool,ok,refused,at FROM tool_calls ORDER BY id DESC LIMIT ?",
                              (USAGE_ROW_LIMIT,)).fetchall()
    except (OSError, sqlite3.Error) as exc:
        source["error"] = f"Recorded tool use unavailable: {type(exc).__name__}"
        return {}, source
    source.update(available=True, rows_read=len(rows), since=rows[-1][3] if rows else None,
                  until=rows[0][3] if rows else None)
    records: dict[str, dict[str, Any]] = {}
    for name, ok, refused, at in rows:
        entry = records.setdefault(str(name), {"calls": 0, "failures": 0, "refused": 0,
                                              "last_at": at, "last_ok": bool(ok), "last_refused": bool(refused)})
        entry["calls"] += 1
        entry["failures"] += int(not ok and not refused)
        entry["refused"] += int(bool(refused))
    return records, source


def _capability_state(capability: Any, runtime: Any, tools: list[str]) -> tuple[str, str]:
    """Configuration only; do not call arbitrary service status()/ready() methods."""
    if capability.tools and not tools:
        return "off", "No tools for this area are registered in this runtime."
    if capability.key == "senses" and getattr(runtime, "camera", None) is None:
        return "off", "The camera service is unavailable in this runtime."
    if capability.key in _LOCAL_READINESS:
        state, note = readiness(capability, runtime)
        if state != "ready":
            return state, note
        return "registered", note
    backing = CAPABILITY_BACKING.get(capability.key)
    if backing and getattr(runtime, backing, None) is None:
        return "off", f"The {backing.replace('_', ' ')} service is unavailable in this runtime."
    return "registered", "Registered/configured only; provider access and successful execution were not tested."


def _cached_health(mesh: Any) -> dict[str, Any]:
    result = {"checked_at": None, "source": "no cached self-check in this process", "nodes": {},
              "state": "unknown", "scope": "Past local check only; this snapshot does not run health probes."}
    if not isinstance(mesh, BrainMesh):
        return result
    cached = mesh.last_check()
    if not cached:
        return result
    result.update(checked_at=cached.get("generated_at"), source="last completed self-check in this process",
                  nodes=(cached.get("independent") or {}).get("nodes", {}),
                  state="checked" if cached.get("passed") else "attention")
    return result


def build_snapshot(runtime: Any) -> dict[str, Any]:
    """Inventory for a worker-thread inspector; all actions remain on the governed tool bus.

    ``tools`` names every registry entry, ``capabilities`` uses catalog keys,
    and ``nodes``/``edges`` keep the existing BrainMesh topology schema.
    ``health`` is an explicitly dated cached observation, never inferred from
    object presence. ``observations`` declares the bounded historical window.
    """
    gaps: list[dict[str, Any]] = []
    registry = getattr(runtime, "registry", None)
    registry_available = registry is not None
    if not registry_available:
        gaps.append({"kind": "registry", "detail": "The tool registry is unavailable in this runtime."})
    try:
        names = sorted(set(registry.names())) if registry is not None else []
    except Exception as exc:
        names = []
        registry_available = False
        gaps.append({"kind": "registry", "detail": f"The registry inventory could not be read: {type(exc).__name__}"})
    descriptions: dict[str, dict[str, Any]] = {}
    try:
        descriptions = {str(row["name"]): row for row in registry.describe()}
    except Exception as exc:
        gaps.append({"kind": "descriptions", "detail": f"Tool descriptions could not be read: {type(exc).__name__}"})
    grouped, unmatched = classify(names)
    if unmatched:
        gaps.append({"kind": "unmapped_tools", "detail": "Registered tools missing from the capability catalog.",
                     "tools": unmatched})
    capabilities = []
    owners: dict[str, list[str]] = {}
    for capability in CATALOG:
        tools = grouped[capability.key]
        state, note = _capability_state(capability, runtime, tools)
        capabilities.append({"key": capability.key, "title": capability.title, "state": state, "note": note,
                             "state_scope": "registration and local configuration; not functional verification",
                             "tools": tools, "mesh_node": CAPABILITY_HOME.get(capability.key, "tools"),
                             "opens": capability.opens, "opens_label": capability.opens_label})
        for name in tools:
            owners.setdefault(name, []).append(capability.key)
    observations, observation_scope = _recorded_usage(runtime)
    tools = []
    for name in names:
        row = descriptions.get(name, {})
        spec = None
        try:
            spec = registry.get(name)
        except Exception as exc:
            gaps.append({"kind": "handler_unknown", "detail": f"The handler could not be inspected: {type(exc).__name__}",
                         "tools": [name]})
        callable_handler = callable(getattr(spec, "handler", None)) if spec is not None else None
        if callable_handler is False:
            gaps.append({"kind": "handler", "detail": "The registered handler is not callable.", "tools": [name]})
        if not row.get("description"):
            gaps.append({"kind": "description", "detail": "The registered tool has no readable description.", "tools": [name]})
        areas = owners.get(name, [])
        observed = observations.get(name, {"calls": 0, "failures": 0, "refused": 0,
                                           "last_at": None, "last_ok": None, "last_refused": None})
        tools.append({"name": name, "description": str(row.get("description") or getattr(spec, "description", "")),
                      "permission": str(row.get("permission") or getattr(spec, "permission", "")),
                      "risk": str(row.get("risk") or getattr(spec, "risk", "")),
                      "capabilities": areas,
                      "mesh_nodes": sorted({CAPABILITY_HOME.get(key, "tools") for key in areas}) or ["tools"],
                      "component_id": f"tool:{name}", "handler_callable": callable_handler,
                      "observed": {**observed, "scope": observation_scope["scope"],
                                   "available": observation_scope["available"]}})
    # Build only the operational components, never millions of stored/model units.
    components = from_runtime(runtime)
    component_tools = {item.tools[0] for item in components if item.kind == "tool" and item.tools}
    missing = sorted(set(names) - component_tools)
    if missing:
        gaps.append({"kind": "component_coverage", "detail": "Registered tools missing from the component map.", "tools": missing})
    mesh = getattr(runtime, "brain_mesh", None)
    topology = {"nodes": [asdict(node) for node in BrainMesh.NODES],
                "edges": [asdict(edge) for edge in BrainMesh.EDGES]}
    health = _cached_health(mesh)
    if health["state"] == "unknown":
        gaps.append({"kind": "health_unknown", "detail": "No completed self-check is cached; configuration is not proof of health."})
    return {"generated_at": datetime.now(UTC).isoformat(),
            "scope": "Local registry, verified object wiring, bounded past tool calls and cached checks. No tools executed, "
                     "services started, network requests, model inference, or neural activations measured.",
            "summary": {"registry_available": registry_available,
                        "registered_tools": len(names), "mapped_tools": len(names) - len(unmatched),
                        "unmatched_tools": len(unmatched), "operational_components": len(components),
                        "tool_components": len(component_tools), "uncallable_tools": sum(row["handler_callable"] is False for row in tools),
                        "observed_tools": sum(row["observed"]["calls"] > 0 for row in tools),
                        "component_coverage_complete": not missing},
            "tools": tools, "capabilities": capabilities,
            "nodes": topology["nodes"], "edges": topology["edges"],
            "connections": [asdict(link) for link in integration_links(components, runtime)],
            "health": health, "observations": observation_scope, "gaps": gaps}
