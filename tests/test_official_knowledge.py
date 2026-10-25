from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.research.fetcher import FetchedResource
from app.research.knowledge import KnowledgeStore
from app.research.official_pack import refresh_official_pack


URL = "https://docs.python.org/3/library/queue.html"
EXCERPT = "The queue module implements multi-producer, multi-consumer queues."
ENTRY = {"url": URL, "topic": "Python thread queue", "facts": [{"claim": EXCERPT, "excerpt": EXCERPT}]}


def test_hugging_face_reference_allowlist_excludes_user_repositories():
    from app.research.official_pack import _official_url
    assert _official_url("https://huggingface.co/docs/peft/main/en/package_reference/peft_model")
    assert not _official_url("https://huggingface.co/random-user/random-model")
    assert not _official_url("https://huggingface.co.evil.test/docs/peft")


def test_open_model_references_exclude_stale_disputed_and_uncited_claims(tmp_path, monkeypatch):
    from app.models.hayat_open import knowledge_references
    path = tmp_path / "data/knowledge/knowledge.db"
    path.parent.mkdir(parents=True)
    path.touch()
    valid = {"status": "SUPPORTED", "claim_text": "Thread queue reference", "stale": False,
             "evidence": [{"final_url": URL, "excerpt": EXCERPT}]}
    monkeypatch.setattr(KnowledgeStore, "search_claims", lambda *args, **kwargs: [
        {**valid, "stale": True}, {**valid, "has_unresolved_conflict": True},
        {**valid, "status": "DISPUTED"}, {**valid, "evidence": []}, valid])
    notes = knowledge_references("thread queue", tmp_path)
    assert len(notes) == 1 and URL in notes[0]


def test_open_references_do_not_pad_an_exact_match_with_a_partial_topic_match(tmp_path, monkeypatch):
    from app.models.hayat_open import knowledge_references
    path = tmp_path / "data/knowledge/knowledge.db"
    path.parent.mkdir(parents=True)
    path.touch()
    def claim(text):
        return {"status": "SUPPORTED", "claim_text": text, "stale": False,
                "evidence": [{"final_url": URL, "excerpt": text}]}
    monkeypatch.setattr(KnowledgeStore, "search_claims", lambda *args, **kwargs: [
        claim("Moon Earth natural satellite"), claim("Moon Earth natural mythology")])
    references = knowledge_references("Moon Earth natural satellite", tmp_path)
    assert len(references) == 1 and "mythology" not in references[0]


def fetcher(text=EXCERPT, *, days=0, final_url=URL):
    content = f"<html><title>Python queue</title><main><p>{text}</p></main></html>".encode()
    resource = FetchedResource(URL, final_url, 200, {"content-type": "text/html"}, content, 0,
                               (datetime.now(UTC) + timedelta(days=days)).isoformat(), hashlib.sha256(content).hexdigest())
    return SimpleNamespace(fetch=lambda *args, **kwargs: resource)


def knowledge(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    return store


def test_import_preserves_verified_provenance_and_recall(tmp_path):
    store = knowledge(tmp_path)
    report = refresh_official_pack([ENTRY], store, fetcher())
    assert report["supported_claims"] == 1
    assert report["failed_sources"] == 0
    item = report["entries"][0]
    assert len(item["content_hash"]) == 64
    assert item["retrieved_at"] < item["valid_until"]
    results = store.search("Python thread queue")
    assert results and EXCERPT in results[0]["content"]
    claims = store.search_claims("multi-producer queues")
    assert claims[0]["status"] == "SUPPORTED"


def test_specific_query_coverage_outranks_a_repeated_generic_entity():
    import json
    from app.memory.database import text_vector
    rows = [{"id": 1, "topic": "Python Python Python release", "content": "Python version Python release",
             "vector_json": json.dumps(text_vector("Python")), "entities_json": '["Python"]',
             "valid_until": None, "successful_uses": 0, "failed_uses": 0, "confidence": .95,
             "updated_at": "2026-10-09"},
            {"id": 2, "topic": "Python statistics median", "content": "Median is the middle value",
             "vector_json": json.dumps(text_vector("middle value")), "entities_json": '[]',
             "valid_until": None, "successful_uses": 0, "failed_uses": 0, "confidence": .70,
             "updated_at": "2026-10-08"}]
    ranked = KnowledgeStore._rank_rows("Python median statistics", rows, "content", "topic", entity_weight=True)
    assert ranked[0]["id"] == 2


def test_refresh_reuses_knowledge_and_renews_expiry(tmp_path):
    store = knowledge(tmp_path)
    old = refresh_official_pack([ENTRY], store, fetcher(days=-8))["entries"][0]
    new = refresh_official_pack([ENTRY], store, fetcher())["entries"][0]
    assert old["knowledge_id"] == new["knowledge_id"]
    assert old["valid_until"] < new["valid_until"]
    with store.connect() as db:
        rows = db.execute("SELECT valid_until FROM knowledge").fetchall()
    assert len(rows) == 1
    assert rows[0]["valid_until"] == new["valid_until"]


def test_changed_source_cannot_refresh_a_stale_fact(tmp_path):
    store = knowledge(tmp_path)
    old = refresh_official_pack([ENTRY], store, fetcher(days=-8))["entries"][0]
    report = refresh_official_pack([ENTRY], store, fetcher("This page no longer contains the cited text."))
    assert report["imported_sources"] == 0
    assert report["failed_sources"] == 1
    with store.connect() as db:
        assert db.execute("SELECT valid_until FROM knowledge").fetchone()[0] == old["valid_until"]


def test_entire_entry_must_verify_before_any_claim_is_stored(tmp_path):
    store = knowledge(tmp_path)
    entry = {**ENTRY, "facts": [*ENTRY["facts"], {"claim": "Made up fact", "excerpt": "Never published here"}]}
    assert refresh_official_pack([entry], store, fetcher())["failed_sources"] == 1
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0


def test_unapproved_source_or_redirect_is_rejected(tmp_path):
    store = knowledge(tmp_path)
    unexpected = SimpleNamespace(fetch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not fetch")))
    report = refresh_official_pack([{**ENTRY, "url": "https://example.com/data"}], store, unexpected)
    assert "allowlist" in report["entries"][0]["error"]
    report = refresh_official_pack([ENTRY], store, fetcher(final_url="https://www.python.org/"))
    assert "redirected" in report["entries"][0]["error"]


def test_older_claim_can_be_found_after_more_than_one_thousand_newer_claims(tmp_path):
    store = knowledge(tmp_path)
    refresh_official_pack([ENTRY], store, fetcher(days=-1))
    with store.connect() as db:
        original = dict(db.execute("SELECT * FROM claims").fetchone())
        columns = list(original)
        records = []
        for number in range(1005):
            row = {**original, "id": f"unrelated-{number}", "claim_text": "Unrelated garden flowers bloom.",
                   "topic": "Gardening", "vector_json": "[]", "updated_at": datetime.now(UTC).isoformat()}
            records.append(tuple(row[key] for key in columns))
        db.executemany(f"INSERT INTO claims({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", records)
    assert store.search_claims("multi-producer queues", limit=1)[0]["id"] == original["id"]
    assert store.search_claims("multi-producer queues", lexical_only=True)[0]["id"] == original["id"]
    assert store.search_claims("xylophone_unknown", lexical_only=True) == []


def test_distinct_api_scopes_do_not_create_a_false_contradiction(tmp_path):
    store = knowledge(tmp_path)
    a = "Make the path absolute, resolving any symlinks."
    b = "Make the path absolute, without normalization or resolving symlinks."
    entry = {**ENTRY, "facts": [{"claim": a, "excerpt": a, "scope": "Path.resolve"},
                                {"claim": b, "excerpt": b, "scope": "Path.absolute"}]}
    report = refresh_official_pack([entry], store, fetcher(a + " " + b))
    assert report["supported_claims"] == 2


def test_exact_claim_refresh_renews_metadata_even_when_similarity_misses(tmp_path, monkeypatch):
    store = knowledge(tmp_path)
    refresh_official_pack([ENTRY], store, fetcher(days=-8))
    monkeypatch.setattr(store, "_find_similar_claim", lambda *_: None)
    changed = {**ENTRY, "facts": [{**ENTRY["facts"][0], "scope": "Python queues"}]}
    report = refresh_official_pack([changed], store, fetcher())
    with store.connect() as db:
        rows = db.execute("SELECT scope,valid_until FROM claims").fetchall()
    assert len(rows) == 1 and rows[0]["scope"] == "Python queues"
    assert rows[0]["valid_until"] == report["entries"][0]["valid_until"]


def test_html_inline_spacing_does_not_invalidate_a_real_citation(tmp_path):
    store = knowledge(tmp_path)
    report = refresh_official_pack([ENTRY], store, fetcher(EXCERPT[:-1] + " ."))
    assert report["supported_claims"] == 1


def test_typographic_normalization_does_not_drop_a_negation(tmp_path):
    store = knowledge(tmp_path)
    text = EXCERPT.replace("implements", "does not implement")
    assert refresh_official_pack([ENTRY], store, fetcher(text))["failed_sources"] == 1


def test_evidence_audit_demotes_uncited_claims_preserves_history_and_expires_summary(tmp_path):
    store = knowledge(tmp_path)
    refresh_official_pack([ENTRY], store, fetcher())
    with store.connect() as db:
        db.execute("DELETE FROM evidence")
    assert store.stats()["unsupported_claims"] == 1
    assert store.audit_claim_evidence()["missing_evidence"] == 1
    result = store.audit_claim_evidence(repair=True)
    assert result["demoted"] == 1 and result["expired_summaries"] == 1
    assert store.search_claims("Python queues") == []
    assert store.stats()["unsupported_claims"] == 0
    assert store.stats()["unverified_claims"] == 1
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
        assert db.execute("SELECT old_status,new_status FROM claim_history").fetchone()[:] == ("SUPPORTED", "UNVERIFIED")
        assert db.execute("SELECT valid_until FROM knowledge").fetchone()[0] <= datetime.now(UTC).isoformat()
    assert store.audit_claim_evidence(repair=True)["demoted"] == 0
def test_local_knowledge_recall_does_not_require_web_extractor_dependencies(tmp_path):
    import subprocess
    import sys
    from app.config import PROJECT_ROOT

    code = ("import sys; sys.modules['bs4'] = None; "
            "from app.research.knowledge import KnowledgeStore; "
            "store = KnowledgeStore(sys.argv[1]); store.initialize(); "
            "assert store.search_claims('median', lexical_only=True) == []")
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "isolated-knowledge.db")],
                            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr

