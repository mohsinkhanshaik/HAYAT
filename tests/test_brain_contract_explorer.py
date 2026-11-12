"""Explore actual directed contracts without inventing delivery or historical readiness."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.brain_mesh import BrainMesh
from app.gui_brain import BrainExplorer, BrainGraph, LAYERS, curve_point
from app.gui_theme import OBSIDIAN, Palette, style_controls, style_hayat_buttons


@pytest.fixture
def graph(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, '1000x620-4000-4000')
    opened = []
    widget = BrainGraph(window, Palette, opened.append)
    widget.pack(fill='both', expand=True)
    widget.set_motion(False)
    window.update()
    yield window, widget, opened
    close_window(window)


def test_check_and_layer_filters_intersect_and_empty_views_remain_keyboard_safe(graph):
    _window, widget, opened = graph
    widget.set_checks({'memory': {'passed': True}, 'dna': {'passed': False}, 'authority': {'passed': False}})
    widget.set_layer('knowledge')
    widget.set_health_filter('Needs attention')
    assert set(widget._positions) == {'dna'}
    widget.set_health_filter('Ready')
    assert set(widget._positions) == {'memory'}
    widget.set_layer('interaction')
    assert not widget.VIEWS and widget.find_withtag('empty-filter')
    assert widget._step_key(1) == widget._enter_key() == 'break'
    assert not opened
    widget._health_key()
    assert widget.health_filter == 'All checks' and set(widget._positions) == {'interface'}


def test_route_only_draws_the_actual_directed_chain_and_clear_restores_the_view(graph):
    _window, widget, _opened = graph
    chain = widget.trace_path('interface', 'memory')
    widget.set_path_only(True)
    assert set(widget._positions) == set(chain)
    assert set(widget._contract_curves) == set(zip(chain, chain[1:]))
    assert all(pair in {(edge.source, edge.target) for edge in BrainMesh.EDGES} for pair in widget._contract_curves)
    widget.clear_path()
    assert set(widget._positions) == set(LAYERS)


def test_contract_inspection_exposes_endpoint_evidence_without_claiming_link_delivery(graph):
    _window, widget, _opened = graph
    widget.set_checks({'intent': {'passed': False, 'detail': 'router not attached'}})
    widget.set_evidence({'nodes': {'intent': {'checked_at': '2026-11-10T08:00:00Z', 'gaps': ['classification failed']}}})
    widget.select_contract('interface', 'intent')
    info = widget.contract_detail('interface', 'intent')
    assert info['contract'] == 'normalized request + session'
    assert info['source_check']['state'] == 'not checked'
    assert info['target_check']['state'] == 'needs attention'
    assert info['target_check']['checked_at'] == '2026-11-10T08:00:00Z'
    assert info['target_check']['gaps'] == ['classification failed']
    assert 'does not verify delivery' in info['scope']
    with pytest.raises(ValueError, match='not in the brain mesh'):
        widget.select_contract('interface', 'dna')


def test_keyboard_can_inspect_a_real_link_then_follow_it_without_running_a_tool(graph):
    _window, widget, opened = graph
    widget.select('interface')
    widget._link_key('outgoing')
    assert widget.contract_active == ('interface', 'intent')
    widget._enter_key()
    assert widget.active == widget._hover == 'intent'
    assert widget.contract_active is None and not opened
    widget._link_key('incoming')
    assert widget.contract_active[1] == 'intent'
    widget._cancel_interaction()
    assert widget.contract_active is None and not widget._path_nodes


def test_clicking_a_rendered_contract_inspects_it_without_opening_a_subsystem(graph):
    _window, widget, opened = graph
    candidate = None
    for pair, (curve, _depth) in widget._contract_curves.items():
        point = curve_point(curve, 0.5)
        if widget._hit(*point) is None and widget._hit_contract(*point) == pair:
            candidate = pair, point
            break
    assert candidate is not None
    pair, (x, y) = candidate
    event = SimpleNamespace(x=x, y=y)
    widget._press(event)
    widget._release(event)
    assert widget.contract_active == pair
    assert widget.find_withtag('inspected-contract') and widget.find_withtag('contract-detail')
    assert not opened


def test_historical_view_does_not_masquerade_latest_checks_as_recorded_readiness(graph):
    _window, widget, _opened = graph
    widget.set_checks({'memory': {'passed': True, 'detail': 'latest live-only detail'}})
    widget.set_health_filter('Ready')
    assert set(widget._positions) == {'memory'}
    widget.show_moment(100, [])
    assert set(widget._positions) == set(LAYERS)  # latest-check filter suspended
    assert widget._node_colour('memory') == Palette.ROYAL_SOFT
    info = widget.contract_detail('verifier', 'memory')
    assert info['target_check']['state'] == 'not recorded at this moment'
    assert info['target_check']['checked_at'] is None and not info['target_check']['detail']
    widget.show_moment(None)
    assert set(widget._positions) == {'memory'}


def test_native_explorer_selection_filters_and_copyable_evidence_stay_in_sync(graph):
    window, widget, _opened = graph
    explorer = BrainExplorer(window, widget, Palette)
    explorer.pack(side='right', fill='both')
    widget.select('interface')
    window.update()
    iid = 'interface|intent'
    assert explorer.links.exists(iid)
    explorer.links.selection_set(iid)
    explorer._selected()
    window.update()
    assert widget.contract_active == ('interface', 'intent')
    assert 'normalized request + session' in explorer.detail.get('1.0', 'end')
    assert 'not measured neural firing' in explorer.detail.get('1.0', 'end')
    explorer.source.set('Interface')
    explorer.target.set('Memory')
    explorer.trace()
    assert widget._path_chain[0] == 'interface' and widget._path_chain[-1] == 'memory'
    explorer.clear()
    window.update()
    assert not widget._path_nodes and widget.contract_active is None
    explorer.destroy()
    widget.select('memory')
    window.update()  # detached inspectors leave no stale Tk callbacks


@pytest.mark.skipif(os.environ.get('HAYAT_BRAIN_SHOTS') != '1', reason='Synthetic screenshot on request')
def test_synthetic_contract_explorer_screenshot(shared_tk):
    from PIL import ImageGrab
    from tests.conftest import close_window, open_window

    palette = type('SyntheticObsidian', (Palette,), OBSIDIAN)
    window = open_window(shared_tk, '1320x780+20+20')
    from tkinter import ttk
    style = ttk.Style(window)
    old_theme = style.theme_use()
    try:
        style.theme_use('clam')
        window.configure(bg=palette.BLACK)
        style_controls(window, palette)
        style_hayat_buttons(window, palette)
        widget = BrainGraph(window, palette, lambda _key: None, width=780, height=760)
        widget.pack(side='left', fill='both', expand=True, padx=8, pady=8)
        widget.set_motion(False)
        explorer = BrainExplorer(window, widget, palette, width=500)
        explorer.pack(side='right', fill='both', padx=(0, 8), pady=8)
        widget.set_checks({node.node_id: {'passed': node.node_id != 'network', 'detail': 'Synthetic check fixture'}
                           for node in BrainMesh.NODES if node.node_id != 'learning'})
        widget.set_evidence(BrainMesh.evidence_layer(widget.checks, '2026-11-10T08:00:00Z'))
        widget.select('interface')
        widget.trace_path('interface', 'memory')
        widget.select_contract('authority', 'tools')
        window.update()
        assert widget.contract_active == ('authority', 'tools')
        explorer.refresh()
        assert 'Authority → Tool Bus' in explorer.detail.get('1.0', 'end')
        output = Path(__file__).resolve().parents[1] / '.test-runs/gui-shots'
        output.mkdir(parents=True, exist_ok=True)
        captured = ImageGrab.grab(window=int(window.wm_frame(), 16))
        assert captured.width >= 1000 and captured.height >= 600
        captured.save(output / 'brain-contract-explorer-synthetic.png')
    finally:
        style.theme_use(old_theme)
        style_controls(window, Palette)
        style_hayat_buttons(window, Palette)
        close_window(window)
