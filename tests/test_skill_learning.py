from __future__ import annotations

import httpx
import pytest

from app.research.search import BingRssSearch
from app.research.skill_learning import ResearchSkillLearner


class _Knowledge:
    """Every query looks like it has been researched many times."""

    def similar_run_count(self, query: str) -> int:
        return 5


def test_repeated_research_becomes_a_reviewable_candidate(tmp_path):
    learner = ResearchSkillLearner(_Knowledge(), tmp_path)

    path = learner.consider("python 3.14 release notes 2026-10-09", "DEEP")

    assert path is not None and path.exists()
    assert "trusted: false" in path.read_text(encoding="utf-8")


def test_lookups_naming_a_person_never_become_skill_files(tmp_path):
    learner = ResearchSkillLearner(_Knowledge(), tmp_path)

    assert learner.consider("find current location of someone@example.com", "DEEP") is None
    assert learner.consider("who owns the number +1 (555) 010-4477", "DEEP") is None
    assert list((tmp_path / "candidates").glob("*.yaml")) == []


def test_bing_rss_refuses_entity_declarations(monkeypatch):
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    request = httpx.Request("GET", "https://www.bing.com/search")
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, content=bomb, request=request))

    with pytest.raises(ValueError, match="entity"):
        BingRssSearch().search("anything")
