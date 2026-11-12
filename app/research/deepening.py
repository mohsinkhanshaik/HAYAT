"""A second look: after reading, notice what is still unknown and search for exactly that.

A research run used to be one pass. It planned its queries up front, read what they found, then answered with
whatever the pages covered. It already computed its knowledge gaps (too few independent sources, sources that
disagree, no dates), but only reported them; nothing went back to close them. A person researching does go back.
Reading a page shows which part of the question it did not answer, and which names and terms the sources use
that the question did not.

:func:`plan_second_look` turns what the first pass found into a short list of follow-up searches:

* **Uncovered parts of the question.** Each content word or name in the question is a facet. A facet that no
  usable claim mentions is still unanswered. It is searched together with the part already covered (the anchor),
  so "Widget maximum upload size" follows a first pass that only answered "Widget safe mode default".
* **Too few independent sources.** The subject is searched again with ``-site:`` for the domains already used,
  and results from those domains are skipped, so a second voice is not the first one again.
* **Disagreement.** When sources disagree, the next search looks for which account the evidence supports.
* **Terms the sources taught** (deep mode). A name or term that two or more usable claims use, but the question
  did not, is searched with the anchor. This is how a reader learns that an aurora question is really about the
  solar wind and the magnetosphere.

Follow-ups that repeat a query already searched are dropped. The manager runs them only with at least 30% of the
time budget left, fetches only pages it has not read, and puts every new page through the same poisoning check,
claim extraction, relevance filter and citation validation as the first pass. Nothing here writes an answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit

from app.research.text import STOPWORDS, jaccard

#: Words that shape a question but are not something to find out about.
_FRAME = STOPWORDS | {
    "how", "why", "who", "whom", "whose", "much", "many", "some", "any", "all", "can", "will", "has", "had", "are",
    "is", "be", "been", "for", "not", "but", "you", "our", "out", "now", "today", "please", "tell", "explain",
    "describe", "compare", "find", "look", "search", "show", "give", "list", "know", "want", "need", "deep", "deeply",
    "detail", "detailed", "information", "info", "answer", "question", "online", "internet", "web", "per", "each",
    "versus", "difference", "between", "work", "works", "mean", "means", "use", "used", "uses", "get", "make",
    "made", "like", "just", "also", "very", "really", "thing", "things", "way", "ways", "kind", "type", "types",
}

_NAME = re.compile(r"\b[A-Z][A-Za-z0-9.+-]*(?:\s+[A-Z][A-Za-z0-9.+-]*){0,3}\b")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")
#: A name in the question with the version or number that belongs to it: "Widget 2", "Python 3.13", "Mount Everest".
_SUBJECT_NAME = re.compile(r"\b[A-Z][A-Za-z0-9.+-]*(?:\s+(?:[A-Z][A-Za-z0-9.+-]*|\d[\d.]*\b)){0,3}")

#: How many follow-up searches each research depth may add.
FOLLOWUP_SEARCHES = {"NORMAL": 2, "PRIMARY_ONLY": 2, "DEEP": 4, "ADVERSARIAL": 4}
#: How many new pages each depth may read for them.
FOLLOWUP_PAGES = {"NORMAL": 3, "PRIMARY_ONLY": 3, "DEEP": 6, "ADVERSARIAL": 6}


@dataclass
class SecondLook:
    queries: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    focus: list[str] = field(default_factory=list)
    skip_domains: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    learned_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"queries": list(self.queries), "reasons": list(self.reasons), "covered": list(self.covered),
                "uncovered": list(self.uncovered), "learned_terms": list(self.learned_terms),
                "skip_domains": list(self.skip_domains)}


def _stem(word: str) -> str:
    word = word.lower()
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ied", "es", "ed", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def facets(question: str) -> list[str]:
    """The parts of a question that have to be answered: its content words and names, in order."""
    found: list[str] = []
    seen: set[str] = set()
    for word in _WORD.findall(question):
        lower = word.lower().strip(".+-")
        if len(lower) < 3 and not lower.isdigit():
            continue
        if lower in _FRAME or _stem(lower) in seen:
            continue
        seen.add(_stem(lower))
        found.append(word.strip(".") if word[:1].isupper() else lower)
    return found


def _mentions(text: str, facet: str) -> bool:
    stem = _stem(facet)
    return any(_stem(word).startswith(stem[: max(4, len(stem) - 1)]) or _stem(word) == stem
               for word in _WORD.findall(text))


def _usable(claims: Iterable[Any]) -> list[str]:
    texts = []
    for claim in claims:
        status = getattr(getattr(claim, "status", None), "value", getattr(claim, "status", ""))
        if status in {"SUPPORTED", "PARTIALLY_SUPPORTED", "DISPUTED"}:
            texts.append(str(getattr(claim, "text", "")))
    return texts


def coverage(question: str, claims: Iterable[Any]) -> tuple[list[str], list[str]]:
    """(covered, uncovered) facets of the question, judged against usable claims only."""
    texts = _usable(claims)
    covered, uncovered = [], []
    for facet in facets(question):
        (covered if any(_mentions(text, facet) for text in texts) else uncovered).append(facet)
    return covered, uncovered


def _learned_terms(question: str, claims: Iterable[Any], limit: int = 2) -> list[str]:
    asked = {_stem(word) for word in _WORD.findall(question)}
    counts: dict[str, int] = {}
    shown: dict[str, str] = {}
    for text in _usable(claims):
        for raw in dict.fromkeys(match.group(0).strip() for match in _NAME.finditer(text)):
            # "The Solar Wind" at the start of a sentence is the term "Solar Wind".
            parts = raw.split()
            while parts and parts[0].lower() in _FRAME:
                parts = parts[1:]
            name = " ".join(parts)
            words = [_stem(word) for word in parts]
            if len(name) < 3 or not words or all(word in asked for word in words):
                continue
            key = " ".join(words)
            counts[key] = counts.get(key, 0) + 1
            shown.setdefault(key, name)
    ranked = sorted((key for key, count in counts.items() if count >= 2), key=lambda key: (-counts[key], key))
    return [shown[key] for key in ranked[:limit]]


def _anchor(question: str, covered: list[str]) -> str:
    """What a follow-up search keeps from the answered part: the question's names first, then its first words.

    Live, 2026-11-08: "boiling point of water at the top of Mount Everest" followed up as "boiling point top",
    without Everest, and found a company called BP Group. Names carry the subject; ordinary words only narrow it.
    """
    names = []
    for match in _SUBJECT_NAME.finditer(question):
        parts = match.group(0).split()
        while parts and parts[0].lower() in _FRAME:
            parts = parts[1:]
        if parts:
            names.append(" ".join(parts))
    if names:
        return " ".join(names[:2])
    return " ".join(covered[:2])


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _new(query: str, used: list[str]) -> bool:
    return bool(query.strip()) and not any(jaccard(query, earlier) >= 0.82 for earlier in used)


def plan_second_look(question: str, subject: str, claims: list[Any], sources: list[Any], *, min_sources: int,
                     independent_sources: int, used_queries: Iterable[str], mode: str) -> SecondLook:
    """What to search for next, and why; empty when the first pass already answered everything it could."""
    plan = SecondLook()
    limit = FOLLOWUP_SEARCHES.get(mode, 0)
    if not limit:
        return plan
    used = [str(item) for item in used_queries]
    plan.covered, plan.uncovered = coverage(question, claims)
    anchor = _anchor(question, plan.covered)

    def add(query: str, reason: str) -> None:
        query = re.sub(r"\s+", " ", query).strip()
        if len(plan.queries) < limit and _new(query, used + plan.queries):
            plan.queries.append(query)
            if reason not in plan.reasons:
                plan.reasons.append(reason)

    if plan.covered and plan.uncovered:
        missing = plan.uncovered[:3]
        plan.focus.extend(missing)
        add(f"{anchor} {' '.join(missing)}", "part of the question no source answered: " + ", ".join(missing))
        if len(plan.uncovered) > 3:
            more = plan.uncovered[3:6]
            plan.focus.extend(more)
            add(f"{anchor} {' '.join(more)}", "part of the question no source answered: " + ", ".join(more))
    if independent_sources < min_sources and sources:
        domains = list(dict.fromkeys(_domain(getattr(source, "final_url", "") or getattr(source, "url", ""))
                                     for source in sources))
        plan.skip_domains = [domain for domain in domains if domain][:2]
        exclusions = " ".join(f"-site:{domain}" for domain in plan.skip_domains)
        add(f"{subject} {exclusions}", f"only {independent_sources} independent source(s); {min_sources} wanted")
    if any(getattr(getattr(claim, "status", None), "value", "") == "DISPUTED" for claim in claims):
        add(f"{subject} which account is correct evidence", "sources disagree")
    if mode in {"DEEP", "ADVERSARIAL"}:
        plan.learned_terms = _learned_terms(question, claims)
        for term in plan.learned_terms:
            plan.focus.append(term)
            add(f"{anchor or subject} {term}", "terms the sources use that the question did not: "
                + ", ".join(plan.learned_terms))
    return plan


def skipped(url: str, plan: SecondLook) -> bool:
    """Whether a follow-up result comes from a domain the corroboration search must not count again."""
    domain = _domain(url)
    return any(domain == item or domain.endswith("." + item) for item in plan.skip_domains)
