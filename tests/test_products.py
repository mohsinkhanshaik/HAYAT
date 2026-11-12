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


def test_a_sale_counts_in_the_month_of_its_local_date_not_its_utc_date(tools):
    # Sales are stamped in UTC; a sale at 01:00 UTC on 1 November happened on 31 October evening in
    # Michigan, and the October report must include it (text comparison had moved it to November).
    from datetime import datetime

    store, *_ = tools if isinstance(tools, tuple) else (tools,)
    product = create(store)
    assert store.record_sale(product['id'], 'late-order', '9.00', '0')['recorded']
    stamp = '2026-11-01T01:00:00+00:00'
    db = store._db()
    with db:
        db.execute("UPDATE sales SET recorded=? WHERE reference='late-order'", (stamp,))
    db.close()
    local_day = datetime.fromisoformat(stamp).astimezone().date().isoformat()
    month_start = local_day[:8] + '01'
    next_month = '2026-11-01' if local_day < '2026-11-01' else '2026-12-01'
    assert store.net_by_currency(month_start, next_month) == {product['currency']: 900}
