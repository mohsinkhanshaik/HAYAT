from datetime import UTC, datetime

import pytest

from app.research.freshness import is_stale
from app.research.official_pack import refresh_official_pack
from tests.test_official_knowledge import ENTRY, fetcher, knowledge


@pytest.fixture
def store(tmp_path):
    result = knowledge(tmp_path)
    refresh_official_pack([ENTRY], result, fetcher())
    with result.connect() as db:
        db.execute("UPDATE claims SET valid_until='2099-01-01T00:00:00+00:00'")
    return result


def clone_claim(store, identifier, *, deadline="2099-01-01T00:00:00+00:00", evidence=True):
    with store.connect() as db:
        row = dict(db.execute("SELECT * FROM claims ORDER BY id LIMIT 1").fetchone())
        original = row["id"]
        row.update(id=identifier, valid_until=deadline)
        db.execute(f"INSERT INTO claims({','.join(row)}) VALUES({','.join('?' for _ in row)})", list(row.values()))
        if evidence:
            item = dict(db.execute("SELECT * FROM evidence WHERE claim_id=? LIMIT 1", (original,)).fetchone())
            item.update(id="e-" + identifier, claim_id=identifier)
            db.execute(f"INSERT INTO evidence({','.join(item)}) VALUES({','.join('?' for _ in item)})", list(item.values()))


def test_quality_filters_and_health_require_direct_evidence(store):
    clone_claim(store, "stale", deadline="2000-01-01T00:00:00Z")
    clone_claim(store, "invalid", deadline="damaged timestamp")
    clone_claim(store, "undated", deadline=None)
    clone_claim(store, "missing", evidence=False)
    clone_claim(store, "indirect")
    with store.connect() as db:
        db.execute("UPDATE evidence SET support_type='INDIRECT' WHERE claim_id='indirect'")
    assert len(store.search_claims("", quality="verified_current")) == 1
    assert {row["id"] for row in store.search_claims("", quality="needs_refresh")} == {"stale", "invalid"}
    assert {row["id"] for row in store.search_claims("", quality="missing_evidence")} == {"missing", "indirect"}
    stats = store.stats()
    assert stats["verified_current_claims"] == 1
    assert stats["stale_claims"] == 2
    assert stats["invalid_deadlines"] == 1
    assert stats["undated_claims"] == 1
    assert stats["claims_missing_direct_evidence"] == 2
    assert stats["measured_at"]


def test_quality_filter_runs_before_candidate_and_result_limits(store):
    with store.connect() as db:
        row = dict(db.execute("SELECT * FROM claims LIMIT 1").fetchone())
        original = row["id"]
        row.update(valid_until="2000-01-01", updated_at="2099-01-01")
        values = []
        for index in range(1005):
            row["id"] = f"stale-{index}"
            values.append(list(row.values()))
        db.executemany(f"INSERT INTO claims({','.join(row)}) VALUES({','.join('?' for _ in row)})", values)
    assert [row["id"] for row in store.search_claims("", limit=1, quality="verified_current")] == [original]


def test_deadline_offsets_compare_instants_in_stats_and_recall(store, monkeypatch):
    import app.research.knowledge as module
    monkeypatch.setattr(module, "now_iso", lambda: "2026-10-30T12:00:00+00:00")
    # 11:00 UTC is past; its local date/time sorts after 12:00.
    with store.connect() as db:
        db.execute("UPDATE knowledge SET valid_until='2026-10-30T13:00:00+02:00'")
        db.execute("UPDATE claims SET valid_until='2026-10-30T13:00:00+02:00'")
    assert store.stats()["stale"] == 1
    assert store.stats()["stale_claims"] == 1
    assert len(store.stale_entries()) == 1
    assert store.search_claims("", quality="verified_current") == []


def test_invalid_deadline_cannot_break_retrieval_or_look_fresh(store):
    with store.connect() as db:
        db.execute("UPDATE claims SET valid_until='not a date'")
    assert store.search_claims("", quality="needs_refresh")[0]["stale"] is True
    assert is_stale("not a date") is True
    assert is_stale("2026-10-30T11:00:00", datetime(2026, 10, 30, 12, tzinfo=UTC))


def test_unknown_quality_filter_is_rejected(store):
    with pytest.raises(ValueError, match="quality filter"):
        store.search_claims("", quality="made-up")


def test_conflicted_claims_cannot_appear_verified_and_current(store):
    clone_claim(store, "alternative")
    with store.connect() as db:
        original = db.execute("SELECT id FROM claims WHERE id<>'alternative'").fetchone()[0]
        db.execute("INSERT INTO claim_conflicts(claim_id,conflicting_claim_id,detected_at,reason,confidence) "
                   "VALUES(?,?,'2026-10-30','contradiction',0.9)", (original, "alternative"))
    assert store.search_claims("", quality="verified_current") == []
    assert len(store.search_claims("", quality="conflicts")) == 2
    assert store.stats()["verified_current_claims"] == 0


def test_missing_evidence_view_includes_claims_whose_verification_was_withdrawn(store):
    clone_claim(store, "withdrawn", evidence=False)
    with store.connect() as db:
        db.execute("UPDATE claims SET status='UNVERIFIED',confidence=0 WHERE id='withdrawn'")
    assert [row["id"] for row in store.search_claims("", quality="missing_evidence")] == ["withdrawn"]
    assert store.stats()["claims_missing_direct_evidence"] == 1


@pytest.mark.parametrize("deadline,current", [
    ("2026-10-30T13:00:00+02:00", False),
    ("2026-10-30T10:00:00-03:00", True),
    ("invalid", False),
])
def test_answer_sources_and_query_cache_use_the_same_deadline_rule(store, monkeypatch, deadline, current):
    import app.research.knowledge as module
    monkeypatch.setattr(module, "now_iso", lambda: "2026-10-30T12:00:00+00:00")
    with store.connect() as db:
        db.execute("UPDATE claims SET valid_until=?", (deadline,))
        row = db.execute("SELECT id,run_id FROM knowledge LIMIT 1").fetchone()
    assert bool(store.supported_sources_for_answer(row["id"], row["run_id"])) is current
    store.cache_query("check", "thread queue", "fixture", [{"title": "Reference"}], deadline)
    assert (store.get_cached_query("check") is not None) is current
    report = store.optimize()
    assert report["expired_query_cache_removed"] == (0 if current else 1)
