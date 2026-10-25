from __future__ import annotations

from app.memory.database import MemoryDatabase
from app.safety.permissions import PermissionManager
from app.tools.memory import register_memory_tools
from app.tools.registry import ToolRegistry


def test_ranked_memory_recall_can_continue_page_by_page(tmp_path):
    memory = MemoryDatabase(tmp_path / "paged.db")
    memory.initialize()
    for index in range(27):
        memory.store_memory(
            "semantic", f"orion paged recall fact number {index}",
            0.7 + (index / 1000), "test",
        )

    first = memory.retrieve_page("orion paged recall", page=1, page_size=10)
    second = memory.retrieve_page("orion paged recall", page=2, page_size=10)
    third = memory.retrieve_page("orion paged recall", page=3, page_size=10)

    assert first["returned"] == second["returned"] == 10
    assert third["returned"] == 7
    assert first["next_page"] == 2 and second["next_page"] == 3
    assert third["has_more"] is False and third["next_page"] is None
    ids = [item["id"] for page in (first, second, third) for item in page["items"]]
    assert len(ids) == len(set(ids)) == 27
    assert first["model_window_independent"] is True


def test_paged_recall_is_exposed_as_a_read_only_tool(tmp_path):
    memory = MemoryDatabase(tmp_path / "tool.db")
    memory.initialize()
    memory.store_memory("semantic", "vega memory page", 0.9, "test")
    registry = ToolRegistry(PermissionManager({
        "default": "DENY", "permissions": {"system_read": "ALLOW"},
    }, tmp_path))
    register_memory_tools(registry, memory)

    result = registry.execute("recall_memory_page", {
        "query": "vega memory", "page": 1, "page_size": 5,
    })
    assert result.success
    assert result.output["returned"] == 1
    assert result.output["items"][0]["content"] == "vega memory page"
