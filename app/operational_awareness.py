"""Bounded task evidence for HAYAT's self-view; never model-generated introspection."""
from __future__ import annotations

from typing import Any


def _attempts(events: list[dict]) -> dict[str, Any]:
    # No arguments or tool outputs: the self-view needs execution metadata only.
    rows = [row for row in events if isinstance(row, dict)]
    verdicts = [row['verification'].get('passed') if isinstance(row.get('verification'), dict) else None for row in rows]
    return {'attempts': len(rows), 'verified_attempts': sum(value is True for value in verdicts),
            'failed_checks': sum(value is False for value in verdicts),
            'unchecked_attempts': sum(value is not True and value is not False for value in verdicts),
            'last_tool': str(rows[-1].get('tool') or '')[:100] if rows else None}


def execution_state(agent: Any, session: Any) -> dict[str, Any]:
    """Keep current work distinct from a previous task, including retries and failures."""
    running = bool(getattr(agent, 'is_running', False))
    current = None
    if running:
        step = getattr(agent, 'active_step', None)
        step = step if isinstance(step, dict) else {}
        current = {'goal': str(getattr(agent, 'active_goal', '') or '')[:1000],
                   'activity': str(getattr(agent, 'active_activity', 'unknown')),
                   'planned_steps': len(getattr(agent, 'active_plan', []) or []),
                   'current_tool': step.get('tool'),
                   **_attempts(list(getattr(agent, 'active_history', []) or []))}
    outcome = getattr(session, 'last_task', None)
    previous = None
    if outcome is not None:
        goal_check = getattr(outcome, 'goal_verification', None)
        goal_check = goal_check if isinstance(goal_check, dict) else {}
        passed = goal_check.get('passed')
        if passed is True and getattr(outcome, 'success_criteria', None) == []:
            passed = None  # A tool-success default is not an independently checked goal contract.
        complete = bool(getattr(outcome, 'complete', False))
        previous = {'task_id': str(getattr(outcome, 'task_id', '') or '')[:100],
                    'goal': str(getattr(outcome, 'goal', '') or '')[:1000],
                    'status': 'waiting_for_input' if getattr(outcome, 'needs_user_input', False)
                    else 'complete' if complete else 'incomplete',
                    'completion_reported': complete,
                    'goal_verified': passed if type(passed) is bool else None,
                    'verification_conflict': complete and passed is False,
                    **_attempts(list(getattr(outcome, 'history', []) or []))}
    return {'running': running, 'current': current, 'last_task': previous,
            'basis': 'Runtime task metadata and recorded verification; attempts include retries, not percent complete.'}
