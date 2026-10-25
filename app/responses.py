"""One canonical assistant response, shared by CLI, voice, and any future UI.

Interfaces previously each decided for themselves what had happened -- the CLI
read one string, the voice layer independently re-derived success from tool
history, and both could disagree. Truth is decided once, here, upstream of
presentation. A renderer may shorten or re-word; it may not reinterpret.

The central rule is that SUCCESS is not a mood: a response may only claim it
when the deliverable the request actually called for is present. That is what
made "Done. I verified the requested result." possible while nothing had been
delivered.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ResponseStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DEGRADED = "DEGRADED"


class DeliverableType(StrEnum):
    CONVERSATION = "CONVERSATION"
    ANSWER = "ANSWER"
    ACTION_RESULT = "ACTION_RESULT"
    RESEARCH_RESULT = "RESEARCH_RESULT"
    CAMERA_OBSERVATION = "CAMERA_OBSERVATION"
    SCREEN_OBSERVATION = "SCREEN_OBSERVATION"
    OCR_RESULT = "OCR_RESULT"
    FILE_RESULT = "FILE_RESULT"
    MEMORY_RESULT = "MEMORY_RESULT"
    SYSTEM_STATUS = "SYSTEM_STATUS"
    DIAGNOSTIC = "DIAGNOSTIC"
    CONTROL_RESULT = "CONTROL_RESULT"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    ERROR = "ERROR"

# Deliverable types whose whole purpose is to hand something back. Claiming
# SUCCESS for one of these without content is the generic-"Done" bug, so it is
# rejected structurally rather than by review.
_REQUIRES_CONTENT = {
    DeliverableType.ANSWER, DeliverableType.RESEARCH_RESULT,
    DeliverableType.CAMERA_OBSERVATION, DeliverableType.SCREEN_OBSERVATION,
    DeliverableType.OCR_RESULT, DeliverableType.FILE_RESULT,
    DeliverableType.MEMORY_RESULT, DeliverableType.DIAGNOSTIC,
    DeliverableType.ACTION_RESULT,
}


class MissingDeliverable(ValueError):
    """Raised when a response claims success without the thing it promised."""


@dataclass
class AssistantResponse:
    display_text: str
    spoken_text: str = ""
    status: ResponseStatus = ResponseStatus.SUCCESS
    deliverable_type: DeliverableType = DeliverableType.CONVERSATION
    response_id: str = field(default_factory=lambda: f"resp-{uuid.uuid4().hex[:12]}")
    runtime_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    research: dict[str, Any] | None = None
    file_results: list[dict[str, Any]] = field(default_factory=list)
    memory: dict[str, Any] | None = None
    provenance: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    approval: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.spoken_text:
            self.spoken_text = self.display_text
        self.validate()

    def validate(self) -> None:
        if self.status is not ResponseStatus.SUCCESS:
            return
        if self.deliverable_type in _REQUIRES_CONTENT and not str(self.display_text).strip():
            raise MissingDeliverable(
                f"{self.deliverable_type.value} claimed SUCCESS with no delivered content"
            )
        if self.status is ResponseStatus.SUCCESS and self.failure:
            raise MissingDeliverable("a response cannot be SUCCESS and carry a failure")

    @property
    def complete(self) -> bool:
        """Back-compatible boolean for callers that only need success/not."""
        return self.status is ResponseStatus.SUCCESS

    @classmethod
    def failure_response(cls, display_text: str, *, failure: dict[str, Any] | None = None, spoken_text: str = "", **kwargs: Any) -> "AssistantResponse":
        return cls(
            display_text=display_text, spoken_text=spoken_text or display_text,
            status=ResponseStatus.FAILED, deliverable_type=DeliverableType.ERROR,
            failure=failure, **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_id": self.response_id, "runtime_id": self.runtime_id,
            "session_id": self.session_id, "turn_id": self.turn_id, "task_id": self.task_id,
            "status": self.status.value, "deliverable_type": self.deliverable_type.value,
            "display_text": self.display_text, "spoken_text": self.spoken_text,
            "observations": self.observations, "actions": self.actions,
            "research": self.research, "file_results": self.file_results,
            "memory": self.memory, "provenance": self.provenance, "citations": self.citations,
            "approval": self.approval, "failure": self.failure, "metadata": self.metadata,
        }
