"""How well a posting fits the owner, as a score whose every part is shown.

Hard checks come first. Each makes a job "not eligible" with the reason, whatever its score:
* it limits where you may live or work, and none of your places or allowed countries is among them;
* you want remote only and it is on-site or hybrid;
* it needs a language your profile does not have;
* it contains a company or word you excluded.

Then the parts, weighted:
| Part | Weight | Reads |
|---|---|---|
| Skills | 45 % | required skills count double, nice-to-have and mentioned once |
| Title | 20 % | words shared with your target titles or titles you held |
| Experience | 15 % | your years against the years it asks |
| Level | 10 % | your level against its level |
| Pay | 5 % | its pay against your minimum, when both are known in one currency |
| Freshness | 5 % | how recently it was posted |

The score is HAYAT's reading of two texts, not the employer's decision: a 90 can be rejected and
a 50 can get an interview. Missing required skills are listed so you can decide what to say.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.jobs.profile import WORK_STATUSES, level_for
from app.jobs.text import CATEGORY, words

SMOOTHING = 2
#: A role held before counts for less than the role the owner is looking for.
HELD_TITLE = .6
WEIGHTS = {"skills": .35, "duties": .15, "title": .17, "experience": .13, "level": .10, "pay": .05, "freshness": .05}
#: The most the owner's past choices can move a score, either way.
LEARNED_LIMIT = 6
LEVEL_ORDER = ["intern", "junior", "mid", "senior", "lead", "manager"]
SYNONYMS = {"developer": "engineer", "dev": "engineer", "programmer": "engineer", "swe": "engineer", "sde": "engineer",
            "analytics": "analyst", "frontend": "front-end", "backend": "back-end", "fullstack": "full-stack", "ml": "machine learning",
            "admin": "administrator", "mgr": "manager", "sr": "senior", "jr": "junior"}
STOP = {"and", "of", "the", "a", "an", "for", "with", "in", "at", "to", "m", "f", "d", "w", "x", "remote", "hybrid", "onsite", "-", "/",
        "senior", "junior", "lead", "staff", "principal", "intern", "ii", "iii", "i"}
COUNTRY_REGION = {"germany": "EU", "france": "EU", "spain": "EU", "netherlands": "EU", "poland": "EU", "italy": "EU", "ireland": "EU",
                  "portugal": "EU", "austria": "EU", "belgium": "EU", "sweden": "EU", "india": "APAC", "pakistan": "APAC",
                  "philippines": "APAC", "singapore": "APAC", "australia": "APAC", "japan": "APAC", "brazil": "LATAM",
                  "mexico": "LATAM", "argentina": "LATAM", "colombia": "LATAM", "usa": "US", "united states": "US", "us": "US",
                  "uk": "UK", "united kingdom": "UK", "england": "UK", "canada": "Canada", "uae": "UAE", "dubai": "UAE"}


def _title_words(text: str) -> set[str]:
    return {SYNONYMS.get(word, word) for word in words(text) if word not in STOP and len(word) > 1}


def _places(preferences: dict[str, Any]) -> set[str]:
    """Regions and countries the owner can work from, in the names postings use."""
    found = set()
    for place in preferences.get("locations", []) + preferences.get("countries_ok", []):
        lowered = place.lower().strip()
        found.add(lowered)
        for country, region in COUNTRY_REGION.items():
            if re.search(rf"\b{re.escape(country)}\b", lowered):
                found |= {country, region.lower()}
    return found


def eligibility(job: dict[str, Any], reading: dict[str, Any], profile: dict[str, Any], skills: list[str]) -> list[str]:
    preferences = profile["preferences"]
    reasons = []
    places = _places(preferences)
    regions = [region for region in reading["regions"] if region != "Worldwide"]
    away = bool(regions and places and not any(region.lower() in places for region in regions)
                and "Worldwide" not in reading["regions"])
    # A place mismatch is a hard block only when the owner cannot or will not move. If they are willing to
    # relocate, and the job is not remote (remote needs no move), it stays eligible with a relocation note.
    if away and not (preferences.get("willing_to_relocate") and reading["mode"] != "remote"):
        yours = ", ".join(preferences.get("locations", []) + preferences.get("countries_ok", []))
        reasons.append(f"limited to {', '.join(regions)}; your places are {yours}")
    if preferences.get("remote_only") and reading["mode"] in ("on-site", "hybrid"):
        reasons.append(f"it is {reading['mode']} and you want remote only")
    known_languages = {name.lower() for name in profile["cv"].get("languages", [])} | {name.lower() for name in skills}
    missing_languages = [name for name in reading["languages_needed"] if name.lower() not in known_languages and name != "English"]
    if missing_languages:
        reasons.append(f"needs {', '.join(missing_languages)}" + (" (the posting is written in German)" if reading["written_in"] == "German" else ""))
    status = WORK_STATUSES.get(preferences.get("work_status") or "")
    if status:
        if reading.get("citizenship_required") and not status["is_citizen"]:
            reasons.append("it requires US citizenship")
        if status["needs_now"] and reading.get("sponsorship") == "not offered":
            reasons.append(f"it does not sponsor visas and you are {status['label']}")
        elif status["needs_future"] and reading.get("future_sponsorship_excluded"):
            reasons.append("it requires authorization without sponsorship now or in the future")
    blob = f"{job.get('company', '')} {job.get('title', '')}".lower()
    for company in preferences.get("exclude_companies", []):
        if company.lower() and company.lower() in job.get("company", "").lower():
            reasons.append(f"you excluded {company}")
    for word in preferences.get("exclude_words", []):
        if word.lower() and re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", blob):
            reasons.append(f"its title has '{word}', which you excluded")
    return reasons


def relocation(job: dict[str, Any], reading: dict[str, Any], profile: dict[str, Any]) -> str | None:
    """A note when an eligible job would need the owner to move: a region they do not list, not remote, and
    they said they would relocate. None otherwise (remote, already in their places, or they will not move)."""
    preferences = profile["preferences"]
    if not preferences.get("willing_to_relocate") or reading["mode"] == "remote":
        return None
    places = _places(preferences)
    regions = [region for region in reading["regions"] if region != "Worldwide"]
    if regions and places and not any(region.lower() in places for region in regions) and "Worldwide" not in reading["regions"]:
        return f"would mean relocating to {', '.join(regions)} ({reading['mode']})"
    return None


def sponsorship_note(reading: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any] | None:
    """How a job's visa stance meets the owner's own status, for an eligible job: a positive note when it
    sponsors and they need it, or a soft warning (small penalty) when they will need sponsorship later and it
    says it will not. Hard mismatches are already blocked in eligibility(); this only shades eligible roles."""
    status = WORK_STATUSES.get((profile["preferences"].get("work_status") or ""))
    if not status or status["is_citizen"] or not (status["needs_now"] or status["needs_future"]):
        return None
    offered = reading.get("sponsorship")
    named = ", ".join(sorted(reading.get("visa_types") or [])).replace("_", " ").upper() if reading.get("visa_types") else ""
    if offered == "offered":
        return {"state": "sponsors", "penalty": 0,
                "advice": "It offers visa sponsorship" + (f" (names {named})" if named else "") + ", which you need — a plus."}
    if offered == "not offered" and status["needs_future"]:
        return {"state": "no_future_sponsorship", "penalty": 6,
                "advice": f"It says it will not sponsor visas. You are {status['label']}, so you could work now but it "
                          "would not sponsor you later; kept, scored a little lower."}
    if offered == "not stated":
        return {"state": "sponsorship_unstated", "penalty": 0,
                "advice": "It does not say whether it sponsors visas; ask early, since you will need it."}
    return None


def learned_from(pursued: list[str], hidden: list[str]) -> dict[str, Any]:
    """Title words in jobs the owner pursued (prepared, applied, interview, offer) against ones they hid."""
    if len(pursued) + len(hidden) < 3:
        return {}
    plus = Counter(word for title in pursued for word in _title_words(title))
    minus = Counter(word for title in hidden for word in _title_words(title))
    return {word: (plus[word] - minus[word]) / (plus[word] + minus[word] + 1) for word in set(plus) | set(minus)}


def score(job: dict[str, Any], reading: dict[str, Any], profile: dict[str, Any], skills: list[str],
          now: datetime | None = None, learned: dict[str, float] | None = None) -> dict[str, Any]:
    """The match, every part with its value and reason; never above 25 when a hard check fails."""
    now = now or datetime.now(UTC)
    mine = {skill.lower() for skill in skills}
    wanted = reading["skills"]
    # Soft skills ("communication", "teamwork") count half and are never listed as missing requirements:
    # every posting names them and no CV proves them.
    soft = lambda name: CATEGORY.get(name) == "Support & people"
    weighted = [(name, 1 if soft(name) else 2) for name in wanted["required"]] + \
        [(name, .5 if soft(name) else 1) for name in wanted["nice"] + wanted["mentioned"]]
    total = sum(weight for _name, weight in weighted)
    have = sum(weight for name, weight in weighted if name.lower() in mine)
    matched = [name for name, _weight in weighted if name.lower() in mine]
    missing_required = [name for name in wanted["required"] if name.lower() not in mine and not soft(name)]
    missing_other = [name for name in wanted["nice"] + wanted["mentioned"] + wanted["required"]
                     if name.lower() not in mine and name not in missing_required]
    parts: dict[str, dict[str, Any]] = {}
    if total:
        hard = [name for name, _weight in weighted if not soft(name)]
        # Two skills named and both matched is thin evidence: coverage is pulled toward neutral by two extra half-matched skills.
        parts["skills"] = {"value": (have + .5 * SMOOTHING) / (total + SMOOTHING), "reason": f"{len([name for name in matched if not soft(name)])} of {len(hard)} skills it names"
                           + (f"; missing required: {', '.join(missing_required[:6])}" if missing_required else "")}
    else:
        parts["skills"] = {"value": .3, "reason": "it names no skills HAYAT recognises; read it yourself"}
    from app.jobs.insight import alignment, responsibilities
    fit = alignment(responsibilities(job.get("description", "")), profile["cv"].get("lines", []), skills)
    if fit["available"]:
        parts["duties"] = {"value": fit["percent"] / 100, "reason": f"{fit['aligned']} of {len(fit['rows'])} duties shown in your CV"
                           + (f", {fit['partly']} partly" if fit["partly"] else "")}
    else:
        parts["duties"] = {"value": .5, "reason": fit["reason"]}
    preferences = profile["preferences"]
    targets = [(title, 1.0) for title in preferences.get("target_titles", [])] + \
        [(title, HELD_TITLE) for title in profile["cv"].get("titles", [])[:4]]
    title_words = _title_words(job.get("title", ""))
    best, best_title, title_value = 0.0, "", 0.0
    for target, factor in targets:
        target_words = _title_words(target)
        if target_words and title_words:
            overlap = len(title_words & target_words) / len(title_words | target_words)
            value = min(1.0, overlap * 1.4) * factor
            if value > title_value:
                best, best_title, title_value = overlap, target, value
    title_value = title_value if targets else .5
    parts["title"] = {"value": title_value, "reason": f"close to your “{best_title}”" if best >= .5 else
                      f"shares a little with “{best_title}”" if best_title else "unlike your target titles" if targets else
                      "you have set no target titles"}
    years = profile["cv"].get("years_experience")
    asked = reading["years_min"]
    if asked is None or years is None:
        parts["experience"] = {"value": .7, "reason": "years asked or yours are not known"}
    else:
        gap = asked - years
        parts["experience"] = {"value": 1.0 if gap <= 0 else .6 if gap <= 2 else .2,
                               "reason": f"asks {asked}+ years, you have about {years:g}"}
    level = preferences.get("level") or profile["cv"].get("level") or level_for(years)
    theirs = reading["level"]
    if not level or not theirs:
        parts["level"] = {"value": .7, "reason": "level not stated" if not theirs else "your level is not known"}
    else:
        distance = abs(LEVEL_ORDER.index(theirs) - LEVEL_ORDER.index(level)) if theirs in LEVEL_ORDER and level in LEVEL_ORDER else 1
        parts["level"] = {"value": 1.0 if distance == 0 else .6 if distance == 1 else .2, "reason": f"it is {theirs}, you are {level}"}
    pay, floor = reading["salary"], preferences.get("min_salary")
    if pay and floor and pay["currency"] == preferences.get("salary_currency") and pay["period"] == "year":
        top = pay["max"] or pay["min"]
        parts["pay"] = {"value": 1.0 if top >= floor else .2, "reason": f"pays up to {top:,.0f} {pay['currency']}, your minimum is {floor:,.0f}"}
    else:
        parts["pay"] = {"value": .6, "reason": "pay or your minimum not comparable" if pay or floor else "pay not stated"}
    try:
        age = (now - datetime.fromisoformat(job["posted_at"])).days if job.get("posted_at") else None
    except ValueError:
        age = None
    parts["freshness"] = {"value": .6 if age is None else 1.0 if age <= 7 else .7 if age <= 30 else .3,
                          "reason": "posting date unknown" if age is None else f"posted {age} day{'s' if age != 1 else ''} ago"}
    value = round(100 * sum(WEIGHTS[name] * part["value"] for name, part in parts.items()))
    choices = None
    if learned:
        signals = {word: learned[word] for word in _title_words(job.get("title", "")) if word in learned}
        shift = max(-LEARNED_LIMIT, min(LEARNED_LIMIT, round(sum(signals.values()) * 3)))
        if shift:
            value = max(0, min(100, value + shift))
            liked = [word for word, signal in signals.items() if signal > 0]
            disliked = [word for word, signal in signals.items() if signal < 0]
            choices = {"shift": shift, "reason": ("like jobs you pursued (" + ", ".join(liked) + ")" if liked else "")
                       + ("; " if liked and disliked else "") + ("like jobs you hid (" + ", ".join(disliked) + ")" if disliked else "")}
    blocked = eligibility(job, reading, profile, skills)
    if blocked:
        value = min(value, 25)
    move = relocation(job, reading, profile)
    if move:
        value = max(0, value - 10)  # eligible, but moving is a real cost, so it ranks below equal remote/local roles
    visa = sponsorship_note(reading, profile)
    if visa and visa["penalty"]:
        value = max(0, value - visa["penalty"])
    if reading["warnings"]:
        value = max(0, value - 15)
    verdict = ("not eligible" if blocked else "strong" if value >= 75 else "good" if value >= 60 else "stretch" if value >= 45 else "weak")
    advice = []
    if missing_required:
        advice.append(f"It asks for {', '.join(missing_required[:5])}, which your profile does not show. If you have used them, add "
                      f"them to your profile; if not, say what is closest in the cover letter rather than claiming them.")
    if parts["experience"]["value"] < 1 and asked is not None and years is not None:
        advice.append("It asks for more years than you have; strong matching skills can still get a reply.")
    if reading["warnings"]:
        advice.append("Warning signs: " + "; ".join(reading["warnings"]) + ". Check the company before sharing anything personal.")
    if move:
        advice.append(f"This {move}. You said you would relocate, so it is kept, scored a little below equal remote or local roles.")
    if visa:
        advice.append(visa["advice"])
    return {"score": value, "verdict": verdict, "blocked": blocked, "relocation": move,
            "sponsorship": visa["state"] if visa else None, "parts": {name: {"value": round(part["value"], 2),
                                                                                    "weight": WEIGHTS[name], "reason": part["reason"]}
                                                                             for name, part in parts.items()},
            "choices": choices, "duties_fit": fit,
            "matched": matched, "missing_required": missing_required, "missing_other": missing_other[:10], "advice": advice}
