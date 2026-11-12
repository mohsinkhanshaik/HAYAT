"""Prior art, novelty, cross-domain ideas, feasibility and risks for an invention idea (2026-11-09).

The Invention Center could already propose and test formulas against frozen cases and read the most relevant
papers (app/invention_literature.py). This adds the questions an inventor asks before building:

* **Has it been done?** Patents found through HAYAT's search chain on Google Patents (only the public
  /patent/ pages its robots.txt allows; its own search is not used) and papers from OpenAlex, each read for
  its title, date, owner and abstract.
* **What is new about it?** The idea is split into its features (its distinctive words and pairs of words);
  for each, HAYAT shows which prior items already contain it. Features found nowhere are the candidate
  novelty; the closest items are listed with how much of the idea they share (word-weighted overlap).
* **How do other fields do it?** The papers are grouped by OpenAlex's research field, so an inventor sees
  where else the same function is being solved (materials science, biology, civil engineering…).
* **How feasible, and what are the risks?** An evidence-based reading, labelled as a heuristic: researched
  only, patented, or crowded; how recent the work is; a crowded field means freedom-to-operate questions
  (not legal advice); regulated areas are named.

Everything shown links to its source. Prior-art search is never complete: absence of a match is not proof
of novelty, and the report says so.
"""
from __future__ import annotations

import logging
import math
import re
import statistics
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from app.logging import log_swallowed

_log = logging.getLogger(__name__)
OPENALEX = "https://api.openalex.org/works"
_STOP = frozenset("""a an and the of for to in on with without by from into onto over under using use used uses that this
these those which who whom what when where how why is are was were be been being it its can could would should will
may might must not no or as at via per than then so such very more most less least new novel device system method
apparatus means wherein thereof therein comprising comprises include includes including make makes made way ways based
like also only just about between among within each other some any all one two three first second""".split())
#: Words that put an invention in a regulated area; each needs its own approval or standard before it is sold.
REGULATED = {"medical": "medical devices", "implant": "medical devices", "drug": "medicines", "diagnos": "medical devices",
             "drone": "aviation rules", "aircraft": "aviation rules", "battery": "battery safety and transport",
             "lithium": "battery safety and transport", "pressure": "pressure-equipment safety", "radio": "radio licensing",
             "wireless": "radio licensing", "food": "food safety", "child": "child-product safety", "vehicle": "vehicle safety",
             "car": "vehicle safety", "weapon": "weapons law", "laser": "laser safety", "x-ray": "radiation safety",
             "nuclear": "radiation safety", "gas": "gas-appliance safety", "electric": "electrical safety"}


def words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z][a-z0-9-]{2,}", str(text).lower()) if word not in _STOP]


def stem(word: str) -> str:
    """A crude stem: plurals folded ("pots" = "pot", "batteries" = "battery"), then the first six letters."""
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word[:6]


def features(idea: str, limit: int = 8) -> list[str]:
    """The idea's distinctive parts: its content words, and adjacent pairs that read as one thing ("heat pipe")."""
    tokens = words(idea)
    # Pairs only of words that stand next to each other in the idea ("phone from braking" gives no "phone braking").
    raw = re.findall(r"[a-z][a-z0-9-]{2,}|\S", str(idea).lower())
    pairs = [f"{a} {b}" for a, b in zip(raw, raw[1:]) if a in tokens and b in tokens]
    chosen: list[str] = []
    for pair in pairs:
        if len(chosen) < limit // 2 and pair not in chosen:
            chosen.append(pair)
    for token in sorted(set(tokens), key=lambda word: (-len(word), word)):
        if len(chosen) >= limit:
            break
        if not any(token in item for item in chosen):
            chosen.append(token)
    return chosen


def contains(text: str, feature: str) -> bool:
    stems = {stem(word) for word in words(text)}
    return all(stem(part) in stems for part in feature.split())


def overlap(idea: str, text: str, weights: dict[str, float]) -> float:
    """Share of the idea's word weight that the text also contains (0..1)."""
    mine = {stem(word) for word in words(idea)}
    theirs = {stem(word) for word in words(text)}
    total = sum(weights.get(word, 1.0) for word in mine) or 1.0
    return sum(weights.get(word, 1.0) for word in mine & theirs) / total


# ------------------------------------------------------------------------------------------- sources

def patent_urls(search: Any, idea: str, limit: int = 8) -> list[str]:
    """Patent pages found by the search chain (Google Patents /patent/ pages only)."""
    if search is None:
        return []
    query = " ".join(words(idea)[:10])
    try:
        results = search.search(query, limit=20, domains=["patents.google.com"])
    except Exception:
        log_swallowed(_log, "searching for patents")
        return []
    urls = []
    for result in results:
        url = str(getattr(result, "url", "") or "")
        parts = urlsplit(url)
        if parts.hostname == "patents.google.com" and parts.path.startswith("/patent/"):
            clean = f"https://patents.google.com{'/'.join(parts.path.split('/')[:3])}/en"
            if clean not in urls:
                urls.append(clean)
    return urls[:limit]


def read_patent(fetch_html: Callable[[str], str], url: str) -> dict[str, Any] | None:
    """Title, number, dates, owner, inventors, abstract and cited patents from a Google Patents page."""
    from bs4 import BeautifulSoup

    try:
        page = BeautifulSoup(fetch_html(url), "html.parser")
    except Exception:
        log_swallowed(_log, f"reading patent {url}")
        return None
    meta = lambda name, scheme=None: [tag.get("content", "").strip() for tag in page.find_all("meta", attrs={"name": name})
                                      if scheme is None or tag.get("scheme") == scheme]
    abstract = page.find("div", class_="abstract")
    title = " ".join((page.title.get_text(" ", strip=True) if page.title else "").split()).replace(" - Google Patents", "")
    # The granted number when there is one ("US:4126995" -> "US 4126995"), else the publication id from the address
    # (an application number such as "PC:T/IN2017/050465" is not how anyone looks a patent up).
    granted = meta("citation_patent_number")
    number = granted[0].replace(":", " ", 1) if granted else urlsplit(url).path.split("/")[2]
    return {"kind": "patent", "url": url, "number": number, "title": title[:240],
            "filed": (meta("DC.date", "dateSubmitted") or [""])[0], "granted": (meta("DC.date", "issue") or [""])[0],
            "owner": ", ".join(meta("DC.contributor", "assignee")[:2]), "inventors": meta("DC.contributor", "inventor")[:4],
            "abstract": " ".join((abstract.get_text(" ", strip=True) if abstract else (meta("description") or [""])[0]).split())[:1500],
            "cites": len(meta("DC.relation", "references"))}


def papers_with_fields(get_json: Callable[[str], Any], idea: str, limit: int = 20) -> list[dict[str, Any]]:
    """OpenAlex works on the idea, with each one's research field (for cross-domain views)."""
    from app.invention_literature import abstract_text

    query = " ".join(words(idea)[:10])
    if not query:
        return []
    url = (f"{OPENALEX}?search={quote(query)}&per-page={max(1, min(limit, 25))}"
           "&select=id,doi,display_name,publication_year,cited_by_count,abstract_inverted_index,primary_topic")
    data = get_json(url)  # failures go to the caller, which says papers were unavailable rather than "none found"
    out = []
    for work in data.get("results", []) if isinstance(data, dict) else []:
        topic = work.get("primary_topic") or {}
        out.append({"kind": "paper", "url": work.get("doi") or work.get("id"), "title": str(work.get("display_name") or "")[:240],
                    "year": work.get("publication_year"), "citations": int(work.get("cited_by_count") or 0),
                    "abstract": abstract_text(work.get("abstract_inverted_index"))[:1500],
                    "field": ((topic.get("field") or {}).get("display_name") or "Unclassified"),
                    "domain": ((topic.get("domain") or {}).get("display_name") or ""),
                    "topic": topic.get("display_name") or ""})
    return out


# ------------------------------------------------------------------------------------------- analysis

def analyse(idea: str, patents: list[dict[str, Any]], papers: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """Novelty by feature, closest prior art, other fields, feasibility reading and risks, from what was found."""
    now = now or datetime.now(UTC)
    items = [item for item in [*patents, *papers] if item]
    documents = [f"{item.get('title', '')} {item.get('abstract', '')}" for item in items]
    # Rarer words weigh more: a shared "heat" says less than a shared "thermosiphon".
    frequency = Counter(stem(word) for text in documents for word in set(words(text)))
    weights = {word: math.log((1 + len(documents)) / (1 + count)) + 1 for word, count in frequency.items()}
    parts = features(idea)
    coverage = []
    for feature in parts:
        holders = [index for index, text in enumerate(documents) if contains(text, feature)]
        coverage.append({"feature": feature, "found_in": len(holders),
                         "examples": [items[index]["title"][:120] for index in holders[:3]]})
    ranked = sorted(((overlap(idea, text, weights), index) for index, text in enumerate(documents)), reverse=True)
    closest = [{**{key: items[index].get(key) for key in ("kind", "title", "url", "number", "year", "filed", "owner")},
                "shares_pct": round(score * 100)} for score, index in ranked[:5] if score > 0]
    new_parts = [entry["feature"] for entry in coverage if entry["found_in"] == 0]
    top = ranked[0][0] if ranked else 0.0
    novelty = ("no close prior art found in what was searched" if top < .35 else
               "partly anticipated: the closest work shares much of it" if top < .65 else
               "largely anticipated: close prior art exists")
    fields = Counter(paper["field"] for paper in papers)
    cross = [{"field": field, "papers": count,
              "example": next((paper["title"] for paper in sorted(papers, key=lambda paper: -paper["citations"])
                               if paper["field"] == field), "")}
             for field, count in fields.most_common(6)]
    years = [int(str(item.get("filed") or "")[:4]) for item in patents if str(item.get("filed") or "")[:4].isdigit()]
    years += [int(paper["year"]) for paper in papers if paper.get("year")]
    recent = sum(year >= now.year - 10 for year in years)
    maturity = ("crowded: many patents already cover this area" if len(patents) >= 6 else
                "patented: others have protected versions of this" if patents else
                "researched: studied in papers, no patent found in this search" if papers else
                "unexplored in what was searched (or the search found nothing)")
    risks = []
    if len(patents) >= 3 and top >= .5:
        risks.append("Close patents exist: before building to sell, check freedom to operate with a patent professional "
                     "(this is not legal advice).")
    if not items:
        risks.append("Nothing comparable was found, so there is no evidence it works: plan a small test first.")
    lowered = idea.lower()
    regulated = sorted({area for word, area in REGULATED.items() if re.search(r"\b" + re.escape(word), lowered)})
    if regulated:
        risks.append("Regulated area(s): " + ", ".join(regulated) + " — approvals or standards apply before selling.")
    if years and statistics.median(years) < now.year - 15:
        risks.append("Most related work is old: either the problem was solved long ago, or it stalled for a reason worth finding.")
    plan = ["Write the one-sentence claim: what it does that the closest item above does not."]
    if new_parts:
        plan.append("Prototype the part that looks new first: " + ", ".join(new_parts[:3]) + ".")
    if cross:
        plan.append(f"Read how {cross[0]['field']} solves it (e.g. “{cross[0]['example'][:90]}”) before designing.")
    plan.append("Measure one number that would prove it works, and test it on the smallest cheap version.")
    return {"idea": idea, "features": coverage, "new_features": new_parts, "closest": closest,
            "closest_share_pct": round(top * 100), "novelty": novelty, "cross_domain": cross,
            "maturity": maturity, "recent_works": recent, "works_found": {"patents": len(patents), "papers": len(papers)},
            "risks": risks, "plan": plan,
            "basis": ("From the patents and papers this search found; prior-art search is never complete, so 'not found' "
                      "is not proof of novelty. Overlap is word-based, not a legal comparison of claims.")}


def report(idea: str, *, search: Any = None, fetch_html: Callable[[str], str] | None = None,
           get_json: Callable[[str], Any] | None = None, patent_limit: int = 6) -> dict[str, Any]:
    if not str(idea).strip() or len(words(idea)) < 2:
        raise ValueError("Describe the invention in a sentence or two: what it does and how.")
    patents = []
    if fetch_html is not None:
        for url in patent_urls(search, idea, patent_limit):
            item = read_patent(fetch_html, url)
            if item and item.get("title"):
                patents.append(item)
    papers, gaps = [], []
    if get_json is not None:
        try:
            papers = papers_with_fields(get_json, idea)
        except Exception as exc:
            log_swallowed(_log, "searching OpenAlex for prior art")
            limited = "429" in str(exc) or "Too Many" in str(exc)
            gaps.append("papers: OpenAlex refused for now (its free keyless budget is shared by everyone on this "
                        "network address and resets daily)" if limited else f"papers: OpenAlex failed ({type(exc).__name__})")
    if fetch_html is not None and not patents:
        gaps.append("patents: the search engines returned no Google Patents pages this time (they may be limiting requests)")
    result = analyse(idea, patents, papers)
    result["gaps"] = gaps
    result.update({"invention_prior_art": True, "patents": patents, "papers": papers[:12],
                   "sources": "Google Patents public pages (found via HAYAT's search chain) and OpenAlex",
                   "text": text(result)})
    return result


def register_prior_art_tool(registry: Any, research: Any) -> None:
    fetcher = research.fetcher

    def fetch_html(url: str) -> str:
        resource = fetcher.fetch(url, 3 * 1024 * 1024, {"text/html", "application/xhtml+xml"})
        return bytes(resource.content).decode("utf-8", errors="replace")

    registry.register(
        "invention_prior_art",
        "Before building an invention: patents (Google Patents public pages found by search) and papers (OpenAlex) "
        "like it, which of its features already appear and which look new, the closest prior art and how much it "
        "shares, how other research fields solve the same thing, an evidence-based feasibility reading and the risks "
        "(crowded patents, regulated areas, no evidence it works). Never proof of novelty; not legal advice.",
        {"type": "object", "properties": {"idea": {"type": "string", "minLength": 8, "maxLength": 1500}},
         "required": ["idea"], "additionalProperties": False},
        "low", "public_web_read",
        lambda idea: report(idea, search=research.search, fetch_html=fetch_html, get_json=fetcher.get_json))


def text(result: dict[str, Any]) -> str:
    lines = [f"Prior art and feasibility: {result['idea'][:160]}",
             f"Found {result['works_found']['patents']} patents and {result['works_found']['papers']} papers. "
             f"Novelty: {result['novelty']} (closest shares {result['closest_share_pct']} % of the idea's wording)."]
    if result["closest"]:
        lines.append("Closest:")
        lines += [f"  · {item['kind']}: {item['title'][:110]}"
                  + (f" ({item.get('number') or item.get('year') or ''})" if item.get("number") or item.get("year") else "")
                  + f" — shares {item['shares_pct']} %  {item.get('url') or ''}" for item in result["closest"][:4]]
    if result["new_features"]:
        lines.append("Not found in anything searched (candidate novelty): " + ", ".join(result["new_features"]) + ".")
    if result["cross_domain"]:
        lines.append("Other fields working on it: " + "; ".join(f"{item['field']} ({item['papers']})" for item in result["cross_domain"]) + ".")
    lines.append(f"Feasibility reading: {result['maturity']}; {result['recent_works']} of the works are from the last ten years.")
    if result["risks"]:
        lines.append("Risks: " + " ".join(result["risks"]))
    lines.append("Next steps: " + " ".join(result["plan"]))
    if result.get("gaps"):
        lines.append("Not searched this time: " + "; ".join(result["gaps"]) + ". The verdict above rests on the rest.")
    lines.append(result["basis"])
    return "\n".join(lines)
