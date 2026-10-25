"""Opt-in real publisher catalogs through storage, filtering, preview and cache."""
import json
import os
from pathlib import Path

from PIL import Image
import pytest

pytestmark = pytest.mark.skipif(os.getenv('HAYAT_LIVE_CAMERA_EXPANSION') != '1',
                                reason='manual real public camera catalog and snapshot check')


def test_actual_nsw_and_lyon_catalogs_and_supported_snapshots(tmp_path):
    from app.intelligence.manager import IntelligenceManager
    from app.research.fetcher import PublicFetcher
    fetcher = PublicFetcher('HAYAT-public-camera-verification/1.0', timeout=25, delay=.35)
    manager = IntelligenceManager(tmp_path / 'cameras.db', fetcher, None, extended=True, enabled=False)
    report = {'networks': {}, 'scope': 'Read-only public catalogs and two snapshots; no video or capture freshness claim.'}
    try:
        for key in ('cameras_nsw', 'cameras_lyon'):
            refreshed = manager.refresh([key])
            assert refreshed['updated'] == 1 and not refreshed['failed'], refreshed
            result = manager.cameras(feed=key, limit=1000)
            rows = result['cameras']
            assert rows and all(row['details']['availability_verified'] is False for row in rows)
            row = next((row for row in rows if row['details']['image_url'].startswith('https://data.livetraffic.com/')),
                       next(row for row in rows if row['details']['image_url']))
            frame = manager.camera_frame(row['id'])
            with Image.open(frame['path']) as picture:
                picture.load()
                size, image_format = picture.size, picture.format
            assert image_format in ('JPEG', 'PNG')
            assert manager.camera_frame(row['id'])['cached'] is True
            report['networks'][key] = {'catalog_records': result['total_matching'], 'camera': row['id'],
                                       'image_url': frame['image_url'], 'frame_bytes': Path(frame['path']).stat().st_size,
                                       'frame_size': size, 'image_format': image_format,
                                       'cached_second_fetch': True, 'fetched_at': frame['fetched_at'],
                                       'capture_time_verified': False}
        Path('.test-runs/live-public-camera-expansion.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    finally:
        manager.close()
        fetcher.close()
