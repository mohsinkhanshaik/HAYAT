"""Everything about one job, from its own text, the owner's CV, the jobs HAYAT has collected, and the owner's history.

* **The role:** its responsibilities (the duties under "What you'll do", or lines that start with an action
  verb), how success is measured, who you work with, and what the first 90 days would likely hold.
* **How it fits your CV:** every duty matched against your CV's own lines, as aligned, partly aligned or a gap,
  with the CV line that shows it; an alignment percentage.
* **What it could do for you:** skills you would build, the level step, pay against your minimum and against
  similar postings, and how it fits your preferences.
* **The market around it, from HAYAT's own collected postings:** how many similar jobs it has seen recently,
  their pay, the skills they ask most, how many are remote, and the usual next roles. This is a sample of what
  HAYAT searched, not the whole market, and career paths are common patterns, not predictions.
* **Your own history:** what happened with similar roles you applied to or hid.
* **What applicants miss:** instructions in the posting (attach a portfolio, a subject line, a deadline, a
  question to answer), must-have filters, place and language limits, and how old the posting is.
* **Preparation:** likely interview questions from its duties and skills, a study plan for missing skills with
  the official documentation, CV tailoring for this job, and questions to ask them.

Nobody knows who was hired before or what will happen next; HAYAT says so instead of guessing.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.jobs.text import CATEGORY, skills_in, words

ACTION_VERBS = (
    "build", "develop", "design", "lead", "manage", "analyse", "analyze", "create", "own", "work", "collaborate", "support",
    "maintain", "drive", "partner", "deliver", "implement", "write", "prepare", "monitor", "ensure", "identify", "conduct",
    "coordinate", "present", "translate", "define", "improve", "optimise", "optimize", "automate", "report", "track", "help",
    "mentor", "review", "test", "deploy", "research", "communicate", "handle", "respond", "assist", "plan", "execute",
    "establish", "evaluate", "investigate", "troubleshoot", "configure", "operate", "oversee", "negotiate", "sell", "train",
    "teach", "produce", "edit", "model", "forecast", "audit", "reconcile", "process", "answer", "resolve", "set", "shape")
DUTY_HEADING = re.compile(r"(?i)^(?:[#*•\-\s]*)(?:(?:key\s+|main\s+|your\s+|core\s+)?(?:responsibilities|duties|accountabilities)|"
                          r"what\s+you(?:'ll|\s+will)\s+(?:do|be\s+doing|work\s+on)|(?:your|the)\s+(?:role|impact|mission|day[\s-]to[\s-]day)|"
                          r"day[\s-]to[\s-]day|in\s+this\s+role(?:\s+you\s+will)?|you\s+will|the\s+job|aufgaben|deine\s+aufgaben|ihre\s+aufgaben)\b.{0,40}$")
END_HEADING = re.compile(r"(?i)^(?:[#*•\-\s]*)(?:requirements?|qualifications?|what\s+you(?:'ll)?\s+(?:need|bring)|must[\s-]have|nice[\s-]to|"
                         r"preferred|benefits|perks|what\s+we\s+offer|about\s+(?:us|you|the\s+company)|who\s+you\s+are|your\s+profile|"
                         r"compensation|salary|how\s+to\s+apply|ihr\s+profil|dein\s+profil|wir\s+bieten)\b.{0,40}$")
SUCCESS = re.compile(r"(?i)\b(?:success\s+(?:looks|means|is)|you(?:'ll|\s+will)\s+(?:own|be\s+responsible\s+for|be\s+measured)|kpis?|"
                     r"measured\s+by|within\s+(?:your\s+)?(?:first\s+)?(?:\d+|three|six|twelve)\s+(?:days|months)|by\s+the\s+end\s+of\s+your\s+first|"
                     r"in\s+your\s+first\s+(?:\d+|ninety|90)\s+days|targets?|quota|goals?\s+(?:will|include))\b")
PEOPLE = re.compile(r"(?i)\b(?:report(?:ing)?\s+(?:directly\s+)?to|work(?:ing)?\s+(?:closely\s+)?with|partner(?:ing)?\s+with|"
                    r"collaborat(?:e|ing)\s+with|team\s+of\s+\d+|join(?:ing)?\s+(?:a|our)\s+(?:team|squad))\b")
INSTRUCTIONS = (
    (r"(?i)\b(?:include|attach|send|provide|submit|share|link\s+to)\b[^.\n]{0,60}\b(portfolio|cover\s+letter|github|code\s+samples?|writing\s+samples?|"
     r"case\s+stud(?:y|ies)|references|salary\s+expectations?|availability|notice\s+period|video|loom)\b", "include your {0}"),
    (r"(?i)\b(?:in|use)\s+the\s+subject(?:\s+line)?\b[^.\n]{0,80}", "use the subject line the posting asks for"),
    (r"(?i)\b(?:apply\s+by(?=\s+(?:\d|today|tomorrow|midnight|noon|eod|end\b|the\s+end|next\b|"
     r"mon(?:day)?\b|tue(?:sday)?\b|wed(?:nesday)?\b|thu(?:rsday)?\b|fri(?:day)?\b|sat(?:urday)?\b|sun(?:day)?\b|"
     r"jan\w*\b|feb\w*\b|mar(?:ch)?\b|apr\w*\b|may\b|jun\w*\b|jul\w*\b|aug\w*\b|sep\w*\b|oct\w*\b|nov\w*\b|dec\w*\b))"
     r"|deadline|closing\s+date|applications\s+close)\b[^.\n]{0,40}", "note the deadline"),
    (r"(?i)\b(?:answer|respond\s+to)\s+(?:the\s+)?(?:following|these|this)\s+questions?\b", "answer the question(s) the posting asks"),
    (r"(?i)\b(?:mention|use|include)\s+the\s+(?:word|phrase|code)\b[^.\n]{0,40}", "include the word or phrase it asks for (a test of reading)"),
    (r"(?i)\bapplications?\s+(?:without|that\s+do\s+not\s+include|missing)\b[^.\n]{0,80}\b(?:will\s+not|won't)\s+be\s+(?:considered|reviewed)",
     "applications missing what it asks for are not considered"),
    (r"(?i)\b(?:only\s+)?applications\s+in\s+(english|german|french|spanish)\b", "apply in {0}"),
    (r"(?i)\bno\s+(?:recruiters|agencies)\b", "it does not want agencies"),
)
#: Official documentation, the honest first stop for a missing skill.
DOCS = {
    "Python": "https://docs.python.org/3/tutorial/", "SQL": "https://www.postgresql.org/docs/current/tutorial.html",
    "PostgreSQL": "https://www.postgresql.org/docs/current/tutorial.html", "Power BI": "https://learn.microsoft.com/en-us/power-bi/",
    "Excel": "https://support.microsoft.com/en-us/excel", "Tableau": "https://help.tableau.com/current/guides/get-started-tutorial/en-us/",
    "Looker": "https://cloud.google.com/looker/docs", "Snowflake": "https://docs.snowflake.com/en/user-guide-getting-started",
    "dbt": "https://docs.getdbt.com/docs/introduction", "Airflow": "https://airflow.apache.org/docs/",
    "Pandas": "https://pandas.pydata.org/docs/getting_started/", "NumPy": "https://numpy.org/doc/stable/user/absolute_beginners.html",
    "scikit-learn": "https://scikit-learn.org/stable/getting_started.html", "PyTorch": "https://pytorch.org/tutorials/",
    "TensorFlow": "https://www.tensorflow.org/tutorials", "Spark": "https://spark.apache.org/docs/latest/quick-start.html",
    "Kafka": "https://kafka.apache.org/quickstart", "Docker": "https://docs.docker.com/get-started/",
    "Kubernetes": "https://kubernetes.io/docs/tutorials/", "Terraform": "https://developer.hashicorp.com/terraform/tutorials",
    "AWS": "https://docs.aws.amazon.com/", "Azure": "https://learn.microsoft.com/en-us/azure/", "GCP": "https://cloud.google.com/docs",
    "Git": "https://git-scm.com/doc", "GitHub Actions": "https://docs.github.com/en/actions", "Linux": "https://linuxjourney.com/",
    "JavaScript": "https://developer.mozilla.org/en-US/docs/Learn/JavaScript", "TypeScript": "https://www.typescriptlang.org/docs/",
    "HTML": "https://developer.mozilla.org/en-US/docs/Learn/HTML", "CSS": "https://developer.mozilla.org/en-US/docs/Learn/CSS",
    "React": "https://react.dev/learn", "Node.js": "https://nodejs.org/en/learn", "Django": "https://docs.djangoproject.com/en/stable/intro/",
    "Flask": "https://flask.palletsprojects.com/", "FastAPI": "https://fastapi.tiangolo.com/tutorial/", "Java": "https://dev.java/learn/",
    "Go": "https://go.dev/learn/", "Rust": "https://doc.rust-lang.org/book/", "C#": "https://learn.microsoft.com/en-us/dotnet/csharp/",
    "Figma": "https://help.figma.com/", "Google Analytics": "https://support.google.com/analytics",
    "BigQuery": "https://cloud.google.com/bigquery/docs", "Databricks": "https://docs.databricks.com/",
    "Statistics": "https://www.khanacademy.org/math/statistics-probability", "Machine learning": "https://developers.google.com/machine-learning/crash-course",
    "Selenium": "https://www.selenium.dev/documentation/", "Playwright": "https://playwright.dev/docs/intro", "Jira": "https://support.atlassian.com/jira-software-cloud/",
    "Salesforce": "https://trailhead.salesforce.com/", "HubSpot": "https://academy.hubspot.com/",
}
PRACTICE = {
    "Programming": "write a small script or tool with it and put it on GitHub",
    "Web": "build a one-page app with it and deploy it",
    "Mobile": "build a two-screen app with it",
    "Data & AI": "analyse a public dataset with it and write up three findings",
    "Databases": "model a small schema and answer five business questions with queries",
    "Cloud & DevOps": "deploy a small app with it on a free tier, then tear it down",
    "Security": "work through a free lab on it and write what you learned",
    "Testing & quality": "add tests with it to one of your own projects",
    "Product & design": "redesign one screen of an app you use and explain the choices",
    "Marketing & content": "plan and measure one small campaign or piece of content",
    "Sales & business": "write a one-page plan using it for a company you know",
    "Finance & admin": "do one month of sample books or a model with it",
}
QUESTION_TEMPLATES = {
    "Programming": "How would you structure a small {0} project so others can maintain it? What is a bug you fixed in {0}?",
    "Web": "How would you build and test a feature with {0}? How do you keep it fast?",
    "Mobile": "How do you handle state and offline use in {0}?",
    "Data & AI": "How do you check data quality before trusting an analysis in {0}? Walk through a result you produced with it.",
    "Databases": "How would you find and fix a slow query in {0}?",
    "Cloud & DevOps": "How would you deploy and monitor a service with {0}? What went wrong once and how did you recover?",
    "Security": "How would you investigate a suspected incident using {0}?",
    "Testing & quality": "How do you decide what to test with {0}, and what not to?",
    "Product & design": "Walk through a design decision you made with {0} and how you tested it with users.",
    "Marketing & content": "How did you measure whether your {0} work was working?",
    "Sales & business": "Tell me about a deal or project where {0} made the difference.",
    "Finance & admin": "How do you make sure your {0} work is accurate and on time?",
}
CAREER = (
    (r"data\s+analyst|business\s+analyst|bi\s+analyst|reporting\s+analyst|analytics", ["Senior Data Analyst", "Analytics Engineer", "Data Scientist", "Analytics Manager"]),
    (r"data\s+engineer", ["Senior Data Engineer", "Data Platform Lead", "Data Architect"]),
    (r"data\s+scientist|machine\s+learning|ml\s+engineer|ai\s+engineer", ["Senior Data Scientist", "ML Engineer", "Lead Data Scientist", "AI Engineering Manager"]),
    (r"devops|site\s+reliability|sre|platform\s+engineer|cloud\s+engineer", ["Senior SRE", "Platform Lead", "Infrastructure Architect"]),
    (r"qa|test|quality\s+assurance", ["Senior QA / SDET", "QA Lead", "QA Manager"]),
    (r"engineer|developer|programmer", ["Senior Engineer", "Staff / Lead Engineer", "Engineering Manager", "Architect"]),
    (r"product\s+manager|product\s+owner", ["Senior Product Manager", "Group Product Manager", "Director of Product"]),
    (r"designer|ux|ui", ["Senior Designer", "Lead Designer", "Design Manager"]),
    (r"project\s+manager|program\s+manager|scrum", ["Senior Project Manager", "Program Manager", "PMO Lead"]),
    (r"marketing|seo|content|social\s+media|growth", ["Senior Marketing Manager", "Marketing Lead", "Head of Marketing"]),
    (r"sales|account\s+executive|business\s+development", ["Senior Account Executive", "Sales Manager", "Head of Sales"]),
    (r"customer\s+(?:success|support|service)|support", ["Team Lead", "Customer Success Manager", "Support Operations Lead"]),
    (r"accountant|finance|bookkeep|controller|audit", ["Senior Accountant", "Finance Manager", "Financial Controller"]),
    (r"recruit|talent|hr\b|human\s+resources|people", ["Senior Recruiter", "Talent Acquisition Lead", "HR Business Partner"]),
    (r"teacher|tutor|lecturer|instructor", ["Senior Teacher", "Head of Department", "Curriculum Lead"]),
)
_STOP = frozenset("a an and the of to for in on with as at by from or our your you we will be is are this that it its their they "
                  "who what how all any into across using use within new key strong good great other more such per".split())
_SAME = {"analyze": "analys", "analyse": "analys", "analysis": "analys", "analyses": "analys", "analytic": "analys", "analytics": "analys",
         "develop": "build", "create": "build", "built": "build", "building": "build", "dashboards": "dashboard", "reports": "report",
         "reporting": "report", "automate": "automat", "automated": "automat", "automating": "automat", "automation": "automat",
         "stakeholders": "stakeholder", "lead": "lead", "led": "lead", "manage": "lead", "managed": "lead", "owned": "lead", "own": "lead",
         "pipelines": "pipeline", "models": "model", "modeling": "model", "modelling": "model", "customers": "customer", "clients": "customer",
         "client": "customer", "visualisation": "visual", "visualization": "visual", "visualize": "visual", "insights": "insight",
         "monitor": "track", "monitoring": "track", "tracking": "track", "metrics": "metric", "kpi": "metric", "kpis": "metric",
         "communicate": "present", "presentation": "present", "presenting": "present", "findings": "insight", "decisions": "decision",
         "collaborate": "partner", "collaboration": "partner", "partners": "partner", "cross-functional": "partner"}


def _stem(word: str) -> str:
    word = _SAME.get(word, word)
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return _SAME.get(word[:-len(suffix)], word[:-len(suffix)])
    return word


def tokens(text: str) -> set[str]:
    return {_stem(word) for word in words(text) if word not in _STOP and len(word) > 2 and not word.isdigit()}


def responsibilities(text: str, limit: int = 12) -> list[str]:
    """The posting's duties: the lines under a duties heading, else lines that start with an action verb."""
    lines = [line.strip(" •-*\t") for line in str(text or "").splitlines()]
    found, inside = [], False
    for line in lines:
        if not line:
            continue
        if len(line) <= 70 and DUTY_HEADING.match(line.rstrip(":")):
            inside = True
            continue
        if len(line) <= 70 and END_HEADING.match(line.rstrip(":")):
            inside = False
            continue
        if inside and 15 <= len(line) <= 300:
            found.append(line)
    if not found:
        for line in lines:
            first = (words(line) or [""])[0]
            if first in ACTION_VERBS and 20 <= len(line) <= 300:
                found.append(line)
    # Sub-headings inside the duties ("Data Quality & Validation", "Process Improvement & Documentation") are not duties:
    # short, no full stop, and either no leading verb or written as a title.
    def heading(line: str) -> bool:
        if len(line.split()) > 6 or line.endswith((".", ";")) or re.search(r"\b(?:you|we)\b", line, re.I):
            return False
        titled = "&" in line or all(word[0].isupper() for word in re.findall(r"[A-Za-z]{4,}", line))
        return titled or (words(line) or [""])[0] not in ACTION_VERBS
    # How success is measured and who you report to are read separately, not as duties.
    found = [line for line in found if not heading(line) and not SUCCESS.search(line) and not re.match(r"(?i)you\s+will\s+report", line)]
    clean = []
    for line in found:
        sentence = re.split(r"(?<=[.;])\s+", line)[0].rstrip(".;")
        if sentence and sentence not in clean:
            clean.append(sentence)
    return clean[:limit]


def alignment(duties: list[str], cv_lines: list[str], skills: list[str]) -> dict[str, Any]:
    """Each duty against the CV's own lines: aligned, partly aligned or a gap, with the line that shows it."""
    if not duties:
        return {"available": False, "reason": "the posting lists no duties HAYAT can separate"}
    if not cv_lines:
        return {"available": False, "reason": "your profile has no CV lines yet: read your CV again (“read my CV from …”)"}
    documents = [tokens(line) for line in cv_lines] + [tokens(duty) for duty in duties]
    frequency = Counter(term for document in documents for term in document)
    idf = {term: math.log(1 + len(documents) / count) for term, count in frequency.items()}
    mine = {skill.lower() for skill in skills}
    rows = []
    for duty in duties:
        wanted = tokens(duty)
        duty_skills = {name.lower() for name in skills_in(duty)}
        weight = sum(idf.get(term, 1.0) for term in wanted) or 1.0
        best, best_line = 0.0, ""
        for line, have in zip(cv_lines, documents):
            shared = sum(idf.get(term, 1.0) for term in wanted & have) / weight
            line_skills = {name.lower() for name in skills_in(line)}
            shared += .25 * len(duty_skills & line_skills) / max(1, len(duty_skills)) if duty_skills else 0
            if shared > best:
                best, best_line = shared, line
        skill_cover = len(duty_skills & mine) / len(duty_skills) if duty_skills else None
        value = max(best, .5 * skill_cover if skill_cover is not None else 0)
        status = "aligned" if value >= .38 else "partly" if value >= .18 else "gap"
        rows.append({"duty": duty, "status": status, "score": round(min(1.0, value), 2),
                     "evidence": best_line if status != "gap" else "", "skills": sorted(name for name in skills_in(duty))})
    points = {"aligned": 1.0, "partly": .5, "gap": 0.0}
    percent = round(sum(points[row["status"]] for row in rows) / len(rows) * 100)
    return {"available": True, "percent": percent, "rows": rows,
            "aligned": sum(row["status"] == "aligned" for row in rows), "partly": sum(row["status"] == "partly" for row in rows),
            "gaps": sum(row["status"] == "gap" for row in rows)}


def _core(title: str) -> set[str]:
    from app.jobs.match import _title_words
    return _title_words(title)


def _yearly(pay: dict[str, Any] | None) -> tuple[str, float] | None:
    if not pay or not pay.get("max"):
        return None
    factor = {"year": 1, "month": 12, "week": 52, "day": 260, "hour": 2080}.get(pay.get("period") or "year")
    if not factor or not pay.get("currency"):
        return None
    middle = (pay["min"] + pay["max"]) / 2 if pay.get("min") else pay["max"]
    return pay["currency"], middle * factor


def market(store: Any, row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Similar jobs in HAYAT's own collection: how many recently, their pay, skills asked, remote share, next roles."""
    now = now or datetime.now(UTC)
    core = _core(row["title"])
    similar = []
    for other in store.matches(limit=500, include_blocked=True, include_hidden=True):
        if other["number"] == row["number"]:
            continue
        theirs = _core(other["title"])
        if core and theirs and len(core & theirs) / len(core | theirs) >= .5:
            similar.append(other)
    recent = cutoff = now - timedelta(days=30)
    older = now - timedelta(days=60)
    seen = lambda item: datetime.fromisoformat(item["first_seen"])
    last30 = sum(seen(item) >= recent for item in similar)
    before = sum(older <= seen(item) < cutoff for item in similar)
    pays = [value for value in (_yearly((item.get("reading") or {}).get("salary")) for item in similar) if value]
    currency = Counter(currency for currency, _value in pays).most_common(1)
    pay = None
    if currency and sum(1 for name, _value in pays if name == currency[0][0]) >= 3:
        values = sorted(value for name, value in pays if name == currency[0][0])
        pay = {"currency": currency[0][0], "low": round(values[len(values) // 4]), "median": round(statistics.median(values)),
               "high": round(values[(3 * len(values)) // 4]), "postings": len(values)}
    asked = Counter(name for item in similar for name in (item.get("reading") or {}).get("skills", {}).get("required", [])
                    + (item.get("reading") or {}).get("skills", {}).get("mentioned", []) if CATEGORY.get(name) != "Support & people")
    remote = sum((item.get("reading") or {}).get("mode") == "remote" for item in similar)
    paths = next((steps for pattern, steps in CAREER if re.search(pattern, row["title"], re.IGNORECASE)), [])
    return {"similar": len(similar), "last_30_days": last30, "previous_30_days": before,
            "trend": ("more postings seen lately" if last30 > before * 1.3 and last30 >= 5 else
                      "fewer postings seen lately" if before > last30 * 1.3 and before >= 5 else
                      "about as many as before" if last30 + before >= 5 else "too few seen to tell"),
            "pay": pay, "top_skills": [{"skill": name, "postings": count} for name, count in asked.most_common(10)],
            "remote_share": round(remote / len(similar) * 100) if similar else None, "next_roles": paths,
            "basis": "From the jobs HAYAT has collected for your searches, not the whole market. Next roles are common career "
                     "patterns, not a prediction."}


def history(store: Any, row: dict[str, Any]) -> dict[str, Any]:
    """The owner's own record with similar roles: applied, interviews, offers, rejections, hidden."""
    core = _core(row["title"])
    counts: Counter = Counter()
    for application in store.applications():
        theirs = _core(application["title"])
        if application["number"] != row["number"] and core and theirs and len(core & theirs) / len(core | theirs) >= .5:
            counts[application["status"]] += 1
    hidden = sum(1 for other in store.matches(limit=500, include_blocked=True, include_hidden=True)
                 if other["hidden"] and core and _core(other["title"]) and len(core & _core(other["title"])) / len(core | _core(other["title"])) >= .5)
    applied = sum(counts[name] for name in ("applied", "interview", "offer", "rejected"))
    replies = counts["interview"] + counts["offer"]
    note = ("You have not applied to a similar role yet." if not applied else
            f"Of {applied} similar applications, {replies} led to an interview or offer." if applied >= 3 else
            f"{applied} similar application{'s' if applied != 1 else ''} so far: too few to see a pattern.")
    return {"counts": dict(counts), "applied": applied, "replies": replies, "hidden": hidden, "note": note,
            "basis": "Only your own record. Nobody publishes who was hired for a role, so HAYAT does not guess it."}


def prospects(store: Any, now: datetime | None = None) -> dict[str, Any]:
    """The owner's own reply record: of the applications they sent, how many drew an interview or offer, overall
    and split by match strength, with a plain projection. Only their own history — never a guess about whether a
    particular employer will reply, and never invented odds of an offer."""
    now = now or datetime.now(UTC)
    sent_states = {"applied", "interview", "offer", "rejected"}
    sent = [row for row in store.applications() if row.get("status") in sent_states]
    replies = sum(1 for row in sent if row["status"] in ("interview", "offer"))
    offers = sum(1 for row in sent if row["status"] == "offer")
    waiting = sum(1 for row in sent if row["status"] == "applied")

    def band(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        got = sum(1 for row in rows if row["status"] in ("interview", "offer"))
        return {"sent": len(rows), "replies": got, "reply_rate": round(got / len(rows), 2)}

    strong = band([row for row in sent if (row.get("score") or 0) >= 60])
    rest = band([row for row in sent if (row.get("score") or 0) < 60])
    rate = replies / len(sent) if sent else None
    # Laplace-smoothed estimate, so a tiny record is not read as 0% or 100%.
    estimate = (replies + 1) / (len(sent) + 2) if sent else None
    enough = len(sent) >= 5
    if not sent:
        note = "You have not sent an application yet, so there is no reply record to learn from."
    elif not enough:
        note = (f"{len(sent)} application{'s' if len(sent) != 1 else ''} sent, {replies} with a reply: too few to read a "
                "rate from yet. Keep going and this sharpens.")
    else:
        note = (f"Of {len(sent)} applications you sent, {replies} drew an interview or offer — about "
                f"{round(rate * 100)}% ({round(estimate * 100)}% allowing for the small sample).")
    projection = None
    if enough and estimate:
        per = max(1, round(1 / estimate))
        projection = {"applications_per_reply": per,
                      "text": f"At your rate, about one reply for every {per} similar applications."}
    return {"prospects": True, "sent": len(sent), "replies": replies, "offers": offers, "waiting": waiting,
            "reply_rate": round(rate, 2) if rate is not None else None,
            "estimate": round(estimate, 2) if estimate is not None else None,
            "by_strength": {"strong_fit": strong, "weaker_fit": rest}, "enough": enough,
            "projection": projection, "note": note,
            "basis": "Only your own application record. Nobody publishes who was hired, so HAYAT never predicts a "
                     "particular employer's decision or invents odds of an offer."}


def prospects_lines(value: dict[str, Any]) -> list[str]:
    lines = ["Your prospects, from your own record:", value["note"]]
    bands = value.get("by_strength") or {}
    if value["enough"]:
        for key, label in (("strong_fit", "strong-fit roles (score ≥ 60)"), ("weaker_fit", "weaker-fit roles")):
            band = bands.get(key)
            if band and band["sent"]:
                lines.append(f"  {label}: {band['replies']} of {band['sent']} replied ({round(band['reply_rate'] * 100)}%).")
    if value["waiting"]:
        lines.append(f"  {value['waiting']} still waiting for a reply.")
    if value.get("projection"):
        lines.append(value["projection"]["text"])
    lines.append(value["basis"])
    return lines


def learning_plan(store: Any, profile: dict[str, Any], skills: list[str], now: datetime | None = None,
                  limit: int = 60) -> dict[str, Any]:
    """Across the owner's current matches, the skills worth learning first.

    For each skill the owner's profile is missing, this counts how many matched jobs ask for it, how many it
    alone blocks (the only required skill they still lack), and the pay those jobs offer, then ranks skills by
    the jobs they would unlock and the pay they open, with where and how to learn each. Patterns from the jobs
    HAYAT has collected for the owner's searches, not the whole market, and never a promise of an offer.
    """
    now = now or datetime.now(UTC)
    currency = (profile.get("preferences") or {}).get("salary_currency") or "USD"
    rows = [row for row in store.matches(limit=limit) if isinstance(row.get("match"), dict)]
    have = {skill.lower() for skill in skills}

    needed: dict[str, dict[str, Any]] = {}

    def track(name: str, number: int, yearly: tuple[str, float] | None, required: bool) -> None:
        entry = needed.setdefault(name, {"jobs": set(), "required_in": set(), "pays": [],
                                         "category": CATEGORY.get(name, "Other")})
        entry["jobs"].add(number)
        if required:
            entry["required_in"].add(number)
        if yearly and yearly[0] == currency:
            entry["pays"].append(yearly[1])

    sole_blocker: Counter = Counter()
    for row in rows:
        match = row["match"]
        missing_required = [name for name in match.get("missing_required", []) if name.lower() not in have]
        if len(missing_required) == 1:
            sole_blocker[missing_required[0]] += 1
        yearly = _yearly((row.get("reading") or {}).get("salary"))
        for name in missing_required:
            track(name, row["number"], yearly, True)
        for name in match.get("missing_other", []):
            if name.lower() not in have and name not in missing_required:
                track(name, row["number"], yearly, False)

    plan = []
    for name, entry in needed.items():
        pays = sorted(entry["pays"])
        plan.append({"skill": name, "category": entry["category"], "needed_by": len(entry["jobs"]),
                     "required_by": len(entry["required_in"]), "unlocks": sole_blocker.get(name, 0),
                     "median_pay": round(statistics.median(pays)) if pays else None,
                     "pay_currency": currency if pays else None, "learn": DOCS.get(name), "how": PRACTICE.get(name)})
    # Rank by the jobs a skill alone unlocks, then how many ask for it, then the pay it opens, then total demand.
    plan.sort(key=lambda item: (item["unlocks"], item["required_by"], item["median_pay"] or 0, item["needed_by"]),
              reverse=True)

    def still_missing(row: dict[str, Any]) -> list[str]:
        return [name for name in row["match"].get("missing_required", []) if name.lower() not in have]

    strong_now = sum(1 for row in rows if row.get("verdict") in ("strong", "good") or not still_missing(row))
    one_away = sum(1 for row in rows if len(still_missing(row)) == 1)
    top = [item["skill"] for item in plan[:3] if item["unlocks"]]
    learn_set = {name.lower() for name in top}
    would_make_strong = sum(1 for row in rows if still_missing(row)
                            and all(name.lower() in learn_set for name in still_missing(row)))
    return {"learning_plan": True, "jobs_considered": len(rows), "currency": currency, "skills": plan[:12],
            "readiness": {"strong_now": strong_now, "one_skill_away": one_away},
            "projection": {"learn": top, "would_make_strong": would_make_strong} if top else None,
            "basis": "From the jobs HAYAT has collected for your searches, not the whole market. 'Unlocks' counts "
                     "jobs where the skill is the only required one you still lack. Not a promise of an offer."}


def learning_lines(plan: dict[str, Any]) -> list[str]:
    """The learning plan as chat lines, most useful skill first."""
    if not plan.get("jobs_considered"):
        return ["No scored matches yet. Run a job search first, then ask what to learn."]
    ready = plan["readiness"]
    lines = [f"What to learn, from {plan['jobs_considered']} of your matches "
             f"({ready['strong_now']} already a strong/good fit, {ready['one_skill_away']} one skill away):"]
    for item in plan["skills"][:8]:
        pay = (f" · ~{item['median_pay']:,} {item['pay_currency']}/yr" if item.get("median_pay") else "")
        unlocks = f" · unlocks {item['unlocks']} job{'s' if item['unlocks'] != 1 else ''}" if item["unlocks"] else ""
        lines.append(f"• {item['skill']} — asked by {item['needed_by']} "
                     f"({item['required_by']} require it){unlocks}{pay}"
                     + (f"\n    learn: {item['learn']}" if item.get("learn") else ""))
    projection = plan.get("projection")
    if projection and projection["learn"]:
        lines.append(f"Learning {', '.join(projection['learn'])} would make you a strong fit for "
                     f"{projection['would_make_strong']} of them.")
    lines.append(plan["basis"])
    return lines


def checklist(text: str) -> list[str]:
    """Instructions in the posting that applicants commonly miss."""
    found = []
    for pattern, template in INSTRUCTIONS:
        for match in re.finditer(pattern, text or ""):
            item = template.format(*(group.lower() for group in match.groups() if group)) if "{0}" in template else template
            quote = " ".join(match.group(0).split())[:120]
            entry = f"{item} — “{quote}”"
            if entry not in found:
                found.append(entry)
    return found[:8]


def pitfalls(row: dict[str, Any], reading: dict[str, Any], match: dict[str, Any], instructions: list[str], now: datetime) -> list[str]:
    """Why applications to this posting commonly fail, from what it says."""
    found = []
    if instructions:
        found.append(f"It gives {len(instructions)} specific instruction{'s' if len(instructions) != 1 else ''} (see the checklist); "
                     "many applicants skip them, and some employers filter on exactly that.")
    if reading["skills"]["required"]:
        found.append("Applicant tracking systems often screen on the required skills' exact names: "
                     + ", ".join(reading["skills"]["required"][:6]) + ". Use the same words where they are true for you.")
    if reading["regions"] and "Worldwide" not in reading["regions"]:
        found.append(f"It is limited to {', '.join(reading['regions'])}; applications from elsewhere are usually rejected without reading.")
    if reading["languages_needed"]:
        found.append(f"It needs {', '.join(reading['languages_needed'])}; say your level plainly.")
    if reading["years_min"]:
        found.append(f"It asks {reading['years_min']}+ years; if you are below that, show the closest experience instead of skipping it.")
    try:
        age = (now - datetime.fromisoformat(row["posted_at"])).days if row.get("posted_at") else None
    except ValueError:
        age = None
    if age is not None and age > 21:
        found.append(f"It was posted {age} days ago; it may have many applicants or be close to filled, so apply soon or not at all.")
    elif age is not None and age <= 3:
        found.append("It is new: applying in the first days is usually an advantage.")
    found.append("A generic letter is the most common mistake: name this company and two of its duties you have done.")
    return found


def benefit(row: dict[str, Any], reading: dict[str, Any], match: dict[str, Any], profile: dict[str, Any], outlook: dict[str, Any]) -> list[str]:
    """How this job could help the owner, said with its evidence."""
    from app.jobs.match import LEVEL_ORDER
    from app.jobs.profile import level_for
    found = []
    learn = [name for name in match.get("missing_required", []) + [item for item in reading["skills"]["nice"] if item in match.get("missing_other", [])]
             if CATEGORY.get(name) != "Support & people"]
    if learn:
        found.append("Skills you would build: " + ", ".join(list(dict.fromkeys(learn))[:6]) + ".")
    mine = profile["preferences"].get("level") or profile["cv"].get("level") or level_for(profile["cv"].get("years_experience"))
    theirs = reading["level"]
    if mine in LEVEL_ORDER and theirs in LEVEL_ORDER:
        step = LEVEL_ORDER.index(theirs) - LEVEL_ORDER.index(mine)
        found.append("A step up in level." if step > 0 else "A step down in level: check the pay and scope." if step < 0 else "The same level as you now.")
    yearly = _yearly(reading["salary"])
    floor = profile["preferences"].get("min_salary")
    if yearly and floor and yearly[0] == profile["preferences"].get("salary_currency"):
        found.append(f"Pay about {yearly[1]:,.0f} {yearly[0]} a year, {'above' if yearly[1] >= floor else 'below'} your minimum of {floor:,.0f}.")
    if yearly and outlook.get("pay") and yearly[0] == outlook["pay"]["currency"]:
        median = outlook["pay"]["median"]
        found.append(f"Its pay is {'above' if yearly[1] > median * 1.05 else 'below' if yearly[1] < median * .95 else 'near'} the median of "
                     f"{outlook['pay']['postings']} similar postings ({median:,.0f} {yearly[0]}).")
    if outlook.get("next_roles"):
        found.append("Common next roles from here: " + ", ".join(outlook["next_roles"][:3]) + ".")
    if reading["mode"] == "remote" and profile["preferences"].get("remote_only"):
        found.append("Remote, as you want.")
    if outlook["similar"] >= 10:
        found.append(f"A common role in what HAYAT has seen ({outlook['similar']} similar postings), so the experience transfers.")
    return found


def first_90_days(duties: list[str], reading: dict[str, Any], match: dict[str, Any], people: list[str], success: list[str]) -> list[dict[str, str]]:
    learn = [name for name in reading["skills"]["required"] or reading["skills"]["mentioned"] if CATEGORY.get(name) != "Support & people"][:4]
    plan = [{"when": "Days 1–30", "what": "Learn the tools, data and people: " + (", ".join(learn) if learn else "the team's main tools")
             + (f"; {people[0]}" if people else "") + (f". Close your gaps first: {', '.join(match['missing_required'][:3])}" if match["missing_required"] else "") + "."}]
    if duties:
        plan.append({"when": "Days 31–60", "what": "Take on the core duties: " + "; ".join(duty[:90] for duty in duties[:2]) + "."})
    plan.append({"when": "Days 61–90", "what": ("Deliver against how success is measured: " + success[0][:140] + ".") if success else
                 ("Own " + duties[2][:100] + " and show a first result." if len(duties) > 2 else "Show a first measurable result and agree goals.")})
    return plan


def preparation(row: dict[str, Any], reading: dict[str, Any], match: dict[str, Any], fit: dict[str, Any], outlook: dict[str, Any],
                profile: dict[str, Any]) -> dict[str, Any]:
    questions = []
    families: dict[str, list[str]] = {}
    for name in reading["skills"]["required"] + reading["skills"]["nice"]:
        if CATEGORY.get(name) in QUESTION_TEMPLATES:
            families.setdefault(CATEGORY[name], []).append(name)
    for family, names in list(families.items())[:4]:
        listed = names[0] if len(names) == 1 else ", ".join(names[:2]) + " and " + names[2] if len(names) > 2 else " and ".join(names)
        questions.append({"kind": "skill", "question": QUESTION_TEMPLATES[family].format(listed)})
    for item in (fit.get("rows") or [])[:4]:
        duty = item["duty"][0].lower() + item["duty"][1:]
        tip = f" Use: “{item['evidence'][:100]}”" if item.get("evidence") else " You have no clear example: choose the closest one."
        questions.append({"kind": "behavioural", "question": f"Tell me about a time you had to {duty}. (Situation, task, action, result.)" + tip})
    pay = outlook.get("pay")
    questions += [{"kind": "standard", "question": f"Why {row['company']}, and why this role?"},
                  {"kind": "standard", "question": "Walk me through your CV in two minutes."},
                  {"kind": "standard", "question": "What are your salary expectations?"
                   + (f" Similar postings HAYAT saw: {pay['low']:,}–{pay['high']:,} {pay['currency']} a year." if pay else "")}]
    study = []
    wanted_nice = [name for name in reading["skills"]["nice"] if name in match.get("missing_other", []) and CATEGORY.get(name) != "Support & people"]
    for name in match["missing_required"][:6] + wanted_nice[:3]:
        asked = next((item["postings"] for item in outlook["top_skills"] if item["skill"] == name), 0)
        study.append({"skill": name, "why": ("required here" if name in match["missing_required"] else "a plus here")
                      + (f"; asked by {asked} similar postings" if asked else ""),
                      "start": DOCS.get(name, "its official documentation"), "practice": PRACTICE.get(CATEGORY.get(name, ""), "use it in a small project")})
    have = [name for name in reading["skills"]["required"] if name in match["matched"]]
    tailoring = []
    if have:
        tailoring.append("Put these first in your Skills section, in the posting's words: " + ", ".join(have[:8]) + ".")
    best = [item for item in (fit.get("rows") or []) if item["status"] != "gap" and item.get("evidence")]
    if best:
        tailoring.append("Move these CV lines up, they show its duties: "
                         + " | ".join(dict.fromkeys(item["evidence"][:90].rstrip(".") for item in best[:3])) + ".")
    gaps = [item["duty"] for item in (fit.get("rows") or []) if item["status"] == "gap"]
    if gaps:
        tailoring.append("Duties your CV does not show yet (add a line only if you have done it): " + "; ".join(duty[:70] for duty in gaps[:3]) + ".")
    tailoring.append(f"Headline for this application: {row['title']} — " + ", ".join(have[:3] or match["matched"][:3]) + ".")
    ask = []
    if not reading["salary"]:
        ask.append("What is the salary range?")
    if reading["mode"] == "unknown":
        ask.append("Is it remote, hybrid or on site?")
    if reading["sponsorship"] == "not stated" and reading["regions"]:
        ask.append("Do you sponsor work visas or hire through an employer of record?")
    ask.append("What does success look like in the first six months?")
    ask.append("What is the team like, and who would I work with most?")
    return {"questions": questions, "study": study, "tailoring": tailoring, "ask_them": ask}


def insight(store: Any, row: dict[str, Any], profile: dict[str, Any], skills: list[str], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    reading, match = row["reading"], row["match"]
    text = row.get("description", "")
    duties = responsibilities(text)
    fit = alignment(duties, profile["cv"].get("lines", []), skills)
    success = [" ".join(line.split())[:200] for line in text.splitlines() if SUCCESS.search(line)][:4]
    people = [" ".join(match_line.split())[:160] for match_line in (line for line in text.splitlines() if PEOPLE.search(line))][:3]
    outlook = market(store, row, now)
    instructions = checklist(text)
    prepare = preparation(row, reading, match, fit, outlook, profile)
    from app.jobs.readiness import application_plan
    return {"jobs_view": True, "insight": True, "number": row["number"], "title": row["title"], "company": row["company"],
            "url": row["url"], "source_name": row["source_name"], "score": row["score"], "verdict": row["verdict"],
            "summary": reading["summary"], "duties": duties, "fit": fit, "success": success, "people": people,
            "first_90_days": first_90_days(duties, reading, match, people, success), "benefit": benefit(row, reading, match, profile, outlook),
            "market": outlook, "history": history(store, row), "checklist": instructions,
            "pitfalls": pitfalls(row, reading, match, instructions, now),
            "prepare": prepare, "application_plan": application_plan(row, profile, fit, prepare, instructions),
            "basis": "Read from the posting, your CV, the jobs HAYAT collected and your own record. Nobody knows who was hired before "
                     "or what will happen next, so none of this is a promise."}


def explain(model: Any, value: dict[str, Any]) -> str:
    """A plain summary of the insight by HAYAT's local model, from these facts only."""
    import json
    from app.models.base import Message
    from app.models.streaming import generate_answer
    if model is None:
        raise RuntimeError("HAYAT's local model is not available in this session.")
    facts = {key: value[key] for key in ("title", "company", "score", "verdict", "summary", "fit", "benefit", "market", "history",
                                         "pitfalls", "first_90_days")}
    answer = generate_answer(model, [
        Message("system", "You are a frank career adviser. In 6 to 9 short sentences, using only the facts given, say how well this job fits, "
                          "what the person would gain, the biggest gaps and risks, and the two most useful things to do before applying. "
                          "Never invent facts about the company, past hires or the future, and never promise an outcome."),
        Message("user", json.dumps(facts, ensure_ascii=False, default=str)[:14000])], temperature=.2, max_tokens=520)
    return str(getattr(answer, "content", answer)).strip()
