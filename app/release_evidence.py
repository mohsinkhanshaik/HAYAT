"""Release evidence bound to the exact source and execution that produced it.

A release is "eligible" only when every check really ran, exited 0, left the
evidence it promised (JUnit with no failures or errors, artifacts present),
and the source did not change while it ran.  ``verify_manifest`` repeats
those checks later, so a manifest from another revision, an edited JUnit
file or a deleted artifact cannot pass for the current tree.
"""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from app.security.xml import fromstring

# The gate's own output is evidence, not source; hashing it would make every
# run change the "source" it is meant to describe.
EVIDENCE_DIR = "data/benchmarks/release"


def junit_outcomes(path: Path) -> dict[str, Any]:
    """Derive outcomes from test cases, rejecting contradictory suite totals."""
    raw = path.read_bytes()
    root = fromstring(raw)
    if root.tag not in {'testsuite', 'testsuites'}:
        raise ValueError('Expected a JUnit testsuite or testsuites root.')
    cases = list(root.iter('testcase'))
    if not cases:
        raise ValueError('JUnit evidence contains no test cases.')

    def counts(rows):
        result = {'tests': len(rows), 'failures': 0, 'errors': 0, 'skipped': 0}
        for row in rows:
            outcomes = [name for name in ('failure', 'error', 'skipped') if row.find(name) is not None]
            if len(outcomes) > 1:
                raise ValueError('JUnit test case has conflicting outcomes.')
            if outcomes:
                result[{'failure': 'failures', 'error': 'errors', 'skipped': 'skipped'}[outcomes[0]]] += 1
        return result

    total = counts(cases)
    if root.tag == 'testsuites':
        for name in total:
            if name in root.attrib and int(root.attrib[name]) != total[name]:
                raise ValueError(f'JUnit aggregate {name} disagrees with its test cases.')
    seconds = 0.0
    timed = False
    for suite in root.iter('testsuite'):
        actual = counts(list(suite.iter('testcase')))
        for name in total:
            if name in suite.attrib and int(suite.attrib[name]) != actual[name]:
                raise ValueError(f'JUnit suite {name} disagrees with its test cases.')
    # Pytest records suite time including setup and teardown. Only count
    # outer suites, so nested aggregates do not double-count elapsed time.
    outer_suites = [root] if root.tag == 'testsuite' else root.findall('testsuite')
    for suite in outer_suites:
        value = suite.get('time')
        if value is None:
            raise ValueError('JUnit suite time is missing.')
        duration = float(value)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError('JUnit suite time must be finite and nonnegative.')
        seconds += duration
        timed = True
    if not timed:
        raise ValueError('JUnit evidence has no timed suite.')
    return {
        'tests_passed': total['tests'] - total['failures'] - total['errors'] - total['skipped'],
        'tests_failed': total['failures'], 'tests_errors': total['errors'],
        'tests_skipped': total['skipped'], 'tests_total': total['tests'],
        'seconds': seconds, 'source': str(path.resolve()),
        'sha256': hashlib.sha256(raw).hexdigest(), 'verified_from_junit': True,
    }


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> bytes:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, check=True, timeout=300,
                          env=env).stdout


def source_state(root: Path) -> dict[str, Any]:
    """Name the exact source by content: the git tree of the working files.

    ``tree`` is the id of a git tree built from every tracked and untracked,
    non-ignored file as it is on disk, in a temporary index, so the real
    index is never touched. It depends only on content: committing the
    tested files unchanged gives a commit with this same tree, and any edit
    anywhere git can see gives a different one.
    """
    root = Path(root)
    head = _git(root, 'rev-parse', 'HEAD').decode().strip()
    git_dir = Path(_git(root, 'rev-parse', '--absolute-git-dir').decode().strip())
    with tempfile.TemporaryDirectory() as folder:
        index = Path(folder) / 'index'
        if (git_dir / 'index').is_file():
            # Reusing stat data means only changed files are re-read. copy2 keeps the
            # mtime git uses to spot "racily clean" entries; a fresh mtime would let
            # a same-second edit pass as unchanged.
            shutil.copy2(git_dir / 'index', index)
        env = {**os.environ, 'GIT_INDEX_FILE': str(index)}
        _git(root, 'add', '-A', '--', '.', env=env)
        _git(root, 'rm', '-r', '-q', '--cached', '--ignore-unmatch', '--', EVIDENCE_DIR, env=env)
        tree = _git(root, 'write-tree', env=env).decode().strip()
        changed = [line for line in _git(root, 'diff', '--cached', '--name-only', 'HEAD', '--', '.',
                                         f':(exclude){EVIDENCE_DIR}', env=env).decode('utf-8', 'replace').splitlines()
                   if line]
    return {'head': head, 'tree': tree, 'dirty': bool(changed), 'changed_files': len(changed)}


def run_check(name: str, command: Sequence[str], *, root: Path, timeout: int,
              junit: Path | None = None, artifacts: Sequence[Path] = ()) -> dict[str, Any]:
    """Run one check and record what actually happened, not what was hoped."""
    started = datetime.now(UTC)
    clock = time.perf_counter()
    try:
        completed = subprocess.run(list(command), cwd=root, capture_output=True, timeout=timeout)
        return_code, output = completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired as exc:
        return_code = None
        output = (exc.stdout or b'') + (exc.stderr or b'') + f'\nTimed out after {timeout} s.'.encode()
    except OSError as exc:
        return_code, output = None, f'Could not start: {exc}'.encode()
    record: dict[str, Any] = {
        'name': name, 'command': [str(part) for part in command], 'return_code': return_code,
        'started_at': started.isoformat(), 'duration_s': round(time.perf_counter() - clock, 3),
        'output_sha256': hashlib.sha256(output).hexdigest(),
        'output_tail': output.decode('utf-8', 'replace').strip()[-4000:],
    }
    if junit is not None:
        record['junit'] = _junit_record(junit)
    record['artifacts'] = {str(path): file_sha256(path) for path in [*([junit] if junit else []), *artifacts]}
    record['passed'] = check_passed(record)
    return record


def _junit_record(path: Path) -> dict[str, Any]:
    try:
        return junit_outcomes(Path(path))
    except (OSError, ValueError) as exc:
        return {'evidence_error': f'{type(exc).__name__}: {exc}', 'source': str(path)}


def check_problems(check: dict[str, Any]) -> list[str]:
    """Every reason a recorded check cannot count as passing."""
    name = check.get('name', 'check')
    problems = []
    if 'return_code' in check and check['return_code'] != 0:
        problems.append(f"{name}: exited {check['return_code']}" if check['return_code'] is not None
                        else f'{name}: did not finish')
    if 'return_code' not in check and not check.get('in_process_passed'):
        problems.append(f'{name}: failed')
    junit = check.get('junit')
    if junit is not None:
        if junit.get('evidence_error'):
            problems.append(f"{name}: JUnit evidence unusable ({junit['evidence_error']})")
        else:
            if not junit.get('tests_total'):
                problems.append(f'{name}: JUnit has no tests')
            if junit.get('tests_failed') or junit.get('tests_errors'):
                problems.append(f"{name}: {junit.get('tests_failed', 0)} failed, "
                                f"{junit.get('tests_errors', 0)} errors in JUnit")
    for path, digest in (check.get('artifacts') or {}).items():
        if digest is None:
            problems.append(f'{name}: artifact missing: {path}')
    return problems


def check_passed(check: dict[str, Any]) -> bool:
    return not check_problems(check)


def build_manifest(checks: list[dict[str, Any]], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    problems = [problem for check in checks for problem in check_problems(check)]
    if not checks:
        problems.append('No checks ran.')
    if before.get('tree') != after.get('tree'):
        problems.append('The source changed while the checks ran; the results describe no single tree.')
    return {
        'created_at': datetime.now(UTC).isoformat(), 'source': before, 'source_after': after,
        'passed': not problems, 'promotion': 'eligible' if not problems else 'blocked',
        'problems': problems, 'checks': checks,
    }


def verify_manifest(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    """Re-check a manifest against today's tree and today's files.

    The stored ``passed`` flag is never trusted on its own: outcomes are
    recomputed from the recorded checks, artifact hashes are re-read and the
    source hash is recomputed.
    """
    problems: list[str] = []
    checks = manifest.get('checks') or []
    if not checks:
        problems.append('The manifest records no checks.')
    for check in checks:
        problems.extend(check_problems(check))
        for path, digest in (check.get('artifacts') or {}).items():
            current = file_sha256(Path(path) if Path(path).is_absolute() else Path(root) / path)
            if digest is not None and current is None:
                problems.append(f"{check.get('name')}: artifact no longer exists: {path}")
            elif digest is not None and current != digest:
                problems.append(f"{check.get('name')}: artifact changed since the run: {path}")
    recorded = (manifest.get('source') or {}).get('tree')
    if recorded != (manifest.get('source_after') or {}).get('tree'):
        problems.append('The source changed while the checks ran.')
    current = source_state(Path(root))
    if not recorded:
        problems.append('The manifest names no source.')
    elif recorded != current['tree']:
        problems.append(f"The evidence is for another source (tested tree {recorded[:12]}, "
                        f"files now {current['tree'][:12]}).")
    if manifest.get('passed') and problems:
        problems.append('The manifest claims a pass its own evidence does not support.')
    return {'passed': not problems, 'promotion': 'eligible' if not problems else 'blocked',
            'problems': problems, 'source_now': current}
