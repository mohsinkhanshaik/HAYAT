"""How the owner's CV reads against the jobs it matches, with what to change and why.

Checks, each passed or not with its reason:
* contact: email, phone and a LinkedIn or portfolio link;
* a Skills section, and skills named in the text but not listed in it;
* experience lines that show results with numbers, and lines that start weakly ("responsible for", "helped");
* length;
* the skills the owner's best matches ask for most that the CV does not name (add only the true ones);
* the jobs it fits best, and what each would want first.
It never rewrites facts: it says what to add or move, and only the owner knows what is true.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.jobs.text import CATEGORY

WEAK = re.compile(r"(?i)^(?:responsible\s+for|worked\s+on|helped(?:\s+with)?|assisted(?:\s+with)?|involved\s+in|duties\s+included|"
                  r"tasked\s+with|participated\s+in|in\s+charge\s+of)\b")
STRONG = {"responsible for": "Led / Owned", "worked on": "Built / Delivered", "helped": "Supported / Improved", "helped with": "Supported",
          "assisted": "Supported", "assisted with": "Supported", "involved in": "Contributed to", "duties included": "(start with the action)",
          "tasked with": "Delivered", "participated in": "Contributed to", "in charge of": "Led"}
NUMBER = re.compile(r"\d|%|\$|€|£|₹|\bpercent\b")


def review(data: dict[str, Any], skills: list[str], best: list[dict[str, Any]]) -> dict[str, Any]:
    cv = data["cv"]
    if not cv.get("read_at"):
        raise ValueError("Read your CV first: “read my CV from C:\\path\\cv.pdf”, or Profile → Read my CV in the Jobs window.")
    checks = []

    def check(name: str, passed: bool, advice: str) -> None:
        checks.append({"check": name, "passed": passed, "advice": "" if passed else advice})
    check("Email", bool(cv.get("email")), "Put your email in the first lines.")
    check("Phone", bool(cv.get("phone")), "Add a phone number with the country code (e.g. +91 …).")
    check("LinkedIn or portfolio", bool(cv.get("links")), "Add your LinkedIn or a portfolio link; many forms ask for it.")
    listed = [item["name"] for item in cv.get("skills", []) if item.get("listed")]
    check("Skills section", bool(listed), "Add a short Skills section with the tools you really use; tracking systems read it first.")
    unlisted = [item["name"] for item in cv.get("skills", []) if not item.get("listed") and CATEGORY.get(item["name"]) not in ("Support & people",)]
    check("Skills listed", not (listed and unlisted), "Named in your text but missing from Skills: " + ", ".join(unlisted[:8]) + ".")
    lines = cv.get("lines", [])
    with_numbers = [line for line in lines if NUMBER.search(line)]
    measured = len(with_numbers) / len(lines) if lines else 0
    check("Results with numbers", measured >= .3, f"Only {len(with_numbers)} of {len(lines)} experience lines show a number. Add the "
          "result where you know it: time saved, % improved, money, volume, users.")
    weak = [line for line in lines if WEAK.match(line)]
    if weak:
        examples = []
        for line in weak[:3]:
            start = WEAK.match(line).group(0).lower()
            examples.append(f"“{line[:70]}” → start with “{STRONG.get(start, 'an action verb')}”")
        check("Strong openings", False, "Lines that start weakly: " + "; ".join(examples) + ".")
    else:
        check("Strong openings", True, "")
    words = cv.get("words") or 0
    check("Length", 250 <= words <= 1100, "Under 250 words says too little; aim for one page (two for 10+ years)." if words < 250 else
          "Over about two pages: cut older or less relevant lines.")
    check("Experience dates", bool(cv.get("date_ranges")), "Give every role its dates (Mon YYYY – Mon YYYY); without them years cannot be counted.")
    eligible = [row for row in best if row.get("verdict") != "not eligible"]
    asked = Counter(name for row in eligible for name in (row.get("match") or {}).get("missing_required", []))
    gaps = [{"skill": name, "jobs": count, "category": CATEGORY.get(name, "Other")} for name, count in asked.most_common(10)]
    fits = [{"number": row["number"], "title": row["title"], "company": row["company"], "score": row["score"],
             "put_first": [name for name in (row.get("reading") or {}).get("skills", {}).get("required", []) if name.lower() in {s.lower() for s in skills}][:5],
             "missing": (row.get("match") or {}).get("missing_required", [])[:4]} for row in eligible[:5]]
    passed = sum(item["passed"] for item in checks)
    return {"jobs_view": True, "cv_review": True, "file": cv.get("file", ""), "checks": checks, "passed": passed, "total": len(checks),
            "keyword_gaps": gaps, "matches_read": len(eligible), "best_fits": fits,
            "basis": "Suggestions only: add a skill or a number only when it is true. Read against your best "
                     f"{len(eligible)} eligible matches."}
