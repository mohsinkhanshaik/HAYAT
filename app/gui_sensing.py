"""The Wi-Fi Sensing window: connect a CSI source, see it live, and cross-check movement with the camera.

Four panels, all drawn from what the radio and camera actually report:

* Source -- scan, choose a source (ESP32 on a COM port, UDP from the local
  network, a recording, or the built-in adapter's signal strength), record,
  connect, disconnect and calibrate. Each button runs the same registered tool
  chat uses, so permissions and privacy mode apply here exactly as there.
* Live -- a waterfall of every subcarrier's amplitude over the last few
  seconds (time runs left to right, bright means strong); for the built-in
  adapter, which gives no CSI, a signal-strength line instead, labelled so.
* Movement -- the level now and one bar per second for the last two minutes,
  coloured still / some movement / strong movement.
* Camera -- cross-check movement with the camera now, or tick "Confirm with
  camera" so a 3-second camera check runs when Wi-Fi movement starts. The
  camera never opens otherwise.
"""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any

from app.gui_icons import apply_icons
from app.gui_resources import BooleanVar, StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.logging import log_swallowed
from app.gui_fonts import DISPLAY

_log = logging.getLogger(__name__)

#: What the source list shows, and the csi_connect kind behind it.
SOURCES = (
    ("Auto (an ESP32 if plugged in, else signal strength)", "auto"),
    ("ESP32 on a USB serial port", "serial"),
    ("UDP from a device on my network", "udp"),
    ("Recording or log file (replay)", "file"),
    ("Built-in Wi-Fi adapter (signal strength, not CSI)", "wifi_link"),
)
WATERFALL = (560, 170)
TIMELINE = (560, 100)
#: Frames drawn in the waterfall, newest on the right.
WATERFALL_FRAMES = 240


def level_colour(palette: Any, level: str) -> str:
    return {"still": palette.OLIVE, "some movement": palette.GOLD, "strong movement": palette.ALERT}.get(
        level, palette.LINE)


def reading_height(reading: dict[str, Any]) -> float:
    """0..1 bar height for one timeline reading, on the scale its level was judged on."""
    if reading.get("ratio") is not None:
        return max(0.05, min(1.0, float(reading["ratio"]) / 6.0))
    if reading.get("basis") == "csi_amplitude" and reading.get("autocorrelation") is not None:
        return max(0.05, min(1.0, float(reading["autocorrelation"])))
    if reading.get("score") is not None:
        return max(0.05, min(1.0, float(reading["score"]) / 4.0))
    return 0.05


def waterfall_pixels(frames: list[Any], height: int) -> Any:
    """Subcarrier-by-time amplitudes as a uint8 array (rows = subcarriers), per-frame gain removed."""
    import numpy as np

    rows = [frame.amplitudes for frame in frames if getattr(frame, "amplitudes", None)]
    if not rows:
        return None
    width = max(set(len(row) for row in rows), key=[len(row) for row in rows].count)
    matrix = np.asarray([row for row in rows if len(row) == width][-WATERFALL_FRAMES:], dtype=float)
    scale = matrix.mean(axis=1, keepdims=True)
    scale[scale == 0] = 1.0
    matrix = matrix / scale
    low, high = np.percentile(matrix, 2), np.percentile(matrix, 98)
    span = high - low if high > low else 1.0
    pixels = np.clip((matrix - low) / span * 255, 0, 255).astype("uint8")
    return pixels.T  # subcarriers down, time across


class SensingStudio(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.registry = gui.runtime.registry
        self.hub, self.monitor = self.registry.csi, self.registry.sensing
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        self._photo = None
        self.title("HAYAT · Wi-Fi sensing & router")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        self._build()
        apply_icons(self, self.palette)
        place_over(self, gui.root, (1220, 800))
        self.deiconify()
        self.lift()
        self.scan()
        self._poll()

    # ---------------------------------------------------------------- layout
    def _card(self, parent: tk.Misc, title: str) -> tk.Frame:
        p = self.palette
        card = tk.Frame(parent, bg=p.PANEL, padx=12, pady=10, highlightthickness=1, highlightbackground=p.LINE)
        tk.Label(card, text=title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 11), anchor="w").pack(fill="x")
        return card

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="Wi-Fi sensing & router", bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 16)).pack(side="left")
        tk.Label(header, text="channel state information · movement · camera cross-check · router and network", bg=p.BLACK, fg=p.MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=10, pady=(5, 0))
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")
        # Status lights: each says one fact, lit only while it is true.
        lights = tk.Frame(self, bg=p.BLACK, padx=16)
        lights.pack(fill="x", pady=(0, 8))
        self.lights: dict[str, tk.Label] = {}
        for key in ("connected", "receiving", "kind", "level", "privacy"):
            label = tk.Label(lights, text="", bg=p.PANEL_2, fg=p.MUTED, font=("Segoe UI Semibold", 9), padx=10, pady=4)
            label.pack(side="left", padx=(0, 6))
            self.lights[key] = label
        self.detail_var = StringVar(self, "")
        tk.Label(lights, textvariable=self.detail_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9),
                 anchor="w").pack(side="left", fill="x", expand=True, padx=(6, 0))

        # The footer is packed before the tabs so a short screen squeezes the tabs, never the status line.
        footer = tk.Frame(self, bg=p.BLACK, padx=16, pady=6)
        footer.pack(fill="x", side="bottom")
        self.status_var = StringVar(self, "")
        tk.Label(footer, textvariable=self.status_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=120, style="Hayat.Horizontal.TProgressbar")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=16)
        body = tk.Frame(self.tabs, bg=p.BLACK, pady=10)
        self.tabs.add(body, text="  Sensing  ")
        router_tab = tk.Frame(self.tabs, bg=p.BLACK)
        self.tabs.add(router_tab, text="  Router & network  ")
        from app.gui_network import RouterPanel

        self.router = RouterPanel(router_tab, self)
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(1, weight=1)

        # -- source
        source = self._card(body, "Source")
        source.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        self.source_var = StringVar(self, SOURCES[0][0])
        self.source_box = ttk.Combobox(source, textvariable=self.source_var, values=[label for label, _ in SOURCES],
                                       state="readonly", width=46)
        self.source_box.pack(fill="x", pady=(6, 6))
        self.source_box.bind("<<ComboboxSelected>>", lambda _event: self._show_fields())
        fields = tk.Frame(source, bg=p.PANEL)
        fields.pack(fill="x")
        self.port_var, self.udp_var, self.file_var = StringVar(self, ""), StringVar(self, "5500"), StringVar(self, "")
        self.fields: dict[str, tk.Frame] = {}
        for key, text, variable in (("serial", "COM port", self.port_var), ("udp", "UDP port", self.udp_var),
                                    ("file", "File", self.file_var)):
            row = tk.Frame(fields, bg=p.PANEL)
            tk.Label(row, text=text, bg=p.PANEL, fg=p.MUTED, width=9, anchor="w", font=("Segoe UI", 9)).pack(side="left")
            ttk.Entry(row, textvariable=variable, width=30).pack(side="left", fill="x", expand=True)
            if key == "file":
                ttk.Button(row, text="Browse", style="Hayat.TButton", width=0, command=self._browse).pack(side="left", padx=(4, 0))
            self.fields[key] = row
        self.record_var = BooleanVar(self, False)
        ttk.Checkbutton(source, text="Record every frame for later", variable=self.record_var).pack(anchor="w", pady=(6, 4))
        buttons = tk.Frame(source, bg=p.PANEL)
        buttons.pack(fill="x", pady=(4, 0))
        self.action_buttons = []
        for text, command in (("Scan", self.scan), ("Connect", self.connect), ("Disconnect", self.disconnect),
                              ("Calibrate (still room)", self.calibrate)):
            button = ttk.Button(buttons, text=text, style="Hayat.TButton", width=0, command=command)
            button.pack(side="left", padx=(0, 4))
            self.action_buttons.append(button)
        self.log = DarkScrolledText(source, wrap="word", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI", 9), height=9,
                                    width=52, padx=8, pady=6, relief="flat", state="disabled")
        self.log.pack(fill="both", expand=True, pady=(8, 0))
        self._show_fields()

        # -- live
        live = self._card(body, "Live signal")
        live.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        self.live_caption = StringVar(self, "Nothing connected.")
        tk.Label(live, textvariable=self.live_caption, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9),
                 anchor="w", justify="left", wraplength=WATERFALL[0]).pack(fill="x")
        self.waterfall = tk.Canvas(live, width=WATERFALL[0], height=WATERFALL[1], bg=p.BLACK, highlightthickness=0)
        self.waterfall.pack(fill="x", pady=(6, 0))

        # -- movement
        movement = self._card(body, "Movement")
        movement.grid(row=1, column=1, sticky="nsew", pady=(0, 10))
        top = tk.Frame(movement, bg=p.PANEL)
        top.pack(fill="x")
        self.level_var = StringVar(self, "Waiting for a reading")
        self.level_label = tk.Label(top, textvariable=self.level_var, bg=p.PANEL, fg=p.WHITE,
                                    font=("Segoe UI Semibold", 15), anchor="w")
        self.level_label.pack(side="left")
        self.level_detail = StringVar(self, "")
        tk.Label(top, textvariable=self.level_detail, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=10)
        self.timeline = tk.Canvas(movement, width=TIMELINE[0], height=TIMELINE[1], bg=p.BLACK, highlightthickness=0)
        self.timeline.pack(fill="x", pady=(6, 4))
        self.reading_var = StringVar(self, "")
        tk.Label(movement, textvariable=self.reading_var, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=560).pack(fill="x")

        # -- camera
        camera = self._card(body, "Camera cross-check")
        camera.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        self.camera_state = StringVar(self, "")
        tk.Label(camera, textvariable=self.camera_state, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9),
                 anchor="w").pack(fill="x", pady=(4, 4))
        self.confirm_var = BooleanVar(self, bool(self.monitor.camera_confirm))
        ttk.Checkbutton(camera, text="Confirm Wi-Fi movement with a 3-second camera check",
                        variable=self.confirm_var, command=self._toggle_confirm).pack(anchor="w")
        self.check_button = ttk.Button(camera, text="Check now: Wi-Fi + camera", style="HayatAccent.TButton",
                                       width=0, command=self.check_with_camera)
        self.check_button.pack(anchor="w", pady=(8, 6))
        self.fused_var = StringVar(self, "No cross-check yet.")
        tk.Label(camera, textvariable=self.fused_var, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 10), anchor="w",
                 justify="left", wraplength=470).pack(fill="x")
        tk.Label(camera, text="The camera opens only for a check you ask for or tick above; no frames are kept. "
                 "Privacy mode stops Wi-Fi sensing and the camera together.", bg=p.PANEL, fg=p.GOLD_SOFT,
                 font=("Segoe UI", 8), anchor="w", justify="left", wraplength=470).pack(fill="x", pady=(8, 0))

    def show_tab(self, name: str) -> None:
        """'sensing' or 'router'."""
        self.tabs.select(1 if name == "router" else 0)

    def _kind(self) -> str:
        label = self.source_var.get()
        return next((kind for text, kind in SOURCES if text == label), "auto")

    def _show_fields(self) -> None:
        kind = self._kind()
        for key, row in self.fields.items():
            if key == kind:
                row.pack(fill="x", pady=1)
            else:
                row.pack_forget()

    def _browse(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Choose a CSI recording or log",
                                          initialdir=str(self.hub.root / "recordings"),
                                          filetypes=(("CSI recordings and logs", "*.jsonl *.txt *.log *.csv"), ("All files", "*.*")))
        if path:
            self.file_var.set(path)

    # --------------------------------------------------------------- actions
    def _write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.configure(state="disabled")

    def _run(self, label: str, tool: str, arguments: dict[str, Any]) -> None:
        if self.busy:
            return
        self.busy = True
        self.status_var.set(label + "…")
        self.progress.pack(side="right")
        self.progress.start(15)

        def job() -> None:
            try:
                result = self.registry.execute(tool, arguments)
                self.results.put((tool, result))
            except Exception as exc:
                log_swallowed(_log, f"running {tool} from the Wi-Fi sensing window", level=logging.WARNING)
                self.results.put((tool, exc))

        threading.Thread(target=job, name=f"hayat-sensing-{tool}", daemon=True).start()

    def scan(self) -> None:
        self._run("Looking for CSI sources", "csi_scan", {})

    def connect(self) -> None:
        kind = self._kind()
        arguments: dict[str, Any] = {"kind": kind, "record": bool(self.record_var.get())}
        if kind == "serial":
            if not self.port_var.get().strip():
                self._write("Type the COM port first (Scan lists them; an ESP32 bridge is marked).")
                return
            arguments["port"] = self.port_var.get().strip()
        elif kind == "udp":
            try:
                arguments["udp_port"] = int(self.udp_var.get())
            except ValueError:
                self._write("The UDP port must be a number, for example 5500.")
                return
        elif kind == "file":
            if not self.file_var.get().strip():
                self._write("Choose a recording or log file first.")
                return
            arguments["path"] = self.file_var.get().strip()
        self._run("Connecting", "csi_connect", arguments)

    def disconnect(self) -> None:
        self._run("Disconnecting", "csi_disconnect", {})

    def calibrate(self) -> None:
        self._write("Calibrating: keep the room still and empty for 10 seconds…")
        self._run("Recording the still room", "csi_calibrate", {"seconds": 10})

    def check_with_camera(self) -> None:
        self.fused_var.set("Checking Wi-Fi and the camera for 4 seconds…")
        self._run("Cross-checking with the camera", "sensing_check", {"with_camera": True, "seconds": 4})

    def _toggle_confirm(self) -> None:
        self.monitor.set_camera_confirm(bool(self.confirm_var.get()))

    def _finish(self, tool: str, result: Any) -> None:
        from app.csi import report

        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        self.status_var.set("")
        if isinstance(result, Exception):
            text = f"That did not run: {type(result).__name__}: {result}"
        elif not result.success:
            text = f"That did not work: {result.error}"
        else:
            text = report([{"tool": tool, "result": {"success": True, "output": result.output},
                            "verification": {"passed": True}}]) or str(result.output)[:2000]
        if tool == "sensing_check":
            self.fused_var.set(text)
        else:
            self._write(text)
        if tool == "csi_scan" and not isinstance(result, Exception) and result.success:
            ports = [item["port"] for item in result.output.get("serial_ports", []) if item.get("likely_csi_device")]
            if ports and not self.port_var.get():
                self.port_var.set(ports[0])

    # ------------------------------------------------------------------ live
    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        while True:
            try:
                tool, result = self.results.get_nowait()
            except queue.Empty:
                break
            self._finish(tool, result)
        try:
            self.router.poll(visible=self.tabs.index("current") == 1)
        except Exception:
            log_swallowed(_log, "updating the router tab")
        try:
            self.paint()
        except Exception:
            log_swallowed(_log, "painting the Wi-Fi sensing window")
        self.after(500, self._poll)

    def _light(self, key: str, text: str, on: bool, colour: str | None = None) -> None:
        p = self.palette
        self.lights[key].configure(text=text, fg=p.BLACK if on else p.MUTED, bg=(colour or p.OLIVE) if on else p.PANEL_2)

    def paint(self) -> None:
        p = self.palette
        snapshot = self.monitor.snapshot(history=120)
        status = snapshot["status"]
        connections = status["connections"]
        active = next((item for item in reversed(connections) if item["receiving"]), connections[-1] if connections else None)
        privacy = bool(getattr(self.gui.runtime.authority, "privacy_mode", False))
        replayed = active is not None and active["state"] == "finished"
        if replayed:
            self._light("connected", "● Replay finished", True, p.HOLO)
        else:
            self._light("connected", "● Connected" if status["connected"] else "○ Not connected", status["connected"])
        if replayed:
            self._light("receiving", f"{active['frames_total']:,} frames replayed", True, p.HOLO)
        else:
            self._light("receiving", "● Receiving" if status["receiving"] else "○ No frames", status["receiving"])
        if active is None:
            self._light("kind", "No source", False)
        elif active["is_csi"]:
            self._light("kind", f"Real CSI · {active['subcarriers']} subcarriers", True, p.ROYAL_SOFT)
        else:
            self._light("kind", "Signal strength only (not CSI)", bool(active["frames_total"]), p.GOLD_SOFT)
        level = snapshot["level"]
        self._light("level", level.capitalize() if level != "unknown" else "No movement reading",
                    level != "unknown", level_colour(p, level))
        self._light("privacy", "Privacy mode: sensing off" if privacy else "Privacy off", privacy, p.GOLD)
        if active is None:
            self.detail_var.set_if_changed("Connect a source to start.")
        else:
            from pathlib import Path

            bits = [Path(active["target"]).name if active["kind"] == "file" else active["target"]]
            if active.get("frames_per_second") is not None:
                bits.append(f"{active['frames_per_second']} frames/s")
            if active.get("rssi") is not None:
                bits.append(f"RSSI {active['rssi']:.0f} dBm")
            if active.get("last_frame_age_s") is not None:
                bits.append(f"last frame {active['last_frame_age_s']:.1f} s ago")
            if active.get("recording"):
                bits.append("recording")
            if active.get("error"):
                bits.append(active["error"])
            self.detail_var.set_if_changed(" · ".join(bits))
        self._paint_live(active)
        self._paint_movement(snapshot)
        camera_on = bool(getattr(self.gui, "_camera_on", False))
        self.camera_state.set_if_changed("Camera is on." if camera_on else "Camera is off; a check opens it briefly.")
        fused = snapshot.get("last_fused")
        if fused and not self.busy:
            self.fused_var.set_if_changed(fused["summary"])

    def _paint_live(self, active: dict[str, Any] | None) -> None:
        p = self.palette
        canvas = self.waterfall
        width, height = WATERFALL
        if active is None:
            canvas.delete("all")
            canvas.create_text(width // 2, height // 2, text="No source connected.", fill=p.MUTED,
                               font=("Segoe UI", 10))
            self.live_caption.set_if_changed("Nothing connected.")
            return
        frames = self.hub.frames(active["name"], 0 if active["kind"] == "file" else 30)
        if active["is_csi"]:
            pixels = waterfall_pixels(frames, height)
            if pixels is None:
                return
            from PIL import Image, ImageTk

            lut = _palette_lut(p)
            image = Image.fromarray(pixels, mode="L").convert("P")
            image.putpalette(lut)
            image = image.convert("RGB").resize((width, height), Image.NEAREST)
            self._photo = ImageTk.PhotoImage(image, master=canvas)
            canvas.delete("all")
            canvas.create_image(0, 0, image=self._photo, anchor="nw")
            canvas.create_text(6, 6, text="subcarrier ↓", fill=p.WHITE, anchor="nw", font=("Segoe UI", 8))
            canvas.create_text(width - 6, height - 6, text="now →", fill=p.WHITE, anchor="se", font=("Segoe UI", 8))
            self.live_caption.set_if_changed(
                f"Amplitude of each of {pixels.shape[0]} subcarriers over the last {pixels.shape[1]} packets. "
                "Bright is strong; ripples moving through it are things moving through the radio paths.")
            return
        values = [frame.rssi for frame in frames if frame.rssi is not None][-120:]
        canvas.delete("all")
        self.live_caption.set_if_changed("Signal strength from the built-in adapter (dBm). This is not CSI: Windows "
                                         "gives no per-subcarrier data from this card.")
        if len(values) < 2:
            canvas.create_text(width // 2, height // 2, text="Waiting for readings…", fill=p.MUTED, font=("Segoe UI", 10))
            return
        low, high = min(values) - 2, max(values) + 2
        step = width / max(1, len(values) - 1)
        points = []
        for index, value in enumerate(values):
            points += [index * step, height - 12 - (value - low) / (high - low) * (height - 24)]
        canvas.create_line(*points, fill=p.GOLD, width=2, smooth=True)
        canvas.create_text(6, 6, text=f"{high:.0f} dBm", fill=p.MUTED, anchor="nw", font=("Segoe UI", 8))
        canvas.create_text(6, height - 4, text=f"{low:.0f} dBm", fill=p.MUTED, anchor="sw", font=("Segoe UI", 8))

    def _paint_movement(self, snapshot: dict[str, Any]) -> None:
        p = self.palette
        canvas = self.timeline
        width, height = TIMELINE
        history = snapshot["history"]
        latest = snapshot["latest"]
        canvas.delete("all")
        if not history:
            canvas.create_text(width // 2, height // 2, text="Movement appears here once frames arrive.",
                               fill=p.MUTED, font=("Segoe UI", 10))
            self.level_var.set_if_changed("Waiting for a reading")
            self.level_label.configure(fg=p.WHITE)
            self.level_detail.set_if_changed("")
            self.reading_var.set_if_changed("")
            return
        bar = width / 120
        for index, reading in enumerate(history[-120:]):
            x = width - (len(history[-120:]) - index) * bar
            top = height - 4 - reading_height(reading) * (height - 10)
            canvas.create_rectangle(x, top, x + bar - 1, height - 2, outline="",
                                    fill=level_colour(p, reading["level"]))
        canvas.create_text(4, 4, text="last 2 minutes", fill=p.MUTED, anchor="nw", font=("Segoe UI", 8))
        level = snapshot["level"] if snapshot["level"] != "unknown" else latest["level"]
        self.level_var.set_if_changed(level.capitalize() if level != "unknown" else "Not enough data")
        self.level_label.configure(fg=level_colour(p, level) if level != "unknown" else p.WHITE)
        moving = sum(1 for item in history[-60:] if item["level"] not in {"still", "unknown"})
        detail = f"moving {moving} of the last {len(history[-60:])} s"
        detail += " · calibrated" if latest.get("calibrated") else " · not calibrated"
        self.level_detail.set_if_changed(detail)
        self.reading_var.set_if_changed(latest["summary"])


def _palette_lut(palette: Any) -> list[int]:
    """256 RGB entries: near black through olive to gold, the window's own colours."""
    stops = [(0, palette.BLACK), (110, palette.OLIVE), (200, palette.GOLD), (255, palette.GOLD_SOFT)]

    def rgb(value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

    lut: list[int] = []
    for index in range(256):
        for (start, a), (end, b) in zip(stops, stops[1:]):
            if start <= index <= end:
                share = (index - start) / max(1, end - start)
                first, second = rgb(a), rgb(b)
                lut += [round(first[c] + (second[c] - first[c]) * share) for c in range(3)]
                break
    return lut
