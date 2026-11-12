"""Recent chats in the main window's right column: the conversations the owner was in, one click from resuming.

The owner's concept boards all carry a "Recent chats" list beside the workspace;
the window had a full-text Conversations view but nothing that showed, at a
glance, what he was talking about an hour or a day ago. The list is read from
the conversation table on a worker thread every 30 seconds and handed to Tk
through the window's own queue. Clicking a chat puts "What did we discuss
about ...?" in the message box: it is answered from memory, and nothing is
sent until he presses Send.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from datetime import datetime, timezone
from typing import Any

from app.gui_a11y import activatable
from app.logging import log_swallowed

_log = logging.getLogger(__name__)
REFRESH_MS = 30_000


def recent_conversations(memory: Any, limit: int = 5) -> list[dict[str, str]]:
    """The latest conversations: their first request and when they were last active."""
    with memory.connect() as db:
        rows = db.execute(
            "SELECT c.session_id, MAX(c.timestamp) AS last, "
            "(SELECT f.content FROM conversations f WHERE f.session_id=c.session_id AND f.role='user' "
            " ORDER BY f.id LIMIT 1) AS first "
            "FROM conversations c GROUP BY c.session_id HAVING first IS NOT NULL "
            "ORDER BY MAX(c.id) DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
    return [{"session": str(row[0]), "last": str(row[1] or ""), "title": " ".join(str(row[2]).split())}
            for row in rows]


def ago(timestamp: str, now: datetime | None = None) -> str:
    """"5m ago" / "3h ago" / "2d ago"; "" for an unreadable time."""
    try:
        then = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int((now - then).total_seconds()))
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "now"


class RecentChats:
    """Fills a right-column section body with the latest conversations."""

    def __init__(self, body: tk.Frame, gui: Any) -> None:
        self.body, self.gui = body, gui
        self.rows = tk.Frame(body, bg=type(gui).PANEL)
        self.rows.pack(fill="x")
        self.items: list[dict[str, str]] | None = None
        self._busy = False
        gui._schedule(1500, self.refresh)

    def refresh(self) -> None:
        if getattr(self.gui, "_closed", False):
            return
        self.gui._schedule(REFRESH_MS, self.refresh)
        if self._busy:
            return
        self._busy = True

        def work() -> None:
            try:
                items = recent_conversations(self.gui.runtime.memory)
            except Exception:
                log_swallowed(_log, "listing recent conversations")
                items = None
            self.gui._post(self.show, items)

        threading.Thread(target=work, name="hayat-recent-chats", daemon=True).start()

    def show(self, items: list[dict[str, str]] | None) -> None:
        self._busy = False
        if items is None or items == self.items:
            return
        self.items = items
        p = type(self.gui)
        for child in self.rows.winfo_children():
            child.destroy()
        if not items:
            tk.Label(self.rows, text="No conversations yet.", bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9),
                     anchor="w").pack(fill="x")
        for item in items:
            row = tk.Frame(self.rows, bg=p.PANEL)
            row.pack(fill="x", pady=1)
            title = item["title"] if len(item["title"]) <= 44 else item["title"][:43].rstrip() + "…"
            tk.Label(row, text=ago(item["last"]), bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8)).pack(side="right")
            label = tk.Label(row, text="💬 " + title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 9), anchor="w",
                             cursor="hand2")
            label.pack(side="left", fill="x", expand=True)
            activatable(label, lambda text=item["title"]: self.open(text), p)
            label.bind("<Enter>", lambda _event, widget=label: widget.configure(fg=p.GOLD_SOFT))
            label.bind("<Leave>", lambda _event, widget=label: widget.configure(fg=p.WHITE))
        folds = getattr(self.gui, "_folds", None)
        if folds is not None and items:
            folds.summarise("Recent chats", f"{len(items)} recent · {items[0]['title'][:30]}")

    def open(self, title: str) -> None:
        self.gui.use_example(f"What did we discuss about \"{title[:120]}\"?")
