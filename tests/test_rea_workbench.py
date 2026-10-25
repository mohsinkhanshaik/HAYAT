import json
import pytest

from app.rea_workbench import DISPLAY_LIMIT, WorkbenchSession, parse_arguments, result_views


@pytest.mark.parametrize('raw', ['[]', 'null', '{"x":NaN}', '{"x":Infinity}', '{"x":1e9999}', 'x' * 64001],
                         ids=['array', 'null', 'nan', 'infinity', 'overflow', 'oversized'])
def test_argument_parser_rejects_invalid_objects_and_nonfinite_numbers(raw):
    with pytest.raises(ValueError):
        parse_arguments(raw)


def test_summary_reports_returned_counts_and_provenance_without_inventing_coverage():
    data = {'result': {'artifact': {'path': 'sample.dll', 'format': 'pe', 'sha256': 'abc', 'byte_length': 100},
                       'graph': {'modules': [{'id': 'one'}, {'id': 'two'}], 'imports': []}}}
    summary, report = result_views({'structured': data, 'report': json.dumps(data)}, operation='inspect_managed_artifact')
    assert 'SHA-256: abc' in summary and '2 modules' in summary and '0 imports' in summary
    assert 'not complete target coverage' in summary
    assert json.loads(report) == data


def test_prepared_action_and_failure_are_distinct_from_completed_analysis():
    summary, _ = result_views({'kind': 'reverse_engineer', 'how_to_confirm': 'Review values'}, operation='open_binary')
    assert 'not executed' in summary and 'Review values' in summary
    summary, report = result_views('Target was not found', False, 'inspect')
    assert 'Failed' in summary and report == 'Target was not found'


def test_link_summary_names_source_collection_limits_and_retained_view():
    value = {'link_analysis': True, 'source_url': 'https://example.com/app', 'resources': [{}],
        'skipped_resources': [{'reason': 'cross-origin'}], 'manifest': 'manifest.json',
        'scope': 'Static frontend only; private/backend code is not recovered.',
        'report': json.dumps({'public_link': {'final_url': 'https://example.com/app', 'title': 'App',
            'analysis_status': 'REA static analysis completed', 'analysis_path': 'analysis.json'},
            'analysis': {'full_result_transport_limited': True}})}
    summary, report = result_views(value, operation='reverse_engineer_link')
    assert 'Scripts collected: 1' in summary and 'Collection notices: 1' in summary
    assert 'Source: https://example.com/app' in summary and 'Saved analysis: analysis.json' in summary
    assert 'this REA session' in summary and 'backend code is not recovered' in summary
    assert json.loads(report)['public_link']['title'] == 'App'


def test_session_history_is_bounded_and_report_truncation_is_explicit():
    session = WorkbenchSession()
    for index in range(20):
        session.remember('inspect', {'path': str(index)}, 180, True, {'report': 'x' * 300_000}, 1.2)
    rows = session.results
    assert len(rows) <= session.MAX_RESULTS
    assert sum(len(row['summary']) + len(row['report']) for row in rows) <= session.MAX_CHARACTERS
    assert json.loads(rows[0]['arguments']) == {'path': '19'}
    rows.clear()
    assert session.results  # callers receive a snapshot of the list
    _, report = result_views({'report': 'x' * (DISPLAY_LIMIT + 100)})
    assert 'Display limit reached' in report and len(report) < DISPLAY_LIMIT + 100
