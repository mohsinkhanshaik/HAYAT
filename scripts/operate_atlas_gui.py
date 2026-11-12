"""Operate the atlas in isolated Chromium using a read-only backup of retained data.

No model generation, feed monitoring or camera playback is started. Source
metadata and screenshots stay in the selected workspace output directory.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from playwright.sync_api import expect, sync_playwright
    from app.intelligence.manager import IntelligenceManager
    from app.intelligence.tools import register_intelligence_tools
    from app.research.fetcher import PublicFetcher
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db', default='data/memory/intelligence.db')
    parser.add_argument('--output', default='output/atlas-operated-review')
    args = parser.parse_args()
    source = (ROOT / args.source_db).resolve(strict=True)
    folder = (ROOT / args.output).resolve()
    folder.relative_to(ROOT)
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / 'preview.db'
    if database == source:
        raise ValueError('The preview database must be separate from the source.')
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as reader, sqlite3.connect(database) as writer:
        reader.backup(writer)
    fetcher = PublicFetcher('HAYAT-AtlasGuiReview/1.0', timeout=15)
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, folder))
    manager = IntelligenceManager(database, fetcher, registry, enabled=False, extended=True)
    register_intelligence_tools(registry, manager)
    report = {'at': datetime.now(UTC).isoformat(), 'source': 'read-only backup of retained public intelligence',
              'live_refresh': False, 'operations': [], 'browser_errors': [], 'camera_media_requests': []}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                page.on('pageerror', lambda error: report['browser_errors'].append(str(error)))
                def requested(request):
                    if '/frame?' in request.url or '/camera/play?' in request.url:
                        report['camera_media_requests'].append(request.url.split('?')[0])
                page.on('request', requested)
                page.goto(manager.open_map()['map_url'])
                expect(page.locator('#loaded-count')).to_contain_text('records loaded', timeout=30000)
                report['loaded'] = page.locator('#loaded-count').inner_text()
                page.screenshot(path=str(folder / '01-world-atlas.png'))
                page.locator('#search-mode').select_option('place')
                page.locator('#query').fill('51.5074 N 0.1278 W')
                page.get_by_role('button', name='Apply search', exact=True).click()
                expect(page.locator('#search-feedback')).to_contain_text('Went to 51.5074, -0.1278')
                report['operations'].append('Navigate by hemisphere coordinates')
                page.get_by_role('button', name='Layers', exact=False).click()
                expect(page.locator('#layer-desk')).to_have_attribute('open', '')
                page.locator('#clear-overlays').click()
                expect(page.locator('#streets')).not_to_be_checked()
                report['operations'].append('Open layer desk and turn overlays off')
                page.locator('#query').fill('geo:91,0')
                page.get_by_role('button', name='Apply search', exact=True).click()
                expect(page.locator('#search-feedback')).to_contain_text('Latitude must be between')
                expect(page.locator('#query')).to_be_focused()
                report['operations'].append('Reject invalid coordinates and retain editable search')
                page.locator('#query').fill('geo:40.7128,-74.0060')
                page.get_by_role('button', name='Apply search', exact=True).click()
                expect(page.locator('#search-feedback')).to_contain_text('Went to 40.7128, -74.0060')
                report['operations'].append('Correct the query and navigate by geo URI')
                page.get_by_role('button', name='Focus map', exact=True).click()
                expect(page.locator('.explorer')).to_be_hidden()
                page.keyboard.press('Escape')
                expect(page.locator('.explorer')).to_be_visible()
                report['operations'].append('Focus map and restore panels with Escape')
                page.locator('#feed').select_option('cameras_london')
                expect(page.locator('#refresh')).to_be_enabled()
                page.get_by_role('button', name='Fit results', exact=True).click()
                expect(page.locator('.observation-card').first).to_be_visible()
                camera_title = page.locator('.observation-card').first.locator('strong').inner_text()
                page.locator('.observation-card').first.click()
                expect(page.locator('#details h1')).to_have_text(camera_title, timeout=15000)
                report['selected_camera'] = page.locator('#details h1').inner_text()
                page.screenshot(path=str(folder / '02-camera-evidence.png'))
                report['operations'].append('Filter real retained cameras and inspect evidence without autoplay')
                page.get_by_role('button', name='Close observation details').click()
                page.locator('.saved-views summary').click()
                page.locator('#view-title').fill('Operated GUI review')
                page.locator('#save-atlas-view').click()
                expect(page.locator('#saved-view-state')).to_contain_text('Saved Operated GUI review')
                report['operations'].append('Save a view in the isolated preview database')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_role('button', name='Layers', exact=False).click()
                page.locator('#clear-overlays').scroll_into_view_if_needed()
                if not page.evaluate('document.documentElement.scrollWidth <= innerWidth'):
                    raise AssertionError('Phone view overflows horizontally')
                page.screenshot(path=str(folder / '03-phone-layers.png'))
                report['operations'].append('Operate the layer desk at 390px without horizontal overflow')
                report['passed'] = not report['browser_errors'] and not report['camera_media_requests']
                if not report['passed']:
                    raise AssertionError('The atlas produced script errors or started media without a request')
            finally:
                browser.close()
    except Exception as exc:
        report.update(passed=False, error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        manager.close()
        fetcher.close()
        (folder / 'operation-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
