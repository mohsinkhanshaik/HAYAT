"""Opt-in public publisher check, using isolated app storage and preview services."""
import json
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(os.environ.get('HAYAT_LIVE_EXPANSION') != '1', reason='opt-in live public camera read')
def test_actual_luxembourg_catalog_and_in_app_preview(tmp_path):
    from PIL import Image
    from app.intelligence.manager import IntelligenceManager
    from app.research.fetcher import PublicFetcher

    fetcher = PublicFetcher('HAYAT public camera verification', timeout=25, delay=.35)
    manager = IntelligenceManager(tmp_path / 'intelligence.db', fetcher, extended=True)
    try:
        refreshed = manager.refresh(feeds=['cameras_luxembourg'])
        assert refreshed['updated'] > 0, refreshed
        catalog = manager.cameras(feed='cameras_luxembourg', limit=5)
        assert catalog['cameras'], catalog
        frame = manager.camera_frame('cameras_luxembourg:3')
        assert not frame['cached'] and 'not verified capture time' in frame['basis']
        assert Path(frame['path']).resolve().is_relative_to(tmp_path.resolve())
        with Image.open(frame['path']) as picture:
            dimensions = list(picture.size)
            assert picture.format == 'JPEG' and all(size > 10 for size in dimensions)
            picture.verify()
        repeat = manager.camera_frame('cameras_luxembourg:3')
        assert repeat['cached'] and repeat['sha256'] == frame['sha256']
        report = {'sources': refreshed['sources'], 'camera_count': refreshed['sources'][0]['count'],
                  'frame': {key: frame[key] for key in ('event_id', 'fetched_at', 'image_url', 'sha256', 'basis')},
                  'dimensions': dimensions, 'cached_on_repeat': repeat['cached']}
        Path('.test-runs/live-expansion-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    finally:
        manager.close()
        fetcher.close()
