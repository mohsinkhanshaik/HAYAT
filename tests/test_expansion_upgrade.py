from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.business_model import evaluate
from app.research.handoff import browser_handoff
from app.skills.registry import SkillRegistry, substitute
from app.skills.loader import SkillLoader
from app.tools.registry import ToolRegistry
from tests.test_reference_upgrade import assumptions


def test_business_sensitivity_separates_price_conversion_and_capacity():
    result = evaluate(assumptions())
    rows = {(row['field'], row['factor']): row for row in result['sensitivity']}
    assert len(rows) == 11
    assert rows['price', '.9']['profit_change'] == '-500.00 USD'
    assert rows['conversion_percent', '.8']['profit_change'] == '0.00 USD'
    assert rows['hours_per_sale', '1.2']['sales'] == 8
    assert rows['hours_per_sale', '1.2']['profit'] == '2,240.00 USD'
    required = result['requirements']
    assert required['price_for_target_at_base_sales'] == '395.00 USD'
    assert required['max_acquisition_cost_for_target'] == '130.00 USD'
    assert required['conversion_percent_for_target'] == 7
    assert required['capacity_can_deliver_target']
    assert 'elasticity' in required['basis']


def test_economic_thresholds_round_in_the_conservative_direction_and_handle_zero_sales():
    result = evaluate({'price': '1', 'capacity_hours': '3', 'monthly_leads': '3',
                       'conversion_percent': '100', 'target_profit': '1'})
    assert result['requirements']['price_for_target_at_base_sales'] == '0.34 USD'
    assert result['requirements']['max_acquisition_cost_for_target'] == '0.66 USD'
    empty = evaluate({})['requirements']
    assert empty['price_for_target_at_base_sales'] is None
    assert empty['max_acquisition_cost_for_target'] is None


def test_skill_exact_placeholders_preserve_typed_values_without_sharing_objects():
    inputs = {'assumptions': {'price': '500'}, 'limit': 4, 'enabled': True}
    rendered = substitute({'assumptions': '{{assumptions}}', 'limit': '{{limit}}', 'enabled': '{{enabled}}'}, inputs)
    assert rendered == inputs
    rendered['assumptions']['price'] = '700'
    assert inputs['assumptions']['price'] == '500'
    assert substitute('Search {{limit}} records', inputs) == 'Search 4 records'


def test_builtin_business_workflow_reaches_a_real_typed_handler(tmp_path):
    from app.safety.permissions import PermissionManager
    tools = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    tools.register('business_model', 'Evaluate the supplied business plan.',
                   {'type': 'object', 'properties': {'assumptions': {'type': 'object'}},
                    'required': ['assumptions'], 'additionalProperties': False},
                   'low', 'filesystem_read', lambda assumptions: evaluate(assumptions))
    skills = SkillRegistry(tools, SkillLoader(tmp_path / 'owner-skills'))
    skills.reload()
    result = skills.execute('business_stress_test', {'assumptions': assumptions()})
    assert result[0].success and result[0].output['sensitivity']


def test_bundled_workflow_is_executable_from_cli_and_keeps_tool_permissions(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv('HAYAT_CLI_JSON', '1')  # machine-readable output; people get plain lines
    import json
    from app.cli import handle_extended_command
    from app.safety.permissions import PermissionManager
    tools = ToolRegistry(PermissionManager({'default': 'DENY'}, tmp_path))
    executions = []
    tools.register('business_model', 'Evaluate planning assumptions.',
                   {'type': 'object', 'properties': {'assumptions': {'type': 'object'}}, 'required': ['assumptions']},
                   'low', 'filesystem_read', lambda assumptions: executions.append(assumptions) or evaluate(assumptions))
    skills = SkillRegistry(tools, SkillLoader(tmp_path / 'owner-skills'))
    skills.reload()
    runtime = SimpleNamespace(skills=skills)
    command = '/skill run business_stress_test ' + json.dumps({'assumptions': assumptions()})
    assert handle_extended_command(runtime, command)
    result = json.loads(capsys.readouterr().out)
    assert not result[0]['success'] and not executions
    tools.permissions.policies['filesystem_read'] = 'ALLOW'
    assert handle_extended_command(runtime, command)
    result = json.loads(capsys.readouterr().out)
    assert result[0]['success'] and result[0]['output']['sensitivity']
    assert executions == [assumptions()]
    with pytest.raises(ValueError, match='JSON object'):
        handle_extended_command(runtime, '/skill run business_stress_test []')


def test_handoff_prepares_a_query_without_opening_or_claiming_to_read_browser_content():
    data = browser_handoff(SimpleNamespace(), 'company & market', onion=True)
    assert parse_qs(urlsplit(data['url']).query)['q'] == ['company & market']
    assert data['tor_required'] and data['status'] == 'ready_to_open'
    assert not data['automatic_extraction']


def test_handoff_preserves_tor_intent_for_an_ordinary_public_search():
    from app.privacy_masks import ROUTE
    original = ROUTE.proxy
    calls = []
    try:
        ROUTE.set('socks5://127.0.0.1:19999')
        research = SimpleNamespace(search_browser=lambda url, required: calls.append((url, required)) or {'opened': True})
        outcome = browser_handoff(research, 'public market research', open_browser=True)
        assert outcome['status'] == 'opened' and calls[0][1] is True
    finally:
        ROUTE.set(original)


def test_handoff_does_not_report_a_missing_browser_as_opened():
    research = SimpleNamespace(search_browser=lambda *args: {'opened': False, 'reason': 'Browser unavailable'})
    with pytest.raises(RuntimeError, match='Browser unavailable'):
        browser_handoff(research, 'topic', open_browser=True)


@pytest.mark.parametrize('outcome', [None, False, {}, {'opened': 'yes'}])
def test_handoff_requires_explicit_launch_confirmation(outcome):
    with pytest.raises(RuntimeError, match='did not confirm'):
        browser_handoff(SimpleNamespace(search_browser=lambda *args: outcome), 'topic', open_browser=True)


def test_luxembourg_catalog_has_provenance_coordinates_and_strict_media_paths():
    from app.intelligence.feeds import BY_KEY, normalize
    from app.intelligence.providers import camera_url, kml_cameras
    content = b'<kml><Document><Placemark id="camera_3"><name>A6 camera</name><Point><coordinates>5.92,49.63,0</coordinates></Point></Placemark></Document></kml>'
    rows, rejected = normalize(BY_KEY['cameras_luxembourg'], kml_cameras(content))
    assert rejected == 0 and len(rows) == 1
    row = rows['cameras_luxembourg:3']
    assert row['latitude'] == 49.63 and row['longitude'] == 5.92
    assert row['details']['country'] == 'Luxembourg'
    assert 'CC0' in row['details']['catalog_license'] and row['observed_at'] is None
    assert camera_url(row['details']['image_url'], 'cameras_luxembourg')
    assert not camera_url('https://www.cita.lu/admin/private.jpg', 'cameras_luxembourg')
    assert not camera_url('https://www.cita.lu.evil.example/info_trafic/cameras/images/cccam_3.jpg', 'cameras_luxembourg')


def test_registry_search_is_contextual_and_keeps_metadata_distinct(monkeypatch):
    from app.research.search import PublicRegistrySearch, SearchProviderChain
    provider = PublicRegistrySearch()
    assert provider.supports('scientific paper methodology')
    assert not provider.supports('Data Analyst jobs in London')
    monkeypatch.setattr('app.research.public_data.PublicData.search', lambda *args, **kwargs:
                        {'records': [{'title': 'Climate study', 'doi_url': 'https://doi.org/10.1234/study',
                                      'publisher': 'Publisher', 'year': 2025}], 'providers': [{'status': 'ok'}]})
    result = provider.search('scientific paper climate')
    assert result[0].url.startswith('https://doi.org/') and 'metadata only' in result[0].snippet
    chain = SearchProviderChain([provider])
    assert chain.search_wide('jobs in London') == []
    assert chain.last_coverage[0]['status'] == 'not_applicable' and not chain.last_errors


def test_registry_search_cleans_discovery_terms_and_rejects_unrelated_titles(monkeypatch):
    from app.research.search import PublicRegistrySearch
    calls = []
    def lookup(_self, topic, kind, limit):
        calls.append(topic)
        return {'records': [{'title': title, 'doi_url': f'https://doi.org/10.1234/{index}'}
                            for index, title in enumerate(['Paper Puzzles', 'Extreme Weather and Climate Change'])],
                'providers': [{'status': 'ok'}]}
    monkeypatch.setattr('app.research.public_data.PublicData.search', lookup)
    result = PublicRegistrySearch().search('scientific paper climate change original sources')
    assert calls == ['climate change']
    assert [row.title for row in result] == ['Extreme Weather and Climate Change']


def test_deep_business_discovery_combines_indexes_and_canonicalizes_leads():
    from app.business_acquisition import discover
    from app.research.models import SearchResult
    from tests.test_business_acquisition import research_fixture
    research = research_fixture()
    wide = research.search.search
    research.search.search_wide = wide
    research.search.search = lambda *args, **kwargs: pytest.fail('deep search must use the combined indexes')
    wide.return_value = [SearchResult('Listing', 'https://broker.example/business/123'),
                         SearchResult('Same listing', 'https://broker.example/business/123?utm_source=index')]
    result = discover(research, 'laundry for sale', level='deep')
    assert wide.call_count == 5
    assert research.fetcher.fetch.call_count == 1 and len(result['candidates']) == 1


def test_all_bundled_workflows_match_live_runtime_schemas(tmp_path):
    from app.runtime import build_runtime
    from jsonschema import validate
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        skills = SkillRegistry(runtime.registry, SkillLoader(tmp_path / 'owner-skills'))
        skills.reload()
        samples = {'deep_public_research': {'query': 'public weather research'},
                   'business_stress_test': {'assumptions': assumptions()},
                   'public_camera_research': {'feed': 'cameras_luxembourg', 'query': 'A6'}}
        for name, inputs in samples.items():
            skill = skills.skills[name]
            assert skill['trusted']
            for step in skill['steps']:
                validate(substitute(step['arguments'], inputs), runtime.registry.get(step['tool']).input_schema)
    finally:
        runtime.close()
