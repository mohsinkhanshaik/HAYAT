"""The "Router & network" tab of the Wi-Fi sensing window: the router, its signal, the air around it, the devices.

Every number comes from the network_* tools (app/network.py), run through the
registry like any other request, every 20 seconds while the tab is showing and
on Refresh. Nothing here logs into the router or changes a setting; "Open
admin page" only opens the router's own page in the browser, where the owner
signs in himself if he wants to.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Any

from app.gui_empty import show_empty
from app.gui_resources import StringVar
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

REFRESH_SECONDS = 20
CHART = (470, 110)
QUALITY_COLOURS = ("excellent", "good", "fair", "poor")


def quality_colour(palette: Any, word: str | None) -> str:
    return {"excellent": palette.OLIVE_SOFT, "good": palette.OLIVE, "fair": palette.GOLD, "poor": palette.ALERT}.get(
        str(word or ""), palette.MUTED)


def hours(seconds: Any) -> str:
    if not seconds:
        return "?"
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    return (f"{days} d " if days else "") + f"{rest // 3600} h {rest % 3600 // 60} min"


class RouterPanel:
    def __init__(self, parent: tk.Frame, studio: Any) -> None:
        self.parent, self.studio, self.palette = parent, studio, studio.palette
        self.registry = studio.registry
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        self.last_refresh = 0.0
        self.last: dict[str, Any] = {}
        self._build()

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        p, parent = self.palette, self.parent
        parent.grid_columnconfigure(0, weight=1, uniform="router")
        parent.grid_columnconfigure(1, weight=1, uniform="router")
        parent.grid_rowconfigure(1, weight=1)

        router = self.studio._card(parent, "Router & connection")
        router.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(10, 10))
        self.router_name = StringVar(self.parent, "Reading the router…")
        tk.Label(router, textvariable=self.router_name, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 13),
                 anchor="w").pack(fill="x", pady=(4, 2))
        self.rows: dict[str, StringVar] = {}
        for key, title in (("firmware", "Firmware"), ("uptime", "Up for"), ("wifi", "Wi-Fi"), ("signal", "Signal"),
                           ("address", "This PC"), ("dns", "DNS"), ("latency", "Latency")):
            row = tk.Frame(router, bg=p.PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=title, bg=p.PANEL, fg=p.MUTED, width=10, anchor="w", font=("Segoe UI", 9)).pack(side="left")
            variable = StringVar(self.parent, "—")
            tk.Label(row, textvariable=variable, bg=p.PANEL, fg=p.WHITE, anchor="w", font=("Segoe UI", 9),
                     wraplength=420, justify="left").pack(side="left", fill="x", expand=True)
            self.rows[key] = variable
        buttons = tk.Frame(router, bg=p.PANEL)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Refresh", style="Hayat.TButton", width=0, command=self.refresh).pack(side="left")
        self.admin_button = ttk.Button(buttons, text="Open admin page", style="Hayat.TButton", width=0,
                                       command=self._open_admin, state="disabled")
        self.admin_button.pack(side="left", padx=4)

        cellular = self.studio._card(parent, "5G / LTE signal (from the gateway)")
        cellular.grid(row=0, column=1, sticky="nsew", pady=(10, 10))
        top = tk.Frame(cellular, bg=p.PANEL)
        top.pack(fill="x", pady=(4, 2))
        self.bars_label = tk.Label(top, text="", bg=p.PANEL, fg=p.GOLD, font=("Segoe UI Semibold", 15))
        self.bars_label.pack(side="left")
        self.cell_detail = StringVar(self.parent, "")
        tk.Label(top, textvariable=self.cell_detail, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=10)
        self.metrics: dict[str, tk.Label] = {}
        line = tk.Frame(cellular, bg=p.PANEL)
        line.pack(fill="x", pady=(2, 4))
        for key in ("sinr", "rsrp", "rsrq"):
            # Invisible (panel-coloured) until the gateway reports a reading: empty they were three grey boxes.
            label = tk.Label(line, text="", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 9), padx=8, pady=3)
            label.pack(side="left", padx=(0, 6))
            self.metrics[key] = label
        self.chart = tk.Canvas(cellular, width=CHART[0], height=CHART[1], bg=p.BLACK, highlightthickness=0)
        self.chart.pack(fill="x", pady=(4, 0))
        self.cell_note = StringVar(self.parent, "")
        tk.Label(cellular, textvariable=self.cell_note, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8), anchor="w",
                 justify="left", wraplength=460).pack(fill="x", pady=(4, 0))

        air = self.studio._card(parent, "Wi-Fi around you")
        air.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        self.channel_var = StringVar(self.parent, "")
        tk.Label(air, textvariable=self.channel_var, bg=p.PANEL, fg=p.GOLD_SOFT, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=470).pack(fill="x", pady=(2, 4))
        self.nearby = ttk.Treeview(air, columns=("signal", "band", "channel", "load"), show="tree headings", height=6)
        for column, title, width in (("#0", "Network", 170), ("signal", "Signal", 60), ("band", "Band", 70),
                                     ("channel", "Ch", 40), ("load", "Stations · busy", 110)):
            self.nearby.heading(column, text=title)
            self.nearby.column(column, width=width, anchor="w")
        self.nearby.pack(fill="both", expand=True)

        devices = self.studio._card(parent, "Devices on this network")
        devices.grid(row=1, column=1, sticky="nsew", pady=(0, 10))
        device_buttons = tk.Frame(devices, bg=p.PANEL)
        device_buttons.pack(fill="x", pady=(4, 2))
        ttk.Button(device_buttons, text="Name device…", style="Hayat.TButton", width=0,
                   command=self.name_selected).pack(side="left")
        ttk.Button(device_buttons, text="Update vendor names (IEEE, ~4 MB)", style="Hayat.TButton", width=0,
                   command=self.update_vendors).pack(side="left", padx=4)
        self.devices_note = StringVar(self.parent, "")
        tk.Label(devices, textvariable=self.devices_note, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8), anchor="w",
                 justify="left", wraplength=470).pack(fill="x", pady=(2, 4))
        self.devices = ttk.Treeview(devices, columns=("name", "mac", "maker", "state"), show="tree headings", height=6)
        for column, title, width in (("#0", "Address", 105), ("name", "Name", 110), ("mac", "MAC", 125),
                                     ("maker", "Maker", 130), ("state", "Seen", 60)):
            self.devices.heading(column, text=title)
            self.devices.column(column, width=width, anchor="w")
        self.devices.pack(fill="both", expand=True)
        show_empty(self.nearby, "Reading the Wi-Fi networks Windows last saw…", p)
        show_empty(self.devices, "Reading the devices recently seen on this network…", p)
        self.bars_label.configure(text="Reading the gateway…", fg=p.MUTED)
        self._paint_chart()

    # --------------------------------------------------------------- refresh
    def refresh(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.last_refresh = time.monotonic()
        self.studio.status_var.set("Reading the router and network…")

        def job() -> None:
            found: dict[str, Any] = {}
            for tool in ("network_router", "network_nearby_wifi", "network_devices"):
                try:
                    result = self.registry.execute(tool, {})
                    found[tool] = result.output if result.success else {"error": result.error}
                except Exception as exc:
                    log_swallowed(_log, f"running {tool} from the router tab", level=logging.WARNING)
                    found[tool] = {"error": f"{type(exc).__name__}: {exc}"}
            self.results.put(("refresh", found))

        threading.Thread(target=job, name="hayat-router-tab", daemon=True).start()

    def update_vendors(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.studio.status_var.set("Downloading the IEEE maker list…")

        def job() -> None:
            result = self.registry.execute("network_vendor_update", {})
            devices = self.registry.execute("network_devices", {})
            self.results.put(("vendors", {"update": result.output if result.success else {"error": result.error},
                                          "network_devices": devices.output if devices.success else {"error": devices.error}}))

        threading.Thread(target=job, name="hayat-router-vendors", daemon=True).start()

    def poll(self, visible: bool) -> None:
        """Called by the window's poll loop; paints results and refreshes on a timer while visible."""
        while True:
            try:
                kind, found = self.results.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            self.studio.status_var.set("")
            if kind == "vendors":
                update = found.get("update") or {}
                self.studio.status_var.set(update.get("summary") or f"Could not update: {update.get('error')}")
                if not found.get("network_devices", {}).get("error"):
                    self._paint_devices(found["network_devices"])
                continue
            self.last = found
            self.paint(found)
        if visible and not self.busy and time.monotonic() - self.last_refresh >= REFRESH_SECONDS:
            self.refresh()

    # ----------------------------------------------------------------- paint
    def paint(self, found: dict[str, Any]) -> None:
        router_view = found.get("network_router") or {}
        if router_view.get("error"):
            self.router_name.set(f"Could not read the router: {router_view['error']}")
        else:
            self._paint_router(router_view)
        nearby = found.get("network_nearby_wifi") or {}
        if not nearby.get("error"):
            self._paint_nearby(nearby)
        else:
            show_empty(self.nearby, f"Could not read nearby Wi-Fi: {nearby['error']}", self.palette)
        devices = found.get("network_devices") or {}
        if not devices.get("error"):
            self._paint_devices(devices)
        else:
            show_empty(self.devices, f"Could not read the devices: {devices['error']}", self.palette)

    def _paint_router(self, view: dict[str, Any]) -> None:
        p = self.palette
        router, wifi, ip, latency = (view.get("router") or {}, view.get("wifi") or {}, view.get("ip") or {},
                                     view.get("latency_ms") or {})
        if not view.get("connected"):
            self.router_name.set("Not connected to a router.")
            return
        name = " ".join(str(part) for part in (router.get("maker"), router.get("model")) if part) \
            or router.get("admin_title") or router.get("vendor") or "Router"
        self.router_name.set(f"{name} · {router.get('address')}")
        self.rows["firmware"].set_if_changed(f"{router.get('firmware') or 'not published'}"
                                             + (f" ({router['firmware_state']})" if router.get("firmware_state") else ""))
        self.rows["uptime"].set_if_changed(hours(router.get("uptime_s")) if router.get("uptime_s") else "not published")
        self.rows["wifi"].set_if_changed(f"{wifi.get('ssid')} · {wifi.get('band')} · channel {wifi.get('channel')} · "
                                         f"{wifi.get('radio') or ''}" if wifi else "wired, or Wi-Fi off")
        if wifi:
            self.rows["signal"].set_if_changed(f"{wifi.get('signal_percent') or 0:.0f}% · {wifi.get('rssi') or 0:.0f} dBm "
                                               f"({wifi.get('rssi_quality')}) · receiving at {wifi.get('receive_mbps') or '?'} Mbps")
        self.rows["address"].set_if_changed(f"{ip.get('address')}/{ip.get('prefix')} via {ip.get('gateway')}")
        self.rows["dns"].set_if_changed(", ".join(str(item) for item in (ip.get("dns") or [])[:3]) or "—")
        internet = latency.get("internet")
        self.rows["latency"].set_if_changed(f"router {latency.get('router') or '?'} ms · internet "
                                            + (f"{internet} ms" if internet is not None else "unreachable"))
        self.admin_url = router.get("admin_page") if router.get("admin_reachable") else None
        self.admin_button.configure(state="normal" if self.admin_url else "disabled")
        cellular = router.get("cellular") or {}
        radio_name = "5g" if "5g" in cellular else next(iter(cellular), None)
        if not radio_name:
            self.bars_label.configure(text="No cellular data", fg=p.MUTED)
            self.cell_detail.set_if_changed("")
            for label in self.metrics.values():
                label.configure(text="", bg=p.PANEL)
            self.cell_note.set_if_changed("This router does not publish a status page HAYAT can read without a login, "
                                          "or it is not a 5G/LTE gateway.")
        else:
            radio = cellular[radio_name]
            quality = radio.get("quality") or {}
            bars = int(radio.get("bars") or 0)
            self.bars_label.configure(text=f"{radio_name.upper()}  " + "▮" * bars + "▯" * max(0, 5 - bars),
                                      fg=quality_colour(p, quality.get("overall")))
            self.cell_detail.set_if_changed(f"band {', '.join(radio.get('bands') or [])} · cell {radio.get('cid')} · "
                                            f"tower {radio.get('gNBID') or radio.get('eNBID') or '?'} · overall {quality.get('overall')}")
            for key, unit in (("sinr", "dB"), ("rsrp", "dBm"), ("rsrq", "dB")):
                self.metrics[key].configure(text=f"{key.upper()} {radio.get(key)} {unit} · {quality.get(key) or '?'}",
                                            bg=quality_colour(p, quality.get(key)), fg=p.BLACK)
            self.cell_note.set_if_changed("SINR is how clean the signal is (higher is better); RSRP is how strong (closer "
                                          "to 0 is better). Moving the gateway near a window, higher up, often helps both.")
        self._paint_chart()

    def _paint_chart(self) -> None:
        p = self.palette
        canvas = self.chart
        canvas.delete("all")
        width, height = CHART
        history = list(getattr(getattr(self.registry, "network", None), "history", []))[-90:]
        series = [("5g_sinr", p.OLIVE_SOFT, -5, 30, "SINR dB"), ("wifi_rssi", p.HOLO, -90, -30, "Wi-Fi dBm")]
        plotted = False
        for key, colour, low, high, label in series:
            values = [(index, item.get(key)) for index, item in enumerate(history) if item.get(key) is not None]
            if len(values) < 2:
                continue
            step = width / max(1, len(history) - 1)
            points = []
            for index, value in values:
                share = (max(low, min(high, value)) - low) / (high - low)
                points += [index * step, height - 6 - share * (height - 18)]
            canvas.create_line(*points, fill=colour, width=2)
            canvas.create_text(width - 6, 6 + 12 * series.index((key, colour, low, high, label)), text=label, fill=colour,
                               anchor="ne", font=("Segoe UI", 8))
            plotted = True
        if not plotted:
            canvas.create_text(width // 2, height // 2, text="History appears after a few refreshes (every 20 s).",
                               fill=p.MUTED, font=("Segoe UI", 9))

    def _paint_nearby(self, view: dict[str, Any]) -> None:
        self.channel_var.set_if_changed((view.get("channel") or {}).get("summary") or "")
        self.nearby.delete(*self.nearby.get_children())
        mine = (view.get("mine") or {}).get("bssid")
        for index, row in enumerate(view.get("networks") or []):
            load = " · ".join(part for part in (f"{row['stations']} on it" if row.get("stations") is not None else "",
                                                 f"{row['utilization_percent']}% busy" if row.get("utilization_percent") is not None
                                                 else "") if part)
            self.nearby.insert("", "end", iid=f"n{index}", text=("● " if row.get("bssid") == mine else "") + str(row.get("ssid")),
                               values=(f"{row.get('signal_percent', '?')}%", row.get("band") or "", row.get("channel") or "",
                                       load or "—"))
        show_empty(self.nearby, "Windows reported no Wi-Fi networks in its last scan. Press Refresh to look again.",
                   self.palette)

    def _paint_devices(self, view: dict[str, Any]) -> None:
        self.devices_note.set_if_changed(f"{view.get('summary', '')} {view.get('note', '')}".strip())
        self.devices.delete(*self.devices.get_children())
        for index, row in enumerate(view.get("devices") or []):
            maker = ("router" if row.get("role") == "router" else "") or row.get("vendor") or \
                ("private (randomised) address" if row.get("private_address") else "unknown maker")
            if row.get("role") == "router" and row.get("vendor"):
                maker = f"router · {row['vendor']}"
            self.devices.insert("", "end", iid=f"d{index}", text=row.get("ip", ""),
                                values=(row.get("name") if row.get("role") != "router" else "router", row.get("mac", ""),
                                        maker, row.get("state", "")))
        show_empty(self.devices, "No devices seen yet. They appear as they talk on the network; press Refresh in a minute.",
                   self.palette)

    def name_selected(self) -> None:
        from tkinter import simpledialog

        selected = self.devices.selection()
        if not selected:
            self.studio.status_var.set("Select a device in the list first.")
            return
        address = self.devices.item(selected[0], "text")
        name = simpledialog.askstring("Name device", f"A name for {address} (for example 'living room TV'):",
                                      parent=self.parent.winfo_toplevel())
        if not name:
            return
        result = self.registry.execute("network_name_device", {"device": address, "name": name})
        self.studio.status_var.set(result.output["summary"] if result.success else f"Could not name it: {result.error}")
        if result.success:
            self.refresh()

    def _open_admin(self) -> None:
        url = getattr(self, "admin_url", None)
        if url:
            webbrowser.open(url)


# --------------------------------------------------------- network guard
#: The main window checks the network this often, starting a minute after it opens.
GUARD_SECONDS = 120


def guard_tick(gui: Any) -> None:
    """From the main window's tick: run network_guard off the Tk thread when due; announce new devices once."""
    state = getattr(gui, "_network_guard", None)
    if state is None:
        state = gui._network_guard = {"next": time.monotonic() + 60, "busy": False, "results": queue.Queue()}
    while True:
        try:
            output = state["results"].get_nowait()
        except queue.Empty:
            break
        state["busy"] = False
        for row in (output or {}).get("new") or []:
            maker = row.get("vendor") or ("a private (randomised) address, usually a phone" if row.get("private_address")
                                          else "unknown maker")
            gui._append("system", f"New device on your network: {row.get('ip')} ({maker}, MAC {row.get('mac')}). "
                                  "If you know it, say 'name device {0} as …'.".format(row.get("ip")))
    registry = gui.runtime.registry
    if (state["busy"] or time.monotonic() < state["next"] or "network_guard" not in registry.names()
            or gui.runtime.authority.privacy_mode):
        return
    state["busy"], state["next"] = True, time.monotonic() + GUARD_SECONDS

    def job() -> None:
        try:
            result = registry.execute("network_guard", {})
            state["results"].put(result.output if result.success else {})
        except Exception:
            log_swallowed(_log, "checking the network for new devices")
            state["results"].put({})

    threading.Thread(target=job, name="hayat-network-guard", daemon=True).start()
