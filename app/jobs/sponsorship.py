"""Posting evidence and employer history, with unknowns kept separate from eligibility."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

SOURCES = [
    {"title": "STEM OPT requirements (USCIS)", "url": "https://www.uscis.gov/working-in-the-united-states/students-and-exchange-visitors/optional-practical-training-extension-for-stem-students-stem-opt"},
    {"title": "Practical training and Form I-983 (ICE)", "url": "https://www.ice.gov/sevis/practical-training"},
    {"title": "Form I-983 instructions (ICE)", "url": "https://www.ice.gov/doclib/sevis/pdf/i983Instructions.pdf"},
    {"title": "E-Verify employer search", "url": "https://www.e-verify.gov/e-verify-employer-search"},
    {"title": "H-1B Employer Data Hub (USCIS)", "url": "https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub"},
    {"title": "LCA and PERM disclosure data (DOL)", "url": "https://www.dol.gov/agencies/eta/foreign-labor/performance"},
    {"title": "H-1B specialty occupations (USCIS)", "url": "https://www.uscis.gov/working-in-the-united-states/h-1b-specialty-occupations"},
]


def _key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower().replace("&", " and ")))


def import_history(path: Path, destination: Path) -> dict:
    """Import a bounded USCIS-style CSV; commit only after every record validates."""
    if path.suffix.lower() != ".csv" or path.stat().st_size > 50_000_000:
        raise ValueError("Choose a USCIS employer-history CSV smaller than 50 MB.")
    raw = path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    headers = {_key(name): name for name in reader.fieldnames or []}
    def column(*names):
        found = next((headers[name] for name in names if name in headers), None)
        if not found:
            raise ValueError("Employer-history CSV is missing " + names[0] + ". LCA filings are a different dataset.")
        return found
    employer = column("employer", "employer name", "petitioner", "petitioner name")
    year = column("fiscal year", "fiscalyear", "fy")
    counts = {name: column(name.replace("_", " "), name.replace("_", " ") + "s")
              for name in ("initial_approval", "initial_denial", "continuing_approval", "continuing_denial")}
    companies, rows = {}, 0
    for line, row in enumerate(reader, 2):
        if not any(value for value in row.values()):
            continue
        name = (row.get(employer) or "").strip()
        fiscal = (row.get(year) or "").strip().upper().removeprefix("FY").strip()
        if not name or not re.fullmatch(r"20\d{2}", fiscal):
            raise ValueError(f"Invalid employer or fiscal year on CSV line {line}; the previous history is unchanged.")
        totals = {}
        for key, header in counts.items():
            value = (row.get(header) or "").strip().replace(",", "")
            if not re.fullmatch(r"\d{1,10}", value):
                raise ValueError(f"Invalid {header} on CSV line {line}; the previous history is unchanged.")
            totals[key] = int(value)
        company = companies.setdefault(_key(name), {"name": name, "years": {}})
        bucket = company["years"].setdefault(fiscal, dict.fromkeys(counts, 0))
        for key, value in totals.items():
            bucket[key] += value
        rows += 1
        if rows > 500_000:
            raise ValueError("The CSV exceeds 500,000 rows; import a smaller fiscal-year extract.")
    if not rows:
        raise ValueError("The employer-history CSV contains no records.")
    report = {"imported_at": datetime.now(UTC).isoformat(timespec="seconds"), "file": path.name,
              "sha256": hashlib.sha256(raw).hexdigest(), "rows": rows, "companies": companies,
              "provenance": "Locally supplied USCIS-style CSV; publisher authenticity has not been verified."}
    from app.tools.file_editing import atomic_save
    atomic_save(destination, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    return {key: value for key, value in report.items() if key != "companies"} | {"employers": len(companies)}


def history_for(path: Path, company: str) -> dict:
    if not path.is_file():
        return {"status": "not loaded", "detail": "Import a USCIS employer-history CSV to check past petition decisions."}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        record = value["companies"].get(_key(company)) if company else None
        return {"status": "historical match" if record else "no exact match" if company else "loaded",
                "record": record, "imported_at": value["imported_at"], "file": value["file"],
                "sha256": value["sha256"], "provenance": value["provenance"],
                "source": value.get("source"),
                "detail": "Exact normalized legal-name match only. A missing name does not mean an employer never sponsored; confirm the hiring entity."}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"status": "unreadable", "detail": "The local history is incomplete. Re-import the source CSV."}


def sponsor_candidates(path: Path, limit: int = 20) -> list[dict]:
    """Discovery leads from the newest fiscal year in the imported extract, never a job list."""
    if not path.is_file():
        return []
    try:
        companies = json.loads(path.read_text(encoding="utf-8"))["companies"].values()
        latest = max(year for company in companies for year in company["years"])
        rows = [{"company": company["name"], "fiscal_year": latest, **company["years"][latest]}
                for company in companies if latest in company["years"] and company["years"][latest]["initial_approval"] > 0]
        return sorted(rows, key=lambda row: (-row["initial_approval"], row["company"].casefold()))[:limit]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def research(row: dict | None, preferences: dict, history_path: Path, company: str = "") -> dict:
    from app.jobs.profile import WORK_STATUSES
    from app.jobs.understand import understand
    reading = understand(row) if row else {}
    company = row["company"] if row else company.strip()
    text = row.get("description", "") if row else ""
    evidence = []
    for line in re.split(r"\n|(?<=[.!?])\s+(?=[A-Z])", text):
        match = re.search(r"(?i)sponsor|\bvisa\b|e[ -]?verify|i[ -]?983|\bopt\b|\bcpt\b|h[ -]?1[ -]?b|citizen|clearance|relocat|travel", line)
        if match:
            start = max(0, match.start() - 180)
            end = min(len(line), start + 600)
            evidence.append(("…" if start else "") + line[start:end].strip() + ("…" if end < len(line) else ""))
        if len(evidence) == 20:
            break
    status = preferences.get("work_status", "")
    checks = [
        "Confirm the legal employer, hiring location, work country and permitted remote states/time zones; remote does not mean worldwide.",
        "Ask whether this specific role supports sponsorship now, future sponsorship or an H-1B change of employer; distinguish these answers.",
        "Verify current base pay, currency, pay period, benefits, equity, employment type, travel frequency and relocation support with the recruiter.",
    ]
    if status in ("stem_opt", "opt", "cpt"):
        checks += ["Check the role's direct relationship to your degree and your authorization dates with your DSO.",
                   "For STEM OPT, confirm E-Verify participation for the actual hiring entity, a completed and signed I-983, at least 20 hours/week, compensation, supervision and training arrangements.",
                   "E-Verify enrollment alone does not establish STEM OPT eligibility; ask your DSO to review the employer and training arrangement."]
    if status in ("h1b", "needs_sponsorship", "stem_opt", "opt", "cpt"):
        checks += ["For H-1B, ask about the petitioning entity, role requirements, worksite, counsel, filing/start dates and whether a cap or cap-exemption analysis is needed.",
                   "Past approvals, certified LCAs and nonprofit/university branding do not establish current sponsorship, a job opening, cap exemption or your eligibility."]
    if reading.get("sponsorship") == "conflicting":
        checks.insert(0, "The posting contains conflicting sponsorship statements. Get a role-specific written answer before relying on either.")
    if reading.get("future_sponsorship_excluded") and WORK_STATUSES.get(status, {}).get("needs_future"):
        checks.insert(0, "The posting excludes future sponsorship, which conflicts with the future sponsorship need in your profile.")
    return {"jobs_view": True, "sponsorship_view": True, "number": row.get("number") if row else None,
            "company": company, "title": row.get("title", "") if row else "", "url": row.get("url", "") if row else "",
            "work_status": WORK_STATUSES.get(status, {}).get("label", "not specified"),
            "posting_sponsorship": reading.get("sponsorship", "not stated"), "visa_types_named": reading.get("visa_types", []),
            "citizenship_required": reading.get("citizenship_required", False), "clearance_mentioned": reading.get("clearance_required", False),
            "evidence": evidence, "history": history_for(history_path, company), "checklist": checks, "sources": SOURCES,
            "sponsor_candidates": sponsor_candidates(history_path) if not row and not company else [],
            "basis": "Posting evidence and locally imported history are separate. No probability of a job offer, visa approval or lottery selection is inferred. Confirm current rules and your individual situation with your DSO or immigration counsel."}


def research_lines(value: dict) -> list[str]:
    lines = [f"Sponsorship research: {value['company'] or 'job search'}" + (f" · {value['title']}" if value.get("title") else ""),
             f"Your status: {value['work_status']} · Posting sponsorship: {value['posting_sponsorship']}",
             "Programs named (mention is not an offer): " + (", ".join(value["visa_types_named"]) or "none")]
    if value.get("citizenship_required"):
        lines.append("The posting states a citizenship requirement.")
    if value.get("clearance_mentioned"):
        lines.append("The posting mentions a clearance; ask about the specific requirement.")
    lines += ["", "Posting evidence:", *(value["evidence"] or ["No authorization statements found in the posting."])]
    history = value["history"]
    lines += ["", "Employer history: " + history["status"], history["detail"]]
    if history.get("record"):
        for year, counts in sorted(history["record"]["years"].items(), reverse=True):
            lines.append(f"FY{year}: initial approvals {counts['initial_approval']}, initial denials {counts['initial_denial']}; continuing approvals {counts['continuing_approval']}, continuing denials {counts['continuing_denial']}")
    if history.get("imported_at"):
        lines += [f"Imported {history['imported_at']} from {history['file']} · SHA-256 {history['sha256']}", history["provenance"]]
    if history.get("source"):
        source = history["source"]
        lines += [f"Historical fiscal years: {source['fiscal_years']} · Source: {source['url']}",
                  "Mirror SHA-256: " + source["sha256"],
                  f"Source rows: {source['source_rows']}; unnamed petitioners omitted: {source['omitted_unidentified_rows']}"]
    if value.get("sponsor_candidates"):
        lines += ["", "Employer research leads from the newest fiscal year in your extract (ranked by initial approvals; no current openings or sponsorship inferred):"]
        lines += [f"• {row['company']} · FY{row['fiscal_year']} · initial approvals {row['initial_approval']}, denials {row['initial_denial']}"
                  for row in value["sponsor_candidates"]]
    lines += ["", "Questions to resolve:", *["• " + check for check in value["checklist"]], "", "Official references (open to verify current rules):"]
    lines += [source["title"] + ": " + source["url"] for source in value["sources"]]
    return lines + ["", value["basis"]]
