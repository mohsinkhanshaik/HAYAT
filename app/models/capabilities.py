from __future__ import annotations

from typing import Any


class ModelCapabilityRegistry:
    def __init__(self, storage, router) -> None:
        self.storage = storage
        self.router = router

    def inventory(self) -> dict[str, Any]:
        inventory = self.storage.model_inventory()
        selections = {}
        for purpose in ("conversation_model", "classification_model", "reasoning_model", "coding_model", "research_synthesis_model", "vision_model", "embedding_model"):
            if purpose == "embedding_model" and not self.router.config.get(purpose, {}).get("model"):
                selections[purpose] = {"provider": None, "model": None, "available": False, "reason": "No installed embedding model is configured."}
                continue
            provider = self.router.get(purpose)
            selections[purpose] = {"provider": getattr(provider, "name", None), "model": getattr(provider, "model", None), "available": provider is not None}
        return {**inventory, "selections": selections}

    def best_for(self, capability: str) -> dict[str, Any] | None:
        rows = self.inventory()["models"]
        eligible = [row for row in rows if capability in row.get("capabilities", []) or capability == "conversation" and "completion" in row.get("capabilities", [])]
        if not eligible:
            return None
        score_key = {"reasoning": "reasoning_accuracy", "conversation": "conversation_accuracy", "planning": "planning_accuracy"}.get(capability)
        return min(eligible, key=lambda row: (-(row.get("benchmark_score") or {}).get(score_key, 0), (row.get("benchmark_score") or {}).get("mean_latency_seconds", 9999)))
