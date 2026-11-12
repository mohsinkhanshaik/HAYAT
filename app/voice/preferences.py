"""Voice choices the owner made, remembered on this computer (data/memory/voice-preferences.json).

Turning on wake mode ("listen for Jarvis") used to last until HAYAT closed; the next launch was push-to-talk again.
The choice is now kept here, outside config/settings.yaml, so it belongs to this machine and its owner and is never
published with the code. The microphone still starts only after the owner allows voice for the session.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

MODES = ("push_to_talk", "wake_word", "continuous")


class VoicePreferences:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        kept: dict[str, Any] = {}
        if data.get("mode") in MODES:
            kept["mode"] = data["mode"]
        names = data.get("wake_names")
        if isinstance(names, list) and all(isinstance(name, str) and name.strip() for name in names):
            kept["wake_names"] = [" ".join(name.lower().split()) for name in names][:6]
        return kept

    def save(self, **changes: Any) -> None:
        with self._lock:
            data = {**self.load(), **{key: value for key, value in changes.items() if value is not None}}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except OSError:
                log_swallowed(_log, "saving voice preferences", level=logging.WARNING)
