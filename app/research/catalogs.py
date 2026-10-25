"""Find a book in the public catalogues: Open Library and the Internet Archive.

General web search is poor at books -- it returns reviews and shop pages, and
old texts in particular live in library catalogues. Shams al-Ma'arif, which a
web search could not find, is catalogued by Open Library in editions from 1870
on and held as an Arabic text by the Internet Archive. Both catalogues are
free public APIs; nothing is logged in to, and a book is only reported with
the access the catalogue itself states.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)
_BOOKISH = re.compile(r"(?i)\b(?:book|books|pdf|ebook|e-book|novel|manuscript|grimoire|kitab|volume|edition|author|"
                      r"written by|text of|read online|scan|copy of|named|titled)\b")
HEADERS = {"User-Agent": "HAYAT local research assistant (read-only catalogue lookup)"}


def looks_like_a_book(query: str, asked: str = "") -> bool:
    return bool(_BOOKISH.search(f"{query} {asked}"))


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[ʻʼ'’`]", "", value).replace("-", " ").replace("_", " ")
    value = re.sub(r"[^a-z0-9؀-ۿ ]+", " ", value)
    return re.sub(r"(.)\1+", r"\1", " ".join(value.split()))


def _relevant(query: str, *titles: str) -> bool:
    from rapidfuzz import fuzz

    wanted = _fold(query)
    return any(fuzz.partial_ratio(wanted, _fold(title)) >= 80 or fuzz.token_set_ratio(wanted, _fold(title)) >= 70
               for title in titles if title)


def find_books(query: str, *, http: Any = None, limit: int = 5) -> list[dict[str, str]]:
    import httpx

    client = http or httpx
    found: list[dict[str, str]] = []
    try:
        response = client.get("https://openlibrary.org/search.json", headers=HEADERS, timeout=20,
                              params={"q": query, "limit": limit,
                                      "fields": "key,title,author_name,first_publish_year,ebook_access,ia"})
        for doc in response.json().get("docs", []):
            if not _relevant(query, doc.get("title", "")):
                continue
            access = {"public": "free to read", "borrowable": "borrowable", "printdisabled": "print-disabled access"}.get(
                doc.get("ebook_access", ""), "catalogue record")
            found.append({"catalogue": "Open Library", "title": doc.get("title", ""),
                          "author": ", ".join(doc.get("author_name") or [])[:80],
                          "year": str(doc.get("first_publish_year") or ""), "access": access,
                          "url": f"https://openlibrary.org{doc.get('key', '')}"})
    except Exception:
        log_swallowed(_log, "searching Open Library")
    try:
        response = client.get("https://archive.org/advancedsearch.php", headers=HEADERS, timeout=20,
                              params={"q": query, "fl[]": ["identifier", "title", "creator", "year", "mediatype"],
                                      "rows": limit, "output": "json"})
        for doc in response.json().get("response", {}).get("docs", []):
            creator = doc.get("creator")
            creator = ", ".join(creator) if isinstance(creator, list) else str(creator or "")
            if doc.get("mediatype") != "texts" or not _relevant(query, doc.get("title", ""), doc.get("identifier", "")):
                continue
            found.append({"catalogue": "Internet Archive", "title": str(doc.get("title", "")), "author": creator[:80],
                          "year": str(doc.get("year") or ""), "access": "digitised text held by the Archive",
                          "url": f"https://archive.org/details/{doc.get('identifier')}"})
    except Exception:
        log_swallowed(_log, "searching the Internet Archive")
    return found[: limit * 2]


def books_section(books: list[dict[str, str]]) -> str:
    if not books:
        return ""
    lines = ["In the public book catalogues:"]
    for book in books[:8]:
        detail = ", ".join(part for part in (book["author"], book["year"]) if part)
        lines.append(f"  • {book['title']}{' (' + detail + ')' if detail else ''} -- {book['catalogue']}, "
                     f"{book['access']}: {book['url']}")
    return "\n".join(lines)
