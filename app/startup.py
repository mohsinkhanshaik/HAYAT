"""Read-only startup readiness with explicit configured and deferred states."""
from __future__ import annotations

from datetime import UTC, datetime
import re


BOOT_STAGES = (
    ('imports', 'Runtime modules'), ('settings', 'Configuration and owner checks'),
    ('stores', 'Memory and permission stores'), ('tools', 'Tool contracts and workspaces'),
    ('models', 'Model routing and research'), ('skills', 'Skills and planning'),
    ('services', 'Brain mesh and service policy'), ('ready', 'Runtime assembled'),
    ('workspace', 'Desktop workspace'),
)


def readiness_request(text):
    return bool(re.fullmatch(r"(?:show|check|inspect|run|report)(?: my| your| hayat)? (?:startup|boot)(?: readiness| status| checks?)[.!?]?",
                             ' '.join(str(text).lower().split())))


def readiness(runtime):
    """Read local wiring, not live provider reachability or model accuracy."""
    registry = runtime.registry
    skills = runtime.skills
    inventory = skills.inventory()
    model = getattr(runtime, 'model', None)
    model_requested = bool(getattr(runtime, 'model_generation_requested', model is not None))
    learner = getattr(runtime, 'learner', None)
    checks = [
        {'name': 'Tool contracts', 'state': 'ready' if registry.names() else 'attention',
         'detail': f'{len(registry.names())} registered tools; individual execution is not verified here.'},
        {'name': 'Skills', 'state': 'deferred' if getattr(runtime, 'safe_mode', False) else 'attention' if inventory['issues'] else 'ready',
         'detail': f"{inventory['trusted']} trusted skills; {inventory['untrusted']} inactive untrusted definitions; "
                   f"{len(inventory['issues'])} definition or dependency issues."},
        {'name': 'Reply model', 'state': 'configured' if model is not None else 'attention' if model_requested else 'deferred',
         'detail': 'Model client configured; generation and loaded weights are not tested by startup.' if model is not None else
                   'The requested reply model was not available. Inspect its local server and selected model in Voice & Models; deterministic tools remain available.' if model_requested else
                   'Model generation was disabled for this launch; deterministic tools remain available.'},
        {'name': 'Perception', 'state': 'deferred',
         'detail': 'Camera, microphone and screen capture are not activated by this readiness check.'},
        {'name': 'Background learning', 'state': 'running' if learner and learner.running else 'paused',
         'detail': 'Uses the existing configured service and budget policy; this check starts no work.'},
    ]
    mesh = getattr(runtime, 'brain_mesh', None)
    if mesh is not None:
        try:
            team = mesh.team_checks()
            checks.append({'name': 'Brain contracts', 'state': 'ready' if team['passed'] else 'attention',
                           'detail': 'Shared authority, tool bus, memory and verification wiring checked.',
                           'failed': [name for name, passed in team['checks'].items() if not passed]})
        except Exception:
            import logging
            from app.logging import log_swallowed
            log_swallowed(logging.getLogger(__name__), 'inspecting startup brain contracts')
            checks.append({'name': 'Brain contracts', 'state': 'attention',
                           'detail': 'Contract inspection did not complete. Open Diagnostics for the recorded details.'})
    actions = []
    if inventory['issues']:
        actions.append('Review skipped skill definitions and their declared tool dependencies.')
    if model is None:
        actions.append('Use deterministic tools now; inspect Voice & Models to configure local generation.')
    actions.append('Run the explicit brain-mesh self-check for deeper subsystem evidence.')
    return {'checked_at': datetime.now(UTC).isoformat(), 'checks': checks, 'skills': inventory,
            'next_actions': actions, 'ready': not any(item['state'] == 'attention' for item in checks),
            'safe_mode': bool(getattr(runtime, 'safe_mode', False)),
            'scope': 'Local wiring snapshot only. Optional engines load on demand; no claim all models are loaded, '
                     'all tools work, internet access works, or answers are accurate.'}


def readiness_lines(report):
    lines = ['Startup readiness', 'Checked: ' + report['checked_at']]
    lines += [item['name'] + ': ' + item['state'] + ' · ' + item['detail'] for item in report['checks']]
    if report['skills']['issues']:
        lines += ['Skills needing attention:']
        lines += ['• ' + item['name'] + ': ' + item['reason'] for item in report['skills']['issues']]
    lines += ['', 'Next steps:', *['• ' + action for action in report['next_actions']], '', report['scope']]
    return lines


def register_startup_tools(registry, runtime):
    registry.register('startup_readiness', 'Inspect local startup, skill dependencies and brain wiring; list deferred engines and next steps without starting services.',
                      {'type': 'object', 'properties': {}, 'additionalProperties': False},
                      'low', 'system_read', lambda: readiness(runtime()))
