r"""Capture the real window at the owner's screen size, for a visual review. On request only.

    $env:HAYAT_SHOTS = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_gui_screenshots.py

Writes PNGs to .test-runs/gui-shots/. Captures target the window handle rather
than a desktop crop, so covering applications cannot contaminate them. The data
stores are the test suite's temporary ones, never the owner's.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from tests.test_gui import _release

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_SHOTS") != "1", reason="screenshots on request")
OUT = Path(__file__).resolve().parents[1] / ".test-runs" / "gui-shots"


class _Overlay:
    """The window's (sandboxed) runtime with a few parts replaced for one capture.

    A bare SimpleNamespace left the window's own status pollers reading parts it lacked
    ("no attribute 'conversation'", "'voice'") on their background threads.
    """

    def __init__(self, base, **parts):
        self._base = base
        self.__dict__.update(parts)

    def __getattr__(self, name):
        return getattr(self._base, name)

CHAT = [
    ("user", "You\nwhere did we leave off?"),
    ("assistant", "HAYAT\nYou have been working on: Learn languages (3 requests), Earn and manage money (2 requests).\n"
                  "Your last request: “use the camera and test it”\n"
                  "Recent tasks: complete: find solar installers in Austin.\n"
                  "Likely next: Search the internet (after Learn languages, Search followed 4 of 9 times)."),
    ("user", "You\nread surah yasin"),
    ("assistant", "HAYAT\nSurah Ya-Seen (Ya Sin) · 36:1-7 of 83 ayahs\n\nBismillaahir Rahmaanir Raheem\n\n"
                  "1. Yaa-Seeen\n   Ya, Seen.\n\n2. Wal-Qur'aanil Hakeem\n   By the wise Qur'an,\n\n"
                  "3. Innaka laminal mursaleen\n   Indeed you, [O Muhammad], are from among the messengers,"),
    ("user", "You\nhow are you today? 😊"),
    ("assistant", "HAYAT\nDoing well, thanks 🙂 Ready when you are: research 🔎, jobs 💼 or markets 📈. "
                  "Yesterday's search finished ✅"),
]


def _grab(window, name, widget=None):
    from PIL import ImageGrab

    root = widget if widget is not None else window.root
    root.update()
    hwnd = int(root.wm_frame(), 16)
    # Capture the window itself. Cropping the desktop silently captured whichever
    # application happened to cover it, even though the GUI assertions passed.
    captured = ImageGrab.grab(window=hwnd)
    assert captured.width > 100 and captured.height > 100, "The GUI capture has invalid bounds."
    assert captured.getextrema() != ((0, 0), (0, 0), (0, 0)), "The GUI capture is blank."
    OUT.mkdir(parents=True, exist_ok=True)
    captured.save(OUT / f"{name}.png")


def _pump(window, seconds):
    end = time.time() + seconds
    while time.time() < end:
        window.root.update()
        time.sleep(0.03)


def test_capture_main_views():
    from tests.test_gui import _build_window

    window = _build_window()
    try:
        root = window.root
        root.deiconify()
        root.attributes("-topmost", True)
        root.geometry("1536x900+0+0")
        for text in ("search the web for the latest AI news", "ok great", "read surah yasin",
                     "search the web for GPU prices", "continue quran", "search the web for solar news"):
            window.runtime.situation.observe_turn(text)
        window._show_welcome()
        _pump(window, 2)
        _grab(window, "00-home-laptop")
        # The right column scrolled down: the folded diagnostics panels with their one-line news.
        window.right_rail.canvas.yview_moveto(1.0)
        _pump(window, 1)
        _grab(window, "00b-right-column-folded")
        # And with every panel open, a page at a time, so a clipped row anywhere in the column shows up.
        window._folds.unfold_all()
        for page, offset in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
            window.right_rail.canvas.yview_moveto(offset)
            _pump(window, .6)
            _grab(window, f"00c-right-column-open-{page}")
        for title in window.FOLDED_BY_DEFAULT:
            window._folds.set(title, True)
            window._folds.panels[title](True)
        window.right_rail.canvas.yview_moveto(0.0)
        for size, name in (("1440x900+40+40", "01-home"), ("1280x800+40+40", "02-home-1280")):
            root.geometry(size)
            _pump(window, 2)
            _grab(window, name)
        root.geometry("1536x900+0+0")  # maximized on this 1920x1200 screen at 125%
        window.runtime.session.standing_instruction = {"rule": "reply with exactly one word", "remaining": 2}
        window.runtime.session.last_answer_source = "exact"
        for tag, text in CHAT:
            window._append(tag, text)
        _pump(window, 1.5)
        _grab(window, "03-chat-laptop")
        window.show_view("operator")
        _pump(window, 3)
        _grab(window, "04-operator-brain")
        window._set_brain_dimensions("3D")
        _pump(window, 2)
        _grab(window, "04b-operator-brain-3d")
        window._set_brain_dimensions("5D")
        _capture_intelligence(window)
        _capture_team(window)
    finally:
        _release(window)


def _capture_intelligence(window):
    """The Global intelligence workspace on recorded public feeds (no network)."""
    from app.gui_intelligence import IntelligenceWindow
    from app.intelligence.manager import IntelligenceManager
    from app.intelligence.tools import register_intelligence_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    from tests.test_public_intelligence import Fetcher

    scratch = OUT / "intelligence-data"
    scratch.mkdir(parents=True, exist_ok=True)
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, scratch))
    manager = IntelligenceManager(scratch / "intelligence.db", Fetcher(), registry)
    register_intelligence_tools(registry, manager)
    manager.refresh()
    original = window.runtime
    window.runtime = _Overlay(original, intelligence=manager, registry=registry)
    try:
        workspace = IntelligenceWindow(window)
        workspace.geometry("1536x900+0+0")
        workspace.attributes("-topmost", True)
        _pump(window, 3)
        _grab(window, "05-intelligence", workspace)
        workspace.close()
    finally:
        window.runtime = original
        manager.close()


def _capture_team(window):
    """The team workspace with a sample team, a selected worker and this machine's limits."""
    from app.gui_team import TeamWorkspace
    from tests.test_gui_team import _sample_team

    workspace = TeamWorkspace(window.root, type(window), _sample_team(), ["researcher", "coder", "reviewer"])
    workspace.geometry("1536x900+0+0")
    workspace.attributes("-topmost", True)
    workspace.jobs.selection_set("team-jobs")
    _pump(window, 1)
    workspace.workers.selection_set("Research Agent")
    _pump(window, 1.5)
    _grab(window, "06-team-workspace", workspace)
    workspace.destroy()


def test_capture_every_studio_window():
    """Every window the Studios menu opens, at the owner's screen size, for a visual review."""
    import tkinter as tk

    from tests.test_gui import _build_window

    window = _build_window()
    menu = window._studios_menu
    failures = []
    try:
        window.root.deiconify()
        window.root.geometry("1536x900+0+0")
        _pump(window, 1)
        for index in range(menu.index("end") + 1):
            if menu.type(index) != "command":
                continue
            label = menu.entrycget(index, "label")
            before = set(window.root.winfo_children())
            try:
                menu.invoke(index)
            except Exception as exc:  # a window that fails to open is itself a finding
                failures.append(f"{label}: {type(exc).__name__}: {exc}")
                continue
            _pump(window, 3)
            opened = [child for child in window.root.winfo_children() if child not in before and isinstance(child, tk.Toplevel)]
            if not opened:
                print(f"NO NEW WINDOW {label} (shown in the main window)")
                _grab(window, f"10-{index:02d}-{label[:24]}".replace("/", "-").replace("?", "").replace(":", ""))
                continue
            for number, top in enumerate(opened):
                try:
                    top.geometry("1536x860+0+0")
                    top.attributes("-topmost", True)
                    _pump(window, 2.5)
                    safe = "".join(ch if ch.isalnum() or ch in " -" else "-" for ch in label)[:28]
                    _grab(window, f"10-{index:02d}-{safe}-{number}", top)
                finally:
                    try:
                        top.destroy()
                    except tk.TclError:
                        pass
            _pump(window, .5)
        assert not failures, "Studio windows failed to open: " + "; ".join(failures)
    finally:
        _release(window)


def test_capture_business_deep_review(tmp_path):
    """The Business window's Deep review tab over sample books (the deep-review test's), for a visual review."""
    from app.gui_business import BusinessStudio
    from tests.test_business_deep import books as sample_books
    from tests.test_gui import _build_window

    window = _build_window()
    try:
        window.root.deiconify()
        window.runtime.business = sample_books.__wrapped__(tmp_path)
        studio = BusinessStudio(window)
        studio.geometry("1536x860+0+0")
        studio.attributes("-topmost", True)
        studio.show_tab("Deep review")
        studio.refresh()
        _pump(window, 2)
        _grab(window, "20-business-deep-review", studio)
        studio.destroy()
    finally:
        _release(window)


def test_capture_wallet_money_windows(tmp_path):
    """The Business window's Money methods and Pay-now QR windows over sample books."""
    from app.gui_business import BusinessStudio
    from app.safety.permissions import PermissionManager
    from app.tools.business import BusinessLedger
    from app.wallet_money import WalletMoney
    from tests.test_gui import _build_window

    window = _build_window()
    try:
        window.root.deiconify()
        ledger = BusinessLedger(tmp_path / "business.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
        ledger.set_profile(business_name="Mohsin Analytics", default_currency="INR")
        ledger.add_client("Acme", hourly_rate="2000", currency="INR")
        wallet = WalletMoney(ledger)
        wallet.add_method("bank", "HDFC savings", "1234", "HDFC")
        wallet.add_method("card", "Salary card", "9876", "SBI")
        wallet.add_method("upi", "UPI", receive="mohsin@okhdfcbank")
        wallet.add_method("paypal", "PayPal", receive="mohsinkhan")
        wallet.add_money("5000", "hdfc", "starting float")
        number = ledger.create_invoice("Acme", [{"description": "Dashboard", "quantity": "1", "unit_price": "12000"}])["number"]
        window.runtime.business = ledger
        studio = BusinessStudio(window)
        studio.geometry("1536x860+0+0")
        studio.show_tab("Wallet")
        studio.refresh()
        _pump(window, 1.5)
        _grab(window, "21-business-wallet", studio)
        methods = studio.money_methods()
        methods.attributes("-topmost", True)
        _pump(window, 1)
        _grab(window, "22-money-methods", methods)
        methods.destroy()
        studio.invoices.selection_set(number)
        pay = studio.pay_now()
        pay.attributes("-topmost", True)
        _pump(window, 1)
        _grab(window, "23-pay-now", pay)
        pay.destroy()
        studio.destroy()
    finally:
        _release(window)


def test_capture_portfolio_window(tmp_path):
    """Studios → Portfolio & net worth over sample holdings, with a week of net-worth history."""
    import sqlite3
    from datetime import date, timedelta

    from app.gui_portfolio import PortfolioWindow
    from app.portfolio import Portfolio
    from tests.test_gui import _build_window
    from tests.test_portfolio import BTC_ADDRESS, Chain, Markets, Rates

    window = _build_window()
    try:
        window.root.deiconify()
        book = Portfolio(tmp_path / "portfolio.db", Markets(), Chain(), Rates())
        book.record("cash", "USD", "2500", "set", account="Chase")
        book.record("cash", "INR", "150000", "set", account="HDFC savings")
        book.record("crypto", "BTC", "0.05", "buy", "60000", "Coinbase")
        book.record("stock", "AAPL", "12", "buy", "180", "Schwab")
        book.record("stock", "AAPL", "2", "sell", "230", "Schwab")
        book.watch_address(BTC_ADDRESS, "Cold wallet")
        with sqlite3.connect(book.path) as db:
            for back, amount in enumerate((61200, 60100, 62900, 63800, 62700, 64900)):
                db.execute("INSERT INTO snapshots VALUES (?, ?, '{}')", ((date.today() - timedelta(days=6 - back)).isoformat(), amount))
        window.runtime.portfolio = book
        panel = PortfolioWindow(window)
        panel.geometry("1180x780+0+0")
        _pump(window, 2)
        _grab(window, "24-portfolio", panel)
        panel.close()
    finally:
        _release(window)


def test_capture_money_desk_window(tmp_path):
    """Studios → HAYAT Wallet over sample books: decisions waiting, pots with targets, bills and a rule."""
    from datetime import date, timedelta

    from app.gui_money_desk import MoneyDeskWindow
    from app.money_desk import MoneyDesk
    from app.safety.permissions import PermissionManager
    from app.tools.business import BusinessLedger
    from tests.test_gui import _build_window

    window = _build_window()
    try:
        window.root.deiconify()
        ledger = BusinessLedger(tmp_path / "business.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
        ledger.set_profile(business_name="Mohsin Analytics", default_currency="USD")
        ledger.add_client("Acme", hourly_rate="60")
        for amount in ("2400", "1800"):
            ledger.record_payment(ledger.create_invoice("Acme", [{"description": "Dashboard", "quantity": "1", "unit_price": amount}])["number"])
        ledger.record_expense("450", "software", "Tools")
        desk = MoneyDesk(ledger)
        desk.create_pot("Tax", "1500")
        desk.create_pot("Rent", "1200", purpose="bills")
        desk.move("600", to_pot="tax")
        desk.add_rule("tax", "25", auto=True)
        desk.add_bill("Rent", "1100", (date.today() + timedelta(days=4)).isoformat(), pot="rent", pay_url="https://landlord.example/pay")
        desk.add_bill("Adobe", "55", (date.today() + timedelta(days=12)).isoformat(), business=True, category="software")
        desk.request_spend("300", "a second monitor", business=True)
        window.runtime.money_desk = desk
        panel = MoneyDeskWindow(window)
        panel.geometry("1180x800+0+0")
        _pump(window, 2)
        _grab(window, "25-wallet-decisions", panel)
        panel.tabs.select(1)
        _pump(window, 1)
        _grab(window, "26-wallet-pots", panel)
        for index, name in ((2, "27-wallet-bills"), (3, "28-wallet-rules")):
            panel.tabs.select(index)
            _pump(window, 1)
            _grab(window, name, panel)
        panel.close()
    finally:
        _release(window)


def capture_trade_center_views(markets, prefix="25"):
    """Trade Center → 3D (markets, odds surface, links) and Signals, at the owner's screen size."""
    from types import SimpleNamespace

    from app.gui_markets import TradeCenter
    from tests.test_gui import _build_window

    window = _build_window()
    try:
        window.root.deiconify()
        window.runtime = _Overlay(window.runtime, markets=markets, intelligence=None)
        center = TradeCenter(window)
        center.geometry("1536x860+0+0")

        def settle(ready, seconds=60):
            end = time.time() + seconds
            while time.time() < end and not ready():
                _pump(window, .2)
            _pump(window, .6)
        center.center_view.set("space")
        center.switch_center()
        space = center.space_panel
        settle(lambda: space.space_data is not None)
        _grab(window, f"{prefix}-trade-center-3d-markets", center)
        bitcoin = space.scene.snapshot()["points"].get("ETH")
        if bitcoin:
            space.scene._move(SimpleNamespace(x=bitcoin[0], y=bitcoin[1]))
            _grab(window, f"{prefix}-trade-center-3d-hover", center)
        for mode, ready in (("odds", lambda: space.periods_data is not None), ("links", lambda: space.links_data is not None)):
            space.mode.set(mode)
            space._mode_changed()
            settle(ready)
            _grab(window, f"{prefix}-trade-center-3d-{mode}", center)
        center.center_view.set("signals")
        center.switch_center()
        settle(lambda: center.signals_panel.signals_data is not None and center.signals_panel.links_data is not None)
        _grab(window, f"{prefix}-trade-center-signals", center)
        center.signals_panel.part.set("links")
        center.signals_panel.show_part()
        _pump(window, .6)
        _grab(window, f"{prefix}-trade-center-what-moves-it", center)
        center.close()
    finally:
        _release(window)


def test_capture_trade_center_3d_and_signals(tmp_path):
    from app.markets import MarketData
    from tests.test_market_periods import market
    from tests.test_markets_live import StockFetcher

    markets = MarketData(StockFetcher(), tmp_path / "markets.db")
    by_symbol: dict = {}
    stocks = {"SPY", "QQQ", "GLD", "SMH", "XLE", "NVDA", "AAPL", "MSFT", "TSLA", "COIN"}

    def candles(symbol, interval, asset="auto"):
        series = by_symbol.setdefault(symbol, market(sum(map(ord, symbol))))
        return symbol, "stock" if symbol in stocks else "crypto", series[interval]
    markets.candles = candles
    capture_trade_center_views(markets)


def test_capture_invention_simulation(tmp_path):
    """Invention → a pendulum project worked out without a model, then its Simulate and Rounds tabs."""
    import math
    import tkinter
    from types import SimpleNamespace

    from app.gui_invention import InventionPanel
    from app.safety.permissions import PermissionManager
    from app.tools.invention import register_invention_tools
    from app.tools.registry import ToolRegistry
    from tests.test_executive_invention import bench
    from tests.test_gui import _build_window

    window = _build_window()
    try:
        workbench = bench(tmp_path, None)
        registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
        register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
        cases = [{"inputs": {"L": length}, "expected": round(2 * math.pi * math.sqrt(length / 9.81), 6)}
                 for length in (0.25, 0.5, 1, 2, 4)]
        project = workbench.create("Pendulum clock: period from the length of the pendulum", ["Length 0.25-4 m"],
                                   cases, tolerance=0.001)
        workbench.work_out(project["id"])
        top = tkinter.Toplevel(window.root)
        top.geometry("1536x900+0+0")
        top.configure(bg=type(window).BLACK)
        panel = InventionPanel(top, type(window), SimpleNamespace(registry=registry, invention=workbench,
                                                                  session=SimpleNamespace(session_id="shots")), lambda *_: None)
        panel.pack(fill="both", expand=True, padx=12, pady=6)
        panel._paint(workbench.get(project["id"]))
        panel.tabs.select(3)
        _pump(window, 1.5)
        _grab(window, "30-invention-simulate", top)
        panel.tabs.select(1)
        panel.round_list.selection_set("1")
        panel.round_selected()
        _pump(window, 1)
        _grab(window, "31-invention-worked-out-round", top)
        top.destroy()
    finally:
        _release(window)


def test_capture_location_privacy_with_its_report():
    """Studios → Location privacy once its check has read this computer (the Studios capture shows it loading)."""
    from tests.test_gui import _build_window

    window = _build_window()
    try:
        window.root.deiconify()
        window.root.geometry("1536x900+0+0")
        window._open_location_privacy()
        privacy = window._location_privacy_window
        _grab(window, "32-location-privacy-loading", privacy)
        end = time.time() + 60
        while (privacy.report is None or getattr(privacy, "masks_report", None) is None) and time.time() < end:
            _pump(window, 0.2)
        privacy.geometry("1536x860+0+0")
        _pump(window, 1)
        _grab(window, "33-location-privacy", privacy)
        privacy.destroy()
    finally:
        _release(window)


def test_capture_accounts_with_social(tmp_path):
    """Accounts & sign-in with the social groups: some accounts connected, Bluesky selected, the not-offered list."""
    import json as _json

    from app.accounts import AccountManager
    from app.gui_accounts import AccountsCenter
    from tests.test_accounts import Broker
    from tests.test_gui import _build_window

    window = _build_window()
    try:
        window.root.deiconify()
        manager = AccountManager(Broker(), tmp_path / "accounts.json", request=lambda *a, **k: None, open_browser=lambda url: None)
        records = {key: {"connected": True, "connected_as": who, "method": "token", "connected_at": "2026-10-17T15:00:00+00:00"}
                   for key, who in (("google", "owner@gmail.com"), ("bluesky", "@mohsin.bsky.social"),
                                    ("telegram", "@hayat_owner_bot"), ("mastodon", "@mohsin@mastodon.social"))}
        records["mastodon"]["settings"] = {"server": "mastodon.social"}
        (tmp_path / "accounts.json").write_text(_json.dumps(records), encoding="utf-8")
        window.runtime.accounts = manager
        center = AccountsCenter(window)
        center.geometry("1536x860+0+0")
        center.select("bluesky")
        _pump(window, 1.5)
        _grab(window, "40-accounts-social", center)
        center.select("facebook")
        center.list_canvas.yview_moveto(1.0)
        _pump(window, 1)
        _grab(window, "41-accounts-facebook-and-not-offered", center)
        center.destroy()
    finally:
        _release(window)
