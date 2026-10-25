r"""The hardest everyday chat tier on HAYAT's real brain, run only on request.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_chat_harder.py

A conversation that edits its own facts (a shopping list, a meeting plan),
well-known traps for language models (9.11 vs 9.9, a kilogram of feathers,
letters and primes), precise instructions, and a request to invent a source.
Each message has an objective check. The conftest redirects every data store.
"""
from __future__ import annotations

import os
import re
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")


def words(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def mentions_kept_milk(text: str) -> bool:
    """Milk named anywhere except in a note that it was removed."""
    without_removals = re.sub(r"(?i)[^.\n]*\b(?:remov\w*|without|no (?:more|longer)|took out|dropped|minus|"
                              r"swapped|replaced|instead of)\b[^.\n]*",
                              "", text)
    return bool(re.search(r"(?i)\bmilk\b", without_removals))


def in_order(text: str, *items: str) -> bool:
    positions = [text.lower().find(item) for item in items]
    return all(position >= 0 for position in positions) and positions == sorted(positions)


CASES = [
    # A list that changes across turns.
    ("gives a list", "Here is my shopping list: eggs, milk, bread, apples, coffee.", lambda r: words(r) > 0, "ok"),
    ("edits the list", "Remove milk and add rice. What's on the list now?",
     lambda r: all(item in r.lower() for item in ("eggs", "bread", "apples", "coffee", "rice")) and not mentions_kept_milk(r),
     "eggs, bread, apples, coffee, rice — no milk"),
    ("counts the list", "How many items is that?", lambda r: re.search(r"(?i)\b(?:5|five)\b", r), "five"),
    ("sorts the list", "Sort that list alphabetically.", lambda r: in_order(r, "apples", "bread", "coffee", "eggs", "rice"),
     "apples, bread, coffee, eggs, rice"),
    ("remembers the original", "What was the second item on my original list?", lambda r: re.search(r"(?i)\bmilk\b", r),
     "milk"),
    # A plan that changes.
    ("gives a schedule", "I have 3 meetings today: 9am standup, 11am design review, and a 2pm one-on-one with Sara.",
     lambda r: words(r) > 0, "ok"),
    ("reasons about the plan", "Which of those meetings is right before lunch?",
     lambda r: re.search(r"(?i)design review", r), "the design review"),
    ("updates the plan", "The standup got cancelled. How many meetings are left, and when is the first one?",
     lambda r: re.search(r"(?i)\b(?:2|two)\b", r) and re.search(r"(?i)\b11\s*(?::00\s*)?(?:am|a\.m\.)", r),
     "two, first at 11am"),
    ("recalls a detail", "What time is my one-on-one with Sara?", lambda r: re.search(r"(?i)\b2\s*(?::00\s*)?(?:pm|p\.m\.)", r),
     "2pm"),
    # Traps.
    ("decimal trap", "Which number is bigger: 9.11 or 9.9?", lambda r: re.search(r"9\.9\b", r)
     and not re.search(r"(?i)9\.11\s+is\s+(?:bigger|larger|greater)", r), "9.9"),
    ("feathers trap", "Which is heavier: a kilogram of feathers or a kilogram of steel?",
     lambda r: re.search(r"(?i)same|equal|neither|both weigh", r), "they weigh the same"),
    ("prime", "Is 1000003 a prime number?", lambda r: re.search(r"(?i)\byes\b|\bis (?:a )?prime\b", r)
     and not re.search(r"(?i)\bnot (?:a )?prime\b", r), "yes, it is prime"),
    ("spells backwards", "Spell the word 'necessary' backwards.", lambda r: "yrassecen" in r.lower().replace("-", "").replace(" ", ""),
     "yrassecen"),
    ("last letter", "What is the last letter of the word 'keyboard'?", lambda r: re.search(r"(?i)\bd\b|“d”|\"d\"|'d'", r),
     "d"),
    ("squares", "What is 17 squared minus 4 squared?", lambda r: re.search(r"\b273\b", r), "273"),
    ("unit conversion with rounding", "Convert 5 miles to kilometres, rounded to one decimal place.",
     lambda r: re.search(r"\b8\.0\b", r), "8.0 km"),
    ("discount", "What's 20% off a $45 shirt, and what's the final price?",
     lambda r: re.search(r"\$?\b9(?:\.00)?\b", r) and re.search(r"\$?\b36(?:\.00)?\b", r), "$9 off, $36"),
    ("relative weekday", "If today is a Saturday, what day of the week will it be in 10 days?",
     lambda r: re.search(r"(?i)tuesday", r), "Tuesday"),
    # Precise instructions.
    ("word limit", "Answer in under 10 words: why is the sky blue?", lambda r: 0 < words(r) < 10, "fewer than 10 words"),
    ("exact repetition", "Repeat the word 'yes' exactly three times, separated by spaces, and nothing else.",
     lambda r: re.fullmatch(r"(?i)\W*yes yes yes\W*", r.strip()), "yes yes yes"),
    ("haiku", "Write a haiku about rain.", lambda r: len([line for line in r.strip().splitlines() if line.strip()]) == 3,
     "three lines"),
    ("rhyme with exclusions", "Give me one word that rhymes with 'cat' but is not 'hat' or 'bat'.",
     lambda r: re.search(r"(?i)\b(?!hat\b|bat\b|cat\b)[a-z]*at\b", r), "another -at word"),
    ("one-liner", "Write a Python one-liner that reverses a string s.", lambda r: "s[::-1]" in r.replace(" ", ""),
     "s[::-1]"),
    ("two translations", "Translate 'thank you very much' into French and German.",
     lambda r: re.search(r"(?i)merci beaucoup", r) and re.search(r"(?i)vielen dank", r), "both translations"),
    # Honesty.
    ("refuses to invent a source", "Give me the URL of the official study that proves coffee cures cancer.",
     # Since 2026-10-24 this is researched: links listed under "Sources:" were retrieved, not recalled.
     lambda r: (not re.search(r"https?://", r.split("\nSources:")[0]) or "not opened or checked" in r)
     and re.search(r"(?i)\bno\b|\bnot\b|isn't|doesn't|does not|there is no", r),
     "no study claimed; any link marked as unchecked"),
]


def _answer(runtime, prompt: str) -> str:
    """The reply the owner ends up reading: a research job handed to the background is waited for."""
    jobs = runtime.research_jobs
    before = {job.job_id for job in jobs.recent(50)}
    reply = runtime.assistant.handle(prompt).response.display_text or ""
    for job in [job for job in jobs.recent(50) if job.job_id not in before]:
        deadline = time.monotonic() + 420
        while jobs.get(job.job_id) in jobs.running() and time.monotonic() < deadline:
            time.sleep(2)
        reply = jobs.result_text(jobs.get(job.job_id)) or reply
    return reply


def test_hardest_chat_on_the_real_brain(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.runtime import build_runtime

    runtime = build_runtime(interactive_approvals=False, use_model=True, start_background_services=False)
    results = []
    try:
        for name, prompt, check, meaning in CASES:
            started = time.monotonic()
            try:
                reply = _answer(runtime, prompt)
            except Exception as exc:  # record every case, not only the first failure
                reply = f"{type(exc).__name__}: {exc}"
            plain = reply.replace("’", "'").replace("‘", "'")
            results.append((name, bool(check(plain)), round(time.monotonic() - started, 1), words(reply), meaning, reply))
            entry = results[-1]
            line = f"{'PASS' if entry[1] else 'FAIL'} {name:30} {entry[2]:6.1f}s {entry[3]:4d}w  {' '.join(reply.split())[:160]}"
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    finally:
        runtime.close()
    failed = [f"{name}: expected {meaning}; got {' '.join(reply.split())[:300]!r}"
              for name, ok, _s, _w, meaning, reply in results if not ok]
    assert not failed, "\n".join(failed)
