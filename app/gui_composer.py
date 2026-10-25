"""Keep longer drafts readable without letting the composer fill the window."""
from __future__ import annotations

import tkinter as tk


class ComposerSizing:
    def __init__(self, text: tk.Text):
        self.text = text
        self.pending = None
        self.closed = False
        self.root = text.winfo_toplevel()
        for event in ("<<Modified>>", "<KeyRelease>", "<Configure>"):
            text.bind(event, self.schedule, add="+")
        text.bind("<Destroy>", self.close, add="+")
        self.root_binding = self.root.bind("<Configure>", self.window_changed, add="+")
        self.schedule()

    def window_changed(self, event):
        if event.widget is self.root:
            self.schedule()

    def schedule(self, _event=None):
        if not self.closed and self.pending is None:
            self.pending = self.text.after_idle(self.resize)

    def resize(self):
        self.pending = None
        if self.closed:
            return
        count = self.text.count("1.0", "end-1c", "displaylines")
        lines = (count[0] if count else 0) + 1
        window = self.root.winfo_height()
        maximum = 4 if window < 800 else 6
        if window < 800 and getattr(self.root, "_hayat_text_scale", 1.0) >= 1.3:
            maximum = 3
        # Under 600 px (200 % display scaling on a 1080p panel) an empty box
        # takes one line, so the conversation keeps its room.
        minimum = 1 if window < 600 else 2
        if window < 600:
            maximum = 3
        height = max(minimum, min(maximum, lines))
        if int(self.text.cget("height")) != height:
            self.text.configure(height=height)

    def close(self, event):
        if event.widget is not self.text:
            return
        self.closed = True
        if self.pending is not None:
            self.text.after_cancel(self.pending)
            self.pending = None
        self.root.unbind("<Configure>", self.root_binding)
