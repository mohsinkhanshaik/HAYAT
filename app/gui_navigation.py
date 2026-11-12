"""Searchable workspace navigation with keyboard focus and local filtering."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import time

from app.gui_panels import NavItem, ScrollableColumn
from app.gui_resources import StringVar
from app.gui_output import DisplayVar


GROUPS = (
    ("WORKSPACE", ("chat", "history", "tasks", "task_graph", "invention", "gallery", "files")),
    ("KNOWLEDGE", ("memory", "research", "learning", "background", "training")),
    ("SYSTEM", ("tools", "toolbox", "models", "operator", "health", "improve", "settings")),
)
ALIASES = {"models": "ai llm inference", "training": "train fine tune lora adapter",
           "operator": "brain map diagnostics", "history": "conversation messages saved",
           "files": "documents attachments", "memory": "recall facts knowledge",
           "health": "doctor diagnostics", "settings": "appearance theme preferences"}


class NavigationHistory:
    def __init__(self, limit=50):
        self.entries, self.index, self.limit = [], -1, max(2, limit)

    def push(self, key):
        if self.index >= 0 and self.entries[self.index] == key:
            return
        self.entries = self.entries[:self.index+1] + [key]
        self.entries = self.entries[-self.limit:]
        self.index = len(self.entries)-1

    def move(self, direction):
        target = self.index + direction
        if not 0 <= target < len(self.entries):
            return None
        self.index = target
        return self.entries[target]

    @property
    def can_back(self): return self.index > 0

    @property
    def can_forward(self): return self.index < len(self.entries)-1


class WorkspaceNavigation(tk.Frame):
    def __init__(self, parent, palette, views, on_select, on_back=None, on_forward=None):
        super().__init__(parent, bg=palette.BLACK)
        self.palette, self.on_select = palette, on_select
        self.query = StringVar(self, "")
        self.status = DisplayVar(self, "Ctrl+Shift+K")
        self._marker_job, self._active_key = None, None
        self._compact = False
        history = tk.Frame(self, bg=palette.BLACK)
        history.pack(fill='x', pady=(2, 4))
        self.back_button = ttk.Button(history, text='← Back', style='Compact.Hayat.TButton',
                                      command=on_back, state='disabled')
        self.forward_button = ttk.Button(history, text='Forward →', style='Compact.Hayat.TButton',
                                         command=on_forward, state='disabled')
        self.back_button.pack(side='left', fill='x', expand=True, padx=(0, 2))
        self.forward_button.pack(side='left', fill='x', expand=True, padx=(2, 0))
        self.search_heading = tk.Label(self, text="Find a workspace", bg=palette.BLACK, fg=palette.WHITE,
                                        font=("Segoe UI Semibold", 9), anchor="w")
        self.search_heading.pack(fill="x", pady=(8, 0))
        search = tk.Frame(self, bg=palette.BLACK)
        search.pack(fill="x", pady=(5, 0))
        self.entry = ttk.Entry(search, textvariable=self.query, width=16)
        # The field's own colour: on PANEL_2 the hint was a lighter box inside the darker field.
        self.search_hint = tk.Label(self.entry, text='Find a workspace…', bg=palette.PANEL, fg=palette.MUTED,
                                    font=('Segoe UI', 9), cursor='xterm', anchor='w')
        self.search_hint.bind('<Button-1>', lambda _event: self.entry.focus_set())
        self.entry.bind('<FocusIn>', self._refresh_hint, add='+')
        self.entry.bind('<FocusOut>', self._refresh_hint, add='+')
        from app.gui_help import Tooltip
        Tooltip(self.entry, 'Find a workspace · Ctrl+Shift+K · Down selects a result · Enter opens it', palette)
        self.entry.bind("<Escape>", self.clear)
        self.entry.bind("<Down>", lambda _event: self.focus_result())
        self.entry.bind("<Return>", self.open_first)
        self.clear_button = ttk.Button(search, text="×", width=2, style="Compact.Hayat.TButton",
                                       command=self.clear, state="disabled")
        self.clear_button.pack(side="right", padx=(4, 0))
        self.entry.pack(side="left", fill="x", expand=True)
        self.search_status = tk.Label(self, textvariable=self.status, bg=palette.BLACK, fg=palette.MUTED,
                                      font=("Segoe UI", 8), anchor="w", justify="left", wraplength=190)
        self.search_status.pack(fill="x", pady=(4, 2))
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
        self.empty = tk.Label(self.column.body, text="No matching pages.\nTry another word, or use Search commands to find all tools and windows.",
                              bg=palette.BLACK, fg=palette.MUTED, justify="left", anchor="w", wraplength=180, pady=14)
        self.visible_keys = []
        self.marker = tk.Frame(self.column.body, bg=palette.GOLD, width=3, takefocus=False)
        self.column.body.bind('<Configure>', lambda _event: self.set_active(self._active_key), add='+')
        self.bind('<Destroy>', self._destroy_marker, add='+')
        self.query.trace_add("write", self.filter)
        self.filter()
        self.column.adopt(self.column.body)

    def set_compact(self, compact):
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self.search_heading.pack_forget()
            self.search_status.pack_forget()
        else:
            self.search_heading.pack(fill='x', pady=(8, 0), before=self.entry.master)
            self.search_status.pack(fill='x', pady=(4, 2), before=self.column)
        for item in self.items.values():
            item.body.configure(pady=4 if compact else 8)
            if compact:
                item.subtitle.pack_forget()
            else:
                item.subtitle.pack(fill='x')
        self.filter(reset_scroll=False)

    def filter(self, *_args, reset_scroll=True):
        terms = self.query.get().casefold().split()
        self.visible_keys = [key for key in self.items if all(term in self.search_text[key] for term in terms)]
        for child in self.column.body.pack_slaves():
            child.pack_forget()
        for heading, keys in GROUPS:
            visible = [key for key in keys if key in self.visible_keys]
            if visible:
                self.headings[heading].pack(fill="x", pady=(6, 3) if self._compact else (12, 5))
                for key in visible:
                    self.items[key].pack(fill="x", pady=2)
        if not self.visible_keys:
            self.empty.pack(fill="x", padx=10)
        count = len(self.visible_keys)
        self.status.set(f"{count} matching {'workspace' if count == 1 else 'workspaces'}" if terms else "Ctrl+Shift+K")
        self.clear_button.configure(state="normal" if self.query.get() else "disabled")
        if reset_scroll:
            self.column.canvas.yview_moveto(0)
        self.set_active(self._active_key)
        self._refresh_hint()

    def _refresh_hint(self, _event=None):
        if self.query.get() or self.entry.focus_get() is self.entry:
            self.search_hint.place_forget()
        else:
            self.search_hint.place(x=7, rely=.5, anchor='w', relwidth=.85)

    def history_state(self, back, forward):
        self.back_button.configure(state='normal' if back else 'disabled')
        self.forward_button.configure(state='normal' if forward else 'disabled')

    def _destroy_marker(self, event):
        if event.widget is self and self._marker_job is not None:
            self.after_cancel(self._marker_job)
            self._marker_job = None

    def set_active(self, key):
        self._active_key = key
        if self._marker_job is not None:
            self.after_cancel(self._marker_job)
            self._marker_job = None
        if key not in self.visible_keys:
            self.marker.place_forget()
            return
        item = self.items[key]
        destination = item.winfo_y()+4
        height = max(12, item.winfo_height()-8)
        from app.gui_motion import MOTION
        start = self.marker.winfo_y() if self.marker.winfo_manager() else destination
        began = time.monotonic()
        def frame():
            self._marker_job = None
            progress = min(1, (time.monotonic()-began)/.14) if MOTION['enabled'] else 1
            y = round(start + (destination-start)*(1-(1-progress)**3))
            self.marker.place(x=1, y=y, width=3, height=height)
            self.marker.lift()
            if progress < 1:
                self._marker_job = self.after(15, frame)
        frame()

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
