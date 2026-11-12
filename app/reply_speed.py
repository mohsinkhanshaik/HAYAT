"""How much thought a reply needs, read from the message itself: reflex, medium or deep.

The owner's setting is how he wants HAYAT to work by default (Auto, Fast, Medium or Deep). HAYAT still reads every
message, because a setting cannot know what will be said:

* **Reflex** talk -- a feeling, an opinion, a joke, "what about you?", "kya haal hai", a quick follow-up, a one-fact
  lookup -- is answered at conversational speed in every setting. In Deep mode a "haha, what do you think about
  pineapple pizza?" went through self-questioning, a second model's opinion and a minimum length, and took a minute
  (live runs, 2026-11-11/12: 30-170 s per chat turn). People talk back and forth; that rhythm is part of understanding.
* **Deep** work -- a proof, a plan, an analysis, code, a long or many-part question, or "think harder" -- gets more
  thought than the setting gives ordinary messages: in Fast it is lifted to Medium, so a fast setting never means a
  careless proof.
* Everything else follows the setting.

Fast never means less context: the conversation, its topic and the dialogue record are given to every reply.
"""
from __future__ import annotations

import re
from typing import Any

REFLEX, MEDIUM, DEEP = "reflex", "medium", "deep"
SETTINGS = ("auto", "fast", "medium", "deep")
LABELS = {"auto": "Auto", "fast": "Fast", "medium": "Medium", "deep": "Deep"}

#: The depth used for each kind of message under each setting.
TABLE: dict[str, dict[str, str]] = {
    "auto": {REFLEX: "FAST", MEDIUM: "BALANCED", DEEP: "DEEP"},
    "fast": {REFLEX: "FAST", MEDIUM: "FAST", DEEP: "BALANCED"},
    "medium": {REFLEX: "FAST", MEDIUM: "BALANCED", DEEP: "DEEP"},
    "deep": {REFLEX: "FAST", MEDIUM: "DEEP+ADVERSARIAL", DEEP: "DEEP+ADVERSARIAL"},
}

_DEEP_WORK = re.compile(
    r"(?i)\b(?:prove|proof|derive|derivation|analy[sz]e|analysis|evaluate|assess|critique|compare\s+.+\s+(?:and|vs|versus|"
    r"with)\b|pros\s+and\s+cons|trade-?offs?|plan\s+(?:for|out|my)|strategy|roadmap|design\s+(?:a|an|the)|architect|"
    r"debug|refactor|optimi[sz]e|write\s+(?:a|an|the|me)\s+(?:\w+\s+){0,3}(?:script|program|function|class|essay|report|"
    r"proposal|business\s+plan|cover\s+letter)|step[- ]by[- ]step|in\s+detail|detailed|thorough(?:ly)?|"
    r"why\s+(?:does|do|did|is|are)\s+.+\s+(?:work|happen|fail|matter)|what\s+would\s+happen\s+if|"
    r"calculate\s+.+\s+(?:and|then)\b|solve\b)")
_REFLEX = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|haha+|lol|lmao|hmm+|oh|wow|yeah|yes|no|nah|right|cool|nice|great|sure|bro|yaar|acha|accha|"
    r"theek|thik|hmm)[\s,.!?]*)*(?:"
    r"what\s+about\s+you|how\s+about\s+you|and\s+you|you\??$|what\s+do\s+you\s+think|do\s+you\s+(?:like|think|ever|"
    r"remember|know)|tell\s+me\s+(?:a\s+)?(?:joke|something\s+(?:fun|interesting|cool))|i(?:'m|\s+am)\s+(?:so\s+|really\s+|"
    r"kinda\s+|very\s+)?(?:tired|bored|happy|sad|excited|stressed|hungry|sleepy|done|back|here|fine|good|ok(?:ay)?)|"
    r"i\s+(?:feel|felt|think|guess|love|like|hate|miss|wish)\b|that(?:'s|\s+is|\s+was)\s+\w+|"
    r"(?:and\s+)?then\s+what|what\s+happened|really\??|seriously\??|no\s+way|kya\b|kaise\b|kaisa\b|kaisi\b|"
    r"aap\b|tum\b|mai(?:n)?\b|mera\b|meri\b|haal\b|chal\b|bhai\b|yaar\b|shukriya|dhanyavaad|ennadi|emiti|ela\s+unnav)")
_CONSIDERED = re.compile(
    r"(?i)^(?:(?:so|ok(?:ay)?|and|but|hey|hayat|jarvis)[,\s]+)*(?:explain|describe|teach\s+me|how\s+(?:does|do|did|can|could|"
    r"should|would|to)\b|why\b|what\s+(?:causes|makes|happens\s+when)|"
    r"should\s+(?:i|we)\b|which\s+(?:one\s+)?(?:should|is\s+better)|is\s+it\s+(?:worth|better|wise|safe|a\s+good\s+idea)|"
    r"would\s+you\s+recommend|help\s+me\s+(?:decide|choose|understand))")
_LOOKUP = re.compile(
    r"(?i)^(?:what(?:'s|\s+is|\s+was)\s+(?:the\s+)?(?:capital|currency|language|meaning|opposite|plural|spelling)\b|"
    r"who\s+(?:is|was|wrote|painted|invented|discovered)\b|when\s+(?:is|was|did)\b|where\s+is\b|how\s+do\s+you\s+say\b|"
    r"translate\b|what\s+time\b|what\s+day\b|what's\s+\d|what\s+is\s+\d)")


def pace(text: str, intent: Any = None) -> str:
    """reflex, medium or deep, from the owner's own words (and the routed kind of request, when given)."""
    value = " ".join(str(text or "").split())
    words = len(value.split())
    kind = str(getattr(getattr(intent, "intent", None), "value", "") or "").upper()
    from app.modes import wants_deliberation

    if wants_deliberation(value):
        return DEEP
    complexity = str(getattr(getattr(intent, "complexity", None), "value", "") or "").upper()
    if (kind in {"RESEARCH", "CODING"} or complexity in {"COMPLEX", "RESEARCH"} or _DEEP_WORK.search(value)
            or words >= 60 or value.count("?") >= 3):
        return DEEP
    if kind in {"COMPUTER_CONTROL", "CONTROL", "SYSTEM_STATE"} and words <= 20:
        return REFLEX  # "open notepad", "mute", "what's my battery" act; they need no essay
    if _CONSIDERED.search(value):
        return MEDIUM  # an explanation or a decision of his own deserves a considered answer, however short
    if words <= 25 and (_REFLEX.search(value) or (kind == "CONVERSATION" and words <= 12)):
        return REFLEX
    if words <= 14 and _LOOKUP.search(value):
        return REFLEX
    if words <= 6 and kind in {"QUESTION", "CONVERSATION", ""}:
        return REFLEX  # "capital of france?"
    return MEDIUM


def setting_of(configured: list[str]) -> str:
    """The speed setting a configured mode list stands for."""
    names = {str(item).upper() for item in configured}
    if names == {"AUTO"}:
        return "auto"
    if "DEEP" in names:
        return "deep"
    if "FAST" in names:
        return "fast"
    if "BALANCED" in names:
        return "medium"
    return "auto"


def depth_for(setting: str, kind: str) -> list[str]:
    return TABLE.get(setting, TABLE["auto"])[kind].split("+")


def explain(setting: str, kind: str) -> str:
    """One line for the reply's details: what the setting was and what this message got."""
    used = " + ".join(mode.title() for mode in depth_for(setting, kind)).replace("Balanced", "Medium")
    why = {REFLEX: "a conversational reply", MEDIUM: "an ordinary question", DEEP: "work that needs thought"}[kind]
    return f"Speed {LABELS.get(setting, setting)}: read as {why}, answered {used}."
