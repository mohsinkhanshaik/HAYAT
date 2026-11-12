"""What the owner says about the answer he just got, read from his own words.

The conversation of 2026-11-11 went wrong four times in a row for one reason: HAYAT had no reading of a
complaint about its last reply.

* "this is same reply u store from last reply so look again but deeper" was filed as a brand-new topic, so the
  exchange it complained about was set aside and the reply came from an older, unrelated thread.
* "i dint ask anything about model errpr i was asking u to look better ideas to make money ..." was recognised as
  a correction, but the restated request was sent straight back to the tool whose reply had just been rejected.
* "again same thing dont treat this fact as i can change" was filed as a new topic again, and "this fact" was
  read backwards: the reply was "tailored to your fixed hardware".

``owner_feedback.verdict`` only knows "that's wrong" / "good answer". The owner's real complaints are worded as
"same reply", "again same thing", "look deeper", "i didn't ask about X", "i was asking you to Y". This module
reads those, and nothing else: it returns ``None`` for a message that is a request of its own.

Everything here is a pure function of the text. What is done with the reading (which topic the message belongs
to, which answer is marked rejected, where the question goes next) is decided in app/topics.py,
app/dialogue_state.py and app/assistant.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: The kinds of feedback a message can carry. A message can carry several.
KINDS = ("repeated", "deeper", "off_topic", "restated", "generic", "wrong", "changeable")
#: Kinds that say the last answer is not acceptable as it stands.
REJECTING = frozenset({"repeated", "off_topic", "generic", "wrong"})

_YOU = r"(?:you|u|ya|hayat)"
_NOT = r"(?:didn['’]?t|dint|didnt|did\s+not|never|don['’]?t|dont|do\s+not)"
_ANSWER = (r"(?:repl(?:y|ies)|answers?|responses?|results?|lists?|ideas?|suggestions?|output|text|message|stuff|"
           r"thing|things|points?|options?|recommendations?)")

#: "this is same reply", "again same thing", "you already said that", "same as before", "stop repeating".
_REPEATED = re.compile(
    rf"(?i)\b(?:this|that|it|these|those)(?:['’]s|\s+is|\s+are|\s+was)?\s+(?:just\s+|exactly\s+|again\s+)?(?:the\s+)?"
    rf"same\s+{_ANSWER}\b"
    rf"|^(?:(?:again|still|and|but|no|hey|hayat|bro)[,.!\s]+)*(?:the\s+|its\s+|it['’]s\s+)?same\s+{_ANSWER}"
    rf"(?:\s+again)?(?=[\s,.!?]|$)(?!\s+(?:for|with|but|except|about|in|on|to|as\s+(?!before|last|earlier|the\s+last))\b)"
    rf"|\bsame\s+(?:{_ANSWER}\s+)?as\s+(?:before|last\s+time|earlier|the\s+(?:last|previous)(?:\s+(?:one|time|{_ANSWER}))?)\b"
    rf"|\b{_YOU}\s+(?:have\s+|['’]ve\s+)?already\s+(?:said|told|gave|given|showed|shown|sent|wrote|listed)\b"
    rf"|\b{_YOU}\s+(?:said|told\s+me|gave\s+me|showed\s+me)\s+(?:this|that|it|these)\s+(?:before|already|earlier|last\s+time)\b"
    rf"|\b{_YOU}\s+(?:keep|kept)\s+(?:saying|repeating|giving|telling|sending|showing)\b"
    rf"|\b{_YOU}\s+(?:are|r|were)\s+(?:just\s+)?repeating\b|\bstop\s+repeating\b|\brepeat(?:ing|ed)?\s+(?:yourself|the\s+same)\b"
    rf"|\b(?:copy|copied)[\s-]?past(?:e|ed|ing)\b|\b(?:stored|saved|canned|old)\s+{_ANSWER}\b"
    rf"|\bsame\s+{_ANSWER}\s+{_YOU}\s+(?:store[ds]?|saved?|gave|said|sent)\b")

#: "look again but deeper", "go deeper", "think harder", "research more", "try again properly".
_DEEPER = re.compile(
    r"(?i)\b(?:look|search|think|research|dig|go|try|check|analy[sz]e|investigate|explore|study|do)\s+"
    r"(?:(?:it|that|this|into\s+it)\s+)?(?:again\s+)?(?:but\s+|and\s+)?(?:much\s+|way\s+|a\s+lot\s+|even\s+)?"
    r"(?:deeper|harder|further|more\s+(?:deeply|carefully|thoroughly|seriously)|in\s+(?:more\s+|greater\s+)?depth|properly)\b"
    r"|\b(?:look|search|research|think|check|investigate)\s+(?:(?:it|that|this)\s+)?(?:up\s+)?again\b"
    r"|\b(?:dig|go|look|think)\s+deeper\b|\bdeeper\b|\bmore\s+depth\b|\bin[\s-]depth\b"
    r"|\b(?:research|search|investigate|explore|look)\s+(?:a\s+lot\s+)?more\b|\bthink\s+(?:it\s+)?(?:through|over)\s+again\b"
    r"|\btry\s+(?:again\s+)?harder\b|\bdo\s+(?:a\s+)?(?:real|proper|actual|deep)\s+(?:research|search|analysis)\b")

#: "i didn't ask anything about X", "that's not what i asked", "wrong topic", "this is irrelevant".
_DENIED = re.compile(
    rf"(?i)\bi\s+{_NOT}\s+(?:ask(?:ed)?|say|said|mean|meant|want(?:ed)?|talk(?:ed)?|request(?:ed)?)\s+(?:{_YOU}\s+)?"
    r"(?:(?:anything|something|a\s+thing)\s+)?(?:about|for|to\s+(?:talk\s+about|do|look\s+at|check))\s+"
    r"(?P<denied>.+?)(?=\s+(?:i\s+(?:was|am|asked|meant|mean|want|wanted|need|said)|but|rather|instead)\b|\s*[,.;!?]|$)")
_OFF_TOPIC = re.compile(
    rf"(?i)\bthat(?:['’]s|\s+is|\s+was)\s+not\s+what\s+i\s+(?:asked|meant|wanted|said|requested)\b"
    rf"|\bwrong\s+(?:topic|subject|question|thing)\b|\boff[\s-]topic\b"
    rf"|\b(?:that|this|these|those|it)\s+(?:{_ANSWER}\s+)?(?:is|are|was|were|has|have)\s+"
    r"(?:not\s+(?:relevant|related|what\s+i\s+(?:asked|meant|wanted))|irrelevant|unrelated|nothing\s+to\s+do\s+with)\b"
    rf"|\bwhat\s+does\s+(?:that|this|it)\s+have\s+to\s+do\s+with\b"
    rf"|\b{_YOU}\s+(?:misunderstood|got\s+(?:me|it|that)\s+wrong|{_NOT}\s+understand)\b"
    rf"|\bi\s+{_NOT}\s+(?:ask|mean|say|want)\s+(?:for\s+)?(?:that|this|it)\b")

#: "i was asking u to Y", "i asked you about Y", "my question was Y", "i meant Y".
_RESTATED = re.compile(
    rf"(?i)\b(?:i\s+(?:was\s+asking|asked|am\s+asking|was\s+talking|was\s+telling|meant|mean|wanted|told)"
    rf"(?:\s+{_YOU})?|my\s+(?:question|request|point)\s+(?:was|is))\s*[:,]?\s+(?:to\s+|about\s+|for\s+|that\s+)?"
    r"(?P<restated>\S.{5,})$")

#: "too generic", "this is basic", "useless", "not helpful", "everyone knows that".
_GENERIC = re.compile(
    rf"(?i)\b(?:too|so|very|just|really|all)\s+(?:generic|basic|shallow|vague|obvious|simple|general|common|boring|surface[\s-]level)\b"
    rf"|\b(?:this|that|it|these|those|your\s+{_ANSWER})(?:['’]s|\s+is|\s+are|\s+was)\s+(?:generic|basic|shallow|useless|"
    r"garbage|rubbish|trash|nonsense|pointless|worthless|lame|weak|not\s+(?:helpful|useful|enough|good\s+enough|helping))\b"
    r"|\beveryone\s+(?:already\s+)?knows\b|\bnot\s+(?:helpful|useful|good\s+enough)\b")
#: "look better ideas", "give me real answers": a wish for better, which is feedback only beside a complaint
#: ("give me better ideas for a logo" on its own is an ordinary request).
_BETTER_ASK = re.compile(
    rf"(?i)\b(?:give|show|find|look|tell|get|want|need)\s+(?:me\s+)?(?:for\s+|up\s+)?(?:something\s+|some\s+)?"
    rf"(?:better|real|actual|serious|new|different|bigger|higher|smarter)\s+(?:{_ANSWER}|ways?)\b")
#: "look again at me", "check the screen again": a look at something in front of the camera, not at the answer.
_LOOK_AT = re.compile(r"(?i)\b(?:at|on)\s+(?:me|my\b|us|the\s+(?:screen|camera|picture|photo|image)|"
                      r"this\s+(?:image|photo|picture|file|page))")

#: "dont treat this fact as i can change", "that can change", "it's only for now", "not permanent".
_CHANGEABLE = re.compile(
    rf"(?i)\b(?:{_NOT}|stop)\s+(?:treat(?:ing)?|tak(?:e|ing)|assum(?:e|ing)|consider(?:ing)?|see(?:ing)?|us(?:e|ing)|count(?:ing)?)\s+"
    r"(?:this|that|it|these|those|them|(?:my|the)\s+[\w$\s]{1,40}?)\s+(?:as\s+)?(?:an?\s+)?(?:fixed\s+|permanent\s+|hard\s+)?"
    r"(?:facts?|fixed|permanent|constants?|limits?|limitation|given|forever|final|constraint)\b"
    r"|\b(?:i|it|that|this|things?|they|everything|my\s+[\w$\s]{1,30}?)\s+can\s+(?:be\s+)?change[ds]?\b"
    r"|\b(?:that|this|it)(?:['’]s|\s+is)\s+(?:temporary|just\s+for\s+now|only\s+for\s+now|not\s+(?:permanent|forever|fixed|final))\b"
    r"|\bi\s+(?:can|will|could)\s+(?:change|upgrade|improve|fix|get\s+(?:more|better|another)|buy|move)\b")

#: Device or media commands that happen to contain "same" or "again" ("play the same song again").
_COMMAND = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:open|close|play|pause|stop|send|email|call|text|type|click|press|save|delete|run|install|"
    r"download|upload|turn\s+(?:on|off)|set|schedule|remind|book|buy|pay|order|generate|create|draw|make\s+(?:an?\s+)?image)\b")


@dataclass(frozen=True)
class Feedback:
    """A reading of the owner's message as feedback on the last answer."""

    kinds: frozenset[str] = field(default_factory=frozenset)
    #: What he says he did not ask about ("model error").
    denied: str = ""
    #: The request he says he did make ("look better ideas to make money and become billionaire by 2030").
    restated: str = ""

    @property
    def rejects(self) -> bool:
        """Whether the last answer is refused as it stands."""
        return bool(self.kinds & REJECTING)

    @property
    def wants_more(self) -> bool:
        """Whether the same question should be worked on again, further than last time."""
        return bool(self.kinds & {"deeper", "repeated", "generic"})

    @property
    def about_answer(self) -> bool:
        """Whether the message is about the last answer rather than a request of its own."""
        return bool(self.kinds - {"changeable"}) or ("changeable" in self.kinds and not self.restated)

    def describe(self) -> str:
        """One plain sentence for the record and for the reply writer."""
        parts = []
        if "repeated" in self.kinds:
            parts.append("says the answer repeated an earlier one")
        if "generic" in self.kinds:
            parts.append("says the answer was too generic")
        if "wrong" in self.kinds:
            parts.append("says the answer was wrong")
        if "off_topic" in self.kinds:
            parts.append("says the answer was about the wrong subject"
                         + (f" (he did not ask about “{self.denied}”)" if self.denied else ""))
        if "deeper" in self.kinds:
            parts.append("asks for a deeper look")
        if "restated" in self.kinds:
            parts.append(f"restates the request as “{self.restated}”")
        if "changeable" in self.kinds:
            parts.append("says his circumstances can change and must not be treated as fixed")
        return "; ".join(parts)

    def to_dict(self) -> dict[str, object]:
        return {"kinds": sorted(self.kinds), "denied": self.denied, "restated": self.restated,
                "rejects": self.rejects, "wants_more": self.wants_more}


def _clean(value: str) -> str:
    return " ".join(str(value or "").split()).strip(" ,.;:!?\"'“”")


def read(text: str) -> Feedback | None:
    """The feedback a message carries about the last answer, or None when it carries none.

    A message that only *asks* for something ("give me better ideas for a logo") is not feedback: at least one
    phrase must point at the answer that was given, deny what it was about, or ask for the same thing again.
    """
    value = " ".join(str(text or "").split())
    if not value or len(value) > 600:
        return None
    if _COMMAND.match(value):
        return None
    kinds: set[str] = set()
    denied = restated = ""
    if _REPEATED.search(value):
        kinds.add("repeated")
    found = _DENIED.search(value)
    if found:
        kinds.add("off_topic")
        denied = _clean(found.group("denied"))[:160]
    elif _OFF_TOPIC.search(value):
        kinds.add("off_topic")
    try:
        from app.owner_feedback import verdict

        judged = verdict(value)
    except Exception:  # noqa: BLE001 - a verdict reading must never stop feedback being read
        judged = None
    if judged is not None and judged[0] < 0:
        kinds.add("wrong")
    # A restatement only counts beside a complaint: "i meant" inside an ordinary sentence is not feedback.
    matches = list(_RESTATED.finditer(value))
    if matches and (kinds or re.match(r"(?i)^(?:(?:no|nope|sorry|actually|hey|hayat)[,.!\s]+)*(?:i\s+(?:meant|mean|was\s+asking|asked)\b|my\s+question)", value)):
        restated = _clean(matches[-1].group("restated"))[:400]
        if restated and len(restated.split()) >= 2:
            kinds.add("restated")
        else:
            restated = ""
    if (_GENERIC.search(value) and (kinds or len(value.split()) <= 14)) or (kinds and _BETTER_ASK.search(value)):
        kinds.add("generic")
    # "deeper" alone is feedback only in a short message with nothing else to do ("go deeper", "look again"):
    # "i just did check again" reports what he did, and "look again at me" asks for the camera.
    if _DEEPER.search(value) and not _LOOK_AT.search(value):
        own = subject_words(value)
        if kinds or (len(own) <= 1 and not re.match(r"(?i)^\s*i\b", value)):
            kinds.add("deeper")
    if _CHANGEABLE.search(value):
        kinds.add("changeable")
    if not kinds:
        return None
    return Feedback(frozenset(kinds), denied, restated)


#: Words that belong to the complaint itself, never to a subject ("look again but deeper" names nothing).
_FEEDBACK_WORDS = frozenset("""
look looking search research think dig check analyse analyze investigate explore study again but and deeper harder
further more deeply carefully thoroughly seriously depth properly this that the it please hayat you your same
reply answer response result list idea ideas thing stuff still just now try into time much way lot even real
proper actual deep through over can could would pls bro yes okay then not all for with from was are what
""".split())


def subject_words(text: str) -> list[str]:
    """The words of a feedback message that are not part of the complaint (usually none)."""
    return [word for word in re.findall(r"[^\W\d_]{3,}", str(text or "").lower()) if word not in _FEEDBACK_WORDS]
