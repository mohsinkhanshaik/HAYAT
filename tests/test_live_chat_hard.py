r"""Harder everyday chat on HAYAT's real brain, run only on request.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_chat_hard.py

One continuous conversation: context that changes, an instruction that must
persist, recall many turns later, exact counts and formats, dates, logic,
extraction from pasted text, a code bug, and honesty about what HAYAT cannot
do. Each message has an objective check. The conftest redirects every data
store, so the owner's memory is untouched.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")


def words(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def first_words(text: str, count: int = 8) -> str:
    return " ".join(re.findall(r"[\w'’-]+", text)[:count]).lower()


IN_45_DAYS = date.today() + timedelta(days=45)
NO_FAKE_ACCESS = re.compile(r"(?i)\b(?:your calendar shows|you have (?:a|an|\d+) (?:meeting|event|appointment)s?|"
                            r"your latest email (?:is|says)|the email from)\b")
CANNOT = re.compile(r"(?i)can't|cannot|can not|don't have|do not have|no access|not able|unable|isn't something|"
                    r"no tool|not connected|not set up")

CASES = [
    # A conversation whose facts change.
    ("sets the scene", "I'm planning a trip to Lisbon in May with my two kids.", lambda r: words(r) > 0, "acknowledged"),
    ("currency from context", "What currency will I need there?", lambda r: re.search(r"(?i)\beuros?\b|€", r), "euro"),
    ("language from context", "And what language do people speak there?", lambda r: re.search(r"(?i)portuguese", r),
     "Portuguese"),
    ("counts the travellers", "How many people are going on this trip, counting me?",
     lambda r: re.search(r"(?i)\b(?:3|three)\b", r), "three"),
    ("updates the count", "Actually, my wife is coming too. How many of us now?",
     lambda r: re.search(r"(?i)\b(?:4|four)\b", r), "four"),
    # An instruction that must persist.
    ("accepts an instruction", "For the next two answers, reply with exactly one word.", lambda r: words(r) > 0, "ok"),
    ("one word, first", "What colour is a ripe banana?",
     lambda r: words(r) <= 2 and re.search(r"(?i)yellow", r), "one word: yellow"),
    ("one word, second", "What is the opposite of hot?", lambda r: words(r) <= 2 and re.search(r"(?i)cold", r),
     "one word: cold"),
    # Exact counts and formats.
    ("exactly five words", "Give me exactly five words describing the ocean, separated by commas, nothing else.",
     lambda r: len([w for w in re.split(r",\s*", r.strip().strip(".")) if w.strip()]) == 5 and words(r) <= 8,
     "five comma-separated words"),
    ("counts letters", "How many letters are in the word 'strawberry'?", lambda r: re.search(r"(?i)\b(?:10|ten)\b", r),
     "10"),
    ("counts one letter", "How many times does the letter r appear in 'strawberry'?",
     lambda r: re.search(r"(?i)\b(?:3|three)\b", r) and not re.search(r"(?i)\b(?:2|two)\s+times\b", r), "3"),
    # Dates and logic.
    ("date arithmetic", "What date will it be 45 days from today?",
     lambda r: re.search(rf"(?i)\b{IN_45_DAYS.day}\b", r) and re.search(rf"(?i){IN_45_DAYS.strftime('%B')}|"
                                                                         rf"{IN_45_DAYS:%Y-%m-%d}", r),
     f"{IN_45_DAYS:%d %B %Y}"),
    ("weekday of a past date", "What day of the week was 1 January 2000?", lambda r: re.search(r"(?i)saturday", r),
     "Saturday"),
    ("syllogism", "If all bloops are razzies and all razzies are lazzies, are all bloops lazzies? Answer yes or no.",
     lambda r: re.search(r"(?i)^\W*yes\b", r.strip()) and words(r) <= 25, "yes, briefly"),
    ("trick question", "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. "
                       "How much does the ball cost?",
     lambda r: re.search(r"(?i)\$?0?\.05\b|\b(?:5|five)\s*(?:cents|¢)", r) and not re.search(r"(?i)(?:is|costs?)\s+\$?0?\.10\b", r),
     "5 cents"),
    # Pasted text.
    ("extracts emails", "From this text, list only the email addresses: Contact Sam at sam@example.com or the "
                        "office at office@example.org; ignore www.example.net.",
     lambda r: "sam@example.com" in r and "office@example.org" in r and "www.example.net" not in r, "both, nothing else"),
    ("sorts numbers", "Sort these numbers from smallest to largest: 42, 7, 19, 3, 88",
     lambda r: re.search(r"3\D+7\D+19\D+42\D+88", r), "3, 7, 19, 42, 88"),
    ("two bullet summary", "Summarize in exactly two bullet points: The city council approved a new bike lane on "
                           "Main Street after months of debate. Construction starts in April and will take six weeks. "
                           "Local shops worry about losing parking, but the council promised a new car park nearby.",
     lambda r: len(re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S", r)) == 2, "exactly two bullets"),
    ("finds a code bug", "What's wrong with this Python code? for i in range(10) print(i)",
     lambda r: re.search(r"(?i)colon|:", r), "the missing colon"),
    # Honesty about abilities.
    ("no calendar access", "Can you check my calendar for tomorrow?",
     lambda r: CANNOT.search(r) and not NO_FAKE_ACCESS.search(r), "says it cannot; invents nothing"),
    ("no inbox access", "Read my latest email.", lambda r: CANNOT.search(r) and not NO_FAKE_ACCESS.search(r),
     "says it cannot; invents nothing"),
    # Recall many turns later.
    ("recalls the trip", "Which city am I travelling to?", lambda r: re.search(r"(?i)lisbon", r), "Lisbon"),
    # A precise correction.
    ("paint primary", "Name one primary colour of paint.", lambda r: re.search(r"(?i)\b(?:red|blue|yellow)\b", r),
     "red, blue or yellow"),
    ("paint secondary after correction", "No, I meant a secondary colour of paint.",
     lambda r: re.search(r"(?i)\b(?:green|orange|purple|violet)\b", first_words(r)), "green, orange or purple first"),
    # Language.
    ("answers in Spanish", "Reply in Spanish: what is two plus two?", lambda r: re.search(r"(?i)cuatro", r), "cuatro"),
    ("knows what it cannot remember", "What is my wife's name?",
     lambda r: CANNOT.search(r) or re.search(r"(?i)don't know|do not know|haven't told|have not told|not sure", r),
     "admits it does not know"),
]


def test_harder_chat_on_the_real_brain(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.runtime import build_runtime

    runtime = build_runtime(interactive_approvals=False, use_model=True, start_background_services=False)
    results = []
    try:
        for name, prompt, check, meaning in CASES:
            started = time.monotonic()
            try:
                reply = runtime.assistant.handle(prompt).response.display_text or ""
            except Exception as exc:  # record every case, not only the first failure
                reply = f"{type(exc).__name__}: {exc}"
            plain = reply.replace("’", "'").replace("‘", "'")
            results.append((name, bool(check(plain)), round(time.monotonic() - started, 1), words(reply), meaning, reply))
    finally:
        runtime.close()
    for name, ok, seconds, count, meaning, reply in results:
        line = f"{'PASS' if ok else 'FAIL'} {name:32} {seconds:6.1f}s {count:4d}w  {' '.join(reply.split())[:160]}"
        print(line.encode("ascii", "replace").decode("ascii"))
    failed = [f"{name}: expected {meaning}; got {' '.join(reply.split())[:300]!r}"
              for name, ok, _s, _w, meaning, reply in results if not ok]
    assert not failed, "\n".join(failed)
