"""Theory and proof for an invention's formula.

The workbench fits a formula to frozen cases (app/invention_sim.py). Fitting
says *what* the numbers do. This module asks *why it should hold* and *what
would prove it wrong*, and labels every answer honestly:

* **Units** -- every number in the formula is a coefficient that may carry
  units. Linear algebra over the seven SI base dimensions works out the units
  each coefficient must have for the formula to be consistent, or shows which
  sum or function breaks the units. A coefficient whose units and value match a
  physical constant (6.674e-11 m^3 kg^-1 s^-2 = G) is named, with the match.
* **Derivation** -- Buckingham's pi theorem: from the inputs' and the output's
  units alone, the law must be ``C * x1**a1 * ...`` times a function of the
  dimensionless groups. That shape is then fitted to the cases, so a theory
  derived without the data is tested against the data.
* **Proofs** -- sympy decides properties of the formula over the inputs'
  domain: rising/falling in each input, exact power-law exponents, limits,
  scaling, symmetry, sign, and the owner's own claims ("increases with L").
  Each answer is *proved*, *disproved* (with a counterexample), *observed*
  (numeric sampling only, not a proof) or *undecided*.
* **Decisive experiment** -- where the best formula and its closest rivals
  disagree most, so one real measurement there can tell them apart.

Nothing here is a physical experiment. A consistent, derived, proven formula is
still a hypothesis about a device until it is measured.
"""
from __future__ import annotations

import ast
import math
import random
import re
import threading
import time
from fractions import Fraction
from typing import Any

import sympy as sp

from app import invention_sim
from app.engineering_units import parse_unit

BASES = ('m', 'kg', 's', 'A', 'K', 'mol', 'cd')
ZERO = (Fraction(0),) * 7
SUPERSCRIPT = str.maketrans('0123456789-', '⁰¹²³⁴⁵⁶⁷⁸⁹⁻')
SCOPE = ('A mathematical and dimensional argument about the formula, not a physical experiment. It does not '
         'establish that a device works, or that the idea is new.')
STEP_SECONDS = 4.0
TOTAL_SECONDS = 25.0

#: name, symbol, value, dimensions (m, kg, s, A, K, mol, cd)
PHYSICAL_CONSTANTS = (
    ('standard gravity', 'g', 9.80665, (1, 0, -2, 0, 0, 0, 0)),
    ('gravitational constant', 'G', 6.67430e-11, (3, -1, -2, 0, 0, 0, 0)),
    ('speed of light', 'c', 299792458.0, (1, 0, -1, 0, 0, 0, 0)),
    ('speed of sound in air at 20 °C', 'v_sound', 343.0, (1, 0, -1, 0, 0, 0, 0)),
    ('Planck constant', 'h', 6.62607015e-34, (2, 1, -1, 0, 0, 0, 0)),
    ('Boltzmann constant', 'k_B', 1.380649e-23, (2, 1, -2, 0, -1, 0, 0)),
    ('Stefan–Boltzmann constant', 'σ', 5.670374419e-8, (0, 1, -3, 0, -4, 0, 0)),
    ('molar gas constant', 'R', 8.314462618, (2, 1, -2, 0, -1, -1, 0)),
    ('elementary charge', 'e', 1.602176634e-19, (0, 0, 1, 1, 0, 0, 0)),
    ('vacuum permittivity', 'ε₀', 8.8541878128e-12, (-3, -1, 4, 2, 0, 0, 0)),
    ('Coulomb constant', 'k_e', 8.9875517923e9, (3, 1, -4, -2, 0, 0, 0)),
    ('vacuum permeability', 'μ₀', 1.25663706212e-6, (1, 1, -2, -2, 0, 0, 0)),
    ('Avogadro constant', 'N_A', 6.02214076e23, (0, 0, 0, 0, 0, -1, 0)),
    ('Faraday constant', 'F', 96485.33212, (0, 0, 1, 1, 0, -1, 0)),
)
FACTORS = ((1, ''), (2, '2·'), (0.5, '½·'), (math.pi, 'π·'), (2 * math.pi, '2π·'), (4 * math.pi, '4π·'),
           (1 / math.pi, '1/π·'), (1 / (2 * math.pi), '1/(2π)·'), (1 / (4 * math.pi), '1/(4π)·'))
POWERS = ((Fraction(1), '{}'), (Fraction(-1), '1/{}'), (Fraction(1, 2), '√{}'), (Fraction(-1, 2), '1/√{}'),
          (Fraction(2), '{}²'), (Fraction(-2), '1/{}²'))
PURE_NUMBERS = (('π', math.pi), ('2π', 2 * math.pi), ('4π', 4 * math.pi), ('π/2', math.pi / 2), ('e', math.e),
                ('√2', math.sqrt(2)), ('1/√2', 1 / math.sqrt(2)), ('√3', math.sqrt(3)), ('ln 2', math.log(2)))
MATCH = 0.005  # how close a coefficient must be to a named constant to be called consistent with it


class _Budget:
    def __init__(self, seconds=TOTAL_SECONDS, cancel=None):
        self.end, self.cancel = time.monotonic() + seconds, cancel

    def left(self):
        invention_sim._check_cancel(self.cancel)
        return max(0.0, self.end - time.monotonic())

    def run(self, function, *args, seconds=STEP_SECONDS):
        """(finished, value). sympy has no timeout, so a step runs in a daemon thread and is abandoned if slow."""
        limit = min(seconds, self.left())
        if limit <= 0:
            return False, None
        box = {}

        def work():
            try:
                box['value'] = function(*args)
            except Exception as exc:  # noqa: BLE001 -- sympy raises many types; an undecided step is reported
                box['error'] = exc
        worker = threading.Thread(target=work, daemon=True, name='invention-theory-step')
        worker.start()
        worker.join(limit)
        if worker.is_alive() or 'error' in box:
            return False, None
        return True, box.get('value')


# ------------------------------------------------------------------ units

def _dims(text: str) -> tuple[Fraction, ...]:
    return tuple(Fraction(value) for value in parse_unit(text).dimensions)


def format_units(dims) -> str:
    parts = []
    for base, power in zip(BASES, dims, strict=True):
        power = Fraction(power)
        if power == 0:
            continue
        if power == 1:
            parts.append(base)
        elif power.denominator == 1:
            parts.append(base + str(power.numerator).translate(SUPERSCRIPT))
        else:
            parts.append(f'{base}^({power})')
    return '·'.join(parts) or 'dimensionless'


class _Affine:
    """Units of a sub-expression: known part + rational multiples of unknown coefficient units."""
    __slots__ = ('known', 'unknown')

    def __init__(self, known=ZERO, unknown=None):
        self.known, self.unknown = tuple(known), dict(unknown or {})

    def plus(self, other, sign=1):
        unknown = dict(self.unknown)
        for key, value in other.unknown.items():
            unknown[key] = unknown.get(key, 0) + sign * value
        return _Affine(tuple(a + sign * b for a, b in zip(self.known, other.known, strict=True)),
                       {k: v for k, v in unknown.items() if v})

    def times(self, power: Fraction):
        return _Affine(tuple(value * power for value in self.known), {k: v * power for k, v in self.unknown.items() if v * power})


def _literal_exponent(node) -> Fraction | None:
    """The exact value of an exponent made only of numbers, or None if it has names in it."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Fraction(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal_exponent(node.operand)
        return None if value is None else (-value if isinstance(node.op, ast.USub) else value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left, right = _literal_exponent(node.left), _literal_exponent(node.right)
        if left is None or right is None or (isinstance(node.op, ast.Div) and right == 0):
            return None
        return {ast.Add: left + right, ast.Sub: left - right, ast.Mult: left * right}.get(type(node.op), left / right if right else None)
    if isinstance(node, ast.Name) and node.id in {'pi', 'e', 'tau'}:
        return None
    return None


DIMENSIONLESS_FUNCTIONS = {'exp', 'log', 'ln', 'log10', 'log2', 'sin', 'cos', 'tan', 'asin', 'acos', 'atan',
                           'sinh', 'cosh', 'tanh'}


def check_units(formula: str, units: dict[str, str], output: str | None = None) -> dict[str, Any]:
    """Whether the formula can be dimensionally consistent, and the units every coefficient must carry."""
    tree = invention_sim._tree(formula).body
    declared = {name: _dims(text) for name, text in (units or {}).items()}
    inputs = invention_sim.names_in(formula) - set(invention_sim.CONSTANTS)
    missing = sorted(name for name in inputs if name not in declared and not re.fullmatch(r'c\d+', name))
    if missing:
        raise ValueError('Declare units for ' + ', '.join(missing) + ' (for example m, s, kg, m/s**2, 1).')
    coefficients: list[dict[str, Any]] = []
    constraints: list[tuple[_Affine, _Affine, str]] = []

    def unknown(text, value=None):
        coefficients.append({'text': text, **({'value': float(value)} if value is not None else {})})
        return _Affine(ZERO, {len(coefficients) - 1: Fraction(1)})

    def visit(node):
        # A purely numeric part such as 1 / 2 or -(3 / 10) is one number: as separate literals only their ratio
        # was constrained, so each came out "not fixed by the units".
        folded = None if isinstance(node, ast.Constant) else _literal_exponent(node)
        if folded is not None:
            return unknown(ast.unparse(node), folded)
        if isinstance(node, ast.Constant):
            return unknown(ast.unparse(node), Fraction(str(node.value)))
        if isinstance(node, ast.Name):
            if node.id in invention_sim.CONSTANTS:
                return _Affine()
            if node.id in declared:
                return _Affine(declared[node.id])
            return unknown(node.id)
        if isinstance(node, ast.UnaryOp):
            return visit(node.operand)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow):
                power = _literal_exponent(node.right)
                base = visit(node.left)
                if power is not None:
                    return base.times(power)
                constraints.append((visit(node.right), _Affine(), f'the power in {ast.unparse(node)}'))
                constraints.append((base, _Affine(), f'the base of {ast.unparse(node)} (raised to a variable power)'))
                return _Affine()
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                constraints.append((left, right, f'the terms of {ast.unparse(node)}'))
                return left
            return left.plus(right, -1 if isinstance(node.op, ast.Div) else 1)
        if isinstance(node, ast.Call):
            name, args = node.func.id, [visit(arg) for arg in node.args]
            if name == 'sqrt':
                return args[0].times(Fraction(1, 2))
            if name in {'abs', 'floor', 'ceil'}:
                return args[0]
            if name in {'min', 'max', 'hypot'}:
                for arg in args[1:]:
                    constraints.append((args[0], arg, f'the arguments of {ast.unparse(node)}'))
                return args[0]
            for arg in args:
                constraints.append((arg, _Affine(), f'the argument of {ast.unparse(node)} (must have no units)'))
            return _Affine()
        raise ValueError(f'Unsupported part of a formula: {type(node).__name__}')

    result = visit(tree)
    target = _dims(output) if output else None
    if target is not None:
        constraints.append((result, _Affine(target), 'the whole formula against the output unit'))
    count = len(coefficients)
    solution, issue = _solve(constraints, count)
    report = {'formula': formula, 'consistent': issue is None, 'output': output or None,
              'inputs': {name: format_units(dims) for name, dims in declared.items() if name in inputs}}
    if issue is not None:
        left, right, where = issue
        report['issues'] = [_explain(left, right, where)]
    else:
        report['issues'] = []
        for index, coefficient in enumerate(coefficients):
            dims = solution[index]
            coefficient['units'] = format_units(dims) if dims is not None else 'not fixed by the units'
            coefficient['determined'] = dims is not None
            coefficient['dims'] = [str(value) for value in dims] if dims is not None else None
            label, symbol = (_identify(repr(coefficient.get('value', coefficient['text'])), dims) if dims is not None
                             else (None, None))
            coefficient['identified'], coefficient['constant'] = label, symbol
        report['coefficients'] = coefficients
        if target is None and not result.unknown:
            report['formula_units'] = format_units(result.known)
    report['scope'] = ('Units only: every number is treated as a coefficient that may carry units, and the units it must '
                       'carry are worked out. Consistent units are necessary for a physical law, not sufficient.')
    return report


def _solve(constraints, count):
    """Solve every base dimension's linear system. (per-coefficient units or None, first failing constraint or None)."""
    if not constraints:
        return [None] * count, None
    columns = max(count, 1)
    for size in range(1, len(constraints) + 1):
        rows = constraints[:size]
        matrix = sp.Matrix([[sp.Rational(left.unknown.get(i, 0) - right.unknown.get(i, 0)) for i in range(columns)]
                            for left, right, _ in rows])
        for base in range(7):
            rhs = sp.Matrix([sp.Rational(right.known[base] - left.known[base]) for left, right, _ in rows])
            if matrix.rank() != matrix.row_join(rhs).rank():
                return None, rows[-1]
    matrix = sp.Matrix([[sp.Rational(left.unknown.get(i, 0) - right.unknown.get(i, 0)) for i in range(columns)]
                        for left, right, _ in constraints])
    values: list[list[Fraction] | None] = [[] for _ in range(count)]
    for base in range(7):
        rhs = sp.Matrix([sp.Rational(right.known[base] - left.known[base]) for left, right, _ in constraints])
        found, params = matrix.gauss_jordan_solve(rhs)
        free = set(params)
        for index in range(count):
            entry = found[index]
            if values[index] is None or entry.free_symbols & free:
                values[index] = None
                continue
            values[index].append(Fraction(int(sp.numer(entry)), int(sp.denom(entry))))
    return [tuple(v) if v is not None else None for v in values], None


def _explain(left, right, where):
    if not left.unknown and not right.unknown:
        return (f'Units clash in {where}: one side is {format_units(left.known)}, the other '
                f'{format_units(right.known)}. No choice of coefficients can fix this.')
    return f'Units cannot be made consistent in {where}, whatever units the coefficients carry.'


def _identify(text: str, dims) -> tuple[str | None, str | None]:
    """(label, physical-constant symbol) this coefficient's value and units are consistent with; symbol is None for
    a pure number such as 2π."""
    try:
        value = abs(float(text))  # -343 in v - 343 is still the speed of sound
    except ValueError:
        return None, None
    if not math.isfinite(value) or value == 0:
        return None, None
    best = None
    if all(d == 0 for d in dims):
        for label, number in PURE_NUMBERS:
            error = abs(value - number) / number
            if error <= MATCH and (best is None or error < best[0]):
                best = (error, label)
        return (f'≈ {best[1]} ({best[0] * 100:.2g} % off)', None) if best else (None, None)
    for name, symbol, constant, constant_dims in PHYSICAL_CONSTANTS:
        for power, template in POWERS:
            if tuple(Fraction(d) * power for d in constant_dims) != tuple(Fraction(d) for d in dims):
                continue
            for factor, factor_label in FACTORS:
                number = factor * constant ** float(power)
                error = abs(value - number) / abs(number)
                if error <= MATCH and (best is None or error < best[0]):
                    shown = template.format(symbol)
                    label = (factor_label.rstrip('·') + shown[1:] if factor_label and shown.startswith('1/')
                             else factor_label + shown)
                    best = (error, label, name, symbol)
    if not best:
        return None, None
    return f'consistent with {best[1]} ({best[2]}), {best[0] * 100:.2g} % off', best[3]


#: formula-safe names for the constants, so a derivation can include one as an input
CONSTANT_KEYS = {'g': 'g0', 'G': 'G', 'c': 'c0', 'v_sound': 'v_sound', 'h': 'h_planck', 'k_B': 'k_B', 'σ': 'sigma_sb',
                 'R': 'R_gas', 'e': 'q_e', 'ε₀': 'eps0', 'k_e': 'k_e', 'μ₀': 'mu0', 'N_A': 'N_A', 'F': 'F_faraday'}


def _constant(symbol):
    for name, sym, value, dims in PHYSICAL_CONSTANTS:
        if sym == symbol:
            text = ' * '.join(f'{base} ** {power}' for base, power in zip(BASES, dims, strict=True) if power) or '1'
            return {'name': name, 'symbol': symbol, 'key': CONSTANT_KEYS[symbol], 'value': repr(value), 'units': text}
    return None


# ------------------------------------------------------------------ derivation

def _exponent_text(power: Fraction) -> str:
    return str(power.numerator) if power.denominator == 1 and power >= 0 else f'({power})'


def derive(units: dict[str, str], output: str) -> dict[str, Any]:
    """Buckingham pi: the law that the inputs' and output's units allow, before looking at any data."""
    if not units:
        raise ValueError('Declare the units of the inputs.')
    names = sorted(units)
    columns = [_dims(units[name]) for name in names]
    target = _dims(output)
    matrix = sp.Matrix([[sp.Rational(column[base]) for column in columns] for base in range(7)])
    rhs = sp.Matrix([sp.Rational(value) for value in target])
    groups = []
    for vector in matrix.nullspace():
        scale = math.lcm(*[int(sp.denom(v)) for v in vector])  # sympy's ilcm needs two or more numbers
        integers = [int(v * scale) for v in vector]
        divisor = math.gcd(*integers) or 1
        integers = [v // divisor for v in integers]
        if next((v for v in integers if v), 0) < 0:
            integers = [-v for v in integers]
        groups.append(dict(zip(names, (Fraction(v) for v in integers), strict=True)))
    result = {'inputs': {name: units[name] for name in names}, 'output': output,
              'basis': ("Buckingham's pi theorem: a physical law can only combine its quantities so the units balance. "
                        'This uses the declared units, not the data, and cannot see dimensionless numbers or missing inputs.')}
    try:
        found, params = matrix.gauss_jordan_solve(rhs)
    except ValueError:
        result.update(possible=False, law=(f'No product of powers of {", ".join(names)} has the units {output}. The law needs a '
                                           'dimensional constant (such as G, c or k_B) or an input that is missing.'),
                      groups=[_group_text(group) for group in groups], shape=None)
        return result
    found = found.subs({param: 0 for param in params})
    exponents = {name: Fraction(int(sp.numer(v)), int(sp.denom(v))) for name, v in zip(names, found, strict=True)}
    if groups:  # prefer a particular solution that uses as few inputs as possible
        exponents = _sparsest(exponents, groups)
    prefix = ' * '.join(f'{name} ** {_exponent_text(p)}' if p != 1 else name for name, p in exponents.items() if p)
    shape = 'c1' + (f' * {prefix}' if prefix else '')
    for index, group in enumerate(groups[:3], start=2):
        shape += f' * ({_group_formula(group)}) ** c{index}'
    used = {name for name, p in exponents.items() if p} | {name for group in groups for name, p in group.items() if p}
    law = 'output = C' + ''.join(f' · {name}^{p}' if p != 1 else f' · {name}' for name, p in exponents.items() if p)
    if groups:
        law += ' · F(' + ', '.join(_group_text(group) for group in groups) + ')'
    result.update(possible=True, exponents={name: str(p) for name, p in exponents.items()},
                  groups=[_group_text(group) for group in groups], shape=shape, law=law,
                  drops_out=[name for name in names if name not in used],
                  unique=not groups,
                  note=('The units fix the law completely, up to one number C.' if not groups else
                        f'{len(groups)} dimensionless group(s) remain; their effect F is unknown, and the shape tested '
                        'assumes F is a power law.'))
    return result


def _sparsest(exponents, groups):
    best = dict(exponents)
    for group in groups:
        for name, power in group.items():
            if power and best.get(name):
                shift = -best[name] / power
                trial = {key: best[key] + shift * group.get(key, 0) for key in best}
                if sum(1 for v in trial.values() if v) < sum(1 for v in best.values() if v):
                    best = trial
    return best


def _group_formula(group):
    return ' * '.join(f'{name} ** {_exponent_text(p)}' if p != 1 else name for name, p in group.items() if p) or '1'


def _group_text(group):
    return '·'.join(f'{name}^{p}' if p != 1 else name for name, p in group.items() if p)


# ------------------------------------------------------------------ sympy

SYMPY_FUNCTIONS = {'sqrt': sp.sqrt, 'exp': sp.exp, 'log': sp.log, 'ln': sp.log, 'log10': lambda x: sp.log(x, 10),
                   'log2': lambda x: sp.log(x, 2), 'sin': sp.sin, 'cos': sp.cos, 'tan': sp.tan, 'asin': sp.asin,
                   'acos': sp.acos, 'atan': sp.atan, 'sinh': sp.sinh, 'cosh': sp.cosh, 'tanh': sp.tanh, 'abs': sp.Abs,
                   'min': sp.Min, 'max': sp.Max, 'floor': sp.floor, 'ceil': sp.ceiling,
                   'hypot': lambda *a: sp.sqrt(sum(x ** 2 for x in a))}


def domains(tests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each input's range in the cases, and the sign assumption that follows from it."""
    values: dict[str, list[float]] = {}
    for case in tests:
        for name, value in case['inputs'].items():
            values.setdefault(name, []).append(float(value))
    result = {}
    for name, seen in values.items():
        low, high = min(seen), max(seen)
        sign = 'positive' if low > 0 else 'nonnegative' if low >= 0 else 'negative' if high < 0 else 'real'
        result[name] = {'low': low, 'high': high, 'sign': sign}
    return result


def to_sympy(formula: str, domain: dict[str, dict[str, Any]]):
    """The formula as a sympy expression built from its checked tree (never sympify, which evaluates text)."""
    tree = invention_sim._tree(formula).body
    symbols = {name: sp.Symbol(name, **({info['sign']: True} if info['sign'] != 'real' else {'real': True}))
               for name, info in domain.items()}

    def visit(node):
        if isinstance(node, ast.Constant):
            return sp.Integer(node.value) if type(node.value) is int else sp.Rational(repr(node.value))
        if isinstance(node, ast.Name):
            if node.id in symbols:
                return symbols[node.id]
            if node.id in {'pi', 'tau'}:
                return sp.pi * (2 if node.id == 'tau' else 1)
            if node.id == 'e':
                return sp.E
            raise ValueError(f'{node.id} is not a case input; fit the constants first.')
        if isinstance(node, ast.UnaryOp):
            return -visit(node.operand) if isinstance(node.op, ast.USub) else visit(node.operand)
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            return {ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right,
                    ast.Div: lambda: left / right, ast.Pow: lambda: left ** right}[type(node.op)]()
        if isinstance(node, ast.Call):
            return SYMPY_FUNCTIONS[node.func.id](*[visit(arg) for arg in node.args])
        raise ValueError(f'Unsupported part of a formula: {type(node).__name__}')
    return visit(tree), symbols


def _sample(domain, count=160, seed=7, widen=0.0):
    rng = random.Random(seed)
    points = []
    for _ in range(count):
        point = {}
        for name, info in domain.items():
            low, high = info['low'], info['high']
            span = (high - low) or max(abs(high), 1.0) * 0.2
            low, high = low - widen * span, high + widen * span
            if info['sign'] == 'positive':
                low = max(low, min(info['low'], high) * 0.25 if info['low'] > 0 else 1e-9)
            elif info['sign'] == 'nonnegative':
                low = max(low, 0.0)
            point[name] = rng.uniform(low, high)
        points.append(point)
    return points


def _numeric(expression, symbols):
    function = sp.lambdify([symbols[name] for name in sorted(symbols)], expression, modules='math')

    def run(point):
        try:
            value = complex(function(*[point[name] for name in sorted(symbols)]))
        except (ValueError, ZeroDivisionError, OverflowError, TypeError):
            return None
        return value.real if abs(value.imag) < 1e-12 * max(1.0, abs(value.real)) and math.isfinite(value.real) else None
    return run


def _round_point(point):
    return {name: float(f'{value:.6g}') for name, value in point.items()}


def _sign_over(expression, symbols, domain, want: int, budget: _Budget):
    """Whether expression > 0 (want=1), < 0 (want=-1) or >= 0 (want=0) everywhere in the inputs' domain."""
    finished, decided = budget.run(lambda: (expression.is_positive, expression.is_negative, expression.is_nonnegative))
    if finished and decided:
        positive, negative, nonnegative = decided
        verdict = {1: positive, -1: negative, 0: nonnegative}[want]
        if verdict is True:
            return {'status': 'proved', 'method': 'sympy sign analysis under the inputs’ sign assumptions'}
    run = _numeric(expression, symbols)
    checked = 0
    for point in _sample(domain, 240):
        value = run(point)
        if value is None:
            continue
        checked += 1
        bad = value <= 0 if want == 1 else value >= 0 if want == -1 else value < 0
        scale = 1e-12 * max(1.0, abs(value))
        if bad and abs(value) > scale:
            return {'status': 'disproved', 'method': 'counterexample inside the cases’ range',
                    'counterexample': _round_point(point), 'value': float(f'{value:.6g}')}
    if not checked:
        return {'status': 'undecided', 'method': 'could not be evaluated'}
    return {'status': 'observed', 'method': f'held at {checked} sampled points across the cases’ range (not a proof)'}


def _elasticity(expression, symbol, budget):
    """x * d(ln f)/dx simplified: a constant means an exact power law in x."""
    finished, value = budget.run(lambda: sp.simplify(symbol * sp.diff(expression, symbol) / expression))
    return value if finished else None


def _family(name, elasticity, symbol, others):
    """Recognise the law family in one input from its elasticity."""
    if elasticity is None:
        return None
    if not elasticity.free_symbols:
        power = sp.nsimplify(elasticity, rational=True, tolerance=1e-9) if elasticity.is_number else elasticity
        return {'kind': 'power', 'power': str(power), 'text': f'proportional to {name}^{power}' if power != 1 else f'proportional to {name}'}
    rate = sp.simplify(elasticity / symbol)
    if rate.free_symbols <= others and symbol not in rate.free_symbols:
        return {'kind': 'exponential', 'rate': str(rate), 'text': f'exponential in {name} (rate {rate} per unit of {name})'}
    activation = sp.simplify(elasticity * symbol)
    if symbol not in activation.free_symbols:
        return {'kind': 'activated', 'scale': str(-activation),
                'text': f'Arrhenius-like in {name}: exp(-{-activation}/{name})'}
    saturating = sp.simplify(1 / elasticity - 1)
    if saturating.free_symbols and symbol in saturating.free_symbols:
        ratio = sp.simplify(saturating / (1 / symbol))
        if symbol not in ratio.free_symbols:
            return {'kind': 'saturating', 'scale': str(ratio), 'text': f'saturating in {name}: rises then levels off, half-way at {name} = {ratio}'}
    return None


MECHANISMS = {
    ('power', '-2'): 'an inverse-square law: something spreading from a point over a sphere’s surface (gravity, '
                     'light, radiation, sound intensity)',
    ('power', '-1'): 'an inverse proportion: a fixed amount shared over this input (pressure × volume, speed × time)',
    ('power', '1'): 'a proportional response: doubling this input doubles the result (Hooke’s law, Ohm’s law, unit cost)',
    ('power', '2'): 'a square law: energy of motion or of a field (½ mv², ½ kx²), or an area',
    ('power', '3'): 'a cube law: a volume, or wind power against wind speed',
    ('power', '4'): 'a fourth-power law: thermal radiation (Stefan–Boltzmann) or flow through a pipe (Poiseuille)',
    ('power', '1/2'): 'a square-root law: pendulums, wave speeds, diffusion and random-walk spreading',
    ('power', '-1/2'): 'an inverse square-root law: frequency of a pendulum or string, diffusion-limited rates',
    ('exponential', '-'): 'exponential decay: the rate of change is proportional to what is left (radioactive decay, '
                          'a capacitor discharging, cooling)',
    ('exponential', '+'): 'exponential growth: the rate of change is proportional to the current amount (compounding, '
                          'unchecked population growth)',
    ('activated', ''): 'a thermally activated process: only a fraction exp(-E/kT) has enough energy (reaction rates, '
                       'diffusion in solids, semiconductor leakage)',
    ('saturating', ''): 'saturation: a limited number of sites or capacity fills up (enzyme kinetics, Langmuir '
                        'adsorption, a sensor reaching its limit)',
}


def _mechanism(family):
    if not family:
        return None
    kind = family['kind']
    if kind == 'power':
        return MECHANISMS.get(('power', family['power']))
    if kind == 'exponential':
        return MECHANISMS[('exponential', '-' if family['rate'].startswith('-') else '+')]
    return MECHANISMS.get((kind, ''))


def properties(formula: str, tests: list[dict[str, Any]], budget: _Budget | None = None,
               units: dict[str, str] | None = None) -> dict[str, Any]:
    """What can be proved about the formula over the inputs’ domain, input by input."""
    budget = budget or _Budget()
    domain = domains(tests)
    expression, symbols = to_sympy(formula, domain)
    used = {name for name, symbol in symbols.items() if symbol in expression.free_symbols}
    rows, families = [], {}
    for name in sorted(domain):
        symbol = symbols[name]
        if name not in used:
            rows.append({'claim': f'does not depend on {name}', 'status': 'proved', 'method': f'{name} does not appear in the formula'})
            continue
        finished, derivative = budget.run(lambda s=symbol: sp.simplify(sp.diff(expression, s)))
        if finished and derivative == 0:
            rows.append({'claim': f'does not depend on {name}', 'status': 'proved', 'method': 'the derivative simplifies to 0'})
            continue
        if finished:
            rising = _sign_over(derivative, symbols, domain, 1, budget)
            if rising['status'] in {'proved', 'observed'}:
                rows.append({'claim': f'rises with {name}', **rising})
            else:
                falling = _sign_over(derivative, symbols, domain, -1, budget)
                if falling['status'] in {'proved', 'observed'}:
                    rows.append({'claim': f'falls with {name}', **falling})
                else:
                    rows.append({'claim': f'changes direction with {name}', 'status': 'proved' if rising['status'] == 'disproved'
                                 and falling['status'] == 'disproved' else 'undecided',
                                 'method': 'the derivative takes both signs inside the cases’ range'})
        family = _family(name, _elasticity(expression, symbol, budget), symbol, set(symbols.values()) - {symbol})
        if family:
            families[name] = family
            rows.append({'claim': family['text'], 'status': 'proved', 'method': 'x·∂ln f/∂x simplifies exactly',
                         'mechanism': _mechanism(family)})
        for point, label in ((0, '0'), (sp.oo, '∞')):
            if point == 0 and domain[name]['sign'] not in {'positive', 'nonnegative'}:
                continue
            finished, limit = budget.run(lambda s=symbol, p=point: sp.limit(expression, s, p, '+' if p == 0 else '-'),
                                         seconds=3.0)
            if finished and limit is not None and not limit.has(sp.AccumBounds) and limit.free_symbols <= set(symbols.values()):
                rows.append({'claim': f'as {name} → {label}, the result → {_short(limit)}', 'status': 'proved',
                             'method': 'sympy limit'})
    positive = _sign_over(expression, symbols, domain, 1, budget)
    if positive['status'] != 'disproved':
        rows.append({'claim': 'the result is positive', **positive})
    same_units = bool(units) and len({format_units(_dims(units[n])) for n in used if n in units}) == 1 and used <= set(units)
    if len(used) >= 2 and same_units:
        scale = sp.Symbol('lam', positive=True)
        finished, ratio = budget.run(lambda: sp.simplify(sp.powsimp(sp.expand_power_base(
            expression.subs({symbols[n]: scale * symbols[n] for n in used}, simultaneous=True) / expression, force=True))))
        if finished and ratio is not None and ratio.free_symbols <= {scale}:
            degree = sp.simplify(sp.log(ratio) / sp.log(scale)) if ratio != 1 else sp.Integer(0)
            if degree.is_number:
                rows.append({'claim': f'scaling every input by λ scales the result by λ^{_short(degree)}',
                             'status': 'proved', 'method': 'substitution and simplification'})
    if len(used) >= 2:
        names = sorted(used)
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                swapped = expression.subs({symbols[first]: symbols[second], symbols[second]: symbols[first]}, simultaneous=True)
                finished, same = budget.run(lambda w=swapped: sp.simplify(expression - w) == 0)
                if finished and same:
                    rows.append({'claim': f'symmetric in {first} and {second}', 'status': 'proved', 'method': 'swapping them leaves it unchanged'})
    return {'formula': formula, 'expression': _short(expression, 200), 'domain': domain, 'properties': rows,
            'families': families}


def _short(value, limit=80):
    text = re.sub(r'\boo\b', '∞', str(value).replace('**', '^'))
    return text if len(text) <= limit else text[:limit - 1] + '…'


# ------------------------------------------------------------------ owner claims

CLAIM_PATTERNS = (
    (r'(?:it |the result |output )?(?:increases|rises|grows|goes up) (?:with|as) (?P<x>\w+)(?: (?:increases|rises|grows))?', 'rising'),
    (r'(?:it |the result |output )?(?:decreases|falls|drops|goes down|shrinks) (?:with|as) (?P<x>\w+)(?: (?:increases|rises|grows))?', 'falling'),
    (r'(?:it |the result |output )?(?:is )?(?:always )?(?:positive|greater than (?:0|zero))', 'positive'),
    (r'(?:it |the result |output )?(?:is )?(?:never negative|non-?negative|at least (?:0|zero))', 'nonnegative'),
    (r'(?:it |the result |output )?(?:is )?(?:independent of|does not depend on|doesn\'t depend on) (?P<x>\w+)', 'independent'),
    (r'(?:it |the result |output )?(?:is )?symmetric in (?P<x>\w+) and (?P<y>\w+)', 'symmetric'),
    (r'(?:it |the result |output )?(?:is )?(?:proportional|linear) (?:to|in) (?P<x>\w+)', 'proportional'),
    (r'(?:it |the result |output )?(?:is |follows )?(?:an )?inverse[- ]square (?:in|with|of|law in) (?P<x>\w+)', 'inverse_square'),
    (r'(?:it |the result |output )?(?:is )?(?:zero|0) (?:when|at|if) (?P<x>\w+) ?= ?(?P<v>-?[\d.]+)', 'zero_at'),
    (r'(?:it |the result |output )?(?:is |stays )?(?:at most|no more than|never (?:above|more than)|below|under|bounded by) (?P<v>-?[\d.e+]+)', 'at_most'),
    (r'(?:it |the result |output )?(?:is |stays )?(?:at least|no less than|never below|above|over) (?P<v>-?[\d.e+]+)', 'at_least'),
)


def check_claims(formula: str, tests: list[dict[str, Any]], claims: list[str], budget: _Budget | None = None) -> list[dict[str, Any]]:
    """Prove or disprove each plain-language claim about the formula over the inputs’ domain."""
    budget = budget or _Budget()
    domain = domains(tests)
    expression, symbols = to_sympy(formula, domain)
    results = []
    for raw in claims or []:
        text = ' '.join(str(raw).split())[:200]
        cleaned = text.lower().rstrip('.!')
        kind, match = None, None
        for pattern, name in CLAIM_PATTERNS:
            match = re.fullmatch(pattern, cleaned)
            if match:
                kind = name
                break
        row = {'claim': text}
        if kind is None:
            results.append({**row, 'status': 'not understood',
                            'method': 'Try: "increases with x", "is positive", "symmetric in a and b", "at most 10".'})
            continue
        lookup = {name.lower(): name for name in symbols}
        variables = [lookup.get(match.groupdict().get(key) or '') for key in ('x', 'y') if match.groupdict().get(key)]
        if any(v is None for v in variables):
            results.append({**row, 'status': 'not understood', 'method': 'It names a quantity that is not a case input.'})
            continue
        try:
            results.append({**row, **_claim(kind, match, variables, expression, symbols, domain, budget)})
        except (ValueError, TypeError, ArithmeticError) as exc:
            results.append({**row, 'status': 'undecided', 'method': f'could not be analysed ({type(exc).__name__})'})
    return results


def _claim(kind, match, variables, expression, symbols, domain, budget):
    if kind in {'rising', 'falling'}:
        finished, derivative = budget.run(lambda: sp.simplify(sp.diff(expression, symbols[variables[0]])))
        if not finished:
            return {'status': 'undecided', 'method': 'the derivative was too slow to simplify'}
        return _sign_over(derivative, symbols, domain, 1 if kind == 'rising' else -1, budget)
    if kind in {'positive', 'nonnegative'}:
        return _sign_over(expression, symbols, domain, 1 if kind == 'positive' else 0, budget)
    if kind == 'independent':
        finished, derivative = budget.run(lambda: sp.simplify(sp.diff(expression, symbols[variables[0]])))
        if not finished:
            return {'status': 'undecided', 'method': 'the derivative was too slow to simplify'}
        if derivative == 0:
            return {'status': 'proved', 'method': 'the derivative is exactly 0'}
        run = _numeric(derivative, symbols)
        for point in _sample(domain, 120, seed=5):
            value = run(point)
            if value is not None and abs(value) > 1e-12:
                return {'status': 'disproved', 'method': f'the result changes with {variables[0]}',
                        'counterexample': _round_point(point)}
        return {'status': 'undecided', 'method': 'the derivative is not identically 0 but vanished at every sampled point'}
    if kind == 'symmetric':
        first, second = (symbols[v] for v in variables)
        swapped = expression.subs({first: second, second: first}, simultaneous=True)
        finished, difference = budget.run(lambda: sp.simplify(expression - swapped))
        if finished and difference == 0:
            return {'status': 'proved', 'method': 'swapping them leaves the formula unchanged'}
        point = _sample(domain, 1, seed=3)[0]
        value = _numeric(expression - swapped, symbols)(point) if finished else None
        if value is not None and abs(value) > 1e-9:
            return {'status': 'disproved', 'method': 'swapping them changes the result', 'counterexample': _round_point(point)}
        return {'status': 'undecided', 'method': 'could not be simplified'}
    if kind in {'proportional', 'inverse_square'}:
        want = 1 if kind == 'proportional' else -2
        value = _elasticity(expression, symbols[variables[0]], budget)
        if value is not None and not value.free_symbols:
            return ({'status': 'proved', 'method': f'x·∂ln f/∂x = {want} exactly'} if sp.simplify(value - want) == 0 else
                    {'status': 'disproved', 'method': f'the exponent in {variables[0]} is {_short(value)}, not {want}'})
        return {'status': 'disproved' if value is not None else 'undecided',
                'method': f'it is not a pure power of {variables[0]}' if value is not None else 'too slow'}
    if kind == 'zero_at':
        symbol, at = symbols[variables[0]], sp.Rational(match['v'])
        finished, value = budget.run(lambda: sp.simplify(sp.limit(expression, symbol, at)))
        if finished and value == 0:
            return {'status': 'proved', 'method': f'the limit at {variables[0]} = {match["v"]} is exactly 0'}
        if finished and value is not None and value.is_number:
            return {'status': 'disproved', 'method': f'the value there is {_short(value)}'}
        return {'status': 'undecided', 'method': 'it depends on the other inputs or was too slow'}
    bound = sp.Rational(match['v'])
    return _sign_over(bound - expression if kind == 'at_most' else expression - bound, symbols, domain, 0, budget)


# ------------------------------------------------------------------ cases, exactly

def prove_cases(formula: str, tests: list[dict[str, Any]], tolerance: Any = 0, budget: _Budget | None = None) -> dict[str, Any]:
    """Each frozen case checked in exact rational arithmetic, or to 50 significant digits where it cannot be exact."""
    budget = budget or _Budget()
    domain = domains(tests)
    expression, symbols = to_sympy(formula, domain)
    allowed = sp.Rational(str(tolerance or 0))
    exact = numeric = failed = 0
    rows = []
    for case in tests:
        values = {symbols[name]: sp.Rational(str(value)) for name, value in case['inputs'].items() if name in symbols}
        expected = sp.Rational(str(case['expected']))
        finished, value = budget.run(lambda v=values: sp.simplify(expression.subs(v)), seconds=2.0)
        limit = allowed * max(1, abs(expected))
        if finished and value is not None and value.is_Rational:
            passed = bool(abs(value - expected) <= limit)
            exact += passed
            rows.append({'inputs': case['inputs'], 'passed': bool(passed), 'how': 'exact'})
        else:
            finished, approx = budget.run(lambda v=values: sp.N(expression.subs(v), 50), seconds=2.0)
            if not finished or approx is None or not approx.is_real:
                rows.append({'inputs': case['inputs'], 'passed': False, 'how': 'not evaluated'})
                failed += 1
                continue
            passed = bool(abs(approx - expected) <= limit)
            numeric += passed
            rows.append({'inputs': case['inputs'], 'passed': bool(passed), 'how': '50 digits'})
        failed += not rows[-1]['passed']
    return {'total': len(tests), 'exact': exact, 'fifty_digit': numeric, 'failed': failed, 'cases': rows,
            'summary': (f'{exact} of {len(tests)} cases proved in exact arithmetic' +
                        (f', {numeric} verified to 50 significant digits (π, e and roots cannot be compared exactly)' if numeric else '') +
                        (f'; {failed} fail' if failed else '') + '.')}


# ------------------------------------------------------------------ decisive experiment

def decisive_test(formula: str, rivals: list[dict[str, Any]], tests: list[dict[str, Any]], tolerance: Any = 0) -> dict[str, Any] | None:
    """The point, inside or just beyond the cases, where the best formula and a rival disagree most."""
    domain = domains(tests)
    best = invention_sim.compile_formula(formula)
    points = [dict((k, float(v)) for k, v in case['inputs'].items()) for case in tests]
    points += _sample(domain, 500, seed=11, widen=0.5)
    margin = max(float(tolerance or 0), 0.01)
    found = None
    for rival in rivals:
        try:
            other = invention_sim.compile_formula(rival['formula'])
        except (ValueError, SyntaxError):
            continue
        for point in points:
            try:
                a, b = best(point), other(point)
            except ValueError:
                continue
            gap = abs(a - b) / max(abs(a), abs(b), 1e-12)
            if found is None or gap > found['gap']:
                found = {'gap': gap, 'inputs': _round_point(point), 'best': float(f'{a:.6g}'), 'rival': float(f'{b:.6g}'),
                         'rival_formula': rival['formula'], 'rival_description': rival.get('description') or rival['formula']}
    if found is None:
        return None
    inside = all(domain[name]['low'] <= value <= domain[name]['high'] for name, value in found['inputs'].items() if name in domain)
    found.update(inside_range=inside, decisive=found['gap'] > 2 * margin,
                 text=(f"Measure at {', '.join(f'{k} = {v:g}' for k, v in found['inputs'].items())}: the best formula predicts "
                       f"{found['best']:g}, {found['rival_description']} predicts {found['rival']:g} "
                       f"({found['gap'] * 100:.3g} % apart" + (', inside the cases’ range' if inside else ', beyond the cases') + ').' +
                       ('' if found['gap'] > 2 * margin else ' They barely differ anywhere nearby, so the cases cannot tell them apart.')))
    return found


def _rivals(data: dict[str, Any], formula: str) -> list[dict[str, Any]]:
    seen, rivals = {ast.unparse(invention_sim._tree(formula))}, []
    total = len(data.get('tests') or [])
    for record in reversed(data.get('rounds') or []):
        for row in record.get('alternatives') or []:
            key = ast.unparse(ast.parse(row['formula'], mode='eval'))
            if key not in seen and row.get('passed', 0) >= max(1, total - 1):
                seen.add(key)
                rivals.append(row)
        candidate = (record.get('candidate') or {}).get('formula') or ''
        try:
            key = ast.unparse(ast.parse(candidate, mode='eval')) if candidate else None
        except SyntaxError:
            key = None
        if key and key not in seen and record['measurement'].get('passed') == total:
            seen.add(key)
            rivals.append({'formula': candidate, 'description': f"round {record['round']}'s formula", 'passed': total})
    return rivals[:4]


# ------------------------------------------------------------------ the whole argument

def theorise(data: dict[str, Any], formula: str | None = None, units: dict[str, str] | None = None,
             output: str | None = None, claims: list[str] | None = None, cancel=None) -> dict[str, Any]:
    """Theory and proof for a project's best formula: units, derivation, proofs, claims and the decisive test."""
    budget = _Budget(cancel=cancel)
    tests = data.get('tests') or []
    if not tests:
        raise ValueError('Theory needs the project’s numeric cases.')
    best_round = data.get('best') or {}
    formula = str(formula or (best_round.get('candidate') or {}).get('formula') or '').strip()
    if not formula:
        raise ValueError('There is no formula yet: work the project out first, or give a formula.')
    tolerance = data.get('tolerance', 0)
    inputs = sorted({name for case in tests for name in case['inputs']})
    if invention_sim.free_parameters(formula, inputs):
        formula = invention_sim.fit(formula, tests, tolerance, cancel=cancel)['formula']
    result: dict[str, Any] = {'formula': formula, 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                              'scope': SCOPE}
    measured = invention_sim.grade(formula, tests, tolerance)
    result['fit'] = {'passed': measured['passed'], 'total': measured['total']}
    result['cases'] = prove_cases(formula, tests, tolerance, budget)
    if units:
        unknown = sorted(set(units) - set(inputs))
        if unknown:
            raise ValueError('Units were given for names that are not case inputs: ' + ', '.join(unknown))
        result['units'] = check_units(formula, units, output)
        if output and set(units) >= set(inputs):
            declared = {name: units[name] for name in inputs}
            derivation = _tested(derive(declared, output), tests, tolerance, cancel)
            if not derivation['possible'] or not _passes(derivation):
                # The units cannot balance, or balance without the physics, unless a dimensional constant takes part
                # (Doppler without the speed of sound says v cannot matter). If a coefficient matched a constant, try
                # the law with it as a fixed input: theory completed by the constant the data pointed to.
                for coefficient in result['units'].get('coefficients') or []:
                    constant = _constant(coefficient.get('constant') or '')
                    if not constant or constant['key'] in declared:
                        continue
                    completed = derive({**declared, constant['key']: constant['units']}, output)
                    if not completed['possible']:
                        continue
                    completed['completed_with'] = constant
                    completed['law'] += f" (with {constant['key']} = {constant['name']}, the constant the coefficient matched)"
                    completed['shape'] = re.sub(rf'\b{re.escape(constant["key"])}\b', f'({constant["value"]})', completed['shape'])
                    completed['without_constant'] = derivation['law']
                    completed = _tested(completed, tests, tolerance, cancel)
                    if not derivation['possible'] or _passed(completed) >= _passed(derivation):
                        derivation = completed
                    break
            result['derivation'] = derivation
            result['agreement'] = _agreement(result['units'], derivation)
    finished = budget.left() > 0
    if finished:
        result['proofs'] = properties(formula, tests, budget, units)
    every = list(claims or [])
    every += [text for text in data.get('constraints') or [] if _looks_like_claim(text)]
    if every:
        result['claims'] = check_claims(formula, tests, every[:16], budget)
    decisive = decisive_test(formula, _rivals(data, formula), tests, tolerance)
    if decisive is None and (result.get('derivation') or {}).get('tested', {}).get('formula'):
        derived = result['derivation']['tested']['formula']
        if ast.unparse(ast.parse(derived, mode='eval')) != ast.unparse(ast.parse(formula, mode='eval')):
            decisive = decisive_test(formula, [{'formula': derived, 'description': 'the unit-derived law'}], tests, tolerance)
    result['decisive_test'] = decisive
    result['ladder'] = _ladder(result, best_round)
    result['summary'] = describe(result)
    return result


def _tested(derivation, tests, tolerance, cancel):
    """The derived shape fitted to the cases (its free number C, and the powers of any dimensionless groups)."""
    if not derivation.get('shape'):
        return derivation
    try:
        fitted = invention_sim.fit(derivation['shape'], tests, tolerance, cancel=cancel)
        graded = invention_sim.grade(fitted['formula'], tests, tolerance)
        derivation['tested'] = {'formula': fitted['formula'], 'passed': graded['passed'], 'total': graded['total'],
                                'mean_relative_error': graded.get('mean_relative_error'),
                                'constants': fitted.get('parameters') or {}}
        leading = (fitted.get('parameters') or {}).get('c1')
        if leading is not None:
            derivation['tested']['c1_identified'] = _identify(repr(float(leading)), ZERO)[0]
    except (ValueError, ArithmeticError) as exc:
        derivation['tested'] = {'error': str(exc)[:200]}
    return derivation


def _passed(derivation):
    return (derivation.get('tested') or {}).get('passed', -1)


def _passes(derivation):
    tested = derivation.get('tested') or {}
    return bool(tested.get('total')) and tested.get('passed') == tested.get('total')


def _agreement(units_report, derivation):
    """Whether the formula is what dimensional analysis allows: every number in it a pure number or a named
    physical constant. A number that must carry units and matches no constant is a property of this device
    (a resistance, a time constant) that units can never supply -- open, not a failure."""
    if not units_report.get('consistent'):
        return {'agrees': False, 'detail': units_report['issues'][0]}
    coefficients = units_report.get('coefficients') or []
    device = [c for c in coefficients if not c.get('determined') or (c['units'] != 'dimensionless' and not c.get('constant'))]
    named = [c for c in coefficients if c.get('constant')]
    if device:
        listed = ', '.join(f"{c['text']} ({c['units']})" for c in device[:4])
        return {'agrees': None, 'device_constants': [{'text': c['text'], 'units': c['units']} for c in device],
                'detail': (f'Units alone cannot fix this law: it holds device constants that must be measured or explained — '
                           f'{listed}.')}
    detail = 'Every number in the formula is a pure number' + (
        ' or a named physical constant (' + ', '.join(sorted({c['constant'] for c in named})) + ')' if named else '')
    if derivation.get('groups') and not _passes(derivation):
        detail += (f"; the units leave a function F({', '.join(derivation['groups'])}) open, and the formula supplies it "
                   '(a simple power-law guess for F does not fit, which is not evidence against the formula)')
    return {'agrees': True, 'detail': detail + '.'}


def _looks_like_claim(text):
    lowered = ' '.join(str(text).lower().split()).rstrip('.!')
    return any(re.fullmatch(pattern, lowered) for pattern, _ in CLAIM_PATTERNS)


def _ladder(result, best_round):
    fit = result['fit']
    loo = best_round.get('leave_one_out') if best_round.get('candidate', {}).get('formula') == result['formula'] else None
    units = result.get('units')
    agreement = result.get('agreement') or {}
    claims = result.get('claims') or []
    rungs = [
        ('Fits the frozen cases', fit['total'] and fit['passed'] == fit['total'], f"{fit['passed']} of {fit['total']} cases"),
        ('Predicts cases it was not fitted to (leave-one-out)',
         bool(loo) and loo.get('predicted_within_tolerance') == loo.get('cases'),
         (f"{loo.get('predicted_within_tolerance')} of {loo.get('cases')} predicted" if loo else 'not run for this formula')),
        ('Units are consistent', units['consistent'] if units else None,
         ('consistent' if units['consistent'] else units['issues'][0]) if units else 'declare the units to check'),
        ('Agrees with the law derived from units alone', agreement.get('agrees'),
         agreement.get('detail') or 'declare input and output units to derive it'),
        ('Owner’s claims proved', all(c['status'] == 'proved' for c in claims) if claims else None,
         (f"{sum(c['status'] == 'proved' for c in claims)} of {len(claims)} proved" if claims else 'no claims stated')),
        ('Measured on a real device', None, 'not something software can do: measure at the decisive point'),
    ]
    return [{'rung': name, 'met': met, 'detail': detail} for name, met, detail in rungs]


def plain_words(result: dict[str, Any]) -> str:
    """One sentence without symbols or jargon: what was found, and what is still not known."""
    fit = result['fit']
    parts = [f"The formula matches {fit['passed']} of your {fit['total']} measurements"]
    units = result.get('units')
    if units:
        parts.append('its units make sense' if units['consistent'] else 'but its units do not make sense, so it cannot be a physical law as written')
    agreement = result.get('agreement') or {}
    if agreement.get('agrees'):
        parts.append('and it agrees with what physics allows from the units alone')
    elif agreement.get('device_constants'):
        parts.append(f"and it depends on {len(agreement['device_constants'])} number(s) belonging to this particular device "
                     'that still need measuring or explaining')
    claims = result.get('claims') or []
    wrong = [claim['claim'] for claim in claims if claim['status'] == 'disproved']
    if wrong:
        parts.append('but this statement is false for it: “' + wrong[0] + '”')
    return ('In plain words: ' + ', '.join(parts) + '. It has not been tested on a real device; '
            'the decisive experiment below says where to measure next.' if result.get('decisive_test') else
            'In plain words: ' + ', '.join(parts) + '. It has not been tested on a real device.')


def describe(result: dict[str, Any]) -> list[str]:
    """Plain lines for chat, the report and the window; the first is for someone who is not a scientist."""
    lines = [plain_words(result),
             f"Formula: {result['formula']} — {result['fit']['passed']} of {result['fit']['total']} frozen cases.",
             'Cases: ' + result['cases']['summary']]
    units = result.get('units')
    if units:
        if units['consistent']:
            named = [c for c in units.get('coefficients', []) if c.get('determined') and c['units'] != 'dimensionless']
            text = 'Units: consistent.'
            if named:
                text += ' ' + '; '.join(f"{c['text']} must carry {c['units']}" + (f" — {c['identified']}" if c.get('identified') else '')
                                        for c in named[:4]) + '.'
            pure = [c for c in units.get('coefficients', []) if c.get('identified') and c['units'] == 'dimensionless']
            if pure:
                text += ' ' + '; '.join(f"{c['text']} is a pure number {c['identified']}" for c in pure[:3]) + '.'
            lines.append(text)
        else:
            lines.append('Units: NOT consistent. ' + ' '.join(units['issues']))
    derivation = result.get('derivation')
    if derivation:
        line = 'From units alone: ' + derivation['law']
        if derivation.get('drops_out'):
            line += f" ({', '.join(derivation['drops_out'])} cannot affect the result)"
        tested = derivation.get('tested') or {}
        line = line.rstrip('.')
        if 'passed' in tested:
            line += f". Fitted to the cases: {tested['formula']} passes {tested['passed']} of {tested['total']}"
            if tested.get('c1_identified'):
                line += f", and its number C = {tested['constants']['c1']:.6g} is {tested['c1_identified']}"
        lines.append(line + '.')
    agreement = result.get('agreement')
    if agreement:
        verdict = {True: 'agrees', False: 'does not agree', None: 'open'}[agreement.get('agrees')]
        lines.append(f"Dimensional agreement: {verdict}. {agreement['detail']}")
    proofs = (result.get('proofs') or {}).get('properties') or []
    proved = sorted((row for row in proofs if row['status'] == 'proved'), key=lambda row: '→' in row['claim'])
    if proved:
        lines.append('Proved: ' + '; '.join(row['claim'] for row in proved[:14]) + '.')
    observed = [row for row in proofs if row['status'] == 'observed']
    if observed:
        lines.append('Observed only (sampled, not proved): ' + '; '.join(row['claim'] for row in observed[:4]) + '.')
    mechanisms = list(dict.fromkeys(row['mechanism'] for row in sorted(
        (row for row in proofs if row.get('mechanism')), key=lambda row: row['mechanism'].startswith('a proportional'))))
    if mechanisms:
        lines.append('Possible mechanism to test: ' + '; or '.join(mechanisms[:2]) +
                     '. This is a hypothesis the shape suggests, not established.')
    for claim in result.get('claims') or []:
        detail = claim.get('method', '')
        if claim.get('counterexample'):
            detail += ' at ' + ', '.join(f'{k} = {v:g}' for k, v in claim['counterexample'].items())
        lines.append(f"Claim “{claim['claim']}”: {claim['status']} ({detail}).")
    if result.get('decisive_test'):
        lines.append('Decisive experiment: ' + result['decisive_test']['text'])
    met = [rung for rung in result.get('ladder') or [] if rung['met']]
    lines.append(f"Evidence: {len(met)} of {len(result.get('ladder') or [])} rungs met. " + SCOPE)
    return lines
