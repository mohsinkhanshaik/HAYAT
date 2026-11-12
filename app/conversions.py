"""Unit conversions worked out exactly, from the units' legal definitions.

"Convert 134 degrees Celsius to degrees Fahrenheit" went to the language model, which
answered 276.2 with "Confidence: 100%" (the right value is 273.2). Conversions are
arithmetic on defined factors, so they are done here: the inch is exactly 2.54 cm,
the pound 0.45359237 kg, the US gallon 3.785411784 L (NIST SP 811). A request with
any other wording, an unknown unit or a mix of kinds (kilograms to miles) declines.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

#: unit word -> (kind, factor to the base unit). Base units: metre, kilogram, litre, metre per second.
_UNITS: dict[str, tuple[str, Decimal]] = {}


def _add(kind: str, factor: str, *names: str) -> None:
    for name in names:
        _UNITS[name] = (kind, Decimal(factor))


_add("length", "0.001", "mm", "millimetre", "millimetres", "millimeter", "millimeters")
_add("length", "0.01", "cm", "centimetre", "centimetres", "centimeter", "centimeters")
_add("length", "1", "m", "metre", "metres", "meter", "meters")
_add("length", "1000", "km", "kilometre", "kilometres", "kilometer", "kilometers")
_add("length", "0.0254", "in", "inch", "inches")
_add("length", "0.3048", "ft", "foot", "feet")
_add("length", "0.9144", "yd", "yard", "yards")
_add("length", "1609.344", "mi", "mile", "miles")
_add("length", "1852", "nmi", "nautical mile", "nautical miles")
_add("mass", "0.000001", "mg", "milligram", "milligrams")
_add("mass", "0.001", "g", "gram", "grams")
_add("mass", "1", "kg", "kilogram", "kilograms", "kilo", "kilos")
_add("mass", "1000", "t", "tonne", "tonnes", "metric ton", "metric tons")
_add("mass", "0.028349523125", "oz", "ounce", "ounces")
_add("mass", "0.45359237", "lb", "lbs", "pound", "pounds")
_add("mass", "6.35029318", "st", "stone", "stones")
_add("volume", "0.001", "ml", "millilitre", "millilitres", "milliliter", "milliliters")
_add("volume", "1", "l", "litre", "litres", "liter", "liters")
_add("volume", "3.785411784", "gal", "gallon", "gallons", "us gallon", "us gallons")
_add("volume", "4.54609", "uk gallon", "uk gallons", "imperial gallon", "imperial gallons")
_add("volume", "0.946352946", "quart", "quarts", "us quart", "us quarts")
_add("volume", "0.473176473", "pint", "pints", "us pint", "us pints")
_add("volume", "0.2365882365", "cup", "cups", "us cup", "us cups")
_add("volume", "0.0295735295625", "fl oz", "fluid ounce", "fluid ounces", "us fluid ounces")
_add("speed", "1", "m/s", "metres per second", "meters per second")
_add("speed", "0.27777777777777777777777777778", "km/h", "kph", "kmh", "kilometres per hour", "kilometers per hour")
_add("speed", "0.44704", "mph", "miles per hour")
_add("speed", "0.51444444444444444444444444444", "knot", "knots", "kn")
_TEMPERATURE = {"c": "C", "°c": "C", "celsius": "C", "degrees celsius": "C", "degree celsius": "C", "centigrade": "C",
                "f": "F", "°f": "F", "fahrenheit": "F", "degrees fahrenheit": "F", "degree fahrenheit": "F",
                "k": "K", "kelvin": "K", "kelvins": "K"}
_SYMBOL = {"C": "°C", "F": "°F", "K": "K"}

_UNIT = r"([a-z°/ ]{1,22}?)"
_REQUEST = re.compile(
    r"(?i)^(?:please\s+)?(?:convert|change|what\s+is|what's|whats|how\s+much\s+is|how\s+many\s+\S+\s+(?:is|are|in))?\s*"
    r"(-?\d+(?:\.\d+)?)\s*" + _UNIT + r"\s+(?:to|in|into)\s+" + _UNIT +
    r"(?:\s*,?\s*(?:rounded\s+)?to\s+(\d+|one|two|three|four)\s+decimal\s+places?)?\s*[?.!]?$")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}


def _unit(name: str) -> str:
    name = " ".join(name.lower().replace("degrees ", "degrees ").split())
    return re.sub(r"^(?:us )?(?=gallons?$)", "", name)


def _temperature(value: Decimal, source: str, target: str) -> Decimal:
    kelvin = {"C": value + Decimal("273.15"), "F": (value - 32) * 5 / 9 + Decimal("273.15"), "K": value}[source]
    return {"C": kelvin - Decimal("273.15"), "F": (kelvin - Decimal("273.15")) * 9 / 5 + 32, "K": kelvin}[target]


def _format(value: Decimal, places: int | None) -> str:
    if places is not None:
        return str(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
    text = format(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def convert_answer(text: str) -> str | None:
    """'134 °C = 273.2 °F.' for a complete conversion request, else None."""
    found = _REQUEST.match(" ".join(str(text or "").split()))
    if not found:
        return None
    raw, source_name, target_name, places_word = found.groups()
    source_name, target_name = _unit(source_name), _unit(target_name)
    places = None if places_word is None else _WORDS.get(places_word.lower(), None)
    if places is None and places_word and places_word.isdigit():
        places = min(int(places_word), 10)
    value = Decimal(raw)
    if source_name in _TEMPERATURE and target_name in _TEMPERATURE:
        source, target = _TEMPERATURE[source_name], _TEMPERATURE[target_name]
        result = _temperature(value, source, target)
        return (f"{raw} {_SYMBOL[source]} = {_format(result, places)} {_SYMBOL[target]}.\n"
                "Converted exactly (°F = °C × 9/5 + 32; K = °C + 273.15).")
    if source_name not in _UNITS or target_name not in _UNITS:
        return None
    (kind, source_factor), (other, target_factor) = _UNITS[source_name], _UNITS[target_name]
    if kind != other:
        return None
    result = value * source_factor / target_factor
    return (f"{raw} {source_name} = {_format(result, places)} {target_name}.\n"
            "Converted exactly from the units' definitions (NIST SP 811).")
