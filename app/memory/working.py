from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    goal: str
    task_id: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def observe(self, observation: dict[str, Any]) -> None:
        self.observations.append(observation)
        self.observations = self.observations[-20:]

    def record(self, event: dict[str, Any]) -> None:
        self.history.append(event)

    def clear(self) -> None:
        self.observations.clear()
        self.history.clear()
