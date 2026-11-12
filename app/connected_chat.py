"""Asking ChatGPT, Claude or Gemini from HAYAT's own chat: "ask claude: ...", "ask chatgpt why ...".

The Connected AI desk (app/connected_ai.py, Studios -> Connected AI) holds the owner's API keys, the exact model he
chose for each provider and his consent switch. This lets the main chat use that same connection for one question
when he names the provider. Nothing else changes: ordinary chat stays on HAYAT's own local brain at no cost, a
question goes out only when he asks for that provider by name, PRIVATE / LOCAL_ONLY and Emergency stop block it,
and the reply says which provider and model answered. When the provider is not connected, the reply says how to
connect it instead of pretending.
"""
from __future__ import annotations

import re
from typing import Any

NAMES = {"openai": "ChatGPT (OpenAI)", "anthropic": "Claude (Anthropic)", "gemini": "Gemini (Google)"}
_PROVIDER_WORDS = {"chatgpt": "openai", "chat gpt": "openai", "gpt": "openai", "openai": "openai",
                   "claude": "anthropic", "anthropic": "anthropic", "gemini": "gemini", "google gemini": "gemini"}

ASK = re.compile(
    r"(?i)^\s*(?:(?:please|hayat|jarvis)[,\s]+)*(?:ask|check\s+with|consult|use|let)\s+"
    r"(?P<who>chat\s?gpt|gpt|openai|claude|anthropic|google\s+gemini|gemini)"
    r"(?:\s+(?:to|about|whether|if|for))?\s*[:,-]?\s*(?P<question>.+)$")


def parse(text: str) -> tuple[str, str] | None:
    """(provider, question) when the owner asks a named provider, else None."""
    found = ASK.match(" ".join(str(text or "").split()))
    if not found or len(found["question"].strip()) < 3:
        return None
    provider = _PROVIDER_WORDS[" ".join(found["who"].lower().split())]
    return provider, found["question"].strip()


def manager_for(runtime: Any) -> Any:
    from app.connected_ai import ConnectedAI

    def private() -> bool:
        session_modes = set(getattr(getattr(runtime, "session", None), "effective_modes", ()) or ())
        modes = {getattr(mode, "value", mode) for mode in getattr(getattr(runtime, "modes", None), "effective", ()) or ()}
        return bool({"PRIVATE", "LOCAL_ONLY"} & (session_modes | modes))

    def halted() -> bool:
        return bool(getattr(getattr(getattr(runtime, "registry", None), "interlock", None), "halted", False))

    return ConnectedAI(runtime.memory.path.parent, private=private, halted=halted)


def answer(runtime: Any, provider: str, question: str, *, manager: Any = None) -> str:
    """The provider's answer with its model named, or a plain account of why it could not be asked."""
    from app.connected_ai import ConnectedAIError

    manager = manager or manager_for(runtime)
    name = NAMES[provider]
    try:
        settings = manager.settings()
    except ConnectedAIError as exc:
        return f"I can't reach {name}: {exc}"
    model = str((settings.get("models") or {}).get(provider) or "")
    missing = []
    if not settings.get("enabled"):
        missing.append("turn on external API access")
    if not manager.has_key(provider):
        missing.append(f"save your {name.split(' (')[0]} API key")
    if not model:
        missing.append("choose an exact model")
    if missing:
        return (f"{name} isn't connected yet, so I haven't sent anything. Open Studios → Connected AI and "
                + ", ".join(missing) + ". It uses your own API key, and the provider may bill you for it.")
    try:
        result = manager.ask(provider, model, [{"role": "user", "content": question}])
    except ConnectedAIError as exc:
        return f"{name} could not answer: {exc}"
    note = " (the answer was cut off at the output limit)" if getattr(result, "incomplete", False) else ""
    return (f"{str(result.text).strip()}\n\n— answered by {name}, model {result.returned_model or model}{note}. "
            "This question was sent to them; nothing else from this conversation was.")
