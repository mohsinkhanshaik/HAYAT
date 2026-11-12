"""Parse explicit response contracts without tying them to any particular fact."""
from dataclasses import dataclass
import re


_NUMBERS = dict(zip(('one two three four five six seven eight nine ten eleven twelve thirteen fourteen '
                     'fifteen sixteen seventeen eighteen nineteen twenty').split(), range(1, 21)))
_COUNTS = r'\d{1,3}|' + '|'.join(_NUMBERS)
_WORDS = re.compile(r'(?i)\b(?P<mode>in exactly|exactly|in under|under|less than|fewer than|at most|'
                    r'no more than|within|maximum(?: of)?|max|in)\s+(?P<count>' + _COUNTS + r')\s+words?\b')
_SENTENCES = re.compile(r'(?i)(?:\b(?:in|using|with)\s+(?:exactly\s+|only\s+|just\s+)?|'
                        r'(?:^|[.!?]\s*)(?:exactly\s+|only\s+|just\s+)?)'
                        r'(?P<count>' + _COUNTS + r')\s+sentences?\b')


def sentence_constraint(text: str) -> int | None:
    match = _SENTENCES.search(' '.join(str(text or '').split()))
    if not match:
        return None
    raw = match['count'].casefold()
    count = int(raw) if raw.isdigit() else _NUMBERS[raw]
    return count if 1 <= count <= 20 else None


@dataclass(frozen=True)
class WordConstraint:
    maximum: int
    exact: bool = False


def word_constraint(text: str) -> WordConstraint | None:
    match = _WORDS.search(' '.join(str(text or '').split()))
    if not match:
        return None
    count = match['count'].casefold()
    number = int(count) if count.isdigit() else _NUMBERS[count]
    mode = match['mode'].casefold()
    if mode in {'in under', 'under', 'less than', 'fewer than'}:
        number -= 1
    return WordConstraint(max(0, number), 'exactly' in mode)


def word_count(text: str) -> int:
    return len(re.findall(r"[\w’'-]+", text))


def binary_only(text: str) -> bool:
    """An explicit yes/no-only answer, without a requested explanation."""
    return bool(re.search(r'(?i)\b(?:answer|reply|respond)(?:\s+with)?\s+(?:only\s+)?yes(?:\s+or\s+|/)no\b', text)
                and not re.search(r'(?i)\b(?:why|explain|reason|justify|elaborate|because)\b', text))


def compact_binary_answer(answer: str, question: str) -> str:
    if not binary_only(question):
        return answer
    constraint = word_constraint(question)
    if constraint is not None and constraint.exact and constraint.maximum != 1:
        return answer
    match = re.match(r'(?i)^\W*(yes|no)\b', answer)
    if not match:
        return answer
    # A draft that contradicts its leading polarity needs reasoning, not clipping.
    opposite = 'no' if match[1].casefold() == 'yes' else 'yes'
    tail = answer[match.end():]
    if (re.search(r'(?i)\b' + opposite + r'\b', tail)
            or re.search(r"(?i)\b(?:but|unless|except|however|depends|if|provided|assuming|uncertain|may|might|could|cannot|can't)\b", tail)):
        return answer
    return match[1].capitalize() + '.'
