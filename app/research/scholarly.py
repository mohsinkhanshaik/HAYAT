"""Scholarly records of a named person's published work, from OpenAlex: each author record is a separate candidate.

Part of public-work research (app/research/people.py). OpenAlex is an open catalogue of scholarly works
and their authors (CC0, no key, robots allow all). For a supplied name it gives author records with their
institution, topics, numbers of works and citations, and most-cited works: professional output only.

The same rules hold as for the rest of the people workflow:
* a record counts only when every part of the supplied name appears in the author's name (OpenAlex's own
  search is fuzzy: "Geoffrey Hinton" also returns other neural-network authors);
* namesakes stay separate candidates, never merged; supplied context (an institution or field) only ranks
  them, it does not prove which one is meant;
* nothing personal is read or kept: no contact details, no country, no photos.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

OPENALEX = "https://api.openalex.org"


def _words(value: Any) -> list[str]:
    plain = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").casefold()
    return [word for word in re.split(r"[^a-z0-9]+", plain) if word]


def _short(value: Any, limit: int) -> str:
    """Text from OpenAlex, bounded: a record once carried a 5,000-character institution name in testing."""
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def name_matches(wanted: str, display_name: str, alternatives: list[str] | None = None) -> bool:
    """Every part of the supplied name (two letters or more) appears in the author's name or one of its spellings.

    The family name must be in the author's main name: OpenAlex's alternative spellings are noisy (live, Yoshua
    Bengio's record listed "Geoffrey Hinton" among them), so they may only complete given names or initials.
    """
    needed = [word for word in _words(wanted) if len(word) > 1]
    if not needed or needed[-1] not in _words(display_name):
        return False
    for candidate in [display_name, *(alternatives or [])]:
        have = set(_words(candidate))
        if all(word in have for word in needed):
            return True
    return False


class Scholarly:
    def __init__(self, get_json: Callable[[str], Any]) -> None:
        self.get_json = get_json

    def records(self, name: str, context: str = "", limit: int = 3) -> dict[str, Any]:
        data = self.get_json(f"{OPENALEX}/authors?search={quote(name)}&per-page=10")
        results = data.get("results") or [] if isinstance(data, dict) else []
        matched = [author for author in results
                   if name_matches(name, author.get("display_name", ""), author.get("display_name_alternatives") or [])]
        context_words = {word for word in _words(context) if len(word) > 2}

        def fits(author: dict[str, Any]) -> int:
            if not context_words:
                return 0
            described = " ".join([institution.get("display_name", "") for institution in author.get("last_known_institutions") or []]
                                 + [topic.get("display_name", "") for topic in author.get("topics") or []])
            return len(context_words & set(_words(described)))
        matched.sort(key=lambda author: (-fits(author), -(author.get("works_count") or 0)))
        candidates = []
        for author in matched[:max(1, limit)]:
            short_id = str(author.get("id", "")).rsplit("/", 1)[-1]
            works = []
            if short_id:
                try:
                    listing = self.get_json(f"{OPENALEX}/works?filter=author.id:{short_id}&sort=cited_by_count:desc&per-page=5"
                                            "&select=display_name,publication_year,doi,cited_by_count")
                    works = [{"title": _short(work.get("display_name", ""), 200), "year": work.get("publication_year"),
                              "doi": work.get("doi") or "", "cited_by": work.get("cited_by_count", 0)}
                             for work in (listing.get("results") or [])]
                except Exception as exc:  # a works lookup failing leaves the author record standing
                    works = [{"title": f"(works could not be read: {type(exc).__name__})"}]
            candidates.append({
                "name": _short(author.get("display_name", ""), 120),
                "institutions": [_short(institution.get("display_name", ""), 120)
                                 for institution in author.get("last_known_institutions") or []][:3],
                "topics": [_short(topic.get("display_name", ""), 80) for topic in author.get("topics") or []][:4],
                "works_count": author.get("works_count", 0), "cited_by_count": author.get("cited_by_count", 0),
                "orcid": author.get("orcid") or "", "record": author.get("id", ""),
                "fits_context": bool(fits(author)), "top_works": works,
            })
        return {"scholarly": True, "source": "OpenAlex", "searched": len(results), "matched": len(matched),
                "candidates": candidates,
                "basis": "OpenAlex author records whose name contains every part of the name given. Each is a separate "
                         "candidate: OpenAlex can split one person or join namesakes, and supplied context only ranks them."}


def scholarly_lines(records: dict[str, Any]) -> list[str]:
    """The records as answer lines, each candidate apart."""
    candidates = records.get("candidates") or []
    lines = ["", f"Scholarly records (OpenAlex): {records.get('matched', 0)} author record(s) carry this name"
             + (f"; showing {len(candidates)}." if candidates else ".")]
    for index, author in enumerate(candidates, 1):
        where = ", ".join(author["institutions"]) or "institution not recorded"
        lines.append(f"[S{index}] {author['name']} · {where} · {author['works_count']:,} works · "
                     f"{author['cited_by_count']:,} citations" + (" · fits the context given" if author["fits_context"] else ""))
        if author["topics"]:
            lines.append("    Topics: " + ", ".join(author["topics"]))
        for work in author["top_works"][:3]:
            lines.append(f"    • {work.get('title', '')}" + (f" ({work['year']})" if work.get("year") else "")
                         + (f", cited {work['cited_by']:,} times" if work.get("cited_by") else "")
                         + (f" — {work['doi']}" if work.get("doi") else ""))
        if author["orcid"]:
            lines.append("    ORCID: " + author["orcid"])
    if candidates:
        lines.append("These are separate records, not one confirmed person: OpenAlex can split one author or join namesakes.")
    return lines
