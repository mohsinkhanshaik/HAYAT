"""The interactive 3D scene, the numbers behind the Trade Center's 3D views, and the 3D and Signals views in the window."""
import math
import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from app.gui_markets_3d import links_scene, move_colour, nice, odds_scene, space_scene  # noqa: E402
from app.gui_motion import MOTION  # noqa: E402
from app.gui_space3d import OrbitScene, inside  # noqa: E402
from app.gui_theme import Palette  # noqa: E402
from app.market_space import link_web, market_space, odds_surface, share_above, squash, unsquash  # noqa: E402


@pytest.fixture(scope="module")
def root(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, "640x480-4000-4000")
    yield window
    close_window(window)


@pytest.fixture
def scene(root):
    selected, opened = [], []
    widget = OrbitScene(root, Palette, on_select=selected.append, on_open=opened.append)
    widget.pack(fill="both", expand=True)
    MOTION["enabled"] = False
    widget.set_scene(points=[{"id": "A", "x": -.5, "y": 0, "z": 0, "label": "A", "tip": "A tip"},
                             {"id": "B", "x": .5, "y": .5, "z": .5, "label": "B", "tip": "B tip"}],
                     faces=[{"id": "f", "corners": [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)], "tip": "floor patch"}],
                     axes={"x": ("return", [(-1, "-5%"), (1, "+5%")])}, note="test")
    root.update()
    yield widget, selected, opened
    MOTION["enabled"] = True
    widget.destroy()


def event(x, y, **extra):
    return SimpleNamespace(**{"x": x, "y": y, "state": 0, "delta": 0, "char": "", **extra})


def test_missing_motion_pan_click_and_cancel_do_not_select_or_open(scene):
    widget, selected, opened = scene
    x, y, *_ = widget.project(-.5, 0, 0)
    widget._down(event(x - 30, y))
    widget._up(event(x, y))
    assert not selected and not opened
    widget._down(event(x, y), pan=True)
    widget._up(event(x, y))
    assert not selected
    widget._down(event(x, y))
    assert widget._cancel_interaction() == "break"
    widget._up(event(x, y))
    assert not selected and not opened
    assert widget._wheel(event(x, y, delta=10 ** 10)) == "break"
    assert widget.ZOOM_LIMITS[0] <= widget.zoom <= widget.ZOOM_LIMITS[1]
    for char in ("", "²", "１２"):
        assert widget._digit(event(x, y, char=char)) is None


def test_projection_centres_the_origin_and_depth_grows_toward_the_viewer(scene):
    widget, _selected, _opened = scene
    widget.yaw = widget.pitch = 0.0
    width, height = widget._size()
    x, y, depth, scale = widget.project(0, 0, 0)
    assert (x, y, depth, scale) == pytest.approx((*widget._centre(), 0, 1)) and widget._centre()[0] == width / 2
    _x, _y, near, near_scale = widget.project(0, 0, 1)
    assert near > 0 and near_scale > 1  # nearer is bigger
    assert inside(5, 5, [(0, 0), (10, 0), (10, 10), (0, 10)]) and not inside(15, 5, [(0, 0), (10, 0), (10, 10), (0, 10)])


def test_click_selects_drag_turns_and_double_click_opens_or_resets(scene):
    widget, selected, opened = scene
    widget.redraw()
    ax, ay, _radius, _depth = (*widget.snapshot()["points"]["A"], 0)[:4]
    widget._down(event(ax, ay))
    widget._up(event(ax, ay))
    assert widget.selected == "A" and selected == ["A"] and widget.tooltip == "A tip"
    yaw = widget.yaw
    widget._down(event(100, 100))
    widget._drag(event(180, 140))
    widget._up(event(180, 140))
    assert widget.yaw != yaw and widget.selected == "A" and selected == ["A"]  # a drag never selects
    assert widget._velocity == (0.0, 0.0)  # with motion off a flick does not coast
    widget.redraw()
    bx, by, _radius = widget.snapshot()["points"]["B"]
    widget._double(event(bx, by))
    assert opened == ["B"] and widget.selected == "B"
    widget._double(event(2, 2))
    assert (widget.yaw, widget.pitch, widget.zoom, widget.pan) == (*OrbitScene.HOME, (0.0, 0.0))


def test_wheel_zoom_keeps_the_spot_under_the_pointer(scene):
    widget, _selected, _opened = scene
    widget.redraw()
    px, py, _radius = widget.snapshot()["points"]["B"]
    for _ in range(3):
        widget._wheel(event(px, py, delta=120))
    widget.redraw()
    after = widget.snapshot()["points"]["B"]
    assert widget.zoom == pytest.approx(1.15 ** 3)
    assert (after[0], after[1]) == pytest.approx((px, py), abs=.01)
    for _ in range(40):
        widget._wheel(event(px, py, delta=-120))
    assert widget.zoom == OrbitScene.ZOOM_LIMITS[0]


def test_shift_drag_and_right_drag_move_the_scene(scene):
    widget, _selected, _opened = scene
    yaw = widget.yaw
    widget._down(SimpleNamespace(x=100, y=100, state=0x0001))
    widget._drag(event(150, 120))
    widget._up(event(150, 120))
    assert widget.pan == (50.0, 20.0) and widget.yaw == yaw
    widget._down(event(10, 10), pan=True)
    widget._drag(event(0, 10))
    widget._up(event(0, 10))
    assert widget.pan == (40.0, 20.0)


def test_keys_presets_tab_and_enter(scene):
    widget, selected, opened = scene
    widget.focus_force()
    assert widget._digit(SimpleNamespace(state=0, char="3")) == "break"  # top view, at once with motion off
    assert (widget.yaw, widget.pitch) == pytest.approx((0.0, 1.5))
    assert widget._digit(SimpleNamespace(state=0x0004, char="3")) is None  # Ctrl+3 is the window's
    widget.turn(.3, 5)
    assert widget.pitch == OrbitScene.PITCH_LIMIT
    widget.step(1)
    first = widget.selected
    widget.step(1)
    assert widget.selected != first and widget.selected in ("A", "B")
    widget._open_selected()
    assert opened[-1] == widget.selected
    widget.set_spin(True)
    assert not widget.spinning  # spinning needs motion


def test_hover_reads_points_before_faces_and_the_frontmost(scene):
    widget, _selected, _opened = scene
    widget.redraw()
    ax, ay, _radius = widget.snapshot()["points"]["A"]
    widget._move(event(ax, ay))
    assert widget.hover == ("point", "A") and widget.tooltip == "A tip"
    widget._hits = [(-1.0, "point", "far", (50, 50, 8)), (1.0, "point", "near", (52, 50, 8)), (2.0, "face", "f", [(0, 0), (100, 0), (100, 100)])]
    assert widget._hit(51, 50) == ("point", "near")
    assert widget._hit(90, 10) == ("face", "f")
    widget._leave(None)
    assert widget.hover is None


def test_motion_on_eases_to_a_view_and_coasts_after_a_flick(scene):
    widget, _selected, _opened = scene
    MOTION["enabled"] = True
    widget.view("side")
    assert widget._target is not None
    for _ in range(200):
        widget._frame_at -= .05
        widget._animate()
        if widget._target is None:
            break
    assert widget._target is None and widget.yaw == pytest.approx(-math.pi / 2, abs=1e-6)
    widget._velocity = (4.0, 0.0)
    yaw = widget.yaw
    widget._frame_at -= .05
    widget._animate()
    assert widget.yaw > yaw and widget._velocity[0] < 4.0  # turning, and slowing down
    widget._halt()


# ---------------------------------------------------------------------- the numbers behind the views
def test_squash_round_trips_and_the_surface_falls_with_the_move():
    for move in (-60, -5, 0, .3, 12, 140):
        assert unsquash(squash(move)) == pytest.approx(move, abs=1e-6)
    levels = {"p05": {"move_pct": -8.0}, "p25": {"move_pct": -2.0}, "p50": {"move_pct": .5}, "p75": {"move_pct": 3.0},
              "p95": {"move_pct": 9.0}}
    assert share_above(levels, .5) == (50.0, True) and share_above(levels, -2.0) == (75.0, True)
    assert share_above(levels, -30) == (100.0, False) and share_above(levels, 40) == (0.0, False)
    periods = {"symbol": "BTC", "price": 100.0, "periods": [
        {"key": "1d", "label": "1 day", "available": True, "chance_higher": {"up_pct": 51.0}, "range": levels},
        {"key": "1m", "label": "1 month", "available": True, "chance_higher": {"up_pct": 55.0},
         "range": {key: {"move_pct": value["move_pct"] * 4} for key, value in levels.items()}},
        {"key": "1y", "label": "1 year", "available": False}]}
    surface = odds_surface(periods)
    assert [row["key"] for row in surface["rows"]] == ["1d", "1m"]
    for row in surface["rows"]:
        assert row["shares"] == sorted(row["shares"], reverse=True)
    assert surface["moves_pct"][len(surface["moves_pct"]) // 2] == pytest.approx(0, abs=1e-9)
    scene = odds_scene(surface, Palette)
    assert len(scene["faces"]) == len(surface["moves_pct"]) - 1 and [point["id"] for point in scene["points"]] == ["1d", "1m"]
    assert any("beyond the measured" in face["tip"] for face in scene["faces"])
    with pytest.raises(ValueError):
        odds_surface({"symbol": "X", "price": 1, "periods": [{"available": False}]})


def test_market_space_places_each_market_and_its_links():
    from tests.test_market_signals import Markets, candles, noise
    btc = noise(40)
    series = {"BTC": candles(btc), "ETH": candles([1.1 * move for move in btc]), "SPY": candles(noise(41, sigma=.01)),
              "THIN": candles(noise(42), trades=1, flat_every=2)}
    markets = Markets(series, {"SPY": "stock", "THIN": "stock"})
    value = market_space(markets, [("BTC", "crypto"), ("ETH", "crypto"), ("THIN", "stock"), ("NOPE", "crypto")], "1m")
    rows = {row["symbol"]: row for row in value["points"]}
    assert set(rows) == {"BTC", "ETH", "THIN"} and any(gap.startswith("NOPE") for gap in value["gaps"])
    assert rows["BTC"]["corr_btc"] == 1.0 and rows["ETH"]["corr_btc"] > .99 and rows["THIN"]["thin"]
    assert rows["ETH"]["volatility_pct"] > rows["BTC"]["volatility_pct"]
    drawn = space_scene(value, "corr_btc", "ETH")
    points = {point["id"]: point for point in drawn["points"]}
    assert points["ETH"]["current"] and -1 <= points["ETH"]["x"] <= 1 and points["ETH"]["z"] == pytest.approx(rows["ETH"]["corr_btc"])
    assert points["THIN"]["colour"] != move_colour(rows["THIN"]["change_today_pct"])  # faded
    assert nice(3.2) == 4 and nice(0) == 1 and nice(120) == 150 and nice(10) == 10
    with pytest.raises(ValueError):
        market_space(markets, None, "2y")


def test_the_link_web_puts_tighter_links_nearer():
    from app.market_influences import influences
    from tests.test_market_signals import linked_markets
    value = influences(linked_markets(), "ALT", "crypto")
    web = link_web(value)
    nodes = {node["symbol"]: node for node in web["nodes"]}
    distance = lambda node: math.hypot(node["x"], node["z"])
    assert distance(nodes["BTC"]) < distance(nodes["SPY"])
    drawn = links_scene(web, Palette)
    assert drawn["points"][0]["id"] == "ALT" and len(drawn["segments"]) == len(web["nodes"])
    assert "explain about" in drawn["points"][0]["tip"]


# ---------------------------------------------------------------------- in the Trade Center
def test_the_trade_center_3d_and_signals_views(shared_tk, tmp_path):
    from app.gui_markets import CENTER_VIEWS, TradeCenter
    from app.markets import MarketData
    from tests.test_market_periods import market
    from tests.test_markets_live import StockFetcher
    markets = MarketData(StockFetcher(), tmp_path / "markets.db")
    by_symbol: dict = {}

    def candles(symbol, interval, asset="auto"):
        if symbol in ("EUR", "SMH", "XLE"):
            raise ValueError(f"Kraken lists no market for {symbol}.")
        series = by_symbol.setdefault(symbol, market(sum(map(ord, symbol))))
        return symbol, "stock" if symbol in ("SPY", "QQQ", "GLD", "NVDA", "AAPL", "MSFT", "TSLA", "COIN") else "crypto", series[interval]
    markets.candles = candles

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(markets=markets, intelligence=None)
    window = TradeCenter(gui)

    def wait(ready, seconds=30):
        deadline = time.time() + seconds
        while time.time() < deadline and not ready():
            shared_tk.update()
            time.sleep(.02)
        return ready()
    try:
        assert [label for _value, label in CENTER_VIEWS] == ["Chart", "Heatmap", "3D", "Signals"]
        window.shortcut(SimpleNamespace(widget=window, state=0, char="s"))
        assert window.center_view.get() == "signals" and window.signals_panel.frame.winfo_manager() == "pack"
        panel = window.signals_panel
        assert wait(lambda: panel.signals_data is not None and panel.links_data is not None)
        assert len(panel.tree.get_children()) == 19 and panel.cards["1d"][0].cget("text").endswith("% higher")
        assert panel.links.get_children() and "BTC" not in panel.links.get_children()  # never linked to itself
        first = panel.tree.get_children()[0]
        panel.tree.selection_set(first)
        panel.signal_selected()
        assert "Verdict:" in panel.said()
        assert panel.lab.winfo_manager() == "pack" and panel.influence.winfo_manager() == ""
        panel.part.set("links")
        panel.show_part()
        assert panel.influence.winfo_manager() == "pack" and panel.lab.winfo_manager() == ""
        assert panel.head.cget("text").startswith(panel.links_data["summary"][:20]) and panel.split_big.cget("text").endswith("%")
        assert "Not measured · Euro vs dollar" in panel.said()  # the markets it could not read are named
        panel.links.selection_set(panel.links.get_children()[0])
        panel.link_selected()
        assert "Next-day lead" in panel.said()
        window.shortcut(SimpleNamespace(widget=window, state=0, char="d"))
        assert window.center_view.get() == "space" and window.signals_panel.frame.winfo_manager() == ""
        space = window.space_panel
        assert wait(lambda: space.space_data is not None)
        assert {"BTC", "ETH", "SPY"} <= {point["id"] for point in space.scene.points}
        assert space.scene.selected == "BTC"  # the market on screen is picked out
        space.mode.set("links")
        space._mode_changed()
        assert space.wanted() == "influences" and space.scene.points[0]["id"] == "BTC"
        space.mode.set("odds")
        space._mode_changed()
        assert wait(lambda: space.periods_data is not None)
        assert space.scene.faces and {point["id"] for point in space.scene.points} >= {"1h", "1d", "1m"}
        space.mode.set("space")
        space._mode_changed()
        space._open("ETH")
        assert window.symbol == "ETH" and space.current == "ETH" and panel.signals_data is None  # a new market resets the views
        assert space.scene.selected == "ETH"
        window.shortcut(SimpleNamespace(widget=window, state=0, char="d"))
        assert window.center_view.get() == "chart" and window.chart.winfo_manager() == "pack"
    finally:
        window.close()
