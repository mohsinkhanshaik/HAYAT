"""The pieces the main window is assembled from.

Kept apart from `gui.py` because they are widgets rather than wiring: a gauge
knows how to draw a number, a console knows how to colour a log line, and
none of them knows anything about HAYAT. That separation is what lets the
window be rearranged without touching what it displays, and what lets each
piece be tested by handing it a number instead of a runtime.

Everything here reads from measurements the runtime already produces. No panel
invents a value, and a panel with nothing to show says so rather than drawing
a plausible zero -- a dial resting at 0% and a dial that could not be read
look identical, and only one of them is good news.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from app.gui_depth import DepthSurface
from app.logging import log_swallowed


class Gauge(tk.Canvas):
    """A ring showing one percentage, coloured by how alarming it is.

    Drawn rather than imported: a dependency-free arc is a handful of lines,
    and the alternative is a charting library loaded at startup to render four
    circles.
    """

    #: Where a reading stops being comfortable. Below the first it is calm,
    #: between them it is worth noticing, above the second it is the reason
    #: the machine feels slow.
    WARN_AT = 70.0
    ALARM_AT = 88.0

    def __init__(self, parent: tk.Misc, palette: Any, label: str, size: int = 78) -> None:
        super().__init__(parent, width=size, height=size, bg=palette.PANEL,
                         highlightthickness=0, bd=0)
        self.palette = palette
        self.label = label
        self.size = size
        self._value: float | None = None
        self._arc: int | None = None
        thickness = max(5, size // 11)
        box = (thickness, thickness, size - thickness, size - thickness)
        # The unfilled remainder, drawn once and left alone.
        self.create_oval(*box, outline=palette.LINE, width=thickness)
        self._arc = self.create_arc(*box, start=90, extent=0, style="arc",
                                    outline=palette.OLIVE, width=thickness)
        self._name = self.create_text(size / 2, size / 2 - 7, text=label,
                                      fill=palette.MUTED, font=("Segoe UI", 8))
        self._reading = self.create_text(size / 2, size / 2 + 7, text="--",
                                         fill=palette.WHITE, font=("Segoe UI Semibold", 11))

    def colour_for(self, value: float) -> str:
        if value >= self.ALARM_AT:
            return self.palette.ALERT
        if value >= self.WARN_AT:
            return self.palette.GOLD
        return self.palette.OLIVE

    def set(self, value: float | None) -> None:
        """Show a percentage, or `--` when there is nothing to show."""
        self._value = value
        if value is None:
            self.itemconfigure(self._arc, extent=0)
            self.itemconfigure(self._reading, text="--", fill=self.palette.MUTED)
            return
        bounded = max(0.0, min(100.0, float(value)))
        # Negative extent sweeps clockwise from the top, which is the
        # direction every dial anyone has ever read turns.
        self.itemconfigure(self._arc, extent=-359.9 * bounded / 100.0,
                           outline=self.colour_for(bounded))
        self.itemconfigure(self._reading, text=f"{bounded:.0f}%", fill=self.palette.WHITE)

    @property
    def value(self) -> float | None:
        return self._value


class InfoCard(tk.Frame):
    """A small labelled fact: what it is, then one or two lines about it."""

    def __init__(self, parent: tk.Misc, palette: Any, title: str) -> None:
        super().__init__(parent, bg=palette.PANEL_2, padx=10, pady=6,
                         relief="flat", bd=0, highlightthickness=1, highlightbackground=palette.LINE)
        self.palette = palette
        tk.Label(self, text=title, bg=palette.PANEL_2, fg=palette.MUTED,
                 font=("Segoe UI Semibold", 8), anchor="w").pack(fill="x")
        # Bound to this widget, not to tkinter's default root. Importing
        # tkinterdnd2 creates a root of its own, and a StringVar that
        # attached to that one is simply never displayed by a label
        # living on the window -- every value silently blank.
        self.primary = StringVar(master=self, value="--")
        self.secondary = StringVar(master=self, value="")
        tk.Label(self, textvariable=self.primary, bg=palette.PANEL_2, fg=palette.WHITE,
                 font=("Segoe UI", 9), anchor="w", justify="left").pack(fill="x", pady=(3, 0))
        tk.Label(self, textvariable=self.secondary, bg=palette.PANEL_2, fg=palette.MUTED,
                 font=("Segoe UI", 8), anchor="w", justify="left").pack(fill="x")

    def set(self, primary: str, secondary: str = "") -> None:
        self.primary.set_if_changed(primary or "--")
        self.secondary.set_if_changed(secondary)


class LiveConsole(tk.Frame):
    """Timestamped lines from the runtime, coloured by which part spoke.

    The colour carries the source so a stream of forty lines can be scanned
    for the one that matters without reading all forty.
    """

    MAX_LINES = 400

    def __init__(self, parent: tk.Misc, palette: Any, height: int = 9) -> None:
        super().__init__(parent, bg=palette.PANEL)
        self.palette = palette
        self.text = tk.Text(self, height=height, bg=palette.BLACK, fg=palette.MUTED,
                            relief="flat", padx=10, pady=8, font=("Cascadia Mono", 8),
                            state="disabled", wrap="word",
                            selectbackground=palette.ROYAL, selectforeground=palette.WHITE)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("time", foreground=palette.OLIVE)
        self.text.tag_configure("source", foreground=palette.ROYAL_SOFT)
        self.text.tag_configure("body", foreground=palette.MUTED)
        self.text.tag_configure("alert", foreground=palette.ALERT)
        self.text.tag_configure("gold", foreground=palette.GOLD_SOFT)
        #: The newest line without its time, for the folded panel's header.
        self.last_line = ""

    def write(self, source: str, message: str, *, level: str = "") -> None:
        follow = self.text.yview()[1] >= .98
        stamp = time.strftime("%H:%M:%S")
        body = "alert" if level == "error" else "gold" if level == "notable" else "body"
        self.text.configure(state="normal")
        self.text.insert("end", f"{stamp} ", "time")
        self.text.insert("end", f"{str(source)[:10]:<10} ", "source")
        self.text.insert("end", " ".join(str(message).split())[:200] + "\n", body)
        self.last_line = f"{source}: " + " ".join(str(message).split())[:80]
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > self.MAX_LINES:
            self.text.delete("1.0", f"{lines - self.MAX_LINES + 1}.0")
        self.text.configure(state="disabled")
        if follow:
            self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class ModelChips(tk.Frame):
    """The models actually reachable right now, one chip each.

    Reachable, not configured: a model listed in a config file that no service
    is serving is exactly the kind of thing this window exists to stop HAYAT
    claiming.
    """

    def __init__(self, parent: tk.Misc, palette: Any, *, rows: bool = False, limit: int | None = None) -> None:
        super().__init__(parent, bg=palette.PANEL)
        self.palette = palette
        self._shown: tuple[str, ...] = ()
        self._active = ""
        self._loaded = False
        self.rows = rows
        self.limit = limit
        self.empty = tk.Label(self, text="Checking local model services…", bg=palette.PANEL,
                              fg=palette.MUTED, font=("Segoe UI", 9), anchor="w")
        self.empty.pack(fill="x")

    @staticmethod
    def _rollback(full: str) -> bool:
        """A saved snapshot such as hayat:rollback-ff12d890ba32, kept only to undo a model change."""
        return ":rollback-" in full

    def set_models(self, models: dict[str, list[str]], active: str = "") -> None:
        flat = tuple(f"{provider}/{name}" for provider, names in sorted(models.items())
                     for name in names)
        # Real models before rollback snapshots: two hash-named backups used
        # to fill the panel ahead of hayat:latest. The order is otherwise kept,
        # so choosing another model never makes the list jump.
        order = {full: index for index, full in enumerate(flat)}
        flat = tuple(sorted(flat, key=lambda full: (self._rollback(full), order[full])))
        if self._loaded and flat == self._shown and active == self._active:
            return  # nothing changed; rebuilding would flicker
        self._shown = flat
        self._loaded = True
        self._active = active
        for child in self.winfo_children():
            child.destroy()
        if not flat:
            tk.Label(self, text="No model service is reachable.", bg=self.palette.PANEL,
                     fg=self.palette.MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x")
            return

        row = tk.Frame(self, bg=self.palette.PANEL)
        row.pack(fill="x")
        displayed = flat[:self.limit] if self.limit else flat
        for index, full in enumerate(displayed):
            if index and (self.rows or index % 3 == 0):
                row = tk.Frame(self, bg=self.palette.PANEL)
                row.pack(fill="x", pady=(5, 0))
            provider, _, name = full.partition("/")
            normalized = lambda value: value if ":" in value else value + ":latest"
            in_use = bool(active) and normalized(name) == normalized(str(active))
            chip = tk.Frame(row, bg=self.palette.PANEL_2, padx=9, pady=6)
            chip.pack(side="left", padx=(0, 6), fill="x", expand=self.rows)
            label = (name if len(name) <= 31 else name[:28] + "…") if self.rows else name.split(":")[0][:16]
            tk.Label(chip, text=label, bg=self.palette.PANEL_2,
                     fg=self.palette.GOLD if in_use else self.palette.WHITE,
                     wraplength=290 if self.rows else 0, anchor="w", justify="left",
                     font=("Segoe UI Semibold", 9)).pack(fill="x" if self.rows else "none")
            status = ("Selected · " + provider if self.rows else "in use") if in_use else provider
            tk.Label(chip, text=status, bg=self.palette.PANEL_2,
                     fg=self.palette.GOLD_SOFT if in_use else self.palette.MUTED,
                     font=("Segoe UI", 7)).pack()
        if len(displayed) < len(flat):
            hidden = flat[len(displayed):]
            snapshots = sum(1 for full in hidden if self._rollback(full))
            extra = f" ({snapshots} rollback snapshot{'s' if snapshots != 1 else ''})" if snapshots else ""
            tk.Label(self, text=f"+ {len(hidden)} more{extra} · Full names in Manage models",
                     bg=self.palette.PANEL, fg=self.palette.MUTED, font=("Segoe UI", 8),
                     anchor="w").pack(fill="x", pady=(6, 0))

    @property
    def shown(self) -> tuple[str, ...]:
        return self._shown


class NavItem(tk.Frame):
    """One row in the sidebar: an icon, a name, and what it is for."""

    def __init__(self, parent: tk.Misc, palette: Any, key: str, icon: str, title: str,
                 subtitle: str, on_click: Callable[[str], None]) -> None:
        super().__init__(parent, bg=palette.BLACK, padx=0, pady=0,
                         cursor="hand2", relief="flat", bd=0)
        self.palette = palette
        self.key = key
        self._selected = False
        from app.gui_theme import ICONS
        icon = ICONS.get(key, icon)
        self.configure(takefocus=True, highlightthickness=1, highlightbackground=palette.BLACK,
                       highlightcolor=palette.GOLD)
        self.bind("<Return>", lambda event: on_click(self.key))
        self.bind("<space>", lambda event: on_click(self.key))

        # A gold bar down the left edge marks the open view. It is always
        # present so selecting does not shift the text sideways by 3px.
        self.marker = tk.Frame(self, bg=palette.BLACK, width=3)
        self.marker.pack(side="left", fill="y")
        self.body = tk.Frame(self, bg=palette.BLACK, padx=9, pady=8)
        self.body.pack(side="left", fill="both", expand=True)
        self.icon = tk.Label(self.body, text=icon, bg=palette.BLACK, fg=palette.MUTED,
                             font=("Segoe UI Symbol", 13), width=2)
        self.icon.pack(side="left", padx=(0, 9))
        from app.gui_icons import icon_image, icon_kind
        kinds = {'background': 'refresh', 'tasks': 'task', 'task_graph': 'brain',
                 'memory': 'model', 'learning': 'book', 'training': 'book',
                 'health': 'check', 'improve': 'refresh', 'toolbox': 'grid', 'files': 'file'}
        self.icon._hayat_icon_kind = kinds.get(key, icon_kind(title))
        self.icon.configure(text='', width=0, image=icon_image(self, self.icon._hayat_icon_kind, palette.MUTED, 20))
        words = tk.Frame(self.body, bg=palette.BLACK)
        words.pack(side="left", fill="x", expand=True)
        self.title = tk.Label(words, text=title, bg=palette.BLACK, fg=palette.WHITE,
                              font=("Segoe UI Semibold", 9), anchor="w", justify="left")
        self.title.pack(fill="x")
        self.subtitle = tk.Label(words, text=subtitle, bg=palette.BLACK, fg=palette.MUTED,
                                 font=("Segoe UI", 8), anchor="w", justify="left", wraplength=130)
        self.subtitle.pack(fill="x")
        def fit_words(event):
            width = max(40, event.width - 2)
            self.title.configure(wraplength=width)
            self.subtitle.configure(wraplength=width)
        words.bind("<Configure>", fit_words)

        for widget in (self, self.body, self.icon, words, self.title, self.subtitle):
            widget.bind("<Button-1>", lambda _event: on_click(self.key))
            widget.bind("<Enter>", lambda _event: self._hover(True))
            widget.bind("<Leave>", lambda _event: self._hover(False))

    def _paint(self, background: str) -> None:
        for widget in (self, self.body, self.icon, self.title, self.subtitle):
            widget.configure(bg=background)
        for child in self.body.winfo_children():
            if isinstance(child, tk.Frame):
                child.configure(bg=background)

    def _hover(self, entering: bool) -> None:
        if self._selected:
            return
        self._paint(self.palette.PANEL if entering else self.palette.BLACK)
        self.configure(highlightbackground=self.palette.LINE if entering else self.palette.BLACK)

    def select(self, selected: bool) -> None:
        self._selected = selected
        self._paint(self.palette.PANEL_2 if selected else self.palette.BLACK)
        self.configure(highlightbackground=self.palette.LINE if selected else self.palette.BLACK)
        self.marker.configure(bg=self.palette.GOLD if selected else self.palette.BLACK)
        self.icon.configure(fg=self.palette.GOLD if selected else self.palette.MUTED)
        from app.gui_icons import icon_image
        self.icon.configure(image=icon_image(self, self.icon._hayat_icon_kind,
                                            self.palette.GOLD if selected else self.palette.MUTED, 20))
        self.title.configure(fg=self.palette.GOLD if selected else self.palette.WHITE)

    @property
    def selected(self) -> bool:
        return self._selected


class StatusPill(tk.Frame):
    """One fact on the bottom bar: a dot, a name, and its current value."""

    def __init__(self, parent: tk.Misc, palette: Any, name: str) -> None:
        super().__init__(parent, bg=palette.PANEL, padx=12, pady=5,
                         relief="flat", bd=0)
        self.palette = palette
        self.dot = tk.Label(self, text="●", bg=palette.PANEL, fg=palette.MUTED,
                            font=("Segoe UI", 8))
        self.dot.pack(side="left", padx=(0, 6))
        self.value = StringVar(master=self, value=f"{name}: --")
        tk.Label(self, textvariable=self.value, bg=palette.PANEL, fg=palette.WHITE,
                 font=("Segoe UI", 9)).pack(side="left")
        self.name = name

    def set(self, value: str, live: bool | None = True) -> None:
        self.value.set_if_changed(f"{self.name}: {value}")
        colour = {True: self.palette.OLIVE, False: self.palette.MUTED,
                  None: self.palette.GOLD}[live]
        if self.dot.cget("fg") != colour:
            self.dot.configure(fg=colour)


class ScrollableColumn(tk.Frame):
    """A fixed-width column whose contents can be taller than the window.

    The right-hand stack -- camera, sensors, dials, console, models -- adds up
    to more height than the window has, and which parts got cut depended on
    pack order rather than on anything meaningful. Two of them simply never
    appeared. Sections are added to `.body` and the column scrolls when it has
    to, which also means the window can be dragged small without anything
    silently disappearing.
    """

    def __init__(self, parent: tk.Misc, palette: Any, width: int = 430) -> None:
        super().__init__(parent, bg=palette.BLACK, width=width)
        self.palette = palette
        self.pack_propagate(False)
        self.canvas = tk.Canvas(self, bg=palette.BLACK, highlightthickness=0, bd=0)
        # A themed bar: the classic Tk scrollbar ignores these colours on
        # Windows and drew a bright white strip down the side of the rail.
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                       style="HayatThin.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=palette.BLACK)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._resized)
        self.canvas.bind("<Configure>", self._canvas_resized)
        # A canvas window can remain internally hidden after an ancestor is
        # unmapped (Focus Mode, minimise/restore, test withdrawal) even though
        # the canvas itself maps again. Restore the embedded body after every
        # map so the rail never returns as an empty scrollbar.
        self.bind("<Map>", self._mapped, add="+")
        self.canvas.bind("<Map>", self._mapped, add="+")
        # Bound on the column rather than globally: a wheel event over the
        # transcript should scroll the transcript.
        from app.gui_motion import attach
        self._smooth = attach(self.canvas)

    def _resized(self, _event: Any = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_resized(self, event: Any) -> None:
        # Keep the inner frame exactly as wide as the visible canvas, so
        # sections fill the column instead of sitting at their natural width.
        self.canvas.itemconfigure(self._window, width=event.width)

    def _mapped(self, _event: Any = None) -> None:
        try:
            self.canvas.itemconfigure(self._window, state="normal")
            self.after_idle(lambda: self.canvas.itemconfigure(self._window, state="normal"))
        except tk.TclError:
            pass

    def _wheel(self, event: Any) -> str:
        if self.body.winfo_height() > self.canvas.winfo_height():
            self._smooth.wheel(event)
        return "break"

    def adopt(self, widget: tk.Misc) -> None:
        """Make a newly added section scroll with the wheel too."""
        if getattr(widget, "_owns_mousewheel", False) or isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview, tk.Entry, ttk.Entry,
                                                                         ttk.Combobox, tk.Scale, ttk.Scale, tk.Spinbox, ttk.Spinbox)):
            return
        # Preserve controls that already handle the wheel (sliders, canvases).
        if not widget.bind("<MouseWheel>"):
            widget.bind("<MouseWheel>", self._wheel)
            widget.bind("<Button-4>", self._wheel)
            widget.bind("<Button-5>", self._wheel)
        for child in widget.winfo_children():
            self.adopt(child)


class ToolTile(tk.Frame):
    """One tool in the toolbox: an icon, a name, what it does, and a ＋.

    Pressing the tile runs the tool. Pressing the ＋ pins it to the workspace,
    which is the point of having a toolbox at all -- the twenty things HAYAT
    can do are always there, and the four Mohsin is using today are in front
    of him.
    """

    def __init__(self, parent: tk.Misc, palette: Any, key: str, icon: str, title: str,
                 subtitle: str, on_run: Callable[[str], None],
                 on_pin: Callable[[str], None] | None = None) -> None:
        super().__init__(parent, bg=palette.PANEL_2, padx=10, pady=9,
                         cursor="hand2", relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=palette.LINE)
        self.palette = palette
        self.key = key

        self.icon = tk.Label(self, text=icon, bg=palette.PANEL_2, fg=palette.GOLD,
                             font=("Segoe UI Emoji", 13), width=2)
        self.icon.pack(side="left", padx=(0, 9))
        words = tk.Frame(self, bg=palette.PANEL_2)
        words.pack(side="left", fill="x", expand=True)
        self.title = tk.Label(words, text=title, bg=palette.PANEL_2, fg=palette.WHITE,
                              font=("Segoe UI Semibold", 9), anchor="w")
        self.title.pack(fill="x")
        self.subtitle = tk.Label(words, text=subtitle, bg=palette.PANEL_2, fg=palette.MUTED,
                                 font=("Segoe UI", 8), anchor="w")
        self.subtitle.pack(fill="x")

        if on_pin is not None:
            self.pin = tk.Label(self, text="＋", bg=palette.PANEL_2, fg=palette.MUTED,
                                font=("Segoe UI", 11), cursor="hand2")
            self.pin.pack(side="right")
            self.pin.bind("<Button-1>", lambda event: (on_pin(self.key), "break")[1])
            self.pin.bind("<Enter>", lambda _e: self.pin.configure(fg=palette.GOLD))
            self.pin.bind("<Leave>", lambda _e: self.pin.configure(fg=palette.MUTED))

        for widget in (self, words, self.icon, self.title, self.subtitle):
            widget.bind("<Button-1>", lambda _event: on_run(self.key))
            widget.bind("<Enter>", lambda _event: self._hover(True))
            widget.bind("<Leave>", lambda _event: self._hover(False))

    def _hover(self, entering: bool) -> None:
        colour = getattr(self.palette, "PANEL_HOVER", "#20242C") if entering else self.palette.PANEL_2
        self.configure(highlightbackground=self.palette.GOLD if entering else self.palette.LINE)
        for widget in (self, self.icon, self.title, self.subtitle):
            widget.configure(bg=colour)
        for child in self.winfo_children():
            if isinstance(child, tk.Frame):
                child.configure(bg=colour)


class Workspace(tk.Frame):
    """The tools pinned for right now, and a place to drop more.

    Empty by default and says so. A workspace that starts full is somebody
    else's idea of what matters.
    """

    def __init__(self, parent: tk.Misc, palette: Any, on_run: Callable[[str], None],
                 on_remove: Callable[[str], None]) -> None:
        super().__init__(parent, bg=palette.BLACK)
        self.palette = palette
        self.on_run = on_run
        self.on_remove = on_remove
        self.pinned: list[tuple[str, str, str]] = []
        self.body = tk.Frame(self, bg=palette.BLACK)
        self.body.pack(fill="both", expand=True)
        self._render()

    def set_pinned(self, items: list[tuple[str, str, str]]) -> None:
        self.pinned = list(items)
        self._render()

    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        if not self.pinned:
            tk.Label(self.body, bg=self.palette.BLACK, fg=self.palette.MUTED,
                     font=("Segoe UI", 9), pady=14, justify="center",
                     text="Nothing pinned yet.\nPress ＋ on a tool to keep it here.",
                     ).pack(fill="both", expand=True)
            return
        for index, (key, icon, title) in enumerate(self.pinned):
            chip = tk.Frame(self.body, bg=self.palette.PANEL_2, padx=9, pady=6,
                            cursor="hand2", relief="raised", bd=2)
            chip.grid(row=index // 4, column=index % 4, sticky="ew", padx=3, pady=3)
            label = tk.Label(chip, text=f"{icon}  {title}", bg=self.palette.PANEL_2,
                             fg=self.palette.WHITE, font=("Segoe UI", 9))
            label.pack(side="left")
            remove = tk.Label(chip, text="✕", bg=self.palette.PANEL_2, fg=self.palette.MUTED,
                              font=("Segoe UI", 8), cursor="hand2")
            remove.pack(side="left", padx=(8, 0))
            remove.bind("<Button-1>", lambda _e, k=key: (self.on_remove(k), "break")[1])
            for widget in (chip, label):
                widget.bind("<Button-1>", lambda _e, k=key: self.on_run(k))
        for column in range(4):
            self.body.grid_columnconfigure(column, weight=1, uniform="pinned")


class ButtonRow(tk.Frame):
    """Buttons sharing a row equally, wrapping onto more rows when they do not fit.

    Packed side by side, buttons that ask for more than the row's width are cut from the right: the right
    column showed "Anywh", "Discor", "Perfor", and Adaptive Max not at all. Here every label stays whole.
    """

    def __init__(self, parent: tk.Misc, palette: Any) -> None:
        super().__init__(parent, bg=palette.PANEL)
        self.buttons: list[ttk.Button] = []
        self._columns = 0
        self.bind("<Configure>", self._layout)

    def add(self, text: str, command: Callable[[], Any], style: str = "Hayat.TButton") -> ttk.Button:
        button = ttk.Button(self, text=text, style=style, command=command)
        self.buttons.append(button)
        self._columns = 0
        self._layout()
        return button

    def _layout(self, _event: Any = None) -> None:
        if not self.buttons:
            return
        width = self.winfo_width()
        widest = max(button.winfo_reqwidth() for button in self.buttons) + 2
        columns = len(self.buttons) if width <= 1 else max(1, min(len(self.buttons), width // widest))
        if columns == self._columns:
            return
        for column in range(max(self._columns, len(self.buttons))):
            self.grid_columnconfigure(column, weight=0, uniform="")
        self._columns = columns
        for index, button in enumerate(self.buttons):
            # The last button of a short final row takes the rest of it, rather than leaving a hole beside it.
            span = columns - index % columns if index == len(self.buttons) - 1 else 1
            button.grid(row=index // columns, column=index % columns, columnspan=span, sticky="ew", padx=1, pady=1)
        for column in range(columns):
            self.grid_columnconfigure(column, weight=1, uniform="buttons")


class FoldMemory:
    """Which titled panels the owner folded, kept in a small JSON file so the window opens the way he left it.

    The main window's right column holds fourteen panels; folding the ones he does not watch brings the ones he
    does (the radar, Wi-Fi sensing) up into view. ``defaults`` are folded until he opens them once.
    """

    def __init__(self, path: Any, defaults: tuple[str, ...] = ()) -> None:
        self.path = Path(path)
        self.defaults = frozenset(defaults)
        self.state: dict[str, bool] = {}
        #: title -> the function that shows a panel folded or open, for panels built with this memory.
        self.panels: dict[str, Callable[[bool], None]] = {}
        #: title -> the function that sets the one line a folded panel shows beside its title, and that line.
        self.summaries: dict[str, Callable[[str], None]] = {}
        self.peeks: dict[str, tk.Label] = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.state = {str(key): bool(value) for key, value in loaded.items()}
        except (OSError, ValueError):
            pass

    def folded(self, key: str) -> bool:
        return self.state.get(key, key in self.defaults)

    def summarise(self, title: str, text: str) -> None:
        """What a folded panel says beside its title ("CPU 12% · RAM 58%"); shown only while it is folded."""
        if title in self.summaries:
            self.summaries[title](text)

    def unfold_all(self) -> None:
        for title, show in self.panels.items():
            self.set(title, False)
            show(False)

    def fold_all(self) -> None:
        for title, show in self.panels.items():
            self.set(title, True)
            show(True)

    def set(self, key: str, folded: bool) -> None:
        self.state[key] = bool(folded)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state, indent=1, sort_keys=True), encoding="utf-8")
        except OSError:
            # A fold that is not remembered is still applied; nothing else depends on the file.
            log_swallowed(logging.getLogger(__name__), "saving which panels are folded")


def section(parent: tk.Misc, palette: Any, title: str,
            action: tuple[str, Callable[[], None]] | None = None, fold: FoldMemory | None = None) -> DepthSurface:
    """A titled panel, returning the frame its contents belong in. With ``fold``, clicking the title folds it."""
    outer = DepthSurface(parent, palette, padding=4)
    header = tk.Frame(outer.content, bg=palette.PANEL, padx=12, pady=9)
    header.pack(fill="x")
    chevron = None
    if fold is not None:
        chevron = tk.Label(header, text="▼", bg=palette.PANEL, fg=palette.MUTED, font=("Segoe UI Symbol", 8), cursor="hand2")
        chevron.pack(side="left", padx=(0, 6))
    heading = tk.Label(header, text=title, bg=palette.PANEL, fg=palette.WHITE, font=("Segoe UI Semibold", 10))
    heading.pack(side="left")
    # Folded, a panel still says the one thing it is for ("CPU 12% · RAM 58%"), so folding hides detail, not news.
    # It has a line of its own under the title: beside it, a long title, a header button or larger text cut it off.
    peek = tk.Label(outer.content, text="", bg=palette.PANEL, fg=palette.MUTED, font=("Segoe UI", 9), anchor="w",
                    justify="left")
    outer.content.bind("<Configure>", lambda event: peek.configure(wraplength=max(80, event.width - 48)), add="+")
    if action is not None:
        label, command = action
        tk.Button(header, text=label, command=command, bg=palette.PANEL_2,
                  fg=palette.MUTED, relief="flat", bd=0, padx=9, pady=1,
                  activebackground=palette.ROYAL, activeforeground=palette.WHITE,
                  font=("Segoe UI", 8), cursor="hand2").pack(side="right")
    body = tk.Frame(outer.content, bg=palette.PANEL, padx=12)
    body.pack(fill="both", expand=True, pady=(0, 12))
    outer.body = body  # type: ignore[attr-defined]
    if fold is not None and chevron is not None:
        def show(folded: bool) -> None:
            chevron.configure(text="▶" if folded else "▼")
            if folded:
                body.pack_forget()
            elif not body.winfo_manager():
                body.pack(fill="both", expand=True, pady=(0, 12))
            if folded and peek.cget("text"):
                if not peek.winfo_manager():
                    peek.pack(fill="x", padx=(32, 12), pady=(0, 9), after=header)
            elif peek.winfo_manager():
                peek.pack_forget()

        def summarise(text: str) -> None:
            text = " ".join(str(text or "").split())
            text = text if len(text) <= 46 else text[:45].rstrip(" ·,") + "…"
            if text != peek.cget("text"):
                peek.configure(text=text)
                show(fold.folded(title))

        def toggle(_event: Any = None) -> str:
            folded = not fold.folded(title)
            fold.set(title, folded)
            show(folded)
            return "break"

        for widget in (header, heading, chevron, peek):
            widget.bind("<Button-1>", toggle)
        # The keyboard folds it too: Tab reaches the title, Enter or Space toggles it.
        heading.configure(cursor="hand2", takefocus=1, highlightthickness=1, highlightbackground=palette.PANEL,
                          highlightcolor=palette.GOLD)
        heading.bind("<Return>", toggle)
        heading.bind("<space>", toggle)
        peek.configure(cursor="hand2")
        fold.panels[title] = show
        fold.summaries[title] = summarise
        fold.peeks[title] = peek
        outer.fold_toggle = toggle  # type: ignore[attr-defined]
        outer.is_folded = lambda: fold.folded(title)  # type: ignore[attr-defined]
        show(fold.folded(title))
    return outer


def human_bytes(value: float | None) -> str:
    """Bytes as something a person reads, or `--` when unknown."""
    if value is None:
        return "--"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in {"B", "KB", "MB"} else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_duration(seconds: float) -> str:
    """Uptime, at the precision a person cares about."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
