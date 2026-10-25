"""Exercise real browser application assistance and optionally public job feeds.

Uses a generated applicant and an isolated local store. Never submits an
application, sends a message or uses the owner's CV, profile or accounts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FORM = """<!doctype html><html lang="en"><meta charset="utf-8"><title>HAYAT generated application verification</title>
<style>body{font:16px system-ui;background:#14201d;color:#f2f4ef;padding:24px}label{display:block;margin:12px}input,select,textarea{display:block;width:450px;padding:8px}</style>
<h1>Generated application · verification only</h1><form onsubmit="window.submissions++;event.preventDefault()">
<label>First name<input id="first"></label><label>Last name<input id="last"></label>
<label>Email<input id="email" type="email"></label><label>Phone<input id="phone" value="Keep my existing answer"></label>
<label>Current city<input id="city" readonly value="Read-only city"></label>
<label>Salary expectation<input id="salary" disabled></label>
<label>Cover letter<textarea id="letter"></textarea></label>
<label>Will you require visa sponsorship?<select id="visa" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>
<label>Resume/CV<input id="cv" type="file"></label>
<label><input id="consent" type="checkbox" required>I consent to processing</label>
<label>Password<input id="password" type="password"></label><button type="submit">Apply now</button></form>
<script>window.submissions=0;</script></html>"""


def verify_browser(folder: Path) -> dict:
    from app.jobs.center import JobCenter
    from app.jobs.sources import job
    from app.safety.permissions import PermissionManager
    from app.tools.browser import BrowserTools
    from app.tools.browser_host import PlaywrightHost
    host = PlaywrightHost("job-verification-browser")
    browser = BrowserTools(PermissionManager({"default": "ALLOW"}, folder), host=host)
    center = JobCenter(folder / "generated-applicant", None, browser=browser)
    cv = folder / "generated-cv.txt"
    cv.write_text("Taylor Example\ntaylor@example.org\nSKILLS\nPython, SQL\nEXPERIENCE\nData Analyst, Example Lab, Jan 2023 - Present\n- Built SQL reports with Python.\n", encoding="utf-8")
    center.read_cv(str(cv))
    center.set_preferences({"work_status": "stem_opt", "locations": ["Austin, US"], "salary_expectation": "100000 USD/year"})
    try:
        browser.launch(headless=False, channel="chromium")
        host.run(lambda: browser.page.set_content(FORM))
        row = job("remoteok", "form", "Data Analyst", "Generated verification employer", url="about:blank",
                  description="Build Python and SQL reports. This is a generated verification fixture.")
        row.update(key="generated:form", source="generated", source_name="Generated verification fixture")
        center.store.upsert([row])
        number = next(number for number in center.store.numbers() if center.store.job(number)["key"] == row["key"])
        center.rescore()
        result = center.fill(number)
        observed = host.run(lambda: browser.page.evaluate("""() => ({first:document.querySelector('#first').value,
          last:document.querySelector('#last').value,email:document.querySelector('#email').value,
          phone:document.querySelector('#phone').value,city:document.querySelector('#city').value,
          salary:document.querySelector('#salary').value,letter:document.querySelector('#letter').value,
          visa:document.querySelector('#visa').value,files:document.querySelector('#cv').files.length,
          consent:document.querySelector('#consent').checked,password:document.querySelector('#password').value,
          submissions:window.submissions})"""))
        assert observed["first"] == "Taylor" and observed["last"] == "Example" and observed["email"] == "taylor@example.org"
        assert observed["phone"] == "Keep my existing answer" and observed["city"] == "Read-only city"
        assert observed["letter"] and not any(observed[key] for key in ("visa", "files", "consent", "password", "submissions", "salary"))
        packet = center.store.application(number)
        assert packet["status"] == "opened" and (Path(packet["folder"]) / "cover_letter.docx").is_file()
        image = folder / "application-browser.png"
        browser.screenshot(str(image))
        return {"passed": True, "observed": observed, "filled": result["filled"], "left": result["left"],
                "files": result["files"], "application_status": packet["status"], "screenshot": str(image)}
    finally:
        browser.close()
        host.shutdown()


def verify_feeds(folder: Path) -> dict:
    from app.jobs.center import JobCenter
    from app.jobs.sources import FEEDS
    from app.research.fetcher import PublicFetcher
    fetcher = PublicFetcher("HAYAT-job-workflow-verification/1.0", timeout=20, delay=.35)
    center = JobCenter(folder / "live-public-feeds", fetcher)
    center.read_cv(str(folder / "generated-cv.txt"))
    center.set_preferences({"search_terms": ["python", "data analyst"], "target_titles": ["Data Analyst", "Python Engineer"],
                            "sources": list(FEEDS), "work_status": "stem_opt"})
    try:
        value = center.search(force=True)
        best = center.matches(limit=5, sort_by="pay")
        detail = center.sponsorship(best["matches"][0]["number"]) if best["matches"] else None
        discovery = center.discover_company("Stripe")
        sources_ok = bool(value["read"]) and all("error" not in source for source in value["read"])
        return {"passed": bool(sources_ok and best["matches"] and discovery["boards"]), "search": value, "pay_sorted": best,
                "employer_board": discovery,
                "sponsorship": detail, "learning": center.learning_plan(), "cv_review": center.cv_review(),
                "basis": "Live public downloads through HAYAT's public-only fetcher with robots checks. Individual source failures are reported, not hidden."}
    finally:
        fetcher.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Also read public job feeds; sends no applications.")
    args = parser.parse_args()
    folder = ROOT / ".test-runs" / "job-hunting-live"
    folder.mkdir(parents=True, exist_ok=True)
    report = {"checked_at": datetime.now(UTC).isoformat(timespec="seconds"), "owner_data_used": False, "checks": {}}
    checks = [("browser", verify_browser)] + ([("public_feeds", verify_feeds)] if args.live else [])
    for name, check in checks:
        print("Checking " + name + "…", flush=True)
        try:
            report["checks"][name] = check(folder)
        except Exception as error:
            report["checks"][name] = {"passed": False, "error": f"{type(error).__name__}: {error}"}
    report["passed"] = all(check["passed"] for check in report["checks"].values())
    destination = folder / ("verification.json" if args.live else "verification-browser.json")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": {key: value["passed"] for key, value in report["checks"].items()}, "report": str(destination)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
