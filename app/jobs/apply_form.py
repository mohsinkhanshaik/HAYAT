"""Which fields of an employer's application form HAYAT may fill, and with what.

Only facts from the owner's profile and the prepared letter: name, email, phone, links, location, years, the
cover letter, and the notice period or salary expectation the owner set. Everything else is left for the owner:
* files (the CV): some application systems upload a file the moment it is chosen, before any submit, so HAYAT
  never attaches one;
* questions about the law or the person (work authorisation, sponsorship, gender, ethnicity, disability,
  veteran status, criminal record, age): those are the owner's to answer;
* tick boxes (consents, terms) and choices HAYAT cannot match exactly;
* the submit button: sending is the owner's.
"""
from __future__ import annotations

import re
from typing import Any

PERSONAL = re.compile(r"(?i)authori[sz]|sponsor|visa|citizen|gender|sex\b|pronoun|race|ethnic|hispanic|disab|veteran|criminal|convict|"
                      r"age\b|date\s+of\s+birth|birth|religio|marital|nationality|clearance")


def fill_plan(fields: list[dict[str, Any]], data: dict[str, Any], letter: str) -> dict[str, Any]:
    cv, preferences = data["cv"], data["preferences"]
    name = (cv.get("name") or "").split()
    links = cv.get("links", [])
    link = lambda word: next((value for value in links if word in value.lower()), "")
    rules = (
        (r"first\s*name|given\s*name|vorname|forename", name[0] if name else ""),
        (r"last\s*name|surname|family\s*name|nachname", " ".join(name[1:]) if len(name) > 1 else ""),
        (r"^(?:full\s+)?name\b|your\s+name|full\s+name|legal\s+name", cv.get("name", "")),
        (r"e-?mail", cv.get("email", "")),
        (r"phone|mobile|telephone|telefon|cell", cv.get("phone", "")),
        (r"linkedin", link("linkedin")),
        (r"github", link("github")),
        (r"website|portfolio|personal\s+(?:site|url)|blog", next((value for value in links if "linkedin" not in value and "github" not in value), "")),
        (r"\blocation\b|\bcity\b|where\s+(?:are\s+you|do\s+you\s+live)|current\s+(?:location|city)", ", ".join(preferences.get("locations", [])[:1])),
        (r"years\s+of\s+(?:relevant\s+)?experience|how\s+many\s+years", str(cv.get("years_experience") or "")),
        (r"notice\s+period|start\s+date|available\s+to\s+start|earliest\s+start", preferences.get("notice_period", "")),
        (r"salary|compensation|pay\s+expectation|expected\s+(?:pay|ctc)", preferences.get("salary_expectation", "")),
    )
    letter_rule = re.compile(r"(?i)cover\s*letter|additional\s+information|anything\s+else|motivation|why\s+(?:do\s+you|are\s+you|this|us|our)|message")
    fill, left, files = [], [], []
    for field in fields:
        label = (field.get("label") or "").strip()
        kind = field.get("type") or ""
        if field.get("secret") or field.get("payment"):
            continue
        if kind == "file":
            files.append(label or "a file upload")
            continue
        if not label:
            continue
        if PERSONAL.search(label) or kind in ("checkbox", "radio"):
            left.append(label)
            continue
        if field.get("disabled") or field.get("read_only") or field.get("has_value"):
            left.append(label + " (already filled or not editable; review in browser)")
            continue
        value = ""
        if field.get("tag") == "textarea" and letter_rule.search(label):
            value = letter.strip()
        else:
            value = next((answer for pattern, answer in rules if re.search(pattern, label, re.IGNORECASE)), "")
        if value:
            fill.append({"mark": field["mark"], "label": label, "value": value})
        elif field.get("required") or field.get("tag") in ("textarea", "select"):
            left.append(label)
    return {"fill": fill, "left": list(dict.fromkeys(left)), "files": list(dict.fromkeys(files))}
