"""Bounded whole-request finance, geometry and energy calculations."""
from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
import re

N = r'(\d+(?:\.\d+)?)'
UNIT = r'(mm|cm|m|km|in|ft)?'


def _number(raw):
    if len(raw) > 40 or ('.' in raw and len(raw.partition('.')[2]) > 12):
        raise ValueError('Precision exceeds calculation budget')
    value = Fraction(Decimal(raw))
    if abs(value) > 10**12:
        raise ValueError('Quantity exceeds calculation budget')
    return value


def _decimal(value, places=6):
    with localcontext() as ctx:
        ctx.prec = 60
        if isinstance(value, Fraction):
            value = Decimal(value.numerator) / Decimal(value.denominator)
        return format(value.quantize(Decimal(1).scaleb(-places)), 'f').rstrip('0').rstrip('.') or '0'


def quantitative_answer(text: str) -> str | None:
    """Explicit supplied inputs only; decline missing units and extra conditions."""
    value = ' '.join(str(text or '').split()).rstrip('?.! ')
    if not value or len(value) > 500:
        return None
    value = re.sub(r'(?i)^(?:calculate|compute|find|what is)\s+(?:the\s+)?', '', value)
    flags = re.I
    try:
        match = re.fullmatch(r'net worth (?:with|from) assets ([$£€₹]?)' + N + r' and liabilities ([$£€₹]?)' + N, value, flags)
        if match:
            symbol, assets, other, liabilities = match.groups()
            if symbol != other:
                return None
            result = _number(assets) - _number(liabilities)
            return f'Net worth = {symbol}{_decimal(result)}.\nAssets − liabilities = {assets} − {liabilities}.\nCalculated locally from your figures.'
        match = re.fullmatch(r'compound interest on ([$£€₹]?)' + N + r' at ' + N +
                             r'% (annually|annual rate compounded (?:annually|monthly|quarterly)) for (\d+) years?', value, flags)
        if match:
            symbol, principal, rate, period, years = match.groups()
            p, r, years = _number(principal), _number(rate), int(years)
            if not 0 <= years <= 50 or r > 100:
                return None
            n = {'annually': 1, 'monthly': 12, 'quarterly': 4}[period.lower().split()[-1]]
            balance = p * (1 + r / (100 * n)) ** (n * years)
            if balance > 10**30:
                return None
            return (f'Projected balance ≈ {symbol}{_decimal(balance, 2)}; interest ≈ {symbol}{_decimal(balance-p, 2)}.\n'
                    f'A = P × (1 + r/n)^(n×t), with r = {rate}/100, n = {n}, t = {years}.\n'
                    'Constant supplied annual rate, no deposits, fees or taxes; scenario calculation, not a guaranteed investment return.')
        match = re.fullmatch(r'real return with nominal return ' + N + r'% and inflation ' + N + r'%', value, flags)
        if match:
            nominal, inflation = map(_number, match.groups())
            result = ((1 + nominal / 100) / (1 + inflation / 100) - 1) * 100
            return (f'Real return ≈ {_decimal(result)}%.\n'
                    f'[(1 + {_decimal(nominal)}/100) / (1 + {_decimal(inflation)}/100) − 1] × 100.\n'
                    'Calculated locally for the supplied period; subtracting the two rates is only an approximation.')
        match = re.fullmatch(r'(?:energy used by|electricity used by) (?:a )?' + N +
                             r' (kW|W) device running for ' + N + r' hours?(?: at ([$£€₹])' + N + r' per kWh)?', value, flags)
        if match:
            power, unit, hours, symbol, tariff = match.groups()
            kw = _number(power) / (1000 if unit.lower() == 'w' else 1)
            energy = kw * _number(hours)
            answer = f'Energy = {_decimal(energy)} kWh ({_decimal(kw)} kW × {hours} h).'
            if tariff:
                answer += f'\nEnergy charge ≈ {symbol}{_decimal(energy * _number(tariff), 2)} at your supplied tariff; fixed charges excluded.'
            return answer + '\nAssumes the stated constant power for the full duration; calculated locally.'
        match = re.fullmatch(r'kinetic energy of ' + N + r' kg moving at ' + N + r' m/s', value, flags)
        if match:
            mass, speed = map(_number, match.groups())
            return f'Kinetic energy = {_decimal(mass * speed**2 / 2)} J.\nE = ½mv²; classical, non-relativistic model. Calculated locally.'
        match = re.fullmatch(r'(area|perimeter) of (?:a )?rectangle with length ' + N + r'\s*' + UNIT +
                             r' and width ' + N + r'\s*' + UNIT, value, flags)
        if match:
            operation, length, unit, width, other = match.groups()
            if (unit or '').lower() != (other or '').lower():
                return None
            a, b = _number(length), _number(width)
            if min(a, b) <= 0:
                return None
            result = a*b if operation.lower() == 'area' else 2*(a+b)
            units = (unit or 'unit').lower() + ('²' if operation.lower() == 'area' else '')
            return f'{operation.capitalize()} = {_decimal(result)} {units}.\nCalculated locally from the supplied rectangle dimensions.'
        match = re.fullmatch(r'hypotenuse of (?:a )?right triangle with legs ' + N + r'\s*' + UNIT +
                             r' and ' + N + r'\s*' + UNIT, value, flags)
        if match:
            a, unit, b, other = match.groups()
            if (unit or '').lower() != (other or '').lower():
                return None
            a, b = _number(a), _number(b)
            if min(a, b) <= 0:
                return None
            with localcontext() as ctx:
                ctx.prec = 60
                squared = a*a + b*b
                root = (Decimal(squared.numerator) / Decimal(squared.denominator)).sqrt()
                return f'Hypotenuse ≈ {_decimal(root)} {(unit or "units").lower()}.\nc = √(a² + b²). Calculated locally for a right triangle.'
    except (ValueError, ArithmeticError):
        return None
    return None
