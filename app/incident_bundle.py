"""Exportable incident bundles: what happened, from where, in one redacted zip.

A bundle is evidence for a person (or a helper) looking into a problem. Every
part passes through the same redaction as the logs, the manifest names where
each part came from and its SHA-256, and a cancelled export leaves no file.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.safety.sensitive import redact

LOG_TAIL_BYTES = 200_000


class ExportCancelled(Exception):
    pass


def log_tail(path: Path, limit: int = LOG_TAIL_BYTES) -> str:
    try:
        with open(path, 'rb') as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            data = handle.read()
    except OSError as exc:
        return f'Log not readable: {type(exc).__name__}: {exc}'
    text = data.decode('utf-8', 'replace')
    # Start at a whole line when the tail cut one in half.
    return text.split('\n', 1)[1] if size > limit and '\n' in text else text


def export_bundle(directory: Path, parts: dict[str, tuple[str, Callable[[], Any]]], *,
                  query: str = '', cancelled: Callable[[], bool] = lambda: False,
                  now: datetime | None = None) -> dict[str, Any]:
    """Write ``incident-<time>.zip`` from named parts.

    ``parts`` maps a file name inside the zip to ``(source description,
    reader)``. A reader that fails is recorded as a failed part with its error;
    the bundle still says what it could not collect.
    """
    now = now or datetime.now(UTC)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'incident-{now:%Y%m%d-%H%M%S}.zip'
    partial = target.with_suffix('.zip.partial')
    manifest: dict[str, Any] = {'created_at': now.isoformat(), 'search': query, 'redacted': True,
                                'changes_made': False, 'parts': []}
    try:
        with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, (source, reader) in parts.items():
                if cancelled():
                    raise ExportCancelled
                try:
                    value = redact(reader())
                    status = 'collected'
                except Exception as exc:  # one unreadable part must not lose the others
                    value, status = f'Not collected: {type(exc).__name__}: {exc}', 'failed'
                data = (value if isinstance(value, str) else
                        json.dumps(value, indent=2, ensure_ascii=False, default=str)).encode('utf-8')
                archive.writestr(name, data)
                manifest['parts'].append({'file': name, 'source': source, 'status': status, 'bytes': len(data),
                                          'items': len(value) if isinstance(value, list) else None,
                                          'sha256': hashlib.sha256(data).hexdigest()})
            if cancelled():
                raise ExportCancelled
            archive.writestr('manifest.json', json.dumps(manifest, indent=2))
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    failed = [part['file'] for part in manifest['parts'] if part['status'] == 'failed']
    return {'path': str(target), 'parts': len(manifest['parts']), 'failed': failed, 'manifest': manifest}


def describe(result: dict[str, Any]) -> str:
    lines = [f"Incident bundle saved: {result['path']}", '']
    for part in result['manifest']['parts']:
        count = f", {part['items']} items" if part['items'] is not None else ''
        lines.append(f"- {part['file']} ({part['status']}{count}, {part['bytes']:,} bytes) from {part['source']}")
    lines += ['', 'Credentials and personal identifiers were redacted. manifest.json lists each part\'s SHA-256.',
              'Nothing was changed on this PC while collecting it.']
    if result['failed']:
        lines.insert(1, 'Some parts could not be collected: ' + ', '.join(result['failed']))
    return '\n'.join(lines)
