"""Find people to contact for a goal: real, public, professional and attributed.

"CTOs of fintech companies in the UAE", "founders of AI startups in Hyderabad", "top machine
learning researchers in India", "python developers in Bangalore", "recruiters at Stripe". The
contact book and contact discovery only worked once the owner already had a name; this finds
the names, from open sources that allow automated reading:

* **Wikidata** (CC0): the chief executives, founders and chairs of organisations in a field and
  place, with the organisation's website and, where the person published it, their LinkedIn ID.
  Ranked by how many Wikipedia editions cover the organisation, so leading organisations come first.
* **OpenAlex** (CC0): researchers on a topic in a country, ranked by citations, with ORCID iD and
  institution.
* **GitHub** (public API): developers in a place by language or keyword, ranked by followers,
  with company and website. Never the e-mail address a profile may carry.
* **The organisations' own team and leadership pages** (robots.txt honoured): names with their
  titles, for roles the others don't hold (CTO, VP, head of…).

Every person comes with where it was found and professional ways to reach them: the
organisation's website, a published profile, a LinkedIn search for the owner to open. It never
guesses an e-mail address, reads data brokers or private details, or contacts anyone; it is the
same public-work boundary as the rest of people research. Records describe a role at a time,
not verified identity: the source is linked so the owner can check.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus, urljoin, urlsplit

from app.identity import PROJECT_URL
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

# Wikimedia refuses clients that do not say who they are with a contact address (403, "Please respect our robot
# policy"). HAYAT's generic research agent had none, so every Wikidata read failed, through Tor or not.
WIKIMEDIA_HEADERS = {"User-Agent": f"HAYAT/1.0 ({PROJECT_URL}; local personal assistant)"}
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
OPENALEX = "https://api.openalex.org"
GITHUB = "https://api.github.com"
SOURCES = ("wikidata", "openalex", "github", "teams")
SOURCE_NAMES = {"wikidata": "Wikidata", "openalex": "OpenAlex", "github": "GitHub", "teams": "Organisation pages"}

_EXEC = re.compile(r"(?i)\b(?:ceo|chief|founder|co-?founder|chair(?:man|woman|person)?|president|owner|managing director|"
                   r"md|executive|cto|cfo|coo|cmo|cio|ciso|vp|vice president|head|director|partner|leader|leadership|"
                   r"board|investor|investors|entrepreneur|entrepreneurs)\b")
_ACADEMIC = re.compile(r"(?i)\b(?:research(?:er|ers)?|scientists?|professors?|academics?|phd|scholars?|lecturers?|faculty)\b")
_TECH = re.compile(r"(?i)\b(?:developers?|engineers?|programmers?|coders?|open[- ]source|maintainers?|devops|data scientists?|"
                   r"ml engineers?|architects?)\b")
_HIRING = re.compile(r"(?i)\b(?:recruiters?|recruiting|talent(?: acquisition)?|hiring(?: managers?)?|hr|human resources|"
                     r"people (?:team|partner))\b")
LANGUAGES = {"python", "java", "javascript", "typescript", "go", "golang", "rust", "c++", "c#", "kotlin", "swift", "ruby", "php",
             "scala", "r", "dart", "elixir", "haskell", "julia", "lua", "perl", "solidity", "c"}
# Titles on a team page. Used to pair a name with its title, never to invent one.
_TITLE = re.compile(r"(?i)\b(?:chief|officer|ceo|cto|cfo|coo|cmo|cio|ciso|cpo|president|founder|co-?founder|chair|director|"
                    r"head of|vp\b|vice president|partner|managing|general manager|lead\b|leader|principal|recruiter|"
                    r"talent|head\b|manager|board member|treasurer|secretary)\b")
_NAME = re.compile(r"^(?:dr\.?\s+|prof\.?\s+)?[A-ZÀ-Ý][a-zà-ÿ'’.-]+(?:\s+(?:[A-ZÀ-Ý][a-zà-ÿ'’.-]+|[A-Z]\.|van|von|de|da|del|al|bin|el)){1,3}$")
_NOT_NAME = re.compile(r"(?i)\b(?:about|team|our|leadership|board|contact|news|careers|privacy|cookie|read more|learn more|"
                       r"company|services|products|investors|press|home|menu|search|login|sign|solutions|blog|read|bio|view|"
                       r"profile|more|meet|see|watch|download|follow|share)\b")


def _words(value: Any) -> list[str]:
    plain = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").casefold()
    return [word for word in re.split(r"[^a-z0-9+#]+", plain) if word]


def _short(value: Any, limit: int = 160) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def _singular(text: str) -> str:
    """'CTOs' -> 'CTO', 'founders' -> 'founder', 'heads of marketing' -> 'head of marketing'; 'business' stays."""
    words = text.strip().split()
    if not words:
        return ""
    index = next((number for number, word in enumerate(words) if word.casefold() in {"of", "at", "for"}), len(words)) - 1
    word = words[index]
    if re.fullmatch(r"[A-Z]{2,4}s", word):
        words[index] = word[:-1]
    elif len(word) > 3 and word[-1:].lower() == "s" and not word.lower().endswith(("ss", "us", "is", "ics")):
        words[index] = word[:-1]
    return " ".join(words)


_LEAD = re.compile(r"(?i)^(?:(?:please\s+)?(?:find|search(?:\s+for)?|look\s+for|show(?:\s+me)?|list|get|give\s+me|who\s+are)\s+)?"
                   r"(?:(?:me|some)\s+)?(?:(?:people|persons|contacts|leads)\s+(?:to\s+(?:contact|network\s+with|reach(?:\s+out\s+to)?|"
                   r"approach|connect\s+with|get\s+in\s+touch\s+with)|i\s+(?:can|could|should)\s+(?:contact|reach|approach))\s*[:,-]?\s*)?"
                   r"(?:the\s+)?(?:(?:top(?:[- ]tier)?|leading|best|senior|influential|key|well[- ]known)\s+)*")
_PURPOSE = re.compile(r"(?i)\s+(?:to|for)\s+(?P<purpose>(?:a\s+)?job|jobs|work|business|clients?|partnerships?|networking|"
                      r"investment|funding|collaboration|mentoring|mentorship)\s*$")
# The last "in …" is the place: "investors in climate tech in Europe" is in Europe, not in "climate tech in Europe".
_PLACE = re.compile(r"(?i)\s+(?:in|based\s+in|near|around|across)\s+(?P<place>(?:the\s+)?[A-Za-z][A-Za-z.'-]*(?:\s+(?!in\s)[A-Za-z.'-]+){0,4})$")
_ORG = re.compile(r"\s+at\s+(?P<org>[A-Z0-9][\w&.'-]*(?:\s+[A-Z0-9][\w&.'-]*){0,3})$")
_KIND = re.compile(r"(?i)\s+(?:companies|company|startups?|start-ups?|firms?|organisations?|organizations?|businesses|industry|"
                   r"sector|space|field|labs?)$")
_FIELD = re.compile(r"(?i)\s+(?:of|in|for|from|working\s+(?:at|in|on)|hiring(?:\s+for)?|specialising\s+in|specializing\s+in|"
                    r"focused\s+on)\s+(?P<field>.+)$")


def parse_goal(text: str) -> dict[str, Any]:
    """'CTOs of fintech companies in UAE' -> role CTO, field fintech, place UAE. Unknown parts stay empty.

    Peeled from the end, one part at a time: the purpose ("for a job"), the place ("in UAE"), an organisation
    ("at Stripe"), then what kind of organisation ("fintech companies") from the role ("CTOs").
    """
    value = " ".join(str(text or "").split()).strip(" .!?")
    value = _LEAD.sub("", value, count=1)
    result: dict[str, Any] = {"role": "", "field": "", "place": "", "purpose": "", "organizations": []}
    if match := _PURPOSE.search(value):
        result["purpose"] = re.sub(r"(?i)^a\s+", "", match["purpose"]).lower()
        value = value[:match.start()]
    if match := _PLACE.search(value):
        result["place"] = re.sub(r"(?i)^the\s+", "", match["place"]).strip(" .'")
        value = value[:match.start()]
    if match := _ORG.search(value):
        result["organizations"] = [match["org"]]
        value = value[:match.start()]
    value = _KIND.sub("", value)
    role, field = value, ""
    if match := _FIELD.search(value):
        role, field = value[:match.start()], _KIND.sub("", match["field"])
        # "heads of marketing": "of marketing" belongs to the title, not the industry.
        if re.fullmatch(r"(?i)heads?|directors?|vps?|vice presidents?", role.strip()):
            role, field = f"{role} of {field}", ""
    else:
        # "machine learning researchers", "python developers": the field is in front of the role.
        words = value.split()
        for index in range(len(words) - 1, 0, -1):
            tail = " ".join(words[index:])
            if _EXEC.search(tail) or _ACADEMIC.search(tail) or _TECH.search(tail) or _HIRING.search(tail):
                field, role = " ".join(words[:index]), tail
                break
    result["role"], result["field"] = _singular(role.strip()), field.strip()
    # "recruiters hiring data analysts": the people who hire, for a job.
    if _HIRING.search(result["role"]) and not result["purpose"]:
        result["purpose"] = "job"
    return result


def choose_sources(role: str, field: str) -> list[str]:
    """Where a role is found: leaders in Wikidata, researchers in OpenAlex, developers on GitHub, the rest on team pages."""
    chosen = []
    text = f"{role} {field}"
    if _ACADEMIC.search(text):
        chosen.append("openalex")
    if _TECH.search(role) or (not role and field.casefold() in LANGUAGES):
        chosen.append("github")
    if _EXEC.search(role) or not chosen:
        chosen.append("wikidata")
    if _HIRING.search(role) or (_EXEC.search(role) and not re.search(r"(?i)\b(?:ceo|founder|co-?founder|chair)", role)):
        chosen.append("teams")
    return list(dict.fromkeys(chosen))


# ------------------------------------------------------------------ the finder
class PeopleFinder:
    """`get_json(url, headers)`, `fetch_html(url) -> (final_url, html)` and `search(query, limit)` are the network."""

    def __init__(self, get_json: Callable[..., Any], fetch_html: Callable[[str], tuple[str, str]] | None = None,
                 search: Callable[[str, int], list[Any]] | None = None, folder: Path | str | None = None,
                 check: Callable[[], Any] | None = None, employers: Callable[[str, str], list[str]] | None = None,
                 contacts: Callable[[str, str, str], dict[str, Any]] | None = None) -> None:
        self.get_json, self.fetch_html, self.search = get_json, fetch_html, search
        self.employers = employers  # the companies in the owner's saved job matches, for "recruiters hiring …"
        self.contacts = contacts  # contact discovery for one person (name, organisation, its website), for deep searches
        self.folder = Path(folder) if folder else None
        self.check = check or (lambda: None)

    # ---------------------------------------------------------------- entry point
    def find(self, goal: str = "", *, role: str = "", field: str = "", place: str = "", organizations: list[str] | str = "",
             purpose: str = "", sources: list[str] | tuple[str, ...] | None = None, limit: int = 30,
             seconds: float = 120, deep: bool = False, deep_people: int = 5) -> dict[str, Any]:
        parsed = parse_goal(goal) if goal else {}
        role = (role or parsed.get("role", "")).strip()
        field = (field or parsed.get("field", "")).strip()
        place = (place or parsed.get("place", "")).strip()
        purpose = (purpose or parsed.get("purpose", "")).strip()
        if not organizations:
            organizations = parsed.get("organizations") or []
        if isinstance(organizations, str):
            organizations = [part.strip() for part in re.split(r"[;,\n]", organizations) if part.strip()]
        organizations = [_short(name, 120) for name in organizations][:8]
        if not (role or field or organizations):
            raise ValueError("Say who you want to reach: a role (founder, CTO, researcher, recruiter…), a field or industry, "
                             "or organisations, e.g. \"CTOs of fintech companies in UAE\".")
        chosen = [name for name in (sources or choose_sources(role, field)) if name in SOURCES]
        hiring = bool(_HIRING.search(role))
        if hiring and not organizations and self.employers is not None:
            try:
                organizations = [_short(name, 120) for name in self.employers(field, place)][:6]
            except Exception as exc:
                log_swallowed(_log, f"reading employers from saved job matches ({type(exc).__name__})")
        if organizations and "teams" not in chosen:
            chosen.append("teams")
        if organizations and not (field or place) and sources is None:
            chosen = [name for name in chosen if name != "wikidata"]
        started = time.monotonic()
        report: dict[str, Any] = {
            "people_view": True, "goal": goal or " ".join(filter(None, (role, field, place))), "role": role, "field": field,
            "place": place, "purpose": purpose, "organizations": organizations, "sources": chosen, "people": [], "errors": [],
            "coverage": {}, "searched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "basis": "Public professional sources only (Wikidata, OpenAlex, GitHub, organisations' own pages). A name and role "
                     "describe what the source says, not verified identity: open the source to check. No e-mail is guessed, "
                     "no broker or private detail is read, and nobody is contacted."}
        place_info = self._place(place) if place and ({"wikidata", "openalex"} & set(chosen)) else {}
        # For recruiters the field is a job ("data analysts"), not an industry: Wikidata looks for organisations in the place.
        jobs = {"wikidata": lambda: self._wikidata(role, "" if hiring else field, place, place_info, limit),
                "openalex": lambda: self._openalex(role, field, place_info, limit),
                "github": lambda: self._github(role, field, place, limit),
                "teams": lambda: self._teams(role, field, place, organizations, place_info, limit, started, seconds)}
        found: dict[str, list[dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {name: pool.submit(jobs[name]) for name in chosen if name != "teams"}
            for name, future in futures.items():
                try:
                    found[name] = future.result(timeout=max(5, seconds - (time.monotonic() - started)))
                except Exception as exc:
                    found[name] = []
                    report["errors"].append({"source": SOURCE_NAMES[name], "error": _plain_error(exc)})
        if "teams" in chosen:
            try:
                # Team pages go after the others: Wikidata's organisations (and their websites) seed them.
                seeds = [{"organization": person["organization"], "website": person.get("organization_site", "")}
                         for person in found.get("wikidata", [])]
                found["teams"] = self._teams(role, field, place, organizations, place_info, limit, started, seconds, seeds)
            except Exception as exc:
                found["teams"] = []
                report["errors"].append({"source": SOURCE_NAMES["teams"], "error": _plain_error(exc)})
        for name in chosen:
            report["coverage"][SOURCE_NAMES[name]] = len(found.get(name, []))
        report["people"] = _merge(found, chosen, limit)
        for person in report["people"]:
            person["routes"].append(_linkedin_search(person, role))
        # Named organisations always get their routes, found people or not: recruiters and marketing heads are rarely
        # listed on a company's own site (Stripe, Zoho), but its careers page and a LinkedIn search for the role are.
        report["organisation_routes"] = [_organisation_routes(name, self._org_site(name) if "teams" in chosen else "", role)
                                         for name in organizations]
        if deep and self.contacts is not None:
            report["deep"] = self._reach(report["people"][:max(0, deep_people)])
        report["seconds"] = round(time.monotonic() - started, 1)
        if self.folder is not None:
            report["saved_to"] = str(self._save(report))
        return report

    # ---------------------------------------------------------------- deep: how to reach them
    def _reach(self, people: list[dict[str, Any]]) -> dict[str, Any]:
        """For the top people: published work e-mail and contact pages, from contact discovery (sources kept)."""
        def seed(person: dict[str, Any]) -> tuple[str, str]:
            """The organisation and the site to start from: a developer's own website stands in for a company."""
            site = person.get("organization_site", "")
            own = next((route["value"] for route in person["routes"] if route["label"] == "Their website"), "")
            organization = person.get("organization") or (urlsplit(own).hostname or "").removeprefix("www.")
            return organization, site or own

        wanted = [person for person in people if len(seed(person)[0]) >= 3]
        checked, reached, refused = 0, 0, 0

        def one(person: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
            try:
                self.check()
                organization, site = seed(person)
                return person, self.contacts(person["name"], organization, site), ""
            except Exception as exc:
                return person, None, _plain_error(exc)
        problems = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            for person, found, problem in pool.map(one, wanted):
                checked += 1
                if problem:
                    problems.append(f"{person['name']}: {problem}")
                    continue
                # Pages that answered with an HTTP refusal: through Tor, universities and publishers often block.
                refused += sum(1 for error in (found or {}).get("errors") or []
                               if error.get("stage") == "fetch" and "HTTPStatusError" in str(error.get("error", "")))
                known = {route["value"].casefold() for route in person["routes"]}
                fresh = []
                for route in _routes_from_discovery(person, found or {}):
                    if route["value"].casefold() not in known:
                        known.add(route["value"].casefold())
                        fresh.append(route)
                person["routes"] = fresh + person["routes"]  # published contact details first
                person["contact_checked"] = True
                if fresh:
                    reached += 1
        note = (f"Published contact details were looked for on {checked} people and found for {reached}. Only details the "
                "person or organisation published are shown; nothing was guessed.")
        if refused:
            note += (f" {refused} page{'s' if refused != 1 else ''} refused HAYAT's request (sites such as universities and "
                     "publishers often block Tor). With Tor off (say \"unmask\"), more of them answer; that is your choice.")
        return {"checked": checked, "with_contact": reached, "refused_pages": refused, "problems": problems[:5], "note": note}

    # ---------------------------------------------------------------- Wikidata
    def _wd_search(self, text: str) -> list[dict[str, Any]]:
        data = self.get_json(f"{WIKIDATA_API}?action=wbsearchentities&search={quote(text)}&language=en&uselang=en&format=json"
                             "&type=item&limit=7", WIKIMEDIA_HEADERS)
        return [item for item in (data.get("search") or []) if isinstance(item, dict)]

    def _sparql(self, query: str) -> list[dict[str, Any]]:
        self.check()
        data = self.get_json(f"{SPARQL}?format=json&query={quote(query)}", WIKIMEDIA_HEADERS, timeout=55)
        return ((data or {}).get("results") or {}).get("bindings") or []

    def _place(self, place: str) -> dict[str, str]:
        """The place as Wikidata knows it: its item, whether it is a country, and the country's two-letter code."""
        aliases = {"uae": "United Arab Emirates", "usa": "United States of America", "us": "United States of America",
                   "uk": "United Kingdom", "u.s.": "United States of America", "america": "United States of America",
                   "ksa": "Saudi Arabia", "europe": "Europe"}
        text = aliases.get(place.casefold().strip(" ."), place)
        for item in self._wd_search(text):
            description = str(item.get("description", "")).casefold()
            if any(word in description for word in ("article", "film", "album", "song", "family name", "given name", "band")):
                continue
            qid = str(item.get("id", ""))
            if not re.fullmatch(r"Q\d+", qid):
                continue
            rows = self._sparql(f"""SELECT ?country ?iso WHERE {{
  {{ wd:{qid} wdt:P297 ?iso. BIND(wd:{qid} AS ?country) }} UNION {{ wd:{qid} wdt:P17 ?country. ?country wdt:P297 ?iso. }} }} LIMIT 1""")
            iso = rows[0]["iso"]["value"] if rows else ""
            country = rows[0]["country"]["value"].rsplit("/", 1)[-1] if rows else ""
            return {"qid": qid, "label": item.get("label", text), "country": country, "iso": iso,
                    "is_country": country == qid, "continent": "continent" in description}
        return {}

    def _field_items(self, field: str) -> list[str]:
        """Industries the field names: 'fintech' -> Q16319025 (and the next close one)."""
        if not field:
            return []
        items = []
        found = self._wd_search(field)
        singular = _singular(field)
        if singular.casefold() != field.casefold():
            found = self._wd_search(singular) + found
        for item in found:
            description = str(item.get("description", "")).casefold()
            if any(word in description for word in ("article", "journal", "book", "film", "album", "song", "company", "firm",
                                                    "human", "person", "family name", "given name", "disambiguation")):
                continue
            if re.fullmatch(r"Q\d+", str(item.get("id", ""))) and item["id"] not in items:
                items.append(item["id"])
            if len(items) == 2:
                break
        return items

    def _wikidata(self, role: str, field: str, place: str, place_info: dict[str, str], limit: int) -> list[dict[str, Any]]:
        industries = self._field_items(field)
        if field and not industries:
            raise LookupError(f"Wikidata has no industry or field called \"{field}\"; try a broader word (fintech, banking, "
                              "artificial intelligence, healthcare).")
        roles = []
        wanted = role.casefold()
        if re.search(r"ceo|chief executive|president|managing director|\bmd\b|executive|leader|head|boss", wanted) or not wanted:
            roles.append("(p:P169 ps:P169 \"chief executive officer\")")
        if re.search(r"found|entrepreneur|owner", wanted) or not wanted or "leader" in wanted or "investor" in wanted:
            roles.append("(p:P112 ps:P112 \"founder\")")
        if re.search(r"chair|board|director|investor|partner", wanted) or not wanted:
            roles.append("(p:P488 ps:P488 \"chairperson\")")
        # Each role is read as a statement so a past holder can be left out: Wikidata keeps former chief executives
        # with an end date, and the first live run listed Stuart Gulliver (left HSBC in 2018) as HSBC's CEO.
        if not roles:  # "CTO", "VP": Wikidata rarely records them; its leaders seed the team pages
            roles = ["(p:P169 ps:P169 \"chief executive officer\")", "(p:P112 ps:P112 \"founder\")"]
        industry = (f"VALUES ?industry {{ {' '.join('wd:' + item for item in industries)} }} "
                    "{ ?org wdt:P452/wdt:P279* ?industry. } UNION { ?org wdt:P31/wdt:P279* ?industry. }" if industries else "")
        where = ""
        if place_info.get("is_country"):
            where = f"?org wdt:P17 wd:{place_info['qid']}."
        elif place_info.get("qid") and not place_info.get("continent"):
            where = f"?org wdt:P159/wdt:P131* wd:{place_info['qid']}."
        elif place and not place_info:
            raise LookupError(f"Wikidata does not know the place \"{place}\"; try the country or a larger city.")
        if not (industry or where):
            raise LookupError("Give a field or a place for Wikidata, e.g. \"founders of fintech companies in India\".")
        query = f"""SELECT DISTINCT ?person ?personLabel ?org ?orgLabel ?site ?li ?links ?role ?since WHERE {{
  VALUES (?statement ?value ?role) {{ {' '.join(roles)} }}
  {industry} {where}
  ?org ?statement ?held. ?held ?value ?person.
  FILTER NOT EXISTS {{ ?held pq:P582 ?ended }} FILTER NOT EXISTS {{ ?held wikibase:rank wikibase:DeprecatedRank }}
  ?person wdt:P31 wd:Q5. FILTER NOT EXISTS {{ ?person wdt:P570 ?died }}
  ?org wikibase:sitelinks ?links. OPTIONAL {{ ?held pq:P580 ?since }}
  OPTIONAL {{ ?org wdt:P856 ?site }} OPTIONAL {{ ?person wdt:P6634 ?li }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }} ORDER BY DESC(?links) LIMIT {min(200, limit * 4)}"""
        try:
            rows = self._sparql(query)
        except Exception as exc:
            if not re.search(r"(?i)time|timeout", f"{type(exc).__name__} {exc}") or "P279*" not in query:
                raise
            # Too slow with every sub-industry (a large country): the field itself only.
            rows = self._sparql(query.replace("?org wdt:P452/wdt:P279* ?industry.", "?org wdt:P452 ?industry.")
                                .replace("?org wdt:P31/wdt:P279* ?industry.", "?org wdt:P31 ?industry."))
        # Wikidata lags: a successor is often added without an end date on the predecessor (KfW listed both its chief
        # executives). Per organisation and role, only the holder who started last is kept, with the year shown.
        latest: dict[tuple[str, str], str] = {}
        for row in rows:
            key = (row.get("org", {}).get("value", ""), row.get("role", {}).get("value", ""))
            since = row.get("since", {}).get("value", "")[:10]
            if since > latest.get(key, ""):
                latest[key] = since
        people: dict[str, dict[str, Any]] = {}
        for row in rows:
            since = row.get("since", {}).get("value", "")[:10]
            key = (row.get("org", {}).get("value", ""), row.get("role", {}).get("value", ""))
            if latest.get(key) and since != latest[key] and key[1] != "founder":
                continue  # a later holder of the same post is recorded
            name, org = row.get("personLabel", {}).get("value", ""), row.get("orgLabel", {}).get("value", "")
            qid = row.get("person", {}).get("value", "").rsplit("/", 1)[-1]
            if not name or re.fullmatch(r"Q\d+", name) or not org or re.fullmatch(r"Q\d+", org):
                continue  # no English label: nothing readable to show
            role_name = row.get("role", {}).get("value", "")
            title = {"chief executive officer": "Chief executive", "founder": "Founder", "chairperson": "Chair"}.get(role_name, role_name)
            if since[:4].isdigit() and role_name != "founder":
                title += f" (since {since[:4]})"
            links = int(float(row.get("links", {}).get("value", 0) or 0))
            person = people.get(qid)
            if person is None:
                person = people[qid] = _person(
                    name, title, org, "Wikidata", f"https://www.wikidata.org/wiki/{qid}",
                    why=f"{title} of {org}" + (f" ({field}" + (f", {place_info.get('label', place)}" if place else "") + ")" if field else
                                               f" ({place_info.get('label', place)})" if place else "") + ", as Wikidata records it; it can lag, so check the organisation's own site.",
                    standing=f"{org} is covered in {links} Wikipedia edition{'s' if links != 1 else ''}", score=10 + min(links, 120) / 4)
                person["organization_site"] = ""
            elif title not in person["role"]:
                person["role"] += f" · {title}"
            site = row.get("site", {}).get("value", "")
            if site.startswith(("http://", "https://")) and not person["organization_site"]:
                person["organization_site"] = site
                person["routes"].append({"kind": "website", "label": f"{org} website", "value": site})
            linkedin = row.get("li", {}).get("value", "")
            if re.fullmatch(r"[A-Za-z0-9_%-]{3,100}", linkedin) and not any(r["label"].startswith("LinkedIn profile") for r in person["routes"]):
                person["routes"].append({"kind": "profile", "label": "LinkedIn profile (published on Wikidata)",
                                         "value": f"https://www.linkedin.com/in/{linkedin}"})
        return list(people.values())[:limit]

    # ---------------------------------------------------------------- OpenAlex
    def _openalex(self, role: str, field: str, place_info: dict[str, str], limit: int) -> list[dict[str, Any]]:
        topic_text = field or re.sub(_ACADEMIC, "", role).strip()
        if not topic_text:
            raise LookupError("Give OpenAlex a research topic, e.g. \"machine learning researchers in India\".")
        topics = self.get_json(f"{OPENALEX}/topics?search={quote(topic_text)}&per-page=3")
        ids = [str(topic.get("id", "")).rsplit("/", 1)[-1] for topic in (topics.get("results") or [])][:3]
        ids = [topic for topic in ids if re.fullmatch(r"T\d+", topic)]
        if not ids:
            raise LookupError(f"OpenAlex has no research topic matching \"{topic_text}\".")
        filters = "topics.id:" + "|".join(ids)
        if place_info.get("iso"):
            filters += f",last_known_institutions.country_code:{place_info['iso'].upper()}"
        data = self.get_json(f"{OPENALEX}/authors?filter={filters}&sort=cited_by_count:desc&per-page={min(50, limit)}")
        people = []
        for author in data.get("results") or []:
            name = _short(author.get("display_name", ""), 120)
            if not name:
                continue
            listed = author.get("last_known_institutions") or []
            wanted_country = place_info.get("iso", "").upper()
            listed = sorted(listed, key=lambda item: str(item.get("country_code", "")).upper() != wanted_country)
            institutions = [_short(item.get("display_name", ""), 120) for item in listed]
            cited, works = int(author.get("cited_by_count") or 0), int(author.get("works_count") or 0)
            source = str(author.get("id", "")).replace("https://openalex.org/", "https://openalex.org/authors/")
            person = _person(name, "Researcher", institutions[0] if institutions else "", "OpenAlex", source,
                             why=f"Publishes on {topic_text}" + (f" at {institutions[0]}" if institutions else "") + ".",
                             standing=f"{cited:,} citations · {works:,} works", score=8 + min(cited, 100_000) ** .5 / 20)
            orcid = str(author.get("orcid") or "")
            if orcid.startswith("https://orcid.org/"):
                person["routes"].append({"kind": "profile", "label": "ORCID record", "value": orcid})
            people.append(person)
        return people[:limit]

    # ---------------------------------------------------------------- GitHub
    def _github(self, role: str, field: str, place: str, limit: int) -> list[dict[str, Any]]:
        words = [word for word in re.split(r"[\s,/]+", f"{field} {role}".casefold()) if word]
        language = next((word for word in words if word in LANGUAGES), "")
        keyword = " ".join(word for word in _words(field) if word != language and word not in LANGUAGES)[:60]
        query = " ".join(filter(None, (keyword, f"language:{language}" if language else "",
                                       f'location:"{place}"' if place else "", "followers:>50", "type:user")))
        data = self.get_json(f"{GITHUB}/search/users?q={quote_plus(query)}&sort=followers&order=desc&per_page={min(20, limit)}")
        people = []
        for item in (data.get("items") or [])[:min(12, limit)]:
            login = str(item.get("login", ""))
            if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
                continue
            self.check()
            try:
                profile = self.get_json(f"{GITHUB}/users/{login}")
            except Exception:  # the search hit stays useful without its details
                profile = {}
            if profile.get("type") == "Organization":
                continue
            name = _short(profile.get("name") or login, 120)
            company = _short(str(profile.get("company") or "").lstrip("@"), 120)
            followers = int(profile.get("followers") or 0)
            bio = _short(profile.get("bio") or "", 140)
            person = _person(name, (f"{language.capitalize()} developer" if language else "Developer"), company, "GitHub",
                             f"https://github.com/{login}",
                             why=(f"\"{bio}\" " if bio else "") + "Builds in " + (language or keyword or "open source")
                             + (f" in {place}" if place else "") + " on GitHub.",
                             standing=f"{followers:,} followers · {int(profile.get('public_repos') or 0)} public repositories",
                             score=6 + min(followers, 50_000) ** .5 / 12)
            person["routes"].append({"kind": "profile", "label": "GitHub profile", "value": f"https://github.com/{login}"})
            blog = str(profile.get("blog") or "").strip()
            if blog and not re.search(r"@|mailto:", blog):
                from app.research.public_contacts import _SOCIAL

                blog = blog if blog.startswith(("http://", "https://")) else "https://" + blog
                host = (urlsplit(blog).hostname or "").removeprefix("www.")
                if not any(host == domain or host.endswith("." + domain) for domain in _SOCIAL):  # personal social pages stay out
                    person["routes"].append({"kind": "website", "label": "Their website", "value": blog})
            people.append(person)
        return people

    # ---------------------------------------------------------------- organisations' own pages
    def _teams(self, role: str, field: str, place: str, organizations: list[str], place_info: dict[str, str], limit: int,
               started: float, seconds: float, seeds: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
        if self.fetch_html is None:
            return []
        targets = [{"organization": name, "website": self._org_site(name)} for name in organizations]
        seen = {target["organization"].casefold() for target in targets}
        for seed in seeds or []:
            if seed["organization"].casefold() not in seen and len(targets) < 6:
                seen.add(seed["organization"].casefold())
                targets.append(seed)
        people: list[dict[str, Any]] = []
        for target in targets:
            if time.monotonic() - started > seconds * .8:
                break
            self.check()
            for url in self._team_pages(target, role):
                try:
                    final, page = self.fetch_html(url)
                except Exception as exc:
                    log_swallowed(_log, f"reading a team page ({type(exc).__name__})")
                    continue
                for name, title in team_members(page):
                    if role and not _role_fits(role, title):
                        continue
                    person = _person(name, title, target["organization"], urlsplit(final).hostname or "organisation page", final,
                                     why=f"Listed as {title} on {target['organization']}'s own page.",
                                     standing="named on the organisation's own website", score=9)
                    own = target.get("website") or (f"{urlsplit(final).scheme}://{urlsplit(final).netloc}/"
                                                    if _own_site(target["organization"], final) else "")
                    if own:
                        person["routes"].append({"kind": "website", "label": f"{target['organization']} website", "value": own})
                        person["organization_site"] = own
                    else:
                        person["why"] = f"Listed as {title} at {target['organization']} on {urlsplit(final).hostname}, a "\
                                        "third-party page: check it on the organisation's own site."
                        person["standing"] = "named on a third-party page"
                        person["score"] = 7
                    people.append(person)
                if len(people) >= limit:
                    return people[:limit]
        return people

    def _org_site(self, name: str) -> str:
        """An organisation's official website as Wikidata records it (P856), or "" when unknown."""
        cache = self.__dict__.setdefault("_sites", {})
        if name.casefold() in cache:
            return cache[name.casefold()]
        cache[name.casefold()] = site = self._lookup_site(name)
        return site

    def _lookup_site(self, name: str) -> str:
        try:
            for item in self._wd_search(name)[:3]:
                description = str(item.get("description", "")).casefold()
                qid = str(item.get("id", ""))
                if not re.fullmatch(r"Q\d+", qid) or any(word in description for word in ("human", "article", "film", "album",
                                                                                              "family name", "given name")):
                    continue
                # The shortest address is the main site: Infosys also lists its Japanese one.
                rows = self._sparql(f"SELECT ?site WHERE {{ wd:{qid} wdt:P856 ?site. }} ORDER BY STRLEN(STR(?site)) LIMIT 1")
                site = rows[0]["site"]["value"] if rows else ""
                if site.startswith(("http://", "https://")):
                    return site
        except Exception as exc:
            log_swallowed(_log, f"looking up an organisation's website ({type(exc).__name__})")
        return ""

    def _team_pages(self, target: dict[str, str], role: str) -> list[str]:
        """The organisation's own team, leadership or careers pages, best first.

        Its homepage's own links come first ("Leadership", "Our team", "Careers"): guessed paths such as
        /about/leadership were all 404 on stripe.com, and a search engine through Tor returns different pages each
        time. Then same-site search hits, then the usual paths.
        """
        from app.research.public_contacts import _reference

        hiring = bool(_HIRING.search(role))
        # Whole phrases, matched on a link's words or the last part of its path: a bare "management" matched
        # "risk management" and "waste management" guides.
        wanted = re.compile(r"(?i)^(?:careers?|jobs|join us|work with us|talent)$|\b(?:careers|join our team)\b" if hiring else
                            r"(?i)^(?:leadership(?: team)?|leaders|management(?: team| profiles?)?|executive team|executives|"
                            r"(?:our |the |meet the )?team|our people|people|founders|board(?: of directors)?|about(?: us)?|aboutus|"
                            r"who we are|company)$")
        site = target.get("website", "")
        host = (urlsplit(site).hostname or "").removeprefix("www.") if site else ""
        linked: list[str] = []
        if site and self.fetch_html is not None:
            try:
                final, page = self.fetch_html(site)
                from bs4 import BeautifulSoup

                for anchor in BeautifulSoup(page, "html.parser").find_all("a", href=True)[:600]:
                    label = " ".join(anchor.get_text(" ", strip=True).split())[:60]
                    url = urljoin(final, anchor["href"]).split("#", 1)[0]
                    same = (urlsplit(url).hostname or "").removeprefix("www.") == host
                    last = re.sub(r"\.\w+$", "", urlsplit(url).path.strip("/").rsplit("/", 1)[-1]).replace("-", " ").replace("_", " ")
                    if same and (wanted.search(label) or wanted.search(last)):
                        # Leadership before a general "About" page.
                        linked.append((0 if re.search(r"(?i)leader|management|executive|team|people|careers", label + url) else 1, url))
            except Exception as exc:
                log_swallowed(_log, f"reading an organisation's homepage ({type(exc).__name__})")
        pages = [url for _rank, url in sorted(linked, key=lambda item: item[0])]
        if self.search is not None:
            topic = "careers recruiting team" if hiring else "leadership team management"
            query = (f"site:{host} {topic}" if host else f"\"{target['organization']}\" {topic}")
            try:
                for row in self.search(query, 5)[:5]:
                    url = str(getattr(row, "url", "") or (row.get("url") if isinstance(row, dict) else ""))
                    title = str(getattr(row, "title", "") or (row.get("title") if isinstance(row, dict) else "") or "")
                    last = re.sub(r"\.\w+$", "", urlsplit(url).path.strip("/").rsplit("/", 1)[-1]).replace("-", " ").replace("_", " ")
                    # A hit must be a team page by its name, not any article on the site that mentions "team".
                    if url and (not host or (urlsplit(url).hostname or "").removeprefix("www.") == host) and (
                            wanted.search(last) or any(wanted.search(part.strip()) for part in re.split(r"[|·–-]", title))):
                        pages.append(url)
            except Exception as exc:
                log_swallowed(_log, f"searching for an organisation's team page ({type(exc).__name__})")
        if site:
            base = f"{urlsplit(site).scheme}://{urlsplit(site).netloc}/"
            paths = ["careers", "jobs"] if hiring else ["about/leadership", "leadership", "about/team", "team", "about"]
            pages += [urljoin(base, path) for path in paths]
        safe = []
        for url in dict.fromkeys(pages):
            try:
                safe.append(_reference(url))
            except ValueError:  # brokers, social sites, credentials in the address: never read
                continue
        return safe[:4]

    # ---------------------------------------------------------------- keeping a run
    def _save(self, report: dict[str, Any]) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        slug = "-".join(_words(report["goal"]))[:60] or "people"
        path = self.folder / f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{slug}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Earlier searches, newest first: their goal, when, how many people and where they are kept."""
        if self.folder is None or not self.folder.exists():
            return []
        rows = []
        for path in sorted(self.folder.glob("*.json"), reverse=True)[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows.append({"goal": data.get("goal", ""), "searched_at": data.get("searched_at", ""),
                         "people": len(data.get("people") or []), "path": str(path)})
        return rows

    @staticmethod
    def load(path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ helpers
def _person(name: str, role: str, organization: str, source: str, source_url: str, *, why: str, standing: str,
            score: float) -> dict[str, Any]:
    key = "|".join(_words(name)) + "@" + "|".join(_words(organization))[:60]
    return {"id": "p-" + hashlib.sha256(key.encode()).hexdigest()[:12], "name": _short(name, 120), "role": _short(role, 160),
            "organization": _short(organization, 160), "source": source, "source_url": source_url, "why": _short(why, 300),
            "standing": _short(standing, 160), "score": round(score, 2), "routes": [], "also_in": []}


def _own_site(organization: str, url: str) -> bool:
    """Whether the page's host carries the organisation's name (stripe.com for Stripe), a hint it is its own site."""
    host = (urlsplit(url).hostname or "").lower().replace("-", "").replace(".", "")
    words = [word for word in _words(organization) if len(word) > 2 and word not in {"inc", "ltd", "llc", "the", "group", "corp"}]
    return bool(words) and all(word in host for word in words[:2])


def _role_fits(wanted: str, title: str) -> bool:
    wanted_words = {word for word in _words(wanted) if len(word) > 1 and word not in {"of", "the", "and", "top", "senior"}}
    title_words = set(_words(title))
    synonyms = {"cto": {"technology", "technical"}, "cfo": {"financial", "finance"}, "coo": {"operating", "operations"},
                "cmo": {"marketing"}, "ceo": {"executive"}, "vp": {"vice", "president"}, "recruiter": {"talent", "recruiting",
                "recruitment", "acquisition", "people"}, "hr": {"people", "human", "talent"}, "head": {"head", "director", "lead"},
                "founder": {"founder", "cofounder", "co"}, "leader": {"chief", "head", "director", "president", "founder"},
                "executive": {"chief", "officer", "president", "director", "vp", "chairman", "chair", "chairperson", "founder",
                              "cofounder", "md"}}
    levels = {"head", "director", "vp", "vice", "president", "chief", "lead", "leader", "manager", "executive", "officer",
              "senior", "principal", "partner"}
    domains = wanted_words - levels - set(synonyms)
    title_expanded = title_words | {key for key, values in synonyms.items() if values & title_words}
    if domains:  # the field of the role must be in the title: "head of marketing" is not any director
        domain_synonyms = {"marketing": {"cmo", "brand", "growth"}, "technology": {"cto", "engineering", "technical"},
                           "engineering": {"cto", "technology", "technical"}, "finance": {"cfo", "financial"},
                           "sales": {"revenue", "commercial", "business"}, "product": {"cpo"}, "people": {"hr", "talent"},
                           "operations": {"coo", "operating"}, "data": {"analytics", "cdo"}, "security": {"ciso"}}
        return any(word in title_expanded or domain_synonyms.get(word, set()) & title_words for word in domains)
    for word in wanted_words:
        if word in title_words or synonyms.get(word, set()) & title_words:
            return True
    return not wanted_words


def _title_like(text: str) -> bool:
    """A job title, not a sentence: "Chief Executive Officer & Managing Director", not "Rolling out our latest addition: …"."""
    return (3 <= len(text) <= 90 and len(text.split()) <= 10 and bool(_TITLE.search(text))
            and not re.search(r"[:!?]|https?://|\b(?:our|we|you|your)\b", text, re.IGNORECASE))


def team_members(page: str) -> list[tuple[str, str]]:
    """(name, title) pairs from a team or leadership page: a person-like name next to a title in the same card."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page, "html.parser")
    for node in soup.select("script,style,noscript,template,svg,nav,footer,form,header"):
        node.decompose()
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for node in soup.find_all(["h2", "h3", "h4", "h5", "h6", "strong", "b", "p", "span", "div", "a", "dt", "figcaption", "li"]):
        text = " ".join(node.get_text(" ", strip=True).split())
        if not (4 <= len(text) <= 45) or not _NAME.match(text) or _NOT_NAME.search(text) or _TITLE.search(text):
            continue
        if node.find(["h2", "h3", "h4", "p", "div", "li"]):  # a container, not the name itself
            continue
        title = ""
        for sibling in [*node.find_next_siblings(limit=3), *(node.parent.find_next_siblings(limit=2) if node.parent else [])]:
            candidate = " ".join(sibling.get_text(" ", strip=True).split())
            if _title_like(candidate) and not _NAME.match(candidate):
                title = candidate
                break
        if not title and node.parent is not None:
            rest = " ".join(node.parent.get_text(" ", strip=True).split()).replace(text, "", 1).strip(" ,|–-")
            if _title_like(rest):
                title = rest
        key = text.casefold()
        if title and key not in seen:
            seen.add(key)
            pairs.append((text, _short(title, 90)))
        if len(pairs) >= 40:
            break
    return pairs


def _merge(found: dict[str, list[dict[str, Any]]], order: list[str], limit: int) -> list[dict[str, Any]]:
    """One row per person: the same name at the same organisation from two sources is kept once, with both credited."""
    people: dict[str, dict[str, Any]] = {}
    for source in order:
        for person in found.get(source, []):
            key = person["id"]
            if key in people:
                kept = people[key]
                kept["also_in"].append({"source": person["source"], "url": person["source_url"]})
                kept["score"] += 2
                known = {route["value"] for route in kept["routes"]}
                kept["routes"] += [route for route in person["routes"] if route["value"] not in known]
                continue
            people[key] = person
    ranked = sorted(people.values(), key=lambda person: -person["score"])
    # Interleave sources so one large source does not push the others off the list.
    by_source: dict[str, list[dict[str, Any]]] = {}
    for person in ranked:
        by_source.setdefault(person["source"] if person["source"] in SOURCE_NAMES.values() else "pages", []).append(person)
    mixed = []
    while any(by_source.values()) and len(mixed) < limit:
        for queue in by_source.values():
            if queue and len(mixed) < limit:
                mixed.append(queue.pop(0))
    return mixed


def _organisation_routes(name: str, site: str, role: str) -> dict[str, Any]:
    routes = []
    if site:
        base = f"{urlsplit(site).scheme}://{urlsplit(site).netloc}/"
        routes.append({"kind": "website", "label": "Website (from Wikidata)", "value": site})
        if _HIRING.search(role):
            routes.append({"kind": "website", "label": "Careers page (if it has one)", "value": urljoin(base, "careers")})
    words = f"{role} {name}".strip()
    routes.append({"kind": "search", "label": f"Search LinkedIn for {role or 'people'} at {name} (opens in your browser; "
                                              "HAYAT does not read LinkedIn)",
                   "value": "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(words)})
    return {"organization": name, "routes": routes}


def _routes_from_discovery(person: dict[str, Any], report: dict[str, Any]) -> list[dict[str, str]]:
    """Contact discovery's published channels as routes: the person's own first, then their organisation's teams."""
    routes = []
    wanted = set(_words(person["name"]))
    for candidate in report.get("candidates") or []:
        if not wanted <= set(_words(candidate.get("name", ""))):
            continue  # another person on the same page
        for channel in candidate.get("channels") or []:
            if channel.get("kind") in {"email", "website"} and channel.get("value"):
                host = urlsplit(channel.get("source_url", "")).hostname or "a public page"
                label = ("Work e-mail" if channel["kind"] == "email" else "Contact page") + f" (published on {host})"
                routes.append({"kind": channel["kind"], "label": label, "value": channel["value"],
                               "source": channel.get("source_url", "")})
    for team in report.get("team_routes") or []:
        if team.get("kind") in {"email", "website"} and team.get("value"):
            host = urlsplit(team.get("source_url", "")).hostname or "a public page"
            routes.append({"kind": team["kind"], "label": f"{team.get('team_name') or 'Team contact'} (published on {host})",
                           "value": team["value"], "source": team.get("source_url", "")})
    return routes[:6]


def _linkedin_search(person: dict[str, Any], role: str) -> dict[str, str]:
    words = f"{person['name']} {person['organization']}".strip()
    return {"kind": "search", "label": "Search LinkedIn for them (opens in your browser; HAYAT does not read LinkedIn)",
            "value": "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(words)}


def _plain_error(exc: Exception) -> str:
    text = str(exc)
    if re.search(r"(?i)\b403\b|forbidden|rate limit", text):
        return "the source refused or rate-limited this request; try again in a few minutes"
    if re.search(r"(?i)timed? ?out|timeout", text):
        return "the source took too long to answer"
    if re.search(r"(?i)robots", text):
        return "the site's robots.txt does not allow reading that page"
    if isinstance(exc, LookupError):
        return text
    return _short(text, 200) or type(exc).__name__


# ------------------------------------------------------------------ chat
_REQUEST = re.compile(
    r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could)\s+you\s+)?(?:find|search\s+for|look\s+for|show\s+me|list|"
    r"who\s+are\s+(?:the\s+)?)(?:\s+me)?\s+(?:(?:people|persons|contacts|leads)\s+(?:to|i\s+can|i\s+could|i\s+should)\s+"
    r"(?:contact|reach(?:\s+out\s+to)?|network\s+with|approach|connect\s+with|get\s+in\s+touch\s+with)\s*[:,-]?\s*(?P<what>.+)|"
    r"(?P<direct>(?:the\s+)?(?:top(?:[- ]tier)?\s+|leading\s+|senior\s+|influential\s+|key\s+)?(?:[\w&+./-]+\s+){0,4}?"
    r"(?:ceos?|ctos?|cfos?|coos?|founders?|co-?founders?|chairs?|executives?|leaders?|directors?|vps?|heads\s+of\s+\w+|"
    r"researchers?|scientists?|professors?|developers?|engineers?|recruiters?|hiring\s+managers?|investors?|partners?)\b.+))\s*[.!?]*$")


def request(text: str) -> dict[str, Any] | None:
    """'find people to contact: CTOs of fintech companies in UAE' -> arguments for people_find."""
    value = " ".join(str(text or "").split())
    if not value or len(value) > 300:
        return None
    match = _REQUEST.match(value)
    if not match:
        return None
    goal = (match["what"] or match["direct"] or "").strip()
    # "find contacts for Ada Example at Example Institute" names a person: that is contact discovery.
    if re.match(r"(?i)^(?:for\s+)?[A-Z][a-z]+\s+[A-Z][a-z]+\s+at\s+", goal):
        return None
    deep = bool(re.search(r"(?i)\b(?:in\s+depth|deep(?:ly)?|with\s+(?:their\s+)?(?:contact|e-?mail)s?(?:\s+details)?|"
                          r"and\s+how\s+to\s+(?:reach|contact)\s+them)\b", goal))
    goal = re.sub(r"(?i)\s*(?:,\s*)?(?:in\s+depth|deep(?:ly)?|with\s+(?:their\s+)?(?:contact|e-?mail)s?(?:\s+details)?|"
                  r"and\s+how\s+to\s+(?:reach|contact)\s+them)\s*$", "", goal).strip()
    goal = re.sub(r"(?i)^deep(?:ly)?\s+", "", goal)
    parsed = parse_goal(goal)
    if not (parsed["role"] or parsed["field"]):
        return None
    return {"goal": goal, "deep": True} if deep else {"goal": goal}


def plan(text: str, names: Any) -> dict[str, Any] | None:
    arguments = request(text)
    if arguments is None or "people_find" not in names:
        return None
    return {"description": "Find real people to contact for the goal, from public professional sources.", "tool": "people_find",
            "arguments": arguments, "expected": {"type": "tool_success"}, "max_retries": 0}


def report(history: list[dict[str, Any]]) -> str | None:
    if not history or any(item.get("tool") != "people_find" for item in history):
        return None
    result = history[-1].get("result") or {}
    if not result.get("success"):
        return "Finding people did not work: " + str(result.get("error") or "no result came back.")
    value = result.get("output") or {}
    return format_report(value)


def format_report(value: dict[str, Any]) -> str:
    people = value.get("people") or []
    sources = ", ".join(f"{name} {count}" for name, count in (value.get("coverage") or {}).items())
    lines = [f"People to contact for \"{value.get('goal', '')}\": {len(people)} found ({sources})."]
    for index, person in enumerate(people[:15], 1):
        head = " · ".join(filter(None, (person["name"], person["role"], person["organization"])))
        lines.append(f"{index}. {head}")
        standing = person["standing"][:1].upper() + person["standing"][1:]
        lines.append(f"   {person['why']} {standing}.".replace("..", "."))
        routes = [route for route in person["routes"] if route["kind"] != "search"][:2]
        for route in routes:
            lines.append(f"   {route['label']}: {route['value']}")
        lines.append(f"   Source: {person['source_url']}")
    if len(people) > 15:
        lines.append(f"…and {len(people) - 15} more in Private networking → Find people to contact.")
    if value.get("deep"):
        lines.append(value["deep"]["note"])
    for organisation in value.get("organisation_routes") or []:
        lines.append(f"{organisation['organization']}: ways in")
        lines += [f"   {route['label']}: {route['value']}" for route in organisation["routes"]]
    for error in value.get("errors") or []:
        lines.append(f"{error['source']}: {error['error']}.")
    if not people:
        lines.append("Nobody matched. Try a broader field (\"fintech\" rather than a product), a country instead of a city, or "
                     "name organisations to read their own team pages.")
    if value.get("saved_to"):
        lines.append(f"Kept for you in {value['saved_to']}; reopen it any time from Private networking → Find people to contact.")
    lines.append(value.get("basis", ""))
    return "\n".join(line for line in lines if line)


# ------------------------------------------------------------------ the tool
def register_people_tools(registry: Any, research: Any, folder: Path | str, jobs: Any = None) -> Callable[..., PeopleFinder]:
    """`people_find`: the finder on HAYAT's own fetcher (robots.txt, the privacy route) and search indexes."""
    fetcher = research.fetcher

    def get_json(url: str, headers: dict[str, str] | None = None, timeout: float | None = None) -> Any:
        accept = {"application/json", "application/sparql-results+json", "application/*+json"}
        return json.loads(fetcher.fetch(url, 4 * 1024 * 1024, accept, headers, timeout).content)

    def fetch_html(url: str) -> tuple[str, str]:
        resource = fetcher.fetch(url, 3 * 1024 * 1024, {"text/html", "application/xhtml+xml"})
        return resource.final_url, resource.content.decode("utf-8", "replace")

    def search(query: str, limit: int) -> list[Any]:
        return list(research.search.search(query, limit))

    def employers(field: str, place: str) -> list[str]:
        """Companies in the owner's saved job matches for this kind of work (and place), best matches first."""
        if jobs is None:
            return []
        from app.jobs.sources import matches_location

        names: list[str] = []
        words = [word for word in _words(field) if len(word) > 2]
        for row in jobs.store.iter_matches(min_score=0):
            title = str(row.get("title", "")).casefold()
            company = str(row.get("company", "")).strip()
            if not company or company in names or (words and not any(word in title for word in words)):
                continue
            if place and not matches_location(row, place, include_remote=False):
                continue
            names.append(company)
            if len(names) == 6:
                break
        return names

    def contacts_for(check: Callable[[], Any] | None) -> Callable[[str, str, str], dict[str, Any]]:
        def contacts(name: str, organization: str, website: str) -> dict[str, Any]:
            from app.research.public_contacts import discover_contacts

            # The first organisation of a list ("Microsoft Translator, AI4Bharat, IIT Madras") searches far better.
            first = re.split(r"[,;/|]", organization)[0].strip() or organization
            return discover_contacts(research, name, first, website, max_pages=5, check=check, relevant_only=True)
        return contacts

    def make(check: Callable[[], Any] | None = None) -> PeopleFinder:
        return PeopleFinder(get_json, fetch_html, search, folder=folder, check=check, employers=employers,
                            contacts=contacts_for(check))

    def people_find(goal: str = "", role: str = "", field: str = "", place: str = "", organizations: str = "",
                    sources: list[str] | None = None, limit: int = 30, deep: bool = False) -> dict[str, Any]:
        interlock = getattr(registry, "interlock", None)
        ticket = interlock.ticket() if interlock else None

        def check() -> None:
            if interlock:
                interlock.check(ticket)
            if getattr(getattr(registry, "authority", None), "privacy_mode", False):
                raise PermissionError("Privacy mode blocks searching for people.")
        check()
        return make(check).find(goal, role=role, field=field, place=place, organizations=organizations,
                                sources=sources or None, limit=max(1, min(int(limit), 60)), deep=bool(deep))

    obj = {"type": "object", "additionalProperties": False, "required": [], "properties": {
        "goal": {"type": "string", "maxLength": 300}, "role": {"type": "string", "maxLength": 80},
        "field": {"type": "string", "maxLength": 120}, "place": {"type": "string", "maxLength": 80},
        "organizations": {"type": "string", "maxLength": 600},
        "sources": {"type": "array", "items": {"enum": list(SOURCES)}, "maxItems": 4},
        "limit": {"type": "integer", "minimum": 1, "maximum": 60}, "deep": {"type": "boolean"}}}
    registry.register("people_find", "Find real people to contact for a goal (founders, executives, researchers, developers, "
                      "recruiters) from public professional sources: Wikidata leaders of organisations by field and place, "
                      "OpenAlex researchers by topic, GitHub developers by place and language, organisations' own team pages. "
                      "Returns each person with their source and professional routes (organisation website, published profile, a "
                      "LinkedIn search link). deep=true also looks for the top people's published work e-mail and contact "
                      "pages. No guessed e-mail, broker or private detail; nobody is contacted.",
                      obj, "low", "public_web_read", people_find)
    return make
