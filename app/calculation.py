"""Small, bounded local calculations; ambiguous questions stay with the model."""
from __future__ import annotations

import ast
import itertools
import math
import re
from decimal import Decimal, DecimalException
from fractions import Fraction


_NUMBER = r'\d+(?:\.\d+)?'
_TAIL = re.compile(r'(?i)[?.!]*\s*(?:(?:give|show) (?:the )?(?:short|brief) (?:calculation|working|answer)[.!]?)?$')


def _format(value: Decimal | Fraction) -> str:
    if isinstance(value, Fraction):
        denominator = value.denominator
        twos = fives = 0
        while denominator % 2 == 0:
            denominator //= 2
            twos += 1
        while denominator % 5 == 0:
            denominator //= 5
            fives += 1
        if denominator != 1:
            return f'{value.numerator}/{value.denominator}'
        places = max(twos, fives)
        digits = str(abs(value.numerator) * (10**places // value.denominator)).zfill(places + 1)
        if places:
            digits = (digits[:-places] + '.' + digits[-places:]).rstrip('0').rstrip('.')
        return ('-' if value < 0 else '') + digits
    return format(value, 'f').rstrip('0').rstrip('.') if '.' in format(value, 'f') else format(value, 'f')


def _evaluate(expression: str, bindings: dict[str, Decimal] | None = None) -> Fraction:
    tree = ast.parse(expression, mode='eval')
    if len(list(ast.walk(tree))) > 64:
        raise ValueError('Calculation is too large')

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            literal = Decimal(ast.get_source_segment(expression, node))
            if (not literal.is_finite() or literal.copy_abs() > Decimal('1e30')
                    or abs(literal.as_tuple().exponent) > 1200):
                raise ValueError('Literal exceeds calculation budget')
            value = Fraction(literal)
        elif isinstance(node, ast.Name) and bindings is not None and node.id in bindings:
            value = Fraction(bindings[node.id])
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            elif isinstance(node.op, ast.Div):
                value = left / right
            elif isinstance(node.op, ast.Pow) and right.denominator == 1 and abs(right) <= 12:
                if left == 0 and right == 0:
                    raise ValueError('Indeterminate power')
                value = left ** int(right)
            else:
                raise ValueError('Unsupported operator')
        else:
            raise ValueError('Unsupported expression')
        if (abs(value) > 10**30 or value.numerator.bit_length() > 4096
                or value.denominator.bit_length() > 4096):
            raise ValueError('Result is too large')
        return value

    return visit(tree.body)


def _numeric_answer(value: str) -> str | None:
    """Full-request grammars for bounded sums, number lists and supplied formulas.

    The grammar must consume every condition. Unrecognized qualifiers, extra
    requests or missing inputs return None rather than a plausible partial answer.
    """
    output = re.search(r'\s+Return\s+\{"(sum|values|result)":\s*(integer|number|\[integers\]|\[numbers\])\}\.?$',
                       value, re.IGNORECASE)
    request = value[:output.start()] if output else value
    request = request.rstrip('?.! ')
    result: Decimal | Fraction | list[Decimal]
    description = ''
    kind = 'result'
    multiples = re.fullmatch(
        r'(?:sum(?: of)?|calculate the sum of) (?:the )?positive integers '
        r'(less than|below|up to|at most) (\d+)'
        r'(?: (?:that are )?divisible by (\d+(?: (?:or|and) \d+){0,3}))?', request, re.IGNORECASE)
    ordered = re.fullmatch(
        r'(sort(?: and deduplicate)?|deduplicate and sort)\s*'
        r'\[\s*([+\-\d.,\s]*)\s*\](?: (ascending|descending))?', request, re.IGNORECASE)
    formula = re.fullmatch(r'(?:calculate|compute|evaluate) (.+?) (?:with|where) (.+)', request, re.IGNORECASE)
    if multiples:
        bound = int(multiples[2])
        if not 0 <= bound <= 10**12:
            return None
        last = bound - 1 if multiples[1].lower() in {'less than', 'below'} else bound
        last = max(0, last)
        conditions = multiples[3] or '1'
        if re.search(r'\bor\b', conditions, re.IGNORECASE) and re.search(r'\band\b', conditions, re.IGNORECASE):
            return None
        divisors = sorted(set(int(item) for item in re.split(r' (?:or|and) ', conditions, flags=re.IGNORECASE)))
        if any(not 1 <= divisor <= 10**12 for divisor in divisors):
            return None
        if re.search(r'\band\b', conditions, re.IGNORECASE):
            divisors = [math.lcm(*divisors)]
        total = 0
        # Inclusion-exclusion counts overlaps once without iterating to bound.
        for count in range(1, len(divisors) + 1):
            for subset in itertools.combinations(divisors, count):
                step = math.lcm(*subset)
                terms = last // step
                subtotal = step * terms * (terms + 1) // 2
                total += subtotal if count % 2 else -subtotal
        result = Decimal(total)
        kind = 'sum'
        description = 'Sum'
    elif ordered:
        parts = ordered[2].split(',') if ordered[2].strip() else []
        if len(parts) > 128 or any(not re.fullmatch(r'[+-]?' + _NUMBER, part.strip()) for part in parts):
            return None
        numbers = [Decimal(part.strip()) for part in parts]
        if any(number.copy_abs() > Decimal('1e30') for number in numbers):
            return None
        deduplicate = 'deduplicate' in ordered[1].lower()
        result = sorted(set(numbers) if deduplicate else numbers, reverse=(ordered[3] or '').lower() == 'descending')
        kind = 'values'
        description = 'Sorted unique values' if deduplicate else 'Sorted values'
    elif formula:
        bindings = {}
        for part in formula[2].split(','):
            binding = re.fullmatch(r'\s*([A-Za-z][A-Za-z_0-9]{0,31})\s*=\s*([+-]?' + _NUMBER + r')\s*', part)
            if binding is None or binding[1] in bindings:
                return None
            bindings[binding[1]] = Decimal(binding[2])
        if not 1 <= len(bindings) <= 12:
            return None
        try:
            used = {node.id for node in ast.walk(ast.parse(formula[1], mode='eval')) if isinstance(node, ast.Name)}
            if used != set(bindings):
                return None
            result = _evaluate(formula[1], bindings)
        except (ValueError, SyntaxError, DecimalException, OverflowError, RecursionError, ZeroDivisionError):
            return None
        description = formula[1] + ' (using ' + formula[2] + ')'
    else:
        return None
    numbers = result if isinstance(result, list) else [result]
    formatted = '[' + ', '.join(_format(number) for number in result) + ']' if isinstance(result, list) else _format(result)
    if output:
        key, schema = output[1], output[2].lower()
        if key.lower() != kind or schema.startswith('[') != isinstance(result, list):
            return None
        # JSON numbers cannot express a repeating fraction exactly. Decline
        # that shortcut rather than silently presenting a rounded value.
        if '/' in formatted:
            return None
        if 'integer' in schema and any(number != int(number) for number in numbers):
            return None
        return '{"' + key + '":' + formatted + '}'
    return f'{description} = {formatted}.\nCalculated locally from your numbers.'


def calculation_answer(text: str) -> str | None:
    """Answer a complete, supported numeric request or an unambiguous price chain.

    No eval, subprocess, code execution, lookup, or guessed external inputs.
    Unsupported operators, extra quantities, and multi-question requests decline.
    """
    value = ' '.join(str(text or '').split())
    if not value or len(value) > 500:
        return None
    numeric = _numeric_answer(value)
    if numeric is not None:
        return numeric
    expression = re.sub(r'(?i)^(?:what is|calculate|compute|evaluate)\s+', '', value)
    # "17 squared minus 4 squared" is 17**2 - 4**2.
    expression = re.sub(r'(?i)\s+squared\b', '**2', re.sub(r'(?i)\s+cubed\b', '**3', expression))
    for words, operator in [('to the power of', '**'), ('multiplied by', '*'),
                            ('divided by', '/'), ('times', '*'), ('plus', '+'), ('minus', '-')]:
        expression = re.sub(r'(?i)\b' + words + r'\b', operator, expression)
    expression = _TAIL.sub('', expression).strip().replace('×', '*').replace('÷', '/').replace('−', '-')
    if len(expression) <= 240 and re.fullmatch(r'[\d\s.+*/()\-]+', expression) and re.search(r'[+*/\-]', expression):
        try:
            result = _evaluate(expression)
        except (ValueError, SyntaxError, DecimalException, OverflowError, RecursionError, ZeroDivisionError):
            return None
        shown = re.sub(r'\*\*2(?!\d)', '²', re.sub(r'\*\*3(?!\d)', '³', expression))
        return f'{shown} = {_format(result)}.\nCalculated locally from your numbers.'

    # A single cost, markup percentage, and discount percentage, in that order.
    # Requiring all three and declining extra numbers avoids guessing about fees,
    # additional items, tax, margin versus markup, or repeated discounts.
    quantities = list(re.finditer(r'(?<![\w.])[$£€₹]?' + _NUMBER + r'\s*%?', value))
    money = list(re.finditer(r'([$£€₹])(' + _NUMBER + ')', value))
    markup = re.search(r'(?i)\bmark(?:s|ed)?\s+(?:it\s+)?up\s+(' + _NUMBER + r')\s*%', value)
    discount = re.search(r'(?i)(' + _NUMBER + r')\s*%\s+off\s+the\s+marked\s+price', value)
    profit_question = re.search(r'(?i)what is (?:the )?profit(?: in dollars)?\?', value)
    if len(quantities) != 3 or len(money) != 1 or not markup or not discount or not profit_question:
        return None
    if re.search(r'(?i)\b(?:not|never|without|unless|instead|margin|tax|fees?|shipping)\b', value):
        return None
    if not (money[0].end() < markup.start() < markup.end() < discount.start() < profit_question.start()):
        return None
    before_money = value[:money[0].start()]
    if not re.search(r'(?i)\b(?:buys?|bought|costs?)\b', before_money):
        return None
    if not _TAIL.fullmatch(value[profit_question.end():]):
        return None
    cost, rate, off = (Fraction(Decimal(raw)) for raw in (money[0][2], markup[1], discount[1]))
    if max(cost, rate, off) > 10**12 or off > 100:
        return None
    marked = cost * (1 + rate / 100)
    sale = marked * (1 - off / 100)
    profit = sale - cost
    symbol = money[0][1]
    return (f'Profit = {symbol}{_format(profit)}.\n'
            f'Marked price: {symbol}{_format(cost)} × (1 + {_format(rate)}/100) = {symbol}{_format(marked)}.\n'
            f'Sale price: {symbol}{_format(marked)} × (1 - {_format(off)}/100) = {symbol}{_format(sale)}.\n'
            f'Profit: {symbol}{_format(sale)} - {symbol}{_format(cost)} = {symbol}{_format(profit)}.\n'
            'Calculated locally from your numbers.')
