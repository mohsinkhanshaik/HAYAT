"""The Business window: what came in, what went out, and what to do next.

A read-mostly view on the owner's books (app/tools/business.py): income,
expenses and profit for a period, the monthly goal, invoices with their
balances, clients, open work leads and expenses. The owner can open an
invoice PDF, mark an invoice paid, open the folder of documents and export a
year for taxes. Creating invoices, logging time and adding leads happens in
chat, where HAYAT fills in the details.

The Products tab manages local drafts, editable starter deliverables, saved
file selections and checked delivery bundles through the tool registry.
"""
from __future__ import annotations

import os
import threading
from app.gui_resources import StringVar

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from app.gui_icons import apply_icons
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY

PERIODS = ("this month", "last month", "this year", "last year", "all")


def summary_text(report: dict, include_goal: bool = True) -> str:
    """The headline numbers as plain lines, one currency per line."""
    if not report["by_currency"]:
        return f"{report['period']}: nothing recorded yet."
    lines = [f"{report['period']}"]
    for row in report["by_currency"]:
        other = row.get("other_income", "")
        extra = f", {other} other income" if other and not other.startswith("0.00") else ""
        lines.append(f"Earned {row['client_income']} from clients, {row['product_sales_net']} from products{extra}. "
                     f"Spent {row['expenses']}. Profit before tax {row['profit_before_tax']}.")
    goal = report.get("goal")
    if goal and include_goal:
        pace = "" if "on_pace" not in goal else (" On pace." if goal["on_pace"] else " Behind pace.")
        lines.append(f"Goal {goal['target']}: {goal['percent']}% reached, {goal['remaining']} to go.{pace}")
    return "\n".join(lines)


CURRENCY_CHOICES = ("USD", "INR", "EUR", "GBP", "AED", "SAR", "PKR", "SGD", "CAD", "AUD", "NZD")


class FormDialog(tk.Toplevel):
    """A small, non-blocking form. A list default becomes a drop-down.

    ``submit`` receives the values; a ValueError it raises is shown in the
    form, which stays open so the owner can correct it.
    """

    def __init__(self, studio: Any, title: str, fields: list[tuple[str, str, Any]], submit: Any,
                 intro: str = "") -> None:
        super().__init__(studio)
        p = studio.palette
        self.studio, self.submit = studio, submit
        self.title(f"HAYAT · {title}")
        self.transient(studio)
        self.configure(bg=p.BLACK, padx=18, pady=14)
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        self.bind("<Return>", lambda _event: (self.save(), "break")[1])
        tk.Label(self, text=title, bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 13), anchor="w").pack(fill="x")
        if intro:
            tk.Label(self, text=intro, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=420).pack(fill="x", pady=(4, 8))
        self.values: dict[str, StringVar] = {}
        for key, label, default in fields:
            tk.Label(self, text=label, bg=p.BLACK, fg=p.WHITE, font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(6, 2))
            if isinstance(default, (list, tuple)):
                var = StringVar(self, default[0] if default else "")
                ttk.Combobox(self, textvariable=var, values=list(default), state="readonly", width=40).pack(fill="x")
            else:
                var = StringVar(self, str(default or ""))
                # A password is masked and never echoed back into the form.
                ttk.Entry(self, textvariable=var, width=42, show="•" if ("password" in key or "token" in key) else "").pack(fill="x")
            self.values[key] = var
        self.error = StringVar(self, "")
        tk.Label(self, textvariable=self.error, bg=p.BLACK, fg=p.ALERT, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=420).pack(fill="x", pady=(8, 0))
        row = tk.Frame(self, bg=p.BLACK)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Save", style="HayatAccent.TButton", command=self.save).pack(side="right")
        ttk.Button(row, text="Cancel", style="Hayat.TButton", command=self.destroy).pack(side="right", padx=(0, 6))
        self.lift()

    def save(self) -> bool:
        values = {key: var.get().strip() for key, var in self.values.items()}
        try:
            message = self.submit(values)
        except ValueError as exc:
            self.error.set(str(exc))
            return False
        if message:
            self.studio.status_var.set(message)
        self.destroy()
        return True


class BusinessStudio(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.ledger = gui.runtime.business
        self.title("HAYAT · Business")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        self._build()
        apply_icons(self, self.palette)
        from app.gui_help import attach_tooltips

        attach_tooltips(self, self.palette)
        self.minsize(980, 680)
        place_over(self, gui.root, (1440, 920))
        self.deiconify()
        self.lift()
        self.refresh()
        # Someone with no business yet starts on the Launch tab, not an empty invoice list.
        if self.launch_tab is not None and not self.launch_tab.current and not self.invoices.get_children():
            self.show_tab("Launch")

    # ------------------------------------------------------------------ layout
    # Card accents: revenue, profit, owed, wallet, target.
    ACCENTS = {"income": "#4ADE80", "profit": "#7AB8FF", "owed": "#F5C451", "wallet": "#B388FF", "target": "#5ED6C0"}
    STATUS_FILTERS = ("All status", "Paid", "Pending", "Overdue", "Void")

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=18, pady=12)
        header.pack(fill="x")
        logo = tk.Canvas(header, width=44, height=44, bg=p.BLACK, highlightthickness=0)
        logo.pack(side="left", padx=(0, 12))
        for top in (8, 16, 24):
            logo.create_oval(8, top, 36, top + 12, outline=p.GOLD, width=2, fill=p.BLACK)
        words = tk.Frame(header, bg=p.BLACK)
        words.pack(side="left")
        tk.Label(words, text="Business", bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 19), anchor="w").pack(fill="x")
        tk.Label(words, text="Your income, clients, invoices and expenses, from your own books", bg=p.BLACK, fg=p.MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(fill="x")
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")
        ttk.Button(header, text="Refresh", style="Hayat.TButton", command=self.refresh).pack(side="right", padx=4)
        ttk.Button(header, text="Today's brief", style="Hayat.TButton",
                   command=self.show_brief).pack(side="right", padx=4)
        ttk.Button(header, text="Email setup", style="Hayat.TButton",
                   command=self.email_setup_form).pack(side="right", padx=4)
        picker_box = tk.Frame(header, bg=p.BLACK)
        picker_box.pack(side="right", padx=10)
        self.period = StringVar(self, PERIODS[0])
        picker = ttk.Combobox(picker_box, textvariable=self.period, values=PERIODS, state="readonly", width=12)
        picker.pack(fill="x")
        picker.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self.range_var = StringVar(self, "")
        tk.Label(picker_box, textvariable=self.range_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 8)).pack(fill="x")

        # Headline cards in the main currency. Other currencies are listed in
        # the summary line under them, never added together.
        cards = tk.Frame(self, bg=p.BLACK)
        cards.pack(fill="x", padx=18, pady=(0, 6))
        self.tile_vars: dict[str, StringVar] = {}
        self.trend_vars: dict[str, StringVar] = {}
        self.trend_labels: dict[str, tk.Label] = {}
        from app.gui_icons import icon_image
        for index, (key, title, icon) in enumerate((("income", "Total revenue", "income"), ("profit", "Profit before tax", "activity"),
                                                    ("owed", "Owed to you", "history"), ("wallet", "Wallet balance", "lock"),
                                                    ("target", "Monthly target", "check"))):
            accent = self.ACCENTS[key]
            card = tk.Frame(cards, bg=p.PANEL, padx=14, pady=10, highlightthickness=1, highlightbackground=accent)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 10, 0))
            cards.columnconfigure(index, weight=1, uniform="card")
            top = tk.Frame(card, bg=p.PANEL)
            top.pack(fill="x")
            tk.Label(top, text=title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 10), anchor="w").pack(side="left")
            badge = tk.Label(top, bg=p.PANEL, image=icon_image(top, icon, accent, 22))
            badge.pack(side="right")
            self.tile_vars[key] = StringVar(self, "--")
            tk.Label(card, textvariable=self.tile_vars[key], bg=p.PANEL, fg=p.WHITE, font=(DISPLAY, 18),
                     anchor="w").pack(fill="x", pady=(2, 0))
            self.trend_vars[key] = StringVar(self, "")
            self.trend_labels[key] = tk.Label(card, textvariable=self.trend_vars[key], bg=p.PANEL, fg=p.MUTED,
                                              font=("Segoe UI", 9), anchor="w", justify="left", wraplength=210)
            self.trend_labels[key].pack(fill="x")
            if key == "target":
                self.goal_holder = card
        self.goal_var = StringVar(self, "")
        self.goal_bar = ttk.Progressbar(self.goal_holder, maximum=100, style="Hayat.Horizontal.TProgressbar")
        # The goal sentence (goal_var) is read by chat and tests; the card shows the bar and the trend line.
        self.goal_label = self.trend_labels["target"]
        self.summary_var = StringVar(self, "")
        tk.Label(self, textvariable=self.summary_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left").pack(fill="x", padx=18)
        self.actions_var = StringVar(self, "")

        footer = tk.Frame(self, bg=p.BLACK, padx=18, pady=6)
        footer.pack(fill="x", side="bottom")
        self.status_var = StringVar(self, "")
        tk.Label(footer, textvariable=self.status_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9),
                 anchor="w", justify="left", wraplength=1200).pack(fill="x")
        self.split = tk.PanedWindow(self, orient="horizontal", bg=p.BLACK, sashwidth=6, bd=0)
        self.split.pack(fill="both", expand=True, padx=(18, 12), pady=(6, 0))
        main = tk.Frame(self.split, bg=p.BLACK)
        # The sidebar scrolls, so every card stays reachable in a shorter window.
        self.sidebar_outer = tk.Frame(self.split, bg=p.BLACK)
        sidebar_canvas = tk.Canvas(self.sidebar_outer, bg=p.BLACK, highlightthickness=0, width=350)
        sidebar_scroll = ttk.Scrollbar(self.sidebar_outer, orient="vertical", command=sidebar_canvas.yview,
                                       style="Hayat.Vertical.TScrollbar")
        sidebar_canvas.configure(yscrollcommand=sidebar_scroll.set)
        sidebar_scroll.pack(side="right", fill="y")
        sidebar_canvas.pack(side="left", fill="both", expand=True)
        self.sidebar = tk.Frame(sidebar_canvas, bg=p.BLACK)
        window = sidebar_canvas.create_window(0, 0, window=self.sidebar, anchor="nw")
        sidebar_canvas.bind("<Configure>", lambda event: sidebar_canvas.itemconfigure(window, width=event.width))
        self.sidebar.bind("<Configure>", lambda _event: sidebar_canvas.configure(scrollregion=sidebar_canvas.bbox("all")))
        self._sidebar_canvas = sidebar_canvas
        self.split.add(main, minsize=560, stretch="always")
        self.split.add(self.sidebar_outer, minsize=300, width=372, stretch="never")
        self._sidebar_hidden = False
        self.bind("<Configure>", self._responsive, add="+")

        # HAYAT's own button row rather than ttk tabs, which have no dark
        # style and showed a light grey strip across the dark window.
        from app.gui_intelligence_parts import FlowRow
        switcher = FlowRow(main, p.BLACK, gap=4, height=40)
        switcher.pack(fill="x")
        self.tab_buttons: dict[str, ttk.Button] = {}
        self.tab_frames: dict[str, tk.Frame] = {}
        self.quick = tk.Frame(main, bg=p.BLACK)
        self.quick.pack(fill="x", side="bottom", pady=(8, 4))
        self.body = tk.Frame(main, bg=p.BLACK)
        self.body.pack(fill="both", expand=True, pady=(6, 0))
        money = {"total", "balance", "rate", "invoiced", "value", "amount"}
        self.invoices = self._table("Invoices", switcher, (
            ("#0", "Number", 130), ("client", "Client", 200), ("issued", "Issued", 100), ("due", "Due date", 100),
            ("total", "Amount", 120), ("balance", "Owed", 120), ("status", "Status", 100)), money)
        self._invoice_tools()
        self.clients = self._table("Clients", switcher, (
            ("#0", "Client", 240), ("email", "Email", 260), ("rate", "Hourly rate", 140),
            ("invoiced", "Invoiced", 160)), money)
        self.leads = self._table("Leads", switcher, (
            ("#0", "Opportunity", 300), ("source", "Source", 140), ("status", "Status", 100),
            ("value", "Value", 130), ("follow", "Follow up", 110)), money)
        self.expenses = self._table("Expenses", switcher, (
            ("#0", "Date", 110), ("category", "Category", 140), ("description", "Description", 380),
            ("amount", "Amount", 130)), money)
        self._wallet_tab(switcher, money)
        self._streams_tab(switcher, money)
        from app.gui_business_deep import attach as attach_deep
        self.deep_tab = attach_deep(self, switcher)
        from app.gui_launch import attach

        self.launch_tab = attach(self, switcher)
        from app.gui_products import attach as attach_products
        self.products_tab = attach_products(self, switcher)
        from app.gui_business_model import BusinessModelTab
        self.model_tab = BusinessModelTab(self, switcher)
        # Tab buttons wrap onto a second row in a narrow window instead of being cut off.
        for button in switcher.winfo_children():
            button.pack_forget()
            switcher.add(button)
        for tree in (self.invoices, self.leads, self.withdrawal_tree):
            tree.tag_configure("overdue", foreground=p.ALERT)
            tree.tag_configure("done", foreground=p.MUTED)
            tree.tag_configure("due", foreground=p.GOLD_SOFT)
        self.show_tab("Invoices")
        self._quick_actions()
        self._build_sidebar()

    def _invoice_tools(self) -> None:
        """Counts, search and status filter above the invoice list; actions on right-click."""
        p = self.palette
        frame = self.tab_frames["Invoices"]
        stats = tk.Frame(frame, bg=p.PANEL, padx=10, pady=8)
        stats.pack(side="top", fill="x", before=self.invoices)
        self.invoice_stat_vars: dict[str, StringVar] = {}
        for index, (key, title, colour) in enumerate((("count", "Invoices", p.ROYAL_SOFT), ("paid", "Paid", "#4ADE80"),
                                                      ("pending", "Pending", p.GOLD_SOFT), ("overdue", "Overdue", p.ALERT),
                                                      ("invoiced", "Total invoiced", p.WHITE))):
            tile = tk.Frame(stats, bg=p.PANEL_2, padx=10, pady=6)
            tile.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            stats.columnconfigure(index, weight=2 if key == "invoiced" else 1, uniform="stat" if key != "invoiced" else None)
            tk.Label(tile, text=title, bg=p.PANEL_2, fg=p.MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x")
            self.invoice_stat_vars[key] = StringVar(self, "0")
            tk.Label(tile, textvariable=self.invoice_stat_vars[key], bg=p.PANEL_2, fg=colour, font=("Segoe UI Semibold", 15),
                     anchor="w").pack(fill="x")
        tools = tk.Frame(frame, bg=p.PANEL, padx=10, pady=4)
        tools.pack(side="top", fill="x", before=self.invoices)
        self.invoice_search = StringVar(self, "")
        self.invoice_filter = StringVar(self, self.STATUS_FILTERS[0])
        tk.Label(tools, text="Search", bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9)).pack(side="left")
        ttk.Entry(tools, textvariable=self.invoice_search, width=28).pack(side="left", padx=(6, 10))
        ttk.Combobox(tools, textvariable=self.invoice_filter, values=self.STATUS_FILTERS, state="readonly",
                     width=12).pack(side="left")
        for variable in (self.invoice_search, self.invoice_filter):
            variable.trace_add("write", lambda *_: self._fill_invoices())
        for label, command in (("Email invoice", self.email_invoice_form), ("Open PDF", self.open_pdf), ("Pay-now QR", self.pay_now)):
            ttk.Button(tools, text=label, style="Compact.Hayat.TButton", command=command).pack(side="right", padx=(6, 0))
        self.invoices.bind("<Double-1>", lambda _event: self.open_pdf())
        menu = tk.Menu(self, tearoff=0, bg=p.PANEL_2, fg=p.WHITE, activebackground=p.GOLD, activeforeground=p.BLACK, bd=0)
        for label, command in (("Open PDF", self.open_pdf), ("Email invoice", self.email_invoice_form),
                               ("Mark paid", self.mark_paid), ("Draft a payment reminder", self.send_reminder),
                               ("Pay-now QR and links", self.pay_now)):
            menu.add_command(label=label, command=command)
        self.invoice_menu = menu

        def popup(event: tk.Event) -> None:
            row = self.invoices.identify_row(event.y)
            if row:
                self.invoices.selection_set(row)
                menu.tk_popup(event.x_root, event.y_root)
        self.invoices.bind("<Button-3>", popup)
        self._invoice_rows: list[tuple] = []

    def _quick_actions(self) -> None:
        p = self.palette
        tk.Label(self.quick, text="Quick actions", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 12),
                 anchor="w").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self.quick_buttons: dict[str, ttk.Button] = {}
        actions = (("Create invoice", "New invoice through HAYAT", self.create_invoice),
                   ("Mark as paid", "Selected invoice", self.mark_paid),
                   ("Send reminder", "Draft for the selected invoice", self.send_reminder),
                   ("Add client", "Name, email and rate", self.add_client_form),
                   ("Export for taxes", "Spreadsheet of the year", self.export),
                   ("Open documents folder", "Invoices and files", self.open_folder),
                   ("Add expense", "Track a business cost", self.add_expense_form),
                   ("Generate report", "Today's brief from your books", self.show_brief))
        for index, (title, detail, command) in enumerate(actions):
            button = ttk.Button(self.quick, text=f"{title}\n{detail}", style="Hayat.TButton", command=command)
            button.grid(row=1 + index // 4, column=index % 4, sticky="nsew", padx=(0, 6), pady=(0, 6))
            self.quick_buttons[title] = button
        for column in range(4):
            self.quick.columnconfigure(column, weight=1, uniform="quick")

    def _sidebar_card(self, title: str) -> tk.Frame:
        p = self.palette
        card = tk.Frame(self.sidebar, bg=p.PANEL, padx=12, pady=8, highlightthickness=1, highlightbackground=p.LINE)
        card.pack(fill="x", pady=(0, 8))
        tk.Label(card, text=title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 11), anchor="w").pack(fill="x")
        return card

    def _build_sidebar(self) -> None:
        p = self.palette
        insights = self._sidebar_card("Business insights")
        grid = tk.Frame(insights, bg=p.PANEL)
        grid.pack(fill="x", pady=(6, 0))
        self.insight_vars: dict[str, StringVar] = {}
        self.insight_notes: dict[str, StringVar] = {}
        for index, (key, title) in enumerate((("revenue", "Revenue"), ("margin", "Profit margin"),
                                              ("expenses", "Expenses"), ("clients", "Clients"))):
            tile = tk.Frame(grid, bg=p.PANEL_2, padx=8, pady=6)
            tile.grid(row=index // 2, column=index % 2, sticky="nsew", padx=(0, 6) if index % 2 == 0 else 0, pady=(0, 6))
            grid.columnconfigure(index % 2, weight=1, uniform="insight")
            tk.Label(tile, text=title, bg=p.PANEL_2, fg=p.MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x")
            variable = StringVar(self, "--")
            if key == "expenses":
                self.tile_vars["expenses"] = variable  # Spent this period, shown here rather than as a card.
            self.insight_vars[key] = variable
            tk.Label(tile, textvariable=variable, bg=p.PANEL_2, fg=p.WHITE, font=("Segoe UI Semibold", 12), anchor="w").pack(fill="x")
            self.insight_notes[key] = StringVar(self, "")
            tk.Label(tile, textvariable=self.insight_notes[key], bg=p.PANEL_2, fg=p.MUTED, font=("Segoe UI", 8),
                     anchor="w").pack(fill="x")
        chart_card = self._sidebar_card("Revenue vs expenses · 6 months")
        self.chart = tk.Canvas(chart_card, height=150, bg=p.PANEL, highlightthickness=0)
        self.chart.pack(fill="x", pady=(4, 0))
        self.chart.bind("<Configure>", lambda _event: self._draw_chart())
        self._series: list[dict] = []
        self.upcoming_frame = tk.Frame(self._sidebar_card("Upcoming & overdue"), bg=p.PANEL)
        self.upcoming_frame.pack(fill="x", pady=(4, 0))
        self.transactions_frame = tk.Frame(self._sidebar_card("Recent transactions"), bg=p.PANEL)
        self.transactions_frame.pack(fill="x", pady=(4, 0))
        suggestions = self._sidebar_card("Suggestions from your books")
        tk.Label(suggestions, textvariable=self.actions_var, bg=p.PANEL, fg=p.GOLD_SOFT, font=("Segoe UI", 9),
                 anchor="w", justify="left", wraplength=320).pack(fill="x", pady=(4, 0))
        acquisitions = self._sidebar_card("Businesses for sale")
        tk.Label(acquisitions, text="Research listings, financial evidence and valuation gaps.",
                 bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=320).pack(fill="x", pady=(4, 6))
        ttk.Button(acquisitions, text="Research an acquisition", style="Hayat.TButton",
                   command=self.acquisition_form).pack(fill="x")

    def _bind_sidebar_wheel(self, widget: tk.Misc | None = None) -> None:
        """Scroll the sidebar from anywhere over it (bound per widget, never application-wide)."""
        widget = widget or self._sidebar_canvas
        widget.bind("<MouseWheel>", lambda event: (self._sidebar_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"), "break")[1])
        for child in widget.winfo_children():
            self._bind_sidebar_wheel(child)

    def _responsive(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        hide = event.width < 1180
        if hide != self._sidebar_hidden:
            self._sidebar_hidden = hide
            self.split.paneconfigure(self.sidebar_outer, hide=hide)

    def _table(self, title: str, switcher: tk.Frame, columns: tuple, money: set[str]) -> ttk.Treeview:
        button = ttk.Button(switcher, text=title, style="Hayat.TButton", command=lambda: self.show_tab(title))
        button.pack(side="left", padx=(0, 4))
        self.tab_buttons[title] = button
        frame = tk.Frame(self.body, bg=self.palette.PANEL)
        self.tab_frames[title] = frame
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns[1:]], show="tree headings", selectmode="browse")
        for column, heading, width in columns:
            tree.heading(column, text=heading)
            # Money lines up on the decimal point when it is right-aligned.
            tree.column(column, width=width, anchor="e" if column in money else "w")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview, style="Hayat.Vertical.TScrollbar")
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def show_tab(self, title: str) -> None:
        for name, frame in self.tab_frames.items():
            if name == title:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
            self.tab_buttons[name].configure(style="HayatChosen.TButton" if name == title else "Hayat.TButton")
        self.current_tab = title
        if title in ("Business model", "Deep review"):  # both need the room more than the quick actions do
            self.quick.pack_forget()
        elif not self.quick.winfo_manager():
            self.quick.pack(fill="x", side="bottom", pady=(8, 4), before=self.body)

    # ------------------------------------------------------------------ income streams
    def _streams_tab(self, switcher: tk.Frame, money: set[str]) -> None:
        """Every recorded income source, its share and its change: several streams, from real records."""
        p = self.palette
        button = ttk.Button(switcher, text="Income streams", style="Hayat.TButton",
                            command=lambda: self.show_tab("Income streams"))
        button.pack(side="left", padx=(0, 4))
        self.tab_buttons["Income streams"] = button
        frame = tk.Frame(self.body, bg=p.PANEL, padx=10, pady=8)
        self.tab_frames["Income streams"] = frame
        self.streams_var = StringVar(self, "")
        tk.Label(frame, textvariable=self.streams_var, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 12),
                 anchor="w", justify="left", wraplength=880).pack(fill="x")
        self.streams_note = StringVar(self, "")
        tk.Label(frame, textvariable=self.streams_note, bg=p.PANEL, fg=p.GOLD_SOFT, font=("Segoe UI", 10),
                 anchor="w", justify="left", wraplength=880).pack(fill="x", pady=(4, 6))
        self.streams_tree = self._tree(frame, (("#0", "Source", 240), ("kind", "Kind", 160), ("amount", "Amount", 150),
                                               ("share", "Share", 90), ("change", "Change vs last period", 180)),
                                       money | {"share", "change"}, height=7)
        tk.Label(frame, text="Only what you recorded: invoice payments, product sales and other income such as "
                             "marketplace payouts. Nothing is estimated or predicted. Use the period picker above.",
                 bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=880
                 ).pack(fill="x", pady=(6, 0))

    def _show_streams(self) -> None:
        try:
            result = self.ledger.income_streams(self.period.get())
        except Exception as exc:
            self.streams_var.set(f"Could not read income streams: {type(exc).__name__}")
            return
        count = result["stream_count"]
        self.streams_var.set(f"{result['period']}: " + (", ".join(result["totals"].values()) +
                                                        f" from {count} source{'s' if count != 1 else ''}"
                                                        if count else "no income recorded yet"))
        self.streams_note.set(" ".join(result["notes"]))
        self._fill(self.streams_tree, [(f"s{index}", stream["source"],
                                        (stream["kind"], stream["amount"], f"{stream['share']:g}%",
                                         stream["change"] or ""))
                                       for index, stream in enumerate(result["streams"])])

    def show_brief(self) -> tk.Toplevel | None:
        """Today's brief in a small window: what needs attention, from the books."""
        brief = getattr(self.gui.runtime, "brief", None)
        if brief is None:
            self.status_var.set("The business brief isn't available in this session.")
            return None
        text = brief.text(brief.brief())
        p = self.palette
        window = tk.Toplevel(self)
        window.title("HAYAT · Today's brief")
        window.transient(self)
        window.configure(bg=p.BLACK, padx=16, pady=12)
        window.bind("<Escape>", lambda _event: (window.destroy(), "break")[1])
        body = DarkScrolledText(window, height=16, width=80, wrap="word", bg=p.PANEL, fg=p.WHITE, relief="flat",
                                font=("Segoe UI", 10), padx=12, pady=10)
        body.pack(fill="both", expand=True)
        body.insert("1.0", text + "\n\nRead from your books. Nothing was sent or changed.")
        body.configure(state="disabled")
        ttk.Button(window, text="Close", style="Hayat.TButton", command=window.destroy).pack(side="right", pady=(8, 0))
        self.brief_window = window
        return window

    # ------------------------------------------------------------------ wallet
    def _wallet_tab(self, switcher: tk.Frame, money: set[str]) -> None:
        """Earnings kept in the app, and withdrawals to a method set up whenever the owner likes."""
        p = self.palette
        button = ttk.Button(switcher, text="Wallet", style="Hayat.TButton", command=lambda: self.show_tab("Wallet"))
        button.pack(side="left", padx=(0, 4))
        self.tab_buttons["Wallet"] = button
        frame = tk.Frame(self.body, bg=p.PANEL, padx=10, pady=8)
        self.tab_frames["Wallet"] = frame
        top = tk.Frame(frame, bg=p.PANEL)
        top.pack(fill="x")
        self.wallet_var = StringVar(self, "")
        tk.Label(top, textvariable=self.wallet_var, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 12),
                 anchor="w", justify="left").pack(side="left")
        for label, command in (("Record income", self.record_income_form), ("Record withdrawal", self.withdraw_form),
                               ("Set up withdrawal", self.payout_form), ("Money methods", self.money_methods),
                               ("Add money", self.add_money_form)):
            ttk.Button(top, text=label, style="Hayat.TButton", command=command).pack(side="right", padx=(6, 0))
        if callable(getattr(self.gui, "_open_money_desk", None)):
            # Pots, bills, rules and HAYAT's money decisions to approve (app/gui_money_desk.py).
            ttk.Button(top, text="Decisions & pots", style="HayatAccent.TButton",
                       command=self.gui._open_money_desk).pack(side="right", padx=(6, 0))
        self.payout_var = StringVar(self, "")
        tk.Label(frame, textvariable=self.payout_var, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=860).pack(fill="x", pady=(4, 6))
        tables = tk.PanedWindow(frame, orient="vertical", bg=p.PANEL, sashwidth=6, bd=0)
        tables.pack(fill="both", expand=True)
        activity = tk.Frame(tables, bg=p.PANEL)
        self.activity_tree = self._tree(activity, (("#0", "Date", 110), ("kind", "Type", 150),
                                                   ("description", "Description", 420), ("amount", "Amount", 150)), money,
                                        height=10)
        self.activity_tree.tag_configure("in", foreground=p.OLIVE_SOFT)
        self.activity_tree.tag_configure("out", foreground=p.WHITE)
        tables.add(activity, stretch="always", minsize=120)
        withdrawals = tk.Frame(tables, bg=p.PANEL)
        row = tk.Frame(withdrawals, bg=p.PANEL)
        row.pack(fill="x", pady=(6, 4))
        tk.Label(row, text="Withdrawals", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 10)).pack(side="left")
        ttk.Button(row, text="Cancel withdrawal", style="Hayat.TButton",
                   command=self.cancel_withdrawal).pack(side="right", padx=(6, 0))
        ttk.Button(row, text="Mark transfer done", style="Hayat.TButton",
                   command=self.complete_withdrawal).pack(side="right")
        self.withdrawal_tree = self._tree(withdrawals, (("#0", "#", 50), ("requested", "Requested", 110),
                                                        ("to", "To", 330), ("amount", "Amount", 150),
                                                        ("status", "Status", 110)), money, height=4)
        # Activity takes any spare height; the withdrawal list keeps a few rows.
        tables.add(withdrawals, stretch="never", minsize=110, height=150)

    def _tree(self, parent: tk.Frame, columns: tuple, money: set[str], height: int = 8) -> ttk.Treeview:
        holder = tk.Frame(parent, bg=self.palette.PANEL)
        holder.pack(fill="both", expand=True)
        tree = ttk.Treeview(holder, columns=[c[0] for c in columns[1:]], show="tree headings", selectmode="browse",
                            height=height)
        for column, heading, width in columns:
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="e" if column in money else "w")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview, style="Hayat.Vertical.TScrollbar")
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def _show_wallet(self) -> None:
        try:
            wallet = self.ledger.wallet(activity=300)
        except Exception as exc:
            self.wallet_var.set(f"Could not read the wallet: {type(exc).__name__}")
            return
        balances = wallet["balances"]
        self.wallet_var.set("Recorded balance: " + (", ".join(b["available"] for b in balances) or "nothing yet"))
        payout = wallet["payout"]
        where = (f"Withdrawals go to {payout['destination']}." if payout["configured"]
                 else "No withdrawal method yet. Set one up later; payments already received can be recorded here.")
        pending = [w for w in wallet["withdrawals"] if w["status"] == "pending"]
        self.payout_var.set(f"{where} {wallet['basis']}" + (f" {len(pending)} withdrawal(s) waiting for you to make "
                                                             "the transfer." if pending else ""))
        self._fill(self.activity_tree, [(f"a{index}", item["date"], (item["kind"], item["description"], item["amount"]),
                                         item["direction"]) for index, item in enumerate(wallet["activity"])])
        self._fill(self.withdrawal_tree, [(str(w["id"]), str(w["id"]), (w["requested_on"], w["destination"], w["amount"],
                                                                         w["status"]),
                                           "due" if w["status"] == "pending" else "done") for w in wallet["withdrawals"]])

    def _form(self, title: str, fields: list[tuple[str, str, Any]], submit: Any, intro: str = "") -> FormDialog:
        self._form_dialog = FormDialog(self, title, fields, submit, intro)
        return self._form_dialog

    def payout_form(self) -> FormDialog:
        payout = self.ledger.payout()
        methods = list(self.ledger.PAYOUT_METHODS)
        default = self.ledger.profile()["default_currency"]
        currencies = [default] + [c for c in CURRENCY_CHOICES if c != default]

        def submit(values: dict) -> str:
            self.ledger.set_payout(values["method"], values["name"], values["last4"], values["currency"] or None)
            self.refresh()
            return "Withdrawal method saved."
        return self._form("Set up withdrawal", [
            ("method", "Method", methods), ("name", "Account name (e.g. My savings, or your PayPal email)", ""),
            ("last4", "Last 4 digits (optional)", ""), ("currency", "Currency", currencies),
        ], submit, intro="Only a name and the last 4 digits are kept. Never enter a full account or card number, "
                         "a password or a PIN: HAYAT records withdrawals, your bank or PayPal moves the money.",
        ) if not payout["configured"] else self._form("Change withdrawal method", [
            ("method", "Method", [payout["method"]] + [m for m in methods if m != payout["method"]]),
            ("name", "Account name", payout["name"]), ("last4", "Last 4 digits (optional)", payout["last4"]),
            ("currency", "Currency", [payout["currency"]] + [c for c in CURRENCY_CHOICES if c != payout["currency"]]),
        ], submit, intro=f"Currently: {payout['destination']}.")

    def withdraw_form(self) -> FormDialog | None:
        payout = self.ledger.payout()
        if not payout["configured"]:
            self.show_tab("Wallet")
            self.status_var.set("Set up a withdrawal method first. Your earnings stay in the wallet meanwhile.")
            return None

        def submit(values: dict) -> str:
            result = self.ledger.withdraw(values["amount"], values["currency"] or None, values["note"])
            self.refresh()
            self.status_var.set(result["next_step"])
            return result["next_step"]
        balances = {b["currency"]: b["available"] for b in self.ledger.wallet(activity=0)["balances"]}
        return self._form("Withdraw", [
            ("amount", "Amount (or 'all')", "all"),
            ("currency", "Currency", [payout["currency"]] + [c for c in balances if c != payout["currency"]]),
            ("note", "Note (optional)", ""),
        ], submit, intro=f"To {payout['destination']}. Available: {', '.join(balances.values()) or 'nothing yet'}. "
                         "HAYAT records it as pending; make the transfer yourself, then mark it done.")

    def add_money_form(self) -> FormDialog:
        """Record money the owner moved into the business themselves; nothing is charged or moved."""
        from app.gui_wallet_money import add_money_form
        return add_money_form(self)

    def money_methods(self) -> Any:
        from app.gui_wallet_money import MoneyMethodsWindow
        return MoneyMethodsWindow(self)

    def pay_now(self) -> Any:
        number = self._selected_invoice()
        if not number:
            return None
        from app.gui_wallet_money import PayNowWindow
        return PayNowWindow(self, number)

    def record_income_form(self) -> FormDialog:
        def submit(values: dict) -> str:
            result = self.ledger.record_income(values["amount"], values["source"], values["description"],
                                               values["date"] or None, values["currency"] or None,
                                               values["reference"] or None)
            self.refresh()
            return f"Recorded {result['amount']}." if result["recorded"] else "That reference was already recorded."
        default = self.ledger.profile()["default_currency"]
        return self._form("Record income", [
            ("amount", "Amount", ""), ("source", "From (e.g. Fiverr, Upwork, a client)", ""),
            ("description", "What for", ""), ("date", "Date (YYYY-MM-DD, empty = today)", ""),
            ("currency", "Currency", [default] + [c for c in CURRENCY_CHOICES if c != default]),
            ("reference", "Payout or order reference (optional)", ""),
        ], submit, intro="Money earned outside HAYAT's invoices and product store.")

    def _selected_withdrawal(self) -> int | None:
        selected = self.withdrawal_tree.selection()
        if not selected:
            self.show_tab("Wallet")
            self.status_var.set("Select a withdrawal first.")
            return None
        return int(selected[0])

    def complete_withdrawal(self) -> None:
        withdrawal = self._selected_withdrawal()
        if withdrawal is None:
            return
        try:
            result = self.ledger.complete_withdrawal(withdrawal)
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self.refresh()
        self.status_var.set(f"Withdrawal #{withdrawal} of {result['amount']} marked done.")

    def cancel_withdrawal(self) -> None:
        withdrawal = self._selected_withdrawal()
        if withdrawal is None:
            return
        try:
            result = self.ledger.cancel_withdrawal(withdrawal)
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self.refresh()
        self.status_var.set(f"Withdrawal #{withdrawal} cancelled. {result['note']}")

    # ------------------------------------------------------------------ data
    @staticmethod
    def _fill(tree: ttk.Treeview, rows: list[tuple]) -> None:
        selected = tree.selection()
        tree.delete(*tree.get_children())
        for iid, text, values, *tag in rows:
            tree.insert("", "end", iid=iid, text=text, values=values, tags=tuple(t for t in tag if t))
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])

    def refresh(self) -> None:
        try:
            report = self.ledger.report(self.period.get())
            invoices = self.ledger.invoices("all")["documents"]
            clients = self.ledger.clients()["clients"]
            leads = self.ledger.leads("all")["leads"]
            expenses = self.ledger.expenses(self.period.get())["expenses"]
        except Exception as exc:
            self.status_var.set(f"Could not read the business books: {type(exc).__name__}: {exc}")
            return
        self.summary_var.set(summary_text(report, include_goal=False).replace("\n", "  ·  "))
        self._show_tiles(report)
        self.actions_var.set("\n".join(f"{index}. {action}" for index, action in enumerate(report["next_actions"], 1)) or
                             "Nothing needs attention right now.")
        self._invoice_rows = [(d["number"], d["number"], (d["client"], d["issued"], d["due"], d["total"], d["balance"],
                                                        "● " + ("Overdue" if d["overdue"] else {"unpaid": "Pending"}.get(d["status"], d["status"].capitalize()))),
                               "overdue" if d["overdue"] else "done" if d["status"] in {"paid", "void"} else "due")
                              for d in invoices]
        self._fill_invoices()
        self._show_dashboard()
        self._fill(self.clients, [(str(c["id"]), c["name"], (c["email"], c["hourly_rate"], c["invoiced"])) for c in clients])
        self._fill(self.leads, [(str(lead["id"]), lead["title"], (lead["source"], lead["status"], lead["value"],
                                                                  lead["follow_up"] or ""),
                                 "due" if lead["follow_up_due"] else "done" if lead["status"] in {"won", "lost"} else "")
                                for lead in leads])
        self._fill(self.expenses, [(str(e["id"]), e["spent_on"], (e["category"], e["description"], e["amount"]))
                                   for e in expenses])
        self._show_wallet()
        self._show_streams()
        if getattr(self, "deep_tab", None) is not None:
            self.deep_tab.refresh()
        if getattr(self, "launch_tab", None) is not None:
            self.launch_tab.refresh()
        self.status_var.set(f"{len(invoices)} invoice(s), {len(clients)} client(s), {len(leads)} lead(s). "
                            "Figures are what you recorded; HAYAT has not verified payments.")
        badge = getattr(self.gui, "refresh_wallet_badge", None)
        if callable(badge):
            badge()

    def _fill_invoices(self) -> None:
        """The invoice list through the search box and status filter."""
        wanted = self.invoice_filter.get()
        needle = self.invoice_search.get().strip().casefold()
        rows = [row for row in self._invoice_rows
                if (wanted == self.STATUS_FILTERS[0] or row[2][5].endswith(wanted))
                and (not needle or needle in " ".join((row[1], *row[2])).casefold())]
        self._fill(self.invoices, rows)

    def _show_dashboard(self) -> None:
        """Trends, counts, the six-month chart and the sidebar lists, all from the books."""
        from app.gui_business_insights import dashboard
        from app.tools.business import period_bounds
        try:
            value = dashboard(self.ledger, self.period.get())
            start, end, _label = period_bounds(self.period.get())
        except Exception as exc:
            self.status_var.set(f"Could not read the dashboard numbers: {type(exc).__name__}: {exc}")
            return
        self.range_var.set("All recorded time" if start.startswith("0000") else f"{start} → {end}")
        kpis, compared = value["kpis"], value["kpis"]["compared_with"]
        for key in ("income", "profit"):
            delta = kpis[key]["change"]
            label = self.trend_labels[key]
            if delta is None:
                self.trend_vars[key].set(f"No {compared} figure to compare" if compared else "Whole period")
                label.configure(fg=self.palette.MUTED)
            else:
                self.trend_vars[key].set(f"{'↑' if delta >= 0 else '↓'} {abs(delta):g}% vs {compared}")
                label.configure(fg="#4ADE80" if delta >= 0 else self.palette.ALERT)
        unpaid = value["report"]["unpaid_count"]
        self.trend_vars["owed"].set(f"{unpaid} unpaid invoice{'s' if unpaid != 1 else ''}" if unpaid else "Nothing outstanding")
        self.trend_vars["wallet"].set("Recorded locally; not a bank balance")
        stats = value["invoices"]
        for key, variable in self.invoice_stat_vars.items():
            variable.set(str(stats[key]))
        self.insight_vars["revenue"].set(kpis["income"]["value"])
        self.insight_notes["revenue"].set(self.trend_vars["income"].get())
        self.insight_vars["margin"].set(f"{kpis['margin']:g}%" if kpis["margin"] is not None else "--")
        self.insight_notes["margin"].set("Profit ÷ revenue" if kpis["margin"] is not None else "No revenue yet")
        delta = kpis["expenses"]["change"]
        self.insight_notes["expenses"].set(f"{'↑' if delta >= 0 else '↓'} {abs(delta):g}% vs {compared}" if delta is not None else "")
        self.insight_vars["clients"].set(str(value["clients"]))
        self.insight_notes["clients"].set("Recorded clients")
        self._series, self._series_currency = value["series"], value["currency"]
        self._draw_chart()
        self._list(self.upcoming_frame, [(item["title"], item["detail"], item["when"],
                                          {"overdue": self.palette.ALERT, "soon": self.palette.GOLD_SOFT}.get(item["state"], self.palette.MUTED),
                                          item["number"]) for item in value["upcoming"]],
                   "Nothing due. Unpaid invoices and lead follow-ups appear here.")
        self._list(self.transactions_frame, [(item["amount"], item["description"][:60], item["date"],
                                              "#4ADE80" if item["direction"] == "in" else self.palette.ALERT, None)
                                             for item in value["transactions"][:5]],
                   "No money in or out recorded yet.")
        self._bind_sidebar_wheel()

    def _list(self, frame: tk.Frame, rows: list[tuple], empty: str) -> None:
        p = self.palette
        for child in frame.winfo_children():
            child.destroy()
        if not rows:
            tk.Label(frame, text=empty, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=320).pack(fill="x")
        for title, detail, when, colour, number in rows:
            line = tk.Frame(frame, bg=p.PANEL, pady=3)
            line.pack(fill="x")
            dot = tk.Canvas(line, width=12, height=12, bg=p.PANEL, highlightthickness=0)
            dot.create_oval(2, 2, 10, 10, fill=colour, outline="")
            dot.pack(side="left", padx=(0, 8), anchor="n", pady=4)
            words = tk.Frame(line, bg=p.PANEL)
            words.pack(side="left", fill="x", expand=True)
            tk.Label(words, text=title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 9), anchor="w").pack(fill="x")
            tk.Label(words, text=detail, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x")
            tk.Label(line, text=when, bg=p.PANEL, fg=colour, font=("Segoe UI", 8)).pack(side="right", anchor="n")
            if number:
                for widget in (line, words, *words.winfo_children()):
                    widget.configure(cursor="hand2")
                    widget.bind("<Button-1>", lambda _event, value=number: self.select_invoice(value))

    def select_invoice(self, number: str) -> None:
        self.show_tab("Invoices")
        self.invoice_search.set("")
        self.invoice_filter.set(self.STATUS_FILTERS[0])
        if self.invoices.exists(number):
            self.invoices.selection_set(number)
            self.invoices.see(number)

    def _draw_chart(self) -> None:
        canvas, p = self.chart, self.palette
        canvas.delete("all")
        width, height = max(200, canvas.winfo_width()), int(canvas["height"])
        series = self._series
        if not series:
            return
        top = max([1] + [max(month["income_cents"], month["expense_cents"]) for month in series])
        base, chart_top = height - 22, 18
        slot = (width - 40) / len(series)
        canvas.create_text(4, 6, text=self._series_currency, fill=p.MUTED, anchor="nw", font=("Segoe UI", 8))
        for name, colour, x in (("Revenue", "#4F8DFD", width - 150), ("Expenses", p.GOLD, width - 76)):
            canvas.create_rectangle(x, 6, x + 9, 15, fill=colour, outline="")
            canvas.create_text(x + 13, 10, text=name, fill=p.MUTED, anchor="w", font=("Segoe UI", 8))
        canvas.create_line(30, base, width - 6, base, fill=p.LINE)
        for index, month in enumerate(series):
            left = 34 + index * slot
            bar = max(6, slot / 2 - 6)
            for offset, cents, colour in ((0, month["income_cents"], "#4F8DFD"), (bar + 2, month["expense_cents"], p.GOLD)):
                y = base - (base - chart_top) * cents / top
                if cents:
                    canvas.create_rectangle(left + offset, y, left + offset + bar, base, fill=colour, outline="")
            canvas.create_text(left + bar, base + 10, text=month["label"], fill=p.MUTED, font=("Segoe UI", 8))
        if top <= 1:
            canvas.create_text(width / 2, (base + chart_top) / 2, text="No revenue or expenses recorded in these months.",
                               fill=p.MUTED, font=("Segoe UI", 9))

    # ------------------------------------------------------------------ quick actions
    def ask_hayat(self, request: str, note: str) -> None:
        """Put a request in the chat box for the owner to finish; nothing is sent from here."""
        use = getattr(self.gui, "use_example", None)
        if callable(use):
            use(request)
            self.status_var.set(note)
        else:
            self.status_var.set("Open the main HAYAT window to finish this in chat: " + request)

    def create_invoice(self) -> None:
        self.ask_hayat("Create an invoice for CLIENT for AMOUNT for DESCRIPTION",
                       "Fill in the client, amount and work in the chat box, then press Send. HAYAT makes the PDF.")

    def acquisition_form(self) -> FormDialog:
        def submit(values: dict) -> str:
            from decimal import Decimal, InvalidOperation
            location = values['location'].strip()
            if not location:
                raise ValueError('Enter a country or region for the search.')
            sector = values['sector'].strip()
            budget = values['budget'].strip().replace(',', '')
            price_filter = ''
            if budget:
                try:
                    amount = Decimal(budget)
                except InvalidOperation:
                    raise ValueError('Enter a numeric acquisition budget.') from None
                if not amount.is_finite() or amount <= 0:
                    raise ValueError('Acquisition budget must be positive and finite.')
                price_filter = f" with an asking price below {values['currency']} {amount}"
            request = (f"Research distressed {sector + ' ' if sector else ''}businesses for sale in {location}"
                       f"{price_filter}. Look deep into financial evidence, valuation, seller mistakes and turnaround risks.")
            self.ask_hayat(request, 'Review the acquisition search in chat, then press Send.')
            return 'Acquisition search prepared in chat.'
        return self._form('Research businesses for sale', [
            ('location', 'Country or region', ''), ('sector', 'Industry (optional)', ''),
            ('budget', 'Maximum acquisition budget (optional)', ''), ('currency', 'Budget currency', CURRENCY_CHOICES),
        ], submit, intro='Listing claims need verification. Fair value requires financial records and comparable completed sales.')

    def send_reminder(self) -> None:
        number = self._selected_invoice()
        if number:
            self.ask_hayat(f"Draft a polite payment reminder for invoice {number}",
                           f"A reminder draft for {number} is ready to send to HAYAT. Nothing is emailed without your approval.")

    def add_client_form(self) -> FormDialog:
        def submit(values: dict) -> str:
            result = self.ledger.add_client(values["name"], values["email"], values["rate"] or "0", values["currency"] or None)
            self.refresh()
            return f"Client {result.get('name', values['name'])} added."
        default = self.ledger.profile()["default_currency"]
        return self._form("Add client", [("name", "Client name", ""), ("email", "Email (optional)", ""),
                                         ("rate", "Hourly rate (optional)", ""),
                                         ("currency", "Currency", [default] + [c for c in CURRENCY_CHOICES if c != default])],
                          submit, intro="Saved in your local books; nothing is sent to the client.")

    def add_expense_form(self) -> FormDialog:
        def submit(values: dict) -> str:
            self.ledger.record_expense(values["amount"], values["category"], values["description"], values["date"] or None,
                                       values["currency"] or None)
            self.refresh()
            return "Expense recorded."
        default = self.ledger.profile()["default_currency"]
        return self._form("Add expense", [("amount", "Amount", ""), ("category", "Category (e.g. software, travel)", ""),
                                          ("description", "What for", ""), ("date", "Date (YYYY-MM-DD, empty = today)", ""),
                                          ("currency", "Currency", [default] + [c for c in CURRENCY_CHOICES if c != default])],
                          submit, intro="A business cost, so profit before tax stays accurate.")

    def _show_tiles(self, report: dict) -> None:
        """Headline numbers in the main currency: the goal's, else the busiest."""
        rows = report["by_currency"]
        goal = report.get("goal")
        currency = goal["target"].rsplit(" ", 1)[1] if goal else None
        main = (next((row for row in rows if row["currency"] == currency), None)
                or max(rows, key=lambda row: row["income_cents"], default=None))
        if main is None:
            owed = next(iter(report["outstanding_invoices"].values()), "--")
            values = {"income": "--", "expenses": "--", "profit": "--", "owed": owed}
        else:
            income = main["income_cents"]
            values = {"income": f"{income / 100:,.2f} {main['currency']}", "expenses": main["expenses"],
                      "profit": main["profit_before_tax"],
                      "owed": report["outstanding_invoices"].get(main["currency"], f"0.00 {main['currency']}")}
        wallet = report.get("wallet", {})
        main_currency = main["currency"] if main is not None else next(iter(wallet), None)
        values["wallet"] = wallet.get(main_currency, f"0.00 {main_currency}") if main_currency else "--"
        for key, value in values.items():
            self.tile_vars[key].set(value)
        if goal:
            pace = "" if "on_pace" not in goal else (" · on pace" if goal["on_pace"] else " · behind pace")
            self.goal_var.set(f"Monthly goal {goal['target']}: {goal['percent']:g}% reached{pace}")
            self.tile_vars["target"].set(goal["target"])
            self.trend_vars["target"].set(f"{goal['earned']} earned · {goal['percent']:g}%{pace}")
            self.goal_bar["value"] = min(100.0, goal["percent"])
            self.goal_bar.pack(fill="x", pady=(4, 0))
        else:
            self.goal_var.set("No monthly goal for this period. Ask HAYAT to set one.")
            self.tile_vars["target"].set("Not set")
            self.trend_vars["target"].set("")
            self.goal_bar.pack_forget()

    def _selected_invoice(self) -> str | None:
        selected = self.invoices.selection()
        if not selected:
            self.show_tab("Invoices")
            self.status_var.set("Select an invoice first.")
            return None
        return selected[0]

    # ------------------------------------------------------------------ actions
    # ------------------------------------------------------------------ email
    def _email(self) -> Any:
        return getattr(self.gui.runtime, "email", None)

    def email_setup_form(self) -> FormDialog | None:
        """Your own address and an app password; the password goes to the Windows credential store."""
        email = self._email()
        if email is None:
            self.status_var.set("Email isn't available in this runtime.")
            return None
        status = email.status()

        def submit(values: dict) -> str:
            from app.contacts import ContactError

            try:
                result = email.configure_account(values["address"], values["password"],
                                                 smtp_host=values["smtp_host"])
            except ContactError as exc:
                raise ValueError(str(exc)) from exc
            self.check_email()
            return f"Email saved for {result['address']} ({result['server']}). Checking the login…"
        current = (f"Now: {status['address']} on {status['server']}, password "
                   f"{'stored' if status['password_stored'] else 'not stored'}. " if status["address"] else "")
        return self._form("Email setup", [
            ("address", "Your email address", status["address"]),
            ("password", "App password (Gmail: Google Account -> Security -> App passwords)", ""),
            ("smtp_host", "Mail server (leave empty for Gmail, Outlook, Yahoo, iCloud, Zoho)", ""),
        ], submit, intro=current + "Used to email invoices and quotes from your own account. The password is kept in "
                         "the Windows credential store, never in a file, and is only used for one send at a time.")

    def check_email(self) -> None:
        """Log in and out of the mail server off the window's thread; nothing is sent."""
        email = self._email()
        if email is None:
            return
        self._email_check: dict[str, Any] = {}

        def work() -> None:
            try:
                self._email_check["result"] = email.check_login()["detail"]
            except Exception as exc:
                self._email_check["result"] = f"The mail server refused the login: {type(exc).__name__}: {exc}"

        threading.Thread(target=work, name="hayat-email-check", daemon=True).start()
        self.after(200, self._poll_email_check)

    def _poll_email_check(self) -> None:
        if not self.winfo_exists():
            return
        result = getattr(self, "_email_check", {}).get("result")
        if result is None:
            self.after(200, self._poll_email_check)
            return
        self.status_var.set(result)

    def email_invoice_form(self) -> FormDialog | None:
        number = self._selected_invoice()
        if not number:
            return None
        email = self._email()
        if email is None or not email.status()["ready"]:
            self.status_var.set("Set up email first, then send the invoice.")
            return self.email_setup_form()
        document = self.ledger.document(number)
        profile = self.ledger.profile()
        sender = profile["business_name"] or profile["owner_name"] or "me"
        kind = "Invoice" if document["kind"] == "invoice" else "Quote"
        body = (f"Hello {document['client']},\n\nPlease find attached {kind.lower()} {number} for {document['total']}"
                + (f", due {document['due']}." if document["kind"] == "invoice" else ".")
                + (f"\n\nHow to pay: {profile['payment_instructions']}" if profile["payment_instructions"]
                   and document["kind"] == "invoice" else "")
                + f"\n\nThank you,\n{sender}")

        def submit(values: dict) -> str:
            send = getattr(self.gui, "_tool", None)
            background = getattr(self.gui, "_background", None)
            if send is None or background is None:
                raise ValueError("Sending is only available from the main HAYAT window.")
            if not values["to"]:
                raise ValueError("Who should it go to?")
            arguments = {"to": values["to"], "subject": values["subject"], "body": values["body"],
                         "attachments": [document["path"]]}
            # The same tool and approval path a chat request uses: the window is
            # another caller, not a way round the send confirmation.
            background(f"Email {number}", lambda: send("email_send", arguments))
            return f"Sending {number} to {values['to']}. The result appears in the chat; HAYAT may ask you to approve."
        return self._form(f"Email {kind.lower()} {number}", [
            ("to", "To", document.get("client_email") or ""),
            ("subject", "Subject", f"{kind} {number} from {sender}"),
            ("body", "Message", body),
        ], submit, intro=f"The PDF is attached. {document['total']} for {document['client']}.")

    def open_pdf(self) -> None:
        number = self._selected_invoice()
        if not number:
            return
        path = self.ledger.document(number).get("path")
        if not path or not Path(path).is_file():
            self.status_var.set(f"The PDF for {number} is missing.")
            return
        self._open(path)

    def mark_paid(self) -> None:
        number = self._selected_invoice()
        if not number:
            return
        try:
            result = self.ledger.record_payment(number)
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self.refresh()
        self.status_var.set(f"{number} marked paid ({result['paid']}). Balance {result['balance']}.")

    def open_folder(self) -> None:
        folder = Path(self.ledger.output)
        folder.mkdir(parents=True, exist_ok=True)
        self._open(str(folder))

    def export(self) -> None:
        try:
            result = self.ledger.export()
        except Exception as exc:
            self.status_var.set(f"Export failed: {type(exc).__name__}: {exc}")
            return
        self.status_var.set(f"Exported {result['year']} to {result['path']}. Check it against your statements.")

    def _open(self, target: str) -> None:
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            self.status_var.set(target)
            return
        try:
            startfile(target)
        except OSError as exc:
            self.status_var.set(f"Could not open {target}: {exc}")
