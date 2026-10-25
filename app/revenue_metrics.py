"""Deterministic calculations for revenue workflows; inputs and unknowns stay visible."""
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median


def number(data, key, default=None, *, maximum=Decimal('1000000000')):
    raw = data.get(key, default)
    if raw is None:
        raise ValueError(f'{key} is required; supply the actual value or an explicit assumption.')
    if isinstance(raw, bool):
        raise ValueError(f'{key} must be a number, not a boolean.')
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'{key} must be a finite number.') from exc
    if not value.is_finite() or not 0 <= value <= maximum:
        raise ValueError(f'{key} must be between 0 and {maximum}.')
    return value


def amount(value):
    return str(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def count(data, key, default=None):
    value = number(data, key, default)
    if value != value.to_integral_value():
        raise ValueError(f'{key} must be a whole count.')
    return int(value)


def ratio(numerator, denominator):
    return round(float(numerator / denominator * 100), 2) if denominator else None


def empirical_probability(data):
    wins, closed = count(data, 'wins', 0), count(data, 'closed', 0)
    if wins > closed:
        raise ValueError('wins cannot exceed closed outcomes.')
    if not closed:
        return {'probability': None, 'sample_size': 0, 'reason': 'No observed closed outcomes; no close probability can be estimated.'}
    p, z = wins / closed, 1.96
    center = (p + z * z / (2 * closed)) / (1 + z * z / closed)
    radius = z * math.sqrt(p * (1 - p) / closed + z * z / (4 * closed * closed)) / (1 + z * z / closed)
    return {'probability': round(p, 4), 'sample_size': closed,
            'interval_95': [round(max(0, center - radius), 4), round(min(1, center + radius), 4)],
            'basis': 'Historical aggregate win rate, Wilson interval; not a calibrated probability for this specific prospect.',
            'sample_warning': 'Small sample; collect more comparable outcomes.' if closed < 30 else ''}


def score(name, data):
    if name == 'score_job_opportunity':
        factors = {'pay': (25, False), 'difficulty': (15, True), 'competition': (15, True), 'fit': (45, False)}
    elif name == 'churn_risk':
        factors = {'disengagement': (40, False), 'complaints': (40, False), 'payment_delay': (20, False)}
    elif name == 'viral_topic_score':
        factors = {'hook': (35, False), 'audience_relevance': (35, False), 'originality': (20, False), 'timeliness': (10, False)}
    elif name == 'feature_priority':
        reach, impact = number(data, 'reach'), number(data, 'impact')
        confidence = number(data, 'confidence', maximum=Decimal(100))
        effort = number(data, 'effort')
        if not effort:
            raise ValueError('effort must be greater than zero.')
        return {'priority': float(reach * impact * confidence / 100 / effort),
                'method': 'RICE-style reach × impact × confidence / effort; supplied estimates, not revenue prediction.', 'inputs': data}
    else:
        factors = {'need': (35, False), 'fit': (30, False), 'budget': (20, False), 'engagement': (15, False)}
    points, weight, evidence, missing = Decimal(0), 0, {}, []
    for key, (importance, inverted) in factors.items():
        if key not in data:
            missing.append(key)
            continue
        value = number(data, key, maximum=Decimal(100))
        evidence[key] = float(value)
        points += (100 - value if inverted else value) * importance
        weight += importance
    return {'score': round(float(points / weight), 1) if weight else None,
            'coverage_percent': weight, 'signals': evidence, 'missing_signals': missing,
            'basis': 'Transparent heuristic from supplied 0–100 signals; unknown inputs are not scored as zero.',
            'close_probability': empirical_probability(data) if name == 'score_job_opportunity' else None,
            'virality_probability': None if name == 'viral_topic_score' else 'not applicable'}


def rank(name, data):
    rows = data.get('opportunities') or data.get('prospects') or data.get('features')
    if rows is None:
        return score(name, data)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200 or not all(isinstance(row, dict) for row in rows):
        raise ValueError('Supply 1–200 opportunity/prospect/feature objects.')
    ranked = [{**row, 'assessment': score(name, row)} for row in rows]
    ranked.sort(key=lambda row: row['assessment'].get('score', row['assessment'].get('priority'))
                if row['assessment'].get('score', row['assessment'].get('priority')) is not None else -1, reverse=True)
    return {'ranked': ranked, 'basis': 'Compared supplied observations only; no outcome is guaranteed.'}


def calculate(name, data):
    if name == 'pricing_engine':
        cost = number(data, 'hours') * number(data, 'hourly_cost') + number(data, 'direct_costs', 0)
        margin, fees = number(data, 'margin_percent', maximum=Decimal(100)), number(data, 'fee_percent', 0, maximum=Decimal(100))
        if margin + fees >= 100:
            raise ValueError('margin_percent + fee_percent must be less than 100.')
        price = cost / (1 - (margin + fees) / 100)
        competitors = data.get('competitor_prices', [])
        values = [number({'price': item}, 'price') for item in competitors]
        result = {'cost': amount(cost), 'price_floor': amount(price), 'estimated_margin': amount(price * margin / 100),
                  'method': 'cost / (1 - margin_fraction - fee_fraction)',
                  'observed_competitor_median': amount(median(values)) if values else None}
    elif name in {'content_roi', 'automation_roi_calculator'}:
        if name == 'content_roi':
            revenue = number(data, 'attributable_revenue')
            cost = number(data, 'production_cost') + number(data, 'distribution_cost', 0)
            result = {'net_contribution': amount(revenue - cost), 'roi_percent': ratio(revenue - cost, cost),
                      'method': '(attributable revenue - costs) / costs; attribution must be supplied.'}
        else:
            before, after = number(data, 'hours_before'), number(data, 'hours_after')
            runs, rate = number(data, 'runs_per_month'), number(data, 'hourly_cost')
            setup, running = number(data, 'implementation_cost'), number(data, 'monthly_running_cost')
            saved = (before - after) * runs
            monthly = saved * rate - running
            result = {'monthly_hours_saved': float(saved), 'monthly_net_savings': amount(monthly),
                      'payback_months': round(float(setup / monthly), 2) if monthly > 0 else None,
                      'first_year_roi_percent': ratio(monthly * 12 - setup, setup)}
    elif name in {'price_comparison_engine', 'inventory_profitability'}:
        sale, buy = number(data, 'sale_price'), number(data, 'acquisition_cost')
        fees = number(data, 'fee_percent', maximum=Decimal(100)) / 100
        returns = number(data, 'return_percent', maximum=Decimal(100)) / 100
        shipping, other = number(data, 'shipping_cost'), number(data, 'other_costs', 0)
        return_cost = number(data, 'return_cost', 0)
        net = sale * (1 - returns) * (1 - fees) - buy - shipping - other - returns * return_cost
        quantity = count(data, 'quantity', 1)
        result = {'expected_unit_contribution': amount(net), 'expected_total_contribution': amount(net * quantity),
                  'capital_at_risk': amount(buy * quantity), 'quantity': quantity,
                  'method': 'Retained sales less fees, acquisition, outbound shipping, other and expected return handling; no recovered resale value assumed.'}
    elif name in {'subscription_metrics', 'saas_pricing_optimizer'}:
        subscriptions = data.get('subscriptions', data.get('tiers'))
        if not isinstance(subscriptions, list) or not subscriptions:
            raise ValueError('Supply subscriptions/tiers with monthly_price and customers.')
        mrr = sum((number(row, 'monthly_price') * count(row, 'customers') for row in subscriptions), Decimal(0))
        customers = sum(count(row, 'customers') for row in subscriptions)
        starting, lost = count(data, 'starting_customers'), count(data, 'lost_customers')
        if lost > starting:
            raise ValueError('lost_customers cannot exceed starting_customers.')
        spend, acquired = number(data, 'acquisition_spend'), count(data, 'new_customers')
        margin = number(data, 'gross_margin_percent', maximum=Decimal(100)) / 100
        churn = Decimal(lost) / starting if starting else None
        arpu = mrr / customers if customers else None
        result = {'mrr': amount(mrr), 'arr': amount(mrr * 12), 'customers': customers,
                  'monthly_churn_percent': float(churn * 100) if churn is not None else None,
                  'cac': amount(spend / acquired) if acquired else None,
                  'ltv_estimate': amount(arpu * margin / churn) if arpu is not None and churn else None,
                  'basis': 'ARR = 12 × current MRR; LTV assumes stable monthly churn and margin. Zero/unknown denominators stay unknown.'}
    else:
        raise ValueError(f'No calculation defined for {name}.')
    result['inputs'] = data
    result['currency'] = data.get('currency', 'USD')
    result['basis'] = result.get('basis', 'Calculated from supplied observations/assumptions, not a market prediction.')
    return result


def performance(data):
    result = {'inputs': data, 'basis': 'User-reported measurements; attribution and platform receipts are not independently verified.'}
    for numerator, denominator, label in [('clicks', 'impressions', 'ctr_percent'),
                                           ('conversions', 'clicks', 'conversion_percent'),
                                           ('watch_seconds', 'duration_seconds', 'retention_percent')]:
        result[label] = ratio(number(data, numerator), number(data, denominator)) if numerator in data and denominator in data else None
    return result
