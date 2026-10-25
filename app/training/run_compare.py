"""Compare two recorded evaluation runs, with where each number came from.

Runs are read from the evaluation files training already writes
(``data/models/<model>/evaluation*.json`` and ``ollama-evaluation.json``).
Nothing is re-measured here. A comparison says plainly when two runs are not
like-for-like (different holdout size, task set or dataset), because then a
better number does not mean a better model.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Metric -> (label, True when a higher value is better).
METRICS = {
    'held_out_token_loss': ('Held-out token loss', False),
    'exact_match_rate': ('Exact answers', True),
    'seconds': ('Evaluation time (s)', False),
}


def _family_scores(values: dict[str, Any]) -> dict[str, tuple[int, int]]:
    scores = {}
    for family, row in (values.get('family_scores') or {}).items():
        if isinstance(row, dict) and row.get('total'):
            scores[family] = (int(row.get('passed', 0)), int(row['total']))
    return scores


def _run(run_id: str, label: str, path: Path, values: dict[str, Any], provenance: dict[str, Any],
         **extra: Any) -> dict[str, Any]:
    tasks = values.get('exact_match_tasks')
    total = len(tasks) if isinstance(tasks, list) else values.get('exact_match_total')
    passed = values.get('exact_match_passed')
    return {
        'id': run_id, 'label': label, 'source': provenance,
        'examples': values.get('examples'), 'scored_tokens': values.get('scored_tokens'),
        'held_out_token_loss': values.get('held_out_token_loss'), 'seconds': values.get('seconds'),
        'exact_match_passed': passed, 'exact_match_total': total,
        'exact_match_rate': (passed / total) if isinstance(passed, int) and total else None,
        'families': _family_scores(values), **extra,
    }


def discover_runs(models_root: Path) -> list[dict[str, Any]]:
    """Every recorded run, newest file first; unreadable files are skipped."""
    files = [path for pattern in ('*/evaluation*.json', '*/ollama-evaluation.json')
             for path in Path(models_root).glob(pattern)]
    runs: list[dict[str, Any]] = []
    for path in sorted(set(files), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode('utf-8-sig'))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        provenance = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                      'modified': datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec='seconds')}
        name = f'{path.parent.name}/{path.stem}'
        dataset = data.get('dataset_sha256') or {}
        results = data.get('results')
        if isinstance(results, dict):
            adapters = {'baseline': data.get('comparison_adapter_sha256'),
                        'candidate': data.get('candidate_adapter_sha256')}
            for role in ('baseline', 'candidate'):
                if isinstance(results.get(role), dict):
                    runs.append(_run(f'{name}:{role}', f'{name} - {role}', path, results[role], provenance,
                                     adapter_sha256=adapters[role], dataset_sha256=dataset.get('test')))
        elif 'exact_match_passed' in data or 'checks' in data:
            runs.append(_run(name, f"{name} - {data.get('model', 'model')}", path, data, provenance,
                             adapter_sha256=data.get('adapter_sha256'), dataset_sha256=dataset.get('test')))
    return runs


def _differs(first: dict[str, Any], second: dict[str, Any], key: str) -> bool:
    return first.get(key) is not None and second.get(key) is not None and first[key] != second[key]


def compare_runs(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    rows = []
    # A metric measured on different material gets no better/worse verdict.
    basis = {'held_out_token_loss': 'examples', 'seconds': 'examples', 'exact_match_rate': 'exact_match_total'}
    for key, (label, higher_is_better) in METRICS.items():
        a, b = first.get(key), second.get(key)
        if a is None and b is None:
            continue
        verdict = 'not measured in both'
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            verdict = ('not like-for-like' if _differs(first, second, basis[key]) else
                       'same' if a == b else ('better' if (b > a) == higher_is_better else 'worse'))
        rows.append({'metric': label, 'first': a, 'second': b, 'second_is': verdict})
    families = []
    for family in sorted(set(first['families']) | set(second['families'])):
        a, b = first['families'].get(family), second['families'].get(family)
        verdict = 'not measured in both'
        if a and b:
            verdict = 'same' if a[0] * b[1] == b[0] * a[1] else ('better' if b[0] * a[1] > a[0] * b[1] else 'worse')
        families.append({'family': family, 'first': a, 'second': b, 'second_is': verdict})
    caveats = []
    for key, what in (('examples', 'held-out examples'), ('exact_match_total', 'exact-answer tasks'),
                      ('scored_tokens', 'scored tokens')):
        if _differs(first, second, key):
            caveats.append(f'Different {what} ({first[key]} vs {second[key]}): these numbers are not like-for-like.')
    if first.get('dataset_sha256') and second.get('dataset_sha256') and \
            first['dataset_sha256'] != second['dataset_sha256']:
        caveats.append('Different test datasets (hashes differ).')
    if first['id'] == second['id']:
        caveats.append('Both sides are the same run.')
    elif first.get('adapter_sha256') and first.get('adapter_sha256') == second.get('adapter_sha256'):
        caveats.append('Both runs used the same adapter weights; differences come from the setup, not training.')
    return {'first': first, 'second': second, 'metrics': rows, 'families': families, 'caveats': caveats}


def _value(value: Any) -> str:
    if value is None:
        return '-'
    if isinstance(value, tuple):
        return f'{value[0]}/{value[1]}'
    if isinstance(value, float):
        return f'{value:.4g}'
    return str(value)


def describe(result: dict[str, Any]) -> str:
    first, second = result['first'], result['second']
    lines = [f"First:  {first['label']}", f"Second: {second['label']}", '']
    for row in result['metrics']:
        a, b = row['first'], row['second']
        if row['metric'] == 'Exact answers':
            a = f"{first['exact_match_passed']}/{first['exact_match_total']}" if a is not None else '-'
            b = f"{second['exact_match_passed']}/{second['exact_match_total']}" if b is not None else '-'
        lines.append(f"{row['metric']}: {_value(a)} -> {_value(b)} ({row['second_is']})")
    if result['families']:
        lines += ['', 'By task family:']
        lines += [f"- {row['family']}: {_value(row['first'])} -> {_value(row['second'])} ({row['second_is']})"
                  for row in result['families']]
    if result['caveats']:
        lines += ['', 'Read with care:'] + [f'- {caveat}' for caveat in result['caveats']]
    lines += ['', 'Where these numbers come from (recorded, not re-measured):']
    for side, run in (('First', first), ('Second', second)):
        source = run['source']
        lines.append(f"- {side}: {source['path']} (saved {source['modified']}, file SHA-256 {source['sha256'][:16]}...)")
        if run.get('adapter_sha256'):
            lines.append(f"  adapter SHA-256 {run['adapter_sha256'][:16]}...")
    return '\n'.join(lines)
