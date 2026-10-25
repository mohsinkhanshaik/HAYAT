"""Evidence checks use declared fixture records, never real-world verification."""
from copy import deepcopy

import pytest

from app.invention_evidence import audit_markdown, audit_project, public_source_url
from tests.test_executive_invention import ScriptedModel, accepted, bench, candidate


STAMP = '2026-10-23T12:00:00+00:00'
URL = 'https://www.nist.gov/fixture-invention'
DIGEST = 'a' * 64


def research():
    return {'complete': True, 'sources': [
        {'url': URL, 'final_url': URL, 'title': 'Declared source fixture', 'primary': True,
         'retrieved_at': STAMP, 'status_code': 200, 'content_hash': DIGEST}],
        'claims': [{'claim_id': 'c1', 'text': 'This is an explicitly declared test assertion.', 'status': 'SUPPORTED',
                    'evidence': [{'claim_id': 'c1', 'source_url': URL, 'source_hash': DIGEST,
                                  'retrieved_at': STAMP, 'excerpt': 'declared test assertion', 'support_type': 'DIRECT'}]}]}


def snapshot(tmp_path):
    workbench = bench(tmp_path, None)
    project = workbench.create('Test a proposed sensor', constraints=['Budget below $50'],
                               tests=[{'inputs': {'x': 2}, 'expected': 4}])
    project['research'] = research()
    return project


def test_audit_distinguishes_recorded_provenance_from_verification(tmp_path):
    project = snapshot(tmp_path)
    before = deepcopy(project)
    result = audit_project(project)
    assert result['contract'] == 'intact' and result['stage'] == 'DRAFT'
    assert result['traceable_claims'] == result['source_count'] == 1
    assert not result['claims'][0]['citations'][0]['excerpt_checked']
    assert not result['physical_validation_verified'] and not result['novelty_verified']
    assert result['constraints'][0]['status'] == 'not measured'
    assert {row['kind'] for row in result['next_experiments']} >= {'holdout', 'physical', 'prior_art'}
    assert project == before, 'Assessing an invention must not rewrite its evidence.'
    rendered = audit_markdown(result)
    assert URL in rendered and DIGEST in rendered and 'source text unavailable' in rendered
    assert 'not measured' in rendered and 'unverified' in rendered


@pytest.mark.parametrize('change', ['url', 'hash', 'time', 'claim', 'excerpt'])
def test_fabricated_or_mismatched_citations_never_count_as_traceable(tmp_path, change):
    project = snapshot(tmp_path)
    evidence = project['research']['claims'][0]['evidence'][0]
    if change == 'url':
        evidence['source_url'] = 'https://www.nasa.gov/not-retrieved'
    elif change == 'hash':
        evidence['source_hash'] = 'b' * 64
    elif change == 'time':
        evidence['retrieved_at'] = '2026-10-22T12:00:00+00:00'
    elif change == 'claim':
        evidence['claim_id'] = 'another-claim'
    else:
        project['research']['sources'][0]['text'] = 'The source never included the quoted passage.'
    result = audit_project(project)
    assert result['traceable_claims'] == 0
    assert result['claims'][0]['rejected_citations'] == 1


def test_excerpt_check_and_conflicts_are_visible_without_promoting_reported_status(tmp_path):
    project = snapshot(tmp_path)
    source = project['research']['sources'][0]
    source['text'] = 'This contains the declared test assertion for a fixture.'
    claim = project['research']['claims'][0]
    claim['contradictions'] = [deepcopy(claim['evidence'][0])]
    result = audit_project(project)
    assert result['claims'][0]['citations'][0]['excerpt_checked']
    assert result['conflicting_claims'] == 1
    assert result['claims'][0]['reported_status'] == 'SUPPORTED'
    assert 'conflict reported' in audit_markdown(result)


@pytest.mark.parametrize('change', ['unsafe_redirect', 'quarantined', 'no_timestamp', 'bad_hash', 'failed_fetch'])
def test_ineligible_sources_are_excluded_with_a_reason(tmp_path, change):
    project = snapshot(tmp_path)
    source = project['research']['sources'][0]
    source.update({'unsafe_redirect': {'final_url': 'http://127.0.0.1/private'},
                   'quarantined': {'metadata': {'knowledge_poisoning_defense': {'quarantine': True}}},
                   'no_timestamp': {'retrieved_at': 'unknown'}, 'bad_hash': {'content_hash': 'made-up'},
                   'failed_fetch': {'status_code': 403}}[change])
    result = audit_project(project)
    assert result['source_count'] == result['traceable_claims'] == 0
    assert result['excluded_sources'][0]['reason']


def test_source_duplicates_and_copied_documents_do_not_inflate_document_count(tmp_path):
    project = snapshot(tmp_path)
    source = project['research']['sources'][0]
    project['research']['sources'] += [deepcopy(source), {**source, 'url': 'https://www.nasa.gov/copied',
                                                       'final_url': 'https://www.nasa.gov/copied'}]
    result = audit_project(project)
    assert result['source_count'] == result['publisher_hosts'] == 2
    assert result['distinct_document_hashes'] == 1
    assert 'do not establish source independence' in result['scope']


@pytest.mark.parametrize('stance', [[], {}, 17, None])
def test_malformed_stance_does_not_crash_audit_or_create_a_conflict(tmp_path, stance):
    project = snapshot(tmp_path)
    project['research']['claims'][0]['evidence'][0]['stance'] = stance
    audit = audit_project(project)
    assert audit['traceable_claims'] == 1 and audit['conflicting_claims'] == 0


def test_fabricated_suffix_cannot_hide_beyond_displayed_excerpt_limit(tmp_path):
    project = snapshot(tmp_path)
    source = project['research']['sources'][0]
    source['text'] = 'real words ' * 180
    project['research']['claims'][0]['evidence'][0]['excerpt'] = source['text'] + 'fabricated ending'
    audit = audit_project(project)
    assert audit['traceable_claims'] == 0
    assert any('did not match' in gap for gap in audit['gaps'])


def test_numeric_success_leaves_physical_and_constraint_testing_open(tmp_path):
    workbench = bench(tmp_path, ScriptedModel(candidate(), accepted()))
    project = workbench.create('Double the inputs', constraints=['Cost less than $50'],
                               tests=[{'inputs': {'x': 2}, 'expected': 4}])
    done = workbench.run(project['id'], cycles=1, research=False, work_out=False)
    audit = audit_project(done)
    assert audit['stage'] == 'NUMERIC_CASES_PASSED' and audit['numeric_cases_passed']
    assert not audit['physical_validation_verified'] and audit['constraints'][0]['status'] == 'not measured'
    done['tests'][0]['expected'] = 5
    audit = audit_project(done)
    assert audit['stage'] == 'CONTRACT_CHANGED' and not audit['numeric_cases_passed']


def test_non_numeric_design_gets_experiment_plan_and_research_gap(tmp_path):
    workbench = bench(tmp_path, ScriptedModel(candidate(''), accepted()))
    project = workbench.create('Propose a sensor')
    done = workbench.run(project['id'], cycles=1, research=False)
    audit = audit_project(done)
    assert audit['stage'] == 'PROPOSAL_REQUIRES_EXPERIMENTS'
    assert audit['next_experiments'][0]['kind'] == 'acceptance'
    assert 'Research is absent or incomplete.' in audit['gaps']


def test_report_exports_fresh_audit_and_omits_unusable_source_links(tmp_path):
    workbench = bench(tmp_path, None)
    project = snapshot(tmp_path)
    project['research']['sources'] += [{**project['research']['sources'][0],
                                       'url': 'http://127.0.0.1/private', 'final_url': 'http://127.0.0.1/private'}]
    workbench._save(project)
    assert workbench.assess(project['id'])['source_count'] == 1
    report = workbench.report_markdown(project['id'])
    assert 'Evidence and validation readiness' in report and URL in report
    assert '127.0.0.1' not in report
    project['research']['complete'] = False
    workbench._save(project)
    assert 'Research is absent or incomplete.' in workbench.report_markdown(project['id'])


@pytest.mark.parametrize('url', ['file:///C:/secret', 'https://name:password@nist.gov/', 'http://localhost/x',
                               'http://127.0.0.1/', 'http://[::1]/', 'https://office.internal/',
                               'https://somewhere.onion/', 'https://nist.gov:8080/', 'https://nist.gov/?api_key=secret',
                               'https://nist.gov/<b>', 'https://nist.gov/\nspoof'])
def test_audit_never_emits_private_or_credential_bearing_source_links(url):
    assert public_source_url(url) == ''


def test_public_url_normalization_preserves_non_default_allowed_port():
    assert public_source_url('HTTPS://WWW.NIST.GOV:443/page#part') == 'https://www.nist.gov/page'
    assert public_source_url('http://www.nist.gov:443/page') == 'http://www.nist.gov:443/page'


def test_audit_bounds_unknown_records_and_does_not_render_injected_markdown(tmp_path):
    project = snapshot(tmp_path)
    project['research']['sources'] = project['research']['sources'] * 140
    project['research']['claims'][0]['text'] = '# PASS\n[trust me](file:///secret)'
    audit = audit_project(project)
    assert audit['audit_limited'] and audit['source_count'] == 1
    rendered = audit_markdown(audit)
    assert '\\# PASS' in rendered and '\\[trust me\\]' in rendered


def test_read_only_assess_command_is_governed_and_does_not_require_a_model(tmp_path):
    from app.agent.planner import Planner
    from app.invention_commands import command
    from app.safety.permissions import PermissionManager
    from app.tools.invention import register_invention_tools
    from app.tools.registry import ToolRegistry
    workbench = bench(tmp_path, None)
    project = workbench.create('Assess a prototype')
    registry = ToolRegistry(PermissionManager({'default': 'DENY', 'permissions': {'filesystem_read': 'ALLOW'}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    text = f"Assess invention {project['id']}"
    name, arguments = command(text)
    assert name == Planner(registry).create_plan(text)[0].tool == 'invention_assess'
    result = registry.execute(name, arguments)
    assert result.success and result.output['stage'] == 'DRAFT'
    assert not registry.execute('invention_simulate', {'project_id': project['id'], 'formula': 'x'}).success
    registry.interlock.halt()
    assert not registry.execute(name, arguments).success
