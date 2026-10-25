"""Live scenarios (odds from a market's own past), world-trade chokepoints, and the replies and windows that show them."""
import json
import math
import random
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.market_scenarios import explain, judge, scenarios, situations, wilson
from app.markets import market_request, report
from app.trade_routes import CHOKEPOINTS, chokepoints, distance_km, report_lines

DAY = 86400


def walk(seed, days=500, drift=.0005, sigma=.025, start=100.0):
    random.seed(seed)
    price, rows, now = start, [], int(time.time()) // DAY * DAY
    for index in range(days):
        opened = price
        price *= math.exp(random.gauss(drift, sigma))
        rows.append({"time": now - DAY * (days - 1 - index), "open": opened, "high": max(opened, price), "low": min(opened, price),
                     "close": price, "volume": 10.0, "trades": 50})
    return rows


class Markets:
    def __init__(self, series, live=None):
        self.series, self.calls = series, []
        if live is not None:
            self.live = SimpleNamespace(snapshot=lambda names: {"prices": {name: {"price": live} for name in names}})

    def candles(self, symbol, interval, asset="auto"):
        self.calls.append((symbol, interval, asset))
        kind = "crypto" if symbol in ("BTC", "ETH") else "stock"
        return symbol, kind, self.series[symbol]

    def stocks(self, limit=1000):
        return {"rows": [{"symbol": "XLE", "change_today_pct": 1.5}, {"symbol": "SMH", "change_today_pct": -2.0}]}


def test_wilson_intervals_and_verdicts():
    low, high = wilson(50, 100)
    assert low == pytest.approx(.404, abs=.002) and high == pytest.approx(.596, abs=.002) and wilson(0, 0) == (0.0, 1.0)
    usual = {"up_pct": 50.0}
    assert judge({"samples": 10, "interval_pct": [60, 90]}, usual) == "too few past cases to tell"
    assert judge({"samples": 40, "interval_pct": [55, 75]}, usual).startswith("rose more often")
    assert judge({"samples": 40, "interval_pct": [20, 45]}, usual).startswith("rose less often")
    assert judge({"samples": 40, "interval_pct": [40, 60]}, usual).startswith("within the noise")


def test_situations_read_trend_volatility_and_rsi():
    rising = [100 * 1.01 ** index for index in range(200)]
    states = situations(rising)
    assert states[129] is None and states[-1]["trend"] == "up" and states[-1]["rsi"] == "overbought"
    falling = [100 * .99 ** index for index in range(200)]
    assert situations(falling)[-1]["trend"] == "down" and situations(falling)[-1]["rsi"] == "oversold"


def test_scenarios_use_the_live_price_and_measure_odds_against_the_usual():
    markets = Markets({"BTC": walk(1), "SPY": walk(2, sigma=.01)}, live=123.45)
    events = {"sectors": [{"sector": "Crypto", "events": [{"id": "e1", "title": "Exchange hack", "observed_at": "2026-10-11T00:00:00+00:00"}]}]}
    value = scenarios(markets, "BTC", "crypto", events)
    assert value["live"] and value["price"] == 123.45 and ("BTC", 1440, "crypto") in markets.calls
    week = value["next_week"]
    assert week["days"] == 7 and week["usual"]["samples"] > 40
    # The most specific match with 15 past cases, else trend alone; too few is said, not hidden.
    assert value["conditions"][0] == "trend" and set(value["conditions"]) <= {"trend", "volatility", "rsi"}
    if week["now"]["samples"] < 15:
        assert value["conditions"] == ["trend"] and week["verdict"] == "too few past cases to tell"
    assert value["paths"]["weak"]["price"] < value["paths"]["middle"]["price"] < value["paths"]["strong"]["price"]
    factors = {item["factor"]: item for item in value["factors"]}
    assert {"Trend", "Volatility", "RSI (14 days)", "Last day", "World events", "Moves with S&P 500"} <= set(factors)
    assert factors["World events"]["leaning"] == "risk" and "not proof of cause" in factors["World events"]["note"]
    assert "past cases" in value["summary"] and "never trades" in value["basis"]
    text = report([{"tool": "market_scenarios", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert "BTC scenarios (shares of past cases, not a forecast)" in text and "live" in text and "What might matter:" in text
    with pytest.raises(ValueError, match="160"):
        scenarios(Markets({"NEW": walk(3, days=90)}), "NEW", "stock")


def test_explain_hands_only_the_numbers_to_the_model():
    from tests.test_executive_invention import ScriptedModel
    value = scenarios(Markets({"BTC": walk(4), "SPY": walk(5)}), "BTC", "crypto")
    model = ScriptedModel("BTC rose about as often as usual in situations like this; the odds are no better than a coin flip.")
    assert "coin flip" in explain(model, value)
    sent = model.calls[0]
    assert "Never predict a price" in sent[0].content and json.loads(sent[1].content)["symbol"] == "BTC"
    with pytest.raises(RuntimeError):
        explain(None, value)


def test_scenario_and_trade_phrases_route():
    assert market_request("what are the chances bitcoin goes up this week")["tool"] == "market_scenarios"
    assert market_request("why is NVDA down today") == {"tool": "market_scenarios", "arguments": {"symbol": "NVDA"}}
    assert market_request("analyze apple stock")["arguments"] == {"symbol": "AAPL", "asset": "stock"}
    assert market_request("why is it down") is None and market_request("analyze my code") is None
    assert market_request("bitcoin forecast")["tool"] == "market_outlook"  # ranges stay with the outlook
    for text in ("how is world trade", "shipping chokepoints", "is shipping disrupted?"):
        assert market_request(text) == {"tool": "market_trade_routes", "arguments": {}}


class Manager:
    def __init__(self, events):
        self.events, self.boxes = events, []

    def dashboard(self, bbox=None, located_only=False, since_hours=None, limit=200, **_kwargs):
        self.boxes.append(bbox)
        west, south, east, north = bbox
        return {"events": [event for event in self.events if west <= event["longitude"] <= east and south <= event["latitude"] <= north]}


def test_chokepoints_count_the_reports_near_each_and_link_funds():
    manager = Manager([
        {"id": "1", "title": "Tanker struck near Hormuz", "severity": "high", "category": "conflict", "latitude": 26.3, "longitude": 56.4},
        {"id": "2", "title": "Port camera", "severity": "", "category": "cameras", "latitude": 26.5, "longitude": 56.2},
        {"id": "3", "title": "Storm off Taiwan", "severity": "medium", "category": "weather", "latitude": 24.0, "longitude": 120.0},
        {"id": "4", "title": "Far inland", "severity": "high", "category": "conflict", "latitude": 30.5, "longitude": 61.0}])
    value = chokepoints(manager, Markets({}), radius_km=350, hours=72)
    rows = {row["key"]: row for row in value["chokepoints"]}
    assert len(rows) == len(CHOKEPOINTS) and value["chokepoints"][0]["key"] == "hormuz"
    assert rows["hormuz"]["status"] == "alert" and rows["hormuz"]["event_count"] == 1  # the camera and the far event do not count
    assert rows["taiwan"]["status"] == "watch" and rows["panama"]["status"] == "calm"
    assert {"symbol": "XLE", "sector": "Energy (oil and gas)", "change_today_pct": 1.5} in rows["hormuz"]["markets"]
    assert all(len(box) == 4 and box[0] < box[2] and box[1] < box[3] for box in manager.boxes)
    assert distance_km(0, 0, 0, 1) == pytest.approx(111.2, abs=.2)
    text = "\n".join(report_lines(value))
    assert "Strait of Hormuz: ▲ alert · 1 event (e.g. Tanker struck near Hormuz)" in text and "not ship traffic" in text
    reply = report([{"tool": "market_trade_routes", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert reply.startswith("World trade chokepoints")


def test_the_browser_atlas_serves_the_trade_layer():
    from app.intelligence.map_server import IntelligenceMapServer
    manager = Manager([{"id": "1", "title": "Ship attacked", "severity": "high", "category": "conflict", "latitude": 12.7, "longitude": 43.4}])
    manager.markets = Markets({})
    server = IntelligenceMapServer(manager)
    try:
        with urllib.request.urlopen(server.url + "trade", timeout=10) as response:
            value = json.loads(response.read())
        assert value["chokepoints"][0]["key"] == "bab" and value["chokepoints"][0]["markets"][0]["change_today_pct"] == 1.5
    finally:
        server.close()
    root = Path(__file__).parents[1] / "app" / "assets" / "intelligence-map"
    assert 'id="trade"' in (root / "index.html").read_text(encoding="utf-8")
    script = (root / "map.js").read_text(encoding="utf-8")
    assert "async function refreshTrade" in script and "tradePanel" in script


def test_the_trade_center_shows_live_scenarios(shared_tk, tmp_path):
    from app.gui_markets import TradeCenter
    from app.gui_theme import Palette
    from app.markets import MarketData
    from tests.test_markets_live import StockFetcher

    class Live(MarketData):
        def candles(self, symbol, interval=60, asset="auto"):
            if interval == 1440:
                return symbol, "crypto" if symbol == "BTC" else "stock", walk(7) if symbol == "BTC" else walk(8)
            return super().candles(symbol, interval, asset)
    markets = Live(StockFetcher(), tmp_path / "markets.db")

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(markets=markets, intelligence=None)
    window = TradeCenter(gui)

    def wait(ready, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline and not ready():
            shared_tk.update()
            time.sleep(.02)
        return ready()
    try:
        window.geometry("1400x880-4000-4000")
        window.side_tabs.select(3)
        assert wait(lambda: window.scenario_data)
        shared_tk.update()
        window.draw_odds()
        window.draw_paths()
        assert "Now: trend" in window.scenario_head.cget("text") and window.factor_tree.get_children()
        assert window.odds.find_all() and window.paths.find_all()
        window.factor_tree.selection_set("0")
        window.factor_selected()
        assert window.scenario_note.cget("text").startswith("Trend:")
        window.explain_scenarios()
        assert "not available" in window.scenario_note.cget("text")  # no local model in this session
        window._live_price = window.scenario_data["price"] * 1.01
        window.draw_paths()  # the live marker moves without recomputing
        assert any(window.paths.type(item) == "oval" for item in window.paths.find_all())
    finally:
        window.close()
