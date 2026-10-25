"""What an empty table says: a centred line of guidance over a list with no rows, gone as soon as rows arrive.

An empty table on first open used to show only its column headings, which reads as broken. Each window says
what will appear there and how to start, in its own words.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any


def _wrap_width(width: int) -> int:
    """Wrap inside the table, leaving room for the label's padding; 560 before the table has a size."""
    return 560 if width <= 1 else max(120, min(560, width - 40))


def show_empty(tree: Any, text: str, palette: Any) -> None:
    """Show `text` over `tree` while it has no rows; hide it otherwise. Call after every refresh."""
    label = getattr(tree, "_hayat_empty", None)
    if label is None:
        label = tk.Label(tree, bg=palette.PANEL_2, fg=palette.MUTED, font=("Segoe UI", 10), justify="center",
                         wraplength=_wrap_width(tree.winfo_width()), padx=12, pady=8)
        tree._hayat_empty = label
        # A fixed 560-pixel wrap cut the guidance off at both edges of a
        # narrow list (Private networking's contacts are about 345 wide).
        tree.bind("<Configure>", lambda event: label.configure(wraplength=_wrap_width(event.width)), add="+")
    if tree.get_children():
        label.place_forget()
        return
    label.configure(text=text)
    label.place(relx=.5, rely=.5, anchor="center")
