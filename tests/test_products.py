import json
import zipfile

import pytest

from app.agent.planner import Planner
from app.intent.router import IntentRouter
from app.safety.permissions import PermissionManager
from app.tools.products import ProductStore, cents, register_product_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def tools(tmp_path):
    permissions = PermissionManager({'default': 'ALLOW'}, tmp_path)
    store = ProductStore(tmp_path / 'products.db', tmp_path / 'bundles', permissions)
    registry = ToolRegistry(permissions)
    register_product_tools(registry, store)
    return store, registry


def create(store):
    return store.create('Planner', 'Freelancers', 'Weekly planning template', '9.99', 'Personal use only.')


def test_delivery_roundtrip_and_ledger(tools, tmp_path):
    store, registry = tools
    product = create(store)
    source = tmp_path / 'template.md'
    source.write_text('# Weekly planner\n', encoding='utf-8')
    result = registry.execute('product_package', {'product_id': product['id'], 'files': [str(source)]})
    assert result.success, result.error
    with zipfile.ZipFile(result.output['path']) as archive:
        assert archive.read('deliverables/template.md') == source.read_bytes()
        assert archive.read('LICENSE.txt').decode() == product['license_text']
        assert json.loads(archive.read('manifest.json'))['published'] is False
    assert store.record_sale(product['id'], 'order-1', '9.99', '1.25')['recorded']
    assert store.record_sale(product['id'], 'order-1', '9.99', '1.25')['duplicate']
    assert store.report()['by_currency'][0]['net_receipts_cents'] == 874
    with pytest.raises(ValueError, match='different amounts'):
        store.record_sale(product['id'], 'order-1', '10')
    # A reopened store preserves both catalog and accounting.
    reopened = ProductStore(store.path, store.output, store.permissions)
    assert len(reopened.catalog()['products']) == 1
    assert reopened.report()['by_currency'][0]['transactions'] == 1


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', '100000001', 'nonsense'])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        cents(value)


def test_currency_separation_and_refunds(tools):
    store, _ = tools
    usd = create(store)
    eur = store.create('Report', 'Authors', 'Report', '3', 'Personal use', 'EUR')
    store.record_sale(usd['id'], 'a', '9.99', '1', '9.99')
    store.record_sale(eur['id'], 'b', '3')
    assert len(store.report()['by_currency']) == 2
    with pytest.raises(ValueError, match='Refunds'):
        store.record_sale(usd['id'], 'c', '1', refunds='2')
    with pytest.raises(ValueError, match='currencies'):
        store.create('x', 'x', 'x', '3', 'x', 'JPY')


def test_package_rejects_secrets_outside_paths_and_collisions(tools, tmp_path):
    store, _ = tools
    product = create(store)
    secret = tmp_path / '.env'
    secret.write_text('secret')
    with pytest.raises(ValueError):
        store.package(product['id'], [str(secret)])
    outside = tmp_path.parent / 'outside.md'
    with pytest.raises((ValueError, PermissionError)):
        store.package(product['id'], [str(outside)])
    source = tmp_path / 'template.md'
    source.write_text('x')
    with pytest.raises(ValueError, match='unique'):
        store.package(product['id'], [str(source), str(source)])


def test_tools_reachable_and_permissions_enforced(tools, tmp_path):
    _, registry = tools
    planner = Planner(registry)
    assert 'product_package' in planner._candidate_tools('Bundle my digital product for sale')
    assert 'product_sales_report' in planner._candidate_tools('Report revenue from templates')
    assert planner.create_plan('Show my product catalog')[0].tool == 'product_catalog'
    assert planner.create_plan('Report my digital product sales revenue')[0].tool == 'product_sales_report'
    denied = ToolRegistry(PermissionManager({'default': 'DENY'}, tmp_path))
    register_product_tools(denied, tools[0])
    assert not denied.execute('product_create', {'title': 'x', 'audience': 'x', 'description': 'x', 'price': '1', 'license_text': 'x'}).success
    assert tools[0].catalog()['products'] == []


@pytest.mark.parametrize('text', [
    'Create a digital product template for freelancers',
    'Package my digital product for sale', 'Show my product catalog',
    'Report my digital product sales revenue', 'Record a sale for my digital product',
])
def test_product_requests_reach_executor(text):
    assert IntentRouter().route(text).requires_tools


def test_product_advice_is_not_a_command():
    assert not IntentRouter().route('What is a digital product?').is_action
