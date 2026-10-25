"""Catalog updates, camera query context and stable browsing on real Tk widgets."""
from datetime import UTC, datetime, timedelta

import pytest

from app.intelligence.camera_catalog import refresh_candidates
from app.intelligence.feeds import Feed
from app.tools.registry import ToolResult
from tests.test_gui_intelligence import explorer as _explorer, pump_until, without_tallinn
from tests.test_intelligence_resources import ExtendedFetcher

explorer = _explorer


def test_catalog_updates_are_bounded_registered_and_respect_cooldowns(monkeypatch):
    from app.intelligence import camera_catalog as module
    monkeypatch.setattr(module, 'ALL_FEEDS', tuple(Feed('cameras_' + name, name + ' public cameras', 'https://example.org', 3600,
                                                     'https://example.org') for name in ('alpha', 'beta', 'gamma')) +
                        (Feed('earthquakes', 'other feed', 'https://example.org', 300, 'https://example.org'),))
    moment = datetime.now(UTC)
    states = {'cameras_alpha': {'last_success_at': moment.isoformat(), 'next_attempt_at': (moment + timedelta(hours=1)).isoformat()},
              'cameras_beta': {'last_success_at': (moment - timedelta(hours=2)).isoformat()}}
    assert refresh_candidates(states=states, limit=1, now=moment) == ['cameras_gamma']
    assert refresh_candidates(states=states, limit=8, now=moment) == ['cameras_gamma', 'cameras_beta']
    assert refresh_candidates(query='Beta', states=states, now=moment) == ['cameras_beta']
    assert refresh_candidates(feed='earthquakes', states=states, now=moment) == []
    assert refresh_candidates(query='unsupported private device', states=states, now=moment) == []


def load_fixture_cameras(window):
    window.manager.fetcher = ExtendedFetcher()
    window.manager.refresh(list(window.manager.fetcher.payloads))
    without_tallinn(window.manager)
    window.notebook.select(window.tabs['cameras'])
    pump_until(window, lambda: not window._busy and bool(window.camera_rows))


def test_camera_sort_change_discards_old_results_and_automatically_loads_current_view(explorer, monkeypatch):
    window, _gui = explorer
    load_fixture_cameras(window)
    old = window.manager.cameras(limit=200)
    window._camera_request_filters = window.camera_filters()
    window.camera_sort.set('Playable first')
    calls = []
    monkeypatch.setattr(window, 'work', lambda tool, arguments, *_: calls.append((tool, arguments)) or True)
    window.handle_resource_result(ToolResult('intelligence_cameras', True, output=old))
    window.update()
    assert calls[-1][0] == 'intelligence_cameras' and calls[-1][1]['sort'] == 'media'
    assert window.camera_offset == 0


def test_camera_refresh_preserves_selection_and_scroll_and_skips_unavailable_alternatives(explorer):
    window, _gui = explorer
    load_fixture_cameras(window)
    selected = list(window.camera_rows)[20]
    window.camera_tree.selection_set(selected)
    window.camera_tree.yview_moveto(.4)
    window.camera_tree.xview_moveto(.3)
    window.update()
    position = window.camera_tree.yview()[0], window.camera_tree.xview()[0]
    output = window.manager.cameras(limit=200)
    window._camera_request_filters = window.camera_filters()
    window.handle_resource_result(ToolResult('intelligence_cameras', True, output=output))
    window.update()
    assert window.camera_tree.selection() == (selected,)
    assert (window.camera_tree.yview()[0], window.camera_tree.xview()[0]) == pytest.approx(position)
    window.camera_rows['disabled'] = {'title': 'Offline', 'details': {'available': False, 'video_url': 'https://example.org/live.m3u8'}}
    assert 'disabled' not in [row['id'] for row in window.camera_alternatives(selected)]


def test_nearest_event_radius_is_validated_before_starting_work(explorer, monkeypatch):
    window, _gui = explorer
    calls = []
    monkeypatch.setattr(window, 'work', lambda tool, arguments, *_: calls.append((tool, arguments)) or True)
    window.camera_near = 'earthquakes:q1'
    window.camera_sort.set('Nearest event')
    for value in ('nan', 'inf', '-1', '2001', 'wrong'):
        window.camera_radius.set(value)
        window.load_cameras()
        assert not calls and 'Near radius' in window.feedback.get()
    window.camera_radius.set('25')
    window.load_cameras()
    assert calls[-1][1]['radius_km'] == 25 and calls[-1][1]['sort'] == 'nearest'
    window.all_cameras()
    assert window.camera_sort.get() == 'Name' and window.camera_near is None


def test_selected_catalog_update_uses_declared_refresh_and_reloads_the_list(explorer):
    from tests.test_public_intelligence import make_due
    window, _gui = explorer
    load_fixture_cameras(window)
    window.layer.set('cameras_london')
    make_due(window.manager, 'cameras_london')
    window.refresh_camera_catalogs()
    pump_until(window, lambda: not window._busy and len(window.camera_rows) == 1)
    assert next(iter(window.camera_rows.values()))['feed'] == 'cameras_london'
    assert window.manager.store.states()['cameras_london']['last_success_at']


def test_camera_actions_are_reachable_on_a_short_window(explorer):
    window, _gui = explorer
    load_fixture_cameras(window)
    window.geometry('760x580-4000-4000')
    window.update()
    assert window.camera_tree.winfo_ismapped() and window.camera_tree.winfo_height() >= 50
    for button in (*window.camera_buttons.values(), window.camera_more, window.camera_sort_choice):
        assert button.winfo_ismapped()
        assert button.winfo_rootx() + button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
