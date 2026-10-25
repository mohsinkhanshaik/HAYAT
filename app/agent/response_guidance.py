"""Mode-specific answer guidance without confusing depth with verbosity."""
from __future__ import annotations

import re

from app.context_budget import ContextBudget
from app.response_constraints import binary_only, word_constraint, word_count


def word_limit(text: str) -> int | None:
    """The word count the user set ("answer in under 10 words"), or None."""
    constraint = word_constraint(text)
    return constraint.maximum if constraint is not None else None


def brief_guidance(text: str) -> str:
    import re

    from app.answers import brief_output_requested, brief_output_unit
    if not brief_output_requested(text):
        return ""
    unit = brief_output_unit(text)
    if unit == 'word':
        return 'The user asked for exactly one word. Reply with one word only: no heading, emoji or explanation.'
    limit = word_limit(text)
    if limit is not None:
        exact = word_constraint(text).exact
        return (f"The user set a hard limit: {'exactly' if exact else 'at most'} {limit} words. Count them. "
                "Keep the actual answer, including its defining name or value; do not replace it with a generic "
                "restatement of the question. Contractions count as one word; use uncontracted wording. "
                "No heading, emoji or sign-off. Give only the answer.")
    if re.search(r"(?i)\byes or no\b|\byes/no\b", text):
        if binary_only(text):
            return 'Start the answer with Yes or No and return only that word when the answer is established; do not add an explanation.'
        return "Start the answer with Yes or No, then give at most one short sentence of reason."
    if unit:
        return (f"The user asked for exactly one {unit}. Reply with one {unit} only: "
                "no heading, no second sentence restating it, no follow-up offer.")
    return ("The current user explicitly requests a short answer. Honor any stricter answer-only or exact-format "
            "request; otherwise return the complete answer and its decisive reason in one to three compact sentences, "
            "under 80 words. Preserve key qualifications and any requested "
            "short working. Omit analogies, extra examples and repeated conclusions unless the user requests them. "
            "Careful reasoning still applies; this constraint concerns the displayed answer.")


def inherited_brief_request(text: str, history: list[dict], *, followup: bool) -> str:
    """Retain explicit response length for a continuation, stopping at a new subject."""
    from app.answers import brief_output_requested, brief_output_unit
    if not followup or brief_output_requested(text) or re.search(
            r'(?i)\b(?:in detail|in depth|longer|expand|detailed|more detail|fully)\b', text):
        return text
    # The supplied history includes this turn. Only the owner's words carry constraints.
    from app.agent.conversation import refers_to_previous_reply
    for row in reversed(history[:-1]):
        if row.get('role') != 'user':
            continue
        previous = str(row.get('content') or '')
        if re.search(r'(?i)\b(?:in detail|in depth|longer|expand|detailed|more detail|fully)\b', previous):
            break
        if brief_output_requested(previous):
            unit = brief_output_unit(previous)
            constraint = word_constraint(previous)
            if constraint is not None:
                instruction = f"Answer in {'exactly' if constraint.exact else 'at most'} {constraint.maximum} words."
            elif unit:
                instruction = f'Reply in one {unit}.'
            else:
                instruction = 'Give a short answer.'
            return text + '\n' + instruction
        if not refers_to_previous_reply(previous):
            break
    return text


def exceeds_brief_request(answer: str, question: str) -> bool:
    """Check compact output constraints without cutting a finished answer."""
    from app.answers import brief_output_unit
    constraint = word_constraint(question)
    if constraint is not None:
        count = word_count(answer)
        if count > constraint.maximum or constraint.exact and count != constraint.maximum:
            return True
    elif len(answer.split()) > (25 if re.search(r'(?i)\byes or no\b|\byes/no\b', question) else 100):
        return True
    unit = brief_output_unit(question)
    if unit == 'word':
        return len(answer.split()) > 1
    if unit == 'line':
        return len([line for line in answer.splitlines() if line.strip()]) > 1
    if unit == 'sentence':
        # Ignore decimal points and common abbreviations when finding sentence endings.
        prose = re.sub(r'(?i)\b(?:e\.g\.|i\.e\.|Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.|vs\.|etc\.)', 'abbr', answer)
        prose = re.sub(r'\b(?:[A-Z]\.){2,}', 'abbr', prose)
        prose = re.sub(r'(?<=\d)\.(?=\d)', '', prose)
        return len([part for part in re.split(r'[.!?]+[\"\u201d\u2019\x27)]*\s+', prose.strip()) if re.search(r'\w', part)]) > 1
    return False


def guidance(text: str, modes: set[str]) -> str:
    social = ContextBudget.classify(text) == "greeting"
    parts = [
        "Understand the user's intended outcome and constraints from the current turn and recent conversation. "
        "A correction replaces only the detail it contradicts; an update that adds something ('also', 'too', "
        "'as well', 'is coming too') adds to the earlier facts, so keep every earlier fact it does not change "
        "and recount from all of them. When the conversation already states the facts a question needs "
        "(who is coming, what was decided), answer from them directly instead of listing 'it depends' cases. "
        "Use the names, values and constraints the user gave (a variable called s stays s). "
        "Ask one focused question only when a missing detail "
        "changes the answer materially; otherwise make a reasonable assumption and state it briefly if needed. "
        "Match the emotional tone without guessing feelings or inventing personal experiences. "
        "Avoid repeating acknowledgments, generic offers to help or questions the user has already answered. "
        "Only claim a search, source check, file change or test when a recorded tool result establishes it. "
        "A prior assistant answer and model advice are not independent evidence. Do not invent citations, "
        "confidence scores, prices, listings, earnings, market multiples or failure causes. Label assumptions "
        "and hypothetical examples explicitly. If current external evidence is missing, explain that gap; "
        "do not present an estimate as researched fact. "
    ]
    from app.code_completion import code_task
    if code_task(text) and re.search(r"(?i)\b(?:one[- ]liner|single[- ]line|one[- ]line)\b", text):
        parts.append("Return one code expression or statement over the supplied input; do not replace that input with a sample value or add a print demonstration. ")
    if not social and modes & {"DEEP", "ADVERSARIAL", "TEACH", "MENTOR"}:
        parts.append(
            "Work through the problem carefully, check constraints, units and arithmetic, and distinguish "
            "observed evidence from inference. Present the answer with the concise rationale needed to assess it. "
            "Reasoning depth does not require a long reply: respect the requested length. "
        )
    if not social and "ADVERSARIAL" in modes:
        parts.append(
            "Before finalizing, test the strongest plausible alternative, look for a counterexample and "
            "check whether the recommendation still holds under the weakest assumption. Correct any contradiction. "
            "Mention the decisive tradeoff or unresolved uncertainty when it affects the conclusion; never invent "
            "a second opinion, test result or numerical confidence. "
        )
    if "VOICE" in modes:
        parts.append(
            "This is a spoken conversation. Lead with the useful answer in natural, speakable sentences. "
            "Use short paragraphs, avoid dense tables and raw URLs, and expand when the user requests depth. "
        )
    return "".join(parts)
