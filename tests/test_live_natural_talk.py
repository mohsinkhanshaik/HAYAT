r"""Natural, fast back-and-forth on HAYAT's real brain, in the owner's own speed setting. Run only on request.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_natural_talk.py

The owner's setting is Deep + Adversarial (data/memory/runtime_profile.json is not redirected by the conftest), which
is exactly the case this checks: casual talk must stay quick and natural in it, Hinglish must be understood and routed
by meaning, being called by name must be answered at once, and "think harder" must still get deep thought. No speed
command is sent: that would change the owner's real setting. Every reply is printed in full.
"""
from __future__ import annotations

import os
import re
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")

REFLEX_SECONDS = 25.0


def _words(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def _alike(first: str, second: str) -> float:
    def grams(text):
        words = re.findall(r"[a-z']+", text.lower())
        return {tuple(words[index:index + 3]) for index in range(max(0, len(words) - 2))}
    a, b = grams(first), grams(second)
    return len(a & b) / len(a | b) if a and b else 0.0


def test_natural_fast_talk_on_the_real_brain(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.evaluation.pipeline import confine_files
    from app.runtime import build_runtime
    from app.session import SessionState

    runtime = build_runtime(interactive_approvals=False, use_model=True, start_background_services=False)
    confine_files(runtime, tmp_path / "files")
    failed: list[str] = []

    def converse(name, turns):
        session = f"live-talk-{time.time_ns()}"
        runtime.session = SessionState(session)
        runtime.conversation.session_id = session
        print(f"\n=== {name} (setting: {runtime.modes.snapshot().get('speed')}) ===", flush=True)
        replies = []
        for turn in turns:
            started = time.monotonic()
            reply = runtime.assistant.handle(turn).response.display_text or ""
            seconds = time.monotonic() - started
            replies.append((reply, seconds))
            print(f"OWNER: {turn}\nHAYAT ({seconds:.1f}s, {_words(reply)}w): {reply}\n".encode("ascii", "replace")
                  .decode("ascii"), flush=True)
        return replies

    def check(ok, meaning):
        print(("PASS " if ok else "FAIL ") + meaning, flush=True)
        if not ok:
            failed.append(meaning)

    try:
        # The first model turn after start-up loads what the desktop app preloads when it opens (34 s on 2026-11-12);
        # it is not a conversation speed, so it is not timed.
        converse("warm-up (not timed)", ["say ready in one word"])
        chat = converse("reflex chat", ["I'm so tired today", "what about you?", "haha fair. tell me a joke",
                                        "that was bad lol, another one", "ok I think I'll make some tea"])
        for (reply, seconds), turn in zip(chat, ["tired", "about you", "joke", "another", "tea"]):
            check(seconds <= REFLEX_SECONDS, f"reflex '{turn}' answered in {seconds:.0f}s (<= {REFLEX_SECONDS:.0f}s)")
            check(_words(reply) <= 70 and not re.search(r"(?m)^\s*(?:[-*•]|\d+\.)\s", reply),
                  f"reflex '{turn}' is short plain talk ({_words(reply)} words)")
        texts = [reply for reply, _ in chat]
        check(max(_alike(a, b) for index, a in enumerate(texts) for b in texts[index + 1:]) < 0.5,
              "no two chat replies say the same thing")
        check(_alike(texts[2], texts[3]) < 0.4, "the second joke is a different joke")

        hinglish = converse("hinglish", ["kya haal hai bhai", "yaar aaj bahut thak gaya hoon"])
        check(all(seconds <= REFLEX_SECONDS for _, seconds in hinglish), "Hinglish small talk is quick")
        # It may answer in Hinglish too: "Thoda relax kar le ... saans lene ka time de" (live, 2026-11-12).
        check(re.search(r"(?i)\b(?:tired|thak\w*|rest|aaram|sleep|break|long day|relax|saans|baith|breathe|recharge|"
                        r"take it easy)\b", hinglish[1][0]) is not None,
              "understands 'thak gaya hoon' as being tired")

        weather = converse("hinglish request", ["aaj mausam kaisa hai hyderabad mein"])
        check(re.search(r"(?i)hyderabad", weather[0][0]) is not None, "the Hinglish weather request is about Hyderabad")

        names = converse("called by name", ["jarvis?", "hey hayat"])
        check(all(seconds <= 5 and _words(reply) <= 25 for reply, seconds in names), "being called is answered at once")

        deep = converse("think harder", ["Is 221 a prime number? think it through"])
        check(re.search(r"(?i)\b13\b.*\b17\b|\b17\b.*\b13\b|not\s+(?:a\s+)?prime", deep[0][0]) is not None,
              "'think it through' still reasons (221 = 13 x 17)")
    finally:
        runtime.close()
    print(f"\n{'all checks passed' if not failed else str(len(failed)) + ' failed'}", flush=True)
    assert not failed, "\n".join(failed)
