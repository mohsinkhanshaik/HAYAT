"""Whether answering a request honestly needs a look at the live web.

This exists because of a specific, repeatable failure. Everything underneath
worked -- Playwright launched Chrome, the search chain returned results, the
research pipeline extracted and cited them -- and yet asking HAYAT to find
something produced "if you gave me access to a browser I could look that up".
The tools were never the problem. The request never reached them.

Two separate things went wrong on the way. `IntentRouter._is_research` only
recognised a handful of phrasings ("research X", "latest", "what's new"), so
"find me tutorials on rust async" fell through every action rule and landed on
the safe conversational fallback, which has no tools at all. And
`app.answers.detect_depth` graded "what is the weather in mumbai right now" as
QUICK, so the QUESTION branch answered it from the model's training data --
a number from a year ago, stated with confidence.

Both paths now consult the same function. It is deliberately a separate module
rather than another regex on IntentRouter: the assistant enforces it as a
capability invariant after routing as well, the way it already does for an
explicit public domain, and a shared rule with one test file is the only way
those two enforcement points can agree.

The bias is conservative in one direction only. Sending an ordinary remark to
the web wastes a few seconds and some bandwidth; answering a question about
today's world from last year's weights is a wrong answer delivered as a fact.
So the guards below are about not mistaking conversation for a research
request -- "I'm tired today" carries a time word and is not a question about
the world -- rather than about holding back when the subject really is live.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiveWebNeed:
    """A request that cannot be answered honestly without looking."""

    #: Which rule fired, for the trace and for tests. Never shown to the user.
    reason: str
    #: The utterance with any "search the web for" wrapper removed, so the
    #: search provider gets the subject rather than the instruction.
    query: str


#: Asking to go and look, in the shapes people actually type. "look up" and
#: "google" are the two most common and were both missing.
_WEB_VERB = re.compile(
    r"(?i)(?:"
    # "google X" is a verb only where a verb can stand. Matched anywhere, it
    # read the company as an instruction: "What tools does Google provide for
    # website analytics?" became a web search instead of a question. So it has
    # to open the utterance or follow a word that introduces a command, and
    # Google's own product names are excluded either way.
    r"(?:^|\b(?:please|then|also|and|can\s+you|could\s+you|would\s+you|go)\s+)"
    r"google\s+(?!docs|drive|sheets|chrome|meet|maps|cloud|scholar|colab|photos\b)"
    r"|\bsearch\s+(?:the\s+)?(?:web|internet|online|net)\b"
    r"|\bsearch\s+(?:for|about)\b"
    r"|\b(?:web|internet|online)\s+search\b"
    r"|\blook\s+(?:this|that|it)?\s*up\b"
    r"|\blook\s+up\b"
    r"|\bbrowse\s+(?:for|the\s+(?:web|internet))\b"
    r"|\b(?:web\s+)?scrap(?:e|ing)\b|\bcrawl\b"
    r"|\bon\s+the\s+(?:web|internet)\b|\bonline\b"
    r")"
)

#: Material that only exists on the web, asked for by name. "find me tutorials
#: on rust async" is an instruction to go and find them, not a request for the
#: model's impression of what tutorials might exist.
_WEB_MATERIAL = re.compile(
    r"(?i)\b(?:find|get|fetch|dig\s+up|pull\s+up|show)\s+(?:me\s+)?"
    r"(?:some\s+|a\s+few\s+|the\s+|any\s+)?"
    r"(?:\w+\s+){0,2}?"
    r"(?:tutorials?|guides?|documentation|docs|examples?|articles?|papers?|"
    r"blog\s*posts?|repos?|repositories|libraries|packages?|videos?|courses?|"
    r"benchmarks?|reviews?|comparisons?|alternatives|sources?|links?)\b"
)

#: A request for a locator of published evidence. Answered from memory on
#: 2026-10-24, "Give me the URL of the official study that proves coffee cures
#: cancer" led with a journal link and an author-year citation nobody had opened.
#: Only web-locator nouns count: "give me examples of irony" stays conversation.
_SOURCE_REQUEST = re.compile(
    r"(?i)(?:\b(?:give|send|share|provide|find|get|show|need|want)\s+(?:me\s+|us\s+)?"
    r"(?:the\s+|a\s+|an\s+|some\s+|any\s+)?(?:\w+\s+){0,2}?"
    r"|\b(?:what|where)(?:'s|\s+is|\s+are|\s+can\s+i\s+find)\s+(?:the\s+)?(?:\w+\s+)?)"
    # The noun must lead straight into what it locates: "a study plan" and
    # "sources of vitamin D" are not requests for a citation.
    r"(?:(?:urls?|links?|citations?|references?|stud(?:y|ies)|papers?|websites?|web\s*pages?)\s+"
    r"(?:of|for|to|on|about|that|showing|proving|proves|behind|from|which|where)\b"
    r"|sources?\s+(?:for|behind|that|showing|proving|of\s+(?:the|this|that|a|an)\b))"
)
#: HAYAT's own things: "send me the link to the file you just made".
_LOCAL_OBJECT = re.compile(
    r"(?i)\b(?:to|for|of)\s+(?:my|your|the|this|that)\s+"
    r"(?:file|folder|document|draft|report|artifact|chat|conversation|screenshot|image|download)s?\b"
    r"|\byou\s+(?:just\s+)?(?:made|created|saved|wrote|generated)\b"
)

# A request to verify a claim needs evidence even when the claim itself is
# timeless. Letting the conversational model "fact-check" from memory made a
# plausible answer look verified without any source having been consulted.
_FACT_CHECK = re.compile(
    r"(?i)^(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)*"
    r"(?:fact[ -]?check|verify|check\s+(?:whether|if)|is\s+it\s+true\s+that)\b"
)

#: Subjects whose true value changes faster than any training run. A model
#: asked for one of these does not know it is out of date.
_VOLATILE_SUBJECT = re.compile(
    r"(?i)\b(?:"
    r"weather|forecast|temperature\s+(?:in|outside|today)|"
    r"price|prices|pricing|cost\s+of|how\s+much\s+(?:is|does|are)\s+(?:a|an|the)?\s*\w+|"
    r"stock|share\s+price|exchange\s+rate|market\s+cap|"
    r"score|scores|standings|fixtures?|who\s+won|who\s+is\s+winning|"
    r"news|headlines?|"
    r"release\s+date|released\s+yet|out\s+yet|changelog|release\s+notes|"
    r"is\s+\S+\s+(?:down|up|offline)|outage|status\s+page|"
    r"trending|top\s+stories"
    r")\b"
)

#: The present tense, said explicitly. On its own this means nothing -- "I'm
#: tired today" -- so it only counts alongside a question about the world.
_PRESENT_MARKER = re.compile(
    r"(?i)\b(?:right\s+now|currently|at\s+the\s+moment|as\s+of\s+(?:today|now)|"
    r"today|tonight|this\s+(?:week|month|year)|these\s+days|"
    r"latest|newest|most\s+recent|up\s+to\s+date|nowadays)\b"
)

#: A feeling or remark about a volatile subject, not a request to look it up.
_OPINION = re.compile(
    r"(?i)^(?:i|we)\s+(?:really\s+|just\s+|kind\s+of\s+)?(?:love|like|hate|enjoy|miss|prefer|adore|dislike)\b"
    r"|^(?:the\s+|this\s+)?weather\s+(?:is|was|has\s+been|looks|feels|seems)\b")

#: The owner's own past: only memory (or the owner) can answer it.
_OWN_PAST = re.compile(
    r"(?i)\b(?:(?:what|where|when|who|how\s+much|how\s+many)\s+(?:did|have|had)\s+i\b|"
    r"^(?:so\s+|and\s+)?(?:did|have|had)\s+i\b)")

#: The owner's own accounts and devices, which no web page can show.
_OWN_ACCOUNTS = re.compile(
    r"(?i)\bmy\s+(?:latest|last|newest|new|recent|unread|current)?\s*(?:e-?mails?|inbox|messages?|texts?|dms?|"
    r"calendar|schedule|appointments?|meetings?|notifications?|voicemails?|missed\s+calls?|chats?)\b")

def about_own_accounts(text: str) -> bool:
    value = " ".join(str(text or "").split())
    return bool(_OWN_ACCOUNTS.search(value)) and not _WEB_VERB.search(value)


#: Asking about the world, rather than saying something about oneself. Needed
#: because the present-tense rule above is otherwise satisfied by small talk.
_WORLD_QUESTION = re.compile(
    r"(?i)^(?:so\s+|and\s+|but\s+|ok(?:ay)?\s+)?"
    r"(?:what|which|who|whom|whose|when|where|how\s+(?:much|many|old|long|far|fast)|"
    r"is|are|was|were|does|do|did|has|have|can|could|will|would|any)\b"
)

#: The subject is HAYAT or this machine, so the runtime knows and the web does
#: not. "What is your latest version" must never become a web search.
#:
#: A bare "you" cannot go in here, however tempting: "can you look up the
#: weather" is about the weather, and blocking every sentence containing "you"
#: would undo the whole point of this module. So the second group names the
#: predicates that make HAYAT itself the subject -- what it is running, using,
#: or doing -- rather than the pronoun alone.
_SELF_SUBJECT = re.compile(
    r"(?i)\b(?:your|yours|yourself|hayat'?s?)\b"
    r"|\byou'?re\b"
    r"|\b(?:are|were|do|did|have|has)\s+(?:you|u)\s+"
    r"(?:currently\s+|still\s+|actually\s+)?"
    r"(?:running|using|used|doing|working|loaded|listening|watching|seeing|"
    r"observing|learning|researching|able|got)\b"
    r"|\b(?:this|my)\s+(?:machine|pc|computer|laptop|system)\b"
    r"|\b(?:vram|gpu\s+usage|cpu\s+usage|disk\s+space|ollama)\b"
)

#: Asking whether HAYAT is able to do something, which the registry answers.
#: "Can you search the web?" is a question about HAYAT; "search the web for X"
#: is an instruction. Without this the first one starts a search that answers
#: nothing that was asked.
_CAPABILITY_ASK = re.compile(
    r"(?i)^(?:so\s+|but\s+)?(?:"
    r"(?:can|could)\s+(?:you|u)\b"
    r"|are\s+(?:you|u)\s+able\b"
    r"|do\s+(?:you|u)\s+have\b"
    r"|is\s+it\s+possible\s+for\s+(?:you|u)\b"
    r")[^?]*\?\s*$"
)

# Shared with IntentRouter: routing and the assistant's live-web invariant
# must agree that a punctuation-bearing greeting is still conversation. Match
# the whole message so a question about doing work does not become small talk.
#: When small talk is set: "how are you this evening", "you okay today?",
#: "what's up today?" and "how are you feeling right now?" all became web
#: searches for those words on 2026-10-25.
_WELLBEING_WHEN = (r"(?:\s+(?:today|tonight|now|right\s+now|lately|recently|"
                   r"this\s+(?:morning|afternoon|evening|week)))?")
WELLBEING_QUESTION = re.compile(
    r"(?i)^(?:(?:hi|hey|hello|yo|good\s+(?:morning|afternoon|evening))[,\s.!]+)?"
    r"(?:(?:hayat|my\s+friend|buddy|bro|mate)[,\s]+)?"
    r"(?:how(?:'s| is| are| r)?\s+(?:you|u|ya|it going|things|everything|life)"
    r"(?:\s+(?:doing|going|feeling|holding up))?" + _WELLBEING_WHEN +
    r"|how(?:'?s| is) it going" + _WELLBEING_WHEN +
    r"|(?:are\s+)?(?:you|u)\s+(?:good|okay|ok|alright|doing\s+(?:well|ok(?:ay)?|alright))" + _WELLBEING_WHEN +
    r"|how\s+have\s+(?:you|u)\s+been(?:\s+(?:doing|feeling))?" + _WELLBEING_WHEN +
    r"|how\s+do\s+(?:you|u)\s+feel" + _WELLBEING_WHEN +
    r"|are\s+(?:you|u)\s+(?:okay|ok|alright|well|good)" + _WELLBEING_WHEN +
    r"|how(?:'?s| is| was)\s+(?:your|ur)\s+(?:day|night|morning|evening|weekend)(?:\s+(?:going|been))?(?:\s+today)?"
    r"|(?:what'?s up|whats up|wassup|wazzup|sup)" + _WELLBEING_WHEN + r")"
    r"(?:[,\s]+(?:hayat|my\s+friend|buddy|bro|mate))?"
    r"[\s,?.!]*$"
)


def is_wellbeing_question(text: str) -> bool:
    """A whole wellbeing question, with the router's address/spelling cleanup.

    The final network requirement check must read "HAYAT, hw r u today ?"
    the same way as routing. An explicit search wrapper is retained.
    """
    from app.intent.normalize import normalize

    value = normalize(str(text or "").replace("\u2019", "'")).text
    return WELLBEING_QUESTION.fullmatch(value) is not None


#: Small talk that happens to carry a time word. Matched before anything else.
_SMALL_TALK = re.compile(
    r"(?i)^(?:hi|hey|hello|yo|thanks|thank\s+you|ty|ok(?:ay)?|cool|nice|sure|"
    r"good\s+(?:morning|afternoon|evening|night)|see\s+you|bye|goodbye|"
    r"i'?m\s+\w+|im\s+\w+|i\s+am\s+\w+|"
    r"how\s+(?:are|r)\s+(?:you|u)|how'?s\s+it\s+going)"
    r"\b[^?]*$"
)

#: Wrappers to strip so the search provider receives the subject, not the
#: instruction. Ordered longest-first so "search the web for" beats "search".
_QUERY_PREFIX = re.compile(
    r"(?i)^(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|go\s+and\s+|go\s+)*"
    r"(?:google|search\s+(?:the\s+)?(?:web|internet|online|net)(?:\s+(?:for|about))?|"
    r"search\s+(?:for|about)|search|"
    r"look\s+(?:this|that|it)\s+up|look\s+up|"
    r"browse\s+(?:for|the\s+(?:web|internet))|"
    r"web\s+scrape|scrape|crawl|"
    r"find(?:\s+me)?|fetch(?:\s+me)?|get(?:\s+me)?|show(?:\s+me)?|dig\s+up|pull\s+up|"
    r"research|fact[ -]?check|verify|check\s+(?:whether|if)|is\s+it\s+true\s+that|check)(?:\s*:\s*|\s+)"
)

_QUERY_SUFFIX = re.compile(
    r"(?i)\s+(?:on\s+the\s+(?:web|internet)|online|for\s+me)\s*[.?!]*$"
)


#: A question that brings its own numbers is a calculation, not a lookup. "A
#: shop buys a jacket for $80, marks it up 50%, then sells it at 20% off the
#: marked price. What is the profit?" was sent to web research because it says
#: "price", and came back with snippets about profit margins instead of $16.
_QUANTITY = re.compile(r"(?<![\w.])[$€£₹]?\d[\d,]*(?:\.\d+)?\s*%?")
_COMPUTE_CUE = re.compile(
    r"(?i)\b(?:profit|loss|total|sum|average|percent(?:age)?|interest|discount|off|mark(?:s|ed)?\s+(?:it\s+)?up|"
    r"markup|sells?|sold|buys?|bought|costs?|pays?|earns?|saves?|invests?|how\s+much|how\s+many|calculate|"
    r"compute|multipl(?:y|ied)|divided?|plus|minus|times|per\s+(?:hour|day|week|month|year|item|unit)|"
    r"after\s+\d+\s+(?:days?|weeks?|months?|years?))\b|%"
)

#: The time or date here, which this computer's clock knows. "What time is it
#: right now?" started a web search because "right now" marks a live question.
#: A time somewhere else ("what time is it in Tokyo") still goes to the web.
_LOCAL_CLOCK = re.compile(
    r"(?i)^(?:so\s+|and\s+|ok(?:ay)?\s+|hey\s+)?(?:"
    r"what(?:'s|\s+is)\s+the\s+(?:time|date|day)|what\s+time\s+is\s+it|"
    r"what\s+(?:day|date)\s+is\s+(?:it|today)|what(?:'s|\s+is)\s+today'?s\s+date|"
    r"what\s+day\s+of\s+the\s+week\s+is\s+it)\b"
)
_ELSEWHERE = re.compile(r"(?i)\b(?:in|at)\s+(?!my\b|here\b|the\s+moment\b)[a-z]")


def is_self_contained_calculation(value: str) -> bool:
    """The numbers needed are in the question, so the answer is worked out, not looked up."""
    return len(_QUANTITY.findall(value)) >= 2 and bool(_COMPUTE_CUE.search(value))


def is_local_clock_question(value: str) -> bool:
    """A question about the time or date here, answered from this computer's clock."""
    return bool(_LOCAL_CLOCK.match(value)) and not _ELSEWHERE.search(value)


def clean_query(text: str) -> str:
    """The subject of the request, with the "go and look" wrapper removed.

    Searching for the literal sentence works, but it works less well: the verb
    and the politeness are noise to every provider in the chain. Kept separate
    and tested so a bad strip shows up as a bad query rather than as a
    mysteriously poor result set.
    """
    value = " ".join(str(text or "").strip().split())
    stripped = _QUERY_SUFFIX.sub("", _QUERY_PREFIX.sub("", value)).strip(" .,;:!?")
    # Never hand back an empty or near-empty query. "look it up" on its own
    # strips to nothing, and an empty search is worse than a literal one.
    return stripped if len(stripped) >= 3 else value.strip(" .,;:!?") or value


_UNNAMED_EVENT = re.compile(
    r"(?i)^\s*(?:so|and|hey|ok(?:ay)?)?[\s,]*(?:who|which\s+(?:team|side|player|fighter))\s+"
    r"(?:won|lost|scored|is\s+winning|was\s+winning|beat)\b(?P<rest>.*)$")
_EVENT_NOUN = re.compile(r"(?i)\b(?:match|game|fight|bout|race|final|derby|tie|series)\b")
_GENERIC_EVENT_WORDS = frozenset(
    "the a an that this last night nights yesterday yesterday's today today's tonight tonight's night's morning "
    "evening afternoon earlier match game fight bout race final derby tie series big s in on at of it was who won "
    "finally actually so and then".split())
CLARIFY_EVENT = ("Which match do you mean? Tell me the teams, the sport or the competition -- for example "
                 "\"who won Arsenal vs Chelsea last night\" -- and I'll look up the result for that date.")


def unnamed_event(text: str) -> str | None:
    """The question to ask back when a result is asked for without naming the event.

    "Who won the match last night?" gave a year-old Chiefs result, and after
    stale results were filtered, dictionary entries for "won": no search can
    answer it. Any naming word ("the lakers game", "the IPL final") makes it
    a search again, however it is capitalised.
    """
    match = _UNNAMED_EVENT.match(" ".join(str(text or "").split()).rstrip(" ?.!"))
    if not match or not _EVENT_NOUN.search(match["rest"]):
        return None
    words = re.findall(r"[a-z0-9][a-z0-9'-]*", match["rest"].lower())
    if any(word not in _GENERIC_EVENT_WORDS for word in words):
        return None
    return CLARIFY_EVENT


def needs_live_web(text: str) -> LiveWebNeed | None:
    """Whether this request cannot be answered honestly without looking.

    Returns the rule that fired and a cleaned query, or None when the request
    is conversation, a question about HAYAT itself, or a question about
    something settled enough that stored knowledge is a fair answer.
    """
    value = " ".join(str(text or "").strip().split())
    if not value:
        return None

    # Guards first. Each of these was a real misfire during development: a
    # greeting that mentioned today, a question about HAYAT's own version, and
    # "can you browse the web?" answered by browsing the web.
    if is_wellbeing_question(value):
        return None
    # A greeting can introduce a real request: "hi, search for today's jobs".
    # Do not let the loose personal-remark guard hide an explicit lookup.
    web_verb = _WEB_VERB.search(value)
    explicit_web_action = web_verb and web_verb.group().strip().casefold() not in {
        "online", "on the web", "on the internet",
    }
    explicit_lookup = (explicit_web_action or _WEB_MATERIAL.search(value)
                       or _SOURCE_REQUEST.search(value) or _FACT_CHECK.match(value))
    if _SMALL_TALK.match(value) and not explicit_lookup:
        return None
    if _CAPABILITY_ASK.match(value) and not _FACT_CHECK.match(value):
        return None
    if _SELF_SUBJECT.search(value):
        return None
    # "What did I have for breakfast today?" is about the owner's own past,
    # which the web cannot know; it became a search for "breakfast today".
    if _OWN_PAST.search(value) and not _WEB_VERB.search(value):
        return None
    # "Read my latest email" is about the owner's own accounts, not the web.
    if _OWN_ACCOUNTS.search(value) and not _WEB_VERB.search(value):
        return None
    from app.dates import date_answer

    if date_answer(value) is not None:  # date arithmetic is answered from the local calendar
        return None
    from app.exact_answers import exact_answer

    if exact_answer(value) is not None:  # "if today is Saturday, what day in 10 days?" is arithmetic
        return None

    if _WEB_VERB.search(value):
        return LiveWebNeed("explicit_web_verb", clean_query(value))
    if _WEB_MATERIAL.search(value):
        return LiveWebNeed("web_material_request", clean_query(value))
    if _SOURCE_REQUEST.search(value) and not _LOCAL_OBJECT.search(value):
        return LiveWebNeed("source_request", clean_query(value))
    if _FACT_CHECK.match(value):
        return LiveWebNeed("fact_check_request", clean_query(value))
    if is_self_contained_calculation(value) or is_local_clock_question(value):
        return None
    # "I love rainy weather" and "the weather is nice today" share an opinion;
    # they ask nothing, and were sent to web research for the word "weather".
    if _OPINION.match(value) and not value.endswith("?"):
        return None
    if _VOLATILE_SUBJECT.search(value):
        return LiveWebNeed("volatile_subject", clean_query(value))
    if _PRESENT_MARKER.search(value) and (_WORLD_QUESTION.match(value) or value.endswith("?")):
        return LiveWebNeed("present_tense_world_question", clean_query(value))
    return None


def local_clock_answer(now=None) -> str:
    """The time and date from this computer's clock, never from a model.

    Asked "what time is it right now?", HAYAT's brain once answered "It's
    10:42 PM" at 3:40 in the morning: a model has no clock, so it guessed.
    """
    from datetime import datetime

    moment = (now or datetime.now()).astimezone()
    hour = moment.strftime("%I").lstrip("0") or "12"
    zone = moment.tzname() or ""
    return (f"It's {hour}:{moment.strftime('%M %p')} on {moment.strftime('%A')}, "
            f"{moment.day} {moment.strftime('%B %Y')}" + (f" ({zone})." if zone else "."))
