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
