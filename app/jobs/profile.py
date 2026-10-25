"""The owner's job profile: what their CV says, read with rules, plus their own preferences.

From a CV (PDF, Word, text or Markdown) HAYAT reads:
* contact lines (name, email, phone, LinkedIn, GitHub, website);
* skills, each with how often the CV names it and whether it sits in a Skills section;
* spoken languages;
* experience: every date range ("Jan 2019 – Present", "2018-2021", "03/2020 - 06/2022"), merged so
  overlapping jobs are not counted twice, giving total years;
* job titles held and education;
* a level from the years (junior under 2, mid 2–5, senior 5–9, lead 9+).

Preferences are the owner's: target titles, search terms, places, remote only, minimum pay, level,
job types, companies and words to avoid, companies to follow, and whether HAYAT may prepare
applications for strong matches on its own. Nothing here is guessed into the profile: a skill is in
it because the CV names it or the owner added it.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.jobs.text import CATEGORY, LANGUAGES, skills_in

MONTHS = {name: index for index, names in enumerate((("jan", "january", "jän", "januar"), ("feb", "february", "februar"),
                                                    ("mar", "march", "mär", "märz"), ("apr", "april"), ("may", "mai"),
                                                    ("jun", "june", "juni"), ("jul", "july", "juli"), ("aug", "august"),
                                                    ("sep", "sept", "september"), ("oct", "october", "okt", "oktober"),
                                                    ("nov", "november"), ("dec", "december", "dez", "dezember")), 1) for name in names}
_MONTH = r"(?:" + "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True)) + r")\.?"
_POINT = rf"(?:{_MONTH}\s*,?\s*(?:19|20)\d{{2}}|(?:0?[1-9]|1[0-2])\s*[/.-]\s*(?:19|20)\d{{2}}|(?:19|20)\d{{2}}\s*[/.-]\s*(?:0?[1-9]|1[0-2])(?!\d)|(?:19|20)\d{{2}})"
RANGE = re.compile(rf"(?i)(?P<start>{_POINT})\s*(?:-|–|—|to|until|bis)\s*(?P<end>{_POINT}|present|current|now|today|ongoing|heute|date)")
ROLE = re.compile(r"(?i)\b(?:engineer|developer|programmer|manager|analyst|designer|consultant|specialist|accountant|teacher|lecturer|"
                  r"scientist|architect|administrator|officer|executive|coordinator|assistant|intern|lead|director|associate|"
                  r"technician|representative|agent|writer|editor|marketer|recruiter|founder|freelancer|tester|operator|supervisor)\b")
DEGREE = re.compile(r"(?i)\b(?:bachelor(?:'s)?|master(?:'s)?|ph\.?d|doctorate|mba|b\.?\s?tech|m\.?\s?tech|b\.?\s?sc|m\.?\s?sc|b\.?\s?com|"
                    r"m\.?\s?com|b\.?\s?a\b|m\.?\s?a\b|b\.?\s?e\b|bca|mca|diploma|high\s+school|intermediate|ssc|hsc)\b")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?){2,4}\d{3,5}(?!\d)")
LINK = re.compile(r"(?i)\b(?:https?://)?(?:www\.)?(?:linkedin\.com/in/[\w-]+|github\.com/[\w-]+|[\w-]+\.(?:dev|io|me|com|net|org)/?[\w/-]*)")
SKILLS_HEADING = re.compile(r"(?i)^\s*(?:technical\s+|core\s+|key\s+)?(?:skills|competenc(?:ies|e)|technologies|tech\s+stack|tools)\b")
LANGUAGE_HEADING = re.compile(r"(?i)^\s*languages?\b")
#: Skill groups that ordinary prose names by accident ("sales and inventory", "Metro Logistics"): from outside a
#: Skills section they count only when named at least twice.
GENERIC = frozenset({"Sales & business", "Support & people", "Finance & admin", "Marketing & content"})


def role_only(line: str) -> str:
    """'Data Analyst, Brightcart Retail — Mar 2021 – Present' → 'Data Analyst'."""
    value = RANGE.sub("", line)
    value = re.split(r"\s*(?:,|\||—|–|\s-\s|\s@\s|\sat\s|\(|\bbei\b)\s*", value, maxsplit=1)[0]
    return value.strip(" -–—|,")
#: A role you have held implies its core skill even when the CV never spells it out ("Data Analyst" → Data analysis).
IMPLIED = ((r"data\s+analyst", "Data analysis"), (r"data\s+scientist", "Data science"), (r"data\s+engineer", "Data engineering"),
           (r"business\s+analyst", "Business analysis"), (r"accountant", "Accounting"), (r"bookkeeper", "Bookkeeping"),
           (r"project\s+manager", "Project management"), (r"product\s+manager", "Product management"), (r"devops", "DevOps"),
           (r"\bqa\b|tester|quality\s+assurance", "QA"), (r"teacher|lecturer|tutor", "Teaching"), (r"recruiter", "Recruiting"),
           (r"customer\s+(?:support|service|success)", "Customer service"), (r"graphic\s+designer", "Graphic design"),
           (r"ux\s+designer|ui/ux", "UX"), (r"sales\s+(?:executive|representative|manager|associate)|account\s+executive", "Sales"),
           (r"marketing\s+(?:manager|executive|specialist)|digital\s+marketer", "Digital marketing"), (r"copywriter", "Copywriting"),
           (r"technical\s+support|help\s*desk", "Technical support"))
#: The owner's own work authorization. needs_now = an employer must sponsor from the start or to keep them;
#: needs_future = authorized now but will need sponsorship later (OPT/CPT); neither for a citizen or green card.
WORK_STATUSES = {
    "citizen": {"label": "US citizen", "needs_now": False, "needs_future": False, "is_citizen": True},
    "permanent_resident": {"label": "green card / permanent resident", "needs_now": False, "needs_future": False, "is_citizen": False},
    "h1b": {"label": "on H-1B (needs transfer)", "needs_now": True, "needs_future": True, "is_citizen": False},
    "stem_opt": {"label": "on STEM OPT", "needs_now": False, "needs_future": True, "is_citizen": False},
    "opt": {"label": "on OPT", "needs_now": False, "needs_future": True, "is_citizen": False},
    "cpt": {"label": "on CPT", "needs_now": False, "needs_future": True, "is_citizen": False},
    "tn": {"label": "TN visa", "needs_now": True, "needs_future": False, "is_citizen": False},
    "e3": {"label": "E-3 visa", "needs_now": True, "needs_future": False, "is_citizen": False},
    "needs_sponsorship": {"label": "needs visa sponsorship", "needs_now": True, "needs_future": True, "is_citizen": False},
}
DEFAULT_PREFERENCES = {
    "target_titles": [], "search_terms": [], "locations": [], "countries_ok": [], "remote_only": False,
    "willing_to_relocate": False, "min_salary": None,
    "salary_currency": "USD", "level": "", "job_types": [], "exclude_companies": [], "exclude_words": [],
    "sources": ["remoteok", "jobicy", "himalayas", "workingnomads", "themuse", "arbeitnow"], "companies": [],
    "auto_prepare": False, "notify_score": 70, "notice_period": "", "work_authorization": "", "work_status": "",
    "salary_expectation": "",
}


def read_document(path: str | Path) -> str:
    """Text of a CV file: PDF, Word (.docx), text or Markdown."""
    file = Path(path).expanduser()
    if not file.is_file():
        raise FileNotFoundError(f"No file at {file}.")
    if file.stat().st_size > 15 * 1024 * 1024:
        raise ValueError("That file is over 15 MB; a CV is usually under 1 MB.")
    suffix = file.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(file)).pages[:12])
    elif suffix == ".docx":
        import docx
        document = docx.Document(str(file))
        rows = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            rows += [" | ".join(cell.text for cell in row.cells) for row in table.rows]
        text = "\n".join(rows)
    elif suffix in (".txt", ".md", ".markdown", ".text"):
        text = file.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError("Give the CV as a PDF, Word (.docx), text or Markdown file.")
    if len(text.strip()) < 80:
        raise ValueError("Almost no text came out of that file. If it is a scanned image, save it as a text PDF or Word file first.")
    return text


def _month(point: str, today: date, end: bool) -> tuple[int, int]:
    value = point.strip().lower().rstrip(".")
    if value in ("present", "current", "now", "today", "ongoing", "heute", "date"):
        return today.year, today.month
    if match := re.match(rf"({_MONTH})\s*,?\s*((?:19|20)\d{{2}})", value):
        return int(match[2]), MONTHS[match[1].rstrip(".")]
    if match := re.match(r"(\d{1,2})\s*[/.-]\s*((?:19|20)\d{2})", value):
        return int(match[2]), int(match[1])
    if match := re.match(r"((?:19|20)\d{2})\s*[/.-]\s*(\d{1,2})", value):
        return int(match[1]), int(match[2])
    year = int(re.search(r"(?:19|20)\d{2}", value).group(0))
    return year, 12 if end else 1


def experience(text: str, today: date | None = None) -> dict[str, Any]:
    """Date ranges in the CV merged into total years, so overlapping roles count once. Education ranges are left out."""
    today = today or datetime.now(UTC).date()
    spans = []
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        nearby = " ".join(lines[max(0, index - 1):index + 2])
        for match in RANGE.finditer(line):
            if DEGREE.search(nearby) and not ROLE.search(nearby):
                continue  # a degree's years are not work experience
            start, end = _month(match["start"], today, False), _month(match["end"], today, True)
            first, last = start[0] * 12 + start[1] - 1, end[0] * 12 + end[1]
            if 0 < last - first <= 50 * 12 and start[0] <= today.year:
                spans.append((first, min(last, today.year * 12 + today.month)))
    spans.sort()
    merged: list[list[int]] = []
    for first, last in spans:
        if merged and first <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    months = sum(last - first for first, last in merged)
    return {"years": round(months / 12, 1) if merged else None, "ranges": len(spans)}


def level_for(years: float | None) -> str:
    if years is None:
        return ""
    return "junior" if years < 2 else "mid" if years < 5 else "senior" if years < 9 else "lead"


def understand_cv(text: str, today: date | None = None) -> dict[str, Any]:
    lines = [" ".join(line.split()) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    head = lines[:12]
    name = next((line for line in head if re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}", line)
                 and not ROLE.search(line) and not SKILLS_HEADING.match(line)), "")
    in_skills, in_languages, listed, languages_listed = False, False, [], []
    experience_lines = []
    for index, line in enumerate(lines):
        if len(line) <= 40 and SKILLS_HEADING.match(line):
            in_skills, in_languages = True, False
            continue
        if len(line) <= 40 and LANGUAGE_HEADING.match(line):
            in_skills, in_languages = False, True
            continue
        if len(line) <= 40 and line.isupper() or (len(line) <= 30 and line.endswith(":")):
            in_skills = in_languages = False
        if in_skills:
            listed.append(line)
        if in_languages:
            languages_listed.append(line)
        # What the owner did, in their own words: the lines duties are matched against.
        body = line.lstrip("-•*–· ").strip()
        if (not in_skills and not in_languages and index >= 3 and 20 <= len(body) <= 300 and not EMAIL.search(body)
                and not DEGREE.search(body) and not SKILLS_HEADING.match(body)):
            experience_lines.append(body)
    counts = skills_in("\n".join(lines))
    listed_skills = skills_in("\n".join(listed))
    skills = [{"name": skill, "category": CATEGORY.get(skill, "Other"), "mentions": count, "listed": skill in listed_skills}
              for skill, count in sorted(counts.items(), key=lambda item: (-(item[0] in listed_skills), -item[1], item[0]))
              if skill not in LANGUAGES and (skill in listed_skills or not listed or CATEGORY.get(skill) not in GENERIC or count >= 2)]
    spoken = sorted({name for name in skills_in("\n".join(languages_listed)) if name in LANGUAGES}
                    | {name for name in counts if name in LANGUAGES and re.search(
                        rf"(?i)\b{name}\b[^\n]{{0,25}}(?:native|fluent|professional|C1|C2|B2|mother\s+tongue)", text)})
    work = experience(text, today)
    titles = []
    for line in lines:
        if ROLE.search(line) and len(line) <= 90 and not EMAIL.search(line) and (RANGE.search(line) or len(line.split()) <= 7):
            title = role_only(line)
            if title and ROLE.search(title) and title not in titles and len(title.split()) <= 6:
                titles.append(title)
    education = list(dict.fromkeys(line[:120] for line in lines if DEGREE.search(line)))[:5]
    without_emails = EMAIL.sub(" ", " ".join(head + lines[-8:]))  # "name@example.org" is not a website
    links = list(dict.fromkeys(match.group(0) for match in LINK.finditer(without_emails)))
    phones = [match.group(0).strip() for match in PHONE.finditer(" ".join(head)) if len(re.sub(r"\D", "", match.group(0))) >= 9]
    return {"name": name, "email": next(iter(EMAIL.findall(" ".join(head + lines))), ""), "phone": phones[0] if phones else "",
            "links": links[:5], "skills": skills, "languages": spoken, "years_experience": work["years"],
            "date_ranges": work["ranges"], "level": level_for(work["years"]), "titles": [title for title in titles if title][:8],
            "education": education, "lines": experience_lines[:80], "words": len(" ".join(lines).split())}


class Profile:
    """data/jobs/profile.json: what the CV says plus the owner's preferences; the owner can change both."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        # On Windows an open read handle can prevent the atomic replacement in
        # save(). GUI refreshes and the watcher share this profile instance.
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        data.setdefault("cv", {})
        data["preferences"] = {**DEFAULT_PREFERENCES, **data.get("preferences", {})}
        data.setdefault("added_skills", [])
        data.setdefault("removed_skills", [])
        return data

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return data

    def read_cv(self, path: str | Path) -> dict[str, Any]:
        file = Path(path).expanduser()
        cv = understand_cv(read_document(file))
        data = self.load()
        data["cv"] = {**cv, "file": str(file.resolve()), "read_at": datetime.now(UTC).isoformat(timespec="seconds")}
        preferences = data["preferences"]
        if not preferences["target_titles"] and cv["titles"]:
            preferences["target_titles"] = cv["titles"][:1]
        if not preferences["search_terms"]:
            preferences["search_terms"] = default_terms(data)
        return self.save(data)

    def skills(self, data: dict[str, Any] | None = None) -> list[str]:
        """The owner's skills: from the CV, plus ones added by hand, minus ones removed by hand."""
        data = data or self.load()
        names = [item["name"] for item in data["cv"].get("skills", [])] + data["added_skills"] + implied(data)
        removed = {name.lower() for name in data["removed_skills"]}
        return list(dict.fromkeys(name for name in names if name.lower() not in removed))

    def set_preferences(self, changes: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        unknown = set(changes) - set(DEFAULT_PREFERENCES)
        if unknown:
            raise ValueError("Unknown preference: " + ", ".join(sorted(unknown)) + ". Known: " + ", ".join(DEFAULT_PREFERENCES) + ".")
        for key, value in changes.items():
            default = DEFAULT_PREFERENCES[key]
            if isinstance(default, list):
                value = [str(item).strip() for item in (value if isinstance(value, list) else str(value).split(",")) if str(item).strip()][:30]
            elif isinstance(default, bool):
                value = bool(value) if not isinstance(value, str) else value.strip().lower() in ("1", "true", "yes", "on")
            elif key == "min_salary":
                value = float(value) if value not in (None, "", 0) else None
            elif key == "notify_score":
                value = max(0, min(100, int(value)))
            elif key == "work_status":
                value = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
                value = {"h_1b": "h1b", "h_1_b": "h1b", "stemopt": "stem_opt", "us_citizen": "citizen",
                         "green_card": "permanent_resident"}.get(value, value)
                if value and value not in WORK_STATUSES:
                    raise ValueError(f"Unknown work status {value!r}. Known: " + ", ".join(sorted(WORK_STATUSES)) + ".")
            else:
                value = str(value or "").strip()[:200]
            data["preferences"][key] = value
        if "companies" in changes:
            from app.jobs.sources import BOARD
            bad = [item for item in data["preferences"]["companies"] if not BOARD.match(item.lower())]
            if bad:
                raise ValueError("Follow companies as greenhouse:name, lever:name or ashby:name, e.g. greenhouse:stripe. Not: " + ", ".join(bad))
            data["preferences"]["companies"] = [item.lower() for item in data["preferences"]["companies"]]
        return self.save(data)

    def change_skill(self, skill: str, add: bool) -> dict[str, Any]:
        data = self.load()
        name = " ".join(str(skill or "").split())[:60]
        if not name:
            raise ValueError("Name the skill.")
        known = next(iter(skills_in(name)), None) or name
        if add:
            data["removed_skills"] = [item for item in data["removed_skills"] if item.lower() != known.lower()]
            if known.lower() not in {item.lower() for item in self.skills(data)}:
                data["added_skills"].append(known)
        else:
            data["added_skills"] = [item for item in data["added_skills"] if item.lower() != known.lower()]
            data["removed_skills"].append(known)
        return self.save(data)


def implied(data: dict[str, Any]) -> list[str]:
    """Skills implied by roles the CV shows the owner has held."""
    roles = " | ".join(data["cv"].get("titles", [])).lower()
    return [skill for pattern, skill in IMPLIED if re.search(pattern, roles)]


def default_terms(data: dict[str, Any]) -> list[str]:
    """Search terms when the owner has set none: target titles' main words, then the most-named listed skills."""
    terms = []
    for title in data["preferences"].get("target_titles", [])[:3]:
        core = re.sub(r"(?i)\b(?:senior|junior|sr\.?|jr\.?|lead|principal|staff|intern|trainee|i{1,3}|associate)\b", "", title)
        core = " ".join(core.split())
        if core:
            terms.append(core.lower())
    skills = [item["name"] for item in data["cv"].get("skills", []) if item.get("listed")] or \
             [item["name"] for item in data["cv"].get("skills", [])]
    # Then the title's main word on its own ("analyst"), for wider reach; the score sorts out which fit.
    for title in list(terms):
        head = title.split()[-1] if title.split() else ""
        if len(title.split()) > 1 and len(head) > 3:
            terms.append(head)
    if len(terms) < 2:
        terms += [skill.lower() for skill in skills[:3] if CATEGORY.get(skill) not in GENERIC]
    return list(dict.fromkeys(terms))[:5]
