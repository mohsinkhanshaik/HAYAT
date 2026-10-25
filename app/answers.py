"""When an answer is finished, and what to do when it is not.

HAYAT had no idea what "finished" meant. It sent a request, took whatever came
back, and displayed it. If the model stopped because it ran out of room, that
was indistinguishable from stopping because it had said everything. If Mohsin
asked about three things and got one, nothing noticed. If he asked for detail
and got a dictionary definition, that was an answer too.

This is the missing contract. Before generating, it works out how much depth
was asked for and which topics were named. Afterwards it checks the reply
against both, and when the reply falls short it says exactly how -- which is
what makes automatic continuation possible, because "keep going" is useless
without knowing what is still missing.

Three failures are distinguished, because they need different repairs:

  truncated    the model hit a limit mid-sentence      -> continue from here
  incomplete   topics were named and never addressed   -> ask for those topics
  shallow      detail was asked for and not given      -> ask again, deeper

The rule underneath all of it: nothing is shown to Mohsin as a finished answer
until every part he asked about has actually been addressed, or HAYAT has said
plainly which parts it could not do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Depth(IntEnum):
    """How much answer was asked for. Ordered, so comparisons mean something."""

    QUICK = 1       # a fact, a yes, a number
    NORMAL = 2      # ordinary conversation
    DETAILED = 3    # "explain", "in detail", "with examples"
    DEEP = 4        # "thoroughly", "everything about", "deep dive"
    RESEARCH = 5    # needs sources, not recall

    @property
    def label(self) -> str:
        return self.name.lower()


#: Phrases that ask for more than a sentence. Ordered most specific first so
#: "explain in detail" reads as DETAILED rather than stopping at "explain".
_DEPTH_MARKERS: tuple[tuple[Depth, re.Pattern[str]], ...] = (
    (Depth.RESEARCH, re.compile(
        r"(?i)\b(?:research|look (?:it |this )?up|search (?:the )?(?:web|internet|online)|"
        r"find sources?|cite|citations?|latest|current|up to date|as of (?:today|now)|"
        r"what'?s new|recent(?:ly)?|news)\b")),
    (Depth.DEEP, re.compile(
        r"(?i)\b(?:deep ?dive|thoroughly|exhaustive|comprehensive|everything (?:about|on)|"
        r"in depth|full(?:y)? (?:explain|understand)|complete(?:ly)? (?:explain|cover)|"
        r"from scratch|first principles|master)\b")),
    (Depth.DETAILED, re.compile(
        r"(?i)\b(?:in detail|detailed|explain|elaborate|walk me through|step by step|"
        r"with examples?|how does|how do|why does|why do|compare|difference between|"
        r"break (?:it|this) down|teach me|help me understand)\b")),
    # Checked last, so anything that also asks for depth has already matched
    # above. "what is X" on its own is a definition request; "explain what X
    # is in detail" is not, and reaches DETAILED before it gets here.
    (Depth.QUICK, re.compile(
        r"(?i)^\s*(?:what(?:'s| is|'re| are) |who (?:is|was|are) |when (?:is|was|did) |"
        r"where (?:is|was) |how (?:many|much|long|old|far) |is (?:it|there) |"
        r"are there |does it |can it |do i |should i |"
        r"yes or no|quick(?:ly)?|briefly|in (?:one|a) (?:word|line|sentence)|tl;?dr)\b")),
)

#: An answer this short to a request for detail is a dictionary entry, not an
#: explanation. Measured in words because sentences vary too much.
_DICTIONARY_WORDS = {
    Depth.QUICK: 0,        # brevity is the point
    Depth.NORMAL: 0,       # no floor; a short answer can be a whole answer
    Depth.DETAILED: 90,
    Depth.DEEP: 220,
    Depth.RESEARCH: 120,
}

# Reasoning effort and answer length are separate choices. A careful answer
# can still be a single number or three lines of working.
_BRIEF_OUTPUT = re.compile(
    r"(?i)\b(?:briefly|keep (?:it|this|the answer) (?:short|brief)|"
    r"(?:short|brief|concise) (?:answer|response|working|calculation|explanation)|"
    r"(?:only|just) (?:the )?answer|in (?:one|a) (?:word|line|sentence)|tl;?dr|"
    r"(?:with|in|using) (?:exactly |just |only )?(?:one|a single) (?:word|line|sentence)|"
    r"answer (?:with )?(?:yes or no|yes/no)|"
    # "Answer in under 10 words" got 191 words.
    r"(?:in|under|in under|less than|fewer than|at most|no more than|within|max(?:imum)?)\s+\d{1,3}\s+words?)\b")

# A separate sentence is a constraint too: "Who are you? One sentence."
# Anchor it to a sentence boundary so prose about one sentence stays a topic.
_BRIEF_UNIT = re.compile(
    r"(?i)(?:^|[.!?;:]\s*)(?:exactly |just |only )?(?:one|a single) "
    r"(word|line|sentence)(?: only)?[.!]?\s*$")


def brief_output_unit(text: str) -> str | None:
    value = " ".join(str(text or "").split())
    match = re.search(r"(?i)\b(?:in|with|using) (?:exactly |just |only )?"
                      r"(?:a single|one|a) (word|line|sentence)\b", value) or _BRIEF_UNIT.search(value)
    return match.group(1).lower() if match else None

#: Endings that mean the model stopped rather than finished. A reply ending in
#: a comma or a half-written list item was cut off, whatever the API said.
_UNFINISHED_TAIL = re.compile(r"[,;:\-–—]\s*$|\b(?:and|or|but|the|a|an|to|of|for|with|is|are)\s*$",
                              re.IGNORECASE)

#: Ollama and llama.cpp both report why generation stopped. "length" means the
#: budget ran out; anything else means the model chose to stop.
_TRUNCATION_REASONS = frozenset({"length", "max_tokens", "max_length"})


@dataclass
class Completeness:
    """What is wrong with an answer, or nothing."""

    complete: bool
    depth: Depth
    covered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    truncated: bool = False
    too_shallow: bool = False
    artifact_issue: str = ""
    word_count: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_continuation(self) -> bool:
        return bool(self.truncated or self.missing or self.too_shallow or self.artifact_issue)

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete, "depth": self.depth.label,
            "covered": list(self.covered), "missing": list(self.missing),
            "truncated": self.truncated, "too_shallow": self.too_shallow,
            "artifact_issue": self.artifact_issue,
            "word_count": self.word_count, "reasons": list(self.reasons),
        }

    def describe(self) -> str:
        if self.complete:
            return f"complete ({self.depth.label}, {self.word_count} words)"
        return f"incomplete ({self.depth.label}): " + "; ".join(self.reasons)


#: A short message that asks for nothing to be explained. Questions and
#: requests ("what is recursion", "explain ...", "write ...") are not small talk
#: however short they are.
_SUBSTANTIVE_START = re.compile(
    r"(?i)^(?:please\s+|can you\s+|could you\s+)?(?:what|why|which|when|where|who|whose|explain|describe|compare|"
    r"define|teach|show|list|write|create|make|build|give|find|summari[sz]e|translate|calculate|solve|fix|"
    r"how\s+(?:do|does|did|to|can|could|should|would|much|many|long|far|old)|tell me about|tell me how|is|are|does|do|can)\b"
)


#: Small talk is about the two people talking, not about a subject.
_ABOUT_US = re.compile(
    r"(?i)\b(?:i|i'?m|im|me|my|mine|you|you'?re|ur|your|yours|we|us|hayat|hi|hey|hello|yo|sup|lol|lmao|haha+|hehe|"
    r"joke|jokes|funny|tired|bored|sleepy|happy|sad|weekend|morning|night|day)\b"
)
#: ...unless it describes a problem, which deserves a real answer.
_PROBLEM = re.compile(
    r"(?i)\b(?:slow|error|errors|broken|crash(?:es|ed|ing)?|bug|bugs|issue|problem|fail(?:s|ed|ing)?|"
    r"not working|doesn'?t work|won'?t|can'?t|help|stuck|virus|hacked|lost|pain|sick|hurt)\b"
)


#: A bare acknowledgement. In always-on Deep mode "ok great" was graded as a
#: deep question, then sent back twice for "never addressed: ok great".
_ACKNOWLEDGEMENT = re.compile(
    r"(?i)^(?:(?:ok|okay|k|great|nice|cool|good|fine|perfect|awesome|alright|all right|sure|thanks|thank you|"
    r"thx|ty|got it|understood|sounds good|makes sense|noted|right|yes|yeah|yep|wow|amazing|excellent|"
    r"brilliant|lovely|done|so|and|that's|that is|very|really|super)[\s,.!]*){1,6}$")


def is_small_talk(text: str) -> bool:
    """A few words of conversation about us, not a question, a request or a problem."""
    value = " ".join(str(text or "").split())
    words = value.split()
    if not words or len(words) > 6 or _SUBSTANTIVE_START.match(value):
        return False
    if any(pattern.search(value) for _depth, pattern in _DEPTH_MARKERS) or _PROBLEM.search(value):
        return False
    return bool(_ABOUT_US.search(value))


def brief_output_requested(text: str) -> bool:
    """Explicit output brevity, independent of the selected reasoning mode."""
    from app.response_constraints import word_constraint
    return bool(_BRIEF_OUTPUT.search(" ".join(str(text or "").split())) or brief_output_unit(text)
                or word_constraint(text) is not None)


#: An instruction to do something, not a request to explain: "book it for tomorrow", "set a reminder", "open notepad".
_SHORT_COMMAND = re.compile(
    r"(?i)^\s*(?:please\s+|ok(?:ay)?\s+|now\s+|then\s+)?(?:book|set|remind|add|remove|delete|cancel|schedule|call|send|open|close|"
    r"start|stop|play|pause|resume|turn|mute|unmute|save|move|copy|rename|run|mark|hide|undo|redo|skip|retry|try|do|use|pick|"
    r"choose|go|confirm|approve|forget|clear|reset|restart|install|uninstall|download|print|share|post|submit|read|check|"
    # "work hARDER" (live 2026-10-24) got a deep word floor and an essay on what working harder means.
    r"work|keep|push|continue|proceed|finish|complete|carry|hurry|speed|focus)\b(?!.*\b(?:explain|why|how)\b)")


def detect_depth(text: str, modes: set[str] | None = None) -> Depth:
    """How much of an answer was asked for.

    Modes set the default depth; an explicit brevity request overrides length,
    without changing the model's reasoning mode or evidence requirements.
    """
    modes = modes or set()
    value = " ".join(str(text or "").split())
    if brief_output_requested(value):
        return Depth.QUICK
    if _EXACT_FORMAT.search(value) and not any(
            pattern.search(value) for depth, pattern in _DEPTH_MARKERS if depth in {Depth.DEEP, Depth.DETAILED}):
        # An exact format (JSON, "exactly three", "nothing else") is judged by
        # its format, never by a word count -- unless detail was asked for too.
        return Depth.QUICK
    # An acknowledgement is answered briefly, but it is not small talk: the
    # situation model still counts "ok great" as a follow-up of the last subject.
    if is_small_talk(value) or _ACKNOWLEDGEMENT.fullmatch(value):
        # God mode (DEEP + ADVERSARIAL) applied a 220-word floor to "i'm tired
        # today", and the brain was sent back to give "the full deep answer:
        # how it works, why, and a concrete example" -- 30 seconds for a reply
        # that should take three.
        return Depth.NORMAL
    if modes & {"RESEARCH"}:
        return Depth.RESEARCH
    if modes & {"DEEP", "ADVERSARIAL", "TEACH", "MENTOR"}:
        # Deep mode sets how carefully HAYAT reasons, not a length for every
        # answer: a one-answer lookup keeps its natural length.
        explicit = next((depth for depth, pattern in _DEPTH_MARKERS
                         if depth in {Depth.DEEP, Depth.DETAILED} and pattern.search(value)), None)
        if explicit is None:
            from app.intent.normalize import normalized_text

            # Judged on the spelling-corrected words too: "waht is teh capitl" is still a lookup.
            if any(_is_lookup(form) or _FEELING.search(form) for form in (value, normalized_text(value))):
                return Depth.NORMAL
            # A short command is answered briefly in any mode. "Book it for tomorrow." got a deep word floor, and the
            # continuation pass padded the reply with paragraphs about the previous subject (2026-10-13 hard-chat check).
            if _SHORT_COMMAND.match(value) and len(value.split()) <= 10:
                return Depth.NORMAL
        return Depth.DEEP

    if not value:
        return Depth.NORMAL
    for depth, pattern in _DEPTH_MARKERS:
        if pattern.search(value):
            return depth

    # FAST is checked only after the words have had their say. It is a routing
    # decision made for speed, not Mohsin asking for one line -- and treating
    # it as one graded "explain me in detail about taxation and tax evasion
    # and tax return" as a quick question, which is precisely backwards.
    if modes & {"FAST"}:
        return Depth.QUICK
    # A long question is asking for a long answer even without saying so.
    if len(value.split()) >= 30:
        return Depth.DETAILED
    return Depth.NORMAL


_EXACT_FORMAT = re.compile(
    r"(?i)\b(?:json|yaml|xml|csv|markdown table|as a table|numbered list|bullet(?:ed)? list|yes or no|yes/no|"
    r"nothing else|only (?:the|a|an|one)\b|return only|answer only|exactly (?:one|two|three|four|five|\d+))\b")
#: How to answer, not what to answer. "Be honest, you're just ChatGPT, aren't you?" split at the comma made "Be honest"
#: a topic the reply never named, and a correct answer ended "I could not properly cover: Be honest…" (2026-10-13).
_FORMAT_DIRECTIVE_START = re.compile(
    r"(?i)^\s*(?:in (?:one|a|two|three|a single) (?:word|line|sentence|paragraph)s?|briefly|quickly|in short|"
    r"tl;?dr|(?:be|please be) (?:honest|real|straight|frank|brief|serious)(?: with me)?|honestly|seriously|frankly|"
    r"(?:just )?between (?:us|you and me)|tell me the truth|quick question|real talk|no offen[cs]e(?: but)?)\s*[,:-]?\s*")
_FORMAT_DIRECTIVE_END = re.compile(
    r"(?i)[\s,.;:-]*(?:keep it (?:short|brief)|in (?:one|a|two|three|a single) (?:word|line|sentence|paragraph)s?|"
    r"briefly|in short)[\s.!?]*$")
#: One-answer lookups. Deep mode still reasons carefully about them, but the
#: answer is a fact, a name, a number or a translation, not an essay: "Which
#: country does she live in?" came back as "Canada" plus 296 padded words.
_LOOKUP = re.compile(
    r"(?i)^\s*(?:(?:and|so|ok(?:ay)?|also|then)[,\s]+)?(?:"
    # "What currency will I need?", "What language do people speak there?", "What colour is a banana?"
    r"what\s+(?!is\b|are\b|was\b|were\b|does\b|do\b|did\b|if\b|about\b|happens\b|causes\b|makes\b|would\b|should\b|"
    r"can\b|could\b|might\b)\w+\s+(?:is|are|was|were|do|does|did|will|would|should|can)\b|"
    # Plain tasks with a definite output.
    r"(?:please\s+)?(?:sort|order|list|count|extract|calculate|compute|spell|rewrite|rephrase|give me (?:exactly\s+)?\d+)\b|"
    r"(?:please\s+)?give\s+(?:me\s+)?(?:one|a single)\s+(?:word|sentence|line)\b|"
    # Creative pieces have their own natural length: a haiku is three lines, not 220 words.
    r"(?:please\s+)?(?:write|compose|tell|give|make\s+up)\s+(?:me\s+)?(?:a|an|one|some)?\s*(?:\w+\s+)?"
    r"(?:haiku|poem|limerick|sonnet|joke|riddle|story|song|tagline|slogan|toast|tongue\s+twister)s?\b"
    r")|^\s*(?:(?:and|so|ok(?:ay)?|also|then)[,\s]+)?(?:which\b|how (?:many|much|long|old|far|tall|big|fast)\b|translate\b|convert\b|"
    r"name (?:one|a|an|two|three|the)\b|what (?:day|date|time|year|month)\b|what does .{1,60} mean\b|"
    r"who (?:is|was) the\b|when (?:is|was|did)\b|where (?:is|was|does|do)\b|did you\b|have you\b|"
    r"is (?:it|there|this)\b|are (?:there|you)\b|can you\b|do you\b|did i\b|what did i\b|where did i\b|"
    r"what(?:'s|’s| is| are) the (?:population|currency|name|height|length|distance|price|cost|"
    r"value|answer|result|date|time|area|speed|weight|age|size|language|symbol|formula for)\b|"
    r"what(?:'s|’s| is| was| were) (?:the |my |our )?(?:first|second|third|fourth|fifth|last|next|previous|\d+(?:st|nd|rd|th)) "
    r"(?:item|word|name|number|entry|meeting|event|appointment|step|line|sentence)\b|"
    # "What is the capital/author/CEO of Y" -- any relation except an abstract one.
    r"what(?:'s|’s| is| are) the (?!meaning\b|purpose\b|point\b|nature\b|role\b|importance\b|history\b|"
    r"significance\b|future\b|cause\b|origin\b|difference\b|best\b|right\b)\w+ (?:of|for|in)\b)")
#: Sharing a feeling is conversation. "I'm feeling stressed about my exam
#: tomorrow" in Deep mode came back as 392 padded words about cortisol.
_FEELING = re.compile(
    r"(?i)^\s*(?:i(?:'m|’m| am)|i feel|i(?:'ve|’ve| have) been|feeling)\s+(?:so\s+|really\s+|very\s+|a bit\s+|kind of\s+)?"
    r"(?:feeling\s+)?(?:stressed|anxious|worried|nervous|sad|down|tired|exhausted|overwhelmed|happy|excited|"
    r"lonely|angry|upset|frustrated|bored|scared|afraid|great|good|bad|sick|unwell|burnt out|burned out)\b")


_LANGUAGE_PREFIX = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:(?:reply|answer|respond|write)\s+(?:only\s+)?in|in)\s+[a-z]+\s*[:,;\-–—]\s*")
_CORRECTION_START = re.compile(r"(?i)^\s*(?:no[,.!\s]+|nope[,.!\s]+|not that[,.!\s]+|wrong[,.!\s]+)?"
                               r"(?:i meant|i mean|i said|actually[,\s]|sorry[,\s]+i meant)")
_WORDED_ARITHMETIC = re.compile(
    r"(?i)\b(?:plus|minus|times|multiplied by|divided by|percent of|% of|square root of|squared|cubed)\b")


#: Telling HAYAT something rather than asking: "Here is my shopping list: eggs,
#: milk…" was graded as a deep question whose "topics" the reply never named.
_STATEMENT = re.compile(
    r"(?i)^\s*(?:ok(?:ay)?[,\s]+|so[,\s]+|fyi[,:\s]+|for context[,:\s]+|note that\s+|just so you know[,\s]+)?"
    r"(?:here(?:'s|’s| is| are)\b|this is my\b|these are my\b|i(?:'m|’m| am| have|'ve|’ve| had| was| will|'ll|’ll)\b|"
    r"my\s+\w+|we(?:'re|’re| are| have|'ve|’ve| will)\b|our\s+\w+)")
#: Editing something already in the conversation is a task, not a topic to explain.
_EDIT_COMMAND = re.compile(
    r"(?i)^\s*(?:please\s+|now\s+|ok(?:ay)?[,\s]+)?(?:remove|add|delete|replace|change|update|move|swap|rename|"
    r"cancel|insert|drop|put|take\s+out|cross\s+off)\b")


def is_statement(text: str) -> bool:
    """The owner sharing information (no question asked)."""
    value = " ".join(str(text or "").split())
    # A problem ("my laptop is slow") deserves a real answer, not an acknowledgement.
    return bool(value) and "?" not in value and bool(_STATEMENT.match(value)) and not _PROBLEM.search(value)


def _without_language_prefix(text: str) -> str:
    """'Reply in Spanish: what is two plus two?' is judged as 'what is two plus two?'."""
    return _LANGUAGE_PREFIX.sub("", str(text or ""), count=1)


_RIDDLE = re.compile(
    r"(?i)^\s*(?:what|who)\s+(?:has|have|gets|goes|comes|can|runs|travels|is\s+(?:always|full|so))\b"
    r"[^?]{0,80}?\b(?:but|the\s+more|yet|without|never)\b|\bwhat\s+am\s+i\s*\??\s*$")


def _is_lookup(text: str) -> bool:
    value = " ".join(_without_language_prefix(str(text or "")).split())
    if re.search(r"(?i)\b(?:why|explain\w*|reasons?|pros and cons|trade-?offs?)\b", value):
        return False  # "Which is better, and why?" asks for reasoning, not a lookup
    if _CORRECTION_START.search(value) and len(value.split()) <= 20:
        return True  # "No, I meant a secondary colour" re-asks the same short question
    if is_statement(value) or (_EDIT_COMMAND.match(value) and len(value.split()) <= 30):
        return True  # sharing a fact, or "Remove milk and add rice", has no essay to write
    if re.search(r"(?i)^\s*what(?:'s|’s| is| are| was| were)\s+my\b", value) or (
            _WORDED_ARITHMETIC.search(value) and len(value.split()) <= 15):
        return True
    if _LOOKUP.search(value):
        return True
    # A riddle has a one-line answer. "What has keys but can't open locks?" got a deep word floor in DEEP mode,
    # and the continuation answered it a second time (live, 2026-10-24).
    if _RIDDLE.search(value) or (re.search(r"\d\s*[-+*/x×÷^]\s*\d", value) and len(value.split()) <= 12):
        return True
    # A supplied fact followed by a bounded question is still one lookup.
    # Do not demand that the answer repeat the update as a separate topic.
    update, separator, question = value.rpartition(". ")
    if separator and len(update.split()) <= 20 and not re.search(r"[.!?]", update) and \
            re.match(r"(?i)^(?:the|this|that|my|our)\b", update) and _LOOKUP.search(question):
        return True
    # A word problem: two or more numbers and a question. The answer is a number with short working.
    return len(re.findall(r"\d+(?:\.\d+)?", value)) >= 2 and value.rstrip().endswith("?")


def required_topics(text: str) -> list[str]:
    """The distinct things this request asks about.

    Reuses the research decomposer, so a question is split the same way
    whether it is answered from memory or from the web. One place to be
    right about what "and" means.
    """
    from app.intent.live_web import is_self_contained_calculation

    # The research decomposer splits word problems into narrative fragments.
    # Their calculation need not repeat nouns like "jacket" or "shop".
    # Completeness here checks termination; it does not verify the arithmetic.
    if is_self_contained_calculation(text):
        return []
    # An exact output format is the whole contract. "Return only a JSON object
    # with keys name and age" split on "and" into two fake topics, and the
    # reply grew a second JSON object and "I could not properly cover".
    if _EXACT_FORMAT.search(str(text or "")):
        return []
    # "If all bloops are razzies and all razzies are lazzies, are all bloops
    # lazzies?" -- the premises are not topics; there is one question.
    if re.match(r"(?i)^\s*(?:so\s+|and\s+)?(?:if|suppose|assuming|given that)\b", str(text or "")):
        return []
    # A one-answer lookup has one thing to answer, however it is phrased:
    # "How many people are going on this trip, counting me?" split into two.
    if _is_lookup(_without_language_prefix(str(text or ""))):
        return []
    # "The difference between RAM and storage" is one question about a pair;
    # split on "and" it became "What is the difference between RAM" + "storage".
    if re.search(r"(?i)\b(?:difference|differences|compare|comparison|versus|vs\.?)\b", str(text or "")) and \
            len(str(text or "").split()) <= 25:
        return []
    # "In one sentence, what is photosynthesis?" made "In one sentence" a topic
    # the answer never named, so a second sentence was demanded and appended.
    text = _FORMAT_DIRECTIVE_END.sub("", _FORMAT_DIRECTIVE_START.sub("", str(text or "")))
    # A message of a few words has no parts to split. Treating "yo" or "salam"
    # as a topic the reply had to name made a friendly reply "incomplete", and
    # the brain was sent back to explain the word: 30-70 seconds and a lecture
    # on "'Yo' is a phonetic shortening" in reply to a greeting.
    if len(str(text or "").split()) <= 5:
        return []
    try:
        from app.research.comprehension import decompose

        parts = decompose(text)
    except Exception:
        return []
    # Drop fragments that carry no actual subject. A stream-of-consciousness
    # request -- "ALL PAPER WORKS AND ALL THINGS I NEED AND ETC EXPLAIN IN
    # DETAILED" -- splits on "and" into pieces like "etc explain in detailed",
    # which is emphasis, not a topic. Left in, the coverage check demands the
    # answer "cover" it, never can, and ends every reply with an awkward "I
    # could not properly cover: etc explain in detailed". A part earns its place
    # only if it names something past the filler.
    real = [part for part in parts if _has_subject(part)]
    return real if len(real) > 1 else []


#: Words that add emphasis or ask for effort but never name a subject. A
#: fragment made only of these is filler, not a topic to be covered.
_FILLER_WORDS = frozenset({
    # Discourse words: "Actually, my wife is coming too" made "Actually" a topic.
    "actually", "also", "now", "okay", "ok", "so", "well", "then", "hmm", "oh", "btw", "anyway", "plus",
    "all", "any", "anything", "everything", "thing", "things", "stuff", "etc",
    "etcetera", "work", "works", "help", "helps", "need", "needs", "want",
    "wants", "explain", "explained", "detail", "detailed", "detailing", "more",
    "deep", "deeply", "please", "give", "tell", "show", "make", "even", "also",
    "and", "the", "that", "this", "these", "those", "with", "about", "for",
    "your", "you", "them", "they", "some", "lot", "lots", "many", "much",
})


def _has_subject(part: str) -> bool:
    """Whether a request fragment names anything past emphasis and filler."""
    words = re.findall(r"[a-z]{3,}", part.lower())
    return any(word not in _FILLER_WORDS for word in words)


def _mentions(answer: str, topic: str) -> bool:
    """Whether the answer actually addresses this topic.

    Matched on the topic's meaningful words rather than the whole phrase,
    because an answer that covers "tax returns" properly will not repeat the
    questioner's exact wording -- and demanding that it does would mark good
    answers incomplete.
    """
    # Format instructions and grammatical glue are not topic coverage. Counting
    # "Give a short answer; state that..." made a grounded short world-effects
    # reply look incomplete even when it covered the requested facts.
    grammar = _FILLER_WORDS | {'what', 'when', 'where', 'which', 'would', 'could', 'should', 'might',
                             'will', 'does', 'have', 'been', 'than', 'into', 'only', 'very',
                             'answer', 'response', 'short', 'brief', 'state'}
    words = [word for word in re.findall(r"[a-z]{4,}", topic.lower()) if word not in grammar]
    if not words:
        return True
    haystack = answer.lower()
    # Compared as stems, because a good answer will not match the questioner's
    # grammar. Asked about "tax returns", a reply explaining what "a tax
    # return" is was scored as never mentioning it -- and sent back for a
    # continuation covering something it had already covered.
    hits = sum(_stem(word) in haystack for word in words)
    return hits >= max(1, len(words) // 2)


def _stem(word: str) -> str:
    """Crudest possible stemming: enough to survive a plural."""
    for suffix in ("ies", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def assess(answer: str, *, question: str = "", depth: Depth | None = None,
           topics: list[str] | None = None, finish_reason: str = "",
           modes: set[str] | None = None) -> Completeness:
    """Decide whether this answer can be shown as finished.

    `finish_reason` is what the provider said about why generation stopped.
    Believed when it reports a length limit, and never relied on alone: a
    model can stop mid-sentence while reporting a clean finish, and the shape
    of the text is the more honest witness of the two.
    """
    depth = depth if depth is not None else detect_depth(question, modes)
    topics = topics if topics is not None else required_topics(question)
    body = str(answer or "").strip()
    words = body.split()
    result = Completeness(complete=True, depth=depth, word_count=len(words))

    if not body:
        result.complete = False
        result.truncated = True
        result.reasons.append("the model returned nothing")
        return result

    if str(finish_reason or "").lower() in _TRUNCATION_REASONS:
        result.truncated = True
        result.reasons.append("generation stopped at the token limit")
    elif not _ends_finished(body):
        result.truncated = True
        result.reasons.append("the reply ends mid-sentence")

    from app.code_completion import code_issue, code_task

    result.artifact_issue, artifact_reason = code_issue(body, question)
    if artifact_reason:
        result.reasons.append(artifact_reason)

    # A runnable program is judged as an artifact. Prose word counts and
    # keyword overlap cannot tell whether its code is complete.
    program_requested = code_task(question)
    for topic in ([] if program_requested else topics):
        (result.covered if _mentions(body, topic) else result.missing).append(topic)
    if result.missing:
        result.reasons.append("never addressed: " + ", ".join(result.missing))

    floor = _DICTIONARY_WORDS.get(depth, 0)
    # A reply that asks for the missing detail is complete as it stands: "Book
    # it for tomorrow." got a clarifying question, was sent back twice as "45
    # words for a deep answer" and grew to 233 words.
    if floor and _asks_for_detail(body):
        floor = 0
    if floor and not program_requested and len(words) < floor:
        result.too_shallow = True
        result.reasons.append(
            f"{len(words)} words for a {depth.label} answer, which reads as a definition")

    result.complete = not result.needs_continuation
    return result


_CLARIFYING = re.compile(
    r"(?i)\b(?:what|which|who|where|when|could you|can you|would you|tell me|do you mean|did you mean|"
    r"are we talking|clarify|specify|need (?:you|to know)|more detail)\b")


def _asks_for_detail(body: str) -> bool:
    """A short reply whose point is a question back to the user."""
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", " ".join(str(body or "").split())) if part]
    questions = [part for part in sentences if part.rstrip(" *_)\"'’").endswith("?")]
    return len(sentences) <= 6 and any(_CLARIFYING.search(part) for part in questions)


def _ends_finished(body: str) -> bool:
    """Whether this text stops somewhere a sentence can stop.

    Emoji count. HAYAT ends replies with them constantly -- "It's four. 🤓" --
    and a punctuation check that did not know that flagged perfectly finished
    answers as cut off, sending every one of them back for a continuation
    that had nothing to add.
    """
    tail = body.rstrip()
    if not tail:
        return False
    # A dozen words cannot have run out of tokens. "sam@example.com
    # office@example.org" was judged cut off and came back listed twice.
    if _UNFINISHED_TAIL.search(tail):
        return False
    if len(tail.split()) <= 12:
        return True
    # No colon here: _UNFINISHED_TAIL already rejects it, and rightly --
    # "Here is the list:" with no list after it is a sentence that stopped.
    if tail.endswith((".", "!", "?", "`", ")", "]", "}", '"', "”", "’")):
        return True
    if re.search(r"(?:^|[\s=])[-+]?(?:[$£€₹]\s*)?\d+(?:[.,]\d+)*(?:%)?$", tail):
        return True
    # Anything non-ASCII at the end is an emoji or other symbol closing the
    # thought, not a word broken in half.
    last = tail[-1]
    return ord(last) > 0x2000 or not last.isalnum()


def continuation_request(assessment: Completeness, question: str, answer_so_far: str) -> str:
    """What to ask the model for next, given exactly what is missing.

    Specific rather than "continue", because a model told to continue will
    happily restate what it already said. Naming the gap is what makes the
    second attempt worth making.
    """
    parts: list[str] = []
    if assessment.artifact_issue == "open_fence":
        parts.append(
            "The code block is unfinished. Continue the exact code from its last character "
            "without reopening the fence or repeating instructions. Finish the program and close ```.")
    elif assessment.artifact_issue:
        parts.append(
            "The program failed this check: " + "; ".join(assessment.reasons[-2:]) + ". "
            "Replace it with one complete, "
            "syntactically valid program from the beginning. Do not repeat the request or offer "
            "to answer it in smaller pieces.")
    if parts:
        return f"The question was: {question}\n\n" + parts[0]
    if assessment.missing:
        parts.append(
            "You did not address: " + ", ".join(assessment.missing)
            + ". Cover each of them now, properly, without repeating what you "
              "already wrote.")
    if assessment.truncated and not assessment.missing:
        tail = " ".join(answer_so_far.split()[-25:])
        parts.append(
            f"Your answer was cut off. Continue from exactly where it stopped: "
            f"...{tail}\nDo not restate anything; carry straight on.")
    if assessment.too_shallow and not assessment.missing:
        # Asked for "how it works, why, and a concrete example", the brain wrote those as labelled sections
        # ("How it works:", "Why it works:", "Concrete Example:", even a "Conclusion:"), which read like a report.
        parts.append(
            f"That was too brief for a {assessment.depth.label} answer. Carry straight on in the same friendly, "
            "conversational voice and go deeper: explain how it works and why, and walk through one concrete "
            "example. Write flowing paragraphs, not labelled sections -- no headings such as 'How it works', "
            "'Why it works', 'Example' or 'Conclusion'. Do not restate what you already said.")
    if not parts:
        parts.append("Finish the answer.")
    return f"The question was: {question}\n\n" + "\n\n".join(parts)


def merge(previous: str, addition: str) -> str:
    """Join a continuation onto what came before, without the seam showing.

    A continuation often re-opens with a sentence the first attempt already
    ended with. Dropping the overlap is the difference between one answer and
    two answers stapled together.
    """
    first, second = previous.rstrip(), addition.strip()
    if not second:
        return first
    if not first:
        return second
    tail_words = first.split()[-40:]
    for size in range(min(len(tail_words), 25), 4, -1):
        overlap = " ".join(tail_words[-size:])
        position = second.lower().find(overlap.lower())
        if 0 <= position <= 200:
            second = second[position + len(overlap):].lstrip(" .,\n")
            break
    # A continuation can also start the answer over: "A piano 🎹. It has keys for music" got "A piano 🎹. It uses
    # metal strings ..." appended (live riddle check, 2026-10-24). The re-stated opening is dropped, the detail kept.
    opening = re.match(r"\s*.+?[.!?](?:\s+|$)", first)
    if opening and _block_signature(opening.group(0)) and second.lower().startswith(opening.group(0).strip().lower()):
        second = second[len(opening.group(0).strip()):].lstrip(" .,\n")
    if not second:
        return first
    joiner = "" if first.endswith(("\n", " ")) else (" " if not first.endswith((".", "!", "?")) else "\n\n")
    return f"{first}{joiner}{second}".strip()


def _block_signature(block: str) -> str:
    """A stable fingerprint of a paragraph, for spotting it when it recurs."""
    return "".join(ch for ch in block.lower() if ch.isalnum())[:80]


def looks_degenerate(text: str) -> bool:
    """Whether a reply is a stuck record -- one sentence over and over.

    Used to keep a degenerate answer out of the 15-minute reply cache, so a bad
    "whatsup?" is not served again unchanged the next time it is asked. A normal
    answer repeats no sentence more than a couple of times.
    """
    if not text:
        return False
    counts: dict[str, int] = {}
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        key = re.sub(r"\s+", " ", sentence.strip().lower())
        if len(key) < 4:
            continue
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > 4:
            return True
    return False


def collapse_repeated_sentences(text: str, *, max_run: int = 2, max_total: int = 3) -> str:
    """Cut a reply where a sentence starts repeating over and over.

    The paragraph fingerprinting below only catches a whole block recurring.
    A model can also jam on a single sentence -- asked "whatsup?", HAYAT
    answered "I miss you." several hundred times in one unbroken paragraph,
    which has no blank lines to split on and so slid straight through. This
    walks the sentences and stops the moment one repeats more than twice in a
    row, or more than three times in all: the coherent opening is kept, the
    stuck record is dropped.
    """
    if not text:
        return text
    previous_key: str | None = None
    run = 0
    totals: dict[str, int] = {}
    cut: int | None = None
    # Truncate the original text at the offending sentence rather than
    # re-joining pieces, so paragraph breaks and other structure survive for
    # the block-level pass that runs after this.
    for match in re.finditer(r"\S.*?(?:[.!?]+|\n|$)", text):
        key = re.sub(r"\s+", " ", match.group(0).strip().lower())
        if not key:
            continue
        run = run + 1 if key == previous_key else 1
        totals[key] = totals.get(key, 0) + 1
        if run > max_run or totals[key] > max_total:
            cut = match.start()
            break
        previous_key = key
    return text[:cut].rstrip() if cut is not None else text


def drop_restated_prefix(response: str, prior: str) -> str:
    """Remove a leading block that merely reprints the previous reply.

    On a vague follow-up ("all of it, explain in detail") a model sometimes
    reprints its last answer verbatim before adding anything new -- Mohsin saw
    his whole first reply repeated, with the actual new material pushed to the
    bottom and, on a tight window, truncated off the end. The new material is
    the point. If the reply opens with a run of sentences that were already in
    the previous reply, drop that run and keep what follows.

    Conservative on purpose: it only strips a genuinely substantial reprint
    (several sentences) and only when real new content remains afterwards, so a
    legitimately similar opening line is left alone.
    """
    if not response or not prior:
        return response
    prior_keys = {
        re.sub(r"\s+", " ", match.group(0).strip().lower())
        for match in re.finditer(r"\S.*?(?:[.!?]+|\n|$)", prior)
    }
    prior_keys.discard("")
    if not prior_keys:
        return response
    cut = 0
    dropped = 0
    for match in re.finditer(r"\S.*?(?:[.!?]+|\n|$)", response):
        key = re.sub(r"\s+", " ", match.group(0).strip().lower())
        if not key:
            continue
        if key not in prior_keys:
            break
        cut = match.end()
        dropped += 1
    remainder = response[cut:].lstrip()
    # Require a real reprint (not one echoed line) and real content left over.
    if dropped >= 3 and len(remainder.split()) >= 8:
        return remainder
    return response


def collapse_word_spiral(text: str, *, window: int = 50, min_unique_ratio: float = 0.35) -> str:
    """Cut a reply where it dissolves into a punctuation-free word spiral.

    A third failure the sentence and paragraph collapses both miss: the model
    stops writing sentences at all and streams one endless run-on -- asked to
    explain a business in detail, HAYAT trailed into "systematically organised
    methodologically structured logically sequenced ..." for sixty thousand
    characters, a fixed adjective phrase repeating with no full stop or line
    break anywhere in it. With nothing to split on, the other two passes see a
    single very long sentence and leave it whole.

    This one looks at the words directly. It slides a window across them and
    stops at the first stretch whose vocabulary has collapsed -- fewer than a
    third of the words in the window distinct is far past anything ordinary
    prose, even a list of prices, ever does, and squarely where a spiral sits.
    Everything from that point is dropped; the real answer before it is kept.
    """
    if not text:
        return text
    matches = list(re.finditer(r"\S+", text))
    if len(matches) < window * 2:
        return text
    words = [match.group(0).lower() for match in matches]
    for start in range(0, len(words) - window + 1):
        chunk = words[start:start + window]
        if len(set(chunk)) / window < min_unique_ratio:
            return text[: matches[start].start()].rstrip()
    return text


def stop_at_first_repeat(text: str) -> str:
    """Cut a runaway answer at the point it starts repeating itself.

    A model can fall into a loop -- emitting the same paragraph again and
    again, sometimes behind an incrementing header ("2. Data Collection",
    "3. Data Cleaning", ... each followed by the identical list). Asked how
    many companies Jews own, HAYAT returned the same 400-word block fifteen
    times, and the completeness contract, seeing lots of new words each round,
    kept letting it grow. merge() only drops a short seam, so it never noticed.

    Two passes. First the sentence-level collapse, which catches a single
    sentence jammed on repeat inside one paragraph. Then the paragraph-level
    one: the first paragraph whose fingerprint has already appeared truncates
    everything from there. What is kept is the first, complete pass; what is
    dropped is the broken record.
    """
    if not text:
        return text
    # Sentence collapse first: it keeps the coherent opening and drops a
    # period-delimited repeat from the point it starts. The word-spiral pass
    # then handles only what has no sentence boundaries left to work with -- a
    # punctuation-free run-on -- so it never sees a short intro followed by a
    # delimited repeat and cut the intro off with it.
    text = collapse_repeated_sentences(text)
    text = collapse_word_spiral(text)
    if len(text) < 400:
        return text
    blocks = text.split("\n\n")
    seen: set[str] = set()
    kept: list[str] = []
    for block in blocks:
        stripped = block.strip()
        signature = _block_signature(stripped)
        if len(signature) >= 40:
            if signature in seen:
                break  # the record has started skipping; stop here.
            seen.add(signature)
        kept.append(block)
    return "\n\n".join(kept).rstrip()


def is_degenerate_repeat(previous: str, addition: str) -> bool:
    """Whether a continuation is mostly a repeat of what is already written.

    The continuation loop asked the model for more and got the same block back
    under a new header. It counted the words as progress and asked again. This
    lets the loop notice the repeat and stop: if most of the addition's
    paragraphs are already present above, there is nothing new to gain.
    """
    additions = [b.strip() for b in addition.split("\n\n") if len(_block_signature(b)) >= 40]
    if not additions:
        return False
    have = {_block_signature(b) for b in previous.split("\n\n")}
    repeated = sum(1 for b in additions if _block_signature(b) in have)
    return repeated >= max(1, len(additions) * 0.6)


def drop_repeated_paragraphs(answer: str, previous: str) -> str:
    """Remove paragraphs copied from the previous reply.

    The model sometimes answers and then pastes a paragraph from its last
    reply underneath, word for word -- on 2026-09-30 three replies in a row
    ended with the same "Mohsin, I hear you clearly now..." block. A paragraph
    that already appeared (or nearly so) in the previous reply is dropped;
    the reply is kept whole if nothing would be left.
    """
    if not previous or not answer:
        return answer
    from rapidfuzz import fuzz

    squash = lambda value: " ".join(value.lower().split())
    earlier = [squash(part) for part in re.split(r"\n\s*\n", previous) if len(squash(part)) >= 60]
    if not earlier:
        return answer
    kept = []
    for part in re.split(r"\n\s*\n", answer):
        text = squash(part)
        repeated = len(text) >= 60 and any(
            fuzz.ratio(text, old) >= 90 or (len(text) >= 120 and (text in old or old.startswith(text[:120])))
            for old in earlier)
        if not repeated:
            kept.append(part)
    result = "\n\n".join(part.strip() for part in kept if part.strip())
    return result or answer
