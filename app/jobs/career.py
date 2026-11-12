"""Career radar: inspectable saved-job scenarios, learning work and search advice."""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.jobs.insight import DOCS, PRACTICE
from app.jobs.match import score
from app.jobs.outcomes import outcome_summary, timestamp
from app.jobs.sources import matches_location, relevant
from app.jobs.text import CATEGORY, SKILLS


def canonical_skills(values: list[str] | None) -> list[str]:
    aliases = {alias.casefold(): entry.split("|")[0] for entries in SKILLS.values()
               for entry in entries for alias in entry.split("|")}
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > 12:
        raise ValueError("Choose up to 12 recognised skills for the scenario")
    result = []
    for value in values:
        if not isinstance(value, str) or value.strip().casefold() not in aliases:
            raise ValueError("Unknown scenario skill: use a skill from the recommendations or HAYAT's skill vocabulary")
        name = aliases[value.strip().casefold()]
        if name not in result:
            result.append(name)
    return result


def requirement_evidence(row: dict[str, Any], missing: list[str]) -> list[dict[str, Any]]:
    from app.jobs.text import skills_in
    from app.jobs.understand import sections
    evidence = []
    required_text = sections(str(row.get("description") or ""))["required"]
    lines = [line.strip(" •-*\t") for line in required_text.splitlines() if line.strip()]
    recognised = [(line, set(skills_in(line))) for line in lines] if missing else []
    for skill in missing[:12]:
        line = next((line for line, names in recognised if skill in names), "")
        evidence.append({"skill": skill, "posting_excerpt": line[:400], "section": "required" if line else "not located",
                         "status": "not shown in profile", "basis": "deterministic posting parser; verify the surrounding requirement"})
    return evidence


def radar(center: Any, proposed_skills: list[str] | None = None, weekly_hours: float = 6,
          query: str = "", location: str = "", now: datetime | None = None, check=None) -> dict[str, Any]:
    if isinstance(weekly_hours, bool) or not isinstance(weekly_hours, (float, int)) or not math.isfinite(weekly_hours) or not 1 <= weekly_hours <= 40:
        raise ValueError("Weekly practice budget must be a finite number from 1 to 40 hours")
    proposed = canonical_skills(proposed_skills)
    now = now or datetime.now(UTC)
    profile = center.profile.load()
    have = center.profile.skills(profile)
    learned = center.learned()
    terms = [part.strip() for part in re.split(r",|\bor\b", query, flags=re.I) if part.strip()]
    jobs, limited = [], False
    for row in center.store.iter_matches(include_blocked=True):
        if check is not None and not check():
            raise RuntimeError("Career analysis cancelled")
        if relevant(row, terms) and matches_location(row, location, include_remote=True):
            if len(jobs) == 500:
                limited = True
                break
            jobs.append(row)
    candidates, demand, required, sole, blocked, unknowns = [], Counter(), Counter(), Counter(), Counter(), Counter()
    current_good, scenario_good, stale, undated = 0, 0, 0, 0
    for row in jobs:
        if check is not None and not check():
            raise RuntimeError("Career analysis cancelled")
        reading = row["reading"]
        baseline = score(row, reading, profile, have, now=now, learned=learned)
        simulated = score(row, reading, profile, list(dict.fromkeys([*have, *proposed])), now=now, learned=learned) if proposed else baseline
        missing = baseline["missing_required"]
        blocked.update(baseline["blocked"])
        eligible = not baseline["blocked"]
        current_good += int(eligible and baseline["score"] >= 60)
        scenario_good += int(not simulated["blocked"] and simulated["score"] >= 60)
        if eligible:
            for name in set(missing + baseline.get("missing_other", [])):
                if CATEGORY.get(name) != "Support & people":
                    demand[name] += 1
            required.update(set(missing))
            if len(missing) == 1:
                sole[missing[0]] += 1
        posted = timestamp(row.get("posted_at"))
        stale += int(posted is not None and (now - posted).days > 30)
        undated += int(posted is None or posted > now)
        concerns = []
        if posted is None or posted > now:
            concerns.append("Verify posting date")
        elif (now - posted).days > 30:
            concerns.append("Recheck this older requisition")
        for field, condition, note in (
            ("salary", not reading.get("salary"), "Confirm pay band"),
            ("sponsorship", reading.get("sponsorship") in {"not stated", "conflicting"}, "Confirm sponsorship if relevant"),
            ("mode", reading.get("mode") == "unknown", "Confirm work location"),
        ):
            if condition:
                unknowns[field] += 1
                concerns.append(note)
        if not missing and baseline["score"] < 60:
            concerns.append("Other score components remain weak despite recognised skill coverage")
        action = "resolve eligibility" if baseline["blocked"] else "check warning signs" if reading.get("warnings") else "prepare evidence" if missing else "verify and tailor"
        candidates.append({"number": row["number"], "title": row["title"], "company": row["company"], "url": row["url"],
                           "score": baseline["score"], "scenario_score": simulated["score"],
                           "score_change": simulated["score"] - baseline["score"], "verdict": baseline["verdict"],
                           "blocked": baseline["blocked"], "remaining_required": simulated["missing_required"],
                           "missing_required": missing, "action": action, "questions": concerns,
                           "warnings": list(reading.get("warnings", [])),
                           "constraints": {"asked_years": reading.get("years_min"),
                                           "profile_years": profile["cv"].get("years_experience"),
                                           "languages": reading.get("languages_needed", []),
                                           "education": reading.get("education", []),
                                           "work_mode": reading.get("mode", "unknown"),
                                           "regions": reading.get("regions", []),
                                           "sponsorship": reading.get("sponsorship", "not stated"),
                                           "publisher_expiry": row.get("expires_at")},
                           "requirements": requirement_evidence(row, missing),
                           "last_observed": row.get("last_seen"), "source": row.get("source_name"),
                           "score_reasons": {key: part["reason"] for key, part in baseline["parts"].items()}})
    priorities = sorted(demand, key=lambda name: (-sole[name], -required[name], -demand[name], name))[:12]
    recommendations = [{"skill": name, "required_by": required[name], "mentioned_by": demand[name],
                        "only_skill_gap": sole[name], "official_docs": DOCS.get(name),
                        "exercise": PRACTICE.get(CATEGORY.get(name, ""), "Build and test a small role-relevant work sample locally")}
                       for name in priorities]
    practice_skills = [name for name in (proposed or priorities[:3]) if name.casefold() not in {skill.casefold() for skill in have}]
    minutes_each, remainder = divmod(int(weekly_hours * 60), len(practice_skills)) if practice_skills else (0, 0)
    sprint = [{"skill": name, "minutes_allocated": minutes_each + int(index < remainder), "official_docs": DOCS.get(name),
               "tasks": ["Read the relevant official guide and explain the key idea in your own words.",
                         PRACTICE.get(CATEGORY.get(name, ""), "Build a small role-relevant work sample"),
                         "Test normal and failure cases; save a README, results and an explanation of your own contribution."],
               "completion": "Demonstrate the work without following a tutorial; record feedback and remaining weaknesses. "
                             "Practice time is a budget, not an estimate of mastery. Add CV claims only when true."}
              for index, name in enumerate(practice_skills)]
    from app.jobs.profile import default_terms
    suggested = list(dict.fromkeys([*profile["preferences"].get("target_titles", []),
                                   *profile["preferences"].get("search_terms", []), *default_terms(profile)]))[:8]
    sources = center.store.source_state()
    cooldowns = center.store.source_cooldowns()
    next_actions = []
    if not profile["cv"].get("lines"):
        next_actions.append("Read your CV and confirm its parsed skills before treating match scores as personal guidance.")
    if not jobs:
        next_actions.append("Search the free public feeds with one target role; broaden terms or location only when it fits your preferences.")
    if proposed and not practice_skills:
        next_actions.append("The selected skills are already listed in your profile. Demonstrate them with work samples and inspect the remaining gaps.")
    if blocked:
        next_actions.append("Review eligibility restrictions separately from skill gaps; learning a tool does not establish work authorization or relocation consent.")
    if cooldowns:
        next_actions.append("Some providers are cooling down: use another enabled public source or wait for its retry time. Complete site verification manually in the preserved tab.")
    next_actions += ["Verify the live employer requisition, location and application instructions before preparing a packet.",
                     "Build evidence for the top missing requirements, then compare the same skill scenario again."]
    candidates.sort(key=lambda item: (bool(item["blocked"]), -item["score"], item["number"]))
    return {"jobs_view": True, "career_radar": True, "measured_at": now.isoformat(),
            "coverage": {"jobs_considered": len(jobs), "limit": 500, "truncated": limited,
                         "query": query, "location": location, "older_than_30_days": stale, "unknown_or_future_date": undated},
            "profile_ready": bool(profile["cv"].get("lines")),
            "scenario": {"skills": proposed, "weekly_hours": weekly_hours, "current_good_or_strong": current_good,
                         "scenario_good_or_strong": scenario_good, "additional_good_or_strong": scenario_good - current_good,
                         "profile_changed": False, "basis": "Recomputed with the same scoring rules, CV, preferences, time and learned preferences. "
                         "Only hypothetical skill coverage changes; this does not establish experience or predict employer decisions."},
            "recommendations": recommendations, "practice_sprint": sprint, "opportunities": candidates,
            "blockers": [{"reason": reason, "jobs": count} for reason, count in blocked.most_common(8)],
            "unknowns": dict(unknowns), "query_suggestions": suggested,
            "sources": {"last_observed": sources, "cooldowns": cooldowns},
            "outcomes": {**outcome_summary(center.store.applications(), now),
                         "population": "All your recorded applications. Current role/location filters and hypothetical skills do not change this forecast cohort."},
            "next_actions": next_actions,
            "basis": "A bounded snapshot of saved, scored jobs, not the whole market. Advertised and last-seen dates do not prove "
                     "a role is open. Scenarios and suggested exercises are planning aids; public documentation links are not fetched by this report."}


def radar_lines(value: dict[str, Any]) -> list[str]:
    coverage, scenario = value["coverage"], value["scenario"]
    lines = ["Career radar — " + value["measured_at"],
             f"{coverage['jobs_considered']} saved roles considered" + (" (500-role limit reached)" if coverage["truncated"] else ""),
             f"Current good/strong matches: {scenario['current_good_or_strong']}; scenario: {scenario['scenario_good_or_strong']}.",
             "Hypothetical skills: " + (", ".join(scenario["skills"]) or "none"), scenario["basis"], "", "Learn first:"]
    for item in value["recommendations"]:
        lines += [f"• {item['skill']}: required by {item['required_by']}; only recognised required gap in {item['only_skill_gap']} roles.",
                  item["official_docs"] or "Look up the official guide for this skill."]
    if scenario["skills"]:
        changes = sorted((row for row in value["opportunities"] if not row["blocked"] and row["score_change"]),
                         key=lambda row: (-row["score_change"], -row["scenario_score"]))[:3]
        lines += ["", "Scenario examples (match scores, not hiring probabilities):"]
        for row in changes:
            lines += [f"• #{row['number']} {row['title']} at {row['company']}: {row['score']} → {row['scenario_score']}; "
                      "remaining requirements: " + (", ".join(row["remaining_required"]) or "none recognised") + ".", row["url"]]
        if not changes:
            lines.append("No score gain in eligible roles was measured for these proposed skills.")
    if value["practice_sprint"]:
        lines += ["", "Weekly practice sprint (time budget, not time to mastery):"]
        for item in value["practice_sprint"][:6]:
            lines += [f"• {item['skill']}: {item['minutes_allocated']} minutes.", item["tasks"][1], item["completion"]]
    outcomes = value["outcomes"]
    lines += ["", f"Interview-reaching record: {outcomes['interview_reached']} / {outcomes['submitted']} recorded submissions.",
              f"{outcomes['matured']} complete {outcomes['window_days']}-day windows; {outcomes['recent_excluded']} recent and "
              f"{outcomes['unknown_timing_excluded']} unknown-timing records excluded."]
    lines.append(outcomes["population"])
    prediction = outcomes["forecast"]
    if prediction:
        low, high = prediction["predictive_interval"]
        lines.append(f"Conditional next {prediction['applications']} comparable applications: expected {prediction['expected_interview_reaching']:g} "
                     f"interview-reaching responses within {outcomes['window_days']} days; 90% model range {low}–{high}.")
    else:
        lines.append("Forecast unavailable: " + outcomes["reason"] + ".")
    lines.append(outcomes["assumptions"])
    lines += ["", "Next actions:", *["• " + item for item in value["next_actions"]], "", value["basis"]]
    return lines
