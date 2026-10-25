"""Working an invention's numbers out, and simulating what its formula does.

The workbench grades a formula on frozen numeric cases. This module widens what
a formula can say and what HAYAT can do with it:

* Formulas may use pi, e, tau and a fixed set of math functions (sqrt, exp, log,
  sin, min, max, ...). Exact arithmetic is tried first; only a formula exact
  arithmetic cannot express is evaluated in floating point, and each case says
  which it was. The formula is parsed and checked node by node: nothing outside
  numbers, names, arithmetic and the listed functions can run.
* Unknown constants are fitted. A formula such as ``c1 * x ** c2`` names its
  shape; the constants are fitted to the cases by least squares, then rounded
  to the shortest values that grade no worse. The model chooses the shape;
  HAYAT works the numbers out.
* ``work_out`` tries standard shapes (straight line, proportional, quadratic,
  power law, exponential, logarithmic, inverse, product, ratio) without a model.
  A shape with as many constants as cases fits anything and proves nothing, so
  it ranks last, and every fit that leaves room is checked by leave-one-out:
  each case predicted by a fit that never saw it.
* ``simulate`` runs the formula over its inputs: a sweep of each input (where it
  rises, falls or breaks), the effect of a 10 % change in each input, and a
  seeded Monte Carlo of every case with its inputs off by up to +/- spread.

None of this tests the physical device. It shows what the formula does, and
the reports say so.
"""
from __future__ import annotations

import ast
import math
import re
import statistics
from decimal import Decimal
from fractions import Fraction
from typing import Any, Callable

from app.calculation import _evaluate, _format
from app.models.streaming import check_cancelled

FUNCTIONS: dict[str, Callable[..., float]] = {
    'sqrt': math.sqrt, 'exp': math.exp, 'log': math.log, 'ln': math.log, 'log10': math.log10, 'log2': math.log2,
    'sin': math.sin, 'cos': math.cos, 'tan': math.tan, 'asin': math.asin, 'acos': math.acos, 'atan': math.atan,
    'sinh': math.sinh, 'cosh': math.cosh, 'tanh': math.tanh, 'abs': abs, 'min': min, 'max': max,
    # Floats, so floor(a) ** floor(b) can never become a huge whole-number power.
    'floor': lambda value: float(math.floor(value)), 'ceil': lambda value: float(math.ceil(value)), 'hypot': math.hypot,
}
CONSTANTS = {'pi': math.pi, 'e': math.e, 'tau': math.tau}
MAX_NODES = 96
MAX_PARAMETERS = 6
#: A formula's value beyond this is treated as a failure, not a number.
LIMIT = 1e30
_BIG_RESIDUAL = 1e3


def _check_cancel(cancel):
    check_cancelled()
    if cancel is not None:
        check_cancelled(cancel)


# ------------------------------------------------------------------ safe formulas

def _tree(formula: str) -> ast.Expression:
    formula = str(formula or '').strip()
    if not formula:
        raise ValueError('Empty formula')
    if len(formula) > 240:
        raise ValueError('Formula exceeds 240 characters')
    tree = ast.parse(formula, mode='eval')
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise ValueError('Formula is too large')
    allowed_ops = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.UAdd, ast.USub)
    for node in nodes:
        if isinstance(node, (ast.Expression, ast.Load)) or isinstance(node, allowed_ops):
            continue
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            continue
        if isinstance(node, (ast.BinOp, ast.UnaryOp)):
            continue
        if isinstance(node, ast.Name) and re.fullmatch(r'[A-Za-z][A-Za-z_0-9]{0,31}', node.id):
            continue
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS
                and not node.keywords and 1 <= len(node.args) <= 4):
            continue
        raise ValueError(f'Unsupported part of a formula: {type(node).__name__}')
    return tree


def names_in(formula: str) -> set[str]:
    """Variable names in a formula (function names excluded)."""
    tree = _tree(formula)
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and id(node) not in called}


def free_parameters(formula: str, inputs: set[str] | list[str]) -> list[str]:
    """Names that are neither case inputs nor known constants: the constants to fit."""
    return sorted(names_in(formula) - set(inputs) - set(CONSTANTS))


def compile_formula(formula: str) -> Callable[[dict[str, float]], float]:
    """A fast float evaluator for a validated formula.

    Every literal becomes a float: with whole numbers, Python would compute 10**10**10 exactly and never
    finish; as floats it overflows at once and the case fails.
    """
    class Floats(ast.NodeTransformer):
        def visit_Constant(self, node):
            return ast.copy_location(ast.Constant(float(node.value)), node)
    code = compile(ast.fix_missing_locations(Floats().visit(_tree(formula))), '<formula>', 'eval')
    base = {'__builtins__': {}, **FUNCTIONS, **CONSTANTS}

    def run(bindings: dict[str, float]) -> float:
        try:
            value = float(eval(code, base, dict(bindings)))  # noqa: S307 -- the tree was checked node by node above
        except NameError as exc:
            raise ValueError(str(exc)) from None
        except Exception as exc:  # overflow, a complex result, or Python's own errors with no builtins present
            raise ValueError(f'Cannot be evaluated here ({type(exc).__name__})') from None
        if not math.isfinite(value) or abs(value) > LIMIT:
            raise ValueError('Result is not a finite number within range')
        return value
    return run


def evaluate(formula: str, bindings: dict[str, Any]) -> tuple[Fraction, bool]:
    """(value, exact). Exact arithmetic when the formula allows it, floating point otherwise."""
    _tree(formula)
    decimals = {key: Decimal(str(value)) for key, value in bindings.items()}
    try:
        return _evaluate(formula, decimals), True
    except ValueError as exc:
        # 'too large' is about an intermediate step (two masses multiply to 1e53 before dividing by r**2):
        # floating point carries it, and only the final value is held to the limit.
        if 'Indeterminate' in str(exc):
            raise
    value = compile_formula(formula)({key: float(value) for key, value in decimals.items()})
    return Fraction(value), False


# ------------------------------------------------------------------ grading

def grade(formula: str, tests: list[dict[str, Any]], tolerance: Any = 0) -> dict[str, Any]:
    """Grade a formula on the frozen cases; each case records how far off it was and whether it was exact."""
    tolerance = Fraction(Decimal(str(tolerance or 0)))
    observations, errors = [], []
    for case in tests:
        try:
            actual, exact = evaluate(formula, case['inputs'])
            expected = Fraction(Decimal(str(case['expected'])))
            gap = abs(actual - expected)
            error = float(gap / max(Fraction(1), abs(expected)))
            errors.append(error)
            observations.append({'inputs': case['inputs'], 'expected': str(case['expected']),
                                 'actual': _format(actual) if exact else f'{float(actual):.12g}',
                                 'relative_error': round(error, 9), 'exact': exact,
                                 'passed': gap == 0 if not tolerance else gap <= tolerance * max(Fraction(1), abs(expected))})
        except Exception as exc:
            observations.append({'inputs': case['inputs'], 'expected': str(case['expected']),
                                 'passed': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'})
    return {'cases': observations, 'passed': sum(x['passed'] for x in observations),
            'total': len(tests), 'tolerance': str(float(tolerance)) if tolerance else '0',
            'mean_relative_error': round(sum(errors) / len(errors), 9) if errors else None,
            'did_not_run': len(tests) - len(errors),
            'floating_point_cases': sum(1 for x in observations if x.get('exact') is False),
            'scope': 'Supplied numeric acceptance cases only.',
            'novelty_verified': False, 'physical_validation': False}


# ------------------------------------------------------------------ fitting constants

def _inputs(tests: list[dict[str, Any]]) -> list[str]:
    return sorted({name for case in tests for name in case['inputs']})


def _rows(tests: list[dict[str, Any]]) -> list[tuple[dict[str, float], float]]:
    return [({key: float(Decimal(str(value))) for key, value in case['inputs'].items()}, float(Decimal(str(case['expected']))))
            for case in tests]


def _substitute(formula: str, values: dict[str, float | str]) -> str:
    class Replace(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id not in values:
                return node
            text = str(values[node.id])
            if '/' in text:  # a simple fraction such as 1/3, kept exact
                numerator, denominator = text.split('/')
                value = ast.BinOp(ast.Constant(abs(int(numerator))), ast.Div(), ast.Constant(int(denominator)))
                value = ast.UnaryOp(ast.USub(), value) if numerator.startswith('-') else value
                return ast.copy_location(value, node)
            number = float(text)
            literal = ast.Constant(int(text) if text.lstrip('-').isdigit() else float(text))
            if number < 0:
                literal = ast.Constant(-literal.value)
                return ast.copy_location(ast.UnaryOp(ast.USub(), literal), node)
            return ast.copy_location(literal, node)
    class Simplify(ast.NodeTransformer):
        """1*x -> x, x*1 -> x, x+0 -> x, x**1 -> x: fitted constants that came out as identities."""
        def visit_BinOp(self, node):
            self.generic_visit(node)
            left, right = node.left, node.right

            def is_(value, number):
                return isinstance(value, ast.Constant) and value.value == number

            def negative_power(value):
                return (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Pow)
                        and isinstance(value.right, ast.UnaryOp) and isinstance(value.right.op, ast.USub)
                        and isinstance(value.right.operand, ast.Constant))

            def inverse(power):  # x ** -2 -> x ** 2, x ** -1 -> x
                exponent = power.right.operand
                return power.left if exponent.value == 1 else ast.BinOp(power.left, ast.Pow(), exponent)
            if isinstance(node.op, ast.Mult) and (is_(left, 0) or is_(right, 0)):
                return ast.Constant(0)
            if isinstance(node.op, ast.Div) and is_(left, 0):
                return ast.Constant(0)
            # V ** 2 * R ** -1 reads as V ** 2 / R
            if isinstance(node.op, ast.Mult) and negative_power(right):
                return ast.BinOp(left, ast.Div(), inverse(right))
            if isinstance(node.op, ast.Mult) and negative_power(left) and not negative_power(right):
                return ast.BinOp(right, ast.Div(), inverse(left))
            if (isinstance(node.op, ast.Mult) and isinstance(left, ast.BinOp) and isinstance(left.op, ast.Div)
                    and is_(left.left, 1)):
                return ast.BinOp(right, ast.Div(), left.right)
            # x ** (1 / 2) -> sqrt(x)
            if (isinstance(node.op, ast.Pow) and isinstance(right, ast.BinOp) and isinstance(right.op, ast.Div)
                    and is_(right.left, 1) and is_(right.right, 2)):
                return ast.Call(ast.Name('sqrt', ast.Load()), [left], [])
            # -1 * x -> -x
            if (isinstance(node.op, ast.Mult) and isinstance(left, ast.UnaryOp) and isinstance(left.op, ast.USub)
                    and is_(left.operand, 1)):
                return ast.UnaryOp(ast.USub(), right)
            if isinstance(node.op, ast.Mult) and is_(left, 1):
                return right
            if isinstance(node.op, (ast.Mult, ast.Div, ast.Pow)) and is_(right, 1):
                return left
            if isinstance(node.op, ast.Add) and is_(left, 0):
                return right
            if isinstance(node.op, (ast.Add, ast.Sub)) and is_(right, 0):
                return left
            # a + -c * x  ->  a - c * x, and a - -c * x -> a + c * x: fitted negatives read as people write them.
            if isinstance(node.op, (ast.Add, ast.Sub)):
                negative = right
                while isinstance(negative, ast.BinOp) and isinstance(negative.op, (ast.Mult, ast.Div)):
                    negative = negative.left
                if isinstance(negative, ast.UnaryOp) and isinstance(negative.op, ast.USub):
                    if negative is right:
                        node.right = right.operand
                    else:
                        parent = right
                        while parent.left is not negative:
                            parent = parent.left
                        parent.left = negative.operand
                    node.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
            return node
    tree = Simplify().visit(Replace().visit(_tree(formula)))
    return ast.unparse(ast.fix_missing_locations(tree))


def _fraction(value: float) -> str | None:
    """'2' or '1/3' when the value is a whole number or a simple fraction to within a part in a million."""
    nearest = Fraction(value).limit_denominator(12)
    if abs(float(nearest) - value) > 1e-6 * max(1.0, abs(value)) or (value != 0 and nearest == 0):
        return None
    return str(nearest.numerator) if nearest.denominator == 1 else f'{nearest.numerator}/{nearest.denominator}'


def _short(value: float, digits: int) -> str:
    text = f'{value:.{digits}g}'
    if 'e' in text or 'E' in text:
        text = format(Decimal(text), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


_STEP = 2.0 ** -26  # √(machine epsilon): SciPy's own forward-difference step


def _vector_functions() -> dict[str, Any]:
    import functools

    import numpy as np

    def fold(ufunc):
        return lambda *values: functools.reduce(ufunc, values)
    return {'sqrt': np.sqrt, 'exp': np.exp, 'log': np.log, 'ln': np.log, 'log10': np.log10, 'log2': np.log2,
            'sin': np.sin, 'cos': np.cos, 'tan': np.tan, 'asin': np.arcsin, 'acos': np.arccos, 'atan': np.arctan,
            'sinh': np.sinh, 'cosh': np.cosh, 'tanh': np.tanh, 'abs': np.abs, 'min': fold(np.minimum),
            'max': fold(np.maximum), 'floor': np.floor, 'ceil': np.ceil, 'hypot': fold(np.hypot)}


def _vector_residuals(formula: str, params: list[str], rows: list[tuple[dict[str, float], float]]):
    """Residuals of every case in one NumPy evaluation (hard-tested 2026-10-17: one Python evaluation per case per
    step was 2.3 million evaluations for the ideal-gas task). A case that cannot be evaluated (a negative square
    root, a power that is not real, an overflow) gets the same large residual the per-case path gave it."""
    import numpy as np

    class Floats(ast.NodeTransformer):
        def visit_Constant(self, node):
            return ast.copy_location(ast.Constant(float(node.value)), node)
    code = compile(ast.fix_missing_locations(Floats().visit(_tree(formula))), '<formula>', 'eval')
    base = {'__builtins__': {}, **_vector_functions(), **CONSTANTS}
    names = sorted({name for inputs, _ in rows for name in inputs})
    columns = {name: np.array([inputs.get(name, 0.0) for inputs, _ in rows], dtype=float) for name in names}
    expected = np.array([value for _, value in rows], dtype=float)
    scale = np.maximum(1.0, np.abs(expected))

    def evaluate(bindings, shape):
        with np.errstate(all='ignore'):
            try:
                value = np.asarray(eval(code, base, {**columns, **bindings}), dtype=float)  # noqa: S307 -- checked tree
                if value.shape != shape:
                    value = np.broadcast_to(value, shape)
            except (ArithmeticError, ValueError, TypeError):
                return np.full(shape, _BIG_RESIDUAL)
            out = (value - expected) / scale
        bad = ~np.isfinite(value) | (np.abs(value) > LIMIT)
        return np.where(bad, _BIG_RESIDUAL, out)

    def residuals(theta):
        return evaluate(dict(zip(params, (float(x) for x in theta))), expected.shape)

    def jacobian(theta):
        # Forward differences, every constant at once: row 0 is θ, row i+1 is θ with constant i nudged.
        theta = np.asarray(theta, dtype=float)
        steps = _STEP * np.maximum(1.0, np.abs(theta)) * np.where(theta < 0, -1.0, 1.0)
        grid = np.tile(theta, (len(params) + 1, 1))
        grid[1:] += np.diag(steps)
        rows_out = evaluate({name: grid[:, [index]] for index, name in enumerate(params)}, (len(params) + 1, len(expected)))
        return ((rows_out[1:] - rows_out[0]) / steps[:, None]).T
    residuals.jacobian = jacobian
    return residuals


def _linear_solution(residuals, count: int):
    """The least-squares answer of a shape that is a straight line in its constants (c1 + c2*x, a quadratic, a
    product, c1*P*(1 + r)**n), or None for any other shape.

    The residuals of such a shape are r(θ) = r(0) + J θ with J fixed. J is read off at θ = 0 and each unit
    vector, the line is checked at two other points, and the answer is one linear solve (most of the standard
    shapes are of this kind; each used to run a dozen iterative fits from a dozen starts, hard-tested 2026-10-17).
    """
    import numpy as np

    zero = np.zeros(count)
    base = residuals(zero)
    if not np.all(np.isfinite(base)) or np.any(np.abs(base) >= _BIG_RESIDUAL):
        return None
    columns = []
    for index in range(count):
        unit = zero.copy()
        unit[index] = 1.0
        column = residuals(unit) - base
        if not np.all(np.isfinite(column)):
            return None
        columns.append(column)
    jacobian = np.column_stack(columns)
    for trial in (np.linspace(-1.7, 2.3, count), np.linspace(3.1, -0.6, count) * 7):
        predicted = base + jacobian @ trial
        actual = residuals(trial)
        if not np.all(np.isfinite(actual)) or np.any(np.abs(actual) >= _BIG_RESIDUAL):
            return None
        if np.max(np.abs(actual - predicted)) > 1e-9 * max(1.0, float(np.max(np.abs(actual)))):
            return None
    if np.linalg.matrix_rank(jacobian) < count:
        return None  # constants the cases cannot tell apart: left to the iterative fit and its plainest-answer rule
    theta, *_ = np.linalg.lstsq(jacobian, -base, rcond=None)
    final = residuals(theta)
    if not np.all(np.isfinite(final)) or np.any(np.abs(final) >= _BIG_RESIDUAL):
        return None
    from types import SimpleNamespace
    return SimpleNamespace(x=theta, cost=0.5 * float(final @ final))


def _least_squares(run, params, rows, starts, *, residuals=None, stop_when_exact=False, cancel=None):
    import numpy as np
    from scipy.optimize import least_squares

    def per_case(theta):
        out = []
        for inputs, expected in rows:
            try:
                value = run({**inputs, **dict(zip(params, theta))})
                out.append((value - expected) / max(1.0, abs(expected)))
            except (ArithmeticError, ValueError, TypeError):
                out.append(_BIG_RESIDUAL)
        return np.array(out)

    unguarded = residuals or per_case

    def residuals(theta):
        _check_cancel(cancel)
        return unguarded(theta)

    if getattr(unguarded, 'jacobian', None):
        def jacobian(theta):
            _check_cancel(cancel)
            return unguarded.jacobian(theta)
        residuals.jacobian = jacobian
    linear = _linear_solution(residuals, len(params))
    if linear is not None:
        return linear  # one best answer, solved directly: no starting point can find another
    # Relative errors of about a part in a billion or less in every case: the cases are matched as given.
    exact = 0.5 * len(rows) * 1e-18
    found = []
    # Levenberg-Marquardt runs its loop in compiled MINPACK code, several times faster than the trust-region
    # method's Python loop; it needs at least as many cases as constants, so the trust region does the rest.
    method = 'lm' if len(rows) >= len(params) else 'trf'
    jacobian = getattr(residuals, 'jacobian', None)
    # Without a supplied Jacobian, lm counts its derivative calls in the budget too.
    budget = 400 * len(params) * ((len(params) + 1) if method == 'lm' and jacobian is None else 1)
    for start in starts:
        try:
            result = least_squares(residuals, np.array(start, dtype=float), method=method, max_nfev=budget,
                                   jac=jacobian or '2-point')
        except (ValueError, ArithmeticError):
            continue
        if math.isfinite(result.cost):
            found.append(result)
            if stop_when_exact and result.cost <= exact:
                break  # no other start can do better, and without a wave no different answer can do as well
    if not found:
        return None
    # Several fits can explain the cases equally well: sin(x)/x and -sin(11.57x)/x agree at every multiple of
    # 0.5. Among the equally good, the one with the smallest constants is the plainer explanation.
    # Near-identical twins of one answer are not different explanations: then the most precise one wins.
    best = min(found, key=lambda result: result.cost)
    equal = [result for result in found if result.cost <= best.cost * (1 + 1e-6) + 1e-16]
    plainest = min(equal, key=lambda result: float(np.abs(result.x).sum()))
    size = float(np.abs(best.x).sum())
    return plainest if float(np.abs(plainest.x).sum()) < 0.9 * size else best


def _starts(count: int, extra: int = 5) -> list[list[float]]:
    import numpy as np

    starts = [[value] * count for value in (1.0, 0.5, 2.0, -1.0, 0.1, 10.0)]
    rng = np.random.default_rng(7)
    starts += [list(rng.normal(0, 3, count)) for _ in range(extra)]
    return starts


def fit(formula: str, tests: list[dict[str, Any]], tolerance: Any = 0, *, quick: bool = False,
        starts: list[list[float]] | None = None, cancel=None) -> dict[str, Any]:
    """Fit a formula's unknown constants to the cases. Returns the formula with the constants written in."""
    _check_cancel(cancel)
    inputs = _inputs(tests)
    params = free_parameters(formula, inputs)
    if not params:
        return {'formula': formula, 'shape': formula, 'parameters': {}, 'fitted': False}
    if len(params) > MAX_PARAMETERS:
        raise ValueError(f'At most {MAX_PARAMETERS} constants can be fitted; this formula has {len(params)}: {", ".join(params)}.')
    if not tests:
        raise ValueError('Fitting needs numeric cases.')
    run = compile_formula(formula)
    rows = _rows(tests)
    generic = _starts(len(params), 1 if quick else 5)[:2 if quick else None]
    given = [list(start) for start in starts or [] if len(start) == len(params)
             and all(math.isfinite(value) for value in start)]
    try:
        vector = _vector_residuals(formula, params, rows)
    except Exception:  # noqa: BLE001 -- the per-case path below is always available
        vector = None
    # Periodic shapes can match the cases equally well with different constants (sin(x)/x and -sin(11.57x)/x
    # at multiples of 0.5), so every start is tried for them and the plainest kept.
    periodic = any(isinstance(node, ast.Call) and node.func.id in {'sin', 'cos', 'tan'}
                   for node in ast.walk(_tree(formula)))
    result = _least_squares(run, params, rows, given + generic, residuals=vector, stop_when_exact=not periodic, cancel=cancel)
    if result is None:
        raise ValueError('The constants could not be fitted: the formula fails on these cases.')
    raw = {name: float(value) for name, value in zip(params, result.x)}
    exact = _substitute(formula, {name: repr(value) for name, value in raw.items()})
    if quick:
        return {'formula': exact, 'shape': formula, 'parameters': raw, 'fitted': True}
    reference = grade(exact, tests, tolerance)
    tidy, chosen = exact, None
    # The shortest constants that grade no worse: "2*x" rather than "1.9999999998*x". With a tolerance, a
    # tenth of it is slack (L ** 0.5 rather than L ** 0.50000012); exact projects allow none.
    slack = float(Decimal(str(tolerance or 0))) / 10
    allowed = (reference['mean_relative_error'] or 0) * 1.000001 + 1e-12 + slack

    def no_worse(candidate):
        graded = grade(candidate, tests, tolerance)
        return graded['passed'] >= reference['passed'] and (graded['mean_relative_error'] or 0) <= allowed
    # Constants that are really zero (a term the data does not need) are dropped first.
    largest = max((abs(value) for value in raw.values()), default=0) or 1
    for name, value in list(raw.items()):
        if abs(value) < 1e-7 * largest:
            trial = {**raw, name: 0.0}
            if no_worse(_substitute(formula, {k: repr(v) for k, v in trial.items()})):
                raw = trial
    # Each constant on its own: a whole number or a simple fraction (2, 1/3) when it is one to within a part in a
    # million, otherwise the fewest digits that grade no worse.
    simple = {name: _fraction(value) for name, value in raw.items()}
    for digits in range(1, 16):
        written = {name: simple[name] or _short(value, digits) for name, value in raw.items()}
        candidate = _substitute(formula, written)
        if no_worse(candidate):
            tidy, chosen = candidate, written
            break
    # The constants reported are the ones written in the formula.
    values = {name: float(Fraction(chosen[name])) if chosen else value for name, value in raw.items()}
    return {'formula': tidy, 'shape': formula, 'parameters': values, 'fitted': True}


def leave_one_out(shape: str, tests: list[dict[str, Any]], tolerance: Any = 0,
                  start: list[float] | None = None, cancel=None) -> dict[str, Any] | None:
    """Predict each input group from the others, excluding all repeats of that point.

    Repeating a measurement is useful, but a duplicate must not leak a held-out
    point into its training fit or masquerade as an independent validation case.
    """
    params = free_parameters(shape, _inputs(tests))
    def point_key(case):
        return tuple(sorted((name, Decimal(str(value))) for name, value in case['inputs'].items()))

    groups = {point_key(case) for case in tests}
    if not params or len(groups) < len(params) + 2 or len(tests) > 40:
        return None
    band = float(Decimal(str(tolerance or 0))) or 1e-9
    errors, within, failures = [], 0, 0
    fitted_groups = {}
    for case in tests:
        _check_cancel(cancel)
        group = point_key(case)
        try:
            if group not in fitted_groups:
                others = [other for other in tests if point_key(other) != group]
                fitted_groups[group] = fit(shape, others, quick=True, starts=[start] if start else None, cancel=cancel)
            fitted = fitted_groups[group]
            predicted = compile_formula(fitted['formula'])({k: float(Decimal(str(v))) for k, v in case['inputs'].items()})
        except (ValueError, ArithmeticError, TypeError):
            failures += 1
            errors.append(1.0)
            continue
        expected = float(Decimal(str(case['expected'])))
        error = abs(predicted - expected) / max(1.0, abs(expected))
        errors.append(error)
        within += error <= band
    return {'cases': len(tests), 'mean_relative_error': round(sum(errors) / len(errors), 9),
            'predicted_within_tolerance': within, 'independent_input_groups': len(groups),
            'duplicate_cases': len(tests) - len(groups), 'failed_predictions': failures,
            'basis': 'Each case was predicted by fitting only different input points; all repeats of the held-out '
                     'input point were excluded together. This is internal cross-validation after shape selection, '
                     'not an independent external test.'}


# ------------------------------------------------------------------ standard shapes

def _parameter_names(inputs: list[str], count: int) -> list[str]:
    taken = set(inputs) | set(CONSTANTS) | set(FUNCTIONS)
    names, index = [], 1
    while len(names) < count:
        name = f'c{index}'
        if name not in taken:
            names.append(name)
        index += 1
    return names


def _line(xs: list[list[float]], ys: list[float]) -> list[float] | None:
    """Least-squares coefficients of ys on [1, *xs]: the closed-form start for shapes that become a straight line."""
    import numpy as np

    try:
        matrix = np.column_stack([np.ones(len(ys)), *[np.array(column, dtype=float) for column in xs]])
        solution, *_ = np.linalg.lstsq(matrix, np.array(ys, dtype=float), rcond=None)
    except (ValueError, np.linalg.LinAlgError):
        return None
    values = [float(value) for value in solution]
    return values if all(math.isfinite(value) for value in values) else None


def shapes(tests: list[dict[str, Any]]) -> list[tuple[str, str, list[list[float]]]]:
    """(description, shape, starting points) worth trying for these inputs.

    Shapes that turn into a straight line under a logarithm (power laws, exponentials, the logistic curve)
    get a closed-form starting point, so a constant like 6.674e-11 is found instead of searched for from 1.
    """
    inputs = _inputs(tests)
    rows = _rows(tests)
    ys = [y for _, y in rows]
    c = _parameter_names(inputs, max(len(inputs) + 3, 6))
    column = {name: [row[0].get(name, 0.0) for row in rows] for name in inputs}
    positive = {name: all(value > 0 for value in column[name]) for name in inputs}
    nonzero = {name: all(value != 0 for value in column[name]) for name in inputs}
    y_positive = all(y > 0 for y in ys)
    logs = {name: [math.log(value) for value in column[name]] for name in inputs if positive[name]}
    log_y = [math.log(y) for y in ys] if y_positive else None

    def power_start(names):
        if log_y is None or not all(name in logs for name in names):
            return []
        line = _line([logs[name] for name in names], log_y)
        return [[math.exp(line[0]), *line[1:]]] if line else []

    found: list[tuple[str, str, list[list[float]]]] = [('a constant', c[0], [[statistics.fmean(ys)]])]
    for x in inputs:
        found += [(f'proportional to {x}', f'{c[0]}*{x}', []),
                  (f'a straight line in {x}', f'{c[0]} + {c[1]}*{x}', []),
                  (f'a quadratic in {x}', f'{c[0]} + {c[1]}*{x} + {c[2]}*{x}**2', [])]
        exponential = _line([column[x]], log_y) if log_y else None
        found.append((f'exponential in {x}', f'{c[0]}*exp({c[1]}*{x})',
                      [[math.exp(exponential[0]), exponential[1]]] if exponential else []))
        if positive[x]:
            found += [(f'a power law in {x}', f'{c[0]}*{x}**{c[1]}', power_start([x])),
                      (f'logarithmic in {x}', f'{c[0]} + {c[1]}*log({x})', [])]
        if nonzero[x]:
            found.append((f'inverse in {x}', f'{c[0]} + {c[1]}/{x}', []))
        # Settling towards a level: cooling, charging, a learning curve.
        ordered = sorted(zip(column[x], ys))
        span = (ordered[-1][0] - ordered[0][0]) or 1.0
        level, first = ordered[-1][1], ordered[0][1]
        found.append((f'settling towards a level as {x} grows', f'{c[0]} + {c[1]}*exp({c[2]}*{x})',
                      [[level, first - level, -rate / span] for rate in (1.0, 3.0, 6.0)]))
        # An S-curve: growth that levels off.
        if y_positive and len(rows) >= 4:
            ceiling = max(ys) * 1.05
            line = _line([column[x]], [math.log(max(ceiling / y - 1, 1e-9)) for y in ys])
            if line:
                found.append((f'an S-curve (logistic) in {x}', f'{c[0]}/(1 + {c[1]}*exp({c[2]}*{x}))',
                              [[ceiling, math.exp(line[0]), line[1]]]))
        # A decaying wave: try a spread of frequencies, since the fit cannot find one it does not start near.
        if len(rows) >= 6:
            size = max(abs(y) for y in ys) or 1.0
            found.append((f'a damped oscillation in {x}', f'{c[0]}*exp({c[1]}*{x})*cos({c[2]}*{x} + {c[3]})',
                          [[size, -0.2, omega, phase] for omega in (0.5, 1.0, 2.0, 3.0, 5.0, 8.0) for phase in (0.0, -1.5)]))
    if len(inputs) > 1:
        terms = ' + '.join(f'{c[index + 1]}*{x}' for index, x in enumerate(inputs))
        found.append(('a straight line in every input', f'{c[0]} + {terms}', []))
        found.append(('proportional to the product of the inputs', f'{c[0]}*' + '*'.join(inputs), []))
        if all(positive.values()) and len(inputs) <= MAX_PARAMETERS - 1:
            powers = '*'.join(f'{x}**{c[index + 1]}' for index, x in enumerate(inputs))
            found.append(('a power law in every input', f'{c[0]}*{powers}', power_start(inputs)))
        for first in inputs:
            for second in inputs:
                if first == second:
                    continue
                if first < second:
                    found.append((f'proportional to {first}×{second}', f'{c[0]}*{first}*{second}', []))
                    found.append((f'{first}, {second} and their product',
                                  f'{c[0]} + {c[1]}*{first} + {c[2]}*{second} + {c[3]}*{first}*{second}', []))
                    found.append((f'a quadratic in {first} and {second}',
                                  f'{c[0]} + {c[1]}*{first} + {c[2]}*{second} + {c[3]}*{first}*{second} + '
                                  f'{c[4]}*{first}**2 + {c[5]}*{second}**2', []))
                if nonzero[second]:
                    found.append((f'proportional to {first}/{second}', f'{c[0]}*{first}/{second}', []))
                # Decay or growth of an amount: N0 * exp(-k t).
                ratio = ([math.log(y / value) for y, value in zip(ys, column[first])]
                         if y_positive and positive[first] else None)
                line = _line([column[second]], ratio) if ratio else None
                found.append((f'{first} growing or decaying exponentially with {second}', f'{c[0]}*{first}*exp({c[1]}*{second})',
                              [[math.exp(line[0]), line[1]]] if line else []))
                # Ohm's law with an internal resistance: x / (y + r).
                if nonzero[first]:
                    inverse_line = _line([column[second]], [value / y for value, y in zip(column[first], ys)]) \
                        if all(y != 0 for y in ys) else None
                    if inverse_line and inverse_line[1]:
                        found.append((f'{first} over {second} plus a constant', f'{c[0]}*{first}/({second} + {c[1]})',
                                      [[1 / inverse_line[1], inverse_line[0] / inverse_line[1]]]))
                # Compound growth: amount * (1 + rate) ** periods.
                for third in inputs:
                    if third not in (first, second):
                        if first < second and len(inputs) <= 4:
                            # Setup cost plus quantity times unit cost, and analogous additive-product laws.
                            # Bound this family to small input sets so the search does not grow cubically at 12 inputs.
                            found.append((f'{third} plus the product of {first} and {second}',
                                          f'{c[0]}*{third} + {c[1]}*{first}*{second}', []))
                        found.append((f'{first} compounded at rate {second} over {third} periods',
                                      f'{c[0]}*{first}*(1 + {second})**{third}', [[1.0]]))
    return found


def work_out(tests: list[dict[str, Any]], tolerance: Any = 0, *, cancel=None) -> dict[str, Any]:
    """Fit every standard shape to the cases and rank them, with no model involved."""
    if not tests:
        raise ValueError('Working a formula out needs numeric cases.')
    tried = []
    for description, shape, starts in shapes(tests):
        _check_cancel(cancel)
        if len(free_parameters(shape, _inputs(tests))) > MAX_PARAMETERS:
            continue
        try:
            fitted = fit(shape, tests, tolerance, starts=starts, cancel=cancel)
        except (ValueError, ArithmeticError):
            continue
        measured = grade(fitted['formula'], tests, tolerance)
        count = len(fitted['parameters'])
        tried.append({'description': description, 'shape': shape, 'formula': fitted['formula'],
                      'parameters': fitted['parameters'], 'constants': count, 'measurement': measured,
                      'too_flexible': count >= len({tuple(sorted((name, Decimal(str(value)))
                                                               for name, value in case['inputs'].items()))
                                                   for case in tests})})
    if not tried:
        raise ValueError('No standard shape could be fitted to these cases.')
    tried.sort(key=lambda row: (row['too_flexible'], -row['measurement']['passed'], row['measurement']['did_not_run'],
                                row['measurement']['mean_relative_error'] or 0, row['constants']))
    # Leave-one-out decides among the closest few: a fit that also predicts unseen cases is preferred.
    head = tried[:8]
    for row in head:
        row['leave_one_out'] = leave_one_out(row['shape'], tests, tolerance,
                                             start=[row['parameters'][name] for name in sorted(row['parameters'])], cancel=cancel)
    head.sort(key=lambda row: (row['too_flexible'], -row['measurement']['passed'], row['measurement']['did_not_run'],
                               row['leave_one_out'] is None,
                               (row['leave_one_out'] or {}).get('failed_predictions', 0),
                               (row['leave_one_out'] or {}).get('mean_relative_error', row['measurement']['mean_relative_error'] or 0),
                               row['constants']))
    ranked = head + tried[8:]
    return {'best': ranked[0], 'alternatives': ranked[1:6], 'shapes_tried': len(tried),
            'basis': 'Standard shapes fitted to the frozen cases by least squares, no model. A shape with as many '
                     'constants as distinct input points can fit arbitrary observations, so it ranks last. '
                     'Leave-one-input-group-out is internal cross-validation after shape selection; it does not '
                     'replace independent new measurements.'}


# ------------------------------------------------------------------ simulation

def _trend(values: list[float]) -> str:
    steps = [b - a for a, b in zip(values, values[1:])]
    scale = max((abs(v) for v in values), default=0) or 1
    signs = [0 if abs(step) <= 1e-12 * scale else (1 if step > 0 else -1) for step in steps]
    signs = [s for s in signs if s]
    if not signs:
        return 'flat'
    changes = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    if changes == 0:
        return 'rises' if signs[0] > 0 else 'falls'
    if changes == 1:
        return 'rises then falls' if signs[0] > 0 else 'falls then rises'
    return 'goes up and down'


def simulate(formula: str, tests: list[dict[str, Any]], tolerance: Any = 0, *, spread: float = 0.1,
             samples: int = 400, seed: int = 7, cancel=None) -> dict[str, Any]:
    """How the formula behaves across its inputs: sweeps, sensitivity and a seeded Monte Carlo of every case."""
    import numpy as np

    _check_cancel(cancel)
    if not tests:
        raise ValueError('Simulation needs the cases, to know each input\'s range.')
    if isinstance(spread, bool) or not isinstance(spread, (int, float)) or not 0 < spread <= 0.5:
        raise ValueError('Spread is a fraction from 0 to 0.5 (0.1 means each input off by up to 10 %).')
    if type(samples) is not int or not 20 <= samples <= 5000:
        raise ValueError('Simulation samples must be an integer from 20 to 5000.')
    if type(seed) is not int or not 0 <= seed < 2 ** 32:
        raise ValueError('Simulation seed must be an integer from 0 to 4294967295.')
    if len(tests) > 32:
        raise ValueError('Simulation supports at most 32 numeric cases.')
    inputs = _inputs(tests)
    unknown = free_parameters(formula, inputs)
    if unknown:
        raise ValueError(f'Fit the constants first ({", ".join(unknown)}): a simulation needs every number known.')
    run = compile_formula(formula)
    rows = _rows(tests)
    columns = {name: [row[0][name] for row in rows if name in row[0]] for name in inputs}
    base = {name: statistics.median(values) for name, values in columns.items()}

    def value_at(point):
        _check_cancel(cancel)
        try:
            return run(point)
        except (ArithmeticError, ValueError, TypeError, OverflowError):
            return None

    sweeps, warnings = [], []
    for name in inputs:
        low, high = min(columns[name]), max(columns[name])
        if low == high:
            low, high = (low * .5, low * 1.5) if low else (-1.0, 1.0)
            if low > high:
                low, high = high, low
        margin = (high - low) * .25
        xs = list(np.linspace(low - margin, high + margin, 41))
        points = [[float(x), value_at({**base, name: float(x)})] for x in xs]
        broken = [x for x, y in points if y is None]
        inside = [x for x in broken if low <= x <= high]
        if inside:
            span = f'near {inside[0]:.4g}' if len(inside) == 1 else f'from about {inside[0]:.4g} to {inside[-1]:.4g}'
            warnings.append(f'The formula has no value for {name} {span}, inside the range the cases cover.')
        good = [y for _, y in points if y is not None]
        # A pole (1/(x-3)) rarely lands on a sample: it shows as a sign flip with an outsized jump.
        typical = statistics.median([abs(y) for y in good]) if good else 0
        poles = [round((a[0] + b[0]) / 2, 6) for a, b in zip(points, points[1:])
                 if a[1] is not None and b[1] is not None and (a[1] > 0) != (b[1] > 0)
                 and min(abs(a[1]), abs(b[1])) > 10 * max(typical, 1e-12)]
        for pole in poles:
            if low <= pole <= high:
                warnings.append(f'The formula shoots off to infinity near {name} ≈ {pole:.4g}, inside the range the cases cover.')
        central = [y for x, y in points if low <= x <= high and y is not None]
        trend = ('has domain gaps' if inside else 'has a pole' if any(low <= pole <= high for pole in poles)
                 else _trend(central) if len(central) > 2 else 'too few points')
        sweeps.append({'input': name, 'held': {k: v for k, v in base.items() if k != name}, 'range': [low, high],
                       'points': points, 'breaks_at': [round(x, 6) for x in broken[:10]], 'poles': poles[:10],
                       'trend': trend, 'extended_trend': _trend(good) if len(good) > 2 else 'too few points',
                       'trend_basis': 'Sampled points inside the stated range; extended_trend includes the plotted margins. '
                                      'A sampled trend is not a proof of monotonicity between points.'})
    centre = value_at(base)
    sensitivity = []
    for name in inputs:
        if centre is None:
            break
        moved = value_at({**base, name: base[name] * 1.1 if base[name] else 0.1})
        if moved is None:
            sensitivity.append({'input': name, 'effect_of_10_percent': None})
            continue
        effect = (moved - centre) / max(abs(centre), 1e-12)
        sensitivity.append({'input': name, 'effect_of_10_percent': round(effect, 6)})
    sensitivity.sort(key=lambda row: -abs(row['effect_of_10_percent'] or 0))
    band = float(Decimal(str(tolerance or 0))) or 0.01
    rng = np.random.default_rng(seed)
    total_samples = max(samples, len(rows))
    per_case, remainder = divmod(total_samples, len(rows))
    zero_scales = {name: max((abs(value) for value in values), default=0) or 1.0
                   for name, values in columns.items()}
    robustness, within_all, completed_all = [], 0, 0
    for index, ((point, expected), case) in enumerate(zip(rows, tests)):
        attempted = per_case + int(index < remainder)
        outputs = []
        for _ in range(attempted):
            shaken = {k: v + (abs(v) if v else zero_scales[k]) * rng.uniform(-spread, spread)
                      for k, v in point.items()}
            value = value_at(shaken)
            if value is not None:
                outputs.append(value)
        if not outputs:
            robustness.append({'inputs': case['inputs'], 'expected': str(case['expected']),
                               'attempted': attempted, 'ran': 0, 'failed': attempted, 'share_within_band': 0.0})
            continue
        within = sum(1 for v in outputs if abs(v - expected) <= band * max(1.0, abs(expected)))
        within_all += within
        completed_all += len(outputs)
        low, middle, high = np.percentile(outputs, [5, 50, 95])
        robustness.append({'inputs': case['inputs'], 'expected': str(case['expected']),
                           'attempted': attempted, 'ran': len(outputs), 'failed': attempted - len(outputs),
                           'p5': float(low), 'p50': float(middle), 'p95': float(high),
                           'share_within_band': round(within / attempted, 4)})
    failed = total_samples - completed_all
    if failed:
        warnings.append(f'{failed} of {total_samples} perturbed evaluations failed; failed evaluations count '
                        'against the robustness success rate.')
    return {'formula': formula, 'at': {k: round(v, 9) for k, v in base.items()}, 'value_at_centre': centre,
            'sweeps': sweeps, 'sensitivity': sensitivity, 'warnings': warnings,
            'monte_carlo': {'spread': spread, 'band': band, 'band_from': 'tolerance' if tolerance and float(tolerance) else 'default 1 %',
                            'samples': total_samples, 'requested_samples': samples, 'completed_samples': completed_all,
                            'failed_samples': failed, 'evaluation_coverage': round(completed_all / total_samples, 4),
                            'seed': seed, 'cases': robustness,
                            'zero_input_scales': zero_scales,
                            'perturbation_basis': 'Uniform independent input perturbations. Nonzero inputs use their '
                                                  'absolute magnitude; zero inputs use the largest observed absolute '
                                                  'value for that input, or one unit when all supplied values are zero.',
                            'share_within_band': round(within_all / total_samples, 4)},
            'basis': 'A simulation of the formula itself: how its output responds to its inputs. It does not show that '
                     'a physical device follows the formula; that still needs real measurements.'}


def describe_simulation(result: dict[str, Any]) -> list[str]:
    """Plain sentences for a simulation, for replies and reports."""
    lines = []
    for sweep in result.get('sweeps', []):
        held = ', '.join(f'{k}={v:.4g}' for k, v in sweep['held'].items())
        low, high = sweep['range']
        outside = [x for x in sweep['breaks_at'] if not low <= x <= high]
        lines.append(f"As {sweep['input']} goes from {low:.4g} to {high:.4g}"
                     + (f' (with {held})' if held else '') + f", the result {sweep['trend']}"
                     + (f"; beyond the cases' range it has no value at {sweep['input']} ≈ {outside[0]:.4g}" if outside else '') + '.')
    effects = [row for row in result.get('sensitivity', []) if row['effect_of_10_percent'] is not None]
    if effects:
        lines.append('A 10 % rise in each input moves the result by: '
                     + ', '.join(f"{row['input']} {row['effect_of_10_percent'] * 100:+.1f} %" for row in effects) + '.')
    monte = result.get('monte_carlo') or {}
    if monte.get('share_within_band') is not None:
        lines.append(f"With every input off by up to ±{monte['spread'] * 100:g} %, {monte['share_within_band'] * 100:.1f} % of "
                     f"{monte['samples']} simulated cases stay within {monte['band'] * 100:g} % of the expected value "
                     f"(band from the {monte['band_from']}).")
    lines += result.get('warnings', [])
    return lines
