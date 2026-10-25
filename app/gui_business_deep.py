"""Business → Deep review: the owner's books read in depth (app/business_deep.py).

Two small charts, then the review: money expected by when (each client's paying habit moves the date), six months
of money in and out, and below them what to do first, by the money at stake, with the reasons. Read-only.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from app.gui_resources import StringVar

IN, OUT, RISK = "#4ADE80", "#F05D5E", "#E8C46A"


class DeepTab:
    def __init__(self, studio: Any, switcher: tk.Frame) -> None:
        self.studio = studio
        p = self.p = studio.palette
        button = ttk.Button(switcher, text="Deep review", style="Hayat.TButton", command=lambda: studio.show_tab("Deep review"))
        button.pack(side="left", padx=(0, 4))
        studio.tab_buttons["Deep review"] = button
        self.frame = tk.Frame(studio.body, bg=p.PANEL, padx=12, pady=10)
        studio.tab_frames["Deep review"] = self.frame
        self.value: dict[str, Any] | None = None
        self.title = StringVar(studio, "Reading your books…")
        tk.Label(self.frame, textvariable=self.title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 13), anchor="w").pack(fill="x")
        tk.Label(self.frame, text="Where the money is, when it should arrive, and what to do first. From your own records, plus pay in "
                                  "the job postings HAYAT collected; nothing is sent or changed.",
                 bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=900).pack(fill="x", pady=(0, 6))
        charts = tk.Frame(self.frame, bg=p.PANEL)
        charts.pack(fill="x")
        self.cash = tk.Canvas(charts, bg=p.PANEL_2, highlightthickness=0, height=150)
        self.cash.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.months = tk.Canvas(charts, bg=p.PANEL_2, highlightthickness=0, height=150)
        self.months.pack(side="left", fill="both", expand=True)
        for canvas in (self.cash, self.months):
            canvas.bind("<Configure>", lambda _event: self.draw())
        from app.gui_theme import DarkScrolledText
        self.text = DarkScrolledText(self.frame, wrap="word", bg=p.PANEL_2, fg=p.WHITE, font=("Segoe UI", 10), padx=12, pady=10,
                                     relief="flat", height=14)
        self.text.pack(fill="both", expand=True, pady=(8, 0))
        self.text.configure(state="disabled")

    def refresh(self) -> None:
        from app.business_deep import report_lines, review
        jobs = getattr(getattr(self.studio.gui, "runtime", None), "jobs", None)
        try:
            self.value = review(self.studio.ledger, jobs)
        except Exception as exc:  # shown, never raised into Tk
            self.title.set(f"Could not read the books: {type(exc).__name__}: {exc}")
            return
        value = self.value
        self.title.set("Your books are empty" if value["empty"] else
                       f"Deep review · {value['currency']} · " + (f"real rate {value['effective_rate']}/h · " if value["effective_rate"] else "")
                       + f"pipeline {value['pipeline']['value']}")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(report_lines(value)))
        self.text.configure(state="disabled")
        self.draw()

    def draw(self) -> None:
        self._draw_cash()
        self._draw_months()

    def _draw_cash(self) -> None:
        canvas, p = self.cash, self.p
        canvas.delete("all")
        width, height = max(200, canvas.winfo_width()), max(120, canvas.winfo_height())
        canvas.create_text(10, 8, text="Money expected", anchor="nw", fill=p.WHITE, font=("Segoe UI Semibold", 9))
        buckets = (self.value or {}).get("cash", {}).get("buckets", [])
        if not buckets:
            canvas.create_text(width / 2, height / 2, text="No unpaid invoices", fill=p.MUTED, font=("Segoe UI", 9))
            return
        top = max(item["cents"] for item in buckets) or 1
        step = (width - 20) / len(buckets)
        for index, item in enumerate(buckets):
            x = 10 + step * index
            bar = (height - 80) * item["cents"] / top  # room above the tallest bar for its amount, below the title
            colour = OUT if item["when"].startswith("at risk") else RISK if item["when"].startswith("overdue") else IN
            canvas.create_rectangle(x + 6, height - 34 - bar, x + step - 6, height - 34, fill=colour, outline="")
            canvas.create_text(x + step / 2, height - 38 - bar, text=item["amount"], anchor="s", fill=p.WHITE, font=("Segoe UI", 8))
            canvas.create_text(x + step / 2, height - 30, text=item["when"].replace(" (over 60 days overdue)", ""), anchor="n",
                               fill=p.MUTED, font=("Segoe UI", 8), width=step - 6)

    def _draw_months(self) -> None:
        canvas, p = self.months, self.p
        canvas.delete("all")
        width, height = max(200, canvas.winfo_width()), max(120, canvas.winfo_height())
        canvas.create_text(10, 8, text="Six months", anchor="nw", fill=p.WHITE, font=("Segoe UI Semibold", 9))
        for offset, (label, colour) in enumerate((("in", IN), ("out", OUT))):
            right = width - 70 + offset * 34
            canvas.create_rectangle(right, 11, right + 9, 20, fill=colour, outline="")
            canvas.create_text(right + 12, 8, text=label, anchor="nw", fill=p.MUTED, font=("Segoe UI", 8))
        trend = (self.value or {}).get("trend", [])
        top = max([row["income_cents"] for row in trend] + [row["expense_cents"] for row in trend] + [1])
        if top <= 1:
            canvas.create_text(width / 2, height / 2, text="No money recorded in these months", fill=p.MUTED, font=("Segoe UI", 9))
            return
        step = (width - 20) / len(trend)
        for index, row in enumerate(trend):
            x = 10 + step * index
            for offset, cents, colour in ((0, row["income_cents"], IN), (1, row["expense_cents"], OUT)):
                bar = (height - 64) * cents / top
                left = x + 8 + offset * (step - 16) / 2
                canvas.create_rectangle(left, height - 26 - bar, left + (step - 16) / 2 - 2, height - 26, fill=colour, outline="")
            canvas.create_text(x + step / 2, height - 22, text=row["month"][5:] + "/" + row["month"][2:4], anchor="n",
                               fill=p.MUTED, font=("Segoe UI", 8))


def attach(studio: Any, switcher: tk.Frame) -> DeepTab:
    return DeepTab(studio, switcher)
