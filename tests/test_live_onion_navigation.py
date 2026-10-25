"""Opt-in genuine public onion source read using an owned temporary Tor daemon."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.environ.get('HAYAT_LIVE_ONION_NAVIGATION') != '1', reason='opt-in real public onion navigation')
def test_public_tor_documentation_read_with_citations_and_no_destination_dns(tmp_path, monkeypatch):
    from app.privacy_masks import ROUTE, TorMask
    from app.research.onion_navigation import navigate_onion

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    tor = TorMask(state=tmp_path / 'tor', settings=tmp_path / 'tor-settings.json', route=ROUTE, port=port)
    assert tor.installed(), 'Pinned local Tor must be installed; this test does not download software.'
    original = socket.getaddrinfo
    dns = []
    output = Path(os.environ.get('HAYAT_ONION_NAVIGATION_REPORT', '.test-runs/live-onion-navigation-20261024.json'))
    record = {'publisher_directory': 'https://onion.torproject.org/', 'provider': 'Tor Project public services',
              'no_private_or_authenticated_records': True, 'attempts': []}
    def resolver(host, *args, **kwargs):
        dns.append(host)
        assert host in {'127.0.0.1', '::1'}, f'Destination used local DNS: {host}'
        return original(host, *args, **kwargs)
    try:
        ready = tor.start(timeout=180)
        record['bootstrap'] = {'masked': ready.get('masked'), 'percent': tor.bootstrapped()}
        assert ready.get('masked'), ready
        monkeypatch.setattr(socket, 'getaddrinfo', resolver)
        # Addresses are publisher-listed at onion.torproject.org, not index leads.
        urls = ['http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/',
                'http://rzuwtpc4wb3xdzrj3yeajsvm3fkq4vbeubm2tdxaqruzzzgs5dwemlad.onion/']
        parent = SimpleNamespace(settings={'onion_navigation_seconds': 45, 'onion_request_timeout_seconds': 20})
        success = None
        for url in urls:
            report = navigate_onion(parent, url)
            record['attempts'].append(report)
            if report['usable_pages']:
                success = report
                break
        assert success is not None, record['attempts']
        assert success['tor_required'] and success['route_unchanged']
        page = success['pages'][0]
        assert page['status_code'] == 200 and 'tor' in page['text'].lower()
        assert page['sha256'] and page['retrieved_at'] and success['citations'][0]['url'] == page['final_url']
        assert page['links'] and any(link['same_origin'] for link in page['links'])
        record['passed'] = True
        record['destination_dns_lookups'] = sum(host not in {'127.0.0.1', '::1'} for host in dns)
    finally:
        tor.stop(remember=False)
        ROUTE.set(None)
        record['owned_daemon_stopped'] = not tor.running()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8')
