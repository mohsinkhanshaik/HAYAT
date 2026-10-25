"""Studios → HAYAT Wallet: HAYAT's money decisions to approve, pots, bills, rules and history (app/money_desk.py).

* The top row: available, in pots, unallocated, held for approved payments, bills in the next 30 days, and how
  many decisions wait.
* **Decisions:** one card each, coloured by urgency, with HAYAT's reasons and what approving means. Approve and
  Decline are the owner's buttons; an approved bill or purchase gets *Open payment page* and *I paid it*.
* **Pots:** progress toward each target, create a pot, move money between pots.
* **Bills:** due dates, repeats, the pot that pays and the biller's page.
* **Rules & targets:** set-aside rules (optionally automatic) and the portfolio's target mix.
* **History:** decided proposals and every pot movement.

Approving here is the owner's own click. HAYAT never pays, buys or moves money outside the wallet record.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Any, Callable

from app.gui_resources import BooleanVar, StringVar
from app.money_desk import PURPOSES

URGENCY_COLOURS = {"high": "#F05D5E", "medium": "#E8C46A", "low": "#7DD3FC"}
POT_COLOURS = ("#E8C46A", "#7DD3FC", "#A3E635", "#F0ABFC", "#FDBA74", "#5EEAD4")
UNALLOCATED = "Unallocated"
TABS = ("Decisions", "Pots", "Bills", "Rules & targets", "History")


class MoneyDeskWindow(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p, self.desk = gui, type(gui), gui.runtime.money_desk
        self.palette = self.p  # FormDialog reads it
        p = self.p
        self.title("HAYAT · Wallet")
        self.configure(bg=p.BLACK)
        self.minsize(1000, 660)
        self.bind("<Escape>", lambda _event: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._jobs: queue.Queue = queue.Queue()
        self._results: queue.Queue = queue.Queue()
        self._closed = False
        self.value: dict[str, Any] | None = None
        head = tk.Frame(self, bg=p.BLACK)
        head.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(head, text="HAYAT Wallet", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 16), anchor="w").pack(side="left")
        ttk.Button(head, text="Think now", style="HayatAccent.TButton", command=self.think).pack(side="right")
        ttk.Button(head, text="Can I afford…?", style="Hayat.TButton", command=self.afford).pack(side="right", padx=6)
        self.status = tk.Label(head, text="", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9))
        self.status.pack(side="right", padx=8)
        tk.Label(self, text="HAYAT reads your books and proposes what to do with the money; nothing changes until you approve. Inside the "
                            "wallet an approval acts at once. Bills and purchases you pay yourself in your bank or app: HAYAT never pays.",
                 bg=p.BLACK, fg=p.MUTED, wraplength=1120, justify="left", anchor="w").pack(fill="x", padx=16)
        self.cards = tk.Frame(self, bg=p.BLACK)
        self.cards.pack(fill="x", padx=16, pady=(10, 2))
        self.card_values: dict[str, tk.Label] = {}
        for key, title in (("available", "Available"), ("in_pots", "In pots"), ("unallocated", "Unallocated"),
                           ("held", "Held for payments"), ("bills", "Bills in 30 days"), ("waiting", "Decisions waiting")):
            box = tk.Frame(self.cards, bg=p.PANEL, padx=12, pady=8)
            box.pack(side="left", fill="x", expand=True, padx=(0, 8) if key != "waiting" else 0)
            tk.Label(box, text=title, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x")
            self.card_values[key] = tk.Label(box, text="—", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 13), anchor="w")
            self.card_values[key].pack(fill="x")
        self.other = tk.Label(self, text="", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 8), anchor="w")
        self.other.pack(fill="x", padx=16)
        self.message = StringVar(self, "")
        tk.Label(self, textvariable=self.message, bg=p.BLACK, fg=p.GOLD_SOFT, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=1120).pack(side="bottom", fill="x", padx=16, pady=(0, 10))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(6, 4))
        self.tabs.add(self._build_decisions(), text="Decisions")
        self.tabs.add(self._build_pots(), text="Pots")
        self.tabs.add(self._build_bills(), text="Bills")
        self.tabs.add(self._build_rules(), text="Rules & targets")
        self.tabs.add(self._build_history(), text="History")
        threading.Thread(target=self._work, name="hayat-wallet-window", daemon=True).start()
        from app.gui_icons import apply_icons
        from app.gui_windows import place_over
        apply_icons(self, p)
        place_over(self, gui.root, (1180, 800))
        self.deiconify()
        self.think()
        self.after(150, self._tick)

    # ------------------------------------------------------------------ building
    def _tree(self, parent: tk.Misc, columns: tuple[tuple[str, str, int], ...], height: int = 7) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=[key for key, _title, _width in columns], show="headings", height=height)
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, stretch=width >= 160)
        tree.pack(fill="both", expand=True)
        return tree

    def _form(self, parent: tk.Misc, fields: tuple[tuple[str, str, int, Any], ...], button: str, command: Callable[[], None]) -> dict[str, Any]:
        p = self.p
        form = tk.Frame(parent, bg=p.PANEL, padx=10, pady=8)
        form.pack(fill="x", pady=(8, 0))
        values: dict[str, Any] = {}
        for column, (key, label, width, choices) in enumerate(fields):
            tk.Label(form, text=label, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8)).grid(row=0, column=column, sticky="w", padx=3)
            if choices is bool:
                variable = BooleanVar(self, False)
                ttk.Checkbutton(form, variable=variable).grid(row=1, column=column, sticky="w", padx=3)
            elif choices:
                variable = StringVar(self, choices[0] if isinstance(choices, (list, tuple)) else "")
                widget = ttk.Combobox(form, textvariable=variable, values=list(choices) if isinstance(choices, (list, tuple)) else [],
                                      state="readonly", width=width)
                widget.grid(row=1, column=column, sticky="we", padx=3)
                values[key + "_widget"] = widget
            else:
                variable = StringVar(self, "")
                ttk.Entry(form, textvariable=variable, width=width).grid(row=1, column=column, sticky="we", padx=3)
            values[key] = variable
        ttk.Button(form, text=button, style="HayatAccent.TButton", command=command).grid(row=1, column=len(fields), padx=(8, 0))
        return values

    def _build_decisions(self) -> tk.Frame:
        p = self.p
        frame = tk.Frame(self.tabs, bg=p.BLACK)
        self.decision_canvas = tk.Canvas(frame, bg=p.BLACK, highlightthickness=0)
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.decision_canvas.yview, style="Hayat.Vertical.TScrollbar")
        self.decision_canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.decision_canvas.pack(side="left", fill="both", expand=True)
        self.decision_list = tk.Frame(self.decision_canvas, bg=p.BLACK)
        window = self.decision_canvas.create_window(0, 0, window=self.decision_list, anchor="nw")
        self.decision_list.bind("<Configure>", lambda _event: self.decision_canvas.configure(scrollregion=self.decision_canvas.bbox("all")))
        self.decision_canvas.bind("<Configure>", lambda event: self.decision_canvas.itemconfigure(window, width=event.width))
        from app.gui_motion import attach
        attach(self.decision_canvas)
        self.decision_buttons: dict[tuple[int, str], ttk.Button] = {}
        return frame

    def _build_pots(self) -> tk.Frame:
        p = self.p
        frame = tk.Frame(self.tabs, bg=p.BLACK, padx=4, pady=6)
        self.pot_bars = tk.Canvas(frame, bg=p.PANEL_2, highlightthickness=0, height=84)
        self.pot_bars.pack(fill="x", pady=(0, 6))
        self.pot_bars.bind("<Configure>", lambda _event: self._draw_pot_bars())
        self.pots = self._tree(frame, (("name", "Pot", 160), ("purpose", "Purpose", 90), ("balance", "Holds", 120), ("target", "Target", 120),
                                       ("progress", "Progress", 80), ("free", "Free", 110), ("sits", "Money sits in", 180)), 3)
        self.pot_form = self._form(frame, (("name", "New pot", 18, None), ("target", "Target", 10, None), ("purpose", "Purpose", 10, PURPOSES),
                                           ("currency", "Currency", 6, ("USD", "INR", "EUR", "GBP", "AED"))), "Create pot", self.create_pot)
        self.move_form = self._form(frame, (("amount", "Move amount", 10, None), ("source", "From", 18, [UNALLOCATED]),
                                            ("target", "To", 18, [UNALLOCATED])), "Move", self.move)
        row = tk.Frame(frame, bg=p.BLACK)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Remove selected (empty) pot", style="Hayat.TButton", command=self.remove_pot).pack(side="left")
        return frame

    def _build_bills(self) -> tk.Frame:
        p = self.p
        frame = tk.Frame(self.tabs, bg=p.BLACK, padx=4, pady=6)
        self.bills = self._tree(frame, (("name", "Bill", 170), ("amount", "Amount", 110), ("due", "Next due", 100), ("days", "In", 80),
                                        ("every", "Repeats", 80), ("pot", "Paid from", 130), ("kind", "Books", 90), ("page", "Payment page", 220)), 8)
        self.bill_form = self._form(frame, (("name", "Bill", 14, None), ("amount", "Amount", 9, None), ("due", "Next due (YYYY-MM-DD)", 12, None),
                                            ("every", "Repeats", 7, ("month", "week", "year", "once")), ("pot", "Paid from", 14, [""]),
                                            ("pay_url", "Biller's https page", 22, None), ("business", "Business", 4, bool)),
                                    "Add bill", self.add_bill)
        row = tk.Frame(frame, bg=p.BLACK)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Remove selected bill", style="Hayat.TButton", command=self.remove_bill).pack(side="left")
        return frame

    def _build_rules(self) -> tk.Frame:
        p = self.p
        frame = tk.Frame(self.tabs, bg=p.BLACK, padx=4, pady=6)
        tk.Label(frame, text="Set-aside rules: a share of every new payment goes to a pot. Automatic rules are applied by HAYAT itself; the "
                             "others are proposed for your approval.", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=1080).pack(fill="x", pady=(0, 6))
        self.rules = self._tree(frame, (("pot", "Pot", 200), ("percent", "Share of income", 120), ("auto", "Applied", 200), ("since", "Since", 120)), 5)
        self.rule_form = self._form(frame, (("pot", "Pot", 18, [""]), ("percent", "Percent", 6, None), ("auto", "Automatic", 4, bool)),
                                    "Add rule", self.add_rule)
        row = tk.Frame(frame, bg=p.BLACK)
        row.pack(fill="x", pady=(6, 10))
        ttk.Button(row, text="Remove selected rule", style="Hayat.TButton", command=self.remove_rule).pack(side="left")
        tk.Label(frame, text="Portfolio target mix: HAYAT proposes a rebalance when your portfolio drifts more than 5 points (you trade at your "
                             "broker; HAYAT never trades).", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=1080).pack(fill="x")
        self.target_form = self._form(frame, (("cash", "Cash %", 6, None), ("stock", "Stocks %", 6, None), ("crypto", "Crypto %", 6, None)),
                                      "Save targets", self.save_targets)
        return frame

    def _build_history(self) -> tk.Frame:
        frame = tk.Frame(self.tabs, bg=self.p.BLACK, padx=4, pady=6)
        self.history = self._tree(frame, (("when", "When", 150), ("what", "What", 520), ("amount", "Amount", 130), ("status", "Result", 140)), 12)
        return frame

    # ------------------------------------------------------------------ work off the UI thread
    def run(self, kind: str, call: Callable[[], Any], busy: str = "") -> None:
        if busy:
            self.status.configure(text=busy)
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
            self.status.configure(text="")
            if error:
                self.message.set(error)
                continue
            getattr(self, "show_" + kind)(value)
        self.after(150, self._tick)

    def close(self) -> None:
        self._closed = True
        self.destroy()

    # ------------------------------------------------------------------ actions
    def think(self) -> None:
        self.run("thought", lambda: (self.desk.think(), self.desk.overview()), "HAYAT is thinking about your money…")

    def reload(self, note: str = "") -> None:
        if note:
            self.message.set(note)
        self.run("overview", self.desk.overview)

    def act(self, call: Callable[[], dict[str, Any]], think: bool = True) -> None:
        """Run a change, show what it did, then let HAYAT think again over the new numbers."""
        def work() -> tuple[dict[str, Any], dict[str, Any]]:
            result = call()
            if think:
                self.desk.think(portfolio=False)
            return result, self.desk.overview()
        self.run("change", work, "Working…")

    def approve(self, decision_id: int) -> None:
        self.act(lambda: self.desk.approve(decision_id, by_owner=True))

    def decline(self, decision_id: int) -> None:
        self.act(lambda: self.desk.decline(decision_id, by_owner=True), think=False)

    def paid(self, decision_id: int, reference: str) -> None:
        self.act(lambda: self.desk.paid(decision_id, by_owner=True, reference=reference))

    def afford(self) -> Any:
        from app.gui_business import FormDialog
        pots = [""] + [pot["name"] for pot in (self.value or {}).get("pots", [])]

        def submit(values: dict) -> str:
            result = self.desk.request_spend(values["amount"], values["what"], values["pot"], url=values["url"],
                                             business=values["business"] == "business")
            self.reload()
            self.tabs.select(0)
            return f"HAYAT says: {result['verdict']}. It is in Decisions for you to approve or decline."
        return FormDialog(self, "Can I afford it?", [
            ("amount", "Amount", ""), ("what", "What for", ""), ("pot", "From pot (empty = unallocated money)", pots),
            ("business", "Kind", ["personal", "business"]), ("url", "Shop page (https, optional)", "")], submit,
            intro="HAYAT checks the pot or your unallocated money, the bills due in 30 days, your emergency fund and your income, "
                  "gives its verdict, and files it for your approval. HAYAT never buys anything.")

    def create_pot(self) -> None:
        form = self.pot_form
        values = {key: form[key].get() for key in ("name", "target", "currency", "purpose")}  # read now; the form is cleared below
        self.act(lambda: self.desk.create_pot(values["name"], values["target"] or "0", values["currency"], values["purpose"]))
        form["name"].set("")
        form["target"].set("")

    def move(self) -> None:
        form = self.move_form
        source, target, amount = form["source"].get(), form["target"].get(), form["amount"].get()
        self.act(lambda: self.desk.move(amount, "" if target == UNALLOCATED else target.split(". ", 1)[0],
                                        "" if source == UNALLOCATED else source.split(". ", 1)[0]))
        form["amount"].set("")

    def remove_pot(self) -> None:
        selection = self.pots.selection()
        if selection:
            self.act(lambda: self.desk.remove_pot(selection[0]), think=False)
        else:
            self.message.set("Select a pot first.")

    def add_bill(self) -> None:
        form = self.bill_form
        pot = form["pot"].get().split(". ", 1)[0]
        values = {key: form[key].get() for key in ("name", "amount", "due", "every", "pay_url", "business")}
        self.act(lambda: self.desk.add_bill(values["name"], values["amount"], values["due"], values["every"], pot=pot,
                                            pay_url=values["pay_url"], business=bool(values["business"])))
        for key in ("name", "amount", "due", "pay_url"):
            form[key].set("")

    def remove_bill(self) -> None:
        selection = self.bills.selection()
        if selection:
            self.act(lambda: self.desk.remove_bill(int(selection[0])))
        else:
            self.message.set("Select a bill first.")

    def add_rule(self) -> None:
        form = self.rule_form
        pot, percent, auto = form["pot"].get().split(". ", 1)[0], form["percent"].get(), bool(form["auto"].get())
        self.act(lambda: self.desk.add_rule(pot, percent, auto))
        form["percent"].set("")

    def remove_rule(self) -> None:
        selection = self.rules.selection()
        if selection:
            self.act(lambda: self.desk.remove_rule(int(selection[0])), think=False)
        else:
            self.message.set("Select a rule first.")

    def save_targets(self) -> None:
        cash, stock, crypto = (self.target_form[key].get() for key in ("cash", "stock", "crypto"))
        self.act(lambda: self.desk.set_targets(cash, stock, crypto))

    # ------------------------------------------------------------------ showing
    def show_thought(self, value: tuple[dict[str, Any], dict[str, Any]]) -> None:
        thought, overview = value
        words = list(thought["applied"])
        words.append(f"{len(thought['waiting'])} decision(s) wait for you." if thought["waiting"] else "Nothing needs a decision right now.")
        self.message.set(" ".join(words))
        self.show_overview(overview)

    def show_change(self, value: tuple[dict[str, Any], dict[str, Any]]) -> None:
        from app.money_desk import report_lines
        result, overview = value
        if result.get("approved"):
            text = f"Approved: {result['title']}. " + (result.get("done") or result.get("next") or "")
            if result.get("open"):
                text += " Use “Open payment page” on the card."
        elif result.get("declined"):
            text = f"Declined: {result['title']}. {result['note']}"
        elif result.get("paid"):
            text = f"Recorded {result['title']} as paid ({result['recorded']})."
        else:
            text = " ".join(report_lines(result))
        self.message.set(text)
        self.show_overview(overview)

    def show_overview(self, value: dict[str, Any]) -> None:
        self.value = value
        rows = value["money"]
        default = self.desk._currency(None)
        main = next((row for row in rows if row["currency"] == default), rows[0] if rows else None)
        if main:
            self.card_values["available"].configure(text=main["available"])
            self.card_values["in_pots"].configure(text=main["in_pots"])
            self.card_values["unallocated"].configure(text=main["unallocated"], fg="#F05D5E" if main["unallocated_cents"] < 0 else self.p.WHITE)
            self.card_values["held"].configure(text=main["held_for_payments"])
            self.card_values["bills"].configure(text=main["bills_30_days"])
        self.card_values["waiting"].configure(text=str(len(value["waiting"])), fg="#E8C46A" if value["waiting"] else self.p.WHITE)
        others = [f"{row['currency']}: available {row['available']}, unallocated {row['unallocated']}" for row in rows if row is not main]
        self.other.configure(text="   ·   ".join(others))
        self.tabs.tab(0, text=f"Decisions ({len(value['waiting']) + len(value['approved'])})" if value["waiting"] or value["approved"] else "Decisions")
        self._show_decisions(value)
        self._show_pots(value)
        self._show_bills(value)
        self._show_rules(value)
        self._show_history(value)

    def _show_decisions(self, value: dict[str, Any]) -> None:
        p = self.p
        for child in self.decision_list.winfo_children():
            child.destroy()
        self.decision_buttons.clear()
        items = value["waiting"] + value["approved"]
        if not items:
            tk.Label(self.decision_list, text="Nothing needs a decision right now.\n\nGive HAYAT something to plan around: create pots with "
                                              "targets (Tax, Emergency fund, Rent), add your bills, or a rule like “set aside 25% of income "
                                              "for tax”. Then press Think now.", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 10),
                     justify="left", anchor="w").pack(fill="x", padx=8, pady=20)
            return
        for item in items:
            card = tk.Frame(self.decision_list, bg=p.PANEL)
            card.pack(fill="x", pady=(0, 8), padx=(0, 6))
            tk.Frame(card, bg=URGENCY_COLOURS.get(item["urgency"], p.MUTED), width=5).pack(side="left", fill="y")
            body = tk.Frame(card, bg=p.PANEL, padx=12, pady=8)
            body.pack(side="left", fill="both", expand=True)
            top = tk.Frame(body, bg=p.PANEL)
            top.pack(fill="x")
            tk.Label(top, text=f"#{item['id']}  {item['title']}", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 11), anchor="w").pack(side="left")
            tag = "approved · waiting for you to pay" if item["status"] == "approved" else item["urgency"]
            tk.Label(top, text=tag, bg=p.PANEL, fg=URGENCY_COLOURS.get(item["urgency"], p.MUTED) if item["status"] == "proposed" else p.GOLD_SOFT,
                     font=("Segoe UI", 8)).pack(side="right")
            for reason in item["reasons"]:
                tk.Label(body, text="•  " + reason, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                         wraplength=960).pack(fill="x")
            if item["status"] == "proposed":
                tk.Label(body, text="If you approve: " + item["approve_means"], bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 9), anchor="w",
                         justify="left", wraplength=960).pack(fill="x", pady=(4, 0))
            buttons = tk.Frame(body, bg=p.PANEL)
            buttons.pack(fill="x", pady=(6, 0))
            if item["status"] == "proposed":
                label = "Seen" if item["kind"] == "alert" else "Approve"
                self.decision_buttons[(item["id"], "approve")] = ttk.Button(
                    buttons, text=label, style="HayatAccent.TButton", command=lambda number=item["id"]: self.approve(number))
                self.decision_buttons[(item["id"], "approve")].pack(side="left")
                if item["kind"] != "alert":
                    ttk.Button(buttons, text="Decline", style="Hayat.TButton",
                               command=lambda number=item["id"]: self.decline(number)).pack(side="left", padx=6)
            else:
                link = item["payload"].get("pay_url") or item["payload"].get("url") or ""
                if link:
                    ttk.Button(buttons, text="Open payment page", style="Hayat.TButton",
                               command=lambda url=link: webbrowser.open(url)).pack(side="left")
                reference = StringVar(self, "")
                if item["kind"] != "rebalance":
                    tk.Label(buttons, text="Bank reference (optional)", bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(10, 4))
                    ttk.Entry(buttons, textvariable=reference, width=18).pack(side="left")
                self.decision_buttons[(item["id"], "paid")] = ttk.Button(
                    buttons, text="I paid it" if item["kind"] != "rebalance" else "Done", style="HayatAccent.TButton",
                    command=lambda number=item["id"], ref=reference: self.paid(number, ref.get()))
                self.decision_buttons[(item["id"], "paid")].pack(side="left", padx=6)
                ttk.Button(buttons, text="Release", style="Hayat.TButton",
                           command=lambda number=item["id"]: self.decline(number)).pack(side="left")

    def _show_pots(self, value: dict[str, Any]) -> None:
        p = self.p
        self.pots.delete(*self.pots.get_children())
        for pot in value["pots"]:
            free = f"{pot['free_cents'] / 100:,.2f} {pot['currency']}"
            self.pots.insert("", "end", iid=str(pot["id"]), values=(pot["name"], pot["purpose"], pot["balance"], pot["target"] or "—",
                                                                    f"{pot['progress']}%" if pot["progress"] is not None else "—", free,
                                                                    pot["sits_in"] or "your account"))
        from app.gui_empty import show_empty
        show_empty(self.pots, "No pots yet. Create one below: Tax, Emergency fund, Rent, Marketing…", p)
        names = [f"{pot['id']}. {pot['name']}" for pot in value["pots"]]
        for form, key, extra in ((self.move_form, "source", [UNALLOCATED]), (self.move_form, "target", [UNALLOCATED]),
                                 (self.bill_form, "pot", [""]), (self.rule_form, "pot", [])):
            form[key + "_widget"].configure(values=extra + names)
            if form[key].get() not in extra + names or (key == "target" and form[key].get() == UNALLOCATED and names):
                # Money usually moves into a pot, so "To" starts at the first pot.
                choices = names + extra if key == "target" else extra + names
                form[key].set(choices[0] if choices else "")
        self._draw_pot_bars()

    def _draw_pot_bars(self) -> None:
        p, canvas = self.p, self.pot_bars
        canvas.delete("all")
        width = canvas.winfo_width()
        if width < 50 or self.value is None:
            return  # not laid out yet; <Configure> draws it
        pots = [pot for pot in self.value["pots"] if pot["target_cents"]][:4]
        if not pots:
            canvas.create_text(12, 48, text="Pots with a target show their progress here.", anchor="w", fill=p.MUTED, font=("Segoe UI", 9))
            return
        row_height = 76 / len(pots)
        for index, pot in enumerate(pots):
            top = 6 + index * row_height
            share = max(0.0, min(pot["balance_cents"] / pot["target_cents"], 1.0))
            canvas.create_text(12, top + row_height / 2, text=pot["name"], anchor="w", fill=p.WHITE, font=("Segoe UI", 9))
            left, right = 170, width - 190
            canvas.create_rectangle(left, top + 4, right, top + row_height - 4, fill=p.PANEL, outline="")
            canvas.create_rectangle(left, top + 4, left + (right - left) * share, top + row_height - 4,
                                    fill=POT_COLOURS[index % len(POT_COLOURS)], outline="")
            canvas.create_text(width - 12, top + row_height / 2, text=f"{pot['balance']} of {pot['target']}", anchor="e", fill=p.MUTED,
                               font=("Segoe UI", 8))

    def _show_bills(self, value: dict[str, Any]) -> None:
        self.bills.delete(*self.bills.get_children())
        for bill in value["bills"]:
            days = bill["days"]
            when = "—" if days is None else "overdue" if days < 0 else "today" if days == 0 else f"{days} days"
            self.bills.insert("", "end", iid=str(bill["id"]), values=(bill["name"], bill["amount"], bill["next_due"] or "finished", when,
                                                                      bill["every"], bill["pot"] or UNALLOCATED.lower(),
                                                                      "business" if bill["business"] else "personal", bill["pay_url"] or "—"))
        from app.gui_empty import show_empty
        show_empty(self.bills, "No bills yet. Add rent, internet, subscriptions… HAYAT proposes paying each one before it is due.", self.p)

    def _show_rules(self, value: dict[str, Any]) -> None:
        self.rules.delete(*self.rules.get_children())
        for rule in value["rules"]:
            self.rules.insert("", "end", iid=str(rule["id"]), values=(rule["pot"], f"{rule['percent']}%",
                                                                      "automatically by HAYAT" if rule["auto"] else "after your approval", rule["created"][:10]))
        from app.gui_empty import show_empty
        show_empty(self.rules, "No rules. Example: Tax pot, 25%, automatic.", self.p)
        for key in ("cash", "stock", "crypto"):
            if value["targets"].get(key) is not None and not self.target_form[key].get():
                self.target_form[key].set(f"{value['targets'][key]:g}")

    def _show_history(self, value: dict[str, Any]) -> None:
        self.history.delete(*self.history.get_children())
        rows = [((item["decided"] or item["created"])[:16].replace("T", " "), item["title"], item["amount"], item["status"]) for item in value["history"]]
        rows += [(move["at"][:16].replace("T", " "), f"{move['pot']}: {move['reason']}", move["amount"], "pot") for move in value["moves"]]
        for index, row in enumerate(sorted(rows, reverse=True)[:80]):
            self.history.insert("", "end", iid=f"h{index}", values=row)
        from app.gui_empty import show_empty
        show_empty(self.history, "Decisions you make and money moved between pots appear here.", self.p)
