"""The owner's verdicts on HAYAT's replies: which were right, which were wrong, and why.

"That's wrong, the capital is Canberra" used to change nothing beyond the next
reply: the mistake and the correction were never consulted again, so the same
question could get the same wrong answer a week later. Each verdict is kept
here with the request and reply it judged. Corrections on a similar question
are put in front of the model, and "what did you get wrong?" is answered from
this record rather than from the model's impression of itself.

Owner feedback is the owner's judgement, not verified evidence; it is labelled
that way wherever it is used.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

# "That's wrong, ..." / "you're not right about ..." / "wrong answer". "It is
# wrong to ..." is an opinion about the world, not about the reply, so "it"
# is not a subject here; a question ("is that wrong?") never matches.
NEGATIVE = re.compile(
    r"(?i)^(?:(?:no|nope|nah|hayat)[,.!\s]+)*(?:"
    r"(?:that|this|your\s+(?:answer|reply|response))(?:'s|’s|\s+is|\s+was)|you(?:'re|’re|\s+are|\s+were)"
    r")\s+(?:(?:completely|totally|all|quite|so|still|just)\s+)?"
    r"(?:wrong|incorrect|not\s+(?:right|correct|true|accurate)|false|mistaken|inaccurate)\b"
    r"|^(?:(?:no|nope)[,.!\s]+)?(?:wrong|incorrect|bad|poor)\s+(?:answer|reply|response)\b"
    r"|^(?:(?:no|nope)[,.!\s]+)?wrong[\s.!]*$"
    r"|^(?:👎|thumbs[\s-]+down\b)"
)
POSITIVE = re.compile(
    r"(?i)^(?:(?:yes|yeah|yep|ok|okay|hayat)[,.!\s]+)*(?:"
    r"(?:that|this|your\s+(?:answer|reply|response))(?:'s|’s|\s+is|\s+was)\s+"
    r"(?:(?:exactly|absolutely|totally|completely|perfectly)\s+)?(?:right|correct|accurate|true|perfect|spot\s+on)"
    r"|you(?:'re|’re|\s+are|\s+were)\s+(?:(?:exactly|absolutely)\s+)?(?:right|correct)"
    r"|(?:very\s+)?(?:good|great|excellent|perfect|correct|right|nice|accurate)\s+(?:answer|reply|response|job|work)"
    r"|well\s+done|exactly\s+right|spot\s+on|👍|thumbs[\s-]+up"
    r")[\s.!,]*(?:(?:thanks|thank\s+you)[\s.!]*)?$"
)
_LEAD_IN = re.compile(
    r"(?i)^[\s,.;:!—–-]*(?:(?:and|because|since|as)\s+)?(?:(?:the\s+)?(?:right|correct|real|actual|true)\s+"
    r"(?:answer|one|fact)\s+is|it(?:'s|’s|\s+is)\s+actually|actually)?[\s,.;:—–-]*"
)
_STOP = frozenset(
    "a an the is are was were be been of in on at to for and or but with by from as it its this that these those "
    "what which who whom whose when where why how do does did can could would should will shall may might must "
    "i me my you your he she they them we us our his her their please tell give show about than then there "
    "into over under also just more most some any all much many very really".split())


def _words(text: str) -> set[str]:
    words = set()
    for word in re.findall(r"[a-z0-9]+", str(text).casefold()):
        if len(word) > 2 and word not in _STOP:
            words.add(word[:-1] if len(word) > 4 and word.endswith("s") else word)
    return words


def verdict(text: str) -> tuple[int, str] | None:
    """(rating, correction) when the message judges the previous reply, else None.

    -1 for "wrong" with whatever correction follows; +1 for a short approval.
    """
    value = " ".join(str(text or "").split())
    if not value or len(value) > 1500 or value.endswith("?"):
        return None
    if POSITIVE.match(value):
        return 1, ""
    found = NEGATIVE.match(value)
    if found:
        return -1, _LEAD_IN.sub("", value[found.end():], count=1).strip()[:1500]
    return None


class OwnerFeedback:
    """A small local record beside the memory database."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    @classmethod
    def beside(cls, memory: Any) -> OwnerFeedback:
        return cls(Path(memory.path).parent / "owner-feedback.db")

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, "
            "session_id TEXT, rating INTEGER NOT NULL, kind TEXT NOT NULL, correction TEXT NOT NULL, "
            "request TEXT NOT NULL, reply TEXT NOT NULL)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(feedback)")}
        for name in ("check_status", "check_note", "checked_at"):
            if name not in columns:
                db.execute(f"ALTER TABLE feedback ADD COLUMN {name} TEXT")
        return db

    def record(self, rating: int, kind: str, correction: str, request: str, reply: str,
               session_id: str | None = None) -> int:
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO feedback (timestamp, session_id, rating, kind, correction, request, reply) "
                "VALUES (?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), session_id, 1 if rating > 0 else -1,
                 kind, correction[:1500], request[:1500], reply[:1500]))
            return int(cursor.lastrowid)

    def rows(self, limit: int = 500) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))]

    def relevant(self, question: str, limit: int = 3) -> list[dict[str, Any]]:
        """Earlier verdicts on a question like this one, best match first.

        Word overlap, not meaning: a correction is only applied where the new
        question shares most of its content words with the judged request.
        """
        asked = _words(question)
        if len(asked) < 2:
            return []
        scored = []
        for row in self.rows():
            judged = _words(row["request"])
            shared = asked & judged
            if len(shared) >= 2 and len(shared) / min(len(asked), len(judged)) >= 0.6:
                scored.append((len(shared) / len(asked | judged), row))
        scored.sort(key=lambda pair: (-pair[0], -pair[1]["id"]))
        return [row for _, row in scored[:limit]]

    def prompt_note(self, question: str) -> str:
        """A system note for the conversation model, or "" when nothing applies."""
        lines = []
        for row in self.relevant(question):
            if row["rating"] < 0:
                line = (f"- On {row['timestamp'][:10]} he asked \"{row['request'][:300]}\" and marked your reply "
                        f"(\"{row['reply'][:300]}\") as {'a misunderstanding' if row['kind'] == 'misunderstood' else 'wrong'}.")
                if row["correction"]:
                    line += f" His correction: \"{row['correction'][:400]}\"."
                if row.get("check_status") in CHECK_WORDS:
                    line += f" Sources checked on {str(row['checked_at'])[:10]}: {CHECK_WORDS[row['check_status']]}."
            else:
                line = (f"- On {row['timestamp'][:10]} he confirmed your reply to \"{row['request'][:300]}\" "
                        f"was right: \"{row['reply'][:300]}\".")
            lines.append(line)
        if not lines:
            return ""
        return ("The owner has judged your answers to similar questions before (his feedback, not verified "
                "evidence):\n" + "\n".join(lines) + "\nDo not repeat a reply he marked wrong. Follow his correction "
                "unless evidence in this conversation contradicts it; if it does, say so plainly and show the evidence.")

    def report(self, limit: int = 8) -> str:
        rows = self.rows()
        if not rows:
            return ("You haven't judged any of my replies yet. Tell me \"that's wrong, <the right answer>\" or "
                    "\"good answer\" after a reply; I keep it, and I use your corrections on similar questions.")
        wrong = [row for row in rows if row["rating"] < 0]
        right = len(rows) - len(wrong)
        lines = [f"From your feedback: {right} repl{'y' if right == 1 else 'ies'} marked right, "
                 f"{len(wrong)} marked wrong or misunderstood ({len(rows)} judged in total)."]
        if wrong:
            lines.append("What I got wrong, newest first:")
            for row in wrong[:limit]:
                line = f"- {row['timestamp'][:10]} · you asked: {row['request'][:160] or '(no earlier request)'}"
                line += f" → fix: {row['correction'][:200]}" if row["correction"] else " → no correction given"
                if row.get("check_status") in CHECK_WORDS:
                    line += f" (sources: {CHECK_WORDS[row['check_status']]})"
                lines.append(line)
            if len(wrong) > limit:
                lines.append(f"…and {len(wrong) - limit} older.")
            lines.append("When you ask something similar, your correction is put in front of me so I don't repeat it.")
        return "\n".join(lines)

    def summary_line(self) -> str:
        """One line for the self-review: the owner's verdicts and how his corrections checked out."""
        rows = self.rows(5000)
        if not rows:
            return ""
        wrong = sum(row["rating"] < 0 for row in rows)
        checked = [row["check_status"] for row in rows if row.get("check_status") in CHECK_WORDS]
        line = (f"Your feedback on my replies: {len(rows) - wrong} marked right, {wrong} marked wrong "
                "(ask \"what did you get wrong?\" for the list).")
        if checked:
            line += (f" Of {len(checked)} corrections checked against sources, {checked.count('agrees')} were "
                     f"supported, {checked.count('disagrees')} sided with my original reply and "
                     f"{checked.count('unclear')} were not settled.")
        return line

    # ------------------------------------------------------------ checking corrections against sources
    def pending_checks(self, limit: int = 1, retry_after: timedelta = timedelta(days=1)) -> list[dict[str, Any]]:
        """Corrections not yet compared with sources, oldest first; a failed try waits a day."""
        cutoff = (datetime.now(timezone.utc) - retry_after).isoformat(timespec="seconds")
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM feedback WHERE rating<0 AND kind='wrong' AND correction<>'' AND request<>'' "
                "AND check_status IS NULL AND (checked_at IS NULL OR checked_at<?) ORDER BY id LIMIT ?",
                (cutoff, max(1, int(limit))))]

    def set_check(self, row_id: int, status: str | None, note: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE feedback SET check_status=?, check_note=?, checked_at=? WHERE id=?",
                       (status, note[:600], datetime.now(timezone.utc).isoformat(timespec="seconds"), row_id))

    def check_corrections(self, research: Any, limit: int = 1) -> list[dict[str, Any]]:
        """Research each pending correction's question and compare the evidence with both answers.

        The owner's correction is his judgement, not proof; this records whether
        public sources agree with it, side with the original reply, or settle
        neither. Private-looking questions are never sent to the web.
        """
        done = []
        for row in self.pending_checks(limit):
            if not _publicly_researchable(row["request"]):
                self.set_check(row["id"], "private", "Not researched: the question looks private.")
                continue
            try:
                result = research.research(row["request"], level="fast")
            except Exception as exc:
                self.set_check(row["id"], None, f"Research failed ({type(exc).__name__}); will retry.")
                continue
            sources = [getattr(source, "final_url", "") for source in (getattr(result, "sources", None) or [])][:3]
            answer = str(getattr(result, "answer", "") or "")
            status = compare_with_evidence(row["request"], row["reply"], row["correction"], answer) if sources else "unclear"
            note = "Sources: " + ", ".join(sources) if sources else "Research found no usable sources."
            self.set_check(row["id"], status, note)
            done.append({"id": row["id"], "status": status, "sources": sources})
        return done


#: How each check result is said to the owner and the model.
CHECK_WORDS = {
    "agrees": "they support his correction",
    "disagrees": "they side with the original reply, not his correction",
    "unclear": "they did not settle it",
}


def compare_with_evidence(request: str, reply: str, correction: str, evidence: str) -> str:
    """'agrees', 'disagrees' or 'unclear', from which answer's own words the evidence contains.

    Only the words that distinguish each answer count (the question's words
    are shared by both). This is a word check, not understanding, so anything
    short of a clear lean is "unclear".
    """
    asked = _words(request)
    fixed = _words(correction) - asked
    original = _words(reply) - asked - fixed
    found = _words(evidence)
    if not fixed or not found:
        return "unclear"
    fixed_hit = len(fixed & found) / len(fixed)
    original_hit = len(original & found) / len(original) if original else 0.0
    if fixed_hit >= 0.6 and fixed_hit >= original_hit:
        return "agrees"
    if original_hit >= 0.6 and fixed_hit < 0.3:
        return "disagrees"
    return "unclear"


def _publicly_researchable(text: str) -> bool:
    """The background learner's own privacy test for text sent to the public web.

    (Paths, secrets, personal questions and people lookups fail it; its separate
    minimum-length rule for queue topics is not a privacy rule and is not used.)
    """
    from app.learning.continuous import public_learning_topic

    return public_learning_topic(" ".join(str(text).split()))


_REDO = re.compile(r"(?i)^(?:please\s+)?(?:check|search|look|try|redo|do|fix|research|verify|read|open|find)\b")


def acknowledgement(text: str) -> str | None:
    """What HAYAT says back to a verdict on a plain reply; None when it is not one, or asks for a redo."""
    found = verdict(text)
    if found is None:
        return None
    rating, correction = found
    if rating > 0:
        return "Thanks, noted that this one was right."
    if correction and _REDO.match(correction):
        return None  # "that's wrong, check again" asks for work, not just a note.
    if correction:
        return (f"Noted: I've marked my last reply as wrong and kept your correction (\"{correction[:300]}\"). "
                "I'll use it when you ask something similar, and when I'm idle I'll check it against public "
                "sources and tell you if they disagree. Ask \"what did you get wrong?\" to see the list.")
    return ("Noted: I've marked my last reply as wrong. Tell me the right answer and I'll keep it for similar "
            "questions, or ask me to check again.")


def record_from_message(memory: Any, session_id: str, text: str, *, misunderstood: bool = False) -> int | None:
    """Store a verdict on the previous reply in this session, if the message gives one."""
    found = verdict(text)
    if found is None and not misunderstood:
        return None
    rating, correction = found if found else (-1, str(text).strip())
    history = memory.conversation_history(session_id, 6)
    if history and history[-1].get("role") == "user" and history[-1].get("content") == text:
        history = history[:-1]
    reply = next((item["content"] for item in reversed(history) if item["role"] == "assistant"), "")
    if not reply:
        return None
    request = next((item["content"] for item in reversed(history) if item["role"] == "user"), "")
    kind = "misunderstood" if misunderstood and found is None else ("right" if rating > 0 else "wrong")
    recorded = OwnerFeedback.beside(memory).record(rating, kind, correction, request, reply, session_id)
    # The self-review of that reply learns how reliable answers like it really are (app/self_review.py).
    if hasattr(memory, "path"):
        try:
            from app.self_review import SelfReviewLog

            SelfReviewLog.beside(memory).judge(session_id, reply, rating)
        except Exception:
            log_swallowed(_log, "attaching the verdict to the reply's self-review")
    # The memories that reply was built from learn from the verdict: a right answer strengthens the links
    # between them, a wrong one weakens what they had learned together (app/memory/plasticity.py).
    last = next((item for item in reversed(history) if item["role"] == "assistant"), {})
    recalled = (last.get("metadata") or {}).get("recalled_memory_ids") if isinstance(last, dict) else None
    if recalled and hasattr(memory, "connect"):
        from app.memory.plasticity import reinforce_quietly

        reinforce_quietly(memory, recalled, rating > 0, reason=f"owner said the reply was {kind}")
    return recorded
