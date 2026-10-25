"""Bounded, cancellable pagination of documented free public job APIs.

Pages are yielded before requesting the next one: later failures retain earlier
evidence. Cursors are opaque tokens, never URLs to follow to another host.
"""
from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import urlencode

from app.jobs.sources import _get, arbeitnow_rows, himalayas_rows, jobicy_rows, muse_rows, read_source, relevant

DEPTHS = {
    "normal": {"pages": 1, "terms": 3, "requests": 24, "seconds": 90},
    "deep": {"pages": 5, "terms": 6, "requests": 100, "seconds": 180},
    "maximum": {"pages": 20, "terms": 12, "requests": 240, "seconds": 300},
}


class SearchStopped(RuntimeError):
    pass


class SourceLimit(RuntimeError):
    pass


class SearchBudget:
    def __init__(self, fetcher: Any, settings: dict, check: Callable | None, progress: Callable | None):
        self.fetcher, self.settings = fetcher, settings
        self.check, self.progress = check, progress
        self.started, self.requests = time.monotonic(), 0
        self.source = ""
        self.source_limit = settings["requests"]

    def checkpoint(self) -> None:
        if self.check and not self.check():
            raise SearchStopped("stopped by user")
        if self.requests >= self.settings["requests"]:
            raise SearchStopped("request budget reached")
        if self.requests >= self.source_limit:
            raise SourceLimit("per-source request budget reached")
        if time.monotonic() - self.started >= self.settings["seconds"]:
            raise SearchStopped("time budget reached")

    def fetch(self, url, *args, **kwargs):
        self.checkpoint()
        self.requests += 1
        if self.progress:
            self.progress({"source": self.source, "requests": self.requests,
                           "request_limit": self.settings["requests"]})
        return self.fetcher.fetch(url, *args, **kwargs)


def source_pages(fetcher: Any, name: str, terms: list[str], pages: int):
    """Yield (jobs, limit reason) for fixed, documented provider endpoints."""
    if name not in {"arbeitnow", "himalayas", "jobicy", "themuse"}:
        # Employer boards and snapshot APIs expose their available list in one call.
        for start in range(0, max(1, len(terms)), 3) if name == "remoteok" else (0,):
            yield read_source(fetcher, name, terms[start:start + 3]), "snapshot API; historical coverage unknown"
        return
    queries = (terms or [""]) if name in {"himalayas", "jobicy"} else [""]
    for term in queries:
        seen, cursors = set(), set()
        cursor = None
        for index in range(pages):
            if name == "arbeitnow":
                data = _get(fetcher, "https://www.arbeitnow.com/api/job-board-api?" + urlencode({"page": index + 1}))
                rows = arbeitnow_rows(data)
                end = not (data.get("links") or {}).get("next")
            elif name == "himalayas":
                params = {"q": term, "page": index + 1} if term else {"limit": 20}
                if cursor and not term:
                    params["cursor"] = cursor
                data = _get(fetcher, "https://himalayas.app/jobs/api" + ("/search" if term else "") + "?" + urlencode(params))
                rows = himalayas_rows(data)
                end = not rows if term else not data.get("nextCursor")
            elif name == "jobicy":
                params = {"count": 100}
                if term:
                    params["tag"] = term
                if cursor:
                    params["cursor"] = cursor
                data = _get(fetcher, "https://jobicy.com/api/jobs?" + urlencode(params))
                if data.get("success") is False:
                    raise ValueError(str(data.get("error") or "Jobicy rejected the request")[:160])
                rows = jobicy_rows(data)
                end = not data.get("nextCursor")
            else:
                data = _get(fetcher, f"https://www.themuse.com/api/public/jobs?page={index}&descending=true")
                rows = muse_rows(data)
                end = not rows or index + 1 >= int(data.get("page_count") or pages)
            keys = {row["key"] for row in rows}
            repeated = bool(keys) and keys <= seen
            fresh = [row for row in rows if row["key"] not in seen]
            seen.update(keys)
            next_cursor = data.get("nextCursor")
            invalid_cursor = bool(next_cursor and (not isinstance(next_cursor, str) or len(next_cursor) > 4096))
            if invalid_cursor:
                yield fresh, "invalid continuation token"
                raise ValueError("Provider returned an invalid continuation token")
            repeated_cursor = bool(next_cursor and next_cursor in cursors)
            if next_cursor:
                cursors.add(next_cursor)
            reason = ("provider repeated a page or cursor" if repeated or repeated_cursor else
                      "page limit reached" if index + 1 == pages and not end else "")
            yield fresh, reason
            if end or repeated or repeated_cursor or not rows:
                break
            cursor = next_cursor


def collect_deep(fetcher: Any, sources: list[str], terms: list[str], depth: str,
                 check: Callable | None = None, progress: Callable | None = None) -> dict[str, Any]:
    settings = DEPTHS[depth]
    budget = SearchBudget(fetcher, settings, check, progress)
    jobs, report, cooling, deferred = {}, [], {}, []
    stopped = ""
    for position, name in enumerate(sources):
        if name.split(":", 1)[0] in cooling:
            deferred.append({"source": name, **cooling[name.split(":", 1)[0]]})
            continue
        budget.source = name
        budget.source_limit = budget.requests + max(1, (settings["requests"] - budget.requests) // (len(sources) - position))
        item = {"source": name, "read": 0, "kept": 0, "pages": 0, "requests": 0, "limited": False}
        before = budget.requests
        seen = set()
        try:
            budget.checkpoint()
            for rows, reason in source_pages(budget, name, terms[:settings["terms"]], settings["pages"]):
                item["pages"] += 1
                if reason:
                    item.update(limited=True, stop_reason=reason)
                for row in rows:
                    if row["key"] in seen:
                        continue
                    seen.add(row["key"])
                    item["read"] += 1
                    if row["title"] and row["url"] and relevant(row, terms):
                        jobs[row["key"]] = row
                        item["kept"] += 1
                if len(jobs) >= 20000:
                    raise SearchStopped("20,000 collected-job budget reached")
        except SearchStopped as exc:
            stopped = str(exc)
            if budget.requests == before:
                break  # This source was not read; keep it in unvisited_sources.
            item.update(limited=True, stop_reason=stopped)
        except SourceLimit as exc:
            item.update(limited=True, stop_reason=str(exc))
        except Exception as exc:  # each provider's earlier pages remain usable
            from app.jobs.access import failure_details
            item.update(error=f"{type(exc).__name__}: {str(exc)[:160]}", limited=True, **failure_details(exc))
            if item.get("retry_at"):
                cooling[name.split(":", 1)[0]] = {"retry_at": item["retry_at"], "reason": item["access_status"]}
        item["requests"] = budget.requests - before
        report.append(item)
        if stopped:
            break
    return {"jobs": list(jobs.values()), "sources": report, "deferred_sources": deferred,
            "coverage": {"depth": depth, "requests": budget.requests, "request_limit": settings["requests"],
                         "pages_per_query": settings["pages"], "terms_searched": terms[:settings["terms"]],
                         "terms_omitted": terms[settings["terms"]:], "stop_reason": stopped or "configured sources checked",
                         "unvisited_sources": [name for name in sources if name not in {item['source'] for item in report}], "exhaustive": False}}
