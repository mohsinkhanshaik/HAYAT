"""Understanding how Mohsin actually talks, rather than how a parser wishes he did.

Every intent pattern in this project expects a tidy sentence starting with a
verb. Real input is not that. Speech arrives as "HAYAT. Open Notepad and
search the web" -- the recogniser punctuates the name as its own sentence, the
verb is no longer first, and the whole request routes to conversation. HAYAT
then chats about opening Notepad instead of opening it. Typed input arrives as
"bro can u open chrome n check mail", with the address in front, "u" for you,
and "n" for and.

Both fail the same way and for the same reason: the request is in there, with
something wrapped around it. So this strips the wrapping and repairs the
spelling before the router looks, and returns what it removed so nothing is
silently discarded.

Two rules keep this honest. It only ever removes an address or a filler that
it can name -- it does not rewrite the request itself, because a normaliser
that paraphrases is a normaliser that can change what was asked. And every
substitution is a whole-word match, so "u" becomes "you" while "ubuntu" is
left alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.intent.emoji import contains_emoji, translate


#: How Mohsin addresses HAYAT. Stripped from the front so the verb can lead,
#: and from the end because "open notepad hayat" is just as common in speech.
_ADDRESS = r"(?:hayat|hyatt|hayaat|haiyat|high at|ai|assistant|bro|dude|man|buddy|mate)"

#: A leading address, with whatever punctuation the recogniser attached to it.
#: The recogniser reliably emits "HAYAT." as its own sentence, which is what
#: pushed the verb out of first position and broke routing.
_LEADING_ADDRESS = re.compile(
    rf"^(?:(?:hey|hi|hello|yo|ok|okay|listen|so)\s+)?{_ADDRESS}\b[\s,.!?:-]*",
    re.IGNORECASE,
)
_TRAILING_ADDRESS = re.compile(rf"[\s,]*\b{_ADDRESS}\b[\s.!?]*$", re.IGNORECASE)

#: Politeness and hedging that carries no instruction.
_FILLER = re.compile(
    r"^(?:please|pls|plz|kindly|just|quickly|now|can you|could you|would you|will you|"
    r"i want you to|i need you to|i want to|i need to|help me|lets|let's|go ahead and)\s+",
    re.IGNORECASE,
)

#: Shorthand and habitual misspellings, matched whole-word only so that
#: "ubuntu" survives "u" and "android" survives "n".
_SUBSTITUTIONS = {
    "u": "you", "ur": "your", "r": "are", "n": "and", "nd": "and", "&": "and",
    "pls": "please", "plz": "please", "thx": "thanks", "ty": "thanks",
    "y": "why", "k": "ok", "cuz": "because", "coz": "because", "bcz": "because",
    "wat": "what", "wht": "what", "wat's": "what's", "hw": "how", "wen": "when",
    "wher": "where", "wich": "which", "shud": "should", "wud": "would",
    "cud": "could", "dont": "don't", "cant": "can't", "wont": "won't",
    "im": "I'm", "ive": "I've", "id": "I'd", "isnt": "isn't", "didnt": "didn't",
    "doesnt": "doesn't", "wasnt": "wasn't", "couldnt": "couldn't",
    "shouldnt": "shouldn't", "wouldnt": "wouldn't", "thats": "that's",
    "whats": "what's", "hows": "how's", "lets": "let's", "its": "it's",
    "gonna": "going to", "wanna": "want to", "gotta": "got to",
    "abt": "about", "acc": "account", "acess": "access", "acsess": "access",
    "recieve": "receive", "seperate": "separate", "occured": "occurred",
    "definately": "definitely", "teh": "the", "adn": "and", "taht": "that",
    "opne": "open", "opem": "open", "serach": "search", "searh": "search",
    "chekc": "check", "downlaod": "download", "instal": "install",
    "screenshoot": "screenshot", "sceen": "screen", "camra": "camera",
    "immage": "image", "voideo": "video", "vedio": "video",
    "cn": "can", "shw": "show", "tel": "tell", "mke": "make",
    # Swapped letters in everyday words ("waht is teh capitl of france?").
    "waht": "what", "whta": "what", "hwat": "what", "hwo": "how", "whcih": "which", "wehre": "where",
    "wheer": "where", "whne": "when", "wiht": "with", "wnat": "want", "knwo": "know", "cna": "can",
    "becuase": "because", "beacuse": "because", "becasue": "because", "pleae": "please", "plase": "please",
    "tommorow": "tomorrow", "tomorow": "tomorrow", "tommorrow": "tomorrow", "yesturday": "yesterday",
    "freind": "friend", "beleive": "believe", "wierd": "weird", "untill": "until",
}

#: Runs of the same letter that are emphasis rather than spelling.
# Letters only: "1,000 seconds" once became "1,00 seconds" and "₹5,000" "₹5,00".
_STRETCHED = re.compile(r"([^\W\d_])\1{2,}")

#: Markers after which the words are content to be reproduced, not a request
#: to be parsed. Everything from here on is left exactly as spoken.
#:
#: This is not a precaution -- it is a bug that shipped. "Write a note in
#: Notepad saying: Hello from my AI." had "AI." stripped off the end, because
#: "ai" is a way of addressing the assistant and the sentence happened to end
#: with it. The note would have been written with the last word missing, and
#: the whole point of this module is that it removes wrapping without ever
#: altering what was asked for.
_DICTATION = re.compile(
    r"\b(?:saying|says|say|type|typing|write|writing|note that|message|text|"
    r"content|titled|named|called)\b\s*[:\-\"']?\s*",
    re.IGNORECASE,
)


@dataclass
class Normalized:
    """The cleaned request, and an honest account of what was taken off it."""

    text: str
    original: str
    address_removed: str = ""
    filler_removed: str = ""
    corrections: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.text != self.original

    @property
    def was_addressed(self) -> bool:
        """Whether HAYAT was spoken to by name. Useful for wake-word handling."""
        return bool(self.address_removed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "original": self.original,
            "address_removed": self.address_removed, "filler_removed": self.filler_removed,
            "corrections": [list(pair) for pair in self.corrections],
            "changed": self.changed, "was_addressed": self.was_addressed,
        }


def _fix_word(word: str) -> str:
    """Repair one token, preserving the capitalisation the writer used."""
    bare = word.strip(".,!?;:\"'")
    if not bare:
        return word
    replacement = _SUBSTITUTIONS.get(bare.lower())
    if replacement is None:
        # "soooo" -> "soo" keeps it a word without inventing a different one.
        collapsed = _STRETCHED.sub(r"\1\1", bare)
        return word.replace(bare, collapsed) if collapsed != bare else word
    if bare.isupper() and len(bare) > 1:
        replacement = replacement.upper()
    elif bare[0].isupper():
        replacement = replacement[0].upper() + replacement[1:]
    return word.replace(bare, replacement)


def normalize(text: str) -> Normalized:
    """Strip the wrapping and repair the spelling. Never rewrite the request.

    The order matters: the address comes off first, because "HAYAT, please
    open notepad" hides the filler behind the name, and the filler comes off
    before the verb is looked for.
    """
    original = " ".join(str(text).strip().split())
    working = original

    # Emoji first: "📷 what's this" is a request to look through the camera,
    # and a bare "👍" is an answer. Left in place they break verb-first
    # patterns exactly the way a leading "HAYAT." did.
    if contains_emoji(working):
        translated = translate(working)
        if translated:
            working = translated

    # Anything after a dictation marker is content, and is carried through
    # untouched: not de-addressed, not spell-corrected, not trimmed.
    dictation = _DICTATION.search(working)
    payload = ""
    if dictation:
        payload = working[dictation.start():]
        working = working[: dictation.start()].rstrip()

    address = ""
    match = _LEADING_ADDRESS.match(working)
    if match and len(working) > match.end():
        address = match.group(0).strip()
        working = working[match.end():].lstrip()

    # Only when there is no dictated payload: a request that ends in content
    # must keep its last word, whatever that word happens to be.
    if not payload:
        trailing = _TRAILING_ADDRESS.search(working)
        if trailing and trailing.start() > 0:
            address = (address + " " + trailing.group(0).strip()).strip()
            working = working[: trailing.start()].rstrip()

    filler = ""
    while True:
        match = _FILLER.match(working)
        if not match or len(working) <= match.end():
            break
        filler = (filler + " " + match.group(0).strip()).strip()
        working = working[match.end():].lstrip()

    corrections: list[tuple[str, str]] = []
    words = []
    for word in working.split():
        fixed = _fix_word(word)
        if fixed != word:
            corrections.append((word, fixed))
        words.append(fixed)
    working = " ".join(words)

    if payload:
        working = f"{working} {payload}".strip() if working else payload

    if working and working[0].islower() and original[:1].isupper():
        working = working[0].upper() + working[1:]

    return Normalized(
        text=working or original, original=original,
        address_removed=address, filler_removed=filler, corrections=corrections,
    )


def normalized_text(text: str) -> str:
    """Just the cleaned request, for callers that do not need the account."""
    return normalize(text).text
