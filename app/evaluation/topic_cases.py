"""Labelled conversations for topic routing: does each message land in the right relation and topic?

Each case is a short conversation built from a failure HAYAT really had, or one the owner described: a follow-up
searched as typed ("predict for nxt year" -> wrestling), an earlier topic that could not be restored, a correction
searched as a new subject, an unrelated thread written into an answer. Every message carries the relation a careful
listener would give it, a group label (messages with the same label belong to the same topic), and for a follow-up
that leans on its topic, words its standalone search query must contain.

``run_benchmark`` measures, on a throwaway database:

* relation accuracy        -- the relation decided for each message
* grouping accuracy        -- pairs of messages put together or apart as the labels say
* reference resolution     -- leaning follow-ups whose query names the topic's subject
* context contamination    -- answers whose history still holds a message from an unrelated topic

and the same four for the *baseline* -- the behaviour before topics existed: every message continues one thread,
follow-ups are searched as typed, and the reply sees the whole recent history.
"""
from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Any

#: (message, expected relation, topic group, words the anchored query must contain or None, minutes since previous)
Turn = tuple[str, str, str, tuple[str, ...] | None, int]

TODAY = date(2026, 11, 9)

CASES: list[tuple[str, list[Turn]]] = [
    ("economic follow-ups are read in their topic (the WWE NXT failure)", [
        ("What is the GDP growth forecast for India in 2026?", "new_conversation", "econ", None, 0),
        ("Look at the latest and predict for nxt year", "continue", "econ", ("gdp", "2027", "india"), 2),
        ("what about china", "continue", "econ", ("gdp", "china"), 2),
        ("how does inflation affect it?", "continue", "econ", ("gdp", "inflation"), 3),
        ("thanks", "small_talk", "econ", None, 1),
    ]),
    ("an unrelated question in the same chat, then back", [
        ("Which GPU should I buy for running local LLMs with 8GB VRAM?", "new_conversation", "gpu", None, 0),
        ("is the RTX 5060 enough?", "continue", "gpu", None, 2),
        ("btw give me a quick pasta recipe for dinner", "new_topic", "food", None, 4),
        ("can I use whole wheat pasta instead?", "continue", "food", None, 2),
        ("back to the gpu question from earlier", "return", "gpu", None, 5),
        ("and what about the 5070?", "continue", "gpu", None, 2),
    ]),
    ("corrections stay in their topic and are never a new subject", [
        ("search the latest news on NXT", "new_conversation", "nxt", None, 0),
        ("no I meant the stock exchange ticker NXT, not wrestling", "correction", "nxt", None, 1),
        ("these results are irrelevant", "correction", "nxt", None, 1),
        ("that's not what I asked", "correction", "nxt", None, 1),
    ]),
    ("asking about running work goes to the topic that started it", [
        ("Research the best solar inverters for a small home", "new_conversation", "solar", None, 0),
        ("is it done?", "task_status", "solar", None, 3),
        ("any update?", "task_status", "solar", None, 4),
    ]),
    ("information about another person is kept as that person's", [
        ("my brother Omar works as a nurse in Dubai", "new_conversation", "omar", None, 0),
        ("what shifts does he usually work?", "continue", "omar", None, 2),
        ("What's a good laptop for video editing under $1000?", "new_topic", "laptop", None, 3),
    ]),
    ("two topics connected on purpose", [
        ("What is the GDP growth forecast for India in 2026?", "new_conversation", "econ2", None, 0),
        ("Which Nvidia GPU has the best price per VRAM right now?", "new_topic", "gpu2", None, 3),
        ("how does the GPU shortage relate to the india growth outlook?", "connect", "gpu2", None, 2),
    ]),
    ("a misspelt request and its follow-up", [
        ("whats the wether in london tomorow", "new_conversation", "weather", None, 0),
        ("and in paris?", "continue", "weather", ("paris",), 1),
        ("teach me some arabic greetings", "new_topic", "arabic", None, 5),
        ("and how do i say thank you", "continue", "arabic", None, 2),
        ("lets go back to the weather in london from earlier", "return", "weather", None, 6),
    ]),
    ("a long gap starts a new conversation; a greeting is not a topic", [
        ("What is the capital of Australia?", "new_conversation", "aus", None, 0),
        ("hello", "small_talk", "greet", None, 60 * 8),
        ("how do vaccines train the immune system?", "new_topic", "greet", None, 1),
        ("why do some need boosters?", "continue", "greet", None, 2),
    ]),
    ("returning implicitly by naming the old subject", [
        ("Explain how a heat pump works in winter", "new_conversation", "heat", None, 0),
        ("Who won the 2022 FIFA World Cup final?", "new_topic", "football", None, 3),
        ("does a heat pump still work when it is minus 15 outside in winter", "return", "heat", None, 3),
    ]),
]


#: Written after the engine was tuned on CASES and scored once, untuned: the honest out-of-sample figure.
HOLDOUT_CASES: list[tuple[str, list[Turn]]] = [
    ("crypto price follow-ups", [
        ("What's the bitcoin price outlook for 2027?", "new_conversation", "btc", None, 0),
        ("and ethereum?", "continue", "btc", ("ethereum",), 1),
        ("compare it with last year", "continue", "btc", ("2025",), 2),
    ]),
    ("job search interrupted by a cooking question", [
        ("Find me remote python developer jobs in Europe", "new_conversation", "jobs", None, 0),
        ("how long should I boil an egg for a soft yolk?", "new_topic", "egg", None, 3),
        ("going back to the python jobs, any in Germany?", "return", "jobs", None, 4),
    ]),
    ("a correction after a misread", [
        ("tell me about mercury", "new_conversation", "mercury", None, 0),
        ("no I meant the planet, not the element", "correction", "mercury", None, 1),
        ("how hot does it get there?", "continue", "mercury", None, 1),
    ]),
    ("a friend's plans are not the owner's", [
        ("What's the weather like in Istanbul this weekend?", "new_conversation", "ist", None, 0),
        ("my friend Yusuf is flying to Tokyo on Friday", "other_person", "yusuf", None, 3),
        ("what should he see there first?", "continue", "yusuf", None, 2),
    ]),
    ("status of running research", [
        ("Look up the newest research on solid state batteries", "new_conversation", "batt", None, 0),
        ("ok", "small_talk", "batt", None, 1),
        ("still working on it?", "task_status", "batt", None, 2),
        ("how close are they to cars?", "continue", "batt", None, 2),
    ]),
]


def _run(cases: list[tuple[str, list[Turn]]], *, baseline: bool) -> dict[str, Any]:
    from app.topics import ConversationTopics

    relation_total = relation_ok = 0
    pairs_total = pairs_ok = 0
    lean_total = lean_ok = 0
    contamination_turns = contaminated = 0
    failures: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as folder:
        store = ConversationTopics(Path(folder) / "topics.db")
        for index, (name, turns) in enumerate(cases):
            session = f"bench-{index}"
            moment = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
            placed: list[tuple[str, str, str]] = []  # (group, topic_id, text)
            history: list[dict[str, Any]] = []
            for position, (text, relation, group, query_words, minutes) in enumerate(turns):
                moment += timedelta(minutes=minutes)
                decision = store.decide(session, text, now=moment, today=TODAY)
                decided = ("new_conversation" if position == 0 else "continue") if baseline else decision.relation
                relation_total += 1
                if decided == relation:
                    relation_ok += 1
                else:
                    failures.append({"case": name, "message": text, "expected": relation, "decided": decided,
                                     "reasons": decision.reasons})
                topic_id = "one-thread" if baseline else decision.topic_id
                placed.append((group, topic_id, text))
                if query_words is not None:
                    lean_total += 1
                    query = (text if baseline else (decision.query or text)).lower()
                    if all(word in query for word in query_words):
                        lean_ok += 1
                    else:
                        failures.append({"case": name, "message": text, "expected_query_words": list(query_words),
                                         "query": query})
                history.append({"role": "user", "content": text})
                kept = history if baseline else store.scope_history(session, history, decision)[0]
                if position:
                    groups_by_text = {item[2]: item[0] for item in placed}
                    others = [item for item in kept[:-1] if item.get("role") == "user"
                              and groups_by_text.get(item["content"], group) != group]
                    # A connect message is meant to see both topics.
                    if relation != "connect" and any(groups_by_text.get(item["content"]) != group
                                                     for item in history[:-1] if item.get("role") == "user"):
                        contamination_turns += 1
                        contaminated += bool(others)
                answer = f"(reply about {decision.title})"
                store.observe_reply(decision, answer, session_id=session, now=moment + timedelta(seconds=20))
                history.append({"role": "assistant", "content": answer})
            for (group_a, topic_a, _), (group_b, topic_b, _) in combinations(placed, 2):
                pairs_total += 1
                pairs_ok += (group_a == group_b) == (topic_a == topic_b)
    return {
        "relation_cases": relation_total, "relation_correct": relation_ok,
        "relation_accuracy": round(relation_ok / relation_total, 3) if relation_total else None,
        "grouping_accuracy": round(pairs_ok / pairs_total, 3) if pairs_total else None,
        "reference_resolution": round(lean_ok / lean_total, 3) if lean_total else None,
        "contamination_rate": round(contaminated / contamination_turns, 3) if contamination_turns else 0.0,
        "contamination_turns": contamination_turns, "failures": failures,
    }


def run_benchmark(cases: list[tuple[str, list[Turn]]] | None = None) -> dict[str, Any]:
    """The topic engine's measured accuracy on the labelled cases, with the one-thread baseline beside it."""
    cases = cases or CASES
    result = _run(cases, baseline=False)
    baseline = _run(cases, baseline=True)
    baseline.pop("failures", None)
    result["baseline"] = baseline
    result["cases"] = len(cases)
    return result
