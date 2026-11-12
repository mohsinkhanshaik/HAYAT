"""Which state agency answers a state question beyond tax: business, unemployment, hiring, labor, contracts, law.

"How do I register an LLC in Michigan?" or "unemployment tax rate in Ohio" are
answered by one state office each, and the rules differ in every state.
config/state-agencies.json lists, for all 50 states and DC, the agencies on
each state's IRS page (Secretary of State and licensing, unemployment
insurance, new-hire reporting, labor, procurement, statutes, the state
portal), each checked on 2026-10-31. This module recognises the topic and
the state ("my state" is HAYAT_HOME_STATE) and turns them into searches on
that agency's own site. It holds no rules or rates itself.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).resolve().parents[2] / "config" / "state-agencies.json"

#: Topic words, checked in order: the first that matches decides the agency.
TOPICS = (
    ("new_hire", re.compile(r"(?i)\bnew[- ]hires?\b")),
    ("unemployment", re.compile(r"(?i)\b(?:unemployment|jobless|reemployment|ui (?:tax|claims?|benefits?|rate)|"
                                r"employer (?:account|tax rate)|payroll tax(?:es)?|suta)\b")),
    ("labor", re.compile(r"(?i)\b(?:minimum wage|overtime|labou?r laws?|wage (?:claims?|laws?|theft)|"
                         r"workers'? comp\w*|paid (?:sick|leave)|child labou?r|final paycheck)\b")),
    ("business", re.compile(r"(?i)\b(?:llc|l\.l\.c\.|corporation|incorporat\w*|register (?:a |my |an )?(?:business|company)|"
                            r"start(?:ing)? (?:a |my )?business|business (?:registration|licen[cs]es?|entity|name|filings?)|"
                            r"annual (?:report|statement)|dba|doing business as|articles of (?:organization|incorporation)|"
                            r"secretary of state|professional licen[cs]es?|licen[cs]e lookup|sales (?:tax )?permit)\b")),
    ("procurement", re.compile(r"(?i)\b(?:state contracts?|government contracts?|bids?|rfps?|vendor registration|"
                               r"procurement|sell(?:ing)? to the state|supplier registration)\b")),
    ("statutes", re.compile(r"(?i)\b(?:state laws?|statutes?|administrative code|state code|legislation|"
                            r"what does the law say|is it legal)\b")),
)


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def topic_of(text: str) -> str | None:
    value = str(text or "")
    return next((name for name, pattern in TOPICS if pattern.search(value)), None)


def agencies_for(text: str) -> list[dict[str, Any]]:
    """[{region, code, topic, agencies}] for each US state the text names, when its topic is known."""
    from app.research.tax_sources import named_regions

    topic = topic_of(text)
    if topic is None:
        return []
    by_code = {state["code"]: state for state in catalog()["states"]}
    found = []
    for row in named_regions(text):
        state = by_code.get(row["code"])
        if state is None:
            continue
        agencies = [agency for agency in state["agencies"] if agency["topic"] == topic]
        if not agencies:
            agencies = [agency for agency in state["agencies"] if agency["topic"] == "portal"] or state_domain(row)
        if agencies:
            found.append({"region": state["region"], "code": state["code"], "topic": topic, "agencies": agencies})
    return found


def state_domain(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The state government's own .gov domain, from its tax agency's address (tax.ohio.gov → ohio.gov).

    Used when the IRS page listed no agency for a topic ("minimum wage in
    Ohio"): every state agency publishes under it. An agency-only domain
    (azdor.gov) or a non-.gov one says nothing about the rest, so none is given.
    """
    labels = str(row.get("site", "")).split(".")
    if len(labels) < 3 or labels[-1] != "gov":
        return []
    domain = ".".join(labels[-2:])
    return [{"topic": "portal", "label": f"{row['region']} state government sites", "url": f"https://{domain}/",
             "site": domain, "status": "derived from the tax agency's address"}]


def planned_queries(question: str, subject: str, limit: int = 2) -> list[tuple[str, str]]:
    """Searches restricted to the named state's agency for the topic: (query, rationale)."""
    found = agencies_for(question)
    if not found or len(found) > 3:
        return []
    queries = []
    for state in found:
        sites = list(dict.fromkeys(agency["site"] for agency in state["agencies"]))[:limit]
        for site, agency in ((site, next(a for a in state["agencies"] if a["site"] == site)) for site in sites):
            queries.append((f"site:{site} {subject}", f"{state['region']}: {agency['label']} (official)"))
    return queries


def sources_answer(place: str, topic: str = "") -> dict[str, Any]:
    """The state agencies for a place, optionally one topic, for the state_agencies tool."""
    from app.research.tax_sources import named_regions

    data = catalog()
    states = {state["code"]: state for state in data["states"]}
    rows = [states[row["code"]] for row in named_regions(place) if row["code"] in states]
    wanted = topic.strip().lower() if topic else ""
    if wanted and wanted not in data["topics"]:
        wanted = topic_of(topic) or ""
    result = [{"region": state["region"], "code": state["code"],
               "agencies": [agency for agency in state["agencies"] if not wanted or agency["topic"] == wanted]}
              for state in rows]
    return {"states": result, "topics": data["topics"], "source": data["source"],
            "note": ("No US state recognised in that place." if not result else
                     "Rules, fees and rates change; read the agency's current page before relying on them.")}
