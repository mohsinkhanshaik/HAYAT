"""Responsive security reports, owner ban controls and isolated defense checks."""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable

from app.gui_icons import apply_icons
from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY


def _ban_store() -> Path:
    from app.config import PROJECT_ROOT

    return PROJECT_ROOT / "data" / "security" / "intrusion-bans.json"


class SecurityStudio(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.results: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._closed = False
        self._poll_id = None
        self._next_refresh = 0.0
        self._ban_rows = None
        self.busy = False
        self.title("HAYAT · Security")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.minsize(620, 460)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        self._build()
        apply_icons(self, self.palette)
        place_over(self, gui.root, (900, 720))
        self.deiconify()
        self.lift()
        self.refresh_bans()
        self.scan()
        self._poll()

    def _shield(self):
        """Live shield, or a read of saved bans; never start remote access here."""
        server = getattr(getattr(self.gui, "_remote_access", None), "server", None)
        if server is not None and getattr(server, "shield", None) is not None:
            return server.shield
        from app.security.intrusion import IntrusionShield

        return IntrusionShield(_ban_store())

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="Security", bg=p.BLACK, fg=p.WHITE,
                 font=(DISPLAY, 16)).pack(side="left")
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")
        self.overview = StringVar(self, "Reading local defenses…")
        summary = tk.Label(self, textvariable=self.overview, bg=p.BLACK, fg=p.GOLD_SOFT,
                           font=("Segoe UI", 10), anchor="w", justify="left", padx=16)
        summary.pack(fill="x", pady=(0, 10))
        self.bind("<Configure>", lambda event: summary.configure(wraplength=max(200, event.width - 40))
                  if event.widget is self else None, add="+")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        blocked = self._tab("Remote defense")
        self._label(blocked, "Blocked sources", heading=True)
        self._label(blocked, "Suspicious remote sources can be blocked or receive inert synthetic read responses. "
                    "Writes remain denied. Select a row to inspect its full reason or lift the ban.")
        table = tk.Frame(blocked, bg=p.PANEL)
        table.pack(fill="both", expand=True, pady=8)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.bans = ttk.Treeview(table, columns=("reason", "left", "count", "decoy"),
                                show="tree headings", height=8, selectmode="browse")
        for column, title, width in (("#0", "Source", 170), ("reason", "Reason", 310),
                                     ("left", "Time left", 100), ("count", "Bans", 60), ("decoy", "Decoy", 70)):
            self.bans.heading(column, text=title)
            self.bans.column(column, width=width, minwidth=60, anchor="w")
        self.bans.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.bans.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.bans.xview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.bans.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.bans.bind("<<TreeviewSelect>>", self._select_ban)
        self.ban_detail = StringVar(self, "Select a source to see the complete recorded reason.")
        detail = tk.Label(blocked, textvariable=self.ban_detail, bg=p.PANEL, fg=p.MUTED,
                          anchor="w", justify="left", wraplength=560)
        detail.pack(fill="x")
        row = tk.Frame(blocked, bg=p.PANEL)
        row.pack(fill="x", pady=(8, 0))
        self.unban_button = ttk.Button(row, text="Lift selected ban", style="Hayat.TButton", command=self.unban)
        self.unban_button.pack(side="left")
        ttk.Button(row, text="Refresh", style="Hayat.TButton", command=self.refresh_bans).pack(side="right")
        self.ban_note = StringVar(self, "Loading saved or live bans…")
        note = tk.Label(blocked, textvariable=self.ban_note, bg=p.PANEL, fg=p.GOLD_SOFT,
                        anchor="w", justify="left", wraplength=560)
        note.pack(fill="x", pady=6)
        blocked.bind("<Configure>", lambda e: [w.configure(wraplength=max(200, e.width - 32))
                                                for w in (detail, note)], add="+")

        watch = self._tab("Machine scan")
        self._label(watch, "What is watching this machine", heading=True)
        self.scan_button = ttk.Button(watch, text="Re-scan this machine", style="Hayat.TButton", command=self.scan)
        self.scan_button.pack(anchor="w", pady=6)
        self.scan_text = self._report(watch)

        checks = self._tab("Poisoning & decoys")
        self._label(checks, "Test the defenses locally", heading=True)
        self._label(checks, "Checks use a separate in-memory shield and synthetic source text. "
                    "They do not contact a hacker, change your bans, train a model or read real keys. "
                    "External instructions never grant tool permissions. Pattern detection can miss attacks.")
        self.check_button = ttk.Button(checks, text="Run local defense checks", style="Hayat.TButton",
                                       command=self.check_defenses)
        self.check_button.pack(anchor="w", pady=8)
        self.check_text = self._report(checks)
        self._show(self.check_text, "No local checks have been run in this window.")

        footer = tk.Frame(self, bg=p.BLACK, padx=16, pady=6)
        footer.pack(fill="x", side="bottom")
        self.status_var = StringVar(self, "")
        tk.Label(footer, textvariable=self.status_var, bg=p.BLACK, fg=p.MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=120,
                                       style="Hayat.Horizontal.TProgressbar")

    def _tab(self, title: str) -> tk.Frame:
        frame = tk.Frame(self.tabs, bg=self.palette.PANEL, padx=12, pady=10)
        self.tabs.add(frame, text=title)
        return frame

    def _label(self, parent, text: str, *, heading: bool = False) -> None:
        label = tk.Label(parent, text=text, bg=self.palette.PANEL,
                         fg=self.palette.WHITE if heading else self.palette.MUTED,
                         font=("Segoe UI Semibold", 12) if heading else ("Segoe UI", 10),
                         anchor="w", justify="left", wraplength=560)
        label.pack(fill="x", pady=(0, 6))
        parent.bind("<Configure>", lambda event: label.configure(wraplength=max(200, event.width - 32)), add="+")

    def _report(self, parent):
        text = DarkScrolledText(parent, wrap="word", bg=self.palette.BLACK, fg=self.palette.WHITE,
                                font=("Segoe UI", 10), padx=12, pady=10, relief="flat", state="disabled")
        text.pack(fill="both", expand=True, pady=(6, 0))
        return text

    @staticmethod
    def _show(widget, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def _work(self, kind: str, operation: Callable[[], Any]) -> None:
        if self._closed or kind in self._pending:
            return
        self._pending.add(kind)
        self._activity()
        results = self.results

        def job() -> None:
            try:
                results.put((kind, operation(), None))
            except Exception as exc:
                results.put((kind, None, exc))

        threading.Thread(target=job, name=f"hayat-security-{kind}", daemon=True).start()

    def _activity(self) -> None:
        self.busy = bool(self._pending)
        self.scan_button.configure(state="disabled" if "scan" in self._pending else "normal")
        self.check_button.configure(state="disabled" if "checks" in self._pending else "normal")
        self.unban_button.configure(state="disabled" if "unban" in self._pending else "normal")
        labels = {"scan": "Scanning this machine", "checks": "Checking local defenses",
                  "bans": "Refreshing bans", "unban": "Lifting selected ban"}
        self.status_var.set(" · ".join(labels[k] for k in sorted(self._pending)))
        if self.busy:
            self.progress.pack(side="right")
            self.progress.start(20)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def refresh_bans(self) -> None:
        self._next_refresh = time.monotonic() + 5
        self._work("bans", lambda: self._shield().status())

    def _render_bans(self, status) -> None:
        live = getattr(getattr(self.gui, "_remote_access", None), "server", None)
        running = live is not None and getattr(live, "shield", None) is not None
        mode = "Live remote shield" if running else "Saved ban snapshot (remote shield not running)"
        count = status.get("decoy_responses", 0)
        activity = f"{count} decoy response{'s' if count != 1 else ''} in this shield session" if running else "Activity counters unavailable while remote access is off"
        self.overview.set(f"{mode}\n{status['banned_count']} blocked · {status['watched_sources']} scored sources · "
                          + activity)
        rows = []
        for entry in status["banned"]:
            left = entry["seconds_left"]
            human = f"{left // 3600} h {left % 3600 // 60} m" if left >= 3600 else f"{max(1, left // 60)} m"
            rows.append((entry["source"], "; ".join(entry["reasons"]), human, entry["bans"],
                         ("Sent" if entry.get("decoy_sent") else "—") if running else "Unknown"))
        if rows != self._ban_rows:
            selected, position = self.bans.selection(), self.bans.yview()
            self.bans.delete(*self.bans.get_children())
            for source, *values in rows:
                self.bans.insert("", "end", iid=source, text=source, values=values)
            if selected and self.bans.exists(selected[0]):
                self.bans.selection_set(selected[0])
            if position:
                self.bans.yview_moveto(position[0])
            self._ban_rows = rows
            self._select_ban()
        self.ban_note.set(status.get("store_error") or ("No active bans in this snapshot. This is not an attack history."
                          if not rows else f"{len(rows)} active bans. Trusted owner networks are exempt from scoring."))

    def _select_ban(self, _event=None) -> None:
        selected = self.bans.selection()
        self.ban_detail.set(f"{selected[0]}: {self.bans.item(selected[0], 'values')[0]}" if selected else
                            "Select a source to see the complete recorded reason.")

    def unban(self) -> None:
        selected = self.bans.selection()
        if not selected:
            self.ban_note.set("Select a source to unban.")
            return
        source = selected[0]
        self._work("unban", lambda: (source, self._shield().unban(source)))

    def scan(self) -> None:
        from app.safety import watch

        self._work("scan", watch.summary)

    def check_defenses(self) -> None:
        from app.security.diagnostics import run_defense_checks

        self.tabs.select(2)
        self._work("checks", run_defense_checks)

    def _poll(self) -> None:
        if self._closed:
            return
        self._poll_id = None
        while True:
            try:
                kind, value, error = self.results.get_nowait()
            except queue.Empty:
                break
            self._pending.discard(kind)
            if error is not None:
                self.gui._record_failure(f"Security {kind}", error)
                message = f"Could not complete {kind}: {type(error).__name__}. See diagnostics."
                if kind in {"bans", "unban"}:
                    self.ban_note.set(message)
                else:
                    self._show(self.scan_text if kind == "scan" else self.check_text, message)
            elif kind == "bans":
                self._render_bans(value)
            elif kind == "unban":
                self.ban_note.set(f"Lifted the block on {value[0]}." if value[1] else "This ban is no longer active.")
                self._next_refresh = 0
            elif kind == "scan":
                self._show(self.scan_text, f"Snapshot taken at {time.strftime('%H:%M:%S')}\n\n{value}")
            elif kind == "checks":
                from app.security.diagnostics import format_defense_checks

                self._show(self.check_text, format_defense_checks(value))
        if time.monotonic() >= self._next_refresh:
            self.refresh_bans()
        self._activity()
        self._poll_id = self.after(100, self._poll)

    def destroy(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        self.progress.stop()
        super().destroy()
