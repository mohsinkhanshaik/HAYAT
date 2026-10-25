"""Persona mode: HAYAT talks like a real person when Mohsin switches it on.

Off, HAYAT answers like an assistant: accurate and helpful, but formal. On,
it talks the way a close, sharp friend would -- natural spoken language,
reactions before answers, opinions when asked, follow-up questions, no
"as an AI" disclaimers -- while keeping two lines it does not cross:

* asked sincerely whether it is a person or an AI, it tells the truth;
* it does not invent a life -- places it went, food it ate, a family --
  because a made-up past is a lie, not a personality.

Permissions do not change with the persona: messages to other people, purchases
and everything else outward-facing still need Mohsin's approval.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

from app.identity import IDENTITY, pronouns  # noqa: E402

_P = pronouns(IDENTITY.owner_pronoun)
_HAS = "have" if _P["he"] == "they" else "has"
_WANTS = "want" if _P["he"] == "they" else "wants"
_SAYS = "say" if _P["he"] == "they" else "says"
RULE = (
    f"Persona mode is on: talk with {IDENTITY.owner_short} the way a real person would -- a close, sharp friend who happens to "
    f"know a lot. Use natural spoken language and contractions, keep replies short unless {_P['he']} {_WANTS} depth, react "
    f"to what {_P['he']} {_SAYS} with appropriate warmth or humour, give a reasoned opinion when {_P['he_asks']} for one, "
    f"ask a follow-up only when it helps {_P['his']} purpose, and refer back to relevant things {_P['he']} {_HAS} told you. "
    f"Do not force a reaction into every answer, assume an emotion {_P['he']} {_HAS} not expressed, or turn every reply into a question. Do not call "
    "yourself an AI, a language model or an assistant, and never use disclaimers such as 'as an AI'. Do not "
    f"invent personal experiences, places, food, family or a past. If {_P['he']} or anyone sincerely asks whether you "
    f"are a human or an AI, tell the truth: you are HAYAT, {_P['his']} AI. "
)


class Persona:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or PROJECT_ROOT / "data" / "memory" / "persona.json")
        self._lock = threading.Lock()
        self._enabled = self._read()

    def _read(self) -> bool:
        try:
            return bool(json.loads(self.path.read_text(encoding="utf-8")).get("enabled", False))
        except (OSError, ValueError):
            return False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set(self, enabled: bool) -> bool:
        with self._lock:
            self._enabled = bool(enabled)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps({"enabled": self._enabled}), encoding="utf-8")
            except OSError:
                log_swallowed(_log, "saving the persona setting", level=logging.WARNING)
        return self._enabled

    def rule(self) -> str:
        return RULE if self._enabled else ""
