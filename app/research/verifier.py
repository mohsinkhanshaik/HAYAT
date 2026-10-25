from __future__ import annotations

import re
from collections import defaultdict

from app.research.citation_validator import CitationValidator
from app.research.models import Claim, ClaimStatus, Evidence, SourceDocument, SupportType
from app.research.text import jaccard, token_set


class EvidenceVerifier:
    """Validates provenance, resolves scope-aware contradictions, and calibrates factual confidence."""

    def __init__(self) -> None:
        self.citations = CitationValidator()

    def verify_claims(self, claims: list[Claim], sources: list[SourceDocument]) -> tuple[list[Claim], float, dict[str, int | float]]:
        claims, validation = self.citations.validate_all(claims, sources)
        self._merge_support(claims)
        self._detect_contradictions(claims)
        for claim in claims:
            claim.confidence = self._confidence(claim, sources)
        supported = [claim for claim in claims if claim.status in {ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED}]
        overall = sum(claim.confidence for claim in supported) / max(1, len(supported))
        if any(claim.status == ClaimStatus.DISPUTED for claim in claims):
            overall *= 0.78
        return claims, round(min(0.97, overall), 3), validation

    def verify(self, query: str, sources: list[SourceDocument], excerpts: list[str]) -> tuple[list[Claim], float]:
        """Compatibility wrapper for callers that still supply excerpts."""
        from app.research.claims import ClaimExtractor
        claims: list[Claim] = []
        extractor = ClaimExtractor()
        for source, excerpt in zip(sources, excerpts, strict=False):
            claims.extend(extractor.extract(query, source, excerpt))
        verified, confidence, _ = self.verify_claims(claims, sources)
        return verified, confidence

    @staticmethod
    def _merge_support(claims: list[Claim]) -> None:
        for index, claim in enumerate(claims):
            for other in claims[index + 1:]:
                if jaccard(claim.text, other.text) >= 0.72:
                    existing = {(evidence.source_url, evidence.excerpt) for evidence in claim.evidence}
                    claim.evidence.extend(evidence for evidence in other.evidence if (evidence.source_url, evidence.excerpt) not in existing)

    def _detect_contradictions(self, claims: list[Claim]) -> None:
        for index, claim in enumerate(claims):
            for other in claims[index + 1:]:
                if not self._same_scope(claim, other):
                    continue
                if self.contradiction_reason(claim, other):
                    for evidence in other.evidence:
                        claim.contradictions.append(self._as_contradiction(evidence))
                    claim.status = ClaimStatus.DISPUTED
                    other.status = ClaimStatus.DISPUTED

    @classmethod
    def contradiction_reason(cls, left: Claim, right: Claim) -> str:
        """Explain a same-scope logical conflict, or return an empty string."""
        if not cls._same_scope(left, right):
            return ""
        left_tokens = cls._proposition_tokens(left.text)
        right_tokens = cls._proposition_tokens(right.text)
        similarity = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
        if similarity < 0.42:
            return ""
        if cls._negative(left.text) != cls._negative(right.text):
            return "same-scope claims have opposite polarity"
        if cls._incompatible_measurements(left.text, right.text):
            return "same-scope claims report incompatible numeric values"
        return ""

    @staticmethod
    def _confidence(claim: Claim, sources: list[SourceDocument]) -> float:
        if claim.status == ClaimStatus.UNVERIFIED:
            return 0.0
        quality_by_url = {source.final_url: source.quality for source in sources}
        primary_urls = {source.final_url for source in sources if source.primary}
        direct = [evidence for evidence in claim.evidence if evidence.support_type == SupportType.DIRECT]
        inferred = [evidence for evidence in claim.evidence if evidence.support_type == SupportType.INFERRED]
        groups = {evidence.independence_group or evidence.source_url for evidence in [*direct, *inferred]}
        evidence_quality = sum(quality_by_url.get(evidence.source_url, evidence.quality) for evidence in [*direct, *inferred]) / max(1, len([*direct, *inferred]))
        directness = min(1.0, len(direct) * 0.55 + len(inferred) * 0.22)
        independence = min(1.0, len(groups) / 3)
        conflict_penalty = min(0.45, len(claim.contradictions) * 0.16)
        primary_bonus = 0.06 if any(evidence.source_url in primary_urls for evidence in direct) else 0.0
        value = 0.16 + evidence_quality * 0.36 + directness * 0.28 + independence * 0.20 + primary_bonus - conflict_penalty
        return round(max(0.05, min(0.97, value)), 3)

    @staticmethod
    def _same_scope(left: Claim, right: Claim) -> bool:
        if left.scope and right.scope and left.scope.casefold() != right.scope.casefold():
            return False
        left_years = set(re.findall(r"\b(?:19|20)\d{2}\b", left.text))
        right_years = set(re.findall(r"\b(?:19|20)\d{2}\b", right.text))
        if left_years and right_years and left_years != right_years:
            return False
        left_versions = set(re.findall(r"(?i)\bv?\d+(?:\.\d+){1,3}\b", left.text))
        right_versions = set(re.findall(r"(?i)\bv?\d+(?:\.\d+){1,3}\b", right.text))
        if left_versions and right_versions and left_versions != right_versions:
            return False
        return True

    @staticmethod
    def _proposition_tokens(text: str) -> set[str]:
        return token_set(re.sub(r"\b(?:not|never|no longer|does not|cannot|unsupported)\b", "", text.lower()))

    #: A contrast, not a denial. "It saves you $220, not $1,000" asserts the
    #: $220 and rules out the $1,000; it does not negate the proposition. Read
    #: naively it does, which is how six sources that agreed exactly about a
    #: 22% bracket and a $1,000 deduction were recorded as contradicting each
    #: other -- the ones phrased "saves you $220, not $1,000" counted as
    #: negative and the ones phrased "saves you $220" counted as positive.
    _CONTRASTIVE = re.compile(
        r"(?:,\s*|\s+—\s*|\s+--\s*|\s+\(\s*)(?:and\s+|but\s+)?not\b[^.;)]*\)?"
        r"|\brather\s+than\b[^.;]*"
        r"|\binstead\s+of\b[^.;]*",
        re.IGNORECASE,
    )

    @classmethod
    def _negative(cls, text: str) -> bool:
        """Whether the claim denies its proposition.

        The contrastive tail is removed first, so polarity is judged on what
        the sentence actually asserts rather than on any appearance of "not".
        """
        stripped = cls._CONTRASTIVE.sub(" ", text.lower())
        return bool(re.search(r"\b(?:not|no longer|never|unsupported|doesn't|does not|cannot|can't|without)\b", stripped))

    @staticmethod
    def _numbers(text: str) -> set[str]:
        return set(re.findall(r"\b\d+(?:\.\d+)?%?\b", text))

    @staticmethod
    def _measurements(text: str) -> dict[str, set[str]]:
        """Numeric values keyed by an explicit unit, conservatively.

        Treating every differing number as a contradiction marks model names,
        years, sample sizes and resolutions as competing facts. A numeric
        conflict needs the same measurement unit on both sides.
        """
        units = (
            r"%|percent|percentage|fps|frames? per second|ms|milliseconds?|"
            r"seconds?|minutes?|hours?|days?|gb|mb|tb|kb|mhz|ghz|watts?|w|"
            r"usd|dollars?|euros?|pounds?|million|billion|trillion|degrees?"
        )
        found: dict[str, set[str]] = defaultdict(set)
        for match in re.finditer(rf"\b(\d+(?:\.\d+)?)\s*({units})\b", text, re.I):
            unit = match.group(2).casefold()
            aliases = {"%": "percent", "percentage": "percent", "frames per second": "fps"}
            found[aliases.get(unit, unit)].add(match.group(1))
        return found

    @classmethod
    def _incompatible_measurements(cls, left: str, right: str) -> bool:
        left_values, right_values = cls._measurements(left), cls._measurements(right)
        for unit in left_values.keys() & right_values.keys():
            if left_values[unit].isdisjoint(right_values[unit]):
                return True
        return False

    @staticmethod
    def _as_contradiction(evidence: Evidence) -> Evidence:
        from dataclasses import replace
        return replace(evidence, stance="contradicting")
