"""Manually provision pinned image helpers inside HAYAT; no global upgrades."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {'ImageHash': '4.3.2', 'ExifRead': '3.5.1', 'PyWavelets': '1.10.0'}
WHEEL_HASHES = {
    '02b0f965f8c77cd813f61d7d39031ea27d4780e7ebcad56c6cd6a709acc06e5f',
    'e5426ce2857423ad401e575ea9d159dc97449dc041fb6e61b35109caea72c311',
    '5be6bdee86f8088eb15becaefabfecaa6336cd6103c58f46d2466d127b4f08cc',
    '47179b616e3afc53bfdc1274437af9b541bb1ddf2404929599affdfbd21e51c4',
    'cc2567834ca971b1655eb3220b1343d71ff3318f9405ab5d83ee621b2342192d',
    '1085f7381eaa9e25428b196c2dc7fef0c136dcb1e68ca92f737cb6d7a9f5fbee',
}


def inventory():
    rows = []
    for name, wanted in PACKAGES.items():
        try:
            distribution = importlib.metadata.distribution(name)
            location = Path(distribution.locate_file('')).resolve()
            rows.append({'name': name, 'version': distribution.version, 'expected_version': wanted,
                         'inside_project': location.is_relative_to(ROOT.resolve()),
                         'ready': distribution.version == wanted and location.is_relative_to(ROOT.resolve())})
        except importlib.metadata.PackageNotFoundError:
            rows.append({'name': name, 'version': None, 'expected_version': wanted, 'ready': False, 'inside_project': False})
    return {'project': str(ROOT), 'python': sys.version.split()[0], 'packages': rows,
            'ready': all(row['ready'] for row in rows), 'automatic_install': False}


def validate_install_environment():
    if (sys.platform != 'win32' or platform.machine().casefold() not in {'amd64', 'x86_64'}
            or not (3, 12) <= sys.version_info[:2] <= (3, 15)):
        raise RuntimeError('Pinned installer supports Windows x64 CPython 3.12-3.15 only.')
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        raise RuntimeError('Run this setup with HAYAT .venv Python; a global Python installation is never modified.')
    for name in ('numpy', 'Pillow', 'scipy'):
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise RuntimeError(f'Missing existing base dependency {name}; run the normal project setup first.') from None
    for row in inventory()['packages']:
        if row['version'] is not None and (row['version'] != row['expected_version'] or not row['inside_project']):
            raise RuntimeError(f"Preserving existing {row['name']} {row['version']}; resolve the environment manually before installing pinned helpers.")


def verify_wheels(folder):
    rows = []
    for path in sorted(folder.glob('*.whl')):
        if path.stat().st_size > 12 * 1024 * 1024:
            raise ValueError('Image-tool wheel exceeds its storage limit.')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in WHEEL_HASHES:
            raise ValueError('Wheel does not match a pinned image-tool artifact.')
        with zipfile.ZipFile(path) as wheel:
            names = wheel.namelist()
            metadata_names = [name for name in names if name.endswith('.dist-info/METADATA')]
            if len(metadata_names) != 1:
                raise ValueError('Wheel has no unique distribution metadata.')
            from email.parser import BytesParser
            metadata = BytesParser().parsebytes(wheel.read(metadata_names[0]))
            package, version = metadata['Name'], metadata['Version']
            expected = next((value for name, value in PACKAGES.items() if name.casefold() == package.casefold()), None)
            if version != expected:
                raise ValueError('Wheel metadata disagrees with its pinned version.')
            # Preserve notices beside the wheel; never extract publisher paths.
            notices = []
            for name in names:
                if ('.dist-info/' in name and any(part in name.rsplit('/', 1)[-1].casefold() for part in ('license', 'copying'))):
                    info = wheel.getinfo(name)
                    if info.file_size > 128 * 1024:
                        raise ValueError('License notice exceeds its size limit.')
                    content = wheel.read(name).decode('utf-8', errors='replace')
                    notices.append({'file': name, 'text': content})
        rows.append({'name': package, 'version': version, 'wheel': path.name, 'sha256': digest,
                     'source': 'https://pypi.org/project/' + package + '/' + version + '/', 'notices': notices})
    if {row['name'].casefold() for row in rows} != {name.casefold() for name in PACKAGES}:
        raise ValueError('All three pinned image-tool wheels are required in the project cache.')
    return rows


def install():
    validate_install_environment()
    target = ROOT / 'runtime' / 'image-tools'
    wheels = target / 'wheels'
    wheels.resolve().relative_to(ROOT.resolve())
    wheels.mkdir(parents=True, exist_ok=True)
    requirements = ROOT / 'requirements-image-tools.txt'
    pip = [sys.executable, '-m', 'pip', '--isolated', '--disable-pip-version-check']
    subprocess.run([*pip, 'download', '--no-deps', '--require-hashes', '--only-binary=:all:',
                    '--no-cache-dir', '--index-url', 'https://pypi.org/simple', '--dest', str(wheels),
                    '-r', str(requirements)], check=True, timeout=180)
    provenance = verify_wheels(wheels)
    subprocess.run([*pip, 'install', '--no-index', '--find-links', str(wheels), '--no-deps',
                    '--require-hashes', '--only-binary=:all:', '-r', str(requirements)], check=True, timeout=120)
    # Invoke the actual libraries; installed metadata alone is insufficient.
    probe = ('import imagehash,exifread,pywt; from PIL import Image; '
             'assert str(imagehash.phash(Image.new("RGB",(32,32),"white"))); '
             'assert exifread.process_file; assert len(pywt.wavedec([1.,2.,3.,4.],"haar"))>0')
    subprocess.run([sys.executable, '-I', '-c', probe], check=True, timeout=30)
    (target / 'provenance.json').write_text(json.dumps({'verified_at': datetime.now(UTC).isoformat(),
        'scope': 'Pinned wheel hashes and local smoke checks; no global installs or model changes.',
        'packages': provenance}, indent=2), encoding='utf-8')
    report = inventory()
    if not report['ready']:
        raise RuntimeError('Installed helpers did not match the project-folder environment.')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--install', action='store_true', help='Manually download and install the pinned project-local helpers.')
    action.add_argument('--check', action='store_true', help='Read versions only; no installation/network request.')
    options = parser.parse_args()
    result = install() if options.install else inventory()
    print(json.dumps(result, indent=2))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
