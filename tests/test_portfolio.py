"""Portfolio and net worth in USD: holdings the owner keeps at their own bank, broker and exchange, valued live, with
watch-only crypto addresses read from the chain. Nothing here holds, trades or keeps a key."""
import json
import sqlite3
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.agent.planner import Planner
from app.intent.router import IntentRouter
from app.portfolio import Portfolio, portfolio_request, refuse_keys, register_portfolio_tools, report, report_lines
from app.safety.money_requests import money_request as money_guard
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger
from app.tools.registry import ToolRegistry

BTC_ADDRESS = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
ETH_ADDRESS = "0x" + "ab" * 20


class Markets:
    prices = {"BTC": 100_000.0, "ETH": 4_000.0, "AAPL": 220.0}

    def __init__(self):
        self.calls = []

    def quote(self, symbol, quote="USD", asset="auto"):
        self.calls.append((symbol, asset))
        if symbol not in self.prices:
            raise ValueError(f"Kraken lists no pair for {symbol}.")
        return {"price": self.prices[symbol], "change_today_pct": 1.5}


class Chain:
    def lookup(self, query, chain=""):
        if query == BTC_ADDRESS:
            return {"balance": 0.5, "unit": "BTC"}
        return {"balance": 2.0, "unit": "ETH", "tokens_usd": 100.0}


class Rates:
    def __init__(self, fail=False):
        self.fail, self.urls = fail, []

    def fetch(self, url, max_bytes, types):
        self.urls.append(url)
        if self.fail:
            raise OSError("offline")
        rates = {"INR": 95.28, "EUR": 0.89} if "frankfurter" in url else {"USD": 1, "INR": 95.3, "AED": 3.6725}
        return SimpleNamespace(content=json.dumps({"rates": rates}).encode())


@pytest.fixture
def book(tmp_path):
    return Portfolio(tmp_path / "portfolio.db", Markets(), Chain(), Rates())


def test_holdings_are_valued_in_usd_with_average_cost(book):
    book.record("cash", "USD", "2500", "set", account="Chase")
    book.record("cash", "INR", "100000", "set", account="HDFC savings")
    book.record("crypto", "BTC", "0.25", "set", "58000", "Coinbase")
    book.record("stock", "AAPL", "10", "buy", "180", "Schwab")
    book.record("stock", "AAPL", "10", "buy", "200", "Schwab")
    sold = book.record("stock", "AAPL", "5", "sell", "210", "Schwab")
    assert sold["now_held"] == "15" and "HAYAT holds nothing" in sold["note"]
    view = book.view()
    rows = {row["symbol"]: row for row in view["holdings"]}
    assert rows["USD"]["value_usd"] == 2500 and rows["INR"]["value_usd"] == round(100000 / 95.28, 2)
    assert rows["BTC"]["value_usd"] == 25000 and rows["BTC"]["gain_usd"] == 10500
    aapl = rows["AAPL"]
    assert (aapl["value_usd"], aapl["cost_usd"], aapl["gain_usd"], aapl["realised_usd"]) == (3300, 2850, 450, 100)
    assert view["net_worth_usd"] == round(2500 + 100000 / 95.28 + 25000 + 3300, 2)
    assert view["by_kind"]["crypto"] == 25000 and round(sum(view["allocation"].values())) == 100
    assert view["by_account"]["Schwab"] == 3300 and view["realised_usd"] == 100
    assert view["holdings"][0]["symbol"] == "BTC"  # largest first
    assert "European Central Bank" in view["fx_source"] and "HAYAT holds nothing" in view["basis"]
    lines = "\n".join(report_lines(view))
    assert lines.startswith("Net worth $") and "#" in lines and "+15.8%" in lines


def test_mistakes_and_secrets_are_refused(book):
    with pytest.raises(ValueError, match="more than"):
        book.record("crypto", "ETH", "1", "sell", "4000")
    with pytest.raises(ValueError, match="future"):
        book.record("stock", "AAPL", "1", "buy", "200", on_day=(date.today() + timedelta(days=3)).isoformat())
    with pytest.raises(ValueError, match="cash, stock or crypto"):
        book.record("bonds", "X", "1")
    with pytest.raises(ValueError, match="not its number"):
        book.record("cash", "USD", "100", "set", account="Chase 123456789")
    for secret in ("a" * 64, "abandon " * 11 + "about", "my seed is lion", "private key 5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ"):
        with pytest.raises(ValueError, match="never keeps"):
            refuse_keys(secret)
        with pytest.raises(ValueError):
            book.watch_address(secret)
    with pytest.raises(ValueError, match="public Bitcoin"):
        book.watch_address("not-an-address-at-all-really")
    assert book.view()["holdings"] == []


def test_watched_addresses_are_read_from_the_chain(book):
    watched = book.watch_address(BTC_ADDRESS, "Cold wallet")
    assert watched["symbol"] == "BTC" and "no key" in watched["note"]
    book.watch_address(ETH_ADDRESS)
    with pytest.raises(ValueError, match="Already watching"):
        book.watch_address(BTC_ADDRESS)
    rows = {row["symbol"]: row for row in book.view()["holdings"]}
    assert rows["BTC"]["quantity"] == "0.5" and rows["BTC"]["value_usd"] == 50000 and rows["BTC"]["origin"] == "read from the blockchain"
    assert rows["ETH"]["value_usd"] == 8100 and "priced tokens" in rows["ETH"]["origin"]
    book.remove(watched["holding_id"])
    assert [row["symbol"] for row in book.view()["holdings"]] == ["ETH"]


def test_missing_prices_and_rates_are_shown_not_hidden(tmp_path):
    book = Portfolio(tmp_path / "p.db", Markets(), Chain(), Rates(fail=True))
    book.record("cash", "INR", "5000", "set")
    book.record("stock", "ZZZZ", "3", "buy", "10")
    view = book.view()
    assert view["fx_source"] == "no exchange-rate source reachable" and set(view["unpriced"]) == {"INR", "ZZZZ"}
    assert view["net_worth_usd"] == 0 and "no pair" in next(row for row in view["holdings"] if row["symbol"] == "ZZZZ")["note"]


def test_rates_are_cached_and_the_second_source_fills_gaps(book):
    rates, source = book.fx_rates()
    assert rates["AED"] == 3.6725 and rates["INR"] == 95.28 and "ExchangeRate-API" in source
    book.fx_rates()
    assert len(book.fetcher.urls) == 2


def test_net_worth_history_and_the_business_wallet_in_usd(tmp_path):
    ledger = BusinessLedger(tmp_path / "business.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
    ledger.set_profile(business_name="Studio", default_currency="INR")
    from app.wallet_money import WalletMoney
    WalletMoney(ledger).add_money("9528")
    book = Portfolio(tmp_path / "portfolio.db", Markets(), Chain(), Rates(), ledger)
    book.record("cash", "USD", "1000", "set")
    with sqlite3.connect(book.path) as db:
        db.execute("INSERT INTO snapshots VALUES (?, '800.00', '{}')", ((date.today() - timedelta(days=5)).isoformat(),))
    view = book.view()
    assert [point["net_worth_usd"] for point in view["history"]] == [800.0, 1000.0]
    assert view["business_wallet"]["total_usd"] == 100.0 and view["net_worth_usd"] == 1000
    lines = "\n".join(report_lines(view))
    assert "→ $1,000.00" in lines and "Business wallet ≈ $100.00" in lines and "not added above" in lines


@pytest.mark.parametrize("text, tool, arguments", [
    ("show my portfolio", "portfolio_view", {}),
    ("what is my net worth", "portfolio_view", {}),
    ("how much is my crypto worth", "portfolio_view", {}),
    ("i hold 0.25 btc at 58000 on coinbase", "portfolio_record",
     {"kind": "crypto", "symbol": "BTC", "quantity": "0.25", "action": "set", "price_usd": "58000", "account": "coinbase"}),
    ("add 10 AAPL shares at 180", "portfolio_record", {"kind": "stock", "symbol": "AAPL", "quantity": "10", "action": "buy", "price_usd": "180"}),
    ("i sold 2 AAPL at 210", "portfolio_record", {"kind": "stock", "symbol": "AAPL", "quantity": "2", "action": "sell", "price_usd": "210"}),
    ("i have 2500 usd in my chase account", "portfolio_record",
     {"kind": "cash", "symbol": "USD", "quantity": "2500", "action": "set", "account": "chase account"}),
    (f"add address {BTC_ADDRESS} to my portfolio", "portfolio_watch_address", {"address": BTC_ADDRESS}),
    (f"watch my bitcoin address {ETH_ADDRESS} in my portfolio", "portfolio_watch_address", {"address": ETH_ADDRESS}),
    ("remove holding 3", "portfolio_remove", {"holding_id": 3}),
])
def test_portfolio_phrases_route_and_are_not_money_moves(tmp_path, text, tool, arguments):
    assert portfolio_request(text) == {"tool": tool, "arguments": arguments}
    assert not money_guard(text)
    assert IntentRouter().route(text).entities.get("modality") == "portfolio"
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    register_portfolio_tools(registry, Portfolio(tmp_path / "p.db"))
    step = Planner(registry).create_plan(text)[0]
    assert (step.tool, step.arguments) == (tool, arguments)


def test_a_plain_watch_address_stays_with_the_web3_explorer():
    assert portfolio_request(f"watch address {BTC_ADDRESS}") is None


def test_buying_and_selling_for_real_is_still_stopped():
    for text in ("buy 1 btc", "sell my bitcoin", "buy 10 apple shares for me", "transfer my bitcoin to ali"):
        assert portfolio_request(text) is None and money_guard(text), text


def test_the_reply_comes_from_a_verified_tool_result(book):
    value = book.record("crypto", "BTC", "1", "buy", "50000")
    history = [{"tool": "portfolio_record", "result": {"success": True, "output": value}, "verification": {"passed": True}}]
    assert report(history).startswith("Recorded: buy 1 BTC")
    assert report([{"tool": "portfolio_record", "result": {"success": False}, "verification": {"passed": False}}]) is None


def test_the_portfolio_window(shared_tk, book):
    from app.gui_portfolio import PortfolioWindow
    from app.gui_theme import Palette
    book.record("cash", "USD", "1000", "set", account="Chase")
    book.record("crypto", "BTC", "0.1", "buy", "60000", "Coinbase")

    class Gui(Palette):
        pass
    gui = Gui()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(portfolio=book)
    window = PortfolioWindow(gui)
    try:
        for _ in range(60):
            shared_tk.update()
            if window.value:
                break
            time.sleep(.05)
        assert window.net.cget("text") == "$11,000.00" and len(window.tree.get_children()) == 2
        assert window.allocation.find_all()
        window.fields["symbol"].set("ETH")
        window.fields["kind"].set("crypto")
        window.fields["quantity"].set("1")
        window.fields["price_usd"].set("3000")
        window.record()
        for _ in range(80):
            shared_tk.update()
            if len(window.tree.get_children()) == 3:
                break
            time.sleep(.05)
        assert len(window.tree.get_children()) == 3 and "HAYAT holds nothing" in window.message.get()
    finally:
        window.close()
