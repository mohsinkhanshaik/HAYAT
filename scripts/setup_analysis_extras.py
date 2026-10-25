"""Provision pinned WABT and JADX engines used by REA, without global installs."""
from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = {
    'wabt': ('https://github.com/WebAssembly/wabt/releases/download/1.0.42/wabt-1.0.42-windows-x64.tar.gz',
             '62f1eb2b51aa57cf0f0b8ba333bf2c72a049930aa9c5de01dcad271c4fe48c88'),
    'jadx': ('https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar',
             '6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75'),
}


def main():
    import sys
    if sys.platform != 'win32':
        raise SystemExit('This installer provisions Windows x64 analysis tools.')
    cache = ROOT / '.tmp' / 'analysis-extras'
    cache.mkdir(parents=True, exist_ok=True)
    for kind, (url, digest) in ARTIFACTS.items():
        target = ROOT / 'runtime' / kind
        marker = 'bin/wasm-validate.exe' if kind == 'wabt' else 'jadx-headless-mcp-0.7.1-all.jar'
        if (target / marker).is_file():
            print(f'Already installed: {kind}', flush=True)
            continue
        if target.exists():
            raise RuntimeError(f'Preserving incomplete existing installation: {target}')
        archive = cache / url.rsplit('/', 1)[1]
        with httpx.stream('GET', url, follow_redirects=True, timeout=60) as response, archive.open('wb') as stream:
            response.raise_for_status()
            size, actual = 0, hashlib.sha256()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 128 * 1024**2:
                    raise ValueError('Analysis engine archive exceeds the size limit.')
                actual.update(chunk)
                stream.write(chunk)
        if actual.hexdigest() != digest:
            raise ValueError(f'Checksum mismatch: {kind}')
        if kind == 'wabt':
            staging = cache / 'wabt-unpacked'
            staging.mkdir(exist_ok=True)
            with tarfile.open(archive) as package:
                members = package.getmembers()
                if sum(item.size for item in members) > 256 * 1024**2:
                    raise ValueError('Expanded engine archive exceeds the size limit.')
                for item in members:
                    (staging / item.name).resolve().relative_to(staging.resolve())
                    if not (item.isfile() or item.isdir()):
                        raise ValueError('Only regular engine files and directories are allowed.')
                package.extractall(staging, filter='data')
            candidates = [path.parent.parent for path in staging.rglob('wasm-validate.exe') if path.parent.name == 'bin']
            if len(candidates) != 1:
                raise ValueError('Cannot identify a unique WABT installation.')
            source = candidates[0]
            source.resolve().relative_to(ROOT.resolve())
            target.resolve().relative_to((ROOT / 'runtime').resolve())
            shutil.move(str(source), str(target))
        else:
            target.mkdir(parents=True)
            shutil.copyfile(archive, target / marker)
        (target / 'hayat-provenance.json').write_text(json.dumps({'source': url, 'sha256': digest}, indent=2), encoding='utf-8')
        print(f'Installed and verified {kind}: {target}', flush=True)


if __name__ == '__main__':
    main()
