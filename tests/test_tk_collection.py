import threading
from types import SimpleNamespace

import pytest

from app import gui_resources as resources


class Root:
    def bind(self, *args, **kwargs):
        pass

    def after(self, delay, callback):
        self.callback = callback
        return 'timer'

    def after_cancel(self, timer):
        self.cancelled = timer


def test_collection_stays_on_ui_and_restores_prior_state_after_last_root(monkeypatch):
    calls, state = [], SimpleNamespace(enabled=True)
    monkeypatch.setattr(resources, '_COLLECTION_GUARDS', set())
    monkeypatch.setattr(resources, '_collection_was_enabled', False)
    monkeypatch.setattr(resources.gc, 'isenabled', lambda: state.enabled)
    monkeypatch.setattr(resources.gc, 'disable', lambda: setattr(state, 'enabled', False))
    monkeypatch.setattr(resources.gc, 'enable', lambda: setattr(state, 'enabled', True))
    monkeypatch.setattr(resources.gc, 'collect', lambda generation=2: calls.append((threading.get_ident(), generation)))
    first, second = Root(), Root()
    a, b = resources.manage_tk_collection(first), resources.manage_tk_collection(second)
    assert not state.enabled
    a.collect()
    assert calls == [(a.owner, 0)]
    failures = []
    def worker():
        try:
            a.collect()
        except RuntimeError as exc:
            failures.append(str(exc))
    thread = threading.Thread(target=worker)
    thread.start(); thread.join()
    assert failures and len(calls) == 1
    a.close()
    assert not state.enabled
    b.close()
    assert state.enabled and first.cancelled == 'timer'


def test_preexisting_disabled_collection_is_not_enabled_by_cleanup(monkeypatch):
    monkeypatch.setattr(resources, '_COLLECTION_GUARDS', set())
    monkeypatch.setattr(resources, '_collection_was_enabled', False)
    monkeypatch.setattr(resources.gc, 'isenabled', lambda: False)
    monkeypatch.setattr(resources.gc, 'disable', lambda: None)
    monkeypatch.setattr(resources.gc, 'collect', lambda *args: None)
    monkeypatch.setattr(resources.gc, 'enable', lambda: pytest.fail('Must preserve the original disabled state'))
    root = Root()
    resources.manage_tk_collection(root).close()
