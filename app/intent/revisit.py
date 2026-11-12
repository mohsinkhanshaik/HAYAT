"""Questions about HAYAT's own last answer: how it was reached, do it again better, look it up properly.

"how did you come to this conclusion so make it again but better look up" was researched word for word: the
search subject was the owner's sentence about the answer, not the question the answer was about. Such a message
is about the conversation, so it is read here before any search or computer-control pattern sees it:

* **explain** -- how did you get that / why do you say that / what is your source / are you sure;
* **redo** -- do it again / make it better / redo that / try again but better;
* **research** -- look it up / check it / verify it / search properly / with sources.

The answer to "explain" comes only from what was recorded (the answer's provenance, the research audit and the
steps taken); HAYAT never invents a line of reasoning after the fact. A redo with research searches the
*original* question, deeper.
"""
from __future__ import annotations

import re
from typing import Any

_YOU = r"(?:you|u|ya|hayat)"
_THAT = r"(?:this|that|the|your|ur|these|those|such an?|an?)"
_RESULT = (r"(?:conclusion|answer|result|reply|response|number|figure|estimate|idea|claim|opinion|verdict|decision|"
           r"recommendation|explanation|information|info|fact|facts|statement|point|it|that|this|so|there)")

EXPLAIN = re.compile(
    # The verb must point at an answer ("how do you get better" asks for a self-review, not an explanation).
    rf"(?i)\bhow\s+(?:did|do|have|could)\s+{_YOU}\s+(?:come\s+up\s+with|come\s+to|reach(?:ed)?|arrive(?:d)?\s+at|get(?:\s+to)?|"
    rf"got(?:\s+to)?|work(?:ed)?|figure(?:d)?|decide(?:d)?|conclude(?:d)?|know|knew|find(?:\s+out)?|calculate(?:d)?|"
    rf"determine(?:d)?)\s+(?:this|that|the|your|ur|such|those|these|my|our|it|so|there)\b"
    rf"|\bhow\s+(?:do|did)\s+{_YOU}\s+know\s*[?.!]*$"
    rf"|\bwhy\s+(?:did|do|would)\s+{_YOU}\s+(?:say|think|conclude|answer|believe|suggest|claim|pick|choose)\b"
    rf"|\bwhat\s+(?:is|was|were|are)\s+(?:your|ur)\s+(?:source|sources|basis|reasoning|evidence|proof|logic|method)\b"
    rf"|\bwhat\s+(?:is|was|were|are)\s+the\s+(?:source|sources|basis|reasoning|evidence|proof)\s+(?:for|of|behind)\s+"
    rf"(?:that|this|it|your|ur)\b"
    rf"|\bwhere\s+did\s+{_YOU}\s+(?:get|find|read|hear|learn)\s+(?:that|this|it|those|these|the)\b"
    rf"|\b(?:explain|show|tell\s+me)\s+(?:me\s+)?how\s+{_YOU}\s+(?:got|reached|came|arrived|decided|worked|figured|know)\b"
    rf"|\b(?:how|what)\s+(?:makes|made)\s+{_YOU}\s+(?:say|think|sure|conclude)\b"
    rf"|\b(?:are|r)\s+{_YOU}\s+(?:really\s+)?sure\b|\bhow\s+sure\s+(?:are|r)\s+{_YOU}\b"
    rf"|\bon\s+what\s+basis\b|\bbased\s+on\s+what\b|\bwhat\s+(?:is|was)\s+(?:that|this|it)\s+based\s+on\b"
    rf"|\b(?:cite|show)\s+(?:me\s+)?(?:your|the)\s+sources?\b")
_AGAIN = re.compile(r"(?i)\b(?:make|do|write|answer|try|give|say|explain|work)\s+"
                    r"(?:it|that|this|the\s+answer|your\s+answer|ur\s+answer)?\s*(?:again|over|once\s+more)\b|\bredo\b")
#: "do it again" alone may repeat an action; it is about the answer only when it asks for better or names the answer.
_BETTER = re.compile(r"(?i)\b(?:better|properly|correctly|more\s+(?:carefully|accurate(?:ly)?|detailed|thorough(?:ly)?))\b"
                     r"|\b(?:the|your|ur|that|this)\s+answer\b")
REDO = re.compile(
    r"(?i)\b(?:make|do)\s+(?:it|that|this)\s+(?:better|properly|right|correctly|more\s+accurate(?:ly)?)\b"
    r"|\b(?:a\s+)?better\s+(?:answer|version|job)\b"
    r"|\bimprove\s+(?:it|that|this|the\s+answer|your\s+answer)\b"
    r"|\bagain\s+but\s+better\b")
RESEARCH = re.compile(
    r"(?i)\blook(?:\s+(?:it|that|this|them))?\s+up\b|\blook\s+(?:it|that|this)\s+(?:over|into)\b"
    r"|\b(?:search|check|verify|research|google|fact[- ]check|double[- ]check)\b"
    r"(?:\s+(?:it|that|this|again|properly|online|the\s+web|the\s+internet|sources))?"
    r"|\bwith\s+(?:real\s+)?(?:sources|references|citations|evidence)\b|\bfind\s+(?:real\s+)?sources\b")
#: A message that names a new task is not about the last answer ("redo the kitchen layout plan" names something).
_META = frozenset("""
how did do does you your ur u come came to this that the conclusion answer result reply response so make made it
again but better look up please can could would and then now also again just properly right correctly why say said
think what is was were source sources basis reasoning evidence proof where get got from find found explain show me
tell reach reached arrive arrived at decide decided know knew check verify search research google redo improve a an
one version job sure are really on based with real references citations of for this time over once more try give
figure figured out work worked conclude concluded number estimate idea claim opinion verdict decision information
info fact facts statement point bro hey hayat okay ok yeah yes no not online web internet into double fact-check
double-check calculate calculated determine determined there such learn learned read heard hear these those them
recommendation explanation suggest suggested believe pick picked choose chose method logic cite which wrong right
""".split())


def parse(text: str) -> dict[str, bool] | None:
    """The parts of a message about the last answer, or None when it is about something else."""
    value = " ".join(str(text or "").split())
    if not value or len(value) > 300:
        return None
    explain = bool(EXPLAIN.search(value))
    redo = bool(REDO.search(value) or (_AGAIN.search(value) and _BETTER.search(value)))
    research = bool(RESEARCH.search(value))
    if not explain and not redo:
        return None
    words = [word for word in re.findall(r"[^\W\d_]+(?:[-'][^\W\d_]+)?", value.lower()) if word not in _META]
    if len(words) > (3 if explain else 1):
        return None  # It names its own subject ("redo the kitchen layout plan"): an ordinary request.
    return {"explain": explain, "redo": redo, "research": research}


def original_question(history: list[dict[str, Any]], current: str = "") -> tuple[str, str]:
    """(the owner's question the last answer was about, that answer), skipping earlier revisit messages."""
    answer = ""
    for item in reversed(history or []):
        role, content = item.get("role"), " ".join(str(item.get("content") or "").split())
        if not content:
            continue
        if role == "assistant" and not answer:
            answer = content
            continue
        if role != "user" or content == current or parse(content) is not None:
            continue
        if answer:
            return content, answer
    return "", answer


def related(query: str, question: str) -> bool:
    """Whether a recorded search was about this question (shared content words)."""
    def words(text):
        return {word for word in re.findall(r"[^\W\d_]{3,}", str(text).lower()) if word not in _META}
    asked, searched = words(question), words(query)
    return bool(asked and searched) and len(asked & searched) >= max(1, min(len(asked), len(searched)) // 2)
