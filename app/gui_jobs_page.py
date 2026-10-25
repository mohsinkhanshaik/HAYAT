"""Jobs pages scroll as a whole; tables and documents keep their own scrollbars."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.gui_panels import ScrollableColumn


class JobsPage(ScrollableColumn):
    def __init__(self, parent, palette):
        super().__init__(parent, palette)
        self._fit_after = None
        self.bind("<Destroy>", self._cancel_fit, add="+")
        self.canvas.pack_forget()
        self.scrollbar.pack_forget()
        self.across = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.across.set)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.across.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(takefocus=True, yscrollincrement=24, xscrollincrement=24)
        self.canvas.bind("<Button-1>", lambda _event: self.canvas.focus_set())
        for key, count, mode in (("Next", 1, "pages"), ("Prior", -1, "pages"),
                                 ("Down", 1, "units"), ("Up", -1, "units")):
            self.canvas.bind(f"<{key}>", lambda _event, n=count, m=mode: self._key_scroll(n, m))
        self.canvas.bind("<Home>", lambda _event: self.canvas.yview_moveto(0))
        self.canvas.bind("<End>", lambda _event: self.canvas.yview_moveto(1))
        self.canvas.bind("<Shift-MouseWheel>", self._horizontal)

    def _canvas_resized(self, event=None):
        if event is not None:
            self._fit(event.width, event.height)

    def _resized(self, _event=None):
        self._fit(self.canvas.winfo_width(), self.canvas.winfo_height())
        super()._resized()

    def _fit(self, width, height):
        # Preserve room for the filters and split panes at compact sizes.
        self.canvas.itemconfigure(self._window, width=max(780, width),
                                  height=max(height, self.body.winfo_reqheight()))

    def _key_scroll(self, count, mode):
        self.canvas.yview_scroll(count, mode)
        return "break"

    def _horizontal(self, event):
        self.canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def adopt(self, widget: tk.Misc):
        # A child can request more height (wrapped buttons/labels) while the
        # canvas still holds the body's old explicit height. The body itself
        # then receives no Configure event, leaving controls outside its bbox.
        if not getattr(widget, "_jobs_fit_bound", False):
            widget._jobs_fit_bound = True
            widget.bind("<Configure>", self._queue_fit, add="+")
        super().adopt(widget)

    def _queue_fit(self, _event=None):
        if self._fit_after is None:
            self._fit_after = self.after_idle(self._flush_fit)

    def _flush_fit(self):
        self._fit_after = None
        self._resized()

    def _cancel_fit(self, event):
        if event.widget is self and self._fit_after is not None:
            self.after_cancel(self._fit_after)
            self._fit_after = None
