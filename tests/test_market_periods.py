"""Possibilities by time period (1 hour to 1 year), compared across markets, in chat and in the Trade Center's Periods tab."""
import math
import random
import time
from types import SimpleNamespace

import pytest

from app.market_periods import (LABELS, MIN_PERIODS, compare, detail_lines, measure, nice_step, period_key, periods, quantile,
                                record_verdict, trim)
from app.markets import market_request, register_market_tools, report

HOUR = 3600


def walk(seed, count=721, sigma=.01, drift=0.0, seconds=86400, start=100.0, calm_until=0, calm=.003):
    random.seed(seed)
    price, rows, now = start, [], int(time.time()) // seconds * seconds
    for index in range(count):
        opened = price
        price *= math.exp(random.gauss(drift, calm if index < calm_until else sigma))
        rows.append({"time": now - seconds * (count - 1 - index), "open": opened, "high": max(opened, price) * 1.002,
                     "low": min(opened, price) * .998, "close": price, "volume": 5.0, "trades": 40})
    return rows


class Markets:
    """Candles per (symbol, interval); `missing` symbols are unknown to Kraken."""

    def __init__(self, series, live=None, path=None, watch=(), calendar=None):
        self.series, self.calls, self.path, self.watch = series, [], path, list(watch)
        if live is not None:
            self.live = SimpleNamespace(snapshot=lambda names: {"prices": {name: {"price": live} for name in names}})
        if calendar is not None:
            self.calendar = calendar

    def candles(self, symbol, interval, asset="auto"):
        self.calls.append((symbol, interval, asset))
        if symbol not in self.series:
            raise ValueError(f"Kraken lists no market for {symbol}.")
        kind = "crypto" if symbol in ("BTC", "ETH") else "stock"
        return symbol, kind, self.series[symbol][interval]

    def watchlist(self, action="list"):
        return {"symbols": self.watch}


def market(seed, weekly=680, drift=0.0):
    return {60: walk(seed, sigma=.004, seconds=HOUR), 1440: walk(seed + 1, drift=drift), 10080: walk(seed + 2, count=weekly, sigma=.06,
                                                                                                         seconds=7 * 86400)}


def test_period_words_map_to_the_nearest_measured_period():
    assert [period_key(text) for text in ("in the next 3 months", "in a year", "half a year", "the next 4 hours", "long term",
                                          "next 2 weeks", "tomorrow", "this month", "a quarter", "12 months", "an hour", "hello")] == \
        ["3m", "1y", "6m", "4h", "1y", "1w", "1d", "1m", "3m", "1y", "1h", None]
    assert nice_step(3.1) == 2.5 and nice_step(0) == .1 and nice_step(70) == 75
    assert quantile([1.0, 2.0, 3.0, 4.0], .5) == 2.5 and quantile([], .5) == 0.0


def test_a_period_is_measured_from_separate_periods_with_its_possibilities():
    rows = walk(1, drift=.001)
    value = measure(rows, 7, "1 week")
    chance = value["chance_higher"]
    # Overlapping weekly windows are not independent: the interval counts 102 separate weeks, not 713 windows.
    assert value["available"] and chance["separate_periods"] == 720 // 7
    assert chance["interval_pct"][0] < chance["up_pct"] < chance["interval_pct"][1]
    levels = value["range"]
    assert levels["p05"]["price"] < levels["p25"]["price"] < levels["p50"]["price"] < levels["p75"]["price"] < levels["p95"]["price"]
    shares = {item["key"]: item["share_pct"] for item in value["possibilities"]}
    assert sum(shares.values()) == pytest.approx(100, abs=.3) and list(shares) == ["big_fall", "fall", "flat", "rise", "big_rise"]
    # Touching a step on the way happens at least as often as ending beyond it.
    assert value["touch"]["rise_pct"] >= shares["rise"] + shares["big_rise"] - .1
    assert value["touch"]["fall_pct"] >= shares["fall"] + shares["big_fall"] - .1
    assert value["like_now"]["direction"] in ("rise", "fall") and value["like_now"]["verdict"]
    record = value["track_record"]
    assert record["tested"] >= 8 and 75 <= record["inside_wide_pct"] <= 100
    assert any("Possibilities:" in line for line in detail_lines({**value, "label": "1 week"}))


def test_too_little_history_is_said_not_guessed():
    value = measure(walk(2, count=60), 7, "1 week")
    assert not value["available"] and value["separate_periods"] == 8 and "too little history" in value["reason"]
    assert str(MIN_PERIODS) in value["reason"]


def test_the_range_method_that_held_better_on_its_own_past_is_used():
    # Calm for 450 days, then four times the swings: ranges from all past days are too narrow; scaled ones keep up.
    rows = walk(3, sigma=.024, calm_until=450, calm=.006)
    value = measure(rows, 1, "1 day")
    assert value["method"] == "scaled to today's volatility"
    other = value["track_record_other"]
    assert other["inside_wide_pct"] < value["track_record"]["inside_wide_pct"] and "too narrow" in other["verdict"]
    assert record_verdict(5, 50, 90) == "too few past tests to judge these ranges"
    assert record_verdict(40, 52, 88) == "the ranges held about as often as stated"
    assert record_verdict(40, 70, 99).startswith("the ranges were wider")


def test_untraded_candles_before_a_market_opened_are_dropped():
    rows = [{"time": index, "close": 10.0, "high": 10.0, "low": 10.0, "trades": 0} for index in range(5)] + walk(4, count=10)
    assert len(trim(rows)) == 10 and trim([{"close": 0.0, "trades": 3}]) == []


def test_every_period_from_the_live_price_with_scheduled_events_inside():
    calendar = SimpleNamespace(upcoming=lambda days: {"events": [
        {"kind": "fed", "title": "Fed interest-rate decision (FOMC)", "days_until": 10},
        {"kind": "earnings", "title": "Apple earnings", "symbol": "AAPL", "days_until": 3}]})
    markets = Markets({"BTC": market(10), "AAPL": market(20, weekly=61)}, live=123.0, calendar=calendar)
    value = periods(markets, "BTC", "crypto", "1m")
    assert value["live"] and value["price"] == 123.0 and value["focus"] == "1m"
    assert [row["key"] for row in value["periods"]] == list(LABELS) and all(row["available"] for row in value["periods"])
    assert {interval for _symbol, interval, _asset in markets.calls} == {60, 1440, 10080}
    rows = {row["key"]: row for row in value["periods"]}
    assert rows["1w"]["scheduled"] == [] and rows["1m"]["scheduled"][0]["title"].startswith("Fed")  # 10 days: inside a month only
    assert "scheduled" not in rows["1h"]
    assert rows["1h"]["candles"] == "hourly" and rows["1y"]["candles"] == "weekly"
    assert value["summary"].startswith("BTC: higher after 1 day in") and "1 year" in value["summary"]
    text = report([{"tool": "market_periods", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    # Since 2026-10-24 a plain-words line leads; the table header follows it.
    lead, header = text.split("\n")[:2]
    assert lead.startswith("💬 In plain words: nobody can know. Over past 1 month periods BTC ended higher")
    assert header.startswith("BTC by time period (how often each outcome happened before; not a forecast)") and " live" in text
    assert "Over 1 month in detail:" in text and "Possibilities:" in text and "Scheduled inside it: Fed" in text
    assert "never trades" in text
    # A stock with a year of weekly history: the long periods say why they are missing.
    stock = periods(markets, "AAPL", "stock", "1y")
    long = {row["key"]: row for row in stock["periods"]}
    assert not long["1y"]["available"] and "too little history" in long["1y"]["reason"] and "1 year" in stock["missing"]
    assert stock["longest_measured"] == "6 months" or not long["6m"]["available"]
    assert long["1m"]["scheduled"][0]["title"] == "Apple earnings"
    text = report([{"tool": "market_periods", "result": {"success": True, "output": stock}, "verification": {"passed": True}}])
    assert "Too little history for 1 year in this market." in text
    with pytest.raises(ValueError, match="Choose a period"):
        periods(markets, "BTC", "crypto", "2y")


def test_comparing_one_period_across_markets_ranks_and_keeps_unknown_ones():
    markets = Markets({"BTC": market(30, drift=.003), "ETH": market(40, drift=-.003)})
    value = compare(markets, ["ETH", "BTC", "NOPE"], "1w")
    assert value["origin"] == "given" and [row["symbol"] for row in value["rows"]] == ["BTC", "ETH", "NOPE"]
    assert value["rows"][0]["chance_higher"]["up_pct"] > value["rows"][1]["chance_higher"]["up_pct"]
    assert not value["rows"][2]["available"] and "no market" in value["rows"][2]["reason"]
    assert all(call[1] == 1440 for call in markets.calls)
    text = report([{"tool": "market_periods_compare", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert text.startswith("Chance higher after 1 week, across markets (as asked):") and "noise, not an edge" in text
    watched = compare(Markets({"BTC": market(30)}, path="db", watch=["BTC"]), None, "1m")
    assert watched["origin"] == "watchlist" and [row["symbol"] for row in watched["rows"]] == ["BTC"]
    standard = compare(Markets({"BTC": market(30)}), None, "1d")
    assert standard["origin"] == "default" and len(standard["rows"]) == 8


@pytest.mark.parametrize("text, expected", [
    ("what are the chances bitcoin goes up in a month", {"symbol": "BTC", "period": "1m", "asset": "crypto"}),
    ("will bitcoin go up next year", {"symbol": "BTC", "period": "1y", "asset": "crypto"}),
    ("where will tesla stock be in 6 months", {"symbol": "TSLA", "period": "6m", "asset": "stock"}),
    ("how much will ETH be worth in 3 months", {"symbol": "ETH", "period": "3m", "asset": "crypto"}),
    ("bitcoin prediction for next month", {"symbol": "BTC", "period": "1m", "asset": "crypto"}),
    ("what are the odds of bitcoin rising in the next 4 hours", {"symbol": "BTC", "period": "4h", "asset": "crypto"}),
    ("bitcoin long term outlook", {"symbol": "BTC", "period": "1y", "asset": "crypto"}),
    ("bitcoin possibilities", {"symbol": "BTC", "period": "all", "asset": "crypto"}),
    ("apple stock by time period", {"symbol": "AAPL", "period": "all", "asset": "stock"}),
    ("possibilities for NVDA", {"symbol": "NVDA", "period": "all"}),
    ("nvidia stock possibilities over a year", {"symbol": "NVDA", "period": "1y", "asset": "stock"}),
])
def test_period_questions_route_to_the_periods_tool(text, expected):
    assert market_request(text) == {"tool": "market_periods", "arguments": expected}


def test_comparisons_route_and_ordinary_sentences_do_not():
    assert market_request("compare btc, eth and spy over a year") == {"tool": "market_periods_compare",
                                                                      "arguments": {"period": "1y", "symbols": ["BTC", "ETH", "SPY"]}}
    assert market_request("compare my watchlist") == {"tool": "market_periods_compare", "arguments": {"period": "1m"}}
    assert market_request("which coin has the best odds this month")["tool"] == "market_periods_compare"
    assert market_request("compare apple and microsoft stock")["arguments"]["symbols"] == ["AAPL", "MSFT"]
    for text in ("compare apples and oranges", "compare two files", "compare apple and microsoft revenue", "compare AI and ML",
                 "compare apple and microsoft", "will it go up next month"):
        assert market_request(text) is None, text
    # A day or a week stays with the scenarios and the outlook.
    assert market_request("what are the chances bitcoin goes up this week")["tool"] == "market_scenarios"
    assert market_request("will bitcoin go up tomorrow")["tool"] == "market_outlook"
    assert market_request("bitcoin forecast")["tool"] == "market_outlook"


def test_the_period_tools_are_registered_read_only():
    found = {}

    class Registry:
        def register(self, name, description, schema, risk, category, handler):
            found[name] = (description, schema, risk, category)
    from app.markets import MarketData
    register_market_tools(Registry(), MarketData(None))
    description, schema, risk, category = found["market_periods"]
    assert schema["properties"]["period"]["enum"] == [*LABELS, "all"] and (risk, category) == ("low", "public_api_get")
    assert "never a forecast" in description and "cannot trade" in description
    assert found["market_periods_compare"][1]["properties"]["period"]["enum"] == list(LABELS)


def test_the_trade_center_periods_tab(shared_tk, tmp_path):
    from app.gui_markets import TradeCenter
    from app.gui_theme import Palette
    from app.markets import MarketData
    from tests.test_markets_live import StockFetcher
    markets = MarketData(StockFetcher(), tmp_path / "markets.db")
    series = market(50)
    markets.candles = lambda symbol, interval, asset="auto": (symbol if symbol != "BTC" else "BTC", "crypto", series[interval])

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(markets=markets, intelligence=None)
    window = TradeCenter(gui)

    def wait(ready, seconds=15):
        deadline = time.time() + seconds
        while time.time() < deadline and not ready():
            shared_tk.update()
            time.sleep(.02)
        return ready()
    try:
        assert [window.side_tabs.tab(tab, "text") for tab in window.side_tabs.tabs()] == \
            ["Book", "Outlook", "Events", "Scenarios", "Calendar", "Periods"]
        panel = window.periods_panel
        window.side_tabs.select(5)
        assert wait(lambda: panel.data is not None)
        tree = panel.tree
        assert list(tree.get_children()) == list(LABELS) and tree.item("1d", "values")[0] == "1 day"
        assert tree.item("1m", "values")[1].endswith("%") and "…" in tree.item("1m", "values")[3]
        assert tree.selection() == ("1m",) and "Possibilities:" in panel.said() and panel.said().startswith("1 month: higher in")
        tree.selection_set("1h")
        panel.row_selected()
        assert panel.selected == "1h" and panel.said().startswith("1 hour:")
        assert panel.cone.find_all()  # the cone is drawn
        panel.compare()
        assert wait(lambda: panel.said().startswith("Chance higher after 1 hour"))
        panel.show(panel.data)  # the two-minute refresh leaves the comparison on show
        for _ in range(5):
            shared_tk.update()
        assert panel.said().startswith("Chance higher after 1 hour")
        tree.selection_set("1d")
        panel.row_selected()
        assert panel.said().startswith("1 day:")
    finally:
        window.close()


def test_calm_or_volatile_times_and_the_odds_test_are_measured_and_said_plainly():
    from app.market_periods import calm_or_volatile, odds_test
    value = measure(walk(5, sigma=.024, calm_until=650, calm=.006), 1, "1 day")
    calm = value["volatility"]
    # Four times the usual swings over the last 70 days: today is volatile, and those periods moved wider.
    assert calm["state"] == "volatile" and calm["now_vs_usual"] > 1.3 and calm["width_ratio"] > 1.5
    assert calm["verdict"].startswith(("within the noise", "rose", "too few"))
    test = value["odds_test"]
    assert test["tested"] >= 8 and {"brier_like_now", "brier_plain"} <= set(test)
    assert test["verdict"] in ("the like-now odds called it better than the plain odds", "the like-now odds were no better than the plain odds",
                               "the like-now odds did worse than the plain odds: ignore them")
    lines = "\n".join(detail_lines({**value, "label": "1 day"}))
    assert "In volatile times like now" in lines and "Odds test (" in lines and "lower is better" in lines
    assert odds_test([.01] * 20, [100.0] * 21, 1)["verdict"] == "too few past tests to judge the like-now odds"
    assert calm_or_volatile([.01] * 10, [None] * 10, 1, {"up_pct": 50}, "1 day") is None


def test_explain_hands_only_the_period_numbers_to_the_model():
    import json

    from app.market_periods import explain
    from tests.test_executive_invention import ScriptedModel
    value = periods(Markets({"BTC": market(60)}), "BTC", "crypto")
    model = ScriptedModel("Over a day Bitcoin rose about as often as it fell; over a year it rose more often, but that mirrors its past.")
    assert "mirrors its past" in explain(model, value)
    system, user = model.calls[0]
    assert "Never predict a price" in system.content and json.loads(user.content)["symbol"] == "BTC"
    with pytest.raises(RuntimeError):
        explain(None, value)


def test_plain_words_call_a_coin_flip_a_coin_flip():
    from app.market_periods import plain_odds

    assert plain_odds(52.7, (33.8, 70.9)).startswith("about a coin flip")  # the live month: interval spans 50%
    assert plain_odds(66.0, (58.0, 74.0)) == "more often up than down in the past"
    assert plain_odds(35.0, (28.0, 42.0)) == "more often down than up in the past"
