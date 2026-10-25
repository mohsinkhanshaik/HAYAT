"""Resizable overview column, with independent visibility choices for wide and compact windows."""
from __future__ import annotations

import tkinter as tk


class OverviewLayout:
    DEFAULT_WIDTH = 404
    MIN_WIDTH = 300
    MAX_WIDTH = 600

    def __init__(self, gui):
        self.gui, self.parent, self.rail = gui, gui._body, gui.right_rail
        self.desired_width = self.DEFAULT_WIDTH
        self.choices = {False: True, True: False}
        self.drag = None
        self.grip = tk.Frame(self.parent, width=6, bg=gui.LINE, cursor='sb_h_double_arrow', takefocus=1)
        self.grip.bind('<ButtonPress-1>', self._start)
        self.grip.bind('<B1-Motion>', self._move)
        self.grip.bind('<ButtonRelease-1>', self._stop)
        self.grip.bind('<Escape>', self._stop)
        self.grip.bind('<Left>', lambda _event: self.resize_by(24))
        self.grip.bind('<Right>', lambda _event: self.resize_by(-24))
        self.grip.bind('<Double-Button-1>', lambda _event: self.reset())
        self.grip.bind('<FocusIn>', lambda _event: self.grip.configure(bg=gui.GOLD))
        self.grip.bind('<FocusOut>', lambda _event: self.grip.configure(bg=gui.LINE))
        from app.gui_help import Tooltip
        Tooltip(self.grip, 'Drag to resize the overview. Left/Right adjusts its width. Double-click resets it.', type(gui))

    def preferred_open(self, compact):
        return self.choices[compact]

    def remember_open(self, value):
        self.choices[bool(getattr(self.gui, '_compact_layout', False))] = bool(value)

    def width(self):
        available = max(1, self.gui.root.winfo_width())
        if getattr(self.gui, '_compact_layout', False):
            maximum = min(self.MAX_WIDTH, int(available * .60))
        else:
            # Preserve a useful conversation width beside the navigation rail.
            maximum = min(self.MAX_WIDTH, available - self.gui._sidebar.winfo_reqwidth() - 580)
        return min(max(self.MIN_WIDTH, self.desired_width), max(self.MIN_WIDTH, maximum))

    def hide_grip(self):
        if self.drag is not None:
            self._stop()
        self.grip.pack_forget()
        self.grip.place_forget()

    def dock_grip(self):
        self.rail.configure(width=self.width())
        self.grip.pack(side='right', fill='y', padx=(2, 0))

    def reflow(self):
        if self.gui._closed:
            return
        visible = getattr(self.gui, '_overview_open', True) and not self.gui._focus_mode
        if not visible:
            self.hide_grip()
            return
        width = self.width()
        if getattr(self.gui, '_compact_layout', False):
            if self.rail.winfo_manager() == 'place':
                self.rail.place_configure(width=width)
                self.grip.place(relx=1, x=-width, y=0, anchor='ne', width=6, relheight=1)
                self.grip.lift()
        else:
            self.rail.configure(width=width)

    def resize_by(self, delta):
        self.desired_width = min(self.MAX_WIDTH, max(self.MIN_WIDTH, self.width() + delta))
        self.reflow()
        return 'break'

    def reset(self):
        self.desired_width = self.DEFAULT_WIDTH
        self.reflow()
        return 'break'

    def _start(self, event):
        self.drag = event.x_root, self.width()
        self.grip.focus_set()
        self.grip.grab_set()
        return 'break'

    def _move(self, event):
        if self.drag is not None:
            x, width = self.drag
            self.desired_width = min(self.MAX_WIDTH, max(self.MIN_WIDTH, width + x - event.x_root))
            self.reflow()
        return 'break'

    def _stop(self, _event=None):
        self.drag = None
        if self.grip.grab_current() is self.grip:
            self.grip.grab_release()
        return 'break'
