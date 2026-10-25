"""Saved business assumptions and bounded monthly unit-economics scenarios.

These are planning estimates, never entries in the owner's accounting books.
Money uses Decimal throughout; owner time is priced explicitly.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from typing import Any

TEXT_FIELDS = {
    "customer": "", "problem": "", "offer": "", "channel": "",
    "advantage": "", "experiment": "",
}
NUMERIC_FIELDS = {
    "price": "0", "direct_cost": "0", "acquisition_cost": "0",
    "hours_per_sale": "1", "hourly_value": "0", "capacity_hours": "0",
    "monthly_leads": "0", "conversion_percent": "0", "fixed_cost": "0",
    "target_profit": "0",
}
DEFAULTS = {**TEXT_FIELDS, **NUMERIC_FIELDS, "currency": "USD"}


def normalize(values: dict[str, Any]) -> dict[str, str]:
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError("Use the supported business-model fields.")
    result = dict(DEFAULTS)
    for key in TEXT_FIELDS:
        result[key] = str(values.get(key, DEFAULTS[key])).strip()
        if len(result[key]) > 2000:
            raise ValueError(f"{key.replace('_', ' ')} must be under 2,000 characters.")
    currency = str(values.get("currency", "USD")).strip().upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ValueError("Currency must be a three-letter code.")
    result["currency"] = currency
    for key, default in NUMERIC_FIELDS.items():
        try:
            raw = str(values.get(key, default))
            if len(raw) > 64:
                raise ValueError
            value = Decimal(raw)
        except (InvalidOperation, ValueError):
            raise ValueError(f"{key.replace('_', ' ')} must be a number.") from None
        if not value.is_finite() or value < 0 or value > 1_000_000_000:
            raise ValueError(f"{key.replace('_', ' ')} must be finite and between 0 and 1 billion.")
        if value.as_tuple().exponent < -6:
            raise ValueError(f"{key.replace('_', ' ')} supports up to six decimal places.")
        if key == "conversion_percent" and value > 100:
            raise ValueError("Conversion must be between 0 and 100 percent.")
        if key == "hours_per_sale" and value <= 0:
            raise ValueError("Hours per sale must be greater than zero.")
        if key == "monthly_leads" and value != value.to_integral_value():
            raise ValueError("Monthly leads must be a whole number.")
        result[key] = format(value, "f")
    return result


def _money(value: Decimal, currency: str) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f} {currency}"


def _projection(n, price=None, conversion=None):
    price = n['price'].quantize(Decimal('.01'), rounding=ROUND_HALF_UP) if price is None else price
    conversion = n['conversion_percent'] if conversion is None else conversion
    capacity = int((n['capacity_hours'] / n['hours_per_sale']).to_integral_value(rounding=ROUND_FLOOR))
    demand = int((n['monthly_leads'] * conversion / 100).to_integral_value(rounding=ROUND_FLOOR))
    sales = min(demand, capacity)
    cash = price - n['direct_cost'] - n['acquisition_cost']
    contribution = cash - n['hours_per_sale'] * n['hourly_value']
    return sales, demand, capacity, cash, contribution, contribution * sales - n['fixed_cost']


def evaluate(values: dict[str, Any]) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 48
        return _evaluate(values)


def _evaluate(values: dict[str, Any]) -> dict[str, Any]:
    assumptions = normalize(values)
    n = {key: Decimal(assumptions[key]) for key in NUMERIC_FIELDS}
    currency = assumptions["currency"]
    capacity = int((n["capacity_hours"] / n["hours_per_sale"]).to_integral_value(rounding=ROUND_FLOOR))
    rows = []
    for name, price_factor, conversion_factor in (
        ("Downside", Decimal("0.9"), Decimal("0.8")),
        ("Base", Decimal(1), Decimal(1)),
        ("Upside", Decimal("1.1"), Decimal("1.2")),
    ):
        price = (n["price"] * price_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        conversion = min(Decimal(100), n["conversion_percent"] * conversion_factor)
        sales, demand, capacity, cash_unit, contribution, profit = _projection(n, price, conversion)
        break_even = (int((n["fixed_cost"] / contribution).to_integral_value(rounding=ROUND_CEILING))
                      if contribution > 0 else None)
        target_sales = (int(((n["fixed_cost"] + n["target_profit"]) / contribution).to_integral_value(rounding=ROUND_CEILING))
                        if contribution > 0 else None)
        rows.append({
            "name": name, "sales": sales, "demand": demand, "capacity": capacity,
            "price": _money(price, currency), "revenue": _money(price * sales, currency),
            "cash_surplus": _money(cash_unit * sales - n["fixed_cost"], currency),
            "owner_time_cost": _money(n["hours_per_sale"] * n["hourly_value"] * sales, currency),
            "profit": _money(profit, currency), "contribution": _money(contribution, currency),
            "margin_percent": float((contribution / price * 100).quantize(Decimal("0.1"))) if price else None,
            "break_even_sales": break_even, "target_sales": target_sales,
            "target_feasible": target_sales is not None and target_sales <= sales,
            "capacity_limited": demand > capacity,
        })
    base = rows[1]
    actions = []
    if n["price"] == 0:
        actions.append("Set a price and test whether customers will pay it.")
    if base["break_even_sales"] is None:
        actions.append("Each sale has no positive contribution after delivery, acquisition and owner time. Test a higher price or lower unit costs.")
    elif base["break_even_sales"] > base["sales"]:
        actions.append(f"Break-even needs {base['break_even_sales']} sales; the base case supports {base['sales']}. Reduce fixed costs or test demand and capacity.")
    if base["capacity_limited"]:
        actions.append(f"Delivery capacity limits sales to {capacity}. Reduce hours per sale or add delivery capacity.")
    if not assumptions["customer"] or not assumptions["problem"]:
        actions.append("Define one customer segment and the problem they will pay to solve.")
    if not assumptions["experiment"]:
        actions.append("Choose a measurable next experiment: who, how many, what offer, deadline and success threshold.")
    if not actions:
        actions.append("Validate price, conversion and delivery time with paid pilots before scaling.")
    sales, _, _, _, _, base_profit = _projection(n)
    sensitivity = []
    for field, factors in (('price', ('.9', '1.1')), ('direct_cost', ('1.2',)), ('acquisition_cost', ('1.2',)),
                           ('conversion_percent', ('.8', '1.2')), ('hours_per_sale', ('.8', '1.2')),
                           ('monthly_leads', ('.8', '1.2')), ('fixed_cost', ('1.2',))):
        for factor in factors:
            changed = {**n, field: n[field] * Decimal(factor)}
            if field == 'conversion_percent':
                changed[field] = min(Decimal(100), changed[field])
            if field == 'monthly_leads':
                changed[field] = changed[field].to_integral_value(rounding=ROUND_FLOOR)
            units, _, _, _, _, profit = _projection(changed)
            sensitivity.append({'field': field, 'factor': factor, 'assumed_value': format(changed[field], 'f'),
                                'sales': units, 'profit': _money(profit, currency),
                                'profit_change': _money(profit - base_profit, currency)})
    minimum_price = (n['direct_cost'] + n['acquisition_cost'] + n['hours_per_sale'] * n['hourly_value']
                     + (n['fixed_cost'] + n['target_profit']) / sales) if sales else None
    max_cac = (n['price'].quantize(Decimal('.01'), rounding=ROUND_HALF_UP) - n['direct_cost']
               - n['hours_per_sale'] * n['hourly_value'] - (n['fixed_cost'] + n['target_profit']) / sales) if sales else None
    target_conversion = (Decimal(base['target_sales']) / n['monthly_leads'] * 100
                         if base['target_sales'] is not None and n['monthly_leads'] else None)
    return {"assumptions": assumptions, "scenarios": rows, "next_actions": actions, 'sensitivity': sensitivity,
            'requirements': {'price_for_target_at_base_sales': _money(minimum_price.quantize(Decimal('.01'), rounding=ROUND_CEILING), currency) if minimum_price is not None else None,
                             'max_acquisition_cost_for_target': _money(max_cac.quantize(Decimal('.01'), rounding=ROUND_FLOOR), currency) if max_cac is not None else None,
                             'conversion_percent_for_target': float(target_conversion.quantize(Decimal('.01'), rounding=ROUND_CEILING)) if target_conversion is not None else None,
                             'capacity_can_deliver_target': base['target_sales'] is not None and base['target_sales'] <= capacity,
                             'basis': 'One input changes at a time; other assumptions stay fixed. Price changes do not model demand elasticity. '
                                      'Target thresholds assume the base sales volume; negative acquisition allowance means the target is not viable at that volume.'},
            "basis": "Monthly planning estimates. Sales = floor(leads × conversion), capped by delivery hours. Profit includes owner time; cash surplus excludes it. Downside uses 10% lower price and 20% lower conversion; upside uses 10% higher price and 20% higher conversion. Acquisition cost is per paid sale. Taxes, refunds and payment timing are excluded."}
