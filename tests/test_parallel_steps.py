"""Independent read-only plan steps run at the same time; everything else keeps its order (app/agent/parallel.py)."""
from __future__ import annotations

import threading
import time

from app.agent.core import Agent
from app.agent.executor import Executor
from app.agent.parallel import independent_run, parallel_safe
from app.agent.planner import PlanStep
from app.agent.verifier import Verifier
from app.execution import current_context
from app.memory.database import MemoryDatabase
from app.safety.approvals import CallBudget
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "additionalProperties": False}
DELAY = 0.3


class Planner:
    last_direct_response = None

    def __init__(self, plan, recovery=None):
        self.plan, self.recovery = plan, recovery or []

    def create_plan(self, goal, context, intent=None, session=None):
        return list(self.plan)

    def replan(self, goal, failed_step, error, context):
        recovery, self.recovery = self.recovery, []
        return recovery


class Clock:
    def __init__(self):
        self.lock = threading.Lock()
        self.spans: dict[str, tuple[float, float]] = {}
        self.tasks: dict[str, object] = {}
        self.attempts: dict[str, int] = {}

    def tool(self, kind, *, fail_first=False):
        def handler(name=""):
            started = time.perf_counter()
            with self.lock:
                self.attempts[name] = self.attempts.get(name, 0) + 1
                attempt = self.attempts[name]
                self.tasks[name] = current_context().task_id
            time.sleep(DELAY)
            with self.lock:
                self.spans[name] = (started, time.perf_counter())
            if fail_first and attempt == 1:
                raise ConnectionError("source busy")
            return {"kind": kind, "name": name}
        return handler


def _agent(tmp_path, plan, recovery=None, max_steps=20):
    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    clock = Clock()
    registry.register("lookup", "Reads.", SCHEMA, "low", "public_api_get", clock.tool("read"))
    registry.register("read_note", "Reads.", SCHEMA, "low", "filesystem_read", clock.tool("read"))
    registry.register("flaky_lookup", "Reads.", SCHEMA, "low", "web_search", clock.tool("read", fail_first=True))
    registry.register("save_note", "Writes.", SCHEMA, "low", "filesystem_write_project", clock.tool("write"))
    registry.register("risky_read", "Reads.", SCHEMA, "medium", "filesystem_read", clock.tool("read"))
    registry.register("type_text", "Types.", SCHEMA, "low", "computer_input", clock.tool("input"))
    agent = Agent(Planner(plan, recovery), Executor(registry), Verifier(), memory, max_steps=max_steps)
    return agent, clock, memory, registry


def step(tool, name):
    return PlanStep(f"{tool} {name}", tool, {"name": name}, {"type": "tool_success"}, 1)


def overlaps(clock, a, b):
    (a0, a1), (b0, b1) = clock.spans[a], clock.spans[b]
    return a0 < b1 and b0 < a1


def test_three_independent_lookups_take_the_time_of_one(tmp_path):
    plan = [step("lookup", "dubai"), step("lookup", "lahore"), step("read_note", "karachi")]
    agent, clock, memory, _ = _agent(tmp_path, plan)
    started = time.perf_counter()
    outcome = agent.run("weather in dubai, lahore and karachi")
    elapsed = time.perf_counter() - started
    assert outcome.complete
    assert elapsed < DELAY * 2.2, elapsed  # sequential would be 0.9 s
    assert overlaps(clock, "dubai", "lahore") and overlaps(clock, "lahore", "karachi")
    # Recorded in plan order, each verified, exactly as if run one by one.
    assert [event["result"]["output"]["name"] for event in outcome.history] == ["dubai", "lahore", "karachi"]
    assert all(event["verification"]["passed"] for event in outcome.history)
    with memory.connect() as db:
        rows = db.execute("SELECT step_index,tool FROM actions WHERE task_id=? ORDER BY id", (outcome.task_id,)).fetchall()
    assert [(row[0], row[1]) for row in rows] == [(0, "lookup"), (1, "lookup"), (2, "read_note")]
    # Each worker thread ran inside the task's own execution context, so approvals bind to the right task.
    assert set(clock.tasks.values()) == {outcome.task_id}


def test_a_write_waits_for_the_reads_before_it_and_order_is_never_changed(tmp_path):
    plan = [step("lookup", "a"), step("lookup", "b"), step("save_note", "w"), step("lookup", "c"), step("lookup", "d")]
    agent, clock, _, _ = _agent(tmp_path, plan)
    outcome = agent.run("look up a and b, save, then look up c and d")
    assert outcome.complete
    assert overlaps(clock, "a", "b") and overlaps(clock, "c", "d")
    assert clock.spans["w"][0] >= max(clock.spans["a"][1], clock.spans["b"][1])
    assert clock.spans["c"][0] >= clock.spans["w"][1] and clock.spans["d"][0] >= clock.spans["w"][1]
    assert [event["tool"] for event in outcome.history] == ["lookup", "lookup", "save_note", "lookup", "lookup"]


def test_devices_placeholders_and_riskier_tools_stay_sequential(tmp_path):
    agent, clock, _, registry = _agent(tmp_path, [])
    assert not parallel_safe(step("type_text", "x"), registry)
    assert not parallel_safe(step("risky_read", "x"), registry)
    assert not parallel_safe(step("save_note", "x"), registry)
    assert not parallel_safe(PlanStep("ref", "lookup", {"name": "{{desktop_observation_id}}"}), registry)
    assert not parallel_safe(step("not_registered", "x"), registry)
    assert parallel_safe(step("lookup", "x"), registry)
    assert independent_run([step("lookup", "a"), step("type_text", "b"), step("lookup", "c")], 0, registry, limit=9) == []
    assert independent_run([step("lookup", "a"), step("lookup", "b"), step("lookup", "c")], 0, registry, limit=2) == [0, 1]


def test_a_failed_early_result_is_retried_the_ordinary_way(tmp_path):
    plan = [step("flaky_lookup", "busy"), step("lookup", "fine")]
    agent, clock, _, _ = _agent(tmp_path, plan)
    outcome = agent.run("two lookups")
    assert outcome.complete
    assert clock.attempts == {"busy": 2, "fine": 1}
    assert [(event["tool"], event["retry"], event["verification"]["passed"]) for event in outcome.history] == [
        ("flaky_lookup", 0, False), ("flaky_lookup", 1, True), ("lookup", 0, True)]


def test_a_recovery_spliced_in_keeps_each_early_result_with_its_own_step(tmp_path):
    first = PlanStep("broken", "lookup", {"name": "first"}, {"type": "tool_success"}, 0)
    first.arguments = {"name": "first", "unknown": 1}  # fails schema validation: no retries, so recovery runs
    plan = [first, step("lookup", "second")]
    agent, clock, _, _ = _agent(tmp_path, plan, recovery=[step("read_note", "fixed")])
    outcome = agent.run("recover then continue")
    # `first` qualifies (low-risk read, no placeholder), so it started early with `second` and failed.
    names = [event["result"].get("output", {}).get("name") if event["result"].get("output") else None
             for event in outcome.history]
    assert names[-2:] == ["fixed", "second"] and outcome.complete
    assert clock.attempts["second"] == 1  # the early result was used, not run twice


def test_the_call_budget_holds_across_threads():
    budget = CallBudget(max_total_calls=10_000, max_calls_per_tool=10_000)

    def spend():
        for _ in range(2000):
            budget.consume("lookup")

    threads = [threading.Thread(target=spend) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert budget.total == 16_000 and budget.counts["lookup"] == 16_000


def test_steps_started_early_count_against_the_task_budget(tmp_path):
    # Budget 3: all three start together (3 calls). The flaky one's retry would be a 4th call, so it is refused.
    plan = [step("flaky_lookup", "busy"), step("lookup", "b"), step("lookup", "c")]
    agent, clock, _, _ = _agent(tmp_path, plan, max_steps=3)
    outcome = agent.run("three lookups with a three-call budget")
    assert sum(clock.attempts.values()) == 3
    assert not outcome.complete and "3-call execution budget" in outcome.summary


def test_it_can_be_switched_off(tmp_path):
    plan = [step("lookup", "a"), step("lookup", "b")]
    agent, clock, _, _ = _agent(tmp_path, plan)
    agent.parallel_steps = False
    assert agent.run("two lookups").complete
    assert not overlaps(clock, "a", "b")
