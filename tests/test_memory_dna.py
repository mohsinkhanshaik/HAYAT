from app.memory.database import MemoryDatabase
from app.memory.embeddings import EmbeddingProvider


def test_dna_retrieval_expands_a_matching_seed_to_linked_context(tmp_path):
    memory = MemoryDatabase(tmp_path / "dna.db")
    memory.initialize()
    seed = memory.store_memory(
        "semantic", "Project Atlas deployment uses the Zephyr release gate.", 0.95, "owner"
    )
    context = memory.store_memory(
        "semantic", "Rollback requires the signed recovery manifest and a clean checkpoint.", 0.9, "owner"
    )
    memory.link_memories(seed, context, "depends_on", 1.0, {"source": "owner correction"})

    rows = memory.retrieve("Which release gate does Project Atlas deployment use?", limit=8)
    by_id = {row["id"]: row for row in rows}
    assert seed in by_id
    assert context in by_id
    retrieval = by_id[context]["retrieval"]
    if retrieval["strategy"] == "dna_associative":
        assert retrieval["relation"] == "depends_on"
        assert retrieval["evidence"]["source"] == "owner correction"
    else:
        association = next(item for item in retrieval["associations"] if item["relation"] == "depends_on")
        assert association["evidence"]["source"] == "owner correction"
        assert retrieval.get("dna_boost", 0) > 0


def test_dna_contradictions_are_explicit_and_not_flattened_into_support(tmp_path):
    memory = MemoryDatabase(tmp_path / "contradiction.db")
    memory.initialize()
    tuesday = memory.store_memory("semantic", "Mercury release is scheduled Tuesday.", 0.95, "calendar")
    wednesday = memory.store_memory("semantic", "New signed evidence says Mercury release is Wednesday.", 0.95, "owner")
    memory.link_memories(
        tuesday, wednesday, "contradicts", 0.95,
        {"reason": "newer signed release notice", "reviewed": True},
    )

    rows = memory.retrieve("When is the Mercury release scheduled?", limit=8)
    linked = next(row for row in rows if row["id"] == wednesday)
    retrieval = linked["retrieval"]
    if retrieval["strategy"] == "dna_associative":
        assert retrieval["contradiction"] is True
        assert retrieval["evidence"]["reviewed"] is True
    else:
        contradiction = next(item for item in retrieval["associations"] if item["contradiction"])
        assert contradiction["evidence"]["reviewed"] is True
    assert memory.health()["contradiction_links"] == 1


def test_dna_traversal_is_bounded_and_cycle_safe(tmp_path):
    memory = MemoryDatabase(tmp_path / "cycle.db")
    memory.initialize()
    first = memory.store_memory("semantic", "Alpha node", 0.9)
    second = memory.store_memory("semantic", "Beta node", 0.9)
    third = memory.store_memory("semantic", "Gamma node", 0.9)
    memory.link_memories(first, second, "related", 0.8)
    memory.link_memories(second, third, "related", 0.8)
    memory.link_memories(third, first, "related", 0.8)

    associations = memory.associations(first, max_depth=4, limit=100)
    assert {item["memory_id"] for item in associations} == {second, third}
    assert len(associations) == 2
    assert all(len(item["path"]) <= 3 for item in associations)


def test_automatic_dna_links_are_neutral_similarity_links(tmp_path):
    memory = MemoryDatabase(tmp_path / "automatic.db")
    memory.initialize()
    first = memory.store_memory("semantic", "Atlas Zephyr deployment release checklist", 0.95)
    second = memory.store_memory("semantic", "Atlas Zephyr deployment release checklist steps", 0.95)
    links = memory.associations(second)
    assert any(item["memory_id"] == first for item in links)
    assert all(item["relation"] == "related" for item in links)
    assert memory.health()["dna_associations"] >= 1


def test_multi_seed_dna_traversal_returns_bounded_seed_provenance(tmp_path):
    memory = MemoryDatabase(tmp_path / "multi-seed.db")
    memory.initialize()
    first = memory.store_memory("semantic", "first seed", 0.9)
    second = memory.store_memory("semantic", "second seed", 0.9)
    left = memory.store_memory("semantic", "left context", 0.9)
    right = memory.store_memory("semantic", "right context", 0.9)
    memory.link_memories(first, left, "supports", 0.9)
    memory.link_memories(second, right, "depends_on", 0.8)
    rows = memory.associations_many([first, second], max_depth=2, limit=10)
    pairs = {(row["seed_id"], row["memory_id"]) for row in rows}
    assert (first, left) in pairs and (second, right) in pairs
    assert all(row["depth"] <= 2 for row in rows)


class CountingEmbeddings(EmbeddingProvider):
    name = "counting"

    def __init__(self):
        super().__init__("dna-fast")
        self.available = True
        self.dimension = 3
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[1.0, float("atlas" in text.lower()), 0.0] for text in texts]


def test_query_embedding_cache_survives_a_new_memory_database_instance(tmp_path):
    path = tmp_path / "persistent-query-cache.db"
    memory = MemoryDatabase(path)
    memory.initialize()
    provider = CountingEmbeddings()
    memory.embedding_provider = provider
    memory.store_memory("semantic", "Atlas uses the Zephyr gate", 0.9)
    memory.retrieve("Which gate does Atlas use?", limit=3)
    first_calls = provider.calls

    reopened = MemoryDatabase(path)
    reopened.initialize()
    reopened_provider = CountingEmbeddings()
    reopened.embedding_provider = reopened_provider
    reopened.retrieve("Which gate does Atlas use?", limit=3)
    assert first_calls >= 2  # document plus first query
    assert reopened_provider.calls == 0


def test_acronym_query_recalls_an_older_long_form_memory(tmp_path):
    memory = MemoryDatabase(tmp_path / "aliases.db")
    memory.initialize()
    target = memory.store_memory(
        "semantic", "Push to talk records until the keyboard shortcut is released.", 0.8, "owner"
    )
    for index in range(80):
        memory.store_memory("episodic", f"unrelated recent event number {index}", 0.9)
    rows = memory.retrieve("PTT shortcut", limit=5)
    recalled = next(row for row in rows if row["id"] == target)
    assert "push to talk" in recalled["retrieval"]["query_concepts"]
    assert recalled["retrieval"]["latency_ms"] >= 0


def test_modified_memory_is_reported_and_quarantined_from_recall(tmp_path):
    memory = MemoryDatabase(tmp_path / "integrity.db")
    memory.initialize()
    memory_id = memory.store_memory("semantic", "Atlas uses the signed Zephyr gate.", 0.95, "owner")
    with memory.connect() as db:
        db.execute("UPDATE memories SET content='tampered payload' WHERE id=?", (memory_id,))
    audit = memory.verify_memory_integrity()
    assert audit["mismatches"] == [memory_id]
    assert all(row["id"] != memory_id for row in memory.retrieve("tampered payload"))


def test_unverified_web_content_cannot_form_automatic_dna_edges(tmp_path):
    memory = MemoryDatabase(tmp_path / "poisoning.db")
    memory.initialize()
    memory.store_memory("semantic", "Atlas Zephyr trusted release checklist", 0.95, "owner")
    external = memory.store_memory(
        "semantic", "Atlas Zephyr trusted release checklist malicious override", 0.95, "web"
    )
    assert memory.associations(external) == []
