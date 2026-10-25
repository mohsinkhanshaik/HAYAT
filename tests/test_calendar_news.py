"""Scheduled events (Fed calendar, jobs reports, earnings) with past reactions; market news with rumours marked unconfirmed."""
import json
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.market_calendar import MarketCalendar, first_fridays, parse_fomc, reaction, report_lines as calendar_lines
from app.market_news import RUMOUR, MarketNews, link_markets, parse_feed, report_lines as news_lines
from app.markets import market_request, report

FOMC_PAGE = """<h4><a>2025 FOMC Meetings</a></h4>
<div class="fomc-meeting__month col"><strong>January</strong></div><div class="fomc-meeting__date col">28-29</div>
<div class="fomc-meeting__month col"><strong>April/May</strong></div><div class="fomc-meeting__date col">30-1</div>
<div class="fomc-meeting__month col"><strong>August</strong></div><div class="fomc-meeting__date col">22 (notation vote)</div>
<h4><a>2026 FOMC Meetings</a></h4>
<div class="fomc-meeting__month col"><strong>March</strong></div><div class="fomc-meeting__date col">17-18*</div>"""


def test_the_fed_calendar_is_read_with_decision_days_and_notation_votes_left_out():
    assert parse_fomc(FOMC_PAGE) == [date(2025, 1, 29), date(2025, 5, 1), date(2026, 3, 18)]
    assert first_fridays(date(2026, 10, 12), date(2026, 12, 31)) == [date(2026, 11, 6), date(2026, 12, 4)]


def candles_with_jumps(jump_days, days=400):
    today = datetime.now(UTC).date()
    rows, price = [], 100.0
    for index in range(days):
        day = today - timedelta(days=days - 1 - index)
        price *= 1.03 if day in jump_days else (1.002 if index % 2 else .998)
        rows.append({"time": int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()), "close": price})
    return rows


def test_event_days_are_measured_against_usual_days():
    today = datetime.now(UTC).date()
    events = [today - timedelta(days=30 * index) for index in range(1, 10)]
    measured = reaction(candles_with_jumps(set(events)), events)
    assert measured["events"] == 9 and measured["ratio"] > 2 and measured["up_share_pct"] == 100
    assert reaction(candles_with_jumps(set()), events[:2]) is None  # fewer than three events says nothing


class Fetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def fetch(self, url, max_bytes=0, accept=None, request_headers=None):
        self.calls.append((url, request_headers))
        for key, value in self.pages.items():
            if key in url:
                return SimpleNamespace(content=value.encode() if isinstance(value, str) else value)
        raise ConnectionError("offline " + url)


class DailyMarkets:
    def __init__(self, days):
        self.days = days

    def candles(self, symbol, interval, asset="auto"):
        return symbol, asset, candles_with_jumps(set(self.days))


def test_upcoming_events_carry_countdowns_effects_and_honest_gaps(monkeypatch):
    today = datetime.now(UTC).date()
    soon, past = today + timedelta(days=5), [today - timedelta(days=45 * index) for index in range(1, 8)]
    page = "".join(f'<h4><a>{year} FOMC Meetings</a></h4>' + "".join(
        f'<div class="fomc-meeting__month c"><strong>{day.strftime("%B")}</strong></div><div class="fomc-meeting__date c">{day.day}</div>'
        for day in [*past, soon] if day.year == year) for year in sorted({day.year for day in [*past, soon]}))
    monkeypatch.delenv("HAYAT_KEY_FINNHUB", raising=False)
    calendar = MarketCalendar(Fetcher({"fomccalendars": page}), DailyMarkets(past))
    value = calendar.upcoming(30)
    fed = next(item for item in value["events"] if item["kind"] == "fed")
    assert fed["days_until"] == 5 and fed["basis"] == "published by the Federal Reserve"
    assert {row["symbol"] for row in fed["effects"]} >= {"SPY", "GLD", "BTC"} and fed["effects"][0]["ratio"] > 1
    jobs = [item for item in value["events"] if item["kind"] == "jobs"]
    assert jobs and "usual pattern" in jobs[0]["basis"]
    assert any("Finnhub key" in gap for gap in value["gaps"])
    text = "\n".join(calendar_lines(value))
    assert "Fed interest-rate decision (FOMC)" in text and "On past fed decisions: SPY moved" in text and "not which way" in text
    reply = report([{"tool": "market_calendar", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert reply.startswith("Scheduled events that can move markets")


def test_earnings_come_from_finnhub_with_the_key_in_a_header(monkeypatch):
    monkeypatch.setenv("HAYAT_KEY_FINNHUB", "secret-key")
    day = (datetime.now(UTC).date() + timedelta(days=3)).isoformat()
    fetcher = Fetcher({"fomccalendars": FOMC_PAGE,
                       "calendar/earnings": json.dumps({"earningsCalendar": [{"symbol": "NVDA", "date": day, "hour": "amc"},
                                                                            {"symbol": "TINY", "date": day}]})})
    value = MarketCalendar(fetcher, None).upcoming(10)
    earnings = [item for item in value["events"] if item["kind"] == "earnings"]
    assert [(item["symbol"], item["time"]) for item in earnings] == [("NVDA", "after the close")]
    url, headers = next(call for call in fetcher.calls if "earnings" in call[0])
    assert "secret-key" not in url and headers == {"X-Finnhub-Token": "secret-key"}


FEED = """<?xml version="1.0"?><rss><channel>
<item><title>Nvidia in talks to buy chip start-up, sources say</title><link>https://example.com/a</link>
<description>&lt;p&gt;A deal is not yet agreed.&lt;/p&gt;</description><pubDate>{now}</pubDate></item>
<item><title>Oil prices fall as OPEC meets</title><link>https://example.com/b</link><description>Crude slid.</description><pubDate>{now}</pubDate></item>
<item><title>Father was asked for DNA test to get a passport</title><link>https://example.com/c</link>
<description>He travelled to France with inflation worries.</description><pubDate>{now}</pubDate></item>
<item><title>Old story about Bitcoin</title><link>https://example.com/d</link><description>x</description><pubDate>{old}</pubDate></item>
</channel></rss>"""


def test_feeds_are_parsed_safely_and_headlines_linked_to_the_markets_they_name():
    now = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = parse_feed(FEED.format(now=now, old=now).encode(), "Test")
    assert items[0]["title"] == "Nvidia in talks to buy chip start-up, sources say" and items[0]["summary"] == "A deal is not yet agreed."
    assert "NVDA" in link_markets(items[0]["title"], items[0]["title"]) and link_markets(items[1]["title"], items[1]["title"]) == ["XLE", "XOP"]
    # A summary mentioning France or inflation in passing does not make a story about those markets.
    assert link_markets(items[2]["title"] + " " + items[2]["summary"], items[2]["title"]) == []
    assert RUMOUR.search(items[0]["title"]) and not RUMOUR.search("Zcash plans to hide your keys") and not RUMOUR.search("Rates could rise")
    with pytest.raises(Exception):
        parse_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss>&a;</rss>', "Bomb")


def test_news_marks_rumours_and_shows_moves_since(monkeypatch):
    now = datetime.now(UTC)
    old = (now - timedelta(days=5)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    page = FEED.format(now=now.strftime("%a, %d %b %Y %H:%M:%S +0000"), old=old)
    import app.market_news as module
    monkeypatch.setattr(module, "FEEDS", (("test", "Test Outlet", "https://feeds.example.com/rss"),))
    monkeypatch.setattr("app.market_events._since", lambda markets, symbol, since: {"change_pct": 1.5 if symbol != "SPY" else .1,
                                                                                    "thin": False, "covers_event": True})
    news = MarketNews(Fetcher({"feeds.example.com": page}), markets=object())
    value = news.news(hours=48)
    assert [item["title"][:12] for item in value["items"]] == ["Nvidia in ta", "Oil prices f"] and value["rumours"] == 1
    assert value["items"][0]["rumour"] and {move["symbol"] for move in value["items"][0]["moves"]} >= {"NVDA", "SPY"}
    assert [item["title"][:6] for item in news.news("NVDA", 48, True)["items"]] == ["Nvidia"]
    text = "\n".join(news_lines(value))
    assert "UNCONFIRMED · Nvidia in talks" in text and "nearness in time is not proof" in text


def test_chat_routes_calendar_news_and_rumours():
    assert market_request("when is the next fed meeting") == {"tool": "market_calendar", "arguments": {}}
    assert market_request("what events could move markets this week")["tool"] == "market_calendar"
    assert market_request("market rumors") == {"tool": "market_news", "arguments": {"rumours_only": True}}
    assert market_request("any rumors about NVDA") == {"tool": "market_news", "arguments": {"symbol": "NVDA", "rumours_only": True}}
    assert market_request("news about bitcoin") == {"tool": "market_news", "arguments": {"symbol": "BTC"}}
    assert market_request("news about it") is None and market_request("what is the news") is None


def test_the_trade_center_shows_the_calendar_and_news(shared_tk, tmp_path, monkeypatch):
    from app.gui_markets import TradeCenter
    from app.gui_theme import Palette
    from app.markets import MarketData
    from tests.test_markets_live import StockFetcher
    markets = MarketData(StockFetcher(), tmp_path / "markets.db")
    today = datetime.now(UTC).date()
    markets.calendar = SimpleNamespace(upcoming=lambda days: {"events": [
        {"kind": "fed", "title": "Fed interest-rate decision (FOMC)", "date": (today + timedelta(days=2)).isoformat(), "days_until": 2,
         "time": "14:00 ET", "basis": "published by the Federal Reserve",
         "effects": [{"symbol": "GLD", "ratio": 1.7, "event_move_pct": 2.57, "usual_move_pct": 1.51, "up_share_pct": 56, "events": 9}]}],
        "effects": {}, "gaps": [], "basis": "Event-day moves…"})
    markets.news = SimpleNamespace(news=lambda symbol, hours, rumours, limit: {
        "items": [{"title": "Nvidia in talks, sources say", "outlet": "Test", "published": now_iso(), "summary": "Not agreed.",
                   "link": "https://example.com/a", "markets": ["NVDA"], "rumour": True,
                   "moves": [{"symbol": "NVDA", "change_pct": 1.5}, {"symbol": "SPY", "change_pct": .1}]}],
        "rumours": 1, "outlets": ["Test"], "symbol": symbol, "rumours_only": rumours, "hours": hours})
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)

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
        window.side_tabs.select(4)
        assert wait(lambda: window.calendar_data and window.news_data)
        assert window.calendar_tree.item("0", "values")[0] == "2 days" and "GLD 1.7×" in window.calendar_tree.item("0", "values")[1]
        assert window.news_tree.item("0", "text").startswith("UNCONFIRMED · ") and window.news_tree.item("0", "values")[0] == "NVDA +1.5%"
        window.calendar_tree.selection_set("0")
        window.calendar_selected()
        assert "On past ones: GLD moved 2.57%" in window.calendar_note.cget("text")
        window.news_tree.selection_set("0")
        window.news_selected()
        assert window.calendar_note.cget("text").startswith("UNCONFIRMED: rumour wording")
        window.open_news()
        assert opened == ["https://example.com/a"]
    finally:
        window.close()


def now_iso():
    return datetime.now(UTC).isoformat(timespec="minutes")
