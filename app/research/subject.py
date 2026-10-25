"""What to search for when the request only says "find it".

"just go all the way to the internet and search for references and complete
the whole research and find it" was searched word for word and came back
with advice about literature reviews: the subject -- the book Shams al-Ma'arif,
named two messages earlier -- was not in the query. A vague request is now
resolved against the conversation: first by the local model, which reads the
recent exchange, then by a rule that finds what was named.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from app.intent.live_web import clean_query
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

_GENERIC = frozenset("""
search searching find finding look looking research researching references reference internet web online whole
complete way some other go all the for it that this these those them and to a an of on in me you u can could
please just more again book books full entire or with from about by out up do did have has get got let lets
something anything everything thing things info information details yourself self one ways there here now
""".split())
_POINTING = re.compile(r"(?i)\b(?:it|that|this|them|those|the book|that book|the topic|the subject|the same|the whole research)\b")
_TITLED = re.compile(r"(?i)\b(?:named|called|titled)\s+[\"'“]?(?P<subject>[^\"'”?!.]{3,80})")
_NAMED = re.compile(r"(?i)\b(?:search(?:ing)? for|look(?:ing)? for|find|about)\s+"
                    r"[\"'“]?(?P<subject>[^\"'”?!.]{3,80})")
_AFFIRMATION = re.compile(r"(?i)^(?:yes|yeah|yep|ok(?:ay)?|sure|no|thanks?|thank you|hello|hi|hmm+)\b[.!?]*$")


def is_research_feedback(text):
    """Complaints about the search process are not subjects to search verbatim."""
    return bool(re.search(r"(?i)\b(?:you|u) (?:are|r|'re)?\s*(?:just |only )?(?:talking|going) (?:around|in circles)|"
                          r"\bwithout (?:actually )?(?:look(?:ing)?|search(?:ing)?)\b.{0,80}\b(?:web|internet|online|up)\b", str(text)))


def requested_level(text: str, default: str = "normal") -> str:
    """Honour a depth request without treating subjects like deep learning as commands."""
    if re.search(r"\b(?:quick|brief)\s+(?:search|research|overview)\b", text, re.I):
        return "quick"
    if re.search(r"\b(?:in[ -](?:depth|deep)|in detail|deep(?:er)?\s+(?:research|search|dive|look|analysis)|"
                 r"(?:research|search|look|dig)\b.{0,40}\b(?:deeper|deeply|thoroughly)|"
                 r"(?:look|dig)\s+deep\s+(?:into|in|at)|"
                 r"(?:everything|all)\s+about)\b", text, re.I):
        return "deep"
    return default


def content_words(text: str) -> list[str]:
    return [word for word in re.findall(r"[^\W\d_]{3,}", text.lower()) if word not in _GENERIC]


def is_vague(query: str) -> bool:
    words = content_words(query)
    return len(words) < 2 or (bool(_POINTING.search(query)) and len(words) < 4)


def subject_from_history(history: list[dict[str, Any]], current: str = "", *, research_only: bool = False) -> str:
    """The most recent thing Mohsin named, read from his own messages."""
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        text = " ".join(str(item.get("content", "")).split())
        if not text or text == current or _AFFIRMATION.match(text):
            continue
        if is_research_feedback(text):
            continue
        if research_only and not re.search(r"(?i)\b(?:search|research|look up|browse)\b", text):
            continue
        titled = _TITLED.search(text)
        if titled and content_words(titled.group("subject")):
            return titled.group("subject").strip(" ,;:")
        named = next((match.group("subject").strip() for match in _NAMED.finditer(text)
                      if not is_vague(match.group("subject"))), None)
        if named:
            return named.strip(" ,;:")
        query = clean_query(text)
        if not is_vague(query) and len(query.split()) <= 12:
            return query
    return ""


def resolve_query(text: str, history: list[dict[str, Any]] | None = None,
                  rewrite: Callable[[str], str] | None = None) -> str:
    """A standalone search query for ``text``, using the conversation when it is vague."""
    history = history or []
    if is_research_feedback(text):
        return subject_from_history(history, text, research_only=True)
    base = clean_query(text) or text.strip(" .!?")
    if not is_vague(base):
        return base
    if rewrite is not None and history:
        transcript = "\n".join(f"{item.get('role')}: {str(item.get('content', ''))[:300]}" for item in history[-8:])
        prompt = ("From this conversation, write the one web search query that finds what the user wants in their "
                  "last message. Name the subject explicitly. Reply with the query only, no quotes, under 12 words.\n\n"
                  f"{transcript}\nuser: {text}")
        try:
            candidate = " ".join(str(rewrite(prompt) or "").split()).strip(" \"'.")
            if 3 <= len(candidate) <= 120 and not is_vague(candidate):
                return candidate
        except Exception:
            log_swallowed(_log, "rewriting a vague research query")
    return subject_from_history(history, text) or base


#: Words that say nothing about what a question is about. A claim that shares
#: only these with the question is not evidence for it.
_RELEVANCE_NOISE = _GENERIC | frozenset("""
what which when where why how who whom whose are was were been being does doing done will would should could might
must shall than then into onto over under very much many most more less least such only also just even still best
good great nice cool okay common practices practice limitations trade offs tradeoffs failure failures modes mode
diagnostic signals internally mechanisms matter work works working today learn learned learning required stuff
""".split())


def _stems(text: str) -> set[str]:
    return {word[:5] for word in re.findall(r"[a-z]{3,}", text.lower()) if word not in _RELEVANCE_NOISE}


def relevant_to_question(question: str, claim: str) -> bool:
    """Whether a claim shares at least one meaningful word stem with the question.

    The background learner stored "The best ships for mining are the Python or
    Type-9" (a video game) as knowledge about the question "money required
    elite": the claim was backed by its source, but it was not about the
    question. Only Latin-script questions are judged; a question in Arabic
    answered from English sources is left alone rather than emptied.
    """
    letters = re.findall(r"[^\W\d_]", question)
    if not letters or sum(char.isascii() for char in letters) / len(letters) < 0.9:
        return True
    subject = _stems(question)
    if len(subject) < 1:
        return True
    # A relation question needs its named subject, not merely the relation's
    # words. A "Capital City Quiz" project is not evidence for Australia's
    # capital just because it shares "capital" and "city" with the request.
    relation = re.search(r"(?i)\b(?:capital(?: city)?|population|currency|official language)\s+of\s+(.+?)(?:[?!.]|$)", question)
    if relation:
        anchor = _stems(relation[1])
        if anchor and not anchor.intersection(_stems(claim)):
            return False
        if re.search(r"(?i)\bcapital(?: city)?\s+of\b", question) and not re.search(r"(?i)\bcapital(?:s)?\b|\bseat of (?:the )?(?:national )?government\b", claim):
            return False
    return bool(subject & _stems(claim))


_COPULA = re.compile(r"(?i)\b(?:is|was|means|denotes)\b")
_REPORTED_BELIEF = re.compile(
    r"(?i)\b(?:guess(?:es|ed)?|assum(?:e|es|ed|ing)|suppos(?:e|es|ed)|think|thinks|thought|"
    r"believ(?:e|es|ed|ing)|mistak(?:e|es|en|enly)|misconception|myth|wrongly|incorrectly|"
    r"confus(?:e|es|ed)|rumou?r(?:s|ed)?|allegedly|claim(?:s|ed)?\s+that)\b"
)


def direct_answer_score(question: str, claim: str) -> int:
    """Prefer a stated answer to a short identity/definition question.

    This ranks source text; it never generates a fact or raises confidence.
    "Canberra is the national capital" answers a capital question more directly
    than a historical paragraph that happens to repeat its subject words.
    """
    if not re.match(r"(?i)^\s*(?:what|who)(?:\s+is|'s)\b", question):
        return 0
    wanted = _stems(question)
    copula = _COPULA.search(claim)
    if copula is None:
        return 0
    head = claim[:copula.start()]
    # "Many travellers guess that Sydney is Australia's capital" repeats the
    # question words after "is" but reports a belief, not the answer. On
    # 2026-10-24 it outranked "The capital city of Australia is Canberra".
    if _REPORTED_BELIEF.search(head):
        return -1
    # The question's subject named before the copula states the answer:
    # "The capital city of Australia is Canberra".
    if len(head.split()) <= 12 and len(wanted & _stems(head)) >= min(2, len(wanted)):
        return 1
    for match in re.finditer(r"(?i)\b(?:is|means|denotes)\s+(?:(?:also|currently|the|a|an|national|official)\s+){0,4}(\w+)", claim):
        if wanted & _stems(match[1]):
            return 1
    return 0
