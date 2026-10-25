"""Opt-in: an owned Tor daemon, official exit check and official v3 onion read."""
import hashlib
import json
import os
from pathlib import Path
import socket
import httpx

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE_TOR") != "1", reason="live Tor: set HAYAT_LIVE_TOR=1")


def test_owned_tor_routes_real_https_and_official_onion_without_destination_dns(tmp_path, monkeypatch):
    from app.privacy_masks import ROUTE, TorMask
    from app.research.fetcher import PublicFetcher

    root = Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    tor = TorMask(state=tmp_path / "tor-state", settings=tmp_path / "settings.json", route=ROUTE, port=port)
    assert tor.installed(), "The pinned Tor bundle must be installed for this live verification."
    fetcher = PublicFetcher("HAYAT-Verification/1.0", timeout=30, delay=.2, respect_robots=True)
    report = {"scope": "Owned temporary Tor daemon; official endpoints only. No IP addresses stored.",
              "onion_source": "https://onion.torproject.org/", "checks": []}
    original_dns = socket.getaddrinfo
    dns = []

    def resolver(host, *args, **kwargs):
        dns.append(host)
        assert host in ("127.0.0.1", "::1"), f"destination used local DNS: {host}"
        return original_dns(host, *args, **kwargs)

    try:
        started = tor.start(timeout=120)
        assert started["masked"], started
        assert tor.status()["state"] == "ready"
        monkeypatch.setattr(socket, "getaddrinfo", resolver)
        exit_check = fetcher.get_json("https://check.torproject.org/api/ip")
        assert exit_check.get("IsTor") is True
        report["checks"].append({"name": "official HTTPS exit check", "passed": True})
        urls = ["http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/",
                "http://rzuwtpc4wb3xdzrj3yeajsvm3fkq4vbeubm2tdxaqruzzzgs5dwemlad.onion/"]
        report["onion_attempts"] = []
        resource = None
        for url in urls:
            try:
                resource = fetcher.fetch(url, max_bytes=2_000_000, accept={"text/html"}, require_tor=True)
                report["onion_attempts"].append({"url": url, "status": "read"})
                break
            except httpx.HTTPError as exc:
                report["onion_attempts"].append({"url": url, "error": type(exc).__name__})
        assert resource is not None, report["onion_attempts"]
        assert resource.status_code == 200 and b"tor" in resource.content.lower()
        report["checks"].append({"name": "official Tor Project v3 onion HTTP read", "passed": True,
                                 "bytes": len(resource.content), "sha256": hashlib.sha256(resource.content).hexdigest()})
        assert dns and all(host in ("127.0.0.1", "::1") for host in dns)
        report["checks"].append({"name": "no destination DNS in HAYAT Python retrieval", "passed": True})
        tor.process.terminate()
        tor.process.wait(timeout=10)
        assert tor.status()["routing_required"] and not tor.status()["masked"]
        report["checks"].append({"name": "dead daemon retains required proxy", "passed": True})
    finally:
        fetcher.close()
        tor.stop(remember=False)
        ROUTE.set(None)
        report["owned_daemon_stopped"] = not tor.running()
        output = root / ".test-runs/live-tor-verification.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
