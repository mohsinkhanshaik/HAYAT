"""Manual $0 public-profile verification, isolated from owner memory."""
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('HAYAT_LIVE_PERSON_RESEARCH') != '1', reason='manual public network verification')


def test_public_github_research_report_end_to_end(tmp_path):
    from app.research.manager import ResearchManager
    from app.research.investigations import build_report, profile_seed
    manager = ResearchManager({'database': str(tmp_path / 'live-person.db'), 'model_synthesis': False,
                               'budgets': {'deep': {'searches': 2, 'pages': 4, 'max_seconds': 30, 'max_workers': 2}}}, model=None)
    manager.fetcher.timeout = 6
    try:
        name, url = profile_seed('@torvalds', get_json=manager.fetcher.get_json)
        result = manager.research_person(url, level='deep')
        report = build_report(result, 'public repositories')
        assert result.trace.get('github_profile', {}).get('projection', {}).get('login') == 'torvalds'
        assert any(source['url'] == 'https://api.github.com/users/torvalds' for source in report['sources'])
        assert any('public repositories' in claim['text'] for claim in report['claims'])
        assert not report['identity_verified']
        Path('.test-runs/live-professional-research.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    finally:
        manager.close()


def test_live_image_provider_failure_never_discards_local_metadata(tmp_path):
    from PIL import Image
    from app.research.fetcher import PublicFetcher
    from app.research.image_sources import exact_sources
    target = tmp_path / 'synthetic-source.png'
    Image.new('RGB', (11, 13), '#23bbaa').save(target)
    fetcher = PublicFetcher('HAYAT-ResearchVerification/1.0', timeout=8)
    try:
        report = exact_sources(target, fetcher)
        assert report['metadata']['width'] == 11 and report['metadata']['height'] == 13
        assert all(row['match'] == 'verified_exact_bytes' for row in report['matches'])
        Path('.test-runs/live-image-source-research.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    finally:
        fetcher.close()
