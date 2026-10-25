"""Find real businesses to approach, and keep them as leads.

Built after a live conversation in which HAYAT, asked to "create lead and make
a list", ran ``lead_list`` on an empty table and then -- on the next "ok great"
-- wrote a table of "verified" installers with owners named John Doe and Sarah
Smith, said the list was saved in a CSV that did not exist, and said ten cold
emails had gone out from an account that was never set up. None of it was real.

This tool does the real version of that work, and only that:

* searches the public web for the kind of business and place asked for;
* drops directories, review sites and "top 10" articles, keeping one result per
  company website;
* opens each company's own homepage (robots.txt honoured) and records only the
  email address and phone number that business publishes there;
* saves the list as a CSV and adds each new company to the lead tracker.

It never invents a person, an email address or a "verified" mark. A published
address is labelled as published, not verified -- nobody here has checked that
it is read. It contacts no one: outreach stays a separate, owner-confirmed step.
"""

from __future__ import annotations

import csv
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Sites that list other businesses rather than being one. A lead is a company
#: that could become a client, not the page where it was found.
DIRECTORY_DOMAINS = frozenset({
    "yelp.com", "yellowpages.com", "angi.com", "angieslist.com", "homeadvisor.com", "thumbtack.com", "bbb.org",
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
    "reddit.com", "quora.com", "pinterest.com", "nextdoor.com", "google.com", "maps.google.com", "bing.com",
    "mapquest.com", "wikipedia.org", "indeed.com", "glassdoor.com", "ziprecruiter.com", "porch.com", "houzz.com",
    "forbes.com", "thisoldhouse.com", "consumeraffairs.com", "energysage.com", "solarreviews.com", "enfsolar.com",
    "bobvila.com", "networx.com", "buildzoom.com", "manta.com", "chamberofcommerce.com", "expertise.com",
    "upcity.com", "clutch.co", "trustpilot.com", "crunchbase.com", "zoominfo.com", "dnb.com", "apollo.io",
    "needinstall.com", "solarnearme.com", "bark.com", "craigslist.org", "amazon.com", "nerdwallet.com",
    "usnews.com", "cnet.com", "investopedia.com", "superpages.com", "citysearch.com", "birdeye.com",
})

#: Titles of round-ups and comparisons, which are directories by another name.
_LISTICLE = re.compile(
    r"(?i)\b(?:best|top\s*\d+|top\s+(?:ten|rated)|compare|comparison|reviews?\b|ratings?\b|rankings?|"
    r"near\s+me|guide|list\s+of|directory|companies\s+in|installers\s+in|\d+\s+(?:best|top))\b"
)

_EMAIL = re.compile(r"(?i)\b[a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9.-]+\.[a-z]{2,24}\b")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b[2-9]\d{2}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_SITE_NAME = re.compile(r"""(?is)<meta[^>]+property=["']og:site_name["'][^>]+content=["']([^"']+)["']""")
#: Addresses that are not a way to reach the business.
_NOT_A_CONTACT = re.compile(
    r"(?i)(?:\.(?:png|jpe?g|gif|webp|svg|css|js)$|@(?:\d+x|sentry|example|domain|email|yourdomain|wixpress|"
    r"godaddy|squarespace)\.|^(?:noreply|no-reply|donotreply|user|name|your|email)@)"
)

COLUMNS = ("name", "website", "published_email", "published_phone", "found_by_search", "search_result_title",
           "snippet", "checked_homepage", "found_on", "status")


def registered_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


#: Domain words of quote brokers and comparison sites ("instantlocalquote",
#: "solarproguide"): they sell the same leads, so they are not clients.
_BROKER_DOMAIN = re.compile(r"(?i)(?:quotes?|guide|finders?|compare|reviews?|ratings?|nearme|directory|leads|"
                            r"matcher|rankings?|bids?|listings?)")


def is_directory(url: str, title: str = "") -> bool:
    domain = registered_domain(url)
    if domain in DIRECTORY_DOMAINS or any(domain.endswith("." + item) for item in DIRECTORY_DOMAINS):
        return True
    if _BROKER_DOMAIN.search(domain.split(".")[0]):
        return True
    return bool(_LISTICLE.search(title or ""))


def _company_name(titles: list[str], domain: str, site_name: str = "") -> str:
    """The business's own name: og:site_name, else the title part that matches its domain."""
    stem = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())

    def matches(part: str) -> bool:
        squashed = re.sub(r"[^a-z0-9]", "", part.lower())
        return bool(stem and squashed and (stem in squashed or squashed in stem))

    site = html.unescape(" ".join((site_name or "").split()))
    # Some sites put image alt text in og:site_name; a name is short and not a sentence.
    if site and (matches(site) or len(site.split()) <= 5):
        return site[:90]
    parts_by_title = [[part.strip() for part in re.split(r"\s+[|\-–—:·]\s+", html.unescape(title or ""))
                       if part.strip()] for title in titles if title]
    for parts in parts_by_title:
        for part in parts:
            if matches(part):
                return part[:90]
    for parts in parts_by_title:
        short = [part for part in parts if len(part.split()) <= 5]
        if short:
            return short[-1 if len(parts) > 1 else 0][:90]
    return domain.split(".")[0].replace("-", " ").title() or domain


def published_contacts(page: str, domain: str) -> tuple[list[str], list[str]]:
    """Emails and phone numbers printed on a page, best first. Nothing is guessed."""
    text = html.unescape(page or "")
    emails: list[str] = []
    for match in _EMAIL.findall(text):
        email = match.strip(".").lower()
        if _NOT_A_CONTACT.search(email) or email in emails:
            continue
        emails.append(email)
    emails.sort(key=lambda item: (not item.endswith("@" + domain) and not item.endswith("." + domain), len(item)))
    phones: list[str] = []
    for match in _PHONE.findall(re.sub(r"<[^>]+>", " ", text)):
        digits = re.sub(r"\D", "", match)[-10:]
        if len(digits) == 10 and digits not in {re.sub(r"\D", "", p)[-10:] for p in phones}:
            phones.append(f"({digits[:3]}) {digits[3:6]}-{digits[6:]}")
    return emails[:3], phones[:2]


def search_queries(niche: str, place: str) -> list[str]:
    niche, place = " ".join(niche.split()), " ".join(place.split())
    where = f" in {place}" if place else ""
    return [f"{niche} company{where}", f"local {niche} {place}".strip(), f"{niche} {place} contact us".strip(),
            f"{niche} {place} free estimate".strip()]


class ProspectFinder:
    """Searches, filters, reads homepages and records. `search`/`fetch_text` are injectable for tests."""

    def __init__(self, search: Callable[[str, int], list[Any]], fetch_text: Callable[[str], str] | None,
                 ledger: Any, output: Path) -> None:
        self.search, self.fetch_text, self.ledger, self.output = search, fetch_text, ledger, Path(output)

    def find(self, niche: str, place: str = "", limit: int = 15, check_websites: bool = True,
             save_to_leads: bool = True) -> dict[str, Any]:
        niche = " ".join(str(niche or "").split()).strip(" .")
        place = " ".join(str(place or "").split()).strip(" .")
        if len(niche) < 3:
            raise ValueError("Say what kind of business to look for, for example: solar installers in Texas.")
        limit = max(1, min(int(limit or 15), 40))
        candidates: dict[str, dict[str, Any]] = {}
        skipped = 0
        queries_run: list[str] = []
        for query in search_queries(niche, place):
            if len(candidates) >= limit:
                break
            queries_run.append(query)
            try:
                results = self.search(query, 20)
            except Exception:
                log_swallowed(_log, f"prospect search {query!r}", level=logging.WARNING)
                continue
            for item in results:
                url = str(_field(item, "url") or "")
                title = str(_field(item, "title") or "")
                if not url.lower().startswith(("http://", "https://")):
                    continue
                if is_directory(url, title):
                    skipped += 1
                    continue
                domain = registered_domain(url)
                if not domain or domain in candidates:
                    continue
                parts = urlsplit(url)
                candidates[domain] = {
                    "domain": domain, "website": f"{parts.scheme}://{parts.netloc}/",
                    "search_result_title": title[:160], "snippet": " ".join(str(_field(item, "snippet") or "").split())[:240],
                    "found_by_search": query, "found_on": url,
                }
                if len(candidates) >= limit:
                    break
        prospects = list(candidates.values())[:limit]
        if check_websites and self.fetch_text is not None and prospects:
            with ThreadPoolExecutor(max_workers=6) as pool:
                pages = list(pool.map(self._homepage, prospects))
        else:
            pages = [None] * len(prospects)
        today = date.today().isoformat()
        for prospect, page in zip(prospects, pages):
            site_name = (_SITE_NAME.search(page or "") or [None, ""])[1] if page else ""
            page_title = (_TITLE.search(page or "") or [None, ""])[1] if page else ""
            prospect["name"] = _company_name([page_title, prospect["search_result_title"]], prospect["domain"], site_name)
            emails, phones = published_contacts(page or "", prospect["domain"]) if page else ([], [])
            prospect["published_email"] = emails[0] if emails else ""
            prospect["other_emails"] = emails[1:]
            prospect["published_phone"] = phones[0] if phones else ""
            prospect["checked_homepage"] = ("yes" if page else "could not open") if check_websites else "not checked"
            prospect["status"] = "not contacted"
            prospect["checked_on"] = today
        added, existing = self._save_leads(prospects, niche, place) if save_to_leads else (0, 0)
        csv_path = self._write_csv(prospects, niche, place) if prospects else None
        with_email = sum(1 for item in prospects if item["published_email"])
        result = {
            "niche": niche, "place": place, "prospects": prospects, "count": len(prospects),
            "with_published_email": with_email, "queries": queries_run, "directories_skipped": skipped,
            "csv": str(csv_path) if csv_path else "", "added_to_leads": added, "already_in_leads": existing,
            "searched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "note": ("Real businesses from a public web search just now. Contact details are only what each "
                     "business prints on its own homepage, so they are published, not verified. Nobody was "
                     "contacted."),
            "next_step": ("Pick the ones worth approaching; I can draft an honest first email to one of them, and "
                          "it is sent only after you confirm it."),
        }
        if not prospects:
            result["note"] = ("The search found no company websites for that, only directories and review sites. "
                              "Try a city instead of a whole state, or a more specific kind of business.")
        return result

    def _homepage(self, prospect: dict[str, Any]) -> str | None:
        try:
            return self.fetch_text(prospect["website"])
        except Exception:
            log_swallowed(_log, f"reading the homepage of {prospect['domain']}")
            return None

    def _save_leads(self, prospects: list[dict[str, Any]], niche: str, place: str) -> tuple[int, int]:
        if self.ledger is None:
            return 0, 0
        try:
            known = {registered_domain(str(row.get("url") or "")) for row in self.ledger.leads("all")["leads"]}
        except Exception:
            log_swallowed(_log, "reading existing leads before saving prospects")
            known = set()
        added = existing = 0
        for prospect in prospects:
            if prospect["domain"] in known:
                prospect["lead"] = "already tracked"
                existing += 1
                continue
            contact = "; ".join(part for part in (
                f"published email {prospect['published_email']}" if prospect["published_email"] else "",
                f"published phone {prospect['published_phone']}" if prospect["published_phone"] else "") if part)
            notes = (f"Prospect for {niche}{' in ' + place if place else ''}. "
                     + (contact + ". " if contact else "No email or phone on the homepage. ")
                     + f"Found by web search on {prospect['checked_on']}; not contacted.")
            try:
                record = self.ledger.add_lead(prospect["name"], f"web search: {prospect['found_by_search']}",
                                              prospect["website"], notes=notes)
            except Exception:
                log_swallowed(_log, f"saving prospect {prospect['domain']} as a lead", level=logging.WARNING)
                continue
            prospect["lead"] = f"lead {record['lead_id']}"
            known.add(prospect["domain"])
            added += 1
        return added, existing

    def _write_csv(self, prospects: list[dict[str, Any]], niche: str, place: str) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", f"{niche} {place}".lower()).strip("-")[:60] or "prospects"
        folder = self.output / "leads"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"prospects-{slug}-{date.today().isoformat()}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for prospect in prospects:
                writer.writerow(prospect)
        return path


def _field(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


# ---------------------------------------------------------------- routing
_FIND = re.compile(
    r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:now\s+)?"
    r"(?:find|search\s+for|look\s+for|get|build|make|create|compile|generate|research)\s+(?:me\s+)?"
    r"(?:(?:a|an|the|some|my)\s+)?(?:(?:lead|prospect|client|customer|target)s?\s+(?:list\s+)?(?:of|for|from)\s+)?"
    r"(?:(?P<count>\d{1,2})\s+)?(?P<niche>[a-z][a-z0-9&'/ -]{2,60}?)\s+(?:in|near|around|across|from)\s+"
    r"(?P<place>[a-z][a-z .,'-]{1,50}?)"
    r"(?:\s+(?:and|then|&)\s+(?:make|create|build|save|put)\s+(?:them\s+)?(?:(?:a|an|in|into|to|as)\s+)*"
    r"(?:(?:lead|prospect|client)s?\s*)?(?:list|leads|csv|spreadsheet))?"
    r"(?:\s+(?:as|for)\s+(?:leads|prospects|clients))?[\s?.!]*$"
)

_VAGUE_LEADS = re.compile(
    r"(?i)^(?:(?:ok|okay|now|so|then|please)\s+)*(?:find|create|make|build|get|generate)\s+(?:me\s+)?(?:some\s+|new\s+|a\s+)?"
    r"(?:leads?|prospects?)(?:\s+(?:and|&)\s+(?:make|create|build)\s+(?:a\s+)?(?:lead\s+)?list)?"
    r"|^(?:(?:ok|okay|now|so|please)\s+)*(?:make|create|build)\s+(?:me\s+)?(?:a\s+)?(?:lead|prospect)\s+list[\s?.!]*$"
)

#: Words that mean the "niche" is not a kind of business ("find files in D:").
_NOT_A_NICHE = re.compile(
    r"(?i)\b(?:files?|folders?|documents?|photos?|images?|videos?|emails?|messages?|errors?|bugs?|code|text|word|"
    r"words|cameras?|streams?|weather|news|jobs?|flights?|hotels?|restaurants?\s+to\s+eat|apartments?|"
    r"information|info|details|location|devices?|it|this|that)\b"
)
_BUSINESS_HINT = re.compile(
    r"(?i)\b(?:installers?|contractors?|companies|company|businesses|firms?|agencies|agency|shops?|stores?|"
    r"dentists?|clinics?|lawyers?|attorneys?|plumbers?|roofers?|electricians?|landscapers?|cleaners?|"
    r"restaurants?|cafes?|salons?|gyms?|realtors?|brokers?|accountants?|consultants?|studios?|"
    r"manufacturers?|suppliers?|distributors?|wholesalers?|startups?|practices?|services?|providers?|"
    r"leads?|prospects?|clients?|customers?)\b"
)


def prospect_request(text: str) -> dict[str, Any] | None:
    """{"niche", "place", "limit"} for a prospecting request, {"vague": True} when it names no target, or None."""
    value = " ".join(str(text or "").split()).strip()
    if _VAGUE_LEADS.match(value) and not _FIND.match(value):
        return {"vague": True}
    match = _FIND.match(value)
    if not match:
        return None
    niche = re.sub(r"(?i)^(?:local|real|new|potential|possible)\s+", "", match["niche"].strip())
    if _NOT_A_NICHE.search(niche) or not _BUSINESS_HINT.search(value):
        return None
    place = match["place"].strip(" .,")
    limit = int(match["count"]) if match["count"] else 15
    return {"niche": niche, "place": place, "limit": max(1, min(limit, 40))}


def direct_prospect_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    request = prospect_request(text)
    if not request or request.get("vague") or "find_prospects" not in set(names):
        return None
    return [{"description": (f"Search the web for {request['niche']} in {request['place']}, keep real company "
                             "websites, read the contact details each one publishes, and save them as leads."),
             "tool": "find_prospects", "arguments": request,
             "expected": {"type": "tool_success"}, "max_retries": 0}]


VAGUE_REPLY = ("Tell me which businesses and where, and I'll find real ones and save them as leads. For example: "
               "\"find solar installers in Austin, Texas and make a lead list\" or \"find 20 dentists in Detroit\". "
               "I only list companies a web search actually finds, with the contact details they publish themselves.")


def register_prospect_tools(registry: Any, research: Any, ledger: Any, output: Path) -> ProspectFinder:
    def fetch_text(url: str) -> str:
        resource = research.fetcher.fetch(url, 1_500_000, {"text/html", "application/xhtml+xml"}, timeout=8)
        return resource.content.decode("utf-8", errors="replace")

    finder = ProspectFinder(lambda query, limit: research.search.search(query, limit), fetch_text, ledger, output)
    registry.register(
        "find_prospects",
        "Find real businesses of a kind in a place to approach as clients: searches the public web, skips "
        "directories and review sites, reads each company's homepage for the email and phone it publishes, saves a "
        "CSV and adds them to the lead tracker. Invents nothing and contacts no one.",
        {"type": "object", "properties": {
            "niche": {"type": "string", "description": "The kind of business, e.g. 'solar installers'."},
            "place": {"type": "string", "description": "City, state or country."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 40},
            "check_websites": {"type": "boolean"},
            "save_to_leads": {"type": "boolean"}},
         "required": ["niche"], "additionalProperties": False},
        "low", "web_search", finder.find)
    return finder
