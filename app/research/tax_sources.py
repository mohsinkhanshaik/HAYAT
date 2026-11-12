"""Which government agency speaks for a tax question, so research asks it first.

A question about Texas sales tax was answered from whichever blog ranked
first, and tax rates change by law every year. The authority is the state's
own revenue agency: config/tax-authorities.json lists every US state and DC
(from the IRS list of state government websites) plus the national agencies.
This module names the agencies a question is about and turns them into
site-restricted searches. It knows no rates itself; those are read from the
agency's current pages.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).resolve().parents[2] / "config" / "tax-authorities.json"

TAX_SUBJECT = re.compile(
    r"(?i)\b(?:tax(?:es|ation|ed|able|payers?)?|gst|vat|excise|tariffs?|(?:stamp|customs|excise) dut(?:y|ies)|"
    r"levy|levies|withholding|tds|itr|irs|1040|w-?2|1099|tax return|revenue department)\b")
_ALL_STATES = re.compile(r"(?i)\b(?:all|every|each|which|what|the 50|50|fifty)\s+(?:us\s+|u\.s\.\s+|american\s+|indian\s+)?states\b"
                         r"|\bstate[- ]by[- ]state\b|\bby state\b")
_DC = re.compile(r"(?i)\b(?:district of columbia|washington,?\s+d\.?\s?c\.?|d\.c\.)(?=\W|$)|\bDC\b")
_INDIA = re.compile(r"(?i)\b(?:india|indian|gst|tds|itr|rupees?|inr|₹|cbic|pan card)\b|₹")
_US_FEDERAL = re.compile(r"(?i)\b(?:federal|irs|united states|u\.s\.|usa|america(?:n)?|1040|w-?2|1099)\b|\bUS\b")


@lru_cache(maxsize=1)
def authorities() -> tuple[dict[str, Any], ...]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return tuple(data["authorities"])


def source_note() -> str:
    return json.loads(CATALOG.read_text(encoding="utf-8"))["source"]


def is_tax_question(text: str) -> bool:
    return bool(TAX_SUBJECT.search(str(text or "")))


_MY_STATE = re.compile(r"(?i)\b(?:my|our|this|home)\s+state\b|\bwhere i live\b")


def home_state() -> str:
    """The owner's state from HAYAT_HOME_STATE in .env (kept out of the published repository), or ""."""
    import os

    from app.config import load_environment

    load_environment()
    return os.environ.get("HAYAT_HOME_STATE", "").strip()


def named_regions(text: str) -> list[dict[str, Any]]:
    """The US states/DC and Indian states/UTs a text names; "my state" means the owner's home state.

    "Washington D.C." is the District, not the state; "West Virginia" is not
    Virginia and "Arkansas" is not Kansas. "Which states…" means every state
    (Indian ones when the text is about India).
    """
    value = " ".join(str(text or "").split())
    # Sub-national agencies: US states (two-letter codes) and Indian states/UTs ("IN-TG").
    states = [row for row in authorities()
              if (row["country"] == "US" and row["code"] != "US") or row["code"].startswith("IN-")]
    if _ALL_STATES.search(value):
        country = "IN" if _INDIA.search(value) else "US"
        return [row for row in states if row["country"] == country]
    found = []
    without_dc = value
    if _DC.search(value):
        found.append(next(row for row in states if row["code"] == "DC"))
        without_dc = _DC.sub(" ", value)
    for row in sorted(states, key=lambda item: -len(item["region"])):
        if row["code"] == "DC" or row in found:
            continue
        names = [row["region"], *row.get("aliases", [])]
        pattern = r"(?i)\b(?:" + "|".join(map(re.escape, names)) + r")\b"
        if row["region"] == "Virginia":
            pattern = r"(?i)(?<!west )\bvirginia\b"
        elif row["region"] == "Kansas":
            pattern = r"(?i)(?<!arkansas)\bkansas\b"
        if re.search(pattern, without_dc):
            found.append(row)
    if not found and _MY_STATE.search(value):
        home = home_state().casefold()
        found = [row for row in states if home and home in {row["region"].casefold(), row["code"].casefold()}]
    return found


def authorities_for(text: str) -> list[dict[str, Any]]:
    """The agencies a tax question names, national first; [] when none is clear.

    State names only count inside a tax question ("Georgia" is also a country).
    "Washington D.C." is the District, not the state.
    """
    value = " ".join(str(text or "").split())
    if not is_tax_question(value):
        return []
    rows = authorities()
    found = named_regions(value)
    national = []
    if _US_FEDERAL.search(value):
        national += [row for row in rows if row["code"] == "US"]
    if _INDIA.search(value):
        india = [row for row in rows if row["country"] == "IN" and not row["code"].startswith("IN-")]
        indirect = bool(re.search(r"(?i)\b(?:gst|cbic|customs|excise|indirect)\b", value))
        direct = bool(re.search(r"(?i)\b(?:income|itr|tds|pan|direct)\b", value))
        if indirect != direct:
            india = [row for row in india if (row["site"] == "incometax.gov.in") == direct]
        national += india
    return national + found


def planned_queries(question: str, subject: str, limit: int = 3) -> list[tuple[str, str]]:
    """Site-restricted searches on the named agencies: (query, rationale)."""
    found = authorities_for(question)
    if not found or len(found) > limit:
        # Many states at once: one search per agency would crowd out everything else.
        return [(f"{subject} by state official revenue department", "State tax agencies (many named)")] if found else []
    return [(f"site:{row['site']} {subject}", f"{row['agency']}, the official tax authority") for row in found]


def sources_answer(text: str) -> dict[str, Any]:
    """The official agencies for a place, for the tax_authorities tool."""
    found = authorities_for(text if is_tax_question(text) else f"tax {text}")
    if not found:
        found = [row for row in authorities() if row["code"] in {"US", "IN"}]
        match = "none named; national agencies listed"
    else:
        match = "named in the request"
    return {"match": match, "authorities": found, "source": source_note(),
            "note": "Rates and rules change by law; read the agency's current page and its date before relying on them."}
