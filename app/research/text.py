from __future__ import annotations

import hashlib
import re


STOPWORDS = {
    "about", "after", "again", "also", "because", "before", "being", "between", "could", "does",
    "from", "have", "into", "more", "most", "other", "should", "than", "that", "their", "there",
    "these", "they", "this", "those", "through", "using", "what", "when", "where", "which", "with",
    "would", "your", "current", "latest", "official", "research", "source", "sources", "the", "and", "its", "did", "was", "were",
}


def tokens(text: str, minimum: int = 3) -> list[str]:
    return [value for value in re.findall(r"[a-z0-9][a-z0-9_.+-]*", text.lower()) if len(value) >= minimum and value not in STOPWORDS]


def token_set(text: str, minimum: int = 3) -> set[str]:
    return set(tokens(text, minimum))


def jaccard(left: str | set[str], right: str | set[str]) -> float:
    a = token_set(left) if isinstance(left, str) else left
    b = token_set(right) if isinstance(right, str) else right
    return len(a & b) / max(1, len(a | b))


def overlap(needle: str, haystack: str) -> float:
    wanted = token_set(needle)
    present = token_set(haystack)
    return len(wanted & present) / max(1, len(wanted))


def stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8", errors="replace")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def extract_entities(text: str) -> list[str]:
    acronyms = re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", text)
    names = re.findall(r"\b(?:[A-Z][a-z0-9.+-]+(?:\s+[A-Z][A-Za-z0-9.+-]+){0,3})\b", text)
    values: list[str] = []
    for value in [*acronyms, *names]:
        clean = value.strip()
        if clean not in values and clean.lower() not in {"the", "this", "what", "how"}:
            values.append(clean)
    return values[:20]
