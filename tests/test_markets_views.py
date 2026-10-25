"""Trade Center's advanced views: ticker strip, heatmap and world-event markers."""
import time

import pytest

from app.gui_markets_views import (TICKER, Heatmap, TickerStrip, event_time, events_for, heat_colour, heat_groups, squarify)
from app.gui_theme import Palette


def test_squarify_fills_the_area_in_proportion():
    weights = [5.0, 3.0, 1.0, 1.0]
    rects = squarify(weights, 0, 0, 400, 200)
    assert len(rects) == 4
    areas = [w * h for _x, _y, w, h in rects]
    assert sum(areas) == pytest.approx(400 * 200)
    for area, weight in zip(areas, weights):
        assert area / (400 * 200) == pytest.approx(weight / sum(weights), rel=.01)
    assert all(0 <= x and 0 <= y and x + w <= 400.01 and y + h <= 200.01 for x, y, w, h in rects)
    assert squarify([], 0, 0, 10, 10) == [] and squarify([1], 2, 3, 4, 5) == [(2, 3, 4, 5)]


def test_colours_grow_stronger_with_the_move():
    background = "#101C29"
    assert heat_colour(None, background) != heat_colour(0.0, background)
    assert heat_colour(3.0, background) != heat_colour(.3, background) != heat_colour(-.3, background)
    assert heat_colour(9.0, background) == heat_colour(3.0, background)  # full colour from 3 %


def rows(symbols, change=1.0, volume=1e6):
    return {symbol: {"symbol": symbol, "price": 10.0, "change_today_pct": change, "volume_24h_quote": volume, "name": symbol}
            for symbol in symbols}


def test_heat_groups_put_each_market_once_in_its_group():
    stocks = rows(["AAPL", "NVDA", "SPY", "XLE", "XOP", "SMH", "GLD", "COIN"])
    crypto = rows(["BTC", "ETH", "SOL"])
    groups = heat_groups(stocks, crypto)
    names = [group["group"] for group in groups]
    assert names[:3] == ["Crypto", "Big tech", "US market"] and "Energy (oil and gas)" in names
    symbols = [tile["symbol"] for group in groups for tile in group["tiles"]]
    assert len(symbols) == len(set(symbols))  # NVDA is in Big tech, not again under Semiconductors
    assert {"BTC", "AAPL", "SPY", "XLE", "GLD", "COIN"} <= set(symbols) and "ETH" in symbols


def test_events_for_a_market_come_from_the_industries_it_belongs_to():
    data = {"sectors": [{"sector": "Energy (oil and gas)", "events": [{"id": "e1", "title": "Strikes on tankers",
                                                                        "observed_at": "2026-10-11T08:00:00+00:00"}]},
                        {"sector": "Semiconductors", "events": [{"id": "e2", "title": "Quake in Taiwan", "observed_at": "x"}]}]}
    assert [event["id"] for event in events_for(data, "XLE")] == ["e1"] and events_for(data, "BTC") == []
    assert events_for(None, "XLE") == []
    assert event_time({"observed_at": "2026-10-11T08:00:00+00:00"}) and event_time({"observed_at": "bad"}) is None


def test_the_ticker_strip_updates_flashes_and_opens_a_market(shared_tk):
    from tests.conftest import close_window, open_window
    window = open_window(shared_tk, "1200x120-4000-4000")
    opened = []
    strip = TickerStrip(window, Palette(), lambda symbol, asset: opened.append((symbol, asset)))
    strip.pack(fill="x")
    window.update()
    try:
        assert TickerStrip.stream_names()[:4] == ["BTC/USD", "ETH/USD", "SOL/USD", "SPYx/USD"] and len(strip.cells) == len(TICKER)
        strip.update_from({"prices": {"BTC/USD": {"price": 83000.0, "change_pct": .5}}})
        strip.update_from({"prices": {"BTC/USD": {"price": 83010.0, "change_pct": .51}}})
        assert strip.cells["BTC"]["price"].cget("text") == "$83,010.00" and strip.cells["BTC"]["price"].cget("fg") == "#4ADE80"
        assert strip.cells["BTC"]["change"].cget("text") == "+0.51% 24 h"
        strip.cells["NVDA"]["price"].event_generate("<Button-1>", x=2, y=2)
        window.update()
        assert opened == [("NVDA", "stock")]
    finally:
        close_window(window)


def test_the_heatmap_draws_tiles_and_a_click_opens_one(shared_tk):
    from tests.conftest import close_window, open_window
    window = open_window(shared_tk, "800x500-4000-4000")
    opened = []
    heatmap = Heatmap(window, Palette(), lambda symbol, asset: opened.append((symbol, asset)))
    heatmap.pack(fill="both", expand=True)
    try:
        heatmap.show(heat_groups(rows(["AAPL", "SPY", "XLE"]), rows(["BTC", "ETH"], change=-2.0)))
        window.update()
        heatmap.draw()
        assert len(heatmap.tiles) == 5
        left, top, right, bottom, tile = next(item for item in heatmap.tiles if item[4]["symbol"] == "BTC")
        heatmap.click(type("Event", (), {"x": (left + right) / 2, "y": (top + bottom) / 2})())
        assert opened == [("BTC", "crypto")]
        heatmap.hover(type("Event", (), {"x": (left + right) / 2, "y": (top + bottom) / 2})())
        assert heatmap.find_withtag("hover")
    finally:
        close_window(window)


def test_the_trade_center_switches_to_the_heatmap_and_marks_events_on_the_chart(shared_tk, tmp_path):
    from types import SimpleNamespace
    from app.gui_markets import TradeCenter
    from app.markets import MarketData
    from tests.test_markets import Fetcher
    markets = MarketData(Fetcher(), tmp_path / "markets.db")

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(markets=markets, intelligence=None)
    window = TradeCenter(gui)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not window.chart_data:
            shared_tk.update()
            time.sleep(.02)
        candles = window.chart_data["candles"]
        moment = candles[len(candles) // 2]["time"]
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(moment))
        window.show_events({"sectors": [{"sector": "Crypto", "why": "w", "markets": [],
                                         "events": [{"id": "x1", "title": "Exchange hack", "severity": "high", "observed_at": stamp}]}],
                            "hours": 48, "benchmark": None})
        assert len(window._event_marks) == 1
        x, _event = window._event_marks[0]
        window.chart_hover(type("Event", (), {"x": x, "y": 10})())
        assert any("Exchange hack" in window.chart.itemcget(item, "text") for item in window.chart.find_withtag("hover")
                   if window.chart.type(item) == "text")
        window.center_view.set("heatmap")
        window.switch_center()
        shared_tk.update()
        assert window.heatmap.winfo_ismapped() and not window.chart.winfo_ismapped()
        window.center_view.set("chart")
        window.switch_center()
        shared_tk.update()
        assert window.chart.winfo_ismapped()
    finally:
        window.close()


# ------------------------------------------------------------------ alerts, overlays, live candle, keys
def test_moving_average_waits_for_its_period():
    from app.gui_markets import moving_average
    assert moving_average([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0] and moving_average([1.0], 20) == [None]


def test_checking_alerts_reports_the_ones_the_price_read_marked(tmp_path):
    from app.markets import MarketData
    from tests.test_markets import Fetcher
    markets = MarketData(Fetcher(), tmp_path / "markets.db")
    created = markets.alerts("add", "BTC", direction="above", price=80000)["created"]
    markets.alerts("add", "ETH", direction="above", price=9000)
    reached = markets.alerts("check")["triggered"]
    assert [alert["id"] for alert in reached] == [created["id"]] and reached[0]["triggered_price"] == 83000.0
    assert markets.alerts("check")["triggered"] == []  # reported once


def test_the_watchlist_takes_shares_as_well_as_coins(tmp_path):
    from app.markets import MarketData
    from tests.test_markets_live import StockFetcher
    markets = MarketData(StockFetcher(), tmp_path / "markets.db")
    assert markets.watchlist("add", "BTC")["symbols"] == ["BTC"]
    assert markets.watchlist("add", "AAPL")["symbols"] == ["BTC", "AAPL"]
    with pytest.raises(ValueError):
        markets.watchlist("add", "NOSUCH")


def test_the_alert_watcher_reports_reached_alerts_and_reads_nothing_while_none_wait(tmp_path):
    from app.market_alerts import AlertWatcher
    from app.markets import MarketData
    from tests.test_markets import Fetcher
    markets = MarketData(Fetcher(), tmp_path / "markets.db")
    told = []
    watcher = AlertWatcher(markets, told.append, interval_seconds=1)
    assert watcher.check_once() == [] and markets.fetcher.calls == []
    markets.alerts("add", "BTC", direction="below", price=90000)
    reached = watcher.check_once()
    assert len(reached) == 1 and "BTC is below $90,000.00" in told[0] and "nothing was bought or sold" in told[0]
    calls = len(markets.fetcher.calls)
    assert watcher.check_once() == [] and len(markets.fetcher.calls) == calls  # none waiting any more
    assert watcher.interval == 30.0  # never faster than every 30 s


def test_heatmap_tiles_flag_world_events(shared_tk):
    from tests.conftest import close_window, open_window
    window = open_window(shared_tk, "800x500-4000-4000")
    heatmap = Heatmap(window, Palette(), lambda symbol, asset: None)
    heatmap.pack(fill="both", expand=True)
    try:
        window.update()
        heatmap.show(heat_groups(rows(["AAPL", "XLE"]), rows(["BTC"])), {"XLE": 2})
        diamonds = [item for item in heatmap.find_all() if heatmap.type(item) == "text" and heatmap.itemcget(item, "text") == "◆"]
        assert len(diamonds) == 1
        left, top, right, bottom, _tile = next(item for item in heatmap.tiles if item[4]["symbol"] == "XLE")
        heatmap.hover(type("Event", (), {"x": (left + right) / 2, "y": (top + bottom) / 2})())
        assert any("2 world events in its industry" in heatmap.itemcget(item, "text")
                   for item in heatmap.find_withtag("hover") if heatmap.type(item) == "text")
    finally:
        close_window(window)


def wait_for(window, root, ready, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline and not ready():
        root.update()
        time.sleep(.02)
    return ready()


def test_overlays_live_candle_shortcuts_and_the_alerts_window(shared_tk, tmp_path):
    from types import SimpleNamespace
    from app.gui_markets import TradeCenter
    from app.markets import MarketData
    from tests.test_markets_live import StockFetcher
    markets = MarketData(StockFetcher(), tmp_path / "markets.db")

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(markets=markets, intelligence=None)
    window = TradeCenter(gui)
    try:
        window.geometry("1400x880-4000-4000")
        assert wait_for(window, shared_tk, lambda: window.chart_data and window.rows)
        shared_tk.update()
        window.draw_chart()
        assert "MA 20" in window.legend_text  # on by default
        # The S&P 500 line, rescaled to start where this market starts.
        window.overlay["compare"].set(True)
        window.overlay_changed("compare")
        assert wait_for(window, shared_tk, lambda: window.compare_data)
        window.draw_chart()
        assert "S&P 500" in window.legend_text and window.compare_line(window.chart_data["candles"])
        # Each streamed trade moves the current candle.
        window._live_drawn = 0.0
        # The fake candles are dated from when the test modules were imported; in a full run that is ~20 minutes
        # earlier, the last candle's hour has ended and a trade rightly no longer moves it. Make it the current one.
        window.chart_data["candles"][-1]["time"] = time.time() - 60
        window.live_candle(99000.0)
        last = window.chart_data["candles"][-1]
        assert last["close"] == 99000.0 and last["high"] >= 99000.0 and "last candle live" in window.legend_text
        # Keys: a range, the heatmap, and never while typing in the search box.
        key = lambda char, widget=window: type("Event", (), {"char": char, "widget": widget, "state": 0})()
        assert window.shortcut(key("2")) == "break" and window.span.get() == "1W"
        window.shortcut(key("h"))
        shared_tk.update()
        assert window.center_view.get() == "heatmap" and not window.overlay_row.winfo_ismapped()
        window.shortcut(key("h"))
        assert window.shortcut(key("3", window.search)) is None and window.span.get() == "1W"
        # Live prices from the stream reach the price alerts.
        stream = SimpleNamespace(snapshot=lambda names: {"prices": {"BTC/USD": {"price": 91000.0}, "AAPLx/USD": {"price": 1.0}}})
        prices = window.live_prices(stream)
        assert prices["BTC"] == 91000.0 and prices["AAPL"] == 1.0
        # The alerts window: list, check now, delete.
        markets.alerts("add", "BTC", direction="above", price=80000)
        markets.alerts("add", "ETH", direction="above", price=9000)
        dialog = window.alerts_dialog()
        assert len(window.alerts_tree.get_children()) == 2 and "2 waiting" in window.alerts_message.cget("text")
        assert window.alerts_dialog() is dialog  # one window, raised again
        window.check_alerts_now()
        assert wait_for(window, shared_tk, lambda: "reached" in window.alerts_message.cget("text"))
        assert "1 reached" in window.alerts_message.cget("text") and "reached" in window.footer.cget("text")
        window.alerts_tree.selection_set(window.alerts_tree.get_children()[1])
        window.delete_alert()
        assert len(window.alerts_tree.get_children()) == 1
        # The watchlist view mixes coins and shares.
        markets.watchlist("add", "BTC")
        markets.watchlist("add", "AAPL")
        window.view.set("watch")
        assert wait_for(window, shared_tk, lambda: set(window.rows) == {"BTC", "AAPL"})
    finally:
        window.close()
