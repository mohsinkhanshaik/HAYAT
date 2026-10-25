"""Job tools, chat routes ("find python jobs", "show my job matches", "send my application for job 12") and replies."""
from __future__ import annotations

import re
from typing import Any

from app.jobs.store import STATUSES
from app.jobs.understand import pay_text

TOOL_NAMES = ("job_search", "job_matches", "job_detail", "job_hide", "job_profile", "job_application_prepare", "job_application_send",
              "job_open", "job_applications", "job_sources", "job_insight", "job_cv_review", "job_learning_plan",
              "job_prospects", "job_discover_company", "job_application_fill", "job_sponsorship", "job_browser_search", "job_source_open")


def register_job_tools(registry: Any, center: Any, watcher: Any = None) -> None:
    def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    number = {"type": "integer", "minimum": 1}
    registry.register("job_source_open", "Open the official website of a known job feed or supported employer board for manual reading/verification. "
                      "Does not alter API permissions, override cooldowns, import jobs or apply.",
                      obj({"source": {"type": "string", "maxLength": 100}}, ("source",)), "low", "application_launch", center.open_source)
    registry.register("job_browser_search", "Open an explicit role/location search on LinkedIn, Indeed, Web or Employer boards. "
                      "Optionally read and scroll the visible HAYAT browser page. Results are unverified leads, not imported jobs. "
                      "Manual interaction is needed for site challenges; resume=true continues the pending search in the same session without reloading. "
                      "Auto requires scan=true and tries DuckDuckGo/Bing/Google once each on blocked routes, preserving challenge tabs; "
                      "Google/Bing/DuckDuckGo can also be selected explicitly for Web/Employer boards. Does not apply or submit.",
                      obj({"platform": {"enum": ["Employer boards", "LinkedIn", "Indeed", "Web"]},
                           "query": {"type": "string", "maxLength": 120}, "location": {"type": "string", "maxLength": 120},
                           "scan": {"type": "boolean"}, "engine": {"enum": ["Auto", "Google", "Bing", "DuckDuckGo"]},
                           "resume": {"type": "boolean"}}), "low", "application_launch", center.browser_search)
    registry.register("job_search", "Search public job boards (Remote OK, Jobicy, Himalayas, Working Nomads, The Muse, Arbeitnow, and company "
                      "boards the owner follows on Greenhouse, Lever and Ashby) for the owner's search terms or a query, read each posting, "
                      "and score it against the owner's CV and preferences. Explicit queries and deeper searches refresh sources; "
                      "background rounds use hourly caching. Depth normal/deep/maximum has bounded pagination, not exhaustive coverage. "
                      "Blank or Worldwide location means no location restriction; any city/country text or semicolon-separated alternatives work.",
                      obj({"query": {"type": "string", "maxLength": 120}, "location": {"type": "string", "maxLength": 60},
                           "remote": {"type": "boolean"}, "force": {"type": "boolean"},
                           "depth": {"enum": ["normal", "deep", "maximum"]},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 500}}), "low", "public_api_get", center.search)
    registry.register("job_matches", "The owner's best job matches so far, with score, verdict, matched and missing skills, and the source.",
                      obj({"min_score": {"type": "integer", "minimum": 0, "maximum": 100}, "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                           "offset": {"type": "integer", "minimum": 0},
                           "include_blocked": {"type": "boolean"}, "query": {"type": "string", "maxLength": 80},
                           "location": {"type": "string", "maxLength": 60},
                           "sort_by": {"enum": ["score", "newest", "pay"]}, "remote_only": {"type": "boolean"},
                           "sponsorship": {"enum": ["all", "offered", "not stated", "not offered", "conflicting"]}}),
                      "low", "filesystem_read", center.matches)
    registry.register("job_detail", "One job in depth: what the posting asks for (required and nice-to-have skills, years, level, work mode, "
                      "where you must be, languages, pay, warning signs) and every part of its match score.",
                      obj({"number": number}, ("number",)), "low", "filesystem_read", center.detail)
    registry.register("job_hide", "Hide a job the owner is not interested in.", obj({"number": number}, ("number",)),
                      "low", "filesystem_write_project", center.hide)

    def profile(action: str = "show", path: str = "", preferences: dict[str, Any] | None = None, skill: str = "",
                company: str = "") -> dict[str, Any]:
        if action == "read_cv":
            return center.read_cv(path)
        if action == "import_sponsors":
            return center.import_sponsors(path)
        if action == "set":
            return center.set_preferences(preferences or {})
        if action in ("add_skill", "remove_skill"):
            return center.change_skill(skill, action == "add_skill")
        if action in ("follow", "unfollow"):
            current = center.profile.load()["preferences"]["companies"]
            value = company.lower().strip()
            changed = (current + [value]) if action == "follow" else [item for item in current if item != value]
            return center.set_preferences({"companies": list(dict.fromkeys(changed))})
        return center.show_profile()
    registry.register("job_profile", "The owner's job profile: show it; read their CV from a PDF, Word or text file; set preferences "
                      "(target_titles, search_terms, locations, countries_ok, remote_only, min_salary, salary_currency, level, job_types, "
                      "exclude_companies, exclude_words, sources, auto_prepare, notify_score, notice_period, work_authorization, "
                      "salary_expectation, willing_to_relocate, work_status); import_sponsors reads a local USCIS-style employer-history CSV; "
                      "add or remove a skill; follow or unfollow a company board (greenhouse:name, lever:name, ashby:name).",
                      obj({"action": {"enum": ["show", "read_cv", "set", "import_sponsors", "add_skill", "remove_skill", "follow", "unfollow"]},
                           "path": {"type": "string", "maxLength": 400}, "preferences": {"type": "object"},
                           "skill": {"type": "string", "maxLength": 60}, "company": {"type": "string", "maxLength": 80}}),
                      "low", "filesystem_write_project", profile)
    registry.register("job_sponsorship", "Research STEM OPT, H-1B and employer sponsorship for a job number or company: posting evidence, "
                      "exact legal-name history from a locally imported USCIS-style CSV, recruiter checklist and official sources. "
                      "Historical decisions do not prove current sponsorship, cap exemption or individual eligibility.",
                      obj({"number": number, "company": {"type": "string", "maxLength": 160}}),
                      "low", "filesystem_read", center.sponsorship)
    registry.register("job_application_prepare", "Prepare an application packet for one job: a cover letter (Markdown and Word) that uses only "
                      "facts from the owner's profile and the posting, an answers sheet, posting, unchanged CV, preparation.md and readiness.json "
                      "with required skill practice, interview evidence, application stages, pay assumptions and public referral research links. "
                      "Writes local files only; sends nothing.",
                      obj({"number": number}, ("number",)), "low", "filesystem_write_project", center.prepare)
    registry.register("job_application_send", "Propose emailing a prepared application to the employer's own application address. It appears "
                      "in the confirmation window with the letter and attachments, and is sent only when the owner confirms it there.",
                      obj({"number": number}, ("number",)), "high", "send_message", center.send)
    registry.register("job_open", "Open a job's application page in the owner's browser, for postings that take applications on the "
                      "employer's own site; the owner submits there.", obj({"number": number}, ("number",)), "low", "application_launch", center.open)

    def applications(action: str = "list", number: int | None = None, status: str = "", note: str = "") -> dict[str, Any]:
        if action == "update":
            if number is None:
                raise ValueError("Which job number?")
            return center.set_status(number, status, note)
        return center.applications(status if status in STATUSES else "")
    registry.register("job_applications", "The owner's application tracker: list applications (optionally by status), or update one "
                      "(saved, prepared, opened, applied, interview, offer, rejected, withdrawn) with a note. Applied ones get a follow-up "
                      "reminder after a week.",
                      obj({"action": {"enum": ["list", "update"]}, "number": number, "status": {"enum": ["", *STATUSES]},
                           "note": {"type": "string", "maxLength": 500}}), "low", "filesystem_write_project", applications)

    registry.register("job_insight", "Everything about one job: its responsibilities and how each matches the owner's CV (aligned, partly, "
                      "gap, with the CV line), alignment %, what it could do for the owner (skills, level, pay), similar postings HAYAT has "
                      "seen (count, pay, skills asked, remote share, usual next roles), the owner's own history with similar roles, "
                      "instructions applicants miss, why applications fail, the first 90 days, and preparation (interview questions, study "
                      "plan with official docs, CV tailoring, questions to ask). explain=true adds a plain summary by the local model.",
                      obj({"number": number, "explain": {"type": "boolean"}}, ("number",)), "low", "filesystem_read", center.insight)
    registry.register("job_cv_review", "Review the owner's CV against their best job matches: contact, Skills section, results with numbers, "
                      "weak openings, length, the skills their matches ask for most that the CV lacks, and what to put first for the best "
                      "fits. Suggestions only; never rewrites facts.", obj({}), "low", "filesystem_read", center.cv_review)
    registry.register("job_learning_plan", "What the owner should learn to reach more of their matched jobs: across their current "
                      "matches, the missing skills ranked by how many jobs each unlocks (where it is the only required skill still "
                      "missing), how many ask for it, the pay those jobs offer, and where and how to learn each, plus how ready the "
                      "owner already is and what learning the top few would open. Patterns from collected jobs, never a promise.",
                      obj({}), "low", "filesystem_read", center.learning_plan)
    registry.register("job_application_fill", "Fill an employer's web application form in HAYAT's visible browser with facts from the owner's "
                      "profile and prepared letter, then stop: HAYAT attaches no file, answers no personal or legal question and presses "
                      "nothing; the owner checks, attaches the CV and submits.",
                      obj({"number": number}, ("number",)), "medium", "browser_type", center.fill)

    def sources(source: str = "") -> dict[str, Any]:
        if source:
            return center.source_access(source)
        from app.jobs.center import source_catalog
        preferences = center.profile.load()["preferences"]
        return {"jobs_view": True, "sources_view": True, "catalog": source_catalog(), "enabled": preferences["sources"],
                "companies": preferences["companies"], "state": center.store.source_state(), "cooldowns": center.store.source_cooldowns(),
                "watcher": {"last_round": getattr(watcher, "last_round", None), "every_hours": round(getattr(watcher, "interval", 0) / 3600, 1)},
                "basis": "Keyless public APIs are attempted through the fetcher's robots and public-address checks. Current access can change; "
                         "inspect errors and cooldowns. Enabling a source is a local preference, not publisher authorization. "
                         "Remotive's disallowed API and credential-in-URL services are excluded."}
    registry.register("job_sources", "Which job sources HAYAT reads, when each was last read and how many jobs it gave, the companies "
                      "followed, and the automatic round. Pass source to inspect its observed access state, cooldown and official browser route.",
                      obj({"source": {"type": "string", "maxLength": 100}}), "low", "filesystem_read", sources)
    registry.register("job_prospects", "The owner's own reply record: of the applications they sent, how many drew an interview or "
                      "offer, overall and split by match strength, with a plain projection (about one reply per N applications). "
                      "Only the owner's history; never a guess about a particular employer or invented offer odds.",
                      obj({}), "low", "filesystem_read", center.prospects)
    registry.register("job_discover_company", "Find a company's own public careers board (Greenhouse, Lever or Ashby) from its name, public HTTPS "
                      "ATS board URL or employer careers page linking to a board, and "
                      "list its open roles — the jobs that often never reach the big boards. Public keyless boards only, no login or "
                      "gated pages. follow=true also adds the board to the owner's searches.",
                      obj({"company": {"type": "string", "maxLength": 500}, "follow": {"type": "boolean"}}, ("company",)),
                      "low", "public_api_get", lambda company, follow=False: center.discover_company(company, follow))


# ---------------------------------------------------------------------- chat routes
_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_END = r"\s*[?.!]*$"
_N = r"#?(?P<n>\d{1,6})"


def _c(pattern: str) -> re.Pattern[str]:
    return re.compile(_LEAD + pattern + _END, re.IGNORECASE)


_FIND = _c(r"(?:find|search(?:\s+for)?|look\s+for|get|show|any)\s+(?:me\s+)?(?:some\s+|new\s+|a\s+|more\s+)?(?P<remote>remote\s+)?"
           r"(?P<what>[\w+#./&-]+(?:\s+[\w+#./&-]+){0,4}?\s+)??(?:jobs?|job\s+openings|openings|vacancies|positions)"
           r"(?:\s+(?:in|at|near)\s+(?P<where>[\w ,.-]{2,40}?))?(?:\s+for\s+me)?(?:\s+(?:today|now|online))?")
_MATCHES = _c(r"(?:show\s+(?:me\s+)?|list\s+|what\s+are\s+)?(?:my\s+)?(?:best\s+|top\s+|new\s+|latest\s+)?job\s+matches|"
              r"what\s+jobs\s+(?:match|suit|fit)\s+me|(?:best|top)\s+jobs\s+for\s+me|jobs\s+(?:that\s+)?match(?:ing)?\s+(?:me|my\s+(?:cv|resume|profile))")
_DETAIL = _c(r"(?:show(?:\s+me)?|explain|tell\s+me\s+about|details?\s+(?:of|for|on))\s+job\s+" + _N + r"|why\s+(?:is|was)\s+job\s+"
             + _N.replace("<n>", "<n2>") + r"\s+(?:a\s+)?(?:good|bad|strong|weak|poor)?\s*(?:match|fit)|job\s+" + _N.replace("<n>", "<n3>")
             + r"\s+details|(?:does|do)\s+job\s+" + _N.replace("<n>", "<n4>") + r"\s+(?:fit|suit|match)\s+me")
_CV = _c(r"(?:read|load|use|import|scan|analy[sz]e|understand|upload)\s+(?:my\s+)?(?:cv|resume|résumé)\s*(?:from|at|in|:)?\s*(?P<path>.+)")
_CV2 = _c(r"(?:use|read)\s+(?P<path>.+?\.(?:pdf|docx|txt|md))\s+as\s+my\s+(?:cv|resume|résumé)")
_PROFILE = _c(r"(?:show\s+(?:me\s+)?)?my\s+(?:job|cv|resume|career)\s+profile|what\s+does\s+my\s+(?:cv|resume)\s+say|"
              r"what\s+(?:skills|experience)\s+(?:did\s+you\s+find|do\s+you\s+see)\s+(?:in|on)\s+my\s+(?:cv|resume)")
_ADD = _c(r"add\s+(?P<skill>[\w+#./ -]{1,40}?)\s+to\s+my\s+(?:job\s+)?(?:skills|profile)")
_REMOVE = _c(r"remove\s+(?P<skill>[\w+#./ -]{1,40}?)\s+from\s+my\s+(?:job\s+)?(?:skills|profile)")
_WANT = _c(r"i\s+(?:want|am\s+looking\s+for|'m\s+looking\s+for|need|would\s+like)\s+(?:a\s+|an\s+)?(?P<remote>remote\s+)?"
           r"(?P<title>[\w+#./&-]+(?:\s+[\w+#./&-]+){0,4}?)\s+(?:jobs?|roles?|positions?)(?:\s+in\s+(?P<where>[\w ,.-]{2,40}?))?")
_REMOTE_ONLY = _c(r"(?:i\s+)?(?:only\s+want\s+remote(?:\s+jobs)?|only\s+remote\s+jobs|remote\s+jobs\s+only|remote\s+only)")
#: "I'm willing to relocate" / "I can move for work" / "I won't relocate" → the willing_to_relocate preference.
_RELOCATE = _c(r"(?:i(?:\s+am|['’]m)\s+)?(?P<no>not\s+|un)?(?:willing|happy|open|able|prepared)\s+to\s+relocate|"
               r"i\s+(?P<no2>can['’]?t|cannot|won['’]?t|will\s+not|do\s*n['’]?t\s+want\s+to)\s+(?:relocate|move\s+for\s+work)|"
               r"i\s+(?:can|would|will)\s+(?:relocate|move\s+for\s+work)|(?:happy|open|willing)\s+to\s+move\s+for\s+work")
_SALARY = _c(r"my\s+minimum\s+(?:salary|pay)\s+is\s+(?P<amount>\d[\d,]*)\s*(?P<k>k)?\s*(?P<cur>usd|eur|gbp|inr|dollars?|euros?|pounds?|rupees?)?"
             r"(?:\s+(?:a|per)\s+year)?")
_AUTO = _c(r"(?P<off>stop\s+|don't\s+|do\s+not\s+)?(?:auto[\s-]?prepare|prepar(?:e|ing))\s+(?:job\s+)?applications\s+(?:automatically|for\s+strong\s+matches)"
           r"|(?:turn|switch)\s+(?P<state>on|off)\s+auto[\s-]?prepare(?:d)?(?:\s+applications)?")
_FOLLOW = _c(r"(?P<verb>follow|unfollow|stop\s+following)\s+(?P<co>[a-z0-9][a-z0-9-]{0,60})\s+on\s+(?P<ats>greenhouse|lever|ashby)")
#: "what are my chances", "how likely am I to hear back", "my response rate", "my job prospects/odds".
_PROSPECTS = _c(r"(?:what\s+are\s+my\s+(?:chances|odds|prospects)|how\s+likely\s+(?:am\s+i|is\s+it)\s+(?:to\s+)?"
                r"(?:hear\s+back|get\s+(?:a\s+)?(?:reply|interview|response))|my\s+(?:job\s+)?(?:prospects|odds|chances|"
                r"response\s+rate|reply\s+rate|hit\s+rate)|(?:what(?:'s|\s+is)\s+my\s+)?(?:response|reply)\s+rate|"
                r"how\s+am\s+i\s+doing\s+(?:with|on)\s+(?:my\s+)?applications?)")
#: "find hidden jobs at Ramp", "does Stripe have any jobs", "check Acme's careers page", "discover Figma careers".
_DISCOVER = _c(r"(?:(?:find\s+)?hidden\s+jobs?\s+(?:at|for|from)\s+(?P<co1>.+?)"
               r"|(?:does|has|is)\s+(?P<co2>.+?)\s+(?:have\s+(?:any\s+)?(?:open\s+)?(?:jobs|roles|openings|vacancies)|hiring)"
               r"|(?:find|check|discover|look\s+up|see|show)\s+(?:me\s+)?(?P<co3>.+?)(?:['’]s)?\s+"
               r"(?:careers?(?:\s+page)?|jobs?\s+(?:board|page)|openings|ats(?:\s+board)?))")
_PREPARE = _c(r"(?:prepare|draft|write|get\s+ready)\s+(?:an?\s+|my\s+|the\s+)?(?:application|cover\s+letter)\s+(?:for|to)\s+job\s+" + _N
              + r"|prepare\s+job\s+" + _N.replace("<n>", "<n2>"))
_SEND = _c(r"(?:send|submit|email)\s+(?:my\s+|the\s+)?application\s+(?:for|to)\s+job\s+" + _N + r"|apply\s+(?:to|for)\s+job\s+" + _N.replace("<n>", "<n2>"))
_OPEN = _c(r"open\s+job\s+" + _N + r"(?:\s+(?:page|posting|link|application))?|open\s+the\s+(?:application|posting)\s+(?:page\s+)?for\s+job\s+"
           + _N.replace("<n>", "<n2>"))
_STATUS = _c(r"mark\s+job\s+" + _N + r"\s+(?:as\s+)?(?P<status>applied|interview(?:ing)?|offer(?:ed)?|rejected|withdrawn|saved)"
             r"|i\s+(?:got|have)\s+an?\s+(?P<status2>interview|offer)\s+(?:for|from)\s+job\s+" + _N.replace("<n>", "<n2>")
             + r"|job\s+" + _N.replace("<n>", "<n3>") + r"\s+(?:rejected\s+me|said\s+no)")
_APPLICATIONS = _c(r"(?:show\s+(?:me\s+)?)?my\s+(?:job\s+)?applications|(?:job\s+)?application\s+(?:status|tracker)|"
                   r"(?:which|what)\s+jobs\s+(?:have\s+i|did\s+i)\s+appl(?:y|ied)\s+(?:to|for)")
_HIDE = _c(r"(?:hide|skip|dismiss)\s+job\s+" + _N + r"|(?:i'm\s+|i\s+am\s+)?not\s+interested\s+in\s+job\s+" + _N.replace("<n>", "<n2>"))
_INSIGHT = _c(r"(?:tell\s+me\s+everything\s+about|deep[\s-]?dive(?:\s+into)?|analy[sz]e|break\s+down|insights?\s+(?:on|for|into)|"
              r"explain\s+everything\s+about)\s+job\s+" + _N
              + r"|(?:is|would)\s+job\s+" + _N.replace("<n>", "<n2>") + r"\s+(?:be\s+)?(?:good|right|worth\s+it|a\s+good\s+move)\s+for\s+(?:me|my\s+career)"
              + r"|how\s+(?:do|should|can)\s+i\s+(?:prepare|get\s+ready)\s+for\s+job\s+" + _N.replace("<n>", "<n3>")
              + r"|(?:prepare\s+me|interview\s+(?:prep|preparation|questions))\s+for\s+job\s+" + _N.replace("<n>", "<n4>")
              + r"|what\s+would\s+i\s+(?:do|be\s+doing)\s+(?:in|at)\s+job\s+" + _N.replace("<n>", "<n5>")
              + r"|how\s+(?:well\s+)?does\s+(?:my\s+(?:cv|resume)\s+(?:fit|match|align\s+with)\s+job\s+" + _N.replace("<n>", "<n6>")
              + r"|job\s+" + _N.replace("<n>", "<n7>") + r"\s+(?:fit|match|align\s+with)\s+my\s+(?:cv|resume))"
              + r"|job\s+" + _N.replace("<n>", "<n8>") + r"\s+(?:insights?|deep[\s-]?dive|in\s+depth)"
              + r"|(?:does|do)\s+job\s+" + _N.replace("<n>", "<n9>") + r"\s+(?:fit|match|suit|align\s+with)\s+my\s+(?:cv|resume|profile|skills)")
_CV_REVIEW = _c(r"(?:review|check|improve|rate|critique|fix)\s+my\s+(?:cv|resume|résumé)|how\s+(?:good|strong|is)\s+(?:is\s+)?my\s+(?:cv|resume)"
                r"|what\s+should\s+i\s+(?:change|improve|add)\s+(?:in|to|on)\s+my\s+(?:cv|resume)|(?:cv|resume)\s+(?:tips|review|feedback)")
_FILL = _c(r"(?:fill|pre-?fill)\s+(?:in\s+|out\s+)?(?:the\s+)?(?:application\s+)?(?:form\s+)?(?:for\s+)?job\s+" + _N)
#: "what should I learn", "skills to learn for jobs", "learning plan", "what to study to get these jobs".
_LEARN = _c(r"(?:what\s+(?:should|do)\s+i\s+(?:learn|study)|what\s+(?:to|should\s+i)\s+learn|"
            r"(?:what|which)\s+skills?\s+(?:should\s+i\s+)?(?:learn|study|build|focus|get|develop)|"
            r"skills?\s+(?:to|i\s+should)\s+(?:learn|study|build)|learning\s+plan|what\s+to\s+study|"
            r"what\s+(?:skills?|knowledge)\s+(?:do\s+)?i\s+need)(?:\s+(?:for|to\s+(?:get|land|reach))\b.*)?")
_SOURCES = _c(r"(?:which|what)\s+job\s+(?:sources|sites|boards)(?:\s+do\s+you\s+(?:use|read|search))?|(?:show\s+)?(?:my\s+)?job\s+sources")
#: "find research jobs" is a job search; "show my research jobs" or "running jobs" are HAYAT's own background work.
NOT_JOB_WORDS = {"research", "background", "scheduled", "running", "automation", "cron", "print", "my", "the", "your", "these", "those",
                 "all", "pending", "failed", "learning", "download", "queued", "batch"}
STATUS_WORDS = {"interviewing": "interview", "offered": "offer"}


def _n(match: re.Match[str]) -> int:
    return int(next(value for key, value in match.groupdict().items() if key.startswith("n") and value))


def job_request(text: str) -> dict[str, Any] | None:
    raw = " ".join(str(text or "").split())
    if not raw or len(raw) > 300:
        return None
    if re.fullmatch(_LEAD + r"(?:find|show|list)\s+(?:me\s+)?(?:h[ -]?1[ -]?b\s+)?(?:sponsors|sponsoring\s+employers)" + _END, raw, re.IGNORECASE):
        return {"tool": "job_sponsorship", "arguments": {}}
    if match := re.fullmatch(_LEAD + r"(?:check\s+)?(?:sponsorship|stem[ -]?opt|h[ -]?1[ -]?b)(?:\s+(?:eligibility|research))?\s+(?:for\s+)?job\s+" + _N + _END, raw, re.IGNORECASE):
        return {"tool": "job_sponsorship", "arguments": {"number": _n(match)}}
    if match := re.fullmatch(_LEAD + r"(?:check|research)\s+sponsorship\s+(?:at|for)\s+(?P<company>.+?)" + _END, raw, re.IGNORECASE):
        return {"tool": "job_sponsorship", "arguments": {"company": match["company"]}}
    if re.fullmatch(_LEAD + r"(?:show\s+)?(?:my\s+)?job\s+matches\s+(?:sorted\s+)?by\s+(?:pay|salary)" + _END, raw, re.IGNORECASE):
        return {"tool": "job_matches", "arguments": {"sort_by": "pay"}}
    if match := re.fullmatch(_LEAD + r"(?:i(?:['’]m|\s+am)|my\s+work\s+status\s+is)\s+(?:on\s+)?(?P<status>stem[ -]?opt|opt|cpt|h[ -]?1[ -]?b|us\s+citizen|permanent\s+resident)" + _END, raw, re.IGNORECASE):
        status = re.sub(r"[ -]", "", match["status"].lower())
        status = {"stemopt": "stem_opt", "uscitizen": "citizen", "permanentresident": "permanent_resident"}.get(status, status)
        return {"tool": "job_profile", "arguments": {"action": "set", "preferences": {"work_status": status}}}
    for pattern in (_CV2, _CV):
        if match := pattern.match(raw):
            path = match["path"].strip().strip("\"'")
            if re.search(r"[\\/]|\.(?:pdf|docx|txt|md)$", path, re.IGNORECASE):
                return {"tool": "job_profile", "arguments": {"action": "read_cv", "path": path}}
    if match := _SEND.match(raw):
        return {"tool": "job_application_send", "arguments": {"number": _n(match)}}
    if match := _PREPARE.match(raw):
        return {"tool": "job_application_prepare", "arguments": {"number": _n(match)}}
    if match := _FILL.match(raw):
        return {"tool": "job_application_fill", "arguments": {"number": _n(match)}}
    if match := _INSIGHT.match(raw):
        explain = bool(re.search(r"(?i)\b(?:good|right|worth|move|career|everything)\b", raw))
        return {"tool": "job_insight", "arguments": {"number": _n(match), **({"explain": True} if explain else {})}}
    if _CV_REVIEW.match(raw):
        return {"tool": "job_cv_review", "arguments": {}}
    if _LEARN.match(raw):
        return {"tool": "job_learning_plan", "arguments": {}}
    if _PROSPECTS.match(raw):
        return {"tool": "job_prospects", "arguments": {}}
    if match := _DISCOVER.match(raw):
        company = (match["co1"] or match["co2"] or match["co3"] or "").strip()
        if company:
            return {"tool": "job_discover_company", "arguments": {"company": company}}
    if match := _OPEN.match(raw):
        return {"tool": "job_open", "arguments": {"number": _n(match)}}
    if match := _STATUS.match(raw):
        word = (match["status"] or match["status2"] or "rejected").lower()
        return {"tool": "job_applications", "arguments": {"action": "update", "number": _n(match), "status": STATUS_WORDS.get(word, word)}}
    if match := _HIDE.match(raw):
        return {"tool": "job_hide", "arguments": {"number": _n(match)}}
    if match := _DETAIL.match(raw):
        return {"tool": "job_detail", "arguments": {"number": _n(match)}}
    if _MATCHES.match(raw):
        return {"tool": "job_matches", "arguments": {}}
    if _APPLICATIONS.match(raw):
        return {"tool": "job_applications", "arguments": {"action": "list"}}
    if _SOURCES.match(raw):
        return {"tool": "job_sources", "arguments": {}}
    if _PROFILE.match(raw):
        return {"tool": "job_profile", "arguments": {"action": "show"}}
    if match := _ADD.match(raw):
        return {"tool": "job_profile", "arguments": {"action": "add_skill", "skill": match["skill"].strip()}}
    if match := _REMOVE.match(raw):
        return {"tool": "job_profile", "arguments": {"action": "remove_skill", "skill": match["skill"].strip()}}
    if match := _FOLLOW.match(raw):
        action = "follow" if match["verb"].lower() == "follow" else "unfollow"
        return {"tool": "job_profile", "arguments": {"action": action, "company": f"{match['ats'].lower()}:{match['co'].lower()}"}}
    if _REMOTE_ONLY.match(raw):
        return {"tool": "job_profile", "arguments": {"action": "set", "preferences": {"remote_only": True}}}
    if match := _RELOCATE.match(raw):
        return {"tool": "job_profile", "arguments": {"action": "set",
                "preferences": {"willing_to_relocate": not (match["no"] or match["no2"])}}}
    if match := _SALARY.match(raw):
        amount = float(match["amount"].replace(",", "")) * (1000 if match["k"] else 1)
        currency = {"dollar": "USD", "euro": "EUR", "pound": "GBP", "rupee": "INR"}.get((match["cur"] or "").lower().rstrip("s"),
                                                                                         (match["cur"] or "").upper())
        return {"tool": "job_profile", "arguments": {"action": "set", "preferences": {"min_salary": amount,
                                                                                       **({"salary_currency": currency} if currency else {})}}}
    if match := _AUTO.match(raw):
        on = not match["off"] and (match["state"] or "on").lower() == "on"
        return {"tool": "job_profile", "arguments": {"action": "set", "preferences": {"auto_prepare": on}}}
    if match := _WANT.match(raw):
        title = match["title"].strip()
        if title.lower() not in NOT_JOB_WORDS and not re.fullmatch(r"(?i)new|good|better|different|full[\s-]time|part[\s-]time", title):
            preferences: dict[str, Any] = {"target_titles": [title]}
            if match["remote"]:
                preferences["remote_only"] = True
            if match["where"]:
                preferences["locations"] = [match["where"].strip()]
            return {"tool": "job_profile", "arguments": {"action": "set", "preferences": preferences}}
    search_text, depth = raw, ""
    if qualifier := re.match(r"(?i)^(deep|maximum)\s+(search(?:\s+for)?|find|look\s+for)\b", search_text):
        depth = qualifier[1].lower()
        search_text = qualifier[2] + search_text[qualifier.end():]
    if not re.search(r"(?i)\b(?:in|at|near)\s+(?:worldwide|whole world|anywhere|globally)\s*$", search_text):
        search_text = re.sub(r"(?i)\s+(?:worldwide|whole world|anywhere|globally)\s*$", " in Worldwide", search_text)
    if match := _FIND.match(search_text):
        what = (match["what"] or "").strip()
        if what.lower().split()[0:1] and what.lower().split()[0] in NOT_JOB_WORDS:
            return None
        if re.search(r"(?i)\b(?:background|research|scheduled|running|automation)\s+jobs?\b", raw):
            return None
        arguments: dict[str, Any] = {}
        if depth:
            arguments["depth"] = depth
        if what and what.lower() not in ("a", "new", "some", "good"):
            arguments["query"] = what
        if match["remote"]:
            arguments["remote"] = True
        if match["where"]:
            arguments["location"] = match["where"].strip()
        return {"tool": "job_search", "arguments": arguments}
    return None


def direct_plan(text: str, names: Any) -> dict[str, Any] | None:
    found = job_request(text)
    if not found or found["tool"] not in names:
        return None
    return {"description": "Work with the owner's job search: public postings, their profile, matches and applications.",
            "tool": found["tool"], "arguments": found["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}


# ---------------------------------------------------------------------- replies
def _row_line(row: dict[str, Any], location_filter: str = "") -> str:
    parts = [f"#{row['number']} {row['title']} at {row['company']}", f"{row['score']} ({row['verdict']})"]
    if row.get("mode") and row["mode"] != "unknown":
        parts.append(row["mode"])
    if row.get("location"):
        place = row["location"][:40]
        # "Python Developer · remote · United States" under an India filter looked like a wrong result:
        # the board listed the US, the employer's text said "work remotely - anywhere from India".
        from app.jobs.sources import listed_in_location
        if (location_filter and location_filter.casefold() not in {"remote", "worldwide", "whole world", "all locations", "global", "anywhere", "world"}
                and not listed_in_location(row["location"], location_filter)):
            place += " (listed; filter matched stated eligibility)"
        parts.append(place)
    if row.get("pay"):
        parts.append(row["pay"])
    parts.append(row["source_name"])
    line = "  " + " · ".join(parts)
    if row.get("matched"):
        line += f"\n      has: {', '.join(row['matched'][:5])}"
    if row.get("missing_required"):
        line += f" · missing: {', '.join(row['missing_required'][:4])}"
    if row.get("warnings"):
        line += " · ⚠ " + "; ".join(row["warnings"][:2])
    if row.get("application"):
        line += f" · {row['application']}"
    return line


def report_lines(value: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if value.get("source_access_view"):
        lines = [("Opened official site: " if value.get("source_opened") else "Source access: ") + value["source"],
                 value["official_url"], f"Enabled: {value['enabled']} · Observed access: {value['access_status']}"]
        if value.get("last_read"):
            lines.append(f"Last read: {value['last_read']} · read {value['read']} · kept {value['kept']}")
        if value.get("last_error"):
            lines.append("Last error: " + value["last_error"])
        if value.get("retry_at"):
            lines.append("Retry after: " + value["retry_at"])
        return lines + [value["automatic_instruction"], value["manual_instruction"]]
    if value.get("browser_search"):
        page = value.get("page") or {}
        attempts = [f"Provider {item['engine']}: {item['status']}" + (f"; retry after {item['retry_at']}" if item.get("retry_at") else "")
                    + (f"; {item['error']}" if item.get("error") else "")
                    for item in value.get("attempts", [])]
        return [value["next_step"], *attempts, value["url"], str(page.get("title", "")), str(page.get("text", ""))[:12000]]
    if value.get("sponsorship_view"):
        from app.jobs.sponsorship import research_lines
        return research_lines(value)
    if value.get("sponsor_import"):
        return [f"Imported {value['rows']} employer-history rows for {value['employers']} employers from {value['file']}.",
                value["provenance"], "Historical decisions do not establish current sponsorship or a job opening."]
    if value.get("search") or value.get("matches_list"):
        if value.get("search"):
            read = ", ".join(f"{item['source']} {item['kept']}/{item['read']}" + (" (failed)" if item.get("error") else "") for item in value["read"])
            lines.append(f"Searched for {', '.join(value['terms'])}: {value['new']} new job{'s' if value['new'] != 1 else ''}"
                         + (f" · read {read}" if read else "") + (f" · read within the hour, not again: {', '.join(value['skipped_recent'])}"
                                                                   if value["skipped_recent"] else ""))
            for item in value['read']:
                if item.get('error'):
                    lines.append(f"Source unavailable: {item['source']} — {item['error']}")
                    if item.get("retry_at"):
                        lines.append(f"Retry after: {item['retry_at']} ({item['access_status']}).")
                if item.get("limited"):
                    lines.append(f"Coverage limit: {item['source']} — {item.get('pages', 0)} page(s); {item.get('stop_reason', 'partial results')}.")
            for item in value.get("deferred_sources", []):
                lines.append(f"Source cooling down: {item['source']} — {item['reason']}; retry after {item['retry_at']}. Saved jobs remain available.")
        criteria = value.get('criteria') or {}
        if criteria.get('location'):
            lines.append('Location filter: ' + criteria['location'])
        if 'matching_total' in value:
            offset = value.get("offset", 0)
            lines.append(f"Showing {len(value['matches'])} of {value['matching_total']} matching saved postings."
                         + (f" Results {offset + 1}–{offset + len(value['matches'])}." if value['matches'] else ""))
            if value.get("next_offset") is not None:
                lines.append(f"More saved matches: use job_matches with offset {value['next_offset']} and the same filters, or page through Jobs → Matches.")
        if coverage := value.get("coverage"):
            lines.append(f"Search depth: {coverage.get('depth', 'normal')}; {coverage.get('requests', 'unknown')} API requests. "
                         + coverage.get("stop_reason", "Configured source snapshots checked") + ". Coverage is not exhaustive.")
        if not value["profile_ready"]:
            lines.append("Your profile has no skills yet, so scores lean on titles only. Say “read my CV from C:\\path\\cv.pdf”.")
        if value["matches"]:
            lines.append("Best matches:")
            lines += [_row_line(row, criteria.get("location") or "") for row in value["matches"]]
            lines.append("Say “show job N” for the details, “prepare an application for job N”, or “hide job N”.")
        else:
            lines.append(value.get('empty_reason') or 'No saved jobs match these filters. Broaden the role, location or work-mode filters, or search additional public boards.')
        lines.append(value["basis"])
    elif value.get("detail"):
        job, reading, match = value["job"], value["reading"], value["match"]
        posted = f" · posted {str(job['posted_at'])[:10]}" if job.get("posted_at") else ""
        lines.append(f"Job {job['number']}: {job['title']} at {job['company']} · {value['score']} ({value['verdict']}) · "
                     f"{job['source_name']}{posted} · {job['url']}")
        lines.append(reading["summary"])
        skills = reading["skills"]
        if skills["required"]:
            lines.append("Required: " + ", ".join(skills["required"]))
        if skills["nice"]:
            lines.append("Nice to have: " + ", ".join(skills["nice"]))
        if skills["mentioned"]:
            lines.append("Also mentioned: " + ", ".join(skills["mentioned"][:12]))
        facts = [f"work: {reading['mode']}", f"pay: {pay_text(reading['salary'])}", f"written in {reading['written_in']}"]
        if reading["regions"]:
            facts.append("where: " + ", ".join(reading["regions"]))
        if reading["sponsorship"] != "not stated":
            facts.append(f"visa sponsorship {reading['sponsorship']}")
        if reading["education"]:
            facts.append("education: " + ", ".join(reading["education"][:2]))
        lines.append(" · ".join(facts))
        lines.append("Score parts:")
        lines += [f"  {name}: {part['value'] * 100:.0f}% × {part['weight'] * 100:.0f}% · {part['reason']}" for name, part in match["parts"].items()]
        if match["blocked"]:
            lines.append("Not eligible: " + "; ".join(match["blocked"]) + ".")
        lines += match["advice"]
        record = value.get("application_record")
        if record:
            lines.append(f"Application: {record['status']}" + (f" · folder {record['folder']}" if record.get("folder") else ""))
        lines.append(value["basis"])
    elif value.get("profile_view"):
        lines.append(f"Job profile{' from ' + value['cv_file'] if value['cv_file'] else ' (no CV read yet)'}:")
        if value["name"]:
            lines.append(f"  {value['name']}" + (f" · {value['email']}" if value["email"] else ""))
        lines.append(f"  Experience: {value['years'] if value['years'] is not None else 'unknown'} years · level {value['level'] or 'unknown'}")
        if value["titles"]:
            lines.append("  Roles: " + "; ".join(value["titles"][:4]))
        lines.append("  Skills: " + (", ".join(value["skills"][:30]) or "none yet")
                     + (f" (from your roles: {', '.join(value['implied_skills'])})" if value.get("implied_skills") else ""))
        if value["languages"]:
            lines.append("  Languages: " + ", ".join(value["languages"]))
        preferences = value["preferences"]
        lines.append("  Looking for: " + (", ".join(preferences["target_titles"]) or "not set") + " · search terms: "
                     + (", ".join(preferences["search_terms"]) or "not set") + (" · remote only" if preferences["remote_only"] else "")
                     + (" · will relocate" if preferences.get("willing_to_relocate") else "")
                     + (f" · in {', '.join(preferences['locations'])}" if preferences["locations"] else "")
                     + (f" · minimum {preferences['min_salary']:,.0f} {preferences['salary_currency']}" if preferences["min_salary"] else ""))
        lines.append("  Automatic preparing for strong matches: " + ("on" if preferences["auto_prepare"] else "off")
                     + (f" · following {', '.join(preferences['companies'])}" if preferences["companies"] else ""))
        if value.get("rescored"):
            lines.append(f"Re-scored {value['rescored']} job{'s' if value['rescored'] != 1 else ''} against the updated profile.")
        if "wanted_matches" in value:
            if value["wanted_matches"]:
                lines.append(f"Saved jobs that fit this now ({len(value['wanted_matches'])} of {value['wanted_total']}):")
                lines += [_row_line(row, value.get("wanted_location") or "") for row in value["wanted_matches"]]
                lines.append("Say “find jobs” to check the boards for new ones, or “show job N” for details.")
            else:
                lines.append("No saved job fits this yet. Say “find jobs” and I'll search the public boards for these roles; "
                             "your places and remote wish then shape each score.")
        lines.append(value["basis"])
    elif value.get("prepared"):
        lines.append(f"Application for job {value['number']} ({value['title']} at {value['company']}) is ready in {value['folder']}:")
        lines.append("  " + ", ".join(value["files"]) + f" · letter: {value['letter']}" + ("" if value["cv_attached"] else
                                                                                           " · no CV file in your profile, so none attached"))
        if value["missing_required"]:
            lines.append(f"  It asks for {', '.join(value['missing_required'][:5])}, which your profile does not show; the letter does not claim them.")
        lines.append(value["next_step"])
        lines.append(value["basis"])
    elif value.get("proposed"):
        lines.append(f"Your application for job {value['number']} is in the confirmation window: {value.get('title', '')}.")
        lines.append(value["how_to_confirm"])
    elif value.get("opened"):
        lines.append(f"Opened the application page for job {value['number']} ({value['title']} at {value['company']}): {value['url']}")
        lines.append(value["next_step"])
    elif value.get("status_changed"):
        lines.append(f"Job {value['number']} ({value['title']} at {value['company']}) is now marked {value['status']}."
                     + (f" HAYAT will remind you to follow up on {value['follow_up_at'][:10]} if you hear nothing." if value["status"] == "applied"
                        and value.get("follow_up_at") else ""))
    elif value.get("applications_view"):
        rows = value["applications"]
        if not rows:
            lines.append("No applications yet. Say “show my job matches”, then “prepare an application for job N”.")
        for row in rows[:20]:
            lines.append(f"  #{row['number']} {row['title']} at {row['company']} · {row['status']} · updated {row['updated_at'][:10]}"
                         + (f" · applied {row['applied_at'][:10]}" if row.get("applied_at") else "")
                         + (f" · follow up {row['follow_up_at'][:10]}" if row["status"] == "applied" and row.get("follow_up_at") else ""))
    elif value.get("sources_view"):
        state = value["state"]
        lines.append("Job sources (keyless, robots.txt allows reading; each credited and applied to on its own page):")
        for item in value["catalog"]:
            if item["kind"] != "feed":
                continue
            seen = state.get(item["name"]) or {}
            lines.append(f"  {item['title']} ({item['site']}): {item['about']} · " + ("on" if item["name"] in value["enabled"] else "off")
                         + (f" · last read {str(seen.get('fetched_at'))[:16].replace('T', ' ')} UTC, kept {seen.get('kept')}" if seen.get("fetched_at") else "")
                         + (f" · error: {seen['error']}" if seen.get("error") else ""))
        lines.append("  Company boards followed: " + (", ".join(value["companies"]) or "none (say “follow stripe on greenhouse”)"))
        lines.append(value["basis"])
    elif value.get("insight"):
        lines += insight_lines(value)
    elif value.get("learning_plan"):
        from app.jobs.insight import learning_lines
        lines += learning_lines(value)
    elif value.get("prospects"):
        from app.jobs.insight import prospects_lines
        lines += prospects_lines(value)
    elif value.get("discover_view"):
        boards = value.get("boards") or []
        if not boards:
            lines.append(f"No public careers board found for {value['company']} on Greenhouse, Lever or Ashby. "
                         "It may use another system or advertise only on the big boards.")
        for board in boards:
            lines.append(f"{value['company']} posts on {board['ats'].title()} ({board['follow']}): "
                         f"{board['jobs']} open role{'s' if board['jobs'] != 1 else ''}.")
            lines += [f"  • {title}" for title in board.get("samples", [])]
            if board.get("evidence"):
                lines.append("  Found by: " + board["evidence"])
            if board.get("url"):
                lines.append("  Board: " + board["url"])
            if value.get("followed") == board["follow"]:
                lines.append("  Added to your searches; it will be included from now on.")
            else:
                lines.append(f"  Say \"follow {board['slug']} on {board['ats']}\" to include it in your searches.")
        lines.append(value["basis"])
    elif value.get("cv_review"):
        lines.append(f"CV review ({value['passed']} of {value['total']} checks passed, read against your best {value['matches_read']} matches):")
        lines += [f"  {'✓' if item['passed'] else '✗'} {item['check']}" + (f": {item['advice']}" if item["advice"] else "") for item in value["checks"]]
        if value["keyword_gaps"]:
            lines.append("Asked by your best matches but not in your CV (add only what is true):")
            lines += [f"  {item['skill']}: {item['jobs']} job{'s' if item['jobs'] != 1 else ''}" for item in value["keyword_gaps"][:8]]
        for fit in value["best_fits"][:3]:
            lines.append(f"For job {fit['number']} ({fit['title']} at {fit['company']}, {fit['score']}): put first "
                         + (", ".join(fit["put_first"]) or "your closest skills") + (f"; it also wants {', '.join(fit['missing'])}" if fit["missing"] else "") + ".")
        lines.append(value["basis"])
    elif value.get("filled_form"):
        lines.append(f"Filled {len(value['filled'])} field{'s' if len(value['filled']) != 1 else ''} on the application page for job {value['number']} "
                     f"({value['title']} at {value['company']}) in HAYAT's browser window:")
        lines += [f"  {item['label']}: {item['value'][:80]}" for item in value["filled"][:12]]
        if value["files"]:
            lines.append("Attach yourself: " + ", ".join(value["files"]) + (f" (your CV: {value['cv']})" if value["cv"] else ""))
        if value["left"]:
            lines.append("Left for you: " + "; ".join(value["left"][:10]))
        lines.append(value["next_step"])
        lines.append(value["basis"])
    elif value.get("hidden"):
        lines.append(f"Job {value['hidden']} is hidden.")
    return lines


def insight_lines(value: dict[str, Any]) -> list[str]:
    lines = [f"Job {value['number']}: {value['title']} at {value['company']} · {value['score']} ({value['verdict']}) · {value['source_name']}",
             value["summary"]]
    if value.get("narrative"):
        lines += ["", "HAYAT's read: " + value["narrative"]]
    fit = value["fit"]
    lines.append("")
    if fit["available"]:
        lines.append(f"How it matches your CV: {fit['percent']}% of its duties ({fit['aligned']} shown, {fit['partly']} partly, {fit['gaps']} not yet)")
        mark = {"aligned": "✓", "partly": "~", "gap": "✗"}
        for row in fit["rows"][:8]:
            lines.append(f"  {mark[row['status']]} {row['duty'][:100]}" + (f"\n      your CV: {row['evidence'][:100]}" if row["evidence"] else ""))
    else:
        lines.append("Duties against your CV: " + fit["reason"] + ".")
    if value["success"]:
        lines.append("How success is measured: " + " | ".join(value["success"][:2]))
    if value["benefit"]:
        lines.append("What it could do for you:")
        lines += [f"  • {item}" for item in value["benefit"]]
    market = value["market"]
    lines.append(f"Around it (HAYAT's own sample): {market['similar']} similar postings seen, {market['trend']}"
                 + (f"; pay {market['pay']['low']:,}–{market['pay']['high']:,} {market['pay']['currency']} a year (median {market['pay']['median']:,}, "
                    f"{market['pay']['postings']} postings)" if market["pay"] else "; too few with pay to compare")
                 + (f"; {market['remote_share']}% remote" if market["remote_share"] is not None else "") + ".")
    if market["top_skills"]:
        lines.append("  Most asked in similar jobs: " + ", ".join(f"{item['skill']} ({item['postings']})" for item in market["top_skills"][:8]))
    lines.append("Your history: " + value["history"]["note"])
    if value["checklist"]:
        lines.append("Instructions applicants miss:")
        lines += [f"  • {item}" for item in value["checklist"]]
    lines.append("Why applications to it fail:")
    lines += [f"  • {item}" for item in value["pitfalls"][:6]]
    lines.append("Your first 90 days, from its duties:")
    lines += [f"  {item['when']}: {item['what']}" for item in value["first_90_days"]]
    prepare = value["prepare"]
    lines.append("Prepare:")
    lines += [f"  Q: {item['question']}" for item in prepare["questions"][:8]]
    if prepare["study"]:
        lines.append("  Study plan:")
        lines += [f"    {item['skill']} ({item['why']}): start at {item['start']}; then {item['practice']}." for item in prepare["study"][:6]]
    lines += [f"  CV: {item}" for item in prepare["tailoring"]]
    lines.append("  Ask them: " + " ".join(prepare["ask_them"]))
    lines.append(value["basis"])
    if value.get("application_plan"):
        plan = value["application_plan"]
        lines += ["", "Application readiness: " + plan["state"],
                  *["  Check: " + item for item in plan["unknowns"]],
                  "Prepare a packet to save the full checklist, practice and referral research in preparation.md."]
    return lines


def report(history: list[dict[str, Any]]) -> str | None:
    sections = []
    for event in history or []:
        if not str(event.get("tool", "")).startswith("job_"):
            continue
        result = event.get("result") or {}
        if not result.get("success") or not (event.get("verification") or {}).get("passed"):
            continue
        value = result.get("output") or {}
        if value.get("jobs_view"):
            sections.append("\n".join(report_lines(value)))
    return "\n\n".join(section for section in sections if section) or None
