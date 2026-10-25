from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from app.research.text import overlap

_TECHNICAL = re.compile(r"(?i)\b(?:apis?|librar(?:y|ies)|packages?|software|python|github|releases?|versions?|documentation|errors?|traceback|(?:type|value|runtime|import|attribute|key|os|name)error)\b")


def technical_question(query: str) -> bool:
    """Match technical terms as words: 'capital' contains letters 'api'."""
    return bool(_TECHNICAL.search(query))


@dataclass(slots=True)
class QuestionProfile:
    kind: str
    freshness: str
    preferred_types: tuple[str, ...]


def profile_question(query: str) -> QuestionProfile:
    lower = query.lower()
    if any(word in lower for word in ("paper", "study", "methodology", "sample size", "scientific")):
        return QuestionProfile("science", "slow_changing", ("original_research", "systematic_review", "institution", "secondary"))
    if technical_question(query):
        return QuestionProfile("software", "changing", ("official_docs", "official_repository", "release_notes", "standard", "issue_tracker", "technical_analysis"))
    if any(word in lower for word in ("price", "company", "ceo", "product", "earnings", "revenue")):
        return QuestionProfile("company", "fast_changing", ("official_announcement", "regulatory_filing", "official_docs", "reputable_reporting"))
    if any(word in lower for word in ("today", "latest", "news", "current", "recent")):
        return QuestionProfile("current", "live", ("primary", "official_announcement", "reputable_reporting"))
    return QuestionProfile("general", "stable", ("primary", "standard", "institution", "reputable_secondary"))


def classify_source(url: str, title: str, query: str) -> tuple[str, bool]:
    host = (urlsplit(url).hostname or "").lower()
    path = urlsplit(url).path.lower()
    lower_title = title.lower()
    if host in {"www.rfc-editor.org", "rfc-editor.org", "www.ietf.org", "ietf.org", "www.w3.org", "w3.org"}:
        return "standard", True
    if host in {"arxiv.org", "doi.org", "pubmed.ncbi.nlm.nih.gov"} or "paper" in lower_title:
        return "original_research", True
    if host.endswith(".gov") or ".gov." in host:
        return "institution", True
    if host == "github.com":
        if "/issues/" in path or "/pull/" in path:
            return "issue_tracker", False
        if "/releases" in path or "/tags" in path:
            return "release_notes", True
        return "official_repository", True
    if any(host.startswith(prefix) for prefix in ("docs.", "developer.", "developers.", "api.")) or "/docs" in path or "/documentation" in path:
        return "official_docs", True
    if any(value in path or value in lower_title for value in ("release", "changelog", "what's new", "whats-new")):
        return "release_notes", True
    if host.endswith(".edu"):
        return "institution", True
    if any(value in host for value in ("medium.com", "substack.com", "blogspot.com")):
        return "blog", False
    return "web_page", False


def score_for_query(url: str, title: str, query: str, published_at: str | None = None) -> tuple[float, list[str], str, bool]:
    profile = profile_question(query)
    source_type, primary = classify_source(url, title, query)
    reasons = [f"question profile: {profile.kind}", f"source type: {source_type}"]
    base = 0.40 + min(0.24, overlap(query, f"{title} {url}") * 0.28)
    if source_type in profile.preferred_types:
        position = profile.preferred_types.index(source_type)
        base += max(0.10, 0.30 - position * 0.045)
        reasons.append("preferred for question type")
    elif source_type in {"blog", "web_page"}:
        base -= 0.08
    if primary:
        base += 0.12
        reasons.append("primary/first-party evidence")
    if profile.freshness in {"changing", "fast_changing", "live"} and not published_at:
        base -= 0.06
        reasons.append("date unavailable for changing fact")
    host = (urlsplit(url).hostname or "").lower()
    if any(marker in host for marker in ("pinterest.", "quora.", "answers.", "fandom.com")):
        base -= 0.28
        reasons.append("low-authority aggregation")
    return round(max(0.0, min(1.0, base)), 3), reasons, source_type, primary
