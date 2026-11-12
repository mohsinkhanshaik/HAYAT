"""Adaptive master/detail panes with native drag handles and no new renderer."""
from __future__ import annotations

import tkinter as tk


class ResponsiveSplit(tk.PanedWindow):
    """Reflow a detail panel below its list when horizontal space runs out."""
    def __init__(self, parent, palette, breakpoint=860):
        super().__init__(parent, orient='horizontal', bg=palette.BLACK, bd=0,
                         sashwidth=8, sashpad=3, opaqueresize=True,
                         showhandle=False, height=300)
        self.breakpoint = breakpoint
        self.compact = None
        self.bind('<Configure>', self._fit, add='+')

    def add(self, child, **kw):
        kw.pop('weight', None)
        super().add(child, minsize=110, stretch='always', **kw)

    def _fit(self, event):
        compact = event.width < self.breakpoint
        if compact != self.compact:
            self.compact = compact
            self.configure(orient='vertical' if compact else 'horizontal', height=440 if compact else 300)
            for child in self.panes():
                self.paneconfigure(child, width='', height='')
