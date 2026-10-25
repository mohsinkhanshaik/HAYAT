"""Tools for HAYAT's measurable adaptive control plane."""
from __future__ import annotations

from typing import Any


def register_metacognition_tools(registry: Any, control: Any) -> None:
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register(
        "metacognition_status",
        "Report HAYAT's evidence-based self-model, learning priorities, control loop, and limits without claiming subjective consciousness.",
        empty, "low", "system_read", control.status,
    )
    registry.register(
        "model_delegation_map",
        "Show which local model HAYAT would use for each capability and the verified evidence behind each choice.",
        empty, "low", "system_read", control.delegation_map,
    )
    registry.register(
        "sensory_fusion_status",
        "Report voice, camera multi-frame, OCR, and display evidence with explicit uncertainty and grounding precedence.",
        empty, "low", "system_read", control.sensory_state,
    )
    registry.register(
        "adaptive_learning_curriculum",
        "Rank current learning gaps from conflicts, stale evidence, runtime failures, and measured weaknesses.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False},
        "low", "system_read", control.curriculum,
    )
    registry.register(
        "queue_adaptive_learning",
        "Queue a small public-safe batch of the highest-value verified learning gaps for the restricted continuous learner.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}}, "additionalProperties": False},
        "low", "knowledge_research", control.queue_curriculum,
    )
    registry.register(
        "run_adaptive_review",
        "Queue the highest-value public learning gaps, apply only small reversible maintenance, and report larger upgrade proposals for review.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}}, "additionalProperties": False},
        "medium", "knowledge_research", control.adapt,
    )
