"""The HAYAT Wallet desk: HAYAT decides what to do with the money in the wallet, and the owner approves.

The wallet is the business books' balance (app/tools/business.py): money the owner earned or added, which sits in
the owner's own bank, PayPal or store account. Here HAYAT works with it instead of only reading it:

* **Pots** hold parts of the wallet for a purpose: Tax, Emergency fund, Rent, Marketing, a laptop. A pot can name
  the account its money actually sits in. Unallocated = available in the wallet - what the pots hold.
* **Bills** with due dates and repeats (weekly, monthly, yearly), the pot that pays them, and the biller's own
  https payment page.
* **Rules** the owner sets once: "set aside 25 % of income for Tax". With *automatically*, HAYAT applies the rule to
  every new payment itself; otherwise each one is proposed.
* **Decisions:** ``think()`` reads the books and proposes what to do, each with its reasons, the numbers, and what
  approving or declining means: fill the pots by priority, top up a pot before a bill is due, pay a due bill,
  start an emergency fund or a tax pot, rebalance the portfolio when it drifts from your targets, warn when the
  money will not cover the bills. A **spend request** ("can I afford a 300 dollar laptop?") gets HAYAT's verdict.

Permission, in code:

* Nothing changes until the owner approves, in the HAYAT Wallet window or by saying "approve decision N" at this
  computer. No tool can approve, so neither the model nor a web page can approve its own proposal, and a remote
  device cannot either (the assistant checks the principal). Declined proposals are not proposed again.
* Inside the wallet, an approval acts at once: money moves between pots and a rule's share is set aside.
* Outside it, money moves only in the owner's own bank or app. An approved bill or purchase waits ("pay it, then
  say decision N is paid"); HAYAT opens the biller's page, never fills in card details, never pays. When the owner
  reports it paid, the books record it (a business expense, or money taken out for a personal bill) and the pot
  goes down by the same amount. Payments and purchases by HAYAT itself stay refused (config/permissions.yaml).
"""
from __future__ import annotations

import calendar
import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.tools.business import money
from app.tools.products import CURRENCIES, cents

EVERY = ("once", "week", "month", "year")
PURPOSES = ("emergency", "tax", "bills", "savings", "spending", "investing", "other")
#: Lower fills first when HAYAT shares out unallocated money.
PRIORITY = {"emergency": 0, "tax": 1, "bills": 2, "savings": 3, "investing": 4, "spending": 5, "other": 6}
#: Proposals HAYAT makes itself; refreshed (or dropped) each time it thinks.
AUTO_KINDS = ("allocate", "pay_bill", "rule_share", "create_pot", "rebalance", "alert")
EXTERNAL = ("pay_bill", "spend", "rebalance")
URGENCY = {"high": 0, "medium": 1, "low": 2}
BILL_WINDOW_DAYS = 10


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _day(value: Any, name: str = "Date") -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError(f"{name}: give it as YYYY-MM-DD.") from None


def _advance(day: date, every: str) -> date | None:
    if every == "once":
        return None
    if every == "week":
        return day + timedelta(days=7)
    if every == "year":
        try:
            return day.replace(year=day.year + 1)
        except ValueError:  # 29 February
            return day.replace(year=day.year + 1, day=28)
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def _https(url: str) -> str:
    url = (url or "").strip()
    if url and not re.fullmatch(r"https://[^\s<>\"']{4,300}", url):
        raise ValueError("A payment page must be an https:// link.")
    return url


class MoneyDesk:
    def __init__(self, ledger: Any, portfolio: Any = None) -> None:
        self.ledger, self.portfolio = ledger, portfolio
        self.ledger._run(lambda db: db.executescript("""
            CREATE TABLE IF NOT EXISTS pots (id INTEGER PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL,
                target_cents INTEGER NOT NULL DEFAULT 0, purpose TEXT NOT NULL DEFAULT 'other', method_id INTEGER,
                created TEXT NOT NULL, removed TEXT);
            CREATE TABLE IF NOT EXISTS pot_moves (id INTEGER PRIMARY KEY, pot_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
                reason TEXT NOT NULL, decision_id INTEGER, at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pot_rules (id INTEGER PRIMARY KEY, pot_id INTEGER NOT NULL, percent TEXT NOT NULL,
                auto INTEGER NOT NULL DEFAULT 0, since TEXT NOT NULL, created TEXT NOT NULL, removed TEXT);
            CREATE TABLE IF NOT EXISTS rule_applied (income_key TEXT NOT NULL, rule_id INTEGER NOT NULL, move_id INTEGER,
                PRIMARY KEY (income_key, rule_id));
            CREATE TABLE IF NOT EXISTS bills (id INTEGER PRIMARY KEY, name TEXT NOT NULL, amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL, every TEXT NOT NULL, next_due TEXT, pot_id INTEGER, pay_url TEXT NOT NULL DEFAULT '',
                business INTEGER NOT NULL DEFAULT 0, category TEXT NOT NULL DEFAULT 'bills', created TEXT NOT NULL, removed TEXT);
            CREATE TABLE IF NOT EXISTS bill_payments (id INTEGER PRIMARY KEY, bill_id INTEGER NOT NULL, due TEXT NOT NULL,
                amount_cents INTEGER NOT NULL, paid_on TEXT NOT NULL, reference TEXT NOT NULL DEFAULT '', decision_id INTEGER,
                recorded TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT UNIQUE, title TEXT NOT NULL,
                amount_cents INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL, urgency TEXT NOT NULL DEFAULT 'medium',
                payload TEXT NOT NULL, reasons TEXT NOT NULL, approve_means TEXT NOT NULL, decline_means TEXT NOT NULL,
                status TEXT NOT NULL, created TEXT NOT NULL, decided TEXT, outcome TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS desk_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """))

    # ------------------------------------------------------------------ small reads
    def _currency(self, value: str | None) -> str:
        currency = (value or self.ledger.profile().get("default_currency") or "USD").strip().upper()
        if currency not in CURRENCIES:
            raise ValueError("Supported currencies: " + ", ".join(sorted(CURRENCIES)) + ".")
        return currency

    def _available(self) -> dict[str, int]:
        return {currency: row["available"] for currency, row in self.ledger._balances().items()}

    def _pot_rows(self, db: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = [dict(row) for row in db.execute(
            "SELECT p.*, COALESCE((SELECT SUM(amount_cents) FROM pot_moves m WHERE m.pot_id=p.id), 0) AS balance_cents, "
            "(SELECT nickname || CASE WHEN last4 != '' THEN ' ····' || last4 ELSE '' END FROM money_methods WHERE id=p.method_id) AS sits_in "
            "FROM pots p WHERE p.removed IS NULL ORDER BY p.id")]
        committed = self._committed(db)
        for row in rows:
            row["committed_cents"] = committed.get(row["id"], 0)
            row["free_cents"] = row["balance_cents"] - row["committed_cents"]
        return rows

    @staticmethod
    def _committed(db: sqlite3.Connection) -> dict[Any, int]:
        """Approved bills and purchases not yet paid, by pot (None = straight from unallocated money)."""
        held: dict[Any, int] = {}
        for row in db.execute("SELECT kind, amount_cents, currency, payload FROM decisions WHERE status='approved' AND kind IN ('pay_bill', 'spend')"):
            payload = json.loads(row["payload"])
            key = payload.get("pot_id") or ("unallocated", row["currency"])
            held[key] = held.get(key, 0) + row["amount_cents"]
        return held

    def _pot(self, db: sqlite3.Connection, which: Any) -> dict[str, Any]:
        rows = self._pot_rows(db)
        text = str(which or "").strip().lower()
        if text.isdigit():
            found = [row for row in rows if row["id"] == int(text)]
        else:
            text = re.sub(r"\b(?:my|the|pot|fund|account)\b", " ", text).strip()
            found = [row for row in rows if row["name"].lower() == text] or \
                [row for row in rows if text and (text in row["name"].lower() or text == row["purpose"])]
        if not found:
            names = ", ".join(row["name"] for row in rows) or "none yet"
            raise ValueError(f"No pot called {which!r}. Your pots: {names}. Say “create a pot called Tax with target 2000”.")
        return found[0]

    def unallocated(self, currency: str | None = None) -> int:
        currency = self._currency(currency)

        def work(db: sqlite3.Connection) -> int:
            pots = sum(row["balance_cents"] for row in self._pot_rows(db) if row["currency"] == currency)
            return self._available().get(currency, 0) - pots - self._committed(db).get(("unallocated", currency), 0)
        return self.ledger._run(work)

    # ------------------------------------------------------------------ pots
    def create_pot(self, name: str, target: str = "0", currency: str | None = None, purpose: str = "", method: str = "") -> dict[str, Any]:
        name = " ".join(str(name or "").split())[:40]
        if not name:
            raise ValueError("Give the pot a name, e.g. Tax, Emergency fund, Rent.")
        currency = self._currency(currency)
        target_cents = cents(target or "0") if str(target or "0").strip() not in ("", "0") else 0
        purpose = (purpose or "").strip().lower() or next((key for key in PURPOSES if key in name.lower()), "other")
        if "emergenc" in name.lower() or "rainy" in name.lower():
            purpose = "emergency"
        if purpose not in PURPOSES:
            raise ValueError("Purpose is one of: " + ", ".join(PURPOSES) + ".")
        method_id = None
        if method:
            from app.wallet_money import WalletMoney
            method_id = WalletMoney(self.ledger).find_method(method)["id"]

        def work(db: sqlite3.Connection) -> int:
            if db.execute("SELECT 1 FROM pots WHERE lower(name)=lower(?) AND removed IS NULL", (name,)).fetchone():
                raise ValueError(f"You already have a pot called {name}.")
            return db.execute("INSERT INTO pots (name, currency, target_cents, purpose, method_id, created) VALUES (?, ?, ?, ?, ?, ?)",
                              (name, currency, target_cents, purpose, method_id, _now())).lastrowid
        pot_id = self.ledger._run(work)
        return {"money_desk": True, "pot_created": True, "pot_id": pot_id, "name": name, "purpose": purpose,
                "target": money(target_cents, currency) if target_cents else "", "currency": currency,
                "note": "A pot is a part of your wallet set aside for one purpose. The money stays in your own account."}

    def move(self, amount: str, to_pot: str = "", from_pot: str = "", reason: str = "", decision_id: int | None = None) -> dict[str, Any]:
        """Move money into a pot, out of one, or between two (to_pot or from_pot empty = unallocated)."""
        if not to_pot and not from_pot:
            raise ValueError("Say which pot, e.g. “move 300 to Tax”.")

        def work(db: sqlite3.Connection) -> dict[str, Any]:
            target = self._pot(db, to_pot) if to_pot else None
            source = self._pot(db, from_pot) if from_pot else None
            if target and source and target["id"] == source["id"]:
                raise ValueError("That is the same pot.")
            currency = (target or source)["currency"]
            if target and source and target["currency"] != source["currency"]:
                raise ValueError(f"{source['name']} is in {source['currency']} and {target['name']} in {target['currency']}; "
                                 "pots move money in one currency.")
            everything = str(amount).strip().lower() in ("all", "everything")
            if source:
                value = source["free_cents"] if everything else cents(amount)
                if value > source["free_cents"]:
                    raise ValueError(f"{source['name']} has {money(source['free_cents'], currency)} free.")
            else:
                free = self._available().get(currency, 0) - sum(row["balance_cents"] for row in self._pot_rows(db)
                                                                 if row["currency"] == currency) \
                    - self._committed(db).get(("unallocated", currency), 0)
                value = free if everything else cents(amount)
                if value > free:
                    raise ValueError(f"Only {money(max(free, 0), currency)} is unallocated in {currency}. Add money or "
                                     "record income first, or move it from another pot.")
            if value <= 0:
                raise ValueError("Amount must be above 0.")
            words = reason.strip()[:200] or ("from " + source["name"] if source else "from unallocated money")
            if source:
                db.execute("INSERT INTO pot_moves (pot_id, amount_cents, reason, decision_id, at) VALUES (?, ?, ?, ?, ?)",
                           (source["id"], -value, words if not target else f"to {target['name']}", decision_id, _now()))
            if target:
                db.execute("INSERT INTO pot_moves (pot_id, amount_cents, reason, decision_id, at) VALUES (?, ?, ?, ?, ?)",
                           (target["id"], value, words, decision_id, _now()))
            return {"amount": money(value, currency), "to": target["name"] if target else "unallocated",
                    "from": source["name"] if source else "unallocated"}
        moved = self.ledger._run(work)
        return {"money_desk": True, "moved": True, **moved,
                "note": "Moved inside your HAYAT wallet. If your pots live in separate bank accounts, move it there too."}

    def remove_pot(self, which: str) -> dict[str, Any]:
        def work(db: sqlite3.Connection) -> dict[str, Any]:
            pot = self._pot(db, which)
            if pot["balance_cents"]:
                raise ValueError(f"{pot['name']} still holds {money(pot['balance_cents'], pot['currency'])}. Move it out first.")
            db.execute("UPDATE pots SET removed=? WHERE id=?", (_now(), pot["id"]))
            db.execute("UPDATE pot_rules SET removed=? WHERE pot_id=? AND removed IS NULL", (_now(), pot["id"]))
            return pot
        pot = self.ledger._run(work)
        return {"money_desk": True, "pot_removed": True, "name": pot["name"]}

    # ------------------------------------------------------------------ rules
    def add_rule(self, pot: str, percent: str, auto: bool = False) -> dict[str, Any]:
        try:
            share = float(str(percent).strip().rstrip("%"))
        except ValueError:
            raise ValueError("Percent must be a number, e.g. 25.") from None
        if not 0 < share <= 90:
            raise ValueError("Set aside between 0 and 90 percent.")

        def work(db: sqlite3.Connection) -> tuple[int, dict[str, Any]]:
            target = self._pot(db, pot)
            total = sum(float(row["percent"]) for row in db.execute("SELECT percent FROM pot_rules WHERE removed IS NULL"))
            if total + share > 100:
                raise ValueError(f"Your rules already set aside {total:g}% of income.")
            rule_id = db.execute("INSERT INTO pot_rules (pot_id, percent, auto, since, created) VALUES (?, ?, ?, ?, ?)",
                                 (target["id"], f"{share:g}", int(bool(auto)), self._watermark(db), _now())).lastrowid
            return rule_id, target
        rule_id, target = self.ledger._run(work)
        how = ("HAYAT sets it aside by itself when a payment is recorded" if auto else
               "HAYAT proposes it for your approval each time a payment is recorded")
        return {"money_desk": True, "rule_added": True, "rule_id": rule_id, "pot": target["name"], "percent": f"{share:g}",
                "auto": bool(auto), "note": f"{share:g}% of each new payment goes to {target['name']}: {how}. Income already "
                                            "recorded is left as it is."}

    def remove_rule(self, rule_id: int) -> dict[str, Any]:
        changed = self.ledger._run(lambda db: db.execute("UPDATE pot_rules SET removed=? WHERE id=? AND removed IS NULL",
                                                         (_now(), int(rule_id))).rowcount)
        if not changed:
            raise ValueError(f"No rule {rule_id}.")
        return {"money_desk": True, "rule_removed": True, "rule_id": int(rule_id)}

    # ------------------------------------------------------------------ bills
    def add_bill(self, name: str, amount: str, next_due: str, every: str = "month", currency: str | None = None, pot: str = "",
                 pay_url: str = "", business: bool = False, category: str = "bills") -> dict[str, Any]:
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise ValueError("Name the bill, e.g. Rent, Internet, Adobe.")
        every = (every or "month").strip().lower().removesuffix("ly").replace("monthly", "month")
        every = {"week": "week", "month": "month", "year": "year", "annual": "year", "once": "once", "one": "once"}.get(every, every)
        if every not in EVERY:
            raise ValueError("Repeat is once, week, month or year.")
        value, currency = cents(amount), self._currency(currency)
        if value <= 0:
            raise ValueError("Amount must be above 0.")
        due = _day(next_due, "Due date")
        url = _https(pay_url)

        def work(db: sqlite3.Connection) -> int:
            pot_id = self._pot(db, pot)["id"] if pot else None
            return db.execute("INSERT INTO bills (name, amount_cents, currency, every, next_due, pot_id, pay_url, business, category, created) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (name, value, currency, every, due.isoformat(), pot_id, url,
                                                                       int(bool(business)), (category or "bills").strip().lower()[:30], _now())).lastrowid
        bill_id = self.ledger._run(work)
        return {"money_desk": True, "bill_added": True, "bill_id": bill_id, "name": name, "amount": money(value, currency),
                "next_due": due.isoformat(), "every": every,
                "note": "HAYAT will propose paying it before it is due. You pay it in your own bank or app; HAYAT never pays."}

    def remove_bill(self, bill_id: int) -> dict[str, Any]:
        changed = self.ledger._run(lambda db: db.execute("UPDATE bills SET removed=? WHERE id=? AND removed IS NULL",
                                                         (_now(), int(bill_id))).rowcount)
        if not changed:
            raise ValueError(f"No bill {bill_id}.")
        return {"money_desk": True, "bill_removed": True, "bill_id": int(bill_id)}

    # ------------------------------------------------------------------ what the books say
    @staticmethod
    def _watermark(db: sqlite3.Connection) -> str:
        """The newest payment and income ids now: a rule applies only to income entered after it was made."""
        return json.dumps({"payment": db.execute("SELECT COALESCE(MAX(id), 0) FROM payments").fetchone()[0],
                           "income": db.execute("SELECT COALESCE(MAX(id), 0) FROM other_income").fetchone()[0]})

    def _incomes(self, db: sqlite3.Connection, since: str, after: str = "") -> list[dict[str, Any]]:
        """Payments and other income dated from ``since``; with a watermark, only those entered after it."""
        mark = json.loads(after) if after.startswith("{") else {"payment": 0, "income": 0}
        rows = [{"key": f"payment:{row[0]}", "day": row[1], "what": f"payment for {row[2]}", "currency": row[3], "cents": row[4]}
                for row in db.execute("SELECT p.id, p.paid_on, p.number, d.currency, p.amount_cents FROM payments p "
                                      "JOIN documents d ON d.number=p.number WHERE p.paid_on >= ? AND p.id > ?", (since, mark["payment"]))]
        rows += [{"key": f"income:{row[0]}", "day": row[1], "what": f"income from {row[2]}", "currency": row[3], "cents": row[4]}
                 for row in db.execute("SELECT id, received_on, source, currency, amount_cents FROM other_income "
                                       "WHERE received_on >= ? AND id > ?", (since, mark["income"]))]
        return sorted(rows, key=lambda row: row["day"])

    def _monthly(self, db: sqlite3.Connection, today: date) -> dict[str, dict[str, int]]:
        """Average money in and out per month over the last 90 days, plus the bills, by currency."""
        since = (today - timedelta(days=90)).isoformat()
        result: dict[str, dict[str, int]] = {}

        def add(currency: str, key: str, value: int) -> None:
            result.setdefault(currency, {"income": 0, "spending": 0, "bills": 0})[key] += int(value)
        for row in self._incomes(db, since):
            add(row["currency"], "income", row["cents"] / 3)
        for row in db.execute("SELECT currency, SUM(amount_cents) FROM expenses WHERE spent_on >= ? GROUP BY currency", (since,)):
            add(row[0], "spending", row[1] / 3)
        factor = {"week": 52 / 12, "month": 1, "year": 1 / 12, "once": 0}
        for row in db.execute("SELECT currency, amount_cents, every FROM bills WHERE removed IS NULL"):
            add(row[0], "bills", row[1] * factor[row[2]])
        return result

    def targets(self) -> dict[str, float]:
        row = self.ledger._run(lambda db: db.execute("SELECT value FROM desk_settings WHERE key='portfolio_targets'").fetchone())
        return json.loads(row[0]) if row else {}

    def set_targets(self, cash: str = "", stock: str = "", crypto: str = "") -> dict[str, Any]:
        values = {kind: float(str(value).rstrip("%")) for kind, value in (("cash", cash), ("stock", stock), ("crypto", crypto))
                  if str(value).strip() != ""}
        if not values:
            raise ValueError("Give target percentages, e.g. cash 20, stock 60, crypto 20.")
        if any(value < 0 for value in values.values()) or abs(sum(values.values()) - 100) > 0.5:
            raise ValueError(f"Targets must add up to 100% (they add up to {sum(values.values()):g}%).")
        self.ledger._run(lambda db: db.execute("INSERT INTO desk_settings VALUES ('portfolio_targets', ?) ON CONFLICT(key) DO UPDATE "
                                               "SET value=excluded.value", (json.dumps(values),)))
        return {"money_desk": True, "targets_set": True, "targets": values,
                "note": "HAYAT will propose a rebalance when your portfolio drifts more than 5 points from these."}

    # ------------------------------------------------------------------ thinking
    def think(self, today: date | None = None, portfolio: bool = True) -> dict[str, Any]:
        """Read the wallet, pots, bills, rules and portfolio; apply automatic rules; propose what to do next."""
        today = today or date.today()
        proposals: list[dict[str, Any]] = []
        applied: list[str] = []
        available = self._available()

        def work(db: sqlite3.Connection) -> None:
            pots = self._pot_rows(db)
            free_by_currency = {currency: value - sum(row["balance_cents"] for row in pots if row["currency"] == currency)
                                - self._committed(db).get(("unallocated", currency), 0) for currency, value in available.items()}
            # 1. Rules: a share of each new payment, applied by HAYAT when the owner said "automatically".
            for rule in db.execute("SELECT r.*, p.name, p.currency AS pot_currency FROM pot_rules r JOIN pots p ON p.id=r.pot_id "
                                   "WHERE r.removed IS NULL AND p.removed IS NULL").fetchall():
                for income in self._incomes(db, "0000-01-01", rule["since"]):
                    if income["currency"] != rule["pot_currency"] or db.execute(
                            "SELECT 1 FROM rule_applied WHERE income_key=? AND rule_id=?", (income["key"], rule["id"])).fetchone():
                        continue
                    share = int(round(income["cents"] * float(rule["percent"]) / 100))
                    if share <= 0:
                        continue
                    if rule["auto"] and free_by_currency.get(income["currency"], 0) >= share:
                        move = db.execute("INSERT INTO pot_moves (pot_id, amount_cents, reason, at) VALUES (?, ?, ?, ?)",
                                          (rule["pot_id"], share, f"rule {rule['id']}: {rule['percent']}% of {income['what']}", _now())).lastrowid
                        db.execute("INSERT INTO rule_applied VALUES (?, ?, ?)", (income["key"], rule["id"], move))
                        free_by_currency[income["currency"]] -= share
                        applied.append(f"Set aside {money(share, income['currency'])} ({rule['percent']}% of {income['what']}) in {rule['name']}.")
                    else:
                        proposals.append({
                            "kind": "rule_share", "key": f"rule:{rule['id']}:{income['key']}", "urgency": "medium",
                            "title": f"Set aside {money(share, income['currency'])} for {rule['name']}", "amount": share,
                            "currency": income["currency"],
                            "payload": {"pot_id": rule["pot_id"], "rule_id": rule["id"], "income_key": income["key"]},
                            "reasons": [f"Your rule: {rule['percent']}% of each payment goes to {rule['name']}.",
                                        f"New {income['what']} on {income['day']}: {money(income['cents'], income['currency'])}."],
                            "approve": f"{money(share, income['currency'])} moves from unallocated money into {rule['name']}.",
                            "decline": "This payment stays unallocated; the rule still applies to the next one."})
            pots = self._pot_rows(db)
            # 2. Bills due soon: pay them, and top up their pot first when it is short.
            reserved: dict[str, int] = {}
            topped: dict[int, int] = {}  # proposed top-ups by pot, so the share-out below doesn't fill the same gap twice
            for bill in db.execute("SELECT * FROM bills WHERE removed IS NULL AND next_due IS NOT NULL ORDER BY next_due").fetchall():
                due = date.fromisoformat(bill["next_due"])
                days = (due - today).days
                if days > BILL_WINDOW_DAYS:
                    continue
                pot = next((row for row in pots if row["id"] == bill["pot_id"]), None)
                when = f"was due {-days} day{'s' if days != -1 else ''} ago" if days < 0 else "is due today" if days == 0 else \
                    f"is due in {days} day{'s' if days != 1 else ''} ({due.isoformat()})"
                if pot and pot["free_cents"] < bill["amount_cents"]:
                    short = bill["amount_cents"] - max(pot["free_cents"], 0)
                    if free_by_currency.get(bill["currency"], 0) - reserved.get(bill["currency"], 0) >= short:
                        reserved[bill["currency"]] = reserved.get(bill["currency"], 0) + short
                        topped[pot["id"]] = topped.get(pot["id"], 0) + short
                        proposals.append({
                            "kind": "allocate", "key": f"topup:{bill['id']}:{bill['next_due']}:{short}", "urgency": "high",
                            "title": f"Top up {pot['name']} by {money(short, bill['currency'])} for {bill['name']}", "amount": short,
                            "currency": bill["currency"], "payload": {"splits": [{"pot_id": pot["id"], "cents": short}]},
                            "reasons": [f"{bill['name']} ({money(bill['amount_cents'], bill['currency'])}) {when}.",
                                        f"{pot['name']} has {money(max(pot['free_cents'], 0), bill['currency'])} free."],
                            "approve": f"{money(short, bill['currency'])} moves from unallocated money into {pot['name']}.",
                            "decline": f"{pot['name']} stays short; the bill can still be paid from unallocated money."})
                    else:
                        proposals.append(self._alert(f"short:{bill['id']}:{bill['next_due']}", "high",
                                                     f"Not enough for {bill['name']}", bill["currency"], [
                                                         f"{bill['name']} ({money(bill['amount_cents'], bill['currency'])}) {when}.",
                                                         f"{pot['name']} has {money(max(pot['free_cents'], 0), bill['currency'])} free and only "
                                                         f"{money(max(free_by_currency.get(bill['currency'], 0), 0), bill['currency'])} is unallocated.",
                                                         "Record a payment you've received, add money, or move it from another pot."]))
                if db.execute("SELECT 1 FROM decisions WHERE key=? AND status IN ('approved', 'done')", (f"bill:{bill['id']}:{bill['next_due']}",)).fetchone():
                    continue
                proposals.append({
                    "kind": "pay_bill", "key": f"bill:{bill['id']}:{bill['next_due']}", "urgency": "high" if days <= 3 else "medium",
                    "title": f"Pay {bill['name']}: {money(bill['amount_cents'], bill['currency'])}", "amount": bill["amount_cents"],
                    "currency": bill["currency"],
                    "payload": {"bill_id": bill["id"], "due": bill["next_due"], "pot_id": bill["pot_id"], "pay_url": bill["pay_url"],
                                "what": bill["name"], "business": bool(bill["business"]), "category": bill["category"]},
                    "reasons": [f"{bill['name']} {when}."] + ([f"Paid from {pot['name']}."] if pot else ["Paid from unallocated money."])
                    + ([f"Repeats every {bill['every']}."] if bill["every"] != "once" else []),
                    "approve": "The money is held for this bill. Pay it in your bank or app" + (" (HAYAT opens the biller's page)" if bill["pay_url"] else "")
                    + ", then say it is paid and HAYAT records it.",
                    "decline": "Nothing is held; HAYAT asks again before the next due date."})
            # 3. Start the pots that protect you: an emergency fund and money for tax.
            monthly = self._monthly(db, today)
            currency = self._currency(None)
            month = monthly.get(currency, {})
            outgoing = month.get("spending", 0) + month.get("bills", 0)
            if outgoing and not any(row["purpose"] == "emergency" for row in pots):
                target = int(round(outgoing * 3, -2))
                proposals.append({
                    "kind": "create_pot", "key": f"create:emergency:{currency}", "urgency": "medium",
                    "title": f"Start an emergency fund: {money(target, currency)}", "amount": target, "currency": currency,
                    "payload": {"name": "Emergency fund", "target_cents": target, "purpose": "emergency"},
                    "reasons": [f"You spend about {money(int(outgoing), currency)} a month (expenses over the last 90 days plus bills).",
                                "Three months of that covers a slow month or a late client without borrowing."],
                    "approve": "An Emergency fund pot is created with that target; HAYAT then proposes filling it first.",
                    "decline": "No emergency fund; HAYAT won't suggest it again."})
            if month.get("income") and not any(row["purpose"] == "tax" for row in pots):
                proposals.append({
                    "kind": "create_pot", "key": f"create:tax:{currency}", "urgency": "medium",
                    "title": "Keep money aside for tax", "amount": int(round(month["income"] * .25 * 3, -2)), "currency": currency,
                    "payload": {"name": "Tax", "target_cents": int(round(month["income"] * .25 * 3, -2)), "purpose": "tax", "rule_percent": "25"},
                    "reasons": [f"You've received about {money(int(month['income']), currency)} a month over the last 90 days.",
                                "Freelance income usually has no tax taken off; a quarter of it set aside is a common safe start "
                                "(check the rate where you live)."],
                    "approve": "A Tax pot is created, and a rule proposes setting aside 25% of each new payment.",
                    "decline": "No tax pot; HAYAT won't suggest it again."})
            # 4. Share out unallocated money: the pots furthest from their target, most important first.
            upcoming: dict[str, int] = {}
            for bill in db.execute("SELECT * FROM bills WHERE removed IS NULL AND pot_id IS NULL AND next_due IS NOT NULL"):
                if (date.fromisoformat(bill["next_due"]) - today).days <= 30:
                    upcoming[bill["currency"]] = upcoming.get(bill["currency"], 0) + bill["amount_cents"]
            for money_currency, free in free_by_currency.items():
                spare = free - reserved.get(money_currency, 0) - upcoming.get(money_currency, 0)
                gaps = sorted((row for row in pots if row["currency"] == money_currency
                               and row["target_cents"] > row["balance_cents"] + topped.get(row["id"], 0)),
                              key=lambda row: (PRIORITY.get(row["purpose"], 9), row["id"]))
                if spare <= 0 or not gaps:
                    continue
                splits, left = [], spare
                for row in gaps:
                    share = min(row["target_cents"] - row["balance_cents"] - topped.get(row["id"], 0), left)
                    if share >= 100:
                        splits.append({"pot_id": row["id"], "cents": share, "name": row["name"]})
                        left -= share
                    if left < 100:
                        break
                if not splits:
                    continue
                total = sum(split["cents"] for split in splits)
                proposals.append({
                    "kind": "allocate", "key": f"fill:{money_currency}:{spare}:{len(splits)}", "urgency": "low" if len(gaps) and total < spare else "medium",
                    "title": f"Put {money(total, money_currency)} to work in your pots", "amount": total, "currency": money_currency,
                    "payload": {"splits": [{"pot_id": split["pot_id"], "cents": split["cents"]} for split in splits]},
                    "reasons": [f"{money(free, money_currency)} is unallocated"
                                + (f"; {money(upcoming[money_currency], money_currency)} stays free for bills due in 30 days" if upcoming.get(money_currency) else "") + "."]
                    + [f"{split['name']}: {money(split['cents'], money_currency)} toward its target." for split in splits]
                    + ["Order: emergency fund, then tax, bills, savings, investing, spending."],
                    "approve": "; ".join(f"{money(split['cents'], money_currency)} to {split['name']}" for split in splits) + ".",
                    "decline": "The money stays unallocated."})
            # 5. Will the money cover the next 30 days?
            for money_currency, need in upcoming.items():
                if free_by_currency.get(money_currency, 0) < need:
                    proposals.append(self._alert(f"runway:{money_currency}:{today.isoformat()[:7]}:{need}", "high",
                                                 f"Bills in the next 30 days exceed your free money in {money_currency}", money_currency, [
                                                     f"Bills without a pot due in 30 days: {money(need, money_currency)}.",
                                                     f"Unallocated: {money(free_by_currency.get(money_currency, 0), money_currency)}.",
                                                     "Chase an unpaid invoice (say “when will I get paid?”) or move money from a pot."]))
        self.ledger._run(work)
        if portfolio and self.portfolio is not None and self.targets():
            proposals.extend(self._rebalance())
        changed = self._store(proposals)
        waiting = self.decisions("proposed")["decisions"]
        return {"money_desk": True, "thought": True, "applied": applied, "new": changed["new"], "updated": changed["updated"],
                "dropped": changed["dropped"], "waiting": waiting, "approved_waiting_payment": self.decisions("approved")["decisions"],
                "note": "Nothing changes until you approve. Say “approve decision N” or use the HAYAT Wallet window."}

    @staticmethod
    def _alert(key: str, urgency: str, title: str, currency: str, reasons: list[str]) -> dict[str, Any]:
        return {"kind": "alert", "key": key, "urgency": urgency, "title": title, "amount": 0, "currency": currency, "payload": {},
                "reasons": reasons, "approve": "Marks it as seen.", "decline": "Marks it as seen."}

    def _rebalance(self) -> list[dict[str, Any]]:
        targets = self.targets()
        try:
            view = self.portfolio.view()
        except Exception as exc:  # no prices: no rebalance this time
            return [self._alert(f"rebalance-unpriced:{date.today().isoformat()}", "low", "Couldn't check your portfolio", "USD",
                                [f"Prices could not be read ({type(exc).__name__}); the rebalance check waits for the next time."])]
        total = view["net_worth_usd"]
        if not total:
            return []
        current = {kind: view["by_kind"].get(kind, 0) / total * 100 for kind in targets}
        drift = {kind: current[kind] - targets[kind] for kind in targets}
        if max(abs(value) for value in drift.values()) < 5:
            return []
        trades = [{"kind": kind, "usd": round(-value / 100 * total, 2)} for kind, value in drift.items() if abs(value) >= 1]
        words = [f"{'Buy' if trade['usd'] > 0 else 'Sell'} about ${abs(trade['usd']):,.2f} of {trade['kind']}" for trade in trades]
        signature = ":".join(f"{kind}{round(value)}" for kind, value in sorted(drift.items()))
        return [{"kind": "rebalance", "key": f"rebalance:{signature}", "urgency": "low", "title": "Rebalance your portfolio",
                 "amount": int(sum(abs(trade["usd"]) for trade in trades) / 2 * 100), "currency": "USD",
                 "payload": {"trades": trades, "targets": targets},
                 "reasons": ["Now: " + ", ".join(f"{kind} {current[kind]:.0f}%" for kind in targets) + "; your targets: "
                             + ", ".join(f"{kind} {targets[kind]:g}%" for kind in targets) + "."] + words
                 + ["Taxes and fees on selling are yours to weigh; HAYAT doesn't know them."],
                 "approve": "You make these trades at your broker or exchange; HAYAT never trades. Record them in Portfolio afterwards.",
                 "decline": "No rebalance until the drift changes."}]

    def _store(self, proposals: list[dict[str, Any]]) -> dict[str, int]:
        new = updated = 0

        def work(db: sqlite3.Connection) -> int:
            nonlocal new, updated
            keys = set()
            for item in proposals:
                keys.add(item["key"])
                row = db.execute("SELECT id, status FROM decisions WHERE key=?", (item["key"],)).fetchone()
                values = (item["title"], item["amount"], item["currency"], item["urgency"], json.dumps(item["payload"]),
                          json.dumps(item["reasons"]), item["approve"], item["decline"])
                if row is None:
                    db.execute("INSERT INTO decisions (title, amount_cents, currency, urgency, payload, reasons, approve_means, decline_means, "
                               "kind, key, status, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)",
                               values + (item["kind"], item["key"], _now()))
                    new += 1
                elif row["status"] == "proposed":
                    db.execute("UPDATE decisions SET title=?, amount_cents=?, currency=?, urgency=?, payload=?, reasons=?, approve_means=?, "
                               "decline_means=? WHERE id=?", values + (row["id"],))
                    updated += 1
            stale = [row["id"] for row in db.execute("SELECT id, key FROM decisions WHERE status='proposed' AND kind IN (%s)"
                                                     % ",".join("?" * len(AUTO_KINDS)), AUTO_KINDS) if row["key"] not in keys]
            for decision_id in stale:
                db.execute("UPDATE decisions SET status='expired', decided=? WHERE id=?", (_now(), decision_id))
            return len(stale)
        dropped = self.ledger._run(work)
        return {"new": new, "updated": updated, "dropped": dropped}

    # ------------------------------------------------------------------ spend requests: HAYAT's verdict
    def request_spend(self, amount: str, what: str, pot: str = "", currency: str | None = None, url: str = "",
                      business: bool = False, category: str = "") -> dict[str, Any]:
        what = " ".join(str(what or "").split())[:80]
        if not what:
            raise ValueError("Say what it's for, e.g. “a 300 dollar laptop”.")
        value = cents(amount)
        if value <= 0:
            raise ValueError("Amount must be above 0.")
        link = _https(url)
        today = date.today()

        def work(db: sqlite3.Connection) -> dict[str, Any]:
            chosen = self._pot(db, pot) if pot else None
            cur = chosen["currency"] if chosen else self._currency(currency)
            pots = self._pot_rows(db)
            free = self._available().get(cur, 0) - sum(row["balance_cents"] for row in pots if row["currency"] == cur) \
                - self._committed(db).get(("unallocated", cur), 0)
            month = self._monthly(db, today).get(cur, {})
            bills_30 = sum(row["amount_cents"] for row in db.execute(
                "SELECT amount_cents, next_due FROM bills WHERE removed IS NULL AND currency=? AND next_due IS NOT NULL", (cur,))
                if (date.fromisoformat(row["next_due"]) - today).days <= 30)
            emergency = next((row for row in pots if row["purpose"] == "emergency"), None)
            source = chosen["free_cents"] if chosen else free
            reasons = [(f"{chosen['name']} has {money(chosen['free_cents'], cur)} free." if chosen else f"Unallocated: {money(free, cur)}."),
                       f"Bills due in the next 30 days: {money(bills_30, cur)}."]
            if month.get("income"):
                reasons.append(f"That is {value / month['income'] * 100:.0f}% of a month's income ({money(int(month['income']), cur)}).")
            if emergency:
                reasons.append(f"Emergency fund: {money(emergency['balance_cents'], cur)} of {money(emergency['target_cents'], cur)}.")
            left_after = source - value - (0 if chosen else bills_30)
            if source < value:
                verdict, urgency = "Not enough", "high"
                reasons.append(f"Short by {money(value - source, cur)}" + ("; move money into the pot first." if chosen else "."))
            elif left_after < 0 or (emergency and emergency["balance_cents"] < emergency["target_cents"] / 3 and not chosen):
                verdict, urgency = "Better to wait", "medium"
                reasons.append("It would leave too little for the bills due soon." if left_after < 0 else
                               "Your emergency fund is under a third of its target; filling it first is safer.")
            else:
                verdict, urgency = "Yes, it fits", "low"
                reasons.append(f"Afterwards {money(left_after, cur)} stays free" + (" after the bills." if not chosen else f" in {chosen['name']}."))
            cursor = db.execute("INSERT INTO decisions (kind, key, title, amount_cents, currency, urgency, payload, reasons, approve_means, "
                                "decline_means, status, created) VALUES ('spend', NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)", (
                                    f"Spend {money(value, cur)} on {what} · HAYAT says: {verdict}", value, cur, urgency,
                                    json.dumps({"pot_id": chosen["id"] if chosen else None, "what": what, "url": link, "business": bool(business),
                                                "category": (category or ("business" if business else "personal")).strip().lower()[:30],
                                                "verdict": verdict}),
                                    json.dumps(reasons), "The money is held for it. Buy it yourself" + (" (HAYAT opens the page)" if link else "")
                                    + ", then say it is paid and HAYAT records it. HAYAT never pays or enters card details.",
                                    "Nothing is held.", _now()))
            return {"decision_id": cursor.lastrowid, "verdict": verdict, "reasons": reasons, "currency": cur}
        found = self.ledger._run(work)
        return {"money_desk": True, "spend_judged": True, **found, "amount": money(value, found["currency"]), "what": what,
                "note": f"Your call: say “approve decision {found['decision_id']}” or “decline decision {found['decision_id']}”."}

    # ------------------------------------------------------------------ the owner's word
    def _decision(self, db: sqlite3.Connection, decision_id: int) -> dict[str, Any]:
        row = db.execute("SELECT * FROM decisions WHERE id=?", (int(decision_id),)).fetchone()
        if row is None:
            raise ValueError(f"No decision {decision_id}. Say “what should I do with my money?” to see them.")
        return self._public(row)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"], item["reasons"], item["outcome"] = json.loads(item["payload"]), json.loads(item["reasons"]), json.loads(item["outcome"])
        item["amount"] = money(item["amount_cents"], item["currency"]) if item["amount_cents"] else ""
        item["external"] = item["kind"] in EXTERNAL
        return item

    def approve(self, decision_id: int, *, by_owner: bool) -> dict[str, Any]:
        """The owner's approval. Inside the wallet it acts now; outside, it holds the money until the owner has paid."""
        if not by_owner:
            raise PermissionError("Only you can approve a money decision, in the HAYAT Wallet window or at this computer.")
        item = self.ledger._run(lambda db: self._decision(db, decision_id))
        if item["status"] != "proposed":
            raise ValueError(f"Decision {decision_id} is {item['status']}; it can only be approved once.")
        payload, outcome = item["payload"], {}
        if item["kind"] == "allocate":
            for split in payload["splits"]:
                self.move(str(split["cents"] / 100), to_pot=str(split["pot_id"]), reason=item["title"], decision_id=item["id"])
            outcome["done"] = item["approve_means"]
        elif item["kind"] == "rule_share":
            self.move(str(item["amount_cents"] / 100), to_pot=str(payload["pot_id"]), reason=item["title"], decision_id=item["id"])
            self.ledger._run(lambda db: db.execute("INSERT OR IGNORE INTO rule_applied VALUES (?, ?, NULL)",
                                                   (payload["income_key"], payload["rule_id"])))
            outcome["done"] = item["approve_means"]
        elif item["kind"] == "create_pot":
            pot = self.create_pot(payload["name"], str(payload["target_cents"] / 100), item["currency"], payload["purpose"])
            if payload.get("rule_percent"):
                self.add_rule(str(pot["pot_id"]), payload["rule_percent"])
            outcome["done"] = item["approve_means"]
        elif item["kind"] in ("pay_bill", "spend"):
            def check(db: sqlite3.Connection) -> None:
                pots = self._pot_rows(db)
                pot = next((row for row in pots if row["id"] == payload.get("pot_id")), None)
                if pot:
                    if pot["free_cents"] < item["amount_cents"]:
                        raise ValueError(f"{pot['name']} has {money(max(pot['free_cents'], 0), item['currency'])} free; move money into it first.")
                else:
                    free = self._available().get(item["currency"], 0) - sum(row["balance_cents"] for row in pots if row["currency"] == item["currency"]) \
                        - self._committed(db).get(("unallocated", item["currency"]), 0)
                    if free < item["amount_cents"]:
                        raise ValueError(f"Only {money(max(free, 0), item['currency'])} is unallocated; this needs {item['amount']}.")
            self.ledger._run(check)
            outcome["next"] = f"Pay {item['amount']} yourself, then say “decision {item['id']} is paid”."
            outcome["open"] = payload.get("pay_url") or payload.get("url") or ""
        elif item["kind"] == "rebalance":
            outcome["next"] = "Make the trades at your broker or exchange, record them in Portfolio, then say it is done."
        status = "approved" if item["kind"] in EXTERNAL else "done"
        self.ledger._run(lambda db: db.execute("UPDATE decisions SET status=?, decided=?, outcome=? WHERE id=?",
                                               (status, _now(), json.dumps(outcome), item["id"])))
        return {"money_desk": True, "approved": True, "decision_id": item["id"], "title": item["title"], "status": status, **outcome}

    def decline(self, decision_id: int, *, by_owner: bool) -> dict[str, Any]:
        if not by_owner:
            raise PermissionError("Only you can decline a money decision.")
        item = self.ledger._run(lambda db: self._decision(db, decision_id))
        if item["status"] not in ("proposed", "approved"):
            raise ValueError(f"Decision {decision_id} is {item['status']}.")
        self.ledger._run(lambda db: db.execute("UPDATE decisions SET status='declined', decided=? WHERE id=?", (_now(), item["id"])))
        return {"money_desk": True, "declined": True, "decision_id": item["id"], "title": item["title"],
                "note": item["decline_means"] if item["status"] == "proposed" else "Released: the money held for it is free again."}

    def paid(self, decision_id: int, *, by_owner: bool, reference: str = "", paid_on: str | None = None) -> dict[str, Any]:
        """The owner says they paid an approved bill or purchase: the books and the pot record it."""
        if not by_owner:
            raise PermissionError("Only you can say a payment was made.")
        item = self.ledger._run(lambda db: self._decision(db, decision_id))
        if item["status"] != "approved":
            raise ValueError(f"Decision {decision_id} is {item['status']}; only an approved one can be marked paid.")
        payload, day = item["payload"], _day(paid_on or date.today().isoformat())
        if day > date.today():
            raise ValueError("A payment can't be dated in the future.")
        if item["kind"] == "rebalance":
            self.ledger._run(lambda db: db.execute("UPDATE decisions SET status='done', decided=? WHERE id=?", (_now(), item["id"])))
            return {"money_desk": True, "paid": True, "decision_id": item["id"], "title": item["title"], "recorded": "Marked done."}
        amount = str(item["amount_cents"] / 100)
        if payload.get("business"):
            self.ledger.record_expense(amount, payload.get("category") or "bills", payload["what"], day.isoformat(), item["currency"],
                                       f"hayat-decision-{item['id']}")
            recorded = f"a business expense ({payload.get('category') or 'bills'})"
        else:
            self.ledger._run(lambda db: db.execute(
                "INSERT INTO withdrawals (amount_cents, currency, destination, status, note, reference, requested_on, completed_on, recorded) "
                "VALUES (?, ?, ?, 'done', ?, ?, ?, ?, ?)", (item["amount_cents"], item["currency"], f"Paid: {payload['what']}",
                                                            f"decision {item['id']}", reference.strip()[:80] or None, day.isoformat(),
                                                            day.isoformat(), _now())))
            recorded = "money taken out of the wallet for a personal payment"

        def finish(db: sqlite3.Connection) -> None:
            db.execute("UPDATE decisions SET status='done', decided=?, outcome=? WHERE id=?",
                       (_now(), json.dumps({"paid_on": day.isoformat(), "reference": reference.strip()[:80]}), item["id"]))
            if payload.get("pot_id"):
                db.execute("INSERT INTO pot_moves (pot_id, amount_cents, reason, decision_id, at) VALUES (?, ?, ?, ?, ?)",
                           (payload["pot_id"], -item["amount_cents"], f"paid: {payload['what']}", item["id"], _now()))
            if item["kind"] == "pay_bill":
                bill = db.execute("SELECT * FROM bills WHERE id=?", (payload["bill_id"],)).fetchone()
                db.execute("INSERT INTO bill_payments (bill_id, due, amount_cents, paid_on, reference, decision_id, recorded) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (bill["id"], payload["due"], item["amount_cents"], day.isoformat(), reference.strip()[:80], item["id"], _now()))
                following = _advance(date.fromisoformat(payload["due"]), bill["every"])
                db.execute("UPDATE bills SET next_due=? WHERE id=?", (following.isoformat() if following else None, bill["id"]))
        self.ledger._run(finish)
        return {"money_desk": True, "paid": True, "decision_id": item["id"], "title": item["title"], "recorded": recorded,
                "source": "owner-reported; HAYAT did not see the payment"}

    # ------------------------------------------------------------------ views
    def decisions(self, status: str | None = None, limit: int = 50) -> dict[str, Any]:
        query = "SELECT * FROM decisions" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT ?"
        rows = self.ledger._run(lambda db: [self._public(row) for row in db.execute(query, ((status,) if status else ()) + (limit,))])
        if status in ("proposed", "approved"):
            rows.sort(key=lambda row: (URGENCY.get(row["urgency"], 9), -row["amount_cents"]))
        return {"money_desk": True, "decisions": rows}

    def overview(self) -> dict[str, Any]:
        today = date.today()

        def work(db: sqlite3.Connection) -> dict[str, Any]:
            pots = self._pot_rows(db)
            for row in pots:
                row.update(balance=money(row["balance_cents"], row["currency"]), target=money(row["target_cents"], row["currency"]) if row["target_cents"] else "",
                           progress=round(row["balance_cents"] / row["target_cents"] * 100) if row["target_cents"] else None)
            bills = [dict(row) for row in db.execute("SELECT b.*, p.name AS pot FROM bills b LEFT JOIN pots p ON p.id=b.pot_id "
                                                     "WHERE b.removed IS NULL ORDER BY b.next_due IS NULL, b.next_due")]
            for bill in bills:
                bill["amount"] = money(bill["amount_cents"], bill["currency"])
                bill["days"] = (date.fromisoformat(bill["next_due"]) - today).days if bill["next_due"] else None
            rules = [dict(row) for row in db.execute("SELECT r.*, p.name AS pot FROM pot_rules r JOIN pots p ON p.id=r.pot_id WHERE r.removed IS NULL")]
            moves = [dict(row) for row in db.execute("SELECT m.*, p.name AS pot, p.currency FROM pot_moves m JOIN pots p ON p.id=m.pot_id "
                                                     "ORDER BY m.id DESC LIMIT 40")]
            for move in moves:
                move["amount"] = ("+" if move["amount_cents"] > 0 else "") + money(move["amount_cents"], move["currency"])
            committed = self._committed(db)
            return {"pots": pots, "bills": bills, "rules": rules, "moves": moves, "committed": committed}
        data = self.ledger._run(work)
        available = self._available()
        currencies = sorted(set(available) | {pot["currency"] for pot in data["pots"]})
        money_rows = []
        for currency in currencies:
            in_pots = sum(pot["balance_cents"] for pot in data["pots"] if pot["currency"] == currency)
            held = data["committed"].get(("unallocated", currency), 0) + sum(pot["committed_cents"] for pot in data["pots"] if pot["currency"] == currency)
            unallocated = available.get(currency, 0) - in_pots - data["committed"].get(("unallocated", currency), 0)
            bills_30 = sum(bill["amount_cents"] for bill in data["bills"] if bill["currency"] == currency and bill["days"] is not None and bill["days"] <= 30)
            money_rows.append({"currency": currency, "available_cents": available.get(currency, 0), "available": money(available.get(currency, 0), currency),
                               "in_pots": money(in_pots, currency), "unallocated_cents": unallocated, "unallocated": money(unallocated, currency),
                               "held_for_payments": money(held, currency), "bills_30_days": money(bills_30, currency)})
        waiting = self.decisions("proposed")["decisions"]
        approved = self.decisions("approved")["decisions"]
        data.pop("committed")
        return {"money_desk": True, "overview": True, "money": money_rows, **data, "waiting": waiting, "approved": approved,
                "history": [row for row in self.decisions(limit=60)["decisions"] if row["status"] in ("done", "declined", "expired")][:30],
                "targets": self.targets(),
                "basis": "Your wallet is the money recorded in your books; it sits in your own bank, PayPal or store accounts. Pots, "
                         "bills and rules organise it. HAYAT proposes, you approve; it never pays, buys or moves money outside "
                         "the wallet record."}


# ---------------------------------------------------------------------- tools
TOOL_NAMES = ("wallet_desk", "wallet_think", "wallet_pot_create", "wallet_pot_move", "wallet_pot_remove", "wallet_rule_add",
              "wallet_rule_remove", "wallet_bill_add", "wallet_bill_remove", "wallet_spend_request", "wallet_targets_set")


def register_money_desk_tools(registry: Any, desk: MoneyDesk) -> None:
    """No tool approves, declines or marks paid: those are the owner's word only (window, or the assistant's owner check)."""
    def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    text = {"type": "string", "maxLength": 80}
    reg = registry.register
    reg("wallet_desk", "The HAYAT Wallet desk: money per currency (available, in pots, unallocated, held for approved payments), pots with "
        "targets, bills with due dates, rules, and the decisions waiting for the owner's approval.", obj({}), "low", "filesystem_read", desk.overview)
    reg("wallet_think", "HAYAT reads the wallet, pots, bills, rules and portfolio, applies the owner's automatic rules, and proposes what to "
        "do with the money (fill pots, pay due bills, start an emergency fund or tax pot, rebalance), each with reasons. Nothing else "
        "changes until the owner approves.", obj({}), "low", "filesystem_write_project", desk.think)
    reg("wallet_pot_create", "Create a pot in the HAYAT wallet: a part of the money set aside for a purpose, with an optional target.",
        obj({"name": text, "target": {"type": "string", "maxLength": 20}, "currency": {"type": "string", "maxLength": 3},
             "purpose": {"enum": list(PURPOSES)}, "method": text}, ("name",)), "low", "filesystem_write_project", desk.create_pot)
    reg("wallet_pot_move", "Move money inside the HAYAT wallet: from unallocated into a pot, out of one, or between pots, as the owner said.",
        obj({"amount": {"type": "string", "maxLength": 20}, "to_pot": text, "from_pot": text, "reason": {"type": "string", "maxLength": 200}},
            ("amount",)), "low", "filesystem_write_project", desk.move)
    reg("wallet_pot_remove", "Remove an empty savings pot from the HAYAT Wallet (a pot holding money cannot be removed).", obj({"which": text}, ("which",)), "low", "filesystem_write_project", desk.remove_pot)
    reg("wallet_rule_add", "A standing rule: set aside a percent of each new payment in a pot; auto=true lets HAYAT apply it itself.",
        obj({"pot": text, "percent": {"type": "string", "maxLength": 6}, "auto": {"type": "boolean"}}, ("pot", "percent")), "low",
        "filesystem_write_project", desk.add_rule)
    reg("wallet_rule_remove", "Remove a set-aside rule from the HAYAT Wallet by its number, so income is no longer split by it.", obj({"rule_id": {"type": "integer", "minimum": 1}}, ("rule_id",)), "low",
        "filesystem_write_project", desk.remove_rule)
    reg("wallet_bill_add", "Add a bill with its amount, next due date, repeat (once, week, month, year), paying pot and the biller's https "
        "page. HAYAT proposes paying it before it is due; the owner pays it.",
        obj({"name": text, "amount": {"type": "string", "maxLength": 20}, "next_due": {"type": "string", "maxLength": 10},
             "every": {"enum": list(EVERY)}, "currency": {"type": "string", "maxLength": 3}, "pot": text,
             "pay_url": {"type": "string", "maxLength": 300}, "business": {"type": "boolean"}, "category": text},
            ("name", "amount", "next_due")), "low", "filesystem_write_project", desk.add_bill)
    reg("wallet_bill_remove", "Remove a recurring bill from the HAYAT Wallet by its number; nothing is paid or cancelled.", obj({"bill_id": {"type": "integer", "minimum": 1}}, ("bill_id",)), "low",
        "filesystem_write_project", desk.remove_bill)
    reg("wallet_spend_request", "The owner wants to spend money: HAYAT judges whether it fits (pot, unallocated money, bills due, emergency "
        "fund, income) and files it for the owner's approval. HAYAT never buys or pays.",
        obj({"amount": {"type": "string", "maxLength": 20}, "what": text, "pot": text, "currency": {"type": "string", "maxLength": 3},
             "url": {"type": "string", "maxLength": 300}, "business": {"type": "boolean"}, "category": text}, ("amount", "what")), "low",
        "filesystem_write_project", desk.request_spend)
    reg("wallet_targets_set", "Target portfolio allocation in percent (cash, stock, crypto) for HAYAT's rebalance proposals.",
        obj({"cash": {"type": "string", "maxLength": 6}, "stock": {"type": "string", "maxLength": 6}, "crypto": {"type": "string", "maxLength": 6}}),
        "low", "filesystem_write_project", desk.set_targets)


# ---------------------------------------------------------------------- chat
_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_AMOUNT = r"(?:\$|₹|€|£)?\s*(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<unit>usd|dollars?|inr|rupees?|eur|euros?|gbp|pounds?|aed)?"
_UNITS = {"usd": "USD", "dollar": "USD", "dollars": "USD", "inr": "INR", "rupee": "INR", "rupees": "INR", "eur": "EUR", "euro": "EUR",
          "euros": "EUR", "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "aed": "AED"}
_THINK = re.compile(_LEAD + r"(?:what\s+should\s+i\s+do\s+with\s+my\s+money|(?:any\s+|show\s+(?:me\s+)?(?:my\s+)?)?money\s+decisions|"
                    r"(?:think\s+about|plan|organi[sz]e|manage)\s+my\s+(?:money|wallet)(?:\s+for\s+me)?|"
                    r"what\s+does\s+hayat\s+(?:suggest|recommend)\s+for\s+my\s+money)\s*[?.!]*$", re.IGNORECASE)
_VIEW = re.compile(_LEAD + r"(?:show|open|list)\s+(?:me\s+)?(?:my\s+)?(?:pots|bills|wallet\s+desk|hayat\s+wallet|set[\s-]?aside\s+rules)\s*[?.!]*$",
                   re.IGNORECASE)
_POT = re.compile(_LEAD + r"(?:create|make|start|add|open)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:pot|savings\s+pot|fund)\s+(?:called|named|for)\s+"
                  r"(?P<name>[\w '&-]{2,40}?)(?:\s+(?:with\s+(?:a\s+)?)?target\s+(?:of\s+)?" + _AMOUNT + r")?\s*[?.!]*$", re.IGNORECASE)
_MOVE = re.compile(_LEAD + r"(?:move|put|set\s+aside|allocate)\s+" + _AMOUNT.replace("amount", "amount").replace("(?P<unit>", "(?P<unit>")
                   + r"(?:\s+from\s+(?:my\s+)?(?P<source>[\w '&-]{2,40}?)(?:\s+pot)?)?\s+(?:in|into|to)\s+(?:my\s+|the\s+)?(?P<target>[\w '&-]{2,40}?)"
                   r"(?:\s+pot)?\s*[?.!]*$", re.IGNORECASE)
_RULE = re.compile(_LEAD + r"(?:always\s+)?(?:set\s+aside|save|keep|put)\s+(?P<percent>\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(?:my\s+|each\s+|every\s+|all\s+)?"
                   r"(?:income|payments?|earnings)\s+(?:for|in|into|to)\s+(?:my\s+|the\s+)?(?P<pot>[\w '&-]{2,40}?)(?:\s+pot)?"
                   r"(?P<auto>\s+automatically)?\s*[?.!]*$", re.IGNORECASE)
_BILL = re.compile(_LEAD + r"add\s+(?:a\s+)?bill[:\s]+(?P<name>[\w '&.-]{2,40}?)\s+" + _AMOUNT
                   + r"(?:\s+(?P<every>weekly|monthly|yearly|every\s+(?:week|month|year)|once))?\s+due\s+(?:on\s+)?(?P<due>\d{4}-\d{2}-\d{2})"
                   r"(?:\s+(?P<every2>weekly|monthly|yearly|every\s+(?:week|month|year)|once))?\s*[?.!]*$", re.IGNORECASE)
_SPEND = re.compile(_LEAD + r"(?:can\s+i\s+afford|should\s+i\s+(?:buy|spend\s+money\s+on|get)|i\s+want\s+to\s+spend|i'd\s+like\s+to\s+spend|"
                    r"request\s+(?:money|spending)\s+(?:of|for))\s+(?:an?\s+)?" + _AMOUNT
                    + r"(?:\s+(?:dollar|rupee|euro|pound)s?)?\s+(?:on\s+|for\s+)?(?:an?\s+|the\s+)?(?P<what>[\w '&.-]{2,60}?)"
                    r"(?:\s+from\s+(?:my\s+)?(?P<pot>[\w '&-]{2,40}?)(?:\s+pot)?)?\s*[?.!]*$", re.IGNORECASE)
#: The owner's own word on a decision; routed as a CONTROL so no tool can do it.
DECISION_WORD = re.compile(r"(?i)\s*(?:yes,?\s+)?(?P<verb>approve|decline|reject)\s+(?:money\s+)?decision\s+#?(?P<id>\d+)[\s.!]*$"
                           r"|\s*(?:money\s+)?decision\s+#?(?P<paid_id>\d+)\s+(?:is\s+)?(?:paid|done|bought)(?:[,:]?\s+(?:ref(?:erence)?|txn)\s*(?P<ref>[\w-]{2,40}))?[\s.!]*$")


def _amount(match: re.Match) -> tuple[str, str | None]:
    return match["amount"].replace(",", ""), _UNITS.get((match["unit"] or "").lower())


def desk_request(text: str) -> dict[str, Any] | None:
    raw = " ".join(str(text or "").split())
    if not raw or len(raw) > 220:
        return None
    if _THINK.match(raw):
        return {"tool": "wallet_think", "arguments": {}}
    if _VIEW.match(raw):
        return {"tool": "wallet_desk", "arguments": {}}
    if match := _RULE.match(raw):
        return {"tool": "wallet_rule_add", "arguments": {"pot": match["pot"].strip(), "percent": match["percent"], "auto": bool(match["auto"])}}
    if match := _POT.match(raw):
        arguments: dict[str, Any] = {"name": match["name"].strip()}
        if match["amount"]:
            arguments["target"], currency = _amount(match)
            if currency:
                arguments["currency"] = currency
        return {"tool": "wallet_pot_create", "arguments": arguments}
    if match := _BILL.match(raw):
        amount, currency = _amount(match)
        every = (match["every"] or match["every2"] or "monthly").lower().replace("every ", "")
        arguments = {"name": match["name"].strip(), "amount": amount, "next_due": match["due"],
                     "every": {"weekly": "week", "monthly": "month", "yearly": "year"}.get(every, every)}
        if currency:
            arguments["currency"] = currency
        return {"tool": "wallet_bill_add", "arguments": arguments}
    if match := _SPEND.match(raw):
        amount, currency = _amount(match)
        arguments = {"amount": amount, "what": match["what"].strip()}
        if currency:
            arguments["currency"] = currency
        if match["pot"]:
            arguments["pot"] = match["pot"].strip()
        return {"tool": "wallet_spend_request", "arguments": arguments}
    if match := _MOVE.match(raw):
        if re.search(r"(?i)\b(?:bank|account|paypal|card|friend|wallet\s+address)\b", match["target"]):
            return None  # out of the wallet: that is a real transfer, which the money guard stops
        amount, _currency = _amount(match)
        arguments = {"amount": amount, "to_pot": match["target"].strip()}
        if match["source"]:
            arguments["from_pot"] = match["source"].strip()
        return {"tool": "wallet_pot_move", "arguments": arguments}
    return None


def direct_plan(text: str, names: Any) -> dict[str, Any] | None:
    found = desk_request(text)
    if not found or found["tool"] not in names:
        return None
    return {"description": "Work on the HAYAT wallet: pots, bills, rules and decisions; outside payments stay the owner's.",
            "tool": found["tool"], "arguments": found["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}


def decision_lines(item: dict[str, Any]) -> list[str]:
    lines = [f"Decision {item['id']} [{item['urgency']}]: {item['title']}"]
    lines += [f"  · {reason}" for reason in item["reasons"]]
    if item["status"] == "proposed":
        lines.append(f"  If you approve: {item['approve_means']}")
    elif item["status"] == "approved":
        lines.append(f"  Approved. Pay it yourself, then say “decision {item['id']} is paid”.")
    return lines


def report_lines(value: dict[str, Any]) -> list[str]:
    if value.get("thought"):
        lines = list(value["applied"])
        if not value["waiting"] and not value["approved_waiting_payment"]:
            lines.append("Nothing needs a decision right now. Add pots with targets, bills or a set-aside rule and I'll plan around them.")
        for item in value["waiting"][:8]:
            lines += decision_lines(item)
        for item in value["approved_waiting_payment"][:5]:
            lines += decision_lines(item)
        if value["waiting"]:
            lines.append("Say “approve decision N” or “decline decision N”. Nothing changes until you do.")
        return lines
    if value.get("overview"):
        lines = [f"{row['currency']}: available {row['available']} · in pots {row['in_pots']} · unallocated {row['unallocated']}"
                 + (f" · bills in 30 days {row['bills_30_days']}" if not row["bills_30_days"].startswith("0.00") else "") for row in value["money"]]
        lines += [f"  Pot {pot['id']} {pot['name']}: {pot['balance']}" + (f" of {pot['target']} ({pot['progress']}%)" if pot["target"] else "")
                  + (f" · in {pot['sits_in']}" if pot["sits_in"] else "") for pot in value["pots"]]
        lines += [f"  Bill {bill['id']} {bill['name']}: {bill['amount']} due {bill['next_due'] or '—'} ({bill['every']})" for bill in value["bills"]]
        lines += [f"  Rule {rule['id']}: {rule['percent']}% of income to {rule['pot']}" + (" (automatic)" if rule["auto"] else "") for rule in value["rules"]]
        if value["waiting"]:
            lines.append(f"{len(value['waiting'])} decision(s) wait for you: say “what should I do with my money?”.")
        return lines or ["Your HAYAT wallet has no pots or bills yet."]
    if value.get("spend_judged"):
        return [f"Spend {value['amount']} on {value['what']}: HAYAT says {value['verdict']}."] + [f"  · {reason}" for reason in value["reasons"]] + [value["note"]]
    if value.get("pot_created"):
        return [f"Created pot {value['name']}" + (f" with target {value['target']}" if value["target"] else "") + ".", value["note"]]
    if value.get("moved"):
        return [f"Moved {value['amount']} from {value['from']} to {value['to']}.", value["note"]]
    if value.get("rule_added"):
        return [value["note"]]
    if value.get("bill_added"):
        return [f"Added bill {value['name']}: {value['amount']} due {value['next_due']} (every {value['every']})." if value["every"] != "once"
                else f"Added bill {value['name']}: {value['amount']} due {value['next_due']}.", value["note"]]
    if value.get("targets_set"):
        return ["Portfolio targets: " + ", ".join(f"{kind} {share:g}%" for kind, share in value["targets"].items()) + ".", value["note"]]
    for key, words in (("pot_removed", "Removed pot {name}."), ("rule_removed", "Removed rule {rule_id}."), ("bill_removed", "Removed bill {bill_id}.")):
        if value.get(key):
            return [words.format(**value)]
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


def owner_word(desk: MoneyDesk, text: str) -> str | None:
    """Approve, decline or mark paid, from the owner's own words. The caller has checked it is the owner at this computer."""
    match = DECISION_WORD.fullmatch(text or "")
    if not match:
        return None
    try:
        if match["paid_id"]:
            result = desk.paid(int(match["paid_id"]), by_owner=True, reference=match["ref"] or "")
            return f"Recorded: {result['title']} is paid, as {result['recorded']}. ({result['source']}.)"
        decision_id = int(match["id"])
        if match["verb"].lower() == "approve":
            result = desk.approve(decision_id, by_owner=True)
            if result["status"] == "done":
                return f"Approved and done: {result['title']}. " + result.get("done", "")
            return f"Approved: {result['title']}. " + result.get("next", "") + (f" Payment page: {result['open']}" if result.get("open") else "")
        result = desk.decline(decision_id, by_owner=True)
        return f"Declined: {result['title']}. {result['note']}"
    except (ValueError, PermissionError) as exc:
        return str(exc)
