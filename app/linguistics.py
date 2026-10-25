"""Bounded language repair shared by voice and memory recall.

This is deliberately a small, auditable concept lexicon rather than a fuzzy
autocorrect engine.  Fuzzy rewriting can change filenames, commands, secrets,
or approval language.  These rules only join well-known technical acronyms,
their spoken forms, and a few HAYAT-specific recognition errors.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class Concept:
    canonical: str
    aliases: tuple[str, ...]


CONCEPTS: tuple[Concept, ...] = (
    Concept("push to talk", ("ptt", "push-to-talk", "push two talk")),
    Concept("speech to text", ("stt", "speech-to-text", "speech recognition", "voice transcription")),
    Concept("text to speech", ("tts", "text-to-speech", "voice synthesis")),
    Concept("dynamic neural associations", ("dna memory", "dna retrieval", "dna recall", "dna storage")),
    Concept("optical character recognition", ("ocr", "text recognition")),
    Concept("retrieval augmented generation", ("rag", "retrieval-augmented generation")),
    Concept("model context protocol", ("mcp", "model-context protocol")),
    Concept("large language model", ("llm", "language model")),
    Concept("graphics processing unit", ("gpu", "graphics card")),
    Concept("central processing unit", ("cpu", "processor")),
    Concept("virtual ram", ("vram", "video memory")),
    Concept("application programming interface", ("api", "application interface")),
    Concept("web browser", ("browser", "internet browser")),
    Concept("memory recall", ("remember", "recall", "retrieve memory", "memory retrieval")),
)
_CONCEPT_PATTERNS = tuple(
    re.compile(
        "|".join(rf"(?<!\w){re.escape(phrase)}(?!\w)" for phrase in (concept.canonical, *concept.aliases)),
        re.IGNORECASE,
    )
    for concept in CONCEPTS
)

_HAYAT = re.compile(r"(?i)\b(?:hayaat|haiyat|hyatt|high\s+at|hi\s+at)\b")
_SPOKEN_TECH = (
    (re.compile(r"(?i)\bpower\s+shell\b"), "PowerShell"),
    (re.compile(r"(?i)\bpie\s+torch\b"), "PyTorch"),
    (re.compile(r"(?i)\bkuber\s*net(?:es|ties)\b"), "Kubernetes"),
    (re.compile(r"(?i)\bgit\s+hub\b"), "GitHub"),
    (re.compile(r"(?i)\bollama\b"), "Ollama"),
    (re.compile(r"(?i)\bcuda\b"), "CUDA"),
)


@dataclass(frozen=True, slots=True)
class QueryExpansion:
    text: str
    concepts: tuple[str, ...]


def normalize_transcript(text: str) -> str:
    """Repair only high-confidence proper names and spoken technical terms."""
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return ""
    value = _HAYAT.sub("HAYAT", value)
    for pattern, replacement in _SPOKEN_TECH:
        value = pattern.sub(replacement, value)
    return value


def expand_query(text: str, *, maximum_concepts: int = 6) -> QueryExpansion:
    """Append canonical/alias vocabulary for deterministic multi-layer recall.

    The original text always leads. Expansion is bounded, adds words only, and
    is used solely for retrieval scoring/candidate generation; it never enters
    command execution or permission decisions.
    """
    original = normalize_transcript(text)
    bounded = max(0, min(int(maximum_concepts), len(CONCEPTS)))
    # Candidate rows are scored repeatedly across a session. Cache ordinary
    # notes, while avoiding retention of very large document bodies.
    if len(original) <= 4096:
        return _expand_cached(original, bounded)
    return _expand_uncached(original, bounded)


@lru_cache(maxsize=4096)
def _expand_cached(original: str, maximum_concepts: int) -> QueryExpansion:
    return _expand_uncached(original, maximum_concepts)


def _expand_uncached(original: str, maximum_concepts: int) -> QueryExpansion:
    if maximum_concepts <= 0:
        return QueryExpansion(original, ())
    folded = original.casefold()
    additions: list[str] = []
    matched: list[str] = []
    for concept, pattern in zip(CONCEPTS, _CONCEPT_PATTERNS, strict=True):
        phrases = (concept.canonical, *concept.aliases)
        if not pattern.search(folded):
            continue
        matched.append(concept.canonical)
        for phrase in phrases:
            if phrase.casefold() not in folded and phrase not in additions:
                additions.append(phrase)
        if len(matched) >= maximum_concepts:
            break
    expanded = " ".join([original, *additions]).strip()
    return QueryExpansion(expanded, tuple(matched))


# ------------------------------------------------------------------ acronyms
#
# Memories are often written out ("Fully Sharded Data Parallel") and asked
# about by acronym ("FSDP"). A run of consecutive Capitalised words whose
# initials spell the acronym exactly counts as defining it. Lowercase prose is
# never read as an expansion, so "data never stops" cannot answer "DNS".

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_VOWELS = set("aeiou")


def _initials(word: str) -> str:
    """What one word contributes: all of a short all-caps word (PCI), else its first letter."""
    if word.isupper() and len(word) <= 5:
        return word.lower()
    return word[0].lower()


def query_acronyms(text: str) -> tuple[str, ...]:
    """Words in a query that are plausibly acronyms, lowercased.

    Written with two or more capitals (FSDP, PCIe, KV), or typed lowercase
    with no vowel at all (fsdp, ddp, dns). Lowercase words with vowels are
    left alone: "what" and "the" are words, and so, sadly, is "rdma" to a
    regex; write it RDMA to be matched as an acronym.
    """
    found: list[str] = []
    for word in _WORD.findall(str(text or "")):
        if not 2 <= len(word) <= 6 or not word.isalpha():
            continue
        capitals = sum(char.isupper() for char in word)
        lowered = word.lower()
        plausible = capitals >= 2 or (word.islower() and len(word) >= 3
                                      and not any(char in _VOWELS for char in lowered))
        if plausible and lowered not in found:
            found.append(lowered)
    return tuple(found)


@lru_cache(maxsize=8192)
def defined_acronyms(text: str) -> frozenset[str]:
    """Every acronym spelled by a run of 2-6 consecutive Capitalised words."""
    spelled: set[str] = set()
    run: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]*|[^A-Za-z\s]+|\s+", str(text or "")[:20_000]):
        if token.isspace():
            continue
        if token[0].isalpha() and token[0].isupper():
            run.append(_initials(token))
            for size in range(2, min(6, len(run)) + 1):
                spelled.add("".join(run[-size:]))
        elif token not in {"-"}:
            run = []
    return frozenset(item for item in spelled if 2 <= len(item) <= 8)
