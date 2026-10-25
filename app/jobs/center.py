"""The job center: search, read, match, prepare, send (after confirmation), track, follow up.

"Automatic" here means HAYAT does the searching, reading, matching and preparing on its own, on a
schedule, and tells the owner what it found. Sending an application acts as the owner toward an
employer, so it is always a proposal the owner confirms in the confirmation window, and an
application on an employer's own web form is finished by the owner on that page.
"""
from __future__ import annotations

import json
import hashlib
import logging
import re
import shutil
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app.jobs.match import score
from app.jobs.profile import Profile, default_terms
from app.jobs.sources import FEEDS, SOURCES, collect, matches_location, relevant, same_posting
from app.jobs.store import JobStore
from app.jobs.text import skills_in, slug
from app.jobs.understand import pay_text, understand
from app.logging import log_swallowed

_log = logging.getLogger(__name__)
REFRESH_MINUTES = 60  # each source asks not to be read more often than this


class JobCenter:
    def __init__(self, folder: Path | str, fetcher: Any, *, mailer: Any = None, gate: Any = None, model: Any = None,
                 opener: Callable[[str], Any] = webbrowser.open, browser: Any = None) -> None:
        self.folder = Path(folder)
        self.store = JobStore(self.folder / "jobs.db")
        self.profile = Profile(self.folder / "profile.json")
        self.fetcher, self.mailer, self.gate, self.model, self.opener = fetcher, mailer, gate, model, opener
        self.browser = browser

    # ------------------------------------------------------------------ searching and matching
    def source_access(self, source: str) -> dict[str, Any]:
        """Report observed access state; never treat a local setting as publisher permission."""
        from app.jobs.sources import BOARD, SOURCES
        name = source.strip()
        board = BOARD.fullmatch(name)
        if name in FEEDS:
            url = SOURCES[name]["site"]
        elif board:
            roots = {"lever": "https://jobs.lever.co/", "greenhouse": "https://job-boards.greenhouse.io/",
                     "ashby": "https://jobs.ashbyhq.com/"}
            url = roots[board[1]] + board[2]
        else:
            raise ValueError("Choose a known public feed or a board such as lever:company, greenhouse:company or ashby:company.")
        preferences = self.profile.load()["preferences"]
        enabled = name in preferences["sources"] or name in preferences["companies"]
        state = self.store.source_state().get(name, {})
        cooldown = self.store.source_cooldowns().get(name.split(":", 1)[0], {})
        status = (cooldown.get("reason") or ("last_request_failed" if state.get("error") else
                  "last_request_succeeded" if state.get("fetched_at") else "not_checked"))
        instruction = ("Automatic requests are deferred until the displayed retry time; other public sources remain available." if cooldown else
                       "This source is paused in your preferences." if not enabled else
                       "HAYAT may attempt its public API. The provider's current robots rules and access controls are checked during retrieval.")
        return {"jobs_view": True, "source_access_view": True, "source": name, "official_url": url, "enabled": enabled,
                "access_status": status, "retry_at": cooldown.get("retry_at"), "last_error": state.get("error") or "",
                "last_read": state.get("fetched_at"), "read": state.get("read"), "kept": state.get("kept"),
                "automatic_instruction": instruction,
                "manual_instruction": "Open the official site to read it yourself or complete the provider's verification/sign-in. "
                                      "Manual browsing does not import jobs, submit an application or change API permissions/cooldowns."}

    def open_source(self, source: str) -> dict[str, Any]:
        report = self.source_access(source)
        if self.opener(report["official_url"]) is False:
            raise RuntimeError("The browser could not be opened. Open this official address manually: " + report["official_url"])
        return {**report, "source_opened": True}

    def browser_search(self, platform: str = "Employer boards", query: str = "", location: str = "", scan: bool = False,
                       engine: str = "Google", resume: bool = False, check=None) -> dict[str, Any]:
        from app.jobs.browser_search import browse
        return browse(self, platform, query, location, scan, engine, resume, check)

    def search(self, query: str = "", location: str = "", remote: bool | None = None, force: bool = False,
               depth: str = "normal", limit: int = 100, check=None, progress=None) -> dict[str, Any]:
        """Refresh explicit queries; reuse the hourly cache for background rounds, then filter all saved matches."""
        from app.jobs.search_depth import DEPTHS
        if depth not in DEPTHS:
            raise ValueError("Choose normal, deep or maximum job search depth.")
        limit = max(1, min(int(limit), 500))
        data = self.profile.load()
        preferences = data["preferences"]
        terms = [term.strip() for term in re.split(r"\s*(?:,|\bor\b)\s*", query, flags=re.IGNORECASE) if term.strip()] if query else \
            (preferences["search_terms"] or default_terms(data))
        if not terms:
            raise ValueError("Tell me what to look for first: “find python jobs”, or read your CV with “read my CV from C:\\path\\cv.pdf”.")
        sources = [name for name in preferences["sources"] if name in FEEDS] + preferences["companies"]
        state = self.store.source_state()
        cooldowns = self.store.source_cooldowns()
        deferred = [{"source": name, **{key: value for key, value in cooldowns[name.split(':', 1)[0]].items() if key != "name"}}
                    for name in dict.fromkeys(sources) if name.split(":", 1)[0] in cooldowns]
        deferred_names = {item["source"] for item in deferred}
        fresh = datetime.now(UTC) - timedelta(minutes=REFRESH_MINUTES)
        def stale(name):
            if state.get(name, {}).get("error"):
                return True
            try:
                stamp = datetime.fromisoformat(state.get(name, {}).get("fetched_at") or "")
                return stamp.tzinfo is None or stamp < fresh
            except (ValueError, TypeError):
                return True
        due = [name for name in dict.fromkeys(sources) if name not in deferred_names and (force or query or depth != "normal" or stale(name))]
        found = ({"jobs": [], "sources": []} if not due else
                 collect(self.fetcher, due, terms) if depth == "normal" and check is None and progress is None else
                 collect(self.fetcher, due, terms, depth, check, progress))
        deferred.extend(found.get("deferred_sources", []))
        deferred_names.update(item["source"] for item in deferred)
        for item in found["sources"]:
            self.store.record_source(item["source"], item["read"], item["kept"], item.get("error", ""))
            if item.get("retry_at"):
                self.store.defer_source(item["source"], item["retry_at"], item["access_status"])
        # Keep collected roles for future filters. A location view must not discard the other locations from the cache.
        unique, identities = [], {}
        for row in found["jobs"]:
            identity = (row["title"].casefold().strip(), row["company"].casefold().strip())
            peers = identities.setdefault(identity, [])
            if not any(same_posting(row, saved) for saved in peers) and not self.store.duplicate(row):
                unique.append(row)
                peers.append(row)
        new = self.store.upsert(unique)
        self.rescore()  # Existing postings can change pay, requirements or sponsorship on a later fetch.
        matches = [row for row in self.store.iter_matches() if relevant(row, terms) and matches_location(row, location)
                   and (remote is None or (row["reading"].get("mode") == "remote" if remote else
                                          row["reading"].get("mode") in ("on-site", "hybrid")))]
        failed = sum(bool(item.get("error")) for item in found["sources"])
        summary = {"attempted": len(found["sources"]), "succeeded": len(found["sources"]) - failed, "failed": failed,
                   "cached": len(set(sources) - set(due) - deferred_names)}
        empty_reason = ""
        if not matches:
            empty_reason = ("No eligible saved jobs match these terms and location/work-mode filters. "
                            "Try broader role terms, clear a location filter, or include blocked roles in saved matches.")
            if failed:
                empty_reason += " Some sources failed; inspect the source errors and retry later. This is not a complete search of the market."
            if deferred:
                empty_reason += " Some sources are cooling down after access restrictions; their retry times are shown."
            if not sources:
                empty_reason += " Enable at least one public source or follow an employer board in Preferences."
        return {"jobs_view": True, "search": True, "terms": terms, "read": found["sources"], "deferred_sources": deferred,
                "skipped_recent": [name for name in sources if name not in due and name not in deferred_names],
                "new": len(new), "matches": [self.brief(row) for row in matches[:limit]], "matching_total": len(matches),
                "offset": 0, "limit": limit, "next_offset": limit if len(matches) > limit else None,
                "coverage": found.get("coverage", {"depth": depth, "exhaustive": False}),
                "criteria": {"query": query, "terms": terms, "location": location, "remote": remote},
                "source_summary": summary, "empty_reason": empty_reason, "counts": self.store.counts(),
                "profile_ready": bool(self.profile.skills(data)),
                "basis": "Scores are HAYAT's reading of the posting and your profile, not the employer's decision. Sources are credited on each job; "
                         "apply through the posting's own page. Results include saved postings that match every active filter; "
                         "a saved posting is not proof the requisition is still open. Explicit searches refresh sources outside publisher cooldowns; background rounds use the hourly cache."}

    def rescore(self, numbers: list[int] | None = None) -> int:
        """Read and score jobs (all of them when `numbers` is None, e.g. after the profile changed)."""
        data = self.profile.load()
        skills = self.profile.skills(data)
        learned = self.learned()
        count = 0
        for number in (self.store.numbers() if numbers is None else numbers):
            row = self.store.job(number)
            reading = understand(row)
            self.store.set_match(number, reading, score(row, reading, data, skills, learned=learned))
            count += 1
        return count

    def learned(self) -> dict[str, float]:
        """What the owner's own choices say: titles of jobs pursued against jobs hidden."""
        from app.jobs.match import learned_from
        pursued = [row["title"] for row in self.store.applications() if row["status"] in ("prepared", "opened", "applied", "interview", "offer")]
        hidden = [row["title"] for row in self.store.matches(limit=500, include_blocked=True, include_hidden=True) if row["hidden"]]
        return learned_from(pursued, hidden)

    @staticmethod
    def brief(row: dict[str, Any]) -> dict[str, Any]:
        reading, match = row.get("reading") or {}, row.get("match") or {}
        return {"number": row["number"], "title": row["title"], "company": row["company"], "location": row["location"],
                "revision": hashlib.sha256(json.dumps([reading, match, row.get("description"), row.get("application")],
                                                     sort_keys=True, default=str).encode("utf-8")).hexdigest(),
                "mode": reading.get("mode", ""), "score": row.get("score"), "verdict": row.get("verdict"), "posted_at": row["posted_at"],
                "source_name": row["source_name"], "url": row["url"], "pay": pay_text(reading.get("salary")) if reading.get("salary") else "",
                "matched": match.get("matched", [])[:6], "missing_required": match.get("missing_required", [])[:6],
                "blocked": match.get("blocked", []), "warnings": reading.get("warnings", []), "application": row.get("application")}

    def matches(self, min_score: int = 0, limit: int = 15, include_blocked: bool = False, query: str = "",
                sort_by: str = "score", sponsorship: str = "all", remote_only: bool = False, location: str = "",
                offset: int = 0) -> dict[str, Any]:
        if sort_by not in ("score", "newest", "pay") or sponsorship not in ("all", "offered", "not stated", "not offered", "conflicting"):
            raise ValueError("Choose score, newest or pay; sponsorship can be all, offered, not stated, not offered or conflicting.")
        rows = list(self.store.iter_matches(min_score, include_blocked))
        if query.strip():
            terms = [term.strip() for term in re.split(r"\s*(?:,|\bor\b)\s*", query, flags=re.IGNORECASE) if term.strip()]
            rows = [row for row in rows if relevant(row, terms) or any(term.casefold() in row["company"].casefold() for term in terms)]
        rows = [row for row in rows if (not remote_only or row["reading"].get("mode") == "remote")
                and (sponsorship == "all" or row["reading"].get("sponsorship") == sponsorship) and matches_location(row, location)]
        currency = self.profile.load()["preferences"]["salary_currency"]
        if sort_by == "newest":
            rows.sort(key=lambda row: row.get("posted_at") or "", reverse=True)
        elif sort_by == "pay":
            from app.jobs.insight import _yearly
            def pay_key(row):
                yearly = _yearly(row["reading"].get("salary"))
                return yearly[1] if yearly and yearly[0] == currency else -1
            rows.sort(key=lambda row: (pay_key(row), row["score"]), reverse=True)
        total = len(rows)
        limit, offset = max(1, min(int(limit), 500)), max(0, int(offset))
        rows = rows[offset:offset + limit]
        return {"jobs_view": True, "matches_list": True, "matches": [self.brief(row) for row in rows], "counts": self.store.counts(),
                "matching_total": total, "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < total else None,
                "criteria": {"query": query, "location": location, "remote_only": remote_only,
                                                       "sort_by": sort_by, "sponsorship": sponsorship, "min_score": min_score,
                                                       "include_blocked": include_blocked},
                "profile_ready": bool(self.profile.skills()),
                "basis": f"Sorted by {sort_by} across all saved matches after filtering. Pay sorting uses annualized midpoint in {currency}; "
                         "other currencies and unknown pay stay last. Hourly pay assumes 2,080 hours/year; daily 260 days, weekly 52 weeks. "
                         "Not-eligible jobs are left out unless asked for; sponsorship is what the posting states, not employer verification."}

    def sponsorship(self, number: int | None = None, company: str = "") -> dict[str, Any]:
        from app.jobs.sponsorship import research
        row = self.store.job(number) if number is not None else None
        return research(row, self.profile.load()["preferences"], self.folder / "sponsor-history.json", company)

    def import_sponsors(self, path: str) -> dict[str, Any]:
        from app.jobs.sponsorship import import_history
        report = import_history(Path(path).expanduser(), self.folder / "sponsor-history.json")
        return {"jobs_view": True, "sponsor_import": True, **report}

    def detail(self, number: int) -> dict[str, Any]:
        row = self.store.job(number)
        if "reading" not in row:
            self.rescore([number])
            row = self.store.job(number)
        from app.jobs.readiness import application_routes
        email_problem = "Configure an email account and the confirmation window in this session."
        if self.mailer is not None and self.gate is not None:
            try:
                email_problem = self.mailer.setup_problem() if hasattr(self.mailer, "setup_problem") else ""
            except Exception:
                email_problem = "Email setup could not be checked; review Email setup before sending."
        routes = application_routes(row, browser_available=self.browser is not None,
                                    email_available=not bool(email_problem))
        for route in routes:
            if route["channel"] == "email" and email_problem:
                route["setup_problem"] = email_problem
        return {"jobs_view": True, "detail": True, "application_routes": routes, "job": {key: row[key] for key in ("number", "title", "company", "location", "url",
                                                                                     "apply_url", "source_name", "posted_at", "employment_type",
                                                                                     "application")},
                "description": row["description"][:3000], "reading": row["reading"], "match": row["match"], "score": row["score"],
                "verdict": row["verdict"], "application_record": self.store.application(number),
                "basis": "Every reading comes from the posting's own text; the score is a reading, not a promise."}

    def hide(self, number: int) -> dict[str, Any]:
        self.store.hide(number)
        self.rescore()  # what the owner hides now teaches the scores
        return {"jobs_view": True, "hidden": number}

    def insight(self, number: int, explain: bool = False) -> dict[str, Any]:
        """Everything about one job: role, fit with the CV, benefit, market, history, pitfalls, preparation."""
        from app.jobs.insight import explain as narrate, insight
        row = self.store.job(number)
        if "reading" not in row:
            self.rescore([number])
            row = self.store.job(number)
        data = self.profile.load()
        value = insight(self.store, row, data, self.profile.skills(data))
        if explain and self.model is not None:
            try:
                value["narrative"] = narrate(self.model, value)
            except Exception:  # the facts stand on their own
                log_swallowed(_log, "explaining a job insight")
        return value

    def cv_review(self) -> dict[str, Any]:
        from app.jobs.cv_review import review
        data = self.profile.load()
        best = self.store.matches(limit=40)
        return review(data, self.profile.skills(data), best)

    def learning_plan(self) -> dict[str, Any]:
        from app.jobs.insight import learning_plan
        data = self.profile.load()
        return {"jobs_view": True, **learning_plan(self.store, data, self.profile.skills(data))}

    def prospects(self) -> dict[str, Any]:
        from app.jobs.insight import prospects
        return {"jobs_view": True, **prospects(self.store)}

    def discover_company(self, company: str, follow: bool = False) -> dict[str, Any]:
        """Find a company's public ATS board and its open roles; optionally follow it so searches include it."""
        from app.jobs.sources import discover
        boards = discover(self.fetcher, company)
        followed = None
        if follow and boards:
            board = boards[0]["follow"]
            companies = self.profile.load()["preferences"]["companies"]
            if board not in companies:
                self.profile.set_preferences({"companies": companies + [board]})
                followed = board
        return {"jobs_view": True, "discover_view": True, "company": company, "boards": boards, "followed": followed,
                "basis": "Public keyless ATS boards (Greenhouse, Lever, Ashby), including links from a provided public HTTPS careers page. "
                         "No login or gated pages. A name-based slug is only a lead: verify the employer identity. "
                         "A company not found here may use another system; unpublished jobs cannot be guaranteed."}

    def fill(self, number: int) -> dict[str, Any]:
        """Fill the employer's application form in HAYAT's visible browser and stop: the owner attaches the CV and submits."""
        if self.browser is None:
            raise RuntimeError("HAYAT's browser is not available in this session.")
        from app.jobs.apply_form import fill_plan
        row = self.store.job(number)
        url = row["apply_url"] or row["url"]
        record = self.store.application(number)
        if not record or not record.get("folder") or not Path(record["folder"]).is_dir():
            self.prepare(number, polish=False)
            record = self.store.application(number)
        letter = (Path(record["folder"]) / "cover_letter.md").read_text(encoding="utf-8")
        fields = self.browser.form_fields(url, visible=True)
        data = self.profile.load()
        plan = fill_plan(fields, data, letter)
        filled = self.browser.fill_fields(plan["fill"]) if plan["fill"] else []
        cv = next((path for path in Path(record["folder"]).iterdir() if path.name.startswith("CV.")), None)
        if record["status"] in ("saved", "prepared"):
            self.store.set_status(number, "opened", f"form pre-filled ({len(filled)} fields)")
        return {"jobs_view": True, "filled_form": True, "number": number, "title": row["title"], "company": row["company"], "url": url,
                "filled": filled, "left": plan["left"], "files": plan["files"], "cv": str(cv) if cv else "",
                "next_step": "Check every field in the browser window, attach your CV" + (f" ({cv})" if cv else "")
                             + ", answer what is left, and press their submit button yourself. Then say “mark job "
                             + str(number) + " applied”.",
                "basis": "HAYAT typed only facts from your profile and letter, attached nothing and pressed nothing: sending is yours."}

    # ------------------------------------------------------------------ profile
    def read_cv(self, path: str) -> dict[str, Any]:
        data = self.profile.read_cv(path)
        rescored = self.rescore()
        return {"jobs_view": True, "profile_view": True, **self.profile_summary(data), "rescored": rescored}

    def profile_summary(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        data = data or self.profile.load()
        cv = data["cv"]
        from app.jobs.profile import implied
        return {"implied_skills": implied(data), "cv_file": cv.get("file", ""), "name": cv.get("name", ""), "email": cv.get("email", ""), "years": cv.get("years_experience"),
                "level": data["preferences"]["level"] or cv.get("level", ""), "titles": cv.get("titles", []),
                "skills": self.profile.skills(data), "languages": cv.get("languages", []), "education": cv.get("education", []),
                "preferences": data["preferences"],
                "basis": ("Read from your CV with rules; a skill is here because the CV names it or you added it."
                          if cv.get("file") else
                          "No CV read yet, so skills are only the ones you add. Say “read my CV from C:\\path\\cv.pdf” "
                          "for scores that know your experience.")}

    def show_profile(self) -> dict[str, Any]:
        return {"jobs_view": True, "profile_view": True, **self.profile_summary()}

    def set_preferences(self, changes: dict[str, Any]) -> dict[str, Any]:
        data = self.profile.set_preferences(changes)
        if {"target_titles"} & set(changes) and "search_terms" not in changes:
            data["preferences"]["search_terms"] = default_terms(data)
            self.profile.save(data)
        rescored = self.rescore()
        # "I want remote data analyst jobs in Hyderabad" saved the wish and showed no job (live, 2026-10-24).
        preferences = data["preferences"]
        terms = preferences.get("search_terms") or default_terms(data)
        places = preferences.get("locations") or []
        # Any part of any preferred place counts: "Hyderabad, India" keeps a remote role open to all of India, and a
        # remote role with no country restriction is open to Hyderabad too.
        parts = [part.strip() for place in places for part in str(place).split(",") if part.strip()]

        def fits_place(row):
            reading = row["reading"]
            unrestricted = reading.get("mode") == "remote" and (not reading.get("regions") or "Worldwide" in reading["regions"])
            return not parts or unrestricted or any(matches_location(row, part) for part in parts)
        fitting = [row for row in self.store.iter_matches() if (not terms or relevant(row, terms)) and fits_place(row)
                   and (not preferences.get("remote_only") or row["reading"].get("mode") == "remote")]
        return {"jobs_view": True, "profile_view": True, **self.profile_summary(), "changed": sorted(changes), "rescored": rescored,
                "wanted_matches": [self.brief(row) for row in fitting[:5]], "wanted_total": len(fitting),
                "wanted_location": places[0] if len(places) == 1 else ""}

    def change_skill(self, skill: str, add: bool) -> dict[str, Any]:
        self.profile.change_skill(skill, add)
        rescored = self.rescore()
        return {"jobs_view": True, "profile_view": True, **self.profile_summary(), "changed": ["skills"], "rescored": rescored}

    # ------------------------------------------------------------------ applying
    def packet_folder(self, row: dict[str, Any]) -> Path:
        return self.folder / "applications" / f"{row['number']}-{slug(row['company'], 24)}-{slug(row['title'], 30)}"

    def prepare(self, number: int, polish: bool = True) -> dict[str, Any]:
        """Write the application packet: cover letter (Markdown and Word), answers sheet, the posting, the CV unchanged."""
        row = self.store.job(number)
        if "reading" not in row:
            self.rescore([number])
            row = self.store.job(number)
        data = self.profile.load()
        folder = self.packet_folder(row)
        folder.mkdir(parents=True, exist_ok=True)
        letter, how = cover_letter(row, data, self.profile.skills(data)), "template from your profile"
        if polish and self.model is not None:
            try:
                better = polished(self.model, letter, row, self.profile.skills(data))
                if better:
                    letter, how = better, "polished by HAYAT's local model, checked to add no skill you do not have"
            except Exception:  # the template stands on its own
                log_swallowed(_log, "polishing a cover letter")
        files = {"cover_letter.md": letter, "answers.md": answers(row, data, self.profile.skills(data)), "posting.md": posting_text(row)}
        from app.jobs.readiness import plan_lines
        plan = self.insight(number)["application_plan"]
        files["preparation.md"] = "\n".join(plan_lines(plan))
        files["readiness.json"] = json.dumps(plan, ensure_ascii=False, indent=2)
        for name, text in files.items():
            (folder / name).write_text(text, encoding="utf-8")
        write_docx(folder / "cover_letter.docx", letter)
        cv = data["cv"].get("file")
        attached = None
        if cv and Path(cv).is_file():
            attached = folder / ("CV" + Path(cv).suffix.lower())
            shutil.copy2(cv, attached)
        record = {"job": {key: row[key] for key in ("number", "title", "company", "url", "apply_url", "source_name")},
                  "score": row["score"], "verdict": row["verdict"], "prepared_at": datetime.now(UTC).isoformat(timespec="seconds"),
                  "letter": how, "files": sorted(files) + ["cover_letter.docx"] + ([attached.name] if attached else [])}
        (folder / "application.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        current = self.store.application(number)
        if not current or current["status"] in ("saved", "prepared"):
            self.store.set_status(number, "prepared", f"packet written ({how})", folder=str(folder))
        emails = row["reading"]["apply_emails"]
        return {"jobs_view": True, "prepared": True, "number": number, "title": row["title"], "company": row["company"], "folder": str(folder),
                "files": record["files"], "letter": how, "cv_attached": bool(attached), "apply_by": "email" if emails else "web page",
                "apply_email": emails[0] if emails else "", "apply_url": row["apply_url"], "missing_required": row["match"]["missing_required"],
                "next_step": (f"Say “send my application for job {number}” to see it in the confirmation window before anything is sent."
                              if emails else f"This employer takes applications on its own page. Say “open job {number}”, paste from "
                              f"answers.md, attach the CV, submit there, then say “mark job {number} applied”."),
                "basis": "The CV is attached unchanged; the letter only uses facts from your profile and the posting. "
                         "preparation.md and readiness.json contain the skill gaps, practice, interview checklist and public research links."}

    def send(self, number: int) -> dict[str, Any]:
        """Propose emailing the application; it is sent only when the owner confirms it in the confirmation window."""
        row = self.store.job(number)
        record = self.store.application(number)
        if not record or not record.get("folder") or not Path(record["folder"]).is_dir():
            self.prepare(number)
            record = self.store.application(number)
        if record["status"] in ("applied", "interview", "offer"):
            raise ValueError(f"Job {number} is already marked {record['status']}; nothing was sent again.")
        emails = row["reading"]["apply_emails"]
        if not emails:
            raise ValueError(f"Job {number} takes applications on the employer's own page, not by email. Say “open job {number}”, "
                             f"submit there, then “mark job {number} applied”.")
        if self.gate is None or self.mailer is None:
            raise RuntimeError("Sending needs your email account and the confirmation window, which this session does not have.")
        if hasattr(self.mailer, "setup_problem") and (problem := self.mailer.setup_problem()):
            raise RuntimeError(problem)
        folder = Path(record["folder"])
        letter = (folder / "cover_letter.md").read_text(encoding="utf-8")
        attachments = [str(path) for path in sorted(folder.iterdir()) if path.name.startswith("CV.") or path.name == "cover_letter.docx"]
        to, subject = emails[0], f"Application: {row['title']}"
        details = [("To", to), ("Subject", subject), ("Job", f"#{number} {row['title']} at {row['company']} ({row['source_name']})"),
                   ("Attachments", ", ".join(Path(path).name for path in attachments) or "none"), ("Letter", letter[:1500])]

        def run(_secrets: dict[str, str]) -> dict[str, Any]:
            result = self.mailer.send(to, subject, letter, attachments)
            self.store.set_status(number, "applied", f"emailed to {to}", channel="email")
            return {**result, "job": number}
        from app.safety.confirmations import fingerprint
        proposal = self.gate.propose("apply", f"Send your application for {row['title']} at {row['company']}", to, details, run,
                                     evidence=attachments, notes=["This emails the employer as you. Nothing has been sent yet.",
                                                                  "Read the letter: it is in your name."],
                                     fingerprint_value=fingerprint(details))
        return {"jobs_view": True, "proposed": True, "number": number, **proposal}

    def open(self, number: int) -> dict[str, Any]:
        row = self.store.job(number)
        url = row["apply_url"] or row["url"]
        if not re.match(r"https?://", url or ""):
            raise ValueError(f"Job {number} has no web address to open.")
        self.opener(url)
        record = self.store.application(number)
        if not record or record["status"] in ("saved", "prepared"):
            self.store.set_status(number, "opened", "application page opened")
        return {"jobs_view": True, "opened": True, "number": number, "url": url, "title": row["title"], "company": row["company"],
                "next_step": f"Submit on their page (answers.md in the packet has your answers), then say “mark job {number} applied”."}

    def set_status(self, number: int, status: str, note: str = "") -> dict[str, Any]:
        record = self.store.set_status(number, status, note)
        row = self.store.job(number)
        return {"jobs_view": True, "status_changed": True, "number": number, "title": row["title"], "company": row["company"],
                "status": record["status"], "follow_up_at": record.get("follow_up_at")}

    def applications(self, status: str = "") -> dict[str, Any]:
        return {"jobs_view": True, "applications_view": True, "applications": self.store.applications(status), "counts": self.store.counts()}

    def follow_up_draft(self, number: int) -> Path:
        row = self.store.job(number)
        record = self.store.application(number) or {}
        folder = Path(record.get("folder") or self.packet_folder(row))
        folder.mkdir(parents=True, exist_ok=True)
        name = self.profile.load()["cv"].get("name") or "[your name]"
        applied = str(record.get("applied_at") or "")[:10]
        text = (f"Subject: Following up: {row['title']} application\n\nDear Hiring Team at {row['company']},\n\n"
                f"I applied for the {row['title']} role{f' on {applied}' if applied else ''} and wanted to check whether there is any update. "
                f"I remain very interested and would be glad to share anything else that helps.\n\nKind regards,\n{name}\n")
        path = folder / "follow_up.md"
        path.write_text(text, encoding="utf-8")
        return path

    # ------------------------------------------------------------------ the background round
    def round(self) -> dict[str, Any]:
        """Search (if the profile has terms), note new strong matches, prepare them if allowed, and draft follow-ups."""
        data = self.profile.load()
        preferences = data["preferences"]
        result: dict[str, Any] = {"searched": False, "new_matches": [], "prepared": [], "follow_ups": []}
        if preferences["search_terms"] or default_terms(data):
            self.search()
            result["searched"] = True
        fresh = self.store.unnotified(preferences["notify_score"])
        self.store.mark_notified([row["number"] for row in fresh])
        result["new_matches"] = [self.brief(row) for row in fresh[:8]]
        if preferences["auto_prepare"]:
            for row in fresh[:3]:
                if row["verdict"] == "strong" and not row.get("application"):
                    try:
                        self.prepare(row["number"])
                        result["prepared"].append(row["number"])
                    except Exception:  # one packet failing leaves the others
                        log_swallowed(_log, "preparing an application in the background", level=logging.WARNING)
        for application in self.store.due_follow_ups():
            path = self.follow_up_draft(application["number"])
            self.store.mark_followed_up(application["number"])
            result["follow_ups"].append({"number": application["number"], "company": application["company"], "draft": str(path)})
        self.store.forget_old()
        return result


# ---------------------------------------------------------------------- the packet's texts
def _join(items: list[str]) -> str:
    items = [item for item in items if item]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def cover_letter(row: dict[str, Any], data: dict[str, Any], skills: list[str]) -> str:
    cv, reading, match = data["cv"], row["reading"], row["match"]
    matched = [name for name in match["matched"] if name.lower() in {skill.lower() for skill in skills}][:5]
    years = cv.get("years_experience")
    recent = (cv.get("titles") or [""])[0]
    recent = re.sub(r"(?i)\s*(?:\(|\b(?:19|20)\d{2}\b).*$", "", recent).strip(" ,-|")
    opening = (f"I am applying for the {row['title']} role at {row['company']}" + (f", which I found on {row['source_name']}" if row.get("source_name") else "") + ".")
    experience = ""
    if years:
        experience = f"I bring about {years:g} years of experience" + (f", most recently as {recent}" if recent else "") + ". "
    elif recent:
        experience = f"Most recently I have worked as {recent}. "
    skills_line = (f"The role asks for {_join(matched)}, and these are skills I use in my work, as my CV shows." if matched else
                   "My CV sets out the experience I would bring to this role.")
    mode = {"remote": "I work well remotely and communicate clearly in writing.", "hybrid": "The hybrid setup suits me.",
            "on-site": "I am glad to work on site."}.get(reading["mode"], "")
    contact = " · ".join(part for part in (cv.get("email"), cv.get("phone"), *cv.get("links", [])[:2]) if part)
    return "\n".join([f"Dear Hiring Team at {row['company']},", "", opening, "", (experience + skills_line).strip(), "",
                      (mode + " " if mode else "") + f"I would welcome the chance to talk about how I can help {row['company']}.", "",
                      "Kind regards,", cv.get("name") or "[your name]", contact]).strip() + "\n"


def polished(model: Any, letter: str, row: dict[str, Any], skills: list[str]) -> str | None:
    """A smoother letter from the local model, kept only if it names no skill the owner lacks and still names the company."""
    from app.models.base import Message
    from app.models.streaming import generate_answer
    answer = generate_answer(model, [
        Message("system", "Rewrite this cover letter to read naturally and warmly, in under 220 words. Keep every fact exactly as given. "
                          "Do not add any skill, tool, number, employer or achievement that is not in the letter. Keep the greeting, the "
                          "sign-off, the name and the contact line. Reply with the letter only."),
        Message("user", letter)], temperature=.3, max_tokens=500)
    text = str(getattr(answer, "content", answer)).strip()
    mine = {skill.lower() for skill in skills}
    added = [name for name in skills_in(text) if name.lower() not in mine and name not in skills_in(letter)]
    if added or row["company"].lower() not in text.lower() or len(text) < 200 or len(text) > 2500:
        return None
    return text + ("\n" if not text.endswith("\n") else "")


def answers(row: dict[str, Any], data: dict[str, Any], skills: list[str]) -> str:
    cv, preferences, match, reading = data["cv"], data["preferences"], row["match"], row["reading"]
    lines = [f"# Answers for {row['title']} at {row['company']}", "", "Copy these into the application form; change anything that is not right.", "",
             "## You", f"- Name: {cv.get('name') or '[fill in]'}", f"- Email: {cv.get('email') or '[fill in]'}",
             f"- Phone: {cv.get('phone') or '[fill in]'}"]
    lines += [f"- Link: {link}" for link in cv.get("links", [])[:4]]
    lines += [f"- Location: {', '.join(preferences['locations']) or '[fill in]'}",
              f"- Years of experience: {cv.get('years_experience') if cv.get('years_experience') is not None else '[fill in]'}",
              f"- Work authorisation: {preferences['work_authorization'] or '[fill in]'}",
              f"- Notice period: {preferences['notice_period'] or '[fill in]'}",
              f"- Salary expectation: {preferences['salary_expectation'] or '[fill in]'}", "",
              "## Why this role", f"The {row['title']} role uses " + (_join(match['matched'][:4]) or "skills from my background")
              + f", which is the work I want to do more of, and {row['company']}'s "
              + ({"remote": "remote setup", "hybrid": "hybrid setup", "on-site": "team"}.get(reading["mode"], "role")) + " suits me.", "",
              "## Skills that match", *([f"- {name}" for name in match["matched"][:10]] or ["- (none recognised; read the posting)"]), "",
              "## Recent roles", *([f"- {title}" for title in cv.get("titles", [])[:5]] or ["- [fill in]"])]
    if match["missing_required"]:
        lines += ["", "## Asked for but not in your profile (answer honestly)", *[f"- {name}" for name in match["missing_required"]]]
    if reading["warnings"]:
        lines += ["", "## Warning signs in this posting", *[f"- {item}" for item in reading["warnings"]]]
    return "\n".join(lines) + "\n"


def posting_text(row: dict[str, Any]) -> str:
    return (f"# {row['title']} at {row['company']}\n\nSource: {row['source_name']} — {row['url']}\nApply: {row['apply_url']}\n"
            f"Location: {row['location'] or 'not stated'}\nPosted: {row['posted_at'][:10] or 'not stated'}\n\n{row['description']}\n")


def write_docx(path: Path, text: str) -> None:
    import docx
    document = docx.Document()
    for paragraph in text.split("\n"):
        document.add_paragraph(paragraph)
    document.save(str(path))


def source_catalog() -> list[dict[str, str]]:
    return [{"name": name, "title": item["name"], "site": item["site"], "about": item["about"],
             "kind": "feed" if "reader" in item else "company board"} for name, item in SOURCES.items()]
