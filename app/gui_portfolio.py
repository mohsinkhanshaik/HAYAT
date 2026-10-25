"""Studios → Portfolio & net worth: what the owner holds at their own bank, broker and exchange, valued live in USD.

* The top line is net worth in USD, then cash, stocks and crypto with their share, what was invested, and the
  unrealised and realised gains.
* Two charts: the allocation bar and net worth by day (one snapshot a day, taken when the portfolio is read).
* The table lists each holding with its price, value, gain and where the amount came from (recorded, or read
  from the blockchain for a watched address).
* The form records a buy, sell, deposit, withdrawal or opening balance made elsewhere; a public Bitcoin or
  Ethereum address can be watched.

HAYAT holds nothing, trades nothing and keeps no keys (app/portfolio.py).
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from app.gui_resources import StringVar
from app.portfolio import KINDS, usd

KIND_COLOURS = {"cash": "#7DD3FC", "stock": "#A3E635", "crypto": "#E8C46A"}
GAIN, LOSS = "#4ADE80", "#F05D5E"


class PortfolioWindow(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p, self.portfolio = gui, type(gui), gui.runtime.portfolio
        p = self.p
        self.title("HAYAT · Portfolio & net worth")
        self.configure(bg=p.BLACK)
        self.minsize(980, 640)
        self.bind("<Escape>", lambda _event: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._jobs: queue.Queue = queue.Queue()
        self._results: queue.Queue = queue.Queue()
        self._closed = False
        self.value: dict[str, Any] | None = None
        head = tk.Frame(self, bg=p.BLACK)
        head.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(head, text="Portfolio & net worth", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 16), anchor="w").pack(side="left")
        self.status = tk.Label(head, text="", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9))
        self.status.pack(side="right")
        ttk.Button(head, text="Refresh prices", style="Hayat.TButton", command=self.reload).pack(side="right", padx=8)
        tk.Label(self, text="What you hold at your own bank, broker and exchange, valued live in US dollars. HAYAT holds nothing, trades "
                            "nothing and keeps no keys: record moves you made elsewhere, or watch a public crypto address.",
                 bg=p.BLACK, fg=p.MUTED, wraplength=1100, justify="left", anchor="w").pack(fill="x", padx=16)
        summary = tk.Frame(self, bg=p.BLACK)
        summary.pack(fill="x", padx=16, pady=(10, 4))
        self.net = tk.Label(summary, text="—", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 24), anchor="w")
        self.net.pack(side="left")
        self.split = tk.Label(summary, text="", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 10), anchor="w", justify="left")
        self.split.pack(side="left", padx=16)
        charts = tk.Frame(self, bg=p.BLACK)
        charts.pack(fill="x", padx=16)
        self.allocation = tk.Canvas(charts, bg=p.PANEL_2, highlightthickness=0, height=110, width=420)
        self.allocation.pack(side="left", fill="x", expand=True)
        self.history = tk.Canvas(charts, bg=p.PANEL_2, highlightthickness=0, height=110, width=520)
        self.history.pack(side="left", fill="x", expand=True, padx=(8, 0))
        columns = (("symbol", "Holding", 90), ("kind", "Kind", 60), ("account", "Account", 130), ("quantity", "Quantity", 100),
                   ("price", "Price", 100), ("value", "Value (USD)", 110), ("gain", "Gain", 150), ("origin", "Amount from", 240))
        self.tree = ttk.Treeview(self, columns=[key for key, _title, _width in columns], show="headings", height=6)
        for key, title, width in columns:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=key in ("account", "origin"), anchor="e" if key in ("price", "value", "gain") else "w")
        self.tree.tag_configure("gain", foreground=GAIN)
        self.tree.tag_configure("loss", foreground=LOSS)
        self.tree.pack(fill="both", expand=True, padx=16, pady=(8, 0))
        self._build_form()
        self.message = StringVar(self, "")
        tk.Label(self, textvariable=self.message, bg=p.BLACK, fg=p.GOLD_SOFT, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=1100).pack(fill="x", padx=16, pady=(4, 10))
        threading.Thread(target=self._work, name="hayat-portfolio-window", daemon=True).start()
        from app.gui_icons import apply_icons
        from app.gui_windows import place_over
        apply_icons(self, p)
        place_over(self, gui.root, (1180, 780))
        self.deiconify()
        self.reload()
        self.after(150, self._tick)

    def _build_form(self) -> None:
        p = self.p
        form = tk.Frame(self, bg=p.PANEL, padx=10, pady=8)
        form.pack(fill="x", padx=16, pady=(8, 0))
        self.fields: dict[str, StringVar] = {}
        for column, (key, label, width, choices) in enumerate((
                ("action", "Move", 9, ("buy", "sell", "deposit", "withdraw", "set")), ("kind", "Kind", 8, ("stock", "crypto", "cash")),
                ("symbol", "Symbol (USD, BTC, AAPL)", 12, None), ("quantity", "Quantity", 10, None),
                ("price_usd", "Price each, USD", 11, None), ("account", "Account (Chase, Coinbase…)", 18, None),
                ("on_day", "Date (empty = today)", 12, None))):
            tk.Label(form, text=label, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8)).grid(row=0, column=column, sticky="w", padx=3)
            variable = StringVar(self, choices[0] if choices else "")
            widget = (ttk.Combobox(form, textvariable=variable, values=list(choices), state="readonly", width=width) if choices
                      else ttk.Entry(form, textvariable=variable, width=width))
            widget.grid(row=1, column=column, sticky="we", padx=3)
            self.fields[key] = variable
        ttk.Button(form, text="Record", style="HayatAccent.TButton", command=self.record).grid(row=1, column=7, padx=(8, 0))
        row = tk.Frame(self, bg=p.BLACK)
        row.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(row, text="Watch a public address:", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.address = StringVar(self, "")
        ttk.Entry(row, textvariable=self.address, width=48).pack(side="left", padx=6)
        ttk.Button(row, text="Watch", style="Hayat.TButton", command=self.watch).pack(side="left")
        ttk.Button(row, text="Close", style="Hayat.TButton", command=self.close).pack(side="right")
        ttk.Button(row, text="Remove selected", style="Hayat.TButton", command=self.remove).pack(side="right", padx=6)

    # ------------------------------------------------------------------ work off the UI thread
    def run(self, kind: str, call: Callable[[], Any], busy: str = "") -> None:
        if busy:
            self.status.configure(text=busy, fg=self.p.MUTED)
        self._jobs.put((kind, call))

    def _work(self) -> None:
        while not self._closed:
            try:
                kind, call = self._jobs.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                self._results.put((kind, call(), None))
            except Exception as exc:  # shown in the window, never raised into Tk
                self._results.put((kind, None, str(exc)))

    def _tick(self) -> None:
        if self._closed:
            return
        while not self._results.empty():
            kind, value, error = self._results.get_nowait()
            if error:
                self.status.configure(text="", fg=self.p.MUTED)
                self.message.set(error)
                continue
            self.status.configure(text="", fg=self.p.MUTED)
            getattr(self, "show_" + kind)(value)
        self.after(150, self._tick)

    def close(self) -> None:
        self._closed = True
        self.destroy()

    # ------------------------------------------------------------------ actions
    def reload(self) -> None:
        self.run("view", self.portfolio.view, "Reading live prices…")

    def record(self) -> None:
        values = {key: variable.get().strip() for key, variable in self.fields.items()}
        values["on_day"] = values["on_day"] or None
        self.run("change", lambda: self.portfolio.record(**values), "Recording…")

    def watch(self) -> None:
        address = self.address.get().strip()
        self.run("change", lambda: self.portfolio.watch_address(address), "Checking the address…")

    def remove(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self.message.set("Select a holding first.")
            return
        self.run("change", lambda: self.portfolio.remove(int(selection[0])), "Removing…")

    def show_change(self, value: dict[str, Any]) -> None:
        from app.portfolio import report_lines
        self.message.set(" ".join(report_lines(value)))
        for key in ("symbol", "quantity", "price_usd", "on_day"):
            self.fields[key].set("")
        self.address.set("")
        self.reload()

    # ------------------------------------------------------------------ the view
    def show_view(self, value: dict[str, Any]) -> None:
        self.value = value
        self.net.configure(text=usd(value["net_worth_usd"]))
        parts = [f"{kind.title()} {usd(amount)} · {value['allocation'].get(kind, 0):g}%" for kind, amount in value["by_kind"].items() if amount]
        if value["invested_usd"]:
            parts.append(f"Invested {usd(value['invested_usd'])} · unrealised {usd(value['unrealised_usd'])} · "
                         f"realised {usd(value['realised_usd'])}")
        wallet = value.get("business_wallet") or {}
        if wallet.get("rows"):
            parts.append(f"Business wallet ≈ {usd(wallet['total_usd'])} (kept apart: it sits in your own accounts)")
        self.split.configure(text="\n".join(parts))
        self.tree.delete(*self.tree.get_children())
        for row in value["holdings"]:
            gain = f"{usd(row['gain_usd'])} ({row['gain_pct']:+.1f}%)" if row["gain_pct"] is not None else ""
            origin = row["origin"] + (f" · {row['note']}" if row["note"] else "") + (f" · {row['price_source']}" if row["price_source"] else "")
            tag = ("gain",) if (row["gain_usd"] or 0) > 0 else ("loss",) if (row["gain_usd"] or 0) < 0 else ()
            self.tree.insert("", "end", iid=str(row["id"]), tags=tag, values=(
                row["symbol"], row["kind"], row["account"] or "—", row["quantity"],
                usd(row["price_usd"]) if row["price_usd"] is not None and row["kind"] != "cash" else "", usd(row["value_usd"]), gain, origin))
        from app.gui_empty import show_empty
        show_empty(self.tree, "Nothing recorded yet. Record what you hold elsewhere (e.g. set · cash · USD · 2500 · Chase), a stock "
                              "or coin you bought, or watch a public Bitcoin or Ethereum address.", self.p)
        self.message.set(value["basis"] + (f" No price for: {', '.join(value['unpriced'])}." if value["unpriced"] else ""))
        self.draw_allocation(value)
        self.draw_history(value["history"])

    def draw_allocation(self, value: dict[str, Any]) -> None:
        canvas, p = self.allocation, self.p
        canvas.delete("all")
        canvas.update_idletasks()
        width = max(canvas.winfo_width(), 300)
        canvas.create_text(12, 14, text="Allocation", anchor="w", fill=p.MUTED, font=("Segoe UI", 9))
        total = value["net_worth_usd"]
        if not total:
            canvas.create_text(12, 56, text="No priced holdings yet.", anchor="w", fill=p.MUTED, font=("Segoe UI", 9))
            return
        left = 12
        for kind in KINDS:
            share = value["by_kind"].get(kind, 0) / total
            if share <= 0:
                continue
            right = left + share * (width - 24)
            canvas.create_rectangle(left, 32, right, 60, fill=KIND_COLOURS[kind], outline="")
            left = right
        x = 12
        for kind in KINDS:
            if value["by_kind"].get(kind):
                canvas.create_rectangle(x, 76, x + 10, 86, fill=KIND_COLOURS[kind], outline="")
                label = f"{kind} {value['allocation'].get(kind, 0):g}%"
                canvas.create_text(x + 14, 81, text=label, anchor="w", fill=p.WHITE, font=("Segoe UI", 9))
                x += 30 + 7 * len(label)

    def draw_history(self, history: list[dict[str, Any]]) -> None:
        canvas, p = self.history, self.p
        canvas.delete("all")
        canvas.update_idletasks()
        width, height = max(canvas.winfo_width(), 300), max(canvas.winfo_height(), 100)
        canvas.create_text(12, 14, text="Net worth by day (USD)", anchor="w", fill=p.MUTED, font=("Segoe UI", 9))
        if len(history) < 2:
            canvas.create_text(12, 56, text="One point a day; the line appears from the second day you open this.", anchor="w",
                               fill=p.MUTED, font=("Segoe UI", 9))
            return
        values = [point["net_worth_usd"] for point in history]
        low, high = min(values), max(values)
        span = (high - low) or 1
        top, bottom, left, right = 28, height - 18, 12, width - 90
        points = []
        for index, amount in enumerate(values):
            points += [left + index * (right - left) / (len(values) - 1), bottom - (amount - low) / span * (bottom - top)]
        canvas.create_line(*points, fill=GAIN if values[-1] >= values[0] else LOSS, width=2, smooth=True)
        canvas.create_text(width - 8, top, text=usd(high), anchor="e", fill=p.MUTED, font=("Segoe UI", 8))
        canvas.create_text(width - 8, bottom, text=usd(low), anchor="e", fill=p.MUTED, font=("Segoe UI", 8))
        canvas.create_text(left, height - 6, text=history[0]["day"], anchor="w", fill=p.MUTED, font=("Segoe UI", 8))
        canvas.create_text(right, height - 6, text=history[-1]["day"], anchor="e", fill=p.MUTED, font=("Segoe UI", 8))
