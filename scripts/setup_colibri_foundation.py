"""Let Colibri serve HAYAT's own brain weights, without copying or converting them.

Colibri v2.1.0 runs a GGUF as published (``coli import --mode link`` reads the file where it lies). Ollama keeps
hayat:latest's weights as a blob without a ``.gguf`` name, so this script:

1. reads the blob's digest from hayat:latest's Ollama manifest;
2. gives it a ``.gguf`` name with an NTFS hard link (same file, no extra disk, no administrator rights);
3. runs ``coli import --mode link`` into models/colibri/hayat-foundation-q4km;
4. checks that the imported pointer names the very file Ollama serves, and records the result in
   models/colibri-foundation-manifest.json (tracked; the weights themselves are ignored by Git).

It downloads nothing and changes no model role. Run it again after the brain's weights change.

    .\\.venv\\Scripts\\python.exe scripts/setup_colibri_foundation.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models.colibri_service import PROFILES, ColibriService, _version  # noqa: E402

LINK_NAME = 'Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf'
MANIFEST = ROOT / 'models/colibri-foundation-manifest.json'


def main() -> int:
    service = ColibriService(ROOT)
    blob = service.brain_blob()
    if blob is None or not blob.is_file():
        print('hayat:latest has no model layer on disk; nothing to import.', file=sys.stderr)
        return 1
    runtime = service.runtime_dir('foundation')
    if _version(runtime) < PROFILES['foundation']['min_runtime']:
        print(f'Colibri 2.1.0 or newer is needed for GGUF import; found {runtime.name}.', file=sys.stderr)
        return 1
    link = ROOT / 'models/colibri/hayat-foundation-gguf' / LINK_NAME
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not os.path.samefile(link, blob):
        link.unlink()  # the brain's weights changed: the old name points at the old file
    if not link.exists():
        os.link(blob, link)
    out = ROOT / PROFILES['foundation']['model_dir']
    command = [sys.executable, str(runtime / 'coli'), 'import', '--mode', 'link', '--out', str(out), '--no-mtp',
               str(link)]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                               errors='replace', check=False)
    print(completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else completed.stderr.strip())
    if completed.returncode != 0:
        return completed.returncode
    check = service.weights_check('foundation')
    record = {
        'recorded_at': datetime.now(UTC).isoformat(timespec='seconds'),
        'engine': {'path': str(runtime.relative_to(ROOT)).replace('\\', '/'), 'version': '.'.join(map(str, _version(runtime)))},
        'brain_model': 'hayat:latest',
        'brain_blob_digest': 'sha256:' + blob.name.split('-', 1)[-1],
        'gguf_bytes': blob.stat().st_size,
        'import': {'mode': check.get('mode'), 'arch': check.get('arch'), 'model_dir': PROFILES['foundation']['model_dir'],
                   'model_id': PROFILES['foundation']['model_id'], 'mtp': False},
        'same_file_as_brain': check['same_as_brain'],
        'note': 'Link mode reads the GGUF in place: identical weights to Ollama, no conversion or re-quantization.',
    }
    MANIFEST.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(check['detail'])
    return 0 if check['same_as_brain'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
