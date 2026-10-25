"""Readable operational self-description, assembled from observed state."""
from __future__ import annotations


def describe_self(state: dict) -> str:
    camera, voice = state.get("camera", {}), state.get("voice", {})
    change = state.get("self_change", {})
    lines = ["HAYAT · observed system state", f"Observed: {state.get('observed_at', 'unavailable')}", "",
             f"Current activity: {state.get('activity', 'unknown')}",
             f"Current task: {state.get('active_task') or 'No independent task reported'}",
             f"Model: {state.get('model') or 'No active model'}",
             f"Provider: {state.get('provider') or 'Unavailable'}",
             f"Camera: {'active' if camera.get('active') else camera.get('state', 'unknown')}",
             f"Voice: {voice.get('state', 'unknown')} · {'enabled' if voice.get('enabled') else 'disabled'}",
             f"Registered tools: {state.get('tool_count', 0)}",
             f"Restart: {'required; code on disk differs from this process' if change.get('restart_required') else 'not reported as required'}",
             "", "What my records support"]
    records = state.get("tools_i_know_how_they_behave") or []
    if not records:
        lines.append("No tool-use evidence is available in this snapshot.")
    for row in records:
        lines.append(f"• {row['tool']}: {row['worked']} worked / {row['calls']} calls; {row['failed']} failed · {row['verdict']}")
    execution = state.get('execution') or {}
    lines.extend(['', 'Task evidence in this session'])
    current = execution.get('current')
    if current:
        lines.extend([f"Running: {current['goal']}",
                      f"Current tool: {current.get('current_tool') or 'No tool selected yet'}",
                      f"Current task attempts: {current['verified_attempts']} verified; {current['failed_checks']} failed checks; "
                      f"{current['unchecked_attempts']} unchecked."])
    previous = execution.get('last_task')
    if previous:
        lines.extend([f"Last task: {previous['status']} — {previous['goal']}",
                      f"Recorded attempts: {previous['attempts']}; verified: {previous['verified_attempts']}; "
                      f"failed checks: {previous['failed_checks']}; unchecked: {previous['unchecked_attempts']}.",
                      'Goal verification: ' + ('passed' if previous['goal_verified'] is True else
                                               'failed' if previous['goal_verified'] is False else 'not recorded')])
        if previous['verification_conflict']:
            lines.append('Needs review: completion was reported despite a failed goal check.')
    else:
        lines.append('No completed task record in this session; no background accomplishments inferred.')
    lines.append('Attempt counts include retries; they are not a percentage of the task completed.')
    lines.extend(["", "How to read this", "This view reads runtime state and recorded outcomes; it does not ask a model to guess.",
                  "A registered tool can still need a device, service or permission. Past success does not guarantee the next action.",
                  "The brain mesh shows software parts and their connections. A model's interpretation is distinct from an observed result."])
    return "\n".join(lines)
