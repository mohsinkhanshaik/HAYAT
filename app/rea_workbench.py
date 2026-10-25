"""Bounded, in-memory REA results and source-reported summaries."""
from __future__ import annotations

import json
import math
import threading
from copy import deepcopy
from datetime import datetime


DISPLAY_LIMIT = 500_000


def parse_arguments(raw):
    if len(raw) > 64_000:
        raise ValueError('Arguments exceed 64,000 characters.')
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('JSON numbers must be finite; NaN and infinity are not supported.')
        return number
    value = json.loads(raw, parse_constant=finite, parse_float=finite)
    if not isinstance(value, dict):
        raise ValueError('Arguments must be a JSON object.')
    return value


def result_views(value, success=True, operation=''):
    body = value.get('report') if isinstance(value, dict) else None
    structured = value.get('structured') if isinstance(value, dict) else None
    if body is None:
        body = json.dumps(value, indent=2, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    if structured is None and len(body) <= DISPLAY_LIMIT:
        try:
            structured = json.loads(body)
        except ValueError:
            structured = None
    if structured is not None and len(body) <= DISPLAY_LIMIT:
        body = json.dumps(structured, indent=2, ensure_ascii=False)
    if len(body) > DISPLAY_LIMIT:
        body = body[:DISPLAY_LIMIT] + '\n\n[Display limit reached; narrow the analysis request.]'
    prepared = success and isinstance(value, dict) and value.get('kind') == 'reverse_engineer'
    outcome = 'Prepared for confirmation; not executed' if prepared else 'Completed' if success else 'Failed'
    lines = [operation.replace('_', ' ').capitalize(), outcome, '']
    if prepared:
        lines += [str(value.get('title', '')), str(value.get('how_to_confirm', 'Review the action in Confirmations.'))]
    elif not success:
        lines.append(body[:2000])
    elif isinstance(value, dict) and value.get('link_analysis'):
        metadata = (structured or {}).get('public_link') or {}
        lines += [f"Source: {metadata.get('final_url') or value.get('source_url', '')}",
                  f"Page title: {metadata.get('title') or 'Not supplied'}",
                  f"Scripts collected: {len(value.get('resources') or [])}",
                  f"Advertised source maps collected: {len(value.get('source_maps') or [])}",
                  f"Import relationships: {len(value.get('dependencies') or [])}",
                  f"Collection notices: {len(value.get('skipped_resources') or [])}",
                  f"Analysis: {metadata.get('analysis_status', 'See full report')}",
                  f"Source manifest: {value.get('manifest', '')}"]
        if metadata.get('analysis_path'):
            lines.append('Saved analysis: ' + metadata['analysis_path'])
        analysis = (structured or {}).get('analysis') or {}
        if isinstance(analysis, dict) and analysis.get('full_result_transport_limited'):
            lines.append('Returned view: retained-evidence summary; full evidence is limited to this REA session.')
        lines += ['', str(value.get('scope') or 'Static public source only; inspect full coverage and limitations.')]
    elif isinstance(structured, dict):
        data = structured.get('result', structured)
        if isinstance(data, dict):
            artifact = data.get('artifact')
            if isinstance(artifact, dict):
                for key, label in (('path', 'Target'), ('format', 'Format'), ('byte_length', 'Bytes'), ('sha256', 'SHA-256')):
                    if artifact.get(key) is not None:
                        lines.append(f'{label}: {str(artifact[key])[:1000]}')
            for key, item in list(data.items())[:25]:
                if isinstance(item, list):
                    lines.append(f'{key.replace("_", " ").capitalize()}: {len(item)} entries returned')
                elif isinstance(item, dict) and key != 'artifact':
                    lists = [(field, len(rows)) for field, rows in item.items() if isinstance(rows, list)]
                    if lists:
                        lines.append(key.replace('_', ' ').capitalize() + ': ' + ', '.join(f'{size} {field}' for field, size in lists[:8]))
        lines += ['', 'Counts describe returned records, not complete target coverage. Read the full report for evidence and limitations.']
    else:
        lines.append('REA returned a text report. Open Full report to read its findings.')
    return '\n'.join(lines), body


class WorkbenchSession:
    """Survives closing the window, ends with HAYAT. Never writes analysis data to disk."""
    MAX_RESULTS = 10
    MAX_CHARACTERS = 2_000_000

    def __init__(self):
        self.drafts = {}
        self.field_drafts = {}
        self._results = []
        self._lock = threading.Lock()
        self._sequence = 0

    def remember(self, operation, arguments, timeout, success, value, elapsed):
        summary, report = result_views(value, success, operation)
        prepared = success and isinstance(value, dict) and value.get('kind') == 'reverse_engineer'
        status = 'Prepared' if prepared else 'Complete' if success else 'Failed'
        record = {'operation': operation,
                  'arguments': json.dumps(arguments, indent=2, ensure_ascii=False), 'timeout': timeout,
                  'summary': summary, 'report': report, 'elapsed': elapsed,
                  'label': f'{datetime.now():%H:%M:%S} · {operation} · {status}'}
        if success and isinstance(value, dict) and value.get('link_analysis'):
            record['source_evidence'] = deepcopy(value.get('source_evidence') or {})
            record['evidence_root'] = str(value.get('workspace') or '')
        with self._lock:
            self._sequence += 1
            record['id'] = self._sequence
            record['label'] = f'#{self._sequence} · ' + record['label']
            self._results.insert(0, record)
            while len(self._results) > self.MAX_RESULTS or sum(len(row['report']) + len(row['summary'])
                    + len(json.dumps(row.get('source_evidence', {}))) for row in self._results) > self.MAX_CHARACTERS:
                self._results.pop()
        return record

    @property
    def results(self):
        with self._lock:
            return list(self._results)


def verify_collected_file(row, root):
    """Verify a numbered retained source, never publisher-supplied source-map paths."""
    import hashlib
    from pathlib import Path
    if not root or not row.get('path'):
        raise ValueError('No retained evidence file is selected.')
    path = Path(row['path']).resolve()
    path.relative_to(Path(root).resolve())
    expected_size = row.get('bytes')
    if type(expected_size) is not int or not 0 <= expected_size <= 5_000_000:
        raise ValueError('Evidence size is outside the retained source budget.')
    with path.open('rb') as stream:
        data = stream.read(expected_size + 1)
    digest = hashlib.sha256(data).hexdigest()
    matched = len(data) == expected_size and digest == row.get('sha256')
    return {'verified': matched, 'bytes': len(data), 'sha256': digest,
            'message': 'Saved file matches recorded bytes and SHA-256.' if matched else
                       'Saved file differs from recorded evidence; do not treat this copy as verified.'}
