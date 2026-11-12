"""Trade Center intelligence (app/market_intelligence.py), 2026-11-09: a scorecard that learns from HAYAT's own
forecasts, event impact with a chance test, CFTC positioning, news tone, and a plain brief. All offline fixtures."""
from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.market_intelligence import (DAY, MIN_SCORED, ForecastLedger, apply_track_record, brief_text, event_study,
                                     pearson, permutation_p, positioning, sentiment)

START = datetime(2025, 1, 1, tzinfo=UTC).timestamp()


class Markets:
    """Daily (and weekly) candles from a seeded random walk; a fixed drift can be injected after given days."""

    def __init__(self, days=720, seed=3, bumps=None):
        generator = random.Random(seed)
        price, self.daily = 100.0, []
        for index in range(days):
            price *= 1 + generator.gauss(0, .02) + (bumps or {}).get(index, 0)
            self.daily.append({"time": int(START + index * DAY), "open": price, "high": price, "low": price,
                               "close": price, "volume": 1, "trades": 10})
        self.fetcher = None

    def candles(self, symbol, interval=1440, asset="auto"):
        if interval == 10080:
            return symbol, "crypto", self.daily[::7]
        return symbol, "crypto", list(self.daily)


def day(index):
    return datetime.fromtimestamp(START + index * DAY, UTC)


def test_statistics_say_no_link_for_noise_and_find_a_real_one():
    generator = random.Random(1)
    noise = [generator.gauss(0, 1) for _ in range(200)]
    assert pearson(noise, [generator.gauss(0, 1) for _ in range(200)])[1] > .01
    r, p = pearson(noise, [value * .8 + generator.gauss(0, .5) for value in noise])
    assert r > .7 and p < .001
    assert pearson([1, 2, 3], [1, 2, 3]) == (None, None)  # too few
    population = [generator.gauss(0, 1) for _ in range(2000)]
    assert permutation_p([value + 1.0 for value in population[:30]], population) < .01
    assert permutation_p(population[:30], population) > .05


def test_forecasts_are_recorded_once_a_day_scored_and_a_bad_model_is_replaced(tmp_path):
    markets = Markets()
    ledger = ForecastLedger(tmp_path / "forecasts.db")
    issued = START + 600 * DAY
    assert ledger.record("BTC", "crypto", "situation odds", 1, markets.daily[600]["close"], .9, .5, now=issued)
    assert not ledger.record("BTC", "crypto", "situation odds", 1, 1.0, .9, .5, now=issued + 3600)  # same day: one forecast
    # A confident model that is right only by chance: 30 daily calls of 90 % "up".
    for index in range(601, 631):
        ledger.record("BTC", "crypto", "situation odds", 1, markets.daily[index]["close"], .9, .5, now=START + index * DAY)
    assert ledger.resolve(markets, now=START + 700 * DAY) == 31
    card = ledger.record_card("BTC")["cards"][0]
    assert card["scored"] == 31 and card["brier"] > card["usual_rate_brier"] and card["skill"] < 0 and not card["trusted"]
    assert "Worse than the usual rate" in card["verdict"]
    value = {"symbol": "BTC", "summary": "s", "next_day": {"now": {"up_pct": 90}, "usual": {"up_pct": 52}},
             "next_week": {"now": {"up_pct": 60}, "usual": {"up_pct": 55}, "days": 7}}
    corrected = apply_track_record(value, ledger)
    assert corrected["next_day"]["now"]["up_pct"] == 52 and corrected["next_day"]["replaced_by_usual_rate"]
    assert corrected["lessons"] and "did worse than the usual rate over 31" in corrected["summary"]
    assert "track_record" not in corrected["next_week"]  # nothing scored for the week yet: left alone


def test_a_scenarios_result_records_odds_and_its_range(tmp_path):
    ledger = ForecastLedger(tmp_path / "forecasts.db")
    value = {"symbol": "ETH", "asset": "crypto", "price": 3000.0,
             "next_day": {"now": {"up_pct": 55}, "usual": {"up_pct": 51}},
             "next_week": {"now": {"up_pct": 58}, "usual": {"up_pct": 53}, "days": 7},
             "paths": {"weak": {"price": 2800.0}, "middle": {"price": 3010.0}, "strong": {"price": 3250.0}}}
    assert ledger.record_scenarios(value, now=START) == 2
    assert "No forecast has come due yet (2 waiting)" in ledger.record_card()["summary"]
    assert MIN_SCORED == 20


def test_event_impact_finds_a_real_reaction_and_calls_noise_noise():
    from app.intelligence.classify import CATEGORIES

    category = next(iter(CATEGORIES))
    event_days = list(range(200, 700, 25))  # 20 event days
    reacted = Markets(bumps={index: -.05 for index in event_days})
    plain = Markets()
    manager = SimpleNamespace(_matching=lambda *args: ([], [{"observed_at": day(index).isoformat()} for index in event_days]))
    found = event_study(manager, reacted, "BTC", category)
    assert found["event_days"] == 20 and found["p_value"] < .05 and found["mean_move_after_pct"] < -3
    assert "chance alone gives a gap that large only" in found["verdict"]
    calm = event_study(manager, plain, "BTC", category)
    assert calm["p_value"] > .05 and calm["verdict"].startswith("No measurable effect")
    few = SimpleNamespace(_matching=lambda *args: ([], [{"observed_at": day(300).isoformat()}]))
    assert event_study(few, plain, "BTC", category)["verdict"].startswith("Too few to measure")
    with pytest.raises(ValueError):
        event_study(manager, plain, "BTC", "not-a-kind")


def _cot_rows(weeks=60):
    rows = []
    for week in range(weeks):
        moment = datetime.fromtimestamp(START + 400 * DAY + week * 7 * DAY, UTC)
        lev_short = 7000 + (3000 if week == weeks - 1 else (week % 5) * 100)
        rows.append({"report_date_as_yyyy_mm_dd": moment.strftime("%Y-%m-%dT00:00:00.000"), "open_interest_all": "17000",
                     "asset_mgr_positions_long": "4400", "asset_mgr_positions_short": "2000",
                     "lev_money_positions_long": "5100", "lev_money_positions_short": str(lev_short),
                     "dealer_positions_long_all": "6000", "dealer_positions_short_all": "5500"})
    return rows


def test_positioning_reads_the_cftc_report_and_flags_crowding():
    value = positioning(None, "bitcoin", Markets(), rows=_cot_rows())
    lev = value["groups"]["lev_money"]
    assert value["market"] == "BTC" and value["contract"] == "Bitcoin futures, CME"
    assert lev["net_contracts"] == 5100 - 10000 and lev["reading"] == "near its most short in three years" and lev["extreme"]
    assert lev["weekly_change_contracts"] < 0 and value["leads_next_week"]["weeks"] > 30
    assert "CFTC Commitments of Traders" in value["source"] and "a week old" in value["basis"]
    with pytest.raises(ValueError, match="published for"):
        positioning(None, "dogecoin", rows=_cot_rows())


def test_positioning_rows_ask_the_cftc_api_for_the_contract():
    from app.market_intelligence import positioning_rows

    seen = {}

    def fetch(url, maximum, accept):
        seen["url"] = url
        return SimpleNamespace(content=json.dumps(_cot_rows(10)).encode())
    rows = positioning_rows(SimpleNamespace(fetch=fetch), "133741")
    assert "publicreporting.cftc.gov/resource/gpe5-46if.json" in seen["url"] and "133741" in seen["url"]
    assert rows[0]["report_date_as_yyyy_mm_dd"] < rows[-1]["report_date_as_yyyy_mm_dd"]


def test_news_tone_is_compared_with_usual_and_tested_against_the_next_day():
    markets = Markets()
    series = [(day(index).strftime("%Y-%m-%d"), -1.0 + math.sin(index / 5)) for index in range(600, 690)]
    series[-7:] = [(day_, -4.0) for day_, _value in series[-7:]]
    value = sentiment(None, markets, "BTC", series=series)
    assert value["reading"] == "more negative than usual" and value["tone_last_7_days"] == -4.0
    assert value["leads_next_day"]["days"] > 60 and "GDELT" in value["source"]
    assert "Tone measures wording" in value["basis"]


def test_gdelt_throttling_is_reported_not_crashed():
    from app.market_intelligence import _tone_cache, tone_series

    _tone_cache.clear()
    fetcher = SimpleNamespace(fetch=lambda *args: SimpleNamespace(content=b"Please limit requests to one every 5 seconds"))
    with pytest.raises(ValueError, match="one every five seconds"):
        tone_series(fetcher, "bitcoin-test")
    good = {"timeline": [{"series": "Average Tone", "data": [{"date": "20261101T000000Z", "value": -1.5},
                                                             {"date": "20261101T120000Z", "value": -0.5}]}]}
    fetcher = SimpleNamespace(fetch=lambda *args: SimpleNamespace(content=json.dumps(good).encode()))
    assert tone_series(fetcher, "bitcoin-test-2") == [("2026-11-01", -1.0)]


def test_the_brief_reads_plainly_and_never_promises():
    sections = {"scenarios": {"price": 65000.0, "source": "Kraken daily candles", "situation": {"trend": "up"},
                              "next_week": {"now": {"up_pct": 57, "samples": 80}, "usual": {"up_pct": 53},
                                            "verdict": "within the noise"},
                              "paths": {label: {"move_pct": move} for label, move in (("weak", -8), ("middle", 1), ("strong", 9))},
                              "big_move": {"threshold_pct": 5, "now_pct": 4.2}},
                "positioning": {"summary": "Leveraged funds are short.", "groups": {"lev_money": {
                    "who": "leveraged funds", "reading": "near its most short in three years", "extreme": True}}},
                "track_record": {"summary": "No forecast has come due yet."}}
    text = brief_text("btc", sections, ["news tone: GDELT asked for fewer requests"])
    assert "higher in 57 % of 80 similar past cases" in text and "-8.0 % / +1.0 % / +9.0 %" in text
    assert "three-year extreme among leveraged funds" in text and "Not available this time: news tone" in text
    assert ".." not in text
    assert "not predictions or advice" in text and "guarantee" not in text.lower()


def test_the_trade_center_opens_a_full_brief(shared_tk, tmp_path, monkeypatch):
    import time

    import app.market_intelligence as intelligence
    from app.gui_markets import TradeCenter
    from app.gui_theme import Palette
    from app.markets import MarketData
    from tests.test_markets_live import StockFetcher

    markets = MarketData(StockFetcher(), tmp_path / "markets.db")
    monkeypatch.setattr(intelligence, "brief", lambda markets, manager, ledger, symbol, asset="auto": {
        "symbol": symbol, "text": f"{symbol} — what the evidence says\nThese are odds from the past, not predictions or advice."})

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(markets=markets, intelligence=None)
    window = TradeCenter(gui)
    try:
        assert window.brief_button.cget("text") == "Full brief"
        window.full_brief()
        deadline = time.time() + 10
        while time.time() < deadline and getattr(window, "_brief_window", None) is None:
            shared_tk.update()
            time.sleep(.02)
        text = window._brief_window.text.get("1.0", "end")
        assert "what the evidence says" in text and "not predictions or advice" in text
        assert window.markets.ledger is not None  # the scorecard store exists beside the market store
    finally:
        window.close()


def test_the_tools_are_registered_with_read_only_permissions(tmp_path):
    from app.markets import register_market_intelligence_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    markets = Markets()
    ledger = ForecastLedger(tmp_path / "f.db")
    register_market_intelligence_tools(registry, SimpleNamespace(_matching=lambda *a: ([], [])), markets, ledger)
    tools = {item["name"]: item for item in registry.describe()}
    assert {"market_track_record", "market_event_study", "market_positioning", "market_sentiment", "market_brief"} <= set(tools)
    assert all(tools[name]["permission"] == "public_api_get" for name in tools)
    result = registry.execute("market_track_record", {})
    assert result.success and result.output["cards"] == []
