"""Small questions with one exact answer that language models famously get wrong.

"Which is bigger, 9.11 or 9.9?", "is 1000003 prime?", "spell necessary
backwards", "what's the last letter of keyboard?", "if today is Saturday,
what day is it in 10 days?". A model guesses these from patterns in text; a
few lines of code know them. Only a whole question of one of these shapes is
answered; anything else returns None and goes to the brain as usual.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation, localcontext

_NUMBER = r"[-+]?\d[\d,]*(?:\.\d+)?"
_COMPARE = re.compile(
    rf"(?i)^\s*(?:which\s+(?:number\s+)?is\s+(?P<way>bigger|larger|greater|higher|smaller|lower|less)|"
    rf"is\s+(?P<a2>{_NUMBER})\s+(?P<way2>bigger|larger|greater|higher|smaller|lower|less)\s+than\s+(?P<b2>{_NUMBER}))"
    rf"(?:\s*[:,]?\s*(?P<a>{_NUMBER})\s+or\s+(?P<b>{_NUMBER}))?[?.!]*\s*$")
_PRIME = re.compile(r"(?i)^\s*is\s+(?P<n>\d[\d,]{0,15})\s+(?:a\s+)?prime(?:\s+number)?[?.!]*\s*$")
_MASS_UNITS = {"kg": Decimal(1000), "kilogram": Decimal(1000), "kilograms": Decimal(1000),
               "g": Decimal(1), "gram": Decimal(1), "grams": Decimal(1),
               "mg": Decimal(".001"), "milligram": Decimal(".001"), "milligrams": Decimal(".001")}
_MASS_ITEM = rf"(?P<n{{i}}>a|an|one|{_NUMBER})\s*(?P<u{{i}}>kilograms?|kg|milligrams?|mg|grams?|g)\s+(?:of\s+)?(?P<label{{i}}>[a-z][a-z -]{{0,30}}?)"
_MASS_COMPARE = re.compile(r"(?i)^which is (?P<way>heavier|lighter)\s*[:,]?\s*" +
                           _MASS_ITEM.replace("{i}", "1") + r"\s+or\s+" + _MASS_ITEM.replace("{i}", "2") + r"[?!.]*$")
_BACKWARDS = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:spell|write|reverse)\s+(?:the\s+word\s+)?[\"'“‘]?(?P<word>[a-z]{2,40})[\"'”’]?"
    r"(?:\s+(?:backwards|backward|in reverse|reversed))?[?.!]*\s*$")
_POSITION = re.compile(
    r"(?i)^\s*what(?:'s|’s|\s+is)\s+the\s+(?P<which>first|last|second|third)\s+letter\s+(?:of|in)\s+(?:the\s+word\s+)?"
    r"[\"'“‘]?(?P<word>[a-z]{1,40})[\"'”’]?[?.!]*\s*$")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_RELATIVE_DAY = re.compile(
    r"(?i)^\s*if\s+today\s+is\s+(?:a\s+)?(?P<day>" + "|".join(_WEEKDAYS) + r")[,\s]+what\s+day\s+(?:of\s+the\s+week\s+)?"
    r"(?:will\s+it\s+be|is\s+it|was\s+it)\s+(?:in\s+)?(?P<n>\d{1,4})\s+(?P<unit>days?|weeks?)(?P<ago>\s+ago)?"
    r"(?:\s+from\s+(?:now|today))?[?.!]*\s*$")


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _is_prime(number: int) -> bool:
    if number < 2:
        return False
    if number % 2 == 0:
        return number == 2
    return all(number % factor for factor in range(3, math.isqrt(number) + 1, 2))


def _smallest_factor(number: int) -> int:
    if number % 2 == 0:
        return 2
    return next((factor for factor in range(3, math.isqrt(number) + 1, 2) if number % factor == 0), number)


def exact_answer(text: str) -> str | None:
    value = " ".join(str(text or "").split())
    if not value or len(value) > 160:
        return None
    mass = _MASS_COMPARE.fullmatch(value)
    if mass and not re.search(r"(?i)\b(?:on|in|at|earth|moon|vacuum|air|apparent|buoyancy)\b", value):
        first = _decimal("1" if mass["n1"].lower() in {"a", "an", "one"} else mass["n1"])
        second = _decimal("1" if mass["n2"].lower() in {"a", "an", "one"} else mass["n2"])
        if first is not None and second is not None and min(first, second) >= 0:
            with localcontext() as context:
                context.prec = 180  # More than the entire bounded question.
                a, b = first * _MASS_UNITS[mass["u1"].lower()], second * _MASS_UNITS[mass["u2"].lower()]
            if a == b:
                return f"Neither is {mass['way'].lower()}: both have the same mass ({a:f} grams)."
            picked = "1" if (a > b) == (mass["way"].lower() == "heavier") else "2"
            return (f"The {mass['label' + picked].strip()} is {mass['way'].lower()}: "
                    f"the stated masses are {a:f} grams and {b:f} grams, respectively.")
    match = _COMPARE.match(value)
    if match:
        a, b = match.group("a") or match.group("a2"), match.group("b") or match.group("b2")
        way = (match.group("way") or match.group("way2") or "").lower()
        first, second = (_decimal(a) if a else None), (_decimal(b) if b else None)
        if first is not None and second is not None:
            if first == second:
                return f"{a} and {b} are equal."
            bigger, smaller = (a, b) if first > second else (b, a)
            if match.group("a2"):  # "is A bigger than B?"
                asked_bigger = way in {"bigger", "larger", "greater", "higher"}
                yes = (first > second) == asked_bigger
                return f"{'Yes' if yes else 'No'}: {bigger} is bigger than {smaller}."
            answer = bigger if way in {"bigger", "larger", "greater", "higher"} else smaller
            note = (" (compare digit by digit after the point: 0.90 is more than 0.11)"
                    if "." in a + b and answer == bigger and len(bigger) < len(smaller) else "")
            return f"{answer} is {way}{note}."
    match = _PRIME.match(value)
    if match:
        number = int(match.group("n").replace(",", ""))
        if number <= 10**12:
            if _is_prime(number):
                return f"Yes, {number:,} is a prime number."
            if number < 2:
                return f"No, {number} is not a prime number."
            factor = _smallest_factor(number)
            return f"No, {number:,} is not prime: it is {factor:,} × {number // factor:,}."
    match = _BACKWARDS.match(value)
    if match and re.search(r"(?i)backward|reverse", value):
        word = match.group("word")
        return f"“{word}” backwards is “{word[::-1]}”."
    match = _POSITION.match(value)
    if match:
        word, which = match.group("word"), match.group("which").lower()
        index = {"first": 0, "second": 1, "third": 2, "last": -1}[which]
        if abs(index) < len(word) or index == -1:
            return f"The {which} letter of “{word}” is “{word[index]}”."
    match = _RELATIVE_DAY.match(value)
    if match:
        start = _WEEKDAYS.index(match.group("day").lower())
        days = int(match.group("n")) * (7 if match.group("unit").lower().startswith("week") else 1)
        days = -days if match.group("ago") else days
        result = _WEEKDAYS[(start + days) % 7].capitalize()
        if days < 0:
            return f"{abs(days)} days earlier it was a {result}."
        return f"{days} days later it will be a {result}."
    return None
