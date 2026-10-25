"""Read-only, bounded readiness checks for HAYAT's local native-analysis engines."""
from __future__ import annotations

import json
from pathlib import Path

from app.safety.sandbox import ProcessSandbox
from app.safety.sensitive import redact

SETUP_COMMANDS = ('.venv\\Scripts\\python.exe scripts/setup_rea.py\n'
                  '.venv\\Scripts\\python.exe scripts/setup_native_analysis.py\n'
                  '.venv\\Scripts\\python.exe scripts/setup_analysis_extras.py')


def engine_report(*, node: Path, script: Path, environment: dict[str, str], root: Path, runner=None) -> dict:
    """Inspect the Ghidra provider without opening a target or changing its session."""
    components = []
    for name, path in [('Node', node), ('REA', script),
                       ('Ghidra', Path(environment.get('GHIDRA_INSTALL_DIR', root / 'runtime/ghidra')) / 'Ghidra/application.properties'),
                       ('Java JDK', Path(environment.get('JAVA_HOME', root / 'runtime/java')) / 'bin/javac.exe')]:
        components.append({'name': name, 'path': str(path), 'installed': path.is_file()})
    report = {'ready': False, 'components': components, 'checks': [], 'setup_commands': SETUP_COMMANDS,
              'scope': 'Local Ghidra readiness; no target is opened, analyzed or executed.'}
    if not all(row['installed'] for row in components[:2]):
        report['error'] = 'HAYAT’s bundled Node or REA is missing. Run setup_rea.py, then check again.'
        return report
    run = runner or ProcessSandbox(output_limit=300_000).run
    try:
        response = run([str(node), str(script), 'doctor', '--provider', 'ghidra', '--json'],
                       cwd=root, timeout=45, env=environment)
        if response.get('timed_out'):
            raise RuntimeError('Engine check timed out after 45 seconds; its process tree was stopped.')
        if response.get('stdout_truncated'):
            raise RuntimeError('Engine check returned too much output; the result is incomplete.')
        result = json.loads(response.get('stdout') or '{}')
        if not isinstance(result, dict) or not isinstance(result.get('healthy'), bool):
            raise ValueError('Engine check did not return a valid readiness report.')
        checks = result.get('scope_checks')
        if not isinstance(checks, list) or not checks or not all(isinstance(row, dict) and isinstance(row.get('ok'), bool)
                                                               for row in checks):
            raise ValueError('Engine check did not return complete scoped checks.')
        report['ready'] = result['healthy'] and response.get('exit_code') == 0
        report['checks'] = checks
        if not report['ready'] and not report['checks']:
            report['error'] = 'The native engine check failed without diagnostic checks.'
    except (OSError, RuntimeError, ValueError) as error:
        report['error'] = str(error)[:1000]
    return redact(report)


def format_engine_report(value: dict) -> str:
    report = value.get('engine_check', value)
    lines = ['Ghidra is ready.' if report.get('ready') else 'Native analysis needs setup or repair.', '']
    for row in report.get('components', []):
        lines.append(f"{'Installed' if row['installed'] else 'Missing'} · {row['name']}\n  {row['path']}")
    lines.append('')
    for row in report.get('checks', []):
        lines.append(f"{'OK' if row.get('ok') else 'Needs attention'} · {row.get('name', 'Check')}: {row.get('detail') or ''}")
        if not row.get('ok') and row.get('remediation'):
            lines.append('  ' + str(row['remediation']))
    if report.get('error'):
        lines.extend(['', str(report['error'])])
    lines.extend(['', report.get('scope', ''), '', 'Setup commands (run from HAYAT’s folder):',
                  report.get('setup_commands', SETUP_COMMANDS), '',
                  'Refreshing operations keeps existing inputs and results. Restart HAYAT after replacing an engine used by an open session.'])
    return '\n'.join(lines)
