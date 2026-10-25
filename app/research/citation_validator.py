from __future__ import annotations

import re
import time
from dataclasses import dataclass

from app.research.models import Claim, ClaimStatus, SourceDocument, SupportType
from app.research.text import overlap


@dataclass(slots=True)
class CitationValidation:
    valid: bool
    source_retrieved: bool
    url_matches: bool
    hash_matches: bool
    excerpt_found: bool
    support_type: SupportType
    support_score: float
    reason: str


class CitationValidator:
    def validate_claim(self, claim: Claim, sources: list[SourceDocument]) -> list[CitationValidation]:
        indexed = {source.final_url: source for source in sources}
        results: list[CitationValidation] = []
        for evidence in claim.evidence:
            source = indexed.get(evidence.source_url)
            if source is None:
                results.append(CitationValidation(False, False, False, False, False, SupportType.UNSUPPORTED, 0.0, "Cited URL was not retrieved."))
                evidence.support_type = SupportType.UNSUPPORTED
                evidence.strength = 0.0
                continue
            normalized_excerpt = self._normalize(evidence.excerpt)
            normalized_source = self._normalize(source.text)
            excerpt_found = bool(normalized_excerpt) and normalized_excerpt in normalized_source
            url_matches = evidence.source_url == source.final_url
            hash_matches = not evidence.source_hash or evidence.source_hash == source.content_hash
            support_score = overlap(claim.text, evidence.excerpt)
            support_type = SupportType.DIRECT if support_score >= 0.72 else SupportType.INFERRED if support_score >= 0.46 else SupportType.WEAK if support_score >= 0.25 else SupportType.UNSUPPORTED
            valid = url_matches and hash_matches and excerpt_found and support_type != SupportType.UNSUPPORTED
            if not valid:
                support_type = SupportType.UNSUPPORTED
                support_score = 0.0
            evidence.support_type = support_type
            evidence.strength = round(support_score * source.quality, 3)
            reason = "Retrieved evidence directly supports the claim." if support_type == SupportType.DIRECT and valid else "Evidence is inferential." if valid else "Retrieved content does not validate this citation."
            results.append(CitationValidation(valid, True, url_matches, hash_matches, excerpt_found, support_type, round(support_score, 3), reason))
        valid = [item for item in results if item.valid]
        if not valid:
            claim.status = ClaimStatus.UNVERIFIED
            claim.confidence = 0.0
        elif any(item.support_type == SupportType.DIRECT for item in valid):
            claim.status = ClaimStatus.SUPPORTED
        else:
            claim.status = ClaimStatus.PARTIALLY_SUPPORTED
        return results

    def validate_all(self, claims: list[Claim], sources: list[SourceDocument]) -> tuple[list[Claim], dict[str, int | float]]:
        started = time.perf_counter()
        totals = {"DIRECT": 0, "INFERRED": 0, "WEAK": 0, "UNSUPPORTED": 0}
        for claim in claims:
            validations = self.validate_claim(claim, sources)
            if not validations:
                totals["UNSUPPORTED"] += 1
            for validation in validations:
                totals[validation.support_type.value] += 1
        totals["validation_ms"] = int((time.perf_counter() - started) * 1000)
        return claims, totals

    @staticmethod
    def citations_for_claim(claim: Claim, sources: list[SourceDocument]) -> list[int]:
        by_url = {source.final_url: index for index, source in enumerate(sources, 1)}
        return sorted({by_url[evidence.source_url] for evidence in claim.evidence if evidence.support_type != SupportType.UNSUPPORTED and evidence.source_url in by_url})

    @staticmethod
    def _normalize(text: str) -> str:
        value = re.sub(r"\s+", " ", text).strip().lower()
        # HTML emphasis/code nodes can insert spaces before sentence punctuation
        # or inside brackets. Normalize that typography without dropping words.
        value = re.sub(r"\s+([.,;:!?%)\]])", r"\1", value)
        return re.sub(r"([(\[])\s+", r"\1", value)
