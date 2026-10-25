"""Turning verified claims into something understood.

The research pipeline is good at what it was built for: find pages, extract
factual claims, check each one against the page it came from, and refuse to
state anything that fails. That is where truthfulness comes from and none of
it changes here.

What it produced, though, was a list. Asked to explain taxation, tax evasion
and tax returns in detail, HAYAT came back with one bullet -- a 5% late-filing
penalty -- at 0.53 confidence, and filed that as having learned the topic.
Across sixty-eight stored entries the average was 1285 characters and 53 of
them sat below 0.6 confidence. It was collecting definitions and calling it
knowledge.

The difference between a definition and an understanding is three things, and
this builds all three on top of the claims rather than instead of them:

  the parts      -- a compound question is several questions, answered apart
  the mechanism  -- not what a thing is, but how it works and why it holds
  the gaps       -- what could not be answered, said out loud

That last one is what makes it honest rather than merely longer. A model asked
to "explain in detail" will always produce detail; what it will not do unless
asked is admit which parts it never found evidence for. An understanding that
names its own holes can be built on. One that papers over them cannot, and is
worse than the bullet list it replaced.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.base import Message
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


#: Compound questions, split on the words people actually join clauses with.
#: Deliberately conservative: splitting too eagerly turns one question into
#: three shallow ones, which is the failure this exists to fix.
_JOINERS = re.compile(r"(?i)\s*(?:,\s*|\band\b|\bversus\b|\bvs\.?\b|\balso\b|\bplus\b)\s*")

#: Words that mean the clause is a fragment rather than a question of its own.
_FRAGMENT = re.compile(r"(?i)^(?:the|a|an|in|on|of|for|to|it|its|their|how|why|what)?\s*$")

#: Below this, an answer is a guess with citations attached, and saying so
#: plainly matters more than the answer itself.
LOW_CONFIDENCE = 0.6

#: How many sub-questions are worth pursuing. Beyond this the question was
#: not compound, it was a list, and each part deserves its own request.
MAX_PARTS = 4


@dataclass
class Understanding:
    """What was actually understood, including what was not."""

    question: str
    parts: list[str] = field(default_factory=list)
    mechanism: str = ""
    relations: str = ""
    open_questions: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    claim_count: int = 0

    @property
    def shallow(self) -> bool:
        """Whether this is still a definition wearing a longer coat."""
        return not self.mechanism or self.claim_count < 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question, "parts": list(self.parts),
            "mechanism": self.mechanism, "relations": self.relations,
            "open_questions": list(self.open_questions),
            "contradictions": list(self.contradictions),
            "confidence": self.confidence, "claim_count": self.claim_count,
            "shallow": self.shallow,
        }

    def render(self) -> str:
        """The understanding as prose, with its limits at the top.

        At the top, not in a footnote. A caveat that arrives after the answer
        has already been read is decoration; one that arrives first changes
        how the answer is read, which is the entire point of having it.
        """
        lines: list[str] = []
        if self.confidence < LOW_CONFIDENCE:
            lines.append(
                f"Confidence is low ({self.confidence:.2f}). Treat this as a starting "
                f"point rather than an answer -- {self.claim_count} claim(s) survived "
                "verification, which is not enough to be sure."
            )
            lines.append("")
        if self.mechanism:
            lines.extend(["How it works:", self.mechanism, ""])
        if self.relations and len(self.parts) > 1:
            lines.extend(["How the parts fit together:", self.relations, ""])
        if self.contradictions:
            lines.append("Sources disagree on:")
            lines.extend(f"  - {item}" for item in self.contradictions)
            lines.append("")
        if self.open_questions:
            lines.append("Still unanswered:")
            lines.extend(f"  - {item}" for item in self.open_questions)
            lines.append("")
        return "\n".join(lines).rstrip()


def decompose(question: str) -> list[str]:
    """Split a compound question into the questions it actually asks.

    "explain me in detail about taxation and tax evasion and tax return" is
    three questions, and answering it as one is how two of them get dropped
    without anyone noticing.
    """
    value = " ".join(str(question or "").split())
    if not value:
        return []
    # Strip the request wrapper so the parts are subjects rather than verbs.
    body = re.sub(
        r"(?i)^(?:can you |could you |please )?"
        r"(?:explain|tell me|describe|teach me|compare|contrast|walk me through)"
        r"(?: me)?(?: in detail)?(?: about)?(?: how)?\s*", "", value).strip(" ?.")
    parts = [item.strip(" ?.") for item in _JOINERS.split(body) if item.strip()]
    parts = [item for item in parts if not _FRAGMENT.match(item) and len(item.split()) >= 1]
    # Two-word-or-shorter fragments are usually a split that went too far.
    parts = [item for item in parts if len(item) >= 4]
    if len(parts) < 2:
        return [value]
    return parts[:MAX_PARTS]


class Comprehension:
    """Builds an Understanding from claims that already passed verification."""

    def __init__(self, model: Any = None) -> None:
        self.model = model

    def build(self, question: str, claims: list[Any], sources: list[Any],
              confidence: float, *, use_model: bool = True) -> Understanding:
        """Explain the mechanism behind verified claims, and name the gaps.

        Grounded the same way the claim list is: the model is given only
        claims that survived citation checking and told not to add facts. What
        it is asked for is the connective tissue -- how and why -- which is
        exactly what a list of independently true sentences leaves out.
        """
        parts = decompose(question)
        texts = [str(getattr(claim, "text", claim)) for claim in claims]
        understanding = Understanding(
            question=question, parts=parts, confidence=float(confidence),
            claim_count=len(texts),
        )
        understanding.contradictions = [
            str(getattr(claim, "text", claim)) for claim in claims
            if str(getattr(getattr(claim, "status", ""), "value", "")) == "disputed"
        ]
        if not texts:
            understanding.open_questions = list(parts)
            return understanding
        if self.model is None or not use_model:
            understanding.open_questions = self._unanswered(parts, texts)
            return understanding

        data = self._ask(question, parts, texts)
        if data is None:
            understanding.open_questions = self._unanswered(parts, texts)
            return understanding

        understanding.mechanism = str(data.get("mechanism", "")).strip()
        understanding.relations = str(data.get("relations", "")).strip()
        stated = [str(item).strip() for item in data.get("open_questions", []) if str(item).strip()]
        # Union with what the claims plainly do not cover, so a model that
        # declines to admit a gap does not get to hide one.
        understanding.open_questions = _unique(stated + self._unanswered(parts, texts))
        return understanding

    def _ask(self, question: str, parts: list[str], texts: list[str]) -> dict[str, Any] | None:
        """Ask for the explanation, with and then without a schema.

        Schema-constrained decoding is not supported the same way by every
        backend, and when it silently returns nothing the result is an
        Understanding with no mechanism -- which is scored as shallow and
        dropped, so the whole layer appears to do nothing. Falling back to
        plain prose keeps the explanation even when the structure is refused.
        """
        try:
            response = self.model.generate([
                Message("system",
                        "You explain how things work using ONLY the verified claims given. "
                        "Never add a fact that is not in them. Your job is the connective "
                        "tissue the claims leave out: mechanism, cause, and how the parts "
                        "relate. If the claims do not support an explanation for one of "
                        "the sub-questions, list it under open_questions instead of "
                        "guessing. An admitted gap is more useful than a fluent invention."),
                Message("user", json.dumps({
                    "question": question,
                    "sub_questions": parts,
                    "verified_claims": texts[:20],
                }, default=str)),
            ], temperature=0.0, response_schema={
                "type": "object",
                "properties": {
                    "mechanism": {"type": "string"},
                    "relations": {"type": "string"},
                    "open_questions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["mechanism", "relations", "open_questions"],
                "additionalProperties": False,
            })
            data = json.loads(response.content)
            if str(data.get("mechanism", "")).strip():
                return data
        except Exception:
            log_swallowed(_log, "model comprehension of a source")

        # No structure, or structure with nothing in it. Ask again in prose.
        try:
            response = self.model.generate([
                Message("system",
                        "Explain how this works using ONLY the verified claims given. Never "
                        "add a fact that is not in them. Give the mechanism and the causes, "
                        "not restated definitions. Two short paragraphs at most."),
                Message("user", f"Question: {question}\nVerified claims:\n"
                                + "\n".join(f"- {item}" for item in texts[:20])),
            ], temperature=0.0)
            prose = str(response.content or "").strip()
        except Exception:
            return None
        return {"mechanism": prose, "relations": "", "open_questions": []} if prose else None

    @staticmethod
    def _unanswered(parts: list[str], claims: list[str]) -> list[str]:
        """Sub-questions no claim even mentions.

        Checked mechanically rather than asked about, because "did you cover
        everything?" is a question models answer optimistically.
        """
        if len(parts) < 2:
            return []
        haystack = " ".join(claims).lower()
        missing = []
        for part in parts:
            words = [word for word in re.findall(r"[a-z]{4,}", part.lower())]
            if words and not any(word in haystack for word in words):
                missing.append(part)
        return missing


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    kept: list[str] = []
    for item in items:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            kept.append(item)
    return kept
