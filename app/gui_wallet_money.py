"""Business → Wallet: add money you moved yourself, keep your accounts and cards by nickname, and show pay-now QR codes.

Nothing here moves money or charges a card (app/wallet_money.py): it records, and it shows the owner's own payment
handles so whoever pays uses their own app.
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Any

from app.gui_resources import StringVar
from app.wallet_money import KINDS, KIND_NAMES, WalletMoney, qr_matrix

CURRENCIES = ("INR", "USD", "EUR", "GBP", "AED", "SAR", "CAD", "AUD")
NOT_SAVED = "(not one of my saved methods)"


def wallet(studio: Any) -> WalletMoney:
    return WalletMoney(studio.ledger)


def add_money_form(studio: Any) -> Any:
    from app.gui_business import FormDialog
    methods = wallet(studio).methods()["methods"]
    labels = [f"{item['id']}. {item['kind_name']}: {item['label']}" for item in methods]
    default = studio.ledger.profile()["default_currency"]

    def submit(values: dict) -> str:
        chosen = values["method"]
        method = chosen.split(".", 1)[0] if chosen and chosen != NOT_SAVED else ""
        result = wallet(studio).add_money(values["amount"], method, values["note"], values["date"] or None,
                                          values["reference"] or None, values["currency"] or None)
        studio.refresh()
        return result["note"] if not result["added"] else f"Recorded {result['amount']} you added. Wallet now {result['balance']}."
    return FormDialog(studio, "Add money to your wallet", [
        ("amount", "Amount you added", ""), ("method", "From", labels + [NOT_SAVED] if labels else [NOT_SAVED]),
        ("currency", "Currency", [default] + [c for c in CURRENCIES if c != default]),
        ("note", "Note (optional)", ""), ("date", "Date (YYYY-MM-DD, empty = today)", ""),
        ("reference", "Your bank's reference (optional; stops recording it twice)", "")], submit,
        intro="Record money you moved into the business yourself, in your bank or payment app. HAYAT moves no money and "
              "charges no card; it is kept as money you added, never as income.")


class MoneyMethodsWindow(tk.Toplevel):
    """The owner's accounts, cards and payment handles: nicknames and last 4 digits only."""

    def __init__(self, studio: Any) -> None:
        super().__init__(studio)
        self.studio, self.p = studio, studio.palette
        p = self.p
        self.title("HAYAT · Money methods")
        self.configure(bg=p.BLACK, padx=16, pady=12)
        self.transient(studio)
        self.bind("<Escape>", lambda _event: self.destroy())
        tk.Label(self, text="Your accounts, cards and ways to be paid", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 14),
                 anchor="w").pack(fill="x")
        tk.Label(self, text="Kept by a nickname and at most the last 4 digits. Full card or account numbers, expiry dates, CVVs, PINs and "
                            "passwords are refused: they belong only in your bank's own app. HAYAT cannot charge, debit or move money "
                            "with any of these. A UPI ID, PayPal.me name or Stripe / Razorpay / Wise link appears on your invoices "
                            "as a pay-now option.", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=760).pack(fill="x", pady=(2, 8))
        self.tree = ttk.Treeview(self, columns=("kind", "label", "receive", "currency"), show="headings", height=6)
        for key, title, width in (("kind", "Kind", 120), ("label", "Name", 260), ("receive", "Pay-now handle", 260), ("currency", "Currency", 80)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=key in ("label", "receive"))
        self.tree.pack(fill="both", expand=True)
        form = tk.Frame(self, bg=p.PANEL, padx=10, pady=8)
        form.pack(fill="x", pady=(8, 0))
        self.fields: dict[str, StringVar] = {}
        for column, (key, label, width) in enumerate((("kind", "Kind", 12), ("nickname", "Nickname", 20), ("institution", "Bank / brand", 14),
                                                      ("last4", "Last 4", 6), ("currency", "Currency", 6), ("receive", "UPI ID / PayPal.me / link", 28))):
            tk.Label(form, text=label, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8)).grid(row=0, column=column, sticky="w", padx=3)
            variable = StringVar(self, "bank" if key == "kind" else studio.ledger.profile()["default_currency"] if key == "currency" else "")
            if key in ("kind", "currency"):
                widget = ttk.Combobox(form, textvariable=variable, values=list(KINDS) if key == "kind" else list(CURRENCIES),
                                      state="readonly", width=width)
            else:
                widget = ttk.Entry(form, textvariable=variable, width=width)
            widget.grid(row=1, column=column, sticky="we", padx=3)
            self.fields[key] = variable
        row = tk.Frame(self, bg=p.BLACK)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Save method", style="HayatAccent.TButton", command=self.save).pack(side="left")
        ttk.Button(row, text="Remove selected", style="Hayat.TButton", command=self.remove).pack(side="left", padx=6)
        ttk.Button(row, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")
        self.message = StringVar(self, "")
        tk.Label(self, textvariable=self.message, bg=p.BLACK, fg=p.GOLD_SOFT, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=760).pack(fill="x", pady=(6, 0))
        self.refresh()
        from app.gui_windows import place_over
        place_over(self, studio, (880, 640))

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for item in wallet(self.studio).methods()["methods"]:
            self.tree.insert("", "end", iid=str(item["id"]), values=(KIND_NAMES[item["kind"]], item["label"], item["receive"] or "—", item["currency"]))
        from app.gui_empty import show_empty
        show_empty(self.tree, "No methods yet. Add your bank account or card (nickname and last 4 digits), or a UPI ID so clients can "
                              "pay you by QR.", self.p)

    def save(self) -> None:
        values = {key: variable.get().strip() for key, variable in self.fields.items()}
        try:
            result = wallet(self.studio).add_method(values["kind"], values["nickname"], values["last4"], values["institution"],
                                                    values["currency"], values["receive"])
        except ValueError as exc:
            self.message.set(str(exc))
            return
        for key in ("nickname", "institution", "last4", "receive"):
            self.fields[key].set("")
        self.message.set(result["note"])
        self.refresh()
        self.studio.refresh()

    def remove(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self.message.set("Select a method first.")
            return
        result = wallet(self.studio).remove_method(int(selection[0]))
        self.message.set(f"Removed {result['label']}.")
        self.refresh()


class PayNowWindow(tk.Toplevel):
    """A pay-now QR and links for one invoice's balance; the payer's own app does the paying."""

    def __init__(self, studio: Any, number: str) -> None:
        super().__init__(studio)
        self.studio, self.p = studio, studio.palette
        p = self.p
        self.title(f"HAYAT · Pay now · {number}")
        self.configure(bg=p.BLACK, padx=16, pady=12)
        self.transient(studio)
        self.bind("<Escape>", lambda _event: self.destroy())
        try:
            self.value = wallet(studio).payment_request(number=number)
        except ValueError as exc:
            self.value = None
            tk.Label(self, text=str(exc), bg=p.BLACK, fg=p.ALERT, font=("Segoe UI", 10), wraplength=440, justify="left").pack(fill="x")
            ttk.Button(self, text="Money methods…", style="HayatAccent.TButton",
                       command=lambda: (self.destroy(), MoneyMethodsWindow(studio))).pack(anchor="w", pady=(10, 0))
            return
        value = self.value
        tk.Label(self, text=f"{value['label']} · {value['amount']}" + (f" · {value['client']}" if value["client"] else ""), bg=p.BLACK,
                 fg=p.WHITE, font=("Segoe UI Semibold", 14), anchor="w").pack(fill="x")
        qr = next((option for option in value["options"] if option["qr"]), None)
        if qr:
            matrix = qr_matrix(qr["link"])
            cell, border = 6, 4
            size = (len(matrix) + 2 * border) * cell
            canvas = tk.Canvas(self, width=size, height=size, bg="white", highlightthickness=0)
            canvas.pack(pady=(8, 4))
            for row, cells in enumerate(matrix):
                for column, dark in enumerate(cells):
                    if dark:
                        left, top = (column + border) * cell, (row + border) * cell
                        canvas.create_rectangle(left, top, left + cell, top + cell, fill="black", outline="")
            self.qr_canvas = canvas
        for option in value["options"]:
            box = tk.Frame(self, bg=p.PANEL, padx=10, pady=6)
            box.pack(fill="x", pady=3)
            tk.Label(box, text=option["title"], bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 10), anchor="w").pack(fill="x")
            tk.Label(box, text=option["how"], bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=460).pack(fill="x")
            buttons = tk.Frame(box, bg=p.PANEL)
            buttons.pack(fill="x", pady=(4, 0))
            ttk.Button(buttons, text="Copy link", style="Compact.Hayat.TButton",
                       command=lambda link=option["link"]: self.copy(link)).pack(side="left")
            if option["link"].startswith("https://"):
                ttk.Button(buttons, text="Open", style="Compact.Hayat.TButton",
                           command=lambda link=option["link"]: webbrowser.open(link)).pack(side="left", padx=6)
        self.message = StringVar(self, value["note"] + (f" QR picture: {value['qr_picture']}" if value["qr_picture"] else ""))
        tk.Label(self, textvariable=self.message, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=460).pack(fill="x", pady=(8, 0))
        from app.gui_windows import place_over
        place_over(self, studio)

    def copy(self, link: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(link)
        self.message.set("Copied. Paste it into your message to the client.")
