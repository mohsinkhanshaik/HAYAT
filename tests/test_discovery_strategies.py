"""Smarter discovery: the strategy log, category gaps, open-data portals and the isolated trial."""
import json
import random
from types import SimpleNamespace

import pytest

from app.intelligence.discovery import DiscoveryStore, SourceScout, shared_prefix
from app.intelligence.discovery_strategies import (CKAN_PORTALS, KINDS, SmartDiscovery, StrategyLog, arcgis_candidates,
                                                   category_counts, ckan_candidates, socrata_candidates, trial)


@pytest.fixture
def store(tmp_path):
    return DiscoveryStore(tmp_path / "discovery.db")


def test_the_log_counts_what_each_search_yields_and_credits_working_catalogs(store):
    log = StrategyLog(store)
    log.record("ckan:Canada:traffic", "ckan", "traffic camera", ["https://a.ca/cams.json", "https://b.ca/x.json"])
    log.record("web:Chile traffic", "web", "Chile traffic cameras", [])
    log.credit("https://a.ca/cams.json", 340)
    log.credit("https://unknown.example/none.json", 99)  # not found by a logged strategy: nothing to credit
    rows = {row["id"]: row for row in log.summary()}
    assert rows["ckan:Canada:traffic"]["candidates"] == 2 and rows["ckan:Canada:traffic"]["catalogs"] == 1
    assert rows["ckan:Canada:traffic"]["cameras"] == 340 and rows["web:Chile traffic"]["runs"] == 1
    scores = log.kind_scores()
    assert scores["ckan"] > scores["web"] and set(scores) == set(KINDS)


def test_a_search_that_found_nothing_rests_and_the_best_kind_is_chosen_most(store):
    log = StrategyLog(store)
    log.record("arcgis:volcano", "arcgis", "volcano webcam", [])
    assert log.rested("arcgis:volcano") and not log.rested("never-run")
    log.record("socrata:traffic", "socrata", "traffic camera", ["https://x.gov/a.json"])
    log.credit("https://x.gov/a.json", 50)
    picks = [log.choose(random.Random(seed)) for seed in range(200)]
    assert picks.count("socrata") > 120 and len(set(picks)) > 2  # mostly the best, still exploring


def test_cameras_are_counted_by_kind_to_find_category_gaps():
    rows = [{"title": "I-5 at Main", "feed": "cameras_wsdot", "details": {"provider": "WSDOT"}},
            {"title": "Kilauea crater cam", "feed": "cameras_volcanoes", "details": {"provider": "USGS"}},
            {"title": "Harbour view", "feed": "cameras_x", "details": {}}]
    manager = SimpleNamespace(_matching=lambda *args: ([], rows))
    counts = category_counts(manager)
    assert counts["traffic"] == 1 and counts["volcano"] == 1 and counts["coast"] == 1 and counts["mountain"] == 0
    assert category_counts(None)["weather"] == 0


class Portal:
    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def get_json(self, url, max_bytes=0):
        self.urls.append(url)
        return next(value for prefix, value in self.answers.items() if url.startswith(prefix))


def test_open_data_portals_list_their_camera_datasets():
    fetcher = Portal({
        CKAN_PORTALS["Canada"]: {"result": {"results": [{"title": "Traffic cameras", "resources": [
            {"url": "https://ws.example.ca/cameras.geojson", "format": "GeoJSON"}, {"url": "https://ws.example.ca/readme.pdf", "format": "PDF"},
            {"url": "http://insecure.example.ca/c.json", "format": "JSON"}]}]}},
        "https://api.us.socrata.com": {"results": [{"resource": {"id": "b4k4-adkb", "name": "Traffic Cameras"},
                                                     "metadata": {"domain": "datahub.example.gov"}}]},
        "https://www.arcgis.com": {"results": [{"url": "https://services.arcgis.com/ORG/arcgis/rest/services/Cams/FeatureServer",
                                                 "title": "Cams"}, {"url": "https://example.com/not-a-service", "title": "x"}]}})
    assert ckan_candidates(fetcher, CKAN_PORTALS["Canada"], "traffic camera") == [("https://ws.example.ca/cameras.geojson",
                                                                                   "Traffic cameras (geojson)")]
    assert socrata_candidates(fetcher, "traffic camera") == [("https://datahub.example.gov/resource/b4k4-adkb.json?$limit=5000",
                                                              "Traffic Cameras")]
    url, title = arcgis_candidates(fetcher, "traffic camera")[0]
    assert url.startswith("https://services.arcgis.com/ORG/arcgis/rest/services/Cams/FeatureServer/0/query?") and "f=geojson" in url
    assert len(arcgis_candidates(fetcher, "traffic camera")) == 1


class Media:
    def __init__(self, answers):
        self.answers = answers

    def fetch(self, url, max_bytes=0, accept=None, timeout=None):
        answer = self.answers.get(url)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=answer)


CATALOG = [{"name": f"Cam {index}", "lat": 45.0, "lon": -75.0, "image": f"https://cams.example.gov/c{index}.jpg"} for index in range(12)]
MAPPING = {"records_path": "", "image": "image", "video": None, "count": 12}


def test_the_trial_checks_real_pictures_before_anything_goes_live():
    good = Media({item["image"]: b"\xff\xd8\xff\xe0 jpeg" for item in CATALOG})
    result = trial(good, CATALOG, MAPPING, lambda n: True)
    assert result["passed"] and result["sampled"] == 3 and result["working"] == 3
    html = Media({item["image"]: b"<html>login</html>" for item in CATALOG})
    failed = trial(html, CATALOG, MAPPING, lambda n: True)
    assert not failed["passed"] and "not an image or playlist" in failed["detail"]
    dead = Media({item["image"]: ConnectionError("down") for item in CATALOG})
    assert not trial(dead, CATALOG, MAPPING, lambda n: True)["passed"]
    waiting = trial(good, CATALOG, MAPPING, lambda n: False)
    assert waiting["waiting"] and not waiting["passed"]
    hls = Media({"https://v.example.gov/live.m3u8": b"#EXTM3U\n#EXT-X-VERSION:3"})
    assert trial(hls, [{"video": "https://v.example.gov/live.m3u8"}], {"records_path": "", "image": None, "video": "video"},
                 lambda n: True)["passed"]


def test_arcgis_publishers_are_told_apart_on_the_shared_platform(monkeypatch):
    assert shared_prefix("https://services2.arcgis.com/ORG1/arcgis/rest/services/Cams/FeatureServer/0/query?x") == \
        "services2.arcgis.com/org1/cams"
    from app.intelligence import feeds
    monkeypatch.setitem(feeds.BY_KEY, "test_arcgis", SimpleNamespace(
        url="https://services3.arcgis.com/T4Q/arcgis/rest/services/Wildfires/FeatureServer/0/query"))
    scout = SourceScout(DiscoveryStore(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "d.db"),
                        search=lambda *a: [], fetcher=None)
    assert scout._known("https://services3.arcgis.com/T4Q/arcgis/rest/services/Wildfires/FeatureServer/0/query?f=geojson")
    assert not scout._known("https://services2.arcgis.com/OTHER/arcgis/rest/services/TrafficCams/FeatureServer/0/query")


def test_a_discovery_cycle_picks_a_strategy_runs_it_and_logs_it(store):
    queued = []

    class Scout:
        def __init__(self):
            self.store, self.manager = store, SimpleNamespace(_matching=lambda *args: ([], []))
            self.fetcher = Portal({"https://api.us.socrata.com": {"results": [
                {"resource": {"id": "abcd-1234"}, "metadata": {"domain": "data.example.gov"}}]},
                "https://www.arcgis.com": {"results": []}, **{portal: {"result": {"results": []}} for portal in CKAN_PORTALS.values()}})

        def coverage(self):
            return {"countries": {"Canada": 10}}

        def query(self):
            return "Chile traffic cameras", None

        def web_search(self, query, domains):
            return [("https://transito.example.cl/camaras.json", "Camaras")]

        def consider(self, url, title, found_by):
            queued.append((url, found_by))
            return True
    smart = SmartDiscovery(Scout(), rng=random.Random(4))
    results = [smart.run() for _ in range(12)]
    kinds = {result["kind"] for result in results}
    assert kinds >= {"web", "socrata"} and all(result["ok"] for result in results)
    assert any(url.startswith("https://data.example.gov/resource/abcd-1234.json") for url, _found_by in queued)
    assert sum(row["runs"] for row in StrategyLog(store).summary(50)) == 12


def test_the_report_shows_strategies_categories_and_failed_trials(store):
    scout = SourceScout(store, search=lambda *a: [], fetcher=None)
    StrategyLog(store).record("socrata:traffic", "socrata", "traffic camera", ["https://x.gov/a.json"])
    value = scout.report()
    assert value["strategies"][0]["id"] == "socrata:traffic" and set(value["kind_scores"]) == set(KINDS)
    assert "traffic" in value["category_coverage"] and value["trial_failed"] == []
    assert json.dumps(value, default=str)
