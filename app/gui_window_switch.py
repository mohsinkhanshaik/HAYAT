"""A quick switcher between HAYAT's open windows: press the shortcut, type a few letters, Enter to raise.

HAYAT opens many Studio windows (Reverse engineering, Trade Center, Jobs, Accounts, the 3D graph…). This
lists the ones currently open, filters as you type, and raises and focuses the chosen one — so you jump to a
window instead of hunting the taskbar. It only ever shows and raises existing windows; it opens nothing and
closes nothing.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any


def open_windows(root: tk.Misc, exclude: tk.Misc | None = None) -> list[tuple[tk.Toplevel, str]]:
    """Every currently-visible Toplevel under root (except ``exclude``), as (window, title), ordered by title.

    A withdrawn or iconified window is skipped: the switcher jumps between what is actually on screen."""
    found: list[tuple[tk.Toplevel, str]] = []
    for child in root.winfo_children():
        if not isinstance(child, tk.Toplevel) or child is exclude:
            continue
        try:
            if child.state() not in ("normal", "zoomed"):
                continue
            title = child.title() or child.winfo_name()
        except tk.TclError:
            continue  # a window torn down mid-enumeration
        found.append((child, title))
    return sorted(found, key=lambda item: item[1].casefold())


def _raise(window: tk.Toplevel) -> None:
    try:
        window.deiconify()
        window.lift()
        window.focus_force()
    except tk.TclError:
        pass


class WindowSwitch(tk.Toplevel):
    """A small centred overlay listing open windows; type to filter, Up/Down to move, Enter to raise, Esc to close."""

    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui = gui
        self._windows = open_windows(gui.root, exclude=self)
        self.overrideredirect(True)
        self.configure(bg=gui.GOLD if hasattr(gui, "GOLD") else "#D4AF37", padx=1, pady=1)
        inner = tk.Frame(self, bg=gui.BLACK, padx=10, pady=10)
        inner.pack(fill="both", expand=True)
        tk.Label(inner, text="Switch to window", bg=gui.BLACK, fg=gui.GOLD, anchor="w").pack(fill="x")
        self.query = tk.StringVar(self, "")
        entry = tk.Entry(inner, textvariable=self.query, width=44, bg=gui.PANEL if hasattr(gui, "PANEL") else "#1b1f24",
                         fg=gui.WHITE, insertbackground=gui.WHITE, relief="flat")
        entry.pack(fill="x", pady=(6, 6))
        self.listbox = tk.Listbox(inner, height=8, width=44, activestyle="none",
                                  bg=gui.PANEL if hasattr(gui, "PANEL") else "#1b1f24", fg=gui.WHITE,
                                  selectbackground=gui.GOLD, selectforeground=gui.BLACK, relief="flat",
                                  highlightthickness=0)
        self.listbox.pack(fill="both", expand=True)
        self.empty = tk.Label(inner, text="", bg=gui.BLACK, fg=gui.WHITE, anchor="w")
        self.query.trace_add("write", lambda *_a: self._refill())
        entry.bind("<Down>", lambda _e: (self._move(1), "break")[1])
        entry.bind("<Up>", lambda _e: (self._move(-1), "break")[1])
        entry.bind("<Return>", lambda _e: (self._choose(), "break")[1])
        self.listbox.bind("<Return>", lambda _e: (self._choose(), "break")[1])
        self.listbox.bind("<Double-Button-1>", lambda _e: (self._choose(), "break")[1])
        for widget in (self, entry, self.listbox):
            widget.bind("<Escape>", lambda _e: (self.close(), "break")[1])
        self.bind("<FocusOut>", lambda _e: self.close())
        self._refill()
        self._centre()
        self.deiconify()
        entry.focus_set()

    def _centre(self) -> None:
        self.update_idletasks()
        root = self.gui.root
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _matches(self) -> list[tuple[tk.Toplevel, str]]:
        needle = self.query.get().strip().casefold()
        return [item for item in self._windows if needle in item[1].casefold()] if needle else list(self._windows)

    def _refill(self) -> None:
        self.listbox.delete(0, "end")
        matches = self._matches()
        for _window, title in matches:
            self.listbox.insert("end", title)
        if matches:
            self.empty.pack_forget()
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(0)
            self.listbox.activate(0)
        else:
            self.empty.configure(text="No other open windows." if not self._windows else "No window matches.")
            self.empty.pack(fill="x")

    def _move(self, delta: int) -> None:
        size = self.listbox.size()
        if not size:
            return
        current = self.listbox.curselection()
        index = ((current[0] if current else 0) + delta) % size
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.activate(index)

    def _choose(self) -> None:
        matches = self._matches()
        selection = self.listbox.curselection()
        index = selection[0] if selection else 0
        if 0 <= index < len(matches):
            window = matches[index][0]
            self.close()
            _raise(window)

    def close(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass
