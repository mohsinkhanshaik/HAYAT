from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.evaluation.manager import EvaluationManager
from app.learning.manager import LearningManager
from app.memory.database import MemoryDatabase
from app.memory.embeddings import EmbeddingProvider, OpenAICompatibleEmbeddingProvider, content_hash
from app.memory.retrieval import RetrievalIndex
from app.models.intelligent_router import IntelligentModelRouter, RouteRequest
from app.session import SessionState
from app.training.pipeline import TrainingDataPipeline


class FakeEmbeddings(EmbeddingProvider):
    name = "fake"; locality = "local"
    def __init__(self): super().__init__("semantic"); self.available = True; self.dimension = 3; self.calls = 0
    def embed(self, texts):
        self.calls += 1
        vectors = []
        for text in texts:
            lower = text.lower(); vectors.append([float("gpu" in lower or "collective" in lower), float("container" in lower), float("network" in lower)])
        return vectors


def test_local_embeddings_cache_queries_and_cloud_requires_opt_in():
    provider = FakeEmbeddings(); first = provider.embed_query("GPU collectives"); second = provider.embed_query("GPU collectives")
    assert first == second and content_hash("  GPU   collectives ") == content_hash("GPU collectives")
    cloud = OpenAICompatibleEmbeddingProvider("https://example.com/v1", "cloud-model", allow_cloud=False)
    assert not cloud.available and cloud.locality == "cloud"


def test_cross_object_retrieval_reuses_content_hash(tmp_path: Path):
    database = MemoryDatabase(tmp_path / "memory.db"); database.initialize(); provider = FakeEmbeddings()
    index = RetrievalIndex(database, provider, project_root=tmp_path)
    assert index.upsert("semantic", "1", "NCCL", "NCCL handles GPU collective communication.")
    assert not index.upsert("semantic", "1", "NCCL", "NCCL handles GPU collective communication.")
    assert index.search("communication among graphics processors")[0]["title"] == "NCCL"


def test_cross_object_full_text_index_tracks_upserts(tmp_path: Path):
    database = MemoryDatabase(tmp_path / "memory.db"); database.initialize()
    index = RetrievalIndex(database, None, project_root=tmp_path)
    index.upsert("project_document", "old-doc", "Atlas notes", "Atlas uses the Kestrel scheduler.")
    index.upsert("project_document", "old-doc", "Atlas notes", "Atlas now uses the Zephyr scheduler.")
    with database.connect() as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "retrieval_objects_fts" not in tables:
            import pytest
            pytest.skip("This SQLite build does not provide FTS5")
        stale = db.execute(
            "SELECT rowid FROM retrieval_objects_fts "
            "WHERE retrieval_objects_fts MATCH 'kestrel'"
        ).fetchall()
    assert stale == []
    assert index.search("Zephyr scheduler")[0]["source_id"] == "old-doc"


def test_memory_embedding_cache_reuses_duplicate_content(tmp_path: Path):
    database = MemoryDatabase(tmp_path / "memory.db"); database.initialize(); provider = FakeEmbeddings(); database.embedding_provider = provider
    database.store_memory("semantic", "NCCL handles GPU collectives", source="test")
    database.store_memory("episodic", "NCCL handles GPU collectives", source="test")
    assert provider.calls == 1


def test_adaptive_router_uses_only_verified_outcomes(tmp_path: Path):
    rows = [{"model": "fast", "provider": "ollama", "capabilities": ["completion"], "loaded": True, "warmth": "warm", "size_bytes": 1, "benchmark_score": {"coding_accuracy": .6, "mean_latency_seconds": .1}}, {"model": "deep", "provider": "ollama", "capabilities": ["completion"], "loaded": False, "warmth": "cold", "size_bytes": 1, "benchmark_score": {"coding_accuracy": .9, "mean_latency_seconds": 1}}]
    storage = SimpleNamespace(model_inventory=lambda: {"models": rows}); model_router = SimpleNamespace(providers={})
    router = IntelligentModelRouter(storage, model_router, tmp_path / "routing.db")
    router.record_outcome("coding", "fast", True, 50, {}, verified=False)
    assert router.status()["verified_outcomes"] == 0
    router.record_outcome("coding", "deep", True, 500, {"passed": True}, verified=True)
    assert router.route(RouteRequest("coding", modes=("DEEP",))).primary == "deep"
    assert router.route(RouteRequest("windows", complexity="complex")).deterministic


def test_simple_conversation_prefers_measured_latency_until_reasoning_is_needed(tmp_path: Path):
    rows = [
        {"model": "small-fast", "provider": "ollama", "capabilities": ["completion"],
         "loaded": True, "size_bytes": 1, "benchmark_score": {
             "conversation_accuracy": .78, "reasoning_accuracy": .55, "mean_latency_seconds": .15}},
        {"model": "large-deep", "provider": "ollama", "capabilities": ["completion"],
         "loaded": False, "size_bytes": 1, "benchmark_score": {
             "conversation_accuracy": 1.0, "reasoning_accuracy": 1.0, "mean_latency_seconds": 2.5}},
    ]
    storage = SimpleNamespace(model_inventory=lambda: {"models": rows})
    router = IntelligentModelRouter(storage, SimpleNamespace(providers={}), tmp_path / "routing.db")
    quick = router.route(RouteRequest("conversation", complexity="simple", latency_budget_ms=1200))
    deep = router.route(RouteRequest("reasoning", complexity="complex", modes=("DEEP",)))
    assert quick.primary == "small-fast"
    assert deep.primary == "large-deep"


def test_comparable_non_qwen_model_wins_without_removing_qwen_fallback(tmp_path: Path):
    rows = [
        {"model": "qwen3.5:latest", "provider": "ollama", "capabilities": ["completion"],
         "loaded": False, "size_bytes": 1, "benchmark_score": {
             "conversation_accuracy": 1.0, "mean_latency_seconds": .90}},
        {"model": "gemma4:latest", "provider": "ollama", "capabilities": ["completion"],
         "loaded": False, "size_bytes": 1, "benchmark_score": {
             "conversation_accuracy": 1.0, "mean_latency_seconds": .94}},
    ]
    router = IntelligentModelRouter(
        SimpleNamespace(model_inventory=lambda: {"models": rows}),
        SimpleNamespace(providers={}), tmp_path / "routing.db",
    )
    decision = router.route(RouteRequest("conversation", complexity="simple", latency_budget_ms=1200))
    assert decision.primary == "gemma4:latest"
    assert "qwen3.5:latest" in decision.fallbacks
    assert "non-Qwen" in decision.reason


def test_hayat_identity_inherits_base_evidence_and_wins_only_close_ties(tmp_path: Path):
    rows = [
        {"model": "hayat:latest", "provider": "ollama", "capabilities": ["completion"],
         "loaded": False, "size_bytes": 10},
        {"model": "gemma4:latest", "provider": "ollama", "capabilities": ["completion"],
         "loaded": False, "size_bytes": 10, "benchmark_score": {
             "conversation_accuracy": 1.0, "reasoning_accuracy": 1.0,
             "mean_latency_seconds": 1.0}},
        {"model": "specialist", "provider": "ollama", "capabilities": ["completion"],
         "loaded": False, "size_bytes": 10, "benchmark_score": {
             "conversation_accuracy": .2, "reasoning_accuracy": 1.0,
             "mean_latency_seconds": .05}},
        {"model": "qwen3.5:latest", "provider": "ollama", "capabilities": ["completion"],
         "loaded": True, "warmth": "warm", "loaded_vram_bytes": 1, "size_bytes": 10,
         "benchmark_score": {"conversation_accuracy": 1.0, "reasoning_accuracy": 1.0,
                             "mean_latency_seconds": .05}},
    ]
    model_router = SimpleNamespace(
        providers={}, config={"identity_model": {
            "model": "hayat:latest", "base_model": "gemma4:latest",
        }},
    )
    router = IntelligentModelRouter(
        SimpleNamespace(model_inventory=lambda: {"models": rows}),
        model_router, tmp_path / "routing.db",
    )
    conversation = router.route(RouteRequest("conversation", complexity="complex"))
    assert conversation.primary == "hayat:latest"
    assert "identity model" in conversation.reason
    reasoning = router.route(RouteRequest("reasoning", complexity="complex", modes=("DEEP",)))
    assert reasoning.primary == "qwen3.5:latest", "specialist routing must remain task-first"


def test_mastery_hints_exam_and_lab_are_evidence_based(tmp_path: Path):
    learning = LearningManager(tmp_path / "learning.db"); session = SessionState()
    lesson = learning.handle("Teach me CUDA streams", session)
    assert "CUDA" in lesson and "prerequisite" in lesson.lower()
    question = learning.handle("Test me on CUDA streams", session); assert "no hints" in question.lower()
    assert "Small hint" in learning.handle("small hint", session)
    learning.handle("asynchronous work requires dependency synchronization", session)
    lab = learning.record_lab("CUDA streams", completed=True, duration_seconds=100, hints_used=1, errors=0, recovered=True, verified=True)
    state = learning.topic("CUDA streams")
    assert lab["score"] > .7 and state["correct_answers"] >= 1 and state["next_review"]


def test_training_stages_redact_dedupe_and_prevent_split_leakage(tmp_path: Path):
    pipeline = TrainingDataPipeline(tmp_path / "training")
    raw = pipeline.ingest("routing", {"input": "hi", "api_key": "sk-secret-value-123456"}, provenance={"source": "test"})
    assert raw["state"] == "RAW" and raw["redactions"] >= 1
    validated = pipeline.validate(raw["id"], deterministic_correct=True); assert validated["state"] == "VALIDATED"
    ready = pipeline.promote(raw["id"]); assert ready["state"] in {"TRAINING_READY", "HELD_OUT"}
    duplicate = pipeline.ingest("routing", {"input": "hi", "api_key": "sk-another-secret-123456"}, provenance={"source": "test"})
    assert duplicate["duplicate"] and pipeline.validate_dataset()["passed"]
    assert pipeline.estimate("router")["safe_to_start"] is False


def test_evaluation_status_declares_golden_gate(tmp_path: Path):
    manager = EvaluationManager(tmp_path)
    status = manager.status()
    assert status["golden_gate"] == "required" and len(status["golden_cases"]) >= 6
    score = manager.score_task(intent_correct=True, plan_quality=1, tool_correctness=1, final_state_success=True, verification_correct=True, latency_ms=100, unnecessary_actions=0, user_correction_required=False)
    assert score["score"] > .95


def test_sqlite_helpers_release_windows_file_handles(tmp_path: Path):
    learning_path = tmp_path / "learning.db"
    learning = LearningManager(learning_path)
    learning.handle("Teach me CUDA streams", SessionState())
    learning_path.replace(tmp_path / "learning-moved.db")

    pipeline = TrainingDataPipeline(tmp_path / "training")
    pipeline.status()
    pipeline.registry.replace(tmp_path / "training-registry-moved.db")
