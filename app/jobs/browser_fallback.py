"""Try distinct public search providers; never retry through a site's challenge."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

PROVIDERS = ("DuckDuckGo", "Bing", "Google")
COOLDOWN_SECONDS = 300


def automatic_search(center, platform, query, location, scan, check=None):
    from app.jobs.browser_search import browse, search_url
    from app.privacy_masks import ROUTE
    if platform not in {"Web", "Employer boards"}:
        raise ValueError("Auto fallback supports Web or Employer boards. Choose LinkedIn/Indeed directly for their own searches.")
    if not scan or center.browser is None:
        raise ValueError("Auto fallback requires Read page and scroll with HAYAT's visible browser.")
    initial_url = search_url(platform, query, location, PROVIDERS[0])
    route = ROUTE.snapshot()
    if route[0] is not None:
        raise PermissionError("Auto browser fallback cannot preserve the active privacy proxy. Use the route-aware free API search instead.")
    cooldowns = getattr(center, "_job_browser_cooldowns", {})
    center._job_browser_cooldowns = cooldowns
    attempts, last_blocked = [], None
    deadline = time.monotonic() + 180
    attempted = 0

    def checkpoint():
        if ROUTE.snapshot() != route:
            raise ValueError("The privacy route changed. Start a new platform search.")
        return (check is None or check()) and time.monotonic() < deadline

    def active_tab():
        if hasattr(center.browser, "list_tabs"):
            return next((row["index"] for row in center.browser.list_tabs()["tabs"] if row.get("active")), None)
        return None

    for engine in PROVIDERS:
        if not checkpoint():
            break
        retry = cooldowns.get(engine)
        if retry and retry > datetime.now(UTC):
            attempts.append({"engine": engine, "status": "cooling_down", "retry_at": retry.isoformat(timespec="seconds")})
            continue
        try:
            if attempted:
                # Keep the blocked tab and its cookies available for manual completion.
                center.browser.new_tab()
            attempted += 1
            report = browse(center, platform, query, location, True, engine=engine)
            attempts.append({"engine": engine, "status": report["status"],
                             "access_status": report.get("access_status", "accessible"), "url": report["url"]})
            if not checkpoint():
                break
            if not report.get("manual_interaction_required"):
                return {**report, "automatic_fallback": True, "attempts": attempts,
                        "next_step": f"Auto selected {engine} after {attempted} provider attempt(s). " + report["next_step"]}
            last_blocked = (report, getattr(center, "_job_browser_pending", None), active_tab())
        except Exception as exc:
            if ROUTE.snapshot() != route:
                raise ValueError("The privacy route changed. Start a new platform search.") from exc
            attempts.append({"engine": engine, "status": "unavailable", "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        cooldowns[engine] = datetime.now(UTC) + timedelta(seconds=COOLDOWN_SECONDS)

    stopped = not checkpoint()
    if last_blocked and not stopped:
        report, pending, tab = last_blocked
        if tab is not None:
            center.browser.switch_tab(tab)
        center._job_browser_pending = pending
        return {**report, "automatic_fallback": True, "attempts": attempts,
                "next_step": "No attempted public route was usable. " + report["next_step"]}
    pending = getattr(center, "_job_browser_pending", None)
    return {"jobs_view": True, "browser_search": True, "automatic_fallback": True,
            "status": "stopped" if stopped else "no_provider_available", "attempts": attempts,
            "url": pending["url"] if pending else initial_url, "platform": platform, "engine": "Auto",
            "manual_interaction_required": bool(pending), "imported_jobs": 0, "application_submitted": False,
            "next_step": ("Automatic search stopped; no further provider was contacted." if stopped else
                          "Public search routes are cooling down or unavailable. Use Search free APIs or complete verification manually.")}
