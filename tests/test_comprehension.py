"""Understanding a topic, as distinct from collecting definitions about it."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.research.comprehension import (
    LOW_CONFIDENCE, MAX_PARTS, Comprehension, Understanding, decompose,
)


class Claim:
    def __init__(self, text: str, status: str = "supported") -> None:
        self.text = text
        self.status = SimpleNamespace(value=status)


class Model:
    """A model that answers with whatever it was told to answer."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def generate(self, messages, **kwargs):
        self.prompts.append(messages[-1].content)
        return SimpleNamespace(content=json.dumps(self.payload))


# ------------------------------------------------------------- decomposing


def test_a_compound_question_is_several_questions():
    """"explain me in detail about taxation and tax evasion and tax return"
    is three questions, and answering it as one is how two of them get
    dropped without anyone noticing. This exact question came back with a
    single bullet about a 5% late-filing penalty."""
    parts = decompose("explain me in detail about taxation and tax evasion and tax return")
    assert parts == ["taxation", "tax evasion", "tax return"]


def test_a_simple_question_is_left_alone():
    """Splitting too eagerly turns one question into three shallow ones,
    which is the failure this exists to fix."""
    assert decompose("what is a b-tree") == ["what is a b-tree"]
    assert decompose("") == []


def test_the_request_wrapper_is_not_mistaken_for_a_subject():
    assert "explain" not in " ".join(decompose("explain taxation and tax returns")).lower()
    assert decompose("compare sqlite and postgres") == ["sqlite", "postgres"]


def test_a_list_is_not_pursued_forever():
    parts = decompose("explain a and b and c and d and e and f and g")
    assert len(parts) <= MAX_PARTS


# ------------------------------------------------------------ understanding


def test_it_asks_for_mechanism_rather_than_more_definitions():
    model = Model({"mechanism": "Tax is withheld at source and reconciled annually.",
                   "relations": "A return is how evasion is detected.",
                   "open_questions": []})
    understanding = Comprehension(model).build(
        "explain taxation and tax returns",
        [Claim("Employers withhold tax from wages."), Claim("Returns are filed annually.")],
        [], 0.8)

    assert understanding.mechanism
    assert understanding.relations
    assert not understanding.shallow
    # The model is given the claims and asked for the connective tissue.
    assert "verified_claims" in model.prompts[0]


def test_a_gap_the_model_will_not_admit_is_found_anyway():
    """"Did you cover everything?" is a question models answer optimistically.

    Checked mechanically instead: a sub-question no surviving claim even
    mentions is unanswered, whatever the explanation says.
    """
    model = Model({"mechanism": "Something confident.", "relations": "",
                   "open_questions": []})
    understanding = Comprehension(model).build(
        "explain taxation and tax evasion and tax returns",
        [Claim("A late return incurs 5% per month up to 25%.")],
        [], 0.53)

    assert "tax evasion" in understanding.open_questions


def test_one_claim_is_not_an_understanding():
    """The state this was built to stop: a single penalty rate filed as
    having learned taxation."""
    understanding = Comprehension(None).build(
        "explain taxation and tax evasion and tax returns",
        [Claim("A late return incurs 5% per month.")], [], 0.53)

    assert understanding.shallow
    assert understanding.open_questions


def test_disputed_claims_are_surfaced_as_disagreement():
    understanding = Comprehension(None).build(
        "what is the rate", [Claim("The rate is 5%.", status="disputed")], [], 0.5)
    assert understanding.contradictions == ["The rate is 5%."]


def test_no_claims_means_every_part_is_open():
    understanding = Comprehension(None).build("explain a thing and another thing", [], [], 0.0)
    assert understanding.open_questions
    assert understanding.shallow


def test_a_model_that_fails_does_not_lose_the_gaps():
    class Broken:
        def generate(self, *args, **kwargs):
            raise RuntimeError("no model today")

    understanding = Comprehension(Broken()).build(
        "explain taxation and tax evasion",
        [Claim("A late return incurs 5% per month.")], [], 0.5)
    assert "tax evasion" in understanding.open_questions


# ---------------------------------------------------------------- honesty


def test_low_confidence_is_stated_first_not_footnoted():
    """A caveat after the answer is decoration. One before it changes how the
    answer is read, which is the entire point of having it."""
    understanding = Understanding(question="q", mechanism="It works like this.",
                                  confidence=0.53, claim_count=1)
    rendered = understanding.render()

    assert rendered.startswith("Confidence is low")
    assert rendered.index("Confidence is low") < rendered.index("How it works")
    assert "0.53" in rendered


def test_a_confident_answer_does_not_apologise():
    understanding = Understanding(question="q", mechanism="It works like this.",
                                  confidence=0.9, claim_count=6)
    assert "Confidence is low" not in understanding.render()
    assert understanding.render().startswith("How it works")


def test_the_unanswered_parts_are_in_the_output():
    understanding = Understanding(
        question="q", mechanism="Some of it.", confidence=0.9, claim_count=4,
        open_questions=["tax evasion"], contradictions=["the rate"])
    rendered = understanding.render()
    assert "Still unanswered:" in rendered and "tax evasion" in rendered
    assert "Sources disagree on:" in rendered and "the rate" in rendered


def test_the_threshold_is_where_a_number_stops_meaning_anything():
    assert 0.5 <= LOW_CONFIDENCE <= 0.7


def test_it_round_trips_as_data():
    understanding = Understanding(question="q", mechanism="m", confidence=0.7, claim_count=3)
    payload = understanding.to_dict()
    assert payload["question"] == "q" and payload["shallow"] is False


# ------------------------------------- what is allowed to be called a claim


@pytest.mark.parametrize("text", [
    # Every one of these was returned as a "validated claim" by a real run.
    "Is the willful failure to file a return enough to constitute the felony?",
    "What Is Tax Evasion and Its Impact on U.S.",
    "Tax Evasion: The Ultimate Guide to Understanding IRS Rules",
    "Learn more about our tax preparation services today",
    "Example 2: An individual claims false deductions (hypothetical example).",
])
def test_things_that_cannot_be_true_are_not_claims(text):
    """A question cannot be true or false, so it cannot be verified, and
    presenting one as validated is a plain falsehood about what was
    established. The same goes for a page title and a call to action."""
    from app.research.claims import is_assertion

    assert not is_assertion(text)


@pytest.mark.parametrize("text", [
    "Engaging in tax evasion can lead to fines up to 100,000 dollars.",
    "Income tax evasion is the intentional underpayment of taxes owed to the IRS.",
    "The primary federal law governing tax evasion is in the Internal Revenue Code.",
    "Rust uses ownership and borrowing to guarantee memory safety without a collector.",
])
def test_real_assertions_still_get_through(text):
    """Filtering out questions must not filter out answers."""
    from app.research.claims import is_assertion

    assert is_assertion(text)


def test_the_extractor_drops_them_before_anything_downstream_sees_them():
    """Every later stage -- verification, citation checking, confidence --
    assumes it is working with an assertion. Feed it a question and the whole
    pipeline dutifully computes a confidence score for something nobody
    stated."""
    from app.research.claims import ClaimExtractor
    from app.research.models import SourceDocument

    excerpt = (
        "Is the willful failure to file a return enough to constitute the felony? "
        "Income tax evasion is the intentional underpayment of taxes owed to the IRS. "
        "Learn more about our tax preparation services today and save money now."
    )
    source = SourceDocument(
        url="https://example.invalid/a", final_url="https://example.invalid/a",
        title="Taxes", text=excerpt, retrieved_at="2026-08-20T00:00:00+00:00",
        status_code=200, content_type="text/html", content_hash="abc", bytes_downloaded=1,
        domain="example.invalid",
    )
    claims = ClaimExtractor().extract("tax evasion", source, excerpt, max_claims=5)

    assert claims, "the real assertion was dropped too"
    for claim in claims:
        assert "?" not in claim.text
        assert not claim.text.lower().startswith("learn more")


# ------------------------------------------------ learned once, not learned twice


#: Content that genuinely addresses the taxation question these tests reuse.
#: Reuse now has to clear the same completeness bar a fresh answer does, so a
#: placeholder like "A long answer." is correctly refused -- it covers none of
#: the topics asked about.
_COVERING_ANSWER = "\n".join([
    "Answer:",
    "- Taxation is how a government raises revenue from income and trade.",
    "- Tax evasion is illegally avoiding tax that is owed, and is a crime.",
    "- A tax return is the annual filing that reconciles what was owed and paid.",
])


def _store_answer(tmp_path, query: str, content: str = _COVERING_ANSWER):
    """A knowledge store holding one previously answered question."""
    from app.research.knowledge import KnowledgeStore

    store = KnowledgeStore(tmp_path / "k.db")
    store.initialize()
    when = "2026-08-19T00:00:00+00:00"
    with store.connect() as db:
        # knowledge.run_id is a foreign key, so the run has to exist first.
        db.execute(
            "INSERT INTO research_runs(id, query, started_at, completed_at, level, "
            "complete, stop_reason, confidence, answer, metrics_json, errors_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("run-1", query, when, when, "NORMAL", 1, "done", 0.55, content, "{}", "[]"))
        db.execute(
            "INSERT INTO knowledge(created_at, updated_at, query, content, confidence, "
            "freshness_class, status, run_id, vector_json, content_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (when, when, query, content, 0.55, "stable", "active", "run-1", "[]", "h"))
    return store


def test_the_same_question_asked_again_is_not_researched_again(tmp_path):
    """The reuse path matched a question against individual claim sentences,
    and a question does not read like the sentences that answer it. Asking the
    exact words already researched scored 0.46 against a 0.68 bar and went
    back to the web -- so nothing ever felt learned."""
    asked = "explain me in detail about taxation and tax evasion and tax return"
    store = _store_answer(tmp_path, asked)
    assert store.answered_before(asked)


@pytest.mark.parametrize("reworded", [
    # None of these is the stored wording, and all are the same question. He is
    # not going to retype it exactly.
    "explain me about taxation and tax evasion and tax returns",
    "tell me about taxation and tax returns",
    "taxation, tax evasion and tax return",
])
def test_a_reworded_question_still_counts_as_the_same_one(tmp_path, reworded):
    store = _store_answer(
        tmp_path, "explain me in detail about taxation and tax evide and tax return")
    assert store.answered_before(reworded), reworded


def test_a_different_question_is_still_researched(tmp_path):
    """Reusing a wrong answer is worse than searching again."""
    store = _store_answer(tmp_path, "explain taxation and tax evasion")
    assert store.answered_before("how do submarines work") is None
    assert store.answered_before("what is the capital of Peru") is None


def test_hyphens_are_not_the_difference_between_knowing_and_searching(tmp_path):
    store = _store_answer(tmp_path, "prompt-injection defenses for tool-using local agents")
    assert store.answered_before("prompt injection defenses for tool using local agents")


def test_a_one_word_overlap_is_not_a_match(tmp_path):
    """Two shared subject words minimum, so "tax" alone cannot pull back an
    answer about something else entirely."""
    store = _store_answer(tmp_path, "explain taxation and tax evasion in detail")
    assert store.answered_before("what is a tax") is None


# ------------------------------------- reuse has to clear the same bar as new


def test_an_answer_that_todays_extractor_would_reject_is_not_reused(tmp_path):
    """Reuse only helps if what was learned was worth learning. The store
    still held an answer whose top line was "Is the willful failure to file a
    return, by itself, enough to constitute the felony of tax evasion?" -- a
    question, filed as a validated claim. Serving that in three milliseconds
    is not learning, it is being wrong faster."""
    from app.research.knowledge import answer_still_acceptable

    rubbish = ("Answer:\n"
               "- Is the willful failure to file a return enough to be a felony?\n"
               "- What Is Tax Evasion and Its Impact on U.S.\n"
               "- Learn more about our tax services today\n")
    assert not answer_still_acceptable(rubbish)


def test_a_real_answer_is_still_reusable():
    from app.research.knowledge import answer_still_acceptable

    good = ("Answer:\n"
            "- Income tax evasion is the intentional underpayment of taxes owed to the IRS.\n"
            "- Engaging in tax evasion can lead to fines of up to $100,000.\n")
    assert answer_still_acceptable(good)


def test_prose_without_bullets_is_left_alone():
    from app.research.knowledge import answer_still_acceptable

    assert answer_still_acceptable("Taxation is how governments raise revenue.")
    assert not answer_still_acceptable("")


def test_a_stored_answer_that_covers_one_of_three_topics_is_not_reused():
    """It had a 604-character reply consisting of one bullet about a
    late-filing penalty, for a question asking about three things."""
    from app.research.knowledge import answer_covers

    question = "explain taxation and tax evasion and tax returns"
    thin = "Answer:\n- A late return incurs 5% per month up to 25% of the unpaid tax.\n"
    assert not answer_covers(question, thin)

    whole = ("Answer:\n- Taxation is how governments raise revenue.\n"
             "- Tax evasion is illegally avoiding it.\n"
             "- A tax return is the annual filing that reconciles both.\n")
    assert answer_covers(question, whole)


def test_a_single_topic_question_needs_no_coverage_check():
    from app.research.knowledge import answer_covers

    assert answer_covers("what is a b-tree", "A b-tree is a balanced tree structure.")
