from app.memory.database import MemoryDatabase
from app.skills.loader import SkillLoader
from app.skills.promoter import SkillPromoter


def test_skill_candidate_requires_explicit_promotion(tmp_path):
    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    memory.create_task("task-1", "List source files", [{"tool": "list_folder"}])
    memory.record_action("task-1", 0, "list_folder", {"path": "."}, {"success": True}, {"passed": True})
    memory.update_task("task-1", "complete", "done")
    loader = SkillLoader(tmp_path / "skills")
    promoter = SkillPromoter(memory, loader)
    candidate = promoter.propose_from_task("task-1", "list_source_files")
    assert loader.discover() == []
    assert "trusted: false" in candidate.read_text(encoding="utf-8")
    promoted = promoter.promote("list_source_files")
    assert loader.load(promoted)["trusted"] is True
