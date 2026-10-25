"""The background learner studies questions about the world, not chat, and keeps only evidence about them.

On 2026-10-04 its journal held "great what have learn about today" and "ok good now look for ways to make
money" researched word for word, and "failure modes ... for money required elite", which stored claims about
a video game.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.learning.continuous import ContinuousLearner
from app.research.subject import relevant_to_question


def test_relation_question_keeps_its_subject_instead_of_a_shared_category_word():
    assert not relevant_to_question("What is the capital city of Australia?", "Capital City Quiz is a web game built with Node.js.")
    assert not relevant_to_question("What is the currency of Japan?", "A currency converter supports many currencies.")
    assert relevant_to_question("What is the capital city of Australia?", "Canberra is Australia's capital city.")
    assert relevant_to_question("What is the currency of Japan?", "Japan uses the yen.")
    assert not relevant_to_question("What is the capital city of Australia?", "Australia Club honorary member invitation (PDF)")


def test_capital_and_conversion_are_not_software_queries():
    from app.research.ranking import profile_question, technical_question
    from app.research.query_planner import ResearchQueryPlanner
    from app.research.models import ResearchBudget
    for query in ("What is the capital city of Australia?", "Currency conversion from yen to pounds", "Rapid population growth"):
        assert not technical_question(query)
        assert profile_question(query).kind != "software"
        planned = ResearchQueryPlanner().plan(query, "NORMAL", ResearchBudget())
        assert not any("GitHub issues" in item.text or "official documentation" in item.text for item in planned)
    assert technical_question("Python APIs and package versions")
    from app.research.freshness import classify_freshness
    assert classify_freshness("capital city of Australia")[0] == "stable"
    assert classify_freshness("unit conversion from miles to kilometres")[0] == "stable"


def test_a_referential_fact_keeps_its_actual_adjacent_source_context():
    from app.research.claims import ClaimExtractor
    from app.research.models import SourceDocument
    from datetime import UTC, datetime
    text = ("There are eight capital cities in Australia, serving its states and territories. "
            "One of these, Canberra, is also the national capital.")
    source = SourceDocument("https://example.org/geography", "https://example.org/geography", "Geography", "example.org",
                            text, datetime.now(UTC).isoformat(), 200, "text/html", "fixture", len(text), quality=.9)
    claims = ClaimExtractor().extract("capital city of Australia", source, text)
    kept = [claim for claim in claims if "Canberra" in claim.text]
    assert kept and "Australia" in kept[0].text
    assert kept[0].evidence[0].excerpt in source.text
    from app.research.manager import ResearchManager
    excerpt = ResearchManager._relevant_excerpt(text, "what is the capital city of Australia")
    assert "Australia" in excerpt and "Canberra" in excerpt
    assert not ClaimExtractor().extract("capital city of Australia", source, "Categories: Australian capital citiesDemographics of Australia")


def test_direct_definitions_rank_before_incidental_subject_mentions():
    from app.research.subject import direct_answer_score
    assert direct_answer_score("What is the capital city of Australia?", "Canberra is also the national capital.") == 1
    assert direct_answer_score("What is the capital city of Australia?", "Australia's national government relocated in 1927.") == 0
    assert direct_answer_score("Who is the CEO of Example?", "Jordan is the CEO of Example.") == 1
    assert direct_answer_score("How did Example grow?", "Jordan is the CEO of Example.") == 0


def test_stated_subject_answer_outranks_a_reported_misconception():
    # Live 2026-10-24: the misconception led the answer and Canberra came third.
    from app.research.subject import direct_answer_score
    question = "what is the capital city of Australia"
    myth = ("Many travellers guess that Sydney is Australia's capital because it is the largest city "
            "and one of the main international arrival points.")
    assert direct_answer_score(question, myth) == -1
    assert direct_answer_score(question, "The capital city of Australia is Canberra, not Sydney or Melbourne.") == 1
    assert direct_answer_score(question, "The capital city of Australia is Canberra, not Sydney as many "
                                         "people mistakenly believe.") == 1
    assert direct_answer_score(question, "The population of Australia's capital cities grew by 1.8 per cent.") == 0
    assert direct_answer_score("Who is the CEO of Example?", "The CEO of Example is Jordan.") == 1
    assert direct_answer_score("What is Python?", "Python is a programming language.") == 1


def test_synthesis_leads_with_the_answer_and_drops_the_misconception():
    from app.research.manager import ResearchManager
    from app.research.models import Claim, ClaimStatus, Evidence, SourceDocument

    url = "https://travel.example/capital"
    source = SourceDocument(url=url, final_url=url, title="Capital", domain="travel.example", text="x",
                            retrieved_at="2026-10-24", status_code=200, content_type="text/html",
                            content_hash="h", bytes_downloaded=1)
    def claim(text):
        evidence = Evidence(evidence_id="e-" + text[:8], claim_id="c-" + text[:8], source_url=url,
                            source_title="Capital", excerpt=text, quality=.7, retrieved_at="2026-10-24")
        return Claim(claim_id="c-" + text[:8], text=text, status=ClaimStatus.SUPPORTED, confidence=.7,
                     evidence=[evidence])
    myth = claim("Many travellers guess that Sydney is Australia's capital because it is the largest city.")
    answer = claim("The capital city of Australia is Canberra, not Sydney or Melbourne.")
    manager = ResearchManager.__new__(ResearchManager)
    manager.model, manager.settings = None, {}
    manager.citations = type("C", (), {"citations_for_claim": staticmethod(lambda _c, _s: [1])})()
    manager.independence = type("I", (), {"independent_count": staticmethod(lambda _s: 1)})()
    manager.comprehend = lambda *_a, **_k: None
    text = manager._synthesize("what is the capital city of Australia", [source], [myth, answer], .7, None)
    bullets = [line for line in text.splitlines() if line.startswith("- ")]
    assert bullets[0].startswith("- The capital city of Australia is Canberra")
    assert not any("guess" in line for line in bullets)


class Brain:
    def __init__(self, reply: str = "", fail: bool = False) -> None:
        self.reply, self.fail, self.calls = reply, fail, []

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        self.calls.append(messages[-1]["content"])
        if self.fail:
            raise ConnectionError("model not loaded")
        return SimpleNamespace(content=self.reply)


def _learner(tmp_path, brain: Brain | None = None) -> ContinuousLearner:
    learner = ContinuousLearner(root=tmp_path, anticipate_followups=True, anticipated_per_request=2)
    learner.runtime = SimpleNamespace(model=brain) if brain is not None else None
    return learner


def test_chat_queued_from_a_request_is_dropped_before_any_research(tmp_path):
    brain = Brain("NONE")
    learner = _learner(tmp_path, brain)
    learner.enqueue("great what have learn about today", source="user_conversation")
    assert learner._research_question("great what have learn about today") is None
    assert brain.calls == ["great what have learn about today"]


def test_a_rambling_request_is_researched_as_the_clean_question_behind_it(tmp_path):
    learner = _learner(tmp_path, Brain("Question: What are realistic ways to earn extra income online?"))
    learner.enqueue("ok good now look for ways to make money", source="user_question")
    assert learner._research_question("ok good now look for ways to make money") == \
        "What are realistic ways to earn extra income online?"


def test_clean_topics_and_an_unreachable_model_never_stop_learning(tmp_path):
    seeded = _learner(tmp_path / "seed", Brain("NONE"))
    seeded.enqueue("SQLite WAL durability backup and corruption recovery guidance", source="seed")
    assert seeded._research_question("SQLite WAL durability backup and corruption recovery guidance") == \
        "SQLite WAL durability backup and corruption recovery guidance"
    offline = _learner(tmp_path / "offline", Brain(fail=True))
    offline.enqueue("How do vaccines train the immune system?", source="user_question")
    assert offline._research_question("How do vaccines train the immune system?") == \
        "How do vaccines train the immune system?"


def test_only_a_well_formed_question_gets_predicted_follow_ups(tmp_path):
    learner = _learner(tmp_path)
    learner.observe_request("great and all about money and stuff that are required and all elite things to learn",
                            "QUESTION")
    learner.observe_request("Why does NCCL performance depend on GPU topology?", "QUESTION")
    predicted = [row["topic"] for row in learner.queue_status(20)["topics"] if row["source"] == "anticipated_follow_up"]
    assert predicted and all("NCCL" in topic for topic in predicted)


def test_a_claim_that_is_not_about_the_question_is_not_evidence_for_it():
    question = "What are the common failure modes and diagnostic signals for money required elite?"
    assert not relevant_to_question(question, "The best ships for mining are the Python or Type-9.")
    assert relevant_to_question("How does compound interest work?",
                                "Compound interest is earned on both the principal and past interest.")
    # Arabic questions answered from English sources are not judged by English words.
    assert relevant_to_question("ما هو الربا", "Riba is an Arabic term for usury.")


def test_sources_list_only_cited_pages_and_keep_their_numbers():
    from app.research.citations import markdown_sources
    from app.research.models import SourceDocument

    def page(index, title):
        url = f"https://example.org/{index}"
        return SourceDocument(url=url, final_url=url, title=title, domain="example.org", text="x",
                              retrieved_at="2026-10-24T09:00:00", status_code=200, content_type="text/html",
                              content_hash=str(index), bytes_downloaded=1)
    sources = [page(1, "ABS"), page(2, "Capital University"), page(3, "Capital University visit"), page(4, "NFSA")]
    text = markdown_sources(sources, {1, 4})
    assert "[1] [ABS]" in text and "[4] [NFSA]" in text and "Capital University" not in text
    assert "(2 other pages read but not cited.)" in text
    assert markdown_sources(sources).count("\n") == 3  # unchanged without a cited set
