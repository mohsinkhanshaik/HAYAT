"""Which findings are confirmed, which are disputed, and which rest on one source.

Research already scored each claim against its evidence; the answer did not
say so. "Find by yourself which is correct and which is false" is answered
here from that evidence: a claim is called confirmed only when independent
sources (different sites, not copies of each other) agree, disputed when
credible evidence contradicts it, and single-source otherwise.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.research.models import ClaimStatus


def _independent(evidence: list[Any]) -> int:
    groups = set()
    for item in evidence:
        group = getattr(item, "independence_group", "") or urlparse(getattr(item, "source_url", "") or "").netloc
        groups.add(group.lower().removeprefix("www."))
    groups.discard("")
    return len(groups)


def _short(text: str, limit: int = 140) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def verdict(result: Any) -> str:
    claims = list(getattr(result, "claims", []) or [])
    if not claims:
        return ""
    confirmed = [claim for claim in claims if claim.status == ClaimStatus.SUPPORTED and _independent(claim.evidence) >= 2]
    disputed = [claim for claim in claims if claim.status == ClaimStatus.DISPUTED or claim.contradictions]
    single = [claim for claim in claims if claim not in confirmed and claim not in disputed
              and claim.status != ClaimStatus.SUPERSEDED]
    lines = ["How far to trust this:"]
    for claim in confirmed[:3]:
        lines.append(f"  ✓ Confirmed by {_independent(claim.evidence)} independent sources: {_short(claim.text)}")
    for claim in disputed[:3]:
        against = ", ".join(sorted({urlparse(item.source_url).netloc.removeprefix('www.') for item in claim.contradictions
                                    if getattr(item, 'source_url', '')}))[:120]
        lines.append(f"  ✗ Disputed{' by ' + against if against else ''}: {_short(claim.text)}")
    for claim in single[:2]:
        lines.append(f"  ? Only one source says: {_short(claim.text)}")
    if len(lines) == 1:
        return ""
    confidence = float(getattr(result, "confidence", 0.0) or 0.0)
    lines.append(f"  Overall confidence: {confidence:.0%}"
                 + (" -- check the primary sources before relying on it." if confidence < 0.6 else "."))
    return "\n".join(lines)
