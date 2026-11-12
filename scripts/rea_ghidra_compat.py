"""Apply HAYAT's explicit Ghidra 12.2 compatibility patch to pinned REA 6.8.

Upstream verifies 12.1.4. This admits exactly 12.2, keeps the observed version
and qualification limits visible, and leaves every other admission check alone.
Run scripts.verify_native_analysis after installation to qualify this machine.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4


POLICY = 'return version.major === verified.major && version.minor === verified.minor'
PATCHED_POLICY = 'return raw === "12.2" || version.major === verified.major && version.minor === verified.minor'
LIMIT = ('This session uses Ghidra ${providerVersion}. The bridge is verified against ${SUPPORTED_GHIDRA_VERSION}; '
         'other ${ghidraReleaseLine()} builds are accepted and the observed version is reported.')
PATCHED_LIMIT = ('This session uses Ghidra ${providerVersion}. Upstream REA verifies ${SUPPORTED_GHIDRA_VERSION}. '
                 'HAYAT locally admits exactly 12.2 for static analysis; run scripts.verify_native_analysis '
                 'to check this host. This does not qualify every processor or file format.')


def _write_bytes_atomically(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + '.hayat-stage-' + uuid4().hex)
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_ghidra_compatibility(package: Path) -> dict:
    manifest = json.loads((package / 'package.json').read_text(encoding='utf-8'))
    if manifest.get('version') != '6.8.0':
        raise ValueError('The local Ghidra patch is scoped to REA 6.8.0; review a different release first.')
    candidates = sorted(path for path in (package / 'dist').rglob('*.js')
                        if 'ghidraReleaseCompatibility = ' in path.read_text(encoding='utf-8'))
    if len(candidates) != 3:
        raise ValueError('Expected exactly three pinned REA release-policy copies; installation preserved.')
    pending = []
    # Validate all copies before modifying any of them.
    for path in candidates:
        before_bytes = path.read_bytes()
        before = before_bytes.decode('utf-8')
        if before.count(PATCHED_POLICY) == 1 and before.count(PATCHED_LIMIT) == 1:
            after = before
        elif before.count(POLICY) == 1 and before.count(LIMIT) == 1:
            after = before.replace(POLICY, PATCHED_POLICY).replace(LIMIT, PATCHED_LIMIT)
            after = after.replace('compatible ${ghidraReleaseLine()} release (verified build ${SUPPORTED_GHIDRA_VERSION})',
                                  'admitted release (upstream verified ${SUPPORTED_GHIDRA_VERSION}; local 12.2 patch)')
        else:
            raise ValueError(f'Unexpected Ghidra policy in {path.name}; installation preserved.')
        backup = path.with_suffix(path.suffix + '.hayat-original')
        after_bytes = after.encode('utf-8')
        if before_bytes != after_bytes and backup.exists() and backup.read_bytes() != before_bytes:
            raise ValueError(f'Conflicting retained backup for {path.name}; installation preserved.')
        pending.append((path, before_bytes, after_bytes, backup))
    # Backups and their digests identify actual bytes. Older patch markers hashed
    # normalized text; the retained backup is the available historical evidence,
    # which may differ from the original npm artifact's newline spelling.
    for path, before, after, backup in pending:
        if before != after and not backup.exists():
            _write_bytes_atomically(backup, before)
    changed = []
    try:
        for path, before, after, backup in pending:
            if before != after:
                _write_bytes_atomically(path, after)
                changed.append((path, before))
        records = []
        for path, before, after, backup in pending:
            records.append({'file': str(path.relative_to(package)),
                            'before_file': str(backup.relative_to(package)) if backup.exists() else None,
                            'before_digest_source': 'retained_backup_bytes' if backup.exists() else 'unavailable',
                            'before_sha256': hashlib.sha256(backup.read_bytes()).hexdigest() if backup.exists() else None,
                            'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        report = {'patch': 'hayat-ghidra-12.2-v1', 'rea_version': '6.8.0', 'upstream_verified': '12.1.4',
                  'locally_admitted': '12.2', 'qualification': 'Run scripts.verify_native_analysis on this host.',
                  'provenance_format': 2, 'digest_scope': 'SHA-256 of on-disk bytes',
                  'before_digest_note': 'Before digests identify the retained backup bytes; historical pre-patch '
                                        'installation bytes are not independently reverified.',
                  'files': records}
        marker = package / 'hayat-ghidra-compatibility.json'
        content = json.dumps(report, indent=2).encode('utf-8')
        if not marker.exists() or marker.read_bytes() != content:
            _write_bytes_atomically(marker, content)
    except Exception as error:
        for path, before in reversed(changed):
            try:
                _write_bytes_atomically(path, before)
            except OSError as rollback_error:
                error.add_note(f'Could not restore {path.name}: {rollback_error}; its retained backup remains available.')
        raise
    return report
