from app.agent.core import Agent
from app.agent.executor import Executor
from app.agent.verifier import Verifier
from app.memory.database import MemoryDatabase
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


class FailingPlanner:
    def create_plan(self, goal, context):
        raise TimeoutError("model timed out")


def test_planning_failure_is_recorded_not_raised(tmp_path):
    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    agent = Agent(FailingPlanner(), Executor(registry), Verifier(), memory)
    outcome = agent.run("a goal")
    assert not outcome.complete
    assert "model timed out" in outcome.summary
    assert memory.recent_tasks()[0]["status"] == "failed"


def test_planning_memory_is_compact_relevant_and_omits_failed_experience(tmp_path):
    memory = MemoryDatabase(tmp_path / "planning-memory.db")
    memory.initialize()
    memory.store_memory("episodic", "Read README personal Windows agent successfully " + "x" * 3000, 0.9, metadata={"complete": True})
    memory.store_memory("episodic", "Read README personal Windows agent failed", 0.9, metadata={"complete": False})
    memory.store_memory("semantic", "Banana inventory", 0.5)
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    agent = Agent(FailingPlanner(), Executor(registry), Verifier(), memory)
    recalled = agent._planning_memories("Read README personal Windows agent")
    assert len(recalled) == 1
    assert len(recalled[0]["content"]) == 1200
    assert "metadata" not in recalled[0]


def test_a_reference_to_another_steps_output_is_refused_not_passed_on():
    # Live 2026-10-24: a model plan sent "{{steps.0.output.contacts[0].id}}" to a tool as a contact name.
    import pytest

    from app.agent.executor import Executor

    executor = Executor(registry=None)
    for reference in ("{{steps.0.output.contacts[0].id}}", "{{ step_1.output }}", "{{previous.result}}"):
        with pytest.raises(ValueError, match="earlier step's output"):
            executor._resolve({"contact_id": reference}, [])
    template = "<h1>{{ name }}</h1>{% for item in items %}{{ item.title }}{% endfor %}"
    assert executor._resolve({"content": template}, []) == {"content": template}
