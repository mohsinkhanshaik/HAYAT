from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.memory.database import MemoryDatabase


_REFERENTIAL = re.compile(r"\b(it|its|that|this|there|them|those|the latest|same)\b", re.IGNORECASE)


@dataclass(slots=True)
class VoiceSession:
    memory: MemoryDatabase
    session_id: str

    @classmethod
    def create(cls, memory: MemoryDatabase, session_id: str | None = None) -> "VoiceSession":
        return cls(memory, session_id or f"voice-{uuid.uuid4().hex}")

    def record(self, role: str, content: str, **metadata) -> int:
        return self.memory.record_message(self.session_id, role, content, metadata=metadata)

    def contextual_goal(self, transcript: str) -> str:
        if not _REFERENTIAL.search(transcript):
            return transcript
        history = self.memory.conversation_history(self.session_id, limit=6)
        context = [f"{item['role']}: {item['content'][:500]}" for item in history if item["content"] != transcript]
        if not context:
            return transcript
        return "Current spoken request: " + transcript + "\nPrior voice context for resolving references only:\n" + "\n".join(context[-4:])


class ApprovalPhraseParser:
    """Parses a spoken reply to a pending approval.

    Exact-set membership was too brittle to use by voice: "Approve" was
    accepted but "Approved.", "Approved all of them.", "Yes, approve it." and
    "Go ahead." were all rejected as "not explicit enough", so a user could
    say the same thing four different ways and be refused each time.

    Matching is still deliberately strict rather than sentiment-based: the
    whole utterance must be an affirmative or negative response and nothing
    else. A sentence that merely contains "yes" ("yes, but change the file
    first") returns None so the caller re-asks instead of guessing. Negatives
    are checked first so "no, go ahead" is never read as approval.
    """

    _FILLER = r"(?:please\s+|just\s+|okay\s+|ok\s+|alright\s+|sure\s+|yeah\s+|yep\s+|um\s+|uh\s+)*"
    _TRAILER = r"(?:\s+(?:it|that|this|them|all|all of them|everything|please|now|then))*"

    DENY = re.compile(
        rf"^{_FILLER}(?:no|nope|deny|denied|reject|rejected|cancel|cancelled|stop|abort|"
        rf"don'?t|do not(?:\s+continue)?|never\s?mind|negative|"
        rf"no\s+(?:thanks|thank you)){_TRAILER}$"
    )
    APPROVE = re.compile(
        rf"^{_FILLER}(?:approve[ds]?|confirm(?:ed)?|accept(?:ed)?|authorized?|"
        rf"yes|yeah|yep|yup|affirmative|"
        rf"go\s+ahead|proceed|continue|carry\s+on|do\s+it|"
        rf"(?:yes|yeah|okay|ok|sure),?\s+(?:approve[ds]?|confirm(?:ed)?|continue|proceed|go\s+ahead|do\s+it)"
        rf"){_TRAILER}$"
    )

    @classmethod
    def parse(cls, text: str) -> bool | None:
        normalized = " ".join(text.lower().strip().rstrip(".!?").replace(",", " ").split())
        if cls.DENY.match(normalized):
            return False
        if cls.APPROVE.match(normalized):
            return True
        return None
