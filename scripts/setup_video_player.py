"""Provision a checksum-verified portable VideoLAN runtime without an installer."""
from __future__ import annotations
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '3.0.24'
BASE = f'https://download.videolan.org/pub/videolan/vlc/{VERSION}/win64/'
NAME = f'vlc-{VERSION}-win64.zip'


def main():
    if sys.platform != 'win32':
        raise SystemExit('Install libVLC using your platform package manager; this provisioner is for Windows x64.')
    destination = ROOT / 'runtime' / 'vlc'
    if (destination / 'libvlc.dll').is_file():
        print('Portable libVLC is already installed.')
        return
    import requests
    cache = ROOT / '.tmp' / 'video-player-setup'
    cache.mkdir(parents=True, exist_ok=True)
    response = requests.get(BASE + NAME + '.sha256', timeout=30)
    response.raise_for_status()
    expected = response.text.split()[0].lower()
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('Invalid VideoLAN checksum')
    archive = cache / NAME
    with requests.get(BASE + NAME, stream=True, timeout=60) as response:
        response.raise_for_status()
        size = 0
        digest = hashlib.sha256()
        with archive.open('wb') as handle:
            for block in response.iter_content(1024 * 1024):
                size += len(block)
                if size > 150 * 1024**2:
                    raise ValueError('VideoLAN archive exceeded the size limit')
                digest.update(block)
                handle.write(block)
    if digest.hexdigest() != expected:
        raise ValueError('VideoLAN archive checksum does not match')
    staging = cache / 'unpacked'
    staging.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        if sum(item.file_size for item in package.infolist()) > 500 * 1024**2:
            raise ValueError('VideoLAN expanded archive exceeded the size limit')
        for item in package.infolist():
            target = (staging / item.filename).resolve()
            target.relative_to(staging.resolve())
        package.extractall(staging)
    source = staging / f'vlc-{VERSION}'
    if not (source / 'libvlc.dll').is_file():
        raise ValueError('VideoLAN runtime is incomplete')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('Existing runtime/vlc is incomplete; preserve or repair it before retrying')
    shutil.move(str(source), str(destination))
    (destination / 'hayat-provenance.json').write_text(json.dumps({
        'source': BASE + NAME, 'sha256': expected, 'version': VERSION,
        'license': 'VideoLAN archive retains COPYING and all bundled license files',
    }, indent=2), encoding='utf-8')
    print(f'Installed portable VLC {VERSION}: {destination}')


if __name__ == '__main__':
    main()
