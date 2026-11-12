"""Keyboard reach for things drawn as labels or frames but used as buttons.

A label that runs something on a click (the wallet badge, a recent chat, a rating, a
sample phrase) was reachable only with the mouse: Tab skipped it and Enter did nothing.
`activatable` gives it what a ttk button has: a place in the Tab order, a visible focus
ring in the accent colour, Enter/Space to run it, and the hand pointer.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any, Callable


def activatable(widget: tk.Misc, command: Callable[[], Any], palette: Any, *,
                ring: int = 1, keys_only: bool = False) -> tk.Misc:
    """Make `widget` focusable and runnable from the keyboard; returns it.

    The ring is drawn in the widget's own background until it has focus, so the
    layout does not move when focus arrives. `keys_only` leaves an existing
    click binding alone (the widget already handles the mouse its own way).
    """
    def run(_event: Any = None) -> str:
        command()
        return "break"

    try:
        background = widget.cget("bg")
    except tk.TclError:
        background = palette.BLACK
    try:
        widget.configure(takefocus=1, cursor="hand2", highlightthickness=ring,
                         highlightbackground=background, highlightcolor=palette.GOLD)
    except tk.TclError:  # a widget without a highlight ring still joins the Tab order
        widget.configure(takefocus=1)
    for sequence in ("<Return>", "<KP_Enter>", "<space>"):
        widget.bind(sequence, run)
    if not keys_only:
        widget.bind("<Button-1>", run)
    widget._hayat_activate = run  # type: ignore[attr-defined]
    return widget
