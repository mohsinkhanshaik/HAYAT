"""Theme-aware scientific entry point for Home; the drawing is an illustration."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk


class ScienceLaunch(tk.Frame):
    """A responsive, keyboard-accessible launch card with no background work."""

    def __init__(self, parent, palette, on_open):
        super().__init__(parent, bg=palette.BLACK, highlightthickness=1,
                         highlightbackground=palette.LINE, padx=16, pady=14)
        self.palette = palette
        self._compact = False
        self.words = tk.Frame(self, bg=palette.BLACK)
        self.words.pack(side='left', fill='both', expand=True)
        self.eyebrow = tk.Label(self.words, text='DISCOVER  /  MEASURE  /  UNDERSTAND', bg=palette.BLACK,
                                fg=palette.GOLD_SOFT, anchor='w', font=('Segoe UI Semibold', 8))
        self.eyebrow.pack(fill='x')
        self.title = tk.Label(self.words, text='Give your ideas a scientific edge.', bg=palette.BLACK,
                              fg=palette.WHITE, anchor='w', justify='left', wraplength=520,
                              font=('Segoe UI Semibold', 20))
        self.title.pack(fill='x', pady=(5, 3))
        self.description = tk.Label(self.words,
            text='Inspect local data, explore patterns, and take the evidence with you.',
            bg=palette.BLACK, fg=palette.MUTED, anchor='w', justify='left',
            wraplength=520, font=('Segoe UI', 10))
        self.description.pack(fill='x')
        self.actions = tk.Frame(self.words, bg=palette.BLACK)
        self.actions.pack(fill='x', pady=(12, 0))
        self.open_button = ttk.Button(self.actions, text='Scientific Workbench  →',
            style='HayatAccent.TButton', command=lambda: on_open('science'))
        self.open_button.pack(side='left')
        self.note = tk.Label(self.actions, text='CSV · statistics · relationships', bg=palette.BLACK,
                              fg=palette.MUTED, font=('Segoe UI', 8), anchor='w')
        self.note.pack(side='left', padx=(12, 0))
        self.art = tk.Canvas(self, width=150, height=122, bg=palette.BLACK,
                             highlightthickness=0, takefocus=False)
        self.art.pack(side='right', padx=(12, 0))
        self._roomy = None
        self.bind('<Configure>', self._fit, add='+')
        self.refresh_theme()

    def _fit(self, _event=None):
        width = self.winfo_width()
        scale = max(1.0, float(getattr(self.winfo_toplevel(), '_hayat_text_scale', 1.0)))
        roomy = not self._compact and width / scale >= 750
        if roomy != self._roomy:
            self._roomy = roomy
            if roomy:
                self.art.pack(side='right', padx=(12, 0), before=self.words)
                self.note.pack(side='left', padx=(12, 0))
            else:
                self.art.pack_forget()
                self.note.pack_forget()
        wrap = max(140, width - (210 if roomy else 38))
        self.title.configure(wraplength=wrap)
        self.description.configure(wraplength=wrap)

    def set_compact(self, compact):
        """Keep a row of task cards visible when Home has little vertical space."""
        if compact == self._compact:
            return
        self._compact = compact
        self.configure(pady=8 if compact else 14)
        self.title.configure(font=('Segoe UI Semibold', 16 if compact else 20))
        self.actions.pack_configure(pady=(6 if compact else 12, 0))
        if compact:
            self.eyebrow.pack_forget()
            self.description.pack_forget()
        else:
            self.eyebrow.pack(fill='x', before=self.title)
            self.description.pack(fill='x', after=self.title)
        self._fit()

    def refresh_theme(self):
        p = self.palette
        self.art.delete('all')
        # Orbital instrument motif, deliberately without measurement labels or invented readings.
        cx, cy = 73, 57
        for radius in (27, 43, 55):
            self.art.create_oval(cx-radius, cy-radius, cx+radius, cy+radius, outline=p.LINE)
        self.art.create_line(8, cy, 139, cy, fill=p.LINE)
        self.art.create_line(cx, 1, cx, 113, fill=p.LINE)
        for rotation in (0, math.pi / 3, -math.pi / 3):
            points = []
            for step in range(65):
                angle = step * math.tau / 64
                x, y = 49 * math.cos(angle), 17 * math.sin(angle)
                points.extend((cx + x*math.cos(rotation) - y*math.sin(rotation),
                               cy + x*math.sin(rotation) + y*math.cos(rotation)))
            self.art.create_line(*points, fill=p.HOLO, width=1.4, smooth=True)
        self.art.create_oval(cx-6, cy-6, cx+6, cy+6, fill=p.GOLD, outline=p.GOLD_SOFT)
        for angle, colour in ((.45, p.GOLD_SOFT), (2.55, p.ROYAL_SOFT), (4.6, p.OLIVE_SOFT)):
            x, y = cx + 43*math.cos(angle), cy + 43*math.sin(angle)
            self.art.create_oval(x-3, y-3, x+3, y+3, fill=colour, outline=p.BLACK)
