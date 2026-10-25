"""Time order, data admission and calibrated forecast coverage, without live APIs."""
import math
import random
from types import SimpleNamespace

import pytest

from app.market_forecast import calibrated_range, closed_history, quantile_rank
from app.market_outlook import outlook, rsi, track_record
from app.market_signals import History, combine, lab, outcomes, usual_rates


def series(seed=1, count=800):
    rng, price, rows = random.Random(seed), 100.0, []
    for index in range(count):
        price *= math.exp(rng.gauss(0, .005))
        rows.append({'time': 1700000000 + index * 3600, 'close': price, 'trades': 10})
    return rows


def test_quality_sorts_deduplicates_excludes_open_bars_and_does_not_compress_gaps():
    rows = series(count=300)
    now = rows[-1]['time'] + 1800
    raw = rows[::-1] + [rows[10], {'time': now - 3600, 'close': float('nan')}, {'time': now + 50, 'close': 99}]
    del raw[300 - 100 - 1]  # remove hour 100 in the reverse-order source
    closed, quality = closed_history(raw, now)
    assert quality['invalid_bars'] == 2 and quality['duplicates'] == 1
    assert quality['forming_bars_excluded'] == 1 and quality['gaps'] == 1
    assert closed[0]['time'] == rows[101]['time']
    assert closed[-1]['time'] == rows[-2]['time']
    assert all(b['time'] - a['time'] == 3600 for a, b in zip(closed, closed[1:]))


def test_outlook_anchors_to_completed_price_and_ignores_an_unfinished_spike(monkeypatch):
    rows = series()
    now = rows[-1]['time'] + 1800
    monkeypatch.setattr('app.market_forecast.time.time', lambda: now)
    source = SimpleNamespace(candles=lambda *_: ('BTC', 'crypto', rows))
    first = outlook(source, 'BTC')
    rows[-1]['close'] *= 100
    second = outlook(source, 'BTC')
    assert second['price'] == first['price'] == rows[-2]['close']
    assert second['ranges'] == first['ranges']
    assert second['data_quality']['forming_bars_excluded'] == 1


def test_outlook_refuses_a_short_contiguous_run_instead_of_treating_bars_as_hours():
    rows = series()
    rows[-100]['time'] += 1800
    with pytest.raises(ValueError, match='Not enough price history'):
        outlook(SimpleNamespace(candles=lambda *_: ('AAPL', 'stock', rows)), 'AAPL', 'stock')


def test_tail_rank_is_conservative_and_ranges_are_nested_with_horizon_specific_support():
    assert quantile_rank(list(range(20)), .95) == 19
    closes = [row['close'] for row in series()]
    day, week = calibrated_range(closes, 24), calibrated_range(closes, 168)
    assert day['method'] == 'past-error calibrated'
    assert week['method'] == 'volatility baseline' and week['validation']['status'] == 'limited evidence'
    assert day['wide_95'][0] <= day['likely_68'][0] <= closes[-1] <= day['likely_68'][1] <= day['wide_95'][1]
    assert day['validation']['non_overlapping'] and day['validation']['calibration_cases'] < 30


def test_calibration_is_scored_before_adding_the_test_outcome_to_its_errors(monkeypatch):
    import app.market_forecast as module
    seen = []
    original = module.quantile_rank
    def quantile(values, probability):
        seen.append(len(values))
        return original(values, probability)
    monkeypatch.setattr(module, 'quantile_rank', quantile)
    value = calibrated_range([row['close'] for row in series(count=192 + 20)], 1)
    assert seen[0] == 20  # current outcome is not the 21st calibration error
    assert value['validation']['tested'] == value['validation']['calibration_cases'] - 20


def test_baseline_direction_and_nonoverlapping_test_counts():
    closes = [100 * math.exp(.002 * index) for index in range(720)]
    measured = track_record(closes, horizon=24, step=1)
    assert measured['tested'] <= 22 and measured['momentum_right_pct'] == measured['baseline_right_pct'] == 100
    assert not measured['direction_improves_baseline']


def test_flat_price_is_neutral_rsi_instead_of_a_false_overbought_reading():
    assert rsi([100] * 30) == 50
    assert rsi(list(range(100, 130))) == 100


def test_duplicate_correlated_signals_cannot_multiply_the_same_evidence():
    reading = (1, {'calls': 100, 'hits': 65, 'expected': 50})
    assert combine(.5, [reading] * 5) == pytest.approx(combine(.5, [reading]))
    assert combine(.999, []) == .999


def test_weekly_baseline_counts_only_completed_nonoverlapping_outcomes():
    results = [1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1]
    rates = usual_rates(results, 7)
    assert rates[6] == .5 and rates[7] == pytest.approx(2 / 3)
    assert rates[13] == rates[7] and rates[14] == .75


def test_forming_daily_candle_is_not_a_known_direction_outcome():
    history = History([100, 101, 5000], [0, 86400, 172800], [1, 1, 1], completed=2)
    assert outcomes(history, 1) == [1, None, None]


def test_live_price_cannot_rewrite_an_already_completed_daily_close():
    from app.market_signals import signals
    from tests.test_market_signals import Markets, candles, noise
    rows = candles(noise(4))
    for row in rows:
        row['time'] -= 86400
    found = signals(Markets({'ALT': rows}), 'ALT', live_price=1000000)
    assert not found['live'] and found['price'] == rows[-1]['close']
    assert found['closed_history_days'] == len(rows)


def test_bad_holdout_withholds_a_historical_signal_shift(monkeypatch):
    import app.market_signals as module
    rng, move, closes = random.Random(3), 0, [100.0]
    for _ in range(720):
        move = .5 * move + rng.gauss(0, .02)
        closes.append(closes[-1] * math.exp(move))
    history = History(closes, [i * 86400 for i in range(len(closes))], [10] * len(closes))
    monkeypatch.setattr(module, 'holdout', lambda *_: {'chosen': ['momentum_5'], 'tested': 50,
                                                    'skill_pct': -10, 'short': 'worse', 'verdict': 'worse'})
    found = lab(history, None)
    assert any(row['status'] == 'works' for row in found['signals'])
    for horizon in found['horizons']:
        assert not horizon['validation_passed'] and not horizon['from_signals']
        assert horizon['odds_up_pct'] == horizon['usual_up_pct']


def test_current_odds_use_the_frozen_model_that_was_actually_tested(monkeypatch):
    import app.market_signals as module
    history = History([row['close'] for row in series(count=720)], list(range(720)), [10] * 720)
    # A later-period check can select a different set from the descriptive full-history table.
    monkeypatch.setattr(module, 'holdout', lambda *_: {'chosen': ['weekday'], 'active': ['weekday'], 'tested': 50,
                                                    'current_odds': .61, 'skill_pct': 5, 'short': 'helped', 'verdict': 'helped'})
    found = lab(history, None)
    for horizon in found['horizons']:
        assert horizon['validation_passed'] and horizon['odds_up_pct'] == 61
        assert horizon['from_signals'] == ['weekday']
