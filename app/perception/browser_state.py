from __future__ import annotations

from typing import Any

from app.tools.browser import BrowserTools


class BrowserStatePerception:
    def __init__(self, browser: BrowserTools) -> None:
        self.browser = browser

    def observe(self) -> dict[str, Any]:
        state = self.browser.page_state()
        state["trust"] = "untrusted_external_data"
        return state
