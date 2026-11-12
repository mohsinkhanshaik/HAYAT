"""Explain the strength and gaps of a retained claim without treating a citation as proof.

This does not fetch pages or decide truth. It checks whether recorded excerpts can be tied to the
recorded source versions, distinguishes direct support from inference, and names the next check.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


def _stamp(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment.astimezone(UTC) if moment.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def explain_claim(claim: dict, sources: list[dict], *, now: datetime | None = None) -> dict[str, Any]:
    """Bounded, read-only explanation of one claim's retained record; never a probability of truth."""
    now = now or datetime.now(UTC)
    by_url: dict[str, list[dict]] = {}
    for source in sources[:200]:
        if isinstance(source, dict):
            for key in ("url", "final_url", "canonical_url"):
                if isinstance(source.get(key), str) and source[key]:
                    by_url.setdefault(source[key], []).append(source)
    gaps: list[str] = []
    direct, inferred, contradicting, passage_checked, passage_unavailable = set(), set(), set(), set(), set()
    groups: set[str] = set()
    links: list[dict[str, str]] = []
    examined, truncated = 0, len(sources) > 200
    for field in ("evidence", "contradictions"):
        records = claim.get(field) or []
        if not isinstance(records, list):
            gaps.append(f"Malformed {field} record.")
            continue
        truncated |= len(records) > 100
        for evidence in records[:100]:
            examined += 1
            if not isinstance(evidence, dict):
                gaps.append("An evidence entry is malformed.")
                continue
            url, digest = evidence.get("source_url"), evidence.get("source_hash")
            candidates = by_url.get(url, []) if isinstance(url, str) else []
            if not candidates:
                gaps.append("A cited document is absent from the retained source set.")
                continue
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                gaps.append("A citation has no valid source-version fingerprint.")
                continue
            source = next((row for row in candidates if str(row.get("content_hash", "")).lower() == digest.lower()), None)
            if source is None:
                gaps.append("A citation's source fingerprint does not match its retained document.")
                continue
            retrieved = _stamp(evidence.get("retrieved_at"))
            if retrieved is None or retrieved > now:
                gaps.append("A citation's retrieval time is missing, invalid, or in the future.")
                continue
            excerpt = evidence.get("excerpt")
            if not isinstance(excerpt, str) or not excerpt.strip():
                gaps.append("A citation has no retained excerpt.")
                continue
            identity = (url, digest.lower())
            content = source.get("text")
            if isinstance(content, str) and content.strip():
                if " ".join(excerpt.split()).casefold() not in " ".join(content.split()).casefold():
                    gaps.append("An excerpt cannot be found in its retained source text.")
                    continue
                passage_checked.add(identity)
            else:
                passage_unavailable.add(identity)
            kind = str(evidence.get("support_type", "UNSUPPORTED"))
            if field == "contradictions" or evidence.get("stance") == "contradicting":
                contradicting.add(identity)
            elif kind == "DIRECT":
                direct.add(identity)
            elif kind == "INFERRED":
                inferred.add(identity)
            else:
                gaps.append("Some retained evidence is weak or explicitly unsupported.")
            group = evidence.get("independence_group") or source.get("independence_group")
            if isinstance(group, str) and group.strip():
                groups.add(group.strip())
            links.append({"url": url, "support": kind, "relationship": field})
    deadline = _stamp(claim.get("validity_horizon") or claim.get("valid_to"))
    freshness = "expired" if deadline is not None and deadline <= now else "within recorded horizon" if deadline else "not established"
    if freshness == "expired":
        gaps.append("The claim's recorded validity horizon has expired; look it up again.")
    if not direct:
        gaps.append("No traceable direct-support excerpt passed this record check.")
    if contradicting or claim.get("status") == "DISPUTED":
        assessment, next_check = "Conflicting evidence recorded", "Compare the conflicting sources' dates, scope and methods."
    elif direct:
        assessment, next_check = "Direct-source records available", "Read the excerpts in context and corroborate the claim independently."
    elif inferred:
        assessment, next_check = "Inference only", "Find direct evidence for the conclusion and test alternative explanations."
    else:
        assessment, next_check = "Evidence not established", "Retrieve a primary source with an exact passage that supports this claim."
    return {"assessment": assessment, "reported_status": str(claim.get("status") or "UNVERIFIED"),
            "direct_sources": len(direct), "inferred_sources": len(inferred), "contradicting_sources": len(contradicting),
            "recorded_independence_groups": len(groups), "passages_checked": len(passage_checked),
            "source_text_unavailable": len(passage_unavailable), "freshness": freshness,
            "gaps": list(dict.fromkeys(gaps)), "links": links, "examined": examined, "truncated": truncated,
            "next_check": next_check,
            "scope": "Checks retained evidence records only. Neither a citation, a model confidence score nor this check proves truth or causation."}


def explanation_lines(check: dict) -> list[str]:
    lines = ["Evidence check: " + check["assessment"],
             f"Traceable sources: {check['direct_sources']} direct, {check['inferred_sources']} inferred, "
             f"{check['contradicting_sources']} contradictory.",
             f"Freshness: {check['freshness']}. Recorded independence groups: {check['recorded_independence_groups']}.",
             f"Passages checked against retained text: {check['passages_checked']}; "
             f"source text unavailable for {check['source_text_unavailable']} source versions."]
    lines.extend("Needs checking: " + gap for gap in check["gaps"])
    if check["truncated"]:
        lines.append("Bounded view: first 200 sources and 100 entries per evidence type were examined.")
    return [*lines, "Next: " + check["next_check"], check["scope"]]
