"""Studios → Keys: add the free developer keys that unlock more cameras and exact stock prices.

Each row says what the key unlocks, opens the provider's free sign-up page, takes the key
(hidden as it is typed), saves it to the local .env, and tests it for real.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

from app import keys
from app.gui_resources import StringVar
from app.gui_fonts import DISPLAY

INTRO = ("Each key is a free sign-up you do yourself with the provider (HAYAT cannot register for you). Press Get a free key, "
         "copy the key they give you, paste it here and press Save & test. Keys stay in this computer's .env file and are never "
         "shown again in full.")


class KeysWindow(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p = gui, type(gui)
        p = self.p
        self.title("HAYAT · Keys")
        self.configure(bg=p.BLACK)
        self.minsize(820, 560)
        self.bind("<Escape>", lambda _event: self.destroy())
        tk.Label(self, text="Keys · unlock more", bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 16), anchor="w").pack(
            fill="x", padx=16, pady=(14, 2))
        tk.Label(self, text=INTRO, bg=p.BLACK, fg=p.MUTED, wraplength=820, justify="left", anchor="w").pack(fill="x", padx=16)
        holder = tk.Frame(self, bg=p.BLACK)
        holder.pack(fill="both", expand=True, padx=16, pady=10)
        canvas = tk.Canvas(holder, bg=p.BLACK, highlightthickness=0)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.list = tk.Frame(canvas, bg=p.BLACK)
        window = canvas.create_window(0, 0, window=self.list, anchor="nw")
        self.list.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        from app.gui_motion import attach
        attach(canvas)
        self.rows: dict[str, dict[str, Any]] = {}
        self._results: queue.Queue = queue.Queue()  # test outcomes from worker threads, shown by the UI thread
        group = None
        for item in keys.status():
            if item["group"] != group:
                group = item["group"]
                tk.Label(self.list, text=group.upper(), bg=p.BLACK, fg=p.GOLD, font=("Segoe UI Semibold", 9), anchor="w").pack(
                    fill="x", pady=(10, 2))
            self._row(item)
        from app.gui_icons import apply_icons
        from app.gui_windows import place_over
        apply_icons(self, p)
        place_over(self, gui.root, (900, 700))
        self.deiconify()

    def _row(self, item: dict[str, Any]) -> None:
        p = self.p
        frame = tk.Frame(self.list, bg=p.PANEL, padx=10, pady=8)
        frame.pack(fill="x", pady=3)
        top = tk.Frame(frame, bg=p.PANEL)
        top.pack(fill="x")
        tk.Label(top, text=item["title"], bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 10), anchor="w").pack(side="left")
        state = StringVar(self, item["state"])
        tk.Label(top, textvariable=state, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="right")
        tk.Label(frame, text=item["unlocks"], bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left").pack(fill="x")
        controls = tk.Frame(frame, bg=p.PANEL)
        controls.pack(fill="x", pady=(4, 0))
        value = StringVar(self, "")
        ttk.Entry(controls, textvariable=value, show="•", width=40).pack(side="left", fill="x", expand=True)
        for label, command, icon in (("Get a free key", lambda: self.gui._open_source(item["signup"]), "link"),
                                     ("Save & test", lambda: self.save(item["name"]), "check"),
                                     ("Remove", lambda: self.remove(item["name"]), "stop")):
            button = ttk.Button(controls, text=label, style="Compact.Hayat.TButton", command=command)
            button._hayat_icon = icon
            button.pack(side="left", padx=(6, 0))
        self.rows[item["name"]] = {"value": value, "state": state}

    def save(self, name: str) -> bool:
        row = self.rows[name]
        try:
            state = keys.save(name, row["value"].get())
        except ValueError as error:
            row["state"].set(str(error))
            return False
        finally:
            row["value"].set("")  # never left in the window
        row["state"].set(state + " · testing…")

        def work():
            try:
                outcome = keys.test(name, self.gui.runtime)
            except Exception as error:  # the key is saved; the test result is what failed
                outcome = f"Saved; the test could not run ({type(error).__name__}: {str(error)[:120]})"
            self._results.put((name, state + " · " + outcome))
        threading.Thread(target=work, name="hayat-key-test", daemon=True).start()
        self.after(100, self._poll)
        return True

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        while not self._results.empty():
            name, text = self._results.get_nowait()
            self.rows[name]["state"].set(text)
        if any("testing…" in row["state"].get() for row in self.rows.values()):
            self.after(150, self._poll)

    def remove(self, name: str) -> None:
        keys.remove(name)
        self.rows[name]["state"].set("not set")
