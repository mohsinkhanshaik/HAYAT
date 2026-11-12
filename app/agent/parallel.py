"""Running a plan's independent read-only steps at the same time.

A plan like "weather in Dubai, weather in Lahore, weather in Karachi" or "read these three files" was executed one
step after another, so three 2-second lookups took 6 seconds even though none needed another's answer. This module
finds such a run of steps and starts them together. The agent loop then consumes each result in plan order exactly
as if it had just run the step itself: the same verification, the same recording, events, retries and replanning.
Only the waiting overlaps.

What may run together is decided by rules, never by the model:

* the tool is registered, its risk is ``low`` and its permission category only reads
  (:data:`PARALLEL_PERMISSIONS`): files, system state, web search, public pages and APIs, knowledge, connector and
  MCP reads. Anything that writes, sends, types, clicks, opens an application, uses the browser window, the camera,
  the clipboard or the screen stays sequential, because order or a shared device matters there;
* the step's arguments hold no placeholder (``{{...}}``) that could depend on what ran before it;
* the run is consecutive: the first step that does not qualify ends it, so a write after two reads still waits
  for both reads, and nothing is reordered around it;
* at most :data:`MAX_WORKERS` at once, and never more than the task's remaining call budget.

Every call still goes through ``ToolRegistry.execute``: the permission check, authority, location shield, call
budget and failure log apply to each one, in the caller's execution context (``contextvars`` are copied into each
worker thread, so approvals bind to the right task). A step whose early result fails is retried by the loop the
ordinary way.
"""
from __future__ import annotations

import contextvars
import json
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from app.tools.registry import ToolResult

#: Permission categories whose tools only read, so running two at once cannot change what either sees.
PARALLEL_PERMISSIONS = frozenset({
    "filesystem_read", "system_read", "web_search", "public_web_read", "public_api_get", "knowledge_research",
    "connector_read", "mcp_read", "dependency_inspect",
})
#: The most steps started together. Public sources are polite-rate-limited and the machine is shared with the model.
MAX_WORKERS = 4


def parallel_safe(step: Any, registry: Any) -> bool:
    """Whether this one step may run alongside its neighbours."""
    try:
        spec = registry.get(step.tool)
    except Exception:
        return False
    if str(getattr(spec, "risk", "")).lower() != "low" or getattr(spec, "permission", None) not in PARALLEL_PERMISSIONS:
        return False
    try:
        arguments = json.dumps(step.arguments, default=str)
    except (TypeError, ValueError):
        return False
    return "{{" not in arguments


def independent_run(plan: list[Any], start: int, registry: Any, *, limit: int,
                    already: set[int] = frozenset()) -> list[int]:
    """Indices of the consecutive parallel-safe steps from ``start``; empty unless two or more qualify.

    ``already`` holds ``id()`` of steps whose early result is waiting; the run stops there, so a step is never
    started twice (after a recovery is spliced in before it, for example).
    """
    indices: list[int] = []
    for index in range(start, len(plan)):
        if (len(indices) >= max(0, limit) or id(plan[index]) in already
                or not parallel_safe(plan[index], registry)):
            break
        indices.append(index)
    return indices if len(indices) >= 2 else []


def run_together(executor: Any, steps: list[Any], history: list[dict[str, Any]]) -> list[Future]:
    """Start the steps at once, each in a copy of the caller's context; one future per step, in the steps' order.

    The caller takes each result when it reaches that step, so the first result is checked and recorded while the
    later ones are still running, and the task's live state always names the step being handled.
    """
    snapshot = list(history)

    def call(step: Any, context: contextvars.Context) -> ToolResult:
        try:
            return context.run(executor.execute, step, snapshot)
        except Exception as exc:  # the loop treats it like any failed call: verify, retry, replan
            return ToolResult(step.tool, False, error=f"{type(exc).__name__}: {exc}")

    pool = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(steps)), thread_name_prefix="hayat-step")
    try:
        return [pool.submit(call, step, contextvars.copy_context()) for step in steps]
    finally:
        pool.shutdown(wait=False)  # the submitted calls finish; the pool takes no more
