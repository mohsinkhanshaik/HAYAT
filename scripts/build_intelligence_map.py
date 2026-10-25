"""Build a small offline basemap from Natural Earth's public-domain data."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.fetcher import PublicFetcher

URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_land.geojson'
TARGET = ROOT / 'app/assets/world-land.json'


def main():
    fetcher = PublicFetcher('HAYAT-MapBuilder/1.0', timeout=20)
    try:
        resource = fetcher.fetch(URL, 2 * 1024 * 1024)
    finally:
        fetcher.close()
    data = json.loads(resource.content)
    polygons = []
    for feature in data['features']:
        geometry = feature['geometry']
        rings = geometry['coordinates'] if geometry['type'] == 'Polygon' else [ring for polygon in geometry['coordinates'] for ring in polygon]
        for ring in rings:
            if len(ring) >= 4:
                polygons.append([[round(float(lon), 2), round(float(lat), 2)] for lon, lat, *_ in ring])
    if not polygons:
        raise ValueError('No basemap geometry found.')
    TARGET.write_text(json.dumps({'source': URL, 'source_sha256': hashlib.sha256(resource.content).hexdigest(),
        'license': 'Public domain', 'terms': 'https://www.naturalearthdata.com/about/terms-of-use/',
        'transformation': 'Coordinates rounded to 0.01 degrees; use as context, not for navigation.',
        'polygons': polygons}, separators=(',', ':')), encoding='utf-8')
    print(f'Built offline map: {len(polygons)} rings, {TARGET.stat().st_size} bytes')


if __name__ == '__main__':
    main()
