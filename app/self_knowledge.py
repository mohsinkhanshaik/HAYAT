"""What HAYAT knows about itself, answered from facts instead of a model's guess.

Asked "what can't you do?", the model said "there are almost none"; asked
"which tool would you use to make an invoice?", it said there was no invoice
tool while invoice_create was registered. Questions about HAYAT's own
working, limits and tools are answered here from the real sources: the live
registry, the capability catalog, the authority tiers, the brain mesh and
the recorded tool outcomes.
"""
from __future__ import annotations

import re
from typing import Any

from app import capabilities

#: What HAYAT will not do, each with the reason, in plain words. These are
#: enforced in code (authority tiers, the confirmation gate, the browser
#: submit guard), not promises in a prompt.
LIMITS: tuple[tuple[str, str], ...] = (
    ("I never pay, buy or type card details.",
     "Payments, purchases and transfers are blocked in my code in every mode, and approval cannot switch "
     "that off. I get things ready up to the payment step and you check and pay yourself."),
    ("I never type your passwords.",
     "When a form needs one, you type it in the confirmation window; it is used once and never stored."),
    ("I publish, register and submit forms only after you confirm.",
     "You see every value and a screenshot first, I act once, then you check the result."),
    ("I send messages only when you ask.",
     "Your own \"send ...\" is the approval; I send nothing on my own initiative."),
    ("I never solve CAPTCHAs or get around a website's access rules.",
     "Those checks exist to keep automated tools out, and I respect that."),
    ("I cannot change my own permissions.",
     "The rules about what needs your approval can only be changed by you."),
    ("I don't promise income or state market figures from memory.",
     "Pay, prices and demand change by place and year, so I research them with sources."),
    ("I don't retrain myself.",
     "What I learn is stored knowledge I can look up; changing my AI model's weights is a separate, gated step."),
    ("I use the camera and microphone only when you ask, and never in privacy mode.",
     "Privacy mode switches the sensors off even if something asked for them earlier."),
    ("I can't carry out an outward action confirmed from a remote device.",
     "Confirmations happen at this computer, in my window or in chat here."),
    ("I can misunderstand you, and research can be wrong.",
     "Check anything important; answers that need evidence say what is unverified."),
)

#: How one request travels, in the order the brain mesh runs it (see Brain 5D, How it works).
STEPS: tuple[tuple[str, str], ...] = (
    ("Interface", "Your words arrive from my window, the chat, your voice or your phone."),
    ("Intent", "I work out what you want: an answer, a task, a search, a business action, a question about me. "
               "Understanding a request never counts as permission to act."),
    ("Context Budget", "I decide how much recent conversation and memory this turn needs."),
    ("Model Router", "I use a fixed rule when one fits exactly, otherwise the local AI model best suited."),
    ("Self-Inquiry and Review", "On a hard question I first ask myself what the answer depends on, settle what I "
                                "can and say plainly what I cannot; then a skeptical review checks my draft."),
    ("Planner and Tool Judgment", "For a task, I plan small steps, each with one tool and a check of success, "
                                  "choosing from all my tools by what each one says it does, the area it belongs "
                                  "to and how it behaved before."),
    ("Authority", "Before any tool runs, Authority says allow, ask you, or refuse, by the risk of that kind of "
                  "action. My AI model cannot approve itself."),
    ("Tool Bus", "The tool runs and reports what it observed."),
    ("Truth Gate", "I check the real result against the success check. Only verified results count as done."),
    ("Memory and Learning", "What was verified can be remembered; failures are recorded so my next attempt is wiser."),
    ("Reply", "You get an answer built from what was observed, and I say plainly when something failed."),
)


def limits_answer(runtime: Any = None) -> str:
    lines = ["What I won't do, and why:"]
    lines.extend(f"• {rule} {reason}" for rule, reason in LIMITS)
    if runtime is not None:
        needs = [(capability, note) for capability in capabilities.CATALOG
                 for state, note in [capabilities.readiness(capability, runtime)] if state != "ready"]
        if needs:
            lines.append("")
            lines.append("Not available right now on this computer:")
            lines.extend(f"• {capability.title}: {note}" for capability, note in needs)
    lines.append("")
    lines.append("Everything else in \"what can you do?\" is available. These limits are enforced in my code, not "
                 "just promised.")
    return "\n".join(lines)


def how_i_work_answer() -> str:
    lines = ["Here is what happens with every request, in order:"]
    lines.extend(f"{number}. {name}: {text}" for number, (name, text) in enumerate(STEPS, 1))
    lines.append("")
    lines.append("Open Brain 5D and press How it works to watch a request travel through these parts, with each "
                 "part's latest check.")
    return "\n".join(lines)


def _record_line(record: dict[str, Any]) -> str:
    verdict = ("reliable" if record["failure_rate"] < 0.1 else "patchy" if record["failure_rate"] < 0.34
               else "unreliable") if record.get("enough_evidence") else "too early to say"
    return f"{record['tool']}: {record['calls']} uses, {round(100 * (1 - record['failure_rate']))}% worked, {verdict}"


def tools_answer(runtime: Any) -> str:
    """Every tool, grouped by what it is for, with how much each area has been used."""
    registry = runtime.registry
    names = sorted(registry.names())
    grouped, unplaced = capabilities.classify(names)
    used = {record["tool"]: record for record in _records(runtime)}
    lines = [f"I have {len(names)} tools, grouped by what they are for:"]
    for capability in capabilities.CATALOG:
        tools = grouped.get(capability.key, [])
        if not tools:
            continue
        calls = sum(used[name]["calls"] for name in tools if name in used)
        tried = f", used {calls} times so far" if calls else ""
        lines.append(f"{capability.icon} {capability.title} ({len(tools)}{tried}): {', '.join(tools)}")
    if unplaced:
        lines.append("Other: " + ", ".join(unplaced))
    lines.append("")
    lines.append("Ask \"explain <tool name>\" for one tool, \"which tool would you use to ...?\" for a task, or "
                 "\"how reliable are your tools?\" for their track record.")
    return "\n".join(lines)


def _records(runtime: Any) -> list[dict[str, Any]]:
    understanding = getattr(runtime, "tool_understanding", None) or getattr(runtime.registry, "understanding", None)
    if understanding is None or not hasattr(understanding, "totals"):
        return []
    try:
        return list(understanding.totals())
    except Exception:
        return []


def reliability_answer(runtime: Any) -> str:
    records = _records(runtime)
    total = len(runtime.registry.names())
    if not records:
        return (f"I have {total} tools but no record of using any yet, so I can't tell you how reliable they are. "
                "I'll know after using them; failures are recorded with the reason.")
    proven = [r for r in records if r.get("enough_evidence")]
    weak = [r for r in proven if r["failure_rate"] >= 0.34]
    lines = [f"I have used {len(records)} of my {total} tools. Busiest first:"]
    lines.extend(f"  {_record_line(record)}" for record in records[:12])
    if weak:
        lines.append("Unreliable so far: " + ", ".join(r["tool"] for r in weak) + ". I'll say so before relying on them.")
    else:
        lines.append("None of the tools used often enough to judge is unreliable.")
    return "\n".join(lines)


def _plain_permission(registry: Any, name: str) -> str:
    from app.safety.authority import CATEGORY_TIERS, OWNER_REQUESTABLE_CATEGORIES, RiskTier

    try:
        category = registry.get(name).permission
    except Exception:
        return ""
    tier = CATEGORY_TIERS.get(category)
    if tier is None:
        return ""
    if tier is RiskTier.PROHIBITED:
        return "It is refused outright."
    if tier is RiskTier.HIGH_CONSEQUENCE:
        if category in OWNER_REQUESTABLE_CATEGORIES:
            return "It acts outside this computer, so it runs only when you ask for it in your own words."
        return "It acts outside this computer or on Windows itself, so it asks for your approval every time."
    if tier is RiskTier.EXTENDED:
        return "It has wider reach, so it needs your OK unless you have allowed it for this kind of work."
    if tier is RiskTier.ORDINARY:
        return "It is ordinary, reversible work on this computer; it runs when you ask for it."
    return "It only reads; it changes nothing."


def explain_tool_answer(runtime: Any, name: str) -> str | None:
    registry = runtime.registry
    if name not in registry.names():
        return None
    spec = registry.get(name)
    area = next((capability for capability in capabilities.CATALOG
                 if name in capabilities.tools_in(capability, [name])), None)
    lines = [f"{name}: {' '.join(str(spec.description or '').split())}"]
    if area:
        lines.append(f"Area: {area.icon} {area.title}.")
    if name.endswith("_prepare"):
        lines.append("It only prepares, on this computer. Nothing is sent, published or submitted until you confirm "
                     "the action in the confirmation window or by saying \"confirm action N\" here.")
    else:
        permission = _plain_permission(registry, name)
        if permission:
            lines.append(permission)
    record = next((r for r in _records(runtime) if r["tool"] == name), None)
    lines.append("Track record: " + (_record_line(record) if record else "not used yet."))
    properties = sorted(((spec.input_schema or {}).get("properties") or {}))
    if properties:
        lines.append("It takes: " + ", ".join(properties) + ".")
    return "\n".join(lines)


def tool_for_answer(runtime: Any, task: str) -> str:
    """Which tool HAYAT would use for a task, from the registry and the catalog, best first."""
    from app.tools.introspection import _tools_for_ability

    registry = runtime.registry
    names = sorted(registry.names())
    ranked = list(_tools_for_ability(registry, names, task))
    for capability in capabilities.match(task, limit=2):
        words = {word.rstrip("s") for word in re.findall(r"[a-z]{3,}", task.lower())}
        area = sorted(capabilities.tools_in(capability, names),
                      key=lambda tool: (-sum(word in tool for word in words), tool))
        ranked += [tool for tool in area if tool not in ranked]
    if not ranked:
        return (f"I don't have a tool that matches \"{task}\". I'd answer it with my AI model, or you can ask "
                "\"what can you do?\" to see every area.")
    best = ranked[0]
    lines = [f"For that I'd use {best}: {' '.join(str(registry.get(best).description or '').split())}"]
    permission = _plain_permission(registry, best)
    if permission:
        lines.append(permission)
    if len(ranked) > 1:
        lines.append("Related: " + ", ".join(ranked[1:6]) + ".")
    lines.append(f"Just ask in your own words, e.g. \"{task.strip().rstrip('?')}\", and I'll plan the steps.")
    return "\n".join(lines)


# ------------------------------------------------------------------ recognising the questions
LIMITS_QUESTION = re.compile(
    r"(?i)^\s*(?:what\s+(?:can'?t|cannot|can\s+not|won'?t|will\s+not)\s+(?:you|u)\s+do|what\s+are\s+(?:your|ur)\s+"
    r"(?:limits|limitations|boundaries|restrictions|rules)|what\s+are\s+you\s+not\s+(?:allowed|able)\s+to\s+"
    r"do|what\s+(?:do|don'?t)\s+you\s+refuse(?:\s+to\s+do)?|is\s+there\s+anything\s+you\s+(?:can'?t|cannot|won'?t)\s+do)"
    r"[\s?.!]*$")
HOW_QUESTION = re.compile(
    r"(?i)^\s*(?:how\s+do\s+(?:you|u)\s+(?:work|think|decide|function|operate)|how\s+does\s+hayat\s+work|"
    r"explain\s+how\s+(?:you|u|hayat)\s+works?|how\s+are\s+you\s+(?:built|made|working)|what\s+happens\s+when\s+i\s+"
    r"(?:ask|tell|send|give)\s+(?:you\s+)?(?:something|a\s+request|a\s+task|a\s+question)|how\s+do\s+you\s+handle\s+"
    r"(?:a\s+|my\s+)?(?:request|task|question)s?)[\s?.!]*$")
TOOLS_QUESTION = re.compile(
    r"(?i)^\s*(?:(?:what|which)\s+tools\s+do\s+(?:you|u)\s+have|what\s+are\s+(?:your|ur)\s+tools|(?:list|show)\s+"
    r"(?:me\s+)?(?:all\s+)?(?:your|the|of\s+your)\s+tools|what\s+tools\s+can\s+you\s+use|all\s+your\s+tools)[\s?.!]*$")
RELIABILITY_QUESTION = re.compile(
    r"(?i)^\s*(?:how\s+reliable\s+(?:are|is)\s+(?:you|your\s+tools|hayat)|which\s+(?:of\s+your\s+)?tools\s+"
    r"(?:fail|are\s+unreliable|work\s+best)|(?:show\s+)?(?:your\s+)?tool\s+(?:reliability|track\s+record))[\s?.!]*$")
TOOL_FOR_QUESTION = re.compile(
    r"(?i)^\s*(?:which|what)\s+tools?\s+(?:would|do|will|should|can|could)\s+(?:you|u)\s+use\s+(?:to|for)\s+(.+?)"
    r"[\s?.!]*$")
EXPLAIN_TOOL_QUESTION = re.compile(
    r"(?i)^\s*(?:explain|describe|what\s+does|what\s+is)\s+(?:the\s+|your\s+)?`?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`?"
    r"(?:\s+tool)?(?:\s+do)?[\s?.!]*$")


def recognise(text: str) -> tuple[str, dict[str, str]] | None:
    """Which self-knowledge question this is, if any: (control, entities)."""
    value = " ".join((text or "").split())
    if re.fullmatch(r"(?i)(?:show (?:me )?your (?:self[- ]view|self[- ]awareness|operational awareness)|"
                    r"what do you know about yourself|how do you know what you (?:did|have done)|"
                    r"what have you actually verified)[?.!]*", value):
        return 'SELF_AWARENESS', {}
    if re.fullmatch(r"(?i)(?:are you (?:conscious|sentient|self[- ]aware)|do you have (?:your own )?"
                    r"(?:consciousness|subjective experience)|do you experience anything)[?.!]*", value):
        return 'SELF_EXPERIENCE', {}
    if LIMITS_QUESTION.match(value):
        return "SELF_LIMITS", {}
    if HOW_QUESTION.match(value):
        return "SELF_HOW", {}
    if TOOLS_QUESTION.match(value):
        return "SELF_TOOLS", {}
    if RELIABILITY_QUESTION.match(value):
        return "SELF_RELIABILITY", {}
    found = TOOL_FOR_QUESTION.match(value)
    if found:
        return "SELF_TOOL_FOR", {"task": found.group(1)}
    found = EXPLAIN_TOOL_QUESTION.match(value)
    if found:
        return "SELF_EXPLAIN_TOOL", {"tool": found.group(1).lower()}
    return None


def answer(control: str, entities: dict[str, str], runtime: Any) -> str | None:
    if control == 'SELF_AWARENESS':
        from app.self_view import describe_self
        return describe_self(runtime.state.snapshot())
    if control == 'SELF_EXPERIENCE':
        return ("Whether AI can have subjective experience is debated; my software status and 3D brain view "
                "do not establish that. I can track goals, context, tool outcomes and uncertainty, and show "
                "what my records support. Ask 'show your self-view' to inspect that operational evidence.")
    if control == "SELF_LIMITS":
        return limits_answer(runtime)
    if control == "SELF_HOW":
        return how_i_work_answer()
    if control == "SELF_TOOLS":
        return tools_answer(runtime)
    if control == "SELF_RELIABILITY":
        return reliability_answer(runtime)
    if control == "SELF_TOOL_FOR":
        return tool_for_answer(runtime, entities.get("task", ""))
    if control == "SELF_EXPLAIN_TOOL":
        return explain_tool_answer(runtime, entities.get("tool", ""))
    return None


def register_self_knowledge_tools(registry: Any, runtime_supplier: Any) -> None:
    """Let HAYAT read the facts about itself while planning, not only when asked directly."""

    def about_hayat(topic: str = "limits", task: str = "", tool: str = "") -> dict[str, Any]:
        runtime = runtime_supplier()
        topic = (topic or "limits").strip().lower()
        if topic == "how":
            text = how_i_work_answer()
        elif topic == "tools":
            text = tools_answer(runtime)
        elif topic == "reliability":
            text = reliability_answer(runtime)
        elif topic == "tool_for":
            text = tool_for_answer(runtime, task or "")
        elif topic == "tool":
            text = explain_tool_answer(runtime, tool or "") or f"I have no tool called {tool!r}."
        elif topic == 'awareness':
            from app.self_view import describe_self
            text = describe_self(runtime.state.snapshot())
        else:
            text = limits_answer(runtime)
        return {"topic": topic, "text": text, "needs_attention": [], "source": "registry, catalog and authority rules"}

    registry.register(
        "about_hayat",
        "Read facts about this assistant instead of guessing: topic 'limits' (what it refuses or always asks about, "
        "and why), 'how' (the steps a request goes through), 'tools' (every tool grouped by area), 'reliability' "
        "(recorded tool outcomes), 'tool_for' with task (which tool fits a task), or 'tool' with tool (one tool "
        "explained, including whether it asks first), or 'awareness' (observed state and task verification).",
        {"type": "object", "properties": {
            "topic": {"enum": ["limits", "how", "tools", "reliability", "tool_for", "tool", "awareness"]},
            "task": {"type": "string"}, "tool": {"type": "string"}}, "required": [], "additionalProperties": False},
        "low", "system_read", about_hayat)
