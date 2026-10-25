"""Exact answers a model gets wrong: dates from this computer's clock, letters counted in a word.

"What date will it be 45 days from today?" was sent to web search because it
mentions today, and came back as snippets about a "Days From Today
Calculator". A date is arithmetic on the local calendar: exact, instant and
offline. Only whole questions of these shapes are answered; anything else
returns None and goes through the normal path.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_UNITS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "fortnight": 14, "fortnights": 14}
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
                 "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90, "hundred": 100}
_AMOUNT = r"(?P<n>\d{1,5}|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\s+(?P<unit>days?|weeks?|fortnights?)"
_LEAD = r"(?i)^\s*(?:hey\s+|so\s+|and\s+|ok(?:ay)?\s+|please\s+|hayat[,\s]+)*"

_FORWARD = re.compile(
    _LEAD + r"(?:what(?:'s|’s| is)?\s+(?:the\s+)?(?:date|day(?:\s+of\s+the\s+week)?|weekday)\s+(?:will\s+it\s+be\s+|is\s+it\s+|is\s+)?)?(?:in\s+)?"
    + _AMOUNT + r"\s+(?:from\s+(?:today|now)|after\s+today|later|from\s+now)?\s*(?:,?\s*what(?:'s|’s| is)\s+the\s+date)?[?.!]*\s*$")
_FORWARD_2 = re.compile(
    _LEAD + r"what\s+(?:date|day(?:\s+of\s+the\s+week)?|weekday)\s+(?:will\s+it\s+be|is\s+it|is)\s+(?:in\s+)?" + _AMOUNT
    + r"(?:\s+(?:from\s+(?:today|now)|after\s+today|later))?[?.!]*\s*$")
_BACKWARD = re.compile(
    _LEAD + r"what\s+(?:date|day(?:\s+of\s+the\s+week)?|weekday)\s+(?:was\s+it|was)\s+" + _AMOUNT + r"\s+ago[?.!]*\s*$")
_WEEKDAY = re.compile(
    _LEAD + r"what\s+day\s+(?:of\s+the\s+week\s+)?(?:was|is|will\s+be|will|falls\s+on|did|does)\s+(?P<when>.+?)"
    r"(?:\s+(?:fall\s+on|be|on))?[?.!]*\s*$")
_UNTIL = re.compile(
    _LEAD + r"how\s+many\s+days\s+(?:are\s+there\s+|is\s+it\s+|left\s+)?(?:until|till|til|to|before)\s+(?P<when>.+?)[?.!]*\s*$")

_BETWEEN = re.compile(
    _LEAD + r"how\s+many\s+days\s+(?:are\s+there\s+|is\s+it\s+|passed\s+|have\s+passed\s+)?(?:from|between)\s+"
    r"(?P<start>.+?)\s+(?:to|and|until|till)\s+(?P<end>.+?)[?.!]*\s*$")

_FORMATS = ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%Y-%m-%d", "%d/%m/%Y", "%B %d", "%d %B", "%b %d", "%d %b")


def _count(raw: str) -> int:
    return int(raw) if raw.isdigit() else _NUMBER_WORDS[raw.lower()]


def parse_date(text: str, today: date) -> date | None:
    value = re.sub(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", " ".join(str(text).replace(",", " ").split()))
    value = re.sub(r"(?i)^(?:the\s+|on\s+)", "", value).strip(" .?!")
    if value.lower() in {"today", "now"}:
        return today
    if value.lower() == "tomorrow":
        return today + timedelta(days=1)
    if value.lower() == "yesterday":
        return today - timedelta(days=1)
    # Holidays on a fixed date: this year's, or next year's once this year's has passed ("next christmas" too).
    holiday = re.fullmatch(r"(?i)(?:(this|next)\s+)?(christmas(?:\s+day)?|new\s+year(?:'s|’s)?(?:\s+day)?|halloween|"
                           r"valentine(?:'s|’s)?(?:\s+day)?|christmas\s+eve|new\s+year(?:'s|’s)?\s+eve)(?:\s+(\d{4}))?", value)
    if holiday:
        name = holiday.group(2).lower()
        month, day = ((12, 24) if "eve" in name and "christmas" in name else (12, 31) if "eve" in name else (12, 25) if "christmas" in name
                      else (1, 1) if "new" in name else (10, 31) if "halloween" in name else (2, 14))
        year = int(holiday.group(3)) if holiday.group(3) else today.year
        found = date(year, month, day)
        if not holiday.group(3) and (found < today or (holiday.group(1) or "").lower() == "next" and found <= today):
            found = date(year + 1, month, day)
        return found
    for pattern in _FORMATS:
        # A date without a year is this year's; parsed with the year attached
        # so 29 February works and nothing is ambiguous.
        source, form = (value, pattern) if "%Y" in pattern else (f"{value} {today.year}", f"{pattern} %Y")
        try:
            return datetime.strptime(source, form).date()
        except ValueError:
            continue
    return None


def _long(day: date) -> str:
    return f"{day:%A}, {day.day} {day:%B %Y}"


def date_answer(text: str, today: date | None = None) -> str | None:
    """A plain answer to a date question, or None when the text is not one."""
    today = today or date.today()
    value = " ".join(str(text or "").split())
    if not value or len(value) > 160:
        return None
    for pattern, sign in ((_FORWARD_2, 1), (_FORWARD, 1), (_BACKWARD, -1)):
        match = pattern.match(value)
        if match and match.group("n"):
            days = _count(match.group("n")) * _UNITS[match.group("unit").lower()]
            target = today + timedelta(days=sign * days)
            span = f"{days} day{'s' if days != 1 else ''}"
            when = "from today" if sign > 0 else "ago"
            return f"{span} {when} is {_long(target)}.\nWorked out from this computer's calendar (today is {_long(today)})."
    match = _BETWEEN.match(value)
    if match:
        start, end = parse_date(match.group("start"), today), parse_date(match.group("end"), today)
        if start is not None and end is not None:
            gap = abs((end - start).days)
            first, last = sorted((start, end))
            # "How many days from A to B" is read two ways; give the difference and say the other.
            return (f"{gap} day{'s' if gap != 1 else ''} from {_long(first)} to {_long(last)} "
                    f"({gap + 1} if both of those days are counted).\nWorked out from the calendar.")
    match = _WEEKDAY.match(value)
    if match:
        day = parse_date(match.group("when"), today)
        if day is not None:
            tense = "was" if day < today else "is" if day == today else "will be"
            return f"{day.day} {day:%B %Y} {tense} a {day:%A}.\nWorked out from the calendar."
    match = _UNTIL.match(value)
    if match:
        day = parse_date(match.group("when"), today)
        if day is not None:
            if day < today and "%Y" not in match.group("when") and not re.search(r"\d{4}", match.group("when")):
                day = day.replace(year=today.year + 1)
            gap = (day - today).days
            if gap >= 0:
                return (f"{gap} day{'s' if gap != 1 else ''} until {_long(day)}.\n"
                        f"Worked out from this computer's calendar (today is {_long(today)}).")
    return None


_LETTERS_IN = re.compile(
    r"(?i)^\s*how\s+many\s+(?P<what>letters|characters|vowels|consonants)\s+(?:are\s+)?(?:there\s+)?in\s+"
    r"(?:the\s+word\s+|the\s+name\s+)?[\"'“‘]?(?P<word>[a-z][a-z'-]{0,40})[\"'”’]?[?.!]*\s*$")
_LETTER_TIMES = re.compile(
    r"(?i)^\s*how\s+many\s+(?:times\s+does\s+(?:the\s+)?(?:letter\s+)?[\"'“‘]?(?P<a>[a-z])[\"'”’]?\s+(?:appear|occur|show\s+up)|"
    r"[\"'“‘]?(?P<b>[a-z])[\"'”’]?'?s(?:\s+are)?(?:\s+there)?)\s+in\s+(?:the\s+word\s+)?[\"'“‘]?(?P<word>[a-z][a-z'-]{0,40})"
    r"[\"'”’]?[?.!]*\s*$")


def letter_count_answer(text: str) -> str | None:
    """Counting letters in a word, done exactly ("strawberry" has three r's)."""
    value = " ".join(str(text or "").split())
    match = _LETTER_TIMES.match(value)
    if match:
        letter, word = (match.group("a") or match.group("b")).lower(), match.group("word").strip("'-")
        count = word.lower().count(letter)
        return (f"The letter “{letter}” appears {count} time{'s' if count != 1 else ''} in “{word}”: "
                + "-".join(word) + ".")
    match = _LETTERS_IN.match(value)
    if match:
        word, what = match.group("word").strip("'-"), match.group("what").lower()
        letters = [char for char in word.lower() if char.isalpha()]
        vowels = [char for char in letters if char in "aeiou"]
        count = {"letters": len(letters), "characters": len(word), "vowels": len(vowels),
                 "consonants": len(letters) - len(vowels)}[what]
        return f"“{word}” has {count} {what if count != 1 else what[:-1]}: " + "-".join(word) + "."
    return None
