"""The conversation of 2026-11-11 as a regression case, and two measures for any replayed conversation.

The owner's own messages are kept word for word (misspellings included: they are what the reader must handle).
Each case says what the message is about and what a reply must and must not be about, so a replay can be scored
without a model:

* **relevance** -- the share of the question's own subject words that the reply takes up. A reply about ModelError
  logs to a question about making money scores near zero, however fluent it is.
* **repetition** -- how alike two replies are (word 3-shingles, Jaccard; 1.0 = the same text). The canned list sent
  twice in three messages scored 1.0.

Neither measure says an answer is *correct*. A reply can be on topic, new, and wrong; that is why sourced research
and the owner's verdicts exist. These measures catch the two failures that need no judge to see.
"""
from __future__ import annotations

from typing import Any

from app.dialogue_state import alike

#: The owner's messages of 2026-11-11 (Pacific time 2026-11-10 23:10 onwards), minutes from the first.
TRANSCRIPT: tuple[tuple[int, str], ...] = (
    (0, "what are u learning and what are skill that u learned"),
    (5, "whats wrong?"),
    (5, "work on it"),
    (14, "ok can u fix it?"),
    (17, "how did u fix it this time?"),
    (20, "great u got smarter and better and u actually were impressive"),
    (24, "what was the error which happen in u said Error That turn failed: ModelError: No bounded streaming provider "
         "completed the response The session is still running. Use /doctor to inspect subsystems. ?"),
    (32, "Investigate the two ModelError failures between 11:10 PM and 11:24 PM on November 10, 2026 Pacific Time. "
         "Retrieve the actual Ollama, inference-provider, and orchestrator logs. Determine whether recovery resulted "
         "from an automatic retry, provider restart, timeout expiration, fallback, or another mechanism. Show "
         "timestamps and evidence for every conclusion. Also investigate why my instruction \"can you fix it?\" was "
         "interpreted as a tool-demonstration request. Do not modify anything until the cause is established. Do not "
         "invent tool executions, repairs, or metrics."),
    (42, "HAYAT, you have my permission to independently investigate this issue using your existing authorized file "
         "and diagnostic tools. Check the relevant logs, identify the actual cause of the failure, and explain what "
         "happened in simple language."),
    (49, "HAYAT, stop asking me which diagnostic command to run when I've already explained the problem and "
         "authorized an investigation."),
    (300, "generate an image ufc blood shed fight"),
    (308, "ok look all ways to make money and better and higher ways to grow up as rise of ai and all jobs laid off"),
    (309, "this is same reply u store from last reply so look again but deeper"),
    (313, "i dint ask anything about model errpr i was asking u to look better ideas to make money and become "
          "billionaire by 2030"),
    (315, "again same thing dont treat this fact as i can change"),
)

#: The replies HAYAT actually gave to the last four messages (shortened), the baseline the fixes are measured against.
BASELINE_REPLIES: tuple[str, ...] = (
    "Here are businesses one person can start with my help. Tell me your skills and I'll narrow it down: 1. Freelance "
    "writing and editing (service): Write or polish blog posts, website text, product descriptions and newsletters "
    "for small businesses. 2. Graphic and logo design (service). 3. Social media management (service).",
    "You are right. I missed the context in my previous turn. The conversation history shows we were investigating "
    "ModelError failures in logs within your working directory (D:\\AI) using read-only methods, specifically looking "
    "for keywords like Traceback or Exception.",
    "Here are businesses one person can start with my help. Tell me your skills and I'll narrow it down: 1. Freelance "
    "writing and editing (service): Write or polish blog posts, website text, product descriptions and newsletters "
    "for small businesses. 2. Graphic and logo design (service). 3. Social media management (service).",
    "Here are businesses tailored to your fixed hardware (RTX 5060 8GB, 48GB RAM) that require low startup costs: "
    "1. Local AI Automation Agency (Service).",
)

#: What each of the last four messages is about (the subject a relevant reply takes up).
MONEY_SUBJECT = "make money grow become billionaire 2030 ways business revenue company"


def subject_words(text: str) -> set[str]:
    from app.topics import _stem, content_words

    return {_stem(word) for word in content_words(text)}


def relevance(subject: str, reply: str) -> float:
    """Share of the subject's words that the reply takes up (0..1)."""
    wanted = subject_words(subject)
    if not wanted:
        return 0.0
    return round(len(wanted & subject_words(reply)) / len(wanted), 3)


def repetition(replies: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """The most alike pair of replies, and how many pairs are copies (alike >= 0.8)."""
    worst, pair, copies = 0.0, (0, 0), 0
    for left in range(len(replies)):
        for right in range(left + 1, len(replies)):
            score = alike(replies[left], replies[right])
            copies += int(score >= 0.8)
            if score > worst:
                worst, pair = score, (left, right)
    return {"max_alike": worst, "pair": pair, "copies": copies}


def score(replies: list[str] | tuple[str, ...], subject: str = MONEY_SUBJECT) -> dict[str, Any]:
    return {"relevance": [relevance(subject, reply) for reply in replies], **repetition(replies)}
