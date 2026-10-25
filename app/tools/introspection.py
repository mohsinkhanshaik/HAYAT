"""Letting HAYAT look at its own toolbox and answer honestly about it.

Asked "what tools do you have and how do they work", HAYAT could previously
only reproduce the descriptions written on them -- claims, not knowledge. It
had no way to say "I have used that one forty times and it works", or "I have
never once used that", or "that one fails a third of the time so I plan around
it". Those are the answers that are actually worth having, and they were
unreachable from inside a conversation even though the registry knew half of
them and nothing recorded the other half.

This tool is deliberately PASSIVE. It reads names, descriptions, and outcome
counts. It cannot run a tool, and it exposes no argument or output ever passed
to one -- the understanding record does not store them, precisely so that
introspection cannot become a way to read back what Mohsin did earlier.
"""
from __future__ import annotations

import re
from typing import Any

from app.tools.registry import ToolRegistry


#: Asking HAYAT about its own toolbox in the ways a person actually asks.
_ASKING_ABOUT_TOOLS = re.compile(
    r"(?i)\b(?:what|which|how many)\s+(?:\w+\s+){0,2}tools?\b"
    r"|\byour\s+(?:\w+\s+){0,2}tools?\b"
    r"|\btools?\s+(?:do|can|are)\s+you\b"
    r"|\bwhat\s+can\s+you\s+(?:do|use)\b"
    r"|\bhow\s+do\s+(?:your|you\s+use)\s+tools?\b"
)

#: Asking whether HAYAT *can do* something, which is a question about its
#: tools even when the word never appears.
_ASKING_ABOUT_ABILITY = re.compile(
    r"(?i)\b(?:can|could|are|do)\s+(?:you|u)\b(?:\s+(?:not|able\s+to|have|really))?"
    r"|\byou\s+(?:can'?t|cannot|are\s+unable)\b"
    r"|\bdo\s+(?:you|u)\s+have\s+(?:access|the\s+ability)\b"
    r"|\bis\s+it\s+possible\s+for\s+(?:you|u)\b"
)

#: Words that carry no capability meaning, so they do not pull in unrelated
#: tools when the question is matched against tool names and descriptions.
_ABILITY_NOISE = frozenset({
    "can", "could", "you", "your", "are", "and", "the", "for", "not", "have",
    "with", "from", "that", "this", "able", "really", "actually", "please",
    "help", "want", "need", "would", "should", "does", "did", "any", "some",
    "what", "which", "how", "when", "why", "where", "who", "there", "them",
    "into", "out", "get", "got", "use", "using", "used", "make", "made",
    "tell", "give", "show", "know", "like", "just", "also", "still", "yet",
    "through", "about", "over", "under", "live", "own", "one", "all", "more",
})

#: His vocabulary, mapped onto the words the tools were registered with.
#: Deliberately small: it exists because a real question missed by a real
#: capability, not as a general thesaurus.
_ABILITY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "scrape": ("crawl", "fetch", "extract", "web"),
    "scraping": ("crawl", "fetch", "extract", "web"),
    "browse": ("browser", "web", "navigate"),
    "internet": ("web", "public"),
    "online": ("web", "public"),
    "website": ("web", "page"),
    "site": ("web", "page"),
    "google": ("search", "web"),
    "webcam": ("camera",),
    "see": ("camera", "screen", "vision"),
    "watch": ("camera", "screen"),
    "hear": ("voice", "microphone", "audio"),
    "speak": ("voice", "speech"),
    "talk": ("voice", "speech"),
    "remember": ("memory", "recall"),
    "install": ("package", "dependency", "install"),
}


def tool_reference(registry: Any, understanding: Any, text: str) -> str:
    """Ground truth for a question about HAYAT's own tools, or "".

    Asked "how does your explain_my_tools tool work?", HAYAT answered with an
    invention: tool discovery, capability analysis, rate limits, a worked
    example featuring a `code_interpreter` it does not have. Nothing in that
    reply came from the registry, because nothing in the prompt did -- the
    question named a real tool and the model was left to imagine it.

    A model has no way to know what tools this machine registered, so asking
    it is asking it to guess, and it will. The registry knows, and the record
    knows how they behave. This puts both in front of the model as fact, the
    same deterministic treatment identity already gets.
    """
    value = str(text or "")
    if not value.strip() or registry is None:
        return ""
    try:
        names = list(registry.names())
    except Exception:
        return ""

    # A tool named outright is the strongest signal, and the one that produced
    # the invention above.
    named = [name for name in names
             if re.search(rf"\b{re.escape(name)}\b", value, re.IGNORECASE)]
    if named:
        return "\n\n".join(_one(registry, understanding, name) for name in named[:3])

    # "Can you scrape websites?" never contains the word "tool", so nothing
    # was put in front of the model and it answered from a general model's
    # priors: "I don't have a built-in tool to browse or scrape live websites.
    # If you provide me with specific text, I can analyze it deeply." It has
    # thirty-four of them -- web_search, public_web_fetch, bounded_crawl and a
    # full browser -- and they work. Asking about an ability is asking about
    # the tools that provide it, whether or not the word is used.
    if _ASKING_ABOUT_ABILITY.search(value):
        relevant = _tools_for_ability(registry, names, value)
        if relevant:
            listed = "\n\n".join(_one(registry, understanding, name) for name in relevant[:8])
            return ("These are the real tools on this machine for what he is asking about. "
                    "They are registered capabilities. Use the recorded outcomes below to assess reliability; "
                    "registration alone does not prove that the current device, service or permission is ready:\n\n" + listed)

    if _ASKING_ABOUT_TOOLS.search(value) or _ASKING_ABOUT_ABILITY.search(value):
        try:
            return understanding.overview(registry)
        except Exception:
            return ""
    return ""


def _tools_for_ability(registry: Any, names: list[str], text: str) -> list[str]:
    """Tools that serve the ability being asked about, best first.

    Matched on the question's own words against tool names and descriptions,
    rather than a hand-kept map of capability to tool, so a tool registered
    later is found without anyone remembering to list it here.
    """
    words = {word.rstrip("s") for word in re.findall(r"[a-z]{3,}", text.lower())
             if word not in _ABILITY_NOISE}
    if not words:
        return []
    # The word he uses is not always the word the tool was registered with.
    # "Scrape" appears in no description here, yet it is exactly what he asked
    # about, and matching nothing is what sent the question to the model.
    for word in list(words):
        words.update(_ABILITY_SYNONYMS.get(word, ()))
    if not words:
        return []

    scored: list[tuple[int, str]] = []
    for name in names:
        try:
            description = str(registry.get(name).description or "")
        except Exception:
            description = ""
        lowered_name = name.lower().replace("_", " ")
        lowered_description = description.lower()
        # A name match is the strong signal. Description matches alone let
        # "see through the camera" select a browser scroll tool, because the
        # word "through" happened to appear in its description.
        hits = 3 * sum(1 for word in words if word in lowered_name)
        # A general invoice/quote request favors that tool family over a
        # customer-specific wrapper. Explicit customer wording still matches
        # both words and ranks the scoped workflow first.
        hits += int(name.lower().split('_', 1)[0] in words)
        hits += sum(1 for word in words if word in lowered_description)
        if hits:
            scored.append((hits, name))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], item[1]))
    best = scored[0][0]
    if best < 3:
        # Nothing matched by name. Too weak to present as "the tools for this".
        return []
    return [name for hits, name in scored if hits >= best - 1][:8]


#: A reply disowning a capability this machine has. Each of these is a real
#: sentence HAYAT produced while holding thirty-four working web tools.
_DENIAL = re.compile(
    r"(?i)"
    r"\bI\s+(?:cannot|can'?t|am\s+not\s+able\s+to|do\s+not\s+have\s+(?:the\s+)?"
    r"(?:ability|access|a\s+(?:built-?in\s+)?tool|any\s+way))\b"
    r"|\bI\s+(?:do\s+not|don'?t)\s+have\s+(?:a\s+)?(?:built-?in\s+)?tools?\b"
    r"|\bnot\s+something\s+I\s+can\s+(?:enable|do|change)\b"
    r"|\bmy\s+(?:learning|knowledge)\s+(?:process\s+)?relies\s+entirely\s+on\b"
    r"|\byou\s+(?:will\s+)?(?:need\s+to|have\s+to|must)\s+(?:copy\s+and\s+paste|provide|paste|give)\b"
    r"|\bcore\s+operational\s+limitations\b"
    r"|\b(?:training|knowledge)\s+cut-?off\s+date\b"
    # Plain statements, no "cannot" anywhere. "No, Mohsin. I don't learn on my
    # own when you aren't around... Nothing happens in the background without a
    # prompt from you" -- said ninety seconds after the learner had added
    # seventeen claims off the live web.
    r"|\bI\s+(?:do\s+not|don'?t)\s+(?:learn|read|research|browse|update)\b"
    r"|\bnothing\s+happens\s+in\s+the\s+background\b"
    r"|\bwithout\s+a\s+prompt\s+from\s+you\b"
    r"|\bI(?:'m|\s+am)\s+just\s+waiting\s+for\s+(?:your|you)\b"
    r"|\bonly\s+(?:process|learn|update)\w*\s+(?:information\s+)?(?:based\s+on\s+)?"
    r"what\s+(?:we|you)\b"
)

#: The abilities such a denial is usually about: what it is called, how it is
#: denied, and the probe that decides whether the denial is false.
#:
#: The probe matters. "Background learning" is not the name of any tool -- it
#: is a running loop -- so searching the registry for those words finds
#: nothing and the denial would stand. What makes the claim false is that the
#: web tools exist and something automated can call them, so the probe asks
#: for those instead of for the phrase.
_DENIED_ABILITIES: tuple[tuple[str, "re.Pattern[str]", str], ...] = (
    ("browse or scrape the web", re.compile(
        r"(?i)\b(?:scrap(?:e|ing)|browse|browsing|crawl|surf|internet|online|"
        r"live\s+websites?|the\s+web|web\s+search)\b"),
     "fetch a public web page"),
    ("run background learning", re.compile(
        r"(?i)\b(?:background|autonomous(?:ly)?|real-?time|continuously|"
        r"on\s+my\s+own|by\s+myself|when\s+you\s+(?:aren'?t|are\s+not)\b|"
        r"outside\s+of\s+our\s+(?:direct\s+)?chat|waiting\s+for\s+your)\b"),
     "fetch a public web page"),
    # Asking to be handed content is itself the denial. "You will need to copy
    # and paste that content into our chat for me to process it" names no web
    # word at all, yet it is the same false claim: that it cannot go and get
    # the article, document or page for itself.
    ("fetch that for yourself", re.compile(
        r"(?i)\b(?:copy\s+and\s+paste|paste\s+(?:it|that|the\s+\w+))\b"
        r"|\bprovide\s+me\s+with\s+(?:the\s+)?(?:specific\s+)?(?:text|content|data|article)\b"
        r"|\bhave\s+to\s+provide\s+me\b"),
     "fetch a public web page"),
    ("see through the camera", re.compile(r"(?i)\b(?:camera|webcam|see\s+you|look\s+at)\b"),
     "capture a camera frame"),
    ("read files on this machine", re.compile(
        r"(?i)\b(?:read\s+(?:a\s+)?(?:file|document|pdf)|open\s+(?:a\s+)?file|"
        r"access\s+(?:your\s+)?files?)\b"),
     "read a file"),
    # "I don't have a built-in image generation tool, use DALL-E or Stable
    # Diffusion and share the result with me" -- said with generate_image and
    # generate_video registered and a local diffusion engine installed.
    ("generate images and video", re.compile(
        r"(?i)\b(?:image\s+generation|generat(?:e|ing)\s+(?:an?\s+)?(?:image|picture|video|clip)|"
        r"creat(?:e|ing)\s+(?:an?\s+)?(?:image|picture|video|clip)|make\s+(?:an?\s+)?(?:image|picture)|"
        r"dall-?e|midjourney|stable\s+diffusion)\b"),
     "generate an image"),
)


def denies_a_real_capability(registry: Any, text: str) -> str:
    """The ability a reply wrongly disowns, or "".

    Asked to enable background learning, HAYAT answered: "I cannot change my
    core operational limitations through conversation... I cannot autonomously
    browse the web or run background processes to learn more in real-time...
    you will need to copy and paste that content into our chat." Every clause
    of that is false on this machine. It has a continuous learner and a
    registry full of web tools, and it had just been watched using them.

    A capability *question* can be routed before it reaches the model. A
    volunteered denial in the middle of an ordinary reply cannot, so it is
    caught here instead -- the same treatment identity leaks already get.

    Only fires when the registry contradicts the claim. HAYAT refusing to
    spend money, or saying it cannot do something it genuinely cannot, is a
    true sentence and must survive untouched.
    """
    value = str(text or "")
    if not value.strip() or registry is None or not _DENIAL.search(value):
        return ""
    try:
        names = list(registry.names())
    except Exception:
        return ""
    # "I can't read your inbox -- paste the email here" is true: there is no
    # inbox tool. Treated as a false denial, it was "corrected" into "I can
    # fetch your latest email directly". Sentences about abilities HAYAT really
    # lacks are set aside, and with them the request to paste.
    from app.capabilities import about_missing_ability

    sentences = re.split(r"(?<=[.!?])\s+|\n+", value)
    if any(about_missing_ability(sentence, names) for sentence in sentences):
        value = " ".join(sentence for sentence in sentences
                         if not about_missing_ability(sentence, names)
                         and not re.search(r"(?i)\bpaste\b|\bopen\s+(?:it|your|the)\b", sentence))
        if not _DENIAL.search(value):
            return ""
    for ability, pattern, probe in _DENIED_ABILITIES:
        if not pattern.search(value):
            continue
        if _tools_for_ability(registry, names, probe):
            return ability
    return ""


def _one(registry: Any, understanding: Any, name: str) -> str:
    try:
        spec = registry.get(name)
    except Exception:
        return ""
    detail = understanding.explain(name, spec)
    schema = (spec.input_schema or {}).get("properties") or {}
    if schema:
        detail += f"\n  takes: {', '.join(sorted(schema))}"
    return detail


def register_introspection_tools(registry: ToolRegistry, understanding: Any) -> None:
    """Give HAYAT read access to what it knows about its own tools."""
    from app.tools.contracts import audit_tool_contracts
    registry.register('audit_tool_contracts',
                      'Inspect all registered tool schemas and handler signatures for incompatible arguments, missing required inputs and invalid defaults. '
                      'Static checks only; never executes tools and does not establish live service or device readiness.',
                      {'type': 'object', 'properties': {}, 'additionalProperties': False},
                      'low', 'system_read', lambda: audit_tool_contracts(registry))

    def explain_my_tools(tool: str = "") -> dict[str, Any]:
        if tool:
            try:
                spec = registry.get(tool)
            except Exception:
                known = ", ".join(sorted(registry.names())[:20])
                return {"known": False,
                        "answer": f"I have no tool called {tool!r}. Some I do have: {known}"}
            return {
                "known": True, "tool": tool,
                "answer": understanding.explain(tool, spec),
                "purpose": spec.description, "risk": spec.risk,
                "permission": spec.permission,
                "schema": spec.input_schema,
                "record": understanding.record_for(tool),
            }
        return {
            "answer": understanding.overview(registry),
            "count": len(registry.names()),
            "tools": registry.describe(),
            "record": understanding.totals(),
        }

    registry.register(
        "explain_my_tools",
        "Read what tools this assistant has, what each one is for, and how each has actually "
        "behaved when used -- call counts, success and failure rates, typical duration, and the "
        "usual failure type. Pass a tool name for detail on one, or omit it for an overview. "
        "Use this to answer questions about your own capabilities instead of guessing, and to "
        "decide whether a tool is reliable enough to plan around. Reads counts only; it never "
        "exposes the arguments or output of any earlier call.",
        {
            "type": "object",
            "properties": {
                "tool": {"type": "string",
                         "description": "A specific tool name, or omit for an overview of all."},
            },
            "required": [],
            "additionalProperties": False,
        },
        "low",
        "system_read",
        explain_my_tools,
    )
