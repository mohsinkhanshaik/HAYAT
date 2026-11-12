"""Geometry and Earth distances are worked out exactly; tax and rumour questions go to the authority's record."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.calculation import calculation_answer
from app.geometry import geometry_answer
from app.intent.live_web import needs_live_web
from app.research.fetcher import FetchedResource
from app.research.knowledge import KnowledgeStore
from app.research.official_pack import _official_url, due_entries, refresh_due_packs
from app.research.query_planner import ResearchQueryPlanner
from app.research.models import ResearchBudget
from app.research.tax_sources import authorities, authorities_for, planned_queries, sources_answer


# ------------------------------------------------------------------ geometry
@pytest.mark.parametrize("text, expected", [
    ("What is the area of a circle with radius 3 cm?", "≈ 28.274334 cm²"),
    ("circumference of a circle with diameter 10 m", "≈ 31.415927 m."),
    ("volume of a sphere with radius 2 m", "≈ 33.510322 m³"),
    ("volume of a cylinder with radius 2 cm and height 5 cm", "≈ 62.831853 cm³"),
    ("volume of a cone with radius 3 cm and height 4 cm", "≈ 37.699112 cm³"),
    ("surface area of a cube with side 3 m", "= 54 m²"),
    ("area of a triangle with base 10 cm and height 4 cm", "= 20 cm²"),
    ("area of a triangle with sides 3, 4 and 5 cm", "Area ≈ 6 cm²"),
    ("sum of interior angles of a hexagon", "= 720°"),
    ("each interior angle of a regular octagon", "= 135°"),
    ("third angle of a triangle with angles 50 and 60 degrees", "= 70°"),
    ("distance between (1, 2) and (4, 6)", "≈ 5 units"),
])
def test_geometry_is_worked_out_from_the_request(text, expected):
    answer = calculation_answer(text)
    assert answer is not None and expected in answer


def test_great_circle_distance_between_coordinates():
    # Hyderabad to London is about 7,720 km along the surface.
    answer = geometry_answer("great-circle distance between 17.385, 78.4867 and 51.5074, -0.1278")
    assert answer.startswith("Great-circle distance ≈ 7,721.9 km") and "haversine" in answer
    assert "Latitude must be" in geometry_answer("distance between coordinates 95, 0 and 10, 10")


@pytest.mark.parametrize("text", [
    "area of a circle", "area of a triangle with base 10 cm and height 4 m",
    "what is the area of my kitchen", "volume of a cylinder with radius 2 cm",
    "distance between Paris and London",
])
def test_incomplete_or_mixed_geometry_declines(text):
    assert geometry_answer(text) is None


def test_impossible_triangle_is_named_not_computed():
    assert "can't form a triangle" in geometry_answer("area of a triangle with sides 1, 2 and 5")


# ------------------------------------------------------------------ tax authorities
def test_every_state_and_dc_has_an_official_https_or_government_agency():
    states = [row for row in authorities() if row["country"] == "US" and row["code"] != "US"]
    assert len(states) == 51 and len({row["code"] for row in states}) == 51
    for row in states:
        assert row["url"].startswith(("https://", "http://")) and row["site"] and row["agency"]


@pytest.mark.parametrize("text, codes", [
    ("What is the sales tax rate in Texas?", ["TX"]),
    ("Washington DC income tax", ["DC"]),
    ("Washington state sales tax", ["WA"]),
    ("Does West Virginia have a state income tax", ["WV"]),
    ("Kansas and Arkansas property tax", ["AR", "KS"]),
    ("federal tax brackets 2026", ["US"]),
])
def test_tax_questions_name_their_agencies(text, codes):
    assert sorted(row["code"] for row in authorities_for(text)) == sorted(codes)


def test_place_names_outside_tax_questions_are_ignored():
    assert authorities_for("How is Georgia doing in the election?") == []


def test_india_gst_and_income_tax_go_to_their_own_agencies():
    assert [row["site"] for row in authorities_for("GST rate on laptops in India")] == ["gst.gov.in", "cbic.gov.in"]
    assert [row["site"] for row in authorities_for("India income tax slabs")] == ["incometax.gov.in"]


def test_research_plans_a_search_on_the_state_agency():
    plan = ResearchQueryPlanner().plan("What is the sales tax rate in Texas?", "NORMAL", ResearchBudget(searches=6))
    assert any(item.text.startswith("site:comptroller.texas.gov ") for item in plan)
    many = planned_queries("Which states have no income tax?", "states no income tax")
    assert len(many) == 1 and "site:" not in many[0][0]


def test_rumour_questions_also_seek_fact_checks_and_the_record():
    plan = ResearchQueryPlanner().plan("was the moon landing faked?", "NORMAL", ResearchBudget(searches=8))
    texts = [item.text for item in plan]
    assert any(text.endswith("fact check") for text in texts)
    assert any(text.endswith("official record evidence") for text in texts)


def test_the_tool_lists_the_source_and_holds_no_rates():
    result = sources_answer("Ohio")
    assert result["authorities"][0]["site"] == "tax.ohio.gov" and "irs.gov" in result["source"]
    assert "Rates and rules change" in result["note"]


# ------------------------------------------------------------------ routing
@pytest.mark.parametrize("text, reason", [
    ("was the moon landing fake?", "fact_check_request"),
    ("is climate change a hoax?", "fact_check_request"),
    ("is the earth flat?", "fact_check_request"),
    ("did the Tuskegee study really happen?", "fact_check_request"),
    ("what is the sales tax in Texas?", "tax_rule"),
    ("Texas sales tax rate", "tax_rule"),
    ("federal tax brackets 2026", "tax_rule"),
])
def test_rumours_and_tax_rules_are_researched_not_recalled(text, reason):
    need = needs_live_web(text)
    assert need is not None and need.reason == reason


@pytest.mark.parametrize("text", ["is my screen flat?", "is this tie flat enough for a wedding?",
                                  "what is income tax?", "I paid Texas sales tax", "can you make a fake invoice?"])
def test_ordinary_questions_are_not_taken_as_rumours_or_tax_lookups(text):
    need = needs_live_web(text)
    assert need is None or need.reason not in {"fact_check_request", "tax_rule"}


# ------------------------------------------------------------------ verified reference pack
def test_reviewed_government_pages_are_official_but_other_pages_on_the_host_are_not():
    assert _official_url("https://www.irs.gov/filing/federal-income-tax-rates-and-brackets")
    assert _official_url("https://www.who.int/news-room/fact-sheets/detail/autism-spectrum-disorders")
    assert not _official_url("https://www.irs.gov/some-unreviewed-page")
    assert not _official_url("https://www.irs.gov.evil.test/filing/federal-income-tax-rates-and-brackets")
    assert not _official_url("http://www.irs.gov/filing/federal-income-tax-rates-and-brackets")


def test_reference_catalog_cites_only_reviewed_official_pages():
    from app.research.official_pack import PACKS
    from app.config import PROJECT_ROOT

    entries = json.loads((PROJECT_ROOT / "config/reference-knowledge.json").read_text(encoding="utf-8"))
    assert len(entries) >= 20 and ("config/reference-knowledge.json", "data/knowledge/reference-pack-report.json") in PACKS
    topics = " ".join(entry["topic"].lower() for entry in entries)
    for area in ("earth", "energy", "finance", "taxation", "public data", "fact check", "documented real conspiracy"):
        assert area in topics
    for entry in entries:
        assert _official_url(entry["url"]) and entry["facts"]
        assert all(fact["claim"] and fact["excerpt"] for fact in entry["facts"])


def test_only_expiring_failed_or_new_entries_are_due():
    now = datetime(2026, 10, 31, tzinfo=UTC)
    entries = [{"url": u} for u in ("a", "b", "c", "d")]
    report = {"entries": [
        {"url": "a", "status": "imported", "valid_until": (now + timedelta(days=20)).isoformat()},
        {"url": "b", "status": "imported", "valid_until": (now + timedelta(days=1)).isoformat()},
        {"url": "c", "status": "failed"}]}
    assert [entry["url"] for entry in due_entries(entries, report, now)] == ["b", "c", "d"]


def test_refresh_reverifies_due_entries_and_keeps_a_good_import_when_a_retry_fails(tmp_path, monkeypatch):
    from app.research import official_pack

    url = "https://docs.python.org/3/library/queue.html"
    excerpt = "The queue module implements multi-producer, multi-consumer queues."
    (tmp_path / "config").mkdir()
    (tmp_path / "config/pack.json").write_text(json.dumps([
        {"url": url, "topic": "Python queue", "facts": [{"claim": excerpt, "excerpt": excerpt}]}]), encoding="utf-8")
    monkeypatch.setattr(official_pack, "PACKS", (("config/pack.json", "data/report.json"),))
    content = f"<html><title>queue</title><main><p>{excerpt}</p></main></html>".encode()
    resource = FetchedResource(url, url, 200, {"content-type": "text/html"}, content, 0,
                               datetime.now(UTC).isoformat(), hashlib.sha256(content).hexdigest())
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    first = refresh_due_packs(store, SimpleNamespace(fetch=lambda *a, **k: resource), tmp_path)
    assert first == {"checked": 1, "refreshed": 1, "failed": 0}
    report = json.loads((tmp_path / "data/report.json").read_text(encoding="utf-8"))
    assert report["entries"][0]["status"] == "imported"
    # Fresh: nothing is fetched.
    assert refresh_due_packs(store, SimpleNamespace(fetch=None), tmp_path)["refreshed"] == 0

    def broken(*args, **kwargs):
        raise OSError("offline")

    later = datetime.now(UTC) + timedelta(days=40)
    monkeypatch.setattr(official_pack, "due_entries", lambda entries, rep, now: list(entries))
    assert refresh_due_packs(store, SimpleNamespace(fetch=broken), tmp_path, now=later)["failed"] == 1
    kept = json.loads((tmp_path / "data/report.json").read_text(encoding="utf-8"))["entries"][0]
    assert kept["status"] == "imported" and "offline" in kept["last_refresh_error"]


def test_rumour_answers_lead_with_the_official_record_on_the_same_subject():
    from app.research.manager import ResearchManager

    def claim(text, topic, url):
        return {"claim_text": text, "topic": topic, "stale": False, "status": "SUPPORTED",
                "evidence": [{"final_url": url, "title": "Official page"}]}

    claims = [
        claim("The current warming trend is clearly the result of human activities since the mid-1800s.",
              "Climate change evidence warming human cause hoax fact check", "https://science.nasa.gov/climate-change/evidence/"),
        claim("Neil Armstrong was the first human to set foot on the Moon.",
              "Moon landing Apollo 11 hoax conspiracy fact check Neil Armstrong", "https://www.nasa.gov/mission/apollo-11/"),
        claim("A blog says the Moon landing was staged.", "Moon landing", "https://example.com/moon"),
    ]
    stub = SimpleNamespace(knowledge=SimpleNamespace(search_claims=lambda *a, **k: claims),
                           _RUMOUR_WORDS=ResearchManager._RUMOUR_WORDS)
    note = ResearchManager._official_record(stub, "was the moon landing fake?")
    assert note.startswith("What the official record says")
    assert "Neil Armstrong" in note and "warming" not in note and "blog" not in note
    assert ResearchManager._official_record(stub, "what is the capital of France?") is None


def test_verified_indian_state_tax_departments_are_named_without_colliding_with_us_codes():
    indian = [row for row in authorities() if row["code"].startswith("IN-")]
    assert {row["region"] for row in indian} >= {"Telangana", "Odisha", "Uttar Pradesh", "Ladakh"}
    assert all(row["site"].endswith((".gov.in", ".nic.in")) for row in indian)
    assert [row["code"] for row in authorities_for("professional tax in Telangana")] == ["IN-TG"]
    assert [row["site"] for row in authorities_for("Orissa VAT")] == ["odishatax.gov.in"]
    assert [row["code"] for row in authorities_for("Louisiana sales tax")] == ["LA"]
    assert [row["code"] for row in authorities_for("Ladakh state taxes")] == ["IN-LA"]
    assert "site:tgct.gov.in " in planned_queries("professional tax in Telangana", "professional tax")[0][0]
    every = authorities_for("which Indian states have the highest stamp duty tax?")
    assert len([row for row in every if row["code"].startswith("IN-")]) == len(indian)
    assert authorities_for("Telangana weather tomorrow") == []


# ------------------------------------------------------------------ state agencies beyond tax
def test_every_state_and_dc_has_official_agencies_by_topic():
    from app.research.state_agencies import catalog

    states = catalog()["states"]
    assert len(states) == 51
    for state in states:
        assert state["agencies"], state["region"]
        for agency in state["agencies"]:
            assert agency["topic"] in catalog()["topics"] and agency["site"] and agency["status"]
            assert "laws.com" not in agency["site"] and "perfdrive" not in agency["site"]


@pytest.mark.parametrize("text, code, topic", [
    ("How do I register an LLC in Michigan?", "MI", "business"),
    ("new hire reporting in Texas", "TX", "new_hire"),
    ("unemployment claim in Nevada", "NV", "unemployment"),
    ("how do I sell to the state of California as a vendor", "CA", "procurement"),
    ("minimum wage in Ohio", "OH", "labor"),
])
def test_state_questions_find_the_state_agency_for_their_topic(text, code, topic):
    from app.research.state_agencies import agencies_for

    found = agencies_for(text)
    assert [(item["code"], item["topic"]) for item in found] == [(code, topic)]
    assert found[0]["agencies"][0]["site"]


def test_a_topic_without_a_listed_agency_falls_back_to_the_states_own_domain():
    from app.research.state_agencies import agencies_for

    # Ohio's IRS page lists no labor agency or portal: tax.ohio.gov gives ohio.gov.
    assert [agency["site"] for agency in agencies_for("minimum wage in Ohio")[0]["agencies"]] == ["ohio.gov"]


def test_my_state_means_the_owners_home_state_from_the_environment(monkeypatch):
    from app.research.state_agencies import agencies_for

    monkeypatch.setenv("HAYAT_HOME_STATE", "Michigan")
    assert [row["code"] for row in authorities_for("what is the income tax rate in my state?")] == ["MI"]
    assert agencies_for("how do I start a business in my state")[0]["code"] == "MI"
    monkeypatch.setenv("HAYAT_HOME_STATE", "")
    assert authorities_for("what is the income tax rate in my state?") == []


def test_state_rules_are_researched_on_the_agency_site():
    from app.research.state_agencies import planned_queries as state_queries

    need = needs_live_web("How do I register an LLC in Michigan?")
    assert need is not None and need.reason == "state_rule"
    assert state_queries("How do I register an LLC in Michigan?", "register LLC")[0][0] == "site:michigan.gov register LLC"
    assert needs_live_web("I registered my LLC in Michigan") is None or \
        needs_live_web("I registered my LLC in Michigan").reason != "state_rule"
    assert needs_live_web("Michigan weather today").reason != "state_rule"
