"""Explicit browser handoffs for platforms without a configured free feed."""
from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

from app.jobs.access import page_access


def interaction_required(page: dict) -> bool:
    return page_access(page) != "accessible"


def blocked_report(report, page):
    kind = page_access(page)
    instructions = {
        "human_verification": "Complete human verification manually in the same visible browser tab, then click Continue after verification.",
        "sign_in_required": "Sign in manually on the provider's page, return to the original job search, then click Continue after verification.",
        "rate_limited": "The site rate limited this session. Wait for its stated cooldown before manually refreshing the search, then continue.",
        "access_denied": "The site denied access. Use the free API search or another public provider; repeated retries will not resolve this denial.",
    }
    report.update(status="manual_interaction_required", access_status=kind, manual_interaction_required=True, page=page,
                  next_step=instructions[kind] + " No job results were verified or imported, and no application was submitted.")
    return report


def search_url(platform: str, query: str, location: str, engine: str = "Google") -> str:
    query, location = query.strip()[:120], location.strip()[:120]
    if not query:
        raise ValueError("Enter a role or skill before opening a platform search.")
    if location.casefold() in {"worldwide", "whole world", "all locations", "global", "anywhere", "world"}:
        location = ""
    if platform == "LinkedIn":
        return "https://www.linkedin.com/jobs/search/?" + urlencode({"keywords": query, "location": location})
    if platform == "Indeed":
        return "https://www.indeed.com/jobs?" + urlencode({"q": query, "l": location})
    if platform not in {"Web", "Employer boards"}:
        raise ValueError("Choose Web, Employer boards, LinkedIn or Indeed.")
    endpoints = {"Google": "https://www.google.com/search", "Bing": "https://www.bing.com/search",
                 "DuckDuckGo": "https://duckduckgo.com/"}
    if engine not in endpoints:
        raise ValueError("Choose Google, Bing or DuckDuckGo.")
    sites = " (site:jobs.lever.co OR site:boards.greenhouse.io OR site:job-boards.greenhouse.io OR site:jobs.ashbyhq.com OR site:myworkdayjobs.com OR site:jobs.smartrecruiters.com)" if platform == "Employer boards" else ""
    return endpoints[engine] + "?" + urlencode({"q": f"{query} jobs {location}".strip() + sites})


def check_scope(pending, current_url):
    expected, current = urlsplit(pending["url"]), urlsplit(current_url)
    if current.scheme != "https" or current.hostname != expected.hostname:
        raise ValueError("Return to the original public job search tab before continuing.")
    if not interaction_required({"url": current_url}):
        if current.path.rstrip("/") != expected.path.rstrip("/"):
            raise ValueError("Return to the original public job search page before continuing.")
        query_key = "keywords" if pending["platform"] == "LinkedIn" else "q"
        if parse_qs(current.query).get(query_key) != parse_qs(expected.query).get(query_key):
            raise ValueError("The browser search query changed. Start a new platform search for it.")


def resume_page(center):
    """Read only the pending public search in the same browser session/route."""
    from app.privacy_masks import ROUTE
    pending = getattr(center, "_job_browser_pending", None)
    if not pending or pending["browser"] is not center.browser:
        raise ValueError("No pending browser verification. Start a new platform search first.")
    if ROUTE.snapshot() != pending["route"]:
        raise ValueError("The privacy route changed. Start a new platform search.")
    state = center.browser.session_state()
    if not state["running"] or state["session"] != pending["session"]:
        raise ValueError("The original browser session closed. Start a new platform search.")
    if pending.get("tab") is not None and state.get("tab") != pending["tab"]:
        raise ValueError("The original browser tab changed. Return to that tab before continuing.")
    check_scope(pending, state["url"])
    page = center.browser.structured_page()
    check_scope(pending, page["url"])
    if ROUTE.snapshot() != pending["route"]:
        raise ValueError("The privacy route changed. Start a new platform search.")
    return pending, page


def browse(center, platform: str, query: str, location: str, scan: bool, engine: str = "Google", resume: bool = False, check=None):
    if engine == "Auto" and not resume:
        from app.jobs.browser_fallback import automatic_search
        return automatic_search(center, platform, query, location, scan, check)
    from app.privacy_masks import ROUTE
    page = None
    if resume:
        pending, page = resume_page(center)
        url, platform, engine, scan = pending["url"], pending["platform"], pending["engine"], True
    else:
        url = search_url(platform, query, location, engine)
        center._job_browser_pending = None
    report = {"jobs_view": True, "browser_search": True, "url": url, "platform": platform,
              "engine": engine, "resumed": resume, "imported_jobs": 0, "application_submitted": False}
    if scan:
        if center.browser is None:
            raise RuntimeError("Connect HAYAT's browser for page reading, or uncheck Read page to open a manual search.")
        # Existing browser helper shows its window and navigates without filling or submitting anything.
        if not resume:
            center.browser.form_fields(url, visible=True)
            state = center.browser.session_state() if hasattr(center.browser, "session_state") else None
            center._job_browser_pending = {"url": url, "platform": platform, "engine": engine,
                                           "browser": center.browser, "route": ROUTE.snapshot(),
                                           "tab": state.get("tab") if state else None,
                                           "session": state["session"] if state else None}
            page = center.browser.structured_page()
        if interaction_required(page):
            return blocked_report(report, page)
        report["scroll"] = center.browser.scroll_through(max_scrolls=8, return_to_top=True)
        report["page"] = center.browser.structured_page()
        if resume:
            check_scope(pending, report["page"]["url"])
            if ROUTE.snapshot() != pending["route"]:
                raise ValueError("The privacy route changed. Start a new platform search.")
        report.update(status="page_read", manual_interaction_required=False)
        if interaction_required(report["page"]):
            return blocked_report(report, report["page"])
        center._job_browser_pending = None
        report["next_step"] = ("The visible browser page was read and scrolled. Page text and links are unverified leads, "
                               "not saved or scored jobs. Inspect a posting and use Sources to follow its supported employer board. "
                               "Sign-in, CAPTCHA and site restrictions require manual interaction; no application was submitted.")
    else:
        opened = center.opener(url)
        if opened is False:
            raise RuntimeError("The operating system could not open the browser. Copy the search URL and open it manually: " + url)
        report["status"] = "manual_browser_opened"
        report["next_step"] = ("Opened a manual platform search. Choose the site's country/location and inspect postings there. "
                               "Use Sources to discover and follow a supported employer board; browsing alone does not import or score its results.")
    return report
