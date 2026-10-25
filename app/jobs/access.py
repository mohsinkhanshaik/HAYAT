"""Describe access failures and publisher cooldowns without evading them."""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


CHALLENGE_PATHS = ("/sorry/", "/checkpoint/", "/challenge", "/captcha", "/turing/", "/cdn-cgi/challenge", "/anomaly")
LOGIN_PATHS = ("/authwall", "/login", "/signin", "/account/login", "/accounts/login")


def page_access(page: dict) -> str:
    path = urlsplit(str(page.get("url") or "")).path.casefold()
    text = str(page.get("text") or "").casefold()[:15000]
    title = str(page.get("title") or "").casefold()
    signals = page.get("access_signals") or {}
    if path.startswith(CHALLENGE_PATHS) or signals.get("visible_challenge"):
        return "human_verification"
    if path.startswith(LOGIN_PATHS):
        return "sign_in_required"
    if "unusual traffic from your computer network" in text:
        return "human_verification"
    if (len(text) < 2000 or "verification" in title or "robot check" in title) and any(
        phrase in text for phrase in ("verify you are human", "verify that you are human", "complete the captcha")
    ):
        return "human_verification"
    if len(text) < 2000 and ("security verification" in text or "checking your browser" in text
                            or "just a moment" in title or "attention required" in title):
        return "human_verification"
    if page.get("http_status") == 429 or "too many requests" in title or "rate limit exceeded" in title:
        return "rate_limited"
    if page.get("http_status") in (401, 403) or title.strip() in {"access denied", "request blocked", "forbidden", "403 forbidden"}:
        return "access_denied"
    return "accessible"


def failure_details(exc: Exception, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {})
    kind, seconds = "unavailable", 0
    if status == 429:
        kind, seconds = "rate_limited", 60
    elif status == 403:
        kind, seconds = "access_denied", 900
    elif status == 503:
        kind, seconds = "temporarily_unavailable", 30
    elif isinstance(exc, PermissionError) and "robots.txt" in str(exc).lower():
        kind, seconds = "robots_disallowed", 3600
    retry = str(headers.get("retry-after", "")).strip()
    if retry and status in (429, 503):
        try:
            duration = float(retry) if retry.isdecimal() else (parsedate_to_datetime(retry).astimezone(UTC) - now).total_seconds()
            if math.isfinite(duration):
                seconds = max(1, math.ceil(duration))
        except (TypeError, ValueError, OverflowError):
            pass
    result = {"access_status": kind}
    if seconds:
        # Very large publisher values remain a long deferral, never an immediate retry.
        seconds = min(seconds, 10 * 365 * 86400)
        result.update(retry_after_seconds=seconds, retry_at=(now + timedelta(seconds=seconds)).isoformat(timespec="seconds"))
    return result
