"""Small, nonmodal controls for the per-user login launcher."""
from __future__ import annotations

import datetime as dt
import queue
import sys
import threading
from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk

from app.gui_windows import place_over
from scripts import autostart


def status_text(info: dict) -> str:
    if not info.get("installed"):
        return "Off. HAYAT will open only when you launch it."
    last = info.get("last_run") or {}
    state = str(last.get("state", "")).replace("_", " ")
    if not state:
        return "On. HAYAT will open when you sign in."
    try:
        when = dt.datetime.fromtimestamp(float(last["updated_at"])).strftime("%b %d, %I:%M %p")
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        when = "time unknown"
    return f"On. HAYAT will open when you sign in.\nLast session: {state} · {when}"


class StartupDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, palette) -> None:
        super().__init__(parent)
        self.withdraw()
        self.title("HAYAT · Start at login")
        self.transient(parent)
        self.configure(bg=palette.BLACK)
        self.resizable(False, False)
        self._busy = False
        self._poll_handle = None
        self._result: queue.SimpleQueue[str | None] = queue.SimpleQueue()
        self._message = StringVar(self)
        self._detail = StringVar(self)

        content = tk.Frame(self, bg=palette.BLACK, padx=24, pady=20)
        content.pack(fill="both", expand=True)
        tk.Label(content, text="Start at login", bg=palette.BLACK, fg=palette.WHITE,
                 font=("Segoe UI Semibold", 16), anchor="w").pack(fill="x")
        platform_name = "Windows account" if sys.platform == "win32" else "user account"
        tk.Label(content, text=f"Runs for your {platform_name} after sign-in. The login launcher"
                 " supervises crashes and does not need administrator access.",
                 bg=palette.BLACK, fg=palette.MUTED, font=("Segoe UI", 10),
                 justify="left", anchor="w", wraplength=440).pack(fill="x", pady=(8, 18))
        tk.Label(content, textvariable=self._message, bg=palette.BLACK, fg=palette.WHITE,
                 font=("Segoe UI", 10), justify="left", anchor="w", wraplength=440).pack(fill="x")
        tk.Label(content, textvariable=self._detail, bg=palette.BLACK, fg=palette.MUTED,
                 font=("Segoe UI", 9), justify="left", anchor="w", wraplength=440).pack(fill="x", pady=(7, 0))
        buttons = tk.Frame(content, bg=palette.BLACK)
        buttons.pack(fill="x", pady=(22, 0))
        self.enable_button = ttk.Button(buttons, text="Turn on", style="HayatAccent.TButton",
                                        command=lambda: self._change(True))
        self.enable_button.pack(side="left")
        self.disable_button = ttk.Button(buttons, text="Turn off", style="Hayat.TButton",
                                         command=lambda: self._change(False))
        self.disable_button.pack(side="left", padx=(8, 0))
        self.refresh_button = ttk.Button(buttons, text="Refresh", style="Hayat.TButton", command=self.refresh)
        self.refresh_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        self.refresh()
        self.update_idletasks()
        place_over(self, parent, (500, max(300, self.winfo_reqheight())))
        self.deiconify()
        self.lift()

    def refresh(self) -> None:
        try:
            info = autostart.status()
            installed = bool(info["installed"])
            self._message.set(status_text(info))
            self._detail.set("Changes take effect at your next sign-in.")
        except Exception as exc:
            installed = None
            self._message.set("Could not read login startup status.")
            self._detail.set(str(exc))
        self.enable_button.configure(state="disabled" if self._busy or installed is not False else "normal")
        self.disable_button.configure(state="disabled" if self._busy or installed is not True else "normal")

    def _change(self, enable: bool) -> None:
        if self._busy:
            return
        self._busy = True
        self.enable_button.configure(state="disabled")
        self.disable_button.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self._detail.set("Updating login startup…")

        def work() -> None:
            try:
                (autostart.install if enable else autostart.remove)()
                error = None
            except Exception as exc:
                error = str(exc)
            self._result.put(error)

        threading.Thread(target=work, name="hayat-autostart", daemon=True).start()
        self._poll_handle = self.after(50, self._poll)

    def _poll(self) -> None:
        self._poll_handle = None
        try:
            error = self._result.get_nowait()
        except queue.Empty:
            self._poll_handle = self.after(50, self._poll)
            return
        self._finished(error)

    def _finished(self, error: str | None) -> None:
        if not self.winfo_exists():
            return
        self._busy = False
        self.refresh_button.configure(state="normal")
        self.refresh()
        if error:
            self._detail.set("Could not update login startup: " + error)

    def destroy(self) -> None:
        if self._poll_handle is not None:
            self.after_cancel(self._poll_handle)
            self._poll_handle = None
        super().destroy()
