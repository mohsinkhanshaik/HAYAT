"""Which conversation and which topic a message belongs to, and what it means in that topic.

Every reply used to be written from the last N messages of the session, whatever they were about. Three real
failures came out of that:

* "Look at the latest and predict for nxt year", sent right after a question about India's GDP forecast, was read
  on its own: it routed to a generic action and its words were searched as typed (the spec's example: "NXT" is a
  wrestling brand, not "next year").
* "back to the gpu question from earlier" restored nothing: the earlier exchange had scrolled out of the window,
  and the router had no idea a topic existed to go back to.
* Unrelated threads in one chat were all handed to the model together, so an answer about pasta could be written
  next to an exchange about a tax return.

This module keeps a persistent record (``topics.db`` beside the memory database) of conversations, topics and
messages, each with its own id and time, and decides for each new message how it relates to what came before:

``continue``          the same topic
``new_topic``         an unrelated subject in the same chat (the earlier topic is kept separately)
``new_conversation``  first message, or the first after a long gap
``return``            back to an earlier topic; only that topic's history is restored
``reference``         points at an earlier question or answer of the current topic
``correction``        "no, I meant ..." -- the current topic, misunderstood
``task_status``       "is it done?" -- the topic whose work is still running
``other_person``      information about someone else (kept apart from facts about the owner)
``connect``           "how does this relate to the GPU question" -- two topics on purpose
``small_talk``        "thanks", "ok" -- stays in the current topic and changes nothing

A message that leans on its topic ("what about china", "and for 2028?", "predict for nxt year") is *anchored*: it
gets a standalone reading built from the topic's own subject ("latest GDP growth forecast for India in 2027"),
which is what gets routed and searched. Only the owner's words are ever recorded as what he said.

Decisions are lexical and deterministic (word stems plus a small concept thesaurus), so they cost about a
millisecond and never wait on a model. They are also checkable: the owner can say a message was a separate topic,
or the same one, and each such override is kept as a measured routing error (``stats()``), not hidden.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

RELATIONS = ("continue", "new_topic", "new_conversation", "return", "reference", "correction", "task_status",
             "other_person", "connect", "small_talk")
#: A message after this long a silence starts a new conversation (unless it explicitly goes back to something).
NEW_CONVERSATION_GAP = timedelta(hours=6)
#: A message that leans on its topic is only anchored to one that was active this recently.
ANCHOR_WINDOW = timedelta(hours=3)
#: How far back "back to the ... question" may reach.
RETURN_HORIZON = timedelta(days=45)
CONTINUE_THRESHOLD = 0.25
RETURN_THRESHOLD = 0.5
MAX_TERMS = 48

# --------------------------------------------------------------------------------------------- words


#: Chat shorthand that changes meaning when read literally ("nxt" is not a name).
SHORTHAND = {
    "nxt": "next", "nex": "next", "yr": "year", "yrs": "years", "yeat": "year", "mnth": "month", "mnths": "months",
    "wk": "week", "wks": "weeks", "prev": "previous", "abt": "about", "bcz": "because", "bc": "because",
    "coz": "because", "cuz": "because", "pls": "please", "plz": "please", "thx": "thanks", "u": "you", "ur": "your",
    "r": "are", "wat": "what", "wats": "what's", "whats": "what's", "hw": "how", "hows": "how's", "tmrw": "tomorrow",
    "tmr": "tomorrow", "2day": "today", "2morrow": "tomorrow", "b4": "before", "w/": "with", "info": "information",
    "econ": "economy", "govt": "government", "gov": "government", "approx": "approximately", "diff": "difference",
    "vs": "versus", "v": "versus", "pred": "prediction", "preds": "predictions",
}

_STOP = frozenset("""
a an the and or but if so of to in on at by for from with about into onto over under than then there here this that
these those it its it's is are was were be been being am do does did doing done have has had having will would shall
should can could may might must i me my mine we us our you your yours he him his she her they them their what which
who whom whose when where why how whats what's hows how's please pls thanks thank ok okay yes no yeah yep nope just
also too very really quite more most less least much many some any all each every other another such only own same
again still even ever never now then today tomorrow yesterday tell give show let lets let's know think want need
like get got make made go going look looking see seen find found say said ask asked one ones thing things something
anything everything stuff way ways lot lots bit kind sort enough fine possible hayat hey hi hello dear sir well right sure maybe perhaps
please whether while because though although since until up down out off back new old good bad best better big small
time times year years month months week weeks day days next last previous current latest recent recently earlier
before after soon later question questions answer answers topic topics talk talked talking discuss discussed said
predict prediction predictions forecast forecasts forecasting estimate projection projections outlook trend trends
compare comparison versus update updates information info detail details explain explanation tell about regarding
search searching searched research researching references reference internet web online whole complete entire full
sources source google browse lookup properly thoroughly deeper deep
""".split())

#: Words that make a message about a *time slice* or *view* of its subject, not a subject of its own.
_SLICE_WORDS = frozenset("""
latest recent newest current now today update updates updated predict prediction predictions forecast forecasts
projection projections outlook trend trends estimate estimates next last previous this coming following year years
month months quarter week compare comparison versus vs change changes rise fall growth expected expect expectation
future
""".split())

#: A small thesaurus: words that name the same field, so "inflation" continues a talk about "GDP".
CONCEPTS: dict[str, frozenset[str]] = {
    "economy": frozenset("economy economic economics economies gdp inflation recession unemployment imf cpi monetary "
                         "fiscal deflation stagflation macroeconomic central bank interest rates repo".split()),
    "markets": frozenset("stock stocks shares nasdaq dow s&p index etf etfs portfolio trading invest investing "
                         "investment dividend equities bonds".split()),
    "crypto": frozenset("bitcoin btc ethereum eth crypto cryptocurrency token tokens solana blockchain defi wallet "
                        "altcoin altcoins".split()),
    "hardware": frozenset("gpu gpus vram cpu ram nvidia rtx graphics laptop processor amd intel ssd motherboard "
                          "cuda chip chips".split()),
    "ai": frozenset("llm llms ai model models neural training finetune fine-tune inference ollama gpt qwen "
                    "transformer embedding embeddings quantized quantization".split()),
    "code": frozenset("python code coding bug bugs function script javascript compile programming api debug "
                      "exception traceback library".split()),
    "weather": frozenset("weather rain raining temperature storm snow sunny humidity wind cloudy umbrella".split()),
    "health": frozenset("health doctor symptom symptoms pain medicine fever disease diet sleep exercise injury "
                        "headache blood vaccine vaccines vaccination booster boosters immune immunity virus viruses "
                        "infection antibodies".split()),
    "travel": frozenset("flight flights hotel hotels trip travel visa airport booking itinerary airline".split()),
    "language": frozenset("arabic language lesson lessons grammar vocabulary translate translation pronounce "
                          "pronunciation fluent".split()),
    "jobs": frozenset("job jobs cv resume interview hiring salary career recruiter vacancy".split()),
    "sports": frozenset("match football cricket soccer wwe wrestling nba score league tournament fixture".split()),
    "food": frozenset("recipe recipes cook cooking pasta food dinner lunch meal bake baking ingredients".split()),
    "faith": frozenset("quran surah prayer salah islam ayah hadith".split()),
    "business": frozenset("business customers customer sales revenue invoice leads marketing startup clients "
                          "pricing profit".split()),
}
_CONCEPT_OF = {word: name for name, words in CONCEPTS.items() for word in words}
CONCEPT_WEIGHT = 0.7

_WORD = re.compile(r"[^\W_][\w'&-]*", re.UNICODE)
_YEAR = re.compile(r"\b(19[5-9]\d|20\d\d|21\d\d)\b")


def expand(text: str) -> str:
    """The message with chat shorthand spelled out ("predict for nxt yr" -> "predict for next year")."""
    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        replacement = SHORTHAND.get(word.lower())
        return replacement if replacement is not None else word
    return re.sub(r"(?<![\w/])[A-Za-z0-9/]+(?![\w/])", fix, " ".join(str(text or "").split()))


def resolve_relative_years(text: str, today: date | None = None) -> str:
    """'next year' -> '2027' on this computer's calendar; the year is arithmetic, never a guess."""
    year = (today or date.today()).year
    value = re.sub(r"(?i)\b(?:the\s+)?(?:next|coming|following)\s+year\b", str(year + 1), text)
    value = re.sub(r"(?i)\b(?:the\s+)?(?:last|previous|past)\s+year\b", str(year - 1), value)
    return re.sub(r"(?i)\bthis\s+year\b", str(year), value)


def _stem(word: str) -> str:
    word = word.lower().strip("'-&")
    for suffix in ("ations", "ation", "ings", "ing", "ies", "es", "ed", "ly", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            word = word[: -len(suffix)] + ("y" if suffix == "ies" else "")
            break
    return word[:7]


def content_words(text: str) -> list[str]:
    """The words of a message that say what it is about (shorthand expanded, filler dropped)."""
    words = [word.lower().strip("'-") for word in _WORD.findall(expand(text))]
    return [word for word in words if len(word) >= 2 and word not in _STOP and not word.isdigit()
            and (len(word) >= 3 or word in _CONCEPT_OF)]


def terms(text: str) -> dict[str, float]:
    """Stems and concept tokens of a message, each with its weight."""
    found: dict[str, float] = {}
    for word in content_words(text):
        found[_stem(word)] = 1.0
        concept = _CONCEPT_OF.get(word)
        if concept:
            found["#" + concept] = CONCEPT_WEIGHT
    return found


def similarity(message: dict[str, float], topic: dict[str, float]) -> float:
    """How much of the message is about the topic: matched weight over the message's own weight."""
    if not message or not topic:
        return 0.0
    total = sum(message.values())
    # Presence, not accumulated weight: a concept the topic has met counts in full for the message.
    matched = sum(weight for key, weight in message.items() if topic.get(key, 0.0) >= 0.3)
    return round(matched / total, 3) if total else 0.0


def own_subject_words(text: str) -> list[str]:
    """Content words that are neither filler nor a time slice/view: what the message names by itself."""
    return [word for word in content_words(text) if word not in _SLICE_WORDS and not _YEAR.fullmatch(word)]


# --------------------------------------------------------------------------------------------- cues

_SMALL_TALK = re.compile(
    r"(?i)^\s*(?:hi|hello|hey|salam|salaam|assalamu\s*alaikum|good\s+(?:morning|afternoon|evening|night)|thanks?(?:\s+you)?"
    r"|thank\s+you(?:\s+so\s+much)?|thx|ty|ok(?:ay)?|k|cool|nice|great|awesome|perfect|got\s+it|i\s+see|alright|sure"
    r"|yes|yeah|yep|yup|no|nope|nah|hmm+|lol|haha+|wow|bye|see\s+you|good\s+job|well\s+done|exactly|right)"
    r"(?:[\s,.!]+(?:hayat|thanks?|thank\s+you|man|bro|dude|mate))*[\s.!?]*$")
_NEW_TOPIC = re.compile(
    r"(?i)\b(?:new\s+(?:topic|question|subject)|different\s+(?:topic|question|subject|thing)|(?:something|anything)\s+else"
    r"|unrelated(?:\s+question)?|change\s+(?:of\s+)?(?:the\s+)?subject|switch(?:ing)?\s+(?:gears|topics?)|on\s+another\s+note"
    r"|by\s+the\s+way|\bbtw\b|another\s+question|separate\s+(?:question|topic)|moving\s+on|forget\s+(?:that|about\s+that)"
    r"|let'?s\s+talk\s+about)\b")
_RETURN = re.compile(
    r"(?i)\b(?:(?:let'?s\s+)?(?:go(?:ing)?|get(?:ting)?|come|coming)\s+back\s+to|back\s+to|return(?:ing)?\s+to|revisit(?:ing)?"
    r"|(?:as|like)\s+(?:i|we|you)\s+(?:said|asked|discussed|mentioned)\s+(?:earlier|before|yesterday|last\s+time)"
    r"|(?:remember|recall)\s+(?:when|what|the|that|our|my)\b"
    r"|(?:earlier|before|yesterday|last\s+time)\s+(?:you|we|i)\s+(?:said|asked|discussed|talked|mentioned|were)"
    r"|(?:we|i|you)\s+(?:discussed|talked\s+about|were\s+discussing|were\s+talking\s+about|mentioned|asked\s+about)"
    r"\s+(?:earlier|before|yesterday|last\s+time|this\s+morning)"
    r"|(?:from|about)\s+(?:the|that|our|my)\s+[\w\s'-]{1,40}?\s+(?:earlier|before|yesterday|last\s+time)"
    r"|the\s+[\w\s'-]{1,40}?\s+(?:question|topic|thing|discussion|conversation|chat)\s+(?:from\s+)?(?:earlier|before|yesterday))\b")
_RETURN_FILLER = frozenset("""
go going get getting come coming back return returning revisit revisiting remember recall earlier before yesterday
last time morning question topic thing discussion conversation chat discussed talked talking mentioned asked
""".split())
_CORRECTION = re.compile(
    r"(?i)^(?:(?:no|nope|nah|sorry|actually|wait|hey|hayat)[,.!\s]+)*(?:i\s+(?:mean|meant|was\s+asking|was\s+talking)"
    r"|i\s+(?:didn['’]?t|dint|did\s+not)\s+(?:ask|mean|say|want)|that(?:'s|’s|\s+is|\s+was)\s+not\s+what\s+i"
    r"|not\s+(?:that|this|what\s+i)|(?:you|u)\s+(?:misunderstood|got\s+(?:me|it|that)\s+wrong|(?:didn['’]?t|did\s+not)\s+understand)"
    r"|wrong\s+(?:topic|thing|one|answer|results?)|(?:that|this|these|those)\s+(?:results?\s+)?(?:is|are|was|were)\s+"
    r"(?:not\s+(?:relevant|related|it)|irrelevant|unrelated|wrong))")
_TASK_STATUS = re.compile(
    r"(?i)^(?:(?:so|and|hey|ok(?:ay)?|hayat)[,\s]+)*(?:is\s+it\s+(?:done|ready|finished|complete)|are\s+(?:you|u)\s+(?:done|finished)"
    r"|did\s+(?:you|u)\s+(?:finish|find|get)\s+(?:it|anything|that)|any\s+(?:update|news|progress|luck)"
    r"|(?:what(?:'s|\s+is)\s+the\s+)?status(?:\s+of)?|how(?:'s|\s+is)\s+(?:it|that|the\s+\w+)\s+going"
    r"|still\s+(?:working|searching|looking)|where\s+are\s+(?:you|we)\s+(?:on|with)\s+(?:it|that|the))\b")
_CONNECT = re.compile(
    r"(?i)\b(?:connect|relate|link|tie|combine|bring\s+together|compare)\b.{0,60}\b(?:with|to|and)\b"
    r"|\bhow\s+(?:does|do|is|are|would|could)\b.{0,60}\b(?:relate|connect|link|tie\s+in|fit)\b"
    r"|\b(?:relationship|connection|link)\s+between\b")
_PERSON = re.compile(
    r"(?i)\b(?:my|our)\s+(?P<who>(?:little\s+|big\s+|older\s+|younger\s+)?(?:brother|sister|mother|father|mom|mum|mam|dad|"
    r"wife|husband|son|daughter|friend|cousin|uncle|aunt|boss|colleague|coworker|co-worker|neighbou?r|grandmother|"
    r"grandfather|grandma|grandpa|nephew|niece|fiance|fiancée|partner|teacher|student|client|manager|roommate))\b"
    r"(?:\s+(?P<name>[A-Z][a-z]{2,}))?")
#: Someone or something joining the plan under discussion ("my wife is coming too. How many of us now?"). Live chat,
#: 2026-11-11: that message shared no word with "a trip to Lisbon with my two kids", so it became a new topic about
#: the wife, the trip's history was set aside, and "how many of us" was answered as two instead of four.
_JOINING = re.compile(
    r"(?i)\b(?:is|are|'s|'re|will\s+be|was|were)\s+(?:also\s+)?(?:coming|joining|going|travel(?:l)?ing|tagging\s+along|"
    r"in)\s+(?:too|as\s+well|also|with\s+(?:us|me|them))\b"
    r"|\b(?:coming|joining|going)\s+(?:too|as\s+well)\b|\bjoin(?:s|ing)?\s+us\b|\bhow\s+many\s+of\s+us\b"
    r"|\b(?:add|include|bring(?:ing)?)\s+(?:my|our)\s+\w+\s+(?:too|as\s+well|also)\b")
#: A first-person update to what was just said: "Actually I moved to Austin", "Correction: we have $800".
_UPDATE = re.compile(
    r"(?i)^\s*(?:actually|correction|update|no\s+wait|wait|oh\s+and|scratch\s+that|change\s+of\s+plans?)[,:\s-]+"
    r"(?:now\s+|i\s+mean\s+)?(?:i|i['’]m|i['’]ve|we|we['’]re|we['’]ve|my|our)\b")
#: A refinement of the last answer: "keep it under 50 dollars", "make it shorter", "something cheaper", "under $50".
#: Live, 2026-11-12: after gift ideas for a sister who paints, "keep it under 50 dollars" became a new topic, the
#: gift talk was set aside, and the reply asked "What are we buying?".
_REFINES = re.compile(
    r"(?i)^\s*(?:(?:ok(?:ay)?|and|but|also|hmm+|actually)[,\s]+)*(?:(?:keep|make)\s+(?:it|them|that|this)\b|"
    r"(?:under|below|less\s+than|no\s+more\s+than|max(?:imum)?|within)\s+[$€£₹]?\s?\d|"
    r"(?:something|anything|one|ones)\s+(?:cheaper|smaller|bigger|shorter|longer|simpler|faster|better|else|different)|"
    r"(?:cheaper|smaller|bigger|shorter|longer|simpler)\s+(?:one|ones|please|options?)|"
    r"(?:only|just)\s+(?:the\s+)?(?:top|first|best|cheapest)\b|in\s+(?:a\s+)?(?:list|table|bullets?)\b)")
#: A message that carries its own calendar or arithmetic subject never leans on a topic: "What date will it be 45
#: days from today?" was read as pointing at "it", and "What day of the week was 1 January 2000?" as asking about a
#: year of the subject; both were re-read as "…, about the exactly five words describing the ocean" and searched.
_SELF_CONTAINED = re.compile(
    r"(?i)\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d+\s+(?:days?|weeks?|months?|years?|hours?|minutes?)\s+(?:from|after|before|since|ago|until|till)\b"
    r"|\b(?:from|since|until|till)\s+(?:today|now|tomorrow|yesterday)\b|\bday\s+of\s+the\s+week\b"
    r"|\d\s*[-+*/x×÷^]\s*\d|\b\d+(?:\.\d+)?\s*%\s+of\s+\d")
#: Messages that start by pointing at the topic instead of naming a subject.
_LEANING_START = re.compile(
    r"(?i)^\s*(?:(?:and|but|also|so|or|plus|ok(?:ay)?|then|hmm+)[,\s]+)*(?:"
    r"(?:what|how)\s+about|and\s+(?:for|in|with|on)|same\s+(?:for|with|thing\s+for)|what\s+if|compared?\s+(?:to|with)|"
    r"versus|vs\.?|(?:and|but|also|so|plus)\b|(?:for|in)\s+(?:19|20)\d\d\b)")
_POINTER = re.compile(
    r"(?i)\b(?:it|its|it's|that|this|they|them|their|those|these|there|same|such|one|he|she|him|her|his|hers)\b")
_SLICE = re.compile(
    r"(?i)\b(?:latest|newest|recent(?:ly)?|current(?:ly)?|now|update[sd]?|predict(?:ion|ions|ed)?|forecast(?:s|ing)?|"
    r"projection|outlook|trend|estimate|next|last|previous|coming|(?:19|20)\d\d|compare|versus|vs)\b")
#: Messages that ask the computer to do something are never re-read through a topic ("open it" stays "open it").
_DEVICE_COMMAND = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:open|close|click|type|press|save|delete|remove|send|email|mail|post|play|pause|stop|run|"
    r"install|uninstall|start|launch|kill|restart|shut\s*down|turn\s+(?:on|off)|switch|move|copy|rename|print|download|"
    r"upload|record|call|text|message|share|zoom|scroll|mute|unmute|volume|lock|book|buy|pay|order|cancel|undo|redo|"
    r"remember|forget|note|remind|schedule|set)\b")
_QUESTION = re.compile(r"(?i)^\s*(?:what|which|who|whom|whose|when|where|why|how|is|are|was|were|do|does|did|will|"
                       r"would|can|could|should|has|have)\b|\?\s*$")
_LEADING_QUESTION = re.compile(
    r"(?i)^\s*(?:(?:hey|hi|ok(?:ay)?|so|and|please|hayat|btw|by\s+the\s+way|anyway|also|oh)[,\s]+)*"
    r"(?:(?:(?:can|could|would)\s+(?:you|u)\s+(?:please\s+)?)?(?:tell\s+me(?:\s+about)?|find(?:\s+(?:out|me))?|"
    r"look\s+up|search(?:\s+for)?|check|give\s+me|show\s+me|explain(?:\s+to\s+me)?|describe|"
    r"i\s+(?:want|need)(?:\s+to\s+know)?)\s+)?"
    r"(?:(?:what|who|how|why|when|where|which)(?:['’]s)?\s+)?"
    r"(?:(?:is|are|was|were|does|do|did|will|would|can|could|should|has|have)\s+)?"
    r"(?:(?:the|a|an|some)\s+)?(?:(?:good|best|great|nice|decent)\s+)?")


def is_small_talk(text: str) -> bool:
    return bool(_SMALL_TALK.match(" ".join(str(text or "").split())))


def subject_phrase(text: str) -> str:
    """The subject of a question with its question words removed: 'What is the GDP forecast ...?' -> 'GDP forecast ...'."""
    value = " ".join(expand(text).split()).strip(" ?!.")
    stripped = _LEADING_QUESTION.sub("", value, count=1).strip(" ,;:?!.")
    return (stripped if len(stripped) >= 3 else value)[:160]


def _entities(phrase: str) -> list[str]:
    """Names in a subject phrase: capitalised words after the first, but not acronyms ('India', not 'GDP')."""
    words = phrase.split()
    return [word.strip(",.;:") for word in words[1:] if re.fullmatch(r"[A-Z][a-z][\w'-]+[,.;:]?", word)]


def _about_the_last_answer(text: str) -> bool:
    """'do it', 'go on', 'how did you get that?', 'no I meant ...' are about the last reply, not its topic's subject."""
    try:
        from app.agent.conversation import _CONTINUATION_PHRASES, _CORRECTION_FOLLOWUP, _PROPOSAL_FOLLOWUP
        from app.intent.revisit import parse as revisit

        from app.investigation import follow_up_on_failure

        value = " ".join(str(text or "").split())
        # "ok can u fix it?" acts on what was just said; read as "fix it, about <the topic>" it became a tool demo.
        return bool(_CONTINUATION_PHRASES.match(value) or _PROPOSAL_FOLLOWUP.match(value)
                    or _CORRECTION_FOLLOWUP.match(value) or revisit(value) or follow_up_on_failure(value))
    except Exception:
        log_swallowed(_log, "checking whether a message is about the last answer")
        return False


def leans_on_topic(text: str) -> str:
    """Why a message cannot be read on its own ('' when it names its own subject or is a command)."""
    value = " ".join(expand(text).split())
    if (not value or _DEVICE_COMMAND.match(value) or is_small_talk(value) or _SELF_CONTAINED.search(value)
            or _about_the_last_answer(text)):
        return ""
    own = own_subject_words(value)
    # Only a question leans this way ("and for 2028?", "what about china"). "but finally weekend hai" is a statement;
    # re-read as "..., about the bro aaj office mein bahut kaam tha" it was answered three times (live, 2026-11-12).
    if (_LEANING_START.match(value) and len(own) <= 3
            and (_QUESTION.search(value) or re.match(r"(?i)^\s*(?:(?:and|but|so|ok(?:ay)?)[,\s]+)?(?:(?:what|how)\s+about|"
                                                       r"(?:and|same)\s+(?:for|in|with)|for\s+(?:19|20)\d\d)\b", value))):
        return "starts from the previous subject"
    if _SLICE.search(value) and len(own) <= 1:
        return "asks for a time or view of the subject without naming it"
    if _QUESTION.search(value) and _POINTER.search(value) and len(own) <= 2:
        return "points at the subject with a pronoun"
    return ""


# --------------------------------------------------------------------------------------------- results


@dataclass
class TopicDecision:
    relation: str
    topic_id: str
    title: str
    conversation_id: str
    message_id: int = 0
    previous_topic_id: str = ""
    connected_topic_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    reasons: list[str] = field(default_factory=list)
    #: The standalone reading of a message that leaned on its topic ('' when it stands on its own).
    anchored: str = ""
    #: A search query for the anchored reading.
    query: str = ""
    person: str = ""
    original: str = ""
    restored: list[dict[str, Any]] = field(default_factory=list)
    set_aside: list[str] = field(default_factory=list)
    topic_subject: str = ""
    #: What the message says about the last answer (app/answer_feedback.py): kinds, what he denied, what he restated.
    feedback: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["restored"] = len(self.restored)
        return data

    def note(self) -> str:
        """What the reply writer must know about this message's place in the conversation ('' when nothing)."""
        parts: list[str] = []
        if self.relation == "new_topic" and self.set_aside:
            parts.append("The owner started a new topic. Earlier messages about other topics ("
                         + "; ".join(f"“{title}”" for title in self.set_aside[:4])
                         + ") were set aside: do not bring them into this answer unless he connects them.")
        elif self.relation == "return":
            parts.append(f"The owner went back to an earlier topic, “{self.title}”. "
                         + ("Its earlier exchange is restored below; continue from it, without repeating it."
                            if self.restored else "Continue that topic."))
        elif self.relation == "connect" and self.connected_topic_ids:
            parts.append("The owner is connecting this topic with another one discussed earlier; both are in context. "
                         "Use only relationships the evidence supports and label any speculative link as such.")
        elif self.relation == "correction" and self.feedback.get("kinds"):
            said = str(self.feedback.get("said") or "is not satisfied with the last answer")
            parts.append(f"In the topic “{self.title}”, the owner {said}. The exchange he means is in the "
                         "conversation below. Do not answer about the complaint and do not repeat the refused answer: "
                         "answer the request itself again, and say what is different this time."
                         + (" Its earlier exchange is restored below." if self.restored else ""))
        elif self.relation == "correction":
            parts.append(f"This message corrects how you understood the topic “{self.title}”. Answer the corrected "
                         "request itself; do not search for or discuss the complaint.")
        if self.anchored:
            parts.append(f"The message leans on the topic “{self.title}”. Read it as: “{self.anchored}”. Chat "
                         "shorthand is shorthand (nxt = next, yr = year), never the name of something unrelated.")
        if self.person:
            parts.append(f"This message is about the owner's {self.person}, not about the owner: keep facts about "
                         "that person separate from facts about him.")
        return " ".join(parts)


# --------------------------------------------------------------------------------------------- store


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(text: str) -> str:
    return hashlib.sha1(" ".join(str(text or "").split()).lower().encode("utf-8"), usedforsecurity=False).hexdigest()


def _parse(moment: str | None) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(moment))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class ConversationTopics:
    """Persistent conversations, topics and messages, and the decision for each new message."""

    SOURCE = "topics"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._ready = False
        #: Set by the runtime: the event bus.
        self.events: Any = None

    def _emit(self, event_type: str, payload: dict[str, Any], **scope: Any) -> None:
        """Report activity on the event bus (ids and counts only, never text), so the Brain view shows it."""
        events = getattr(self, "events", None)
        if events is None:
            return
        try:
            events.publish(event_type, self.SOURCE, payload, **scope)
        except Exception:
            log_swallowed(_log, f"publishing {event_type}")

    def status(self) -> dict[str, Any]:
        stats = self.stats()
        current = next((row["title"] for row in self.topics(None, 30) if row["current"]), None)
        return {"current_topic": current, "topics": stats["topics"], "conversations": stats["conversations"],
                "messages_placed": stats["decisions"], "follow_ups_read_in_topic": stats["anchored_follow_ups"],
                "corrected_by_owner": stats["flagged_wrong"]}

    # ---- storage

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS conversations(
                    conversation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, started_at TEXT NOT NULL,
                    last_at TEXT NOT NULL, active_topic_id TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS conversations_session ON conversations(session_id, last_at);
                CREATE TABLE IF NOT EXISTS topics(
                    topic_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    title TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', subject_intent TEXT NOT NULL DEFAULT '',
                    terms_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'active',
                    merged_into TEXT NOT NULL DEFAULT '', open_task TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, turns INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS topics_session ON topics(session_id, updated_at);
                CREATE TABLE IF NOT EXISTS topic_messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, content_hash TEXT NOT NULL,
                    relation TEXT NOT NULL DEFAULT '', anchored TEXT NOT NULL DEFAULT '', person TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0, reasons TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS topic_messages_topic ON topic_messages(topic_id, id);
                CREATE INDEX IF NOT EXISTS topic_messages_hash ON topic_messages(session_id, content_hash);
                CREATE TABLE IF NOT EXISTS topic_links(
                    a TEXT NOT NULL, b TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(a, b, kind));
                CREATE TABLE IF NOT EXISTS topic_overrides(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, action TEXT NOT NULL,
                    from_topic TEXT NOT NULL DEFAULT '', to_topic TEXT NOT NULL DEFAULT '',
                    decided_relation TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
            """)
            self._ready = True
        return db

    def _topic(self, db: sqlite3.Connection, topic_id: str) -> sqlite3.Row | None:
        row = db.execute("SELECT * FROM topics WHERE topic_id=?", (topic_id,)).fetchone()
        # A merged topic speaks through the one it was merged into.
        hops = 0
        while row is not None and row["merged_into"] and hops < 8:
            row = db.execute("SELECT * FROM topics WHERE topic_id=?", (row["merged_into"],)).fetchone()
            hops += 1
        return row

    def _conversation(self, db: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
        return db.execute("SELECT * FROM conversations WHERE session_id=? ORDER BY last_at DESC LIMIT 1",
                          (session_id,)).fetchone()

    def _new_conversation(self, db: sqlite3.Connection, session_id: str, now: datetime) -> str:
        conversation_id = "c-" + uuid.uuid4().hex[:12]
        db.execute("INSERT INTO conversations(conversation_id,session_id,started_at,last_at) VALUES(?,?,?,?)",
                   (conversation_id, session_id, now.isoformat(), now.isoformat()))
        return conversation_id

    def _new_topic(self, db: sqlite3.Connection, conversation_id: str, session_id: str, text: str, now: datetime,
                   intent: str = "") -> str:
        topic_id = "t-" + uuid.uuid4().hex[:12]
        # "hello" names no subject: the topic waits for the first message that does.
        subject = subject_phrase(text) if own_subject_words(text) else ""
        db.execute("INSERT INTO topics(topic_id,conversation_id,session_id,title,subject,subject_intent,terms_json,"
                   "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (topic_id, conversation_id, session_id, _title(subject), subject, intent,
                    json.dumps(terms(text)), now.isoformat(), now.isoformat()))
        return topic_id

    @staticmethod
    def _learn_terms(db: sqlite3.Connection, topic: sqlite3.Row, text: str, weight: float, now: datetime) -> None:
        known = json.loads(topic["terms_json"] or "{}")
        for key, value in terms(text).items():
            known[key] = round(min(3.0, known.get(key, 0.0) + value * weight), 3)
        if len(known) > MAX_TERMS:
            known = dict(sorted(known.items(), key=lambda item: item[1], reverse=True)[:MAX_TERMS])
        db.execute("UPDATE topics SET terms_json=?, updated_at=?, turns=turns+1, status='active' WHERE topic_id=?",
                   (json.dumps(known), now.isoformat(), topic["topic_id"]))

    def _candidates(self, db: sqlite3.Connection, session_id: str, now: datetime) -> list[sqlite3.Row]:
        horizon = (now - RETURN_HORIZON).isoformat()
        return db.execute("SELECT * FROM topics WHERE merged_into='' AND (session_id=? OR updated_at>=?) "
                          "ORDER BY updated_at DESC LIMIT 80", (session_id, horizon)).fetchall()

    # ---- the decision

    def decide(self, session_id: str, text: str, *, now: datetime | None = None, intent: str = "",
               today: date | None = None) -> TopicDecision:
        """Place the owner's message in a conversation and topic, record it, and say how it relates."""
        now = now or _now()
        value = " ".join(str(text or "").split())
        with self._lock, closing(self._db()) as db, db:
            conversation = self._conversation(db, session_id)
            last_at = _parse(conversation["last_at"]) if conversation is not None else None
            active = self._topic(db, conversation["active_topic_id"]) if conversation is not None and conversation["active_topic_id"] else None
            reasons: list[str] = []
            gap = now - last_at if last_at is not None else None
            fresh = conversation is None or gap is None or gap >= NEW_CONVERSATION_GAP
            candidates = [row for row in self._candidates(db, session_id, now)
                          if active is None or row["topic_id"] != active["topic_id"]]
            message_terms = terms(value)
            relation, topic, confidence = "", None, 0.5
            person_match = _PERSON.search(value)
            person = ""
            if person_match:
                person = person_match.group("who").lower() + (f" {person_match.group('name')}" if person_match.group("name") else "")

            def best_other(words: dict[str, float]) -> tuple[sqlite3.Row | None, float]:
                scored = sorted(((similarity(words, json.loads(row["terms_json"] or "{}")), row) for row in candidates),
                                key=lambda pair: pair[0], reverse=True)
                return (scored[0][1], scored[0][0]) if scored and scored[0][0] > 0 else (None, 0.0)

            def shared(words: dict[str, float], row: sqlite3.Row | None) -> int:
                """How many of the message's own words the topic has met (one shared verb is not a subject)."""
                known = json.loads(row["terms_json"] or "{}") if row is not None else {}
                return sum(1 for key in words if known.get(key, 0.0) >= 0.3)

            active_score = similarity(message_terms, json.loads(active["terms_json"] or "{}")) if active is not None else 0.0
            # "this is same reply ... look again but deeper", "i didn't ask about X, i was asking you to Y": a message
            # about the last answer belongs to the topic of that answer. Filed as a new topic (as it was on
            # 2026-11-11), the very exchange it complains about is set aside and the reply comes from somewhere else.
            from app.answer_feedback import read as read_feedback

            feedback = read_feedback(value)
            if feedback is not None and not feedback.about_answer:
                feedback = None
            if feedback is not None and active is not None and not fresh:
                target = active
                if feedback.restated:
                    restated_terms = terms(feedback.restated)
                    own = similarity(restated_terms, json.loads(active["terms_json"] or "{}"))
                    other, score = best_other(restated_terms)
                    if (other is not None and score >= CONTINUE_THRESHOLD and score > own
                            and shared(restated_terms, other) >= 2):
                        target = other
                        reasons.append(f"restates a request that belongs to “{other['title']}” (match {score:.2f})")
                relation, topic, confidence = "correction", target, 0.85
                reasons.append(feedback.describe() or "corrects how the last message was understood")
            else:
                feedback = None
            if not relation and _RETURN.search(value):
                cue_free = {key: weight for key, weight in message_terms.items()
                            if key.lstrip("#") not in {_stem(word) for word in _RETURN_FILLER}}
                other, score = best_other(cue_free)
                own_score = similarity(cue_free, json.loads(active["terms_json"] or "{}")) if active is not None else 0.0
                if other is not None and score >= 0.2 and score >= own_score:
                    relation, topic, confidence = "return", other, min(0.95, 0.55 + score / 2)
                    reasons.append(f"goes back to an earlier topic (match {score:.2f})")
                elif active is not None and not fresh:
                    relation, topic, confidence = "reference", active, 0.6
                    reasons.append("refers to something said earlier in this topic")
            if not relation and active is not None and not fresh and _CORRECTION.match(value):
                relation, topic, confidence = "correction", active, 0.85
                reasons.append("corrects how the last message was understood")
            if not relation and _TASK_STATUS.match(value):
                running = db.execute("SELECT * FROM topics WHERE session_id=? AND open_task!='' ORDER BY updated_at DESC "
                                     "LIMIT 1", (session_id,)).fetchone()
                if running is not None or (active is not None and not fresh):
                    relation, topic, confidence = "task_status", running or active, 0.8
                    reasons.append("asks about work that is still running" if running is not None
                                   else "asks how the current topic's work is going")
            if not relation and active is not None and not fresh and _CONNECT.search(value):
                other, score = best_other(message_terms)
                if other is not None and score >= 0.15 and active_score >= 0.1:
                    relation, topic, confidence = "connect", active, 0.7
                    reasons.append(f"connects this topic with “{other['title']}”")
                    db.execute("INSERT OR IGNORE INTO topic_links(a,b,kind,at) VALUES(?,?,?,?)",
                               (active["topic_id"], other["topic_id"], "connected", now.isoformat()))
            if not relation and is_small_talk(value):
                if active is not None and not fresh:
                    relation, topic, confidence = "small_talk", active, 0.9
                else:
                    relation, confidence = "small_talk", 0.9
                reasons.append("a few words of conversation")
            if (not relation and active is not None and not fresh and gap is not None and gap <= ANCHOR_WINDOW
                    and _REFINES.match(value)):
                relation, topic, confidence = "continue", active, 0.8
                reasons.append("refines the last answer")
            if (not relation and active is not None and not fresh and gap is not None and gap <= ANCHOR_WINDOW
                    and _UPDATE.match(value)):
                # "Actually I moved to Austin last week" updates what was just said. Filed as a new topic, it was set
                # aside when "where do I live now?" went back to "I live in Seattle ...", and the reply said Seattle.
                relation, topic, confidence = "continue", active, 0.8
                reasons.append("updates something the owner said in this topic")
            if (not relation and active is not None and not fresh and gap is not None and gap <= ANCHOR_WINDOW
                    and _JOINING.search(value)):
                relation, topic, confidence = "continue", active, 0.8
                reasons.append("adds someone or something to the plan under discussion")
            lean = "" if relation else leans_on_topic(value)
            if not relation and lean and active is not None and gap is not None and gap <= ANCHOR_WINDOW:
                relation, topic, confidence = "continue", active, 0.8
                reasons.append(lean)
            if not relation and _NEW_TOPIC.search(value):
                other, score = best_other(message_terms)
                if other is not None and score >= RETURN_THRESHOLD:
                    relation, topic, confidence = "return", other, 0.7
                    reasons.append(f"says it is another subject, and it matches an earlier topic (match {score:.2f})")
                else:
                    relation, confidence = "new_topic", 0.85
                    reasons.append("says it is a new subject")
            if (not relation and active is not None and not fresh and gap is not None and gap <= ANCHOR_WINDOW
                    and _about_the_last_answer(value)):
                # "work on it", "do it", "go on": about the reply just given. One shared verb ("work") once sent
                # this to a day-old topic about something else, and "can u fix it?" followed it there.
                relation, topic, confidence = "continue", active, 0.7
                reasons.append("acts on the last reply")
            if not relation and active is not None and not fresh and not active["subject"] and own_subject_words(value):
                # "hello" then "what's the capital of Australia?": the first subject of the conversation fills the
                # greeting's empty topic instead of leaving an empty topic behind.
                relation, topic, confidence = "new_topic", active, 0.8
                reasons.append("the first subject of this conversation")
            if not relation and active is not None and not fresh:
                other, score = best_other(message_terms)
                if active_score >= CONTINUE_THRESHOLD and not (other is not None and score >= active_score + 0.35):
                    relation, topic, confidence = "continue", active, min(0.95, 0.5 + active_score / 2)
                    reasons.append(f"shares its subject with the current topic (match {active_score:.2f})")
                elif (other is not None and score >= RETURN_THRESHOLD and score > active_score + 0.2
                      and shared(message_terms, other) >= 2):
                    relation, topic, confidence = "return", other, min(0.9, 0.4 + score / 2)
                    reasons.append(f"matches an earlier topic, “{other['title']}” (match {score:.2f})")
                # Two words of its own subject, none shared: a new topic. One new word is kept in the current topic,
                # because splitting wrongly drops context the answer needed ("why do some need boosters?").
                elif len(own_subject_words(value)) >= 2:
                    relation, confidence = "new_topic", round(0.75 - active_score, 2)
                    reasons.append("names a subject unrelated to the current topic")
                else:
                    relation, topic, confidence = "continue", active, 0.45
                    reasons.append("too short to tell; kept in the current topic")
            if not relation:
                other, score = best_other(message_terms)
                if (other is not None and score >= RETURN_THRESHOLD and fresh and conversation is not None
                        and shared(message_terms, other) >= 2):
                    relation, topic, confidence = "return", other, min(0.85, 0.35 + score / 2)
                    reasons.append(f"after a break, matches an earlier topic, “{other['title']}” (match {score:.2f})")
                else:
                    relation, confidence = ("new_conversation" if fresh else "new_topic"), 0.8
                    reasons.append("the first message of this conversation" if conversation is None
                                   else "the first message after a long break" if fresh else "a new subject")
            if person and person_match is not None and person_match.start() >= 25:
                person = ""  # named in passing, not what the message is about
            if person and relation in {"new_topic", "new_conversation", "continue"}:
                reasons.append(f"gives information about the owner's {person}")
                if relation == "new_topic":
                    relation = "other_person"
            # Where the message goes.
            if fresh or conversation is None:
                conversation_id = self._new_conversation(db, session_id, now)
            else:
                conversation_id = conversation["conversation_id"]
            previous_topic_id = active["topic_id"] if active is not None else ""
            if topic is None:
                topic_id = self._new_topic(db, conversation_id, session_id, value, now, intent)
                topic = db.execute("SELECT * FROM topics WHERE topic_id=?", (topic_id,)).fetchone()
            elif relation not in {"small_talk", "task_status"}:
                # Of a complaint, only the restated request says what the topic is about.
                learned = feedback.restated if feedback is not None else value
                if learned:
                    self._learn_terms(db, topic, learned, 0.5 if lean else 1.0, now)
            subject_source = feedback.restated if feedback is not None else value
            if not topic["subject"] and subject_source and own_subject_words(subject_source):
                db.execute("UPDATE topics SET subject=?, title=?, subject_intent=? WHERE topic_id=?",
                           (subject_phrase(subject_source), _title(subject_phrase(subject_source)), intent,
                            topic["topic_id"]))
                topic = db.execute("SELECT * FROM topics WHERE topic_id=?", (topic["topic_id"],)).fetchone()
            db.execute("UPDATE conversations SET last_at=?, active_topic_id=? WHERE conversation_id=?",
                       (now.isoformat(), topic["topic_id"], conversation_id))
            if relation == "return":
                db.execute("UPDATE topics SET status='active', updated_at=? WHERE topic_id=?", (now.isoformat(), topic["topic_id"]))
            if previous_topic_id and previous_topic_id != topic["topic_id"]:
                db.execute("UPDATE topics SET status='dormant' WHERE topic_id=? AND status='active'", (previous_topic_id,))
            decision = TopicDecision(
                relation=relation, topic_id=topic["topic_id"], title=topic["title"], conversation_id=conversation_id,
                previous_topic_id=previous_topic_id, confidence=round(float(confidence), 2), reasons=reasons,
                person=person, original=value, topic_subject=topic["subject"])
            if feedback is not None and relation == "correction":
                decision.feedback = {**feedback.to_dict(), "said": feedback.describe()}
            if relation == "connect":
                decision.connected_topic_ids = [row["b"] for row in db.execute(
                    "SELECT b FROM topic_links WHERE a=? AND kind='connected'", (topic["topic_id"],))]
            # A topic about a person is never turned into a search query ("what should she pack?").
            if lean and relation == "continue" and topic["subject"] and topic["subject"] != subject_phrase(value) \
                    and not _PERSON.search(topic["subject"]):
                decision.anchored, decision.query = anchor(value, topic["subject"], today=today)
            cursor = db.execute(
                "INSERT INTO topic_messages(conversation_id,session_id,topic_id,role,text,content_hash,relation,anchored,"
                "person,confidence,reasons,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (conversation_id, session_id, topic["topic_id"], "user", value[:4000], _hash(value), relation,
                 decision.anchored, decision.person, decision.confidence, "; ".join(reasons), now.isoformat()))
            decision.message_id = int(cursor.lastrowid or 0)
            if relation == "return" or (relation == "correction" and previous_topic_id
                                        and previous_topic_id != topic["topic_id"]):
                decision.restored = self._transcript(db, topic["topic_id"], before=decision.message_id)
        self._emit("TOPIC_PLACED", {"relation": relation, "topic_id": decision.topic_id,
                                    "previous_topic_id": previous_topic_id, "anchored": bool(decision.anchored),
                                    "confidence": decision.confidence}, session_id=session_id)
        return decision

    def observe_reply(self, decision: TopicDecision | None, reply: str, *, open_task: str = "",
                      session_id: str = "", now: datetime | None = None) -> None:
        """Keep the reply with its topic, so the topic can be restored later; note running work."""
        if decision is None or not str(reply or "").strip():
            return
        now = now or _now()
        value = " ".join(str(reply).split())
        with self._lock, closing(self._db()) as db, db:
            db.execute(
                "INSERT INTO topic_messages(conversation_id,session_id,topic_id,role,text,content_hash,relation,at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (decision.conversation_id, session_id or "", decision.topic_id, "assistant", value[:1500],
                 _hash(reply), decision.relation, now.isoformat()))
            if open_task:
                db.execute("UPDATE topics SET open_task=? WHERE topic_id=?", (open_task, decision.topic_id))
            row = db.execute("SELECT * FROM topics WHERE topic_id=?", (decision.topic_id,)).fetchone()
            if row is not None:
                known = json.loads(row["terms_json"] or "{}")
                for key, weight in terms(value[:600]).items():
                    if key in known:  # a reply reinforces the topic's words; it never adds the model's own
                        known[key] = round(min(3.0, known[key] + 0.25 * weight), 3)
                db.execute("UPDATE topics SET terms_json=? WHERE topic_id=?", (json.dumps(known), decision.topic_id))

    def task_finished(self, task: str, reply: str = "", *, now: datetime | None = None) -> dict[str, str] | None:
        """A background job reported back: file its result under the topic that started it and close the task."""
        if not task:
            return None
        now = now or _now()
        with self._lock, closing(self._db()) as db, db:
            topic = db.execute("SELECT * FROM topics WHERE open_task=? ORDER BY updated_at DESC LIMIT 1", (task,)).fetchone()
            if topic is None:
                return None
            db.execute("UPDATE topics SET open_task='' WHERE open_task=?", (task,))
            value = " ".join(str(reply or "").split())
            if value:
                db.execute("INSERT INTO topic_messages(conversation_id,session_id,topic_id,role,text,content_hash,relation,at) "
                           "VALUES(?,?,?,?,?,?,?,?)", (topic["conversation_id"], topic["session_id"], topic["topic_id"],
                                                        "assistant", value[:1500], _hash(reply), "task_result", now.isoformat()))
        return {"topic_id": topic["topic_id"], "title": topic["title"], "subject": topic["subject"]}

    # ---- history in scope

    @staticmethod
    def _transcript(db: sqlite3.Connection, topic_id: str, *, before: int | None = None, limit: int = 8) -> list[dict[str, Any]]:
        ids = [topic_id] + [row["topic_id"] for row in db.execute("SELECT topic_id FROM topics WHERE merged_into=?", (topic_id,))]
        marks = ",".join("?" for _ in ids)
        bound = " AND id<?" if before else ""
        rows = db.execute(f"SELECT role,text,at FROM topic_messages WHERE topic_id IN ({marks}){bound} "  # noqa: S608 - placeholders only
                          "ORDER BY id DESC LIMIT ?", (*ids, *( [before] if before else []), limit)).fetchall()
        return [{"role": row["role"], "content": row["text"], "at": row["at"]} for row in reversed(rows)]

    def scope_history(self, session_id: str, history: list[dict[str, Any]],
                      decision: TopicDecision | None) -> tuple[list[dict[str, Any]], list[str]]:
        """The history that belongs to this message's topic, and the titles of the topics set aside.

        Messages whose topic is known and different are left out; messages this module never saw (older history,
        notices, recaps) are kept, because dropping context on a guess is worse than keeping it.
        """
        if decision is None or not history:
            return history, []
        allowed = {decision.topic_id, *decision.connected_topic_ids}
        try:
            with self._lock, closing(self._db()) as db:
                for row in db.execute("SELECT topic_id FROM topics WHERE merged_into IN (%s)" % ",".join("?" for _ in allowed),  # noqa: S608
                                      tuple(allowed)):
                    allowed.add(row["topic_id"])
                hashes = {_hash(item.get("content", "")) for item in history if item.get("role") == "user"}
                owner_of: dict[str, str] = {}
                if hashes:
                    marks = ",".join("?" for _ in hashes)
                    for row in db.execute(f"SELECT content_hash, topic_id FROM topic_messages WHERE session_id=? AND role='user' "  # noqa: S608
                                          f"AND content_hash IN ({marks}) ORDER BY id", (session_id, *hashes)):
                        owner_of[row["content_hash"]] = row["topic_id"]
                merged = {row["topic_id"]: row["merged_into"] for row in db.execute(
                    "SELECT topic_id, merged_into FROM topics WHERE merged_into!=''")}
                titles = {row["topic_id"]: row["title"] for row in db.execute(
                    "SELECT topic_id,title FROM topics WHERE topic_id IN (%s)" % ",".join("?" for _ in set(owner_of.values()) or [""]),  # noqa: S608
                    tuple(set(owner_of.values())) or ("",))}
        except sqlite3.Error:
            log_swallowed(_log, "reading topics for the reply's history")
            return history, []
        kept: list[dict[str, Any]] = []
        set_aside: list[str] = []
        current: str | None = None
        for index, item in enumerate(history):
            last = index == len(history) - 1
            if item.get("role") == "user":
                current = owner_of.get(_hash(item.get("content", "")))
                while current in merged:
                    current = merged[current]
            if last or item.get("role") == "system" or current is None or current in allowed:
                kept.append(item)
            else:
                title = titles.get(current or "", "")
                if title and title not in set_aside:
                    set_aside.append(title)
        if decision.restored:
            present = {_hash(item.get("content", "")) for item in kept}
            restored = [{"role": item["role"], "content": item["content"]} for item in decision.restored
                        if _hash(item["content"]) not in present]
            if restored:
                when = str(decision.restored[0].get("at", ""))[:16].replace("T", " ")
                marker = {"role": "system", "content": f"Restored from the earlier topic “{decision.title}” "
                          f"(from {when} UTC). This is that topic's own earlier exchange:"}
                kept = [marker, *restored, {"role": "system", "content": "End of the restored topic. The owner's "
                                            "messages since then follow."}, *kept]
        return kept, set_aside

    # ---- the owner's controls

    def _override(self, db: sqlite3.Connection, action: str, message_id: int | None, from_topic: str, to_topic: str,
                  relation: str = "") -> None:
        db.execute("INSERT INTO topic_overrides(message_id,action,from_topic,to_topic,decided_relation,at) VALUES(?,?,?,?,?,?)",
                   (message_id, action, from_topic, to_topic, relation, _now().isoformat()))
        self._emit("TOPIC_CHANGED_BY_OWNER", {"action": action, "from_topic": from_topic, "to_topic": to_topic})

    def separate(self, message_id: int) -> dict[str, Any]:
        """The owner says this message started something else: give it (and what followed it in that topic) its own topic."""
        with self._lock, closing(self._db()) as db, db:
            row = db.execute("SELECT * FROM topic_messages WHERE id=? AND role='user'", (message_id,)).fetchone()
            if row is None:
                return {"separated": False, "reason": "No message with that id."}
            now = _now()
            new_id = self._new_topic(db, row["conversation_id"], row["session_id"], row["text"], now)
            db.execute("UPDATE topic_messages SET topic_id=? WHERE topic_id=? AND id>=?", (new_id, row["topic_id"], message_id))
            db.execute("UPDATE conversations SET active_topic_id=? WHERE conversation_id=? AND active_topic_id=?",
                       (new_id, row["conversation_id"], row["topic_id"]))
            db.execute("UPDATE topics SET status='dormant' WHERE topic_id=?", (row["topic_id"],))
            self._override(db, "separate", message_id, row["topic_id"], new_id, row["relation"])
            title = db.execute("SELECT title FROM topics WHERE topic_id=?", (new_id,)).fetchone()["title"]
        return {"separated": True, "topic_id": new_id, "title": title, "from_topic": row["topic_id"]}

    def same_topic(self, message_id: int) -> dict[str, Any]:
        """The owner says this message continued the topic before it: merge its topic into that one."""
        with self._lock, closing(self._db()) as db, db:
            row = db.execute("SELECT * FROM topic_messages WHERE id=? AND role='user'", (message_id,)).fetchone()
            if row is None:
                return {"merged": False, "reason": "No message with that id."}
            before = db.execute("SELECT topic_id FROM topic_messages WHERE session_id=? AND id<? AND topic_id!=? "
                                "ORDER BY id DESC LIMIT 1", (row["session_id"], message_id, row["topic_id"])).fetchone()
            if before is None:
                return {"merged": False, "reason": "There is no earlier topic in this conversation to join."}
        result = self.merge(row["topic_id"], before["topic_id"], action="same_topic", message_id=message_id,
                            relation=row["relation"])
        return result

    def merge(self, source: str, into: str, *, action: str = "merge", message_id: int | None = None,
              relation: str = "") -> dict[str, Any]:
        if source == into:
            return {"merged": False, "reason": "That is the same topic."}
        with self._lock, closing(self._db()) as db, db:
            first, second = self._topic(db, source), self._topic(db, into)
            if first is None or second is None:
                return {"merged": False, "reason": "Unknown topic."}
            if first["topic_id"] == second["topic_id"]:
                return {"merged": False, "reason": "Those are already one topic."}
            combined = json.loads(second["terms_json"] or "{}")
            for key, value in json.loads(first["terms_json"] or "{}").items():
                combined[key] = round(min(3.0, combined.get(key, 0.0) + value), 3)
            db.execute("UPDATE topics SET terms_json=?, turns=turns+?, updated_at=?, status='active' WHERE topic_id=?",
                       (json.dumps(dict(sorted(combined.items(), key=lambda kv: kv[1], reverse=True)[:MAX_TERMS])),
                        int(first["turns"]), _now().isoformat(), second["topic_id"]))
            db.execute("UPDATE topics SET merged_into=?, status='merged' WHERE topic_id=?", (second["topic_id"], first["topic_id"]))
            db.execute("UPDATE topic_messages SET topic_id=? WHERE topic_id=?", (second["topic_id"], first["topic_id"]))
            db.execute("UPDATE conversations SET active_topic_id=? WHERE active_topic_id=?", (second["topic_id"], first["topic_id"]))
            self._override(db, action, message_id, first["topic_id"], second["topic_id"], relation)
        return {"merged": True, "topic_id": second["topic_id"], "title": second["title"], "from_topic": first["topic_id"]}

    def reactivate(self, session_id: str, topic_id: str) -> dict[str, Any]:
        """Make an earlier topic the current one: the next message continues it."""
        with self._lock, closing(self._db()) as db, db:
            topic = self._topic(db, topic_id)
            if topic is None:
                return {"reactivated": False, "reason": "Unknown topic."}
            conversation = self._conversation(db, session_id)
            now = _now()
            conversation_id = conversation["conversation_id"] if conversation is not None else self._new_conversation(db, session_id, now)
            db.execute("UPDATE conversations SET active_topic_id=?, last_at=? WHERE conversation_id=?",
                       (topic["topic_id"], now.isoformat(), conversation_id))
            db.execute("UPDATE topics SET status='active', updated_at=? WHERE topic_id=?", (now.isoformat(), topic["topic_id"]))
            self._override(db, "reactivate", None, "", topic["topic_id"])
        return {"reactivated": True, "topic_id": topic["topic_id"], "title": topic["title"]}

    # ---- reading

    def topics(self, session_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock, closing(self._db()) as db:
            rows = db.execute(
                "SELECT t.*, (SELECT COUNT(*) FROM topic_messages m WHERE m.topic_id=t.topic_id AND m.role='user') AS asked "
                "FROM topics t WHERE merged_into='' " + ("AND session_id=? " if session_id else "")
                + "ORDER BY updated_at DESC LIMIT ?", ((session_id, limit) if session_id else (limit,))).fetchall()
            active = {row["active_topic_id"] for row in db.execute("SELECT active_topic_id FROM conversations")}
        return [{"topic_id": row["topic_id"], "title": row["title"], "subject": row["subject"], "status": row["status"],
                 "messages": int(row["asked"]), "updated_at": row["updated_at"], "created_at": row["created_at"],
                 "open_task": row["open_task"], "current": row["topic_id"] in active,
                 "words": [key for key, _ in sorted(json.loads(row["terms_json"] or "{}").items(),
                                                    key=lambda kv: kv[1], reverse=True) if not key.startswith("#")][:6]}
                for row in rows]

    def messages(self, topic_id: str, limit: int = 40) -> list[dict[str, Any]]:
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT * FROM topic_messages WHERE topic_id=? ORDER BY id DESC LIMIT ?", (topic_id, limit)).fetchall()
        return [{"id": row["id"], "role": row["role"], "text": row["text"], "relation": row["relation"],
                 "anchored": row["anchored"], "person": row["person"], "at": row["at"], "reasons": row["reasons"]}
                for row in reversed(rows)]

    def recent_decisions(self, session_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, closing(self._db()) as db:
            rows = db.execute(
                "SELECT m.*, t.title FROM topic_messages m LEFT JOIN topics t ON t.topic_id=m.topic_id WHERE m.role='user' "
                + ("AND m.session_id=? " if session_id else "") + "ORDER BY m.id DESC LIMIT ?",
                ((session_id, limit) if session_id else (limit,))).fetchall()
        return [{"id": row["id"], "text": row["text"][:160], "relation": row["relation"], "topic": row["title"],
                 "topic_id": row["topic_id"], "anchored": row["anchored"], "confidence": row["confidence"],
                 "reasons": row["reasons"], "at": row["at"]} for row in rows]

    def stats(self) -> dict[str, Any]:
        """Measured: decisions by relation, and how many the owner had to fix."""
        with self._lock, closing(self._db()) as db:
            by_relation = {row[0]: int(row[1]) for row in db.execute(
                "SELECT relation, COUNT(*) FROM topic_messages WHERE role='user' GROUP BY relation")}
            decided = sum(by_relation.values())
            overrides = {row[0]: int(row[1]) for row in db.execute("SELECT action, COUNT(*) FROM topic_overrides GROUP BY action")}
            topics = int(db.execute("SELECT COUNT(*) FROM topics WHERE merged_into=''").fetchone()[0])
            conversations = int(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
            anchored = int(db.execute("SELECT COUNT(*) FROM topic_messages WHERE anchored!=''").fetchone()[0])
            returned = int(db.execute("SELECT COUNT(DISTINCT topic_id) FROM topic_messages WHERE relation='return'").fetchone()[0])
        errors = overrides.get("separate", 0) + overrides.get("same_topic", 0)
        return {"decisions": decided, "by_relation": by_relation, "topics": topics, "conversations": conversations,
                "anchored_follow_ups": anchored, "topics_returned_to": returned, "owner_fixes": overrides,
                "flagged_wrong": errors,
                "owner_checked_accuracy": round(1 - errors / decided, 3) if decided else None}

    def report(self, session_id: str | None = None) -> dict[str, Any]:
        return {"stats": self.stats(), "topics": self.topics(session_id, 12),
                "recent": self.recent_decisions(session_id, 10)}


def _title(subject: str) -> str:
    value = " ".join(str(subject or "").split()).strip(" ?!.,")
    if not value:
        return "Conversation"
    value = value[0].upper() + value[1:]
    return value if len(value) <= 60 else value[:57].rsplit(" ", 1)[0] + "…"


def anchor(text: str, subject: str, *, today: date | None = None) -> tuple[str, str]:
    """A standalone reading and a search query for a message that leaned on its topic's subject.

    "Look at the latest and predict for nxt year" + "GDP growth forecast for India in 2026" ->
    ("Look at the latest and predict for 2027, about the GDP growth forecast for India in 2027",
     "latest GDP growth forecast for India in 2027 prediction").
    """
    message = resolve_relative_years(expand(text), today).strip()
    focus = subject_phrase(subject)
    years = _YEAR.findall(message)
    if years:
        focus = _YEAR.sub(years[-1], focus, count=1) if _YEAR.search(focus) else f"{focus} in {years[-1]}"
    swap = re.match(r"(?i)^\s*(?:(?:and|but|so|also|ok(?:ay)?)[,\s]+)?(?:what|how)\s+about\s+(?P<x>.+?)[\s?.!]*$"
                    r"|^\s*(?:and|same)\s+(?:for|in|with)\s+(?P<y>.+?)[\s?.!]*$", message)
    replacement = (swap.group("x") or swap.group("y")) if swap else ""
    if replacement and not _YEAR.fullmatch(replacement.strip()) and len(replacement.split()) <= 4:
        names = _entities(focus)
        if names:
            focus = re.sub(rf"\b{re.escape(names[-1])}\b", replacement.strip(), focus, count=1)
        elif replacement.lower() not in focus.lower():
            focus = f"{focus} for {replacement.strip()}"
    lead = "latest " if re.search(r"(?i)\b(?:latest|newest|recent|current|update|now)\b", message) \
        and not re.search(r"(?i)\blatest\b", focus) else ""
    tail = ""
    if re.search(r"(?i)\bpredict", message) and not re.search(r"(?i)\b(?:predict|forecast|projection|outlook)", focus):
        tail = " prediction"
    elif re.search(r"(?i)\bcompare|\bversus\b", message):
        tail = " comparison"
    # The message's own subject words ("inflation" in "how does inflation affect it?") stay in the search.
    extra = [word for word in own_subject_words(message)
             if word.lower() not in focus.lower() and word.lower() != replacement.strip().lower()
             and not _POINTER.fullmatch(word)][:4]
    query = " ".join(f"{lead}{focus} {' '.join(extra)}{tail}".split())
    if swap and replacement:
        reading = f"{focus}?" if not re.search(r"(?i)^(?:what|how|who|why|when|where|which)\b", focus) else focus
        reading = f"{message.rstrip(' ?.!')}: {reading}"
    else:
        reading = f"{message.rstrip(' ?.!')}, about the {focus}" if not message.lower().endswith(focus.lower()) else message
    return " ".join(reading.split()), query


def same_reading(decision: TopicDecision | None) -> bool:
    return decision is None or not decision.anchored


_TOPICS_QUESTION = re.compile(
    r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:so\s+|ok(?:ay)?\s+)?(?:"
    r"what\s+(?:topics|subjects|things)\s+(?:have|did)\s+we\s+(?:discuss(?:ed)?|talk(?:ed)?\s+about|cover(?:ed)?)"
    r"|what\s+(?:were|are|have)\s+we\s+(?:been\s+)?(?:talking|chatting)\s+about"
    r"|(?:list|show)(?:\s+me)?\s+(?:our|the|my|all)?\s*(?:conversation\s+)?(?:topics|threads)"
    r"|(?:which|what)\s+topic\s+(?:is\s+this|are\s+we\s+(?:on|in))"
    r"|how\s+(?:did|do)\s+you\s+(?:split|separate|group)\s+(?:our|the|this)\s+(?:conversation|chat|topics))\b")


def topics_request(text: str) -> bool:
    return bool(_TOPICS_QUESTION.match(" ".join(str(text or "").split())))


def direct_topics_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    if not topics_request(text) or "conversation_topics" not in set(names):
        return None
    return [{"description": "Read the conversation's topics from HAYAT's own topic record.",
             "tool": "conversation_topics", "arguments": {"action": "list"}, "expected": {"type": "tool_success"},
             "max_retries": 0}]


def register_topic_tools(registry: Any, topics: ConversationTopics, session: Any) -> None:
    """``conversation_topics``: list, inspect, separate, join, merge or reactivate topics (local records only)."""
    def run(action: str = "list", topic_id: str = "", message_id: int = 0, into: str = "") -> dict[str, Any]:
        session_id = str(getattr(session(), "session_id", "") or "")
        if action == "list":
            return topics.report(session_id or None)
        if action == "messages":
            return {"topic_id": topic_id, "messages": topics.messages(topic_id)}
        if action == "separate":
            return topics.separate(int(message_id))
        if action == "same_topic":
            return topics.same_topic(int(message_id))
        if action == "merge":
            return topics.merge(topic_id, into)
        if action == "reactivate":
            return topics.reactivate(session_id, topic_id)
        if action == "stats":
            return topics.stats()
        raise ValueError("action must be list, messages, separate, same_topic, merge, reactivate or stats")

    registry.register(
        "conversation_topics",
        "The conversation's topics as HAYAT tracked them: each message's topic and how it related to the one before "
        "(continued, new topic, back to an earlier one, correction, about someone else), the standalone reading of "
        "follow-ups, and controls to separate a message into its own topic, join it to the previous one, merge two "
        "topics or make an earlier topic current again. Local records only.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "messages", "separate", "same_topic", "merge", "reactivate", "stats"]},
            "topic_id": {"type": "string"}, "message_id": {"type": "integer"}, "into": {"type": "string"}},
         "additionalProperties": False},
        "low", "system_read", run)


def iter_relations() -> Iterable[str]:
    return iter(RELATIONS)
