"""Exact answers for questions that end with an instruction about the answer's form.

The calculators, the calendar and the letter counter accept a whole request only, and
decline anything they do not fully understand -- deliberately, so a guess never passes
as a calculation. "Compute 82 x 13 - 15 x 6. Reply with only the final answer." was
therefore declined because of its last sentence and went to the language model, which
got it wrong. Measured on the held-out set (2026-10-31): arithmetic 0/5, weekdays and
day counts 1/5, letters 2/5, every one of them a question these tools answer exactly.
Worse, "Write the word 'mississippi' backwards" was routed as a computer action.

Here the trailing instruction is taken off, the exact tools answer the question itself,
and the instruction is then honoured: "only the final answer" gets the bare value, and a
requested confidence line is given (100% only because the value was computed, not guessed).
"""
from __future__ import annotations

import re

#: A message that opens by saying how to answer ("Reply with exactly one word: ...") is a question.
#: "Reply to the landlord's message" names no form and stays an action.
ANSWER_FORM_LEAD = re.compile(
    r"(?i)^(?:please\s+)?(?:(?:reply|respond|answer)(?:\s+me)?|write\s+(?:the|your)\s+answer|give\s+(?:me\s+)?(?:the|your)\s+answer"
    r"|tell\s+me)\s+(?:with|in|using)\s+(?:exactly\s+|only\s+|just\s+|at most\s+)?(?:one|1|a single|two|2|three|3|\d+)\s+"
    r"(?:word|words|sentence|sentences|number|numbers|letter|letters|line|lines)\b"
    r"|^(?:in\s+(?:one|a single|1)\s+word)\s*[,:]")

#: Trailing sentences that say how to answer, not what. Removed one at a time from the end.
_FORM_TAIL = re.compile(
    r"(?is)(?:^|(?<=[.?!])|\s)\s*(?:"
    r"(?:please\s+)?(?:reply|respond|answer)\s+(?:with\s+)?(?:only|just)\s+(?:with\s+)?(?:the\s+)?(?:final\s+)?"
    r"(?:answer|number|result|value|word|day)(?:\s+please)?"
    r"|(?:just|only)\s+(?:give\s+(?:me\s+)?|say\s+|write\s+)?(?:the\s+)?(?:final\s+)?(?:answer|number|result|value)"
    r"(?:\s*,?\s*(?:please|nothing else))?"
    r"|(?:no|without)\s+(?:explanation|working|steps)s?"
    r"|count the days between them(?:\s*\([^)]{0,80}\))?"
    r"|after your answer, add one last line:\s*confidence:[^\n]*"
    r")\s*[.!]?\s*$")
_BARE = re.compile(r"(?i)\b(?:only|just)\b[^.?!]{0,40}\b(?:answer|number|result|value|word|day)\b|"
                   r"\b(?:no|without)\s+(?:explanation|working|steps)")
_CONFIDENCE = re.compile(r"(?i)add one last line:\s*confidence")


def split_form(text: str) -> tuple[str, bool, bool]:
    """(the question itself, wants only the bare answer, asks for a confidence line)."""
    core = str(text or "").strip()
    removed = []
    while True:
        found = _FORM_TAIL.search(core)
        if not found or not core[:found.start()].strip():
            break  # nothing more to remove, or the whole message is the instruction
        removed.append(found.group(0))
        core = core[:found.start()].rstrip()
    tails = " ".join(removed)
    return core, bool(_BARE.search(tails)), bool(_CONFIDENCE.search(tails))


#: How each exact tool states its value, to keep only the value when asked to.
_VALUE = (
    re.compile(r"=\s*(.+?)\.?\s*$"),
    re.compile(r"\b(?:was|is|will be) an? (\w+day)\b", re.I),
    re.compile(r"^(-?[\d,]+(?:\.\d+)?) days?\b", re.I),
    re.compile(r"appears (\d+) times?", re.I),
    re.compile(r"backwards is [“\"'](.+?)[”\"']", re.I),
)


def bare_value(answer: str) -> str:
    """The value an exact answer states, without its working ("82 * 13 - 15 * 6 = 976." -> "976")."""
    first = str(answer or "").strip().splitlines()[0] if str(answer or "").strip() else ""
    for pattern in _VALUE:
        found = pattern.search(first)
        if found:
            return found.group(1).strip()
    return first


#: Misspellings of the few words the exact tools key on ("wat is 25 percnt of 240").
_TYPOS = {"wat": "what", "wht": "what", "whta": "what", "percnt": "percent", "percet": "percent",
          "precent": "percent", "pecent": "percent", "persent": "percent", "percen": "percent",
          "multipy": "multiply", "mutliply": "multiply", "devide": "divide", "divde": "divide",
          "squre": "square", "sqaure": "square", "rooot": "root"}


def _untypo(text: str) -> str:
    return re.sub(r"[A-Za-z]+", lambda m: _TYPOS.get(m.group(0).lower(), m.group(0)), text)


def exact_local_answer(text: str) -> str | None:
    """A computed answer to the question inside ``text``, in the form it asked for; None if none applies."""
    from app.calculation import calculation_answer
    from app.conversions import convert_answer
    from app.dates import date_answer, letter_count_answer
    from app.exact_answers import exact_answer

    value = str(text or "")
    core, bare, confidence = split_form(value)
    for candidate in dict.fromkeys((value, core, _untypo(core))):
        answer = (calculation_answer(candidate) or convert_answer(candidate) or date_answer(candidate)
                  or letter_count_answer(candidate) or exact_answer(candidate))
        if answer is not None:
            if candidate is value and core == value:
                return answer
            result = bare_value(answer) if bare else answer
            return result + ("\nConfidence: 100%" if confidence else "")
    return None
