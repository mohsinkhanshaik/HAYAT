from types import SimpleNamespace

from app.learning.continuous import ContinuousLearner
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


def test_many_learning_cycles_are_independent_of_an_exhausted_foreground_budget(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    registry.budget.max_calls_per_tool = 1
    output = {'complete': True, 'answer': 'Supported research.', 'sources': [{'url': 'https://example.com'}],
              'citations': ['source 1'], 'metrics': {'claims_supported': 1, 'searches': 1}, 'confidence': 0.8}
    registry.register('research_topic', 'Research', {'type': 'object'}, 'low', 'public_web_read', lambda **kw: output)
    assert registry.execute('research_topic').success
    assert not registry.execute('research_topic').success
    learner = ContinuousLearner(SimpleNamespace(registry=registry), root=tmp_path / 'learning')
    for _ in range(65):
        assert learner._default_cycle('SQLite transaction recovery mechanisms').ok
    assert registry.budget.counts['research_topic'] == 1
    assert not registry.execute('research_topic').success


def test_scoped_dispatch_is_still_bounded_and_restores_after_exception(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    registry.budget.max_calls_per_tool = 1
    registry.register('read', 'Read', {'type': 'object'}, 'low', 'filesystem_read', lambda: 'ok')
    try:
        with registry.budget_scope():
            assert registry.execute('read').success
            assert not registry.execute('read').success
            raise RuntimeError('job failed')
    except RuntimeError:
        pass
    assert registry.execute('read').success
    assert not registry.execute('read').success
