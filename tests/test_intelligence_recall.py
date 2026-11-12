import json
import time

import pytest

from app.memory.database import MemoryDatabase
from app.memory.retrieval import RetrievalIndex
from tests.test_memory_dna import CountingEmbeddings


@pytest.fixture
def memory(tmp_path):
    database = MemoryDatabase(tmp_path / "recall.db")
    database.initialize()
    return database


def chain(memory):
    a = memory.store_memory("semantic", "Atlas deployment uses Zephyr signing.", .95, "owner")
    b = memory.store_memory("semantic", "The alternative schedule is not approved.", .8, "owner")
    c = memory.store_memory("semantic", "Recovery requires an offline manifest.", .8, "owner")
    with memory.connect() as db:
        db.execute("DELETE FROM memory_links")
    memory.link_memories(a, b, "contradicts", 1)
    memory.link_memories(b, c, "supports", 1)
    return a, b, c


def test_contradiction_survives_multiple_graph_hops(memory):
    a, b, c = chain(memory)
    linked = next(r for r in memory.associations(a, max_depth=2) if r["memory_id"] == c)
    assert linked["path"] == [a, b, c]
    assert linked["relation"] == "supports" and linked["contradiction"] is True


def test_graph_cannot_reintroduce_tampered_memory_or_use_it_as_a_bridge(memory):
    a, b, c = chain(memory)
    with memory.connect() as db:
        db.execute("UPDATE memories SET content='tampered replacement' WHERE id=?", (b,))
    result = memory.retrieve("Atlas deployment Zephyr", limit=10)
    assert b not in {r["id"] for r in result}
    seed = next(r for r in result if r["id"] == a)
    expanded = memory._expand_dna([seed], memory_type=None, session_id=None, include_foreign_transient=False,
                                 seed_limit=1, max_depth=3, max_nodes=20, edge_decay=.9, minimum_score=0)
    assert {r["id"] for r in expanded} == {a}


def test_cross_session_transient_cannot_bridge_graph_recall(memory):
    a, b, c = chain(memory)
    with memory.connect() as db:
        db.execute("UPDATE memories SET state_class='TRANSIENT',session_id='old-session' WHERE id=?", (b,))
    seed = next(r for r in memory.retrieve("Atlas deployment Zephyr", strategy="lexical") if r["id"] == a)
    expanded = memory._expand_dna([seed], memory_type=None, session_id="new-session", include_foreign_transient=False,
                                 seed_limit=1, max_depth=3, max_nodes=20, edge_decay=.9, minimum_score=0)
    assert {r["id"] for r in expanded} == {a}


def test_dense_graph_has_bounded_traversal_work(memory):
    nodes = [memory.store_memory("semantic", f"Unique measurement record {i}", .7) for i in range(32)]
    with memory.connect() as db:
        db.execute("DELETE FROM memory_links")
        db.executemany("INSERT INTO memory_links(source_id,target_id,relation,weight,evidence_json,created_at,last_reinforced_at) VALUES(?,?,?,?,?,?,?)",
                       [(a, b, "supports", .8, "{}", "2026-11-11", "2026-11-11") for a in nodes for b in nodes if a < b])
    started = time.monotonic()
    result = memory.associations_many(nodes[:4], max_depth=4, limit=10)
    assert len(result) <= 10 and all(len(r["path"]) == len(set(r["path"])) for r in result)
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("change", ["provider", "model", "version", "hash", "dimension", "nan", "broken_json"])
def test_cross_object_recall_rejects_incompatible_vectors(memory, tmp_path, change):
    provider = CountingEmbeddings()
    index = RetrievalIndex(memory, provider, project_root=tmp_path)
    index.upsert("project_document", "atlas", "Atlas", "Atlas deployment requires a signed manifest.")
    assert index.search("Atlas deployment")[0]["retrieval"]["neural_available"]
    with memory.connect() as db:
        statements = {
            "provider": "UPDATE retrieval_object_vectors SET provider='other'",
            "model": "UPDATE retrieval_object_vectors SET model='other'",
            "version": "UPDATE retrieval_object_vectors SET embedding_version='old'",
            "hash": "UPDATE retrieval_object_vectors SET content_hash='stale'",
            "dimension": "UPDATE retrieval_object_vectors SET dimension=42",
            "nan": "UPDATE retrieval_object_vectors SET vector_json='[NaN,0,0]'",
            "broken_json": "UPDATE retrieval_object_vectors SET vector_json='broken'",
        }
        db.execute(statements[change])
    result = index.search("Atlas deployment")
    assert result[0]["content"].startswith("Atlas")
    assert result[0]["retrieval"]["neural_available"] is False
    assert result[0]["retrieval"]["neural"] == result[0]["retrieval"]["hashed"]
    assert "vector_json" not in json.dumps(result)


def test_lexical_index_recall_does_not_wake_embedding_service(memory, tmp_path):
    provider = CountingEmbeddings()
    index = RetrievalIndex(memory, provider, project_root=tmp_path)
    index.upsert("project_document", "atlas", "Atlas", "Atlas deployment requires a signed manifest.")
    before = provider.calls
    assert index.search("Atlas deployment", strategy="lexical")
    assert provider.calls == before
    assert index.search("Atlas", limit=0) == index.search("   ") == []
    with pytest.raises(ValueError):
        index.search("Atlas", strategy="invented")


def test_ineligible_source_update_removes_obsolete_recall(memory, tmp_path):
    index = RetrievalIndex(memory, None, project_root=tmp_path)
    index.upsert("project_document", "atlas", "Atlas", "Atlas deployment requires a signed manifest.")
    assert index.search("Atlas")
    assert not index.upsert("project_document", "atlas", "Atlas", "password=must-not-be-retained-in-retrieval")
    assert index.search("Atlas") == []
