"""Price alerts checked in the background while HAYAT runs, not only when a price is read.

Every two minutes, while an alert is waiting, HAYAT reads those markets' prices (one public
quote each) and reports a reached alert in the conversation, the terminal and by voice.
With no alert waiting it reads nothing. Nothing is sent outside this computer and nothing is traded.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any, Callable

from app.logging import log_swallowed
from app.markets import money

_log = logging.getLogger(__name__)


def alert_text(alert: dict[str, Any]) -> str:
    return (f"Price alert #{alert['id']} reached: {alert['symbol']} is {alert['direction']} {money(alert['price'])} "
            f"(now {money(alert['triggered_price'])}). Only a notice: nothing was bought or sold.")


class AlertWatcher:
    def __init__(self, markets: Any, notify: Callable[[str], Any], interval_seconds: float = 120.0) -> None:
        self.markets, self.notify, self.interval = markets, notify, max(30.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_checked: str | None = None

    def waiting(self) -> int:
        if not self.markets.path:
            return 0
        return sum(1 for row in self.markets.alerts("list")["alerts"] if not row["triggered_at"])

    def check_once(self) -> list[dict[str, Any]]:
        """Check the waiting alerts now; report and return the reached ones."""
        if not self.waiting():
            return []
        reached = self.markets.alerts("check")["triggered"]
        self.last_checked = datetime.now(UTC).isoformat(timespec="seconds")
        for alert in reached:
            try:
                self.notify(alert_text(alert))
            except Exception:  # a failed notice must not stop the next check
                log_swallowed(_log, "reporting a reached price alert", level=logging.WARNING)
        return reached

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hayat-price-alerts", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.check_once()
            except Exception:  # offline or rate-limited: try again next round
                log_swallowed(_log, "checking price alerts in the background")
