from app.memory.database import MemoryDatabase, cosine_similarity, text_vector


class ExplodingIdentifierEmbeddings:
    name = "test"
    model = "must-not-run"
    identifier = "test/must-not-run"
    version = "1"
    available = True

    def embed_documents(self, texts):
        raise AssertionError("exact identifiers must not wake the neural provider")


def test_hybrid_memory_retrieval_and_vectors(tmp_path):
    memory = MemoryDatabase(tmp_path / "vectors.db")
    memory.initialize()
    target = memory.store_memory("semantic", "Playwright browser automation uses DOM selectors", 0.8)
    memory.store_memory("semantic", "SQLite stores task history", 0.8)
    results = memory.retrieve("browser selectors", "semantic")
    assert results[0]["id"] == target
    assert len(results) == 1
    assert cosine_similarity(text_vector("browser selectors"), text_vector("browser selectors")) > 0.99


def test_irrelevant_memories_are_not_returned(tmp_path):
    memory = MemoryDatabase(tmp_path / "threshold.db")
    memory.initialize()
    memory.store_memory("semantic", "oranges and bananas", 0.5)
    assert memory.retrieve("PowerShell registry automation", "semantic") == []


def test_exact_identifier_recall_uses_fast_deterministic_path(tmp_path):
    memory = MemoryDatabase(tmp_path / "identifiers.db")
    memory.initialize()
    memory.store_memory("semantic", "RFC 8785 defines JSON canonicalization", 0.9)
    memory.embedding_provider = ExplodingIdentifierEmbeddings()

    results = memory.retrieve("What does RFC 8785 require?", strategy="hybrid")

    assert results and "RFC 8785" in results[0]["content"]
    assert results[0]["retrieval"]["neural_available"] is False
