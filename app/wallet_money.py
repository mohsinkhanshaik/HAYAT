"""Money in the HAYAT wallet: the owner's accounts and cards, money they add, and getting paid for real.

What HAYAT does and does not do, in code:
* **Money methods:** the owner's bank accounts, cards, UPI IDs, PayPal, Stripe, Razorpay, Wise and Payoneer, kept by a
  nickname, the bank or brand, and at most the last 4 digits. A full card or account number (any run of 6+ digits,
  or a 13–19 digit number that passes the card checksum), an expiry date, a CVV, a PIN, a password or an OTP is
  refused before anything is saved: those belong in the bank's own app, never on this computer.
* **Adding money:** "add 5,000 to my wallet from HDFC savings" records money the owner moved themselves, in their
  own bank or payment app. HAYAT moves no money and charges no card. The wallet shows it as money added, never as
  income, so profit and the monthly report stay true.
* **Getting paid for real:** the owner's *receiving* handles (a UPI ID, a PayPal.me name, a Stripe, Razorpay or
  Wise payment link) turn into pay-now options on every invoice: a UPI QR with the amount and invoice number
  filled in, a PayPal.me link with the amount, the payment link. The payer's own app moves the money straight to
  the owner; HAYAT never holds, touches or sees it. When it arrives, the owner records the payment.

A real wallet that takes cards would need a licensed payment processor, card-data compliance (PCI DSS) and, to
hold money for others, a money-transmitter licence; and HAYAT's own rule (since 2026-10-03) is that it never
moves money. So this keeps records and makes it easy for others to pay the owner directly.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from app.tools.business import _currency, _day, cents, money

KINDS = ("bank", "card", "upi", "paypal", "stripe", "razorpay", "wise", "payoneer", "cash", "other")
KIND_NAMES = {"bank": "Bank account", "card": "Card", "upi": "UPI", "paypal": "PayPal", "stripe": "Stripe", "razorpay": "Razorpay",
              "wise": "Wise", "payoneer": "Payoneer", "cash": "Cash", "other": "Other"}
#: Kinds whose receiving handle can go on an invoice.
RECEIVES = ("upi", "paypal", "stripe", "razorpay", "wise", "payoneer", "other")
SECRET_WORDS = re.compile(r"(?i)\b(?:password|passcode|pin|cvv|cvc|cvv2|otp|one[\s-]?time|security\s+code|netbanking\s+id|login)\b")
EXPIRY = re.compile(r"\b(?:0[1-9]|1[0-2])\s*/\s*(?:\d{2}|20\d{2})\b")
VPA = re.compile(r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$")
PAYPAL = re.compile(r"^(?:https?://)?(?:www\.)?(?:paypal\.me/)?([A-Za-z0-9]{1,20})/?$")
PAY_HOSTS = ("buy.stripe.com", "checkout.stripe.com", "donate.stripe.com", "rzp.io", "razorpay.me", "pages.razorpay.com",
             "wise.com", "paypal.com", "www.paypal.com", "paypal.me", "payoneer.com", "link.payoneer.com", "ko-fi.com",
             "buymeacoffee.com", "www.buymeacoffee.com", "cash.app", "venmo.com")


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def refuse_secrets(*values: str) -> None:
    """Stop before anything that could move money is written down: numbers, expiry dates, CVVs, PINs, passwords."""
    for value in values:
        text = str(value or "")
        compact = re.sub(r"[\s-]", "", text)
        for run in re.findall(r"\d{13,19}", compact):
            if _luhn(run):
                raise ValueError("That looks like a full card number. HAYAT never keeps card numbers: give a nickname and only the "
                                 "last 4 digits.")
        if re.search(r"\d{6,}", compact):
            raise ValueError("Don't give HAYAT account or card numbers: a nickname and only the last 4 digits are enough.")
        if SECRET_WORDS.search(text):
            raise ValueError("Never give HAYAT a password, PIN, CVV or one-time code: they belong only in your bank's own app.")
        if EXPIRY.search(text):
            raise ValueError("Card expiry dates are not kept: a nickname and the last 4 digits are enough.")


class WalletMoney:
    def __init__(self, ledger: Any) -> None:
        self.ledger = ledger

    # ------------------------------------------------------------------ money methods
    def add_method(self, kind: str, nickname: str = "", last4: str = "", institution: str = "", currency: str | None = None,
                   receive: str = "") -> dict[str, Any]:
        kind = (kind or "").strip().lower()
        if kind not in KINDS:
            raise ValueError("Kind is one of: " + ", ".join(KINDS) + ".")
        nickname, institution, last4 = " ".join(str(nickname or "").split())[:60], " ".join(str(institution or "").split())[:60], str(last4 or "").strip()
        if not nickname:
            nickname = f"{institution} {KIND_NAMES[kind].lower()}".strip() if institution else KIND_NAMES[kind]
        refuse_secrets(nickname, institution)
        if last4 and not re.fullmatch(r"\d{4}", last4):
            raise ValueError("Only the last 4 digits, or nothing.")
        receive = self._receive(kind, str(receive or "").strip())
        cur = _currency(currency, self.ledger._default_currency())
        now = datetime.now(UTC).isoformat(timespec="seconds")
        method_id = self.ledger._run(lambda db: db.execute(
            "INSERT INTO money_methods (kind, nickname, institution, last4, currency, receive, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, nickname, institution, last4, cur, receive, now)).lastrowid)
        method = self.method(method_id)
        return {"money_view": True, "method_added": True, **method,
                "note": "Saved on this computer: a nickname" + (" and the last 4 digits" if last4 else "") + (
                    "; it now appears as a pay-now option on your invoices." if receive else ".")
                        + " HAYAT can't charge or move money with it; you add money in your own bank or payment app."}

    @staticmethod
    def _receive(kind: str, value: str) -> str:
        if not value:
            return ""
        if kind not in RECEIVES:
            raise ValueError(f"A {KIND_NAMES[kind].lower()} has no receiving handle; for clients' bank transfers, put your "
                             "details in Business → profile → payment instructions.")
        if kind == "upi":
            if not VPA.fullmatch(value):
                raise ValueError("A UPI ID looks like name@bank, e.g. mohsin@okhdfcbank.")
            return value.lower()
        if kind == "paypal":
            match = PAYPAL.fullmatch(value)
            if not match:
                raise ValueError("Give your PayPal.me name (paypal.me/yourname) so clients can pay you.")
            return match.group(1)
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("A payment link must start with https:// (your Stripe, Razorpay, Wise or Payoneer payment page).")
        refuse_secrets(parsed.netloc)
        return value

    def method(self, method_id: int) -> dict[str, Any]:
        row = self.ledger._run(lambda db: db.execute("SELECT * FROM money_methods WHERE id=?", (int(method_id),)).fetchone())
        if row is None or row["removed"]:
            raise ValueError(f"No money method {method_id}. Say “show my money methods”.")
        return self._public(dict(row))

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        shown = row["nickname"] + (f" ····{row['last4']}" if row["last4"] else "")
        return {"id": row["id"], "kind": row["kind"], "kind_name": KIND_NAMES[row["kind"]], "nickname": row["nickname"],
                "institution": row["institution"], "last4": row["last4"], "currency": row["currency"], "receive": row["receive"],
                "label": shown, "receives": bool(row["receive"])}

    def methods(self) -> dict[str, Any]:
        rows = self.ledger._run(lambda db: [dict(row) for row in db.execute("SELECT * FROM money_methods WHERE removed IS NULL ORDER BY id")])
        return {"money_view": True, "methods_list": True, "methods": [self._public(row) for row in rows],
                "basis": "Nicknames and last 4 digits only. HAYAT cannot charge, debit or move money with any of these."}

    def remove_method(self, method_id: int) -> dict[str, Any]:
        method = self.method(method_id)
        self.ledger._run(lambda db: db.execute("UPDATE money_methods SET removed=? WHERE id=?",
                                               (datetime.now(UTC).isoformat(timespec="seconds"), int(method_id))))
        return {"money_view": True, "method_removed": True, **method}

    def find_method(self, words: str) -> dict[str, Any] | None:
        """A method by its number, nickname, bank or brand ("hdfc", "my visa", "savings")."""
        text = " ".join(str(words or "").lower().replace("my ", "").split())
        if not text:
            return None
        methods = self.methods()["methods"]
        if text.isdigit():
            return next((item for item in methods if item["id"] == int(text) or item["last4"] == text), None)
        scored = []
        for item in methods:
            haystack = f"{item['nickname']} {item['institution']} {item['kind']} {item['kind_name']} {item['last4']}".lower()
            hits = sum(word in haystack for word in re.findall(r"[a-z0-9]+", text))
            if hits:
                scored.append((hits, -item["id"], item))
        return max(scored)[2] if scored else None

    # ------------------------------------------------------------------ adding money
    def add_money(self, amount: str, method: str = "", note: str = "", added_on: str | None = None,
                  reference: str | None = None, currency: str | None = None) -> dict[str, Any]:
        value = cents(amount)
        if value <= 0:
            raise ValueError("Amount must be above 0.")
        refuse_secrets(note, reference or "")
        chosen = self.find_method(method) if method else None
        if method and chosen is None:
            raise ValueError(f"No money method matches “{method}”. Add it first (“add my HDFC savings account ending 1234”), "
                             "or record it without one.")
        cur = _currency(currency, chosen["currency"] if chosen else self.ledger._default_currency())
        day = _day(added_on)
        if day > date.today().isoformat():
            raise ValueError("Money added can't be dated in the future.")
        reference = (reference or "").strip() or None
        now = datetime.now(UTC).isoformat(timespec="seconds")

        def work(db):
            if reference and db.execute("SELECT 1 FROM funding WHERE reference=?", (reference,)).fetchone():
                return None
            return db.execute("INSERT INTO funding (amount_cents, currency, method_id, note, added_on, reference, recorded) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?)", (value, cur, chosen["id"] if chosen else None, (note or "").strip()[:200],
                                                               day, reference, now)).lastrowid
        funding_id = self.ledger._run(work)
        wallet = self.ledger.wallet(activity=0)
        balance = next((row["available"] for row in wallet["balances"] if row["currency"] == cur), money(0, cur))
        if funding_id is None:
            return {"money_view": True, "added": False, "duplicate": True, "amount": money(value, cur),
                    "note": f"Already recorded: reference {reference}. Nothing was added twice.", "balance": balance}
        return {"money_view": True, "added": True, "id": funding_id, "amount": money(value, cur), "currency": cur,
                "from": chosen["label"] if chosen else "", "on": day, "balance": balance,
                "note": "Recorded as money you added yourself, not income. HAYAT moved nothing: make the transfer in your "
                        "bank or payment app if you haven't already."}

    # ------------------------------------------------------------------ getting paid
    def receive_options(self, amount_cents: int, currency: str, label: str, payee: str = "") -> list[dict[str, Any]]:
        """Pay-now options for an amount, from the owner's receiving handles."""
        options = []
        amount = f"{amount_cents / 100:.2f}"
        payee = payee or self.ledger.profile().get("business_name") or self.ledger.profile().get("owner_name") or "Payee"
        for method in self.methods()["methods"]:
            if not method["receive"]:
                continue
            if method["kind"] == "upi":
                if currency != "INR":
                    continue  # UPI is in rupees only
                link = (f"upi://pay?pa={quote(method['receive'], safe='@.-_')}&pn={quote(payee)}&am={amount}&cu=INR"
                        f"&tn={quote(label[:60])}")
                options.append({"kind": "upi", "title": f"UPI to {method['receive']}", "link": link, "qr": True,
                                "how": "Scan with any UPI app (GPay, PhonePe, Paytm, BHIM); the amount and reference are filled in."})
            elif method["kind"] == "paypal":
                options.append({"kind": "paypal", "title": f"PayPal.me/{method['receive']}",
                                "link": f"https://paypal.me/{method['receive']}/{amount}{currency}", "qr": False,
                                "how": "Opens PayPal with the amount filled in; card or PayPal balance."})
            else:
                options.append({"kind": method["kind"], "title": f"{method['kind_name']} payment page", "link": method["receive"],
                                "qr": False, "how": f"Pay {money(amount_cents, currency)} on your payment page; mention {label}."})
        return options

    def payment_request(self, number: str = "", amount: str = "", currency: str | None = None, note: str = "") -> dict[str, Any]:
        """Pay-now options and a QR picture for an invoice's balance, or for any amount."""
        if number:
            document = self.ledger.document(number)
            if document["kind"] != "invoice" or document["status"] == "void":
                raise ValueError("Payment requests are for open invoices.")
            value, cur, label = document["balance_cents"], document["currency"], document["number"]
            if value <= 0:
                raise ValueError(f"{label} is already paid.")
            who = document.get("client", "")
        else:
            value = cents(amount)
            if value <= 0:
                raise ValueError("Give an invoice number or an amount.")
            cur = _currency(currency, self.ledger._default_currency())
            label = (note or "Payment").strip()[:60]
            refuse_secrets(label)
            who = ""
        options = self.receive_options(value, cur, label)
        if not options:
            raise ValueError("Add a way to be paid first: “my UPI ID is name@bank”, “my PayPal.me is yourname”, or a Stripe or "
                             "Razorpay payment link." + (" UPI works for rupee (INR) amounts only." if cur != "INR" else ""))
        picture = ""
        qr = next((option for option in options if option["qr"]), None)
        if qr:
            folder = Path(self.ledger.output) / "pay"
            folder.mkdir(parents=True, exist_ok=True)
            picture = str(save_qr(qr["link"], folder / f"pay-{re.sub(r'[^A-Za-z0-9-]+', '-', label)[:40]}-{value}.png"))
        return {"money_view": True, "payment_request": True, "label": label, "client": who, "amount": money(value, cur),
                "options": options, "qr_picture": picture,
                "note": "Share the QR or a link with whoever pays. Their app sends the money straight to your account; HAYAT never "
                        "touches it. When it arrives, record it: “record payment for " + (label if number else "the invoice") + "”."}


# ---------------------------------------------------------------------- QR pictures and the invoice PDF
def qr_matrix(payload: str) -> list[list[bool]]:
    from reportlab.graphics.barcode import qrencoder
    code = qrencoder.QRCode(None, qrencoder.QRErrorCorrectLevel.M)
    code.addData(payload)
    code.make()
    count = code.getModuleCount()
    return [[bool(code.isDark(row, column)) for column in range(count)] for row in range(count)]


def save_qr(payload: str, path: Path, scale: int = 8, border: int = 4) -> Path:
    from PIL import Image, ImageDraw
    matrix = qr_matrix(payload)
    size = (len(matrix) + 2 * border) * scale
    picture = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(picture)
    for row, cells in enumerate(matrix):
        for column, dark in enumerate(cells):
            if dark:
                left, top = (column + border) * scale, (row + border) * scale
                draw.rectangle([left, top, left + scale - 1, top + scale - 1], fill="black")
    picture.save(path)
    return path


def pdf_payment_block(ledger: Any, document: dict[str, Any], strong: Any, style: Any, text: Any) -> list[Any]:
    """Flowables for the invoice PDF: each pay-now option, with a UPI QR when the invoice is in rupees."""
    if document["kind"] != "invoice" or document.get("balance_cents", document["total_cents"]) <= 0:
        return []
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.platypus import Paragraph
    options = WalletMoney(ledger).receive_options(document.get("balance_cents", document["total_cents"]), document["currency"], document["number"])
    flow: list[Any] = []
    for option in options:
        flow.append(Paragraph(text(f"{option['title']}: {option['how']}"), style))
        if option["qr"]:
            widget = QrCodeWidget(option["link"])
            left, bottom, right, top = widget.getBounds()
            drawing = Drawing(110, 110, transform=[110 / (right - left), 0, 0, 110 / (top - bottom), 0, 0])
            drawing.add(widget)
            flow.append(drawing)
        elif option["link"].startswith("https://"):
            flow.append(Paragraph(text(option["link"]), style))
    return flow


# ---------------------------------------------------------------------- tools, chat, replies
TOOL_NAMES = ("money_method_add", "money_methods", "money_method_remove", "wallet_add_money", "payment_request")


def register_wallet_money_tools(registry: Any, ledger: Any) -> WalletMoney:
    wallet = WalletMoney(ledger)

    def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    text = {"type": "string", "maxLength": 120}
    registry.register("money_method_add", "Save one of the owner's bank accounts, cards or payment handles by nickname, brand and at most "
                      "the last 4 digits; a UPI ID, PayPal.me name or Stripe/Razorpay/Wise link becomes a pay-now option on "
                      "invoices. Full numbers, expiry dates, CVVs, PINs and passwords are refused. Nothing can be charged.",
                      obj({"kind": {"enum": list(KINDS)}, "nickname": text, "last4": {"type": "string", "maxLength": 4},
                           "institution": text, "currency": {"type": "string", "maxLength": 3}, "receive": {"type": "string", "maxLength": 300}},
                          ("kind",)), "low", "filesystem_write_project", wallet.add_method)
    registry.register("money_methods", "List the owner's saved accounts, cards and payment handles (nicknames and last 4 digits).",
                      obj({}), "low", "filesystem_read", wallet.methods)
    registry.register("money_method_remove", "Remove a saved account, card or payment handle.",
                      obj({"method_id": {"type": "integer", "minimum": 1}}, ("method_id",)), "low", "filesystem_write_project",
                      wallet.remove_method)
    registry.register("wallet_add_money", "Record money the owner added to the business themselves (from one of their accounts or cards, "
                      "moved in their own bank or payment app). Shown in the wallet as money added, never as income. HAYAT moves "
                      "no money and charges no card.",
                      obj({"amount": {"type": "string", "maxLength": 20}, "method": text, "note": text, "added_on": {"type": "string", "maxLength": 10},
                           "reference": text, "currency": {"type": "string", "maxLength": 3}}, ("amount",)),
                      "low", "filesystem_write_project", wallet.add_money)
    registry.register("payment_request", "Pay-now options for an open invoice's balance or any amount: a UPI QR picture with the amount "
                      "filled in (rupees), a PayPal.me link with the amount, and the owner's payment links. The payer's app sends "
                      "the money straight to the owner; HAYAT never touches it.",
                      obj({"number": {"type": "string", "maxLength": 20}, "amount": {"type": "string", "maxLength": 20},
                           "currency": {"type": "string", "maxLength": 3}, "note": text}),
                      "low", "filesystem_write_project", wallet.payment_request)
    return wallet


_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_AMOUNT = r"(?P<cur1>[$₹€£])?\s?(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s?(?P<k>k)?\s*(?P<cur2>rupees?|rs\.?|inr|dollars?|usd|euros?|eur|pounds?|gbp)?"
_ADD_MONEY = re.compile(_LEAD + r"(?:add|load|top\s*up|record|log|deposit)\s+" + _AMOUNT +
                        r"\s+(?:(?:to|into|in)\s+(?:my\s+|the\s+)?(?:hayat\s+)?wallet|(?:as\s+)?(?:money\s+)?(?:i\s+)?(?:added|put\s+in|deposited))"
                        r"(?:\s+(?:from|via|using|with)\s+(?P<method>[\w .&'-]{2,40}?))?\s*[?.!]*$", re.IGNORECASE)
_ADD_ACCOUNT = re.compile(_LEAD + r"(?:add|save|remember)\s+my\s+(?P<name>[\w .&'-]{1,40}?)\s*(?P<kind>savings\s+account|current\s+account|"
                          r"bank\s+account|account|debit\s+card|credit\s+card|card)(?:\s+(?:ending(?:\s+in|\s+with)?|last\s+4(?:\s+digits)?)\s+(?P<last4>\d{4}))?"
                          r"(?:\s+(?:called|named|as)\s+(?P<nick>[\w .&'-]{1,40}))?\s*[?.!]*$", re.IGNORECASE)
_UPI = re.compile(_LEAD + r"(?:my\s+upi(?:\s+id)?\s+is|add\s+(?:my\s+)?upi(?:\s+id)?|save\s+(?:my\s+)?upi(?:\s+id)?)\s+(?P<vpa>[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,63})\s*[?.!]*$",
                  re.IGNORECASE)
_PAYPAL = re.compile(_LEAD + r"(?:my\s+paypal(?:\.me)?(?:\s+(?:name|link|handle))?\s+is|add\s+(?:my\s+)?paypal(?:\.me)?)\s+(?P<handle>(?:https?://)?(?:www\.)?(?:paypal\.me/)?[A-Za-z0-9]{1,20})\s*[?.!]*$",
                     re.IGNORECASE)
_LINK = re.compile(_LEAD + r"(?:my\s+|add\s+(?:my\s+)?)(?P<kind>stripe|razorpay|wise|payoneer)\s+(?:payment\s+)?(?:link|page)(?:\s+is)?\s+(?P<url>https://\S+)\s*[?.!]*$",
                   re.IGNORECASE)
_LIST = re.compile(_LEAD + r"(?:show|list|what\s+are)\s+(?:me\s+)?my\s+(?:money|payment)\s+methods|(?:show\s+)?my\s+(?:cards|accounts)\s+and\s+(?:cards|accounts)"
                   r"|(?:show\s+)?my\s+saved\s+(?:cards|accounts|payment\s+methods)\s*[?.!]*$", re.IGNORECASE)
_REMOVE = re.compile(_LEAD + r"(?:remove|delete|forget)\s+(?:money|payment)\s+method\s+#?(?P<id>\d+)\s*[?.!]*$", re.IGNORECASE)
_REQUEST = re.compile(_LEAD + r"(?:create|generate|show|give\s+me|get)\s+(?:a\s+|the\s+)?(?:pay[\s-]?now|payment|upi|pay)\s+(?:qr(?:\s+code)?|link|request)"
                      r"\s+for\s+(?:invoice\s+)?(?:(?P<number>(?:INV|inv)-\d{4}-\d{4})|" + _AMOUNT.replace("(?P<amount>", "(?P<value>") + r")\s*[?.!]*$",
                      re.IGNORECASE)
_UNITS = {"rupee": "INR", "rupees": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "dollar": "USD", "dollars": "USD", "usd": "USD",
          "euro": "EUR", "euros": "EUR", "eur": "EUR", "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}


def _amount(match: re.Match[str], key: str = "amount") -> tuple[str, str]:
    number = float(match[key].replace(",", "")) * (1000 if match["k"] else 1)
    unit = (match["cur1"] or match["cur2"] or "").lower()
    return f"{number:.2f}", _UNITS.get(unit, "")


def money_request(text: str) -> dict[str, Any] | None:
    raw = " ".join(str(text or "").split())
    if not raw or len(raw) > 200:
        return None
    if match := _ADD_MONEY.match(raw):
        amount, currency = _amount(match)
        return {"tool": "wallet_add_money", "arguments": {"amount": amount, **({"currency": currency} if currency else {}),
                                                           **({"method": match["method"].strip()} if match["method"] else {})}}
    if match := _UPI.match(raw):
        return {"tool": "money_method_add", "arguments": {"kind": "upi", "nickname": "UPI", "receive": match["vpa"]}}
    if match := _PAYPAL.match(raw):
        return {"tool": "money_method_add", "arguments": {"kind": "paypal", "nickname": "PayPal", "receive": match["handle"]}}
    if match := _LINK.match(raw):
        kind = match["kind"].lower()
        return {"tool": "money_method_add", "arguments": {"kind": kind, "nickname": KIND_NAMES[kind], "receive": match["url"]}}
    if match := _ADD_ACCOUNT.match(raw):
        kind = "card" if "card" in match["kind"].lower() else "bank"
        institution = match["name"].strip()
        nickname = (match["nick"] or f"{institution} {match['kind'].lower()}").strip()
        return {"tool": "money_method_add", "arguments": {"kind": kind, "nickname": nickname, "institution": institution,
                                                           **({"last4": match["last4"]} if match["last4"] else {})}}
    if _LIST.match(raw):
        return {"tool": "money_methods", "arguments": {}}
    if match := _REMOVE.match(raw):
        return {"tool": "money_method_remove", "arguments": {"method_id": int(match["id"])}}
    if match := _REQUEST.match(raw):
        if match["number"]:
            return {"tool": "payment_request", "arguments": {"number": match["number"].upper()}}
        amount, currency = _amount(match, "value")
        return {"tool": "payment_request", "arguments": {"amount": amount, **({"currency": currency} if currency else {})}}
    return None


def direct_plan(text: str, names: Any) -> dict[str, Any] | None:
    found = money_request(text)
    if not found or found["tool"] not in names:
        return None
    return {"description": "Keep the owner's money records: methods by nickname, money they added, pay-now options. Nothing moves.",
            "tool": found["tool"], "arguments": found["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}


def report_lines(value: dict[str, Any]) -> list[str]:
    if value.get("method_added"):
        return [f"Saved {value['kind_name']}: {value['label']}" + (f" · receives at {value['receive']}" if value["receive"] else "") + ".",
                value["note"]]
    if value.get("methods_list"):
        rows = value["methods"]
        if not rows:
            return ["No money methods saved yet. Say “add my HDFC savings account ending 1234”, “add my visa card ending 4242” "
                    "or “my UPI ID is name@bank”.", value["basis"]]
        return ["Your money methods:"] + [f"  {row['id']}. {row['kind_name']}: {row['label']}" + (f" · pay-now: {row['receive']}" if row["receive"] else "")
                                          for row in rows] + [value["basis"]]
    if value.get("method_removed"):
        return [f"Removed {value['kind_name']}: {value['label']}."]
    if "added" in value and value.get("money_view"):
        if not value["added"]:
            return [value["note"]]
        return [f"Recorded {value['amount']} added to your HAYAT wallet" + (f" from {value['from']}" if value["from"] else "")
                + f" on {value['on']}. Wallet now: {value['balance']}.", value["note"]]
    if value.get("payment_request"):
        lines = [f"Pay-now for {value['label']}" + (f" ({value['client']})" if value.get("client") else "") + f": {value['amount']}"]
        lines += [f"  • {option['title']}: {option['link']}\n    {option['how']}" for option in value["options"]]
        if value["qr_picture"]:
            lines.append(f"QR picture: {value['qr_picture']}")
        lines.append(value["note"])
        return lines
    return []


def report(history: list[dict[str, Any]]) -> str | None:
    for event in history or []:
        if event.get("tool") not in TOOL_NAMES:
            continue
        result = event.get("result") or {}
        if result.get("success") and (event.get("verification") or {}).get("passed"):
            lines = report_lines(result.get("output") or {})
            if lines:
                return "\n".join(lines)
    return None
