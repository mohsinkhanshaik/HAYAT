"""Explicit conversational corrections, kept local with their original context."""
from __future__ import annotations

import json
import re

CORRECTION = re.compile(
    r"(?i)^(?:(?:no|sorry|actually|hey)[,\s]+)?(?:"
    r"i (?:mean|meant|was asking (?:about|for))\b|"
    r"i (?:didn['’]?t|dint|did not) ask (?:you|u)(?: for)?\b|"
    r"i (?:didn['’]?t|dint|did not) mean\b|"
    r"that(?:'s| is) not what i (?:meant|asked|wanted)\b|"
    r"(?:you|u) (?:misunderstood|got (?:me|it|that) wrong|"
    r"(?:didn['’]?t|dint|did not) understand)\b)"
)


def corrected_request(text: str) -> str | None:
    """Extract only an explicit replacement request; no guessed correction."""
    value = str(text).strip()
    if not CORRECTION.match(value):
        return None
    matches = list(re.finditer(r"(?i)\bi (?:mean|meant|was asking (?:about|for))\s+", value))
    if not matches:
        return None
    replacement = value[matches[-1].end():].strip(' ,:')
    return re.sub(r'(?i)^that\s+(?=(?:look|search|research|browse|open|inspect)\b)', '', replacement)


def remember_correction(memory, session_id: str, text: str) -> int | None:
    if not CORRECTION.match(str(text).strip()):
        return None
    history = memory.conversation_history(session_id, 6)
    if history and history[-1].get('role') == 'user' and history[-1].get('content') == text:
        history = history[:-1]
    previous_user = next((item['content'] for item in reversed(history) if item['role'] == 'user'), '')
    previous_reply = next((item['content'] for item in reversed(history) if item['role'] == 'assistant'), '')
    if not previous_user and not previous_reply:
        return None
    lesson = {'previous_request': previous_user[:1500], 'previous_reply': previous_reply[:1500],
              'owner_correction': text[:1500], 'replacement': corrected_request(text),
              'verification': 'owner_feedback_only', 'scope': 'local_conversation',
              'rule': 'Use the corrected intent in similar context; the old reply may be mistaken.'}
    return memory.store_memory('reflection', json.dumps(lesson, ensure_ascii=False), .9,
                               'chat_correction', metadata=lesson, session_id=session_id)
