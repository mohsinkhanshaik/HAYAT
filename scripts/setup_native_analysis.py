"""Install checksum-verified Ghidra/JDK archives required by HAYAT's pinned REA."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
GHIDRA_NAME = 'ghidra_12.2_PUBLIC_20261016.zip'
GHIDRA_URL = f'https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.2_build/{GHIDRA_NAME}'
GHIDRA_SHA = '2c2cd776db3c1f6a097ba418922378ac3649cadd5e844a6f5f088ae628a7be67'


def provision(url: str, expected: str, name: str, target: Path, marker: str, maximum: int = 2 * 1024**3,
              *, upgrade: bool = False):
    target.resolve().relative_to((ROOT / 'runtime').resolve())
    if (target / marker).is_file():
        if upgrade:
            try:
                current = json.loads((target / 'hayat-provenance.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                current = {}
            if current.get('sha256') != expected:
                stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
                staged = target.with_name(target.name + '-stage-' + stamp)
                backup = target.with_name(target.name + '-backup-' + stamp)
                provision(url, expected, name, staged, marker, maximum)
                target.rename(backup)
                try:
                    staged.rename(target)
                except OSError:
                    backup.rename(target)
                    raise
                print(f'Upgraded {target}; previous installation retained at {backup}', flush=True)
                return
        print(f'Already installed: {target}', flush=True)
        return
    if target.exists():
        raise RuntimeError(f'Incomplete existing installation preserved: {target}')
    cache = ROOT / '.tmp' / 'native-analysis-setup'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / name
    cached_digest = ''
    if archive.is_file():
        with archive.open('rb') as cached:
            cached_digest = hashlib.file_digest(cached, 'sha256').hexdigest()
    if cached_digest != expected:
        digest, size = hashlib.sha256(), 0
        with httpx.stream('GET', url, follow_redirects=True, timeout=60) as response, archive.open('wb') as stream:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > maximum:
                    raise ValueError('Archive exceeds the permitted size.')
                digest.update(chunk)
                stream.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f'Checksum failed: {name}')
    print(f'Verified {name}; extracting...', flush=True)
    staging = cache / (name + '.unpacked-' + uuid4().hex[:8])
    staging.mkdir()
    with zipfile.ZipFile(archive) as package:
        if sum(item.file_size for item in package.infolist()) > maximum * 4:
            raise ValueError('Expanded archive exceeds the permitted size.')
        roots = set()
        for item in package.infolist():
            (staging / item.filename).resolve().relative_to(staging.resolve())
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Archive contains a symbolic link.')
            roots.add(Path(item.filename).parts[0])
        if len(roots) != 1:
            raise ValueError('Archive must contain exactly one installation root.')
        package.extractall(staging)
    source = staging / roots.pop()
    if not (source / marker).is_file():
        raise ValueError(f'Archive lacks required engine file: {marker}')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Both paths are fixed below this workspace; preserve existing installs.
    source.resolve().relative_to(ROOT.resolve())
    target.resolve().relative_to((ROOT / 'runtime').resolve())
    shutil.move(str(source), str(target))
    (target / 'hayat-provenance.json').write_text(json.dumps({'source': url, 'sha256': expected}, indent=2), encoding='utf-8')
    print(f'Installed: {target}', flush=True)


def main():
    if os.name != 'nt':
        raise SystemExit('This provisioner installs Windows x64 native-analysis engines.')
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=['ghidra', 'java', 'all'], nargs='?', default='all')
    parser.add_argument('--upgrade', action='store_true', help='Stage upgrades and retain the previous installation.')
    args = parser.parse_args()
    # Java 25 can run the previous Ghidra; provision it first so a failed
    # Java download cannot leave Ghidra 12.2 paired with the old Java 21.
    if args.component in {'java', 'all'}:
        response = httpx.get('https://api.adoptium.net/v3/assets/latest/25/hotspot', params={
            'architecture': 'x64', 'image_type': 'jdk', 'os': 'windows', 'vendor': 'eclipse'}, timeout=30)
        response.raise_for_status()
        package = response.json()[0]['binary']['package']
        provision(package['link'], package['checksum'], package['name'], ROOT / 'runtime' / 'java', 'bin/javac.exe',
                  512 * 1024**2, upgrade=args.upgrade)
    if args.component in {'ghidra', 'all'}:
        provision(GHIDRA_URL, GHIDRA_SHA, GHIDRA_NAME, ROOT / 'runtime' / 'ghidra', 'support/analyzeHeadless.bat',
                  upgrade=args.upgrade)


if __name__ == '__main__':
    main()
