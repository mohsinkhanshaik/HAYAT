"""Training run comparisons read recorded files, name their source and refuse unfair rankings."""
import json
import time
from types import SimpleNamespace

from app.gui_theme import Palette
from app.gui_training import TrainingReview
from app.gui_training_compare import CompareRunsWindow
from app.training.run_compare import compare_runs, describe, discover_runs


def _family(passed, total):
    return {'passed': passed, 'total': total}


def _models(tmp_path):
    model = tmp_path / 'models' / 'test-4b'
    model.mkdir(parents=True)
    (model / 'evaluation.json').write_text(json.dumps({
        'results': {
            'baseline': {'held_out_token_loss': 2.0, 'scored_tokens': 100, 'examples': 10, 'seconds': 30.0,
                         'exact_match_tasks': [{}] * 4, 'exact_match_passed': 2,
                         'family_scores': {'sort': _family(1, 2), 'median': _family(1, 2)}},
            'candidate': {'held_out_token_loss': 1.5, 'scored_tokens': 100, 'examples': 10, 'seconds': 40.0,
                          'exact_match_tasks': [{}] * 4, 'exact_match_passed': 1,
                          'family_scores': {'sort': _family(1, 2), 'median': _family(0, 2)}}},
        'candidate_adapter_sha256': 'c' * 64, 'comparison_adapter_sha256': 'b' * 64,
        'dataset_sha256': {'test': 'd' * 64}}), encoding='utf-8')
    (model / 'ollama-evaluation.json').write_text(json.dumps({
        'model': 'test:4b', 'adapter_sha256': 'c' * 64, 'checks': [{}] * 8,
        'exact_match_passed': 6, 'exact_match_total': 8}), encoding='utf-8')
    (model / 'evaluation-broken.json').write_text('{not json', encoding='utf-8')
    return tmp_path / 'models'


def test_runs_are_discovered_with_their_file_hash_and_unreadable_files_skipped(tmp_path):
    runs = discover_runs(_models(tmp_path))
    assert {run['id'] for run in runs} == {'test-4b/evaluation:baseline', 'test-4b/evaluation:candidate',
                                           'test-4b/ollama-evaluation'}
    candidate = next(run for run in runs if run['id'].endswith('candidate'))
    assert candidate['exact_match_rate'] == .25 and candidate['adapter_sha256'] == 'c' * 64
    assert len(candidate['source']['sha256']) == 64 and candidate['source']['path'].endswith('evaluation.json')
    assert candidate['families'] == {'sort': (1, 2), 'median': (0, 2)}


def test_comparison_gives_each_metric_and_family_a_direction(tmp_path):
    runs = {run['id']: run for run in discover_runs(_models(tmp_path))}
    result = compare_runs(runs['test-4b/evaluation:baseline'], runs['test-4b/evaluation:candidate'])
    verdicts = {row['metric']: row['second_is'] for row in result['metrics']}
    assert verdicts == {'Held-out token loss': 'better', 'Exact answers': 'worse', 'Evaluation time (s)': 'worse'}
    assert {row['family']: row['second_is'] for row in result['families']} == {'median': 'worse', 'sort': 'same'}
    assert result['caveats'] == []
    text = describe(result)
    assert 'Exact answers: 2/4 -> 1/4 (worse)' in text
    assert '- median: 1/2 -> 0/2 (worse)' in text
    assert 'recorded, not re-measured' in text and 'evaluation.json (saved ' in text


def test_different_holdouts_are_flagged_and_not_ranked(tmp_path):
    runs = {run['id']: run for run in discover_runs(_models(tmp_path))}
    result = compare_runs(runs['test-4b/evaluation:candidate'], runs['test-4b/ollama-evaluation'])
    exact = next(row for row in result['metrics'] if row['metric'] == 'Exact answers')
    assert exact['second_is'] == 'not like-for-like'
    assert any('Different exact-answer tasks (4 vs 8)' in caveat for caveat in result['caveats'])
    assert any('same adapter weights' in caveat for caveat in result['caveats'])
    same = compare_runs(runs['test-4b/ollama-evaluation'], runs['test-4b/ollama-evaluation'])
    assert 'Both sides are the same run.' in same['caveats']


def _settle(root, window, until, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if until():
            return
        time.sleep(.01)
    raise AssertionError(window.status.get())


def test_compare_window_opens_from_training_and_shows_the_latest_pair(shared_tk, tmp_path):
    from tests.conftest import close_window, open_window
    root = open_window(shared_tk, '900x700-4000-4000')
    CompareRunsWindow.models_root = _models(tmp_path)
    pane = TrainingReview(root, Palette, SimpleNamespace(training=SimpleNamespace(CATEGORIES={'conversation'})),
                          lambda _text: None)
    window = None
    try:
        window = pane.open_comparison()
        _settle(root, window, lambda: window.status.get() == 'Compared.')
        assert len(window.runs) == 3
        assert window.first.get() and window.second.get() and window.first.get() != window.second.get()
        assert 'Where these numbers come from' in window.body.get('1.0', 'end')
        window.first.set('test-4b/evaluation - baseline')
        window.second.set('test-4b/evaluation - candidate')
        window.compare()
        _settle(root, window, lambda: 'Exact answers: 2/4 -> 1/4' in window.body.get('1.0', 'end'))
        window.close()
        assert window._closed
    finally:
        CompareRunsWindow.models_root = None
        if window is not None and window.winfo_exists():
            window.close()
        close_window(root)


def test_compare_window_explains_when_there_is_nothing_to_compare(shared_tk, tmp_path):
    from tests.conftest import close_window, open_window
    root = open_window(shared_tk, '900x700-4000-4000')
    CompareRunsWindow.models_root = tmp_path
    window = CompareRunsWindow(root, Palette)
    try:
        _settle(root, window, lambda: 'Two are needed' in window.status.get())
        assert 'No comparison yet.' in window.body.get('1.0', 'end')
    finally:
        CompareRunsWindow.models_root = None
        window.close()
        close_window(root)
