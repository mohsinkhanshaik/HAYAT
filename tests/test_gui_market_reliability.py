"""Late market jobs, responsive panes and long forecast reports on real Tk widgets."""
import os
import threading
import time
from types import SimpleNamespace
from tkinter import ttk

import pytest

from app.gui_markets import TradeCenter
from app.gui_theme import Palette, style_controls, style_hayat_buttons
from app.markets import MarketData
from tests.conftest import open_window
from tests.test_markets_live import StockFetcher


@pytest.fixture
def center(shared_tk, tmp_path, monkeypatch):
    parent = open_window(shared_tk, '1100x800-4000-4000')
    style = ttk.Style(parent)
    previous = style.theme_use()
    style.theme_use('clam')
    style_controls(parent, Palette)
    style_hayat_buttons(parent, Palette)
    class GUI(Palette):
        pass
    gui = GUI()
    gui.root = parent
    gui.runtime = SimpleNamespace(markets=MarketData(StockFetcher(), tmp_path / 'markets.db'), intelligence=None)
    window = TradeCenter(gui)
    window.after_cancel(window._tick_id)
    monkeypatch.setattr(window, '_schedule', lambda: None)
    monkeypatch.setattr(window, 'show_live', lambda: None)
    try:
        yield window
    finally:
        window.close()
        style.theme_use(previous)
        parent.destroy()


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.01)
    assert predicate()


def tick(window):
    window._tick()
    window.after_cancel(window._tick_id)


@pytest.mark.parametrize('fail', [False, True])
def test_old_asset_result_or_error_never_replaces_new_selection_or_clears_its_pending_job(center, fail):
    old_started, old_release, new_started, new_release = (threading.Event() for _ in range(4))
    seen = []
    center.show_outlook = seen.append
    def old():
        old_started.set()
        old_release.wait(3)
        if fail:
            raise ValueError('old BTC failure')
        return {'symbol': 'BTC'}
    def new():
        new_started.set()
        new_release.wait(3)
        return {'symbol': 'ETH'}
    try:
        center._submit('outlook', old)
        assert old_started.wait(3)
        center.select('ETH', 'crypto')
        center._submit('outlook', new)
        old_release.set()
        assert new_started.wait(3)
        tick(center)
        assert 'outlook' in center._pending and seen == []
        assert 'old BTC failure' not in center.outlook_text.get('1.0', 'end')
        new_release.set()
        wait_for(lambda: not center._results.empty())
        tick(center)
        assert seen == [{'symbol': 'ETH'}] and 'outlook' not in center._pending
    finally:
        old_release.set()
        new_release.set()


def test_queued_searches_coalesce_and_only_the_latest_query_can_render(center):
    started, release = threading.Event(), threading.Event()
    executed, shown = [], []
    center.show_list = shown.append
    def first():
        started.set()
        release.wait(3)
        executed.append('first')
        return 'first'
    try:
        center.query.set('first')
        center._submit('list', first)
        assert started.wait(3)
        center.query.set('second')
        center._submit('list', lambda: executed.append('second') or 'second')
        center.query.set('third')
        center._submit('list', lambda: executed.append('third') or 'third')
        release.set()
        wait_for(lambda: 'third' in executed)
        tick(center)
        assert executed == ['first', 'third'] and shown == ['third']
    finally:
        release.set()


def test_small_trade_center_keeps_chart_and_layout_controls_reachable(center):
    center.geometry('840x520-4000-4000')
    center.update()
    assert not center.list_pane.winfo_ismapped() and not center.side_pane.winfo_ismapped()
    assert center.chart.winfo_width() >= 400 and center.chart.winfo_height() > 100
    assert center.layout_choice.winfo_rootx() + center.layout_choice.winfo_width() <= center.winfo_rootx() + center.winfo_width()
    center.layout.set('Chart + details')
    center.update()
    assert center.side_pane.winfo_ismapped() and center.chart.winfo_width() >= 400
    center.layout.set('Markets + chart')
    center.update()
    assert center.list_pane.winfo_ismapped() and not center.side_pane.winfo_ismapped()


def test_outlook_first_shows_top_and_refresh_keeps_reading_position(center):
    center.layout.set('Chart + details')
    center.side_tabs.select(1)
    center._set_outlook_text('\n'.join(f'Forecast evidence {i}' for i in range(300)))
    center.update()
    assert center.outlook_text.yview()[0] == 0
    center.outlook_text.yview_moveto(.5)
    center.update()
    before = center.outlook_text.yview()[0]
    center._set_outlook_text('\n'.join(f'Updated evidence {i}' for i in range(300)))
    center.update()
    assert center.outlook_text.yview()[0] == pytest.approx(before, abs=.01)


def test_compact_signals_keep_the_table_and_scrollable_explanation_accessible(center):
    from app.market_signals import signals
    from tests.test_market_signals import Markets, candles, persistent
    data = signals(Markets({'BTC': candles(persistent(3, keep=.45))}), 'BTC')
    center.signals_panel.show_signals(data)
    center.geometry('840x520-4000-4000')
    center.center_view.set('signals')
    center.switch_center()
    center.update()
    assert center.signals_panel.tree.winfo_ismapped()
    assert center.signals_panel.tree.winfo_height() >= 52
    assert center.signals_panel.note.vbar.winfo_ismapped()
    table = center.signals_panel.tree
    selected = table.get_children()[8]
    table.selection_set(selected)
    table.yview_moveto(.4)
    table.xview_moveto(.3)
    position = table.yview()[0], table.xview()[0]
    center.signals_panel.show_signals(data)
    center.update()
    assert table.selection() == (selected,)
    assert (table.yview()[0], table.xview()[0]) == pytest.approx(position)
    center.geometry('1280x820-4000-4000')
    center.update()
    assert center.signals_panel.head.winfo_ismapped()
    assert all(small.winfo_ismapped() for _big, small in center.signals_panel.cards.values())
    assert not center.side_pane.winfo_ismapped()  # Auto gives the results table room.


def test_market_list_refresh_keeps_its_selection_and_scroll(center):
    center.markets.path = None
    center.layout.set('Markets + chart')
    center.geometry('1100x700-4000-4000')
    rows = [{'symbol': f'COIN{i}', 'price': 100 + i, 'quote': 'USD', 'volume_24h_quote': 1000,
             'change_today_pct': 1} for i in range(80)]
    value = ({'rows': rows, 'total': 80, 'quote': 'USD', 'assets_listed': 80}, [])
    center.show_list(value)
    center.update()
    center.tree.selection_set('COIN40')
    center.tree.yview_moveto(.5)
    before = center.tree.yview()
    center.show_list(value)
    center.update()
    assert center.tree.selection() == ('COIN40',)
    assert center.tree.yview() == pytest.approx(before)


@pytest.mark.parametrize(('quote', 'stale', 'visible'), [('USD', False, True), ('EUR', False, False), ('USD', True, False)])
def test_outlook_cone_uses_its_currency_and_completed_close_anchor(center, quote, stale, visible):
    center.chart_data = {'symbol': 'BTC', 'quote': quote, 'range': '1D', 'change_pct': 1, 'interval_minutes': 60,
                         'candles': [{'time': 1700000000 + i * 3600, 'open': 100, 'close': 101,
                                      'low': 99, 'high': 102, 'volume': 1} for i in range(30)]}
    center.outlook_data = {'symbol': 'BTC', 'price': 100, 'data_quality': {'stale': stale},
                           'ranges': {'next_day': {'wide_95': [95, 105], 'likely_68': [98, 102]}}}
    center.draw_chart()
    cones = center.chart.find_withtag('outlook-cone')
    assert bool(cones) is visible
    if visible:
        assert len(cones) == 2
        assert center.chart.coords(cones[0])[1] == pytest.approx(center._chart_geometry[2](100))


@pytest.mark.skipif(os.environ.get('HAYAT_SHOTS') != '1', reason='screenshots on request')
def test_capture_responsive_market_upgrade(tmp_path, monkeypatch):
    from app.market_outlook import outlook
    from app.market_signals import signals
    from tests.test_gui import _build_window
    from tests.test_gui_screenshots import _grab, _Overlay, _pump
    from tests.test_market_signals import Markets, candles, persistent

    window = _build_window()
    center = None
    try:
        markets = MarketData(StockFetcher(), tmp_path / 'shots.db')
        window.runtime = _Overlay(window.runtime, markets=markets, intelligence=None)
        monkeypatch.setattr(TradeCenter, '_schedule', lambda *_: None)
        window.root.deiconify()
        center = TradeCenter(window)
        center.select('AAPL', 'stock')
        center.show_chart(markets.chart('AAPL', asset='stock'))
        value = outlook(markets, 'AAPL', 'stock')
        center.show_outlook(value)
        center.price.configure(text=f"${value['price']:.2f}")
        center.attributes('-topmost', True)
        center.geometry('840x520+0+0')
        _pump(window, .8)
        _grab(window, '41-market-compact', center)
        center.geometry('1280x820+0+0')
        center.layout.set('Auto')
        center.side_tabs.select(1)
        _pump(window, .8)
        _grab(window, '42-market-calibrated-outlook', center)
        data = signals(Markets({'AAPL': candles(persistent(3, keep=.45))}, {'AAPL': 'stock'}), 'AAPL')
        center.show_signals(data)
        center.center_view.set('signals')
        center.switch_center()
        _pump(window, .8)
        _grab(window, '43-market-signal-evidence', center)
        center.geometry('840x520+0+0')
        _pump(window, .8)
        _grab(window, '44-market-signals-compact', center)
    finally:
        if center:
            center.close()
        window.root.destroy()
