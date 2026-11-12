"""Exact geometry and Earth-distance answers from the figures in the request.

"Area of a circle with radius 3 cm" went to the language model, which can
slip a digit or forget the square on the unit. These are formulas, so they are
worked out here: every input is read from the request, the formula is shown,
and anything missing, mixed or extra declines so the normal path can ask.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal

_N = r"(\d+(?:\.\d+)?)"
_U = r"\s*(mm|cm|m|km|in|ft|yd|mi|units?)?"
_SIGNED = r"(-?\d+(?:\.\d+)?)"
#: Mean Earth radius (IUGG), the usual sphere for great-circle distances.
EARTH_RADIUS_KM = 6371.0088
POLYGONS = {"triangle": 3, "quadrilateral": 4, "square": 4, "rectangle": 4, "pentagon": 5, "hexagon": 6,
            "heptagon": 7, "octagon": 8, "nonagon": 9, "decagon": 10, "hendecagon": 11, "dodecagon": 12}


def _num(raw: str) -> float:
    value = float(Decimal(raw))
    if not math.isfinite(value) or abs(value) > 1e12:
        raise ValueError("Quantity exceeds calculation budget")
    return value


def _fmt(value: float, places: int = 6) -> str:
    text = f"{value:,.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _unit(*units: str | None) -> str | None:
    """One shared unit, "" when none is given, None when they differ."""
    named = {unit.rstrip("s") for unit in units if unit and not unit.startswith("unit")}
    if len(named) > 1:
        return None
    return named.pop() if named else ""


def _with(unit: str, power: int = 1) -> str:
    if not unit:
        return " square units" if power == 2 else " cubic units" if power == 3 else " units"
    return " " + unit + ("²" if power == 2 else "³" if power == 3 else "")


def _clean(text: str) -> str:
    value = " ".join(str(text or "").split()).rstrip("?.! ")
    value = re.sub(r"(?i)^(?:please\s+)?(?:calculate|compute|find|work\s+out|what(?:'s|’s|\s+is)|whats|"
                   r"tell\s+me)\s+", "", value)
    return re.sub(r"(?i)\b(?:the|an?)\s+", "", value)


def _circle(value: str) -> str | None:
    found = re.fullmatch(r"(?i)(area|circumference|perimeter|diameter|radius) of circle (?:with|of|whose) "
                         r"(radius|diameter)(?: is| of)? " + _N + _U, value)
    if not found:
        return None
    asked, given, raw, unit = found.groups()
    asked, given, unit = asked.lower(), given.lower(), _unit(unit) or ""
    size = _num(raw)
    if asked == given or size <= 0:
        return None
    r = size / 2 if given == "diameter" else size
    if asked == "area":
        return (f"Area = πr² = π × {_fmt(r)}² ≈ {_fmt(math.pi * r * r)}{_with(unit, 2)}.\n"
                f"Exactly {_fmt(r * r)}π{_with(unit, 2)}.")
    if asked in {"circumference", "perimeter"}:
        return f"Circumference = 2πr = 2 × π × {_fmt(r)} ≈ {_fmt(2 * math.pi * r)}{_with(unit)}."
    if asked == "diameter":
        return f"Diameter = 2r = 2 × {_fmt(r)} = {_fmt(2 * r)}{_with(unit)}."
    return f"Radius = d ÷ 2 = {_fmt(size)} ÷ 2 = {_fmt(r)}{_with(unit)}."


def _solids(value: str) -> str | None:
    found = re.fullmatch(r"(?i)(volume|surface area) of (sphere|ball) (?:with|of) (radius|diameter)(?: of)? "
                         + _N + _U, value)
    if found:
        asked, _, given, raw, unit = found.groups()
        r = _num(raw) / (2 if given.lower() == "diameter" else 1)
        unit = _unit(unit) or ""
        if r <= 0:
            return None
        if asked.lower() == "volume":
            return f"Volume = 4/3 πr³ = 4/3 × π × {_fmt(r)}³ ≈ {_fmt(4 / 3 * math.pi * r ** 3)}{_with(unit, 3)}."
        return f"Surface area = 4πr² = 4 × π × {_fmt(r)}² ≈ {_fmt(4 * math.pi * r * r)}{_with(unit, 2)}."
    found = re.fullmatch(r"(?i)(volume|surface area) of (cylinder|cone) (?:with|of) radius(?: of)? " + _N + _U
                         + r",? and height(?: of)? " + _N + _U, value)
    if found:
        asked, shape, raw_r, unit_r, raw_h, unit_h = found.groups()
        unit = _unit(unit_r, unit_h)
        r, h = _num(raw_r), _num(raw_h)
        if unit is None or r <= 0 or h <= 0:
            return None
        asked, shape = asked.lower(), shape.lower()
        if shape == "cylinder" and asked == "volume":
            return f"Volume = πr²h = π × {_fmt(r)}² × {_fmt(h)} ≈ {_fmt(math.pi * r * r * h)}{_with(unit, 3)}."
        if shape == "cylinder":
            return (f"Surface area = 2πr(r + h) = 2 × π × {_fmt(r)} × ({_fmt(r)} + {_fmt(h)}) "
                    f"≈ {_fmt(2 * math.pi * r * (r + h))}{_with(unit, 2)} (both ends included).")
        if asked == "volume":
            return f"Volume = ⅓πr²h = ⅓ × π × {_fmt(r)}² × {_fmt(h)} ≈ {_fmt(math.pi * r * r * h / 3)}{_with(unit, 3)}."
        slant = math.hypot(r, h)
        return (f"Surface area = πr(r + s), slant s = √(r² + h²) ≈ {_fmt(slant)}; "
                f"≈ {_fmt(math.pi * r * (r + slant))}{_with(unit, 2)} (base included).")
    found = re.fullmatch(r"(?i)(volume|surface area) of cube (?:with|of) (?:side|edge)(?: length)?(?: of)? "
                         + _N + _U, value)
    if found:
        asked, raw, unit = found.groups()
        a, unit = _num(raw), _unit(unit) or ""
        if a <= 0:
            return None
        if asked.lower() == "volume":
            return f"Volume = a³ = {_fmt(a)}³ = {_fmt(a ** 3)}{_with(unit, 3)}."
        return f"Surface area = 6a² = 6 × {_fmt(a)}² = {_fmt(6 * a * a)}{_with(unit, 2)}."
    return None


def _triangle(value: str) -> str | None:
    found = re.fullmatch(r"(?i)area of triangle (?:with|of) base(?: of)? " + _N + _U
                         + r",? and height(?: of)? " + _N + _U, value)
    if found:
        raw_b, unit_b, raw_h, unit_h = found.groups()
        unit = _unit(unit_b, unit_h)
        b, h = _num(raw_b), _num(raw_h)
        if unit is None or b <= 0 or h <= 0:
            return None
        return f"Area = ½ × base × height = ½ × {_fmt(b)} × {_fmt(h)} = {_fmt(b * h / 2)}{_with(unit, 2)}."
    found = re.fullmatch(r"(?i)area of triangle (?:with|of|whose) sides(?: are)? " + _N + _U + r",\s*" + _N + _U
                         + r",?\s*and " + _N + _U, value)
    if found:
        a, ua, b, ub, c, uc = found.groups()
        unit = _unit(ua, ub, uc)
        a, b, c = _num(a), _num(b), _num(c)
        if unit is None or min(a, b, c) <= 0:
            return None
        if a + b <= c or a + c <= b or b + c <= a:
            return (f"Sides {_fmt(a)}, {_fmt(b)} and {_fmt(c)} can't form a triangle: "
                    "the two shorter sides must add up to more than the longest.")
        s = (a + b + c) / 2
        area = math.sqrt(s * (s - a) * (s - b) * (s - c))
        return (f"Area ≈ {_fmt(area)}{_with(unit, 2)} by Heron's formula: s = (a + b + c) ÷ 2 = {_fmt(s)}, "
                f"area = √(s(s − a)(s − b)(s − c)).")
    found = re.fullmatch(r"(?i)(?:third|missing|remaining) angle of triangle (?:with|whose) (?:other )?(?:two )?"
                         r"angles(?: are| of)? " + _N + r"\s*(?:°|degrees?)?,? and " + _N + r"\s*(?:°|degrees?)?", value)
    if found:
        a, b = map(_num, found.groups())
        if a <= 0 or b <= 0 or a + b >= 180:
            return f"Angles of {_fmt(a)}° and {_fmt(b)}° leave nothing for a third angle: a triangle's angles add up to 180°."
        return f"Third angle = 180° − {_fmt(a)}° − {_fmt(b)}° = {_fmt(180 - a - b)}°."
    return None


def _polygon(value: str) -> str | None:
    names = "|".join(POLYGONS)
    found = re.fullmatch(r"(?i)(?:sum of (?:(?:all|its)\s+)?(?:interior )?angles (?:of|in)|(each |one )?interior angle of) "
                         r"(regular )?(?:(" + names + r")|(\d+)[- ]sided polygon|polygon with (\d+) sides)", value)
    if not found:
        return None
    each, regular, name, sides_a, sides_b = found.groups()
    n = POLYGONS[name.lower()] if name else int(sides_a or sides_b)
    if not 3 <= n <= 10_000:
        return None
    total = (n - 2) * 180
    if each or not value.lower().startswith("sum"):
        if not regular and n > 3 and name not in {"square"}:
            return (f"Only a regular {n}-sided polygon has equal angles: each is ({n} − 2) × 180° ÷ {n} = "
                    f"{_fmt(total / n)}°. An irregular one's angles differ but still sum to {total}°.")
        return f"Each interior angle = ({n} − 2) × 180° ÷ {n} = {_fmt(total / n)}°."
    return f"Sum of interior angles = (n − 2) × 180° = ({n} − 2) × 180° = {total}°."


def _distance(value: str) -> str | None:
    point = r"\(\s*" + _SIGNED + r"\s*,\s*" + _SIGNED + r"\s*\)"
    found = re.fullmatch(r"(?i)distance between (?:points? )?" + point + r" and " + point, value)
    if found:
        x1, y1, x2, y2 = map(_num, found.groups())
        d = math.hypot(x2 - x1, y2 - y1)
        return (f"Distance = √((x₂ − x₁)² + (y₂ − y₁)²) = √({_fmt(x2 - x1)}² + {_fmt(y2 - y1)}²) ≈ {_fmt(d)} units "
                "(straight line on a flat plane).")
    pair = r"\(?\s*" + _SIGNED + r"\s*°?\s*,\s*" + _SIGNED + r"\s*°?\s*\)?"
    found = re.fullmatch(r"(?i)(?:great[- ]circle |earth |ground )?distance (?:on earth )?between (?:coordinates|"
                         r"lat(?:itude)?[ /,]*lon(?:gitude)?|gps (?:points|coordinates)) " + pair + r" and " + pair, value)
    if not found and re.match(r"(?i)great[- ]circle distance", value):
        found = re.fullmatch(r"(?i)great[- ]circle distance between " + pair + r" and " + pair, value)
    if not found:
        return None
    lat1, lon1, lat2, lon2 = map(_num, found.groups())
    if not (abs(lat1) <= 90 and abs(lat2) <= 90 and abs(lon1) <= 180 and abs(lon2) <= 180):
        return "Latitude must be between −90 and 90 and longitude between −180 and 180 (decimal degrees)."
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    km = 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))
    return (f"Great-circle distance ≈ {_fmt(km, 1)} km ({_fmt(km / 1.609344, 1)} miles), by the haversine formula "
            f"on a sphere of mean Earth radius {EARTH_RADIUS_KM} km.\n"
            "That is the shortest path over the surface; the ellipsoid shifts it by up to about 0.5%, and roads "
            "or flight routes are longer.")


def geometry_answer(text: str) -> str | None:
    """A worked answer for a complete geometry or Earth-distance request, else None."""
    value = _clean(text)
    if not value or len(value) > 300:
        return None
    try:
        for solve in (_circle, _solids, _triangle, _polygon, _distance):
            answer = solve(value)
            if answer is not None:
                return answer
    except (ValueError, ArithmeticError, OverflowError):
        return None
    return None
