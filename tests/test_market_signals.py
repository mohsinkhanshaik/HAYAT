"""Signal lab and market influences: tested on the market's own past, corrected for luck, said plainly in chat."""
import math
import random
import time

import pytest

from app.market_influences import THIN, daily_moves, fit, influences, measure
from app.market_influences import report_lines as influence_lines
from app.market_signals import (CLAMP, History, all_calls, benjamini_hochberg, catalogue, combine, outcomes, score, signals,
                                usual_rates, weekday_calls)
from app.market_signals import report_lines as signal_lines
from app.markets import market_request, register_market_tools, report

DAY = 86400


def candles(moves, start=100.0, trades=50, flat_every=0):
    """Daily candles from log moves, ending today; `flat_every` repeats the close every n-th day (a thin market)."""
    now = int(time.time()) // DAY * DAY
    price, rows = start, []
    for index, move in enumerate(moves):
        if not (flat_every and index % flat_every):
            price *= math.exp(move)
        rows.append({"time": now - DAY * (len(moves) - 1 - index), "open": price, "high": price, "low": price, "close": price,
                     "volume": 5.0 + (index % 7), "trades": trades})
    return rows


def noise(seed, count=720, sigma=.02):
    rng = random.Random(seed)
    return [rng.gauss(0, sigma) for _ in range(count)]


def persistent(seed, count=720, keep=.35, sigma=.02):
    """Each day repeats part of the day before: a market where short momentum really works."""
    rng, last, moves = random.Random(seed), 0.0, []
    for _ in range(count):
        last = keep * last + rng.gauss(0, sigma)
        moves.append(last)
    return moves


class Markets:
    def __init__(self, series, kinds=None, live=None):
        self.series, self.kinds, self.calls = series, kinds or {}, []
        if live is not None:
            from types import SimpleNamespace
            self.live = SimpleNamespace(snapshot=lambda names: {"prices": {name: {"price": live} for name in names}})

    def candles(self, symbol, interval, asset="auto"):
        self.calls.append((symbol, interval, asset))
        if symbol not in self.series:
            raise ValueError(f"Kraken lists no market for {symbol}.")
        return symbol, self.kinds.get(symbol, "crypto"), self.series[symbol]


# ---------------------------------------------------------------------- signal lab
def test_the_luck_test_keeps_only_what_survives_many_tries():
    assert benjamini_hochberg([.001, .2, .5, .04]) == [True, False, False, True]  # rank 2 of 4 at 10 %: under .05 passes
    assert benjamini_hochberg([.001, .2, .5, .06]) == [True, False, False, False]
    assert benjamini_hochberg([.001, .002, .003]) == [True, True, True]
    assert benjamini_hochberg([.03] + [.5] * 19) == [False] * 20  # one p = .03 among twenty tries is what luck gives


def test_a_random_walk_has_no_signal_and_says_so():
    value = signals(Markets({"ALT": candles(noise(1)), "BTC": candles(noise(2))}), "ALT")
    assert value["signals_view"] and value["history_days"] == 720 and value["leader"] == "Bitcoin"
    assert not [row for row in value["signals"] if row["status"] == "works"]
    for horizon in value["horizons"]:
        assert horizon["odds_up_pct"] == horizon["usual_up_pct"] and horizon["from_signals"] == []
        assert "nothing to combine" in horizon["holdout"]["verdict"]
        low, high = horizon["usual_interval_pct"]
        assert low < horizon["usual_up_pct"] < high  # centred on its own share
    assert "none beat chance after allowing for luck" in value["summary"]
    keys = {row["key"] for row in value["signals"]}
    assert {"momentum_5", "momentum_20", "trend_50", "trend_200", "reversal", "rsi", "streak", "volume", "weekday", "lead"} == keys
    assert len(value["signals"]) == 19  # ten signals for the next day, nine for the week (weekday is next-day only)


def test_real_momentum_is_found_and_moves_the_odds_toward_todays_call():
    value = signals(Markets({"MOM": candles(persistent(3, keep=.45))}), "MOM", "crypto")
    working = [row for row in value["signals"] if row["status"] == "works"]
    assert working and all(row["hit_pct"] > row["chance_pct"] for row in working)
    assert any(row["key"].startswith("momentum") or row["key"] == "trend_50" for row in working)
    day = value["horizons"][0]
    if day["from_signals"]:
        calls = {row["key"]: row["now"] for row in working if row["horizon"] == "1d"}
        direction = sum(calls[key] for key in day["from_signals"])
        assert (day["odds_up_pct"] - day["usual_up_pct"]) * direction > 0
        assert abs(day["odds_up_pct"] - day["usual_up_pct"]) <= CLAMP * 100 + .1


def test_a_leading_market_is_recognised_only_when_it_leads():
    leader = noise(4, sigma=.03)
    follower = [.6 * before + random.Random(index).gauss(0, .012) for index, before in enumerate([0.0] + leader[:-1])]
    value = signals(Markets({"ALT": candles(follower), "BTC": candles(leader)}), "ALT")
    lead = next(row for row in value["signals"] if row["key"] == "lead" and row["horizon"] == "1d")
    assert lead["status"] == "works" and lead["hit_pct"] > 60
    stock = signals(Markets({"AAPL": candles(noise(5)), "SPY": candles(noise(6))}, {"AAPL": "stock", "SPY": "stock"}), "AAPL")
    assert stock["leader"] == "the S&P 500"
    bitcoin = signals(Markets({"BTC": candles(noise(7)), "SPY": candles(noise(8))}, {"SPY": "stock"}), "BTC")
    assert bitcoin["leader"] == "the S&P 500"  # Bitcoin is not its own leader


def test_no_call_ever_looks_at_the_future():
    moves = noise(9)
    base = candles(moves)
    changed = [dict(row) for row in base]
    for row in changed[400:]:
        row["close"] *= 3.0
        row["volume"] *= 9
    first = History([row["close"] for row in base], [row["time"] for row in base], [row["volume"] for row in base])
    second = History([row["close"] for row in changed], [row["time"] for row in changed], [row["volume"] for row in changed])
    signals_list = catalogue(None)
    before, after = all_calls(first, signals_list), all_calls(second, signals_list)
    for key in before:
        assert before[key][:399] == after[key][:399], key
    assert weekday_calls(first)[:399] == weekday_calls(second)[:399]
    rates_first = usual_rates(outcomes(first, 1), 1)
    rates_second = usual_rates(outcomes(second, 1), 1)
    assert rates_first[:399] == rates_second[:399]


def test_always_up_on_a_rising_market_is_not_an_edge():
    rising = [move + .004 for move in noise(10, sigma=.01)]
    history = History([row["close"] for row in candles(rising)], [row["time"] for row in candles(rising)], [1.0] * 720)
    results = outcomes(history, 1)
    rates = usual_rates(results, 1)
    stats = score([1] * len(history), results, rates, list(range(60, 719)))
    assert stats["hits"] / stats["calls"] > .6  # it did rise most days...
    assert abs(stats["z"]) < 2.5 and stats["p_value"] > .01  # ...but the usual up-rate already said so


def test_combining_shrinks_and_caps():
    stats = {"calls": 50, "hits": 45, "expected": 25.0}
    assert combine(.5, [(1, stats)]) <= .5 + CLAMP and combine(.5, [(1, stats)]) > .5
    assert combine(.5, [(-1, stats)]) < .5
    assert combine(.5, [(0, stats)]) == pytest.approx(.5)
    small = {"calls": 5, "hits": 5, "expected": 2.5}
    assert combine(.5, [(1, small)]) - .5 < combine(.5, [(1, stats)]) - .5  # five right calls barely count


def test_short_history_and_the_live_price():
    with pytest.raises(ValueError, match="only 150 days"):
        signals(Markets({"NEW": candles(noise(11, count=150))}), "NEW")
    value = signals(Markets({"ALT": candles(noise(12)), "BTC": candles(noise(13))}, live=123.45), "ALT")
    assert value["live"] and value["price"] == 123.45 and "live streamed price" in value["source"]


def test_the_signal_reply_is_plain_and_honest():
    value = signals(Markets({"ALT": candles(noise(14)), "BTC": candles(noise(15))}), "ALT")
    lines = signal_lines(value)
    assert lines[0].startswith("ALT signal lab (tested on its own past, not a forecast)")
    assert any(line.startswith("Next day: usually higher") for line in lines) and any("Later-third check" in line for line in lines)
    assert "never a forecast" in lines[-1] and "HAYAT never trades" in lines[-1]
    text = report([{"tool": "market_signals", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert "signal lab" in text and "HAYAT does not trade" in text


# ---------------------------------------------------------------------- influences
def test_fit_recovers_an_exact_relation():
    first, second = noise(20, 100), noise(21, 100)
    target = [.5 * a - .25 * b + .001 for a, b in zip(first, second)]
    slopes, intercept, r2 = fit(target, [first, second])
    assert slopes == pytest.approx([.5, -.25], abs=1e-3) and intercept == pytest.approx(.001, abs=1e-4) and r2 == pytest.approx(1, abs=1e-6)


def linked_markets(beta=.8, lead=False, thin_driver=None):
    btc = noise(30, sigma=.03)
    own = noise(31, sigma=.012)
    source = [0.0] + btc[:-1] if lead else btc
    alt = [beta * driver + extra for driver, extra in zip(source, own)]
    series = {"ALT": candles(alt), "BTC": candles(btc), "SPY": candles(noise(32, sigma=.01)), "GLD": candles(noise(33, sigma=.008))}
    if thin_driver:
        series[thin_driver] = candles(noise(34), trades=1, flat_every=2)
    kinds = {"SPY": "stock", "GLD": "stock", "QQQ": "stock", "SMH": "stock"}
    return Markets(series, kinds)


def test_a_planted_link_is_measured_with_its_size_today_and_sell_offs():
    value = influences(linked_markets(), "ALT", "crypto")
    top = value["drivers"][0]
    assert top["symbol"] == "BTC" and top["strength"] == "strong" and top["steady"]
    assert top["beta_90"] == pytest.approx(.8, abs=.12) and top["corr_90"] > .8
    assert top["stress"]["move_pct"] < -2 * top["stress"]["usual_abs_pct"] / 2  # it falls on Bitcoin's worst days
    assert not top["lead"]["significant"]  # same-day link, no lead
    assert "Moves closely with Bitcoin" in top["text"] and value["summary"].startswith("ALT: Moves closely with Bitcoin")
    model = value["model"]
    assert model["r2_pct"] > 70 and "BTC" in model["drivers"]
    split = model["today"]
    assert math.log(1 + split["actual_pct"] / 100) == pytest.approx(
        math.log(1 + split["with_markets_pct"] / 100) + math.log(1 + split["own_pct"] / 100), abs=2e-4)
    assert {gap.split(":")[0] for gap in value["gaps"]} >= {"Ethereum", "Nasdaq-100"}  # missing markets are named, not hidden


def test_a_lead_counts_only_past_the_strict_bar():
    value = influences(linked_markets(lead=True), "ALT", "crypto")
    bitcoin = next(row for row in value["drivers"] if row["symbol"] == "BTC")
    assert bitcoin["lead"]["significant"] and "Leading: Bitcoin" in value["summary"]
    assert not any(row["lead"]["significant"] for row in value["drivers"] if row["symbol"] != "BTC")


def test_a_thinly_traded_market_is_refused_not_called_unlinked():
    rows = candles(noise(35), trades=1, flat_every=2)
    assert daily_moves(rows) and THIN < .5
    value = influences(linked_markets(thin_driver="SMH"), "ALT", "crypto")
    assert "SMH" not in {row["symbol"] for row in value["drivers"]}
    assert any(gap.startswith("Chip stocks: thinly traded on Kraken") for gap in value["gaps"])


def test_measure_needs_shared_days_and_influences_need_history():
    assert measure({1: .1}, {1: .1}) is None
    with pytest.raises(ValueError, match="links need at least"):
        influences(Markets({"NEW": candles(noise(36, count=20))}), "NEW")


def test_the_influence_reply_says_co_movement_not_cause():
    value = influences(linked_markets(), "ALT", "crypto")
    lines = influence_lines(value)
    assert lines[0].startswith("What moves ALT (co-movement measured on daily moves, not cause)")
    assert any(line.startswith("Today's ") and "its own" in line for line in lines)
    assert any(line.startswith("Unavailable: Ethereum") for line in lines) and "not cause" in lines[-1]
    text = report([{"tool": "market_influences", "result": {"success": True, "output": value}, "verification": {"passed": True}}])
    assert "What moves ALT" in text


# ---------------------------------------------------------------------- chat
@pytest.mark.parametrize("text, tool, arguments", [
    ("which signals work for bitcoin?", "market_signals", {"symbol": "BTC", "asset": "crypto"}),
    ("test the signals for ETH", "market_signals", {"symbol": "ETH", "asset": "crypto"}),
    ("backtest solana", "market_signals", {"symbol": "SOL", "asset": "crypto"}),
    ("show me the signal lab for apple stock", "market_signals", {"symbol": "AAPL", "asset": "stock"}),
    ("is bitcoin predictable?", "market_signals", {"symbol": "BTC", "asset": "crypto"}),
    ("does momentum work for BTC", "market_signals", {"symbol": "BTC", "asset": "crypto"}),
    ("what drives bitcoin?", "market_influences", {"symbol": "BTC", "asset": "crypto"}),
    ("what moves apple stock", "market_influences", {"symbol": "AAPL", "asset": "stock"}),
    ("what affects nvidia", "market_influences", {"symbol": "NVDA", "asset": "stock"}),
    ("what is ETH correlated with?", "market_influences", {"symbol": "ETH", "asset": "crypto"}),
    ("what does solana move with?", "market_influences", {"symbol": "SOL", "asset": "crypto"}),
])
def test_chat_routes_signal_and_influence_questions(text, tool, arguments):
    assert market_request(text) == {"tool": tool, "arguments": arguments}


@pytest.mark.parametrize("text", ["what moves it", "backtest it", "what drives you"])
def test_lowercase_words_are_not_tickers(text):
    assert market_request(text) is None


def test_neighbouring_questions_keep_their_tools():
    assert market_request("what might move bitcoin")["tool"] == "market_scenarios"
    assert market_request("why is bitcoin down")["tool"] == "market_scenarios"
    assert market_request("will bitcoin go up next month")["tool"] == "market_periods"


def test_both_tools_are_registered_read_only():
    from app.markets import MarketData, register_market_event_tools
    found = {}

    class Registry:
        def register(self, name, description, schema, risk, category, handler):
            found[name] = (description, schema, risk, category)
    register_market_tools(Registry(), MarketData(None))
    register_market_event_tools(Registry(), None, MarketData(None))
    for name in ("market_signals", "market_influences"):
        description, schema, risk, category = found[name]
        assert (risk, category) == ("low", "public_api_get") and schema["required"] == ["symbol"]
    assert "corrected for luck" in found["market_signals"][0] and "not cause" in found["market_influences"][0]
