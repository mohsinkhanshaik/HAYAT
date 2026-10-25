"""Optional local embeddings must not lose already committed text memory."""
import httpx
import pytest

from app.memory.database import MemoryDatabase
from app.memory.embeddings import EmbeddingProvider
from app.memory.retrieval import RetrievalIndex


class LocalEmbeddings(EmbeddingProvider):
    name = 'local'
    def __init__(self):
        super().__init__('local')
        self.available = True
        self.failure = None
    def embed(self, texts):
        if self.failure:
            raise self.failure('private input or credential must not enter the log')
        return [[1.0, 0.0] for _ in texts]


@pytest.mark.parametrize('failure', [httpx.ConnectError, httpx.ReadTimeout, RuntimeError, PermissionError])
def test_embedding_outage_preserves_memory_and_local_recall(tmp_path, caplog, failure):
    database = MemoryDatabase(tmp_path / 'memory.db')
    database.initialize()
    provider = LocalEmbeddings()
    database.embedding_provider = provider
    provider.failure = failure
    memory_id = database.store_memory('episodic', 'The blueberry project budget is recorded locally.', source='owner')
    assert database.retrieve('blueberry budget')[0]['id'] == memory_id
    with database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM memory_vectors').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM memory_neural_vectors').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM embedding_cache').fetchone()[0] == 0
    assert 'retaining local lexical memory' in caplog.text
    assert 'private input or credential' not in caplog.text
    # Recovery uses the same local provider, with no cloud fallback or fabricated vector.
    provider.failure = None
    database.store_memory('episodic', 'The blueberry project budget is recorded locally.', source='owner')
    with database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM memory_neural_vectors').fetchone()[0] == 1


def test_revised_documents_remain_searchable_without_stale_semantic_vectors(tmp_path):
    database = MemoryDatabase(tmp_path / 'memory.db')
    database.initialize()
    provider = LocalEmbeddings()
    index = RetrievalIndex(database, provider, project_root=tmp_path)
    assert index.upsert('document', 'budget', 'Project budget', 'The blueberry project originally bought copper wiring.')
    with database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM retrieval_object_vectors').fetchone()[0] == 1
    provider.failure = httpx.ConnectError
    assert index.upsert('document', 'budget', 'Project budget', 'The blueberry project now buys optical sensors instead.')
    results = index.search('optical sensors')
    assert results and 'optical sensors' in results[0]['content']
    with database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM retrieval_objects').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM retrieval_object_vectors').fetchone()[0] == 0


def test_model_disabled_runtime_never_inspects_embedding_servers(monkeypatch):
    from app.runtime import build_runtime
    def forbidden(*args):
        pytest.fail('Model-disabled runtime attempted an embedding probe')
    monkeypatch.setattr('app.memory.embeddings.EmbeddingRegistry.from_config', forbidden)
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        assert runtime.memory.embedding_provider is None
        assert runtime.embeddings.active is None
    finally:
        runtime.close()
