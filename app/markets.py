"""Crypto markets and public blockchain data, read-only.

The owner asked for "all crypto and a trade center" (2026-10-10). This reads
public market data and shows it; it never trades. HAYAT holds no exchange
account, wallet, API key or seed phrase, and a request to buy, sell or move
crypto is stopped before any tool (app/safety/money_requests.py).

Sources, each free, keyless and allowed by its robots.txt (checked 2026-10-10):

- Kraken public API: every listed asset (~850) and pair (~1,500), tickers,
  candles, order book and recent trades. CoinGecko and Binance were not used:
  their robots.txt disallow their APIs.
- Coinbase Exchange public API: asset names and a second price for a cross-check.
- mempool.space: Bitcoin fees, mempool and block height.
- Blockscout (eth.blockscout.com): Ethereum gas, block time and activity.

"Change" in the market list is since 00:00 UTC (Kraken's daily open), and the
quote view adds Coinbase's rolling 24-hour change; both are labelled.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

KRAKEN = "https://api.kraken.com/0/public/"
COINBASE = "https://api.exchange.coinbase.com/"
MEMPOOL = "https://mempool.space/api/"
BLOCKSCOUT = "https://eth.blockscout.com/api/v2/stats"
JSON_TYPES = {"application/json", "text/json", "application/*+json", "text/plain"}
#: Kraken's own codes for a few assets, shown under their usual tickers.
KRAKEN_NAMES = {"XBT": "BTC", "XDG": "DOGE"}
QUOTES = ("USD", "EUR", "USDT", "USDC", "GBP")
#: Government currencies Kraken also lists against USD; they are not crypto markets.
FIAT = {"EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USD", "AED", "SGD", "NZD", "HKD", "SEK", "NOK", "DKK", "PLN", "CZK"}
#: Chart ranges: Kraken candle size in minutes and how many candles to show.
RANGES = {"1D": (15, 96), "1W": (60, 168), "1M": (240, 180), "3M": (1440, 90), "1Y": (1440, 365)}
#: Names for the common coins, so chat recognises them without a network call.
COIN_ALIASES = {
    "bitcoin": "BTC", "btc": "BTC", "xbt": "BTC", "ethereum": "ETH", "ether": "ETH", "eth": "ETH", "solana": "SOL", "sol": "SOL",
    "ripple": "XRP", "xrp": "XRP", "dogecoin": "DOGE", "doge": "DOGE", "cardano": "ADA", "ada": "ADA", "tether": "USDT",
    "usdt": "USDT", "usd coin": "USDC", "usdc": "USDC", "litecoin": "LTC", "ltc": "LTC", "polkadot": "DOT", "dot": "DOT",
    "chainlink": "LINK", "link": "LINK", "avalanche": "AVAX", "avax": "AVAX", "tron": "TRX", "trx": "TRX", "shiba inu": "SHIB",
    "shib": "SHIB", "polygon": "POL", "matic": "POL", "pol": "POL", "toncoin": "TON", "ton": "TON", "stellar": "XLM", "xlm": "XLM",
    "monero": "XMR", "xmr": "XMR", "bitcoin cash": "BCH", "bch": "BCH", "uniswap": "UNI", "uni": "UNI", "cosmos": "ATOM",
    "atom": "ATOM", "near": "NEAR", "aptos": "APT", "apt": "APT", "sui": "SUI", "pepe": "PEPE", "arbitrum": "ARB", "arb": "ARB",
    "optimism": "OP", "filecoin": "FIL", "fil": "FIL", "algorand": "ALGO", "algo": "ALGO", "tezos": "XTZ", "xtz": "XTZ",
    "kaspa": "KAS", "kas": "KAS", "render": "RENDER", "injective": "INJ", "inj": "INJ", "aave": "AAVE", "maker": "MKR",
    "bonk": "BONK", "dogwifhat": "WIF", "wif": "WIF", "hedera": "HBAR", "hbar": "HBAR", "binance coin": "BNB", "bnb": "BNB",
    "ethereum classic": "ETC", "etc": "ETC", "zcash": "ZEC", "zec": "ZEC", "dash": "DASH", "trump": "TRUMP",
}
#: Names for the tokenized shares and funds HAYAT uses most (Kraken publishes tickers only).
STOCK_NAMES = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon", "GOOGL": "Alphabet", "META": "Meta", "TSLA": "Tesla",
    "AMD": "AMD", "INTC": "Intel", "TSM": "TSMC", "ASML": "ASML", "AVGO": "Broadcom", "MU": "Micron", "QCOM": "Qualcomm",
    "NFLX": "Netflix", "ORCL": "Oracle", "CRM": "Salesforce", "ADBE": "Adobe", "IBM": "IBM", "CSCO": "Cisco", "PLTR": "Palantir",
    "CRWD": "CrowdStrike", "PANW": "Palo Alto Networks", "NET": "Cloudflare", "COIN": "Coinbase", "MSTR": "Strategy",
    "HOOD": "Robinhood", "PYPL": "PayPal", "V": "Visa", "JPM": "JPMorgan Chase", "BAC": "Bank of America", "GS": "Goldman Sachs",
    "BRK.B": "Berkshire Hathaway", "XOM": "ExxonMobil", "CVX": "Chevron", "LNG": "Cheniere Energy", "BKR": "Baker Hughes",
    "LLY": "Eli Lilly", "JNJ": "Johnson & Johnson", "PFE": "Pfizer", "MRK": "Merck", "MRNA": "Moderna", "UNH": "UnitedHealth",
    "NVO": "Novo Nordisk", "AZN": "AstraZeneca", "ABBV": "AbbVie", "KO": "Coca-Cola", "PEP": "PepsiCo", "MCD": "McDonald's",
    "PG": "Procter & Gamble", "UBER": "Uber", "HON": "Honeywell", "ETN": "Eaton", "GEV": "GE Vernova", "CEG": "Constellation Energy",
    "SPY": "S&P 500 fund", "VOO": "S&P 500 fund (Vanguard)", "QQQ": "Nasdaq-100 fund", "IWM": "US small caps fund",
    "VTI": "US total market fund", "XLE": "US energy sector fund", "XOP": "Oil & gas producers fund", "SMH": "Semiconductors fund",
    "SOXX": "Semiconductors fund (iShares)", "GLD": "Gold fund", "SLV": "Silver fund", "GDX": "Gold miners fund",
    "COPX": "Copper miners fund", "ITA": "Aerospace & defence fund", "URA": "Uranium fund", "NLR": "Nuclear energy fund",
    "MOO": "Agribusiness fund", "PALL": "Palladium fund", "PPLT": "Platinum fund", "EWG": "Germany fund", "EWQ": "France fund",
    "EWU": "United Kingdom fund", "EWY": "South Korea fund", "FEZ": "Euro Stoxx 50 fund", "VGK": "Europe fund", "DAX": "Germany DAX fund",
    "IEMG": "Emerging markets fund", "SGOV": "US Treasury bills fund", "TQQQ": "Nasdaq-100 3x fund", "SOXL": "Semiconductors 3x fund",
}
STOCK_ALIASES = {name.lower(): ticker for ticker, name in STOCK_NAMES.items()} | {
    "google": "GOOGL", "facebook": "META", "s&p 500": "SPY", "s&p": "SPY", "nasdaq": "QQQ", "gold": "GLD", "silver": "SLV",
    "berkshire": "BRK.B", "exxon": "XOM", "tsmc": "TSM", "broadcom": "AVGO"}


class MarketData:
    """Cached public market reads. Every method returns plain data; nothing here can place an order."""

    TTL = {"assets": 6 * 3600, "names": 24 * 3600, "ticker": 30, "ohlc": 60, "depth": 10, "trades": 10, "chain": 60,
           "coinbase": 30}

    def __init__(self, fetcher: Any, path: Path | str | None = None) -> None:
        self.fetcher = fetcher
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                db.execute("CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY, added_at TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
                           "direction TEXT NOT NULL, price REAL NOT NULL, created_at TEXT NOT NULL, triggered_at TEXT, "
                           "triggered_price REAL)")

    # ------------------------------------------------------------------ fetching
    def _db(self) -> sqlite3.Connection:
        if not self.path:
            raise ValueError("The watchlist needs a data folder.")
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _get(self, kind: str, key: str, url: str, max_bytes: int = 8 * 1024 * 1024) -> Any:
        with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < self.TTL[kind]:
                return cached[1]
        resource = self.fetcher.fetch(url, max_bytes, JSON_TYPES)
        data = json.loads(bytes(resource.content))
        if isinstance(data, dict) and data.get("error"):
            raise ValueError("Kraken: " + "; ".join(map(str, data["error"])))
        with self._lock:
            self._cache[key] = (time.monotonic(), data)
            while len(self._cache) > 300:
                self._cache.pop(next(iter(self._cache)))
        return data

    def _kraken(self, kind: str, method: str, query: str = "") -> Any:
        url = KRAKEN + method + (("?" + query) if query else "")
        return self._get(kind, "kraken:" + method + "?" + query, url, 16 * 1024 * 1024)["result"]

    @staticmethod
    def _ticker_name(code: str) -> str:
        return KRAKEN_NAMES.get(code, code)

    def pairs(self) -> dict[str, dict[str, str]]:
        """{pair key: {base, quote, altname}} for every online Kraken pair, in usual tickers."""
        result = {}
        for key, pair in self._kraken("assets", "AssetPairs").items():
            name = pair.get("wsname") or ""
            if "/" not in name or pair.get("status", "online") != "online" or key.endswith(".d"):
                continue
            base, quote = name.split("/", 1)
            result[key] = {"base": self._ticker_name(base), "quote": self._ticker_name(quote), "altname": pair.get("altname", key)}
        return result

    def names(self) -> dict[str, str]:
        """Ticker → name from Coinbase's currency list (Kraken publishes tickers only)."""
        try:
            rows = self._get("names", "coinbase:currencies", COINBASE + "currencies", 4 * 1024 * 1024)
            return {str(row.get("id", "")).upper(): str(row.get("name") or "") for row in rows if isinstance(row, dict)}
        except Exception:  # Names are a nicety: tickers are shown alone when Coinbase is unreachable.
            log_swallowed(_log, "reading Coinbase currency names")
            return {}

    def symbol(self, text: str) -> str:
        """'bitcoin' / 'btc' / 'XBT' → 'BTC'; anything else upper-cased."""
        value = " ".join(str(text or "").lower().split())
        return COIN_ALIASES.get(value, KRAKEN_NAMES.get(value.upper(), value.upper()))

    def _pair_for(self, symbol: str, quote: str = "USD") -> str:
        symbol = self.symbol(symbol)
        pairs = self.pairs()
        for wanted in (quote, "USD", "USDT", "USDC", "EUR"):
            for key, pair in pairs.items():
                if pair["base"] == symbol and pair["quote"] == wanted:
                    return key
        raise ValueError(f"Kraken lists no market for {symbol}. Try its ticker, e.g. BTC, ETH or SOL.")

    # ------------------------------------------------------------------ stocks (tokenized on Kraken)
    def stock_pairs(self) -> dict[str, dict[str, str]]:
        """{'AAPL': {key, ws, base}} for Kraken's tokenized shares in USD (xStocks)."""
        result = {}
        for key, pair in self._kraken("assets", "AssetPairs", "aclass_base=tokenized_asset").items():
            name = pair.get("wsname") or ""
            base, _, quote = name.partition("/")
            if quote == "USD" and base.endswith("x") and pair.get("status") in ("online", "post_only", "limit_only"):
                result[base[:-1]] = {"key": key, "ws": name, "base": base, "status": pair.get("status", "")}
        return result

    def _resolve(self, symbol: str, quote: str = "USD", asset: str = "auto") -> tuple[str, dict[str, str], str, str]:
        """(pair key, pair, extra query, 'crypto'|'stock'). 'auto' prefers crypto, then a tokenized share."""
        if asset not in ("auto", "crypto", "stock"):
            raise ValueError("Asset is crypto, stock or auto.")
        if asset in ("auto", "crypto"):
            try:
                key = self._pair_for(symbol, quote)
                return key, self.pairs()[key], "", "crypto"
            except ValueError:
                if asset == "crypto":
                    raise
        ticker = str(symbol or "").upper().strip().removesuffix("X") if str(symbol).endswith("x") else str(symbol or "").upper().strip()
        ticker = STOCK_ALIASES.get(ticker.lower(), ticker)
        stock = self.stock_pairs().get(ticker)
        if stock is None:
            raise ValueError(f"Kraken lists no market for {self.symbol(symbol)}: neither a coin nor a tokenized stock.")
        return stock["key"], {"base": ticker, "quote": "USD", "altname": stock["key"]}, "&asset_class=tokenized_asset", "stock"

    def stocks(self, query: str = "", sort: str = "volume", limit: int = 400) -> dict[str, Any]:
        """Every tokenized share on Kraken with its last price, today's change and how actively it trades."""
        pairs = self.stock_pairs()
        tickers = self._kraken("ticker", "Ticker", "asset_class=tokenized_asset")
        needle = " ".join(str(query or "").lower().split())
        rows = []
        for symbol, stock in pairs.items():
            row = self._row({"base": symbol, "quote": "USD"}, tickers.get(stock["key"]) or {})
            if row is None:
                continue
            row.update(name=STOCK_NAMES.get(symbol, ""), asset="stock", thin=row["trades_24h"] < 5)
            if needle and needle not in symbol.lower() and needle not in row["name"].lower():
                continue
            rows.append(row)
        orders = {"volume": (lambda row: -row["volume_24h_quote"]), "change": (lambda row: -(row["change_today_pct"] or 0)),
                  "losers": (lambda row: row["change_today_pct"] or 0), "name": (lambda row: row["symbol"])}
        rows.sort(key=orders.get(sort, orders["volume"]))
        return {"markets": True, "stocks_view": True, "quote": "USD", "total": len(rows), "rows": rows[:max(1, min(int(limit), 1000))],
                "assets_listed": len(pairs), "fetched_at": datetime.now(UTC).isoformat(), "source": "Kraken tokenized stocks (xStocks)",
                "basis": "Tokenized shares that track the real stock; they trade around the clock and can drift from the exchange "
                         "price, most when the US market is closed. 'Thin' means under 5 trades in 24 hours: the price may be old."}

    def exchange_quote(self, symbol: str) -> dict[str, Any] | None:
        """The real exchange price from Finnhub, when the owner added a free key (HAYAT_KEY_FINNHUB); else None."""
        import os
        token = os.environ.get("HAYAT_KEY_FINNHUB", "").strip()
        if not token:
            return None
        resource = self.fetcher.fetch(f"https://finnhub.io/api/v1/quote?symbol={symbol.upper()}", 64 * 1024, JSON_TYPES,
                                      request_headers={"X-Finnhub-Token": token})
        data = json.loads(bytes(resource.content))
        if not data.get("t"):
            return None
        return {"source": "Finnhub (exchange quote)", "price": float(data["c"]), "change_pct": data.get("dp"),
                "previous_close": data.get("pc"), "at": datetime.fromtimestamp(int(data["t"]), UTC).isoformat()}

    # ------------------------------------------------------------------ market
    @staticmethod
    def _row(pair: dict[str, str], ticker: dict[str, Any]) -> dict[str, Any] | None:
        try:
            last, opened = float(ticker["c"][0]), float(ticker["o"])
            vwap, volume = float(ticker["p"][1]), float(ticker["v"][1])
            return {"symbol": pair["base"], "quote": pair["quote"], "price": last,
                    "change_today_pct": round((last - opened) / opened * 100, 2) if opened else None,
                    "high_24h": float(ticker["h"][1]), "low_24h": float(ticker["l"][1]),
                    "volume_24h": volume, "volume_24h_quote": round(volume * vwap, 2), "trades_24h": int(ticker["t"][1]),
                    "bid": float(ticker["b"][0]), "ask": float(ticker["a"][0])}
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def overview(self, quote: str = "USD", query: str = "", sort: str = "volume", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Every Kraken market in one quote currency, sorted; `total` counts all of them."""
        quote = str(quote or "USD").upper()
        if quote not in QUOTES:
            raise ValueError("Choose USD, EUR, USDT, USDC or GBP.")
        pairs = {key: pair for key, pair in self.pairs().items() if pair["quote"] == quote and pair["base"] not in FIAT}
        tickers = self._kraken("ticker", "Ticker")
        names = self.names()
        needle = " ".join(str(query or "").lower().split())
        alias = COIN_ALIASES.get(needle)
        rows = []
        for key, pair in pairs.items():
            row = self._row(pair, tickers.get(key) or {})
            if row is None:
                continue
            row["name"] = names.get(row["symbol"], "")
            if needle and not (alias == row["symbol"] or needle in row["symbol"].lower() or needle in row["name"].lower()):
                continue
            rows.append(row)
        orders = {"volume": (lambda row: -row["volume_24h_quote"]), "change": (lambda row: -(row["change_today_pct"] or 0)),
                  "losers": (lambda row: row["change_today_pct"] or 0), "price": (lambda row: -row["price"]),
                  "name": (lambda row: row["symbol"])}
        if sort not in orders:
            raise ValueError("Sort by volume, change, losers, price or name.")
        rows.sort(key=orders[sort])
        limit = max(1, min(int(limit), 1000))
        return {"markets": True, "quote": quote, "sort": sort, "query": query, "total": len(rows),
                "rows": rows[offset:offset + limit],
                "assets_listed": len({pair["base"] for pair in self.pairs().values() if pair["base"] not in FIAT}),
                "fetched_at": datetime.now(UTC).isoformat(), "source": "Kraken public API",
                "basis": "Change is since 00:00 UTC (Kraken's daily open). Volume is the last 24 hours in the quote currency."}

    def quote(self, symbol: str, quote: str = "USD", asset: str = "auto") -> dict[str, Any]:
        key, pair, extra, kind = self._resolve(symbol, quote, asset)
        tickers = self._kraken("ticker", "Ticker", "pair=" + key + extra)
        row = self._row(pair, tickers.get(key) or next(iter(tickers.values()), {}))
        if row is None:
            raise ValueError(f"Kraken returned no price for {pair['base']}/{pair['quote']}.")
        if kind == "stock":
            return self._stock_quote(row, key)
        row["name"] = self.names().get(row["symbol"], "")
        row["asset"] = "crypto"
        check = None
        try:
            product = f"{row['symbol']}-{'USD' if row['quote'] in ('USD', 'USDT', 'USDC') else row['quote']}"
            stats = self._get("coinbase", "coinbase:stats:" + product, COINBASE + f"products/{product}/stats", 64 * 1024)
            other, opened = float(stats["last"]), float(stats["open"])
            check = {"source": "Coinbase Exchange", "product": product, "price": other,
                     "difference_pct": round((other - row["price"]) / row["price"] * 100, 3),
                     "change_24h_pct": round((other - opened) / opened * 100, 2) if opened else None}
        except Exception:  # Not every coin trades on Coinbase; the Kraken price stands alone then.
            log_swallowed(_log, "cross-checking a price on Coinbase")
        triggered = self.check_alerts({row["symbol"]: row["price"]}) if self.path else []
        return {"markets": True, "quote_view": True, **row, "cross_check": check, "triggered_alerts": triggered,
                "fetched_at": datetime.now(UTC).isoformat(), "source": "Kraken public API",
                "basis": "Last trade price on Kraken. Change today is since 00:00 UTC; the Coinbase figure is a rolling 24 hours."}

    def _stock_quote(self, row: dict[str, Any], key: str) -> dict[str, Any]:
        """A tokenized share: its Kraken price, when it last traded, and the exchange price when a key allows."""
        row.update(asset="stock", name=STOCK_NAMES.get(row["symbol"], ""), thin=row["trades_24h"] < 5)
        last_trade = None
        try:
            series = next(value for name, value in self._kraken("trades", "Trades", f"pair={key}&count=1&asset_class=tokenized_asset").items()
                          if name != "last")
            if series:
                last_trade = datetime.fromtimestamp(float(series[-1][2]), UTC).isoformat(timespec="seconds")
        except Exception:  # the age is a nicety; the price stands without it
            log_swallowed(_log, "reading a tokenized share's last trade")
        check = None
        try:
            exchange = self.exchange_quote(row["symbol"])
            if exchange:
                check = {**exchange, "difference_pct": round((row["price"] - exchange["price"]) / exchange["price"] * 100, 3)}
        except Exception:  # an invalid or rate-limited key: the tokenized price stands alone
            log_swallowed(_log, "reading the Finnhub exchange quote")
        triggered = self.check_alerts({row["symbol"]: row["price"]}) if self.path else []
        return {"markets": True, "quote_view": True, **row, "last_trade_at": last_trade, "exchange": check,
                "triggered_alerts": triggered, "fetched_at": datetime.now(UTC).isoformat(),
                "source": "Kraken tokenized stock (xStock)" + (" + Finnhub exchange quote" if check else ""),
                "basis": "A tokenized share tracks the real stock but trades on its own, around the clock; it can drift from the "
                         "exchange price, most when the US market is closed."
                         + ("" if check else " Add a free Finnhub key (Studios → Keys) for the exchange's own price.")}

    def chart(self, symbol: str, span: str = "1D", quote: str = "USD", asset: str = "auto") -> dict[str, Any]:
        if span not in RANGES:
            raise ValueError("Choose 1D, 1W, 1M, 3M or 1Y.")
        interval, count = RANGES[span]
        key, pair, extra, _kind = self._resolve(symbol, quote, asset)
        data = self._kraken("ohlc", "OHLC", f"pair={key}&interval={interval}{extra}")
        series = next(value for name, value in data.items() if name != "last")
        candles = [{"time": int(item[0]), "open": float(item[1]), "high": float(item[2]), "low": float(item[3]),
                    "close": float(item[4]), "volume": float(item[6])} for item in series[-count:]]
        if not candles:
            raise ValueError(f"Kraken returned no candles for {pair['base']}.")
        first, last = candles[0]["close"], candles[-1]["close"]
        return {"markets": True, "chart_view": True, "symbol": pair["base"], "quote": pair["quote"], "range": span,
                "interval_minutes": interval, "candles": candles, "change_pct": round((last - first) / first * 100, 2) if first else None,
                "high": max(item["high"] for item in candles), "low": min(item["low"] for item in candles),
                "source": "Kraken public API (OHLC)"}

    def book(self, symbol: str, depth: int = 20, quote: str = "USD", asset: str = "auto") -> dict[str, Any]:
        key, pair, extra, _kind = self._resolve(symbol, quote, asset)
        depth = max(5, min(int(depth), 100))
        data = next(iter(self._kraken("depth", "Depth", f"pair={key}&count={depth}{extra}").values()))
        side = lambda items: [{"price": float(item[0]), "amount": float(item[1])} for item in items]
        bids, asks = side(data.get("bids", [])), side(data.get("asks", []))
        spread = asks[0]["price"] - bids[0]["price"] if bids and asks else None
        return {"markets": True, "book_view": True, "symbol": pair["base"], "quote": pair["quote"], "bids": bids, "asks": asks,
                "spread": spread, "spread_pct": round(spread / asks[0]["price"] * 100, 4) if spread is not None else None,
                "source": "Kraken public API (order book)",
                "basis": "Resting orders on Kraken only; other exchanges have their own books."}

    def trades(self, symbol: str, count: int = 30, quote: str = "USD", asset: str = "auto") -> dict[str, Any]:
        key, pair, extra, _kind = self._resolve(symbol, quote, asset)
        count = max(5, min(int(count), 200))
        data = self._kraken("trades", "Trades", f"pair={key}&count={count}{extra}")
        series = next(value for name, value in data.items() if name != "last")
        rows = [{"price": float(item[0]), "amount": float(item[1]),
                 "at": datetime.fromtimestamp(float(item[2]), UTC).isoformat(), "side": "buy" if item[3] == "b" else "sell"}
                for item in reversed(series[-count:])]
        return {"markets": True, "trades_view": True, "symbol": pair["base"], "quote": pair["quote"], "trades": rows,
                "source": "Kraken public API (recent trades)"}

    def candles(self, symbol: str, interval: int = 60, asset: str = "auto") -> tuple[str, str, list[dict[str, float]]]:
        """(symbol, asset, candles) at `interval` minutes: up to 720 of them, oldest first."""
        key, pair, extra, kind = self._resolve(symbol, "USD", asset)
        data = self._kraken("ohlc", "OHLC", f"pair={key}&interval={interval}{extra}")
        series = next(value for name, value in data.items() if name != "last")
        return pair["base"], kind, [{"time": int(item[0]), "open": float(item[1]), "high": float(item[2]), "low": float(item[3]),
                                     "close": float(item[4]), "volume": float(item[6]), "trades": int(item[7])} for item in series]

    def chain(self) -> dict[str, Any]:
        """Bitcoin and Ethereum network state: the read-only "Web3" view."""
        result: dict[str, Any] = {"markets": True, "chain_view": True, "fetched_at": datetime.now(UTC).isoformat(), "gaps": []}
        try:
            fees = self._get("chain", "mempool:fees", MEMPOOL + "v1/fees/recommended", 64 * 1024)
            pool = self._get("chain", "mempool:pool", MEMPOOL + "mempool", 512 * 1024)
            height = self._get("chain", "mempool:height", MEMPOOL + "blocks/tip/height", 1024)
            result["bitcoin"] = {"block_height": int(height), "fee_fast_sat_vb": fees.get("fastestFee"),
                                 "fee_30min_sat_vb": fees.get("halfHourFee"), "fee_1h_sat_vb": fees.get("hourFee"),
                                 "fee_economy_sat_vb": fees.get("economyFee"), "mempool_transactions": pool.get("count"),
                                 "mempool_vbytes": pool.get("vsize"), "source": "mempool.space"}
        except Exception as exc:  # One network unreachable leaves the other's figures standing.
            log_swallowed(_log, "reading Bitcoin network state")
            result["gaps"].append(f"Bitcoin (mempool.space): {type(exc).__name__}")
        try:
            stats = self._get("chain", "blockscout:stats", BLOCKSCOUT, 256 * 1024)
            gas = stats.get("gas_prices") or {}
            result["ethereum"] = {"gas_slow_gwei": gas.get("slow"), "gas_average_gwei": gas.get("average"),
                                  "gas_fast_gwei": gas.get("fast"),
                                  "block_time_seconds": round(float(stats["average_block_time"]) / 1000, 1)
                                  if stats.get("average_block_time") else None,
                                  "transactions_today": stats.get("transactions_today"),
                                  "network_utilization_pct": stats.get("network_utilization_percentage"),
                                  "total_blocks": stats.get("total_blocks"), "eth_price_usd": stats.get("coin_price"),
                                  "source": "Blockscout (eth.blockscout.com)"}
        except Exception as exc:  # One network unreachable leaves the other's figures standing.
            log_swallowed(_log, "reading Ethereum network state")
            result["gaps"].append(f"Ethereum (Blockscout): {type(exc).__name__}")
        if "bitcoin" not in result and "ethereum" not in result:
            raise ValueError("Neither blockchain source answered: " + "; ".join(result["gaps"]))
        return result

    # ------------------------------------------------------------------ local lists
    def watchlist(self, action: str = "list", symbol: str = "") -> dict[str, Any]:
        if action not in ("list", "add", "remove"):
            raise ValueError("Use list, add or remove.")
        with self._db() as db:
            if action != "list":
                code = self.symbol(symbol)
                if not re.fullmatch(r"[A-Z0-9]{1,12}", code):
                    raise ValueError("Give a coin ticker such as BTC, or a name such as bitcoin.")
                if action == "add":
                    self._resolve(code)  # only coins and tokenized shares that have a market
                    db.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?)", (code, datetime.now(UTC).isoformat()))
                else:
                    db.execute("DELETE FROM watchlist WHERE symbol=?", (code,))
            symbols = [row["symbol"] for row in db.execute("SELECT symbol FROM watchlist ORDER BY added_at")]
        return {"markets": True, "watchlist_view": True, "action": action, "symbol": self.symbol(symbol) if symbol else "",
                "symbols": symbols}

    def alerts(self, action: str = "list", symbol: str = "", direction: str = "", price: float | None = None,
               alert_id: int | None = None) -> dict[str, Any]:
        """Local price alerts, checked every two minutes while HAYAT runs and whenever it reads prices; nothing is sent anywhere."""
        if action not in ("list", "add", "delete", "check"):
            raise ValueError("Use list, add, delete or check.")
        triggered: list[dict[str, Any]] = []
        created = None
        if action == "add":
            code = self.symbol(symbol)
            target = float(price) if price is not None else 0.0
            if target <= 0:
                raise ValueError("Give the price to watch for, above zero.")
            current = self.quote(code)["price"]
            if direction not in ("above", "below"):
                direction = "above" if target > current else "below"
            with self._db() as db:
                cursor = db.execute("INSERT INTO alerts (symbol, direction, price, created_at) VALUES (?, ?, ?, ?)",
                                    (code, direction, target, datetime.now(UTC).isoformat()))
                created = {"id": cursor.lastrowid, "symbol": code, "direction": direction, "price": target, "current": current}
        elif action == "delete":
            with self._db() as db:
                if not db.execute("DELETE FROM alerts WHERE id=?", (int(alert_id or 0),)).rowcount:
                    raise ValueError(f"No price alert number {alert_id}.")
        elif action == "check":
            symbols = sorted({row["symbol"] for row in self._alert_rows() if not row["triggered_at"]})
            prices = {}
            for code in symbols:
                try:
                    # Reading the price already marks a crossed alert, so collect it from the answer.
                    answer = self.quote(code)
                    triggered += answer.get("triggered_alerts") or []
                    prices[code] = answer["price"]
                except Exception:  # One unpriced coin does not stop the other checks.
                    log_swallowed(_log, "checking a price alert")
            triggered += self.check_alerts(prices)
        return {"markets": True, "alerts_view": True, "action": action, "created": created, "triggered": triggered,
                "alerts": self._alert_rows(),
                "basis": "Alerts are local: HAYAT checks them every two minutes while it runs, and whenever it reads prices "
                         "(chat, the Trade Center window, or \"check my price alerts\"). Nothing is sent to anyone and nothing is traded."}

    def _alert_rows(self) -> list[dict[str, Any]]:
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM alerts ORDER BY id")]

    def check_alerts(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        """Mark and return the untriggered alerts these prices have crossed."""
        if not self.path or not prices:
            return []
        hits = []
        with self._db() as db:
            for row in db.execute("SELECT * FROM alerts WHERE triggered_at IS NULL"):
                price = prices.get(row["symbol"])
                if price is None:
                    continue
                if row["direction"] == "above" and price >= row["price"] or row["direction"] == "below" and price <= row["price"]:
                    hits.append({**dict(row), "triggered_price": price})
                    db.execute("UPDATE alerts SET triggered_at=?, triggered_price=? WHERE id=?",
                               (datetime.now(UTC).isoformat(), price, row["id"]))
        return hits


# ---------------------------------------------------------------------- tools
TOOL_NAMES = ("market_overview", "market_quote", "market_chart", "market_book", "market_trades", "market_chain",
              "market_watchlist", "market_alerts", "market_stocks", "market_outlook", "market_scenarios", "market_periods", "market_periods_compare", "market_live", "market_events", "market_trade_routes", "market_calendar", "market_news",
              "market_event_impact", "market_signals", "market_influences")


def register_market_tools(registry: Any, markets: MarketData) -> None:
    def obj(properties, required=()):
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    symbol = {"type": "string", "minLength": 1, "maxLength": 40}
    quote = {"type": "string", "enum": list(QUOTES)}
    read = "Read-only public market data; HAYAT cannot trade, hold funds or connect a wallet."
    registry.register("market_overview", "Every crypto market on Kraken in one quote currency (USD by default): price, change "
                      "since 00:00 UTC, 24-hour high, low and volume; search by name or ticker; sort by volume, change, losers, "
                      "price or name. " + read,
                      obj({"quote": quote, "query": {"type": "string", "maxLength": 60},
                           "sort": {"enum": ["volume", "change", "losers", "price", "name"]},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                           "offset": {"type": "integer", "minimum": 0, "maximum": 5000}}),
                      "low", "public_api_get", markets.overview)
    asset = {"enum": ["auto", "crypto", "stock"]}
    registry.register("market_quote", "Live price of one coin or stock (name or ticker): crypto on Kraken with a Coinbase "
                      "cross-check; a stock as Kraken's tokenized share, plus the exchange price when a Finnhub key is set. " + read,
                      obj({"symbol": symbol, "quote": quote, "asset": asset}, ["symbol"]), "low", "public_api_get", markets.quote)
    registry.register("market_stocks", "Every tokenized stock and fund on Kraken (Apple, Nvidia, S&P 500, sector funds…): price, "
                      "change since 00:00 UTC, and how actively each trades. " + read,
                      obj({"query": {"type": "string", "maxLength": 60}, "sort": {"enum": ["volume", "change", "losers", "name"]},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}), "low", "public_api_get", markets.stocks)

    def outlook(symbol: str, asset: str = "auto") -> dict[str, Any]:
        from app.market_outlook import outlook as compute
        return compute(markets, symbol, asset)
    registry.register("market_outlook", "Statistical outlook for a coin or stock: expected ranges for the next hour, day and week "
                      "from recent volatility, trend and RSI, and HAYAT's own measured track record for this market. Not a "
                      "prediction of direction and not advice. " + read,
                      obj({"symbol": symbol, "asset": asset}, ["symbol"]), "low", "public_api_get", outlook)

    from app.market_periods import LABELS as PERIOD_LABELS

    def periods(symbol: str, asset: str = "auto", period: str = "all") -> dict[str, Any]:
        from app.market_periods import periods as compute
        return compute(markets, symbol, asset, period)
    registry.register("market_periods", "Possibilities for a coin or stock over every time period (1 hour, 4 hours, 1 day, 1 week, "
                      "1 month, 3 months, 6 months, 1 year) from its own past: how often it ended higher (95 % intervals counted on "
                      "separate periods), likely and wide price ranges from the live price, how often it fell or rose by each step "
                      "and touched levels on the way, the odds after a move like the last one, each range's walk-forward track "
                      "record, and scheduled events inside the period. Optional period to dwell on. Shares of past periods, never a "
                      "forecast or advice. " + read,
                      obj({"symbol": symbol, "asset": asset, "period": {"enum": [*PERIOD_LABELS, "all"]}}, ["symbol"]),
                      "low", "public_api_get", periods)

    def periods_compare(symbols: list[str] | None = None, period: str = "1m") -> dict[str, Any]:
        from app.market_periods import compare
        return compare(markets, symbols, period)
    registry.register("market_periods_compare", "One time period across several coins and stocks (given, else the watchlist, else "
                      "Bitcoin, Ethereum, Solana, S&P 500, Nasdaq, gold, Nvidia, Apple): how often each was higher after it, the "
                      "middle move, the 9-in-10 range and how well those ranges held. Ranked, with the warning that overlapping "
                      "intervals are noise. " + read,
                      obj({"symbols": {"type": "array", "items": symbol, "maxItems": 12}, "period": {"enum": list(PERIOD_LABELS)}}),
                      "low", "public_api_get", periods_compare)

    def signals(symbol: str, asset: str = "auto") -> dict[str, Any]:
        from app.market_signals import signals as compute
        return compute(markets, symbol, asset)
    registry.register("market_signals", "Signal lab for a coin or stock: ten common signals (momentum, moving averages, snap-back after "
                      "big days, RSI extremes, streaks, heavy volume, day of the week, following Bitcoin or the S&P 500) tested "
                      "walk-forward on up to two years of its own daily prices for the next day and week, scored against its usual "
                      "up-rate, corrected for luck across all tries, the combined odds from proven signals only, and an out-of-sample "
                      "check on the later third. Usually finds that nothing beats chance, and says so. " + read,
                      obj({"symbol": symbol, "asset": asset}, ["symbol"]), "low", "public_api_get", signals)

    def live(symbols: list[str], asset: str = "auto") -> dict[str, Any]:
        stream = getattr(markets, "live", None)
        if stream is None:
            raise ValueError("Live streaming is not available in this session.")
        from app.markets_live import ws_symbol
        wanted = [ws_symbol(item, "stock" if asset == "stock" or (asset == "auto" and item.upper() in STOCK_NAMES) else "crypto")
                  for item in symbols]
        stream.watch(wanted)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and len(stream.snapshot(wanted)["prices"]) < len(wanted):
            time.sleep(.2)
        return stream.snapshot(wanted)
    registry.register("market_live", "Prices streamed live from Kraken as trades happen (crypto and tokenized stocks), with the "
                      "measured delay from each trade to its arrival here. " + read,
                      obj({"symbols": {"type": "array", "items": symbol, "minItems": 1, "maxItems": 20}, "asset": asset}, ["symbols"]),
                      "low", "public_api_get", live)
    registry.register("market_chart", "Price candles for one coin over 1D, 1W, 1M, 3M or 1Y, with the change over the range. " + read,
                      obj({"symbol": symbol, "span": {"enum": list(RANGES)}, "quote": quote}, ["symbol"]),
                      "low", "public_api_get", markets.chart)
    registry.register("market_book", "Order book (bids and asks) for one coin on Kraken, with the spread. " + read,
                      obj({"symbol": symbol, "depth": {"type": "integer", "minimum": 5, "maximum": 100}, "quote": quote}, ["symbol"]),
                      "low", "public_api_get", markets.book)
    registry.register("market_trades", "Recent trades for one coin on Kraken. " + read,
                      obj({"symbol": symbol, "count": {"type": "integer", "minimum": 5, "maximum": 200}, "quote": quote}, ["symbol"]),
                      "low", "public_api_get", markets.trades)
    registry.register("market_chain", "Blockchain (Web3) network state, read-only: Bitcoin fees, mempool and block height "
                      "(mempool.space); Ethereum gas, block time and activity (Blockscout). No wallet or keys.",
                      obj({}), "low", "public_api_get", markets.chain)
    registry.register("market_watchlist", "List, add or remove coins on the owner's local crypto watchlist.",
                      obj({"action": {"enum": ["list", "add", "remove"]}, "symbol": {"type": "string", "maxLength": 40}}),
                      "low", "filesystem_write_project", markets.watchlist)
    registry.register("market_alerts", "Local crypto price alerts: list, add (symbol, price, above/below), delete by id, or check "
                      "now. Checked whenever HAYAT reads prices; nothing is sent and nothing is traded.",
                      obj({"action": {"enum": ["list", "add", "delete", "check"]}, "symbol": {"type": "string", "maxLength": 40},
                           "direction": {"enum": ["above", "below", ""]}, "price": {"type": "number", "exclusiveMinimum": 0},
                           "alert_id": {"type": "integer", "minimum": 1}}),
                      "low", "filesystem_write_project", markets.alerts)


def register_market_event_tools(registry: Any, manager: Any, markets: MarketData) -> None:
    """World events lined up with the industries they usually touch (app/market_events.py)."""
    from app.market_events import event_impact, events_and_markets
    obj = lambda properties, required=(): {"type": "object", "properties": properties, "required": list(required),
                                           "additionalProperties": False}
    registry.register("market_events", "Today's significant world events grouped by the industries they usually bear on (energy, "
                      "chips, defence, gold, copper, cybersecurity, crypto…), with each industry's funds and today's moves. "
                      "Rules with reasons; not a claim that events caused the moves.",
                      obj({"hours": {"type": "number", "minimum": 1, "maximum": 168}, "limit": {"type": "integer", "minimum": 1, "maximum": 30}}),
                      "low", "public_api_get", lambda hours=48, limit=12: events_and_markets(manager, markets, hours, limit))
    def scenarios(symbol: str, asset: str = "auto") -> dict[str, Any]:
        from app.market_scenarios import scenarios as compute
        try:
            events = events_and_markets(manager, markets) if manager is not None else None
        except Exception:  # scenarios stand without the world events
            log_swallowed(_log, "reading world events for scenarios")
            events = None
        return compute(markets, symbol, asset, events)
    registry.register("market_scenarios", "Scenarios for a coin or stock from live data: how often it was higher a day and a week "
                      "later in past situations like now (trend, volatility, RSI), with sample sizes and 95 % intervals next to its "
                      "usual odds; weak, middle and strong prices for the coming week; and the factors that might matter (momentum, "
                      "volatility, world events in its industries, how it moves with the S&P 500 or Bitcoin). Shares of past cases, "
                      "never a forecast or advice.",
                      obj({"symbol": {"type": "string", "minLength": 1, "maxLength": 40}, "asset": {"enum": ["auto", "crypto", "stock"]}},
                          ["symbol"]), "low", "public_api_get", scenarios)
    def influences(symbol: str, asset: str = "auto") -> dict[str, Any]:
        from app.market_influences import influences as compute
        try:
            events = events_and_markets(manager, markets) if manager is not None else None
        except Exception:  # the links stand without the world events
            log_swallowed(_log, "reading world events for market influences")
            events = None
        return compute(markets, symbol, asset, events)
    registry.register("market_influences", "What moves a coin or stock: how its daily moves line up with Bitcoin, Ethereum, the S&P 500, "
                      "the Nasdaq-100, gold, chip stocks, energy stocks and the euro (correlation over 90 days and a year, move per 1 %, "
                      "whether the link held, its move on their worst days, whether any leads it a day ahead), how much of its swings "
                      "they explain together, today's move split into 'with the markets' and 'its own', plus world events, scheduled "
                      "events and rumours touching it. Co-movement, not cause; never advice.",
                      obj({"symbol": {"type": "string", "minLength": 1, "maxLength": 40}, "asset": {"enum": ["auto", "crypto", "stock"]}},
                          ["symbol"]), "low", "public_api_get", influences)

    def trade_routes(radius_km: float = 350, hours: float = 72) -> dict[str, Any]:
        from app.trade_routes import chokepoints
        return chokepoints(manager, markets, radius_km, hours)
    registry.register("market_trade_routes", "World trade chokepoints (Hormuz, Malacca, Suez, Bab-el-Mandeb, Panama, Taiwan Strait, "
                      "Bosporus and others): what passes each, the events reported nearby in the last days, and the industries and "
                      "funds each links to with today's moves. Event reports, not ship traffic.",
                      obj({"radius_km": {"type": "number", "minimum": 50, "maximum": 1000}, "hours": {"type": "number", "minimum": 6,
                                                                                                        "maximum": 336}}),
                      "low", "public_api_get", trade_routes)
    from app.market_calendar import MarketCalendar
    from app.market_news import MarketNews
    markets.calendar = getattr(markets, "calendar", None) or MarketCalendar(markets.fetcher, markets)
    markets.news = getattr(markets, "news", None) or MarketNews(markets.fetcher, markets)
    registry.register("market_calendar", "Scheduled events that move markets: Fed interest-rate decisions (from the Fed's own calendar), "
                      "the US jobs report (usual first-Friday pattern) and company earnings (with a Finnhub key), with countdowns and "
                      "how much the S&P 500, Nasdaq, gold and Bitcoin moved on past event days against usual days.",
                      obj({"days_ahead": {"type": "integer", "minimum": 1, "maximum": 120}}), "low", "public_api_get",
                      lambda days_ahead=45: markets.calendar.upcoming(days_ahead))
    registry.register("market_news", "Business and crypto headlines that name markets (BBC, Guardian, MarketWatch, NPR, CoinDesk, "
                      "Cointelegraph, Decrypt), rumours marked unconfirmed, with how the named markets moved since, next to the "
                      "S&P 500. Optionally one market, or rumours only.",
                      obj({"symbol": {"type": "string", "maxLength": 12}, "hours": {"type": "number", "minimum": 1, "maximum": 168},
                           "rumours_only": {"type": "boolean"}}), "low", "public_api_get",
                      lambda symbol="", hours=48, rumours_only=False: markets.news.news(symbol, hours, rumours_only))
    registry.register("market_event_impact", "For one intelligence event: the industries it usually touches, why, and how their "
                      "funds and shares moved since it was reported, next to the whole market (S&P 500).",
                      obj({"event_id": {"type": "string", "maxLength": 220}}, ["event_id"]),
                      "low", "public_api_get", lambda event_id: event_impact(manager, markets, event_id))


# ---------------------------------------------------------------------- chat routing
_COINS = "|".join(sorted(map(re.escape, COIN_ALIASES), key=len, reverse=True))
_COIN = rf"(?P<coin>{_COINS}|[A-Z0-9]{{2,10}}(?= price| chart| order book| trades|\b(?=.*\b(?:coin|token|crypto)\b)))"
_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_END = r"\s*[?.!]*$"
_WHEN = r"(?:\s+(?:now|today|right now|currently|at the moment|live))?"
_IN = r"(?:\s+(?:in|to)\s+(?P<quote>usd|dollars?|eur|euros?|usdt|gbp|pounds?))?"


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_QUOTE = (
    # "What is bitcoin" is a question about bitcoin, not its price: a price word or "how much" is needed.
    _compile(_LEAD + r"how\s+much\s+(?:is|are|does)\s+(?:one\s+|1\s+|a\s+)?" + _COIN + r"(?:\s+(?:coin|token))?"
             r"(?:\s+(?:worth|cost|trading\s+at))?" + _IN + _WHEN + _END),
    _compile(_LEAD + r"(?:what(?:'s|\s+is)|whats|tell\s+me|give\s+me|show(?:\s+me)?|check|get)\s+(?:the\s+)?"
             r"(?:current\s+|live\s+|latest\s+)?(?:(?:price|value|rate)\s+of\s+(?:one\s+|1\s+|a\s+)?" + _COIN
             + r"|" + _COIN.replace("(?P<coin>", "(?P<coin2>") + r"(?:\s+(?:coin|token))?\s+(?:price|value|rate))"
             + _IN + _WHEN + _END),
    _compile(_LEAD + r"(?:the\s+)?(?:current\s+|live\s+|latest\s+)?" + _COIN + r"(?:\s+(?:coin|token))?\s+(?:price|value|rate)" + _IN + _WHEN + _END),
    _compile(_LEAD + r"(?:price\s+of|value\s+of)\s+" + _COIN + _IN + _WHEN + _END),
    _compile(_LEAD + r"(?:convert\s+)?(?:1\s+)?" + _COIN + r"\s+(?:to|in)\s+(?P<quote2>usd|dollars?|eur|euros?|usdt|gbp|pounds?)" + _WHEN + _END),
)
_CHART = _compile(_LEAD + r"(?:show(?:\s+me)?|open|draw|plot|give\s+me)?\s*(?:the\s+|a\s+)?" + _COIN
                  + r"(?:\s+(?:price|coin))?\s+(?:chart|graph|candles|price\s+history)(?:\s+(?:for|over)\s+(?:the\s+)?(?:last\s+|past\s+)?"
                  r"(?P<span>day|24 hours|week|7 days|month|30 days|3 months|90 days|year|12 months))?" + _END)
_BOOK = _compile(_LEAD + r"(?:show(?:\s+me)?\s+)?(?:the\s+)?" + _COIN + r"\s+(?:order\s*book|depth|bids\s+and\s+asks)" + _END)
_TRADES = _compile(_LEAD + r"(?:show(?:\s+me)?\s+)?(?:the\s+)?(?:recent|latest|last)\s+" + _COIN + r"\s+trades" + _END)
_OVERVIEW = _compile(_LEAD + r"(?:(?:show|list|open|give)(?:\s+me)?\s+|how\s+is\s+|how(?:'s|\s+is)\s+|what(?:'s|\s+is)\s+)?(?:the\s+|all\s+(?:the\s+)?)?"
                     r"(?:(?P<order>top|biggest|best|top\s+\d+)\s+)?(?:crypto(?:currency|currencies)?|coins?|cryptocurrencies)"
                     r"(?:\s+(?P<what>market|prices|markets|gainers|losers|movers|today))?(?:\s+(?:today|now|right\s+now))?" + _END)
_MOVERS = _compile(_LEAD + r"(?:show(?:\s+me)?\s+|what\s+are\s+)?(?:the\s+)?(?:(?:biggest|top)\s+)?(?:crypto\s+)?"
                   r"(?P<kind>gainers|losers|movers)(?:\s+in\s+crypto)?(?:\s+(?:today|now))?" + _END)
_CHAIN = _compile(_LEAD + r"(?:what(?:'s|\s+is|\s+are)\s+(?:the\s+)?|show(?:\s+me)?\s+(?:the\s+)?|check\s+(?:the\s+)?|how\s+(?:high|much)\s+(?:is|are)\s+(?:the\s+)?)?"
                  r"(?:current\s+)?(?:(?:bitcoin|btc|ethereum|eth)\s+)?(?:network\s+|transaction\s+|tx\s+)?(?:gas(?:\s+fees?|\s+price)?|fees?"
                  r"|mempool|block\s+height|web\s*3(?:\.0)?(?:\s+stats|\s+data)?|blockchain\s+(?:stats|status|data)|on[- ]chain\s+data)"
                  r"(?:\s+(?:today|now|right\s+now))?" + _END)
_WATCH = _compile(_LEAD + r"(?P<verb>add|put|remove|delete|take)\s+" + _COIN + r"\s+(?:to|on|from|off)\s+(?:my\s+)?(?:crypto\s+)?watch\s*list" + _END)
_WATCH_LIST = _compile(_LEAD + r"(?:show|list|open|what(?:'s|\s+is)\s+on)(?:\s+me)?\s+(?:my\s+)?(?:crypto\s+)?watch\s*list" + _END)
_ALERT = _compile(_LEAD + r"(?:alert|notify|tell|warn|ping)\s+me\s+(?:when|if)\s+" + _COIN + r"\s+(?:goes|gets|rises|climbs|falls|drops|is|hits|reaches|crosses)"
                  r"(?:\s+(?P<direction>above|over|below|under))?\s+(?:to\s+)?\$?(?P<price>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>k)?" + _END)
_ALERT_LIST = _compile(_LEAD + r"(?:show|list|check)(?:\s+me)?\s+(?:my\s+)?(?:crypto\s+)?price\s+alerts" + _END)
_ALERT_DELETE = _compile(_LEAD + r"(?:delete|remove|cancel)\s+(?:price\s+)?alert\s+(?:number\s+|#)?(?P<id>\d+)" + _END)
_SPANS = {"day": "1D", "24 hours": "1D", "week": "1W", "7 days": "1W", "month": "1M", "30 days": "1M", "3 months": "3M",
          "90 days": "3M", "year": "1Y", "12 months": "1Y"}
_QUOTE_WORDS = {"usd": "USD", "dollar": "USD", "dollars": "USD", "eur": "EUR", "euro": "EUR", "euros": "EUR", "usdt": "USDT",
                "gbp": "GBP", "pound": "GBP", "pounds": "GBP"}


def _symbol(match: re.Match[str]) -> str:
    coin = match.groupdict().get("coin") or match.groupdict().get("coin2")
    return COIN_ALIASES.get(coin.lower(), KRAKEN_NAMES.get(coin.upper(), coin.upper()))


_ASSET = (r"(?P<coin>" + _COINS + "|" + "|".join(sorted(map(re.escape, STOCK_ALIASES), key=len, reverse=True))
          + r"|[A-Z][A-Z.]{0,5})")
_TRADE = _compile(_LEAD + r"(?:how\s+is\s+|what(?:'s|\s+is)\s+happening\s+(?:in|with|to)\s+|show(?:\s+me)?\s+|check\s+)?(?:the\s+)?"
                  r"(?:world\s+trade|global\s+trade|world\s+shipping|global\s+shipping|shipping\s+lanes|trade\s+routes|"
                  r"(?:shipping\s+|trade\s+)?chokepoints)(?:\s+(?:status|now|today|disruptions?))?" + _END)
_CALENDAR = _compile(_LEAD + r"(?:(?:when\s+is|when's)\s+the\s+next\s+(?:fed(?:eral\s+reserve)?\s+(?:meeting|decision|rate\s+decision)|fomc(?:\s+meeting)?|"
                     r"jobs\s+report|rate\s+decision)|(?:show(?:\s+me)?\s+(?:the\s+)?|what(?:'s|\s+is)\s+(?:on\s+)?(?:the\s+)?)?"
                     r"(?:economic|market|earnings|fed)\s+calendar|(?:upcoming|scheduled)\s+(?:market\s+|economic\s+)?events|"
                     r"what\s+(?:events|meetings)\s+(?:could|will|might)\s+move\s+(?:the\s+)?markets?(?:\s+(?:this|next)\s+(?:week|month))?|"
                     r"what(?:'s|\s+is)\s+coming\s+up\s+for\s+(?:the\s+)?markets?)" + _END)
_NEWS = _compile(_LEAD + r"(?:(?:show(?:\s+me)?\s+|what(?:'s|\s+is|\s+are)\s+(?:the\s+)?|any\s+|latest\s+)?(?:the\s+)?(?:latest\s+)?"
                 r"(?P<kind>market|crypto|business|stock|financial)\s+(?P<what>news|rumou?rs|headlines)(?:\s+(?:today|now))?|"
                 r"(?:any\s+|what\s+are\s+the\s+)?(?P<what2>news|rumou?rs|headlines)\s+(?:about|on|for)\s+" + _ASSET +
                 r"(?:\s+(?:stock|shares?|coin))?(?:\s+(?:today|now))?)" + _END)
_SCENARIO = (
    _compile(_LEAD + r"(?:what(?:'s|\s+is|\s+are)\s+the\s+|show(?:\s+me)?\s+(?:the\s+)?)?(?:chances?|odds|probability|likelihood)\s+"
             r"(?:that\s+|of\s+)?" + _ASSET + r"(?:\s+(?:stock|shares?|coin|price))?\s+(?:goes?\s+(?:up|down)|going\s+(?:up|down)|rises?|"
             r"rising|falls?|falling|drops?|is\s+higher|is\s+lower|ends?\s+(?:higher|lower)|being\s+(?:higher|lower))"
             r"(?:\s+(?:tomorrow|this\s+week|next\s+week|today|soon))?" + _END),
    _compile(_LEAD + r"(?:show(?:\s+me)?\s+|give\s+me\s+|what\s+are\s+)?(?:the\s+)?(?:scenarios?|probabilit(?:y|ies)|analysis|analytics)\s+"
             r"(?:for|of|on)\s+" + _ASSET + r"(?:\s+(?:stock|shares?|coin|price))?" + _END),
    _compile(_LEAD + r"(?:show(?:\s+me)?\s+)?" + _ASSET + r"(?:\s+(?:stock|shares?|coin|price))?\s+(?:scenarios?|probabilit(?:y|ies)|analysis|"
             r"analytics|odds)" + _END),
    _compile(_LEAD + r"(?:analy[sz]e|analyse)\s+(?:the\s+)?" + _ASSET + r"(?:\s+(?:stock|shares?|coin|price|market))?" + _END),
    _compile(_LEAD + r"why\s+(?:is|are)\s+" + _ASSET + r"(?:\s+(?:stock|shares?|coin|price))?\s+(?:up|down|moving|falling|rising|"
             r"dropping|climbing|crashing|pumping)(?:\s+(?:today|now|this\s+week))?" + _END),
    _compile(_LEAD + r"what\s+(?:might|could|will)\s+move\s+" + _ASSET + r"(?:\s+(?:stock|shares?|coin|price))?" + _END),
)
_STOCKWORD = r"(?:\s+(?:stock|shares?|coin|price))?"
#: Which signals have told something about a market: tested, not trusted.
_SIGNALS = (
    _compile(_LEAD + r"(?:which|what)\s+(?:signals?|indicators?)\s+(?:work|works|matter|hold\s+up|have\s+worked)\s+(?:for|on|with)\s+"
             + _ASSET + _STOCKWORD + _END),
    _compile(_LEAD + r"(?:test|backtest|check)\s+(?:the\s+)?(?:signals?|indicators?|predictors?)\s+(?:for|on|of)\s+" + _ASSET + _STOCKWORD + _END),
    _compile(_LEAD + r"backtest\s+(?:the\s+)?" + _ASSET + _STOCKWORD + _END),
    _compile(_LEAD + r"(?:show(?:\s+me)?\s+)?(?:the\s+)?signal\s+lab\s+(?:for|of|on)\s+" + _ASSET + _STOCKWORD + _END),
    _compile(_LEAD + r"(?:show(?:\s+me)?\s+)?(?:the\s+)?" + _ASSET + _STOCKWORD + r"\s+(?:signals?|signal\s+lab|indicators?|predictors?)" + _END),
    _compile(_LEAD + r"(?:is|can)\s+" + _ASSET + _STOCKWORD + r"\s+(?:be\s+)?predict(?:able|ed)" + _END),
    _compile(_LEAD + r"how\s+predictable\s+is\s+" + _ASSET + _STOCKWORD + _END),
    _compile(_LEAD + r"what\s+(?:predicts|has\s+predicted|can\s+predict)\s+" + _ASSET + _STOCKWORD + _END),
    _compile(_LEAD + r"(?:do|does)\s+(?:technical\s+)?(?:indicators?|signals?|momentum|rsi|moving\s+averages?)\s+work\s+(?:for|on)\s+"
             + _ASSET + _STOCKWORD + _END),
)
#: What moves a market: co-movement with the wider markets, today's split, events.
_INFLUENCES = (
    _compile(_LEAD + r"what\s+(?:drives|moves|affects|influences|impacts|pushes|is\s+driving|is\s+moving|is\s+affecting)\s+(?:the\s+)?"
             + _ASSET + _STOCKWORD + r"(?:\s+prices?)?" + _END),
    _compile(_LEAD + r"what\s+(?:is|are)\s+" + _ASSET + _STOCKWORD + r"\s+(?:linked|correlated|tied|connected)\s+(?:to|with)" + _END),
    _compile(_LEAD + r"(?:show(?:\s+me)?\s+)?(?:the\s+)?" + _ASSET + _STOCKWORD + r"\s+(?:drivers|correlations?|links)" + _END),
    _compile(_LEAD + r"(?:show(?:\s+me)?\s+)?(?:the\s+)?(?:drivers|correlations?|links|influences)\s+(?:of|for|on)\s+" + _ASSET + _STOCKWORD + _END),
    _compile(_LEAD + r"(?:what|which)\s+(?:markets?\s+)?(?:does|do)\s+" + _ASSET + _STOCKWORD + r"\s+(?:move|follow|track)\s+with" + _END),
    _compile(_LEAD + r"(?:what|which)\s+(?:markets?|things|factors)\s+(?:affect|move|drive|influence)\s+" + _ASSET + _STOCKWORD + _END),
)
_SPAN = (r"(?P<span>(?:(?:in|over|within|for|by|during|after)\s+)?(?:the\s+)?(?:next|coming|this|following)?\s*"
         r"(?:(?:\d{1,2}|one|two|three|four|five|six|nine|twelve|a|an|half\s+a)\s+)?(?:hours?|days?|weeks?|months?|quarters?|years?)"
         r"(?:\s+from\s+now)?|(?:in\s+the\s+|over\s+the\s+)?(?:long|short)[\s-]term|tomorrow)")
_PERIODS = (
    _compile(_LEAD + r"(?:what(?:'s|\s+is|\s+are)\s+the\s+|show(?:\s+me)?\s+(?:the\s+)?)?(?:chances?|odds|probability|likelihood|possibilit(?:y|ies))\s+"
             r"(?:that\s+|of\s+)?" + _ASSET + _STOCKWORD + r"\s+(?:goes?\s+(?:up|down)|going\s+(?:up|down)|rises?|rising|falls?|falling|"
             r"drops?|is\s+higher|is\s+lower|ends?\s+(?:higher|lower|up|down)|being\s+(?:higher|lower)|will\s+(?:rise|fall|go\s+up|go\s+down))\s+"
             + _SPAN + _END),
    _compile(_LEAD + r"(?:will|is|does|can)\s+" + _ASSET + _STOCKWORD + r"\s+(?:go(?:ing)?\s+(?:up|down)|rise|fall|drop|crash|pump|"
             r"recover|be\s+(?:higher|lower|up|down))\s+" + _SPAN + _END),
    _compile(_LEAD + r"(?:where\s+(?:is|will)\s+" + _ASSET + _STOCKWORD + r"\s+(?:go|head|be)(?:ing)?|(?:what|how\s+much)\s+will\s+"
             + _ASSET.replace("(?P<coin>", "(?P<coin2>") + _STOCKWORD + r"\s+(?:be(?:\s+worth)?|cost|trade\s+at))\s+" + _SPAN + _END),
    _compile(_LEAD + r"(?:what(?:'s|\s+is)\s+the\s+|give\s+me\s+(?:the\s+|a\s+)?|show(?:\s+me)?\s+(?:the\s+)?)?" + _ASSET + _STOCKWORD
             + r"\s+(?:outlook|forecast|prediction|price\s+prediction|odds|chances)\s+(?:for\s+)?" + _SPAN + _END),
    _compile(_LEAD + r"(?:predict|forecast)\s+(?:the\s+)?" + _ASSET + _STOCKWORD + r"\s+(?:for\s+)?" + _SPAN + _END),
    _compile(_LEAD + r"(?:what(?:'s|\s+is)\s+the\s+|give\s+me\s+(?:the\s+|a\s+)?|show(?:\s+me)?\s+(?:the\s+)?)?" + _ASSET + _STOCKWORD
             + r"\s+(?P<span>(?:long|short)[\s-]term)\s+(?:outlook|forecast|prediction|price\s+prediction|odds|chances|view)" + _END),
)
#: Asking for every period at once: "bitcoin possibilities", "apple stock by time period".
_PERIODS_ALL = (
    _compile(_LEAD + r"(?:show(?:\s+me)?\s+|give\s+me\s+|what\s+are\s+)?(?:the\s+|all\s+(?:the\s+)?)?(?:possibilities|odds\s+by\s+(?:time\s+)?period|"
             r"chances\s+by\s+(?:time\s+)?period)\s+(?:for|of|on)\s+" + _ASSET + _STOCKWORD + r"(?:\s+(?:for|over)\s+" + _SPAN + r")?" + _END),
    _compile(_LEAD + r"(?:show(?:\s+me)?\s+)?" + _ASSET + _STOCKWORD + r"\s+(?:possibilities|(?:odds|chances|outlook|forecast)\s+(?:by|for|over)\s+"
             r"(?:every|each|all|different)?\s*(?:the\s+)?(?:time\s+)?periods?|(?:by|across|over)\s+(?:every\s+|each\s+|all\s+)?(?:time\s+)?"
             r"(?:periods?|frames?|horizons?|timeframes?))(?:\s+(?:for|over)\s+" + _SPAN + r")?" + _END),
)
_PERIODS_COMPARE = (
    _compile(_LEAD + r"compare\s+(?:the\s+)?(?:(?:odds|chances|possibilities|outlook)\s+(?:for|of|across)\s+)?(?P<list>my\s+watch\s*list|"
             r"[\w.&]+(?:(?:\s*,\s*|\s+and\s+|\s+vs\.?\s+|\s+versus\s+|\s+)[\w.&]+){1,11}?)(?:\s+(?:stocks?|shares?|coins?))?(?:\s+(?:odds|chances))?"
             r"(?:\s+" + _SPAN + r")?" + _END),
    _compile(_LEAD + r"which\s+(?:market|coin|crypto|stock|one|asset)s?\s+(?:has|have|had)\s+the\s+(?:best|highest|better)\s+(?:odds|chances)"
             r"(?:\s+(?:of\s+)?(?:going\s+up|rising|ending\s+higher))?(?:\s+" + _SPAN + r")?(?:\s+(?:in|on)\s+my\s+watch\s*list)?" + _END),
)
_OUTLOOK = (
    _compile(_LEAD + r"(?:what(?:'s|\s+is)\s+the\s+|give\s+me\s+(?:the\s+|a\s+)?|show(?:\s+me)?\s+(?:the\s+)?)?" + _ASSET
             + r"(?:\s+(?:stock|shares?|coin|price))?\s+(?:outlook|forecast|prediction|price\s+prediction|expected\s+range)" + _END),
    _compile(_LEAD + r"(?:predict|forecast)\s+(?:the\s+)?" + _ASSET + r"(?:\s+(?:stock|shares?|coin|price))?" + _END),
    _compile(_LEAD + r"(?:will|is|does)\s+" + _ASSET + r"(?:\s+(?:stock|shares?|coin|price))?\s+(?:go(?:ing)?\s+(?:up|down)|rise|fall|"
             r"drop|crash|pump|recover)(?:\s+(?:tomorrow|this\s+week|today|soon))?" + _END),
    _compile(_LEAD + r"where\s+(?:is|will)\s+" + _ASSET + r"(?:\s+(?:stock|shares?|coin|price))?\s+(?:go|head|be)(?:ing)?"
             r"(?:\s+(?:next|tomorrow|this\s+week))?" + _END),
)
_STOCK_QUOTE = _compile(_LEAD + r"(?:what(?:'s|\s+is)\s+|how\s+(?:is|are)\s+|show(?:\s+me)?\s+|check\s+)?(?:the\s+)?(?:price\s+of\s+)?"
                        + _ASSET + r"\s+(?:stock|shares?)(?:\s+(?:price|doing|now|today|trading))*" + _END)
_STOCKS = _compile(_LEAD + r"(?:how\s+(?:is|are)\s+|show(?:\s+me)?\s+|what\s+are\s+)?(?:the\s+)?(?:stocks?|stock\s+market|shares|"
                   r"top\s+stocks|biggest\s+stock\s+(?:gainers|movers))(?:\s+(?:doing|today|now|right\s+now))*" + _END)
_EVENTS = _compile(_LEAD + r"(?:which|what)\s+(?:markets?|industries|sectors|stocks)\s+(?:are|is)\s+(?:affected|hit|moved|touched)\s+by\s+"
                   r"(?:today's\s+|current\s+|the\s+latest\s+|world\s+)?(?:events|news|disasters)" + _END)
_EVENTS2 = _compile(_LEAD + r"(?:how\s+do|how\s+are)\s+(?:world\s+|today's\s+|current\s+)?(?:events|news)\s+(?:affecting|moving|"
                    r"affect|move)\s+(?:the\s+)?markets?" + _END)
_EVENTS3 = _compile(_LEAD + r"(?:events?\s+and\s+markets?|markets?\s+and\s+(?:world\s+)?events|market\s+impact\s+of\s+(?:today's\s+)?events)" + _END)
_IMPACT = _compile(_LEAD + r"(?:market\s+impact|markets?)\s+(?:of|for)\s+(?:intelligence\s+)?event\s+(?P<event>\S+:\S+)" + _END)


def _asset_symbol(match: re.Match[str]) -> tuple[str, str]:
    raw = (match.groupdict().get("coin") or match.groupdict().get("coin2") or "").strip()
    lowered = raw.lower()
    if lowered in COIN_ALIASES:
        return COIN_ALIASES[lowered], "crypto"
    if lowered in STOCK_ALIASES:
        return STOCK_ALIASES[lowered], "stock"
    return raw.upper(), "auto"


def _list_symbols(text: str) -> list[str] | None:
    """'btc, eth and spy' → ['BTC', 'ETH', 'SPY']; None unless every word is a market and at least one is a known one."""
    found, known = [], False
    for word in re.split(r"\s*(?:,|\band\b|&|\bvs\.?|\bversus\b)\s*|\s+", text.strip()):
        if not word:
            continue
        lowered = word.lower()
        if lowered in COIN_ALIASES:
            found.append(COIN_ALIASES[lowered])
        elif lowered in STOCK_ALIASES:
            found.append(STOCK_ALIASES[lowered])
        elif word.upper() in STOCK_NAMES:
            found.append(word.upper())
        elif re.fullmatch(r"[A-Z][A-Z.]{0,5}", word):
            found.append(word)
            continue  # an unknown capitalised word may be a ticker, but cannot vouch for the list
        else:
            return None  # "compare apple and microsoft revenue" is not a market comparison
        known = True
    return list(dict.fromkeys(found)) if known else None


def _periods_request(value: str) -> dict[str, Any] | None:
    """Possibilities by time period. A day or a week asked in other words stays with scenarios and the outlook."""
    from app.market_periods import period_key
    for pattern in _PERIODS_COMPARE:
        if match := pattern.match(value):
            listed = match.groupdict().get("list") or ""
            mine = bool(re.search(r"(?i)watch\s*list", value))
            symbols = _list_symbols(listed) if listed and not mine else []
            if symbols is None or listed and not mine and len(symbols) < 2:
                return None
            # Company names alone ("compare apple and microsoft") may be about the companies: a market word is needed.
            named = any(word.lower() in STOCK_ALIASES and word.upper() not in STOCK_NAMES for word in re.findall(r"[\w.&]+", listed))
            if named and not match["span"] and not re.search(r"(?i)\b(?:stocks?|shares?|odds|chances|possibilities|outlook)\b", value):
                return None
            key = period_key(match["span"] or "") or "1m"
            return {"tool": "market_periods_compare", "arguments": {"period": key, **({"symbols": symbols} if symbols else {})}}
    for every, patterns in ((True, _PERIODS_ALL), (False, _PERIODS)):
        for pattern in patterns:
            if not (match := pattern.match(value)):
                continue
            symbol, asset = _asset_symbol(match)
            raw = match.groupdict().get("coin") or match.groupdict().get("coin2") or ""
            # A bare word is a ticker only in capitals: "will it go up next month" is not about a market called IT.
            if asset == "auto" and not raw.isupper():
                return None
            key = period_key(match.groupdict().get("span") or "")
            if not every and key in (None, "1d", "1w"):
                return None
            asset = "stock" if re.search(r"(?i)\b(?:stock|shares?)\b", value) else asset
            return {"tool": "market_periods", "arguments": {"symbol": symbol, "period": key or "all",
                                                             **({"asset": asset} if asset != "auto" else {})}}
    return None


#: Everyday words a lower-case "<word> outlook" means, never a ticker.
_NOT_TICKERS = frozenset({
    "open", "start", "run", "the", "my", "your", "our", "their", "his", "her", "its", "a", "an", "this", "that", "job",
    "jobs", "work", "life", "sales", "trade", "world", "risk", "rates", "rate", "price", "cost", "costs", "news", "near",
    "next", "short", "long", "local", "home", "house", "rain", "storm", "snow", "heat", "solar", "wind", "food", "farm",
    "mail", "email", "inbox", "team", "plan", "year", "month", "week", "day", "today", "sky", "space", "tech", "auto",
    "cars", "power", "water", "land", "what", "whats", "show", "give", "get", "new", "old", "big", "best",
    "good", "bad", "real", "true", "full", "free", "fast", "slow", "high", "low", "up", "down", "us", "uk", "eu"})


def market_request(text: str) -> dict[str, Any] | None:
    """{"tool", "arguments"} for a read-only market question, or None. Buying and selling never match."""
    value = " ".join(str(text or "").split())
    if not value or len(value) > 240:
        return None
    # "Will bitcoin go up next month? Should I put all my savings in now?" -- a market question with a second
    # sentence went to the bare model, which invented a price and a forecast (2026-10-13 hard-chat check).
    # The first question is answered from HAYAT's measured odds, whose reply says it is not advice.
    first, mark, rest = value.partition("?")
    if mark and rest.strip() and len(first) <= 160:
        found = market_request(first + "?")
        if found:
            return found
    if len(value) > 160:
        return None
    if match := _IMPACT.match(value):
        return {"tool": "market_event_impact", "arguments": {"event_id": match["event"]}}
    if _EVENTS.match(value) or _EVENTS2.match(value) or _EVENTS3.match(value):
        return {"tool": "market_events", "arguments": {}}
    if _CALENDAR.match(value):
        return {"tool": "market_calendar", "arguments": {}}
    if match := _NEWS.match(value):
        rumours = "rumo" in (match["what"] or match["what2"] or "").lower()
        if match["coin"]:
            symbol, asset = _asset_symbol(match)
            if asset == "auto" and not match["coin"].isupper():
                return None
            return {"tool": "market_news", "arguments": {"symbol": symbol, **({"rumours_only": True} if rumours else {})}}
        return {"tool": "market_news", "arguments": {**({"rumours_only": True} if rumours else {})}}
    if periods := _periods_request(value):
        return periods
    for tool, patterns in (("market_signals", _SIGNALS), ("market_influences", _INFLUENCES)):
        for pattern in patterns:
            if match := pattern.match(value):
                symbol, asset = _asset_symbol(match)
                # A bare word is a ticker only in capitals: "what moves it" is not about a market called IT.
                if asset == "auto" and not match["coin"].isupper():
                    break
                asset = "stock" if re.search(r"(?i)\b(?:stock|shares?)\b", value) else asset
                return {"tool": tool, "arguments": {"symbol": symbol, **({"asset": asset} if asset != "auto" else {})}}
    if _TRADE.match(value) or re.fullmatch(r"(?i)is\s+(?:world\s+|global\s+)?shipping\s+disrupted\s*\??", value):
        return {"tool": "market_trade_routes", "arguments": {}}
    for pattern in _SCENARIO:
        if match := pattern.match(value):
            symbol, asset = _asset_symbol(match)
            # A bare word is a ticker only in capitals: "why is it down" is not about a market called IT.
            if asset == "auto" and not match["coin"].isupper():
                break
            asset = "stock" if re.search(r"(?i)\b(?:stock|shares?)\b", value) else asset
            return {"tool": "market_scenarios", "arguments": {"symbol": symbol, **({"asset": asset} if asset != "auto" else {})}}
    for pattern in _OUTLOOK:
        if match := pattern.match(value):
            symbol, asset = _asset_symbol(match)
            # "tsla outlook" is a ticker typed in lower case; "open outlook", "my outlook" and "career outlook" are
            # not (they were read as markets called OPEN, MY and CAREER; hard-tested 2026-10-17).
            coin = match["coin"]
            if asset == "auto" and not coin.isupper() and (len(coin) > 5 or coin.casefold() in _NOT_TICKERS):
                break
            asset = "stock" if re.search(r"(?i)\b(?:stock|shares?)\b", value) else asset
            return {"tool": "market_outlook", "arguments": {"symbol": symbol, **({"asset": asset} if asset != "auto" else {})}}
    if match := _STOCK_QUOTE.match(value):
        symbol, _asset = _asset_symbol(match)
        return {"tool": "market_quote", "arguments": {"symbol": symbol, "asset": "stock"}}
    if _STOCKS.match(value):
        return {"tool": "market_stocks", "arguments": {"limit": 15, **({"sort": "change"} if re.search(r"(?i)gainers|movers", value) else {})}}
    if match := _ALERT.match(value):
        price = float(match["price"].replace(",", "")) * (1000 if match["unit"] else 1)
        direction = {"above": "above", "over": "above", "below": "below", "under": "below"}.get((match["direction"] or "").lower(), "")
        if not direction and re.search(r"(?i)\b(?:falls|drops)\b", value):
            direction = "below"
        if not direction and re.search(r"(?i)\b(?:rises|climbs)\b", value):
            direction = "above"
        return {"tool": "market_alerts", "arguments": {"action": "add", "symbol": _symbol(match), "price": price,
                                                       **({"direction": direction} if direction else {})}}
    if _ALERT_LIST.match(value):
        return {"tool": "market_alerts", "arguments": {"action": "check" if re.search(r"(?i)\bcheck\b", value) else "list"}}
    if match := _ALERT_DELETE.match(value):
        return {"tool": "market_alerts", "arguments": {"action": "delete", "alert_id": int(match["id"])}}
    if match := _WATCH.match(value):
        action = "add" if match["verb"].lower() in ("add", "put") else "remove"
        return {"tool": "market_watchlist", "arguments": {"action": action, "symbol": _symbol(match)}}
    if _WATCH_LIST.match(value):
        return {"tool": "market_watchlist", "arguments": {"action": "list"}}
    if _CHAIN.match(value) and re.search(r"(?i)\b(?:gas|fees?|mempool|block|web\s*3|blockchain|chain|bitcoin|btc|ethereum|eth)\b", value):
        return {"tool": "market_chain", "arguments": {}}
    if match := _CHART.match(value):
        return {"tool": "market_chart", "arguments": {"symbol": _symbol(match), "span": _SPANS.get((match["span"] or "").lower(), "1D")}}
    if match := _BOOK.match(value):
        return {"tool": "market_book", "arguments": {"symbol": _symbol(match)}}
    if match := _TRADES.match(value):
        return {"tool": "market_trades", "arguments": {"symbol": _symbol(match)}}
    if match := _MOVERS.match(value):
        return {"tool": "market_overview", "arguments": {"sort": "losers" if match["kind"].lower() == "losers" else "change", "limit": 15}}
    if match := _OVERVIEW.match(value):
        what = (match["what"] or "").lower()
        sort = "change" if what in ("gainers", "movers") else "losers" if what == "losers" else "volume"
        return {"tool": "market_overview", "arguments": {"sort": sort, "limit": 15}}
    for pattern in _QUOTE:
        if match := pattern.match(value):
            unit = (match.groupdict().get("quote") or match.groupdict().get("quote2") or "").lower()
            return {"tool": "market_quote", "arguments": {"symbol": _symbol(match),
                                                          **({"quote": _QUOTE_WORDS[unit]} if unit in _QUOTE_WORDS else {})}}
    return None


def direct_plan(text: str, names: Any) -> dict[str, Any] | None:
    found = market_request(text)
    if not found or found["tool"] not in names:
        return None
    return {"description": "Read public crypto market data and report it with its source and time.",
            "tool": found["tool"], "arguments": found["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}


# ---------------------------------------------------------------------- replies
def money(value: float | None, quote: str = "USD") -> str:
    if value is None:
        return "—"
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(quote, "")
    digits = 2 if abs(value) >= 1 else 4 if abs(value) >= .01 else 8
    text = f"{value:,.{digits}f}"
    return f"{symbol}{text}" if symbol else f"{text} {quote}"


def signed_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def report(history: list[dict[str, Any]]) -> str | None:
    """Plain text for verified market results, written from the data (no model)."""
    sections = []
    for event in history or []:
        if not str(event.get("tool", "")).startswith("market_"):
            continue
        result = event.get("result") or {}
        if not result.get("success") or not (event.get("verification") or {}).get("passed"):
            continue
        value = result.get("output") or {}
        lines: list[str] = []
        if value.get("quote_view") and value.get("asset") == "stock":
            name = f"{value['name']} ({value['symbol']})" if value.get("name") else value["symbol"]
            exchange = value.get("exchange")
            if exchange:
                lines.append(f"{name}: {money(exchange['price'])} on the exchange ({exchange['source']}, {exchange['at'][11:16]} UTC) · "
                             f"{signed_pct(exchange['change_pct'])} today")
            lines.append(f"{name} tokenized share on Kraken: {money(value['price'])} · {signed_pct(value['change_today_pct'])} since 00:00 UTC"
                         + (f" · last traded {value['last_trade_at'][11:16]} UTC" if value.get("last_trade_at") else "")
                         + (" · thin market, the price may be old" if value.get("thin") else ""))
            lines.append(value["basis"])
        elif value.get("quote_view"):
            name = f"{value['name']} ({value['symbol']})" if value.get("name") else value["symbol"]
            lines.append(f"{name}: {money(value['price'], value['quote'])} on Kraken · {signed_pct(value['change_today_pct'])} since 00:00 UTC")
            lines.append(f"24 h range {money(value['low_24h'], value['quote'])} – {money(value['high_24h'], value['quote'])} · "
                         f"24 h volume {money(value['volume_24h_quote'], value['quote'])}")
            check = value.get("cross_check")
            if check:
                lines.append(f"Coinbase: {money(check['price'], 'USD')} ({check['difference_pct']:+.3f}% vs Kraken) · "
                             f"{signed_pct(check['change_24h_pct'])} over 24 h")
            for alert in value.get("triggered_alerts") or []:
                lines.append(f"Price alert #{alert['id']} reached: {alert['symbol']} is {alert['direction']} {money(alert['price'])}.")
        elif value.get("calendar_view"):
            from app.market_calendar import report_lines as calendar_lines
            lines += calendar_lines(value)
        elif value.get("news_view"):
            from app.market_news import report_lines as news_lines
            lines += news_lines(value)
        elif value.get("trade_view"):
            from app.trade_routes import report_lines
            lines += report_lines(value)
        elif value.get("signals_view"):
            from app.market_signals import report_lines as signal_lines
            lines += signal_lines(value)
        elif value.get("influences_view"):
            from app.market_influences import report_lines as influence_lines
            lines += influence_lines(value)
        elif value.get("periods_view") or value.get("periods_compare"):
            from app.market_periods import report_lines as period_lines
            lines += period_lines(value)
        elif value.get("scenarios_view"):
            day, week = value["next_day"], value["next_week"]
            from app.market_periods import plain_odds
            paths = value["paths"]
            lines.append(f"💬 In plain words: nobody can know. In past situations like now {value['symbol']} was higher a week "
                         f"later {week['now']['up_pct']}% of the time, {plain_odds(week['now']['up_pct'], week['now']['interval_pct'])}. "
                         f"Most past weeks ended between {money(paths['weak']['price'])} and {money(paths['strong']['price'])}.")
            lines.append(f"{value['symbol']} scenarios (shares of past cases, not a forecast) · now {money(value['price'])}"
                         + (" live" if value["live"] else "") + f" · {value['history_days']} days of history")
            lines.append(value["summary"])
            lines.append(f"Next day: higher in {day['now']['up_pct']}% of {day['now']['samples']} similar days "
                         f"({day['now']['interval_pct'][0]}–{day['now']['interval_pct'][1]}%) against {day['usual']['up_pct']}% usually: "
                         f"{day['verdict']}.")
            paths = value["paths"]
            lines.append(f"Coming week, from past weekly moves: weak {money(paths['weak']['price'])} ({paths['weak']['move_pct']:+.1f}%), "
                         f"middle {money(paths['middle']['price'])} ({paths['middle']['move_pct']:+.1f}%), strong "
                         f"{money(paths['strong']['price'])} ({paths['strong']['move_pct']:+.1f}%); 8 in 10 past weeks fell between weak and strong.")
            big = value["big_move"]
            if big.get("now_pct") is not None:
                lines.append(f"A move over {big['threshold_pct']}% tomorrow: {big['now_pct']}% of similar days, {big['usual_pct']}% usually.")
            lines.append("What might matter:")
            lines += [f"  {item['factor']}: {item['reading']}" + {"up": " (leans up)", "down": " (leans down)", "risk": " (adds risk)"}
                      .get(item["leaning"], "") for item in value["factors"]]
            lines.append(value["basis"])
        elif value.get("outlook"):
            day, week, record, trend = value["ranges"]["next_day"], value["ranges"]["next_week"], value["track_record"], value["trend"]
            lines.append(f"💬 In plain words: this can't say whether {value['symbol']} will go up. From how much it usually moves, "
                         f"the next 24 hours will most likely stay between {money(day['likely_68'][0])} and "
                         f"{money(day['likely_68'][1])} (about 2 chances in 3).")
            lines.append(f"{value['symbol']} outlook (statistics, not a prediction) · last completed hourly close {money(value['price'])}")
            lines.append(f"Next 24 h: likely {money(day['likely_68'][0])} – {money(day['likely_68'][1])} (68 %), "
                         f"wide {money(day['wide_95'][0])} – {money(day['wide_95'][1])} (95 %)")
            lines.append(f"Next 7 days: likely {money(week['likely_68'][0])} – {money(week['likely_68'][1])} (68 %)")
            lines.append(f"Trend {trend['direction']} · {signed_pct(trend['change_24h_pct'])} over 24 h · RSI {trend['rsi_14h']} · "
                         f"daily volatility {value['daily_volatility_pct']} %")
            if record.get("tested"):
                uncertainty = record.get('band_coverage_interval_pct')
                lines.append(f"Volatility baseline on this market's history ({record['tested']} non-overlapping tests): the 68 % range held "
                             f"{record['band_coverage_pct']} % of the time"
                             + (f" (95% coverage interval {uncertainty[0]}–{uncertainty[1]}%)" if uncertainty else '')
                             + f"; momentum called the next day's direction "
                             f"{record['momentum_right_pct']} % against {record.get('baseline_right_pct')} % for the past-only majority baseline. "
                             f"{record['verdict'].capitalize()}")
            for label, key in (('Hour', 'next_hour'), ('Day', 'next_day'), ('Week', 'next_week')):
                check = value['ranges'][key].get('validation', {})
                if check:
                    coverage = ' / '.join(f'{check[key]}%' if check[key] is not None else 'not measured'
                                          for key in ('coverage_68_pct', 'coverage_95_pct'))
                    lines.append(f"{label}: {value['ranges'][key]['method']}; {check['tested']} non-overlapping later tests; "
                                 f"68% / 95% coverage {coverage}. {check['note']}")
            lines.append(value["basis"])
        elif value.get("stocks_view"):
            lines.append(f"Stocks and funds (tokenized on Kraken) · {value['total']} trading:")
            for index, row in enumerate(value["rows"][:15], 1):
                lines.append(f"  {index}. {row['symbol']}" + (f" ({row['name']})" if row.get("name") else "")
                             + f" · {money(row['price'])} · {signed_pct(row['change_today_pct'])}" + (" · thin" if row.get("thin") else ""))
            lines.append(value["basis"])
        elif value.get("events_markets"):
            lines.append(f"World events and the markets they touch (last {value['hours']:g} h)"
                         + (f" · S&P 500 {signed_pct(value['benchmark']['change_today_pct'])} today" if value.get("benchmark") else "") + ":")
            for entry in value["sectors"]:
                moves = ", ".join(f"{item['symbol']} {signed_pct(item['change_today_pct'])}" + (" (thin)" if item.get("thin") else "")
                                  for item in entry["markets"]) or "no tradeable fund"
                lines.append(f"• {entry['sector']}: {len(entry['events'])} event(s), e.g. {entry['events'][0]['title'][:80]} · {moves}")
                lines.append(f"    why: {entry['why']}")
            if not value["sectors"]:
                lines.append("No significant event in the period matches a market rule.")
            lines.append(value["basis"])
        elif value.get("event_impact"):
            event = value["event"]
            lines.append(f"Markets and {event['title']} (reported {str(event['observed_at'])[:16]} UTC):")
            if not value["rules"]:
                lines.append("No market rule covers this kind of event here; HAYAT does not guess a link.")
            for rule in value["rules"]:
                lines.append(f"• {', '.join(rule['sectors'])}: {rule['why']}")
            for move in value["moves"]:
                lines.append(f"  {move['symbol']}: {signed_pct(move['change_pct'])} since then"
                             + (f" ({move['vs_market_pct']:+.2f} points vs the S&P 500)" if move.get("vs_market_pct") is not None else "")
                             + (" · thin trading" if move.get("thin") else ""))
            if value.get("note"):
                lines.append(value["note"])
            lines.append(value["basis"])
        elif value.get("live"):
            lines.append(f"Live from Kraken ({value['status']})"
                         + (f" · arriving {value['delay_ms_median']} ms after each trade (median)" if value.get("delay_ms_median") else "") + ":")
            for symbol, item in value["prices"].items():
                lines.append(f"  {symbol}: {item.get('price')}" + (f" · {signed_pct(item.get('change_pct'))} 24 h" if item.get("change_pct") is not None else "")
                             + (f" · last trade {item['last_trade_at'][11:23]} UTC" if item.get("last_trade_at") else ""))
            lines.append(value["basis"])
        elif value.get("chart_view"):
            lines.append(f"{value['symbol']} over {value['range']}: {signed_pct(value['change_pct'])} · "
                         f"high {money(value['high'], value['quote'])} · low {money(value['low'], value['quote'])} · "
                         f"{len(value['candles'])} candles of {value['interval_minutes']} min. Open the Trade Center for the chart.")
        elif value.get("book_view"):
            lines.append(f"{value['symbol']} order book on Kraken · spread {money(value['spread'], value['quote'])} "
                         f"({value['spread_pct']}%)")
            for bid, ask in list(zip(value["bids"], value["asks"]))[:8]:
                lines.append(f"  bid {money(bid['price'], value['quote'])} × {bid['amount']:g}   |   "
                             f"ask {money(ask['price'], value['quote'])} × {ask['amount']:g}")
        elif value.get("trades_view"):
            lines.append(f"Recent {value['symbol']} trades on Kraken:")
            for trade in value["trades"][:12]:
                lines.append(f"  {trade['at'][11:19]} UTC · {trade['side']} {trade['amount']:g} at {money(trade['price'], value['quote'])}")
        elif value.get("chain_view"):
            bitcoin, ethereum = value.get("bitcoin"), value.get("ethereum")
            if bitcoin:
                lines.append(f"Bitcoin: block {bitcoin['block_height']:,} · fees {bitcoin['fee_fast_sat_vb']} sat/vB fastest, "
                             f"{bitcoin['fee_30min_sat_vb']} within 30 min, {bitcoin['fee_1h_sat_vb']} within an hour · "
                             f"{bitcoin['mempool_transactions']:,} transactions waiting (mempool.space)")
            if ethereum:
                lines.append(f"Ethereum: gas {ethereum['gas_slow_gwei']} / {ethereum['gas_average_gwei']} / {ethereum['gas_fast_gwei']} gwei "
                             f"(slow / average / fast) · block every {ethereum['block_time_seconds']} s · "
                             f"{ethereum['network_utilization_pct']:.0f}% of capacity used (Blockscout)"
                             if ethereum.get("network_utilization_pct") is not None else
                             f"Ethereum: gas {ethereum['gas_average_gwei']} gwei average (Blockscout)")
            lines.extend("Unavailable: " + gap for gap in value.get("gaps") or [])
        elif value.get("watchlist_view"):
            change = {"add": f"Added {value['symbol']}. ", "remove": f"Removed {value['symbol']}. "}.get(value["action"], "")
            lines.append(change + ("Your crypto watchlist: " + ", ".join(value["symbols"]) if value["symbols"]
                                   else "Your crypto watchlist is empty."))
        elif value.get("alerts_view"):
            if value.get("created"):
                alert = value["created"]
                lines.append(f"Price alert #{alert['id']} set: {alert['symbol']} {alert['direction']} {money(alert['price'])} "
                             f"(now {money(alert['current'])}).")
            for alert in value.get("triggered") or []:
                lines.append(f"Reached: #{alert['id']} {alert['symbol']} {alert['direction']} {money(alert['price'])} "
                             f"(at {money(alert['triggered_price'])}).")
            waiting = [alert for alert in value["alerts"] if not alert["triggered_at"]]
            if value["action"] in ("list", "check"):
                lines.append(f"{len(waiting)} price alert(s) waiting" + (":" if waiting else "."))
                lines.extend(f"  #{alert['id']} {alert['symbol']} {alert['direction']} {money(alert['price'])}" for alert in waiting)
            if value["action"] == "delete":
                lines.append("Price alert deleted.")
            lines.append(value["basis"])
        elif value.get("markets") and "rows" in value:
            title = {"volume": "by 24 h volume", "change": "biggest gainers since 00:00 UTC",
                     "losers": "biggest fallers since 00:00 UTC"}.get(value["sort"], "")
            lines.append(f"Crypto markets in {value['quote']} on Kraken, {title} · {value['total']} markets "
                         f"({value['assets_listed']} assets listed):")
            for index, row in enumerate(value["rows"][:15], 1):
                lines.append(f"  {index}. {row['symbol']}" + (f" ({row['name']})" if row.get("name") else "")
                             + f" · {money(row['price'], value['quote'])} · {signed_pct(row['change_today_pct'])}")
            lines.append(value["basis"] + " Open the Trade Center for every market, charts and order books.")
        if lines:
            sections.append("\n".join(lines + ["Read-only market data · HAYAT does not trade or hold funds."]))
    return "\n\n".join(sections) or None
