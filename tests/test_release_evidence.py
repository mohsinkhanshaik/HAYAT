from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.release_evidence import (build_manifest, check_problems, run_check, source_state,
                                  verify_manifest)

PASSING_JUNIT = '<testsuite tests="2" time="1.5"><testcase name="a"/><testcase name="b"/></testsuite>'


def _repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    for args in (['init', '-q'], ['config', 'user.email', 't@example.invalid'], ['config', 'user.name', 'T'],
                 ['config', 'core.autocrlf', 'false']):
        subprocess.run(['git', *args], cwd=root, check=True)
    (root / 'app.py').write_text('print(1)\n')
    (root / '.gitignore').write_text('ignored/\n')
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-qm', 'init'], cwd=root, check=True)
    return root


def _write_junit(root, text=PASSING_JUNIT):
    path = root / 'ignored' / 'pytest.xml'
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def _passing_run(root):
    junit = _write_junit(root)
    before = source_state(root)
    check = run_check('tests', [sys.executable, '-c', 'pass'], root=root, timeout=60, junit=junit)
    return build_manifest([check], before, source_state(root))


def test_source_hash_names_commit_edits_and_untracked_files_but_not_ignored_ones(tmp_path):
    root = _repo(tmp_path)
    clean = source_state(root)
    assert not clean['dirty'] and len(clean['head']) == 40
    _write_junit(root)
    assert source_state(root)['tree'] == clean['tree']
    (root / 'app.py').write_text('print(2)\n')
    edited = source_state(root)
    assert edited['dirty'] and edited['changed_files'] == 1 and edited['tree'] != clean['tree']
    (root / 'new.py').write_text('x = 1\n')
    added = source_state(root)
    assert added['changed_files'] == 2 and added['tree'] != edited['tree']
    (root / 'new.py').write_text('x = 2\n')
    assert source_state(root)['tree'] != added['tree']


def test_committing_the_tested_files_keeps_the_evidence_valid_and_the_index_untouched(tmp_path):
    root = _repo(tmp_path)
    (root / 'app.py').write_text('print(5)\n')
    (root / 'new.py').write_text('x = 1\n')
    manifest = _passing_run(root)
    assert subprocess.run(['git', 'status', '--porcelain'], cwd=root, capture_output=True, text=True).stdout.count('??') == 1
    subprocess.run(['git', 'add', 'app.py', 'new.py'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-qm', 'tested'], cwd=root, check=True)
    assert not source_state(root)['dirty']
    result = verify_manifest(manifest, root)
    assert result['passed'], result['problems']


def test_gate_output_folder_is_not_part_of_the_source(tmp_path):
    root = _repo(tmp_path)
    clean = source_state(root)
    evidence = root / 'data' / 'benchmarks' / 'release'
    evidence.mkdir(parents=True)
    (evidence / 'latest.json').write_text('{}')
    assert source_state(root)['tree'] == clean['tree']


def test_a_passing_run_records_command_exit_junit_and_artifact_hashes_and_verifies(tmp_path):
    root = _repo(tmp_path)
    manifest = _passing_run(root)
    check = manifest['checks'][0]
    assert manifest['passed'] and manifest['promotion'] == 'eligible', manifest['problems']
    assert check['return_code'] == 0 and check['command'][1:] == ['-c', 'pass']
    assert check['junit']['tests_passed'] == 2 and check['junit']['verified_from_junit']
    assert all(len(digest) == 64 for digest in check['artifacts'].values())
    assert verify_manifest(json.loads(json.dumps(manifest)), root)['passed']


def test_the_real_exit_status_wins_over_a_clean_junit(tmp_path):
    root = _repo(tmp_path)
    junit = _write_junit(root)
    check = run_check('tests', [sys.executable, '-c', 'raise SystemExit(3)'], root=root, timeout=60, junit=junit)
    manifest = build_manifest([check], source_state(root), source_state(root))
    assert not manifest['passed'] and 'tests: exited 3' in manifest['problems']


@pytest.mark.parametrize('junit, expected', [
    (None, 'artifact missing'),
    ('<testsuite tests="1" time="1"><testcase><failure/></testcase></testsuite>', '1 failed'),
    ('<testsuite tests="1" time="1"><testcase><error/></testcase></testsuite>', '1 errors'),
    ('<testsuite time="1"/>', 'JUnit evidence unusable'),
])
def test_missing_or_failing_evidence_cannot_report_a_pass(tmp_path, junit, expected):
    root = _repo(tmp_path)
    path = _write_junit(root, junit) if junit else root / 'ignored' / 'missing.xml'
    check = run_check('tests', [sys.executable, '-c', 'pass'], root=root, timeout=60, junit=path)
    assert not check['passed']
    assert any(expected in problem for problem in check_problems(check))


def test_a_check_that_cannot_start_or_times_out_is_not_a_pass(tmp_path):
    root = _repo(tmp_path)
    missing = run_check('tool', [str(root / 'no-such-program.exe')], root=root, timeout=60)
    slow = run_check('slow', [sys.executable, '-c', 'import time; time.sleep(5)'], root=root, timeout=1)
    assert missing['return_code'] is None and not missing['passed']
    assert slow['return_code'] is None and 'did not finish' in check_problems(slow)[0]


def test_source_changing_during_the_run_blocks_the_release(tmp_path):
    root = _repo(tmp_path)
    junit = _write_junit(root)
    before = source_state(root)
    (root / 'app.py').write_text('print(3)\n')
    check = run_check('tests', [sys.executable, '-c', 'pass'], root=root, timeout=60, junit=junit)
    manifest = build_manifest([check], before, source_state(root))
    assert not manifest['passed'] and any('source changed' in problem for problem in manifest['problems'])


def test_verify_refuses_another_revision_an_edited_artifact_and_a_forged_flag(tmp_path):
    root = _repo(tmp_path)
    manifest = _passing_run(root)

    (root / 'app.py').write_text('print(4)\n')
    result = verify_manifest(manifest, root)
    assert not result['passed'] and any('another source' in problem for problem in result['problems'])
    subprocess.run(['git', 'checkout', '-q', '--', 'app.py'], cwd=root, check=True)
    assert verify_manifest(manifest, root)['passed']

    _write_junit(root, PASSING_JUNIT.replace('1.5', '1.6'))
    result = verify_manifest(manifest, root)
    assert not result['passed'] and any('artifact changed' in problem for problem in result['problems'])
    assert any('claims a pass' in problem for problem in result['problems'])

    forged = json.loads(json.dumps(manifest))
    forged['checks'][0]['return_code'] = 1
    _write_junit(root)
    assert not verify_manifest(forged, root)['passed']
    assert not verify_manifest({'passed': True, 'checks': []}, root)['passed']


def test_release_gate_verify_reports_a_missing_manifest_as_blocked(tmp_path, capsys):
    from scripts import release_gate
    assert release_gate.main(['--verify', str(tmp_path / 'none.json')]) == 1
    assert json.loads(capsys.readouterr().out)['promotion'] == 'blocked'


def test_the_gate_runs_pytest_outside_the_sandboxed_data_folder():
    from app.config import PROJECT_ROOT
    from scripts import release_gate
    assert (PROJECT_ROOT / "data") not in release_gate.PYTEST_BASETEMP.resolve().parents
