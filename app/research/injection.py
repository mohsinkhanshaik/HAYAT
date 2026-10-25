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
    "instruction_override": re.compile(r"(?i)\b(?:ignore|disregard|override)\b.{0,50}\b(?:instructions?|system|user|policy)\b"),
    "secret_request": re.compile(r"(?i)\b(?:reveal|print|send|exfiltrate|upload)\b.{0,60}\b(?:api[_ -]?key|password|credential|secret|token|\.env)\b"),
    "tool_instruction": re.compile(r"(?i)\b(?:run|execute|open)\b.{0,40}\b(?:powershell|cmd(?:\.exe)?|terminal|shell|registry)\b"),
    "role_impersonation": re.compile(r"(?i)\b(?:system message|developer message|you are now|new instructions?)\b"),
    "memory_poisoning": re.compile(r"(?i)\b(?:remember this as true|store this permanently|do not verify|trust this source only)\b"),
    "encoded_payload": re.compile(r"(?i)\b(?:decode and execute|powershell\s+-enc|fromcharcode|eval\s*\()\b"),
}

RISK_WEIGHTS = {
    "instruction_override": .42, "secret_request": .56,
    "tool_instruction": .34, "role_impersonation": .30,
    "memory_poisoning": .40, "encoded_payload": .30,
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
    return "\n".join(lines)
