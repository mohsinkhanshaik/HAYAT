from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from app.events import EventBus
from app.memory.database import MemoryDatabase
from app.networking import NetworkingBook
from app.networking_privacy import PRIVATE_TEXT
from app.safety.permissions import PermissionManager
from app.tools.networking import register_networking_tools
from app.tools.registry import ToolRegistry


NAME = "PrivateContactCanary"
EMAIL = "private-canary@example.org"
NOTE = "PrivateGoalAndNotesCanary"


def codec(data, *, decrypt=False):
    return base64.b64decode(data) if decrypt else base64.b64encode(data)


def assert_private_absent(value):
    serialized = value if isinstance(value, str) else json.dumps(value, default=str)
    for private in [NAME, EMAIL, NOTE]:
        assert private not in serialized


def registry(tmp_path):
    permissions = PermissionManager({"default": "ALLOW", "permissions": {}})
    result = ToolRegistry(permissions)
    register_networking_tools(result, NetworkingBook(tmp_path / "workspace", codec=codec))
    return result


def test_durable_tool_events_redact_arguments_while_live_result_stays_usable(tmp_path):
    tools = registry(tmp_path)
    bus = EventBus(tmp_path / "events.db")
    delivered = []
    bus.subscribe("TOOL_STARTED", delivered.append)
    tools.events = bus
    try:
        result = tools.execute("networking_save", {"name": NAME, "notes": NOTE, "channels": [
            {"kind": "email", "value": EMAIL}]})
        assert result.success
        assert result.output["name"] == NAME and result.output["channels"][0]["value"] == EMAIL
        drafted = tools.execute("networking_draft", {"contact_id": result.output["id"], "purpose": NOTE})
        assert drafted.success and NAME in drafted.output["body"] and NOTE in drafted.output["body"]
        assert_private_absent(delivered)
        bus.flush()
        assert_private_absent(bus.recent(100))
        assert any(row["payload"].get("private_networking") for row in bus.recent(100))
    finally:
        bus.close()


def test_lifecycle_call_ids_correlate_concurrent_calls_and_failures(tmp_path):
    tools = registry(tmp_path)
    bus = EventBus(tmp_path / "events.db")
    tools.events = bus
    tools.register("local_test_action", "Test action", {"type": "object", "properties": {
        "fail": {"type": "boolean"}}, "additionalProperties": False}, "low", "filesystem_read",
        lambda fail=False: (_ for _ in ()).throw(ValueError("test failure")) if fail else {"ok": True})
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda fail: tools.execute("local_test_action", {"fail": fail}), [False, True, False, True]))
        assert sum(result.success for result in results) == 2
        bus.flush()
        grouped = {}
        for row in bus.recent(100):
            payload = row["payload"]
            grouped.setdefault(payload["call_id"], []).append(row["event_type"])
        assert len(grouped) == 4
        assert all("TOOL_STARTED" in kinds and len(kinds) == 2 for kinds in grouped.values())
        assert sum("TOOL_FAILED" in kinds for kinds in grouped.values()) == 2
        validation = tools.execute("local_test_action", {"unknown": True})
        assert not validation.success
        bus.flush()
        row = bus.recent(1)[0]
        assert row["event_type"] == "TOOL_FAILED" and row["payload"]["started"] is False
        assert row["payload"]["call_id"] not in grouped
    finally:
        bus.close()


def test_validation_errors_dont_echo_contact_data_into_events_or_failure_diagnostics(tmp_path):
    tools = registry(tmp_path)
    bus = EventBus(tmp_path / "events.db")
    tools.events = bus
    failures, observed = [], []
    tools.failures = SimpleNamespace(record=lambda **fields: failures.append(fields))
    tools.understanding = SimpleNamespace(observe=observed.append)
    try:
        result = tools.execute("networking_save", {"name": NAME, "unexpected": {"email": EMAIL, "notes": NOTE}})
        assert not result.success and NAME in result.error  # Local caller can correct the actual validation error.
        assert failures and failures[0]["exception_type"] == "ValidationError"
        assert_private_absent(failures)
        assert observed and observed[0].error == PRIVATE_TEXT
        bus.flush()
        assert_private_absent(bus.recent(100))
    finally:
        bus.close()


def test_private_actions_plans_checkpoints_and_memories_are_not_plaintext_copies(tmp_path):
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    plan = [{"tool": "networking_save", "description": NAME, "arguments": {"name": NAME, "notes": NOTE}}]
    memory.create_task("private-task", NOTE, plan, {"goal": NOTE})
    memory.record_action("private-task", 0, "networking_save", {"name": NAME},
                         {"tool": "networking_save", "success": True, "output": {"name": NAME, "email": EMAIL}},
                         {"passed": True, "evidence": {"name": NAME}})
    memory.record_checkpoint("private-task", 0, "RUNNING", {"plan": plan, "result": {"draft": EMAIL}})
    memory.store_memory("episodic", f"Saved {NAME} for {NOTE}", task_id="private-task", metadata={"draft": EMAIL})
    memory.store_memory("reflection", f"Helped {NAME}", task_id="private-task")
    memory.update_task("private-task", "complete", f"Saved {NAME}", plan)
    with memory.connect() as db:
        for table in ["tasks", "actions", "task_checkpoints", "memories"]:
            assert_private_absent([dict(row) for row in db.execute(f"SELECT * FROM {table}")])
        action = db.execute("SELECT result_json,verification_json FROM actions").fetchone()
    assert json.loads(action["result_json"])["success"] is True
    assert json.loads(action["verification_json"])["passed"] is True


def test_recovery_into_private_tool_marks_task_private_before_later_summaries(tmp_path):
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    memory.create_task("recovered", NAME, [{"tool": "read_file", "arguments": {"path": "example.txt"}}])
    memory.record_action("recovered", 1, "networking_detail", {"contact_id": NOTE}, {"output": {"email": EMAIL}}, {"passed": True})
    memory.update_task("recovered", "complete", NAME)
    memory.store_memory("episodic", NOTE, task_id="recovered")
    with memory.connect() as db:
        for table in ["tasks", "actions", "memories"]:
            assert_private_absent([dict(row) for row in db.execute(f"SELECT * FROM {table}")])


def test_task_logger_redacts_private_start_action_and_finish_without_mutating_live_data(tmp_path, monkeypatch):
    from app import logging as app_logging
    monkeypatch.setattr(app_logging, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_logging, "load_settings", lambda: {"log_directory": "logs"})
    logger = app_logging.TaskLogger("private-log")
    plan = [{"tool": "networking_save", "arguments": {"name": NAME, "notes": NOTE}}]
    logger.log("task_started", goal=NOTE, plan=plan)
    logger.log("action", tool="networking_draft", result={"body": EMAIL})
    logger.log("task_finished", complete=True, summary=f"Saved {NAME}")
    assert_private_absent(logger.path.read_text(encoding="utf-8"))
    assert plan[0]["arguments"]["name"] == NAME
    assert json.loads(logger.path.read_text(encoding="utf-8").splitlines()[-1])["complete"] is True


def test_ephemeral_history_redaction_leaves_live_networking_evidence_intact():
    from app.agent.core import redact_ephemeral_event
    event = {"tool": "networking_draft", "arguments": {"purpose": NOTE}, "description": NAME,
             "result": {"success": True, "output": {"body": EMAIL}}, "verification": {"passed": True, "evidence": NAME}}
    safe = redact_ephemeral_event(event)
    assert_private_absent(safe)
    assert safe["result"]["success"] and safe["verification"]["passed"]
    assert event["result"]["output"]["body"] == EMAIL


def test_other_tool_event_and_task_logging_are_preserved(tmp_path, monkeypatch):
    from app import logging as app_logging
    monkeypatch.setattr(app_logging, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_logging, "load_settings", lambda: {"log_directory": "logs"})
    logger = app_logging.TaskLogger("public-log")
    logger.log("action", tool="calculator", arguments={"expression": "2+2"}, result={"answer": 4})
    assert "2+2" in logger.path.read_text(encoding="utf-8")
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    memory.create_task("ordinary", "Calculate four", [{"tool": "calculator", "arguments": {"expression": "2+2"}}])
    memory.record_action("ordinary", 0, "calculator", {"expression": "2+2"}, {"success": True, "output": 4}, {"passed": True})
    with memory.connect() as db:
        assert "2+2" in db.execute("SELECT parameters_json FROM actions").fetchone()[0]


def test_real_agent_execution_keeps_live_contact_but_not_durable_task_copies(tmp_path, monkeypatch):
    from app import logging as app_logging
    from app.agent.core import Agent
    from app.agent.executor import Executor
    from app.agent.planner import PlanStep
    from app.agent.verifier import Verifier
    monkeypatch.setattr(app_logging, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_logging, "load_settings", lambda: {"log_directory": "logs"})
    tools = registry(tmp_path)
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    bus = EventBus(tmp_path / "events.db")
    tools.events = bus
    planner = SimpleNamespace(last_direct_response=None, last_knowledge_ids=[], create_plan=lambda *args: [
        PlanStep(f"Save {NAME}", "networking_save", {"name": NAME, "notes": NOTE, "channels": [
            {"kind": "email", "value": EMAIL}]}, max_retries=0)])
    agent = Agent(planner, Executor(tools), Verifier(), memory)
    monkeypatch.setattr(agent, "observe_environment", lambda: {"safe": True})
    monkeypatch.setattr(agent, "_observe_with_situation", lambda: {"safe": True})
    experiences = []
    agent.experience = SimpleNamespace(record=lambda *args, **kwargs: experiences.append((args, kwargs)),
                                       similar=lambda *args, **kwargs: [])
    try:
        result = agent.run(f"Save {NAME} ({EMAIL}) for {NOTE}")
        assert result.complete
        assert result.history[0]["result"]["output"]["name"] == NAME
        assert NAME in result.summary
        with memory.connect() as db:
            for table in ["tasks", "actions", "task_checkpoints", "memories"]:
                assert_private_absent([dict(row) for row in db.execute(f"SELECT * FROM {table}")])
        bus.flush()
        assert_private_absent(bus.recent(100))
        assert_private_absent(experiences)
        logs = list((tmp_path / "logs").glob("*.jsonl"))
        assert logs
        assert_private_absent("\n".join(path.read_text(encoding="utf-8") for path in logs))
    finally:
        bus.close()
