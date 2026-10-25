"""Dates from the local calendar and letters counted exactly, never guessed by a model or searched."""
from __future__ import annotations

from datetime import date

import pytest

from app.dates import date_answer, letter_count_answer

TODAY = date(2026, 10, 10)  # a Saturday


@pytest.mark.parametrize("question, expected", [
    ("What date will it be 45 days from today?", "Tuesday, 24 November 2026"),
    ("what date is it in 2 weeks?", "Saturday, 24 October 2026"),
    ("what day was it 10 days ago?", "Wednesday, 30 September 2026"),
    ("What day of the week was 1 January 2000?", "1 January 2000 was a Saturday"),
    ("What day of the week will December 25, 2026 be?", "25 December 2026 will be a Friday"),
    ("how many days until 25 December?", "76 days until Friday, 25 December 2026"),
    ("How many days until January 1st?", "83 days until Friday, 1 January 2027"),
    # The held-out run of 2026-10-14 found the model one day off on these (counting both ends).
    ("How many days are there from 17 February 2024 to 27 December 2024?",
     "314 days from Saturday, 17 February 2024 to Friday, 27 December 2024 (315 if both of those days are counted)"),
    ("how many days between 1 March 2025 and 1 Jan 2025", "59 days from Wednesday, 1 January 2025"),
    ("How many days from today to christmas?", "76 days from Saturday, 10 October 2026"),
])
def test_date_questions_are_worked_out_from_the_calendar(question, expected):
    assert expected in date_answer(question, TODAY)


@pytest.mark.parametrize("question", ["what is the date today?", "what happened 45 days ago?", "tell me about May",
                                      "how many days between my birthday and yours?"])
def test_other_questions_are_left_alone(question):
    assert date_answer(question, TODAY) is None


@pytest.mark.parametrize("question, expected", [
    ("How many letters are in the word 'strawberry'?", "“strawberry” has 10 letters"),
    ("How many times does the letter r appear in 'strawberry'?", "appears 3 times in “strawberry”"),
    ("how many r's are in strawberry?", "appears 3 times"),
    ("How many vowels in banana?", "“banana” has 3 vowels"),
])
def test_letters_are_counted_exactly(question, expected):
    assert expected in letter_count_answer(question)


def test_date_and_personal_questions_are_not_web_searches():
    from app.intent.live_web import needs_live_web

    for text in ("What date will it be 45 days from today?", "Read my latest email.", "check my calendar for tomorrow"):
        assert needs_live_web(text) is None, text
