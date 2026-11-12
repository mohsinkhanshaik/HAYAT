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

    def memory_network() -> dict[str, Any]:
        from app.memory.plasticity import describe, network_report

        report = network_report(memory)
        return {**report, "summary": describe(report)}

    registry.register(
        "memory_network",
        "Report HAYAT's memory network as measured: how many memories and links of each kind (similar purpose, "
        "lesson to its task, one task after another, learned from use), how many stand alone, recall-by-meaning "
        "coverage, and which links got stronger because recalling them together helped. Read-only.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "low", "system_read", memory_network,
    )

    # Forgetting, under the owner's control: first show what matches, then delete exactly what the owner names.
    def memory_forget_find(query: str, limit: int = 10) -> dict[str, Any]:
        page = memory.retrieve_page(query, page=1, page_size=max(1, min(int(limit), 25)), strategy="hybrid")
        rows = [{"id": item["id"], "type": item.get("memory_type"), "saved": str(item.get("timestamp") or "")[:10],
                 "text": " ".join(str(item.get("content") or "").split())[:240]}
                for item in page.get("items") or page.get("results") or [] if item.get("id") is not None]
        return {"query": query, "matches": rows,
                "next_step": ("To forget some of these for good, say which numbers, e.g. “forget memories "
                              + ", ".join(str(row["id"]) for row in rows[:3]) + "”.") if rows else "Nothing matched.",
                "summary": f"{len(rows)} memor{'y' if len(rows) == 1 else 'ies'} match “{query}”; nothing was deleted."}

    registry.register(
        "memory_forget_find",
        "Find the memories that match what the owner wants forgotten, with their numbers, dates and text. Deletes "
        "nothing: the owner then names the numbers to forget with memory_forget.",
        {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 300},
                                          "limit": {"type": "integer", "minimum": 1, "maximum": 25}},
         "required": ["query"], "additionalProperties": False},
        "low", "system_read", memory_forget_find,
    )

    def memory_forget(ids: list[int], confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ValueError("Forgetting is permanent. Show the memories with memory_forget_find first, then call again "
                             "with the owner's chosen ids and confirm=true.")
        result = memory.forget(ids)
        return {**result, "summary": f"Forgot {result['forgotten']} memor{'y' if result['forgotten'] == 1 else 'ies'} "
                                     f"for good, with their links and search entries. {result['not_touched']}"}

    registry.register(
        "memory_forget",
        "Permanently delete memories the owner chose by number (from memory_forget_find), with their links, vectors and "
        "search entries. Only for an explicit request to forget; needs confirm=true.",
        {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer", "minimum": 1},
                                                  "minItems": 1, "maxItems": 100},
                                          "confirm": {"type": "boolean"}},
         "required": ["ids", "confirm"], "additionalProperties": False},
        "medium", "filesystem_delete", memory_forget,
    )

    def self_review_report() -> dict[str, Any]:
        from app.self_review import SelfReviewLog

        log = SelfReviewLog.beside(memory)
        return {"calibration": log.calibration(), "summary": log.report(),
                "basis": "Each reply's self-review is compared with the owner's own verdicts; no model is asked."}

    registry.register(
        "self_review_report",
        "How well HAYAT's own reliability ratings match the owner's verdicts: per level (high, medium, low), how many "
        "replies were judged, how many were right, the rating given and the rate observed. Read-only.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "low", "system_read", self_review_report,
    )
