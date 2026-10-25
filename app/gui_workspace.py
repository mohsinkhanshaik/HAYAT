"""Keyboard commands and bounded, local recovery of unsent text."""
from __future__ import annotations

import sqlite3
import queue
import time
from app.gui_resources import StringVar

import tkinter as tk
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Callable


class DraftStore:
    """Session-scoped drafts; never conversation memory or training input."""

    MAX_CHARACTERS = 64_000
    MAX_DRAFTS = 32
    RETENTION_SECONDS = 7 * 24 * 3600

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=.25)
        db.execute("CREATE TABLE IF NOT EXISTS chat_drafts("
                   "session_id TEXT PRIMARY KEY, content TEXT NOT NULL, updated REAL NOT NULL)")
        return db

    def load(self, session_id: str) -> str:
        if not self.path.exists():
            return ""
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM chat_drafts WHERE updated < ?",
                       (time.time() - self.RETENTION_SECONDS,))
            row = db.execute("SELECT content FROM chat_drafts WHERE session_id=?", (session_id,)).fetchone()
        return str(row[0])[:self.MAX_CHARACTERS] if row else ""

    def save(self, session_id: str, text: str) -> None:
        if len(text) > self.MAX_CHARACTERS:
            raise ValueError("Draft exceeds the 64,000-character recovery limit")
        with closing(self._connect()) as db, db:
            if text:
                db.execute("INSERT INTO chat_drafts VALUES(?,?,?) ON CONFLICT(session_id) "
                           "DO UPDATE SET content=excluded.content,updated=excluded.updated",
                           (session_id, text, time.time()))
            else:
                db.execute("DELETE FROM chat_drafts WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM chat_drafts WHERE updated < ?",
                       (time.time() - self.RETENTION_SECONDS,))
            db.execute("DELETE FROM chat_drafts WHERE session_id NOT IN "
                       "(SELECT session_id FROM chat_drafts ORDER BY updated DESC LIMIT ?)",
                       (self.MAX_DRAFTS,))

    def latest_previous(self, session_id: str) -> str:
        if not self.path.exists():
            return ""
        with closing(self._connect()) as db:
            row = db.execute("SELECT content FROM chat_drafts WHERE session_id != ? AND updated >= ? "
                             "ORDER BY updated DESC LIMIT 1",
                             (session_id, time.time() - self.RETENTION_SECONDS)).fetchone()
        return str(row[0])[:self.MAX_CHARACTERS] if row else ""


class DraftController:
    def __init__(self, widget: tk.Text, store: DraftStore, session: Callable[[], str],
                 enabled: Callable[[], bool], status: Callable[[str], None]) -> None:
        self.widget, self.store, self.session = widget, store, session
        self.enabled, self.status = enabled, status
        self._timer = None
        self._closed = False
        try:
            text = self.store.load(self.session()) if enabled() else ""
            if text:
                widget.insert("1.0", text)
                status("Unsent draft restored")
        except (OSError, sqlite3.Error, ValueError):
            status("Draft recovery unavailable")
        widget.edit_modified(False)
        self._binding = widget.bind("<<Modified>>", self._changed, add="+")

    def _changed(self, event=None):
        if self._closed or not self.widget.edit_modified():
            return
        self.widget.edit_modified(False)
        self._cancel_timer()
        self._timer = self.widget.after(600, self.flush)

    def _cancel_timer(self):
        if self._timer is not None:
            self.widget.after_cancel(self._timer)
            self._timer = None

    def flush(self):
        self._cancel_timer()
        if self._closed or not self.enabled():
            return
        try:
            text = self.widget.get("1.0", "end-1c")
            self.store.save(self.session(), text)
            self.status("Draft saved locally" if text else "")
        except (OSError, sqlite3.Error, ValueError):
            self.status("Draft could not be saved")

    def close(self):
        self.flush()
        self._closed = True
        self.widget.unbind("<<Modified>>", self._binding)

    def recover_previous(self):
        if self._closed or not self.enabled():
            return
        if self.widget.get("1.0", "end-1c").strip():
            self.status("Keep or clear the current text before recovering a draft")
            return
        try:
            text = self.store.latest_previous(self.session())
            if text:
                self.widget.insert("1.0", text)
                self.flush()
                self.status("Previous unsent draft recovered; review before sending")
            else:
                self.status("No previous draft available")
        except (OSError, sqlite3.Error, ValueError):
            self.status("Draft recovery unavailable")


@dataclass(frozen=True)
class WorkspaceCommand:
    label: str
    callback: Callable[[], None]
    keywords: str = ""


def matching_commands(commands: list[WorkspaceCommand], query: str) -> list[WorkspaceCommand]:
    terms = query.casefold().split()
    return [command for command in commands
            if all(term in f"{command.label} {command.keywords}".casefold() for term in terms)]


class CommandPalette(tk.Toplevel):
    """Search only declared callbacks; query text is never an executable command."""

    def __init__(self, parent, commands: list[WorkspaceCommand]):
        super().__init__(parent)
        self.withdraw()
        self.title("HAYAT commands")
        self.transient(parent)
        self.geometry("540x380")
        self.minsize(380, 240)
        self.commands = commands
        self.query = StringVar(self)
        ttk.Label(self, text="Search commands").pack(anchor="w", padx=14, pady=(14, 4))
        self.entry = ttk.Entry(self, textvariable=self.query, takefocus=True)
        self.entry.pack(fill="x", padx=14)
        self.results = tk.Listbox(self, exportselection=False, activestyle="dotbox", takefocus=True)
        self.results.pack(fill="both", expand=True, padx=14, pady=10)
        ttk.Label(self, text="Enter to run · Esc to close · existing permissions apply").pack(pady=(0, 12))
        self.query.trace_add("write", lambda *_: self.refresh())
        self.bind("<Escape>", lambda event: (self.destroy(), "break")[1])
        self.bind("<Return>", self.execute)
        self.entry.bind("<Down>", lambda event: self.move(1))
        self.entry.bind("<Up>", lambda event: self.move(-1))
        self.results.bind("<Double-Button-1>", self.execute)
        self.refresh()
        from app.gui_windows import place_over

        place_over(self, parent, (540, 380))
        self.deiconify()
        self.entry.focus_set()

    def refresh(self):
        self.visible = matching_commands(self.commands, self.query.get())
        self.results.delete(0, "end")
        for command in self.visible:
            self.results.insert("end", command.label)
        if self.visible:
            self.results.selection_set(0)

    def move(self, direction):
        selection = self.results.curselection()
        index = min(max((selection[0] if selection else 0) + direction, 0), len(self.visible) - 1)
        if index >= 0:
            self.results.selection_clear(0, "end")
            self.results.selection_set(index)
            self.results.see(index)
        return "break"

    def execute(self, event=None):
        selection = self.results.curselection()
        if selection and selection[0] < len(self.visible):
            command = self.visible[selection[0]]
            self.destroy()
            command.callback()
        return "break"


class StreamingPreview(tk.Frame):
    """One coalesced UI update per tick, regardless of provider token rate."""

    def __init__(self, parent, palette):
        super().__init__(parent, bg=palette.PANEL_2)
        self.caption = StringVar(self)
        tk.Label(self, textvariable=self.caption, bg=palette.PANEL_2, fg=palette.MUTED,
                 anchor="w").pack(fill="x")
        self.text = tk.Text(self, height=4, wrap="word", state="disabled",
                            bg=palette.PANEL_2, fg=palette.WHITE, relief="flat")
        self.text.pack(fill="x")
        self._updates = queue.Queue(maxsize=1)
        self._content = ""
        self._model = ""
        self._display_model = ""
        self._display_content = ""
        self._follow = True

    def publish(self, kind, text=""):
        if kind == "start":
            self._content, self._model = "", text
        elif kind == "delta":
            self._content = (self._content + text)[-4000:]
        elif kind == "clear":
            self._content, self._model = "", ""
        try:
            self._updates.get_nowait()
        except queue.Empty:
            pass
        try:
            self._updates.put_nowait((self._model, self._content))
        except queue.Full:
            pass

    def drain(self):
        try:
            model, content = self._updates.get_nowait()
        except queue.Empty:
            return
        if not model:
            self.pack_forget()
            self._display_model, self._display_content = "", ""
            return
        new_stream = model != self._display_model or not self.winfo_manager()
        if new_stream:
            self._follow = True
            self.caption.set(f"Generating · {model} · answer checks pending")
        elif self._display_content:
            self._follow = self.text.yview()[1] >= .98
        if not self.winfo_manager():
            from app.gui_rich import pack_above_expanding

            pack_above_expanding(self, pady=4)
        self.text.configure(state="normal")
        if not new_stream and content.startswith(self._display_content):
            self.text.insert("end", content[len(self._display_content):])
        else:
            position = self.text.yview()[0]
            self.text.delete("1.0", "end")
            self.text.insert("1.0", content)
            if not self._follow:
                self.text.yview_moveto(position)
        if self._follow:
            self.text.see("end")
        self.text.configure(state="disabled")
        self._display_model, self._display_content = model, content
