"""Around me: a hazard radar in the main window and a full window of its own.

The scope puts the owner's location in the centre, north up, rings at a third,
two thirds and the full radius, and each hazard as a blip at its real bearing.
Distance is drawn on a square-root scale so close things are not crushed into
the centre. Colour is the publisher-based severity; high-severity blips pulse.
A sweep line turns slowly, which is decoration only: every blip is drawn from
the last reading, never invented between readings.

Readings come from the intelligence_radar tool (app/intelligence/radar.py)
through the registry, off the Tk thread. The owner's location is read once and
then passed as coordinates, so a refresh does not wait on Windows location.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

from app.gui_empty import show_empty
from app.gui_resources import BooleanVar, StringVar
from app.gui_windows import place_over
from app.logging import log_swallowed
from app.gui_fonts import DISPLAY

_log = logging.getLogger(__name__)

SEVERITY_COLOURS = {"high": "#F05D5E", "medium": "#F2A541", "low": "#4FA3E0", "info": "#7D8B99"}
HAZARD_FEEDS = ["earthquakes", "earthquakes_emsc", "earthquakes_jma", "earthquakes_geonet", "earthquakes_bmkg",
                "natural_events", "disasters", "wildfires_us", "storms_nhc", "tsunami_ntwc", "tsunami_ptwc",
                "volcanoes_usgs", "volcanoes_geonet", "outbreaks_who", "aviation_sigmet", "floods_uk",
                "air_quality_sg", "warnings_hko", "fireballs_jpl"]
#: The main window refreshes its radar this often; the full window on demand and every 2 minutes.
PANEL_SECONDS = 600
WINDOW_SECONDS = 120


class RadarScope(tk.Canvas):
    """The animated scope. ``show(view)`` draws a reading; the sweep animates on its own."""

    def __init__(self, parent: tk.Misc, palette: Any, size: int = 420) -> None:
        super().__init__(parent, width=size, height=size, bg=palette.BLACK, highlightthickness=0)
        self.palette, self.size = palette, size
        self.view: dict[str, Any] | None = None
        self.angle = 0.0
        self._blips: list[tuple[int, dict[str, Any]]] = []
        self._tip = None
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _event: self._hide_tip())
        self._draw_frame()
        self._animate()

    def _centre(self) -> tuple[float, float, float]:
        return self.size / 2, self.size / 2, self.size / 2 - 18

    def _draw_frame(self) -> None:
        p = self.palette
        cx, cy, radius = self._centre()
        self.delete("frame")
        for share in (1 / 3, 2 / 3, 1.0):
            r = radius * math.sqrt(share)
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=p.LINE, width=1, tags="frame")
        self.create_line(cx - radius, cy, cx + radius, cy, fill=p.PANEL_2, tags="frame")
        self.create_line(cx, cy - radius, cx, cy + radius, fill=p.PANEL_2, tags="frame")
        for label, x, y in (("N", cx, cy - radius - 9), ("S", cx, cy + radius + 9), ("E", cx + radius + 9, cy),
                            ("W", cx - radius - 9, cy)):
            self.create_text(x, y, text=label, fill=p.MUTED, font=("Segoe UI Semibold", 8), tags="frame")
        self.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=p.GOLD, outline="", tags="frame")

    def _position(self, item: dict[str, Any], radius_km: float) -> tuple[float, float]:
        cx, cy, radius = self._centre()
        share = math.sqrt(max(0.0, min(1.0, item["distance_km"] / max(1.0, radius_km))))
        angle = math.radians(item["bearing_deg"])
        return cx + math.sin(angle) * radius * share, cy - math.cos(angle) * radius * share

    def show(self, view: dict[str, Any] | None) -> None:
        self.view = view
        self.delete("blip", "label")
        self._blips = []
        if not view:
            return
        cx, cy, radius = self._centre()
        radius_km = view.get("radius_km") or 500
        for share in ((1 / 3, 2 / 3, 1.0) if self.size >= 250 else ()):  # the small scope has no room for labels
            self.create_text(cx + 3, cy - radius * math.sqrt(share) + 7, text=f"{radius_km * share:.0f} km", anchor="w",
                             fill=self.palette.MUTED, font=("Segoe UI", 7), tags="label")
        # Least serious first, so the serious ones are drawn on top.
        for item in sorted(view.get("hazards") or [], key=lambda entry: entry["severity_rank"]):
            x, y = self._position(item, radius_km)
            size = {"high": 6, "medium": 5, "low": 4}.get(item["severity"], 3)
            blip = self.create_oval(x - size, y - size, x + size, y + size, fill=SEVERITY_COLOURS.get(item["severity"], "#7D8B99"),
                                    outline=self.palette.BLACK, tags="blip")
            self._blips.append((blip, item))

    def _animate(self) -> None:
        if not self.winfo_exists():
            return
        cx, cy, radius = self._centre()
        self.angle = (self.angle + 3) % 360
        self.delete("sweep")
        for step, shade in ((0, self.palette.OLIVE_SOFT), (6, self.palette.OLIVE), (12, self.palette.PANEL_2)):
            angle = math.radians(self.angle - step)
            self.create_line(cx, cy, cx + math.sin(angle) * radius, cy - math.cos(angle) * radius, fill=shade,
                             width=2 if step == 0 else 1, tags="sweep")
        if self._blips:
            self.tag_lower("sweep", "blip")
        # High-severity blips pulse.
        pulse = 1 + 0.35 * math.sin(time.monotonic() * 4)
        for blip, item in self._blips:
            if item["severity"] == "high":
                x, y = self._position(item, (self.view or {}).get("radius_km") or 500)
                size = 6 * pulse
                self.coords(blip, x - size, y - size, x + size, y + size)
        try:
            self.after(60, self._animate)
        except tk.TclError:
            return

    def _hover(self, event: tk.Event) -> None:
        nearest = None
        for blip, item in self._blips:
            x1, y1, x2, y2 = self.coords(blip)
            if x1 - 4 <= event.x <= x2 + 4 and y1 - 4 <= event.y <= y2 + 4:
                nearest = item
        if nearest is None:
            self._hide_tip()
            return
        text = f"{nearest['title']}\n{nearest['severity']} · {nearest['distance_km']:.0f} km {nearest['bearing']}"
        self._hide_tip()
        self._tip = self.create_text(min(event.x + 12, self.size - 150), max(event.y - 10, 12), text=text, anchor="w",
                                     fill=self.palette.WHITE, font=("Segoe UI", 8), width=200, tags="tip")
        box = self.bbox(self._tip)
        self.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2, fill=self.palette.PANEL, outline=self.palette.LINE,
                              tags="tip")
        self.tag_raise(self._tip)

    def _hide_tip(self) -> None:
        self.delete("tip")
        self._tip = None


class RadarReader:
    """Runs the radar tool off the Tk thread and remembers where 'here' is once it is known."""

    def __init__(self, registry: Any) -> None:
        self.registry = registry
        self.home: tuple[float, float, str] | None = None
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        #: A request made while a read is running: the newest one runs as soon as the current read ends.
        self.pending: tuple[str, float, float, bool] | None = None

    def read(self, place: str = "", radius_km: float = 500, since_hours: float = 72, refresh_feeds: bool = False) -> None:
        if self.busy:
            self.pending = (place, radius_km, since_hours, refresh_feeds)
            return
        self.busy = True

        def job() -> None:
            try:
                if refresh_feeds and "intelligence_refresh" in self.registry.names():
                    self.registry.execute("intelligence_refresh", {"feeds": HAZARD_FEEDS, "due_only": True})
                arguments: dict[str, Any] = {"radius_km": radius_km, "since_hours": since_hours}
                if place:
                    arguments["place"] = place
                elif self.home:
                    arguments.update(latitude=self.home[0], longitude=self.home[1], place=self.home[2])
                result = self.registry.execute("intelligence_radar", arguments)
                if result.success and not place:
                    home = result.output["home"]
                    self.home = (home["latitude"], home["longitude"], home["name"])
                self.results.put(result.output if result.success else {"error": result.error})
            except Exception as exc:
                log_swallowed(_log, "reading the hazard radar", level=logging.WARNING)
                self.results.put({"error": f"{type(exc).__name__}: {exc}"})

        threading.Thread(target=job, name="hayat-radar", daemon=True).start()

    def take(self) -> dict[str, Any] | None:
        latest = None
        while True:
            try:
                latest = self.results.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self.busy = False
            if self.pending is not None:
                waiting, self.pending = self.pending, None
                self.read(*waiting)
                return None  # the newer request's answer replaces this one
        return latest


class RadarWindow(tk.Toplevel):
    """Around me, full size: the scope, the list, the controls."""

    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.reader = getattr(gui, "_radar_reader", None) or RadarReader(gui.runtime.registry)
        gui._radar_reader = self.reader
        self.title("HAYAT · Around me")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        self.last_read = 0.0
        self._build()
        place_over(self, gui.root, (1060, 640))
        self.deiconify()
        self.lift()
        self.refresh()
        self._poll()

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="Around me", bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 16)).pack(side="left")
        tk.Label(header, text="hazard radar · every public feed HAYAT reads, by distance and bearing", bg=p.BLACK,
                 fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=10, pady=(5, 0))
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")
        controls = tk.Frame(self, bg=p.BLACK, padx=16)
        controls.pack(fill="x", pady=(0, 8))
        tk.Label(controls, text="Place", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.place_var = StringVar(self, "")
        entry = ttk.Entry(controls, textvariable=self.place_var, width=26)
        entry.pack(side="left", padx=(6, 12))
        entry.bind("<Return>", lambda _event: self.refresh())
        tk.Label(controls, text="Radius", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.radius_var = StringVar(self, "500 km")
        ttk.Combobox(controls, textvariable=self.radius_var, values=["100 km", "300 km", "500 km", "1000 km", "2000 km"],
                     state="readonly", width=8).pack(side="left", padx=(6, 12))
        tk.Label(controls, text="Last", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.hours_var = StringVar(self, "3 days")
        ttk.Combobox(controls, textvariable=self.hours_var, values=["24 hours", "3 days", "7 days", "30 days"],
                     state="readonly", width=8).pack(side="left", padx=(6, 12))
        ttk.Button(controls, text="Show", style="Hayat.TButton", width=0, command=self.refresh).pack(side="left")
        ttk.Button(controls, text="Fetch latest feeds", style="HayatAccent.TButton", width=0,
                   command=lambda: self.refresh(refresh_feeds=True)).pack(side="left", padx=6)
        body = tk.Frame(self, bg=p.BLACK, padx=16)
        body.pack(fill="both", expand=True)
        self.scope = RadarScope(body, p, size=440)
        self.scope.pack(side="left", anchor="n")
        right = tk.Frame(body, bg=p.BLACK)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.summary = StringVar(self, "Reading…")
        tk.Label(right, textvariable=self.summary, bg=p.BLACK, fg=p.WHITE, font=("Segoe UI", 10), anchor="w", justify="left",
                 wraplength=520).pack(fill="x")
        self.counts = StringVar(self, "")
        tk.Label(right, textvariable=self.counts, bg=p.BLACK, fg=p.GOLD_SOFT, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=520).pack(fill="x", pady=(4, 6))
        self.table = ttk.Treeview(right, columns=("severity", "distance", "what"), show="headings", height=16)
        for column, title, width in (("severity", "Severity", 70), ("distance", "Distance", 90), ("what", "What", 380)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w")
        self.table.pack(fill="both", expand=True)
        for severity, colour in SEVERITY_COLOURS.items():
            self.table.tag_configure(severity, foreground=colour)
        show_empty(self.table, "Reading the hazards HAYAT holds near you…", p)
        # The rings carry their distances from the start, not only once the first reading arrives.
        self.scope.show({"radius_km": 500, "hazards": []})
        self.note = StringVar(self, "")
        tk.Label(right, textvariable=self.note, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 8), anchor="w", justify="left",
                 wraplength=520).pack(fill="x", pady=(6, 0))

    def _numbers(self) -> tuple[float, float]:
        radius = float(self.radius_var.get().split()[0])
        hours = {"24 hours": 24, "3 days": 72, "7 days": 168, "30 days": 720}.get(self.hours_var.get(), 72)
        return radius, hours

    def refresh(self, refresh_feeds: bool = False) -> None:
        radius, hours = self._numbers()
        self.summary.set("Fetching the latest public feeds, then reading the radar…" if refresh_feeds else "Reading…")
        self.last_read = time.monotonic()
        self.reader.read(self.place_var.get().strip(), radius, hours, refresh_feeds=refresh_feeds)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            view = self.reader.take()
            if view is not None:
                self.paint(view)
            if time.monotonic() - self.last_read >= WINDOW_SECONDS and not self.reader.busy:
                self.refresh()
        except Exception:
            log_swallowed(_log, "updating the radar window")
        self.after(500, self._poll)

    def paint(self, view: dict[str, Any]) -> None:
        if view.get("error"):
            self.summary.set(f"The radar could not read: {view['error']}")
            return
        self.scope.show(view)
        self.summary.set(view["summary"])
        self.counts.set(" · ".join(f"{name} {count}" for name, count in sorted(view["by_category"].items(), key=lambda x: -x[1])))
        self.note.set(view["note"])
        self.table.delete(*self.table.get_children())
        for index, item in enumerate(view["hazards"]):
            self.table.insert("", "end", iid=str(index), tags=(item["severity"],),
                              values=(item["severity"], f"{item['distance_km']:.0f} km {item['bearing']}",
                                      item["title"] + (f"  ({item['note']})" if item["note"] else "")))
        radius, _hours = self._numbers()
        show_empty(self.table, f"Nothing within {radius:.0f} km in the last {self.hours_var.get()}. Press Fetch latest "
                               "feeds to check the public feeds now, or widen the radius.", self.palette)


def build_panel(gui: Any, column: tk.Misc, section: Any) -> None:
    """The compact radar in the main window's right column."""
    from app.intelligence.radar import HeadsUp

    panel = section(column, type(gui), "Around me · hazard radar", action=("Open radar", gui._open_radar),
                    fold=getattr(gui, "_folds", None))
    panel.pack(fill="x", pady=(10, 0))
    row = tk.Frame(panel.body, bg=gui.PANEL)
    row.pack(fill="x")
    gui.radar_scope = RadarScope(row, type(gui), size=150)
    gui.radar_scope.pack(side="left")
    gui.radar_text = StringVar(master=gui.root, value="Reading where you are…")
    tk.Label(row, textvariable=gui.radar_text, bg=gui.PANEL, fg=gui.MUTED, font=("Segoe UI", 8), anchor="nw",
             justify="left", wraplength=180).pack(side="left", fill="both", expand=True, padx=(8, 0))
    gui.radar_heads_up = BooleanVar(master=gui.root, value=True)
    gui._radar_reader = getattr(gui, "_radar_reader", None) or RadarReader(gui.runtime.registry)
    gui._radar_heads = HeadsUp()
    gui._radar_read_at = 0.0


def paint_panel(gui: Any) -> None:
    """Called from the main window's tick: start a read when due, paint a finished one, raise a heads-up once."""
    reader = getattr(gui, "_radar_reader", None)
    if reader is None or not hasattr(gui, "radar_scope"):
        return
    if gui.runtime.authority.privacy_mode:
        gui.radar_text.set_if_changed("Paused: privacy mode (the radar uses this computer's location).")
        return
    view = reader.take()
    if view is not None:
        if view.get("error"):
            gui.radar_text.set_if_changed(f"Radar unavailable: {str(view['error'])[:120]}")
        else:
            gui.radar_scope.show(view)
            top = [item for item in view["hazards"] if item["severity"] in ("high", "medium") and not item["scheduled"]][:2]
            lines = [f"{view['total']} hazard(s) within {view['radius_km']:.0f} km of {view['home']['name']}"]
            lines += [f"• {item['title'][:60]} — {item['distance_km']:.0f} km {item['bearing']}" for item in top]
            if not top and view["hazards"]:
                nearest = min(view["hazards"], key=lambda item: item["distance_km"])
                lines.append(f"Nearest: {nearest['title'][:60]} — {nearest['distance_km']:.0f} km {nearest['bearing']}")
            gui.radar_text.set_if_changed("\n".join(lines))
            if gui.radar_heads_up.get():
                for item in gui._radar_heads.new(view)[:3]:  # at most three notes per reading
                    gui._append("system", f"Heads-up: {item['title']} — {item['severity']} severity, "
                                          f"{item['distance_km']:.0f} km {item['bearing']} of you ({item['provider'] or 'public feed'}).")
    if time.monotonic() - gui._radar_read_at >= PANEL_SECONDS and not reader.busy:
        gui._radar_read_at = time.monotonic()
        reader.read(radius_km=500, since_hours=72)
