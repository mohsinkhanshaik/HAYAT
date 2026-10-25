"""Stocks (tokenized), the live stream, world events by industry, and the honest outlook."""
import asyncio
import json
import math
import random
import time
from types import SimpleNamespace

import pytest

from app.market_events import RULES, SECTORS, event_impact, events_and_markets, in_region, matching_rules
from app.market_outlook import outlook, track_record
from app.markets import MarketData, market_request, report
from app.markets_live import LiveStream, display_symbol, ws_symbol
from tests.test_markets import PAYLOADS, Fetcher

NOW = int(time.time())
STOCK_PAIRS = {"error": [], "result": {
    "AAPLxUSD": {"wsname": "AAPLx/USD", "altname": "AAPLxUSD", "status": "online"},
    "SPYxUSD": {"wsname": "SPYx/USD", "altname": "SPYxUSD", "status": "online"},
    "XLExUSD": {"wsname": "XLEx/USD", "altname": "XLExUSD", "status": "post_only"},
    "AAPLSPVUSD": {"wsname": "AAPLSPV/USD", "altname": "AAPLSPVUSD", "status": "online"}}}


def ticker(price, opened, trades):
    return {"c": [str(price), "1"], "o": str(opened), "h": ["0", str(price * 1.01)], "l": ["0", str(price * .99)], "v": ["1", "10"],
            "p": ["0", str(price)], "t": [1, trades], "b": [str(price - .01), "1"], "a": [str(price + .01), "1"]}


STOCK_TICKERS = {"error": [], "result": {"AAPLxUSD": ticker(336.19, 336.08, 54), "SPYxUSD": ticker(780.0, 778.4, 80),
                                         "XLExUSD": ticker(58.3, 58.3, 1)}}


class StockFetcher(Fetcher):
    def __init__(self):
        super().__init__()
        self.headers = []

    def fetch(self, url, max_bytes, accept=None, request_headers=None):
        self.calls.append(url)
        if "finnhub" in url:
            self.headers.append(request_headers)
            return SimpleNamespace(content=json.dumps({"c": 336.5, "d": .4, "dp": .12, "h": 337, "l": 334, "o": 335, "pc": 336.1,
                                                       "t": NOW - 60}).encode())
        if "tokenized_asset" in url:
            if "AssetPairs" in url:
                return SimpleNamespace(content=json.dumps(STOCK_PAIRS).encode())
            if "Ticker" in url:
                return SimpleNamespace(content=json.dumps(STOCK_TICKERS).encode())
            if "Trades" in url:
                return SimpleNamespace(content=json.dumps({"error": [], "result": {"AAPLxUSD": [["336.19", "0.01", NOW - 600, "b", "l", "", 9]],
                                                                                     "last": "1"}}).encode())
            if "OHLC" in url:
                key = url.split("pair=")[1].split("&")[0]
                return SimpleNamespace(content=json.dumps({"error": [], "result": {key: CANDLES.get(key, CANDLES["default"]), "last": NOW}}).encode())
        return super().fetch(url, max_bytes, accept)


def walk(seed, count=721, sigma=.004, start=100.0, trades=10):
    random.seed(seed)
    price, rows = start, []
    for index in range(count):
        opened = price
        price *= math.exp(random.gauss(0, sigma))
        rows.append([NOW - 3600 * (count - index), str(opened), str(max(opened, price)), str(min(opened, price)), str(price), "0", "5", trades])
    return rows


CANDLES = {"default": walk(1), "SPYxUSD": walk(2), "XLExUSD": walk(3, trades=0)}


@pytest.fixture
def markets(tmp_path):
    return MarketData(StockFetcher(), tmp_path / "markets.db")


# ------------------------------------------------------------------ stocks
def test_tokenized_stocks_are_listed_with_names_and_thin_markets_flagged(markets):
    assert set(markets.stock_pairs()) == {"AAPL", "SPY", "XLE"}  # the SPV duplicates are not shares
    value = markets.stocks()
    rows = {row["symbol"]: row for row in value["rows"]}
    assert rows["AAPL"]["name"] == "Apple" and not rows["AAPL"]["thin"] and rows["XLE"]["thin"]
    assert "drift from the exchange price" in value["basis"]
    assert [row["symbol"] for row in markets.stocks("apple")["rows"]] == ["AAPL"]


def test_a_stock_quote_says_it_is_tokenized_and_uses_the_exchange_price_with_a_key(markets, monkeypatch):
    monkeypatch.delenv("HAYAT_KEY_FINNHUB", raising=False)
    quote = markets.quote("apple", asset="stock")
    assert quote["asset"] == "stock" and quote["symbol"] == "AAPL" and quote["exchange"] is None and quote["last_trade_at"]
    assert "Finnhub key" in quote["basis"]
    assert markets.quote("AAPL")["asset"] == "stock"  # no crypto called AAPL: the share
    assert markets.quote("BTC")["asset"] == "crypto"
    monkeypatch.setenv("HAYAT_KEY_FINNHUB", "secret-token")
    quote = markets.quote("AAPL", asset="stock")
    assert quote["exchange"]["price"] == 336.5 and quote["exchange"]["difference_pct"] == pytest.approx(-.092, abs=.01)
    assert markets.fetcher.headers[-1] == {"X-Finnhub-Token": "secret-token"}
    assert not any("secret-token" in url for url in markets.fetcher.calls)  # the key never sits in an address
    text = report([{"tool": "market_quote", "result": {"success": True, "output": quote}, "verification": {"passed": True}}])
    assert "on the exchange (Finnhub" in text and "tokenized share on Kraken" in text
    with pytest.raises(ValueError, match="no market for NOPE"):
        markets.quote("nope")


def test_charts_books_and_trades_work_for_shares(markets):
    assert markets.chart("AAPL", "1W", asset="stock")["symbol"] == "AAPL"
    assert any("asset_class=tokenized_asset" in url and "OHLC" in url for url in markets.fetcher.calls)
    symbol, kind, candles = markets.candles("SPY", 60, "stock")
    assert symbol == "SPY" and kind == "stock" and len(candles) == 721 and candles[0]["trades"] == 10


# ------------------------------------------------------------------ live stream
def test_symbols_for_the_stream():
    assert ws_symbol("btc") == "BTC/USD" and ws_symbol("AAPL", "stock") == "AAPLx/USD" and ws_symbol("ETH/EUR") == "ETH/EUR"
    assert display_symbol("AAPLx/USD") == ("AAPL", "stock") and display_symbol("BTC/USD") == ("BTC", "crypto")


def test_ticks_and_trades_update_prices_and_the_measured_delay():
    stream = LiveStream()
    sent = time.time() - .12
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sent)) + f".{int(sent % 1 * 1e6):06d}Z"
    stream.handle({"channel": "ticker", "type": "snapshot", "data": [{"symbol": "BTC/USD", "last": 83000.0, "bid": 82999.9, "ask": 83000.1,
                                                                     "change_pct": .4, "volume": 1000}]})
    stream.handle({"channel": "trade", "type": "snapshot", "data": [{"symbol": "BTC/USD", "price": 82990.0, "timestamp": stamp}]})
    assert stream.snapshot(["BTC/USD"])["delay_ms_median"] is None  # history is not a live delay
    for _ in range(12):
        stream.handle({"channel": "trade", "type": "update", "data": [{"symbol": "BTC/USD", "price": 83001.5, "side": "buy",
                                                                       "timestamp": stamp}]})
    value = stream.snapshot(["BTC/USD"])
    item = value["prices"]["BTC/USD"]
    assert item["price"] == 83001.5 and item["change_pct"] == .4 and item["last_trade_side"] == "buy"
    assert 80 <= value["delay_ms_median"] <= 2000 and value["delay_ms_p90"] is not None and item["age_seconds"] < 5
    assert "clock offset" in value["basis"]


def test_the_stream_subscribes_follows_changes_and_reconnects(monkeypatch):
    sent, connections = [], []

    class Socket:
        def __init__(self):
            self.queue = [json.dumps({"channel": "ticker", "type": "update", "data": [{"symbol": "ETH/USD", "last": 2500.0}]})]

        async def __aenter__(self):
            connections.append(self)
            if len(connections) == 1:
                raise ConnectionError("network down")  # the first attempt fails: the stream must retry
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, text):
            sent.append(json.loads(text))

        async def recv(self):
            if self.queue:
                return self.queue.pop(0)
            await asyncio.sleep(.05)
            raise TimeoutError

    stream = LiveStream(connect=lambda *args, **options: Socket())
    monkeypatch.setattr("app.markets_live.IDLE_SECONDS", 30)
    stream.watch(["ETH/USD", "AAPLx/USD"])
    deadline = time.time() + 8
    while time.time() < deadline and not stream.snapshot(["ETH/USD"])["prices"]:
        time.sleep(.05)
    assert stream.snapshot(["ETH/USD"])["prices"]["ETH/USD"]["price"] == 2500.0 and stream.status == "live"
    assert {(message["params"]["channel"], tuple(message["params"]["symbol"])) for message in sent
            if message["method"] == "subscribe"} == {("ticker", ("AAPLx/USD", "ETH/USD")), ("trade", ("AAPLx/USD", "ETH/USD"))}
    stream.unwatch(["AAPLx/USD"])
    deadline = time.time() + 3
    while time.time() < deadline and not any(message["method"] == "unsubscribe" for message in sent):
        time.sleep(.05)
    assert any(message["method"] == "unsubscribe" and message["params"]["symbol"] == ["AAPLx/USD"] for message in sent)
    stream.close()


# ------------------------------------------------------------------ world events -> markets
def event(category, latitude, longitude, title, severity="medium", **details):
    return {"id": f"{category}:1", "category": category, "latitude": latitude, "longitude": longitude, "title": title,
            "headline": title, "severity": severity, "details": details, "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                                                                   time.gmtime(NOW - 7200))}


@pytest.mark.parametrize(("row", "rule"), [
    (event("severe_weather", 29.7, -95.3, "Hurricane Delta makes landfall near Houston"), "gulf-storm"),
    (event("earthquakes", 24.0, 121.6, "M6.8 - Hualien, Taiwan", magnitude=6.8), "asia-quake-chips"),
    (event("conflict", 15.4, 44.2, "Houthi missile strikes on Red Sea tankers"), "middle-east-conflict"),
    (event("attack", 47.8, 35.2, "Russian drone attack on Zaporizhzhia"), "europe-war"),
    (event("earthquakes", -23.6, -70.4, "M7.1 - Antofagasta, Chile", magnitude=7.1), "copper-supply"),
    (event("news", 0, 0, "Ransomware attack shuts hospital systems"), "cyber"),
    (event("news", None, None, "Crypto exchange hack drains $200M of bitcoin"), "crypto-incident"),
])
def test_events_meet_the_rules_for_their_markets(row, rule):
    assert rule in [found["id"] for found in matching_rules(row)]


@pytest.mark.parametrize("row", [
    event("earthquakes", -23.6, -70.4, "M4.0 - Antofagasta, Chile", magnitude=4.0),  # too small to touch copper
    event("earthquakes", 24.0, 121.6, "M4.2 - Taiwan", magnitude=4.2),
    event("cyber", 0, 0, "Broadcom VMware vCenter Path Traversal Vulnerability", severity="high"),  # routine notice
    event("attack", 52.2, 21.0, "Woman injures three in Poland school knife attack"),  # not war
    event("conflict", 31.8, 35.2, "Demolition displaces 69 people"),
    event("wildfires", 34.1, -118.2, "HEATHER Fire"),  # "heat" is not "HEATHER"
    event("floods", -10.0, -55.0, "Flood in Brazil"),  # outside the copper countries
])
def test_routine_or_unrelated_events_meet_no_market_rule(row):
    assert matching_rules(row) == []


def test_every_rule_names_real_sectors_and_a_reason():
    for rule in RULES:
        assert rule["why"].endswith(".") and all(sector in SECTORS for sector in rule["sectors"])
    assert in_region(29.7, -95.3, "us_gulf") and not in_region(40.7, -74.0, "us_gulf") and in_region(None, None, "world")


class Candles:
    """markets.candles over prepared series: the event's sectors moved, the market a little."""

    def __init__(self, moves):
        self.moves = moves

    def candles(self, symbol, interval, asset):
        if symbol not in self.moves:
            raise ValueError("not listed")
        change, trades = self.moves[symbol]
        rows = [{"time": NOW - 3600 * (48 - index), "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1,
                 "trades": trades} for index in range(48)]
        rows[-1]["close"] = 100.0 * (1 + change / 100)
        return symbol, "crypto" if symbol in ("BTC", "ETH") else "stock", rows


def test_event_impact_compares_each_market_with_the_whole_market():
    row = event("conflict", 15.4, 44.2, "Houthi missile strikes on Red Sea tankers")
    manager = SimpleNamespace(event_record=lambda event_id: {"event": row})
    moves = {"XLE": (3.0, 40), "XOP": (4.0, 40), "XOM": (2.0, 40), "CVX": (2.0, 40), "GLD": (1.0, 40), "ITA": (.5, 40), "SPY": (.5, 90)}
    value = event_impact(manager, Candles(moves), "conflict:1")
    by = {move["symbol"]: move for move in value["moves"]}
    assert value["rules"][0]["id"] == "middle-east-conflict" and by["XLE"]["vs_market_pct"] == pytest.approx(2.5)
    assert by["SPY"].get("vs_market_pct") is None and not value["quiet"] and value["gaps"] == []
    text = report([{"tool": "market_event_impact", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert "XLE: +3.00% since then (+2.50 points vs the S&P 500)" in text and "does not show the event caused it" in text
    quiet = event_impact(manager, Candles({symbol: (0.0, 0) for symbol in moves}), "conflict:1")
    assert quiet["quiet"] and "not yet a reaction" in quiet["note"]
    none = event_impact(SimpleNamespace(event_record=lambda event_id: {"event": event("floods", -10, -55, "Flood in Brazil")}),
                        Candles({"SPY": (.1, 9)}), "floods:1")
    assert none["rules"] == [] and "does not guess a link" in report([{"tool": "market_event_impact",
                                                                       "result": {"success": True, "output": none},
                                                                       "verification": {"passed": True}}])


def test_events_are_grouped_by_industry_with_todays_moves(markets):
    rows = [event("conflict", 15.4, 44.2, "Houthi missile strikes on tankers", severity="low"),  # low-rated news still counts
            event("cyber", 0, 0, "Routine flaw", severity="medium"),
            event("severe_weather", 29.7, -95.3, "Hurricane nears Houston", severity="medium")]
    manager = SimpleNamespace(dashboard=lambda category="", **options: {"events": [row for row in rows if row["category"] == category]})
    value = events_and_markets(manager, markets)
    sectors = {entry["sector"]: entry for entry in value["sectors"]}
    assert "Energy (oil and gas)" in sectors and "Cybersecurity" not in sectors
    assert [item["symbol"] for item in sectors["Energy (oil and gas)"]["markets"]] == ["XLE"]  # only the listed ones
    assert value["benchmark"]["symbol"] == "SPY" and len(sectors["Energy (oil and gas)"]["events"]) == 2
    text = report([{"tool": "market_events", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert "Energy (oil and gas)" in text and "why:" in text


# ------------------------------------------------------------------ outlook
def test_the_outlook_ranges_hold_about_as_often_as_they_say_on_a_random_walk(markets):
    value = outlook(markets, "SPY", "stock")
    record = value["track_record"]
    assert 15 <= record["tested"] <= 30
    assert record['band_coverage_interval_pct'][0] <= 68.3 <= record['band_coverage_interval_pct'][1]
    assert record['non_overlapping']
    assert 30 <= record["momentum_right_pct"] <= 70  # a random walk: no better than chance, and the answer says so
    low, high = value["ranges"]["next_day"]["likely_68"]
    wide_low, wide_high = value["ranges"]["next_day"]["wide_95"]
    assert wide_low < low < value["price"] < high < wide_high
    assert value["trend"]["direction"] in ("up", "down", "flat") and 0 <= value["trend"]["rsi_14h"] <= 100
    text = report([{"tool": "market_outlook", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert "not a prediction" in text and "majority baseline" in text and "Next 24 h: likely" in text


def test_perfect_momentum_on_an_always_rising_market_does_not_claim_an_edge():
    trending = [100 * math.exp(.002 * index) for index in range(720)]
    record = track_record(trending)
    assert record["momentum_right_pct"] == 100.0 and record['baseline_right_pct'] == 100.0
    assert not record['direction_improves_baseline'] and 'no demonstrated edge' in record['verdict']


def test_a_thin_market_says_its_ranges_are_understated(markets):
    assert outlook(markets, "XLE", "stock")["thin"]


# ------------------------------------------------------------------ chat
@pytest.mark.parametrize(("text", "tool", "arguments"), [
    ("bitcoin forecast", "market_outlook", {"symbol": "BTC", "asset": "crypto"}),
    ("will bitcoin go up tomorrow", "market_outlook", {"symbol": "BTC", "asset": "crypto"}),
    ("where will tesla stock go", "market_outlook", {"symbol": "TSLA", "asset": "stock"}),
    ("apple stock price", "market_quote", {"symbol": "AAPL", "asset": "stock"}),
    ("nvidia shares", "market_quote", {"symbol": "NVDA", "asset": "stock"}),
    ("how are stocks doing", "market_stocks", {"limit": 15}),
    ("which markets are affected by today's events", "market_events", {}),
    ("how are world events affecting markets", "market_events", {}),
    ("market impact of event disasters:EQ-1", "market_event_impact", {"event_id": "disasters:EQ-1"}),
])
def test_market_sentences(text, tool, arguments):
    assert market_request(text) == {"tool": tool, "arguments": arguments}


@pytest.mark.parametrize("text", ["buy 10 apple shares", "sell my nvidia stock", "short tesla stock", "invest 500 dollars in SPY"])
def test_trading_stocks_is_stopped_like_crypto(text):
    from app.safety.money_requests import money_request
    assert money_request(text) and market_request(text) is None


def test_the_live_tool_waits_for_first_prices():
    from app.markets import register_market_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    import tempfile
    from pathlib import Path

    class Stream:
        def __init__(self):
            self.watched = []

        def watch(self, symbols):
            self.watched += symbols

        def snapshot(self, symbols):
            return {"markets": True, "live": True, "status": "live", "prices": {symbol: {"price": 1.0, "age_seconds": 0} for symbol in symbols},
                    "delay_ms_median": 130, "basis": "b"}
    markets = MarketData(StockFetcher())
    markets.live = Stream()
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, Path(tempfile.mkdtemp())))
    register_market_tools(registry, markets)
    result = registry.execute("market_live", {"symbols": ["BTC", "AAPL"]})
    assert result.success and markets.live.watched == ["BTC/USD", "AAPLx/USD"]
    assert "arriving 130 ms after each trade" in report([{"tool": "market_live", "result": result.to_dict(),
                                                          "verification": {"passed": True}}])
    assert PAYLOADS  # the crypto fixtures stay shared with test_markets
