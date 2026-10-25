"""Plate formats: validation and OCR-confusion repair.

A format is a set of masks: L = letter, D = digit, A = either. OCR confuses
O/0, I/1, B/8, S/5, Z/2 and G/6; where a mask fixes the class of a position,
the character is repaired toward it, and the repair with the fewest changes
wins. US plates have no single national layout, so they are only cleaned and
length-checked. More formats are added in the dashboard's settings as masks
or regular expressions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TO_LETTER = {"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}
TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "J": "1", "Z": "2", "A": "4", "S": "5", "G": "6",
            "T": "7", "B": "8"}


@dataclass(frozen=True)
class PlateFormat:
    code: str
    label: str
    masks: tuple[str, ...] = ()
    pattern: str = ""
    min_length: int = 2
    max_length: int = 10
    examples: tuple[str, ...] = field(default_factory=tuple)

    def regex(self) -> re.Pattern[str]:
        if self.pattern:
            return re.compile(self.pattern)
        # Character by character: chained str.replace turned the "A" inside "[A-Z]" into another class.
        classes = {"L": "[A-Z]", "D": "[0-9]", "A": "[A-Z0-9]"}
        parts = ["".join(classes[char] for char in mask) for mask in self.masks]
        return re.compile("|".join(f"(?:{part})" for part in parts) or "[A-Z0-9]+")


#: Built-in formats. India: state (2 letters), RTO district (1-2 digits), series (0-3 letters), number (4 digits);
#: Bharat series: year (2 digits) BH number (4 digits) series (1-2 letters).
FORMATS: dict[str, PlateFormat] = {
    "IN": PlateFormat("IN", "India", ("LLDDLLDDDD", "LLDDLDDDD", "LLDDLLLDDDD", "LLDLLLDDDD", "LLDLLDDDD", "LLDLDDDD", "LLDDDDDD",
                                       "DDLLDDDDL", "DDLLDDDDLL"), min_length=6, max_length=11,
                      examples=("MH12AB1234", "DL3CAF0001", "22BH1234AA")),
    "US": PlateFormat("US", "United States (any state)", pattern=r"[A-Z0-9]{2,8}", min_length=2, max_length=8,
                      examples=("7ABC123", "ABC1234", "HAYAT1")),
    # Great Britain (AB12 CDE) and Northern Ireland (THZ 1773, ABZ 123).
    "UK": PlateFormat("UK", "United Kingdom", ("LLDDLLL", "LLLDDDD", "LLLDDD"), min_length=6, max_length=7,
                      examples=("AB12CDE", "THZ1773")),
    "EU": PlateFormat("EU", "Europe (generic)", pattern=r"[A-Z0-9]{4,10}", min_length=4, max_length=10,
                      examples=("B MW 1234", "AB-123-CD")),
}


def clean(text: str) -> str:
    """Upper case letters and digits only ("mh 12-ab 1234" → "MH12AB1234")."""
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def coerce(text: str, mask: str) -> tuple[str, int] | None:
    """The text forced onto a mask of the same length, with how many characters changed; None if impossible."""
    if len(text) != len(mask):
        return None
    out, changes = [], 0
    for char, kind in zip(text, mask):
        if kind == "L" and char.isdigit():
            if char not in TO_LETTER:
                return None
            char, changes = TO_LETTER[char], changes + 1
        elif kind == "D" and char.isalpha():
            if char not in TO_DIGIT:
                return None
            char, changes = TO_DIGIT[char], changes + 1
        out.append(char)
    return "".join(out), changes


@dataclass
class PlateReading:
    raw: str
    plate: str
    valid: bool
    format: str
    repaired: int = 0
    #: True when the plate was found inside longer text ("AFREENMH09S2112"): kept for review.
    trimmed: bool = False


def read_plate(text: str, region: str = "IN", custom: dict[str, PlateFormat] | None = None) -> PlateReading:
    """Normalise OCR text for a region: repaired to the closest mask when one fits, flagged when none does."""
    formats = {**FORMATS, **(custom or {})}
    value = clean(text)
    chosen = formats.get(region) or formats["US"]
    best: tuple[str, int] | None = None
    for mask in chosen.masks:
        candidate = coerce(value, mask)
        if candidate and (best is None or candidate[1] < best[1]):
            best = candidate
    if best is not None:
        return PlateReading(text, best[0], True, chosen.code, best[1])
    valid = bool(chosen.regex().fullmatch(value)) and chosen.min_length <= len(value) <= chosen.max_length
    if not valid and chosen.masks:
        # OCR often adds nearby words (a dealer banner, "IND"): look for the part that is a plate.
        window: tuple[str, int, int] | None = None
        for mask in chosen.masks:
            for start in range(0, len(value) - len(mask) + 1):
                candidate = coerce(value[start:start + len(mask)], mask)
                if candidate and (window is None or (candidate[1], -len(mask)) < (window[1], -len(window[0]))):
                    window = (candidate[0], candidate[1], start)
        if window is not None:
            return PlateReading(text, window[0], True, chosen.code, window[1], trimmed=True)
    return PlateReading(text, value, valid, chosen.code if valid else "", 0)


def custom_format(code: str, label: str, spec: str) -> PlateFormat:
    """A format from the settings: masks separated by commas ("LLDDLLL, LLDDDD"), or a regex prefixed with 're:'."""
    code = clean(code)[:6] or "CUSTOM"
    if spec.startswith("re:"):
        pattern = spec[3:].strip()
        re.compile(pattern)  # raises on a bad expression, before it is saved
        return PlateFormat(code, label or code, pattern=pattern, min_length=2, max_length=12)
    masks = tuple(mask.strip().upper() for mask in spec.split(",") if mask.strip())
    if not masks or any(not re.fullmatch(r"[LDA]{2,12}", mask) for mask in masks):
        raise ValueError("Masks use L (letter), D (digit) and A (either), e.g. LLDDLLL.")
    return PlateFormat(code, label or code, masks, min_length=min(map(len, masks)), max_length=max(map(len, masks)))
