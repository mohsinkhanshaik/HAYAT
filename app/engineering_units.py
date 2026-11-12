"""Small explicit engineering-unit catalog and bounded dimensional analysis.

This checks declared dimensions and scale, not physical truth or experimental
validity. Numerical literals in a formula are dimensionless coefficients.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
import re


@dataclass(frozen=True)
class Unit:
    dimensions: tuple = (0, 0, 0, 0, 0, 0, 0)  # m, kg, s, A, K, mol, cd
    factor: Decimal = Decimal(1)

    def multiply(self, other, sign=1):
        return Unit(tuple(a + sign*b for a, b in zip(self.dimensions, other.dimensions, strict=True)),
                    self.factor * (other.factor if sign == 1 else 1/other.factor))

    def power(self, exponent):
        factor = self.factor.sqrt() if exponent == Fraction(1, 2) else self.factor ** int(exponent)
        return Unit(tuple(value * exponent for value in self.dimensions), factor)


def _base(index):
    return Unit(tuple(int(position == index) for position in range(7)))


UNITS = dict(zip(('m', 'kg', 's', 'A', 'K', 'mol', 'cd'), (_base(i) for i in range(7)), strict=True))
UNITS.update(rad=Unit(), Hz=_base(2).power(-1),
             N=Unit((1, 1, -2, 0, 0, 0, 0)), Pa=Unit((-1, 1, -2, 0, 0, 0, 0)),
             J=Unit((2, 1, -2, 0, 0, 0, 0)), W=Unit((2, 1, -3, 0, 0, 0, 0)),
             C=Unit((0, 0, 1, 1, 0, 0, 0)), V=Unit((2, 1, -3, -1, 0, 0, 0)),
             ohm=Unit((2, 1, -3, -2, 0, 0, 0)))
for name, parent, factor in (
    ('mm', 'm', '.001'), ('cm', 'm', '.01'), ('km', 'm', '1000'),
    ('inch', 'm', '.0254'), ('ft', 'm', '.3048'), ('g', 'kg', '.001'), ('mg', 'kg', '.000001'),
    ('ms', 's', '.001'), ('us', 's', '.000001'), ('min', 's', '60'), ('h', 's', '3600'),
    ('mA', 'A', '.001'), ('kW', 'W', '1000'), ('MW', 'W', '1000000'),
    ('Wh', 'J', '3600'), ('kWh', 'J', '3600000'), ('kJ', 'J', '1000'),
    ('kPa', 'Pa', '1000'), ('MPa', 'Pa', '1000000'), ('bar', 'Pa', '100000')):
    UNITS[name] = Unit(UNITS[parent].dimensions, Decimal(factor))


def _tree(expression, limit=240):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > limit:
        raise ValueError('Supply a nonempty expression within 240 characters.')
    tree = ast.parse(expression.replace('^', '**').replace('µ', 'u').replace('Ω', 'ohm'), mode='eval')
    if len(list(ast.walk(tree))) > 64:
        raise ValueError('Expression exceeds the 64-node analysis budget.')
    return tree.body


def _exponent(node):
    sign = -1 if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) else 1
    value = node.operand if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) else node
    if not isinstance(value, ast.Constant) or type(value.value) is not int or abs(value.value) > 8:
        raise ValueError('Use literal integer powers from -8 to 8.')
    return sign * value.value


def parse_unit(text):
    if str(text).strip() in {'°C', 'Celsius', 'degC', '°F', 'Fahrenheit', 'degF'}:
        raise ValueError('Affine temperature units cannot appear in formulas. Convert absolute temperatures to K first.')
    def visit(node):
        if isinstance(node, ast.Name) and node.id in UNITS:
            return UNITS[node.id]
        if isinstance(node, ast.Constant) and type(node.value) is int and node.value == 1:
            return Unit()
        if isinstance(node, ast.BinOp):
            left = visit(node.left)
            if isinstance(node.op, ast.Pow):
                return left.power(_exponent(node.right))
            if isinstance(node.op, (ast.Mult, ast.Div)):
                return left.multiply(visit(node.right), -1 if isinstance(node.op, ast.Div) else 1)
        raise ValueError('Unknown unit or operation. Use named units, *, /, parentheses and integer powers; 1 is dimensionless.')
    with localcontext() as context:
        context.prec = 48
        return visit(_tree(text))


def _format(value):
    result = format(value, '.12g')
    return result if 'E' in result or '.' not in result else result.rstrip('0').rstrip('.')


def _same_scale(a, b):
    # sqrt can introduce Decimal rounding at the 48-digit working precision.
    return abs(a-b) <= max(abs(a), abs(b))*Decimal('1e-40')


def convert_units(value, source, target):
    if not isinstance(source, str) or not isinstance(target, str):
        raise ValueError('Unit labels must be strings.')
    if isinstance(value, bool) or len(str(value)) > 80:
        raise ValueError('Supply a bounded finite number.')
    amount = Decimal(str(value))
    if not amount.is_finite() or amount.copy_abs() > Decimal('1e30') or abs(amount.as_tuple().exponent) > 100:
        raise ValueError('Value exceeds the conversion budget.')
    temperatures = {'K': (Fraction(1), Fraction(0)), 'degC': (Fraction(1), Fraction(27315, 100)),
                    '°C': (Fraction(1), Fraction(27315, 100))}
    with localcontext() as context:
        context.prec = 48
        temperatures.update({'degF': (Fraction(5, 9), Fraction(27315, 100)-Fraction(160, 9)),
                             '°F': (Fraction(5, 9), Fraction(27315, 100)-Fraction(160, 9))})
        if source in temperatures or target in temperatures:
            if source not in temperatures or target not in temperatures:
                raise ValueError('Use K, degC or degF for absolute temperature conversion.')
            scale, offset = temperatures[source]
            kelvin = Fraction(amount)*scale + offset
            if kelvin < 0:
                raise ValueError('An absolute temperature cannot be below zero kelvin.')
            scale, offset = temperatures[target]
            converted = (kelvin-offset)/scale
            converted = Decimal(converted.numerator)/Decimal(converted.denominator)
        else:
            a, b = parse_unit(source), parse_unit(target)
            if a.dimensions != b.dimensions:
                raise ValueError('Incompatible dimensions; power and energy, for example, are different quantities.')
            converted = amount*a.factor/b.factor
        return {'value': _format(converted), 'source': source, 'target': target,
                'summary': f'{value} {source} = {_format(converted)} {target}', 'scope': 'Declared-unit conversion; rounded to 12 significant digits.'}


def audit_dimensions(formula, inputs, output):
    if not isinstance(inputs, dict) or not 1 <= len(inputs) <= 12:
        raise ValueError('Declare between 1 and 12 variable units.')
    if any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z_0-9]{0,31}', name) for name in inputs):
        raise ValueError('Use simple variable names.')
    with localcontext() as context:
        context.prec = 48
        declared = {key: parse_unit(unit) for key, unit in inputs.items()}
        used = set()
        def equal(values):
            first = values[0]
            if any(unit.dimensions != first.dimensions for unit in values):
                raise ValueError('Addition, subtraction and min/max require matching dimensions.')
            if any(not _same_scale(unit.factor, first.factor) for unit in values):
                raise ValueError('Convert additive terms to the same unit scale first (e.g. cm and m).')
            return first
        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
                number = Decimal(str(node.value))
                if not number.is_finite() or number.copy_abs() > Decimal('1e30'):
                    raise ValueError('Literal exceeds the analysis budget.')
                return Unit()
            if isinstance(node, ast.Name):
                if node.id in {'pi', 'e'}:
                    return Unit()
                if node.id not in declared:
                    raise ValueError(f'Declare a unit for {node.id}; no constants are fitted during a unit audit.')
                used.add(node.id)
                return declared[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                return visit(node.operand)
            if isinstance(node, ast.BinOp):
                left = visit(node.left)
                if isinstance(node.op, ast.Pow):
                    return left.power(_exponent(node.right))
                right = visit(node.right)
                if isinstance(node.op, (ast.Add, ast.Sub)):
                    return equal([left, right])
                if isinstance(node.op, (ast.Mult, ast.Div)):
                    return left.multiply(right, -1 if isinstance(node.op, ast.Div) else 1)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
                values = [visit(arg) for arg in node.args]
                if node.func.id in {'min', 'max'} and 1 <= len(values) <= 12:
                    return equal(values)
                if len(values) == 1:
                    if node.func.id == 'sqrt':
                        return values[0].power(Fraction(1, 2))
                    if node.func.id == 'abs':
                        return values[0]
                    if node.func.id in {'sin', 'cos', 'tan', 'exp', 'log'}:
                        if values[0] != Unit():
                            raise ValueError('Transcendental functions need dimensionless values (angles in radians).')
                        return Unit()
            raise ValueError('Unsupported expression in unit analysis.')
        actual = visit(_tree(formula))
        target = parse_unit(output)
        if set(declared) != used:
            raise ValueError('Declare exactly the variables used by the formula.')
        compatible = actual.dimensions == target.dimensions
        factor = actual.factor/target.factor if compatible else None
        if factor is not None and _same_scale(factor, Decimal(1)):
            factor = Decimal(1)
        return {'compatible': compatible, 'scale_matches': compatible and factor == 1,
                'conversion_factor': _format(factor) if factor is not None else None,
                'summary': ('Dimensions match; scale also matches.' if factor == 1 else
                            f'Dimensions match. Multiply the numeric result by {_format(factor)} for the output unit.' if compatible else
                            'Output dimensions do not match the declared formula.'),
                'scope': 'Declared-unit analysis only. Literals are dimensionless. No physical validity or experimental accuracy is established.'}
