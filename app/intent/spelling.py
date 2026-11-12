"""Reading the owner's spelling the way a person would.

The owner types fast and phonetically: "imprve", "reserach", "invnetion", "componenets", "halluination", "rember",
"sistr", "mke", "homonyps". The router's patterns, memory recall's word matching and research's relevance checks
all compare words exactly, so each slip could send a request down the wrong path or miss the memory it was about.
``app/intent/typos.py`` repairs about ninety tool words, and only when that is what makes a tool match.

:func:`correct` repairs any misspelt word, conservatively:

* A word is changed only when it is not a word at all: not in the English dictionary (``pyspellchecker``, MIT,
  160,000 words with frequencies), not one of HAYAT's own words (its name, tool and capability words), and not a
  word the owner keeps using (:func:`learn_owner_words`; "mohsin", "jaadoo").
* Candidates come from one or two edits. Two kinds of slip are favoured because they are how fast typing goes
  wrong: letters left out ("rember" is "remember" with "em" dropped, "mke" is "make") and two letters swapped. A
  common word beats a rare one, and HAYAT's own words beat both, so "routr" is "router", not "route".
* The best candidate must clearly win over the next one, or the word is left as typed.
* Never touched: text after a dictation marker ("write a note saying ..."), anything quoted, a capitalised name
  inside a sentence, acronyms, words with digits, paths, links and email addresses, and words under three letters.

Every change is returned (``Spelling.changes``), so the router can record how it read the request. Nothing here
executes or authorises anything; it only changes how a request is read.
"""
from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

#: HAYAT's own words that the English dictionary lacks or ranks low. Tool and capability words are added at runtime.
HAYAT_WORDS = frozenset(
    "hayat gui csi ocr tor onion atlas neuron neurons brain codex api ui wifi router routers modem gateway ollama "
    "webcam screenshot screenshots chatbot app apps login logout signup inbox email emails url urls pdf docx xlsx "
    "pptx json csv yaml github linkedin youtube whatsapp telegram bluesky mastodon threads instagram tiktok bitcoin "
    "ethereum crypto defi nft blockchain gps ipv4 ipv6 vpn dns ssid bssid anpr alpr osint uncensored notepad "
    # Tech words the dictionary lacks or would "correct" ("tech" -> "teach", "wasm" -> "was").
    "tech wasm repo repos config configs async regex sdk cli npm pip venv cuda rocm llm llms rag mcp tts stt gpu gpus "
    "vram kubernetes docker pytorch tensorflow numpy pandas json html css javascript typescript golang oauth jwt "
    "ssh ssl tls http https localhost frontend backend devops saas fintech edtech startup startups nlp ai ml "
    "dataset datasets codebase webhook webhooks plugin plugins inference finetune finetuning tokenizer embeddings "
    "chatgpt openai anthropic claude gemini llama qwen mistral deepseek huggingface ollama runtime runtimes "
    # Brands and services typed in lower case: "on venmo" is not "on venom".
    "venmo paypal zelle cashapp revolut gpay phonepe paytm upi binance coinbase kraken robinhood stripe wise "
    "spotify netflix uber lyft airbnb etsy shopify ebay alibaba aliexpress flipkart swiggy zomato tiktok snapchat "
    "reddit discord twitch pinterest gmail outlook hotmail icloud dropbox onedrive gdrive canva figma notion slack "
    "zoom teams skype signal viber wechat line imo truecaller jio airtel vodafone".split())

#: Marks after which the rest is content to reproduce exactly (same idea as app/intent/normalize._DICTATION).
_DICTATION = re.compile(r"(?i)\b(?:saying|says|say|type|typing|write|writing|note that|message|text|content|"
                        r"titled|named|called)\b\s*[:\-\"']?\s*")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']{2,}'|`[^`]*`|“[^”]*”|‘[^’]*’")
_PROTECTED = re.compile(r"(?i)\b(?:https?://\S+|www\.\S+|\S+@\S+\.\w+|[A-Z]:\\\S*|\S*[/\\]\S+|\S+\.\w{2,4}\b)")
# Whole words in any alphabet, so "café" is one word (and, not being plain ASCII, is left as written).
_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|')*[^\W\d_]|[^\W\d_]")

#: Real words that are almost always a slip for another when typed: a dictionary cannot flag them.
REAL_WORD_SLIPS = {"wether": "weather", "teh": "the", "adn": "and", "taht": "that",
                   "fro": "for", "thier": "their", "recieve": "receive", "untill": "until", "alot": "a lot"}

_lock = threading.Lock()
#: Words that are corrected *to*: HAYAT's own words, which outrank ordinary ones ("routr" is "router").
_extra: set[str] = set(HAYAT_WORDS)
#: Words accepted as written but never offered as corrections: tool descriptions, the owner's vocabulary.
_accepted: set[str] = set()


@dataclass
class Spelling:
    text: str
    changes: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


@lru_cache(maxsize=1)
def _checker():
    try:
        from spellchecker import SpellChecker
    except ImportError:  # the speller is optional: without it, words are read as typed
        return None
    return SpellChecker(distance=1)


def available() -> bool:
    return _checker() is not None


def add_words(words: Iterable[str], *, preferred: bool = False) -> None:
    """Teach the speller words to accept as written; ``preferred`` ones are also corrected *to* (HAYAT's own).

    Only a short, deliberate list may be preferred: every preferred word outranks the whole dictionary, so a
    tool description's "taine" once turned "tagline" into it.
    """
    clean = {str(word).lower() for word in words if str(word).isalpha() and len(str(word)) >= 2}
    with _lock:
        (_extra if preferred else _accepted).update(clean)
    _correct_word.cache_clear()


#: Words the owner said are right as written ("Keep my spelling" in the chat); never changed, even a REAL_WORD_SLIP.
_kept: set[str] = set()


def keep_words(words: Iterable[str], path: Path | None = None) -> list[str]:
    """Never change these words again, and remember that across restarts when ``path`` is given."""
    clean = sorted({str(word).lower() for word in words if str(word).replace("'", "").isalpha() and len(str(word)) >= 2})
    if not clean:
        return []
    with _lock:
        _kept.update(clean)
        _accepted.update(clean)
        everything = sorted(_kept)
    _correct_word.cache_clear()
    if path is not None:
        target = Path(path)
        try:
            saved = set(json.loads(target.read_text(encoding="utf-8")).get("kept", []))
        except (OSError, ValueError, AttributeError):
            saved = set()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"kept": sorted(saved | set(everything))}, indent=1), encoding="utf-8")
        temporary.replace(target)
    return clean


def load_kept_words(path: Path) -> list[str]:
    """The words the owner kept before (an absent or damaged file keeps nothing)."""
    try:
        words = json.loads(Path(path).read_text(encoding="utf-8")).get("kept", [])
    except (OSError, ValueError, AttributeError):
        return []
    return keep_words(str(word) for word in words if isinstance(word, str))


def learn_owner_words(messages: Iterable[str], *, minimum: int = 3) -> list[str]:
    """Learn the non-dictionary words the owner keeps using, but never a typo of a real word.

    A word one edit from a dictionary word is a slip, however often it is made ("imprve"); a word with no
    dictionary word that close is the owner's own vocabulary ("mohsin", "jaadoo").
    """
    checker = _checker()
    if checker is None:
        return []
    counts: dict[str, int] = {}
    for message in messages:
        for word in _WORD.findall(str(message)):
            lower = word.lower()
            if len(lower) >= 3 and lower.isalpha() and not _known(lower):
                counts[lower] = counts.get(lower, 0) + 1
    learned = [word for word, count in counts.items() if count >= minimum and not checker.edit_distance_1(word)
               & set(checker.word_frequency.dictionary)]
    add_words(learned)
    return sorted(learned)


def _known(word: str) -> bool:
    checker = _checker()
    return word in _extra or word in _accepted or (checker is not None and bool(checker.known([word])))


def _frequency(word: str) -> float:
    checker = _checker()
    if word in _extra:
        return 5e6  # HAYAT's own words outrank ordinary ones: "routr" is "router" here
    return float(checker.word_frequency.dictionary.get(word, 0)) if checker is not None else 0.0


def _subsequence(short: str, long: str) -> bool:
    letters = iter(long)
    return all(letter in letters for letter in short)


def _swapped(typed: str, word: str) -> bool:
    if len(typed) != len(word) or typed == word:
        return False
    different = [index for index, (a, b) in enumerate(zip(typed, word)) if a != b]
    return (len(different) == 2 and different[1] == different[0] + 1
            and typed[different[0]] == word[different[1]] and typed[different[1]] == word[different[0]])


@lru_cache(maxsize=1)
def _buckets() -> dict[tuple[str, int], tuple[str, ...]]:
    """Dictionary words by first letter and length, so a word is compared only with plausible neighbours."""
    checker = _checker()
    buckets: dict[tuple[str, int], list[str]] = {}
    for word in (checker.word_frequency.dictionary if checker is not None else ()):
        if word.isalpha():
            buckets.setdefault((word[0], len(word)), []).append(word)
    return {key: tuple(words) for key, words in buckets.items()}


def _slip(typed: str, word: str, distance: int) -> bool:
    """Whether ``word`` could be what was meant: one edit of any kind, or two that are dropped, extra or swapped
    letters. Two changed letters ("mohsin" -> "cousin") is a different word, not a slip."""
    if distance <= 1:
        return True
    return _subsequence(typed, word) or _subsequence(word, typed) or _swapped(typed, word)


def _candidates(word: str) -> set[str]:
    from rapidfuzz.distance import OSA
    from rapidfuzz.process import extract

    found: set[str] = set()
    limit = 1 if len(word) <= 4 else 2
    buckets = _buckets()
    pool: list[str] = []
    for length in range(len(word) - limit, len(word) + limit + 1):
        pool.extend(buckets.get((word[0], length), ()))
    for candidate, distance, _ in extract(word, pool, scorer=OSA.distance, score_cutoff=limit, limit=40):
        if _slip(word, candidate, int(distance)):
            found.add(candidate)
    with _lock:
        extra = list(_extra)
    for candidate in extra:
        if abs(len(candidate) - len(word)) <= limit and candidate[:1] == word[:1]:
            distance = OSA.distance(word, candidate)
            if distance <= limit and _slip(word, candidate, distance):
                found.add(candidate)
    return found


def _score(typed: str, word: str) -> float:
    score = math.log10(_frequency(word) + 1)
    if _subsequence(typed, word) and len(word) - len(typed) <= 2:
        score += 1.6  # letters left out: the commonest slip at speed
    if _swapped(typed, word):
        score += 1.6
    if typed[:1] == word[:1]:
        score += 0.6
    score -= 0.8 * abs(len(word) - len(typed))
    return score


@lru_cache(maxsize=8192)
def _correct_word(lower: str) -> str | None:
    if lower in _kept:
        return None  # the owner said this word is right as written
    if lower in REAL_WORD_SLIPS:
        return REAL_WORD_SLIPS[lower]
    if _known(lower):
        return None
    candidates = _candidates(lower)
    if len(lower) <= 4:
        # Short words are often abbreviations the dictionary lacks; only a swap of two letters is trusted there.
        candidates = {item for item in candidates if _swapped(lower, item)}
    if not candidates:
        return None
    ranked = sorted(((_score(lower, item), item) for item in candidates), reverse=True)
    best_score, best = ranked[0]
    # Forms of one word ("component", "components"; "according", "accordingly") are one reading, not rivals.
    # Among them, the form that keeps every typed letter is the one meant: "accordingl" lost its final y.
    family = [item for _, item in ranked if _same_family(item, best)]
    keeping = [item for item in family if _subsequence(lower, item)]
    choice = keeping[0] if keeping else best
    rivals = [score for score, item in ranked if not _same_family(item, best)]
    runner_up = rivals[0] if rivals else -math.inf
    # The reading must clearly beat every other word; otherwise the word is left as typed.
    return choice if best_score - runner_up >= 0.9 else None


_SUFFIXES = ("", "s", "es", "ed", "d", "ing", "ly", "er", "ers", "al", "ally", "ion", "ions")


def _same_family(one: str, other: str) -> bool:
    short, long = sorted((one, other), key=len)
    return any(long == short + suffix for suffix in _SUFFIXES) or (
        short.endswith("e") and any(long == short[:-1] + suffix for suffix in ("ing", "ed", "er", "ion", "ions")))


def _tool_word_fix(word: str) -> str:
    from app.intent.typos import _fix_word

    return _fix_word(word)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans = [match.span() for pattern in (_QUOTED, _PROTECTED) for match in pattern.finditer(text)]
    dictation = _DICTATION.search(text)
    if dictation:
        spans.append((dictation.end(), len(text)))
    return spans


def correct(text: str) -> Spelling:
    """The text with misspelt words repaired, and the list of what was changed."""
    value = str(text or "")
    if not value.strip() or _checker() is None:
        return Spelling(value)
    spans = _protected_spans(value)
    changes: list[tuple[str, str]] = []
    pieces: list[str] = []
    last = 0
    for match in _WORD.finditer(value):
        start, end = match.span()
        word = match.group(0)
        if any(low <= start < high for low, high in spans):
            continue
        lower = word.lower()
        if (len(lower) < 3 or "'" in word or not word.isascii() or (word.isupper() and len(word) <= 5)
                or (word[:1].isupper() and start > 0 and not re.search(r"(?:^|[.!?]\s+)$", value[:start]))):
            continue
        if _tool_word_fix(word) != word:
            continue  # app/intent/typos.py reads tool words, and only where that is what reaches the tool
        fixed = _correct_word(lower)
        if fixed is None or fixed == lower:
            continue
        if word[:1].isupper():
            fixed = fixed[:1].upper() + fixed[1:]
        pieces.append(value[last:start] + fixed)
        last = end
        changes.append((word, fixed))
    pieces.append(value[last:])
    return Spelling("".join(pieces), changes)


def recall_words(text: str) -> list[str]:
    """Corrected forms to add to a memory or research search (the typed words stay; these are only added)."""
    return [fixed for _typed, fixed in correct(text).changes]
