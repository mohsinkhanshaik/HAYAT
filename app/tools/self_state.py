from __future__ import annotations

from app.state import AgentStateService
from app.tools.registry import ToolRegistry


def register_self_state_tool(registry: ToolRegistry, state: AgentStateService) -> None:
    schema = {
        "type": "object",
        "properties": {"live_internet": {"type": "boolean"}, "detailed": {"type": "boolean"}},
        "required": [],
        "additionalProperties": False,
    }
    registry.register(
        "get_agent_state",
        "Read authoritative runtime state including active model/provider, mode, task, project, application, voice, research, internet, memory, vision, tools, and storage. Quick mode avoids deep disk/model scans; detailed=true explicitly runs them. Never infer fields from model knowledge.",
        schema,
        "low",
        "system_read",
        state.snapshot,
    )
