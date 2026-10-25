"""Deterministic randomized checks of economic thresholds and input extremes."""
from decimal import Decimal
import random

import pytest

from app.business_model import evaluate


def amount(value):
    return Decimal(value.split()[0].replace(',', ''))


def test_target_thresholds_and_sensitivities_across_150_independent_plans():
    rng = random.Random(20261019)
    for _ in range(150):
        plan = {'price': str(rng.randrange(100, 1000)), 'direct_cost': str(rng.randrange(0, 100)),
                'acquisition_cost': str(rng.randrange(0, 100)), 'hours_per_sale': str(rng.randrange(1, 9)),
                'hourly_value': str(rng.randrange(0, 20)), 'capacity_hours': str(rng.randrange(10, 200)),
                'monthly_leads': str(rng.randrange(10, 500)), 'conversion_percent': str(rng.randrange(10, 90)),
                'target_profit': str(rng.randrange(0, 10000)), 'fixed_cost': str(rng.randrange(0, 2000))}
        result = evaluate(plan)
        base = result['scenarios'][1]
        price = result['requirements']['price_for_target_at_base_sales']
        if base['sales']:
            adjusted = evaluate({**plan, 'price': str(amount(price))})['scenarios'][1]
            assert amount(adjusted['profit']) >= Decimal(plan['target_profit'])
            if amount(price) >= Decimal('.01'):
                below = evaluate({**plan, 'price': str(amount(price) - Decimal('.01'))})['scenarios'][1]
                assert amount(below['profit']) < Decimal(plan['target_profit'])
        stress = {(row['field'], row['factor']): row for row in result['sensitivity']}
        assert amount(stress['price', '1.1']['profit']) >= amount(base['profit'])
        assert amount(stress['acquisition_cost', '1.2']['profit']) <= amount(base['profit'])
        assert all(row['sales'] >= 0 for row in result['sensitivity'])


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '-Infinity', '-1', '1e1000000', '1e-1000000', '9' * 65])
def test_extreme_numeric_inputs_fail_without_hanging_or_generating_a_plan(bad):
    with pytest.raises(ValueError):
        evaluate({'price': bad})
