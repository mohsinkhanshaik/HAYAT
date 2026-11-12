from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from app.research.evidence_explanation import explain_claim, explanation_lines

NOW = datetime(2026, 11, 11, tzinfo=UTC)
URL = "https://example.org/research"


def records():
    source = {"url": URL, "content_hash": "a" * 64, "text": "A synthetic test: the treatment changed the score."}
    evidence = {"source_url": URL, "source_hash": "a" * 64, "retrieved_at": "2026-11-10T10:00:00Z",
                "excerpt": "the treatment changed the score.", "support_type": "DIRECT", "independence_group": "fixture"}
    claim = {"text": "A fixture claim", "status": "SUPPORTED", "evidence": [evidence],
             "validity_horizon": "2026-12-01T00:00:00Z"}
    return claim, [source]


def test_exact_source_version_and_excerpt_are_traceable_but_not_declared_true():
    claim, sources = records()
    before = deepcopy((claim, sources))
    result = explain_claim(claim, sources, now=NOW)
    assert result["direct_sources"] == result["passages_checked"] == 1
    assert result["assessment"] == "Direct-source records available"
    assert result["freshness"] == "within recorded horizon"
    assert not result["gaps"]
    assert (claim, sources) == before
    assert "proves truth or causation" in result["scope"]


@pytest.mark.parametrize("field,value,gap", [
    ("source_hash", "b" * 64, "does not match"),
    ("source_hash", "", "no valid"),
    ("source_url", "https://example.org/never-fetched", "absent"),
    ("excerpt", "A fabricated quotation", "cannot be found"),
    ("excerpt", "", "no retained excerpt"),
    ("retrieved_at", "2027-01-01T00:00:00Z", "future"),
    ("retrieved_at", "2026-11-10T00:00:00", "invalid"),
])
def test_supported_label_cannot_hide_broken_provenance(field, value, gap):
    claim, sources = records()
    claim["evidence"][0][field] = value
    result = explain_claim(claim, sources, now=NOW)
    assert result["reported_status"] == "SUPPORTED"
    assert result["direct_sources"] == 0
    assert result["assessment"] == "Evidence not established"
    assert any(gap in item for item in result["gaps"])


def test_missing_source_text_is_an_explicit_limit_not_an_excerpt_verification():
    claim, sources = records()
    sources[0].pop("text")
    result = explain_claim(claim, sources, now=NOW)
    assert result["direct_sources"] == result["source_text_unavailable"] == 1
    assert result["passages_checked"] == 0
    assert "source text unavailable" in "\n".join(explanation_lines(result))


def test_repeated_citations_do_not_become_independent_corroboration():
    claim, sources = records()
    claim["evidence"] *= 10
    result = explain_claim(claim, sources, now=NOW)
    assert result["direct_sources"] == result["recorded_independence_groups"] == 1


def test_conflict_inference_and_expired_claims_explain_the_next_check():
    claim, sources = records()
    claim["evidence"][0]["support_type"] = "INFERRED"
    claim["validity_horizon"] = "2026-11-01T00:00:00Z"
    inferred = explain_claim(claim, sources, now=NOW)
    assert inferred["assessment"] == "Inference only" and inferred["freshness"] == "expired"
    claim["contradictions"] = deepcopy(claim["evidence"])
    result = explain_claim(claim, sources, now=NOW)
    assert result["assessment"] == "Conflicting evidence recorded" and result["contradicting_sources"] == 1
    assert "dates, scope and methods" in result["next_check"]


def test_limits_are_visible_and_invalid_records_do_not_crash_the_inspector():
    claim, sources = records()
    claim["evidence"] = [None, {}, *claim["evidence"] * 110]
    result = explain_claim(claim, sources, now=NOW)
    assert result["examined"] == 100 and result["truncated"]
    assert "Bounded view" in "\n".join(explanation_lines(result))
    assert "malformed" in " ".join(result["gaps"])
