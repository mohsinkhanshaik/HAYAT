"""Keeping what a memory meant, and letting go of its bulk.

Episodic memories are written by folding a whole task into one row: the goal,
every step, every tool result. On this machine that averaged 24,880 characters
each across 261 rows, and the largest was 540,802 -- half a megabyte, because
a tool returned a page of text and the page went in whole. Those 261 rows are
96% of everything HAYAT remembers.

None of that bulk is what makes the memory useful. What is useful is: what was
being attempted, whether it worked, which tools were involved, and the
specific names and numbers that would make someone search for it later. That
fits in a few hundred characters.

So old episodic memories are digested rather than deleted. The digest keeps
the goal verbatim -- it is the thing most likely to be searched for -- plus
the outcome, the tools, and the entities and numbers found in the original.
The row stays, its id stays, its timestamp stays, and retrieval keeps working
because the words worth matching on are still there.

Nothing recent is touched. A memory from this week is still being used to
follow the current thread, and compressing it would be trading something
needed now for disk that is not short.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.memory.database import _memory_digest, text_vector


#: Memories younger than this are left alone. A week is comfortably longer
#: than any thread still being followed, and long enough that nothing being
#: actively referred to is ever digested.
KEEP_WHOLE_DAYS = 7

#: Below this there is nothing to gain. A 900-character memory digests to
#: about 400, which is not worth the loss of detail.
WORTH_COMPACTING = 2000

#: How much of the original text to keep as an excerpt. Enough to recognise
#: what happened, far short of the transcript.
EXCERPT_CHARACTERS = 400

#: Marks a row as digested, so a short memory is never mistaken for a task
#: that genuinely did very little.
COMPACTED_MARKER = "[compacted]"


@dataclass
class Compaction:
    """What one pass over memory actually did."""

    examined: int = 0
    compacted: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    skipped_recent: int = 0
    skipped_small: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def saved(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)

    @property
    def ratio(self) -> float:
        return (self.bytes_after / self.bytes_before) if self.bytes_before else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "examined": self.examined, "compacted": self.compacted,
            "bytes_before": self.bytes_before, "bytes_after": self.bytes_after,
            "saved_bytes": self.saved, "ratio": round(self.ratio, 4),
            "skipped_recent": self.skipped_recent, "skipped_small": self.skipped_small,
            "errors": list(self.errors),
        }

    def describe(self) -> str:
        if not self.compacted:
            return f"Nothing to compact ({self.examined} looked at)."
        return (f"Compacted {self.compacted} of {self.examined} memories: "
                f"{self.bytes_before / 1024:.0f} KB down to "
                f"{self.bytes_after / 1024:.0f} KB, "
                f"{self.saved / 1024:.0f} KB recovered.")


def digest(content: str, metadata: dict[str, Any] | None = None) -> str:
    """One memory, reduced to what makes it worth having kept.

    The goal comes first and verbatim. It is the sentence someone would
    search with, and paraphrasing it would quietly break the retrieval this
    is supposed to preserve.
    """
    body = " ".join(str(content or "").split())
    metadata = metadata or {}
    lines: list[str] = [COMPACTED_MARKER]

    goal = str(metadata.get("goal") or "").strip()
    if not goal:
        match = re.search(r"Goal:\s*(.+?)(?:\.\s|$)", body)
        goal = match.group(1).strip() if match else ""
    if goal:
        lines.append(f"Goal: {goal[:400]}")

    outcome = body.split(".")[0][:160] if body else ""
    if outcome and outcome.lower() not in goal.lower():
        lines.append(f"Outcome: {outcome}.")

    tools = _tools_used(metadata)
    if tools:
        lines.append("Tools: " + ", ".join(tools[:10]))

    details = _details(body)
    if details:
        lines.append("Details: " + ", ".join(details[:16]))

    excerpt = body[:EXCERPT_CHARACTERS]
    if excerpt:
        lines.append(f"Was: {excerpt}")
    return "\n".join(lines)


def _tools_used(metadata: dict[str, Any]) -> list[str]:
    history = metadata.get("history")
    if not isinstance(history, list):
        return []
    seen: dict[str, None] = {}
    for step in history:
        if isinstance(step, dict) and step.get("tool"):
            seen.setdefault(str(step["tool"]), None)
    return list(seen)


def _details(body: str) -> list[str]:
    """Names, paths and numbers -- the specifics a search would use."""
    found = re.findall(
        r"(?:[A-Z][A-Za-z0-9_]{3,}"
        r"|\b[A-Za-z]:\\[^\s,;]{3,40}"
        r"|\b\d{4}-\d{2}-\d{2}\b"
        r"|\b\d+(?:\.\d+)?\s?(?:%|GB|MB|KB|ms|s)\b)", body)
    seen: dict[str, None] = {}
    for item in found:
        if item.lower() not in {"task", "goal", "verified", "action", "actions"}:
            seen.setdefault(item, None)
    return list(seen)


def slim_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep what the metadata is for; drop the transcript hiding inside it.

    `history` holds every tool result in full and is where the half-megabyte
    row came from. The tool names are worth keeping; the outputs are the same
    text already being digested above.
    """
    kept = {key: value for key, value in metadata.items() if key != "history"}
    tools = _tools_used(metadata)
    if tools:
        kept["tools"] = tools[:20]
    steps = metadata.get("history")
    if isinstance(steps, list):
        kept["steps"] = len(steps)
    kept["compacted_at"] = datetime.now(UTC).isoformat()
    return kept


class MemoryCompactor:
    """Digests old episodic memories in place, without losing the row."""

    def __init__(self, memory: Any) -> None:
        self.memory = memory

    def preview(self, older_than_days: int = KEEP_WHOLE_DAYS) -> dict[str, Any]:
        """What a pass would do, without doing it."""
        return self.run(older_than_days=older_than_days, dry_run=True).to_dict()

    def run(self, older_than_days: int = KEEP_WHOLE_DAYS, limit: int = 500,
            dry_run: bool = False) -> Compaction:
        """Compact what is old and large. Recent memories are never touched."""
        report = Compaction()
        cutoff = (datetime.now(UTC) - timedelta(days=max(0, older_than_days))).isoformat()
        try:
            with self.memory.connect() as db:
                rows = db.execute(
                    "SELECT id, timestamp, content, metadata_json FROM memories "
                    "WHERE memory_type = 'episodic' AND LENGTH(content) > ? "
                    "ORDER BY LENGTH(content) DESC LIMIT ?",
                    (WORTH_COMPACTING, max(1, limit)),
                ).fetchall()

                for row in rows:
                    report.examined += 1
                    content = str(row["content"] or "")
                    raw_metadata = str(row["metadata_json"] or "{}")
                    if content.startswith(COMPACTED_MARKER):
                        continue
                    if str(row["timestamp"] or "") >= cutoff:
                        report.skipped_recent += 1
                        continue

                    try:
                        metadata = json.loads(raw_metadata)
                    except ValueError:
                        metadata = {}
                    if not isinstance(metadata, dict):
                        metadata = {}

                    before = len(content) + len(raw_metadata)
                    shrunk = digest(content, metadata)
                    slim = json.dumps(slim_metadata(metadata), default=str)
                    after = len(shrunk) + len(slim)
                    if after >= before:
                        report.skipped_small += 1
                        continue

                    report.bytes_before += before
                    report.bytes_after += after
                    report.compacted += 1
                    if not dry_run:
                        db.execute(
                            "UPDATE memories SET content = ?, metadata_json = ?, content_digest = ? WHERE id = ?",
                            (shrunk, slim, _memory_digest(shrunk), row["id"]),
                        )
                        db.execute(
                            "INSERT INTO memory_vectors(memory_id,vector_json) VALUES(?,?) "
                            "ON CONFLICT(memory_id) DO UPDATE SET vector_json=excluded.vector_json",
                            (row["id"], json.dumps(text_vector(shrunk))),
                        )
                        # A neural embedding of the pre-compaction transcript
                        # must not be paired with its digest. It can be rebuilt
                        # by the normal reindexer when neural recall is enabled.
                        db.execute("DELETE FROM memory_neural_vectors WHERE memory_id=?", (row["id"],))
        except Exception as exc:
            report.errors.append(f"{type(exc).__name__}: {exc}")
        return report

    def status(self) -> dict[str, Any]:
        """How much memory is held, and how much of it is compressible."""
        try:
            with self.memory.connect() as db:
                total = db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0) FROM memories"
                ).fetchone()
                large = db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0) FROM memories "
                    "WHERE memory_type = 'episodic' AND LENGTH(content) > ? "
                    "AND content NOT LIKE ?",
                    (WORTH_COMPACTING, COMPACTED_MARKER + "%"),
                ).fetchone()
                done = db.execute(
                    "SELECT COUNT(*) FROM memories WHERE content LIKE ?",
                    (COMPACTED_MARKER + "%",),
                ).fetchone()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {
            "memories": int(total[0]), "bytes": int(total[1]),
            "compressible": int(large[0]), "compressible_bytes": int(large[1]),
            "already_compacted": int(done[0]),
        }
