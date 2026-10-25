"""Strict matching for answers prepared before a question is asked.

Semantic resemblance is useful for search, but it is not enough to decide
that a prefetched answer answers the question now on screen.  This matcher
requires the same subject and a compatible question facet (mechanism,
failure, comparison, freshness, and so on).  It is deterministic, local, and
fast enough to run before every question.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.linguistics import expand_query


_NOISE = frozenset("""
a an the and or but of to for in on at by with from into about this that these
those it its is are was were be been being do does did can could should would
will may might explain tell show give please me we you your how why what when
where which who actually common best practices practice limitation limitations
tradeoff tradeoffs trade-offs failure failures modes signals diagnostic use
using work works working happen happens question answer expected follow up
occur occurs occurred diagnose diagnosed diagnosing them
""".split())

_FACETS: dict[str, re.Pattern[str]] = {
    "mechanism": re.compile(r"(?i)\b(?:how|mechanism|internals?|architecture|work(?:s|ing)?)\b"),
    "cause": re.compile(r"(?i)\b(?:why|cause[sd]?|reason|because)\b"),
    "comparison": re.compile(r"(?i)\b(?:compare|versus|vs\.?|difference|better than)\b"),
    "failure": re.compile(r"(?i)\b(?:fail|failure|error|bug|wrong|diagnos|troubleshoot|fix)\w*\b"),
    "recommendation": re.compile(r"(?i)\b(?:best practice|recommend|should|choose|trade-?offs?|when to use)\b"),
    "definition": re.compile(r"(?i)\b(?:what is|what are|define|meaning|means)\b"),
    "freshness": re.compile(r"(?i)\b(?:latest|current|today|now|version|release)\b"),
    "security": re.compile(r"(?i)\b(?:secure|security|attack|hack|threat|vulnerab)\w*\b"),
    "performance": re.compile(r"(?i)\b(?:fast|faster|slow|latency|throughput|performance|optim)\w*\b"),
    "requirements": re.compile(r"(?i)\b(?:require(?:s|d|ments?)?|must|shall|rule|conform|compliance)\w*\b"),
}


@dataclass(frozen=True, slots=True)
class QuestionMatch:
    same: bool
    score: float
    subject_overlap: float
    containment: float
    expected_facets: tuple[str, ...]
    actual_facets: tuple[str, ...]
    reason: str


def subject_tokens(text: str) -> set[str]:
    expanded = expand_query(text, maximum_concepts=4).text.casefold()
    return {
        token for token in re.findall(r"[a-z0-9][a-z0-9+#.-]{1,}", expanded)
        if token not in _NOISE and len(token) >= 2
    }


def question_facets(text: str) -> set[str]:
    value = str(text or "")
    return {name for name, pattern in _FACETS.items() if pattern.search(value)}


def _constraints(text: str) -> tuple:
    """Do not let topic similarity erase values, polarity or conditions.

    This is deliberately conservative: a missed cache hit costs generation,
    whereas reusing an answer to the opposite question can mislead the user.
    Keep number order, signs, units and operators, including single digits.
    """
    value = str(text or "").casefold().replace("’", "'").replace("−", "-")
    numbers = tuple(re.findall(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?", value))
    polarity = tuple(re.findall(r"\b(?:not|no|never|without|unless|cannot)\b|\b\w+n't\b", value))
    conditions = tuple(re.findall(
        r"\b(?:and|or|if|before|after|above|below|under|over|between|within|at least|at most|"
        r"more than|less than|greater than|fewer than|exactly|only|except|excluding|including|"
        r"all|any|every|some|none|always|sometimes|ascending|descending|"
        r"one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|billion)\b", value))
    symbols = tuple(re.findall(r"<=|>=|!=|==|[<>=%+*/^$£€₹]", value))
    units = tuple(re.findall(r"\b(?:mm|cm|km|m|mg|kg|g|ms|seconds?|minutes?|hours?|days?|"
                            r"weeks?|months?|years?|meters?|metres?|feet|inches|miles|"
                            r"celsius|fahrenheit|usd|eur|gbp|percent)\b", value))
    literals = tuple(re.findall(r'"([^"\n]+)"', str(text or "")))
    # Yes/no propositions can reverse subject and object without changing a
    # bag of words. Require their content order to remain unchanged.
    proposition = ()
    if re.match(r"^(?:is|are|was|were|do|does|did|can|could|should|would|will|may|must)\b", value.strip()):
        proposition = tuple(token for token in re.findall(r"\b\w+\b", value) if token not in _NOISE)
    return numbers, polarity, conditions, symbols, units, literals, proposition


def compare_questions(expected: str, actual: str) -> QuestionMatch:
    """Decide whether an anticipated question can answer the actual one."""
    left, right = subject_tokens(expected), subject_tokens(actual)
    shared = left & right
    maximum = max(1, len(left), len(right))
    minimum = max(1, min(len(left), len(right)))
    subject_overlap = len(shared) / maximum
    containment = len(shared) / minimum
    expected_facets = question_facets(expected)
    actual_facets = question_facets(actual)
    facets_compatible = not expected_facets or not actual_facets or bool(expected_facets & actual_facets)
    enough_subject = len(shared) >= 2 and containment >= 0.75 and subject_overlap >= 0.55
    constraints_compatible = _constraints(expected) == _constraints(actual)
    same = enough_subject and facets_compatible and constraints_compatible
    score = subject_overlap * 0.70 + containment * 0.20 + (0.10 if facets_compatible else 0.0)
    if not constraints_compatible:
        reason = "different values, polarity or conditions"
    elif not enough_subject:
        reason = "different subject"
    elif not facets_compatible:
        reason = "same subject but a different question"
    else:
        reason = "same subject and compatible question"
    return QuestionMatch(
        same, round(score, 4), round(subject_overlap, 4), round(containment, 4),
        tuple(sorted(expected_facets)), tuple(sorted(actual_facets)), reason,
    )
