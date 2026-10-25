from app.safety.permissions import PermissionManager
from app.skills.loader import SkillLoader
from app.skills.registry import SkillRegistry
from app.tools.filesystem import register_filesystem_tools
from app.tools.registry import ToolRegistry


def test_trusted_declarative_skill_executes(tmp_path):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "list_project.yaml").write_text("""
name: list_project
description: List one approved project folder safely.
inputs: [folder]
tools: [list_folder]
steps:
  - tool: list_folder
    arguments: {path: "{{folder}}", recursive: false}
success_criteria: [folder listed]
trusted: true
""", encoding="utf-8")
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_filesystem_tools(registry)
    skills = SkillRegistry(registry, SkillLoader(skill_dir))
    skills.reload()
    results = skills.execute("list_project", {"folder": "."})
    assert results[0].success


def test_untrusted_skill_cannot_run(tmp_path):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "unsafe.yaml").write_text("""
name: unsafe_skill
description: This skill is intentionally not trusted.
inputs: []
tools: [list_folder]
steps:
  - tool: list_folder
    arguments: {path: "."}
success_criteria: [listed]
trusted: false
""", encoding="utf-8")
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_filesystem_tools(registry)
    skills = SkillRegistry(registry, SkillLoader(skill_dir))
    skills.reload()
    import pytest
    with pytest.raises(PermissionError):
        skills.execute("unsafe_skill", {})
