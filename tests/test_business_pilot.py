"""Measured cohort checks remain separate from planning and accounting."""
import pytest

from app.business_pilot import evaluate_pilot, pilot_text
from app.tools.business import BusinessLedger
from app.safety.permissions import PermissionManager
from tests.test_reference_upgrade import assumptions


def observations(**changes):
    return {'leads': '20', 'customers': '4', 'revenue': '2000', 'direct_cost': '200',
            'acquisition_spend': '100', 'delivery_hours': '16', 'notes': 'October pilot cohort', **changes}


def test_measured_conversion_and_contribution_match_independent_arithmetic():
    result = evaluate_pilot(assumptions(), observations())
    assert result['conversion_percent'] == '20.00'
    assert result['cash_contribution'] == '1,700.00 USD'
    assert result['contribution_after_time'] == '1,300.00 USD'
    assert result['contribution_per_customer'] == result['planned_contribution_per_customer'] == '325.00 USD'
    assert 'repeat with a new cohort' in result['next_actions'][0]
    assert 'not independently verified' in pilot_text(result)


def test_zero_sales_and_sunk_costs_never_claim_paid_demand():
    result = evaluate_pilot(assumptions(), observations(leads='0', customers='0', revenue='0',
                                                       direct_cost='0', acquisition_spend='50', delivery_hours='2'))
    assert result['conversion_percent'] is None
    assert result['contribution_per_customer'] is None
    assert result['contribution_after_time'] == '-100.00 USD'
    assert any('No paying customers' in action for action in result['next_actions'])
    assert 'unknown (no leads)' in pilot_text(result)


def test_missed_assumptions_are_reported_without_market_probability():
    result = evaluate_pilot(assumptions(), observations(customers='2', revenue='800'))
    assert result['conversion_percent'] == '10.00'
    assert any('conversion is below' in action for action in result['next_actions'])
    assert any('contribution per customer is below' in action for action in result['next_actions'])
    assert 'probability' not in result


@pytest.mark.parametrize('changes', [
    {'leads': '1.5'}, {'customers': '21'}, {'customers': True}, {'revenue': 'NaN'},
    {'delivery_hours': 'Infinity'}, {'direct_cost': '-1'}, {'acquisition_spend': '1e-100'},
    {'revenue': '1e100'}, {'notes': 'x' * 2001}, {'invented': 'field'},
])
def test_invalid_observations_rejected(changes):
    with pytest.raises(ValueError):
        evaluate_pilot(assumptions(), observations(**changes))


def test_saved_snapshot_survives_plan_change_without_changing_accounting(tmp_path):
    def ledger():
        return BusinessLedger(tmp_path / 'books.db', tmp_path / 'output', PermissionManager({'default': 'ALLOW'}, tmp_path))
    first = ledger()
    before = first.report('all')
    first.save_business_model(assumptions())
    assert first.business_pilot() == {'available': False}
    compared = first.save_business_pilot(observations())
    first.save_business_model({'price': '900'})
    second = ledger()
    assert second.business_pilot() == {key: value for key, value in compared.items() if key != 'saved'}
    assert second.business_pilot()['assumptions']['price'] == '500'
    assert second.business_model()['assumptions']['price'] == '900'
    assert second.report('all') == before
    assert 'business_pilot' not in second.profile()
    assert second.business_pilot(observations(), {'price': '600'})['assumptions']['price'] == '600'
    assert second.business_pilot()['assumptions']['price'] == '500'


def test_unknown_observation_type_and_plan_without_observations_rejected(tmp_path):
    with pytest.raises(ValueError):
        evaluate_pilot(assumptions(), [])
    ledger = BusinessLedger(tmp_path / 'books.db', tmp_path / 'output', PermissionManager({'default': 'ALLOW'}, tmp_path))
    with pytest.raises(ValueError, match='Supply observations'):
        ledger.business_pilot(assumptions={'price': '200'})
    with pytest.raises(ValueError, match='must be an object'):
        ledger.save_business_pilot(None)


def test_pilot_tools_execute_real_save_and_read_handlers(tmp_path):
    from app.tools.business import register_business_tools
    from app.tools.registry import ToolRegistry
    permissions = PermissionManager({'default': 'ALLOW'}, tmp_path)
    ledger = BusinessLedger(tmp_path / 'tools.db', tmp_path / 'output', permissions)
    registry = ToolRegistry(permissions)
    register_business_tools(registry, ledger)
    saved = registry.execute('business_pilot_save', {'observations': observations(), 'assumptions': assumptions()})
    assert saved.success, saved.error
    read = registry.execute('business_pilot', {})
    assert read.success and read.output['contribution_after_time'] == '1,300.00 USD'
    assert read.output['recorded_at'] == saved.output['recorded_at']
    assert not registry.execute('business_pilot_save', {'observations': {'revenue': 'NaN'}}).success
