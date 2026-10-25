"""Idle-time discovery of public camera and data sources (app/intelligence/discovery.py).

Owner request, 2026-10-06: find, connect and keep checking legitimate public
visual and data sources without waiting for each one to be handed over -- and
never bypass a restriction, use a private camera, or touch a private network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.intelligence import discovery
from app.intelligence.discovery import (DiscoveryStore, SourceScout, analyse_catalog, discovery_request,
                                        refused_domain, restore_discovered)
from app.observed_reply import render


def _catalog(host: str, count: int = 12) -> bytes:
    return json.dumps({"cameras": [{"id": f"C{index}", "name": f"Main St at {index} Ave",
                                    "location": {"lat": 45.0 + index / 100, "lon": -75.0},
                                    "image": f"https://{host}/snapshots/C{index}.jpg"} for index in range(count)]}).encode()


class Fetcher:
    def __init__(self, pages: dict[str, object]) -> None:
        self.pages, self.calls = pages, []

    def fetch(self, url, max_bytes=0, accept=None, request_headers=None, timeout=None):
        self.calls.append(url)
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        if page is None and "/snapshots/" in url and self.pages.get("snapshots", True):
            page = (b"\xff\xd8\xff\xe0 a camera picture", "image/jpeg")  # the publisher's own images answer the trial
        if page is None:
            raise httpx.ConnectError("unreachable")
        content, kind = page if isinstance(page, tuple) else (page, "application/json")
        return SimpleNamespace(content=content, headers={"content-type": kind}, final_url=url, status_code=200,
                               retrieved_at=datetime.now(UTC).isoformat(), sha256="x")

    def get_json(self, url, max_bytes=0):
        return json.loads(self.fetch(url).content)

    def close(self):
        pass


def _status(code: int, url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(str(code), request=request, response=httpx.Response(code, request=request))


@pytest.fixture
def registries():
    """Discovered networks are registered globally; put the registries back afterwards."""
    from app.intelligence.feeds import BY_KEY
    from app.intelligence.providers import MEDIA_HOSTS

    saved = (dict(BY_KEY), dict(MEDIA_HOSTS), dict(discovery.DISCOVERED_MAPPINGS))
    yield
    for target, original in zip((BY_KEY, MEDIA_HOSTS, discovery.DISCOVERED_MAPPINGS), saved):
        target.clear()
        target.update(original)


def _scout(tmp_path, pages, manager=None, results=()):
    search = lambda query, limit, domains=None: [SimpleNamespace(url=url, title=title) for url, title in results]
    return SourceScout(DiscoveryStore(tmp_path / "discovery.db"), search=search, fetcher=Fetcher(pages),
                       manager=manager, requests_per_hour=100)


def test_catalogs_are_recognised_by_shape_and_small_or_media_less_lists_are_not():
    mapping = analyse_catalog(json.loads(_catalog("cams.example.gov")))
    assert mapping["count"] == 12 and mapping["lat"] == "location.lat" and mapping["image"] == "image"
    assert mapping["media_hosts"] == ["cams.example.gov"] and mapping["title"] == "name"
    assert analyse_catalog(json.loads(_catalog("cams.example.gov", 5))) is None
    no_media = {"items": [{"name": f"n{index}", "lat": 1.0, "lon": 2.0} for index in range(20)]}
    assert analyse_catalog(no_media) is None
    geojson = {"type": "FeatureCollection", "features": [
        {"geometry": {"type": "Point", "coordinates": [-75, 45 + index / 100]},
         "properties": {"title": f"Cam {index}", "stream": f"https://video.example.gov/c{index}/playlist.m3u8"}}
        for index in range(15)]}
    assert analyse_catalog(geojson)["coordinates"] == "geometry.coordinates"


def test_private_camera_directories_and_aggregators_are_never_sources(tmp_path):
    assert "never used" in refused_domain("https://www.insecam.org/en/bycity/Hyderabad/")
    assert "never used" in refused_domain("https://www.shodan.io/search?query=webcam")
    assert "original publisher" in refused_domain("https://argosatlas.com/en/cameras/")
    scout = _scout(tmp_path, {}, results=[("https://www.insecam.org/en/", "live cameras"),
                                          ("https://data.city.example.gov/traffic-cameras.json", "Traffic cameras")])
    result = scout.discover("x")
    assert result["candidates"] == 1 and scout.store.sources()[0]["domain"] == "example.gov"


def test_restrictions_are_recorded_with_what_access_is_needed_and_never_bypassed(tmp_path):
    urls = {"https://a.example.gov/cams.json": _status(401, "https://a.example.gov/cams.json"),
            "https://b.example.gov/cams.json": _status(403, "https://b.example.gov/cams.json"),
            "https://c.example.gov/cams.json": PermissionError("robots.txt disallows retrieval")}
    scout = _scout(tmp_path, urls)
    for url in [*urls, "https://d.example.gov/cams.json?apikey=abc"]:
        scout.store.add(url, "cams", "test")
    outcomes = [scout.cycle("probe")["status"] for _ in range(4)]
    assert outcomes == ["needs_access", "refused", "rejected", "needs_access"]
    assert "https://d.example.gov/cams.json?apikey=abc" not in scout.fetcher.calls  # a key in a URL is never used
    report = scout.report()
    assert any("requires authorization" in row["access"] for row in report["needs_access"])
    assert any("does not try to get around it" in row["access"] for row in report["needs_access"])


def test_an_official_catalog_on_its_own_domain_goes_live_by_itself(tmp_path, registries):
    from app.intelligence.manager import IntelligenceManager

    url = "https://data.roads.example.gov/cameras.json"
    fetcher = Fetcher({url: _catalog("cams.roads.example.gov")})
    manager = IntelligenceManager(tmp_path / "intelligence.db", fetcher, None, extended=False)
    try:
        scout = SourceScout(DiscoveryStore(tmp_path / "discovery.db"), search=lambda *a, **k: [], fetcher=fetcher,
                            manager=manager)
        scout.store.add(url, "Road cameras", "test")
        result = scout.cycle("probe")
        assert result["status"] == "active" and result["cameras"] == 12
        cameras = manager.cameras(feed=result["feed"])
        assert cameras["total_matching"] == 12
        assert cameras["cameras"][0]["details"]["image_url"].startswith("https://cams.roads.example.gov/snapshots/")
        # After a restart the network comes back from the store.
        from app.intelligence.feeds import BY_KEY
        del BY_KEY[result["feed"]]
        assert restore_discovered(tmp_path / "discovery.db") == [result["feed"]]
    finally:
        manager.close()


def test_anything_else_waits_for_the_owner_and_approval_turns_it_on(tmp_path, registries):
    from app.intelligence.manager import IntelligenceManager

    url = "https://citycams.example.com/api/cameras"
    fetcher = Fetcher({url: _catalog("media.othercdn.net")})
    manager = IntelligenceManager(tmp_path / "intelligence.db", fetcher, None, extended=False)
    try:
        scout = SourceScout(DiscoveryStore(tmp_path / "discovery.db"), search=lambda *a, **k: [], fetcher=fetcher,
                            manager=manager)
        scout.store.add(url, "City cams", "test")
        assert scout.cycle("probe")["status"] == "proposed"
        text = render(scout.report())
        assert 'approve source 1' in text and "Waiting for you" in text
        approved = scout.approve(1)
        assert approved["cameras"] == 12 and "now live" in render(approved)
        with pytest.raises(ValueError):
            scout.approve(1)  # already active
    finally:
        manager.close()


def test_html_pages_lead_to_their_data_links(tmp_path):
    page = ('<a href="/open-data/traffic-cameras.geojson">Cameras</a> <a href="/about">About</a>'
            '<a href="https://x.example.gov/api/cameras?key=SECRET">keyed</a>').encode()
    scout = _scout(tmp_path, {"https://roads.example.gov/data": (page, "text/html")})
    scout.store.add("https://roads.example.gov/data", "Open data", "test")
    assert scout.cycle("probe")["status"] == "page_read"
    queued = [row["url"] for row in scout.store.sources("found")]
    assert queued == ["https://roads.example.gov/open-data/traffic-cameras.geojson"]


def test_mcp_suggestions_say_what_access_they_need_and_install_nothing(tmp_path):
    registry = {"servers": [
        {"server": {"name": "ai.example/maps", "description": "Maps", "remotes": [
            {"type": "streamable-http", "url": "https://maps.example.ai/mcp",
             "headers": [{"name": "Authorization", "description": "Bearer token", "isRequired": True}]}]}},
        {"server": {"name": "io.example/weather", "description": "Weather", "remotes": [
            {"type": "streamable-http", "url": "https://wx.example.io/mcp"}]}}]}
    registry["servers"].append({"server": {"name": "io.example/recipes", "description": "Cooking"}})  # no topic: skipped
    registry["metadata"] = {"nextCursor": "io.example/weather:1.0"}
    scout = _scout(tmp_path, {"https://registry.modelcontextprotocol.io/v0/servers?limit=100": json.dumps(registry).encode(),
                              "https://registry.modelcontextprotocol.io/v0/servers?limit=100&cursor=io.example%2Fweather%3A1.0":
                              json.dumps({"servers": [], "metadata": {}}).encode()})
    first = scout.mcp()
    assert first["new_servers"] == 2 and first["listing_continues"]
    # The next job reads the next page of the listing, and the one after starts again from the top.
    assert scout.mcp()["listing_continues"] is False and scout.store.preference("mcp_cursor") == ""
    suggestions = {row["name"]: row["access"] for row in scout.report()["mcp_suggestions"]}
    assert suggestions == {"ai.example/maps": "needs Bearer token", "io.example/weather": "remote, no credentials listed"}


def test_it_waits_in_privacy_mode_while_the_owner_works_and_when_its_budget_is_spent(tmp_path):
    scout = _scout(tmp_path, {})
    scout.authority = SimpleNamespace(privacy_mode=True)
    assert scout.may_run() == (False, "privacy mode is on")
    scout.authority = SimpleNamespace(privacy_mode=False)
    scout.learner = SimpleNamespace(may_run=lambda: (False, "paused: answering your question"))
    assert scout.may_run()[1] == "paused: answering your question"
    scout.learner = None
    scout.requests_per_hour = 1
    assert scout._spend() and not scout._spend()
    assert "budget spent" in scout.may_run()[1]


def test_discovery_requests_route_to_its_tools():
    assert discovery_request("what new sources did you find?") == {"tool": "discovery_status", "arguments": {}}
    assert discovery_request("discover new camera sources")["tool"] == "discovery_run"
    assert discovery_request("approve source 12") == {"tool": "discovery_approve", "arguments": {"source_id": 12}}
    assert discovery_request("find public cameras in London") is None


def test_priority_searches_rotate_indian_regions_without_abandoning_global_gaps(tmp_path):
    scout = _scout(tmp_path, {})
    scout.priority_regions = ('India', 'Makkah')
    first, _domains = scout.query()
    second, _domains = scout.query()
    third, _domains = scout.query()
    fourth, _domains = scout.query()
    assert 'India Andhra Pradesh' in first and 'Makkah' in second
    assert 'India Arunachal Pradesh' in fourth
    assert any(region in third for region in discovery.REGIONS)
    assert not third.startswith('India ')
    restarted = _scout(tmp_path, {})
    restarted.priority_regions = ('India', 'Makkah')
    assert 'Makkah' in restarted.query()[0]
    assert restarted.report()['india_regions_in_rotation'] == 36


def test_discovery_recognizes_official_rss_and_connects_it_through_normal_refresh(tmp_path, registries):
    from app.intelligence.manager import IntelligenceManager
    url = 'https://news.example.gov/rss.xml'
    rss = b'<rss version="2.0"><channel><item><title>India weather alert</title><link>https://news.example.gov/alert</link><description>Heavy rainfall in Mumbai</description><pubDate>Wed, 07 Oct 2026 00:00:00 GMT</pubDate></item></channel></rss>'
    pages = {url: (rss, 'application/rss+xml')}
    manager = IntelligenceManager(tmp_path / 'intelligence.db', Fetcher(pages), extended=True)
    scout = _scout(tmp_path, pages, manager)
    try:
        scout.store.add(url, 'Official news RSS', 'test')
        result = scout.cycle('probe')
        assert result['status'] == 'active' and result['reports'] == 1
        assert result['feed'].startswith('news_discovered_')
        rows = manager.dashboard(feed=result['feed'])['events']
        assert len(rows) == 1 and rows[0]['category'] == 'news'
        assert rows[0]['url'] == 'https://news.example.gov/alert'
        assert restore_discovered(scout.store.path) == [result['feed']]
    finally:
        manager.close()


def test_rss_links_are_found_on_publisher_pages_and_untrusted_feeds_wait(tmp_path):
    url = 'https://publisher.example/rss.xml'
    body = b'<rss><channel><item><title>Public update</title><link>https://publisher.example/report</link></item></channel></rss>'
    scout = _scout(tmp_path, {url: (body, 'application/rss+xml')})
    scout.store.add(url, 'news', 'test')
    assert scout.cycle('probe')['status'] == 'proposed'
    assert scout._data_links('https://publisher.example/', '<link href="/rss.xml"><a href="https://insecam.org/feed.xml">x</a>') == [url]


def test_transient_network_failures_are_revisited_and_authorization_blocks_are_preserved(tmp_path):
    scout = _scout(tmp_path, {})
    for url in ('https://a.example.gov/rss.xml', 'https://b.example.gov/cameras.json'):
        scout.store.add(url, 'public feed', 'test')
    first, second = sorted(scout.store.sources(), key=lambda s: s['id'])
    scout.store.update(first['id'], status='unreachable')
    scout.store.update(second['id'], status='needs_access')
    assert scout.store.next_to_probe() is None
    with scout.store.db() as db:
        db.execute('UPDATE sources SET checked_at=?', ('2020-01-01T00:00:00+00:00',))
    assert scout.store.next_to_probe()['id'] == first['id']
