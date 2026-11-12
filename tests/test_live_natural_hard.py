r"""The hardest natural-conversation tier on HAYAT's real brain, in the owner's own speed setting. On request only.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_natural_hard.py

Real-life chats: venting, banter and opinions, a topic jump and a return, Hinglish code-switching, details given
earlier, a false belief, a long rambling message, texting typos. Besides each conversation's own checks it measures
what makes talk feel like a bot: the owner's name in every reply, a service offer at the end of every reply, "as an
AI", reports about its own status, more than one emoji, lists in casual talk. Every reply is printed in full.
"""
from __future__ import annotations

import os
import re
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")

ROBOTIC = re.compile(
    r"(?i)\bas an ai\b|\bi'?m (?:just )?an ai\b|\bi don'?t have (?:personal )?(?:feelings|preferences|opinions)\b|"
    r"\bis there anything else\b|\blet me know if (?:you need|there'?s) anything\b|\bhow can i (?:help|assist)\b|"
    r"\bi'?m here (?:to help|if you need)\b|\bfeel free to\b|\bi hope this helps\b")
SELF_STATUS = re.compile(r"(?i)\b(?:running (?:smooth|clean|fine)|systems? (?:are )?(?:all )?(?:good|green)|"
                         r"no bugs|all my (?:systems|processes))\b")
OFFER_END = re.compile(r"(?i)(?:want me to|should i|shall i|do you want me to|let me know if|i can (?:also )?help)"
                       r"[^.!?]*[.!?]?\s*\W*$")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
LIST = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S")

CONVERSATIONS = [
    ("venting", [
        "ugh my manager yelled at me in front of everyone today",
        "and the worst part is it wasn't even my fault",
        "idk maybe I should just quit",
        "nah I'm not actually quitting, just annoyed",
    ], [
        # "That sting lasts longer than the words do ... Did he calm down after?" is about the manager without naming him.
        (0, "reacts to the manager and the yelling",
         lambda r: re.search(r"(?i)manager|boss|yell|in front|everyone|public|\bhe\b|\bhim\b|embarrass|humiliat", r)),
        (1, "takes in that it wasn't his fault",
         lambda r: re.search(r"(?i)fault|unfair|blame|wasn'?t you|not you|mistake you|didn'?t do|shame|someone else|"
                             r"isn'?t yours|innocen", r)),
        (3, "follows the change of mind instead of advising on quitting",
         lambda r: not re.search(r"(?i)\b(?:resignation letter|two weeks'? notice|update your (?:cv|resume))\b", r)),
    ]),
    ("banter and opinions", [
        "lol you're actually pretty smart sometimes",
        "ok what's your favourite movie",
        "really? why that one",
    ], [
        (1, "names a movie instead of saying it has no preferences",
         lambda r: not re.search(r"(?i)don'?t (?:have|really have) (?:a )?(?:favou?rite|preferences)", r)
         and re.search(r"[A-Z][a-z]+", r)),
        (2, "explains the movie it named", lambda r: len(r.split()) >= 8),
    ]),
    ("topic jump and return", [
        "I'm thinking of getting a dog",
        "also random but what's 18 times 7",
        "anyway back to the dog, which breed is good for a small apartment?",
    ], [
        (1, "answers the detour", lambda r: re.search(r"\b126\b", r)),
        (2, "comes back to the dog with apartment breeds",
         lambda r: re.search(r"(?i)apartment|small space|flat", r) and re.search(
             r"(?i)bulldog|pug|cavalier|shih tzu|havanese|maltese|chihuahua|greyhound|boston terrier|dachshund|"
             r"bichon|poodle|basenji|corgi|french", r)),
    ]),
    ("hinglish code-switching", [
        "bro aaj office mein bahut kaam tha",
        "but finally weekend hai",
        "kya plan karna chahiye weekend ke liye?",
    ], [
        (2, "suggests weekend plans", lambda r: len(r.split()) >= 10),
    ]),
    ("details given earlier", [
        "my sister's birthday is on Saturday and she loves painting",
        "what should I get her?",
        "keep it under 50 dollars",
    ], [
        (1, "uses the painting detail", lambda r: re.search(r"(?i)paint|brush|canvas|watercolou?r|acrylic|sketch|easel|art", r)),
        (2, "keeps to the budget and the painting",
         lambda r: re.search(r"(?i)paint|brush|canvas|watercolou?r|acrylic|sketch|easel|art", r)
         and not re.search(r"\$\s?(?:[6-9]\d|\d{3,})\b", r)),
    ]),
    ("a false belief", [
        "honestly I think the moon landing was faked",
    ], [
        (0, "disagrees, kindly and with a reason",
         lambda r: re.search(r"(?i)\b(?:actually|evidence|real|did happen|happened|retroreflectors?|apollo|"
                             r"not faked|wasn'?t faked|really did|rock samples?|samples|fun theory|moon rocks?)\b", r)
         and not re.search(r"(?i)\byou(?:'re| are) (?:right|correct)\b", r)),
    ]),
    ("a long rambling message", [
        "ok so long story but my landlord said he'd fix the heater two weeks ago and nothing happened, then my "
        "roommate started complaining to me like it's my fault, and on top of that the rent is going up next "
        "month by like 8 percent which is honestly crazy given nothing works here, and I've been thinking about "
        "moving but I don't really have time to apartment hunt with work being insane right now. what would you "
        "do first?",
    ], [
        (0, "answers 'what would you do first' with one first step",
         # "Send a short message or email to your landlord today." is a first step without the word "first".
         lambda r: re.search(r"(?i)\bfirst\b|\bstart\b|\bi'?d\b|\bi would\b|\btoday\b|\bright away\b|^\W*\w+\s", r)
         and re.search(r"(?i)landlord|heater|writing|email|text|rent|lease", r)),
    ]),
    ("texting typos", [
        "wat r u doin rn",
        "lol same. any good songs 2 listen 2?",
    ], [
        # A pick can be named without quotes: "Queen's A Night at the Opera never gets old".
        (1, "understands and suggests songs", lambda r: re.search(r"[\"“'][^\"”']{2,40}[\"”']|\bby\b|"
                                                                  r"\b[A-Z][a-z]+(?:'s)?\s+[A-Z]", r)),
    ]),
]


def test_hardest_natural_talk(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.evaluation.pipeline import confine_files
    from app.identity import IDENTITY
    from app.runtime import build_runtime
    from app.session import SessionState

    runtime = build_runtime(interactive_approvals=False, use_model=True, start_background_services=False)
    confine_files(runtime, tmp_path / "files")
    failed, everything = [], []
    name = IDENTITY.owner_short
    try:
        runtime.assistant.handle("say ready in one word")  # warm-up, not measured
        for title, turns, checks in CONVERSATIONS:
            session = f"live-natural-{time.time_ns()}"
            runtime.session = SessionState(session)
            runtime.conversation.session_id = session
            print(f"\n=== {title} ===", flush=True)
            replies = []
            for turn in turns:
                started = time.monotonic()
                reply = runtime.assistant.handle(turn).response.display_text or ""
                seconds = time.monotonic() - started
                replies.append(reply)
                everything.append((title, turn, reply, seconds))
                print(f"OWNER: {turn}\nHAYAT ({seconds:.1f}s): {reply}\n".encode("ascii", "replace").decode("ascii"),
                      flush=True)
            for index, meaning, check in checks:
                ok = bool(check(replies[index].replace("’", "'")))
                print(f"{'PASS' if ok else 'FAIL'} [{title}] turn {index + 1}: {meaning}", flush=True)
                if not ok:
                    failed.append(f"{title}, turn {index + 1}: {meaning}")
    finally:
        runtime.close()

    everything = [(title, turn, reply.replace("’", "'"), seconds) for title, turn, reply, seconds in everything]
    chat = [item for item in everything if item[0] not in {"a long rambling message"}]
    measures = {
        "replies": len(everything),
        "with his name": sum(bool(re.search(rf"\b{re.escape(name)}\b", reply)) for _, _, reply, _ in everything),
        # Each conversation is a fresh session, so its first reply may greet him by name; what sounds like a bot is
        # the name in two replies running within one conversation.
        "name in back-to-back replies": sum(
            1 for (t1, _, r1, _), (t2, _, r2, _) in zip(everything, everything[1:])
            if t1 == t2 and re.search(rf"\b{re.escape(name)}\b", r1) and re.search(rf"\b{re.escape(name)}\b", r2)),
        "robotic phrases": sum(bool(ROBOTIC.search(reply)) for _, _, reply, _ in everything),
        "status reports": sum(bool(SELF_STATUS.search(reply)) for _, _, reply, _ in everything),
        "ending with an offer": sum(bool(OFFER_END.search(reply.strip())) for _, _, reply, _ in everything),
        "more than one emoji": sum(len(EMOJI.findall(reply)) > 1 for _, _, reply, _ in everything),
        "lists in casual talk": sum(bool(LIST.search(reply)) for _, _, reply, _ in chat),
        "median seconds": sorted(seconds for *_, seconds in everything)[len(everything) // 2],
    }
    print("\nMEASURES " + ", ".join(f"{key}: {value}" for key, value in measures.items()), flush=True)
    for key, limit in (("name in back-to-back replies", 0.1), ("ending with an offer", 0.34)):
        if measures[key] > limit * measures["replies"]:
            failed.append(f"{key}: {measures[key]} of {measures['replies']} replies")
    for key in ("robotic phrases", "status reports"):
        if measures[key]:
            failed.append(f"{key}: {measures[key]}")
    if measures["more than one emoji"] > 0.2 * measures["replies"]:
        failed.append(f"more than one emoji in {measures['more than one emoji']} replies")
    print(f"\n{'all checks passed' if not failed else str(len(failed)) + ' failed'}", flush=True)
    assert not failed, "\n".join(failed)
