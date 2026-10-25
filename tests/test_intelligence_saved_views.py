"""Named intelligence views keep their sources and are re-checked when opened."""
import pytest

from app.gui_intelligence_views import SavedViewsWindow, describe_view
from app.intelligence.manager import IntelligenceManager
from tests.test_gui_intelligence import explorer, pump_until  # noqa: F401 - pytest fixture


def test_saved_view_records_backing_sources_and_reopens_with_a_fresh_check(explorer):  # noqa: F811
    window, _gui = explorer
    manager = window.manager
    saved = manager.named_views('save', '  Quakes  ', {'region': 'World', 'category': 'earthquakes'})['view']
    assert saved['name'] == 'Quakes' and saved['options']['category'] == 'earthquakes'
    evidence = saved['evidence']
    assert evidence['total_matching'] >= 1 and evidence['headline']
    assert all(row['items'] > 0 for row in evidence['sources'])
    assert {'feed', 'items', 'last_success_at', 'stale'} <= set(evidence['sources'][0])
    assert [row['name'] for row in manager.named_views('list')['views']] == ['Quakes']
    opened = manager.named_views('open', 'Quakes')
    assert opened['now']['checked_at'] >= saved['evidence']['checked_at']
    assert opened['changes'][0].startswith(f"Matching items: {evidence['total_matching']} when saved")
    text = describe_view(opened)
    assert 'Saved ' in text and 'Checked now ' in text and 'What changed since it was saved:' in text
    assert "category = 'earthquakes'" in text
    assert manager.named_views('delete', 'Quakes')['deleted']
    assert manager.named_views('list')['views'] == []


def test_changes_name_lost_new_and_stale_sources():
    before = {'total_matching': 5, 'sources': [
        {'feed': 'earthquakes', 'items': 3, 'last_success_at': 'a', 'stale': False},
        {'feed': 'gdacs', 'items': 2, 'last_success_at': 'b', 'stale': False},
        {'feed': 'eonet', 'items': 1, 'last_success_at': 'c', 'stale': True}]}
    after = {'total_matching': 4, 'sources': [
        {'feed': 'earthquakes', 'items': 3, 'last_success_at': 'old', 'stale': True},
        {'feed': 'eonet', 'items': 1, 'last_success_at': 'd', 'stale': False},
        {'feed': 'firms', 'items': 1, 'last_success_at': 'e', 'stale': False}]}
    assert IntelligenceManager._view_changes(before, after) == [
        'Matching items: 5 when saved, 4 now.',
        'earthquakes: stale or unavailable now (last success old).',
        'eonet: current again.',
        'firms: new source, 1 items now.',
        'gdacs: contributed 2 items when saved, none now.']


def test_names_options_and_the_view_limit_are_checked(explorer, monkeypatch):  # noqa: F811
    manager = explorer[0].manager
    with pytest.raises(ValueError, match='name'):
        manager.named_views('save', '   ', {})
    with pytest.raises(ValueError, match='registered'):
        manager.named_views('save', 'Bad', {'region': 'Atlantis'})
    with pytest.raises(ValueError, match='No saved view'):
        manager.named_views('open', 'Missing')
    with pytest.raises(ValueError, match='list, save, open or delete'):
        manager.named_views('rename', 'x')
    monkeypatch.setattr(IntelligenceManager, 'MAX_NAMED_VIEWS', 1)
    manager.named_views('save', 'One', {})
    manager.named_views('save', 'One', {'query': 'replaced'})
    with pytest.raises(ValueError, match='Up to 1 saved views'):
        manager.named_views('save', 'Two', {})
    assert manager.named_views('list')['views'][0]['query'] == 'replaced'


def test_dialog_saves_the_current_map_and_reopening_applies_it(explorer):  # noqa: F811
    window, _gui = explorer
    window.category.set('Wildfires')
    window.severity.set('Medium and above')
    dialog = window.saved_views()
    try:
        pump_until(dialog, lambda: not dialog._busy)
        assert 'No saved views yet' in dialog.status.get()
        dialog.name.set('Fires')
        dialog.save()
        pump_until(dialog, lambda: dialog.views and not dialog._busy)
        assert [view['name'] for view in dialog.views] == ['Fires']
        assert 'Sources behind this view' in dialog.body.get('1.0', 'end')
        window.category.set('All events')
        window.severity.set('All severities')
        dialog.listing.selection_set(0)
        dialog.open()
        pump_until(dialog, lambda: dialog.status.get().startswith('Showing'))
        assert window.category.get() == 'Wildfires' and window.severity.get() == 'Medium and above'
        assert 'What changed since it was saved' in dialog.body.get('1.0', 'end')
        dialog.delete()
        pump_until(dialog, lambda: not dialog.views and not dialog._busy)
        assert window.manager.named_views('list')['views'] == []
    finally:
        if dialog.winfo_exists():
            dialog.close()
    assert isinstance(dialog, SavedViewsWindow)
