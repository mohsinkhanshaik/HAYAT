"""Searches run in the background, report back, never get invented, and say how far to trust them."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.intent.router import IntentRouter
from app.research.catalogs import books_section, find_books, looks_like_a_book
from app.research.jobs import ResearchJobs, compose_answer
from app.research.models import ClaimStatus
from app.research.subject import is_vague, resolve_query
from app.research.verdict import verdict
from app.session import SessionState


class FakeResearch:
    def __init__(self, seconds=0.0, answer="The answer, with sources [1].", fail=False):
        self.seconds, self.answer, self.fail = seconds, answer, fail
        self.queries = []
        self.release = threading.Event()

    def close(self):
        pass

    def research(self, query, level="normal", remember=True):
        self.queries.append((query, level))
        if self.seconds:
            self.release.wait(self.seconds)
        if self.fail:
            raise RuntimeError("all providers failed")
        return SimpleNamespace(answer=self.answer, confidence=0.8, sources=[1, 2, 3], claims=[], errors=[])


def jobs_with(research, **kwargs):
    delivered = []
    jobs = ResearchJobs(lambda: research, deliver=lambda job, text: delivered.append((job.job_id, text)), **kwargs)
    return jobs, delivered


def wait_for(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ------------------------------------------------------------------ jobs
def test_a_quick_search_answers_in_the_turn_and_is_not_repeated():
    jobs, delivered = jobs_with(FakeResearch(), inline_seconds=2)
    job = jobs.start("capital of france")
    assert jobs.wait_inline(job) is True and "The answer" in job.answer
    time.sleep(0.1)
    assert delivered == []


def test_releasing_an_already_finished_gui_search_delivers_exactly_once():
    jobs, delivered = jobs_with(FakeResearch(), inline_seconds=20)
    job = jobs.start("a topic")
    assert job._done.wait(3)
    assert not delivered
    jobs.release(job)
    jobs.release(job)
    assert len(delivered) == 1 and "The answer" in delivered[0][1]


@pytest.mark.parametrize("text, expected", [
    ("research solar panels in depth", "deep"),
    ("search all about the James Webb telescope", "deep"),
    ("look deep into battery recycling", "deep"),
    ("deep research on heat pumps", "deep"),
    ("a quick overview of solar panels", "quick"),
    ("search for deep learning papers", "normal"),
    ("search for Deep Purple albums", "normal"),
])
def test_depth_requests_select_the_research_budget(text, expected):
    from app.research.subject import requested_level
    assert requested_level(text) == expected


def test_a_slow_search_reports_back_once_when_it_finishes():
    research = FakeResearch(seconds=5)
    jobs, delivered = jobs_with(research, inline_seconds=0.2)
    job = jobs.start("shams al marif")
    assert jobs.wait_inline(job) is False and job.status == "running"
    assert "Still searching" in jobs.status_text()
    research.release.set()
    assert wait_for(lambda: delivered)
    time.sleep(0.1)
    assert len(delivered) == 1 and "I finished looking up \"shams al marif\"" in delivered[0][1]


def test_a_replaced_turn_does_not_lose_the_answer():
    jobs, delivered = jobs_with(FakeResearch(), inline_seconds=2)
    job = jobs.start("anything")
    assert jobs.wait_inline(job, still_wanted=lambda: False) is False
    assert wait_for(lambda: delivered) and len(delivered) == 1


def test_a_failed_search_says_so_plainly():
    jobs, delivered = jobs_with(FakeResearch(fail=True), inline_seconds=0.1)
    job = jobs.start("nothing findable")
    jobs.wait_inline(job)
    assert wait_for(lambda: delivered) and "couldn't find a reliable answer" in delivered[0][1]


def test_cancel_stops_a_search_and_nothing_is_delivered():
    research = FakeResearch(seconds=5)
    jobs, delivered = jobs_with(research, inline_seconds=0.1)
    job = jobs.start("slow")
    jobs.wait_inline(job)
    assert jobs.cancel() is job and job.status == "cancelled"
    research.release.set()
    time.sleep(0.2)
    assert delivered == [] and jobs.cancel() is None


def test_an_overdue_search_says_it_is_still_going():
    research = FakeResearch(seconds=5)
    jobs, delivered = jobs_with(research, inline_seconds=0.05, timeout_seconds=0.3)
    jobs.wait_inline(jobs.start("very slow"))
    assert wait_for(lambda: delivered) and "still searching" in delivered[0][1]
    research.release.set()
    assert wait_for(lambda: len(delivered) == 2) and "I finished" in delivered[1][1]


def test_status_before_any_search_is_honest():
    jobs, _ = jobs_with(FakeResearch())
    assert "haven't searched for anything" in jobs.status_text()


def test_redeliver_sends_an_answer_whose_turn_was_suppressed():
    jobs, delivered = jobs_with(FakeResearch(), inline_seconds=2)
    job = jobs.start("q")
    assert jobs.wait_inline(job)
    jobs.redeliver(job.job_id)
    assert len(delivered) == 1


# ------------------------------------------------------------------ trust and books
def claim(text, status, domains, against=()):
    evidence = [SimpleNamespace(source_url=f"https://{domain}/page", independence_group="") for domain in domains]
    contradictions = [SimpleNamespace(source_url=f"https://{domain}/x", independence_group="") for domain in against]
    return SimpleNamespace(text=text, status=status, evidence=evidence, contradictions=contradictions)


def test_the_verdict_separates_confirmed_disputed_and_single_source():
    result = SimpleNamespace(confidence=0.7, claims=[
        claim("Written by al-Buni", ClaimStatus.SUPPORTED, ["britannica.com", "jstor.org"]),
        claim("Dated to the 9th century", ClaimStatus.DISPUTED, ["blog.example"], against=["wikipedia.org"]),
        claim("Has 40 chapters", ClaimStatus.SUPPORTED, ["onesite.example"]),
    ])
    text = verdict(result)
    assert "✓ Confirmed by 2 independent sources: Written by al-Buni" in text
    assert "✗ Disputed by wikipedia.org: Dated to the 9th century" in text
    assert "? Only one source says: Has 40 chapters" in text


def test_copies_on_one_site_do_not_count_as_independent():
    result = SimpleNamespace(confidence=0.5, claims=[claim("x", ClaimStatus.SUPPORTED, ["a.com", "www.a.com"])])
    assert "Only one source" in verdict(result)


class Catalogues:
    def get(self, url, **kwargs):
        if "openlibrary" in url:
            docs = [{"title": "Shams al-maʻārif wa-laṭāʼif al-ʻawārif", "author_name": ["Aḥmad ibn ʻAlī Būnī"],
                     "first_publish_year": 1881, "ebook_access": "no_ebook", "key": "/works/OL1W"},
                    {"title": "Cooking with Rice", "key": "/works/OL2W"}]
            return SimpleNamespace(json=lambda: {"docs": docs})
        docs = [{"identifier": "shams-al-marif", "title": "Shams Al Marif", "mediatype": "texts", "creator": "al-Buni"},
                {"identifier": "unrelated", "title": "Garden birds", "mediatype": "texts"}]
        return SimpleNamespace(json=lambda: {"response": {"docs": docs}})


def test_books_are_found_in_public_catalogues_and_unrelated_titles_dropped():
    books = find_books("shams al maarif", http=Catalogues())
    titles = [book["title"] for book in books]
    assert "Shams Al Marif" in titles and "Cooking with Rice" not in titles and "Garden birds" not in titles
    assert "https://archive.org/details/shams-al-marif" in books_section(books)
    assert looks_like_a_book("shams al marif", "a book named shams al marif")


def test_the_answer_carries_the_verdict_and_the_catalogue(monkeypatch):
    import app.research.catalogs as catalogs

    monkeypatch.setattr(catalogs, "find_books", lambda query: [{"catalogue": "Internet Archive", "title": "Shams Al Marif",
                                                                "author": "", "year": "", "access": "digitised text",
                                                                "url": "https://archive.org/details/x"}])
    result = SimpleNamespace(answer="Answer [1].", confidence=0.7,
                             claims=[claim("Written by al-Buni", ClaimStatus.SUPPORTED, ["a.org", "b.org"])])
    text = compose_answer("shams al marif", "find the book shams al marif", result)
    assert text.startswith("Answer [1].") and "How far to trust this" in text and "public book catalogues" in text


# ------------------------------------------------------------------ what to search for
HISTORY = [
    {"role": "user", "content": "i want u to learn the books written in arabic from internet"},
    {"role": "assistant", "content": "I can search for Arabic books."},
    {"role": "user", "content": "u took me wrong i was talking about a book named shams al marif"},
    {"role": "assistant", "content": "Shams al-Ma'arif is a famous grimoire."},
    {"role": "user", "content": "yes"},
]


def test_a_vague_request_is_resolved_to_the_subject_named_earlier():
    asked = ("You can find it some or the other way, just go all the way to the internet and search for references "
             "and complete the whole research and find it.")
    assert is_vague(asked)
    assert resolve_query(asked, HISTORY) == "shams al marif"
    assert resolve_query("find it", HISTORY) == "shams al marif"


def test_a_specific_request_is_left_as_it_is():
    assert resolve_query("search for the history of the ottoman empire", HISTORY) == "the history of the ottoman empire"


def test_the_model_rewrite_is_used_when_it_names_the_subject():
    assert resolve_query("find it", HISTORY, rewrite=lambda prompt: "Shams al-Ma'arif al-Kubra book pdf") \
        == "Shams al-Ma'arif al-Kubra book pdf"
    assert resolve_query("find it", HISTORY, rewrite=lambda prompt: "it") == "shams al marif"   # vague rewrite ignored


# ------------------------------------------------------------------ routing
@pytest.mark.parametrize("text,control", [
    ("Did you find something?", "RESEARCH_STATUS"), ("any results?", "RESEARCH_STATUS"),
    ("is the search done?", "RESEARCH_STATUS"), ("are you still searching?", "RESEARCH_STATUS"),
    ("cancel the search", "RESEARCH_CANCEL"), ("search again", "RESEARCH_AGAIN"),
])
def test_questions_about_the_search_are_answered_from_the_jobs(text, control):
    assert IntentRouter().route(text).control == control


def test_i_want_you_to_learn_arabic_means_hayat_learns_it():
    intent = IntentRouter().route("i want u learn arabic and i wasnt talking about u teach me arabic")
    assert intent.control == "LANGUAGE_INSTALL" and intent.entities["language"] == "ar"


def test_a_left_behind_lesson_no_longer_catches_short_messages():
    router, session = IntentRouter(), SessionState()
    session.pending_learning = {"type": "language", "course": "arabic", "at": time.time(), "paused": True}
    for text in ("yes", "shams al marif", "Hello", "Look for something in English."):
        assert router.route(text, session).control != "LEARNING" and router.route(text, session).intent.value != "LEARNING"
    assert router.route("hint", session).intent.value == "LEARNING"
    session.pending_learning = {"type": "language", "course": "arabic", "at": time.time() - 3600}
    assert router.route("yes", session).intent.value != "LEARNING"          # idle for an hour


def test_yes_accepts_an_offer_to_search():
    router, session = IntentRouter(), SessionState()
    session.pending_offer = {"kind": "research", "asked": "the book"}
    assert router.route("yes", session).control == "OFFER_ACCEPT"


def test_the_model_is_told_not_to_claim_searches_it_did_not_run():
    import inspect

    from app.agent import conversation

    source = inspect.getsource(conversation)
    assert "Never say you are searching" in source and "say plainly that you will look" not in source


# ------------------------------------------------------------------ the whole turn
def test_research_through_the_assistant_acknowledges_then_reports_back(monkeypatch):
    from app.output import get_output
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    heard = []
    listener = lambda source, text: heard.append((source, text)) if source == "assistant_notice" else None
    get_output().add_listener(listener)
    research = FakeResearch(seconds=5, answer="Shams al-Ma'arif is attributed to al-Buni [1].")
    monkeypatch.setattr(runtime, "research", research)
    runtime.research_jobs.inline_seconds = 0.3
    try:
        runtime.session.pending_learning = {"type": "language", "course": "arabic", "at": time.time()}
        runtime.assistant.handle("i was talking about a book named shams al marif")
        assert runtime.session.pending_learning.get("paused"), "talking about something else pauses the lesson"
        ack = runtime.assistant.handle("search for references and complete the whole research and find it").response.display_text
        assert "I'm searching for \"shams al marif\"" in ack and research.queries[0][0] == "shams al marif"
        status = runtime.assistant.handle("Did you find something?").response.display_text
        assert "Still searching" in status
        research.release.set()
        assert wait_for(lambda: heard, 5)
        assert "attributed to al-Buni" in heard[0][1]
        after = runtime.assistant.handle("did you find it?").response.display_text
        assert "attributed to al-Buni" in after

        runtime.authority.set_privacy_mode(True)
        try:
            assert "won't search the internet" in runtime.assistant.handle("search for the weather in dubai").response.display_text
        finally:
            runtime.authority.set_privacy_mode(False)

        runtime.session.pending_offer = {"kind": "research", "asked": "search for tulips"}
        before = len(research.queries)
        runtime.assistant.handle("yes")
        assert len(research.queries) == before + 1 and runtime.session.pending_offer is None
        research.seconds = 0
        runtime.assistant.handle("research solar panels in depth")
        assert research.queries[-1][1] == 'deep'
    finally:
        research.release.set()
        get_output().remove_listener(listener)
        runtime.session.pending_learning = None
        runtime.close()
