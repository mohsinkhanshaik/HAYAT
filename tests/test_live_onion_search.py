"""Opt-in actual discovery and extraction through an owned Tor process."""
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.environ.get('HAYAT_LIVE_WIDE_SEARCH') != '1', reason='opt-in public multi-index search')
def test_actual_wide_search_has_relevant_public_results():
    from app.research.search import build_search_provider
    search = build_search_provider({'request_timeout_seconds': 12, 'paid_search_allowed': False})
    rows = search.search_wide('Tor Project official documentation', limit=8)
    report = {'query': 'Tor Project official documentation', 'configured_indexes': [item.name for item in search.providers],
              'results': [item.to_dict() for item in rows], 'errors': search.last_errors}
    Path('.test-runs/live-wide-search-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    assert rows, search.last_errors
    assert any('torproject.org' in row.domain for row in rows), 'Search returned no Tor Project source'


@pytest.mark.skipif(os.environ.get('HAYAT_LIVE_ONION_SEARCH') != '1', reason='opt-in real onion search through Tor')
def test_actual_onion_discovery_and_sourced_research(tmp_path, monkeypatch):
    from app.privacy_masks import ROUTE, TorMask
    from app.research.onion import research_onion
    previous = ROUTE.proxy
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    tor = TorMask(state=tmp_path / 'tor-state', settings=tmp_path / 'tor-settings.json', route=ROUTE, port=port)
    assert tor.installed()
    dns = []
    original_dns = socket.getaddrinfo
    def resolver(host, *args, **kwargs):
        dns.append(host)
        assert host in {'127.0.0.1', '::1'}, 'Destination resolved locally'
        return original_dns(host, *args, **kwargs)
    report = {'application_actions': False, 'query': 'Tor Project', 'owned_daemon': True}
    try:
        assert tor.start(timeout=120)['masked']
        monkeypatch.setattr(socket, 'getaddrinfo', resolver)
        parent = SimpleNamespace(settings={'onion_request_timeout_seconds': 30, 'model_synthesis': False}, model=None)
        result = research_onion(parent, 'Tor Project', 'quick')
        report.update(sources=[{'url': source.final_url, 'sha256': source.content_hash, 'bytes': source.bytes_downloaded} for source in result.sources],
                      errors=result.errors, scope=result.trace.get('onion_scope'),
                      stop_reason=result.stop_reason, complete=result.complete,
                      independent_publisher_groups=result.metrics.independent_sources,
                      dns_destinations_only_proxy=all(host in {'127.0.0.1', '::1'} for host in dns))
        assert result.sources and any('.onion' in source.domain for source in result.sources), result.errors
        assert result.trace['onion_scope']['tor_required']
        assert dns and report['dns_destinations_only_proxy']
    finally:
        tor.stop(remember=False)
        ROUTE.set(previous)
        report['owned_daemon_stopped'] = not tor.running()
        Path('.test-runs/live-onion-search-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
