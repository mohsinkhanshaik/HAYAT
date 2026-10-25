"""Read-only, page-by-page access to HAYAT's durable memory."""
from __future__ import annotations

from typing import Any


def register_memory_tools(registry: Any, memory: Any) -> None:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "page": {"type": "integer", "minimum": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
            "strategy": {"type": "string", "enum": ["hybrid", "lexical", "semantic", "hashed_semantic"]},
            "memory_type": {"type": "string"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def recall_memory_page(query: str, page: int = 1, page_size: int = 12,
                           strategy: str = "hybrid", memory_type: str = "") -> dict[str, Any]:
        return memory.retrieve_page(
            query, page=page, page_size=page_size, strategy=strategy,
            memory_type=memory_type or None,
        )

    registry.register(
        "recall_memory_page",
        "Search durable local memory one ranked page at a time; use next_page to recall beyond the current model context without silently truncating the store.",
        schema, "low", "system_read", recall_memory_page,
    )
