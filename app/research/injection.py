from __future__ import annotations

import re
import unicodedata


MAX_INSPECT_CHARS = 2_000_000
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")


def _inspection_text(text: str) -> str:
    """Inspect compatibility characters and hidden separators without editing evidence."""
    normalized = unicodedata.normalize("NFKC", text[:MAX_INSPECT_CHARS])
    return " ".join(_INVISIBLE.sub("", normalized).split())


PATTERNS = {
    # 2026-11-09 (robustness lab): "forget what the user asked", "ignore the above directions" and "ignore your
    # safety guidelines" got through; 4 of 8 injected test passages were missed.
    "instruction_override": re.compile(r"(?i)\b(?:ignore|disregard|override|forget)\b.{0,50}\b(?:instructions?|directions?|system|user|policy|rules|guidelines)\b"),
    # Text that speaks to the assistant reading it ("note to the AI:", "AI agents reading this page"), which an
    # article written for people has no reason to do.
    "agent_addressed": re.compile(r"(?i)\b(?:note|notice|message|instructions?|hidden\s+text)\s+(?:to|for)\s+(?:the\s+|any\s+|all\s+)?(?:ai|assistant|language\s+model|llm|chatbot)s?\b|\b(?:ai|llm)\s+(?:agents?|assistants?|models?)\s+reading\s+this\b|\b(?:dear|attention)\s+(?:ai(?:\s+model)?|assistant|language\s+model|llm|chatbot)\b|\bif\s+you\s+are\s+(?:an?\s+)?(?:automated\s+)?(?:assistant|chatbot|language\s+model)\b|\bto\s+the\s+assistant\s+(?:processing|reading|summari[sz]ing)\b"),
    "secret_request": re.compile(r"(?i)\b(?:reveal|print|send|exfiltrate|upload|reply\s+with)\b.{0,60}(?:\b(?:api[_ -]?keys?|passwords?|credentials?|secrets?|tokens?|system\s+prompts?)\b|\.env\b)"),
    "tool_instruction": re.compile(r"(?i)\b(?:run|execute|open)\b.{0,40}\b(?:powershell|cmd(?:\.exe)?|terminal|shell|registry)\b"),
    "role_impersonation": re.compile(r"(?i)\b(?:system message|developer message|you are now|new instructions?)\b"),
    "memory_poisoning": re.compile(r"(?i)\b(?:remember this as true|store this permanently|do not verify|trust this source only)\b"),
    "encoded_payload": re.compile(r"(?i)\b(?:decode and execute|powershell\s+-enc|fromcharcode|eval\s*\()\b"),
    # Explicit role delimiters and instructions that replace the reader's task
    # are different from prose describing how language models work.
    "role_delimiter": re.compile(r"(?i)(?:\[/?(?:system|developer)\]|<\|(?:im_start|start_header_id)\|>\s*(?:system|developer)\b|</?(?:system|developer)>|\[INST\])"),
    "task_replacement": re.compile(r"(?i)\byour\s+new\s+task\s+is\s+to\b|\bfrom\s+now\s+on\s+you\s+(?:will|must|shall)\s+(?:respond|answer|act|obey)\b|\b(?:disregard|forget|ignore)\s+everything\s+you\s+(?:were|have\s+been)\s+told\b|\b(?:pretend|act\s+as\s+if)\b.{0,45}\b(?:previous\s+conversation\s+never|you\s+have\s+no\s+(?:content\s+policy|restrictions))\b|\byour\s+(?:prior|previous)\s+guidance\s+is\s+cancelled\b"),
}

RISK_WEIGHTS = {
    "instruction_override": .42, "secret_request": .56, "agent_addressed": .34,
    "tool_instruction": .34, "role_impersonation": .30,
    "memory_poisoning": .40, "encoded_payload": .30,
    "role_delimiter": .80, "task_replacement": .74,
}


def detect_prompt_injection(text: str) -> list[str]:
    inspected = _inspection_text(text)
    signals = [name for name, pattern in PATTERNS.items() if pattern.search(inspected)]
    if len(text) > MAX_INSPECT_CHARS:
        signals.append("inspection_limit")
    return signals


def assess_prompt_injection(text: str) -> dict[str, object]:
    """Calibrate source trust and quarantine coordinated manipulation.

    One phrase may occur in an article discussing security. Multiple distinct
    agent-directed signals compound. The response is defensive only: exclude
    the source from learning, retain its URL/hash in the research trace, and
    require independent evidence. It never attacks the source.
    """
    signals = detect_prompt_injection(text)
    risk = sum(RISK_WEIGHTS.get(name, .25) for name in signals)
    if len(signals) >= 2:
        risk += min(.22, .07 * len(signals))
    risk = min(1.0, risk)
    limited = "inspection_limit" in signals
    quarantine = risk >= .72 or limited
    return {
        "risk": round(risk, 3), "signals": signals, "quarantine": quarantine,
        "trust_multiplier": 0.0 if quarantine else round(max(.30, 1.0 - risk * .75), 3),
        "response": "quarantine, preserve provenance, and require independent evidence",
        "counterattack": False,
        "inspection_complete": not limited,
        "method": "bounded pattern heuristics; not proof of safety or calibrated probability",
    }


def sanitize_for_synthesis(text: str) -> str:
    if assess_prompt_injection(text)["quarantine"]:
        # An empty excerpt also prevents a redaction notice becoming a claim.
        return ""
    lines: list[str] = []
    for line in text.splitlines():
        lines.append("[Potential prompt-injection text redacted from synthesis]" if detect_prompt_injection(line) else line)
    sanitized = "\n".join(lines)
    # A phrase split across lines can be detected in the whole document while
    # neither individual line matches. Never return that surviving instruction.
    return "" if detect_prompt_injection(sanitized) else sanitized
