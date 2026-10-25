"""Scholarly records in public-work research: exact name parts, namesakes kept apart, context only ranks.

OpenAlex's answers are stand-ins shaped like the real API (checked live on 2026-10-17: its fuzzy author
search for "Geoffrey Hinton" also returned Yoshua Bengio).
"""
from types import SimpleNamespace

import pytest

from app.research.scholarly import Scholarly, name_matches, scholarly_lines

AUTHORS = {"results": [
    {"id": "https://openalex.org/A1", "display_name": "Jane Q. Example", "works_count": 40, "cited_by_count": 900,
     "orcid": "https://orcid.org/0000-0001-0000-0001",
     "last_known_institutions": [{"display_name": "Example Institute of Optics", "country_code": "US"}],
     "topics": [{"display_name": "Optical Physics"}]},
    {"id": "https://openalex.org/A2", "display_name": "Jane Example", "works_count": 120, "cited_by_count": 3000,
     "last_known_institutions": [{"display_name": "Another University"}], "topics": [{"display_name": "Medieval History"}]},
    {"id": "https://openalex.org/A3", "display_name": "Janet Exampleton", "works_count": 500, "cited_by_count": 9000},
    {"id": "https://openalex.org/A4", "display_name": "Yoshua Bengio", "works_count": 1573, "cited_by_count": 576688},
]}
WORKS = {"results": [{"display_name": "Light in fibres", "publication_year": 2021, "doi": "https://doi.org/10.1/abc",
                      "cited_by_count": 120}]}


def fake(asked):
    def get_json(url):
        asked.append(url)
        if "/authors?" in url:
            return AUTHORS
        return WORKS
    return get_json


def test_every_part_of_the_name_must_appear():
    assert name_matches("Jane Example", "Jane Q. Example") and name_matches("José Núñez", "Jose Nunez")
    assert not name_matches("Jane Example", "Janet Exampleton") and not name_matches("Geoffrey Hinton", "Yoshua Bengio")
    assert name_matches("Geoffrey Hinton", "G. Hinton", ["Geoffrey E. Hinton"])  # another spelling OpenAlex records
    # Live 2026-10-17: Bengio's record listed "Geoffrey Hinton" among its spellings; the family name decides.
    assert not name_matches("Geoffrey Hinton", "Yoshua Bengio", ["Geoffrey Hinton", "Y. Bengio"])


def test_namesakes_stay_separate_and_context_only_ranks_them():
    asked = []
    records = Scholarly(fake(asked)).records("Jane Example", "Example Institute optics", limit=3)
    assert records["searched"] == 4 and records["matched"] == 2  # Janet Exampleton and Bengio are not this name
    first, second = records["candidates"]
    assert first["record"] == "https://openalex.org/A1" and first["fits_context"] and not second["fits_context"]
    assert first["top_works"][0] == {"title": "Light in fibres", "year": 2021, "doi": "https://doi.org/10.1/abc", "cited_by": 120}
    assert "country" not in str(first)  # professional output only
    assert any("filter=author.id:A1" in url for url in asked)
    without_context = Scholarly(fake([])).records("Jane Example", "", limit=3)["candidates"]
    assert without_context[0]["record"] == "https://openalex.org/A2"  # no context: most works first


def test_the_answer_lists_each_record_apart_and_says_they_are_not_one_person():
    records = Scholarly(fake([])).records("Jane Example", "Example Institute", limit=2)
    text = "\n".join(scholarly_lines(records))
    assert "[S1] Jane Q. Example · Example Institute of Optics" in text and "[S2] Jane Example · Another University" in text
    assert "separate records, not one confirmed person" in text and "ORCID: https://orcid.org/0000-0001-0000-0001" in text


def test_a_works_lookup_failing_leaves_the_author_record():
    def get_json(url):
        if "/works?" in url:
            raise TimeoutError("slow")
        return AUTHORS
    records = Scholarly(get_json).records("Jane Example", limit=1)
    assert records["candidates"][0]["top_works"][0]["title"] == "(works could not be read: TimeoutError)"


@pytest.mark.parametrize("level, expected", [("quick", 1), ("normal", 2), ("deep", 3)])
def test_person_research_adds_scholarly_records_by_depth(level, expected, monkeypatch):
    from app.research.manager import ResearchManager

    manager = ResearchManager.__new__(ResearchManager)
    asked = []
    many = {"results": [{"id": f"https://openalex.org/A{index}", "display_name": "Jane Example", "works_count": index}
                        for index in range(5)]}
    manager.fetcher = SimpleNamespace(get_json=lambda url: asked.append(url) or (many if "/authors?" in url else WORKS))
    monkeypatch.setattr(ResearchManager, "normalize_mode", lambda self, value: {"quick": "QUICK", "normal": "NORMAL",
                                                                               "deep": "DEEP"}[value])
    monkeypatch.setattr(ResearchManager, "research", lambda self, query, **options: SimpleNamespace(
        answer="Public professional sources for Jane Example", errors=[], trace={}))
    result = manager.research_person("Jane Example", level=level)
    assert len(result.trace["scholarly"]["candidates"]) == expected
    assert "Scholarly records (OpenAlex)" in result.answer


def test_an_openalex_outage_is_reported_and_the_web_answer_stands(monkeypatch):
    from app.research.manager import ResearchManager

    manager = ResearchManager.__new__(ResearchManager)

    def down(url):
        raise ConnectionError("unreachable")
    manager.fetcher = SimpleNamespace(get_json=down)
    monkeypatch.setattr(ResearchManager, "normalize_mode", lambda self, value: "DEEP")
    monkeypatch.setattr(ResearchManager, "research", lambda self, query, **options: SimpleNamespace(
        answer="Public professional sources for Jane Example", errors=[], trace={}))
    result = manager.research_person("Jane Example")
    assert result.answer == "Public professional sources for Jane Example"
    assert result.errors == ["OpenAlex: ConnectionError: unreachable"]
