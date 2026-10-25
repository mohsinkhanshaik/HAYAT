"""The Command Center: every tile fills from its source, live prices move the sparklines, clicks open windows."""
import time
from types import SimpleNamespace

from app.accounts import AccountManager
from app.gui_theme import Palette
from app.safety.confirmations import ConfirmationGate
from tests.test_accounts import Broker
from tests.test_scenarios_trade import Manager, Markets as DailyMarkets, walk


class Markets(DailyMarkets):
    """Hourly sparklines, daily odds, a live stream, local alerts."""

    def __init__(self, path):
        series = {symbol: walk(index) for index, symbol in enumerate(("BTC", "ETH", "SOL", "SPY", "NVDA", "GLD"))}
        super().__init__(series)
        self.path = path
        self.fetcher = object()
        self.calendar = SimpleNamespace(upcoming=lambda days: {"events": [
            {"kind": "fed", "title": "Fed interest-rate decision (FOMC)", "date": "2026-10-28", "days_until": 2, "time": "14:00 ET",
             "effects": [{"symbol": "GLD", "ratio": 1.7}]},
            {"kind": "jobs", "title": "US jobs report (nonfarm payrolls)", "date": "2026-11-06", "days_until": 11}], "gaps": []})
        self.news = SimpleNamespace(news=lambda symbol, hours, rumours, limit: {"items": [
            {"title": "Nvidia in talks, sources say", "outlet": "Test", "published": "2026-10-12T04:00", "markets": ["NVDA"], "rumour": True,
             "moves": [{"symbol": "NVDA", "change_pct": 1.5}]}], "rumours": 1})
        self.price = 100.0
        self.watched = []
        self.live = SimpleNamespace(status="live", watch=self.watched.append, unwatch=lambda names: None,
                                    snapshot=lambda names: {"prices": {name: {"price": self.price, "change_pct": 1.25} for name in names},
                                                            "delay_ms_median": 120})

    def alerts(self, action="list"):
        return {"alerts": [{"triggered_at": None}, {"triggered_at": "2026-10-12T00:00:00+00:00"}]}


class Intelligence(Manager):
    def __init__(self):
        super().__init__([{"id": "1", "title": "Tanker struck", "severity": "high", "category": "conflict", "latitude": 26.3,
                           "longitude": 56.4, "observed_at": "2026-10-12T01:00:00+00:00"},
                          {"id": "2", "title": "Road closed", "severity": "high", "category": "traffic", "latitude": 40.0,
                           "longitude": -100.0, "observed_at": "2026-10-12T02:00:00+00:00"},
                          {"id": "3", "title": "Storm", "severity": "medium", "category": "severe_weather", "latitude": 10.0,
                           "longitude": 10.0, "observed_at": "2026-10-12T00:00:00+00:00"}])

    def dashboard(self, bbox=None, located_only=False, since_hours=None, limit=200, min_severity="", **_kwargs):
        if bbox is not None:
            return super().dashboard(bbox=bbox)
        rank = {"high": 2, "medium": 1}
        rows = [event for event in self.events if rank.get(event["severity"], 0) >= rank.get(min_severity, 0)]
        counts = {}
        for event in self.events:
            counts[event["category"]] = counts.get(event["category"], 0) + 1
        counts["cameras"] = 500
        return {"events": rows[:limit], "total_matching": len(rows) + (500 if not min_severity else 0),
                "aggregates": {"counts_by_category": counts}}


class Web3:
    path = "x"

    def networks(self):
        return {"networks": [{"key": "bitcoin", "name": "Bitcoin", "block_height": 971066, "fee_fast_sat_vb": 2, "mempool_transactions": 82026,
                              "hashrate_eh_s": 1028.6},
                             {"key": "ethereum", "name": "Ethereum", "gas_average_gwei": .41, "block_time_seconds": 12.1, "utilization_pct": 51.0}]}

    def defi(self, limit=5):
        return {"dex": {"volume_24h_usd": 5.62e9, "change_1d_pct": 8.6, "top": [{"name": "Uniswap V4"}, {"name": "Uniswap V3"}]}}

    def watchlist(self, action="list"):
        return {"addresses": [{"address": "0x1"}]}


def test_every_tile_fills_and_live_prices_move_the_sparklines(shared_tk, tmp_path):
    from app.gui_command_center import MARKETS, CommandCenter
    markets = Markets(tmp_path / "markets.db")
    gate = ConfirmationGate()
    gate.propose("account", "Post", "LinkedIn", [("Post", "Hi")], lambda _secrets: {})
    opened = []

    class GUI(Palette):
        def _open_markets(self):
            opened.append("markets")

        def _show_confirmation(self):
            opened.append("confirm")
    gui = GUI()
    gui.root = shared_tk
    gui.runtime = SimpleNamespace(markets=markets, intelligence=Intelligence(), web3=Web3(), confirmations=gate, email=None,
                                  accounts=AccountManager(Broker(), tmp_path / "accounts.json"))
    window = CommandCenter(gui)

    def wait(ready, seconds=15):
        deadline = time.time() + seconds
        while time.time() < deadline and not ready():
            shared_tk.update()
            time.sleep(.02)
        return ready()
    try:
        window.geometry("1400x880-4000-4000")
        assert markets.watched and len(markets.watched[0]) == len(MARKETS)
        assert wait(lambda: all(key in window.data for key in ("world", "trade", "odds", "web3", "mine", "calendar", "news")) and window.sparks)
        world = window.data["world"]
        # Counts come from the store's totals, cameras left out; the world's reports come before road closures.
        assert world["severity"] == {"high": 2, "medium": 1, "low": 0} and world["total"] == 3
        assert [item["title"] for item in world["top"]] == ["Tanker struck", "Road closed"]
        assert window.data["trade"]["chokepoints"][0]["key"] == "hormuz"
        assert [row["symbol"] for row in window.data["odds"]] == ["BTC", "ETH", "SPY"]
        mine = window.data["mine"]
        assert mine["pending"] == 1 and mine["alerts"] == {"waiting": 1, "reached": 1} and mine["watched"] == 1
        from app.accounts import PROVIDERS
        assert len(mine["accounts"]) == len(PROVIDERS) and not any(row["connected"] for row in mine["accounts"])
        shared_tk.update()
        for key in window.tiles:
            window.draw(key)
            assert window.tiles[key].canvas.find_all(), key
        texts = lambda key: " ".join(window.tiles[key].canvas.itemcget(item, "text") for item in window.tiles[key].canvas.find_all()
                                     if window.tiles[key].canvas.type(item) == "text")
        assert "2 days" in texts("calendar") and "GLD 1.7×" in texts("calendar")
        assert "UNCONFIRMED" in texts("news") and "NVDA +1.5%" in texts("news")
        # A new trade flashes the market and moves the last sparkline point.
        assert window.last_price["BTC"] == 100.0
        markets.price = 101.0
        window.live_markets()
        assert window.last_price["BTC"] == 101.0 and window.flash["BTC"][0] == "#4ADE80"
        assert "live · 120 ms" in window.tiles["markets"].stamp.cget("text")
        # Clicks open the full windows.
        window.tiles["markets"].opener()
        y, method = next(spot for spot in window._mine_spots if spot[1] == "_show_confirmation")
        window.mine_click(SimpleNamespace(y=y))
        assert opened == ["markets", "confirm"]
        window.animate_trade()
        assert window.tiles["trade"].canvas.find_withtag("ring")  # the Hormuz alert pulses
        x, y, _row = window._trade_points[0]
        window.trade_hover(SimpleNamespace(x=x, y=y))
        assert window.tiles["trade"].canvas.find_withtag("hover")
    finally:
        window.close()


def test_fund_rows_name_the_fund_not_the_index_or_the_metal():
    """SPY's price is not the S&P 500's level, nor GLD's an ounce of gold; the tile says which it shows."""
    from app.gui_command_center import MARKETS

    labels = {symbol: label for symbol, _asset, label in MARKETS}
    assert labels["SPY"] == "S&P 500 fund (SPY)" and labels["GLD"] == "Gold fund (GLD)"
