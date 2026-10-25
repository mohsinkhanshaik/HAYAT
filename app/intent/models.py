from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class IntentKind(StrEnum):
    CONVERSATION = "CONVERSATION"
    QUESTION = "QUESTION"
    SYSTEM_STATE = "SYSTEM_STATE"
    DIAGNOSTIC = "DIAGNOSTIC"
    RESEARCH = "RESEARCH"
    ACTION = "ACTION"
    CONTROL = "CONTROL"
    MEMORY = "MEMORY"
    LEARNING = "LEARNING"
    AUTOMATION = "AUTOMATION"
    CODING = "CODING"
    COMPUTER_CONTROL = "COMPUTER_CONTROL"
    BROWSER = "BROWSER"
    UNKNOWN = "UNKNOWN"


class TaskComplexity(StrEnum):
    INSTANT = "INSTANT"
    SIMPLE = "SIMPLE"
    STANDARD = "STANDARD"
    COMPLEX = "COMPLEX"
    RESEARCH = "RESEARCH"


@dataclass(slots=True)
class SuccessCriterion:
    type: str
    description: str
    target: str | None = None
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RequestIntent:
    intent: IntentKind
    confidence: float
    goal: str
    requires_tools: bool = False
    requires_research: bool = False
    requires_planning: bool = False
    complexity: TaskComplexity = TaskComplexity.INSTANT
    entities: dict[str, Any] = field(default_factory=dict)
    success_criteria: list[SuccessCriterion] = field(default_factory=list)
    response: str | None = None
    control: str | None = None
    needs_clarification: bool = False
    classification_source: str = "deterministic"

    @property
    def is_action(self) -> bool:
        return self.intent in {
            IntentKind.ACTION, IntentKind.AUTOMATION, IntentKind.CODING,
            IntentKind.COMPUTER_CONTROL, IntentKind.BROWSER, IntentKind.RESEARCH,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
