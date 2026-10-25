from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import re

# Event questions tied to a recent moment. "Who won the match last night?" was
# classed as stable on 2026-10-24: a year-old result was answered as last
# night's and its search results were cached for 14 days.
_RELATIVE = re.compile(
    r"\b(?:last night|yesterday|tonight|this (?:morning|afternoon|evening|week)|earlier today)\b", re.I)
_MONTHS = {name: index for index, names in enumerate((
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
    ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
    ("november", "nov"), ("december", "dec")), 1) for name in names}
_MONTH = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DATES = (
    (re.compile(_MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I), (3, 1, 2)),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r",?\s+(\d{4})\b", re.I), (3, 2, 1)),
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), (1, 2, 3)),  # also "2026-10-23T22:10Z"
)


def classify_freshness(query: str) -> tuple[str, timedelta | None]:
    text = query.lower()
    if _RELATIVE.search(text) or re.search(r"\b(?:who won|winner of|final score)\b", text):
        return "live", timedelta(hours=6)
    if re.search(r"\b(?:today|now|current price|breaking|live|weather|scores?)\b", text):
        return "live", timedelta(hours=6)
    if re.search(r"\b(?:latest|recent|news|current|releases?|versions?|security)\b", text):
        return "time_sensitive", timedelta(days=7)
    if re.search(r"\b(?:apis?|librar(?:y|ies)|software|documentation|products?|compan(?:y|ies))\b", text):
        return "slow_changing", timedelta(days=90)
    return "stable", None


def validity_horizon(retrieved_at: str, freshness_class: str) -> str | None:
    duration = {"live": timedelta(hours=6), "time_sensitive": timedelta(days=7), "slow_changing": timedelta(days=90)}.get(freshness_class)
    if duration is None:
        return None
    base = datetime.fromisoformat(retrieved_at).astimezone(UTC)
    return (base + duration).isoformat()


def relative_window(query: str, today: date) -> tuple[str, date, date] | None:
    """The phrase and inclusive date range a recent-event question is about, or None.

    One extra day on each side: a match "last night" is often reported with the
    next day's date, and publishers' time zones differ from the owner's.
    """
    match = _RELATIVE.search(query or "")
    if match is None:
        return None
    phrase = match[0].lower()
    days_back = 8 if phrase == "this week" else 2
    return phrase, today - timedelta(days=days_back), today + timedelta(days=1)


def dated_query(query: str, today: date) -> str | None:
    """The query with its relative phrase replaced by the real date, or None.

    A search for "... last night" matches every "last night" article ever
    written; "... October 23, 2026" matches that night's reports.
    """
    match = _RELATIVE.search(query or "")
    if match is None:
        return None
    phrase = match[0].lower()
    day = today - timedelta(days=1) if phrase in {"last night", "yesterday"} else today
    when = f"{day:%B} {day.year}" if phrase == "this week" else f"{day:%B} {day.day}, {day.year}"
    return " ".join((query[:match.start()] + when + query[match.end():]).split())


def mentioned_dates(text: str) -> list[date]:
    """Full calendar dates written in the text (month name or ISO forms); partial dates are ignored."""
    found = []
    for pattern, (year_group, month_group, day_group) in _DATES:
        for match in pattern.finditer(text or ""):
            month = match[month_group]
            month = _MONTHS.get(month.lower().rstrip(".")) if not month.isdigit() else int(month)
            try:
                found.append(date(int(match[year_group]), int(month or 0), int(match[day_group])))
            except ValueError:
                continue
    return found


def source_date(value: str | None) -> date | None:
    """The calendar date of a page's published/updated timestamp, if it parses."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        dates = mentioned_dates(text)
        return dates[0] if dates else None


def is_stale(valid_until: str | None, now: datetime | None = None) -> bool:
    if not valid_until:
        return False
    return datetime.fromisoformat(valid_until).astimezone(UTC) <= (now or datetime.now(UTC))
