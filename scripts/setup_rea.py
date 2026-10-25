"""Reproduce HAYAT's pinned REA install without modifying other agents' settings."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def supported_node(version: str) -> bool:
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.\d+', version.strip())
    if not match:
        return False
    major, minor = map(int, match.groups())
    return major == 22 and minor >= 19 or major == 24 and minor >= 11 or major >= 26


def bundle_toolchain(root: Path, *, find=shutil.which, run=subprocess.run) -> tuple[Path, Path]:
    """Keep both Node and npm in HAYAT so subsequent setup needs no system Node."""
    destination = root / 'runtime' / 'node'
    bundled = destination / 'node.exe'
    system = find('node')
    node = bundled if bundled.is_file() else Path(system) if system else None
    if node is None:
        raise SystemExit('Node.js is missing. Install Node.js 24.11+ in the 24.x series, then rerun setup.')
    version = run([str(node), '--version'], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    if not supported_node(version):
        raise SystemExit(f'Unsupported Node.js {version}; use 22.19+, 24.11+ or 26+.')
    cli = destination / 'node_modules' / 'npm' / 'bin' / 'npm-cli.js'
    if not cli.is_file():
        candidates = [path for path in (Path(system).parent if system else None,
                                        Path(find('npm.cmd') or find('npm') or '.').parent) if path is not None]
        npm = next((path / 'node_modules' / 'npm' for path in candidates
                    if (path / 'node_modules' / 'npm' / 'bin' / 'npm-cli.js').is_file()), None)
        if npm is None:
            raise SystemExit('npm is missing; install the Node.js distribution including npm, then retry.')
        # Preserve incomplete existing installs instead of overwriting them.
        npm_target = destination / 'node_modules' / 'npm'
        if npm_target.exists():
            raise SystemExit(f'Incomplete bundled npm preserved at {npm_target}; repair it before retrying.')
        npm_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(npm, npm_target)
    destination.mkdir(parents=True, exist_ok=True)
    if node.resolve() != bundled.resolve():
        shutil.copy2(node, bundled)
    license_file = node.parent / 'LICENSE'
    if not license_file.is_file() and system:
        license_file = Path(system).parent / 'LICENSE'
    if license_file.is_file() and license_file.resolve() != (destination / 'LICENSE').resolve():
        shutil.copy2(license_file, destination / 'LICENSE')
    (destination / 'hayat-toolchain.json').write_text(json.dumps({'node_version': version,
        'npm': 'node_modules/npm/bin/npm-cli.js', 'scope': 'project-local Node and npm'}, indent=2), encoding='utf-8')
    return bundled, cli


def ensure_node_license(node: Path, version: str, *, fetch=None):
    license_file = node.parent / 'LICENSE'
    if license_file.is_file():
        return
    if not supported_node(version):
        raise ValueError('Cannot retrieve a license for an unsupported Node version.')
    import httpx
    release = version if version.startswith('v') else 'v' + version
    response = (fetch or httpx.get)(f'https://raw.githubusercontent.com/nodejs/node/{release}/LICENSE',
                                    follow_redirects=True, timeout=30)
    response.raise_for_status()
    if len(response.content) > 2_000_000 or b'Node.js' not in response.content:
        raise ValueError('The Node license response was not valid.')
    license_file.write_bytes(response.content)


def install_release(root: Path, node: Path, npm: Path, *, run=subprocess.run) -> dict:
    """Stage and probe a locked release before replacing the working installation."""
    root = root.resolve()
    runtime = root / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    # Fixed, verified workspace paths only; failed stages and previous releases are retained.
    runtime.resolve().relative_to(root)
    lock = runtime / '.rea-install.lock'
    try:
        handle = lock.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise RuntimeError(f'Another REA setup may be running. Inspect {lock} before retrying.') from exc
    with handle:
        handle.write(datetime.now(UTC).isoformat())
        handle.flush()
        try:
            source, target = root / 'integrations/rea', runtime / 'rea'
            stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
            staging, backup = runtime / ('rea-stage-' + stamp), runtime / ('rea-backup-' + stamp)
            for path in (target, staging, backup):
                path.resolve().relative_to(runtime.resolve())
            staging.mkdir()
            for name in ('package.json', 'package-lock.json'):
                shutil.copyfile(source / name, staging / name)
            expected = json.loads((staging / 'package.json').read_text(encoding='utf-8'))['dependencies']['rea-agents']
            # Integrity is enforced by npm ci. Package lifecycle scripts are never run.
            run([str(node), str(npm), 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
                cwd=staging, check=True, timeout=300)
            package = staging / 'node_modules/rea-agents'
            installed = json.loads((package / 'package.json').read_text(encoding='utf-8'))['version']
            if installed != expected:
                raise RuntimeError(f'Expected REA {expected}, staged {installed}. Working release retained.')
            probe = run([str(node), str(package / 'scripts/rea.mjs'), '--version'], cwd=staging,
                        capture_output=True, text=True, check=True, timeout=45)
            if probe.stdout.strip() != expected:
                raise RuntimeError('The staged REA launcher failed its version check. Working release retained.')
            report = {'version': installed, 'verified_at': datetime.now(UTC).isoformat(),
                      'backup': str(backup) if target.exists() else None, 'lifecycle_scripts': False,
                      'checks': ['npm ci integrity', 'package version', 'launcher version'],
                      'scope': 'Installation and startup checks; run verify_native_analysis.py for Ghidra integration.'}
            (staging / 'hayat-install.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            previous = target.exists()
            if previous:
                target.rename(backup)
            try:
                staging.rename(target)
            except OSError:
                if previous:
                    backup.rename(target)
                raise
            return report
        finally:
            # Windows requires the open file handle to be closed before unlinking.
            handle.close()
            lock.unlink()


def main():
    node, npm = bundle_toolchain(ROOT)
    metadata = json.loads((node.parent / 'hayat-toolchain.json').read_text(encoding='utf-8'))
    ensure_node_license(node, metadata['node_version'])
    report = install_release(ROOT, node, npm)
    print(json.dumps(report, indent=2))
    print('REA installed locally. Restart HAYAT before using the upgraded reverse-engineering studio.')


if __name__ == '__main__':
    main()
