"""The deep look, drawn: a situation gauge, the numbers behind it, and one card per finding.

Each card shows its level, its evidence (folded), how sure HAYAT is, and what it is about:
an event opens on the map, a camera plays, a point centres the map. Blind spots are cards
too, dimmed, because what HAYAT could not see belongs in the picture.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

LEVELS = {"alert": ("ALERT", "#F05D5E"), "watch": ("WATCH", "#E8B04B"), "info": ("INFO", "#6CB6FF"), "gap": ("BLIND SPOT", "#7D8A96")}
SITUATION = {"calm": ("CALM", "#57C785", 0.17), "watch": ("WATCH", "#E8B04B", 0.5), "alert": ("ALERT", "#F05D5E", 0.83)}
FILTERS = (("all", "All"), ("alert", "Alerts"), ("watch", "Watch"), ("info", "Information"), ("gap", "Blind spots"))
CHIP_ICONS = {"all": "grid", "alert": "alert", "watch": "eye", "info": "book", "gap": "search"}
#: Below this width the side column becomes a strip above the cards (gauge and counts only).
COMPACT_WIDTH = 820
METRICS = (("temperature_c", "Temperature", "°C"), ("wind_ms", "Wind", " m/s"), ("precipitation_next_hour_mm", "Rain next hour", " mm"),
           ("pm2_5", "PM2.5", " µg/m³"), ("events", "Events", ""), ("road_closures", "Road closures", ""),
           ("cameras", "Cameras", ""), ("aircraft", "Aircraft", ""))


class DeepLookView(tk.Frame):
    """`actions` maps 'event', 'camera', 'point', 'refresh', 'reason' and 'atlas' to callables."""

    def __init__(self, parent: Any, palette: Any, actions: dict[str, Callable[..., Any]]) -> None:
        super().__init__(parent, bg=palette.BLACK)
        self.p, self.actions = palette, actions
        self.value: dict[str, Any] | None = None
        self._rewrap_job = None
        self._evidence: dict[int, tuple[tk.Frame, tk.Label, int]] = {}
        self.filter = "all"
        self.expanded: set[int] = set()
        self.cards: list[tk.Frame] = []
        p = palette
        self.compact = False
        side = self.side = tk.Frame(self, bg=p.PANEL, width=290, padx=12, pady=10)
        side.pack(side="left", fill="y", padx=(0, 8))
        side.pack_propagate(False)
        self.place_label = tk.Label(side, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 11), wraplength=260, justify="left", anchor="w")
        self.place_label.pack(fill="x")
        self.time_label = tk.Label(side, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w")
        self.time_label.pack(fill="x")
        self.gauge = tk.Canvas(side, width=260, height=150, bg=p.PANEL, highlightthickness=0)
        self.gauge.pack(pady=(6, 2))
        self.counts = tk.Frame(side, bg=p.PANEL)
        self.counts.pack(fill="x", pady=4)
        self.metrics = tk.Frame(side, bg=p.PANEL)
        self.metrics.pack(fill="x", pady=4)
        buttons = self.side_buttons = tk.Frame(side, bg=p.PANEL)
        buttons.pack(side="bottom", fill="x")
        for label, key, icon in (("HAYAT's reading", "reason", "brain"), ("Look again", "refresh", "refresh"),
                                 ("Open in atlas", "atlas", "globe")):
            button = ttk.Button(buttons, text=label, style="Compact.Hayat.TButton", command=lambda key=key: self._act(key))
            button._hayat_icon = icon
            button.pack(fill="x", pady=2)
        main = self.main = tk.Frame(self, bg=p.BLACK)
        main.pack(side="left", fill="both", expand=True)
        from app.gui_intelligence_parts import FlowRow
        chips = FlowRow(main, p.BLACK, gap=6, height=30)
        chips.pack(fill="x", pady=(0, 6))
        self.chip_buttons: dict[str, tk.Button] = {}
        for key, label in FILTERS:
            chip = chips.add(tk.Button(chips, text=label, relief="flat", bd=0, padx=10, pady=3, cursor="hand2", font=("Segoe UI", 9),
                                       command=lambda key=key: self.set_filter(key)))
            chip._hayat_icon = CHIP_ICONS[key]
            self.chip_buttons[key] = chip
        holder = tk.Frame(main, bg=p.BLACK)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=p.BLACK, highlightthickness=0)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list = tk.Frame(self.canvas, bg=p.BLACK)
        self._window = self.canvas.create_window(0, 0, window=self.list, anchor="nw")
        self.list.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize)
        from app.gui_motion import attach
        self._smooth = attach(self.canvas)
        self.bind("<Configure>", self._layout, add="+")
        self.bind("<Destroy>", self._dispose, add="+")

    # ----------------------------------------------------------------- data
    def show(self, value: dict[str, Any]) -> None:
        self.value, self.expanded = value, set()
        place = value.get("place") or {}
        self.place_label.configure(text=place.get("name", "?"))
        self.time_label.configure(text=f"within {value.get('radius_km', 0):g} km · {str(value.get('observed_at', ''))[11:16]} UTC")
        self._draw_gauge(value.get("situation", "calm"))
        self._draw_counts()
        self._draw_metrics(value.get("metrics") or {})
        self.render()

    def visible_findings(self) -> list[tuple[int, dict[str, Any]]]:
        findings = list(enumerate((self.value or {}).get("findings") or []))
        return findings if self.filter == "all" else [(index, item) for index, item in findings if item["level"] == self.filter]

    def set_filter(self, key: str) -> None:
        self.filter = key
        self.render()

    # ----------------------------------------------------------------- drawing
    def _layout(self, event: Any) -> None:
        """A narrow window: gauge and counts in a strip above the cards; the measurements wait for room."""
        compact = event.width < COMPACT_WIDTH
        if compact == self.compact or event.widget is not self:
            return
        self.compact = compact
        self.side.pack_forget()
        self.main.pack_forget()
        if compact:
            self.side.configure(width=0, height=96)
            self.side.pack(side="top", fill="x", pady=(0, 6))
            for widget in (self.metrics, self.side_buttons):
                widget.pack_forget()
            self.gauge.configure(width=150, height=86)
            self.place_label.pack_configure(side="left", anchor="n")
            self.gauge.pack_configure(side="left", pady=0)
            self.counts.pack_configure(side="left", fill="x", expand=True, pady=0)
        else:
            self.side.configure(width=290, height=0)
            self.side.pack(side="left", fill="y", padx=(0, 8))
            self.gauge.configure(width=260, height=150)
            for widget in (self.place_label, self.time_label, self.gauge, self.counts):
                widget.pack_forget()
            self.place_label.pack(fill="x")
            self.time_label.pack(fill="x")
            self.gauge.pack(pady=(6, 2))
            self.counts.pack(fill="x", pady=4)
            self.metrics.pack(fill="x", pady=4)
            self.side_buttons.pack(side="bottom", fill="x")
        self.main.pack(side="left", fill="both", expand=True)
        if self.value is not None:
            self._draw_gauge(self.value.get("situation", "calm"))

    def _draw_gauge(self, situation: str) -> None:
        canvas, p = self.gauge, self.p
        canvas.delete("all")
        scale = .55 if self.compact else 1.0
        cx, cy, radius = int(canvas.cget("width")) / 2, 118 * scale, 92 * scale
        for index, key in enumerate(("calm", "watch", "alert")):
            colour = SITUATION[key][1]
            canvas.create_arc(cx - radius, cy - radius, cx + radius, cy + radius, start=180 - index * 60, extent=-58,
                              style="arc", width=max(8, int(14 * scale)), outline=colour if key == situation else p.LINE)
        label, colour, share = SITUATION.get(situation, SITUATION["calm"])
        angle = math.pi * (1 - share)
        canvas.create_line(cx, cy, cx + (radius - 22 * scale) * math.cos(angle), cy - (radius - 22 * scale) * math.sin(angle),
                           width=4, fill=colour, capstyle="round")
        canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, fill=colour, outline="")
        if self.compact:
            canvas.create_text(cx, cy + 13, text=label, fill=colour, font=("Segoe UI Semibold", 9))
        else:
            canvas.create_text(cx, cy + 22, text=label, fill=colour, font=("Segoe UI Semibold", 14))

    def _draw_counts(self) -> None:
        for child in self.counts.winfo_children():
            child.destroy()
        findings = (self.value or {}).get("findings") or []
        for column, level in enumerate(("alert", "watch", "info", "gap")):
            name, colour = LEVELS[level]
            count = sum(item["level"] == level for item in findings)
            cell = tk.Frame(self.counts, bg=self.p.PANEL_2, padx=4, pady=3)
            cell.grid(row=0, column=column, sticky="ew", padx=2)
            tk.Label(cell, text=str(count), bg=self.p.PANEL_2, fg=colour if count else self.p.MUTED,
                     font=("Segoe UI Semibold", 13)).pack()
            tk.Label(cell, text=name.title() if level != "gap" else "Blind", bg=self.p.PANEL_2, fg=self.p.MUTED,
                     font=("Segoe UI", 8)).pack()
            self.counts.columnconfigure(column, weight=1)

    def _draw_metrics(self, metrics: dict[str, Any]) -> None:
        for child in self.metrics.winfo_children():
            child.destroy()
        shown = [(label, f"{metrics[key]:g}{unit}" if isinstance(metrics[key], (int, float)) else str(metrics[key]))
                 for key, label, unit in METRICS if metrics.get(key) is not None]
        for index, (label, text) in enumerate(shown[:8]):
            tk.Label(self.metrics, text=label, bg=self.p.PANEL, fg=self.p.MUTED, font=("Segoe UI", 9), anchor="w").grid(
                row=index, column=0, sticky="w")
            tk.Label(self.metrics, text=text, bg=self.p.PANEL, fg=self.p.WHITE, font=("Segoe UI Semibold", 9), anchor="e").grid(
                row=index, column=1, sticky="e")
        self.metrics.columnconfigure(1, weight=1)

    def render(self) -> None:
        p = self.p
        self._smooth.cancel()
        self._evidence.clear()
        for key, chip in self.chip_buttons.items():
            active = key == self.filter
            chip.configure(bg=p.GOLD if active else p.PANEL_2, fg=p.BLACK if active else p.WHITE,
                           activebackground=p.GOLD, activeforeground=p.BLACK)
        for child in self.list.winfo_children():
            child.destroy()
        self.cards = []
        reading = (self.value or {}).get("reading") or {}
        if reading.get("text") and self.filter == "all":
            box = tk.Frame(self.list, bg=p.PANEL_2, padx=12, pady=8)
            box.pack(fill="x", pady=(0, 6))
            tk.Label(box, text="HAYAT's reading · model inference", bg=p.PANEL_2, fg=p.GOLD, font=("Segoe UI Semibold", 9),
                     anchor="w").pack(fill="x")
            tk.Label(box, text=reading["text"], bg=p.PANEL_2, fg=p.WHITE, justify="left", anchor="w", wraplength=self._wrap(),
                     font=("Segoe UI", 10)).pack(fill="x")
        shown = self.visible_findings()
        if not shown:
            tk.Label(self.list, text="Nothing in this group. That is not proof that nothing is happening; see the blind spots.",
                     bg=p.BLACK, fg=p.MUTED, wraplength=self._wrap(), justify="left").pack(anchor="w", pady=8)
        for index, item in shown:
            self.cards.append(self._card(index, item))
        self.canvas.yview_moveto(0)
        from app.gui_icons import apply_icons
        apply_icons(self, self.p)  # cards are made after the window's own icon pass

    def _card(self, index: int, item: dict[str, Any]) -> tk.Frame:
        p = self.p
        name, colour = LEVELS[item["level"]]
        dim = item["level"] == "gap"
        card = tk.Frame(self.list, bg=p.PANEL, highlightthickness=1, highlightbackground=p.LINE)
        card.pack(fill="x", pady=3)
        tk.Frame(card, bg=colour, width=5).pack(side="left", fill="y")
        body = tk.Frame(card, bg=p.PANEL, padx=10, pady=6)
        body.pack(side="left", fill="both", expand=True)
        head = tk.Frame(body, bg=p.PANEL)
        head.pack(fill="x")
        tk.Label(head, text=name, bg=p.PANEL, fg=colour, font=("Segoe UI Semibold", 8)).pack(side="left")
        tk.Label(head, text=f"confidence {item['confidence']}", bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8)).pack(side="right")
        tk.Label(body, text=item["title"], bg=p.PANEL, fg=p.MUTED if dim else p.WHITE, font=("Segoe UI Semibold", 10),
                 anchor="w", justify="left", wraplength=self._wrap()).pack(fill="x")
        tk.Label(body, text=item["detail"], bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=self._wrap()).pack(fill="x")
        row = tk.Frame(body, bg=p.PANEL)
        row.pack(fill="x", pady=(4, 0))
        if item["evidence"]:
            opened = index in self.expanded
            toggle = tk.Label(row, text=("▾ " if opened else "▸ ") + f"Evidence ({len(item['evidence'])})", bg=p.PANEL,
                              fg=p.ROYAL_SOFT, cursor="hand2", font=("Segoe UI", 9))
            toggle.pack(side="left")
            toggle.bind("<Button-1>", lambda _event, index=index: self.toggle(index))
            evidence = tk.Frame(body, bg=p.PANEL)
            for entry in item["evidence"]:
                tk.Label(evidence, text="• " + entry, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 9), anchor="w", justify="left",
                         wraplength=self._wrap() - 14).pack(fill="x", padx=(12, 0))
            self._evidence[index] = (evidence, toggle, len(item["evidence"]))
            if opened:
                evidence.pack(fill="x")
        target = item.get("target") or {}
        label = {"event": "Show on map", "camera": "Watch camera", "point": "Centre map here"}.get(target.get("kind"))
        if label:
            button = ttk.Button(row, text=label, style="Compact.Hayat.TButton", command=lambda target=target: self.open_target(target))
            button._hayat_icon = {"event": "pin", "camera": "eye", "point": "globe"}[target["kind"]]
            button.pack(side="right")
        return card

    def toggle(self, index: int) -> None:
        if index not in self._evidence:
            return
        self.expanded ^= {index}
        evidence, label, count = self._evidence[index]
        opened = index in self.expanded
        label.configure(text=("▾ " if opened else "▸ ") + f"Evidence ({count})")
        if opened:
            evidence.pack(fill="x")
        else:
            evidence.pack_forget()

    def open_target(self, target: dict[str, Any]) -> None:
        action = self.actions.get(target.get("kind"))
        if action:
            action(target)

    def _act(self, key: str) -> None:
        action = self.actions.get(key)
        if action and self.value is not None:
            action(self.value)

    def _wrap(self) -> int:
        return max(260, self.canvas.winfo_width() - 60)

    def _resize(self, event: Any) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        if self._rewrap_job is None:
            self._rewrap_job = self.after_idle(self._rewrap)

    def _rewrap(self) -> None:
        """Resize text in place; rebuilding cards reset the view during a resize."""
        self._rewrap_job = None
        def visit(widget: Any) -> None:
            if isinstance(widget, tk.Label) and int(widget.cget("wraplength")):
                widget.configure(wraplength=self._wrap())
            for child in widget.winfo_children():
                visit(child)
        visit(self.list)

    def _dispose(self, event: Any) -> None:
        if event.widget is not self:
            return
        if self._rewrap_job is not None:
            self.after_cancel(self._rewrap_job)
