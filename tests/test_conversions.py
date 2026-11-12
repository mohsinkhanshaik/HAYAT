"""Unit conversions are worked out exactly from the units' definitions, never guessed by the model."""
from __future__ import annotations

import pytest

from app.answer_format import exact_local_answer
from app.conversions import convert_answer


@pytest.mark.parametrize("prompt, value", [
    ("Convert 134 degrees Celsius to degrees Fahrenheit, rounded to one decimal place.", "273.2 °F"),  # model said 276.2
    ("Convert 69 kilometres to miles, rounded to one decimal place.", "42.9 miles"),
    ("Convert 41 kilograms to pounds, rounded to one decimal place.", "90.4 pounds"),
    ("Convert 157 inches to centimetres, rounded to one decimal place.", "398.8 centimetres"),
    ("Convert 58 litres to US gallons, rounded to one decimal place.", "15.3 gallons"),
    ("What is 100 mph in km/h?", "160.9344 km/h"),
    ("Convert 0 kelvin to celsius", "-273.15 °C"),
])
def test_conversions_are_exact(prompt, value):
    answer = convert_answer(prompt)
    assert answer is not None and f"= {value}." in answer


def test_a_conversion_with_an_answer_form_instruction_gives_the_bare_value():
    prompt = ("Convert 134 degrees Celsius to degrees Fahrenheit, rounded to one decimal place. "
              "Reply with only the final answer.")
    assert exact_local_answer(prompt) == "273.2 °F"


@pytest.mark.parametrize("prompt", ["5 kg to miles", "Convert 3 bananas to apples", "convert my file to pdf",
                                    "Convert 10 km to miles and tell me a joke"])
def test_mismatched_unknown_or_extra_requests_decline(prompt):
    assert convert_answer(prompt) is None
