"""Emoji carry meaning, and speech cannot say them.

Two separate problems that look like one.

On the way in, an emoji is often the whole message. "👍" is approval, "🎤" after
a request is "do it by voice", and "📷 what's this" is a request to look
through the camera. Dropping them loses the instruction; leaving them in front
of a pattern that expects a verb loses it too, in the same way "HAYAT." did.
So they are translated into the words they stand for, and the router sees a
sentence.

On the way out, an emoji is fine to read and useless to hear. Piper pronounces
"🎉" as nothing, or worse, as its unicode name. Spoken text therefore gets them
stripped while displayed text keeps them -- the same reply, rendered for the
channel it is going to.
"""
from __future__ import annotations

import re
import unicodedata


#: Emoji whose meaning is an instruction or an answer rather than decoration.
#: Only these are translated: turning every emoji into words would make a
#: cheerful message read like a description of itself.
_MEANINGFUL = {
    "👍": "yes", "👎": "no", "✅": "yes", "❌": "no", "🆗": "ok", "👌": "ok",
    "🙏": "please", "❓": "?", "❔": "?", "‼": "!", "⁉": "?",
    "🎤": "using voice", "🔊": "speak this", "🔇": "be quiet", "🔈": "quieter",
    "📷": "look through the camera", "📸": "take a screenshot", "🖥": "on my screen",
    "💻": "on this computer", "🌐": "on the web", "🔍": "search for",
    "🔎": "search for", "📁": "folder", "📂": "folder", "📄": "file",
    "⏰": "remind me", "⏱": "how long", "🛑": "stop", "⏸": "pause",
    "▶": "continue", "🔁": "again", "♻": "retry", "🗑": "delete",
    "⚠": "warning", "🔒": "private", "🔑": "credentials", "🐛": "bug",
    "💡": "idea", "📊": "statistics", "🧠": "think about",
}

#: The ranges an emoji actually lives in. Built as a character test rather than
#: a library dependency, because this is the whole of what is needed.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # symbols, pictographs, extended
    "\U00002600-\U000027BF"   # misc symbols and dingbats
    "\U0001F000-\U0001F2FF"   # tiles, enclosed characters
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "\U00002190-\U000021FF"   # arrows
    "\U00002B00-\U00002BFF"   # additional symbols
    "\U0000200D"              # zero-width joiner
    "\U000024C2-\U0001F251"
    "]+"
)


def contains_emoji(text: str) -> bool:
    return bool(_EMOJI_PATTERN.search(str(text)))


def strip_emoji(text: str) -> str:
    """Remove emoji, for text that is about to be spoken aloud.

    Whitespace is tidied afterwards so a stripped emoji does not leave a
    double space or a sentence starting with a comma.
    """
    without = _EMOJI_PATTERN.sub(" ", str(text))
    without = re.sub(r"\s+([,.!?;:])", r"\1", without)
    return " ".join(without.split())


def translate(text: str) -> str:
    """Replace instruction-carrying emoji with the words they stand for.

    Decorative emoji are dropped rather than described: "great work 🎉" is
    about the work, and "great work party popper" is worse than "great work".
    """
    value = str(text)
    for symbol, meaning in _MEANINGFUL.items():
        if symbol in value:
            value = value.replace(symbol, f" {meaning} ")
    value = strip_emoji(value)
    return " ".join(value.split())


def for_speech(text: str) -> str:
    """The reply as it should be heard: no emoji, nothing else changed."""
    return strip_emoji(text)


def describe(text: str) -> list[str]:
    """Name the emoji present, for records that should not carry raw symbols."""
    names: list[str] = []
    for match in _EMOJI_PATTERN.finditer(str(text)):
        for character in match.group(0):
            if character.isspace() or character == "‍":
                continue
            try:
                names.append(unicodedata.name(character).lower())
            except ValueError:
                continue
    return names
