"""Replies as they should be heard: symbols said as words, long codes left out, cut at a sentence end.

A reply written for the screen ("BTC $83,560.00 · +1.12% 24 h · fees 2 sat/vB") reads badly aloud,
and cutting it at 240 characters stopped mid-word. This turns it into speech:
* $83,560.00 → "83,560 dollars"; +1.12% → "up 1.12 percent"; $3 – $5 → "3 dollars to 5 dollars";
* list bullets and separators (· ● ◆ ▲ → ✓) become pauses or words; each list line its own sentence;
* units: sat/vB, EH/s, km, "24 h" are said in full;
* web addresses, wallet addresses and transaction hashes become "a link", "an address", "a hash";
  a bracketed source domain is left out;
* whole sentences only, up to the limit.
A plain sentence passes through unchanged.
"""
from __future__ import annotations

import re

from app.intent.emoji import for_speech

_URL = re.compile(r"https?://\S+")
_HASH = re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b")
_ADDRESS = re.compile(r"\b0x[0-9a-fA-F]{20,63}\b|\bbc1[02-9ac-hj-np-z]{11,71}\b")
#: A bracketed web domain ("(eth.blockscout.com)") is a source note for the eye, not something to say.
_SOURCE = re.compile(r"\s?\((?:[^()]*,\s*)?[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|space|fi|gov|info)\)")
_MONEY = re.compile(r"(?P<sign>[-+]?)(?P<symbol>[$€£])(?P<whole>\d[\d,]*)(?:\.(?P<cents>\d+))?(?P<scale>\s?[KMBT]\b)?")
_SIGNED_PCT = re.compile(r"(?<![\w.])(?P<sign>[+-])(?P<value>\d+(?:\.\d+)?)\s?%")
_PCT = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s?%")
_RANGE = re.compile(r"(?P<a>\d[\d,.]*)\s?[–—-]\s?(?P<b>\d[\d,.]*)(?=\s|$|[%,.;)])")
_DASH_BETWEEN = re.compile(r"\s[–—]\s(?=[-+]?\d|minus|plus)")
_NEEDS_WORK = re.compile(r"[$€£%·•●○◆★☆✓✗▲▼→←↗×≈…|]|https?://|0x[0-9a-fA-F]{8}|\b[0-9a-fA-F]{64}\b|sat/vB|EH/s"
                         r"|(?<=\d) (?:h|km|min|ms|s)\b")
UNITS = ((re.compile(r"\bsat/vB\b"), "sats per byte"), (re.compile(r"\bEH/s\b"), "exahashes per second"),
         (re.compile(r"(?<=\d) ?km\b"), " kilometres"), (re.compile(r"(?<=\d) h\b"), " hours"), (re.compile(r"(?<=\d) min\b"), " minutes"),
         (re.compile(r"(?<=\d) s\b"), " seconds"), (re.compile(r"(?<=\d) ms\b"), " milliseconds"), (re.compile(r"\bvs\.?(?=\s)"), "versus"),
         (re.compile(r"\be\.g\.\s?"), "for example "), (re.compile(r"\bi\.e\.\s?"), "that is "))
CURRENCY = {"$": "dollars", "€": "euros", "£": "pounds"}
SCALE = {"K": "thousand", "M": "million", "B": "billion", "T": "trillion"}
SYMBOLS = (("·", ","), ("•", ""), ("●", ""), ("○", ""), ("◆", ""), ("◇", ""), ("★", ""), ("☆", ""), ("✓", ""), ("✗", "failed"),
           ("▲", ""), ("▼", ""), ("→", " to "), ("←", ""), ("↗", ""), ("×", " times "), ("≈", "about "), ("…", "."), ("|", ","))


def _money(match: re.Match[str]) -> str:
    whole = match["whole"]
    cents = match["cents"] or ""
    amount = whole if not cents or int(whole.replace(",", "") or 0) >= 100 or set(cents) == {"0"} else f"{whole}.{cents}"
    scale = SCALE.get((match["scale"] or "").strip(), "")
    sign = {"-": "minus ", "+": "plus "}.get(match["sign"], "")
    return f"{sign}{amount}{' ' + scale if scale else ''} {CURRENCY[match['symbol']]}"


def _words(line: str) -> str:
    line = _SOURCE.sub("", line)
    line = _URL.sub("a link", line)
    line = _HASH.sub("a hash", line)
    line = _ADDRESS.sub("an address", line)
    line = _MONEY.sub(_money, line)
    line = _SIGNED_PCT.sub(lambda match: f"{'up' if match['sign'] == '+' else 'down'} {match['value']} percent", line)
    line = _PCT.sub(lambda match: f"{match['value']} percent", line)
    line = _RANGE.sub(lambda match: f"{match['a']} to {match['b']}", line)
    line = _DASH_BETWEEN.sub(" to ", line)
    for pattern, words in UNITS:
        line = pattern.sub(words, line)
    for symbol, words in SYMBOLS:
        line = line.replace(symbol, words)
    line = re.sub(r"\s+([,.;:!?])", r"\1", line)
    line = re.sub(r"([,;:])\1+", r"\1", line)
    line = re.sub(r"\(\s*\)", "", line)
    return " ".join(line.split()).strip(" ,;:")


def speakable(text: str, limit: int = 320) -> str:
    """The reply to say aloud: words for symbols, no codes, whole sentences up to about `limit` characters."""
    lines = [for_speech(line) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1 and not _NEEDS_WORK.search(lines[0]) and len(lines[0]) <= limit:
        return lines[0].strip()  # a plain sentence passes through unchanged
    sentences = []
    for line in lines:
        line = _words(line)
        if line and line not in ("a link", "an address", "a hash"):  # a line that was only a code says nothing
            sentences.append(line if line[-1] in ".!?" else line + ".")
    value = re.sub(r",\s*\.", ".", " ".join(sentences))
    if len(value) <= limit:
        return value
    spoken: list[str] = []
    total = 0
    for sentence in re.split(r"(?<=[.!?])\s+", value):
        if spoken and total + len(sentence) + 1 > limit:
            break
        spoken.append(sentence)
        total += len(sentence) + 1
    result = " ".join(spoken)
    if len(result) > limit:
        result = result[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return result
