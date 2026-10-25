"""Visible attachment ingestion with per-file progress, including partial failure."""
from pathlib import Path
import tkinter as tk

from app.gui_resources import StringVar


def short_name(name, limit=38):
    """Fit a file name in ``limit`` characters, keeping its end and extension."""
    if len(name) <= limit:
        return name
    tail = max(8, limit // 3)
    return name[:limit - tail - 1] + '…' + name[-tail:]


class AttachmentBatchCard(tk.Frame):
    def __init__(self, parent, gui, paths):
        super().__init__(parent, bg=gui.PANEL_2, padx=12, pady=10,
                         highlightthickness=1, highlightbackground=gui.LINE)
        self.statuses = {}
        tk.Label(self, text=f'Attachments · {len(paths)} files', bg=gui.PANEL_2, fg=gui.GOLD,
                 font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x')
        for index, path in enumerate(paths[:10]):
            row = tk.Frame(self, bg=gui.PANEL_2)
            row.pack(fill='x', pady=4)
            tk.Label(row, text=short_name(Path(path).name), anchor='w', bg=gui.PANEL_2, fg=gui.WHITE,
                     width=38).pack(side='left', fill='x', expand=True)
            variable = StringVar(self, value='Queued')
            self.statuses[index] = variable
            # tk, not ttk: a themed label painted the theme's black box on this panel.
            tk.Label(row, textvariable=variable, width=22, anchor='w', bg=gui.PANEL_2,
                     fg=gui.MUTED).pack(side='right')
        if len(paths) > 10:
            tk.Label(self, text=f'And {len(paths) - 10} more files', bg=gui.PANEL_2,
                     fg=gui.MUTED).pack(anchor='w')

    def update_file(self, index, text):
        if self.winfo_exists() and index in self.statuses:
            self.statuses[index].set(text)
