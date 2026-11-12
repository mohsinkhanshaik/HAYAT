from __future__ import annotations


from app.brain_mesh import BrainMesh
from app.tools.registry import ToolRegistry


def register_brain_mesh_tools(registry: ToolRegistry, mesh: BrainMesh) -> None:
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register(
        "brain_mesh_status", "Inspect HAYAT's cooperating brain-node topology and trust contracts.",
        empty, "low", "system_read", mesh.topology,
    )
    registry.register(
        "brain_mesh_insight", "Inspect registered tools, their capability and subsystem connections, bounded recorded "
        "use, and the last cached health check. Returns a page (10 tools by default, at most 50). Filter by capability "
        "area key or tool-name substring; follow next_offset for the remaining matches. Does not execute tools, "
        "start services, or contact the network.",
        {"type": "object", "properties": {
            "area": {"type": "string", "description": "Optional exact capability key, for example research_lab or files."},
            "tool": {"type": "string", "description": "Optional case-insensitive tool-name substring."},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
         "additionalProperties": False}, "low", "system_read", mesh.insight,
    )
    registry.register(
        "brain_mesh_self_check", "Test every brain node independently and then verify their team contracts.",
        {"type": "object", "properties": {"deep": {"type": "boolean"}},
         "additionalProperties": False},
        "low", "system_read", mesh.self_check,
    )
    registry.register(
        "brain_mesh_benchmark", "Benchmark bounded brain-node routing and coordination without model generation.",
        {"type": "object", "properties": {
            "iterations": {"type": "integer", "minimum": 3, "maximum": 200},
            "write_artifact": {"type": "boolean"},
        }, "additionalProperties": False},
        "low", "filesystem_write_project", mesh.benchmark,
    )
