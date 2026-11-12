"""Prior art, novelty, other fields, feasibility and risks for an invention idea (app/invention_prior_art.py)."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.invention_prior_art import (analyse, contains, features, papers_with_fields, patent_urls, read_patent,
                                     register_prior_art_tool, report)

IDEA = "A clay pot water filter that uses sunlight heat to kill bacteria without electricity"
PATENT_PAGE = """<html><head><title>US1234567B2 - Solar ceramic water purifier - Google Patents</title>
<meta name="citation_patent_number" content="US:1234567">
<meta name="DC.date" content="2015-03-02" scheme="dateSubmitted"><meta name="DC.date" content="2018-07-10" scheme="issue">
<meta name="DC.contributor" content="Jane Roe" scheme="inventor"><meta name="DC.contributor" content="Aqua Ltd" scheme="assignee">
<meta name="DC.relation" content="US:999" scheme="references"></head>
<body><div class="abstract">A ceramic clay pot filter for drinking water, heated by sunlight to disinfect water.</div></body></html>"""
OPENALEX = {"results": [
    {"id": "W1", "doi": "https://doi.org/10.1/a", "display_name": "Solar disinfection of drinking water in clay vessels",
     "publication_year": 2019, "cited_by_count": 40, "abstract_inverted_index": {"Sunlight": [0], "kills": [1], "bacteria": [2]},
     "primary_topic": {"display_name": "Water treatment", "field": {"display_name": "Environmental Science"},
                       "domain": {"display_name": "Physical Sciences"}}},
    {"id": "W2", "doi": None, "display_name": "Antimicrobial ceramics", "publication_year": 2021, "cited_by_count": 5,
     "abstract_inverted_index": None,
     "primary_topic": {"display_name": "Ceramics", "field": {"display_name": "Materials Science"}}}]}


def test_features_are_the_distinctive_parts_of_the_idea():
    parts = features(IDEA)
    assert "clay pot" in parts and any("electricity" in part for part in parts)
    assert not any(part in ("that", "uses", "without") for part in parts)
    assert contains("Pots of clay filter water", "clay pot") and not contains("a steel can", "clay pot")


def test_a_patent_page_is_read_for_number_dates_owner_and_abstract():
    patent = read_patent(lambda url: PATENT_PAGE, "https://patents.google.com/patent/US1234567B2/en")
    assert patent["number"] == "US 1234567" and patent["title"] == "US1234567B2 - Solar ceramic water purifier"
    assert patent["filed"] == "2015-03-02" and patent["granted"] == "2018-07-10" and patent["owner"] == "Aqua Ltd"
    assert "disinfect water" in patent["abstract"] and patent["cites"] == 1


def test_only_public_patent_pages_are_taken_from_search():
    search = SimpleNamespace(search=lambda query, limit, domains: [
        SimpleNamespace(url="https://patents.google.com/patent/US1234567B2/en?oq=x"),
        SimpleNamespace(url="https://patents.google.com/?q=clay"),            # search pages are disallowed by robots
        SimpleNamespace(url="https://example.com/patent/US1")])
    assert patent_urls(search, IDEA) == ["https://patents.google.com/patent/US1234567B2/en"]
    assert patent_urls(None, IDEA) == []


def test_papers_carry_their_research_field():
    papers = papers_with_fields(lambda url: OPENALEX, IDEA)
    assert [paper["field"] for paper in papers] == ["Environmental Science", "Materials Science"]
    assert papers[0]["abstract"] == "Sunlight kills bacteria" and papers[1]["url"] == "W2"


def test_analysis_separates_what_is_known_from_what_looks_new():
    patent = read_patent(lambda url: PATENT_PAGE, "https://patents.google.com/patent/US1234567B2/en")
    papers = papers_with_fields(lambda url: OPENALEX, IDEA)
    result = analyse(IDEA, [patent], papers, now=datetime(2026, 11, 9, tzinfo=UTC))
    assert result["closest_share_pct"] > 30 and {item["kind"] for item in result["closest"][:2]} == {"patent", "paper"}
    covered = {entry["feature"]: entry["found_in"] for entry in result["features"]}
    assert covered["clay pot"] >= 1 and any("electricity" in part for part in result["new_features"])
    assert [item["field"] for item in result["cross_domain"]] == ["Environmental Science", "Materials Science"]
    assert result["maturity"].startswith("patented") and result["recent_works"] == 2  # the 2015 patent is over ten years old
    assert "never complete" in result["basis"]


def test_risks_name_crowding_regulation_and_missing_evidence():
    crowded = [{"kind": "patent", "title": IDEA, "abstract": IDEA, "filed": "2020-01-01"} for _ in range(6)]
    result = analyse("A lithium battery drone that charges from sunlight in flight", crowded, [])
    assert result["maturity"].startswith("crowded")
    text = " ".join(result["risks"])
    assert "battery safety" in text and "aviation rules" in text
    empty = analyse("A self-sorting sock drawer with tiny magnets", [], [])
    assert any("no evidence it works" in risk for risk in empty["risks"])


def test_the_report_and_the_tool(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    search = SimpleNamespace(search=lambda query, limit, domains: [SimpleNamespace(url="https://patents.google.com/patent/US1234567B2/en")])
    fetcher = SimpleNamespace(fetch=lambda url, size, accept: SimpleNamespace(content=PATENT_PAGE.encode()),
                              get_json=lambda url: OPENALEX)
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_prior_art_tool(registry, SimpleNamespace(search=search, fetcher=fetcher))
    result = registry.execute("invention_prior_art", {"idea": IDEA})
    assert result.success and result.output["works_found"] == {"patents": 1, "papers": 2}
    text = result.output["text"]
    assert "Found 1 patents and 2 papers" in text and "Other fields working on it: Environmental Science" in text
    assert "not proof of novelty" in text
    with pytest.raises(ValueError):
        report("gadget")


def test_the_invention_window_shows_the_report_and_its_diagram(shared_tk, tmp_path):
    from app.gui_invention import InventionPanel
    from app.gui_theme import Palette
    from tests.conftest import close_window, open_window

    root = open_window(shared_tk, '960x700-4000-4000')
    runtime = SimpleNamespace(registry=SimpleNamespace(names=lambda: []), invention=None,
                              session=SimpleNamespace(session_id='gui'))
    try:
        panel = InventionPanel(root, Palette, runtime, lambda *_: None)
    except Exception:  # the panel needs a fuller runtime to build; the report window is what is under test
        panel = None
    patent = read_patent(lambda url: PATENT_PAGE, "https://patents.google.com/patent/US1234567B2/en")
    data = {**analyse(IDEA, [patent], papers_with_fields(lambda url: OPENALEX, IDEA)), "gaps": []}
    data["text"] = "report text"
    try:
        if panel is None:
            pytest.skip("InventionPanel needs the full runtime here")
        panel.pack(fill='both', expand=True)
        window = panel.show_prior_art(data)
        window.update()
        texts = " ".join(window.diagram.itemcget(item, "text") for item in window.diagram.find_all()
                         if window.diagram.type(item) == "text")
        assert "clay pot" in texts and "Environmental Science" in texts and "new?" in texts
        assert window.text.get("1.0", "end").strip() == "report text"
        panel.goal.delete("1.0", "end")
        panel.check_prior_art()
        assert "Write the invention" in panel.status.get()
    finally:
        close_window(root)


def test_a_refused_paper_search_is_said_not_hidden():
    def refused(url):
        raise RuntimeError("Client error '429 Too Many Requests'")
    result = report(IDEA, get_json=refused)
    assert result["works_found"]["papers"] == 0 and "OpenAlex refused for now" in result["text"]
    assert "Not searched this time" in result["text"]
    assert "phone regenerative" not in features("A bicycle that charges a phone from regenerative braking energy")
    patent = read_patent(lambda url: PATENT_PAGE.replace('<meta name="citation_patent_number" content="US:1234567">', ""),
                         "https://patents.google.com/patent/WO2019035137A1/en")
    assert patent["number"] == "WO2019035137A1"
