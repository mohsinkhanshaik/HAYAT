"""The confirmation window: check what HAYAT prepared, confirm it, then verify what happened."""
from __future__ import annotations

import os
import threading
from app.gui_resources import BooleanVar, StringVar

import tkinter as tk
from tkinter import ttk
from typing import Any

from app.gui_icons import apply_icons
from app.gui_windows import place_over
from app.safety.confirmations import KINDS


def _open(target: str) -> None:
    opener = getattr(os, "startfile", None)
    if opener is not None and target:
        try:
            opener(target)
        except OSError:
            pass


class ConfirmDialog(tk.Toplevel):
    """One prepared action. Nothing happens until the owner ticks the check box and presses Confirm."""

    def __init__(self, gui: Any, action_id: int) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.gate = gui.runtime.confirmations
        self.action = self.gate.get(action_id)
        self.title(f"HAYAT · Please confirm action {action_id}")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK, padx=18, pady=14)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._outcome: dict[str, Any] | None = None
        self._build()
        apply_icons(self, self.palette)
        place_over(self, gui.root, (760, 680))
        self.deiconify()
        self.lift()
        try:
            self.attributes("-topmost", True)
            self.after(800, lambda: self.winfo_exists() and self.attributes("-topmost", False))
        except tk.TclError:
            pass

    def _build(self) -> None:
        p, action = self.palette, self.action
        tk.Label(self, text="Please check before HAYAT does this", bg=p.BLACK, fg=p.GOLD,
                 font=("Segoe UI Semibold", 15), anchor="w").pack(fill="x")
        tk.Label(self, text=action.title or KINDS.get(action.kind, action.kind), bg=p.BLACK, fg=p.WHITE,
                 font=("Segoe UI Semibold", 11), anchor="w", justify="left", wraplength=700).pack(fill="x", pady=(6, 0))
        tk.Label(self, text=f"Where: {action.target}", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=700).pack(fill="x")
        self.body = tk.Frame(self, bg=p.BLACK)
        self.body.pack(fill="both", expand=True, pady=(10, 0))
        table = ttk.Treeview(self.body, columns=("value",), show="tree headings", height=min(10, max(3, len(action.details))))
        table.heading("#0", text="What")
        table.heading("value", text="Value HAYAT will send")
        table.column("#0", width=220, anchor="w")
        table.column("value", width=480, anchor="w")
        for index, (name, value) in enumerate(action.details):
            table.insert("", "end", iid=str(index), text=name, values=(value,))
        table.pack(fill="x")
        for note in action.notes:
            tk.Label(self.body, text="• " + note, bg=p.BLACK, fg=p.WHITE, font=("Segoe UI", 9), anchor="w",
                     justify="left", wraplength=700).pack(fill="x", pady=(4, 0))
        if action.evidence:
            row = tk.Frame(self.body, bg=p.BLACK)
            row.pack(fill="x", pady=(8, 0))
            for path in action.evidence:
                label = "View the filled page" if path.endswith(".png") else "Open the file"
                ttk.Button(row, text=label, style="Hayat.TButton", command=lambda target=path: _open(target)
                           ).pack(side="left", padx=(0, 6))
        self.secret_vars: dict[str, StringVar] = {}
        for name in action.secrets_needed:
            tk.Label(self.body, text=f"Type {name} (used once, never stored)", bg=p.BLACK, fg=p.GOLD_SOFT,
                     font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(10, 2))
            var = StringVar(self, "")
            ttk.Entry(self.body, textvariable=var, show="•", width=40).pack(fill="x")
            self.secret_vars[name] = var
        self.checked = BooleanVar(self, False)
        ttk.Checkbutton(self.body, text="I have checked these details and I want HAYAT to do this now.",
                        variable=self.checked, command=self._sync).pack(anchor="w", pady=(12, 0))
        self.status_var = StringVar(self, "Nothing has been sent yet. It expires 10 minutes after it was prepared.")
        self.status = tk.Label(self, textvariable=self.status_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9),
                               anchor="w", justify="left", wraplength=700)
        self.status.pack(fill="x", pady=(10, 4))
        self.buttons = tk.Frame(self, bg=p.BLACK)
        self.buttons.pack(fill="x")
        self.confirm_button = ttk.Button(self.buttons, text="Confirm and do it", style="HayatAccent.TButton",
                                         command=self.confirm, state="disabled")
        self.confirm_button.pack(side="right")
        ttk.Button(self.buttons, text="Cancel action", style="Hayat.TButton", command=self.cancel
                   ).pack(side="right", padx=(0, 6))

    def _sync(self) -> None:
        self.confirm_button.configure(state="normal" if self.checked.get() else "disabled")

    # ------------------------------------------------------------------ decisions
    def cancel(self) -> None:
        try:
            self.gate.cancel(self.action.id)
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self.destroy()

    def confirm(self) -> None:
        if not self.checked.get():
            self.status_var.set("Tick the box to say you checked the details.")
            return
        secrets = {name: var.get() for name, var in self.secret_vars.items()}
        missing = [name for name, value in secrets.items() if not value]
        if missing:
            self.status_var.set("Type " + ", ".join(missing) + " first.")
            return
        for var in self.secret_vars.values():
            var.set("")
        self.confirm_button.configure(state="disabled")
        self.status_var.set("Working… HAYAT is doing it now and will check the result.")

        def work() -> None:
            try:
                self._outcome = self.gate.confirm(self.action.id, by_owner=True, secrets=secrets)
            except Exception as exc:
                self._outcome = {"status": "failed", "result": {"error": str(exc)}}
            finally:
                secrets.clear()

        threading.Thread(target=work, name="hayat-confirmed-action", daemon=True).start()
        self.after(200, self._poll)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        if self._outcome is None:
            self.after(200, self._poll)
            return
        self.show_result(self._outcome)

    def show_result(self, outcome: dict[str, Any]) -> None:
        p = self.palette
        for child in self.body.winfo_children():
            child.destroy()
        for child in self.buttons.winfo_children():
            child.destroy()
        result = outcome.get("result", {})
        failed = outcome.get("status") == "failed"
        verdict = result.get("error") if failed else result.get("verdict", "done")
        colour = p.ALERT if failed or "fail" in str(verdict) else p.OLIVE_SOFT if "looks" in str(verdict) or \
            "live" in str(verdict) else p.GOLD
        tk.Label(self.body, text=("It did not work: " if failed else "Done. ") + str(verdict), bg=p.BLACK, fg=colour,
                 font=("Segoe UI Semibold", 12), anchor="w", justify="left", wraplength=700).pack(fill="x")
        where = result.get("site_url") or result.get("final_url")
        if where:
            tk.Label(self.body, text=f"Ended up at: {where}", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI", 10),
                     anchor="w", justify="left", wraplength=700).pack(fill="x", pady=(6, 0))
        for key, label in (("success_words", "The page said"), ("error_words", "Warning, the page also said")):
            if result.get(key):
                tk.Label(self.body, text=f"{label}: {', '.join(result[key])}", bg=p.BLACK,
                         fg=p.WHITE if key == "success_words" else p.ALERT, font=("Segoe UI", 10), anchor="w"
                         ).pack(fill="x")
        row = tk.Frame(self.body, bg=p.BLACK)
        row.pack(fill="x", pady=(10, 0))
        if result.get("after_screenshot"):
            ttk.Button(row, text="View the page afterwards", style="Hayat.TButton",
                       command=lambda: _open(result["after_screenshot"])).pack(side="left", padx=(0, 6))
        if result.get("site_url"):
            ttk.Button(row, text="Open the live website", style="Hayat.TButton",
                       command=lambda: _open(result["site_url"])).pack(side="left", padx=(0, 6))
        self.status_var.set("Please check it yourself, then tell HAYAT whether it is right." if not failed
                            else "Nothing more was tried. Check the page yourself before trying again.")
        if not failed:
            ttk.Button(self.buttons, text="Looks right", style="HayatAccent.TButton",
                       command=lambda: self.verify(True)).pack(side="right")
            ttk.Button(self.buttons, text="Something's wrong", style="Hayat.TButton",
                       command=lambda: self.verify(False)).pack(side="right", padx=(0, 6))
        else:
            ttk.Button(self.buttons, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")

    def verify(self, ok: bool) -> None:
        try:
            self.gate.verify(self.action.id, ok)
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        append = getattr(self.gui, "_append", None)
        if callable(append):
            append("system", f"Action {self.action.id} {'verified by you' if ok else 'marked wrong by you'}: "
                             f"{self.action.title}.")
        self.destroy()
