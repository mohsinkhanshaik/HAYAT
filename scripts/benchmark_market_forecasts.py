"""Reproducible prequential range check; synthetic prices, no API calls or trading."""
import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_forecast import calibrated_range


def path(kind, seed, count):
    rng, price, closes = random.Random(seed), 100.0, [100.0]
    for index in range(count):
        sigma = .004 if kind != 'volatility shifts' or index // 240 % 2 == 0 else .016
        value = rng.gauss(0, sigma)
        if kind == 'heavy tails':
            value /= math.sqrt(sum(rng.gauss(0, 1) ** 2 for _ in range(3)) / 3)
        price *= math.exp(value)
        closes.append(price)
    return closes


def run(count=6000):
    rows = []
    for kind in ('normal walk', 'heavy tails', 'volatility shifts'):
        for hours in (1, 24, 168):
            results = [calibrated_range(path(kind, seed, count), hours)['validation'] for seed in (11, 29, 47, 71, 97)]
            tested = sum(item['tested'] for item in results)
            def pooled(key):
                return round(sum((item[key] or 0) * item['tested'] for item in results) / tested, 2) if tested else None
            rows.append({'process': kind, 'horizon_hours': hours, 'later_tests': tested,
                         'calibrated_68_pct': pooled('coverage_68_pct'), 'baseline_68_pct': pooled('matched_baseline_68_pct'),
                         'calibrated_95_pct': pooled('coverage_95_pct'), 'baseline_95_pct': pooled('matched_baseline_95_pct')})
    return {'observations_per_path': count + 1, 'fixed_seeds': [11, 29, 47, 71, 97], 'rows': rows,
            'method': 'Non-overlapping outcomes; each calibration uses only earlier completed forecast errors. Baselines use the same test origins.',
            'limitations': 'Synthetic processes test calibration mechanics, not live profitability or future stock/crypto directional accuracy. Regime changes can degrade coverage.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.test-runs/market-forecast-benchmark.json')
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
