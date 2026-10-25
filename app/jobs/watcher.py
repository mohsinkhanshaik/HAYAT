"""The automatic job round while HAYAT runs: search, match, prepare strong matches (if allowed), follow-ups.

Every six hours by default (each source is read at most hourly anyway). New matches at or above the
owner's notify score are reported in the conversation; follow-up emails are only drafted. Nothing is
ever sent from here: sending stays with the confirmation window.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


def round_text(result: dict[str, Any]) -> str | None:
    lines = []
    if result["new_matches"]:
        lines.append(f"{len(result['new_matches'])} new job match{'es' if len(result['new_matches']) != 1 else ''}:")
        lines += [f"  #{row['number']} {row['title']} at {row['company']} · {row['score']} ({row['verdict']}) · {row['source_name']}"
                  for row in result["new_matches"][:5]]
    if result["prepared"]:
        lines.append("Applications prepared (not sent): " + ", ".join(f"job {number}" for number in result["prepared"])
                     + ". Say “send my application for job N” to review one.")
    for item in result["follow_ups"]:
        lines.append(f"No reply from {item['company']} (job {item['number']}) after a week: a polite follow-up is drafted in {item['draft']}. "
                     "Nothing was sent.")
    return "\n".join(lines) or None


class JobWatcher:
    def __init__(self, center: Any, notify: Callable[[str], Any], interval_seconds: float = 6 * 3600) -> None:
        self.center, self.notify, self.interval = center, notify, max(600.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_round: str | None = None
        self.last_result: dict[str, Any] | None = None

    def run_once(self) -> dict[str, Any]:
        result = self.center.round()
        self.last_round, self.last_result = datetime.now(UTC).isoformat(timespec="seconds"), result
        text = round_text(result)
        if text:
            try:
                self.notify(text)
            except Exception:  # a failed notice must not stop the next round
                log_swallowed(_log, "reporting job matches", level=logging.WARNING)
        return result

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hayat-jobs", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # The first round waits a few minutes, so starting HAYAT stays quick.
        if self._stop.wait(300):
            return
        while True:
            try:
                self.run_once()
            except Exception:  # offline or a source changed: try again next round
                log_swallowed(_log, "the background job round")
            if self._stop.wait(self.interval):
                return
