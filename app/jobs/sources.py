"""Public job sources: keyless APIs whose robots.txt allows reading, each normalised to one shape.

Every source here asks to be credited with a link and for applicants to be sent to the original
posting; HAYAT shows the source on every job and always applies through the posting's own address.
Remotive is not used: its robots.txt disallows /api/. Adzuna and other keyed APIs are not used
either: they put the key in the address, where HAYAT's web log would record it.

Company career boards on Greenhouse, Lever and Ashby are read when the owner follows a company
("greenhouse:stripe", "lever:palantir", "ashby:ramp").
"""
from __future__ import annotations

import html
import json
import logging
import math
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from app.jobs.text import plain
from app.logging import log_swallowed

_log = logging.getLogger(__name__)
JSON = {"application/json", "text/json", "text/plain", "application/javascript"}


def _date(value: Any) -> str:
    """ISO date-time in UTC from an ISO string, a Unix time in seconds or milliseconds, or ''."""
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            return datetime.fromtimestamp(number / 1000 if number > 1e11 else number, UTC).isoformat(timespec="seconds")
        text = str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            moment = datetime.fromisoformat(text[:10])  # "2026-10-12 …" in some other format: the day is enough
        return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).isoformat(timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return ""


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def clean(value: Any) -> str:
    """One line of text: HTML entities decoded ("&amp;"), and UTF-8 that a feed sent as Latin-1 repaired."""
    text = html.unescape(" ".join(str(value or "").split()))
    # Repaired only when the bytes really are UTF-8: honest Latin-1 text such as "Málaga" fails that test and stays.
    if re.search(r"[\u00c0-\u00ff]", text):
        for codec in ("latin-1", "cp1252"):
            try:
                return text.encode(codec).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
    return text


def job(source: str, native_id: Any, title: str, company: str, *, url: str, apply_url: str = "", location: str = "",
        remote: bool | None = None, description: str = "", tags: list[str] | None = None, posted: Any = None,
        employment_type: str = "", seniority: str = "", salary_min: Any = None, salary_max: Any = None, currency: str = "",
        period: str = "") -> dict[str, Any]:
    text = plain(description)
    if re.search(r"[\u00c0-\u00ff]", text):
        text = "\n".join(clean(line) for line in text.splitlines())
    return {"key": f"{source}:{native_id}", "source": source, "source_name": SOURCES[source.split("@")[0]]["name"],
            "title": clean(title)[:200], "company": clean(company)[:120], "location": clean(location)[:200], "remote": remote, "url": url, "apply_url": apply_url or url,
            "description": text[:20000], "tags": [str(tag)[:40] for tag in (tags or []) if tag][:20], "posted_at": _date(posted),
            "employment_type": str(employment_type or "")[:40], "seniority": str(seniority or "")[:40],
            "salary_min": _number(salary_min), "salary_max": _number(salary_max), "currency": str(currency or "")[:8],
            "salary_period": str(period or "")[:12]}


def _get(fetcher: Any, url: str, max_bytes: int = 8 * 1024 * 1024) -> Any:
    resource = fetcher.fetch(url, max_bytes, JSON)
    return json.loads(bytes(resource.content))


# ---------------------------------------------------------------------- feeds
def arbeitnow(fetcher: Any, terms: list[str]) -> list[dict[str, Any]]:
    return arbeitnow_rows(_get(fetcher, "https://www.arbeitnow.com/api/job-board-api"))


def arbeitnow_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = data.get("data", [])
    return [job("arbeitnow", item.get("slug"), item.get("title"), item.get("company_name"), url=item.get("url", ""),
                location=item.get("location", ""), remote=bool(item.get("remote")), description=item.get("description", ""),
                tags=item.get("tags", []) + item.get("job_types", []), posted=item.get("created_at"),
                employment_type=", ".join(item.get("job_types", [])[:2]))
            for item in rows if item.get("slug")]


def remoteok(fetcher: Any, terms: list[str]) -> list[dict[str, Any]]:
    rows, seen = [], set()
    for term in (terms or [""])[:3]:
        tag = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
        for item in _get(fetcher, "https://remoteok.com/api" + (f"?tag={quote(tag)}" if tag else "")):
            if isinstance(item, dict) and item.get("id") and item["id"] not in seen:
                seen.add(item["id"])
                rows.append(item)
    return [job("remoteok", item["id"], item.get("position"), item.get("company"), url=item.get("url", ""),
                apply_url=item.get("apply_url") or item.get("url", ""), location=item.get("location") or "Remote", remote=True,
                description=item.get("description", ""), tags=item.get("tags", []), posted=item.get("epoch") or item.get("date"),
                salary_min=item.get("salary_min"), salary_max=item.get("salary_max"), currency="USD" if item.get("salary_min") else "",
                period="year" if item.get("salary_min") else "")
            for item in rows]


def jobicy(fetcher: Any, terms: list[str]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for term in (terms or [""])[:3]:
        query = "count=100" + (f"&tag={quote(term)}" if term else "")
        for row in jobicy_rows(_get(fetcher, f"https://jobicy.com/api/jobs?{query}")):
            found[row["key"]] = row
    return list(found.values())


def jobicy_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    found = {}
    for item in data.get("jobs", []):
        if item.get("id"):
            row = job("jobicy", item.get("id"), item.get("jobTitle"), item.get("companyName"), url=item.get("url", ""),
                      location=item.get("jobGeo", "Remote"), remote=True, description=item.get("jobDescription") or item.get("jobExcerpt", ""),
                      tags=list(item.get("jobIndustry") or []) + list(item.get("jobType") or []), posted=item.get("pubDate"),
                      employment_type=", ".join(item.get("jobType") or []), seniority=item.get("jobLevel", ""),
                      salary_min=item.get("salaryMin"), salary_max=item.get("salaryMax"), currency=item.get("salaryCurrency", ""),
                      period=item.get("salaryPeriod", ""))
            found[row["key"]] = row
    return list(found.values())


def himalayas(fetcher: Any, terms: list[str]) -> list[dict[str, Any]]:
    rows = []
    if terms:  # its search endpoint, one term at a time
        for term in terms[:3]:
            rows += _get(fetcher, f"https://himalayas.app/jobs/api/search?q={quote(term)}&limit=20").get("jobs", [])
    else:
        rows = _get(fetcher, "https://himalayas.app/jobs/api?limit=20").get("jobs", [])
    return himalayas_rows({"jobs": rows})


def himalayas_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [job("himalayas", item.get("guid") or item.get("applicationLink"), item.get("title"), item.get("companyName"),
                url=item.get("applicationLink") or item.get("guid", ""), location=", ".join(item.get("locationRestrictions") or []) or "Remote",
                remote=True, description=item.get("description") or item.get("excerpt", ""),
                tags=list(item.get("categories") or []), posted=item.get("pubDate"), employment_type=item.get("employmentType", ""),
                seniority=", ".join(item.get("seniority") or []) if isinstance(item.get("seniority"), list) else item.get("seniority", ""),
                salary_min=item.get("minSalary"), salary_max=item.get("maxSalary"), currency=item.get("currency", ""),
                period=item.get("salaryPeriod", ""))
            for item in data.get("jobs", []) if item.get("guid") or item.get("applicationLink")]


def themuse(fetcher: Any, terms: list[str]) -> list[dict[str, Any]]:
    rows = []
    for page in range(3):
        rows += _get(fetcher, f"https://www.themuse.com/api/public/jobs?page={page}&descending=true").get("results", [])
    return muse_rows({"results": rows})


def muse_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in data.get("results", []):
        places = [place.get("name", "") for place in item.get("locations", [])]
        out.append(job("themuse", item.get("id"), item.get("name"), (item.get("company") or {}).get("name", ""),
                       url=(item.get("refs") or {}).get("landing_page", ""), location="; ".join(places),
                       remote=any("remote" in place.lower() for place in places), description=item.get("contents", ""),
                       tags=[category.get("name", "") for category in item.get("categories", [])], posted=item.get("publication_date"),
                       seniority=", ".join(level.get("name", "") for level in item.get("levels", []))))
    return out


def workingnomads(fetcher: Any, terms: list[str]) -> list[dict[str, Any]]:
    rows = _get(fetcher, "https://www.workingnomads.com/api/exposed_jobs/")
    return [job("workingnomads", item.get("url"), item.get("title"), item.get("company_name"), url=item.get("url", ""),
                location=item.get("location") or "Remote", remote=True, description=item.get("description", ""),
                tags=[tag.strip() for tag in str(item.get("tags") or "").split(",")] + [item.get("category_name", "")],
                posted=item.get("pub_date"))
            for item in rows if item.get("url")]


# ---------------------------------------------------------------------- company boards
def greenhouse(fetcher: Any, company: str) -> list[dict[str, Any]]:
    data = _get(fetcher, f"https://boards-api.greenhouse.io/v1/boards/{quote(company)}/jobs?content=true", 16 * 1024 * 1024)
    return [job(f"greenhouse@{company}", item.get("id"), item.get("title"), item.get("company_name") or company,
                url=item.get("absolute_url", ""), location=(item.get("location") or {}).get("name", ""),
                remote="remote" in str((item.get("location") or {}).get("name", "")).lower() or None,
                description=item.get("content", ""), tags=[dept.get("name", "") for dept in item.get("departments", [])],
                posted=item.get("first_published") or item.get("updated_at"))
            for item in data.get("jobs", [])]


def lever(fetcher: Any, company: str) -> list[dict[str, Any]]:
    data = _get(fetcher, f"https://api.lever.co/v0/postings/{quote(company)}?mode=json", 16 * 1024 * 1024)
    out = []
    for item in data:
        categories = item.get("categories") or {}
        salary = item.get("salaryRange") or {}
        body = "\n".join([item.get("descriptionPlain") or item.get("description", "")]
                         + [f"\n{block.get('text', '')}\n{plain(block.get('content', ''))}" for block in item.get("lists", [])]
                         + [item.get("additionalPlain") or ""])
        out.append(job(f"lever@{company}", item.get("id"), item.get("text"), company.title(), url=item.get("hostedUrl", ""),
                       apply_url=item.get("applyUrl", ""), location=categories.get("location", ""),
                       remote=str(item.get("workplaceType", "")).lower() == "remote" or None, description=body,
                       tags=[categories.get("team", ""), categories.get("department", "")], posted=item.get("createdAt"),
                       employment_type=categories.get("commitment", ""), salary_min=salary.get("min"), salary_max=salary.get("max"),
                       currency=salary.get("currency", ""), period=_pay_period(salary.get("interval", ""))))
    return out


def ashby(fetcher: Any, company: str) -> list[dict[str, Any]]:
    data = _get(fetcher, f"https://api.ashbyhq.com/posting-api/job-board/{quote(company)}?includeCompensation=true", 16 * 1024 * 1024)
    return [job(f"ashby@{company}", item.get("id"), item.get("title"), company.title(), url=item.get("jobUrl", ""),
                apply_url=item.get("applyUrl", ""), location=item.get("location", ""), remote=bool(item.get("isRemote")),
                description=item.get("descriptionPlain") or item.get("descriptionHtml", ""),
                tags=[item.get("department", ""), item.get("team", "")], posted=item.get("publishedAt"),
                employment_type=item.get("employmentType", ""), **_ashby_pay(item))
            for item in data.get("jobs", []) if item.get("isListed", True)]


def _pay_period(value: str) -> str:
    return {"1 year": "year", "yearly": "year", "annually": "year", "annual": "year",
            "1 month": "month", "monthly": "month", "1 hour": "hour", "hourly": "hour",
            "weekly": "week", "daily": "day"}.get(str(value).lower(), str(value).lower())


def _ashby_pay(item: dict[str, Any]) -> dict[str, Any]:
    # Summary components preserve the published range. Never rank equity or bonuses as base salary.
    components = (item.get("compensation") or {}).get("summaryComponents") or []
    salary = [part for part in components if part.get("compensationType") == "Salary"]
    if len(salary) != 1:
        return {}
    part = salary[0]
    return {"salary_min": part.get("minValue"), "salary_max": part.get("maxValue"),
            "currency": part.get("currencyCode") or "", "period": _pay_period(part.get("interval") or "")}


SOURCES: dict[str, dict[str, Any]] = {
    "arbeitnow": {"name": "Arbeitnow", "site": "https://www.arbeitnow.com", "reader": arbeitnow,
                  "about": "Europe (mostly Germany), many in German; remote and on-site"},
    "remoteok": {"name": "Remote OK", "site": "https://remoteok.com", "reader": remoteok, "about": "remote jobs worldwide, tech-heavy"},
    "jobicy": {"name": "Jobicy", "site": "https://jobicy.com", "reader": jobicy, "about": "remote jobs, searched by your terms"},
    "himalayas": {"name": "Himalayas", "site": "https://himalayas.app", "reader": himalayas, "about": "remote jobs with location limits"},
    "themuse": {"name": "The Muse", "site": "https://www.themuse.com", "reader": themuse, "about": "US-led, many fields, remote and on-site"},
    "workingnomads": {"name": "Working Nomads", "site": "https://www.workingnomads.com", "reader": workingnomads,
                      "about": "remote jobs worldwide"},
    "greenhouse": {"name": "Greenhouse", "site": "https://www.greenhouse.com", "board": greenhouse, "about": "a company's own careers board"},
    "lever": {"name": "Lever", "site": "https://www.lever.co", "board": lever, "about": "a company's own careers board"},
    "ashby": {"name": "Ashby", "site": "https://www.ashbyhq.com", "board": ashby, "about": "a company's own careers board"},
}
FEEDS = tuple(name for name, item in SOURCES.items() if "reader" in item)
BOARD = re.compile(r"^(greenhouse|lever|ashby):([a-z0-9][a-z0-9-]{0,60})$")


_COMPANY_SUFFIX = re.compile(r"\b(?:inc|llc|ltd|limited|gmbh|corp|co|company|technologies|technology|labs|software|group|"
                             r"holdings|solutions|systems|global|international)\b")


def company_slugs(name: str) -> list[str]:
    """Candidate board slugs for a company name: "Stripe Inc." -> stripe; "Working Nomads" -> workingnomads, working-nomads."""
    base = str(name or "").strip().lower()
    trimmed = _COMPANY_SUFFIX.sub(" ", base).strip()
    candidates = []
    for value in (trimmed, base):
        candidates.append(re.sub(r"[^a-z0-9]+", "", value))
        candidates.append(re.sub(r"[^a-z0-9]+", "-", value).strip("-"))
    return [slug for slug in dict.fromkeys(candidates) if 1 < len(slug) <= 60]


def board_from_url(url: str) -> tuple[str, str] | None:
    """Only known public board hosts; suffix lookalikes and private URLs never become ATS probes."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return None
    ats = {"boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse",
           "jobs.lever.co": "lever", "jobs.ashbyhq.com": "ashby"}.get(parsed.hostname or "")
    slug = parsed.path.strip("/").split("/")[0].lower()
    return (ats, slug) if ats and BOARD.fullmatch(f"{ats}:{slug}") else None


class _BoardLinks(HTMLParser):
    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.boards: dict[tuple[str, str], str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ("a", "iframe") or len(self.boards) >= 12:
            return
        link = dict(attrs).get("href" if tag == "a" else "src")
        if link:
            url = urljoin(self.url, link)
            try:
                board = board_from_url(url)
            except ValueError:
                return
            if board:
                self.boards[board] = url


def discover(fetcher: Any, name: str, limit: int = 12) -> list[dict[str, Any]]:
    """Public ATS roles from a company name, board URL or links on a provided careers page.

    Public, keyless JSON boards only — no login, no challenge page, nothing gated. A company not found here may
    use a different system or advertise nowhere public. At most ``limit`` probes (a slug that is not on an ATS
    answers 404, which is not an error). Name guesses stop at the first hit; provided pages can link multiple boards.
    """
    candidates: list[tuple[str, str, str, str]] = []
    if "://" in name:
        parsed = urlsplit(name)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise ValueError("Use a public HTTPS employer careers page or ATS board URL.")
        board = board_from_url(name)
        if board:
            candidates.append((*board, name, "provided public board URL"))
        else:
            resource = fetcher.fetch(name, 2 * 1024 * 1024, {"text/html", "application/xhtml+xml"})
            links = _BoardLinks(name)
            links.feed(bytes(resource.content).decode("utf-8", errors="replace"))
            candidates.extend((ats, slug, url, f"linked from {name}") for (ats, slug), url in links.boards.items())
    else:
        candidates = [(ats, slug, "", "company-name guess; verify employer identity")
                      for slug in company_slugs(name) for ats in ("greenhouse", "lever", "ashby")]
    found = []
    for ats, slug, url, evidence in candidates[:max(0, min(limit, 12))]:
        try:
            rows = SOURCES[ats]["board"](fetcher, slug)
        except Exception:  # noqa: BLE001, S112 -- public boards can be absent or unavailable
            continue
        if rows:
            found.append({"ats": ats, "slug": slug, "follow": f"{ats}:{slug}", "jobs": len(rows), "url": url,
                          "evidence": evidence, "samples": [row["title"] for row in rows[:6] if row.get("title")]})
            if "://" not in name:
                break
    return found


def read_source(fetcher: Any, name: str, terms: list[str]) -> list[dict[str, Any]]:
    """Jobs from one feed ("remoteok") or one company board ("lever:palantir")."""
    board = BOARD.match(name)
    if board:
        return SOURCES[board[1]]["board"](fetcher, board[2])
    if name not in FEEDS:
        raise ValueError(f"Unknown job source {name!r}.")
    reader: Callable[[Any, list[str]], list[dict[str, Any]]] = SOURCES[name]["reader"]
    return reader(fetcher, terms)


def relevant(row: dict[str, Any], terms: list[str]) -> bool:
    """Whether a job is about a search term: every word of the term in its title (any order), or the term among its tags."""
    if not terms:
        return True
    title, tags = row["title"].lower(), " | ".join(row.get("tags", [])).lower()
    for term in terms:
        parts = [part for part in re.split(r"\s+", term.lower().strip()) if part]
        if not parts:
            continue
        if all(re.search(r"(?<![\w+#])" + re.escape(part) + r"(?![\w+#])", title) for part in parts):
            return True
        if re.search(r"(?<![\w+#])" + r"[\s-]+".join(map(re.escape, parts)) + r"(?![\w+#])", tags):
            return True
    return False


def _posting_urls(row: dict[str, Any]) -> set[str]:
    """Comparable posting addresses, preserving requisition query parameters and SPA fragments."""
    result = set()
    for value in (row.get("url"), row.get("apply_url")):
        try:
            parts = urlsplit(str(value or ""))
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
                continue
            query = [(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
                     if not key.casefold().startswith("utm_") and key.casefold() not in ("gclid", "fbclid", "msclkid")]
            path = parts.path.rstrip("/") or "/"
            # A shared company homepage or generic careers page does not identify a requisition.
            if path.casefold() in ("/", "/jobs", "/careers", "/careers/jobs") and not query and not parts.fragment:
                continue
            result.add(urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path,
                                   urlencode(sorted(query)), parts.fragment)))
        except ValueError:
            continue
    return result


def same_posting(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Conservative cross-board duplicate evidence; a shared title/company is insufficient."""
    if left.get("key") == right.get("key"):
        return True
    # Separate native requisitions on the same board remain separate even when boilerplate is identical.
    if left.get("source") == right.get("source"):
        return False
    def identity(row):
        return (" ".join(str(row.get("title") or "").casefold().split()),
                " ".join(str(row.get("company") or "").casefold().split()))
    if identity(left) != identity(right) or not all(identity(left)):
        return False
    if _posting_urls(left) & _posting_urls(right):
        return True
    def description(row):
        return " ".join(str(row.get("description") or "").casefold().split())
    body = description(left)
    if len(body) < 160 or body != description(right):
        return False
    def place(row):
        return " ".join(str(row.get("location") or "").casefold().split())
    generic_remote = {"remote", "anywhere", "worldwide", "global", "work from anywhere"}
    return (place(left) == place(right) and bool(place(left))) or (
        left.get("remote") is True and right.get("remote") is True
        and place(left) in generic_remote and place(right) in generic_remote)


def matches_location(row: dict[str, Any], location: str) -> bool:
    """Match advertised locations or explicit country restrictions, not stray description words."""
    from app.jobs.understand import REGIONS
    value = location.strip()
    if not value or value.casefold() in {"worldwide", "whole world", "all locations", "anywhere", "global", "world"}:
        return True
    if re.search(r"[;|]", value):
        return any(matches_location(row, part) for part in re.split(r"[;|]", value) if part.strip())
    reading = row.get("reading") or {}
    if value.casefold() == "remote":
        return reading.get("mode") == "remote" or row.get("remote") is True
    if listed_in_location(str(row.get("location") or ""), value):
        return True
    # Country names/aliases use the same bounded patterns as the posting reader.
    for name, pattern in REGIONS.items():
        if re.fullmatch(rf"(?:{pattern})", value, re.IGNORECASE):
            return name in reading.get("regions", [])
    return False


def listed_in_location(place: str, location: str) -> bool:
    """Whether the posting's own location field names this place (directly or by a country alias)."""
    from app.jobs.understand import REGIONS
    normalized = re.sub(r"[^\w]+", " ", str(location or "").casefold()).strip()
    advertised = re.sub(r"[^\w]+", " ", str(place or "").casefold()).strip()
    if normalized and re.search(r"(?<!\w)" + re.escape(normalized) + r"(?!\w)", advertised):
        return True
    for _name, pattern in REGIONS.items():
        if re.fullmatch(rf"(?:{pattern})", str(location or "").strip(), re.IGNORECASE):
            return bool(re.search(rf"\b(?:{pattern})\b", str(place or ""), re.IGNORECASE))
    return False


def collect(fetcher: Any, sources: list[str], terms: list[str], depth: str = "normal", check=None, progress=None) -> dict[str, Any]:
    """Every source's relevant jobs, with per-source counts and errors; one failing source never stops the others."""
    from app.jobs.search_depth import DEPTHS, collect_deep
    if depth not in DEPTHS:
        raise ValueError("Choose normal, deep or maximum job search depth.")
    if depth != "normal" or check is not None or progress is not None:
        return collect_deep(fetcher, sources, terms, depth, check, progress)
    jobs: dict[str, dict[str, Any]] = {}
    report, cooling, deferred = [], {}, []
    for name in sources:
        if name.split(":", 1)[0] in cooling:
            deferred.append({"source": name, **cooling[name.split(":", 1)[0]]})
            continue
        try:
            rows = read_source(fetcher, name, terms)
            kept = [row for row in rows if row["title"] and row["url"] and relevant(row, terms)]
            for row in kept:
                jobs[row["key"]] = row
            report.append({"source": name, "read": len(rows), "kept": len(kept)})
        except Exception as exc:  # offline, refused by robots.txt or changed shape: say so and go on
            from app.jobs.access import failure_details
            log_swallowed(_log, f"reading jobs from {name}")
            details = failure_details(exc)
            report.append({"source": name, "read": 0, "kept": 0, "error": f"{type(exc).__name__}: {str(exc)[:160]}", **details})
            if details.get("retry_at"):
                cooling[name.split(":", 1)[0]] = {"retry_at": details["retry_at"], "reason": details["access_status"]}
    return {"jobs": list(jobs.values()), "sources": report, "deferred_sources": deferred}
