"""The chat header's topic chip: which topic the conversation is on, and one click to go to another.

The header badge used to say "Active" whatever was being discussed. It now names the conversation's current topic
from HAYAT's own topic record (app/topics.py) and changes when the topic does: a new topic, a return to an earlier
one, a merge. Clicking it lists the recent topics; choosing one makes it current, so the next message continues it
(the same as typing "back to the … question"). Nothing is shown that the record does not hold: with no topic yet,
the badge keeps its plain "Active".
"""
from __future__ import annotations

import logging
import tkinter as tk
from typing import Any

from app.gui_a11y import activatable
from app.logging import log_swallowed

_log = logging.getLogger(__name__)
#: Longer titles are cut here so the header keeps to one row at 1536 px.
MAX_TITLE = 30
MENU_TOPICS = 8


def chip_text(title: str) -> str:
    value = " ".join(str(title or "").split())
    if not value:
        return "Active"
    return "◈ " + (value if len(value) <= MAX_TITLE else value[:MAX_TITLE - 1].rstrip() + "…")


class TopicChip:
    def __init__(self, gui: Any, badge: tk.Label) -> None:
        self.gui, self.badge = gui, badge
        self.topic_id = ""
        self.shown = True
        badge.configure(cursor="hand2")
        activatable(badge, lambda: (self.open_menu(), "break")[1], gui)

    def _topics(self) -> Any:
        return getattr(self.gui.runtime, "topics", None)

    def _session_id(self) -> str:
        return str(getattr(getattr(self.gui.runtime, "session", None), "session_id", "") or "")

    def current(self) -> dict[str, Any] | None:
        topics = self._topics()
        if topics is None:
            return None
        try:
            rows = topics.topics(self._session_id() or None, 30)
        except Exception:
            log_swallowed(_log, "reading the current topic for the chat header")
            return None
        return next((row for row in rows if row["current"] and row["subject"]), None)

    def refresh(self, reply_topic: dict[str, Any] | None = None) -> str:
        """Show the current topic; ``reply_topic`` is the decision the last reply carried, when there is one."""
        if getattr(self.gui, "_closed", False) or not self.shown:
            return ""
        title, topic_id = "", ""
        if reply_topic and reply_topic.get("title") and reply_topic.get("topic_subject"):
            title, topic_id = str(reply_topic["title"]), str(reply_topic.get("topic_id") or "")
        else:
            row = self.current()
            if row is not None:
                title, topic_id = row["title"], row["topic_id"]
        self.topic_id = topic_id
        text = chip_text(title)
        self.badge.configure(text=text)
        return text

    def show(self, visible: bool) -> None:
        """Only the chat page has a topic; other pages keep their own badge text."""
        self.shown = bool(visible)
        if visible:
            self.refresh()

    def open_menu(self) -> None:
        topics = self._topics()
        gui = self.gui
        menu = tk.Menu(self.badge, tearoff=0, bg=gui.PANEL_2, fg=gui.WHITE, activebackground=gui.PANEL_HOVER,
                       activeforeground=gui.WHITE, bd=0, font=("Segoe UI", 10))
        rows = []
        if topics is not None and self.shown:
            try:
                rows = [row for row in topics.topics(None, 40) if row["subject"]][:MENU_TOPICS]
            except Exception:
                log_swallowed(_log, "listing topics for the chat header")
        if not rows:
            menu.add_command(label="No topics yet: they appear as you talk", state="disabled")
        for row in rows:
            mark = "● " if row["topic_id"] == self.topic_id else "    "
            label = row["title"] if len(row["title"]) <= 46 else row["title"][:45].rstrip() + "…"
            menu.add_command(label=f"{mark}{label}  ({row['messages']})",
                             command=lambda topic_id=row["topic_id"]: self.go_to(topic_id))
        menu.add_separator()
        menu.add_command(label="All topics, growth and scorecard (Mind)", command=gui._open_mind)
        self.menu = menu
        try:
            menu.tk_popup(self.badge.winfo_rootx(), self.badge.winfo_rooty() + self.badge.winfo_height())
        finally:
            menu.grab_release()

    def go_to(self, topic_id: str) -> str:
        """Make an earlier topic current; the next message continues it."""
        topics = self._topics()
        if topics is None or topic_id == self.topic_id:
            return ""
        try:
            result = topics.reactivate(self._session_id(), topic_id)
        except Exception:
            log_swallowed(_log, "switching topic from the chat header", level=logging.WARNING)
            return ""
        if not result.get("reactivated"):
            return ""
        self.refresh()
        said = f"Back on “{result['title']}”. Your next message continues it."
        self.gui._append("system", said)
        return said
