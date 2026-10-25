"""Portfolio and net worth in US dollars: cash, stocks and crypto the owner holds elsewhere, valued live.

What it is: a record of what the owner holds at their own bank, broker and exchange, with every position valued in
USD from HAYAT's market data, and crypto read straight from the public blockchain for addresses the owner watches.

What it is not, in code: a bank, broker, exchange or crypto wallet. Holding real money, shares or coins for someone
needs a licence (bank, broker-dealer, custodian, money transmitter), and holding crypto means holding private keys;
HAYAT keeps neither, never trades, and never signs. A buy or sell here is a record of one the owner made elsewhere.

* **Cash** in any currency, converted to USD at the day's reference rate (Frankfurter, from the European Central
  Bank; ExchangeRate-API for currencies the ECB does not publish), cached for six hours.
* **Crypto** valued at Kraken's live USD price; **stocks and funds** at Kraken's tokenized share (it tracks the
  exchange price, it is not it), or the exchange's own price when a Finnhub key is set.
* **Watch-only addresses:** a public Bitcoin or Ethereum address's balance, read from the chain each time. No key,
  seed or password is ever asked for, and a pasted one is refused.
* **Cost basis:** average cost per holding from recorded buys; sells realise profit or loss at that average.
* **Net worth over time:** one snapshot a day, taken when the portfolio is read.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)
KINDS = ("cash", "stock", "crypto")
FX_SECONDS = 6 * 3600
KEY_WORDS = re.compile(r"(?i)\b(?:seed|mnemonic|private\s*key|secret\s*key|recovery\s*phrase|passphrase|password|xprv|wif)\b")
BTC_ADDRESS = re.compile(r"^(?:bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$")
ETH_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
#: 64 hex characters or a 12/24-word phrase is a private key or seed: refused, never stored.
SECRET_SHAPES = (re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b"), re.compile(r"^(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}$"),
                 re.compile(r"\b[5KL][1-9A-HJ-NP-Za-km-z]{50,51}\b"))


def _decimal(value: Any, name: str, minimum: str = "0") -> Decimal:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"{name} must be a number.") from None
    if not number.is_finite() or number < Decimal(minimum):
        raise ValueError(f"{name} must be at least {minimum}.")
    return number


def refuse_keys(*values: str) -> None:
    for value in values:
        text = str(value or "").strip()
        if KEY_WORDS.search(text) or any(shape.search(text) for shape in SECRET_SHAPES):
            raise ValueError("That looks like a private key, seed phrase or password. HAYAT never keeps them: give only a public "
                             "address, which is enough to read a balance. Keep your keys in your own wallet app.")


class Portfolio:
    def __init__(self, path: Path | str, markets: Any = None, web3: Any = None, fetcher: Any = None, ledger: Any = None) -> None:
        self.path = Path(path)
        self.markets, self.web3, self.fetcher, self.ledger = markets, web3, fetcher, ledger
        self._fx: tuple[float, dict[str, float], str] | None = None
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS holdings (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, symbol TEXT NOT NULL,
                    account TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL, removed TEXT);
                CREATE TABLE IF NOT EXISTS moves (id INTEGER PRIMARY KEY, holding_id INTEGER NOT NULL REFERENCES holdings(id),
                    kind TEXT NOT NULL, quantity TEXT NOT NULL, price_usd TEXT, fee_usd TEXT NOT NULL DEFAULT '0',
                    on_day TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', recorded TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (day TEXT PRIMARY KEY, net_worth_usd TEXT NOT NULL, detail TEXT NOT NULL);
            """)

    def _db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ------------------------------------------------------------------ recording
    def _holding(self, db: sqlite3.Connection, kind: str, symbol: str, account: str) -> int:
        row = db.execute("SELECT id FROM holdings WHERE kind=? AND symbol=? AND account=? AND address='' AND removed IS NULL",
                         (kind, symbol, account)).fetchone()
        if row:
            return int(row["id"])
        return int(db.execute("INSERT INTO holdings (kind, symbol, account, created) VALUES (?, ?, ?, ?)",
                              (kind, symbol, account, datetime.now(UTC).isoformat(timespec="seconds"))).lastrowid)

    def record(self, kind: str, symbol: str, quantity: str, action: str = "buy", price_usd: str | None = None,
               account: str = "", on_day: str | None = None, fee_usd: str = "0", note: str = "") -> dict[str, Any]:
        """Record a holding change the owner made elsewhere: buy, sell, deposit, withdraw, or set (an opening balance)."""
        kind, action = (kind or "").strip().lower(), (action or "buy").strip().lower()
        if kind not in KINDS:
            raise ValueError("Kind is cash, stock or crypto.")
        if action not in ("buy", "sell", "deposit", "withdraw", "set"):
            raise ValueError("Action is buy, sell, deposit, withdraw or set.")
        symbol = re.sub(r"[^A-Za-z0-9.]", "", str(symbol or "")).upper()[:12]
        if not symbol:
            raise ValueError("Which symbol? e.g. USD, BTC, AAPL.")
        refuse_keys(account, note)
        if re.search(r"\d{6,}", re.sub(r"[\s-]", "", account)):
            raise ValueError("Name the account (e.g. Zerodha, Coinbase, HDFC savings ····1234), not its number.")
        amount = _decimal(quantity, "Quantity")
        if amount == 0 and action != "set":
            raise ValueError("Quantity must be above 0.")
        price = _decimal(price_usd, "Price") if price_usd not in (None, "") else None
        if kind == "cash":
            price = None  # cash is valued at the day's rate, never at a price
        fee = _decimal(fee_usd or "0", "Fee")
        day = (on_day or date.today().isoformat())[:10]
        try:
            if date.fromisoformat(day) > date.today():
                raise ValueError("A move can't be dated in the future.")
        except ValueError as exc:
            if "future" in str(exc):
                raise
            raise ValueError("Give the date as YYYY-MM-DD.") from None
        account = " ".join(str(account or "").split())[:60]
        with self._lock, self._db() as db:
            holding = self._holding(db, kind, symbol, account)
            if action in ("sell", "withdraw"):
                held = self._position(db, holding)["quantity"]
                if amount > held:
                    raise ValueError(f"That is more than the {held.normalize():f} {symbol} recorded in this holding.")
            db.execute("INSERT INTO moves (holding_id, kind, quantity, price_usd, fee_usd, on_day, note, recorded) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (holding, action, str(amount), str(price) if price is not None else None, str(fee), day, note.strip()[:200],
                        datetime.now(UTC).isoformat(timespec="seconds")))
            position = self._position(db, holding)
        return {"portfolio_view": True, "recorded": True, "holding_id": holding, "kind": kind, "symbol": symbol, "action": action,
                "quantity": f"{amount.normalize():f}", "now_held": f"{position['quantity'].normalize():f}", "account": account,
                "note": "Recorded as you said: HAYAT holds nothing and traded nothing. Your bank, broker or exchange holds it."}

    def watch_address(self, address: str, account: str = "") -> dict[str, Any]:
        """A public Bitcoin or Ethereum address whose balance is read from the chain; never a key."""
        value = str(address or "").strip()
        refuse_keys(value, account)
        if BTC_ADDRESS.fullmatch(value):
            symbol = "BTC"
        elif ETH_ADDRESS.fullmatch(value):
            symbol = "ETH"
        else:
            raise ValueError("Give a public Bitcoin (bc1…, 1…, 3…) or Ethereum (0x…) address.")
        with self._lock, self._db() as db:
            found = db.execute("SELECT id FROM holdings WHERE address=? AND removed IS NULL", (value,)).fetchone()
            if found:
                raise ValueError(f"Already watching that address (holding {found['id']}).")
            holding = int(db.execute("INSERT INTO holdings (kind, symbol, account, address, created) VALUES ('crypto', ?, ?, ?, ?)",
                                     (symbol, " ".join(str(account or "").split())[:60] or f"{symbol} address",
                                      value, datetime.now(UTC).isoformat(timespec="seconds"))).lastrowid)
        return {"portfolio_view": True, "watching": True, "holding_id": holding, "symbol": symbol, "address": value,
                "note": "Watch-only: HAYAT reads the public balance from the blockchain each time. It has no key and can't move anything."}

    def remove(self, holding_id: int) -> dict[str, Any]:
        with self._lock, self._db() as db:
            row = db.execute("SELECT * FROM holdings WHERE id=? AND removed IS NULL", (int(holding_id),)).fetchone()
            if row is None:
                raise ValueError(f"No holding {holding_id}. Say “show my portfolio”.")
            db.execute("UPDATE holdings SET removed=? WHERE id=?", (datetime.now(UTC).isoformat(timespec="seconds"), int(holding_id)))
        return {"portfolio_view": True, "removed": True, "holding_id": int(holding_id), "symbol": row["symbol"]}

    # ------------------------------------------------------------------ positions and prices
    @staticmethod
    def _position(db: sqlite3.Connection, holding_id: int) -> dict[str, Any]:
        """Quantity, average cost and realised profit, in recorded order (average-cost method)."""
        quantity, cost, realised = Decimal(0), Decimal(0), Decimal(0)
        for move in db.execute("SELECT * FROM moves WHERE holding_id=? ORDER BY on_day, id", (holding_id,)):
            amount = Decimal(move["quantity"])
            price = Decimal(move["price_usd"]) if move["price_usd"] is not None else None
            fee = Decimal(move["fee_usd"] or "0")
            if move["kind"] == "set":
                quantity, cost = amount, (amount * price if price is not None else Decimal(0))
            elif move["kind"] in ("buy", "deposit"):
                quantity += amount
                cost += (amount * price + fee) if price is not None else Decimal(0)
            else:
                average = cost / quantity if quantity else Decimal(0)
                if price is not None:
                    realised += amount * (price - average) - fee
                cost -= average * amount
                quantity -= amount
        return {"quantity": quantity, "cost": cost, "realised": realised}

    def fx_rates(self) -> tuple[dict[str, float], str]:
        """USD per unit is 1/rate; rates are units per USD. Cached six hours; ECB first, ExchangeRate-API for the rest."""
        with self._lock:
            if self._fx and time.monotonic() - self._fx[0] < FX_SECONDS:
                return self._fx[1], self._fx[2]
        rates: dict[str, float] = {"USD": 1.0}
        sources = []
        for url, name in (("https://api.frankfurter.app/latest?from=USD", "European Central Bank via Frankfurter"),
                          ("https://open.er-api.com/v6/latest/USD", "ExchangeRate-API (www.exchangerate-api.com)")):
            if self.fetcher is None:
                break
            try:
                data = json.loads(bytes(self.fetcher.fetch(url, 2_000_000, {"application/json"}).content))
                fresh = {code.upper(): float(value) for code, value in (data.get("rates") or {}).items() if value}
                added = [code for code in fresh if code not in rates]
                rates.update({code: fresh[code] for code in added})
                if added:
                    sources.append(name)
            except Exception:  # the other source, or the cached rates, stand in
                log_swallowed(_log, f"reading exchange rates from {name}")
        source = " and ".join(sources) or "no exchange-rate source reachable"
        with self._lock:
            if len(rates) > 1 or self._fx is None:
                self._fx = (time.monotonic(), rates, source)
            return self._fx[1], self._fx[2]

    def _price(self, kind: str, symbol: str, rates: dict[str, float]) -> dict[str, Any]:
        if kind == "cash":
            rate = rates.get(symbol)
            return {"usd": 1 / rate if rate else None, "source": "exchange rate" if symbol != "USD" else "",
                    "note": "" if rate else f"no exchange rate for {symbol}"}
        if self.markets is None:
            return {"usd": None, "source": "", "note": "no market data in this session"}
        try:
            quote = self.markets.quote(symbol, "USD", "crypto" if kind == "crypto" else "stock")
            price = (quote.get("exchange") or {}).get("price") or quote.get("price")
            source = ("exchange price (Finnhub)" if quote.get("exchange") else
                      "Kraken tokenized share (tracks the exchange, may drift)" if kind == "stock" else "Kraken")
            return {"usd": float(price) if price else None, "source": source, "change_pct": quote.get("change_today_pct"), "note": ""}
        except Exception as exc:  # an unknown symbol is shown, not hidden
            return {"usd": None, "source": "", "note": str(exc)[:120]}

    # ------------------------------------------------------------------ the view
    def view(self) -> dict[str, Any]:
        rates, fx_source = self.fx_rates()
        rows, total, cost_total, realised_total = [], 0.0, 0.0, 0.0
        with self._db() as db:
            holdings = [dict(row) for row in db.execute("SELECT * FROM holdings WHERE removed IS NULL ORDER BY kind, symbol")]
            positions = {row["id"]: self._position(db, row["id"]) for row in holdings}
        for holding in holdings:
            position = positions[holding["id"]]
            quantity, origin, tokens = position["quantity"], "recorded", 0.0
            if holding["address"] and self.web3 is not None:
                try:
                    found = self.web3.lookup(holding["address"])
                    quantity, origin = Decimal(str(found.get("balance") or 0)), "read from the blockchain"
                    tokens = float(found.get("tokens_usd") or 0)
                    if tokens:
                        origin += f" (+{usd(tokens)} in priced tokens)"
                except Exception as exc:  # the address stays listed with its last recorded amount
                    origin = f"chain unreachable ({type(exc).__name__})"
            if quantity == 0 and not holding["address"]:
                continue
            price = self._price(holding["kind"], holding["symbol"], rates)
            value = float(quantity) * price["usd"] + tokens if price["usd"] is not None else (tokens or None)
            cost = float(position["cost"]) if position["cost"] and not holding["address"] else None
            row = {"id": holding["id"], "kind": holding["kind"], "symbol": holding["symbol"], "account": holding["account"],
                   "address": holding["address"], "quantity": f"{quantity.normalize():f}", "origin": origin,
                   "price_usd": round(price["usd"], 6) if price["usd"] is not None else None, "price_source": price["source"],
                   "value_usd": round(value, 2) if value is not None else None, "cost_usd": round(cost, 2) if cost else None,
                   "gain_usd": round(value - cost, 2) if value is not None and cost else None,
                   "gain_pct": round((value / cost - 1) * 100, 1) if value is not None and cost else None,
                   "change_today_pct": price.get("change_pct"), "realised_usd": round(float(position["realised"]), 2),
                   "note": price["note"]}
            rows.append(row)
            total += value or 0
            cost_total += cost or 0
            realised_total += float(position["realised"])
        by_kind = {kind: round(sum(row["value_usd"] or 0 for row in rows if row["kind"] == kind), 2) for kind in KINDS}
        allocation = {kind: round(value / total * 100, 1) for kind, value in by_kind.items() if total and value}
        by_account: dict[str, float] = {}
        for row in rows:
            by_account[row["account"] or "unnamed"] = round(by_account.get(row["account"] or "unnamed", 0) + (row["value_usd"] or 0), 2)
        history = self._snapshot(total, by_kind)
        unpriced = [row for row in rows if row["value_usd"] is None]
        wallet = None
        if self.ledger is not None:
            try:
                wallet = self.wallet_in_usd(self.ledger)
            except Exception:  # the portfolio stands without the business wallet
                log_swallowed(_log, "converting the business wallet to USD")
        return {"portfolio_view": True, "overview": True, "net_worth_usd": round(total, 2), "by_kind": by_kind, "allocation": allocation,
                "by_account": by_account, "holdings": sorted(rows, key=lambda row: -(row["value_usd"] or 0)),
                "invested_usd": round(cost_total, 2), "unrealised_usd": round(sum(row["gain_usd"] or 0 for row in rows), 2),
                "realised_usd": round(realised_total, 2), "history": history, "unpriced": [row["symbol"] for row in unpriced],
                "business_wallet": wallet, "fx_source": fx_source, "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "basis": ("What you hold at your own bank, broker and exchange, as you recorded it (or read from the chain for "
                          "watched addresses), valued live in USD. HAYAT holds nothing, trades nothing and keeps no keys. Prices: "
                          "Kraken (crypto, tokenized shares) or Finnhub with your key; exchange rates: " + fx_source + ".")}

    def _snapshot(self, total: float, by_kind: dict[str, float]) -> list[dict[str, Any]]:
        today = date.today().isoformat()
        with self._lock, self._db() as db:
            if total:
                db.execute("INSERT INTO snapshots VALUES (?, ?, ?) ON CONFLICT(day) DO UPDATE SET net_worth_usd=excluded.net_worth_usd, "
                           "detail=excluded.detail", (today, f"{total:.2f}", json.dumps(by_kind)))
            return [{"day": row["day"], "net_worth_usd": float(row["net_worth_usd"])}
                    for row in db.execute("SELECT day, net_worth_usd FROM snapshots ORDER BY day DESC LIMIT 90")][::-1]

    def wallet_in_usd(self, ledger: Any) -> dict[str, Any]:
        """The business wallet's balances converted to USD, so everything reads in one currency."""
        rates, source = self.fx_rates()
        rows, total, missing = [], 0.0, []
        for balance in ledger.wallet(activity=0)["balances"]:
            rate = rates.get(balance["currency"])
            if not rate:
                missing.append(balance["currency"])
                continue
            usd = balance["available_cents"] / 100 / rate
            total += usd
            rows.append({"currency": balance["currency"], "available": balance["available"], "usd": round(usd, 2)})
        return {"total_usd": round(total, 2), "rows": rows, "missing": missing, "source": source}


# ---------------------------------------------------------------------- tools, chat, replies
TOOL_NAMES = ("portfolio_view", "portfolio_record", "portfolio_watch_address", "portfolio_remove")


def register_portfolio_tools(registry: Any, portfolio: Portfolio) -> None:
    def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    note = " HAYAT holds nothing, trades nothing and keeps no keys."
    registry.register("portfolio_view", "The owner's portfolio and net worth in USD: cash, stocks and crypto they hold at their own bank, "
                      "broker and exchange, valued live (Kraken, Finnhub with a key, ECB exchange rates), watched crypto addresses read "
                      "from the chain, allocation, cost basis, unrealised and realised gains, and daily net-worth history." + note,
                      obj({}), "low", "public_api_get", portfolio.view)
    registry.register("portfolio_record", "Record a holding change the owner made elsewhere: buy, sell, deposit, withdraw, or set an opening "
                      "balance, for cash (any currency), a stock or fund, or a coin, with the USD price and account." + note,
                      obj({"kind": {"enum": list(KINDS)}, "symbol": {"type": "string", "maxLength": 12}, "quantity": {"type": "string", "maxLength": 30},
                           "action": {"enum": ["buy", "sell", "deposit", "withdraw", "set"]}, "price_usd": {"type": "string", "maxLength": 30},
                           "account": {"type": "string", "maxLength": 60}, "on_day": {"type": "string", "maxLength": 10},
                           "fee_usd": {"type": "string", "maxLength": 20}, "note": {"type": "string", "maxLength": 200}},
                          ("kind", "symbol", "quantity")), "low", "filesystem_write_project", portfolio.record)
    registry.register("portfolio_watch_address", "Watch a public Bitcoin or Ethereum address: its balance is read from the blockchain each time "
                      "the portfolio is shown. Private keys and seed phrases are refused." + note,
                      obj({"address": {"type": "string", "maxLength": 100}, "account": {"type": "string", "maxLength": 60}}, ("address",)),
                      "low", "filesystem_write_project", portfolio.watch_address)
    registry.register("portfolio_remove", "Remove a holding from the portfolio record.",
                      obj({"holding_id": {"type": "integer", "minimum": 1}}, ("holding_id",)), "low", "filesystem_write_project",
                      portfolio.remove)


_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_COINS = {"bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH", "ether": "ETH", "solana": "SOL", "sol": "SOL", "xrp": "XRP",
          "dogecoin": "DOGE", "doge": "DOGE", "cardano": "ADA", "ada": "ADA", "usdc": "USDC", "usdt": "USDT", "litecoin": "LTC", "ltc": "LTC"}
_CASH = {"usd": "USD", "dollars": "USD", "dollar": "USD", "inr": "INR", "rupees": "INR", "rupee": "INR", "eur": "EUR", "euros": "EUR",
         "gbp": "GBP", "pounds": "GBP", "aed": "AED", "dirhams": "AED"}
_VIEW = re.compile(_LEAD + r"(?:show\s+(?:me\s+)?|open\s+|what(?:'s|\s+is)\s+)?(?:my\s+)?(?:portfolio|net\s+worth|holdings)(?:\s+(?:now|today|in\s+usd))?"
                   r"|how\s+much\s+(?:is\s+)?(?:my\s+)?(?:portfolio|crypto|stocks?|holdings)\s+(?:is\s+)?worth(?:\s+now)?|how\s+much\s+am\s+i\s+worth\s*[?.!]*$",
                   re.IGNORECASE)
_HOLD = re.compile(_LEAD + r"(?:(?P<verb>add|record|i\s+(?:have|hold|own|bought|sold)|i've\s+(?:bought|sold)|record\s+(?:a\s+)?(?:buy|sell)\s+of|"
                   r"(?:buy|sell)\s+record(?:ed)?\s+of))\s+(?P<qty>" + _NUMBER + r")\s+(?P<what>[A-Za-z.]{1,12})(?:\s+(?:shares?|coins?|stocks?))?"
                   r"(?:\s+(?:at|@|for)\s+\$?(?P<price>" + _NUMBER + r")(?:\s+(?:each|usd|dollars))?)?"
                   r"(?:\s+(?:in|on|at|with)\s+(?:my\s+)?(?P<account>[\w .&'-]{2,30}?))?(?:\s+to\s+my\s+portfolio)?\s*[?.!]*$", re.IGNORECASE)
#: "Watch address X" alone belongs to the Web3 explorer's watch list (app/web3_chat.py); the portfolio takes it when named.
_WATCH = re.compile(_LEAD + r"(?:watch|track|add)\s+(?:my\s+)?(?:bitcoin|btc|ethereum|eth|crypto)?\s*(?:wallet\s+)?address\s+(?P<address>\S{20,100})"
                    r"\s+(?:to|in|on)\s+(?:my\s+)?(?:portfolio|net\s+worth)\s*[?.!]*$", re.IGNORECASE)
_REMOVE = re.compile(_LEAD + r"(?:remove|delete)\s+holding\s+#?(?P<id>\d+)\s*[?.!]*$", re.IGNORECASE)


def portfolio_request(text: str) -> dict[str, Any] | None:
    raw = " ".join(str(text or "").split())
    if not raw or len(raw) > 200:
        return None
    if _VIEW.fullmatch(raw.rstrip("?.! ")) or _VIEW.match(raw) and raw.lower().rstrip("?.! ").endswith(("portfolio", "net worth", "holdings", "worth")):
        return {"tool": "portfolio_view", "arguments": {}}
    if match := _WATCH.match(raw):
        return {"tool": "portfolio_watch_address", "arguments": {"address": match["address"]}}
    if match := _REMOVE.match(raw):
        return {"tool": "portfolio_remove", "arguments": {"holding_id": int(match["id"])}}
    if match := _HOLD.match(raw):
        verb, word = match["verb"].lower(), match["what"].lower()
        if word in _CASH:
            kind, symbol = "cash", _CASH[word]
        elif word in _COINS:
            kind, symbol = "crypto", _COINS[word]
        elif re.search(r"(?i)\b(?:shares?|stocks?)\b", raw) or match["what"].isupper():
            from app.markets import STOCK_ALIASES
            kind, symbol = "stock", STOCK_ALIASES.get(word, match["what"].upper())
        else:
            return None
        action = "sell" if "sold" in verb or "sell" in verb else "set" if verb in ("i have", "i hold", "i own") else \
            "deposit" if kind == "cash" else "buy"
        arguments = {"kind": kind, "symbol": symbol, "quantity": match["qty"].replace(",", ""), "action": action}
        if match["price"]:
            arguments["price_usd"] = match["price"].replace(",", "")
        if match["account"]:
            arguments["account"] = match["account"].strip()
        return {"tool": "portfolio_record", "arguments": arguments}
    return None


def direct_plan(text: str, names: Any) -> dict[str, Any] | None:
    found = portfolio_request(text)
    if not found or found["tool"] not in names:
        return None
    return {"description": "Keep and value the owner's portfolio record in USD; nothing is held or traded.", "tool": found["tool"],
            "arguments": found["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}


def usd(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def report_lines(value: dict[str, Any]) -> list[str]:
    if value.get("recorded"):
        return [f"Recorded: {value['action']} {value['quantity']} {value['symbol']}" + (f" in {value['account']}" if value["account"] else "")
                + f". Now recorded: {value['now_held']} {value['symbol']}.", value["note"]]
    if value.get("watching"):
        return [f"Watching {value['symbol']} address {value['address'][:12]}…{value['address'][-6:]} (holding {value['holding_id']}).", value["note"]]
    if value.get("removed"):
        return [f"Removed holding {value['holding_id']} ({value['symbol']})."]
    if not value.get("overview"):
        return []
    if not value["holdings"]:
        return ["Your portfolio is empty. Record what you hold elsewhere: “I hold 0.25 BTC at 58000 on Coinbase”, “add 10 AAPL shares at "
                "180”, “I have 2500 USD in my Chase account”, or “add address bc1… to my portfolio” for a public Bitcoin address.", value["basis"]]
    lines = [f"Net worth {usd(value['net_worth_usd'])}: " + ", ".join(f"{kind} {usd(amount)} ({value['allocation'].get(kind, 0):g}%)"
                                                                       for kind, amount in value["by_kind"].items() if amount)]
    if value["invested_usd"]:
        lines.append(f"Invested {usd(value['invested_usd'])} · unrealised {usd(value['unrealised_usd'])} · realised {usd(value['realised_usd'])}.")
    for row in value["holdings"][:15]:
        lines.append(f"  #{row['id']} {row['symbol']} {row['quantity']}" + (f" ({row['account']})" if row["account"] else "")
                     + f" · {usd(row['value_usd'])}" + (f" at {usd(row['price_usd'])}" if row["price_usd"] and row["kind"] != "cash" else "")
                     + (" (tokenized share price)" if "tokenized" in row["price_source"] else "")
                     + (f" · {row['gain_pct']:+.1f}%" if row["gain_pct"] is not None else "")
                     + (f" · {row['origin']}" if row["origin"] != "recorded" else "") + (f" · {row['note']}" if row["note"] else ""))
    wallet = value.get("business_wallet") or {}
    if wallet.get("rows"):
        lines.append(f"Business wallet ≈ {usd(wallet['total_usd'])} (" + ", ".join(row["available"] for row in wallet["rows"])
                     + "); not added above, since that money sits in your own accounts.")
    if len(value["history"]) > 1:
        first, last = value["history"][0], value["history"][-1]
        lines.append(f"Since {first['day']}: {usd(first['net_worth_usd'])} → {usd(last['net_worth_usd'])}.")
    lines.append(value["basis"])
    return lines


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
