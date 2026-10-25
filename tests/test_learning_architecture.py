from __future__ import annotations

from types import SimpleNamespace

from app.learning.architecture import LearningArchitecture
from app.learning.continuous import ContinuousLearner
from app.learning.manager import LearningManager
from app.memory.database import MemoryDatabase
from app.research.knowledge import KnowledgeStore
from app.training.pipeline import TrainingDataPipeline


def _runtime(tmp_path):
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    knowledge = KnowledgeStore(tmp_path / "knowledge.db")
    knowledge.initialize()
    learning = LearningManager(tmp_path / "teaching.db")
    learner = ContinuousLearner(root=tmp_path / "continuous")
    training = TrainingDataPipeline(tmp_path / "training")
    return SimpleNamespace(
        memory=memory,
        research=SimpleNamespace(knowledge=knowledge),
        learning=learning,
        learner=learner,
        training=training,
        apprenticeship=None,
        compaction=None,
        skills=SimpleNamespace(skills={}),
    )


def test_inventory_maps_separate_learning_layers_and_caches(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.memory.store_memory("semantic", "Verified local preference", .8, "test")
    architecture = LearningArchitecture(runtime, artifact=tmp_path / "report.json")

    first = architecture.inventory(refresh=True)
    second = architecture.inventory()

    assert set(first["layers"]) == {
        "durable_memory", "researched_knowledge", "teaching_progress",
        "background_learning", "procedural_learning", "model_training",
    }
    assert first["layers"]["durable_memory"]["records"] == 1
    assert first["recall_health"]["healthy"] is True
    assert second["cache"] == "hit"


def test_alignment_keeps_training_and_background_authority_bounded(tmp_path):
    architecture = LearningArchitecture(_runtime(tmp_path), artifact=tmp_path / "report.json")

    report = architecture.alignment(refresh=True)

    assert report["aligned"] is True
    assert report["blockers"] == []
    assert report["policy"]["private_training"].startswith("requires")
    assert report["policy"]["self_permission_changes"] == "not approved"
    writes = architecture.inventory()["layers"]["background_learning"]["allowed_writes"]
    assert writes == ["knowledge_research"]


def test_safe_optimization_preserves_learned_records_and_writes_audit(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.memory.store_memory("learning", "Keep this learned record", .9, "test")
    artifact = tmp_path / "architecture.json"
    architecture = LearningArchitecture(runtime, artifact=artifact)

    report = architecture.optimize()

    assert report["safe"] is True
    assert report["learned_records_deleted"] == 0
    assert report["evidence_deleted"] == 0
    assert runtime.memory.stats()["memories"]["learning"] == 1
    assert artifact.is_file()
    assert all(item["optimized"] for item in report["databases"].values())

