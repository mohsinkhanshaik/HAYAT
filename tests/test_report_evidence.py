from __future__ import annotations

import hashlib

import pytest
from pypdf import PdfReader

from scripts.architecture_report_evidence import regression_evidence
from scripts.build_architecture_report import build
from tests.test_architecture_report import _evidence


def test_junit_outcomes_are_derived_including_failures_errors_and_skips(tmp_path):
    path = tmp_path / 'tests.xml'
    path.write_text('<testsuites><testsuite tests="4" failures="1" errors="1" skipped="1" time="12.5">'
                    '<testcase name="pass"/><testcase name="fail"><failure/></testcase>'
                    '<testcase name="error"><error/></testcase>'
                    '<testcase name="skip"><skipped/></testcase></testsuite></testsuites>')
    result = regression_evidence(path)
    assert {name: result[name] for name in ('tests_passed', 'tests_failed', 'tests_errors', 'tests_skipped')} == {
        'tests_passed': 1, 'tests_failed': 1, 'tests_errors': 1, 'tests_skipped': 1}
    assert result['seconds'] == 12.5 and result['tests_total'] == 4
    assert result['verified_from_junit']
    assert result['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_nested_suites_do_not_double_count_test_cases_or_time(tmp_path):
    path = tmp_path / 'tests.xml'
    path.write_text('<testsuite tests="2" time="8"><testcase name="one"/>'
                    '<testsuite tests="1" time="3"><testcase name="two"/></testsuite></testsuite>')
    result = regression_evidence(path)
    assert result['tests_passed'] == 2 and result['seconds'] == 8


@pytest.mark.parametrize('text', [
    '<testsuite tests="2" time="1"><testcase/></testsuite>',
    '<testsuites failures="0"><testsuite time="1"><testcase><failure/></testcase></testsuite></testsuites>',
    '<testsuite time="1"><testcase><failure/><skipped/></testcase></testsuite>',
    '<testsuite time="1"/>',
    '<rss><testcase/></rss>',
    '<testsuite><testcase/></testsuite>',
    '<testsuite time="nan"><testcase/></testsuite>',
    '<testsuite time="-1"><testcase/></testsuite>',
    '<!DOCTYPE testsuite><testsuite time="1"><testcase/></testsuite>',
])
def test_invalid_or_contradictory_evidence_is_refused(tmp_path, text):
    path = tmp_path / 'tests.xml'
    path.write_text(text)
    with pytest.raises(ValueError):
        regression_evidence(path)


def test_missing_evidence_fails_before_runtime_inspection(tmp_path, monkeypatch):
    import scripts.architecture_report_evidence as collector
    monkeypatch.setattr('sys.argv', ['collector', '--junitxml', str(tmp_path / 'missing.xml')])
    monkeypatch.setattr(collector, 'build_runtime', lambda **kwargs: pytest.fail('Runtime must not start'))
    with pytest.raises(SystemExit) as exc:
        collector.main()
    assert exc.value.code == 2


def test_architecture_pdf_renders_actual_outcomes_date_and_recorded_mesh(tmp_path):
    evidence = _evidence()
    evidence.update(generated_at='2026-10-10T09:00:00+00:00')
    evidence['release'].update(tests_passed=3, tests_failed=1, tests_errors=2, tests_skipped=7)
    evidence['brain_mesh']['nodes'] = 20
    reader = PdfReader(build(evidence, tmp_path / 'report.pdf'))
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    assert '3 passed - 1 failed - 2 errors - 7 skipped' in text
    assert '3 passed; 1 failed; 2 errors; 7 skipped' in text
    assert 'Evidence report - 2026-10-10' in text
    assert '21 Aug 2026' not in text and 'Gemma foundation' not in text
    assert 'recorded mesh has 20 nodes' in text
