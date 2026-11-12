"""HAYAT reads misspellings, synonyms and words with several meanings as a person would.

app/intent/spelling.py (misspellings), app/linguistics.synonyms_for (everyday synonyms in recall),
app/research/senses.py (words with several meanings in research).
"""
from __future__ import annotations

import pytest

from app.intent import spelling
from app.intent.spelling import correct, learn_owner_words
from app.linguistics import expand_query, synonyms_for
from app.research.senses import resolve, search_text

pytestmark = pytest.mark.skipif(not spelling.available(), reason="pyspellchecker is not installed")


@pytest.mark.parametrize("typed, meant", [
    # The owner's own messages, 2026-11-07..08.
    ("imprve hayat and reserach the invnetion componenets", "improve hayat and research the invention components"),
    ("what do u rember about my sistr birthday", "what do u remember about my sister birthday"),
    ("fix halluination", "fix hallucination"),
    ("hayat must understand all misspelled and synonyms and homonyps",
     "hayat must understand all misspelled and synonyms and homonyms"),
    ("upgade web scraping and impovement knowledge", "upgrade web scraping and improvement knowledge"),
    ("work accordingl", "work accordingly"),
    ("what is teh capitl of france", "what is the capital of france"),
    ("tell me abt quantam computing", "tell me abt quantum computing"),
])
def test_the_owners_real_typos_are_read_as_meant(typed, meant):
    assert correct(typed).text == meant


@pytest.mark.parametrize("text", [
    "mohsin khan shaik",                           # a name two letters from "cousin" is not a typo
    "tech and business world",                     # "tech" is not "teach"
    "wasm components",                             # "wasm" is not "was"
    "write a note saying I luv my sistr",          # dictated words are reproduced exactly
    'search for "helo wrld" exactly',              # quoted text too
    "open D:\\AI\\reprot.docx",                    # paths and file names
    "email bob@exmaple.com",                       # addresses
    "Ask Jaadoo about Hamuzair",                   # capitalised names inside a sentence
    "the GUI and the CSI page",                    # acronyms
    "Python is a language",                        # real words are never changed
])
def test_what_must_never_be_rewritten_is_left_alone(text):
    assert correct(text).text == text


def test_tool_words_are_left_to_the_tool_word_reader_which_fixes_them_where_it_matters():
    from app.intent.typos import fix

    # "camra" and "wether" decide which tool runs; app/intent/typos.py repairs them only where that reaches a tool.
    assert correct("opne the camra").text == "open the camra"  # "opne" is an ordinary word; "camra" is left
    assert fix("opne the camra") == "opne the camera" and fix("wether in dubai") == "weather in dubai"


def test_every_change_is_reported():
    result = correct("rember my sistr")
    assert result.changes == [("rember", "remember"), ("sistr", "sister")] and result.changed


def test_the_owners_own_words_are_learned_but_typos_of_real_words_are_not():
    messages = ["jaadoo is my friend", "call jaadoo", "jaadoo again", "imprve it", "imprve that", "imprve this"]
    learned = learn_owner_words(messages)
    assert "jaadoo" in learned and "imprve" not in learned
    assert correct("ask jaadoo").text == "ask jaadoo"
    assert correct("imprve it").text == "improve it"


def test_the_router_rereads_only_words_that_reach_nothing_and_keeps_words_that_do():
    from app.intent.router import IntentRouter
    from app.intent.models import IntentKind

    router = IntentRouter()
    plain = router.route("reserach quantam computing")
    assert plain.intent == IntentKind.RESEARCH and "quantum" in plain.goal.lower()
    assert ("quantam", "quantum") in router.last_spelling and plain.entities["spelling"]
    # Already specific as typed: the owner's own words are what is acted on, never "venom".
    money = router.route("send $20 to Sara on venmo")
    assert "venmo" in money.goal and router.last_spelling == []
    kept = router.route("reserach the latest quantam computing papers")
    assert kept.intent == IntentKind.RESEARCH and kept.goal.startswith("reserach")


def test_memory_recall_finds_a_memory_through_a_typo_and_a_synonym(tmp_path):
    from app.memory.database import MemoryDatabase

    memory = MemoryDatabase(tmp_path / "recall.db")
    memory.initialize()
    birthday = memory.store_memory("semantic", "Mohsin's sister's birthday is on 12 May", 0.9, "user")
    photo = memory.store_memory("semantic", "The holiday photo of Lahore is saved in Pictures", 0.9, "user")
    for index in range(20):
        memory.store_memory("semantic", f"Unrelated note number {index} about groceries", 0.5, "user")
    assert "sister" in expand_query("my sistr birthday").text
    assert memory.retrieve("when is my sistr birthday", limit=1)[0]["id"] == birthday
    assert "photo" in synonyms_for("holiday picture")
    assert memory.retrieve("where is the holiday picture", limit=1)[0]["id"] == photo


@pytest.mark.parametrize("question, word, meaning, by", [
    ("how fast is a jaguar", "jaguar", "the animal", "question"),
    ("jaguar price in india", "jaguar", "the car maker", "question"),
    ("python length", "python", "the snake", "question"),
    ("install python on windows", "python", "the programming language", "question"),
    ("apple calories", "apple", "the fruit", "question"),
    ("apple stock today", "apple", "the company", "question"),
    ("jaguar", "jaguar", "the animal", "most common"),
])
def test_a_word_with_several_meanings_is_read_from_the_words_around_it(question, word, meaning, by):
    [reading] = resolve(question)
    assert (reading.word, reading.meaning.name, reading.chosen_by) == (word, meaning, by)


def test_the_conversation_decides_when_the_question_does_not_and_named_meanings_are_left_alone():
    [reading] = resolve("python size", context="I just bought a pet snake")
    assert reading.meaning.name == "the snake" and reading.chosen_by == "conversation"
    assert "just talking about" in reading.note
    assert resolve("python snake length") == [] and resolve("freddie mercury songs") == []
    [assumed] = resolve("jaguar")
    assert "say if you meant the car maker" in assumed.note
    assert search_text("how fast is a jaguar", resolve("how fast is a jaguar")) == "how fast is a jaguar animal"


def test_research_searches_with_the_meaning_and_says_when_it_assumed_one(tmp_path):
    from app.research.knowledge import KnowledgeStore
    from app.research.manager import ResearchManager

    searched = []

    class Search:
        def search(self, query, limit=10, recency=None, domains=None):
            searched.append(query)
            return []

    settings = {"database": str(tmp_path / "k.db"), "user_agent": "test", "respect_robots_txt": False,
                "max_concurrency": 1, "model_synthesis": False,
                "budgets": {"fast": {"searches": 1, "pages": 1, "max_seconds": 5, "max_bytes": 100000}}}
    research = ResearchManager(settings, model=None, search_provider=Search(), knowledge=KnowledgeStore(tmp_path / "k.db"))
    research.conversation_context = lambda: "I am comparing cars for my family, an SUV"
    try:
        result = research.research("jaguar mileage", "fast", remember=False)
    finally:
        research.close()
    assert result.trace["meanings"] == [{"word": "jaguar", "meaning": "the car maker", "chosen_by": "conversation"}]
    assert searched and all("jaguar car" in query.lower() for query in searched)
