"""Past-only, horizon-specific range calibration and hourly history admission."""
import math
import statistics
import time

WINDOW = 168
MIN_CALIBRATION = 20


def closed_history(candles, now=None):
    """Validate, order and deduplicate hourly bars; never compress missing hours."""
    now = time.time() if now is None else now
    unique = {}
    invalid = duplicates = forming = 0
    for candle in candles:
        try:
            stamp, close = float(candle['time']), float(candle['close'])
            if not math.isfinite(stamp) or not math.isfinite(close) or stamp <= 0 or close <= 0 or stamp > now:
                raise ValueError('invalid bar')
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid += 1
            continue
        if stamp in unique:
            duplicates += 1
        unique[stamp] = dict(candle, time=stamp, close=close)
    ordered = [unique[key] for key in sorted(unique)]
    closed = [row for row in ordered if row['time'] + 3600 <= now]
    forming = len(ordered) - len(closed)
    gaps = sum(abs(b['time'] - a['time'] - 3600) > 1 for a, b in zip(closed, closed[1:]))
    # A 24-bar forecast means 24 hours only on a contiguous run. No interpolation.
    start = 0
    for index in range(1, len(closed)):
        if abs(closed[index]['time'] - closed[index - 1]['time'] - 3600) > 1:
            start = index
    contiguous = closed[start:]
    age = max(0, now - closed[-1]['time'] - 3600) if closed else None
    return contiguous, {
        'invalid_bars': invalid, 'duplicates': duplicates, 'forming_bars_excluded': forming,
        'gaps': gaps, 'closed_bars': len(closed), 'contiguous_hours': len(contiguous),
        'discarded_before_gap': start, 'age_seconds': round(age, 1) if age is not None else None,
        'stale': age is None or age > 7200,
        'price_basis': 'last completed hourly candle',
    }


def quantile_rank(values, probability):
    """Finite-sample upper rank, rather than interpolating a tail below its target."""
    ordered = sorted(values)
    rank = min(len(ordered), math.ceil((len(ordered) + 1) * probability))
    return ordered[max(0, rank - 1)]


def calibrated_range(closes, horizon):
    """Score non-overlapping outcomes; calibrate each test only with earlier finished errors."""
    scores = []
    raw_hits = [0, 0]
    matched_raw = [0, 0]
    hits = [0, 0]
    tested = 0
    for origin in range(WINDOW, len(closes) - horizon, horizon):
        logs = [math.log(b / a) for a, b in zip(closes[origin - WINDOW:origin], closes[origin - WINDOW + 1:origin + 1])]
        sigma = statistics.pstdev(logs)
        if sigma <= 1e-12:
            continue
        score = abs(math.log(closes[origin + horizon] / closes[origin])) / (sigma * math.sqrt(horizon))
        raw_hits[0] += score <= 1
        raw_hits[1] += score <= 1.96
        if len(scores) >= MIN_CALIBRATION:
            factors = [quantile_rank(scores[-100:], p) for p in (.683, .95)]
            for index in range(2):
                hits[index] += score <= factors[index]
            matched_raw[0] += score <= 1
            matched_raw[1] += score <= 1.96
            tested += 1
        scores.append(score)  # only after the current prediction was scored
    calibrated = len(scores) >= MIN_CALIBRATION
    factors = [quantile_rank(scores[-100:], p) for p in (.683, .95)] if calibrated else [1.0, 1.96]
    sigma = statistics.pstdev([math.log(b / a) for a, b in zip(closes[-WINDOW - 1:-1], closes[-WINDOW:])])
    center = math.log(closes[-1])
    ranges = []
    for factor in factors:
        spread = factor * sigma * math.sqrt(horizon)
        ranges.append([math.exp(max(-700, center - spread)), math.exp(min(700, center + spread))])
    pct = lambda count, total: round(count / total * 100, 1) if total else None
    return {
        'likely_68': ranges[0], 'wide_95': ranges[1],
        'method': 'past-error calibrated' if calibrated else 'volatility baseline',
        'validation': {
            'horizon_hours': horizon, 'non_overlapping': True, 'calibration_cases': len(scores),
            'tested': tested, 'coverage_68_pct': pct(hits[0], tested), 'coverage_95_pct': pct(hits[1], tested),
            'baseline_tested': len(scores), 'baseline_68_pct': pct(raw_hits[0], len(scores)),
            'baseline_95_pct': pct(raw_hits[1], len(scores)),
            'matched_baseline_68_pct': pct(matched_raw[0], tested),
            'matched_baseline_95_pct': pct(matched_raw[1], tested),
            'status': 'measured' if tested >= 20 else 'limited evidence',
            'note': ('Each test uses only earlier finished forecast errors. Future coverage is not guaranteed.' if tested >= 20
                     else 'Too few non-overlapping later tests to establish reliability at this horizon.'),
        },
    }
