"""Read-only crypto markets: data, routing, replies, the money boundary and the Trade Center window."""
import json
import time
from types import SimpleNamespace

import pytest

from app.markets import FIAT, MarketData, TOOL_NAMES, direct_plan, market_request, money, register_market_tools, report
from app.safety.money_requests import describe, money_request

NOW = int(time.time())
PAYLOADS = {
    "AssetPairs": {"error": [], "result": {
        "XXBTZUSD": {"wsname": "XBT/USD", "altname": "XBTUSD", "status": "online"},
        "XETHZUSD": {"wsname": "ETH/USD", "altname": "ETHUSD", "status": "online"},
        "SOLUSD": {"wsname": "SOL/USD", "altname": "SOLUSD", "status": "online"},
        "PEPEUSD": {"wsname": "PEPE/USD", "altname": "PEPEUSD", "status": "online"},
        "ZEURZUSD": {"wsname": "EUR/USD", "altname": "EURUSD", "status": "online"},
        "XXBTZEUR": {"wsname": "XBT/EUR", "altname": "XBTEUR", "status": "online"},
        "OLDUSD": {"wsname": "OLD/USD", "altname": "OLDUSD", "status": "delisted"}}},
    "Ticker": {"error": [], "result": {
        "XXBTZUSD": {"c": ["83000.0", "1"], "o": "80000.0", "h": ["83100", "83200"], "l": ["79000", "78000"],
                     "v": ["100", "500"], "p": ["82000", "81000"], "t": [10, 20000], "b": ["82999", "1"], "a": ["83001", "1"]},
        "XETHZUSD": {"c": ["2500.0", "1"], "o": "2600.0", "h": ["2600", "2650"], "l": ["2450", "2400"],
                     "v": ["100", "1000"], "p": ["2550", "2525"], "t": [5, 9000], "b": ["2499", "1"], "a": ["2501", "1"]},
        "SOLUSD": {"c": ["110.0", "1"], "o": "100.0", "h": ["111", "112"], "l": ["99", "98"],
                   "v": ["1", "10000"], "p": ["105", "105"], "t": [1, 500], "b": ["109.9", "1"], "a": ["110.1", "1"]},
        "PEPEUSD": {"c": ["0.000004", "1"], "o": "0.000004", "h": ["0", "0.0000041"], "l": ["0", "0.0000039"],
                    "v": ["1", "1000000000"], "p": ["0", "0.000004"], "t": [1, 50], "b": ["0.0000039", "1"], "a": ["0.000004", "1"]},
        "XXBTZEUR": {"c": ["76000.0", "1"], "o": "75000.0", "h": ["1", "76100"], "l": ["1", "74000"], "v": ["1", "10"],
                     "p": ["1", "75500"], "t": [1, 9], "b": ["75990", "1"], "a": ["76010", "1"]},
        "ZEURZUSD": {"c": ["1.12", "1"], "o": "1.11", "h": ["1", "1.13"], "l": ["1", "1.1"], "v": ["1", "9999999"],
                     "p": ["1", "1.12"], "t": [1, 9], "b": ["1.1", "1"], "a": ["1.2", "1"]}}},
    "OHLC": {"error": [], "result": {"XXBTZUSD": [[NOW - 900 * (95 - index), "80000", "81000", "79500", str(80000 + index * 30),
                                                   "80500", "12.5", 10] for index in range(96)], "last": NOW}},
    "Depth": {"error": [], "result": {"XXBTZUSD": {"asks": [["83001", "0.5", NOW], ["83002", "1.0", NOW]],
                                                   "bids": [["82999", "0.7", NOW], ["82998", "2.0", NOW]]}}},
    "Trades": {"error": [], "result": {"XXBTZUSD": [["83000", "0.01", NOW - 5, "b", "m", "", 1],
                                                    ["82999", "0.00004512", NOW - 2, "s", "m", "", 2]], "last": "x"}},
    "currencies": [{"id": "BTC", "name": "Bitcoin"}, {"id": "ETH", "name": "Ether"}, {"id": "SOL", "name": "Solana"}],
    "stats": {"open": "80500", "high": "83300", "low": "78100", "last": "82990"},
    "fees/recommended": {"fastestFee": 5, "halfHourFee": 3, "hourFee": 2, "economyFee": 1},
    "mempool": {"count": 81000, "vsize": 12345678},
    "tip/height": 970858,
    "stats/eth": {"gas_prices": {"slow": .08, "average": .23, "fast": 1.7}, "average_block_time": 12000,
                  "transactions_today": "1644445", "network_utilization_percentage": 51.2, "coin_price": "2500"},
}


class Fetcher:
    def __init__(self):
        self.calls = []
        self.down = set()

    def fetch(self, url, max_bytes, accept=None):
        self.calls.append(url)
        if any(host in url for host in self.down):
            raise ConnectionError("offline")
        for key in ("AssetPairs", "Ticker", "OHLC", "Depth", "Trades", "currencies", "fees/recommended", "tip/height"):
            if key in url:
                return SimpleNamespace(content=json.dumps(PAYLOADS[key]).encode())
        if url.endswith("/stats") and "coinbase" in url:
            return SimpleNamespace(content=json.dumps(PAYLOADS["stats"]).encode())
        if "blockscout" in url:
            return SimpleNamespace(content=json.dumps(PAYLOADS["stats/eth"]).encode())
        if url.endswith("/mempool"):
            return SimpleNamespace(content=json.dumps(PAYLOADS["mempool"]).encode())
        raise AssertionError("unexpected " + url)


@pytest.fixture
def markets(tmp_path):
    return MarketData(Fetcher(), tmp_path / "markets.db")


def test_the_market_list_is_every_online_crypto_pair_without_fiat(markets):
    value = markets.overview()
    assert [row["symbol"] for row in value["rows"]] == ["BTC", "ETH", "SOL", "PEPE"]  # by 24 h volume; EUR is fiat
    btc = value["rows"][0]
    assert btc["name"] == "Bitcoin" and btc["price"] == 83000 and btc["change_today_pct"] == 3.75
    assert btc["volume_24h_quote"] == 500 * 81000 and "EUR" in FIAT and value["assets_listed"] == 4
    assert [row["symbol"] for row in markets.overview(sort="change")["rows"]][:2] == ["SOL", "BTC"]
    assert markets.overview(sort="losers")["rows"][0]["symbol"] == "ETH"
    assert [row["symbol"] for row in markets.overview(query="solana")["rows"]] == ["SOL"]
    assert markets.overview(quote="EUR")["rows"][0]["symbol"] == "BTC"
    with pytest.raises(ValueError):
        markets.overview(quote="JPY")


def test_reads_are_cached_so_the_window_does_not_hammer_the_apis(markets):
    markets.overview()
    markets.overview()
    assert sum("Ticker" in url for url in markets.fetcher.calls) == 1


def test_a_quote_is_cross_checked_and_names_kraken_codes_in_usual_tickers(markets):
    value = markets.quote("bitcoin")
    assert value["symbol"] == "BTC" and value["cross_check"]["difference_pct"] == round((82990 - 83000) / 83000 * 100, 3)
    assert value["cross_check"]["change_24h_pct"] == round((82990 - 80500) / 80500 * 100, 2)
    markets.fetcher.down.add("coinbase")
    markets._cache.clear()
    assert markets.quote("BTC")["cross_check"] is None  # Kraken stands alone when Coinbase is down
    with pytest.raises(ValueError, match="no market for DOGE"):
        markets.quote("dogecoin")


def test_chart_book_trades_and_chain(markets):
    chart = markets.chart("BTC", "1D")
    assert len(chart["candles"]) == 96 and chart["interval_minutes"] == 15 and chart["change_pct"] > 0
    book = markets.book("BTC")
    assert book["spread"] == 2 and book["bids"][0]["price"] == 82999
    trades = markets.trades("BTC")
    assert trades["trades"][0]["side"] == "sell" and trades["trades"][0]["amount"] == 0.00004512
    chain = markets.chain()
    assert chain["bitcoin"]["block_height"] == 970858 and chain["bitcoin"]["fee_fast_sat_vb"] == 5
    assert chain["ethereum"]["gas_average_gwei"] == .23 and chain["ethereum"]["block_time_seconds"] == 12
    markets._cache.clear()
    markets.fetcher.down.add("blockscout")
    partial = markets.chain()
    assert "ethereum" not in partial and partial["gaps"][0].startswith("Ethereum")
    with pytest.raises(ValueError):
        markets.chart("BTC", "5Y")


def test_watchlist_and_price_alerts_stay_local(markets):
    assert markets.watchlist("add", "solana")["symbols"] == ["SOL"]
    with pytest.raises(ValueError):
        markets.watchlist("add", "nosuchcoin")
    assert markets.watchlist("remove", "SOL")["symbols"] == []
    above = markets.alerts("add", "BTC", price=90000)["created"]
    below = markets.alerts("add", "BTC", price=70000)["created"]
    assert (above["direction"], below["direction"]) == ("above", "below")  # from the current price
    assert markets.check_alerts({"BTC": 89000}) == []
    reached = markets.check_alerts({"BTC": 91000})
    assert [alert["id"] for alert in reached] == [above["id"]] and markets.check_alerts({"BTC": 92000}) == []
    assert markets.alerts("delete", alert_id=below["id"])["alerts"][0]["triggered_at"]
    with pytest.raises(ValueError):
        markets.alerts("delete", alert_id=999)


@pytest.mark.parametrize(("text", "tool", "arguments"), [
    ("bitcoin price", "market_quote", {"symbol": "BTC"}),
    ("how much is eth", "market_quote", {"symbol": "ETH"}),
    ("price of solana in euros", "market_quote", {"symbol": "SOL", "quote": "EUR"}),
    ("PEPE price", "market_quote", {"symbol": "PEPE"}),
    ("eth chart for the last month", "market_chart", {"symbol": "ETH", "span": "1M"}),
    ("bitcoin order book", "market_book", {"symbol": "BTC"}),
    ("recent btc trades", "market_trades", {"symbol": "BTC"}),
    ("biggest crypto gainers today", "market_overview", {"sort": "change", "limit": 15}),
    ("how is the crypto market", "market_overview", {"sort": "volume", "limit": 15}),
    ("eth gas", "market_chain", {}), ("bitcoin fees", "market_chain", {}), ("web3 stats", "market_chain", {}),
    ("add solana to my watchlist", "market_watchlist", {"action": "add", "symbol": "SOL"}),
    ("alert me when bitcoin goes above 90000", "market_alerts", {"action": "add", "symbol": "BTC", "price": 90000.0, "direction": "above"}),
    ("tell me if eth drops below 2k", "market_alerts", {"action": "add", "symbol": "ETH", "price": 2000.0, "direction": "below"}),
    ("delete alert 2", "market_alerts", {"action": "delete", "alert_id": 2}),
])
def test_market_questions_route_to_the_read_only_tools(text, tool, arguments):
    assert market_request(text) == {"tool": tool, "arguments": arguments}
    assert direct_plan(text, TOOL_NAMES)["tool"] == tool


@pytest.mark.parametrize("text", ["what is bitcoin", "how does ethereum work", "show me the link", "explain blockchain",
                                  "how much is a tesla", "the price is right", "should i buy bitcoin"])
def test_questions_about_crypto_are_not_price_lookups(text):
    assert market_request(text) is None


@pytest.mark.parametrize(("text", "action"), [("buy bitcoin", "purchase"), ("sell my bitcoin", "trade"),
                                              ("send 1 eth to ali", "money transfer"), ("swap my eth for usdc", "trade"),
                                              ("stake my solana", "trade"), ("cash out my crypto", "trade")])
def test_buying_selling_and_sending_crypto_is_always_stopped(text, action):
    assert money_request(text) and describe(text)["action"] == action and market_request(text) is None


@pytest.mark.parametrize("text", ["send a token to the api", "sell me on this plan", "trade ideas for a startup",
                                  "send the file to ali"])
def test_ordinary_requests_with_trade_words_are_not_money(text):
    assert not money_request(text)


def test_the_router_sends_prices_to_the_markets_and_purchases_to_the_guard():
    from app.intent.models import IntentKind
    from app.intent.router import IntentRouter
    from app.session import SessionState
    router = IntentRouter()
    price = router.route("what is the price of bitcoin", SessionState())
    assert price.intent == IntentKind.ACTION and price.entities["modality"] == "markets"
    buy = router.route("buy 0.1 btc", SessionState())
    assert buy.entities.get("modality") != "markets"


def test_replies_are_written_from_the_data(markets):
    def text(tool, output):
        return report([{"tool": tool, "result": {"success": True, "output": output}, "verification": {"passed": True}}])
    quote = text("market_quote", markets.quote("BTC"))
    assert quote.startswith("Bitcoin (BTC): $83,000.00 on Kraken · +3.75% since 00:00 UTC") and "Coinbase" in quote
    assert "HAYAT does not trade" in quote
    assert "1. BTC (Bitcoin) · $83,000.00 · +3.75%" in text("market_overview", markets.overview())
    assert "fees 5 sat/vB fastest" in text("market_chain", markets.chain())
    assert money(0.000004) == "$0.00000400" and money(1234.5, "EUR") == "€1,234.50"
    failed = report([{"tool": "market_quote", "result": {"success": False}, "verification": {"passed": False}}])
    assert failed is None


def test_tools_are_registered_read_only(markets, tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    register_market_tools(registry, markets)
    from app.markets import register_market_event_tools
    register_market_event_tools(registry, None, markets)  # world events need the intelligence manager; registered apart
    assert set(TOOL_NAMES) <= set(registry.names())
    result = registry.execute("market_quote", {"symbol": "eth"})
    assert result.success and result.output["symbol"] == "ETH"
    assert not registry.execute("market_quote", {"symbol": "ETH", "side": "buy"}).success  # no such argument exists


def test_the_trade_center_window_loads_and_never_offers_an_order_button(shared_tk, markets):
    from app.gui_markets import TradeCenter, amount
    from app.gui_theme import Palette
    from tests.conftest import close_window, open_window
    parent = open_window(shared_tk, "400x300-4000-4000")

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = parent, SimpleNamespace(markets=markets)
    center = TradeCenter(gui)
    try:
        center.geometry("1400x880-4000-4000")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not (center.rows and center.quote_view and center.chart_data and center.book_data):
            center.update()
            time.sleep(.02)
        assert list(center.rows) == ["BTC", "ETH", "SOL", "PEPE"] and center.price.cget("text") == "$83,000.00"
        assert center.chart.find_all() and center.book.find_all() and "Ethereum" in center.chain.cget("text")
        import tkinter as tk
        from tkinter import ttk
        buttons = [str(widget.cget("text")).lower() for widget in center._all(center) if isinstance(widget, (tk.Button, ttk.Button))]
        assert buttons and not any(word in label for label in buttons for word in ("buy", "sell", "order", "trade", "send", "swap"))
        assert "never buys, sells, swaps or sends crypto" in "".join(
            str(widget.cget("text")) for widget in center._all(center) if isinstance(widget, tk.Label))
        center.toggle_watch()
        assert center.watchlist == ["BTC"] and center.watch_button.cget("text") == "★ Watching"
        assert amount(0.00004512) == "0.00004512" and amount(1.5) == "1.5"
    finally:
        center.close()
        close_window(parent)
