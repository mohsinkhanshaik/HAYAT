"""Typo-tolerant reading for the requests that must reach a real tool.

The owner types fast: "chek my routr", "whos on my wifi", "list my devises",
"is csi conected", "any erthquakes near tokyo". Each of those fell through to
the conversation model, which then answered from imagination instead of reading
the router, the device list or the earthquake feeds (13 of 25 such phrasings on
2026-10-15).

``fix`` corrects only the words that decide which tool runs -- a fixed list of
everyday shorthand and a small vocabulary of tool words, matched by spelling
similarity. It never changes what the owner wrote: the router uses the fixed
text only to ask the deterministic tool detectors, and only when the original
text matched none of them, so a sentence that already routed is never touched
and an ordinary chat message keeps its own words.
"""

from __future__ import annotations

import difflib
import re

#: Chat shorthand -> words.
SHORTHAND = {
    "any1": "anyone", "sum1": "someone", "some1": "someone", "u": "you", "ur": "your", "r": "are", "wat": "what",
    "wats": "what's", "whats": "what's", "whos": "who's", "wher": "where", "whr": "where", "im": "i'm", "pls": "please",
    "plz": "please", "thx": "thanks", "abt": "about", "dint": "didn't", "didnt": "didn't", "dont": "don't",
    "cant": "can't", "wont": "won't", "isnt": "isn't", "hows": "how's", "n": "and", "&": "and", "b4": "before",
    "2day": "today", "2morrow": "tomorrow", "tmrw": "tomorrow", "tmr": "tomorrow", "nw": "now", "rn": "right now",
    # Real words that are usually a slip for a tool word here, so similarity alone would leave them.
    "devise": "device", "devises": "devices", "rooter": "router", "routter": "router",
}
#: Words that select a tool. A misspelling within ~80 % similarity of one of these is read as it.
VOCABULARY = (
    "router", "routers", "gateway", "modem", "wifi", "internet", "connected", "connection", "connect", "network",
    "networks", "devices", "device", "nearby", "signal", "channel", "check", "status", "location", "located",
    "happening", "around", "hazard", "hazards", "radar", "dangerous", "earthquake", "earthquakes", "tsunami",
    "volcano", "volcanoes", "asteroids", "fireballs", "quality", "pollution", "outbreak", "outbreaks", "weather",
    "forecast", "storms", "floods", "wildfires", "moving", "movement", "anyone", "someone", "sensing", "calibrate",
    "disconnect", "recordings", "camera", "cameras", "sentiment", "readable", "readability", "keywords", "planes",
    "flying", "satellite", "launches", "bitcoin", "price", "markets", "stocks", "portfolio", "wallet", "invoice",
    "where", "show", "list", "scan", "near", "today", "tomorrow", "anything",
)
#: Ordinary words that sit close to a tool word ("model" ~ "modem", "what" ~ "what's") and must never be "corrected".
COMMON = frozenset(
    "what that this with have from your they them then than when were where there their these those which would "
    "could should about after again also because before being between both came come does done down each even "
    "every find first good great here into just know like look made make many more most much must never only "
    "other over same said says some still such take tell than very want well went what will year model modes mode "
    "noted notes north house horse mouse cause chase chest chick check cheek count court route routes rout rooted "
    "connect connects conduct contact content context weathered whether wither radio ready reader rider rated "
    "moving moving movie moves mover proving loving solve signs single sign design desire device devils deviate "
    "status statue states state stats static camel came cameo planet plants planes plans plane flying lying "
    "price prize prise prince pride stock stocks stork store wallet walled wallow invoke invites invoice voice".split())
_VOCABULARY = frozenset(VOCABULARY)
_WORD = re.compile(r"[A-Za-z0-9&'-]+")


def _fix_word(word: str) -> str:
    lower = word.lower()
    if lower in SHORTHAND:
        return SHORTHAND[lower]
    if lower in _VOCABULARY or lower in COMMON or len(lower) < 4 or not lower.isalpha():
        return word
    # "routr" -> "router", "conected" -> "connected", "erthquakes" -> "earthquakes", "chek" -> "check": a typing slip
    # keeps the first letter and the length within two; anything further is a different word, not a typo.
    for match in difflib.get_close_matches(lower, VOCABULARY, n=3, cutoff=0.82):
        if match[0] == lower[0] and abs(len(match) - len(lower)) <= 2:
            return match
    return word


def fix(text: str) -> str:
    """The text with shorthand expanded and tool words' misspellings corrected (lower-case where changed)."""
    return _WORD.sub(lambda match: _fix_word(match.group(0)), str(text or ""))
