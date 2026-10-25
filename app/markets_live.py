"""Live prices as they happen: Kraken's public WebSocket, every trade and ticker change.

Crypto and Kraken's tokenized stocks (AAPLx, SPYx, …) stream over one connection. Each
price carries the exchange's own trade time, and the delay from that time to its arrival
here is measured and shown (measured 2026-10-11: median 135 ms). That figure includes this
computer's clock offset; HAYAT never claims zero delay. Read-only: no order can be sent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)
URL = "wss://ws.kraken.com/v2"
#: Stop streaming when nothing has asked for a price for this long.
IDLE_SECONDS = 300


def ws_symbol(symbol: str, asset: str = "crypto") -> str:
    """'BTC' -> 'BTC/USD'; a stock 'AAPL' -> 'AAPLx/USD' (Kraken's tokenized share)."""
    symbol = symbol.upper().strip()
    if "/" in symbol:
        return symbol
    return f"{symbol}x/USD" if asset == "stock" else f"{symbol}/USD"


def display_symbol(stream: str) -> tuple[str, str]:
    """'AAPLx/USD' -> ('AAPL', 'stock'); 'BTC/USD' -> ('BTC', 'crypto')."""
    base = stream.split("/", 1)[0]
    return (base[:-1], "stock") if base.endswith("x") and base[:-1].isupper() else (base, "crypto")


class LiveStream:
    def __init__(self, connect: Callable[..., Any] | None = None, url: str = URL) -> None:
        self.url, self._connect = url, connect
        self._lock = threading.RLock()
        self._wanted: set[str] = set()
        self._latest: dict[str, dict[str, Any]] = {}
        self._latency: deque[float] = deque(maxlen=400)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._changed = threading.Event()
        self._last_asked = time.monotonic()
        self.status, self.error, self.connected_at = "idle", "", None

    # ----------------------------------------------------------------- what to stream
    def watch(self, symbols: list[str]) -> None:
        with self._lock:
            new = {symbol for symbol in symbols if symbol} - self._wanted
            self._wanted |= new
            self._last_asked = time.monotonic()
        if new:
            self._changed.set()
        self._ensure_running()

    def unwatch(self, symbols: list[str]) -> None:
        with self._lock:
            self._wanted -= set(symbols)
        self._changed.set()

    def snapshot(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """The latest of each price, how old it is, and how late it arrived."""
        now = time.time()
        with self._lock:
            self._last_asked = time.monotonic()
            chosen = symbols if symbols is not None else sorted(self._wanted)
            prices = {}
            for symbol in chosen:
                item = self._latest.get(symbol)
                if item:
                    prices[symbol] = {**item, "age_seconds": round(now - item["received"], 1)}
            latency = list(self._latency)
        return {"markets": True, "live": True, "status": self.status, "error": self.error, "prices": prices,
                "delay_ms_median": round(statistics.median(latency)) if latency else None,
                "delay_ms_p90": round(sorted(latency)[int(len(latency) * .9)]) if len(latency) >= 10 else None,
                "source": "Kraken WebSocket v2 (public)",
                "basis": "Delay is from the exchange's trade time to arrival here, including this computer's clock offset."}

    def close(self) -> None:
        self._stop.set()
        self._changed.set()

    # ----------------------------------------------------------------- the connection
    def _ensure_running(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="hayat-market-stream", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            if time.monotonic() - self._last_asked > IDLE_SECONDS:
                self.status = "idle"
                return
            try:
                asyncio.run(self._session())
                delay = 1.0
            except Exception as error:  # the network dropped: reconnect with backoff, say so meanwhile
                self.status, self.error = "reconnecting", f"{type(error).__name__}: {str(error)[:120]}"
                log_swallowed(_log, "the live market stream")
                self._stop.wait(delay)
                delay = min(30.0, delay * 2)

    async def _session(self) -> None:
        import websockets
        connect = self._connect or websockets.connect
        self.status = "connecting"
        async with connect(self.url, user_agent_header="HAYAT-Markets/1.0", ping_interval=20, open_timeout=15) as socket:
            self.status, self.error, self.connected_at = "live", "", datetime.now(UTC).isoformat(timespec="seconds")
            subscribed: set[str] = set()
            while not self._stop.is_set():
                if time.monotonic() - self._last_asked > IDLE_SECONDS:
                    return
                with self._lock:
                    wanted = set(self._wanted)
                if wanted != subscribed:
                    added, removed = sorted(wanted - subscribed), sorted(subscribed - wanted)
                    for method, symbols in (("subscribe", added), ("unsubscribe", removed)):
                        if symbols:
                            for channel in ("ticker", "trade"):
                                await socket.send(json.dumps({"method": method, "params": {"channel": channel, "symbol": symbols}}))
                    subscribed = wanted
                    self._changed.clear()
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=1.0)
                except TimeoutError:
                    continue
                self.handle(json.loads(raw))

    def handle(self, message: dict[str, Any]) -> None:
        """One message from Kraken: a ticker change or new trades."""
        channel, received = message.get("channel"), time.time()
        if channel == "ticker":
            for item in message.get("data") or []:
                with self._lock:
                    entry = self._latest.setdefault(item["symbol"], {})
                    entry.update(price=item.get("last"), bid=item.get("bid"), ask=item.get("ask"),
                                 change_pct=item.get("change_pct"), high=item.get("high"), low=item.get("low"),
                                 volume=item.get("volume"), received=received)
        elif channel == "trade":
            for item in message.get("data") or []:
                try:
                    traded = datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00")).timestamp()
                except (KeyError, ValueError):
                    continue
                with self._lock:
                    entry = self._latest.setdefault(item["symbol"], {})
                    entry.update(price=item.get("price"), last_trade_at=datetime.fromtimestamp(traded, UTC).isoformat(timespec="milliseconds"),
                                 last_trade_side=item.get("side"), received=received)
                    if message.get("type") == "update":  # the first snapshot is history, not live delay
                        delay = (received - traded) * 1000
                        entry["delay_ms"] = round(delay)
                        if -2000 < delay < 60000:
                            self._latency.append(delay)
