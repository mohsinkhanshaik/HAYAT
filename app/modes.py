from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT
from app.intent.models import IntentKind, TaskComplexity
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class ExecutionMode(StrEnum):
    AUTO = "AUTO"
    FAST = "FAST"
    BALANCED = "BALANCED"
    DEEP = "DEEP"
    RESEARCH = "RESEARCH"
    CODER = "CODER"
    COMPUTER = "COMPUTER"
    VISION = "VISION"
    VOICE = "VOICE"
    LOCAL_ONLY = "LOCAL_ONLY"
    PRIVATE = "PRIVATE"
    ADVERSARIAL = "ADVERSARIAL"
    TEACH = "TEACH"
    LAB = "LAB"
    MENTOR = "MENTOR"
    INCIDENT = "INCIDENT"


ALIASES = {
    "LOCAL": ExecutionMode.LOCAL_ONLY,
    "RESEARCHER": ExecutionMode.RESEARCH,
    "CODE": ExecutionMode.CODER,
    "COMPUTER_CONTROL": ExecutionMode.COMPUTER,
}


@dataclass
class ModeManager:
    path: Path = field(default_factory=lambda: PROJECT_ROOT / "data/memory/runtime_profile.json")
    configured: list[ExecutionMode] = field(default_factory=lambda: [ExecutionMode.AUTO])
    effective: list[ExecutionMode] = field(default_factory=lambda: [ExecutionMode.BALANCED])
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    #: Called with the new effective modes after every change; a failing
    #: listener never undoes or blocks the change.
    listeners: list[Callable[[list[ExecutionMode]], Any]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    @property
    def available(self) -> list[str]:
        return [mode.value for mode in ExecutionMode] + ['GOD', 'BEAST']

    def set(self, value: str | list[str]) -> list[ExecutionMode]:
        parts = value if isinstance(value, list) else value.replace("+", " ").replace(",", " ").split()
        if isinstance(value, str) and value.strip().upper().replace('_', ' ') in {'GOD MODE', 'BEAST MODE'}:
            parts = [value.strip().upper().replace('_', ' ').split()[0]]
        parsed: list[ExecutionMode] = []
        for raw in parts:
            name = str(raw).strip().upper().replace("-", "_")
            if not name:
                continue
            if name in {'GOD', 'GOD_MODE', 'BEAST', 'BEAST_MODE'}:
                for mode in (ExecutionMode.DEEP, ExecutionMode.ADVERSARIAL):
                    if mode not in parsed:
                        parsed.append(mode)
                continue
            mode = ALIASES.get(name)
            if mode is None:
                try:
                    mode = ExecutionMode(name)
                except ValueError as exc:
                    raise ValueError(f"Unknown mode {raw!r}. Available: {', '.join(self.available)}") from exc
            if mode not in parsed:
                parsed.append(mode)
        if not parsed:
            raise ValueError("At least one mode is required.")
        if ExecutionMode.AUTO in parsed and len(parsed) > 1:
            raise ValueError("AUTO chooses modes dynamically and cannot be combined with fixed modes.")
        with self._lock:
            self.configured = parsed
            self.effective = [ExecutionMode.BALANCED] if parsed == [ExecutionMode.AUTO] else list(parsed)
            self._save()
            effective = list(self.effective)
        for listener in list(self.listeners):
            try:
                listener(effective)
            except Exception:
                log_swallowed(_log, "notifying a mode-change listener")
        return list(self.configured)

    def resolve(self, intent: Any, source: str = "text") -> list[ExecutionMode]:
        with self._lock:
            if self.configured != [ExecutionMode.AUTO]:
                resolved = list(self.configured)
            else:
                kind = getattr(intent, "intent", IntentKind.UNKNOWN)
                complexity = getattr(intent, "complexity", TaskComplexity.INSTANT)
                if kind is IntentKind.QUESTION and complexity in {TaskComplexity.COMPLEX, TaskComplexity.RESEARCH}:
                    resolved = [ExecutionMode.DEEP]
                elif kind in {IntentKind.CONVERSATION, IntentKind.QUESTION, IntentKind.SYSTEM_STATE, IntentKind.CONTROL}:
                    resolved = [ExecutionMode.FAST]
                elif kind == IntentKind.RESEARCH:
                    resolved = [ExecutionMode.RESEARCH, ExecutionMode.DEEP if complexity == TaskComplexity.RESEARCH else ExecutionMode.BALANCED]
                elif kind == IntentKind.CODING:
                    resolved = [ExecutionMode.CODER, ExecutionMode.DEEP if complexity == TaskComplexity.COMPLEX else ExecutionMode.BALANCED]
                elif kind == IntentKind.COMPUTER_CONTROL:
                    resolved = [ExecutionMode.COMPUTER, ExecutionMode.FAST if complexity == TaskComplexity.SIMPLE else ExecutionMode.BALANCED]
                    if getattr(intent, "entities", {}).get("modality") == "vision":
                        resolved.insert(0, ExecutionMode.VISION)
                elif kind == IntentKind.BROWSER:
                    resolved = [ExecutionMode.RESEARCH if getattr(intent, "requires_research", False) else ExecutionMode.COMPUTER, ExecutionMode.BALANCED]
                elif kind == IntentKind.LEARNING:
                    resolved = [ExecutionMode.TEACH, ExecutionMode.BALANCED]
                elif kind == IntentKind.DIAGNOSTIC or getattr(intent, "entities", {}).get("mode") == "incident":
                    resolved = [ExecutionMode.INCIDENT, ExecutionMode.DEEP]
                else:
                    resolved = [ExecutionMode.DEEP if complexity == TaskComplexity.COMPLEX else ExecutionMode.BALANCED]
            if source == "voice" and ExecutionMode.VOICE not in resolved:
                resolved.append(ExecutionMode.VOICE)
            self.effective = resolved
            return list(resolved)

    def snapshot(self) -> dict[str, Any]:
        return {"configured": [mode.value for mode in self.configured], "effective": [mode.value for mode in self.effective], "available": self.available}

    def _load(self) -> None:
        try:
            values = json.loads(self.path.read_text(encoding="utf-8")).get("configured", ["AUTO"])
            self.configured = [ExecutionMode(value) for value in values]
            self.effective = [ExecutionMode.BALANCED] if self.configured == [ExecutionMode.AUTO] else list(self.configured)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.configured = [ExecutionMode.AUTO]
            self.effective = [ExecutionMode.BALANCED]

    def _save(self) -> None:
        self.path.write_text(json.dumps({"configured": [mode.value for mode in self.configured]}, indent=2), encoding="utf-8")
