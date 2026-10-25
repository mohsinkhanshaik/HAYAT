from __future__ import annotations

import logging
import inspect
import time
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable

from jsonschema import validate

from app.execution import ExecutionContext, current_context, execution_scope
from app.safety.approvals import ApprovalBroker, ApprovalCallback, CallBudget, deny_unattended
from app.safety.permissions import PermissionDecision, PermissionManager
from app.logging import log_swallowed
from app.networking_privacy import PRIVATE_TEXT, is_private_tool, redact_private_payload

_log = logging.getLogger(__name__)


ToolHandler = Callable[..., Any]

#: Arguments worth showing on a status line: the thing being acted on, not the
#: whole call. A status line is read at a glance, and a full argument dump is
#: not read at all.
_STATUS_KEYS = ("query", "prompt", "path", "file", "url", "application", "target", "goal")


def _describe_arguments(arguments: dict[str, Any]) -> str:
    for key in _STATUS_KEYS:
        value = arguments.get(key)
        if value:
            return str(value)[:60]
    return ""


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: str
    permission: str
    handler: ToolHandler = field(repr=False)

    def model_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.input_schema},
        }


@dataclass(slots=True)
class ToolResult:
    tool: str
    success: bool
    output: Any = None
    error: str = ""
    duration_ms: int = 0
    permission: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolRegistry:
    def __init__(
        self,
        permissions: PermissionManager | None = None,
        approval_callback: ApprovalCallback = deny_unattended,
    ) -> None:
        self.permissions = permissions or PermissionManager()
        self.approvals = (
            approval_callback
            if isinstance(approval_callback, ApprovalBroker)
            else ApprovalBroker(approval_callback)
        )
        self.budget = CallBudget()
        self._scoped_budget: ContextVar[CallBudget | None] = ContextVar("tool_call_budget", default=None)
        self._tools: dict[str, ToolSpec] = {}
        # Set by the runtime. When present it, not the approval callback,
        # decides whether an ASK-category action proceeds -- so ordinary owner
        # work stops prompting while the protected boundary still stops.
        self.authority: Any = None
        self._last_refusal: str | None = None
        # Legacy correlation fallback for callers that do not yet bind an
        # ExecutionContext. It may supply session_id and turn_id only: those
        # are stable for the session, whereas any task id reconstructed from
        # ambient state can name the wrong task (see _call_context).
        self.context_supplier: Any = None
        # Count of authorizations that had to fall back, so the migration to
        # explicit contexts can be finished on evidence rather than guesswork.
        self.context_fallbacks = 0
        # Set by the runtime. Tool failures become records here rather than
        # existing only as a string in a ToolResult the model may paraphrase.
        self.failures: Any = None
        # Set by the runtime, so dispatch can say what it is doing.
        self.events: Any = None
        # Set by the runtime. Every outcome, success included, so HAYAT knows
        # how its tools actually behave rather than only what they claim.
        # Failures alone could never give a rate: a rate needs the calls that
        # worked as its denominator.
        self.understanding: Any = None
        # Set by the runtime (app/location_privacy.py). Screens what a sending or network tool is about to send
        # for the owner's location: held, rounded, or photos stripped of GPS tags.
        self.location_shield: Any = None
        from app.emergency import ExecutionInterlock
        self.interlock = ExecutionInterlock()

    # Arguments that name the thing being acted on, used for scope checks.
    _RESOURCE_KEYS = ("path", "file", "target", "directory", "folder", "url", "application")

    def _call_context(self) -> ExecutionContext:
        """The causal identity of the call being authorized.

        The bound ExecutionContext is authoritative: the caller that knew the
        task put it there. The supplier is only consulted for fields the scope
        left unknown, and its task_id is discarded unconditionally -- a
        supplier reading ambient session state cannot know which task the tool
        in front of us belongs to, and an approval bound to the wrong task is
        worse than one bound to none. Recording None keeps the record honest
        and still refuses to authorize a task-scoped resolution.
        """
        context = current_context()
        if context.session_id and context.turn_id:
            return context
        if self.context_supplier is None:
            return context
        try:
            supplied = dict(self.context_supplier() or {})
        except Exception:
            return context
        self.context_fallbacks += 1
        return replace(
            context,
            session_id=context.session_id or supplied.get("session_id"),
            turn_id=context.turn_id or supplied.get("turn_id"),
        )

    def _authorize(self, tool: ToolSpec, name: str, permission: Any, arguments: dict[str, Any]) -> bool:
        """Apply both configured permission policy and runtime authority.

        Authority is not an ASK-only convenience. It carries privacy mode,
        principal isolation, task envelopes and the protected boundary, so it
        must see *every* tool invocation -- including categories configured as
        ALLOW. Config policy can deny outright; authority may then allow an ASK
        under owner policy, deny an otherwise-allowed sensor/network action,
        or require a one-shot human approval.
        """
        self._last_refusal = None
        if self.authority is None:
            if permission.decision is PermissionDecision.ALLOW:
                return True
            return bool(self.approvals.authorize(name, permission.reason, arguments))

        from app.safety.authority import ActionRequest, Decision, Principal

        context = self._call_context()
        try:
            principal = Principal(context.principal) if context.principal else Principal.LOCAL_OWNER
        except ValueError:
            # An unknown identity must never inherit owner authority.
            principal = Principal.UNTRUSTED_EXTERNAL_CONTENT
        resource = next(
            (str(arguments[key]) for key in self._RESOURCE_KEYS if arguments.get(key)), None
        )
        decision = self.authority.evaluate(ActionRequest(
            tool=name, category=permission.category, arguments=dict(arguments), resource=resource,
            explicitly_requested=context.explicitly_requested,
            principal=principal, session_id=context.session_id, turn_id=context.turn_id,
            task_id=context.task_id,
        ))
        if decision.decision is Decision.ALLOW:
            return True
        if decision.decision is Decision.DENY:
            self._last_refusal = decision.reason
            return False
        # REQUIRE_APPROVAL: a real human decision, with policy's own wording so
        # the prompt says why rather than "permission needed".
        lifecycle = getattr(self.authority, "approvals_lifecycle", None)
        if lifecycle is None:
            if self.approvals.authorize(name, decision.reason, arguments):
                return True
            self._last_refusal = f"Not approved. {decision.reason}"
            return False

        # The lifecycle owns approval truth. The broker only collects the
        # human's answer; whether that answer authorises anything is decided by
        # resolving the exact record it belongs to. A record that expired or
        # was cancelled while the human was deciding resolves to nothing, so a
        # late "yes" cannot execute stale work -- that invariant now holds on
        # the production dispatch path, not only in the lifecycle's own tests.
        record = lifecycle.open(
            name, decision.reason,
            session_id=context.session_id, turn_id=context.turn_id,
            task_id=context.task_id, risk_tier=int(decision.tier),
        )
        human_said_yes = bool(self.approvals.authorize(name, decision.reason, arguments))
        resolved = lifecycle.resolve(human_said_yes, approval_id=record.approval_id, source="broker")
        if resolved is not None and human_said_yes:
            return True
        if resolved is None and human_said_yes:
            self._last_refusal = "The approval expired or was cancelled before it could apply; nothing was executed."
        else:
            self._last_refusal = f"Not approved. {decision.reason}"
        return False

    @property
    def approval_callback(self) -> ApprovalCallback:
        """The function `ApprovalBroker.authorize()` actually consults for ASK decisions.

        This is a property, not a plain attribute, on purpose: `self.approvals`
        (the broker) is what `execute()` calls, bound once at construction.
        Assigning here writes through to `self.approvals.callback` so callers
        that swap approval behavior after construction -- VoiceManager's
        `activate()`/`stop()`, integration tests -- actually change what gets
        asked, instead of mutating a disconnected attribute the broker never
        reads again. That silent disconnect previously made voice-approved
        tool calls fall through to the original construction-time callback
        (typically deny-by-default), which is why an "approved" voice task
        could still report zero verified actions.
        """
        return self.approvals.callback

    @approval_callback.setter
    def approval_callback(self, callback: ApprovalCallback) -> None:
        self.approvals.callback = callback

    def register(self, name: str, description: str, input_schema: dict[str, Any], risk: str, permission: str, handler: ToolHandler) -> None:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        if (input_schema.get('type') == 'object' and 'properties' in input_schema
                and 'additionalProperties' not in input_schema
                and not any(key in input_schema for key in ('allOf', 'anyOf', 'oneOf', '$ref', 'patternProperties'))):
            try:
                parameters = inspect.signature(handler).parameters.values()
                if not any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters):
                    # These handlers already reject unknown kwargs. Declare that
                    # boundary so bad calls fail validation before authorization.
                    input_schema = {**input_schema, 'additionalProperties': False}
            except (ValueError, TypeError):
                pass  # the passive contract audit reports an unavailable signature
        self._tools[name] = ToolSpec(name, description, input_schema, risk, permission, handler)

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._tools[name].model_schema() for name in self.names()]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"name": tool.name, "description": tool.description, "risk": tool.risk, "permission": tool.permission}
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]

    def reset_budget(self) -> None:
        """Start a fresh bounded tool-call budget for one top-level task."""
        self.budget.reset()

    @contextmanager
    def budget_scope(self):
        """Give one independent background job a budget without resetting foreground work.

        Nested dispatch shares this budget; unrelated threads retain theirs.
        The learner's persistent hourly/daily ceilings remain separate.
        """
        budget = CallBudget(max_total_calls=self.budget.max_total_calls,
                            max_calls_per_tool=self.budget.max_calls_per_tool)
        token = self._scoped_budget.set(budget)
        try:
            yield budget
        finally:
            self._scoped_budget.reset(token)

    def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        context: ExecutionContext | None = None,
    ) -> ToolResult:
        """Run a tool. `context` names the turn and task this call belongs to.

        Callers that know their causal identity should pass it (or bind an
        enclosing `execution_scope`); an approval raised here then binds to
        that exact work. Callers that do not are unchanged, and their
        approvals record an unknown task rather than a guessed one.
        """
        arguments = arguments or {}
        started = time.perf_counter()
        scope = nullcontext() if context is None else execution_scope(context=context)
        with scope:
            return self._execute(name, arguments, started)

    def _execute(self, name: str, arguments: dict[str, Any], started: float) -> ToolResult:
        # Whether TOOL_STARTED went out. A failure before it (validation,
        # permission) is still reported, but must not read as the end of a
        # call that never began: the log held 149 browser_navigate ends for
        # 12 starts.
        announced = False
        call_id = "call-" + uuid.uuid4().hex
        try:
            ticket = self.interlock.ticket()
            tool = self.get(name)
            validate(instance=arguments, schema=tool.input_schema)
            budget = self._scoped_budget.get() or self.budget
            if exhausted := budget.check(name):
                result = ToolResult(name, False, error=exhausted, permission="BUDGET")
                result.duration_ms = int((time.perf_counter() - started) * 1000)
                return result
            permission = self.permissions.check(tool.permission, arguments)
            if permission.decision == PermissionDecision.DENY:
                result = ToolResult(name, False, error=permission.reason, permission=permission.decision.value)
                self._record_failure(name, permission.reason, policy=True)
            elif not self._authorize(tool, name, permission, arguments):
                reason = self._last_refusal or "User approval was not granted."
                result = ToolResult(name, False, error=reason, permission=permission.decision.value)
                self._record_failure(name, reason, policy=True)
            else:
                held, notes = None, []
                if self.location_shield is not None:
                    arguments, held, notes = self.location_shield.screen(name, tool.permission, arguments)
                if held:
                    result = ToolResult(name, False, error=held, permission="LOCATION_PRIVACY")
                    self._record_failure(name, held, policy=True)
                    result.duration_ms = int((time.perf_counter() - started) * 1000)
                    return result
                budget.consume(name)
                # Registered while it runs, so the status line can say which
                # tool is taking the time instead of only that something is.
                from app.activity import ACTIVITY

                self._publish("TOOL_STARTED", name, arguments=arguments, call_id=call_id)
                announced = True
                with ACTIVITY.track("tool", name, PRIVATE_TEXT if is_private_tool(name) else _describe_arguments(arguments)):
                    self.interlock.check(ticket)
                    output = tool.handler(**arguments)
                if notes and isinstance(output, dict):
                    output = {**output, "location_privacy": notes}
                result = ToolResult(name, True, output=output, permission=permission.decision.value)
                self._publish("TOOL_COMPLETED", name, call_id=call_id)
        except Exception as exc:
            result = ToolResult(name, False, error=f"{type(exc).__name__}: {exc}")
            self._record_failure(name, exc=exc)
            self._publish("TOOL_FAILED", name, error=f"{type(exc).__name__}: {exc}", started=announced, call_id=call_id)
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        if self.understanding is not None:
            # After duration_ms is set, and inside a guard: bookkeeping about
            # tools must never be the reason a tool call fails.
            try:
                self.understanding.observe(replace(result, output=None, error=PRIVATE_TEXT if result.error else "")
                                           if is_private_tool(name) else result)
            except Exception:
                log_swallowed(_log, "recording a tool outcome for tool understanding", level=logging.WARNING)
        return result

    def _publish(self, event_type: str, tool: str, **payload: Any) -> None:
        """Say what this dispatch is doing, for anything that wants to watch.

        TOOL_STARTED and TOOL_COMPLETED were in the event taxonomy from the
        beginning and nothing ever emitted them -- so a task looked, from
        outside, like a silent pause followed by an answer. The vocabulary
        existed; the sentences did not.
        """
        if self.events is None:
            return
        try:
            from app.execution import current_context

            context = current_context()
            if is_private_tool(tool):
                payload = redact_private_payload(payload)
            self.events.publish(
                event_type, "tools", {"tool": tool, **payload},
                session_id=context.session_id, turn_id=context.turn_id,
                task_id=context.task_id,
            )
        except Exception:
            # Narration must never be the reason a tool call fails.
            log_swallowed(_log, "publishing a tool event")

    def _record_failure(self, tool: str, reason: str = "", *, exc: Exception | None = None, policy: bool = False) -> None:
        """Record a tool failure as a fact, if the runtime gave us somewhere to.

        A tool that raised and a tool that was refused are both things the user
        may ask about afterwards, and neither should be reconstructed from the
        model's memory of the conversation. Correlation comes from the
        execution scope, so the record names the same task the approval did.
        """
        if self.failures is None:
            return
        try:
            from app.failures import FailureClass

            if is_private_tool(tool):
                self.failures.record(
                    stage="tool", tool=tool, exception_type=type(exc).__name__ if exc else "",
                    exception_message=PRIVATE_TEXT,
                    failure_class=FailureClass.POLICY_DENIED if policy else FailureClass.UNKNOWN,
                )
            elif exc is not None:
                evidence = exc.failure_fields() if hasattr(exc, "failure_fields") else {}
                self.failures.from_exception(exc, stage="tool", tool=tool, **evidence)
            else:
                self.failures.record(
                    stage="tool", tool=tool, exception_message=reason,
                    failure_class=FailureClass.POLICY_DENIED if policy else FailureClass.UNKNOWN,
                )
        except Exception:
            # Diagnostics must never be the reason a tool call blows up.
            log_swallowed(_log, "recording a tool failure", level=logging.WARNING)
