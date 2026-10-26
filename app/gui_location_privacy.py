"""Studios → Location privacy: who can learn where you are, what HAYAT does so it is not through HAYAT,
and the masks (a VPN checked for leaks, Tor for HAYAT, the location guard; app/privacy_masks.py).

The check (location_privacy_check, app/location_privacy.py) runs through the tool registry off the Tk
thread. Findings are listed by how much they matter, each with what to do; the right side shows the
protections HAYAT applies and the guard's latest holds, roundings and stripped photos.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any

from app.gui_resources import StringVar
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY

WATCH, INFO = "#F2A541", "#7D8B99"


def _age(minutes: float) -> str:
    """'just now', '4 min ago', '2 h ago' for how old the held location fix is."""
    if minutes < 1:
        return "just now"
    return f"{minutes:.0f} min ago" if minutes < 90 else f"{minutes / 60:.0f} h ago"


class LocationPrivacyWindow(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.registry = gui.runtime.registry
        self._results: queue.Queue = queue.Queue()
        self._busy = False
        self._queued: list[tuple[str, str, dict[str, Any]]] = []
        self.report: dict[str, Any] | None = None
        self.title("HAYAT · Location privacy")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        self._build()
        self._show_local_state()
        place_over(self, gui.root, (1060, 680))
        self.deiconify()
        self.lift()
        self.check()
        self._poll()

    def _show_local_state(self) -> None:
        """What is known without reading anything: the guard, the place names and the protections."""
        from app.location_privacy import SHIELD, PlaceIndex

        places = PlaceIndex().available()
        guard = SHIELD.status()
        self._chip("guard", "Guard on" if guard["guard_on"] else "Guard off", "#4ADE80")
        self._chip("places", "Place names: on this computer" if places else "Place names: not set up",
                   "#4ADE80" if places else WATCH)
        self._chip("fix", "Last fix: in memory, " + _age(guard['fix_age_minutes']) if guard["fix_known"]
                   else "No fix held", self.palette.WHITE)
        held = sum(1 for event in guard["recent"] if event["action"] == "held")
        self._chip("held", f"Held: {held}", WATCH if held else self.palette.WHITE)
        self._places_ready(places)
        self._write(self.protections, [("Reading what HAYAT has done recently…", "fix")])

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        from app.gui_panels import ButtonRow
        from app.gui_theme import DarkScrolledText

        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="Location privacy", bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 16)).pack(side="left")
        tk.Label(header, text="Location protections · Tor readiness · exposure checks and coverage limits",
                 bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=10, pady=(5, 0))
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")
        self.strip = tk.Frame(self, bg=p.BLACK, padx=16)
        self.strip.pack(fill="x")
        self.chips: dict[str, tk.Label] = {}
        for key in ("guard", "places", "fix", "held"):
            chip = tk.Label(self.strip, text="…", bg=p.PANEL_2, fg=p.WHITE, font=("Segoe UI", 9), padx=10, pady=4)
            chip.pack(side="left", padx=(0, 6), pady=(0, 8))
            self.chips[key] = chip
        actions = ButtonRow(self, p)
        actions.pack(fill="x", padx=16, pady=(0, 8))
        self.check_button = actions.add("Check again", self.check)
        self.places_button = actions.add("Set up place names (2 MB)", self.setup_places)
        self.share_button = actions.add("Share my location once…", self.share_once)
        self.photo_button = actions.add("Remove location from a photo…", self.strip_photo)
        self._build_masks()
        body = tk.PanedWindow(self, orient="horizontal", bg=p.BLACK, sashwidth=7, bd=0)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        left = tk.Frame(body, bg=p.BLACK)
        right = tk.Frame(body, bg=p.BLACK)
        body.add(left, minsize=420, width=620)
        body.add(right, minsize=300)
        tk.Label(left, text="Who can learn where you are", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 11),
                 anchor="w").pack(fill="x")
        self.findings = DarkScrolledText(left, bg=p.PANEL, fg=p.WHITE, wrap="word", font=("Segoe UI", 10), padx=12, pady=10)
        self.findings.pack(fill="both", expand=True, pady=(4, 0))
        tk.Label(right, text="What HAYAT does to keep it private", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 11),
                 anchor="w").pack(fill="x")
        self.protections = DarkScrolledText(right, bg=p.PANEL, fg=p.WHITE, wrap="word", font=("Segoe UI", 10), padx=12,
                                            pady=10, height=10)
        self.protections.pack(fill="both", expand=True, pady=(4, 0))
        for widget in (self.findings, self.protections):
            widget.tag_configure("watch", foreground=WATCH, font=("Segoe UI Semibold", 10), spacing1=8)
            widget.tag_configure("info", foreground=p.WHITE, font=("Segoe UI Semibold", 10), spacing1=8)
            widget.tag_configure("fix", foreground=p.MUTED, lmargin1=18, lmargin2=18)
            widget.tag_configure("ok", foreground="#4ADE80")
            widget.tag_configure("label", foreground=p.MUTED, font=("Segoe UI Semibold", 9), spacing1=10)
            widget.configure(state="disabled")
        self.status = StringVar(self, "Checking Windows, your browsers, your internet address and your photos…")
        tk.Label(self, textvariable=self.status, bg=p.PANEL_2, fg=p.GOLD_SOFT, anchor="w", justify="left", padx=12, pady=6,
                 wraplength=1000).pack(fill="x", side="bottom")
        self._write(self.findings, [("Checking Windows, your browsers, your internet address and your photos…", "fix")])

    #: The three masks, in the order they cover more: the whole computer, HAYAT's own traffic, what HAYAT sends.
    MASKS = (("tor", "Tor · HAYAT's web reading"), ("browser", "Masked browser · your browsing"),
             ("guard", "Location guard · what HAYAT sends"))

    def _build_masks(self) -> None:
        p = self.palette
        frame = tk.Frame(self, bg=p.BLACK, padx=16)
        frame.pack(fill="x", pady=(0, 8))
        top = tk.Frame(frame, bg=p.BLACK)
        top.pack(fill="x")
        tk.Label(top, text="Masks", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 11)).pack(side="left")
        self.masks_count = tk.Label(top, text="checking…", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9))
        self.masks_count.pack(side="left", padx=8)
        self.masks_test_button = ttk.Button(top, text="Test again", style="Hayat.TButton", command=self.masks_check)
        self.masks_test_button.pack(side="right")
        cards = tk.Frame(frame, bg=p.BLACK)
        cards.pack(fill="x", pady=(4, 0))
        self.mask_cards: dict[str, dict[str, Any]] = {}
        for index, (key, title) in enumerate(self.MASKS):
            card = tk.Frame(cards, bg=p.PANEL, padx=12, pady=8, highlightthickness=1, highlightbackground=p.LINE)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0))
            cards.columnconfigure(index, weight=1, uniform="mask")
            light = tk.Label(card, text="○ " + title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 10), anchor="w")
            light.pack(fill="x")
            state = tk.Label(card, text="Checking…", bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w",
                             justify="left", wraplength=320)
            state.pack(fill="x", pady=(2, 0))
            buttons = tk.Frame(card, bg=p.PANEL)
            buttons.pack(fill="x", pady=(6, 0))
            self.mask_cards[key] = {"card": card, "light": light, "state": state, "buttons": buttons, "title": title}
        self.seen = StringVar(self, "")
        tk.Label(frame, textvariable=self.seen, bg=p.BLACK, fg=p.GOLD_SOFT, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=1000).pack(fill="x", pady=(6, 0))

    def _card_buttons(self, key: str, buttons: list[tuple[str, Any]]) -> None:
        holder = self.mask_cards[key]["buttons"]
        for child in holder.winfo_children():
            child.destroy()
        for text, command in buttons:
            ttk.Button(holder, text=text, style="Hayat.TButton", command=command).pack(side="left", padx=(0, 6))

    def show_masks(self, report: dict[str, Any]) -> None:
        """The three masks with what each covers now, the leaks, and what websites see of the computer and of HAYAT."""
        self.masks_report = report
        layers = {key: layer for (key, _title), layer in zip(self.MASKS, report.get("layers") or [])}
        for key, card in self.mask_cards.items():
            layer = layers.get(key) or {}
            on = bool(layer.get("on"))
            card["light"].configure(text=("● " if on else "○ ") + card["title"], fg="#4ADE80" if on else WATCH)
            card["state"].configure(text=layer.get("detail", ""), fg=self.palette.WHITE if on else self.palette.MUTED)
        tor = report.get("tor") or {}
        self._card_buttons("browser", [("Open masked Edge", lambda: self.open_browser("edge")),
                                       ("Open masked Chrome", lambda: self.open_browser("chrome"))] if tor.get("masked")
                           else [])
        if tor.get("routing_required") or tor.get("masked"):
            actions = [("Unmask HAYAT", self.mask_off)]
            if not tor.get("masked"):
                actions.append(("Retry Tor", self.mask_on) if tor.get("installed") else ("Set up Tor (21 MB)", self.tor_setup))
        else:
            actions = [("Mask HAYAT with Tor", self.mask_on)] if tor.get("installed") else [("Set up Tor (21 MB)", self.tor_setup)]
        self._card_buttons("tor", actions)
        self.masks_count.configure(text=f"{report.get('masks_on', 0)} of {len(self.MASKS)} on",
                                   fg="#4ADE80" if report.get("masks_on") == len(self.MASKS) else WATCH)
        computer, hayat = report.get("computer_seen_as") or {}, report.get("hayat_seen_as") or {}
        parts = []
        if computer.get("checked"):
            parts.append(f"Websites see your computer near {computer['place']}"
                         + (f" ({computer['network']})" if computer.get("network") else ""))
        if hayat.get("checked"):
            parts.append(f"and HAYAT near {hayat['place']}, through Tor")
        vpn = report.get("vpn") or {}
        leaks = [row["what"] for row in report.get("leaks") or [] if row.get("level") == "watch" and vpn.get("connected")]
        if vpn.get("connected"):
            parts.append("· A VPN is connected" + (": " + " ".join(leaks) if leaks else ", with no leak found"))
        uncovered = (report.get("coverage") or {}).get("not_verified") or []
        coverage = " Coverage not verified: " + ", ".join(uncovered) + "." if uncovered else ""
        self.seen.set((" ".join(parts) + ". " if parts else "") + report.get("limits", "") + coverage)

    def masks_check(self) -> None:
        self.masks_count.configure(text="checking…", fg=self.palette.MUTED)
        self._run("masks", "privacy_masks_status", {})

    def tor_setup(self) -> None:
        self.status.set("Downloading Tor (about 21 MB, checked against its published SHA-256 and by Windows Defender)…")
        self._run("tor", "tor_setup", {})

    def mask_on(self) -> None:
        self.status.set("Connecting to Tor through three relays… (usually about 15 seconds)")
        self._run("mask", "hayat_mask_on", {})

    def mask_off(self) -> None:
        self.status.set("Sending HAYAT's web reading out directly again…")
        self._run("mask", "hayat_mask_off", {})

    def open_browser(self, browser: str) -> None:
        self.status.set(f"Opening a masked {browser.title()} window through Tor…")
        self._run("browser", "masked_browser_open", {"browser": browser})

    @staticmethod
    def _write(widget: tk.Text, parts: list[tuple[str, str | None]]) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for text, tag in parts:
            widget.insert("end", text, tag or ())
        widget.configure(state="disabled")

    # ------------------------------------------------------------------ actions
    def _run(self, mode: str, tool: str, arguments: dict[str, Any]) -> None:
        if self._busy:
            # Queued, not dropped: a click while the checks run used to do nothing at all.
            if (mode, tool, arguments) not in self._queued:
                self._queued.append((mode, tool, arguments))
            return
        self._busy = True
        for button in (self.check_button, self.places_button, self.share_button, self.photo_button, *self._mask_buttons()):
            button.state(["disabled"])

        def work() -> None:
            try:
                result = self.registry.execute(tool, arguments)
                self._results.put((mode, result.output if result.success else None, result.error))
            except Exception as exc:
                self._results.put((mode, None, f"{type(exc).__name__}: {exc}"))
        threading.Thread(target=work, name="hayat-location-privacy", daemon=True).start()

    def _mask_buttons(self) -> list[ttk.Button]:
        cards = [child for card in getattr(self, "mask_cards", {}).values() for child in card["buttons"].winfo_children()
                 if isinstance(child, ttk.Button)]
        return cards + ([self.masks_test_button] if hasattr(self, "masks_test_button") else [])

    def check(self) -> None:
        self.status.set("Checking Windows, your browsers, your internet address and your photos…")
        self._run("check", "location_privacy_check", {})

    def setup_places(self) -> None:
        self.status.set("Downloading the place names (about 2 MB, checked against its published hash)…")
        self._run("places", "location_places_setup", {})

    def share_once(self) -> None:
        self.status.set("Asking you to approve sharing your location in the next message…")
        self._run("share", "location_share_once", {})

    def strip_photo(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Photo to share without its location",
                                          filetypes=[("Photos", "*.jpg *.jpeg *.tif *.tiff *.png *.webp"), ("All files", "*.*")])
        if path:
            self.status.set("Removing the location from the photo…")
            self._run("photo", "photo_location_strip", {"path": path})

    # ------------------------------------------------------------------ showing
    def show(self, report: dict[str, Any]) -> None:
        p = self.palette
        self.report = report
        findings = report.get("findings") or []
        self._show_findings(findings)
        guard = report.get("guard") or {}
        protections: list[tuple[str, str | None]] = [(f"✓  {line}\n", None) for line in report.get("protections") or []]
        recent = guard.get("recent") or []
        protections.append(("\nWhat the guard did recently\n", "label"))
        protections += ([(f"{event['at'][11:16]}  {event['action']}: {event['detail']} ({event['tool']})\n", "fix")
                         for event in reversed(recent)] or [("Nothing held, rounded or stripped yet.\n", "fix")])
        self._write(self.protections, protections)
        watch = sum(1 for row in findings if row.get("level") == "watch")
        held = sum(1 for event in recent if event["action"] == "held")
        places = any("on this computer;" in line for line in report.get("protections") or [])
        self._chip("guard", "Guard on" if guard.get("guard_on", True) else "Guard off", "#4ADE80")
        self._chip("places", "Place names: on this computer" if places else "Place names: not set up",
                   "#4ADE80" if places else WATCH)
        self._chip("fix", ("Last fix: in memory, " + _age(guard['fix_age_minutes']) if guard.get("fix_known")
                           else "No fix held"), p.WHITE)
        self._chip("held", f"Held: {held}", WATCH if held else p.WHITE)
        self._places_ready(places)
        self.status.set(f"{watch} thing{'s' if watch != 1 else ''} to look at. Read from this computer's settings; "
                        "nothing was changed and no location was sent.")

    #: The button under a finding that has a page to fix it.
    ACTION_LABELS = {"windows": "Open Windows location settings", "edge": "Open Edge location permissions",
                     "chrome": "Open Chrome location permissions", "brave": "Open Brave location permissions",
                     "opera": "Open Opera location permissions", "firefox": "Open Firefox privacy settings"}

    def _show_findings(self, findings: list[dict[str, Any]]) -> None:
        """Each finding, what to do, and -- where there is a page for it -- a button that opens that page."""
        for button in getattr(self, "action_buttons", []):
            button.destroy()  # embedded windows outlive the text they sat in
        self.action_buttons: list[ttk.Button] = []
        widget = self.findings
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if not findings:
            widget.insert("end", "Nothing found that gives your location away.\n", "ok")
        seen: set[str] = set()
        for row in sorted(findings, key=lambda row: row.get("level") != "watch"):
            widget.insert("end", ("●  " if row.get("level") == "watch" else "○  ") + row["what"] + "\n",
                          row.get("level") or "info")
            widget.insert("end", row["fix"] + "\n", "fix")
            action = row.get("action")
            if action in self.ACTION_LABELS and action not in seen:  # one button per page, under its first finding
                seen.add(action)
                button = ttk.Button(widget, text=self.ACTION_LABELS[action], style="Hayat.TButton",
                                    command=lambda target=action: self.open_setting(target))
                widget.insert("end", "    ", "fix")
                widget.window_create("end", window=button, padx=2, pady=4)
                widget.insert("end", "\n")
                self.action_buttons.append(button)
        widget.configure(state="disabled")

    def open_setting(self, target: str) -> None:
        self.status.set("Opening the setting…")
        self._run("open", "location_open_setting", {"target": target})

    def _places_ready(self, ready: bool) -> None:
        self.places_button.configure(text="Place names set up ✓" if ready else "Set up place names (2 MB)")
        self.places_button.state(["disabled"] if ready else ["!disabled"])

    def _chip(self, key: str, text: str, colour: str) -> None:
        self.chips[key].configure(text=text, fg=colour)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                mode, output, error = self._results.get_nowait()
                self._busy = False
                for button in (self.check_button, self.share_button, self.photo_button, *self._mask_buttons()):
                    button.state(["!disabled"])
                if not (self.report and any("on this computer;" in line for line in self.report.get("protections") or [])):
                    self.places_button.state(["!disabled"])
                if error:
                    self.status.set(("Not approved: " if mode == "share" else "Could not finish: ") + str(error)[:300])
                elif mode == "check":
                    self.show(output)
                    self.masks_check()  # then the masks, which ask Windows and one website
                elif mode == "masks":
                    self.show_masks(output)
                elif mode == "tor":
                    self.status.set("Tor is set up. Press Mask HAYAT with Tor." if output.get("installed")
                                    else f"Tor was not set up: {output.get('reason')}")
                    self.masks_check()
                elif mode == "mask":
                    self.status.set(output.get("note") or output.get("reason") or "")
                    self.masks_check()
                elif mode == "browser":
                    self.status.set(output.get("note") or output.get("reason") or "")
                    self.masks_check()
                elif mode == "places":
                    self.status.set("Place names are set up on this computer." if output.get("installed")
                                    else f"Place names were not installed: {output.get('reason')}")
                    self.check()
                elif mode == "share":
                    self.status.set(output.get("note", "Your next message may include your location."))
                elif mode == "open":
                    self.status.set(output.get("note", ""))
                elif mode == "photo":
                    self.status.set(f"Saved without its location: {output['path']}" if output.get("stripped")
                                    else output.get("note", ""))
        except queue.Empty:
            pass
        if not self._busy and self._queued:
            self._run(*self._queued.pop(0))
        self.after(150, self._poll)
