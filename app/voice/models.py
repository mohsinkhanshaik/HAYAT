from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class VoiceState(StrEnum):
    READY = "ready"
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    ACTING = "acting"
    VERIFYING = "verifying"
    APPROVAL = "approval_required"
    SPEAKING = "speaking"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(slots=True)
class AudioData:
    samples: Any
    sample_rate: int
    channels: int = 1

    @property
    def duration_seconds(self) -> float:
        return float(len(self.samples)) / float(self.sample_rate or 1)


@dataclass(slots=True)
class Transcription:
    text: str
    language: str | None = None
    confidence: float | None = None
    duration_ms: int = 0
    raw_text: str = ""
    alternatives: tuple[str, ...] = ()
    decode_passes: int = 1


@dataclass(slots=True)
class VoiceResponse:
    display_response: str
    spoken_response: str
    task_id: str | None = None
    complete: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
