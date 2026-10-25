"""Questions with one exact answer that models often get wrong, answered by code."""
from __future__ import annotations

import pytest

from app.calculation import calculation_answer
from app.exact_answers import exact_answer


@pytest.mark.parametrize("question, expected", [
    ("Which number is bigger: 9.11 or 9.9?", "9.9 is bigger"),
    ("Is 9.11 bigger than 9.9?", "No: 9.9 is bigger than 9.11"),
    ("which is smaller, 3 or 7?", "3 is smaller"),
    ("Is 1000003 a prime number?", "Yes, 1,000,003 is a prime number"),
    ("is 91 prime", "No, 91 is not prime: it is 7 × 13"),
    ("Spell the word 'necessary' backwards.", "“yrassecen”"),
    ("What is the last letter of the word 'keyboard'?", "is “d”"),
    ("If today is a Saturday, what day of the week will it be in 10 days?", "will be a Tuesday"),
    ("if today is monday what day was it 3 days ago", "was a Friday"),
])
def test_exact_questions_get_exact_answers(question, expected):
    assert expected in exact_answer(question)


@pytest.mark.parametrize("question", ["Which is bigger, the sun or the moon?", "spell necessary", "is it prime time?"])
def test_other_questions_go_to_the_brain(question):
    assert exact_answer(question) is None


def test_squares_and_cubes_are_calculated():
    assert calculation_answer("What is 17 squared minus 4 squared?").startswith("17² - 4² = 273.")
    assert calculation_answer("what is 3 cubed").startswith("3³ = 27.")


def test_exact_questions_are_not_web_searches():
    from app.intent.live_web import needs_live_web

    assert needs_live_web("If today is a Saturday, what day of the week will it be in 10 days?") is None


@pytest.mark.parametrize("question,answer", [
    ("Which is heavier: a kilogram of feathers or a kilogram of steel?", "Neither is heavier"),
    ("Which is heavier: 1 kg of iron or 1000 g of cotton?", "same mass"),
    ("Which is lighter: 2 kg of gold or 500 grams of silver?", "silver is lighter"),
    ("Which is heavier: 5 mg of salt or 1 g of sugar?", "sugar is heavier"),
])
def test_stated_mass_comparisons_use_quantities_and_convert_units(question, answer):
    assert answer in exact_answer(question)


def test_different_gravity_or_buoyancy_context_is_not_silently_discarded():
    assert exact_answer("Which is heavier: a kilogram of feathers on Earth or a kilogram of steel on the Moon?") is None


def test_mass_comparison_through_hayat_needs_no_model():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        answer = runtime.assistant.handle("Which is heavier: a kilogram of feathers or a kilogram of steel?").display_response
        assert answer.startswith("Neither is heavier") and "same mass" in answer
    finally:
        runtime.close()
