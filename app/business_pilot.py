"""Compare owner-reported pilot observations with a frozen planning scenario."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any

from app.business_model import _money, evaluate

PILOT_FIELDS = {
    'leads': 'Qualified leads approached', 'customers': 'Paying customers',
    'revenue': 'Revenue received after refunds', 'direct_cost': 'Total direct costs',
    'acquisition_spend': 'Total acquisition spend', 'delivery_hours': 'Total delivery hours',
}


def evaluate_pilot(assumptions: dict, observations: dict) -> dict:
    """No inference of hiring/funding/market probabilities from a small cohort."""
    if not isinstance(observations, dict) or set(observations) - (set(PILOT_FIELDS) | {'notes'}):
        raise ValueError('Use the supported pilot observation fields.')
    plan = evaluate(assumptions)
    values: dict[str, str] = {}
    for key in PILOT_FIELDS:
        raw = str(observations.get(key, '0')).strip()
        try:
            if len(raw) > 64:
                raise ValueError
            number = Decimal(raw)
        except (ValueError, InvalidOperation):
            raise ValueError(f'{PILOT_FIELDS[key]} must be a number.') from None
        if (not number.is_finite() or not 0 <= number <= 1_000_000_000
                or number.as_tuple().exponent < -6):
            raise ValueError(f'{PILOT_FIELDS[key]} must be finite, nonnegative, at most 1 billion and have at most six decimal places.')
        if key in ('leads', 'customers') and number != number.to_integral_value():
            raise ValueError(f'{PILOT_FIELDS[key]} must be a whole number.')
        values[key] = format(number, 'f')
    notes = str(observations.get('notes', '')).strip()
    if len(notes) > 2000:
        raise ValueError('Evidence notes must be under 2,000 characters.')
    values['notes'] = notes
    with localcontext() as context:
        context.prec = 48
        n = {key: Decimal(values[key]) for key in PILOT_FIELDS}
        if n['customers'] > n['leads']:
            raise ValueError('Paying customers cannot exceed the qualified leads in this same cohort.')
        p = plan['assumptions']
        conversion = n['customers'] / n['leads'] * 100 if n['leads'] else None
        cash = n['revenue'] - n['direct_cost'] - n['acquisition_spend']
        contribution = cash - n['delivery_hours'] * Decimal(p['hourly_value'])
        per_sale = contribution / n['customers'] if n['customers'] else None
        planned_unit = (Decimal(p['price']).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                        - Decimal(p['direct_cost']) - Decimal(p['acquisition_cost'])
                        - Decimal(p['hours_per_sale']) * Decimal(p['hourly_value']))
        gaps = []
        if not n['customers']:
            gaps.append('No paying customers recorded; this pilot has not demonstrated paid demand.')
        if contribution <= 0:
            gaps.append('Pilot contribution after owner time is not positive; inspect price, delivery costs and acquisition spend.')
        if conversion is not None and conversion < Decimal(p['conversion_percent']):
            gaps.append('Observed conversion is below the planning assumption.')
        if per_sale is not None and per_sale < planned_unit:
            gaps.append('Observed contribution per customer is below the planning assumption.')
        if not gaps:
            gaps.append('This cohort meets the compared assumptions; repeat with a new cohort before increasing spend.')
        currency = p['currency']
        return {
            'observations': values, 'assumptions': p,
            'conversion_percent': str(conversion.quantize(Decimal('.01'))) if conversion is not None else None,
            'cash_contribution': _money(cash, currency), 'contribution_after_time': _money(contribution, currency),
            'contribution_per_customer': _money(per_sale, currency) if per_sale is not None else None,
            'planned_contribution_per_customer': _money(planned_unit, currency),
            'next_actions': gaps,
            'basis': 'Owner-reported observations for one cohort, not independently verified market evidence. '
                     'Revenue is net of reported refunds. Costs are totals for this cohort. Owner time uses the compared plan rate. '
                     'Fixed costs, taxes and unpaid receivables are excluded. Small cohorts do not establish future conversion or market size. '
                     'Saving this check does not record income, payments or expenses in the accounting books.',
        }


def pilot_text(result: dict[str, Any]) -> str:
    conversion = result['conversion_percent']
    return (f"Pilot: {result['observations']['customers']} paying customers / {result['observations']['leads']} leads. "
            f"Conversion: {conversion + '%' if conversion is not None else 'unknown (no leads)'}.\n"
            f"Cash contribution: {result['cash_contribution']}; after owner time: {result['contribution_after_time']}. "
            f"Per customer: {result['contribution_per_customer'] or 'unknown (no paying customers)'} "
            f"versus planned {result['planned_contribution_per_customer']}.\n"
            + '\n'.join(result['next_actions']) + '\n\n' + result['basis'])
