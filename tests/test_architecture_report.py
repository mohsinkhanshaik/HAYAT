from __future__ import annotations

from pypdf import PdfReader

from scripts.build_architecture_report import build


def _evidence():
    return {
        "release": {"tests_passed": 1, "tests_failed": 0, "tests_skipped": 0, "seconds": 1.0},
        "brain_mesh": {
            "passed": True, "nodes": 14, "edges": 16,
            "independent": {"passed": True, "critical_failures": []},
            "team": {"passed": True},
        },
        "brain_benchmark": {"benchmarks": {
            name: {"mean_ms": 1.0, "p95_ms": 1.1, "min_ms": .8, "max_ms": 1.2,
                   "cold_first_ms": 1.2, "warm_mean_ms": .9}
            for name in ("intent", "model_router", "memory_health", "tool_inventory", "team_contract")
        }},
        "learning": {
            "recall_health": {"integrity": "ok", "healthy": True,
                              "missing_hashed_vectors": 0, "missing_fts_rows": 0},
            "alignment": {"status": "aligned"},
        },
        "memory_integrity": {"checked": 10, "valid": 10, "mismatches": []},
        "knowledge": {"sources": 5, "claims": 8, "evidence": 9,
                      "disputed_claims": 0, "unresolved_conflicts": 0, "superseded": 1},
        "security": {
            "passed": True, "pip_check": {"passed": True}, "secret_scan": {"passed": True},
            "download_defense": {"provider": "microsoft_defender", "fail_closed": True,
                                  "automatic_execution": False, "counterattack": False},
        },
        "runtime": {"tools": 100, "zero_cost": {"mode": "ZERO-COST"}},
    }


def test_architecture_pdf_has_complete_sections_and_valid_pages(tmp_path):
    output = build(_evidence(), tmp_path / "report.pdf")
    reader = PdfReader(output)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert len(reader.pages) >= 10
    assert output.stat().st_size > 40_000
    for heading in (
        "Brain mesh", "Request lifecycle", "Memory, neural retrieval, and DNA",
        "Operator and network infrastructure", "Coordination benchmark",
        "Security, file admission, and self-defense", "Limitations and roadmap",
    ):
        assert heading in text
    assert "Cracking, access bypass" in text
    assert "Page 2" in text
    assert all(float(page.mediabox.width) > 0 and float(page.mediabox.height) > 0
               for page in reader.pages)

