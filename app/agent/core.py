from __future__ import annotations

import logging
import time
import threading
import uuid
import copy
from dataclasses import dataclass, field
from typing import Any

from app.agent.context import AgentContext
from app.agent.executor import Executor
from app.agent.planner import Planner
from app.agent.reflection import Reflector
from app.agent.verifier import Verification, Verifier
from app.execution import UNSET, execution_scope
from app.logging import TaskLogger
from app.memory.database import MemoryDatabase
from app.memory.working import WorkingMemory
from app.safety.sensitive import redact as redact_sensitive
from app.logging import log_swallowed
from app.networking_privacy import PRIVATE_TEXT, contains_private_tools, redact_private_payload

_log = logging.getLogger(__name__)


#: Failures that repeat identically, so retrying the same call wastes the step budget.
DETERMINISTIC_ERRORS = ("FileNotFoundError", "NotADirectoryError", "IsADirectoryError", "UnsafeUrlError")


@dataclass(slots=True)
class TaskOutcome:
    task_id: str
    goal: str
    complete: bool
    summary: str
    plan: list[dict[str, Any]]
    history: list[dict[str, Any]] = field(default_factory=list)
    reflection: dict[str, Any] = field(default_factory=dict)
    success_criteria: list[dict[str, Any]] = field(default_factory=list)
    goal_verification: dict[str, Any] = field(default_factory=dict)
    needs_user_input: bool = False


class Agent:
    def __init__(self, planner: Planner, executor: Executor, verifier: Verifier, memory: MemoryDatabase, max_steps: int = 20) -> None:
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.memory = memory
        self.reflector = Reflector(memory)
        self.max_steps = max_steps
        self._cancel_event = threading.Event()
        self._run_lock = threading.Lock()
        self.active_goal: str | None = None
        self.active_plan: list[dict[str, Any]] = []
        self.active_step: dict[str, Any] | None = None
        self.active_history: list[dict[str, Any]] = []
        self.active_activity: str = "idle"
        #: Set by the runtime: app/experience.py ExperienceMemory, and a
        #: callable returning the current situation brief (app/situation.py).
        self.experience: Any = None
        self.situation_brief: Any = None
        self.executive: Any = None

    def run(self, goal: str, intent: Any | None = None, session: Any | None = None) -> TaskOutcome:
        if not self._run_lock.acquire(blocking=False):
            return TaskOutcome("", goal, False, "Another agent task is already running.", [])
        self._cancel_event.clear()
        # Budgets limit one task, not the lifetime of the process. Without a
        # reset a healthy long-running assistant eventually refuses every tool
        # merely because earlier, unrelated tasks used their allowance.
        self.executor.registry.reset_budget()
        self.active_goal = goal
        self.active_history = []
        self.active_activity = "planning"
        try:
            return self._run(goal, intent, session)
        finally:
            self.active_goal = None
            self.active_plan = []
            self.active_step = None
            self.active_history = []
            self.active_activity = "idle"
            self._run_lock.release()

    def _run(self, goal: str, intent: Any | None = None, session: Any | None = None) -> TaskOutcome:
        # The task id is bound here, around everything this task does, because
        # this is the only place that knows it. Every tool the plan executes --
        # and so every approval those tools raise -- is correlated to *this*
        # task rather than to whichever task the session happened to finish
        # last, which is a different task entirely while this one is running.
        task_id = datetime_task_id()
        with execution_scope(
            task_id=task_id,
            session_id=getattr(session, "session_id", None) or UNSET,
        ):
            return self._run_task(task_id, goal, intent, session)

    def _run_task(self, task_id: str, goal: str, intent: Any | None = None, session: Any | None = None) -> TaskOutcome:
        logger = TaskLogger(task_id)
        memories = self._planning_memories(goal)
        lessons = self._lessons(goal)
        context = AgentContext(goal, self._observe_with_situation(), memories, [], lessons)
        executive_frame = None
        if self.executive is not None and len(goal) <= 6000:
            criteria = [str(item.to_dict() if hasattr(item, 'to_dict') else item)[:1000]
                        for item in getattr(intent, 'success_criteria', [])][:16]
            executive_frame = self.executive.prepare(goal, criteria=criteria, max_cycles=1,
                                                      max_calls=min(32, max(1, self.max_steps)), max_seconds=1800)
            context.observation['executive_goal_contract'] = self.executive.brief(executive_frame)
        try:
            plan = self.planner.create_plan(goal, context, intent, session) if intent is not None or session is not None else self.planner.create_plan(goal, context)
            if len(plan) > self.max_steps:
                raise ValueError(f'Plan needs {len(plan)} steps but the task budget is {self.max_steps}; shorten the task before executing it.')
        except Exception as exc:
            summary = f"Task failed during planning: {type(exc).__name__}: {exc}"
            self.memory.create_task(task_id, goal, [])
            self.memory.update_task(task_id, "failed", summary)
            self.memory.store_memory("episodic", summary, 0.9, "task", task_id, {"goal": goal, "complete": False}, session_id=getattr(session, "session_id", None))
            reflection = self.reflector.reflect(task_id, goal, [], False)
            logger.log("task_finished", complete=False, summary=summary, reflection=reflection)
            return TaskOutcome(task_id, goal, False, summary, [], [], reflection)
        self.active_plan = [step.to_dict() for step in plan]
        self._publish("TASK_CREATED", {
            "goal": goal, "steps": [step.description for step in plan], "count": len(plan),
        }, task_id=task_id)
        criteria = [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in getattr(intent, "success_criteria", [])]
        if not plan:
            summary = self.planner.last_direct_response or "The action planner returned no executable plan."
            self.memory.create_task(task_id, goal, [], {"intent": getattr(getattr(intent, "intent", None), "value", None), "success_criteria": criteria})
            self.memory.update_task(task_id, "failed", summary)
            return TaskOutcome(task_id, goal, False, summary, [], success_criteria=criteria, goal_verification={"passed": False, "reason": "empty_action_plan"})
        working = WorkingMemory(goal, task_id)
        self.memory.create_task(task_id, goal, [step.to_dict() for step in plan], {"intent": getattr(getattr(intent, "intent", None), "value", None), "success_criteria": criteria})
        logger.log("task_started", task_id=task_id, goal=goal, plan=[step.to_dict() for step in plan])
        all_passed = True
        executed = 0
        needs_user_input = False
        last_error = ''

        index = 0
        while index < len(plan) and executed < self.max_steps:
            if executive_frame is not None:
                from app.executive import ExecutiveLimit
                try:
                    self.executive.checkpoint(executive_frame, 'execute')
                except ExecutiveLimit:
                    all_passed = False
                    break
            if self._cancel_event.is_set():
                all_passed = False
                break
            step = plan[index]
            self.active_step = step.to_dict()
            self.active_activity = "executing"
            passed = False
            last_error = ""
            for retry in range(step.max_retries + 1):
                if executed >= self.max_steps:
                    last_error = f'Task stopped at its {self.max_steps}-call execution budget; remaining work was not executed.'
                    break
                if self._cancel_event.is_set():
                    last_error = "Task cancelled by the user."
                    break
                observation = self.observe_environment()
                working.observe(observation)
                started = time.perf_counter()
                result = self.executor.execute(step, working.history)
                verification = self.verifier.check(step.expected, result)
                if executive_frame is not None:
                    self.executive.observe(executive_frame, 'verification', {
                        'tool': step.tool, 'passed': verification.passed, 'reason': verification.reason})
                self.active_activity = "verifying"
                duration_ms = int((time.perf_counter() - started) * 1000)
                event = {
                    "step": index + 1,
                    "description": step.description,
                    "tool": step.tool,
                    "arguments": step.arguments,
                    "expected": step.expected,
                    "result": result.to_dict(),
                    "verification": verification.to_dict(),
                    "retry": retry,
                    "observation": observation,
                }
                working.record(event)
                self.active_history.append({'tool': step.tool, 'step': index + 1,
                                            'verification': {'passed': verification.passed}})
                persistent_event = (redact_private_payload(event) if contains_private_tools(self.active_plan)
                                    else redact_ephemeral_event(event))
                self.memory.record_action(task_id, index, step.tool, step.arguments, persistent_event["result"], verification.to_dict(), retry, duration_ms)
                self.memory.record_checkpoint(task_id, index, "RUNNING", {"plan": [item.to_dict() for item in plan], "last_event": persistent_event, "resume_policy": "manual_review_required"})
                logger.log("action", **persistent_event, duration_ms=duration_ms)
                self._publish("TASK_PROGRESS", {
                    "detail": (
                        f"verified step {index + 1}: {step.description}"
                        if verification.passed else
                        f"step {index + 1} needs recovery: {verification.reason}"
                    ),
                    "step": index + 1, "tool": step.tool,
                    "verified": verification.passed, "retry": retry,
                }, task_id=task_id)
                executed += 1
                if isinstance(result.output, dict) and result.output.get("requires_user_choice"):
                    needs_user_input = True
                    last_error = str(result.output.get("reason") or "The action requires an explicit user choice.")
                    break
                if verification.passed:
                    passed = True
                    break
                last_error = verification.reason
                # The same call fails the same way again: a missing file, a file asked for as a folder, a file name
                # taken for a website. Go straight to re-planning with the error instead of repeating it.
                if str(result.error or "").startswith(DETERMINISTIC_ERRORS):
                    break
            if not passed:
                if needs_user_input:
                    all_passed = False
                    break
                if self._cancel_event.is_set():
                    all_passed = False
                    break
                if executed >= self.max_steps:
                    all_passed = False
                    last_error = f'Task stopped at its {self.max_steps}-call execution budget; remaining work was not executed.'
                    break
                # The failure just seen, plus what fixed this tool's failure before.
                context = AgentContext(goal, working.observations[-1], memories, working.history,
                                       lessons + self._fixes(step.tool, last_error))
                try:
                    recovery = self.planner.replan(goal, step, last_error, context)
                except Exception as exc:
                    recovery = []
                    last_error = f"Replanning failed: {type(exc).__name__}: {exc}"
                    logger.log("replanning_failed", error=last_error)
                if recovery:
                    required = len(recovery) + len(plan) - index - 1
                    if required > self.max_steps - executed:
                        all_passed = False
                        last_error = 'Recovery and remaining steps cannot fit the execution budget; no partial recovery was executed.'
                        break
                    plan[index:index + 1] = recovery
                    self.active_plan = [item.to_dict() for item in plan]
                    continue
                all_passed = False
                break
            index += 1

        goal_verification = self.verifier.check_goal(criteria, working.history) if criteria else Verification(True, "No separate goal criteria were supplied.", {})
        cancelled = self._cancel_event.is_set()
        complete = all_passed and index == len(plan) and goal_verification.passed and not cancelled
        summary = "Task cancelled by the user." if cancelled else (last_error if needs_user_input else self._summarize(goal, working.history, complete, goal_verification))
        if not complete and last_error and not cancelled and not needs_user_input:
            summary += f' Stopping reason: {last_error}'
        self.memory.update_task(task_id, "cancelled" if cancelled else ("complete" if complete else "failed"), summary, [step.to_dict() for step in plan])
        private_networking = contains_private_tools([self.active_plan, working.history])
        persistent_history = [(redact_private_payload(event) if private_networking else redact_ephemeral_event(event))
                              for event in working.history]
        self.memory.store_memory("episodic", summary, 0.8 if complete else 0.9, "task", task_id, {"goal": goal, "complete": complete, "history": persistent_history}, session_id=getattr(session, "session_id", None))
        reflection = self.reflector.reflect(task_id, PRIVATE_TEXT if private_networking else goal, persistent_history, complete)
        if self.experience is not None and not cancelled:
            try:
                kind = getattr(getattr(intent, "intent", None), "value", "") or ""
                self.experience.record(PRIVATE_TEXT if private_networking else goal, persistent_history, complete, task_id=task_id, kind=str(kind))
            except Exception:
                log_swallowed(_log, "recording a task experience", level=logging.WARNING)
        knowledge = getattr(self.planner, "knowledge", None)
        for claim_id in getattr(self.planner, "last_knowledge_ids", []):
            try:
                knowledge.record_use(claim_id, complete, "claim")
            except Exception:
                log_swallowed(_log, "recording knowledge use for a finished task", level=logging.WARNING)
        logger.log("task_finished", complete=complete, summary=summary, reflection=reflection)
        return TaskOutcome(task_id, goal, complete, summary, [step.to_dict() for step in plan], working.history, reflection, criteria, goal_verification.to_dict(), needs_user_input)

    def cancel_current(self) -> bool:
        running = self._run_lock.locked()
        if running:
            self._cancel_event.set()
        return running

    def _publish(self, event_type: str, payload: dict[str, Any], *, task_id: str | None = None) -> None:
        events = getattr(self.executor.registry, "events", None)
        if events is None:
            return
        try:
            from app.execution import current_context

            context = current_context()
            if contains_private_tools(self.active_plan):
                payload = redact_private_payload(payload)
            events.publish(
                event_type, "agent", payload, session_id=context.session_id,
                turn_id=context.turn_id, task_id=task_id or context.task_id,
            )
        except Exception:
            log_swallowed(_log, "publishing an agent event")

    @property
    def is_running(self) -> bool:
        return self._run_lock.locked()

    @staticmethod
    def observe_environment() -> dict[str, Any]:
        from app.config import PROJECT_ROOT
        return {"project_root": str(PROJECT_ROOT), "project_exists": PROJECT_ROOT.is_dir(), "timestamp": time.time()}

    def _observe_with_situation(self) -> dict[str, Any]:
        observation = self.observe_environment()
        if callable(self.situation_brief):
            try:
                brief = self.situation_brief()
                if brief:
                    observation["situation"] = brief
            except Exception:
                log_swallowed(_log, "reading the situation brief for planning")
        return observation

    def _lessons(self, goal: str) -> list[dict[str, Any]]:
        if self.experience is None:
            return []
        try:
            return self.experience.lessons(goal)
        except Exception:
            log_swallowed(_log, "reading lessons from past experience", level=logging.WARNING)
            return []

    def _fixes(self, tool: str, error: str) -> list[dict[str, Any]]:
        if self.experience is None or not error:
            return []
        try:
            return self.experience.fixes_for(tool, error)
        except Exception:
            log_swallowed(_log, "reading recorded fixes for a failed step", level=logging.WARNING)
            return []

    def _planning_memories(self, goal: str) -> list[dict[str, Any]]:
        recalled = self.memory.retrieve(goal, limit=20)
        recalled = [item for item in recalled if not (item["memory_type"] == "episodic" and item.get("metadata", {}).get("complete") is False)]
        if not recalled:
            return []
        threshold = max(0.35, recalled[0]["relevance"] - 0.20)
        return [
            {"id": item["id"], "memory_type": item["memory_type"], "content": item["content"][:1200], "relevance": item["relevance"]}
            for item in recalled if item["relevance"] >= threshold
        ][:3]

    @staticmethod
    def _summarize(goal: str, history: list[dict[str, Any]], complete: bool, goal_verification: Any | None = None) -> str:
        passed = sum(1 for item in history if item["verification"]["passed"])
        status = "succeeded" if complete else "failed"
        final_output = history[-1]["result"].get("output") if history else None
        if isinstance(final_output, dict) and isinstance(final_output.get("content"), str):
            content = final_output["content"]
            final_output = {**final_output, "content": content[:500] + ("..." if len(content) > 500 else "")}
        goal_reason = getattr(goal_verification, "reason", "")
        # Plain lines, not a pasted dictionary: "Final evidence: {'application':
        # 'notepad', 'pid': 48848, 'running': True}" reached the owner as a reply.
        from app.plain_output import describe

        steps = f"{passed} verified step{'s' if passed != 1 else ''}"
        lines = [f"Task {status}. {steps[0].upper() + steps[1:]}.", f"Goal: {goal.rstrip('.')}."]
        if goal_reason:
            import re

            counted = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s+user-goal criteria satisfied\.?\s*", goal_reason)
            if counted and counted[2] == "1":
                goal_reason = "what was asked for was " + ("confirmed" if counted[1] == "1" else "not confirmed")
            elif counted:
                goal_reason = f"{counted[1]} of the {counted[2]} things asked for {'was' if counted[1] == '1' else 'were'} confirmed"
            lines.append(f"Check: {goal_reason.rstrip('.')}.")
        if final_output not in (None, "", {}, []):
            lines.append("Last result:\n" + describe(final_output))
        return "\n".join(lines)


def datetime_task_id() -> str:
    return f"task-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def redact_ephemeral_event(event: dict[str, Any]) -> dict[str, Any]:
    """Strip ephemeral payloads and any credential material before persistence.

    Applies to task history, action records, and checkpoints. Ephemeral outputs
    (clipboard, audio) are reduced to their metadata; everything else is passed
    through value-level redaction so a secret in stdout or in a tool argument
    cannot reach the database.
    """
    persistent = copy.deepcopy(event)
    if contains_private_tools(persistent):
        return redact_private_payload(persistent)
    output = persistent.get("result", {}).get("output")
    if isinstance(output, dict) and output.get("_ephemeral"):
        persistent["result"]["output"] = {
            "_ephemeral": True, "redacted": True, "type": output.get("type"),
            "characters": output.get("characters"), "sha256": output.get("sha256"),
            "sensitive_possible": output.get("sensitive_possible"), "memory_policy": "not_persisted",
        }
    return redact_sensitive(persistent)
