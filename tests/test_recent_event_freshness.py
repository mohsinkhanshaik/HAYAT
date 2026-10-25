"""Recent-event questions ("last night", "yesterday") are answered only from evidence dated in that window.

Live 2026-10-24: "Who won the match last night?" was classed as stable knowledge,
answered with "the Kansas City Chiefs ... on October 27, 2025" and its search
results were cached for 14 days.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.research.freshness import classify_freshness, mentioned_dates, relative_window, source_date
from app.research.manager import ResearchManager
from app.research.models import Claim, ClaimStatus, Evidence, SourceDocument

TODAY = date(2026, 10, 24)


def _source(url, published=None):
    return SourceDocument(url=url, final_url=url, title="Report", domain="sport.example", text="x",
                          retrieved_at="2026-10-24T12:00:00+00:00", status_code=200, content_type="text/html",
                          content_hash=url, bytes_downloaded=1, published_at=published)


def _claim(text, url):
    evidence = Evidence(evidence_id="e" + str(abs(hash(text))), claim_id="c" + str(abs(hash(text))), source_url=url,
                        source_title="Report", excerpt=text, quality=.7, retrieved_at="2026-10-24")
    return Claim(claim_id="c" + str(abs(hash(text))), text=text, status=ClaimStatus.SUPPORTED, confidence=.7,
                 evidence=[evidence])


def test_relative_event_questions_are_live_and_windowed():
    assert classify_freshness("Who won the match last night?")[0] == "live"
    assert classify_freshness("what happened yesterday in the election")[0] == "live"
    assert classify_freshness("What is the capital city of Australia")[0] == "stable"
    phrase, start, end = relative_window("Who won the match last night?", TODAY)
    assert phrase == "last night" and start <= TODAY - timedelta(days=1) <= end and end >= TODAY
    assert relative_window("What is the capital city of Australia", TODAY) is None


def test_dates_are_read_in_written_and_iso_forms():
    assert mentioned_dates("defeated the Bills 30-21 on October 27, 2025.") == [date(2025, 10, 27)]
    assert mentioned_dates("Published 23rd Oct 2026") == [date(2026, 10, 23)]
    assert mentioned_dates("updated 2026-10-23T22:10Z") == [date(2026, 10, 23)]
    assert mentioned_dates("in March 2025 the council met") == []
    assert source_date("2026-10-23T22:10:00Z") == date(2026, 10, 23)
    assert source_date("") is None


def test_year_old_result_is_not_presented_as_last_nights():
    url = "https://sport.example/chiefs-bills"
    claims = [_claim("The winner of last night's football game was the Kansas City Chiefs, who defeated the "
                     "Buffalo Bills 30-21 on October 27, 2025.", url),
              _claim("The Kansas City Chiefs won last night's game.", url)]
    kept, reply, caveat = ResearchManager._recent_event_evidence(
        "Who won the match last night?", [_source(url)], claims, today=TODAY)
    assert kept == [] and caveat is None
    assert "couldn't find a result from last night" in reply and "27 October 2025" in reply


def test_stale_page_date_rejects_undated_claims_but_fresh_page_is_kept():
    old, new = "https://sport.example/old", "https://sport.example/new"
    claims = [_claim("United beat City 2-1.", old), _claim("Rovers beat Athletic 3-0.", new)]
    sources = [_source(old, "2025-10-28T09:00:00Z"), _source(new, "2026-10-24T07:30:00Z")]
    kept, reply, caveat = ResearchManager._recent_event_evidence("who won the match last night", sources, claims,
                                                                 today=TODAY)
    assert [claim.text for claim in kept] == ["Rovers beat Athletic 3-0."]
    assert reply is None and caveat is None


def test_undated_evidence_is_kept_with_an_honest_caveat():
    url = "https://sport.example/live"
    kept, reply, caveat = ResearchManager._recent_event_evidence(
        "who won the match last night", [_source(url)], [_claim("Rovers beat Athletic 3-0.", url)], today=TODAY)
    assert len(kept) == 1 and reply is None
    assert "can't confirm this is from last night" in caveat


def test_ordinary_questions_are_untouched():
    url = "https://history.example/canberra"
    claims = [_claim("Canberra was selected as the capital on 1 January 1913.", url)]
    kept, reply, caveat = ResearchManager._recent_event_evidence(
        "What is the capital city of Australia", [_source(url, "2019-01-01")], claims, today=TODAY)
    assert kept == claims and reply is None and caveat is None


ASK_WHICH = ["Who won the match last night?", "who won the game yesterday", "Who won the fight last night",
             "which team won the final?", "ok who won the big game"]
NAMED = ["Who won the Lakers game last night?", "who won the lakers game last night", "who won the IPL final",
         "who won the football game last night", "who won the election", "who won the 2022 world cup",
         "who won Arsenal vs Chelsea last night", "Who won the Nobel Prize in physics this year?"]


@pytest.mark.parametrize("text", ASK_WHICH)
def test_a_result_asked_for_without_naming_the_event_asks_which(text):
    from app.intent.router import IntentRouter

    intent = IntentRouter().route(text)
    assert intent.needs_clarification and "Which match do you mean" in intent.response


@pytest.mark.parametrize("text", NAMED)
def test_any_naming_word_makes_it_a_search_again(text):
    from app.intent.models import IntentKind
    from app.intent.router import IntentRouter

    intent = IntentRouter().route(text)
    assert not intent.needs_clarification and intent.intent == IntentKind.RESEARCH


def test_recent_event_search_uses_the_real_date():
    from app.research.freshness import dated_query

    assert dated_query("who won the lakers game last night", TODAY) == "who won the lakers game October 23, 2026"
    assert dated_query("what happened in the election yesterday", TODAY).endswith("October 23, 2026")
    assert dated_query("arsenal news this week", TODAY) == "arsenal news October 2026"
    assert dated_query("capital city of Australia", TODAY) is None


def test_planner_searches_the_dated_form_first_and_drops_a_leading_article():
    from app.research.models import ResearchBudget
    from app.research.query_planner import ResearchQueryPlanner

    planner = ResearchQueryPlanner()
    plan = [item.text for item in planner.plan("who won the lakers game last night", "NORMAL", ResearchBudget())]
    assert plan[0].startswith("who won the lakers game ") and plan[0] != "who won the lakers game last night"
    assert "who won the lakers game last night" in plan
    # Bing read "the capital city of Australia" as Capital University and Capital One (2026-10-24).
    assert planner.plan("what is the capital city of Australia", "NORMAL", ResearchBudget())[0].text == \
        "capital city of Australia"
    assert planner.plan("What is The Hague known for", "NORMAL", ResearchBudget())[0].text.startswith("The Hague")


def test_an_unsupported_premise_is_named_before_the_evidence():
    url = "https://pmc.example/coffee"
    association = _claim("A cohort of 67,703 women showed no relationship between coffee intake and breast cancer risk.", url)
    note = ResearchManager._unsupported_premise(
        "Give me the URL of the official study that proves coffee cures cancer.", [association])
    assert note and "proves coffee cures cancer" in note
    cure = _claim("Trials found that drug X cures hepatitis C in most patients.", url)
    assert ResearchManager._unsupported_premise("what proves drug X cures hepatitis C", [cure]) is None
    assert ResearchManager._unsupported_premise("what is the capital city of Australia", [association]) is None
