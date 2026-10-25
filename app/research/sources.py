from __future__ import annotations

from urllib.parse import urlsplit

from app.research.ranking import score_for_query


PRIMARY_MARKERS = ("docs.", "developer.", "developers.", "api.", "standards.")
LOW_QUALITY_MARKERS = ("pinterest.", "quora.", "answers.", "contentfarm")


def score_source(url: str, title: str = "", query: str = "") -> tuple[float, list[str]]:
    score, reasons, _, _ = score_for_query(url, title, query)
    return score, reasons


def is_probably_official(url: str, query: str, title: str = "") -> bool:
    host = (urlsplit(url).hostname or "").lower()
    haystack = f"{host} {urlsplit(url).path} {title}".lower()
    generic = {"learn", "current", "latest", "official", "sources", "source", "using", "only", "remember", "what", "about", "documentation", "information", "research"}
    topic = {token for token in query.lower().replace("-", " ").split() if len(token) >= 4 and token not in generic}
    matches = {token.strip(".,:;?!") for token in topic if token.strip(".,:;?!") in haystack}
    relevant = bool(matches) and (len(matches) >= 2 or any(len(token) >= 6 for token in matches))
    if not relevant:
        return False
    labels = set(host.split("."))
    topical_owner = any(token.strip(".,:;?!") in labels for token in topic)
    return (
        host.endswith((".gov", ".edu"))
        or host.startswith(PRIMARY_MARKERS)
        or host in {"github.com", "gitlab.com", "arxiv.org", "doi.org", "pubmed.ncbi.nlm.nih.gov", "pypi.org"}
        or topical_owner
    )
