"""One shared sense of what the owner is waiting for; background parts act accordingly (app/nervous_system.py)."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.nervous_system import BODY, MAX_WAIT, NervousSystem, describe, register_body_tools


def body(idle=5.0):
    return NervousSystem(idle_reader=lambda: idle)


def test_the_state_comes_from_focus_holds_and_the_last_input_time():
    assert body(5).state()["level"] == "present"
    assert body(300).state()["level"] == "idle"
    assert body(4000).state()["level"] == "away"
    assert NervousSystem(idle_reader=lambda: None).state()["level"] == "unknown"
    nerves = body(4000)
    with nerves.focus("answering you"), nerves.focus("running a task: weather"):
        state = nerves.state()
        assert state["level"] == "focused" and state["why"] == "answering you; running a task: weather"
    assert nerves.state()["level"] == "away"


def test_focus_is_released_even_when_the_work_fails():
    nerves = body()
    with pytest.raises(RuntimeError):
        with nerves.focus("answering you"):
            raise RuntimeError("model crashed")
    assert not nerves.focused
    assert nerves.yield_to_owner("world feeds") == 0.0


def test_a_background_part_waits_while_the_owner_waits_and_starts_the_moment_focus_ends():
    nerves = body()
    release = threading.Event()

    def owner_request():
        with nerves.focus("answering you"):
            release.wait(5)

    thread = threading.Thread(target=owner_request)
    thread.start()
    while not nerves.focused:
        time.sleep(0.01)
    threading.Timer(0.3, release.set).start()
    started = time.perf_counter()
    waited = nerves.yield_to_owner("world feeds", max_wait=10)
    elapsed = time.perf_counter() - started
    thread.join()
    assert 0.25 <= waited <= 0.6 and elapsed < 0.6  # woken by the release, not by a slow poll
    organ = nerves.status()["organs"][0]
    assert organ["name"] == "world feeds" and organ["times_waited_for_you"] == 1 and organ["seconds_waited"] >= 0.25


def test_nothing_starves_behind_a_long_task_and_stopping_is_never_blocked():
    nerves = body()
    with nerves.focus("running a task: long"):
        started = time.perf_counter()
        assert 0.2 <= nerves.yield_to_owner("job watcher", max_wait=0.2) < 0.5
        assert "limit" in nerves.status()["organs"][0]["note"]
        stop = threading.Event()
        threading.Timer(0.1, stop.set).start()
        nerves.yield_to_owner("code monitor", stop, max_wait=30)
        assert time.perf_counter() - started < 1.5


def test_a_round_is_counted_with_its_outcome():
    nerves = body()
    with nerves.round("memory consolidation"):
        pass
    with pytest.raises(ValueError):
        with nerves.round("memory consolidation"):
            raise ValueError("disk full")
    [organ] = nerves.status()["organs"]
    assert organ["rounds"] == 2 and organ["failed_rounds"] == 1 and organ["last_round"]


def test_an_agent_task_holds_focus_while_it_runs(tmp_path):
    from app.agent.core import Agent
    from app.agent.executor import Executor
    from app.agent.planner import PlanStep
    from app.agent.verifier import Verifier
    from app.memory.database import MemoryDatabase
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    seen = []
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    registry.register("look", "Reads.", {"type": "object", "properties": {}, "additionalProperties": False},
                      "low", "system_read", lambda: seen.append(BODY.state()) or {"ok": True})
    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()

    class Planner:
        last_direct_response = None

        def create_plan(self, goal, context, intent=None, session=None):
            return [PlanStep("Look.", "look", {}, {"type": "tool_success"}, 0)]

    assert Agent(Planner(), Executor(registry), Verifier(), memory).run("check the weather").complete
    assert seen[0]["level"] == "focused" and "running a task: check the weather" in seen[0]["why"]
    assert not BODY.focused


def test_the_learner_waits_for_a_task_the_owner_started(tmp_path, monkeypatch):
    from app.learning.continuous import ContinuousLearner

    learner = ContinuousLearner(root=tmp_path)
    monkeypatch.setattr(learner, "machine_is_busy", lambda: False)
    with BODY.focus("running a task: report"):
        allowed, reason = learner.may_run()
    assert allowed is False and reason == "waiting while you wait for me: running a task: report"


def test_the_assistant_turn_holds_focus(monkeypatch):
    from app.assistant import AssistantService as Assistant

    seen = []
    assistant = Assistant.__new__(Assistant)
    assistant._interlock = SimpleNamespace(ticket=lambda: None, halted=False, epoch=0)
    assistant.runtime = SimpleNamespace(learner=None)
    monkeypatch.setattr(Assistant, "_handle_turn", lambda self, text, source: seen.append(BODY.state()) or "reply",
                        raising=True)
    assert assistant.handle("hello") == "reply"
    assert seen[0]["level"] == "focused" and seen[0]["why"] == "answering you"
    assert not BODY.focused


def test_the_status_tool_and_brain_part_report_real_numbers(tmp_path):
    from app import brain_components as bc
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    nerves = body(30)
    with nerves.round("world feeds"):
        pass
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_body_tools(registry, nerves)
    result = registry.execute("body_status", {})
    assert result.success and result.output["level"] == "present"
    summary = result.output["summary"]
    assert "You are here" in summary and "world feeds: 1 rounds" in summary and "price alerts" in summary
    assert f"never longer than {int(MAX_WAIT)} s" in describe(nerves.status())
    [part] = [item for item in bc.service_components(["nervous_system"]) if item.component_id == "svc:nervous_system"]
    assert part.parent == "cortex"
    assert "level: present" in bc.check(part, SimpleNamespace(nervous_system=nerves))
