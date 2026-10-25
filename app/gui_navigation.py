"""Searchable workspace navigation with keyboard focus and local filtering."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.gui_panels import NavItem, ScrollableColumn
from app.gui_resources import StringVar


GROUPS = (
    ("WORKSPACE", ("chat", "history", "tasks", "task_graph", "invention", "gallery", "files")),
    ("KNOWLEDGE", ("memory", "research", "learning", "background", "training")),
    ("SYSTEM", ("tools", "toolbox", "models", "operator", "health", "improve", "settings")),
)
ALIASES = {"models": "ai llm inference", "training": "train fine tune lora adapter",
           "operator": "brain map diagnostics", "history": "conversation messages saved",
           "files": "documents attachments", "memory": "recall facts knowledge",
           "health": "doctor diagnostics", "settings": "appearance theme preferences"}


class WorkspaceNavigation(tk.Frame):
    def __init__(self, parent, palette, views, on_select):
        super().__init__(parent, bg=palette.BLACK)
        self.palette, self.on_select = palette, on_select
        self.query = StringVar(self, "")
        self.status = StringVar(self, "Ctrl+Shift+K")
        tk.Label(self, text="Find a workspace", bg=palette.BLACK, fg=palette.WHITE,
                 font=("Segoe UI Semibold", 9), anchor="w").pack(fill="x", pady=(8, 0))
        search = tk.Frame(self, bg=palette.BLACK)
        search.pack(fill="x", pady=(5, 0))
        self.entry = ttk.Entry(search, textvariable=self.query, width=16)
        self.entry.bind("<Escape>", self.clear)
        self.entry.bind("<Down>", lambda _event: self.focus_result())
        self.entry.bind("<Return>", self.open_first)
        self.clear_button = ttk.Button(search, text="×", width=2, style="Compact.Hayat.TButton",
                                       command=self.clear, state="disabled")
        self.clear_button.pack(side="right", padx=(4, 0))
        self.entry.pack(side="left", fill="x", expand=True)
        tk.Label(self, textvariable=self.status, bg=palette.BLACK, fg=palette.MUTED,
                 font=("Segoe UI", 8), anchor="w", justify="left", wraplength=190).pack(fill="x", pady=(4, 2))
        self.column = ScrollableColumn(self, palette, width=194)
        self.column.pack(fill="both", expand=True)
        self.items, self.headings, self.search_text = {}, {}, {}
        by_key = {view[0]: view for view in views}
        for heading, keys in GROUPS:
            label = tk.Label(self.column.body, text=heading, bg=palette.BLACK, fg=palette.MUTED,
                             font=("Segoe UI Semibold", 8), anchor="w", padx=10)
            self.headings[heading] = label
            for key in keys:
                if key not in by_key:
                    continue
                _, icon, title, subtitle = by_key[key]
                item = NavItem(self.column.body, palette, key, icon, title, subtitle, on_select)
                item.bind("<Down>", lambda _event, key=key: self.move_focus(key, 1))
                item.bind("<Up>", lambda _event, key=key: self.move_focus(key, -1))
                self.items[key] = item
                self.search_text[key] = " ".join((heading, key.replace("_", " "), title, subtitle, ALIASES.get(key, ""))).casefold()
        self.empty = tk.Label(self.column.body, text="No matching workspaces.\nTry a name such as Models or Training.",
                              bg=palette.BLACK, fg=palette.MUTED, justify="left", anchor="w", wraplength=180, pady=14)
        self.visible_keys = []
        self.query.trace_add("write", self.filter)
        self.filter()
        self.column.adopt(self.column.body)

    def filter(self, *_args):
        terms = self.query.get().casefold().split()
        self.visible_keys = [key for key in self.items if all(term in self.search_text[key] for term in terms)]
        for child in self.column.body.pack_slaves():
            child.pack_forget()
        for heading, keys in GROUPS:
            visible = [key for key in keys if key in self.visible_keys]
            if visible:
                self.headings[heading].pack(fill="x", pady=(12, 5))
                for key in visible:
                    self.items[key].pack(fill="x", pady=2)
        if not self.visible_keys:
            self.empty.pack(fill="x", padx=10)
        count = len(self.visible_keys)
        self.status.set(f"{count} matching {'workspace' if count == 1 else 'workspaces'}" if terms else "Ctrl+Shift+K")
        self.clear_button.configure(state="normal" if self.query.get() else "disabled")
        self.column.canvas.yview_moveto(0)

    def clear(self, _event=None):
        self.query.set("")
        self.entry.focus_set()
        return "break"

    def focus_result(self, key=None):
        if not self.visible_keys:
            return "break"
        key = key if key in self.visible_keys else self.visible_keys[0]
        item = self.items[key]
        item.focus_set()
        self.column.update_idletasks()
        body_height = max(1, self.column.body.winfo_height())
        top = item.winfo_y()
        first, last = self.column.canvas.yview()
        if top < first * body_height:
            self.column.canvas.yview_moveto(top / body_height)
        elif top + item.winfo_height() > last * body_height:
            self.column.canvas.yview_moveto(max(0, top + item.winfo_height() - self.column.canvas.winfo_height()) / body_height)
        return "break"

    def move_focus(self, key, direction):
        if key in self.visible_keys:
            index = self.visible_keys.index(key) + direction
            if index < 0:
                self.entry.focus_set()
            else:
                self.focus_result(self.visible_keys[min(index, len(self.visible_keys) - 1)])
        return "break"

    def open_first(self, _event=None):
        if self.visible_keys:
            self.on_select(self.visible_keys[0])
        return "break"
