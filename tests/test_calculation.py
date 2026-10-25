import pytest

from app.calculation import calculation_answer


@pytest.mark.parametrize('question,result', [
    ('What is 17 multiplied by 23? Give the short calculation.', '391'),
    ('Calculate 0.1 plus 0.2', '0.3'),
    ('What is (5 + 3) * 2?', '16'),
    ('Compute -3 times 4', '-12'),
    ('Evaluate 2 to the power of 10', '1024'),
    ('Calculate 7 / 2', '3.5'),
])
def test_whole_expressions_use_bounded_decimal_arithmetic(question, result):
    assert f'= {result}.' in calculation_answer(question)


@pytest.mark.parametrize('question', [
    '__import__("os").system("dir")', 'Calculate 2 ** 1000000', 'Calculate 1 / 0',
    'What is 2 plus 2? And what is the capital of France?',
    'What is 2 apples plus 3 oranges?', 'What is the current price of 2 laptops?',
    'Calculate sin(2)', 'What is my account balance?',
])
def test_unsupported_or_multi_part_questions_do_not_guess(question):
    assert calculation_answer(question) is None


def price_question(cost=80, markup=50, discount=20):
    return (f'A shop buys a jacket for ${cost}, marks it up {markup}%, then sells it at {discount}% off the marked price. '
            'What is the profit in dollars? Show the short working.')


@pytest.mark.parametrize('cost,markup,discount,result', [(80, 50, 20, '16'), (200, 25, 10, '25'), (10, 0, 20, '-2')])
def test_price_chain_is_computed_and_leading_answer_matches_working(cost, markup, discount, result):
    answer = calculation_answer(price_question(cost, markup, discount))
    assert answer.startswith(f'Profit = ${result}.')
    assert f'= ${result}.' in answer


def test_price_chain_declines_extra_cost_and_invalid_percentage():
    assert calculation_answer(price_question().replace('What is', 'Shipping costs $5. What is')) is None
    assert calculation_answer(price_question(discount=120)) is None
    assert calculation_answer(price_question().replace('marks it up', 'does not mark it up')) is None
    assert calculation_answer(price_question().replace('What is', 'Tax also applies. What is')) is None


def test_supported_calculations_never_call_the_routing_model():
    from unittest.mock import Mock
    from app.intent.models import IntentKind
    from app.intent.router import IntentRouter
    model = Mock()
    model.generate.side_effect = AssertionError('No model needed for known local calculation')
    routed = IntentRouter(model).route(price_question())
    assert routed.intent is IntentKind.QUESTION
    assert routed.classification_source == 'local_calculation'
    model.generate.assert_not_called()


def test_assistant_routes_arithmetic_without_model_or_research(tmp_path):
    from tests.test_assistant_service import runtime
    rt = runtime(tmp_path)
    rt.modes.set('GOD')
    answer = rt.assistant.handle(price_question())
    assert answer.display_response.startswith('Profit = $16.')
    assert 'Conversational answer' not in answer.display_response
    assert rt.manager.calls == []


@pytest.mark.parametrize('bound,divisors', [(17, [2, 7]), (100, [3, 5]), (200, [3, 5]),
                                         (71, [4, 6, 9]), (36, [3, 3, 6]), (1, [2])])
def test_sums_match_independent_enumeration_and_do_not_count_overlaps_twice(bound, divisors):
    import json
    question = (f'Sum the positive integers less than {bound} divisible by '
                + ' or '.join(map(str, divisors)) + '. Return {"sum":integer}.')
    expected = sum(number for number in range(1, bound) if any(number % d == 0 for d in divisors))
    assert json.loads(calculation_answer(question)) == {'sum': expected}


@pytest.mark.parametrize('question,expected', [
    ('Sum positive integers up to 30 divisible by 3 and 5. Return {"sum":integer}.', 45),
    ('Sum positive integers at most 10. Return {"sum":integer}.', 55),
    ('Calculate the sum of positive integers below 10. Return {"sum":integer}.', 45),
    ('Sum positive integers below 0. Return {"sum":integer}.', 0),
    ('Sum positive integers up to 1000000000000. Return {"sum":integer}.', 500000000000500000000000),
])
def test_sum_bounds_intersections_and_large_inputs(question, expected):
    import json
    assert json.loads(calculation_answer(question)) == {'sum': expected}


@pytest.mark.parametrize('question,expected', [
    ('Sort and deduplicate [9,-4,9,0,13,-4,2]. Return {"values":[integers]}.', [-4, 0, 2, 9, 13]),
    ('Sort [3,1,3] descending. Return {"values":[integers]}.', [3, 3, 1]),
    ('Deduplicate and sort [-1.25,2,2.0,+0] ascending. Return {"values":[numbers]}.', [-1.25, 0, 2]),
    ('Sort []. Return {"values":[integers]}.', []),
])
def test_numeric_lists_sort_numerically_and_only_deduplicate_when_requested(question, expected):
    import json
    assert json.loads(calculation_answer(question)) == {'values': expected}


@pytest.mark.parametrize('question,expected', [
    ('Calculate SDE * multiple - debt + cash with SDE=100000, multiple=3, debt=20000, cash=5000.', '285000'),
    ('Compute (length + width) * 2 with length=7.25, width=3.', '20.5'),
    ('Evaluate principal * (1 + rate) ** years where principal=200, rate=0.1, years=2.', '242'),
    ('Calculate net / count with net=-6, count=4. Return {"result":number}.', '-1.5'),
])
def test_formulas_use_only_supplied_bindings_with_decimal_precision(question, expected):
    answer = calculation_answer(question)
    assert answer is not None and expected in answer
    if 'Return' in question:
        assert answer == '{"result":' + expected + '}'


@pytest.mark.parametrize('question', [
    'Sum positive integers below 20 divisible by 0.',
    'Sum positive integers below 1000000000001.',
    'Sum positive integers below 20 divisible by 2 or 3 and 5.',
    'Sum positive integers below 20 divisible by 3, excluding 9.',
    'Sum positive integers below 20. Then email it.',
    'Sort [1,2,] descending.', 'Sort [True, 1].', 'Sort [1, nan].',
    'Sort [1,3] alphabetically.', 'Sort [1,3]. Remove 1.',
    'Sort [1.2,3]. Return {"values":[integers]}.',
    'Sum positive integers below 5. Return {"values":[integers]}.',
    'Calculate amount * rate with amount=20.',
    'Calculate amount * rate with amount=20, rate=2, tax=5.',
    'Calculate amount * rate with amount=20, rate=2, rate=3.',
    'Calculate x ** 100 with x=2.', 'Calculate x / y with x=2, y=0.',
    'Calculate abs(x) with x=-2.', 'Calculate x.__class__ with x=1.',
    'Calculate x + 1 with x=2. Send it to me.',
    'Calculate x / 2 with x=3. Return {"result":integer}.',
])
def test_numeric_requests_decline_unsupported_conditions_and_unsafe_or_incomplete_formulas(question):
    assert calculation_answer(question) is None


@pytest.mark.parametrize('question,expected', [
    ('Sum the positive integers less than 200 divisible by 3 or 5. Return {"sum":integer}.', '{"sum":9168}'),
    ('Sort and deduplicate [9,-4,9,0,13,-4,2]. Return {"values":[integers]}.', '{"values":[-4, 0, 2, 9, 13]}'),
    ('Calculate SDE * multiple - debt + cash with SDE=100000, multiple=3, debt=20000, cash=5000. Return {"result":integer}.', '{"result":285000}'),
])
def test_assistant_completes_numeric_operations_without_a_model(tmp_path, question, expected):
    from unittest.mock import Mock
    from tests.test_assistant_service import runtime
    rt = runtime(tmp_path)
    rt.modes.set('GOD')
    model = Mock()
    model.generate.side_effect = AssertionError('A supported calculation must not call a model')
    rt.intent_router.model = model
    rt.conversation.reply = Mock(side_effect=AssertionError('A supported calculation must not call conversation'))
    answer = rt.assistant.handle(question)
    assert answer.display_response == expected
    assert answer.intent.classification_source == 'local_calculation'
    assert rt.manager.calls == []
    model.generate.assert_not_called()


@pytest.mark.parametrize('expression,expected', [
    ('(900000000000000000000000000000 + 0.00000000001) - 900000000000000000000000000000', '0.00000000001'),
    ('(1 / 3) * 3', '1'),
    ('(1 / 7) * 7 - 1', '0'),
    ('1 / 3', '1/3'),
    ('-2 / 6', '-1/3'),
    ('7 / 40', '0.175'),
    ('2 ** -10', '0.0009765625'),
])
def test_exact_arithmetic_does_not_silently_round_intermediate_values(expression, expected):
    assert f'= {expected}.\n' in calculation_answer('Calculate ' + expression)


def test_formulas_and_json_require_exact_representable_results():
    assert calculation_answer('Calculate (x / y) * y with x=1, y=3. Return {"result":integer}.') == '{"result":1}'
    assert calculation_answer('Calculate x / y with x=1, y=3. Return {"result":number}.') is None
    assert calculation_answer('Calculate x / y with x=1, y=8. Return {"result":number}.') == '{"result":0.125}'
    assert calculation_answer('Evaluate x + 1e999999999 with x=1.') is None
    assert calculation_answer('Calculate 0 ** -1') is None
    assert calculation_answer('Calculate 0 ** 0') is None
    assert calculation_answer('Calculate 1000000000000000000000000000000 + 0.00000000001') is None


def test_assistant_preserves_exactness_through_the_real_numeric_route(tmp_path):
    from tests.test_assistant_service import runtime
    rt = runtime(tmp_path)
    reply = rt.assistant.handle('Calculate (1 / 7) * 7 - 1')
    assert '= 0.\n' in reply.display_response
    assert reply.intent.classification_source == 'local_calculation'
    assert rt.manager.calls == []
