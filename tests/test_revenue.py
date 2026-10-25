"""Revenue workflows use actual records, safe artifacts and explicit outbound approval."""
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.planner import Planner
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.revenue import RevenueWorkspace, register_revenue_tools
from app.revenue_catalog import CATALOG, relevant_tools
from app.revenue_metrics import calculate, empirical_probability, performance, rank
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger, register_business_tools
from app.tools.registry import ToolRegistry


class Model:
    def __init__(self):
        self.calls = []
        self.files = [{'path': 'index.html', 'content': '<!doctype html><html><title>Local prototype</title><body><h1>Demo</h1></body></html>'},
                      {'path': 'README.md', 'content': '# Local prototype\nInspect before deployment. No external integration configured.'}]

    def generate(self, messages, response_schema=None, **_kwargs):
        self.calls.append(messages)
        return SimpleNamespace(content=json.dumps({'files': self.files}) if response_schema else
                               '# Proposal draft\nI can build the requested Python CSV cleanup workflow.\nNeeds input: delivery date.',
                               finish_reason='stop')


@pytest.fixture
def workspace(tmp_path):
    permissions = PermissionManager({'default': 'ALLOW'}, tmp_path)
    registry = ToolRegistry(permissions)
    ledger = BusinessLedger(tmp_path / 'business.db', tmp_path / 'artifacts/business', permissions)
    register_business_tools(registry, ledger)
    result = RevenueWorkspace(ledger, registry, Model(), output=tmp_path / 'artifacts/revenue')
    register_revenue_tools(registry, result)
    return result


def test_all_named_workflows_have_real_registered_handlers(workspace):
    assert len(CATALOG) == len({tool.name for tool in CATALOG}) == 117
    assert {tool.name for tool in CATALOG} <= set(workspace.registry.names())
    catalog = workspace.registry.execute('revenue_tool_catalog', {})
    assert catalog.success and catalog.output['count'] == len(CATALOG)
    assert len(relevant_tools('Prepare a tailored freelance proposal and follow-up')) <= 14
    assert 'generate_proposal' in relevant_tools('generate_proposal from this job description')


def test_named_chat_request_runs_the_calculation_and_delivers_the_value(workspace, tmp_path):
    from app.agent.core import Agent
    from app.agent.executor import Executor
    from app.agent.verifier import Verifier
    from app.assistant import AssistantService
    from app.memory.database import MemoryDatabase
    from app.session import SessionState

    memory = MemoryDatabase(tmp_path / 'chat.db')
    memory.initialize()
    session = SessionState()
    text = 'pricing_engine {"data":{"hours":10,"hourly_cost":20,"margin_percent":30,"fee_percent":10}}'
    intent = IntentRouter().route(text, session)
    agent = Agent(Planner(workspace.registry), Executor(workspace.registry), Verifier(), memory)
    outcome = agent.run(intent.goal, intent, session)
    assert outcome.complete
    assert [event['tool'] for event in outcome.history] == ['pricing_engine']
    service = AssistantService(SimpleNamespace(proactive_mode='SUGGEST'))
    reply = service._outcome_response(intent, outcome)
    assert '333.33' in reply and 'USD' in reply
    assert 'nothing to show' not in reply
    assert not workspace.model.calls


def test_chat_delivers_draft_preview_bundle_and_unknown_probability(workspace):
    from app.revenue_reply import report

    draft = workspace.execute('generate_proposal', goal='Python CSV cleanup')
    def event(name, output, verified=True):
        return {'tool': name, 'result': {'success': True, 'output': output}, 'verification': {'passed': verified}}
    reply = report([event('generate_proposal', draft)])
    assert 'I can build' in reply and draft['path'] in reply and draft['bundle'] in reply
    assert report([event('generate_proposal', draft, False)]) is None
    unknown = report([event('deal_probability', empirical_probability({}))])
    assert 'Unknown / unavailable' in unknown and 'No observed closed outcomes' in unknown
    search = report([event('find_freelance_jobs', {'results': [{'url': 'https://example.com/job', 'opening_verified': False}],
                                                  'basis': 'Public candidates only', 'gaps': ['contra: unavailable']})])
    assert 'https://example.com/job' in search and 'Opening verified: No' in search and 'contra: unavailable' in search


def test_public_freelance_search_filters_domains_deduplicates_and_discloses_gaps(workspace):
    calls = []
    def search(query, limit, domains):
        calls.append(domains)
        if domains == ['contra.com']:
            raise RuntimeError('search unavailable')
        return {'results': [{'title': 'Sourced listing', 'url': 'https://www.upwork.com/jobs/1'},
                            {'title': 'Duplicate', 'url': 'https://www.upwork.com/jobs/1'},
                            {'title': 'Wrong host', 'url': 'https://upwork.com.attacker.example/fake'}]}
    workspace.registry.register('web_search', 'Public search', {'type': 'object'}, 'low', 'public_web_read', search)
    result = workspace.execute('find_freelance_jobs', goal='Python automation', data={'platforms': ['upwork', 'contra']})
    assert len(result['results']) == 1 and result['gaps']
    assert result['results'][0]['opening_verified'] is False
    assert calls == [['upwork.com'], ['contra.com']]


def test_proposal_is_a_read_back_artifact_with_verified_zip(workspace):
    result = workspace.execute('generate_proposal', goal='Clean a CSV with Python', context='Actual Python experience')
    assert Path(result['path']).is_file() and 'Needs input' in Path(result['path']).read_text()
    with zipfile.ZipFile(result['bundle']) as bundle:
        assert bundle.testzip() is None
        assert 'hayat-manifest.json' in bundle.namelist()
    assert result['published'] is False and result['executed'] is False
    assert 'Actual Python experience' in workspace.model.calls[0][-1]['content']


def test_builder_produces_actual_source_files_and_rejects_traversal_or_invalid_code(workspace):
    built = workspace.execute('website_builder_agent', goal='Create a local demo site')
    assert built['kind'] == 'prototype' and Path(built['path']).suffix == '.html'
    count_before = len(workspace.artifacts())
    for files in [[{'path': '../escape.txt', 'content': 'bad'}],
                  [{'path': 'broken.py', 'content': 'def broken(:'}],
                  [{'path': '.env', 'content': 'secret'}],
                  [{'path': 'hayat-manifest.json', 'content': '{}'}]]:
        workspace.model.files = files
        with pytest.raises((ValueError, SyntaxError)):
            workspace.execute('mvp_generator', goal='A small prototype')
    assert len(workspace.artifacts()) == count_before


def test_prompt_product_can_contain_actual_markdown_templates(workspace):
    workspace.model.files = [{'path': 'prompts.md', 'content': '# Prompt pack\nInput: {{company}}\nDescribe evidenced needs.'}]
    result = workspace.execute('prompt_product_builder', goal='Build a short company research prompt pack')
    assert result['kind'] == 'product' and Path(result['bundle']).is_file()


def test_truncated_model_output_never_becomes_a_successful_artifact(workspace):
    workspace.model.generate = lambda *_args, **_kwargs: SimpleNamespace(content='partial', finish_reason='length')
    with pytest.raises(ValueError, match='truncated'):
        workspace.execute('generate_proposal', goal='Draft a proposal')
    assert workspace.artifacts() == []


def test_missing_model_does_not_fabricate_a_draft(workspace):
    workspace.model = None
    with pytest.raises(RuntimeError, match='working model'):
        workspace.execute('generate_proposal', goal='Draft a proposal')
    assert workspace.artifacts() == []


def test_scores_leave_missing_evidence_and_probabilities_unknown():
    result = rank('score_job_opportunity', {'opportunities': [
        {'title': 'Good fit', 'pay': 80, 'fit': 90, 'difficulty': 20, 'competition': 30},
        {'title': 'Unknown'}, {'title': 'Poor fit', 'pay': 60, 'fit': 20}]})
    assert [row['title'] for row in result['ranked']] == ['Good fit', 'Poor fit', 'Unknown']
    assert result['ranked'][-1]['assessment']['score'] is None
    assert result['ranked'][0]['assessment']['close_probability']['probability'] is None
    assert empirical_probability({})['probability'] is None
    rate = empirical_probability({'wins': 4, 'closed': 10})
    assert rate['probability'] == .4 and rate['interval_95'][0] < .4 < rate['interval_95'][1]
    with pytest.raises(ValueError):
        empirical_probability({'wins': 5, 'closed': 4})


def test_price_roi_subscription_and_inventory_math_use_explicit_inputs():
    pricing = calculate('pricing_engine', {'hours': 10, 'hourly_cost': 20, 'margin_percent': 30, 'fee_percent': 10})
    assert pricing['price_floor'] == '333.33'
    assert calculate('content_roi', {'attributable_revenue': 200, 'production_cost': 100})['roi_percent'] == 100
    assert calculate('content_roi', {'attributable_revenue': 200, 'production_cost': 0})['roi_percent'] is None
    auto = calculate('automation_roi_calculator', {'hours_before': 2, 'hours_after': .5, 'runs_per_month': 10,
                     'hourly_cost': 20, 'implementation_cost': 600, 'monthly_running_cost': 50})
    assert auto['monthly_net_savings'] == '250.00' and auto['payback_months'] == 2.4
    subs = calculate('subscription_metrics', {'subscriptions': [{'monthly_price': 10, 'customers': 100}],
                       'starting_customers': 100, 'lost_customers': 5, 'acquisition_spend': 200,
                       'new_customers': 10, 'gross_margin_percent': 80})
    assert subs['mrr'] == '1000.00' and subs['arr'] == '12000.00' and subs['cac'] == '20.00'
    assert subs['ltv_estimate'] == '160.00'
    inventory = calculate('inventory_profitability', {'sale_price': 100, 'acquisition_cost': 40, 'fee_percent': 10,
                        'return_percent': 10, 'shipping_cost': 5, 'quantity': 2})
    assert inventory['expected_total_contribution'] == '72.00'
    with pytest.raises(ValueError):
        calculate('pricing_engine', {'hours': 1, 'hourly_cost': 1, 'margin_percent': 90, 'fee_percent': 10})
    with pytest.raises(ValueError):
        calculate('pricing_engine', {'hours': 'NaN', 'hourly_cost': 1, 'margin_percent': 10})


def test_experiment_snapshots_persist_without_double_counting(workspace):
    workspace.execute('proposal_ab_test', action='record', key='concise-1', data={'sent': 10, 'responses': 2, 'wins': 1})
    workspace.execute('proposal_ab_test', action='record', key='concise-1', data={'sent': 20, 'responses': 4, 'wins': 2})
    rows = workspace.execute('proposal_ab_test')['experiments']
    assert len(rows) == 1 and rows[0]['data']['sent'] == 20
    assert rows[0]['response_rate']['probability'] == .2
    reopened = RevenueWorkspace(workspace.ledger, workspace.registry, output=workspace.output)
    assert reopened.records('proposal_ab_test')[0]['key'] == 'concise-1'
    with pytest.raises(ValueError):
        workspace.execute('proposal_ab_test', action='record', key='invalid', data={'sent': 1, 'responses': 2})


def test_content_metrics_unknown_denominators_and_local_calendar(workspace):
    assert performance({'views': 20})['ctr_percent'] is None
    assert performance({'impressions': 100, 'clicks': 5})['ctr_percent'] == 5
    workspace.execute('content_performance_tracker', action='record', key='video-1', data={'views': 20})
    assert len(workspace.execute('content_performance_tracker')['measurements']) == 1
    posted = workspace.execute('posting_scheduler', action='create', key='post-1', data={
        'platform': 'linkedin', 'content': 'Actual project recap', 'due_at': '2026-10-05T09:00:00-04:00'})
    assert posted['data']['status'] == 'scheduled_for_review'
    assert workspace.execute('posting_scheduler')['auto_publish'] is False
    with pytest.raises(ValueError, match='timezone'):
        workspace.execute('posting_scheduler', action='create', key='invalid', data={
            'platform': 'linkedin', 'content': 'text', 'due_at': '2026-10-05T09:00:00'})


def campaign(workspace):
    return workspace.execute('outreach_campaign', action='create', data={'messages': [
        {'to': 'review@example.test', 'subject': 'Reviewable offer', 'body': 'A specific offer to review.',
         'due_at': '2020-01-01T00:00:00+00:00'}]})['campaign_id']


def test_campaign_needs_draft_and_exact_send_approval_and_is_not_resent(workspace):
    sent = []
    workspace.registry.register('email_send', 'Fixture sender', {'type': 'object'}, 'high', 'send_message',
                                lambda **args: (sent.append(args) or {'sent': True, 'address': args['to']}))
    identifier = campaign(workspace)
    with pytest.raises(ValueError, match='not approved'):
        workspace.execute('outreach_campaign', action='send_next', data={'campaign_id': identifier})
    assert not workspace.execute('outreach_campaign', action='approve', data={'campaign_id': identifier})['approved']
    approved = []
    workspace.registry.approval_callback = lambda tool, _reason, args: (approved.append((tool, args)) or True)
    workspace.execute('outreach_campaign', action='approve', data={'campaign_id': identifier})
    assert not sent
    result = workspace.execute('outreach_campaign', action='send_next', data={'campaign_id': identifier})
    assert result['sent'] and len(sent) == 1
    assert [tool for tool, _ in approved] == ['outreach_campaign_approve', 'outreach_campaign_send']
    with pytest.raises(ValueError):
        workspace.execute('outreach_campaign', action='send_next', data={'campaign_id': identifier})
    assert len(sent) == 1


def test_ambiguous_delivery_and_edited_review_content_cannot_autoretry(workspace):
    def uncertain(**_args):
        raise RuntimeError('SMTP timed out after attempted transmission')
    workspace.registry.register('email_send', 'Uncertain fixture', {'type': 'object'}, 'high', 'send_message', uncertain)
    workspace.registry.approval_callback = lambda *_args: True
    identifier = campaign(workspace)
    workspace.execute('outreach_campaign', action='approve', data={'campaign_id': identifier})
    with pytest.raises(RuntimeError):
        workspace.execute('outreach_campaign', action='send_next', data={'campaign_id': identifier})
    assert workspace.execute('outreach_campaign')['campaigns'][0]['state'] == 'delivery_unknown'
    with pytest.raises(ValueError):
        workspace.execute('outreach_campaign', action='send_next', data={'campaign_id': identifier})
    changed = campaign(workspace)
    workspace.execute('outreach_campaign', action='approve', data={'campaign_id': changed})
    messages = workspace.execute('outreach_campaign')['campaigns'][0]['messages']
    messages[0]['body'] = 'Different unreviewed message'
    workspace.ledger._run(lambda db: db.execute('UPDATE revenue_campaigns SET messages=? WHERE id=?', (json.dumps(messages), changed)))
    with pytest.raises(ValueError, match='reviewed draft'):
        workspace.execute('outreach_campaign', action='send_next', data={'campaign_id': changed})


def test_campaign_cancel_during_send_review_stops_the_send(workspace):
    sent = []
    workspace.registry.register('email_send', 'Fixture sender', {'type': 'object'}, 'high', 'send_message', lambda **args: sent.append(args))
    identifier = campaign(workspace)
    def approve(tool, *_args):
        if tool == 'outreach_campaign_send':
            workspace.execute('outreach_campaign', action='cancel', data={'campaign_id': identifier})
        return True
    workspace.registry.approval_callback = approve
    workspace.execute('outreach_campaign', action='approve', data={'campaign_id': identifier})
    result = workspace.execute('outreach_campaign', action='send_next', data={'campaign_id': identifier})
    assert not result['sent'] and not sent


def test_pipeline_contract_invoice_profit_and_dashboard_use_existing_books(workspace):
    workspace.ledger.add_client('Acme', hourly_rate='40')
    workspace.ledger.log_time('Acme', '2', 'Actual work')
    quote = workspace.execute('quote_builder', data={'client': 'Acme', 'items': [{'description': 'Work', 'unit_price': '100'}]})
    assert Path(quote['path']).is_file()
    invoice = workspace.execute('invoice_automation', action='create', data={'client': 'Acme', 'items': [{'description': 'Work', 'unit_price': '100'}]})
    workspace.ledger.record_payment(invoice['number'])
    with pytest.raises(ValueError, match='unpaid balance'):
        workspace.execute('payment_reminder', goal='Prepare a reminder', data={'number': invoice['number']})
    lead = workspace.execute('sales_pipeline', action='create', data={'title': 'Python gig', 'source': 'Owner', 'value': '200'})
    workspace.execute('sales_pipeline', action='update', data={'lead_id': lead['lead_id'], 'status': 'lost'})
    assert 'Stop' in workspace.execute('next_sales_action', data={'lead_id': lead['lead_id']})['recommended_action']
    contract = workspace.execute('contract_builder', goal='Fill the approved template',
                                data={'approved_template': 'Client {{client}}; fee {{fee}}.', 'fields': {'client': 'Acme'}})
    assert contract['missing_fields'] == ['fee'] and not contract['ready_for_review']
    report = workspace.execute('revenue_dashboard')
    assert report['books']['by_currency'][0]['client_income'] == '100.00 USD'
    assert report['client_profitability']['clients'][0]['contribution_cents'] is None
    assert report['client_profitability']['clients'][0]['received_per_hour'] == 50


def test_csv_cleanup_preserves_original_and_monitor_compares_real_snapshots(workspace, tmp_path):
    source = tmp_path / 'source.csv'
    source.write_text('name,value\n Alice , 1 \nAlice,1\nBob,2\n', encoding='utf-8')
    before = source.read_bytes()
    cleaned = workspace.execute('data_cleanup_service', data={'path': str(source), 'drop_duplicates': True})
    assert cleaned['duplicates_removed'] == 1 and source.read_bytes() == before
    assert Path(cleaned['path']).read_text().count('Alice') == 1
    pages = [{'url': 'https://company.example', 'final_url': 'https://company.example', 'text': 'Price 10', 'content_hash': 'a'},
             {'url': 'https://company.example', 'final_url': 'https://company.example', 'text': 'Price 12', 'content_hash': 'b'}]
    workspace.registry.register('public_web_fetch', 'Fixture public fetch', {'type': 'object'}, 'low', 'public_web_read', lambda **_kwargs: pages.pop(0))
    first = workspace.execute('pricing_monitor', action='snapshot', key='company', data={'url': 'https://company.example'})
    second = workspace.execute('pricing_monitor', action='snapshot', key='company', data={'url': 'https://company.example'})
    assert not first['changes'][0]['baseline_available']
    assert second['changes'][0]['changed'] and second['changes'][0]['baseline_available']
    assert second['scheduled'] is False


@pytest.mark.parametrize('reply,label', [('Not interested, unsubscribe.', 'not interested'),
                                       ('Maybe interested, not sure yet.', 'uncertain'),
                                       ('Let us discuss next quarter.', 'later'),
                                       ('Send me a proposal', 'interested'), ('Hello', 'uncertain')])
def test_reply_classification_is_conservative(workspace, reply, label):
    result = workspace.execute('reply_classifier', goal=reply)
    assert result['label'] == label and not result['auto_send']


def test_chat_routing_and_bounded_tool_selection_reach_the_named_tools(workspace):
    planner = Planner(workspace.registry)
    query = 'Find freelance jobs for Python automation'
    assert IntentRouter().route(query).intent == IntentKind.ACTION
    assert planner.create_plan(query)[0].tool == 'find_freelance_jobs'
    direct = planner.create_plan('pricing_engine {"data":{"hours":2,"hourly_cost":20,"margin_percent":30}}')[0]
    assert direct.tool == 'pricing_engine' and direct.arguments['data']['hours'] == 2
    selected = planner._candidate_tools('Prepare a tailored freelance proposal')
    assert 'generate_proposal' in selected
    assert len(set(selected) & {tool.name for tool in CATALOG}) <= 14
