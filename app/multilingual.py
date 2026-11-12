"""Understanding the owner in the languages he speaks: English, Hinglish, Hindi, Urdu, Arabic and Telugu.

The brain understands all of them, but HAYAT's routing reads English: "aaj mausam kaisa hai hyderabad mein" (what's
the weather in Hyderabad today?) fell through to plain chat, where the weather would be answered from memory instead
of looked up, and "mujhe kal subah 7 baje uthana" (I need to wake up at 7 tomorrow morning) was never read as a time.

So a message that is not English and that routing could not place is given a short English reading by the model,
and routing uses that reading. His own words are what is recorded and quoted; the reading only decides where the
request goes. Nothing is translated when routing already understood the message, and nothing at all for English.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_TELUGU = re.compile(r"[ఀ-౿]")
_ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿ]")
#: Letters Urdu uses and Arabic does not.
_URDU_ONLY = re.compile(r"[پچڈڑژگںھہےٹ]")

#: Everyday words of romanized Hindi/Urdu, Telugu and Arabic that are not English words.
_ROMANIZED = {
    "hi": frozenset("""
hai hain ho hoon hun tha thi the kya kyu kyun kaise kaisa kaisi kahan kab kaun kitna kitne mera meri mere mujhe mujhko
tera teri tere tumhara tumhari aap aapka aapki tum hum hamara hamari main mein mai yeh ye woh wo yaar bhai bhaiya behen
acha accha achha theek thik nahi nahin haan han ji karo karna karna karu karun kar raha rahi rahe gaya gayi gaye diya
chahiye chahta chahti batao bata bataiye dikhao dikha suna sunao samjha samjhao chal chalo aaj kal parso abhi phir
subah shaam raat din baje bahut bohot thoda zyada kuch sab sabhi agar lekin aur ya bhi toh to waala wala wali kaam
paisa paise ghar khana pani mausam sawal jawab dost yahan wahan matlab shukriya dhanyavaad namaste salaam
""".split()),
    "te": frozenset("""
nenu nuvvu meeru memu manam vadu adi idi ela enti emiti emi ekkada eppudu enduku evaru cheppu cheppandi chesthanu
chestha cheyi cheyandi ledu undi unnadi unnaru unnanu bagunnanu bagunnara kavali vaddu ippudu repu ninna roju
chala konchem inka mari ayindi avunu kadu sare babu amma nanna anna akka tammudu chelli
""".split()),
    "ar": frozenset("""
ana anta enta anti inti huwa hiya nahnu kayf kaif kaifa halak halik shukran afwan marhaba ahlan naam la laa maa matha
mada leh lesh fein wein ayna mata kam hatha haza hadha hathi ureed abghi bidi yalla inshallah mashallah habibi sabah
masa layla youm bukra ams alaan
""".split()),
}
_WORD = re.compile(r"[a-z']+")


def detect(text: str) -> str | None:
    """The language a message is in when it is not English: hi, ur, ar, te, or None."""
    value = str(text or "")
    if _TELUGU.search(value):
        return "te"
    if _DEVANAGARI.search(value):
        return "hi"
    if _ARABIC_SCRIPT.search(value):
        return "ur" if _URDU_ONLY.search(value) else "ar"
    words = _WORD.findall(value.lower())
    if len(words) < 2:
        return None
    best, hits = None, 0
    for code, vocabulary in _ROMANIZED.items():
        count = sum(word in vocabulary for word in words)
        if count > hits:
            best, hits = code, count
    # Two of its everyday words, and at least a third of the message: "yaar mera laptop slow chal raha hai" is
    # Hinglish; "I am in Austin" (with "main"/"mein" lookalikes absent) is not.
    if best is not None and hits >= 2 and hits / len(words) >= 0.34:
        return best
    return None


NAMES = {"hi": "Hindi or Hinglish", "ur": "Urdu", "ar": "Arabic", "te": "Telugu"}


#: Readings already made this run: the same message is not translated twice.
_READINGS: dict[str, str] = {}


def english_reading(text: str, model: Any) -> str | None:
    """A short English rendering of a non-English message, for routing only (None when not needed or not possible)."""
    language = detect(text)
    if language is None or model is None or not hasattr(model, "generate") or len(text) > 600:
        return None
    from app.models.base import Message

    def generate(value: str) -> str:
        result = model.generate([
            Message("system", "You translate the owner's message into plain English so an assistant can tell what he "
                              "wants. The message may mix Hindi, Urdu, Telugu or Arabic with English. Keep names, "
                              "places, numbers and times exactly. Reply with the English sentence only, no notes."),
            Message("user", value)], temperature=0, max_tokens=120)
        return " ".join(str(getattr(result, "content", result) or "").split()).strip(" \"'")

    try:
        english = _READINGS.get(text)
        if english is None:
            english = generate(text)
            if len(_READINGS) > 256:
                _READINGS.clear()
            _READINGS[text] = english
    except Exception:
        log_swallowed(_log, "reading a non-English message in English")
        return None
    if not english or len(english) > 3 * len(text) + 40 or detect(english) is not None:
        return None
    if not re.search(r"[A-Za-z]{2,}", english) or english.lower() == text.lower():
        return None
    return english
