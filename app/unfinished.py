"""A message that stops halfway: ask how it ends, then carry on with the whole sentence.

The owner types fast and sometimes sends before finishing ("find me the", "compare bitcoin and", "what's
better, a laptop or"). Read as a whole request, "find me the" sent HAYAT searching for "the". A person would ask
"the what?" and then hear the rest as part of the same sentence. That is what this does:

* :func:`unfinished` -- the message ends on a word that cannot end a sentence: an article or possessive ("the",
  "my", "your"), a joining word ("and", "or", "but", "because", "than", "vs"), or "a"/"an" right after a verb or
  preposition ("find me a", "write it for an"). "Vitamin A", "plan a" and "what is this for" are left alone.
* :func:`ending_question` -- the short question that asks for the rest.
* :func:`combine` -- the next message joined onto the start; if the owner retyped the whole sentence, theirs is
  used as typed; "never mind" drops it.

Nothing here runs a tool: it only decides that a message should wait for the rest of the sentence.
"""
from __future__ import annotations

import re

#: How long a half-sentence waits for its ending before the next message is read on its own.
FRESH_SECONDS = 600

#: Words that never end a finished sentence.
_DANGLING = frozenset("the my your our their its and or but because than vs versus".split())
#: "a"/"an" ends a sentence after a noun ("vitamin a", "plan a") but not after these.
_BEFORE_ARTICLE = frozenset(
    "find get make write buy need want show give send book order build create draw generate pick choose "
    "recommend suggest is was be me us him for with about of in on into like as from at by to it's "
    "there's what's".split())
_WORDS = re.compile(r"[a-z']+")
_CANCEL = re.compile(r"(?i)^\s*(?:never\s*mind|nevermind|nvm|cancel|forget\s+(?:it|that)|stop|scratch\s+that|ignore\s+(?:it|that))"
                     r"\s*[.!]*\s*$")


def _words(text: str) -> list[str]:
    return _WORDS.findall(str(text or "").lower().replace("’", "'"))


def _tokens(text: str) -> list[str]:
    """Whitespace-separated pieces with their punctuation trimmed, numbers kept ("9.9", "30")."""
    pieces = (piece.strip(".,!?;:…()[]{}%$*-") for piece in str(text or "").replace("’", "'").split())
    return [piece for piece in pieces if piece]


def unfinished(text: str) -> bool:
    """Whether the message stops on a word no finished sentence ends with."""
    value = str(text or "").strip()
    if not value or re.search(r"[\"“”`]", value):
        return False  # quoted content is reproduced as written
    tokens = _tokens(value)
    if len(tokens) < 2:
        return False  # "and?" on its own means "go on", not half a request
    last = tokens[-1]
    if not re.fullmatch(r"[A-Za-z']+", last):
        return False  # "9.11 or 9.9?", "less than 30%": it ends on a number, so it is finished
    lower = last.lower()
    if lower in _DANGLING:
        return True
    # "about A", "option A": a capital A after the first word is a label, not the article.
    return lower in {"a", "an"} and last != "A" and tokens[-2].lower() in _BEFORE_ARTICLE


def ending_question(text: str) -> str:
    """'…the what?' with the owner's own last words, so it is clear which message is meant."""
    value = str(text or "").strip().rstrip(".…!?, ")
    tail = " ".join(value.split()[-5:])
    last = _words(value)[-1] if _words(value) else ""
    ask = "…because of what?" if last == "because" else f"…{last} what?"
    return (f"Your message stopped at “{tail}” {ask} Send the rest and I'll read it together with the start. "
            "(Or say “never mind”.)")


def combine(start: str, follow_up: str) -> str | None:
    """The whole sentence: the start plus the rest; the follow-up alone if it was retyped; None if dropped."""
    rest = str(follow_up or "").strip()
    if _CANCEL.match(rest):
        return None
    begin = str(start or "").strip().rstrip(".…!?, ")
    head = _words(begin)[:-1]
    typed = _words(rest)
    if head and typed[: len(head)] == head:
        return rest  # the owner sent the whole sentence again
    return f"{begin} {rest.lstrip('.… ')}".strip()
