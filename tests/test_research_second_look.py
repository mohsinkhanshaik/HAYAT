"""A second look: read, notice what is still unknown, search for exactly that (app/research/deepening.py)."""
from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from app.research.deepening import coverage, facets, plan_second_look
from app.research.fetcher import FetchedResource
from app.research.knowledge import KnowledgeStore
from app.research.manager import ResearchManager
from app.research.models import SearchResult

PAGES = {
    "https://docs.example.com/widget": ("Widget 2 settings", "Widget 2 safe mode is enabled by default in every install."),
    "https://help.example.net/uploads": ("Widget 2 uploads", "Widget 2 accepts uploads up to a maximum size of 25 MB per file."),
    "https://guide.example.org/widget": ("Widget 2 guide", "In Widget 2 the safe mode default is on, and it can be turned off."),
}


def page(url):
    title, text = PAGES[url]
    body = (f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1><p>{text}</p>"
            "</main></body></html>").encode()
    return FetchedResource(url, url, 200, {"content-type": "text/html; charset=utf-8"}, body, len(body),
                           datetime.now(UTC).isoformat(), hashlib.sha256(body).hexdigest())


class Fetcher:
    def __init__(self):
        self.urls = []

    def fetch(self, url, max_bytes=4 * 1024 * 1024, accept=None):
        self.urls.append(url)
        return page(url)

    def close(self):
        pass


class Web:
    """A tiny search engine: each rule maps words a query must contain (and must not) to the pages it returns."""

    def __init__(self, rules):
        self.rules, self.queries = rules, []

    def search(self, query, limit=10, recency=None, domains=None):
        self.queries.append(query)
        lower = query.lower()
        results = []
        for needs, avoids, url in self.rules:
            if all(word in lower for word in needs) and not any(word in lower for word in avoids):
                results.append(SearchResult(PAGES[url][0], url, PAGES[url][1][:60], url.split("/")[2],
                                            rank=len(results) + 1, provider="fake", quality=0.8))
        return results[:limit]


def manager(tmp_path, web, **extra):
    settings = {
        "database": str(tmp_path / "knowledge.db"), "user_agent": "test", "respect_robots_txt": False,
        "max_concurrency": 2, "model_synthesis": False,
        "budgets": {"fast": {"searches": 1, "pages": 2, "max_seconds": 20, "max_bytes": 1_000_000},
                    "normal": {"searches": 2, "pages": 4, "max_seconds": 30, "max_bytes": 2_000_000}},
        **extra,
    }
    research = ResearchManager(settings, model=None, search_provider=web, knowledge=KnowledgeStore(tmp_path / "k.db"))
    research.fetcher.close()
    research.fetcher = Fetcher()
    return research


QUESTION = "Widget 2 safe mode default and maximum upload size"


def test_the_part_of_the_question_no_source_answered_is_searched_for_and_answered(tmp_path):
    # The question's own wording finds only the settings pages; only a search focused on uploads finds the limit.
    web = Web([(("widget",), (), "https://docs.example.com/widget"),
               (("widget",), (), "https://guide.example.org/widget"),
               (("upload", "maximum"), ("safe",), "https://help.example.net/uploads")])
    research = manager(tmp_path, web)
    try:
        before = coverage(QUESTION, [])
        assert "upload" in before[1]
        result = research.research(QUESTION, "normal", remember=False)
    finally:
        research.close()
    look = result.trace["second_look"]
    assert look["ran"] and any("no source answered" in reason for reason in look["reasons"])
    assert {"maximum", "upload", "size"} <= set(look["uncovered"])
    assert "https://help.example.net/uploads" in look["new_sources"]
    assert "25 MB" in result.answer
    assert "upload" not in look["uncovered_after"]
    assert look["supported_after"] > look["supported_before"]
    assert any(source.final_url == "https://help.example.net/uploads" for source in result.sources)
    # The follow-up was a focused query, not a repeat of the question.
    assert any("upload" in query and "safe" not in query for query in web.queries)


def test_an_answered_question_with_enough_sources_gets_no_second_look(tmp_path):
    web = Web([(("widget",), (), "https://docs.example.com/widget"),
               (("widget",), (), "https://guide.example.org/widget")])
    research = manager(tmp_path, web)
    try:
        result = research.research("Widget 2 safe mode default", "normal", remember=False)
    finally:
        research.close()
    assert result.trace["second_look"]["ran"] is False and result.trace["second_look"]["queries"] == []
    assert len(web.queries) == len(result.trace["queries"])  # no extra searches


def test_a_single_voice_is_corroborated_from_another_domain(tmp_path):
    web = Web([(("widget",), ("-site:",), "https://docs.example.com/widget"),
               (("widget", "-site:docs.example.com"), (), "https://docs.example.com/widget"),
               (("widget", "-site:docs.example.com"), (), "https://guide.example.org/widget")])
    research = manager(tmp_path, web)
    try:
        result = research.research("Widget 2 safe mode default", "normal", remember=False)
    finally:
        research.close()
    look = result.trace["second_look"]
    assert look["skip_domains"] == ["docs.example.com"]
    assert any("independent source" in reason for reason in look["reasons"])
    assert look["new_sources"] == ["https://guide.example.org/widget"]  # the same domain was not counted again
    assert result.metrics.independent_sources >= 2


def test_quick_lookups_stay_quick(tmp_path):
    web = Web([(("widget",), ("upload",), "https://docs.example.com/widget")])
    research = manager(tmp_path, web)
    try:
        result = research.research(QUESTION, "fast", remember=False)
    finally:
        research.close()
    assert result.trace["second_look"]["queries"] == [] and not result.trace["second_look"]["ran"]


def test_no_second_look_without_time_for_it(tmp_path):
    web = Web([(("upload",), ("safe",), "https://help.example.net/uploads")])
    research = manager(tmp_path, web)
    claim = SimpleNamespace(status=SimpleNamespace(value="SUPPORTED"), text="Widget 2 safe mode is on by default.")
    budget = research.budget("normal", QUESTION)
    trace = {}
    try:
        documents, claims, _ = research._second_look(
            QUESTION, "NORMAL", budget, time.perf_counter() - budget.max_seconds * 0.8, research_metrics(),
            [], trace, [], [claim], 0.8, False, [QUESTION], [])
    finally:
        research.close()
    assert trace["second_look"]["skipped"].startswith("less than 30%") and web.queries == []
    assert claims == [claim]


def research_metrics():
    from app.research.models import ResearchMetrics

    return ResearchMetrics()


def test_deep_research_learns_terms_the_sources_use():
    supported = SimpleNamespace(value="SUPPORTED")
    claims = [SimpleNamespace(status=supported, text="Aurora light appears when the Solar Wind reaches the Magnetosphere."),
              SimpleNamespace(status=supported, text="The Solar Wind carries charged particles toward the Magnetosphere."),
              SimpleNamespace(status=supported, text="Aurora colours come from oxygen and nitrogen.")]
    plan = plan_second_look("what causes the aurora", "aurora causes", claims, [], min_sources=1,
                            independent_sources=3, used_queries=["aurora causes"], mode="DEEP")
    assert plan.learned_terms == ["Magnetosphere", "Solar Wind"]
    assert any("Solar Wind" in query for query in plan.queries)
    assert any("terms the sources use" in reason for reason in plan.reasons)
    normal = plan_second_look("what causes the aurora", "aurora causes", claims, [], min_sources=1,
                              independent_sources=3, used_queries=["aurora causes"], mode="NORMAL")
    assert normal.queries == []  # learning new terms is deep-mode work


def test_disagreement_and_question_facets():
    assert facets("What is the maximum upload size in Widget 2?") == ["maximum", "upload", "size", "Widget", "2"]
    disputed = [SimpleNamespace(status=SimpleNamespace(value="DISPUTED"), text="Widget 2 maximum upload size is 25 MB.")]
    plan = plan_second_look("Widget 2 maximum upload size", "Widget 2 maximum upload size", disputed, [],
                            min_sources=1, independent_sources=2, used_queries=[], mode="NORMAL")
    assert plan.reasons == ["sources disagree"] and "which account is correct" in plan.queries[0]


def test_what_stays_unknown_becomes_an_open_question_but_not_from_the_learners_own_research(tmp_path):
    from app.execution import execution_scope
    from app.safety.authority import Principal

    # The upload page never turns up, so "maximum upload size" stays unanswered after both looks.
    web = Web([(("widget",), (), "https://docs.example.com/widget"),
               (("widget",), (), "https://guide.example.org/widget")])
    kept = []
    research = manager(tmp_path, web)
    research.open_question = lambda topic: kept.append(topic) or True
    try:
        result = research.research(QUESTION, "normal", remember=False)
        assert result.trace["second_look"]["ran"] and result.trace["second_look"]["new_sources"] == []
        assert kept == [result.trace["second_look"]["open_question"]]
        assert kept[0] == "Widget 2 safe maximum upload size"
        with execution_scope(principal=Principal.BACKGROUND_LEARNER.value):
            research.research("Widget 2 safe mode default and maximum upload limit", "normal", remember=False)
        assert len(kept) == 1  # the learner's own research does not feed its queue
    finally:
        research.close()


def test_open_questions_reach_the_real_learner_queue(tmp_path):
    from app.learning.continuous import ContinuousLearner

    learner = ContinuousLearner(root=tmp_path)
    assert learner.enqueue("Widget 2 safe maximum upload size", source="open_question", priority=55)
    queued = learner.queue_status()
    assert [(item["topic"], item["source"], item["status"]) for item in queued["topics"]] == [
        ("Widget 2 safe maximum upload size", "open_question", "pending")]


def test_a_follow_up_that_finds_an_unrelated_page_leaves_the_answer_as_it_was(tmp_path):
    # Live, 2026-11-08: "boiling point top" (without Everest) found a company's home page. The anchor now keeps
    # the question's names, and a new page no checked claim rests on is never added to the sources.
    PAGES["https://bp.example.com/"] = ("BP Group", "BP Group sells food packaging machines in Washington State.")
    web = Web([(("widget",), (), "https://docs.example.com/widget"),
               (("widget",), (), "https://guide.example.org/widget"),
               (("maximum",), ("safe",), "https://bp.example.com/")])
    research = manager(tmp_path, web)
    try:
        first = research.research(QUESTION, "normal", remember=False)
    finally:
        PAGES.pop("https://bp.example.com/", None)
        research.close()
    look = first.trace["second_look"]
    assert any(query.startswith("Widget 2 ") for query in look["queries"])
    assert look["kept_first_pass"] and look["unused_new_sources"] == ["https://bp.example.com/"]
    assert all("bp.example.com" not in source.final_url for source in first.sources)
    assert "packaging" not in first.answer
