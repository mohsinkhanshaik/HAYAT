"""Fetch pinned public map/video libraries and retain licenses and hashes."""
from pathlib import Path
import base64
import hashlib
import json
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / 'app/assets/intelligence-map/vendor'
    folder.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=25, follow_redirects=True) as client:
        release = client.get('https://api.github.com/repos/video-dev/hls.js/releases/latest')
        release.raise_for_status()
        version = release.json()['tag_name'].removeprefix('v')
        assets = [
            ('leaflet.js', 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js', '20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo='),
            ('leaflet.css', 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css', 'p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY='),
            ('Leaflet-LICENSE', 'https://raw.githubusercontent.com/Leaflet/Leaflet/v1.9.4/LICENSE', None),
            ('hls.js', f'https://cdn.jsdelivr.net/npm/hls.js@{version}/dist/hls.min.js', None),
            ('HLS-LICENSE', f'https://raw.githubusercontent.com/video-dev/hls.js/v{version}/LICENSE', None),
        ]
        records = []
        for name, url, expected in assets:
            response = client.get(url)
            response.raise_for_status()
            content = response.content
            digest = hashlib.sha256(content).digest()
            if expected and base64.b64encode(digest).decode() != expected:
                raise ValueError('Published library integrity hash did not match: ' + name)
            (folder / name).write_bytes(content)
            records.append({'file': name, 'url': url, 'sha256': digest.hex(), 'bytes': len(content)})
        (folder / 'manifest.json').write_text(json.dumps({'leaflet': '1.9.4', 'hls': version, 'assets': records}, indent=2), encoding='utf-8')
        print(json.dumps({'leaflet': '1.9.4', 'hls': version, 'files': len(records)}))


if __name__ == '__main__':
    main()
