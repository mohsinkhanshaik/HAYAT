"""Explicit, credential-free publisher checks. No model, account write or paid API."""
import json
import os
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.getenv('HAYAT_LIVE_RESOURCES') != '1', reason='manual public resource verification')


def test_actual_literature_tool_and_cursor_pagination():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        runtime.cost_policy.assert_zero_cost()
        first = runtime.registry.execute('public_data_search', {'query': 'climate health', 'kind': 'literature', 'limit': 2})
        assert first.success, first.error
        value = first.output
        assert value['complete_provider_response'] and value['metadata_only'] and value['records'], value
        assert value['next_cursor'], value
        second = runtime.registry.execute('public_data_search', {'query': 'climate health', 'kind': 'literature',
            'limit': 2, 'cursor': value['next_cursor']})
        assert second.success and second.output['records'], second
        assert not ({row['record_id'] for row in value['records']} & {row['record_id'] for row in second.output['records']})
        assert all(row['registry_query_url'].startswith('https://www.ebi.ac.uk/europepmc/webservices/rest/search?')
                   for row in value['records'] + second.output['records'])
        Path('.test-runs/live-literature-verification.json').write_text(json.dumps({'first': value, 'second': second.output}, indent=2), encoding='utf-8')
    finally:
        runtime.close()


def test_actual_valencia_catalog_flows_through_storage_search_and_local_atlas(tmp_path):
    from app.intelligence.manager import IntelligenceManager
    from app.research.fetcher import PublicFetcher
    from app.intelligence.tools import register_intelligence_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    fetcher = PublicFetcher('HAYAT-public-resource-verification/1.0', timeout=25, delay=.35)
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    manager = IntelligenceManager(tmp_path / 'intelligence.db', fetcher, registry, extended=True)
    register_intelligence_tools(registry, manager)
    try:
        refreshed = manager.refresh(feeds=['cameras_valencia'])
        assert refreshed['updated'] > 0, refreshed
        cameras = manager.cameras(query='Pintor', feed='cameras_valencia', limit=5)
        assert cameras['cameras'], cameras
        record = cameras['cameras'][0]
        assert record['latitude'] is not None and record['longitude'] is not None
        assert record['details']['page_url'].startswith('https://camaras.valencia.es/camaras/visualizador.html?id=')
        assert not record['details']['image_url'] and not record['details']['video_url']
        atlas = manager.open_map()['map_url']
        response = httpx.get(atlas + 'event', params={'id': record['id']}, trust_env=False, timeout=10)
        assert response.status_code == 200, response.text
        assert response.json()['event']['id'] == record['id']
        all_cameras = manager.cameras(feed='cameras_valencia', limit=1000)['cameras']
        Path('.test-runs/live-valencia-verification.json').write_text(json.dumps({
            'refresh': refreshed, 'camera': record, 'atlas_event_verified': True,
            'unique_camera_ids': len(all_cameras),
            'unplaced_conflicting_ids': [row['id'] for row in all_cameras if row['details'].get('catalog_conflicts')],
            'playback_verified': False, 'basis': 'Camera locations and publisher links only; viewer not reachable in initial probe.'}, indent=2), encoding='utf-8')
    finally:
        manager.close()
        fetcher.close()
