"""What a job posting asks for, read from its text with rules that say why.

* **Skills,** split by section: under "requirements / must have / you have" they are required; under
  "nice to have / preferred / bonus" they are a plus; elsewhere they are mentioned.
* **Years of experience** ("3+ years", "2–4 years of experience").
* **Level** from the title (intern, junior, mid, senior, lead, manager).
* **Work mode:** remote, hybrid or on-site.
* **Where you must be:** "US only", "must be based in the EU", time zones, work authorisation,
  whether visa sponsorship is offered.
* **Languages** the posting requires, and the language it is written in (a posting in German
  usually needs German).
* **Salary,** from the source's fields or the text.
* **Education** asked for.
* **Warning signs** common in fake postings: fees to apply, bank details, chat-app-only contact,
  pay that is too good for no experience. Shown as "check carefully", never as proof.
"""
from __future__ import annotations

import re
from typing import Any

from app.jobs.text import CATEGORY, LANGUAGES, skills_in

REQUIRED = re.compile(r"(?i)^(?:[#*•\-\s]*)(?:(?:key\s+|minimum\s+|basic\s+|essential\s+)?(?:requirements?|qualifications?)|"
                      r"what\s+(?:you(?:'ll)?\s+(?:need|bring)|we(?:'re|\s+are)\s+looking\s+for|you\s+have)|must[\s-]+haves?|"
                      r"you\s+(?:have|bring|are)|about\s+you|who\s+you\s+are|your\s+(?:profile|skills|experience|background)|"
                      r"skills(?:\s+(?:and|&)\s+experience)?|ihr\s+profil|was\s+du\s+mitbringst|anforderungen|dein\s+profil)\b.{0,40}$")
NICE = re.compile(r"(?i)^(?:[#*•\-\s]*)(?:nice[\s-]+to[\s-]+haves?|(?:preferred|desired|desirable|bonus|additional)\s*"
                  r"(?:qualifications?|skills|points?|experience)?|bonus\s+points|it(?:'s|\s+is)\s+a\s+plus|pluses|good\s+to\s+have|"
                  r"wünschenswert|von\s+vorteil)\b.{0,40}$")
OTHER = re.compile(r"(?i)^(?:[#*•\-\s]*)(?:responsibilities|what\s+you(?:'ll)?\s+do|the\s+role|your\s+role|about\s+(?:us|the\s+(?:company|team|role))|"
                   r"benefits|perks|what\s+we\s+offer|why\s+(?:join|work)|compensation|our\s+offer|deine\s+aufgaben|ihre\s+aufgaben|wir\s+bieten)\b.{0,40}$")
PLUS_LINE = re.compile(r"(?i)\b(?:a\s+plus|is\s+a\s+bonus|nice\s+to\s+have|preferred|ideally|bonus\s+if|would\s+be\s+great)\b")
YEARS = re.compile(r"(?i)(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?(?:years?|yrs?|jahre)\b(?:\s+of)?"
                   r"(?=[^.\n]{0,60}(?:experience|exp\b|working|berufserfahrung|in\s+(?:a|the)\s))")
LEVELS = (("intern", r"\b(?:intern|internship|trainee|werkstudent|praktikum|apprentice)\b"),
          ("junior", r"\b(?:junior|jr\.?|entry[\s-]level|graduate|associate)\b"),
          ("lead", r"\b(?:lead|staff|principal|architect)\b"),
          ("manager", r"\b(?:manager|head\s+of|director|vp|chief)\b"),
          ("senior", r"\b(?:senior|sr\.?)\b"),
          ("mid", r"\b(?:mid[\s-]?level|intermediate|ii)\b"))
REGIONS = {
    "US": r"U\.?S\.?A?\.?|United\s+States|America", "Canada": r"Canada", "UK": r"U\.?K\.?|United\s+Kingdom|Britain",
    "EU": r"E\.?U\.?|Europe(?:an\s+Union)?|EMEA", "Germany": r"Germany|Deutschland", "India": r"India", "Australia": r"Australia",
    "LATAM": r"LATAM|Latin\s+America", "APAC": r"APAC|Asia[\s-]Pacific", "Netherlands": r"Netherlands", "France": r"France",
    "Spain": r"Spain", "Poland": r"Poland", "Brazil": r"Brazil", "Mexico": r"Mexico", "Philippines": r"Philippines",
    "Pakistan": r"Pakistan", "UAE": r"UAE|United\s+Arab\s+Emirates|Dubai", "Singapore": r"Singapore", "Worldwide": r"Worldwide|Anywhere|Global",
}
#: Aimed at the applicant only: "we will not pay fees to agencies" or a gift-card company describing itself is not a warning.
WARNINGS = (
    (r"(?i)\b(?:you|candidates?|applicants?|selected\s+candidates?|new\s+hires?)\s+(?:must|will|need\s+to|are\s+required\s+to|have\s+to|"
     r"should)\s+(?:first\s+)?(?:pay|send\s+money|deposit|transfer\s+money|purchase|buy)\b", "asks you to pay or buy something"),
    (r"(?i)\b(?:registration|training|processing|application|onboarding|equipment|starter[\s-]kit)\s+(?:fee|cost|deposit)\s+"
     r"(?:of|is|will|must|applies|\$|€|£|₹)|\brefundable\s+(?:fee|deposit)\b", "mentions a fee to apply, train or start"),
    (r"(?i)\b(?:send|provide|share|submit|email)\s+(?:us\s+)?(?:your\s+)?(?:bank\s+(?:account\s+)?details|credit\s+card(?:\s+details)?|"
     r"social\s+security\s+number|ssn|passport\s+(?:copy|scan)|id\s+card\s+(?:copy|scan))\b", "asks for bank or identity details"),
    (r"(?i)\b(?:pay(?:ment)?|paid|purchase|buy)\s+(?:\w+\s+){0,3}(?:via|with|using|in|through)\s+(?:gift\s+cards?|western\s+union|moneygram)\b",
     "mentions untraceable payment"),
    (r"(?i)\b(?:contact|message|text|add)\s+(?:us|me|the\s+recruiter|hr)\s+(?:on|via)\s+(?:telegram|whatsapp|signal)\b", "contact only via a chat app"),
    (r"(?i)\bcommission[\s-]only\b|\bunpaid\s+(?:internship|position|role|trial|work)\b", "commission-only or unpaid"),
    (r"(?i)\b(?:no\s+experience\s+(?:needed|required)).{0,120}(?:\$|€|£)\s?\d{3,}\s*(?:/|per)\s*(?:day|hour)", "high pay for no experience"),
    (r"(?i)\b(?:reshipping|package\s+forwarding|parcel\s+forwarding)\b", "parcel reshipping"),
)
GERMAN = {"und", "der", "die", "das", "mit", "für", "wir", "sie", "ist", "du", "ihr", "eine", "einen", "bei", "auf", "zu", "von", "oder"}
ENGLISH = {"and", "the", "with", "for", "you", "we", "are", "our", "to", "of", "in", "a", "is", "will", "your", "or"}
EDUCATION = re.compile(r"(?i)\b(?:bachelor(?:'s)?|master(?:'s)?|ph\.?d|doctorate|mba|b\.?\s?tech|m\.?\s?tech|b\.?sc|m\.?sc|b\.?e\.|"
                       r"degree\s+in\s+[a-z ]{3,40}|university\s+degree|studium|abgeschlossenes\s+studium)\b")
SALARY = re.compile(r"(?i)(?P<cur>[$€£₹]|usd|eur|gbp|inr)\s?(?P<low>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?)\s?(?P<k1>k)?"
                    r"(?:\s*(?:-|–|to)\s*(?:[$€£₹]|usd|eur|gbp|inr)?\s?(?P<high>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?)\s?(?P<k2>k)?)?"
                    r"\s*(?:per|/|a)?\s*(?P<per>year|yr|annum|month|mo|hour|hr|day)?")
CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "usd": "USD", "eur": "EUR", "gbp": "GBP", "inr": "INR"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def sections(text: str) -> dict[str, str]:
    """{'required', 'nice', 'other', 'unsorted'}: the posting's lines, by the heading they sit under."""
    found = {"required": [], "nice": [], "other": [], "unsorted": []}
    current = "unsorted"
    for line in str(text or "").splitlines():
        stripped = line.strip().rstrip(":")
        if len(stripped) <= 70 and not stripped.startswith("•"):
            if REQUIRED.match(stripped):
                current = "required"
                continue
            if NICE.match(stripped):
                current = "nice"
                continue
            if OTHER.match(stripped):
                current = "other"
                continue
        target = "nice" if current in ("required", "unsorted") and PLUS_LINE.search(line) else current
        found[target].append(line)
    return {key: "\n".join(value) for key, value in found.items()}


def written_in(text: str) -> str:
    tokens = re.findall(r"[a-zäöüß]+", str(text or "").lower())
    german, english = sum(token in GERMAN for token in tokens), sum(token in ENGLISH for token in tokens)
    return "German" if german > max(8, english * 1.2) else "English" if english >= 5 else "unknown"


def salary(row: dict[str, Any], text: str) -> dict[str, Any] | None:
    if row.get("salary_min") or row.get("salary_max"):
        return {"min": row.get("salary_min") or row.get("salary_max"), "max": row.get("salary_max") or row.get("salary_min"),
                "currency": row.get("currency") or "", "period": row.get("salary_period") or "year", "from": "listing"}
    for match in SALARY.finditer(text or ""):
        def amount(value, thousand):
            number = float(value.replace(",", "").replace(".", "") if re.search(r"[,.]\d{3}\b", value) else value)
            return number * 1000 if thousand else number
        low = amount(match["low"], match["k1"] or match["k2"])
        high = amount(match["high"], match["k2"]) if match["high"] else low
        period = {"yr": "year", "annum": "year", "mo": "month", "hr": "hour"}.get((match["per"] or "").lower(), (match["per"] or "").lower())
        if not period:
            period = "year" if low >= 10000 else "hour" if low < 300 else "month"
        if low >= 10 and (period != "year" or low >= 5000):
            return {"min": low, "max": max(low, high), "currency": CURRENCY[match["cur"].lower()], "period": period, "from": "text"}
    return None


#: Visa and work-authorization programmes a US posting may name. Each maps a phrase to a short key.
_VISA_TERMS = (
    ("stem_opt", r"stem[\s-]?opt"),
    ("opt", r"\bf[-\s]?1\s+opt\b|optional\s+practical\s+training|\bopt\b(?=[^.\n]{0,40}(?:visa|f[-\s]?1|sponsor|work\s+authori))"),
    ("cpt", r"\bcpt\b|curricular\s+practical\s+training"),
    ("h1b", r"h[-\s]?1[-\s]?b"),
    ("green_card", r"green\s*card|permanent\s+residen|\bperm\b|i-?140|labou?r\s+certificat"),
    ("tn", r"\btn\s+(?:visa|status)\b|\btn[-\s]?1\b"),
    ("e3", r"\be-?3\s+visa\b"),
    ("o1", r"\bo-?1\s+visa\b"),
)
_NO_SPONSOR = re.compile(r"\b(?:no|not|unable\s+to|cannot|can['’]?t|won['’]?t|do\s+not|does\s+not|will\s+not)\s+"
                         r"(?:currently\s+)?(?:be\s+)?(?:(?:able|willing)\s+to\s+)?(?:provide\s+|offer\s+)?(?:visa\s+|employment\s+|work\s+)?sponsor|"
                         r"\bmust\s+be\s+(?:legally\s+)?authori[sz]ed\s+to\s+work[^.\n]{0,100}without\s+(?:visa\s+)?sponsor|"
                         r"\bno\s+(?:visa\s+)?sponsorship\b|\bsponsorship\s+(?:is\s+)?(?:not\s+(?:available|offered|provided)|unavailable)", re.IGNORECASE)
_YES_SPONSOR = re.compile(r"(?i)\b(?:visa\s+)?sponsorship\b[^.\n]{0,40}\b(?:available|offered|provided|possible)|"
                          r"\bwe\s+(?:can\s+|will\s+|do\s+|are\s+able\s+to\s+)?sponsor(?:ship)?\b|"
                          r"\bwe\s+(?:offer|provide)\s+(?:visa\s+)?sponsor|\bsponsor(?:ship)?\s+(?:is\s+)?"
                          r"(?:available|offered|provided)|\bwilling\s+to\s+sponsor|\bopen\s+to\s+sponsor")
_CITIZEN_ONLY = re.compile(r"(?i)\b(?:us|u\.s\.|united\s+states)\s+citizens?(?:hip)?\s+(?:required|only|is\s+required|is\s+a\s+must)|"
                           r"must\s+be\s+(?:a\s+|an\s+)?(?:us\s+|u\.s\.\s+|united\s+states\s+)citizens?\b")
_CLEARANCE = re.compile(r"(?i)\b(?:security\s+clearance|ts/sci|top\s+secret|secret\s+clearance|active\s+clearance|"
                        r"(?:dod|government)\s+clearance|public\s+trust\s+clearance)\b")


def authorization(text: str) -> tuple[str, list[str], bool, bool]:
    """Work-authorization reading of a posting: (sponsorship, visa types named, citizenship required, clearance required).

    sponsorship is "offered", "not offered", "not stated" or "conflicting". Opposing statements need manual
    clarification rather than a blanket promise or exclusion. visa_types are the programmes it names
    (h1b, green_card, opt, stem_opt, ...) whether to offer or to exclude — the match step reads them with
    sponsorship. Detection is deliberately cautious: a bare "opt" only counts near visa/F-1/sponsor words."""
    text = re.sub(r"\b(?:citizenship|security\s+clearance)\s+(?:is\s+)?not\s+required\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bno\s+(?:(?:u\.?s\.?|united\s+states)\s+)?citizenship\s+(?:is\s+)?required\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bno\s+(?:visa\s+)?sponsorship\s+(?:is\s+)?required\b", "", text, flags=re.IGNORECASE)
    no_sponsor = bool(_NO_SPONSOR.search(text))
    yes_sponsor = bool(_YES_SPONSOR.search(_NO_SPONSOR.sub(" ", text)))
    sponsorship = "conflicting" if no_sponsor and yes_sponsor else "not offered" if no_sponsor else "offered" if yes_sponsor else "not stated"
    visa_types = [key for key, pattern in _VISA_TERMS if re.search(pattern, text, re.IGNORECASE)]
    if "stem_opt" in visa_types and "opt" not in visa_types:
        visa_types.append("opt")  # STEM OPT implies OPT
    citizen = bool(_CITIZEN_ONLY.search(text)) and not re.search(
        r"(?i)citizen(?:ship|s)?[^.\n]{0,35}(?:\bor\b|/)[^.\n]{0,35}(?:permanent\s+resident|green\s+card)", text)
    return sponsorship, sorted(set(visa_types)), bool(citizen), bool(_CLEARANCE.search(text))


def excludes_future_sponsorship(text: str) -> bool:
    """A restriction statement, rather than an application's question about the applicant."""
    pattern = r"(?i)(?:without[^.\n]{0,50}sponsor[^.\n]{0,80}(?:future|ever)|(?:not|no|cannot|unable)[^.\n]{0,60}sponsor[^.\n]{0,80}(?:future|ever))"
    return any(not clause.rstrip().endswith("?") and re.search(pattern, clause)
               for clause in re.split(r"(?<=[.!?])\s+|\n", text))


def understand(row: dict[str, Any]) -> dict[str, Any]:
    """Everything HAYAT reads from one posting; every item can be traced to the text."""
    text = row.get("description", "")
    title = row.get("title", "")
    parts = sections(text)
    required, nice = skills_in(parts["required"]), skills_in(parts["nice"])
    mentioned = skills_in("\n".join([title, parts["unsorted"], parts["other"], " ".join(row.get("tags", []))]))
    spoken = {name for name in (required | nice | mentioned) if name in LANGUAGES}
    skills = {
        "required": sorted(name for name in required if name not in LANGUAGES),
        "nice": sorted(name for name in nice if name not in required and name not in LANGUAGES),
        "mentioned": sorted(name for name in mentioned if name not in required and name not in nice and name not in LANGUAGES),
    }
    years = [(int(match[1]), int(match[2]) if match[2] else None) for match in YEARS.finditer(text) if int(match[1]) <= 25]
    level = next((name for name, pattern in LEVELS if re.search(pattern, title, re.IGNORECASE)), "")
    if not level and row.get("seniority"):
        level = next((name for name, pattern in LEVELS if re.search(pattern, row["seniority"], re.IGNORECASE)), "")
    blob = f"{title}\n{row.get('location', '')}\n{text}"
    if row.get("remote") is True or re.search(r"(?i)\b(?:fully\s+remote|100%\s+remote|remote[\s-]first|work\s+from\s+anywhere)\b", blob):
        mode = "remote"
    elif re.search(r"(?i)\bhybrid\b", blob):
        mode = "hybrid"
    elif re.search(r"(?i)\bremote\b", f"{title} {row.get('location', '')}"):
        mode = "remote"
    elif re.search(r"(?i)\b(?:on[\s-]?site|in[\s-]office|in[\s-]person|vor\s+ort)\b", blob) or row.get("remote") is False:
        mode = "on-site"
    else:
        mode = "unknown"
    regions = []
    location_text = f"{row.get('location', '')}"
    for name, pattern in REGIONS.items():
        rule = (rf"(?i)(?:\b(?:{pattern})\b\s*(?:only|based|residents?|citizens?)\b|\b(?:based|located|reside|residing|live|living)\s+in\s+"
                rf"(?:the\s+)?(?:{pattern})\b|\b(?:{pattern})\b\s*(?:time\s*zones?|timezone|hours)|authori[sz]ed\s+to\s+work\s+in\s+(?:the\s+)?(?:{pattern})\b)")
        if re.search(rule, text) or re.search(rf"(?i)\b(?:{pattern})\b", location_text):
            regions.append(name)
    sponsorship, visa_types, citizenship_required, clearance_required = authorization(text)
    language = written_in(text)
    needed_languages = sorted({name for name in spoken if re.search(
        rf"(?i)(?:fluent|fluency|native|business[\s-]level|proficient|excellent|strong|good|verhandlungssicher|fließend|C1|C2|B2)"
        rf"[^.\n]{{0,30}}\b{name}\b|\b{name}\b[^.\n]{{0,30}}(?:required|is\s+a\s+must|essential|mandatory|fluency|C1|C2|B2|skills\s+required)",
        text)} | ({"German"} if language == "German" else set()))
    warnings = sorted({reason for pattern, reason in WARNINGS if re.search(pattern, text)})
    emails = sorted({address for address in EMAIL.findall(text) if not re.search(r"(?i)noreply|no-reply|example\.", address)})
    pay = salary(row, text)
    top = (skills["required"] or skills["mentioned"])[:6]
    summary = (f"{title or 'A role'} at {row.get('company') or 'an unnamed company'}"
               + (f", {level}" if level else "") + (f", {mode}" if mode != "unknown" else "")
               + (f" ({row['location']})" if row.get("location") else "") + "."
               + (f" Asks for {min(low for low, _high in years)}+ years." if years else "")
               + (f" Key skills: {', '.join(top)}." if top else "")
               + (f" Needs {', '.join(needed_languages)}." if needed_languages else "")
               + (f" Pay {pay_text(pay)}." if pay else ""))
    return {"skills": skills, "categories": sorted({CATEGORY.get(name, "Other") for name in skills["required"] + skills["mentioned"]}),
            "years_min": min((low for low, _high in years), default=None), "level": level, "mode": mode, "regions": regions,
            "sponsorship": sponsorship, "visa_types": visa_types, "citizenship_required": citizenship_required,
            "future_sponsorship_excluded": excludes_future_sponsorship(text),
            "clearance_required": clearance_required, "written_in": language, "languages_needed": needed_languages,
            "education": sorted({match.group(0).strip() for match in EDUCATION.finditer(text)})[:4], "salary": pay,
            "warnings": warnings, "apply_emails": emails[:3], "summary": summary}


def pay_text(pay: dict[str, Any] | None) -> str:
    if not pay:
        return "not stated"
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(pay["currency"], pay["currency"] + " " if pay["currency"] else "")
    low, high = pay["min"], pay["max"]
    show = lambda value: f"{value / 1000:g}k" if value >= 10000 else f"{value:g}"
    return f"{symbol}{show(low)}" + (f"–{symbol}{show(high)}" if high and high != low else "") + f" per {pay['period']}"
