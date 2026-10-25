"""Built-in language courses. Each is fixed, checked content -- never generated."""
from __future__ import annotations

import re

from app.learning.courses import arabic

COURSES = {arabic.COURSE["course_id"]: arabic}

#: Words that name a course, including the language's own name for itself.
ALIASES = {
    "arabic": "arabic", "arabi": "arabic", "arbi": "arabic", "arabic language": "arabic",
    "arabic alphabet": "arabic", "the arabic alphabet": "arabic", "arabic letters": "arabic",
    "modern standard arabic": "arabic", "msa": "arabic", "العربية": "arabic", "عربي": "arabic",
}


def course_for(text: str) -> str | None:
    """The course a phrase names, or None. 'teach me arabic' -> 'arabic'."""
    words = re.sub(r"[^\w\s؀-ۿ]", " ", text.lower())
    words = " ".join(words.split())
    for alias in sorted(ALIASES, key=len, reverse=True):
        if re.search(rf"(?<![\w؀-ۿ]){re.escape(alias)}(?![\w؀-ۿ])", words):
            return ALIASES[alias]
    return None
