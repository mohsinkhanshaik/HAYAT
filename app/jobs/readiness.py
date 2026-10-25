"""Evidence, preparation and public research routes for a specific application."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit


def application_routes(row: dict[str, Any], *, browser_available: bool | None = None,
                       email_available: bool | None = None) -> list[dict[str, Any]]:
    """Describe real supported handoffs; opening or filling never means an application was submitted."""
    routes = [{"channel": "packet", "action": "prepare", "available": True, "destination": "local application folder",
               "review": "Review the tailored letter, CV, answers and preparation checklist before sharing."}]
    address = str(row.get("apply_url") or row.get("url") or "")
    try:
        parsed = urlsplit(address)
        valid = parsed.scheme in ("https", "http") and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        valid = False
    if valid:
        routes.extend([
            {"channel": "web", "action": "open", "available": True, "destination": address,
             "review": "Verify the employer and live requisition, attach documents, review and submit on its page."},
            {"channel": "browser prefill", "action": "fill", "available": browser_available, "destination": address,
             "review": "Requires HAYAT's browser and a supported readable form. Review filled fields, answer remaining questions, "
                       "attach documents and submit yourself. No CAPTCHA or access-control bypass."},
        ])
    for email in (row.get("reading") or {}).get("apply_emails", []):
        routes.append({"channel": "email", "action": "send", "available": email_available, "destination": email,
                       "review": "Verify this posting accepts applications at this address. Requires your configured email account "
                                 "and the confirmation window; review recipient, subject, letter and attachments before sending."})
    return routes


def application_plan(row: dict[str, Any], profile: dict[str, Any], fit: dict[str, Any],
                     preparation: dict[str, Any], instructions: list[str]) -> dict[str, Any]:
    from app.jobs.insight import DOCS, PRACTICE, _yearly
    from app.jobs.text import CATEGORY

    reading, match = row["reading"], row["match"]
    missing = match["missing_required"]
    blockers = list(match["blocked"])
    unknowns = []
    if not profile["cv"].get("lines"):
        unknowns.append("Read your CV before assessing experience or tailoring claims.")
    if reading.get("sponsorship") in ("not stated", "conflicting"):
        unknowns.append("Confirm role-specific work authorization and sponsorship with the employer.")
    if not reading.get("salary"):
        unknowns.append("Ask for base salary, currency, location band, bonus and equity separately.")
    if reading.get("mode") == "unknown":
        unknowns.append("Confirm work location, remote country restrictions, travel and relocation expectations.")
    if not row.get("posted_at"):
        unknowns.append("Posting date is unknown; verify this requisition is still open on the employer's site.")
    gaps = [item["duty"] for item in fit.get("rows", []) if item["status"] == "gap"]
    years = profile["cv"].get("years_experience")
    asked = reading.get("years_min")
    experience_gap = years is not None and asked is not None and years < asked
    if years is None and asked is not None:
        unknowns.append(f"Posting asks {asked}+ years; your experience length is not known.")
    practice = []
    for skill in dict.fromkeys(missing):
        practice.append({"skill": skill, "priority": "required", "reference": DOCS.get(skill, "Find its official documentation"),
                         "exercise": PRACTICE.get(CATEGORY.get(skill, ""), "Build a small example relevant to this role"),
                         "proof": f"Demonstrate {skill} without a tutorial; explain trade-offs, test failures and record what you learned. "
                                  "Mark it learned only after practice; update the CV only with truthful experience."})
    evidence = [{"duty": item["duty"], "cv_evidence": item.get("evidence", ""), "status": item["status"],
                 "rehearse": "Explain your own contribution, the result, a constraint and what you would change."}
                for item in fit.get("rows", [])]
    state = "resolve blockers" if blockers else "build evidence" if missing or gaps or experience_gap or not fit.get("available") else "ready to tailor"
    pay = reading.get("salary")
    yearly = _yearly(pay)
    currency = profile["preferences"].get("salary_currency")
    minimum = profile["preferences"].get("min_salary")
    low = _yearly({**pay, "max": pay.get("min")}) if pay and pay.get("min") else None
    high = _yearly({**pay, "min": pay.get("max")}) if pay and pay.get("max") else None
    target = "not comparable"
    if low and high and minimum and low[0] == currency:
        target = "published minimum meets target" if low[1] >= minimum else "entire range below target" if high[1] < minimum else "range straddles target; confirm the attainable band"
    compensation = {"published": pay, "annualized_midpoint": yearly[1] if yearly else None,
                    "currency": yearly[0] if yearly else None,
                    "target_relation": target,
                    "meets_target_midpoint": yearly[1] >= minimum if yearly and minimum and yearly[0] == currency else None,
                    "assumptions": "Gross advertised base pay only. Hourly assumes 2,080 hours/year; daily 260 days, weekly 52 weeks, monthly 12 months. "
                                   "Not guaranteed hours, take-home pay, total compensation or a personal offer. Confirm the applicable location band."}
    company = str(row.get("company") or "").replace('"', " ")
    title = str(row.get("title") or "").replace('"', " ")
    queries = [("Direct company roles", f'"{company}" careers "{title}"'),
               ("Public ATS postings", f'"{company}" (site:jobs.ashbyhq.com OR site:jobs.lever.co OR site:job-boards.greenhouse.io)'),
               ("Public hiring announcements", f'"{company}" "{title}" ("hiring" OR "join our team")'),
               ("Referral research", f'"{company}" "{title}" ("team blog" OR "team" OR "meetup")')]
    research = [{"purpose": purpose, "url": "https://www.google.com/search?" + urlencode({"q": query}),
                 "status": "research link; results not fetched or verified"} for purpose, query in queries]
    stages = [
        {"stage": "Before applying", "tasks": ["Verify employer identity and the live requisition on its official careers page.",
             *blockers, *["Posting warning: " + warning for warning in reading.get("warnings", [])],
             *instructions, "Tailor only supported CV achievements; check portfolio, attachments and every form answer."]},
        {"stage": "Recruiter screen", "tasks": ["Rehearse a two-minute introduction and why this specific role.", *unknowns,
             "Confirm salary range, location, sponsorship, notice period and interview stages early."]},
        {"stage": "Role and practical interview", "tasks": [*gaps,
             "Complete a timed role-relevant work sample; test edge cases and explain your decisions.",
             "Rehearse the questions below aloud; document feedback and repeat weak areas."]},
        {"stage": "Behavioural interview", "tasks": ["Prepare truthful situation/task/action/result examples for the duties below.",
             "Include a failure, disagreement, ambiguous task, deadline and a measurable result; separate your work from the team's."]},
        {"stage": "Offer comparison", "tasks": ["Compare written base pay, variable pay, equity terms, benefits, travel and relocation costs.",
             "Ask for the applicable pay band and level; use comparable published roles without inventing competing offers."]},
    ]
    return {"state": state, "assessed_at": datetime.now(UTC).isoformat(timespec="seconds"), "blockers": blockers,
            "missing_required": missing, "unknowns": unknowns, "evidence": evidence, "practice": practice,
            "experience": {"asked_years": asked, "profile_years": years, "gap": experience_gap},
            "match_reasons": {name: part["reason"] for name, part in match.get("parts", {}).items()},
            "posted_at": row.get("posted_at"), "source_name": row.get("source_name"),
            "stages": stages, "questions": preparation["questions"], "compensation": compensation,
            "research": research, "posting_url": row["url"], "routes": application_routes(row),
            "referral_draft": f"Hi [name], I found the {row['title']} opening at {row['company']} ({row['url']}). "
                              "My relevant experience is [one truthful example]. Could you share what the team values in this role? "
                              "If you know my work and think it fits, would you be comfortable referring me?",
            "basis": "Readiness is a checklist based on the posting and profile, not a probability of passing or an employer decision. "
                     "Public hiring leads may reveal roles missed by aggregators; unpublished openings cannot be guaranteed. "
                     "Research links are suggestions, not verified vacancies or contacts. Referral draft is local and unsent."}


def plan_lines(plan: dict[str, Any]) -> list[str]:
    lines = ["Application readiness: " + plan["state"], "Posting: " + plan["posting_url"], "Assessed: " + plan["assessed_at"]]
    lines += ["Source: " + str(plan.get("source_name") or "unknown"), "Posting date: " + str(plan.get("posted_at") or "unknown"),
              "", "Match evidence:", *[f"  - {name}: {reason}" for name, reason in plan.get("match_reasons", {}).items()]]
    if plan.get("routes"):
        lines += ["", "Application routes:"]
        for route in plan["routes"]:
            availability = "ready" if route["available"] is True else "setup required" if route["available"] is False else "check session setup"
            lines += [f"  - {route['channel']}: {route['destination']} ({availability})", "    " + route["review"]]
    for stage in plan["stages"]:
        lines += ["", stage["stage"] + ":", *["  - " + task for task in stage["tasks"]]]
    lines += ["", "Required skills to practise:"]
    if not plan["practice"]:
        lines.append("  No recognised required skill is missing from your profile. Verify with a work sample.")
    for item in plan["practice"]:
        lines += [f"  - {item['skill']}: {item['reference']}", "    " + item["exercise"], "    " + item["proof"]]
    lines += ["", "Interview evidence:"]
    for item in plan["evidence"]:
        lines += [f"  - {item['duty']} ({item['status']})", "    CV: " + (item["cv_evidence"] or "No supporting CV line found."),
                  "    " + item["rehearse"]]
    lines += ["", "Practice questions:", *["  - " + item["question"] for item in plan["questions"]]]
    pay = plan["compensation"]
    if pay["annualized_midpoint"] is not None:
        lines += ["", f"Published pay midpoint, annualized: {pay['annualized_midpoint']:,.0f} {pay['currency']}"]
    lines.append(pay["assumptions"])
    lines.append("Your minimum-pay comparison: " + pay["target_relation"])
    lines += ["", "Public discovery and referral research:"]
    lines += [f"  - {item['purpose']}: {item['url']} ({item['status']})" for item in plan["research"]]
    lines += ["", "Referral draft (review and personalise):", plan["referral_draft"], "", plan["basis"]]
    return lines
