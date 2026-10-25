from datetime import datetime, timedelta

from app.automation.jobs import JobRepository, ScheduledJob
from app.automation.scheduler import AgentScheduler
from app.automation.triggers import build_trigger
from app.agent.core import Agent
from app.agent.executor import Executor
from app.agent.planner import PlanStep
from app.agent.verifier import Verifier
from app.memory.database import MemoryDatabase
from app.safety.permissions import PermissionManager
from app.tools.filesystem import register_filesystem_tools
from app.tools.registry import ToolRegistry


def test_job_repository_and_triggers(tmp_path):
    database = MemoryDatabase(tmp_path / "jobs.db")
    database.initialize()
    repository = JobRepository(database)
    job = ScheduledJob("daily-report", "Make a report", "daily", {"hour": 9, "minute": 30})
    repository.save(job)
    loaded = repository.list(enabled_only=True)[0]
    assert loaded.goal == "Make a report"
    assert build_trigger("daily", loaded.schedule) is not None
    assert build_trigger("weekly", {"day_of_week": "mon", "hour": 10}) is not None
    assert build_trigger("interval", {"minutes": 5}) is not None
    run_at = (datetime.now() + timedelta(hours=1)).isoformat()
    assert build_trigger("once", {"run_at": run_at}) is not None
    assert build_trigger("cron", {"expression": "0 9 * * 1-5"}) is not None

    repository.set_enabled("daily-report", False)
    assert repository.get("daily-report").enabled is False
    request_id = repository.request_approval("daily-report", "browser_type", {"text": "hello"}, "Approval required", "task-1")
    assert repository.approvals()[0].id == request_id
    assert repository.decide_approval(request_id, "denied").status == "denied"
    repository.delete("daily-report")
    assert repository.list() == []


def test_scheduled_work_coalesces_missed_runs_and_does_not_overlap(tmp_path):
    database = MemoryDatabase(tmp_path / 'bounded-jobs.db')
    database.initialize()
    scheduler = AgentScheduler(None, JobRepository(database))
    scheduler.add(ScheduledJob('bounded', 'Read status', 'interval', {'minutes':5}))
    scheduled = scheduler.scheduler.get_job('bounded')
    assert scheduled.max_instances == 1
    assert scheduled.coalesce is True
    assert scheduled.misfire_grace_time == 60

class DeletePlanner:
    def create_plan(self, goal, context):
        return [PlanStep("delete", "delete_file", {"path": "remove.txt"}, {"type": "tool_success"}, 0)]

    def replan(self, goal, failed_step, error, context):
        return []


def test_scheduled_ask_is_queued_then_explicitly_consumed(tmp_path):
    database = MemoryDatabase(tmp_path / "approval.db")
    database.initialize()
    permissions = PermissionManager({"default": "DENY", "permissions": {"filesystem_delete": "ASK"}}, tmp_path)
    registry = ToolRegistry(permissions)
    register_filesystem_tools(registry)
    (tmp_path / "remove.txt").write_text("temporary", encoding="utf-8")
    agent = Agent(DeletePlanner(), Executor(registry), Verifier(), database)
    repository = JobRepository(database)
    repository.save(ScheduledJob("delete-once", "delete", "interval", {"hours": 1}))
    scheduler = AgentScheduler(agent, repository)
    first = scheduler.run_now("delete-once")
    assert not first.complete
    request = repository.approvals()[0]
    assert (tmp_path / "remove.txt").exists()
    second = scheduler.approve_and_run(request.id)
    assert second.complete
    assert not (tmp_path / "remove.txt").exists()
    assert repository.approvals("consumed")[0].id == request.id
