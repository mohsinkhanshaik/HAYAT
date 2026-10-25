r"""Everyday chat quality on HAYAT's real brain, run only on request.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_chat_quality.py

Ordinary conversation through the whole assistant (routing, memory, the
brain, self-inquiry, review and the reply checks), under the owner's own mode
settings. Each prompt has an objective check: an exact fact, a format, a
length, or that nothing false is claimed. The conftest redirects every data
store, so the owner's memory is untouched.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")


def words(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def numbered_items(text: str) -> int:
    return len(re.findall(r"(?m)^\s*\d+[.)]\s+\S", text))


def json_object(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(match.group()) if match else None
    except ValueError:
        return None


NO_FALSE_ACTION = re.compile(r"(?i)\b(?:i(?:'ve| have)? (?:sent|emailed|booked|ordered|paid)|has been sent|was sent)\b")
_NEGATED = re.compile(r"(?i)\b(?:no|nothing|not|never|nobody)\b[^.!?\n]{0,25}$")


def false_action(reply: str) -> bool:
    """A claimed action; "Nothing has been sent yet" (live 2026-10-24) is the honest opposite."""
    return any(not _NEGATED.search(reply[:match.start()]) for match in NO_FALSE_ACTION.finditer(reply))

#: (name, prompt, check(reply) -> bool, what the check means)
CASES = [
    ("greeting is short", "hi", lambda r: 0 < words(r) <= 45, "a hello, not an essay"),
    ("small talk is short", "how are you doing today?", lambda r: 0 < words(r) <= 70, "conversational length"),
    ("acknowledgement is short", "ok great", lambda r: 0 < words(r) <= 70, "no deep answer to 'ok great'"),
    ("exact list format", "List exactly three benefits of drinking water as a numbered list and nothing else.",
     lambda r: numbered_items(r) == 3, "exactly three numbered items"),
    ("one sentence when asked", "In one sentence, what is photosynthesis?",
     lambda r: words(r) <= 45 and re.search(r"(?i)light", r), "one short sentence that mentions (sun)light"),
    ("explains when asked", "Explain how a refrigerator keeps food cold.",
     lambda r: words(r) >= 60 and re.search(r"(?i)refrigerant|compress|evaporat|heat", r), "a real explanation"),
    ("word problem", "If a train travels 120 km in 1.5 hours, what is its average speed in km/h?",
     lambda r: re.search(r"\b80\b", r), "80 km/h"),
    ("unit conversion", "How many minutes are in 3.5 hours?", lambda r: re.search(r"\b210\b", r), "210"),
    ("remember a fact", "My sister Aisha lives in Toronto.", lambda r: words(r) > 0, "acknowledged"),
    ("follow-up uses context", "Which country does she live in?", lambda r: re.search(r"(?i)\bcanada\b", r),
     "Canada, from the previous turn"),
    ("first answer", "Name one primary colour.", lambda r: re.search(r"(?i)\b(?:red|blue|yellow)\b", r),
     "red, blue or yellow"),
    ("correction is applied", "No, I meant a secondary colour.",
     lambda r: re.search(r"(?i)\b(?:green|orange|purple|violet)\b", r), "a secondary colour after the correction"),
    ("ambiguous request asks", "Book it for tomorrow.",
     lambda r: "?" in r and not false_action(r), "asks what to book; claims nothing"),
    ("summarises pasted text",
     "Summarize in one sentence: Maple Street Bakery opened in March. It sells sourdough, croissants and "
     "seasonal pies. The owner, Lena, trained in Lyon for six years. On weekends there is a queue before 8 am "
     "because the cinnamon rolls sell out early.",
     lambda r: words(r) <= 50 and re.search(r"(?i)bakery", r), "one sentence about the bakery"),
    ("translation", "Translate 'good morning' into Spanish.", lambda r: re.search(r"(?i)buenos d[ií]as", r),
     "buenos días"),
    ("today's weekday", "What day of the week is it today?",
     lambda r: datetime.now().strftime("%A").lower() in r.lower(), "the real weekday"),
    ("honest about actions", "Did you send my email to Ali?",
     lambda r: not false_action(r) and re.search(r"(?i)\b(?:no|not|haven't|have not|didn't|did not)\b", r),
     "says no, claims nothing"),
    ("JSON when asked", 'Return only a JSON object with keys "name" and "age" for a person named Sara who is 30.',
     lambda r: (json_object(r) or {}).get("name") == "Sara" and (json_object(r) or {}).get("age") in (30, "30"),
     "valid JSON with the values"),
    ("short comparison", "What is the difference between RAM and storage? Keep it short.",
     lambda r: words(r) <= 130 and re.search(r"(?i)\bram\b", r) and re.search(r"(?i)storage", r), "short, covers both"),
    ("small code", "Write a Python function that returns the square of a number.",
     lambda r: "def " in r and "return" in r, "a function with def and return"),
    ("meaning of a word", "What does the Arabic word 'shukran' mean?", lambda r: re.search(r"(?i)\bthank", r),
     "thank you"),
    ("understands typos", "waht is teh capitl of france?", lambda r: re.search(r"(?i)\bparis\b", r), "Paris"),
    ("two-part maths", "What is 15% of 200, and what is the square root of 144?",
     lambda r: re.search(r"\b30\b", r) and re.search(r"\b12\b", r), "30 and 12"),
    ("supportive, not a lecture", "I'm feeling stressed about my exam tomorrow.",
     lambda r: 0 < words(r) <= 150 and not false_action(r), "short support, no invented actions"),
    ("does not invent personal facts", "What did I have for breakfast today?",
     lambda r: re.search(r"(?i)don't know|do not know|not sure|haven't told|have not told|no record|didn't mention|"
                         r"did not mention|can't know|cannot know|don't have|do not have|wasn't told|no information", r),
     "admits it does not know"),
    ("simple fact", "What's the capital of Japan?", lambda r: re.search(r"(?i)\btokyo\b", r), "Tokyo"),
    ("no review talk in answers", "Name a planet with rings.",
     lambda r: re.search(r"(?i)\bsaturn|jupiter|uranus|neptune\b", r)
     and not re.search(r"(?i)\b(?:advisor|critic|second opinion|reviewer)\b|confirmed it|no further verification", r),
     "a planet, without internal review talk"),
    ("knows its memory", "Do you remember things I tell you between sessions?",
     lambda r: re.search(r"(?i)\b(?:yes|remember|save|store|memory)\b", r)
     and not re.search(r"(?i)\b(?:no|don't have|do not have|lack)\s+(?:permanent|persistent|long-term)?\s*memory", r),
     "yes, without denying its memory"),
]


def test_everyday_chat_on_the_real_brain(monkeypatch):
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
            # The brain writes curly apostrophes ("don’t"); the checks spell them straight.
            ok = bool(check(reply.replace("’", "'").replace("‘", "'")))
            results.append((name, ok, round(time.monotonic() - started, 1), words(reply), meaning, reply))
    finally:
        runtime.close()
    for name, ok, seconds, count, meaning, reply in results:
        line = f"{'PASS' if ok else 'FAIL'} {name:26} {seconds:6.1f}s {count:4d}w  {' '.join(reply.split())[:170]}"
        print(line.encode("ascii", "replace").decode("ascii"))
    failed = [f"{name}: expected {meaning}; got {' '.join(reply.split())[:300]!r}"
              for name, ok, _s, _w, meaning, reply in results if not ok]
    assert not failed, "\n".join(failed)
