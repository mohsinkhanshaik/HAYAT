"""Reasoning desk uses actual snapshots and stages user decisions without execution."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.gui_reasoning import decision_brief, reasoning_snapshot
from app.modes import ModeManager
from tests.test_gui import _window as _window, gui as gui


def test_reasoning_depth_preserves_specialist_privacy_and_local_modes(tmp_path):
    manager = ModeManager(tmp_path / 'modes.json')
    manager.set(['PRIVATE', 'LOCAL_ONLY', 'CODER', 'FAST'])
    manager.set_reasoning('deep')
    assert manager.snapshot()['configured'] == ['PRIVATE', 'LOCAL_ONLY', 'CODER', 'DEEP', 'ADVERSARIAL']
    manager.set_reasoning('automatic')
    assert manager.snapshot()['configured'] == ['PRIVATE', 'LOCAL_ONLY', 'CODER', 'BALANCED']
    manager.set(['DEEP', 'ADVERSARIAL'])
    manager.set_reasoning('automatic')
    assert manager.snapshot()['configured'] == ['AUTO']
    with pytest.raises(ValueError):
        manager.set_reasoning('superintelligent')


def test_decision_brief_preserves_constraints_and_labels_unknowns():
    text = decision_brief('Choose an offline notes app', 'No subscription; works offline',
                          'Two candidates; synchronization not tested', 'Export to Markdown')
    assert 'No subscription; works offline' in text and 'Export to Markdown' in text
    assert 'not independently verified' in text and 'not tested' in text
    assert 'what evidence would change' in text and 'observed results' in text
    with pytest.raises(ValueError):
        decision_brief(' ')
    with pytest.raises(ValueError):
        decision_brief('x' * 6001)


def pump(window, condition=None):
    deadline = time.monotonic() + 10
    settled = time.monotonic() + .2
    while time.monotonic() < deadline:
        window.update()
        if condition() if condition is not None else time.monotonic() >= settled:
            return
        time.sleep(.01)
    raise AssertionError('Reasoning desk did not settle')


@pytest.fixture
def desk(gui):
    gui._open_reasoning_desk()
    window = gui._reasoning_desk
    pump(window, lambda: not window._busy)
    yield window, gui
    if window.winfo_exists():
        window.close()


def test_real_snapshot_reads_local_contracts_without_starting_generation(gui, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('A snapshot must not start learning or generation')
    monkeypatch.setattr(gui.runtime.learner, 'enqueue', forbidden)
    monkeypatch.setattr(gui.runtime.conversation, 'reply', forbidden)
    value = reasoning_snapshot(gui.runtime)
    assert value['startup']['checks'] and value['modes']['configured']
    assert any(item['name'] == 'Brain contracts' for item in value['startup']['checks'])


def test_desk_reuses_window_masks_output_and_clears_learning_when_privacy_changes(desk):
    window, gui = desk
    gui._open_reasoning_desk()
    assert gui._reasoning_desk is window
    value = {**window.snapshot, 'gaps': [{'topic': 'token=' + 'sk-proj-' + 'Z9nX4bR7qA' * 5,
                                       'source': 'runtime_failure', 'reason': 'A dependency failed',
                                       'verification': 'A reproducible check passes'}]}
    window.render(value)
    assert 'sk-proj-' not in str(window.gaps.item('0'))
    window.gaps.selection_set('0')
    window._select_gap()
    assert 'reproducible check' in window.gap_detail.get()
    gui.runtime.authority.set_privacy_mode(True)
    pump(window, lambda: not window.snapshot or window.snapshot['private'])
    assert not window.gaps.get_children() and (not window.snapshot or not window.snapshot['gaps'])


def test_brief_is_only_staged_and_existing_draft_is_preserved(desk, monkeypatch):
    window, gui = desk
    sent = []
    monkeypatch.setattr(gui, 'send', lambda: sent.append(True))
    window.fields['goal'].insert('1.0', 'Choose an offline notes app')
    window.fields['constraints'].insert('1.0', 'No subscription')
    gui.input.insert('1.0', 'Keep this draft')
    window.prepare()
    assert gui.input.get('1.0', 'end-1c') == 'Keep this draft'
    gui.input.delete('1.0', 'end')
    window.prepare()
    assert 'No subscription' in gui.input.get('1.0', 'end-1c')
    assert not sent and 'Review the brief' in window.status.get()


def test_failed_measurement_recovers_and_closing_cancels_poll(desk, monkeypatch):
    import app.gui_reasoning as module
    window, _gui = desk
    original = module.reasoning_snapshot
    def broken(_runtime):
        raise OSError('private-path-or-secret')
    monkeypatch.setattr(module, 'reasoning_snapshot', broken)
    window.refresh()
    pump(window, lambda: not window._busy)
    assert 'could not be read' in window.status.get()
    assert 'private-path' not in window.status.get()
    monkeypatch.setattr(module, 'reasoning_snapshot', original)
    window.refresh()
    pump(window, lambda: not window._busy)
    assert window.status.get().startswith('Snapshot')
    window.destroy()
    assert window._closed and window._timer is None


@pytest.mark.parametrize('size,scale', [((920, 750), 1.0), ((560, 520), 1.0), ((560, 520), 1.5)])
def test_actions_and_editors_remain_accessible_in_compact_layout(desk, size, scale):
    from app.gui_theme import apply_text_scale
    window, gui = desk
    try:
        apply_text_scale(window, scale)
        window.geometry(f'{size[0]}x{size[1]}')
        pump(window)
        for button in (window.refresh_button, *window.mode_buttons.values()):
            assert button.winfo_rootx() + button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
        assert window.checks.winfo_height() >= 70
        window.tabs.select(window.learning_page)
        pump(window)
        assert window.gap_label.winfo_rooty() + window.gap_label.winfo_height() <= window.winfo_rooty() + window.winfo_height(), (
            window.geometry(), window.overview.winfo_height(), window.tabs.winfo_height(), window.gap_label.winfo_ismapped())
        window.tabs.select(window.brief_page)
        pump(window)
        assert window.prepare_button.winfo_viewable()
        assert window.prepare_button.winfo_rooty() + window.prepare_button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
        assert window.editor_scroll.winfo_height() >= 120
        assert window.fields['goal'].winfo_width() <= window.winfo_width()
    finally:
        apply_text_scale(gui.root, gui._text_scale)


def test_busy_reply_blocks_mode_change_and_decision_preparation(desk, monkeypatch):
    window, gui = desk
    called = []
    monkeypatch.setattr(gui, '_set_reasoning_profile', lambda value: called.append(value))
    monkeypatch.setattr(gui, '_working_since', time.monotonic())
    window.set_mode('deep')
    assert not called and 'Finish or stop' in window.status.get()
    window.render(window.snapshot)
    assert 'answer checks are not final' in window.checks.get('1.0', 'end')
    window.fields['goal'].insert('1.0', 'Plan an offline notes app')
    window.prepare()
    assert not gui.input.get('1.0', 'end-1c') and 'Finish or stop' in window.status.get()


def test_fixed_fast_profile_is_not_mislabelled_as_balanced(desk):
    window, _gui = desk
    window.render({**window.snapshot, 'modes': {'configured': ['FAST', 'PRIVATE']}})
    # Fast has its own button since the reply-speed setting (2026-11-12); it is never shown as Medium/Balanced.
    chosen = [key for key, button in window.mode_buttons.items() if str(button.cget('style')) == 'HayatChosen.TButton']
    assert chosen == ['fast']


@pytest.mark.skipif(os.environ.get('HAYAT_SHOTS') != '1', reason='opt-in native visual review')
def test_native_reasoning_desk_capture(desk):
    from tests.test_gui_screenshots import _grab
    window, gui = desk
    target = Path('.test-runs/gui-shots')
    target.mkdir(parents=True, exist_ok=True)
    for name, tab in [('136-reasoning-desk', window.overview), ('137-decision-brief', window.brief_page),
                      ('138-learning-priorities', window.learning_page)]:
        window.tabs.select(tab)
        window.geometry('920x750')
        pump(window)
        _grab(gui, name, widget=window)
