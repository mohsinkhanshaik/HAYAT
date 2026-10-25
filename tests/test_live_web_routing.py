"""A request that needs the live web must reach the live web.

Every case below was answered from the model's training data, or refused with
"if you gave me access to a browser", while Chrome, the search chain and the
research pipeline all worked. The gap was routing, so these tests are about
where a request goes rather than what comes back.
"""

from __future__ import annotations

from app.intent.live_web import clean_query, needs_live_web
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.session import SessionState


def test_asking_to_go_and_look_is_recognised_however_it_is_phrased():
    for text in (
        "search the web for the python 3.14 release date",
        "google the best mechanical keyboards",
        "look up how tls handshakes work",
        "look this up for me",
        "scrape the headlines",
        "browse the internet for rust benchmarks",
        "what are people saying about it online",
    ):
        need = needs_live_web(text)
        assert need is not None, text
        assert need.reason == "explicit_web_verb", text


def test_material_that_only_exists_on_the_web_is_a_request_to_fetch_it():
    for text in (
        "find me tutorials on rust async",
        "get me the docs for httpx",
        "pull up some papers on retrieval augmented generation",
        "show me examples of pydantic validators",
    ):
        need = needs_live_web(text)
        assert need is not None, text
        assert need.reason == "web_material_request", text


def test_fact_check_from_chat_uses_evidence_instead_of_model_recall():
    router = IntentRouter()
    for text in (
        "Fact-check the claim that Python 3.14 removed the GIL",
        "Can you verify whether Python 3.14 removed the GIL?",
        "Is it true that Python 3.14 removed the GIL?",
    ):
        need = needs_live_web(text)
        assert need is not None and need.reason == "fact_check_request", text
        intent = router.route(text, SessionState())
        assert intent.intent == IntentKind.RESEARCH and intent.requires_tools, text
        assert "python 3.14" in intent.entities["query"].lower()


def test_check_my_recent_changes_routes_to_project_tools():
    router = IntentRouter()
    for text in (
        "i made changes again in u check them",
        "i made changes gain in u check them",
        "review my latest changes in this project",
        "check the changes I made to Hayat",
    ):
        intent = router.route(text, SessionState())
        assert intent.intent == IntentKind.CODING and intent.requires_tools, text
        assert intent.entities["review_project_changes"] is True


def test_volatile_subjects_are_never_answered_from_recall():
    for text in (
        "what is the weather in mumbai right now",
        "what is the current price of bitcoin",
        "who won the match yesterday",
        "is github down",
        "what are the top stories",
        "when is the release date for it",
    ):
        need = needs_live_web(text)
        assert need is not None, text
        assert need.reason == "volatile_subject", text


def test_a_present_tense_question_about_the_world_needs_a_look():
    for text in (
        "what is the latest version of python",
        "what's new in postgres this week",
        "which database is most popular nowadays",
    ):
        need = needs_live_web(text)
        assert need is not None, text
        assert need.reason == "present_tense_world_question", text


def test_conversation_is_not_mistaken_for_a_research_request():
    """The guards. Each of these fired wrongly at some point while building it."""
    for text in (
        "hi",
        "good morning",
        "thanks",
        "i'm tired today",
        "how are you today",
        "tell me a joke",
        "what do you mean by that",
        "explain how a transformer works",
        "what does recursion mean",
        "write a python function that reverses a string",
        "what is 2 plus 2",
        "open notepad",
    ):
        assert needs_live_web(text) is None, text


def test_questions_about_hayat_itself_stay_with_the_runtime():
    """The runtime knows these and the web does not."""
    for text in (
        "what is your latest version",
        "how much vram do you have",
        "what model are you running right now",
        "is your camera working today",
        "what are you doing currently",
    ):
        assert needs_live_web(text) is None, text


def test_asking_whether_hayat_can_browse_is_not_an_instruction_to_browse():
    for text in (
        "can you scrape websites?",
        "can you search the web?",
        "are you able to browse the internet?",
        "do you have access to the internet?",
    ):
        assert needs_live_web(text) is None, text


def test_the_search_query_is_the_subject_not_the_instruction():
    assert clean_query("search the web for the python 3.14 release date") == "the python 3.14 release date"
    assert clean_query("google the best mechanical keyboards") == "the best mechanical keyboards"
    assert clean_query("look up how tls handshakes work") == "how tls handshakes work"
    assert clean_query("find me tutorials on rust async") == "tutorials on rust async"
    assert clean_query("what are people saying about ruff online") == "what are people saying about ruff"
    # Nothing left after stripping means the literal text is the better query.
    assert clean_query("look it up") == "look it up"


def test_the_router_sends_live_web_requests_to_research_with_tools():
    router = IntentRouter()
    session = SessionState()
    for text in (
        "find me tutorials on rust async",
        "what is the price of gold right now",
        "look up how tls handshakes work",
    ):
        intent = router.route(text, session)
        assert intent.intent == IntentKind.RESEARCH, (text, intent.intent)
        assert intent.requires_tools, text
    # Crypto prices are read from the markets (app/markets.py), not searched for.
    crypto = router.route("what is the bitcoin price right now", session)
    assert crypto.intent == IntentKind.ACTION and crypto.entities["modality"] == "markets"
    # A place's weather is read from the public forecast feed, not searched for.
    weather = router.route("what is the weather in mumbai right now", session)
    assert weather.intent == IntentKind.ACTION and weather.entities["modality"] == "public_intelligence"


def test_ordinary_conversation_still_routes_without_tools():
    router = IntentRouter()
    session = SessionState()
    for text in ("hi", "i'm tired today", "thanks"):
        intent = router.route(text, session)
        assert not intent.requires_tools, text
        assert intent.intent != IntentKind.RESEARCH, text


def test_live_web_intent_carries_the_clean_query_and_the_reason():
    intent = IntentRouter.live_web_intent("search the web for rust async runtimes")
    assert intent.intent == IntentKind.RESEARCH
    assert intent.requires_tools
    assert intent.entities["query"] == "rust async runtimes"
    assert intent.entities["live_web_reason"] == "explicit_web_verb"
    assert {item.type for item in intent.success_criteria} == {"research_complete"}


def test_pointing_at_the_open_page_reaches_that_page():
    """"read this page" is the natural phrasing and used to miss entirely."""
    router = IntentRouter()
    session = SessionState()
    session.last_browser_tab = {"url": "https://example.org/report", "title": "Report"}
    for text in (
        "read this page and tell me what it says",
        "summarise it",
        "what does this page say",
        "what's in it",
        "extract the page",
        "can you check this page for me",
    ):
        intent = router.route(text, session)
        assert intent.intent == IntentKind.BROWSER, (text, intent.intent)
        assert intent.entities["url"] == "https://example.org/report", text
        assert intent.entities["reference_resolved"] is True, text


def test_pointing_at_a_page_with_nothing_open_does_not_invent_one():
    router = IntentRouter()
    intent = router.route("read this page and tell me what it says", SessionState())
    assert "url" not in intent.entities


def test_the_summary_state_tells_the_model_it_has_a_browser():
    """Without this the model is instructed to deny having one."""
    from app.agent.conversation import _state_summary

    summary = _state_summary({
        "provider": "ollama", "model": "qwen", "mode": ["BALANCED"],
        "activity": "CONVERSATION", "active_project": None, "active_task": None,
        "browser": {"active": False, "url": None, "title": None},
        "research": {"ready": True, "active": False},
        "internet": {"reachable": True},
        "tool_count": 137,
        "storage_used": 1, "camera": {"state": "READY"},
    })
    assert "browser" in summary
    assert "research" in summary
    assert "internet" in summary
    assert summary["tool_count"] == 137
    # Still a summary, not the whole snapshot.
    assert "storage_used" not in summary and "camera" not in summary


def test_the_research_goal_drops_the_whole_verb_not_one_word_of_it():
    """Observed live: the provider was asked for "me tutorials on rust async"."""
    router = IntentRouter()
    intent = router.route("find me tutorials on rust async", SessionState())
    assert intent.intent == IntentKind.RESEARCH
    assert intent.entities["query"] == "tutorials on rust async"
    assert intent.goal == "tutorials on rust async"


def test_an_explicit_for_clause_still_wins_over_wrapper_stripping():
    router = IntentRouter()
    intent = router.route("research the current state of play for wasm components", SessionState())
    assert intent.intent == IntentKind.RESEARCH
    assert intent.entities["query"] == "wasm components"


def test_the_owners_own_past_is_not_a_web_search():
    """Live chat 2026-10-10: 'What did I have for breakfast today?' searched the web for 'breakfast today'."""
    from app.intent.live_web import needs_live_web

    for text in ("What did I have for breakfast today?", "where did I park today?", "Did I take my medicine today?"):
        assert needs_live_web(text) is None, text
    for text in ("What is the weather today?", "do I need an umbrella today?", "what did Apple announce today?"):
        assert needs_live_web(text) is not None, text


def test_colon_search_wrappers_leave_only_the_requested_subject():
    for text in ("Search the web: what is the capital city of Australia?",
                 "Please search online:what is the capital city of Australia?",
                 "Search: what is the capital city of Australia?"):
        assert clean_query(text) == "what is the capital city of Australia"
    assert clean_query("Research the web browser API") == "the web browser API"
    assert clean_query("Search for web design examples") == "web design examples"
