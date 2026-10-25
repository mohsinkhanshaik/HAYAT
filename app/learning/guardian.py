"""A visible, persistent teaching-only boundary for supervised children."""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.safety.sensitive import redact_text


class ChildGuardian:
    """Restrict one shared runtime to conversation and teaching.

    This is not an identity system. It is a parent-controlled operating mode:
    while active, computer, browser, filesystem, memory, research and coding
    intents are stopped before planning. Every attempt is journalled for the
    parent. A configured HAYAT_PARENT_PIN is required to turn it off.
    """

    SAFE_INTENTS = frozenset({"CONVERSATION", "QUESTION", "LEARNING"})
    SAFE_CONTROLS = frozenset({"stop", "cancel", "mute", "unmute", "be quiet", "pause"})

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or PROJECT_ROOT / "data" / "learning" / "guardian"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.journal_path = self.root / "activity.jsonl"
        self.active = False
        self.age_group = "child"
        self._load()

    def enable(self, age_group: str = "child") -> dict[str, Any]:
        self.active = True
        self.age_group = "young_child" if age_group.lower() in {"young", "young_child"} else "teen" if age_group.lower() == "teen" else "child"
        self._save()
        self._record("mode_enabled", "", True, f"age_group={self.age_group}")
        return self.status()

    def disable(self, pin: str = "") -> dict[str, Any]:
        expected = os.getenv("HAYAT_PARENT_PIN", "").strip()
        if expected and pin.strip() != expected:
            self._record("mode_disable_refused", "", False, "parent PIN did not match")
            raise PermissionError("The parent PIN is required to leave child mode.")
        self.active = False
        self._save()
        self._record("mode_disabled", "", True, "parent control")
        return self.status()

    def check(self, intent: str, text: str) -> tuple[bool, str]:
        if not self.active:
            return True, "child mode is off"
        normalized = " ".join(text.lower().split()).strip(" .!?")
        allowed = intent in self.SAFE_INTENTS or normalized in self.SAFE_CONTROLS
        reason = "teaching/conversation allowed" if allowed else "child mode allows teaching and conversation only"
        self._record(intent, text, allowed, reason)
        return allowed, reason

    def prompt(self) -> str:
        if not self.active:
            return ""
        return (
            f"A parent-controlled {self.age_group} teaching session is active. Use age-appropriate plain language, "
            "one idea at a time, short active-recall questions, and encouraging corrections. Do not expose private "
            "computer state or guide the child around the guardian boundary. If a request needs tools, browsing, "
            "files, accounts, purchases, dangerous instructions, or adult supervision, explain that a parent must take over."
        )

    def report(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.journal_path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.journal_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows[-max(1, min(int(limit), 500)):]

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "age_group": self.age_group,
            "parent_pin_configured": bool(os.getenv("HAYAT_PARENT_PIN", "").strip()),
            "journal": str(self.journal_path),
            "allowed_intents": sorted(self.SAFE_INTENTS),
        }

    def _record(self, intent: str, text: str, allowed: bool, reason: str) -> None:
        cleaned, _ = redact_text(" ".join(text.split())[:500])
        cleaned = re.sub(
            r"(?i)\b(token|password|secret|api[_-]?key|credential)\s*=\s*\S+",
            lambda match: f"{match.group(1)}=[REDACTED]",
            cleaned,
        )
        payload = {
            "timestamp": datetime.now(UTC).isoformat(), "intent": intent,
            "request": cleaned, "allowed": bool(allowed), "reason": reason,
        }
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.active = bool(data.get("active", False))
            self.age_group = str(data.get("age_group", "child"))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"active": self.active, "age_group": self.age_group}, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)
