"""Income decisions integrate with the real registry and isolated cash ledger."""
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.agent.planner import Planner
from app.income import economics, funnel, goal_plan, ideas, validation
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.revenue import RevenueWorkspace, register_revenue_tools
from app.revenue_reply import report
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger, register_business_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def workspace(tmp_path):
    permissions = PermissionManager({'default': 'ALLOW'}, tmp_path)
    registry = ToolRegistry(permissions)
    ledger = BusinessLedger(tmp_path / 'business.db', tmp_path / 'artifacts/business', permissions)
    register_business_tools(registry, ledger)
    workspace = RevenueWorkspace(ledger, registry, output=tmp_path / 'artifacts/revenue')
    register_revenue_tools(registry, workspace)
    return workspace


def test_offers_have_actionable_tools_and_no_invented_demand(workspace):
    rows = workspace.execute('income_idea_engine', data={'skills': ['Python', 'data'], 'limit': 18})['ideas']
    assert len(rows) == 18 and rows[0]['matched_skills']
    assert all(not row['demand_verified'] and row['price'] is None for row in rows)
    assert all(set(row['tools']) <= set(workspace.registry.names()) for row in rows)
    assert 'CSV cleanup service' in [row['idea'] for row in rows[:5]]


def test_contribution_values_time_and_fees_before_target_capacity():
    unit = economics({'price': 200, 'direct_costs': 20, 'delivery_hours': 2,
                      'hourly_cost': 30, 'fee_percent': 10, 'acquisition_cost': 10,
                      'fixed_costs': 250, 'currency': 'eur'})
    assert unit['contribution_per_sale'] == '90.00'
    assert unit['margin_percent'] == '45.00' and unit['break_even_sales'] == 3
    assert unit['currency'] == 'EUR'
    plan = goal_plan({'monthly_target': 1000, 'monthly_fixed_costs': 80,
                      'contribution_per_sale': 90, 'hours_per_sale': 2, 'monthly_available_hours': 20})
    assert plan['required_sales'] == 12 and plan['required_delivery_hours'] == '24.00'
    assert plan['capacity_units'] == 10 and not plan['capacity_feasible']


def test_negative_contribution_has_no_break_even_or_viability():
    unit = economics({'price': 10, 'direct_costs': 20, 'delivery_hours': 1, 'hourly_cost': 5})
    assert unit['contribution_per_sale'] == '-15.00'
    assert unit['break_even_sales'] is None and not unit['viable_under_assumptions']


def test_recurring_target_accounts_for_churn_without_claiming_receipts():
    result = goal_plan({'monthly_target': 1000, 'contribution_per_customer': 100,
                        'hours_per_customer': 2, 'monthly_available_hours': 30,
                        'active_customers': 8, 'monthly_churn_percent': 10}, recurring=True)
    assert result['required_active_customers'] == 10
    assert result['expected_churn_replacements'] == 1 and result['new_customers_needed'] == 3
    assert result['capacity_feasible']


@pytest.mark.parametrize('bad', [True, -1, 'NaN', 'Infinity', 101])
def test_fee_assumptions_reject_invalid_numbers(bad):
    with pytest.raises(ValueError):
        economics({'price': 20, 'direct_costs': 0, 'delivery_hours': 0, 'hourly_cost': 0, 'fee_percent': bad})


def test_no_cohort_denominator_stays_unknown_and_impossible_counts_fail():
    assert all(value is None for value in funnel(dict.fromkeys(['leads', 'replies', 'qualified', 'proposals', 'wins'], 0))['conversion_percent'].values())
    with pytest.raises(ValueError, match='cohort'):
        funnel({'leads': 5, 'replies': 6, 'qualified': 2, 'proposals': 2, 'wins': 1})
    result = funnel({'leads': 100, 'replies': 20, 'qualified': 10, 'proposals': 8, 'wins': 2, 'cohort': 'pilot'})
    assert result['lowest_observed_transition'] == 'lead_to_reply'
    assert result['conversion_percent']['proposal_to_win'] == '25.00'


def test_validation_requires_a_real_buyer_and_consistent_test_threshold():
    with pytest.raises(ValueError, match='buyer'):
        validation({}, 'automation')
    with pytest.raises(ValueError, match='minimum_commitments'):
        validation({'buyer': 'Small teams', 'interviews': 2, 'minimum_commitments': 3}, 'CSV cleanup')
    result = validation({'buyer': 'Small teams', 'interviews': 3}, 'CSV cleanup')
    assert not result['demand_verified'] and result['evidence_to_record']


@pytest.mark.parametrize('text,tool', [
    ('Show Osiris status', 'osiris_status'), ('Show Osiris playbook', 'osiris_playbook'),
    ('Show my income actions', 'income_action_queue'), ('Find income ideas for Python and data', 'income_idea_engine'),
])
def test_natural_chat_routes_to_real_model_free_plans(workspace, text, tool):
    intent = IntentRouter().route(text)
    assert intent.intent == IntentKind.ACTION and intent.requires_tools
    plan = Planner(workspace.registry).create_plan(text, intent=intent)
    assert [step.tool for step in plan] == [tool]
    result = workspace.registry.execute(tool, plan[0].arguments)
    assert result.success, result.error
    reply = report([{'tool': tool, 'result': result.to_dict(), 'verification': {'passed': True}}])
    assert reply and 'Unknown income' not in reply


def test_queue_persists_prioritizes_and_requires_completion_evidence(workspace):
    workspace.execute('income_action_queue', action='create', key='sample', data={
        'title': 'Prepare sample', 'tool': 'data_cleanup_service', 'priority': 2})
    workspace.execute('income_action_queue', action='create', key='buyer', data={
        'title': 'Validate buyer problem', 'priority': 1})
    with pytest.raises(ValueError, match='evidence'):
        workspace.execute('income_action_queue', action='update', key='buyer', data={'status': 'done'})
    reopened = RevenueWorkspace(workspace.ledger, workspace.registry)
    result = reopened.execute('income_action_queue')
    assert [row['key'] for row in result['actions']] == ['buyer', 'sample']
    workspace.execute('income_action_queue', action='update', key='buyer', data={
        'status': 'done', 'evidence': 'Buyer reviewed the sample and provided written feedback.'})
    assert workspace.execute('income_action_queue')['actions'][0]['key'] == 'sample'
    assert len(workspace.records('income_action_queue')) == 2


def test_osiris_uses_current_records_and_readonly_desktop_status(workspace):
    calls = []
    workspace.registry.register('computer_status', 'Read desktop readiness',
        {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'low', 'computer_observe',
        lambda: calls.append('status') or {'available': False, 'reason': 'No display', 'input_sent': False})
    workspace.ledger.add_lead('Owner-reported buyer', 'Owner')
    workspace.execute('income_action_queue', action='create', key='pilot', data={
        'title': 'Build reviewed pilot', 'tool': 'workflow_builder', 'priority': 1})
    before = workspace.ledger.report('all')
    result = workspace.registry.execute('osiris_playbook', {})
    assert result.success, result.error
    assert result.output['next_actions'][0]['title'] == 'Build reviewed pilot'
    assert not result.output['desktop']['available'] and calls == ['status']
    assert 'built-in' in result.output['integration'].lower()
    assert workspace.ledger.report('all') == before


def test_calculation_and_queue_do_not_create_cash_or_artifacts(workspace):
    before = workspace.ledger.report('all')
    result = workspace.registry.execute('offer_unit_economics', {'data': {
        'price': 50, 'direct_costs': 5, 'delivery_hours': 1, 'hourly_cost': 20}})
    assert result.success and result.output['contribution_per_sale'] == '25.00'
    assert workspace.ledger.report('all') == before and not workspace.artifacts()
    assert workspace.registry.get('income_action_queue').permission == 'filesystem_write_project'
    assert workspace.registry.get('osiris_status').permission == 'filesystem_read'


@pytest.mark.parametrize('data', [{'skills': [3]}, {'skills': {}}, {'limit': 0}, {'limit': 19}, {'limit': 1.5}])
def test_bad_idea_inputs_do_not_fake_a_plan(data):
    with pytest.raises(ValueError):
        ideas(data)


def test_queued_tool_must_exist_and_unknown_actions_fail(workspace):
    with pytest.raises(ValueError, match='registered'):
        workspace.execute('income_action_queue', action='create', key='bad', data={'title': 'Test', 'tool': 'fake_money'})
    with pytest.raises(ValueError, match='does not exist'):
        workspace.execute('income_action_queue', action='update', key='missing', data={'title': 'Test'})
    with pytest.raises(ValueError, match='report or list'):
        workspace.execute('osiris_status', action='send_next')


def test_income_ideas_and_osiris_are_available_to_the_model_selector(workspace):
    planner = Planner(workspace.registry)
    assert 'osiris_playbook' in planner._candidate_tools('Prepare an Osiris playbook')
    assert 'offer_unit_economics' in planner._candidate_tools('Calculate offer unit economics')


def test_new_draft_workflows_preserve_actual_facts_and_save_artifacts(workspace):
    class Model:
        def generate(self, messages, **kwargs):
            assert 'Workshop for Excel beginners' in messages[-1]['content']
            return SimpleNamespace(content='# Workshop offer\nA bounded Excel exercise. Price needs input.', finish_reason='stop')
    workspace.model = Model()
    result = workspace.registry.execute('workshop_offer_builder', {'goal': 'Workshop for Excel beginners'})
    assert result.success, result.error
    assert Path(result.output['path']).is_file() and Path(result.output['bundle']).is_file()
