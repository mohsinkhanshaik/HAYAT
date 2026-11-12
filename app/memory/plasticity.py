"""How HAYAT's memory network changes with use: binding, strengthening, fading and consolidation.

The memory store already had an association graph (``memory_links``, walked by
``MemoryDatabase._expand_dna`` after every hybrid recall). On the owner's
machine on 2026-11-08 that graph was nearly static:

* 863 memories, 339 links, every link ``related``, every ``use_count`` 0;
* none of the 404 task memories (episodic) had a single link, because only
  durable types link at write time;
* a task's reflection (what worked, what failed, what to do next time) was not
  connected to the task it reflects on, so recalling the task never brought
  its lesson along;
* nothing ever got stronger for being useful or weaker for being misleading.

This module adds the rules that change the graph. They are borrowed from
how brains are described, but every one is ordinary bookkeeping on rows that
exist; nothing here trains model weights or claims to be biology.

* **Binding** (:func:`bind_episode`): when a task finishes, its reflection is
  linked to its episode (``reflects_on``), and the episode is linked to the
  task that came just before it in the same session (``followed_by``), the way
  one event in a day is remembered as following another.
* **Strengthening** (:func:`reinforce`): memories recalled together for work
  that then succeeded (a completed task, or a reply the owner rated as
  right) get their links strengthened, or a new ``co_recalled`` link if they
  had none. Each memory's own ``successful_uses`` count goes up, which the
  ranker's ``successful_use`` feature already reads. When the work failed or
  the owner said the reply was wrong, their co-recall links weaken and the
  memories' ``failed_uses`` count goes up. "Cells that fire together wire
  together", but only when the firing helped.
* **Fading** (:data:`FADING_RELATIONS`, applied in
  ``MemoryDatabase.associations_many``): associative links (``related``,
  ``followed_by``, ``co_recalled``) lose half their effective weight after
  :data:`HALF_LIFE_DAYS` without being reinforced. Structural links (one
  memory reflects on, supports, contradicts or supersedes another) never fade.
  Nothing is deleted; a faded link comes back to full strength the next time
  it is used.
* **Consolidation** (:func:`consolidate`): a bounded background pass, run once
  after start-up, that binds every past task to its reflection and its
  neighbour, and links task memories with the same purpose. It compares goals,
  not whole records, because the records share boilerplate ("Task succeeded.
  Verified 1 action(s).") that made "look again at me" look like "how
  industries are interlinked". Rare words in the goals count more than common
  ones, with the meaning vector as supporting evidence. A 0.42 threshold was
  chosen by reading pairs from the owner's own memory: above it are pairs like
  "Close notepad" / "Close it" and "see through the camera?" / "See me right
  now with the camera?".

Every function is idempotent, bounded and safe to interrupt. A memory whose
content is the private-networking placeholder, or whose source is untrusted
external content, is never linked automatically.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Any, Iterable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Links that fade unless used. Structural links (reflects_on, supports, contradicts, depends_on, caused_by,
#: part_of, same_entity, supersedes) state a fact about two memories and keep their weight.
FADING_RELATIONS = ("related", "followed_by", "co_recalled")
#: Days after which an unreinforced associative link counts for half its stored weight (hyperbolic, never zero).
HALF_LIFE_DAYS = 180.0
#: How fast a link learns from one co-recall: new = old + RATE * (1 - old) on success, old * (1 - RATE) on failure.
LEARNING_RATE = 0.2
#: Starting weight of a link that co-recall creates.
NEW_LINK_WEIGHT = 0.3
#: At most this many memories from one recall take part in strengthening (6 pairs).
CO_RECALL_LIMIT = 4
#: Episodes this close together in one session are linked as one following the other.
SEQUENCE_WINDOW_SECONDS = 2 * 3600
#: Goal similarity needed for consolidation to link two task memories, and the most links it adds per memory.
GOAL_LINK_THRESHOLD = 0.42
GOAL_LINKS_PER_MEMORY = 3

_STOP = frozenset(
    "the a an and or to of in on for it is be me my i you your this that with at by from as do please can what "
    "how so just now".split()
)


def _now() -> str:
    from app.memory.database import now_iso

    return now_iso()


def _linkable(row: Any) -> bool:
    from app.memory.database import _trusted_for_automatic_links
    from app.networking_privacy import PRIVATE_TEXT

    content = str(row["content"] or "")
    if not content.strip() or content == PRIVATE_TEXT:
        return False
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError):
        metadata = {}
    return _trusted_for_automatic_links(str(row["source"] or ""), metadata)


def _upsert(db: Any, source_id: int, target_id: int, relation: str, weight: float, evidence: dict[str, Any]) -> bool:
    """Insert a link, or refresh an existing one without lowering its weight. True when it is new."""
    if int(source_id) == int(target_id):
        return False
    stamp = _now()
    before = db.total_changes
    db.execute(
        "INSERT OR IGNORE INTO memory_links(source_id,target_id,relation,weight,evidence_json,created_at,"
        "last_reinforced_at,use_count) VALUES(?,?,?,?,?,?,?,0)",
        (int(source_id), int(target_id), relation, max(0.0, min(1.0, weight)), json.dumps(evidence, default=str),
         stamp, stamp),
    )
    return db.total_changes > before


# --------------------------------------------------------------------------- binding


def bind_episode(memory: Any, task_id: str | None) -> dict[str, int]:
    """Link a finished task's reflection to its episode, and the episode to the session's previous one."""
    made = {"reflects_on": 0, "followed_by": 0}
    if not task_id:
        return made
    with memory.connect() as db:
        rows = db.execute(
            "SELECT id,memory_type,content,source,metadata_json,session_id,timestamp FROM memories "
            "WHERE task_id=? AND memory_type IN ('episodic','reflection') ORDER BY id",
            (str(task_id),),
        ).fetchall()
        episodes = [row for row in rows if row["memory_type"] == "episodic" and _linkable(row)]
        if not episodes:
            return made
        episode = episodes[-1]  # the finished record, not the earlier "incomplete" checkpoint
        for row in rows:
            if row["memory_type"] == "reflection" and _linkable(row):
                made["reflects_on"] += _upsert(db, int(row["id"]), int(episode["id"]), "reflects_on", 0.9,
                                               {"method": "same_task", "task_id": str(task_id)})
        made["followed_by"] += _bind_sequence(db, episode)
    return made


def _bind_sequence(db: Any, episode: Any) -> int:
    session = episode["session_id"]
    if not session:
        return 0
    previous = db.execute(
        "SELECT id,content,source,metadata_json,timestamp,task_id FROM memories WHERE memory_type='episodic' "
        "AND session_id=? AND id<? AND COALESCE(task_id,'')<>COALESCE(?,'') ORDER BY id DESC LIMIT 1",
        (session, int(episode["id"]), episode["task_id"] if "task_id" in episode.keys() else None),
    ).fetchone()
    if previous is None or not _linkable(previous):
        return 0
    gap = _seconds_between(previous["timestamp"], episode["timestamp"])
    if gap is None or not 0 <= gap <= SEQUENCE_WINDOW_SECONDS:
        return 0
    return int(_upsert(db, int(previous["id"]), int(episode["id"]), "followed_by", 0.55,
                       {"method": "same_session_sequence", "seconds_apart": round(gap)}))


def _seconds_between(first: str, second: str) -> float | None:
    from datetime import datetime

    try:
        return (datetime.fromisoformat(str(second)) - datetime.fromisoformat(str(first))).total_seconds()
    except (TypeError, ValueError):
        return None


def lesson_for(memory: Any, episode_id: int, *, limit: int = 300) -> str:
    """The recorded lesson of one past task, in a line: what failed and what to do next time ('' when none)."""
    with memory.connect() as db:
        rows = db.execute(
            "SELECT m.content FROM memory_links l JOIN memories m ON m.id=l.source_id WHERE l.target_id=? "
            "AND l.relation='reflects_on' ORDER BY m.id DESC LIMIT 2", (int(episode_id),)).fetchall()
    parts: list[str] = []
    for row in rows:
        try:
            reflection = json.loads(row["content"])
        except (TypeError, ValueError):
            continue
        if not isinstance(reflection, dict):
            continue
        failed = [str(item) for item in reflection.get("what_failed") or [] if item][:2]
        if failed:
            parts.append("Failed before: " + "; ".join(failed))
        worked = [str(item) for item in reflection.get("what_worked") or [] if item][:4]
        if worked and not failed:
            parts.append("Worked before with: " + ", ".join(worked))
        advice = str(reflection.get("next_time") or "").strip()
        if advice and failed:
            parts.append("Next time: " + advice)
    text = " ".join(dict.fromkeys(parts))
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


# --------------------------------------------------------------------------- strengthening


def recalled_memory_ids(items: Iterable[dict[str, Any]]) -> list[int]:
    """The memory-store ids among recalled items, in rank order (index objects from other stores are skipped)."""
    ids: list[int] = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        kind = str(item.get("object_type") or "")
        raw = item.get("source_id") if kind.startswith("memory:") else (item.get("id") if "memory_type" in item and not kind else None)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in ids:
            ids.append(value)
    return ids


def reinforce(memory: Any, memory_ids: Iterable[int], success: bool, *, reason: str = "") -> dict[str, Any]:
    """Learn from one use of recalled memories: strengthen on success, weaken on failure."""
    ids = list(dict.fromkeys(int(item) for item in memory_ids))[:CO_RECALL_LIMIT]
    result = {"memories": 0, "strengthened": 0, "created": 0, "weakened": 0, "success": bool(success)}
    if not ids:
        return result
    stamp = _now()
    placeholders = ",".join("?" for _ in ids)
    with memory.connect() as db:
        rows = {int(row["id"]): row for row in db.execute(
            f"SELECT id,content,source,metadata_json FROM memories WHERE id IN ({placeholders})", ids).fetchall()}
        ids = [item for item in ids if item in rows]
        counter = "successful_uses" if success else "failed_uses"
        for memory_id in ids:
            try:
                metadata = json.loads(rows[memory_id]["metadata_json"] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            if not isinstance(metadata, dict):
                continue
            metadata[counter] = int(metadata.get(counter, 0) or 0) + 1
            metadata["last_used_at"] = stamp
            db.execute("UPDATE memories SET metadata_json=? WHERE id=?", (json.dumps(metadata, default=str), memory_id))
            result["memories"] += 1
        linkable = [item for item in ids if _linkable(rows[item])]
        for position, left in enumerate(linkable):
            for right in linkable[position + 1:]:
                pair = (min(left, right), max(left, right))
                existing = db.execute(
                    "SELECT source_id,target_id,relation,weight,evidence_json FROM memory_links WHERE "
                    "((source_id=? AND target_id=?) OR (source_id=? AND target_id=?)) AND relation IN "
                    f"({','.join('?' for _ in FADING_RELATIONS)})",
                    (*pair, pair[1], pair[0], *FADING_RELATIONS),
                ).fetchall()
                if success:
                    for link in existing:
                        weight = float(link["weight"]) + LEARNING_RATE * (1 - float(link["weight"]))
                        db.execute(
                            "UPDATE memory_links SET weight=?,last_reinforced_at=?,use_count=use_count+1,"
                            "evidence_json=? WHERE source_id=? AND target_id=? AND relation=?",
                            (round(weight, 6), stamp, _evidence(link["evidence_json"], True, reason),
                             link["source_id"], link["target_id"], link["relation"]))
                        result["strengthened"] += 1
                    if not existing:
                        _upsert(db, pair[0], pair[1], "co_recalled", NEW_LINK_WEIGHT,
                                {"method": "co_recall", "successes": 1, "failures": 0, "reason": reason[:120]})
                        db.execute("UPDATE memory_links SET use_count=1 WHERE source_id=? AND target_id=? AND "
                                   "relation='co_recalled'", pair)
                        result["created"] += 1
                else:
                    for link in existing:
                        if link["relation"] != "co_recalled":
                            continue  # a similarity or sequence link stays a fact; only learned co-use is unlearned
                        weight = float(link["weight"]) * (1 - LEARNING_RATE)
                        db.execute(
                            "UPDATE memory_links SET weight=?,evidence_json=? WHERE source_id=? AND target_id=? "
                            "AND relation='co_recalled'",
                            (round(weight, 6), _evidence(link["evidence_json"], False, reason),
                             link["source_id"], link["target_id"]))
                        result["weakened"] += 1
    return result


def _evidence(raw: str, success: bool, reason: str) -> str:
    try:
        evidence = json.loads(raw or "{}")
    except (TypeError, ValueError):
        evidence = {}
    if not isinstance(evidence, dict):
        evidence = {}
    key = "successes" if success else "failures"
    evidence[key] = int(evidence.get(key, 0) or 0) + 1
    if reason:
        evidence["last_reason"] = reason[:120]
    return json.dumps(evidence, default=str)


def reinforce_quietly(memory: Any, memory_ids: Iterable[int], success: bool, *, reason: str = "") -> None:
    """:func:`reinforce` for callers whose own work must never fail because learning did."""
    try:
        reinforce(memory, memory_ids, success, reason=reason)
    except Exception:  # learning is a side effect; the reply or task already happened
        log_swallowed(_log, "strengthening recalled memories")


# --------------------------------------------------------------------------- consolidation


def _goal(row: Any) -> str:
    try:
        goal = json.loads(row["metadata_json"] or "{}").get("goal")
    except (TypeError, ValueError, AttributeError):
        goal = None
    if not goal:
        found = re.search(r"Goal:\s*(.+?)(?:\.\s|\n|$)", str(row["content"] or ""))
        goal = found.group(1) if found else ""
    return str(goal)


def _goal_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 1 and word not in _STOP}


def consolidate(memory: Any, *, max_memories: int = 400, budget_seconds: float = 20.0) -> dict[str, Any]:
    """One bounded pass that binds past tasks and links task memories with the same purpose."""
    started = time.monotonic()
    report: dict[str, Any] = {"reflects_on": 0, "followed_by": 0, "related": 0, "examined": 0, "complete": True}
    with memory.connect() as db:
        task_ids = [row[0] for row in db.execute(
            "SELECT DISTINCT r.task_id FROM memories r JOIN memories e ON e.task_id=r.task_id "
            "AND e.memory_type='episodic' WHERE r.memory_type='reflection' AND r.task_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM memory_links l WHERE l.source_id=r.id AND l.relation='reflects_on')"
        ).fetchall()]
    for task_id in task_ids:
        if time.monotonic() - started > budget_seconds:
            report["complete"] = False
            return report
        made = bind_episode(memory, task_id)
        report["reflects_on"] += made["reflects_on"]
        report["followed_by"] += made["followed_by"]
    with memory.connect() as db:
        for row in db.execute(
            "SELECT id,content,source,metadata_json,session_id,timestamp,task_id FROM memories m "
            "WHERE memory_type='episodic' AND session_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM memory_links l "
            "WHERE l.target_id=m.id AND l.relation='followed_by')"
        ).fetchall():
            if _linkable(row):
                report["followed_by"] += _bind_sequence(db, row)
    report["related"], report["examined"], finished = _link_by_goal(memory, max_memories, started, budget_seconds)
    report["complete"] = report["complete"] and finished
    report["seconds"] = round(time.monotonic() - started, 3)
    return report


def _link_by_goal(memory: Any, max_memories: int, started: float, budget_seconds: float) -> tuple[int, int, bool]:
    try:
        import numpy as np
    except ImportError:  # goal words alone still decide
        np = None
    with memory.connect() as db:
        rows = [row for row in db.execute(
            "SELECT m.id,m.content,m.source,m.metadata_json,m.task_id,n.model,n.vector_json FROM memories m "
            "LEFT JOIN memory_neural_vectors n ON n.memory_id=m.id WHERE m.memory_type='episodic' ORDER BY m.id"
        ).fetchall() if _linkable(row)]
        already = {(int(a), int(b)) for a, b in db.execute(
            "SELECT source_id,target_id FROM memory_links WHERE relation='related'").fetchall()}
        # Which memories a pass has already compared with everything older, so the next pass skips them.
        # A table of its own: the memories themselves are not rewritten to remember that they were examined.
        db.execute("CREATE TABLE IF NOT EXISTS memory_consolidation(memory_id INTEGER PRIMARY KEY, examined_at TEXT "
                   "NOT NULL, FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE)")
        done = {int(row[0]) for row in db.execute("SELECT memory_id FROM memory_consolidation").fetchall()}
    if len(rows) < 2:
        return 0, 0, True
    words = [_goal_words(_goal(row)) for row in rows]
    frequency: dict[str, int] = {}
    for found in words:
        for word in found:
            frequency[word] = frequency.get(word, 0) + 1
    rarity = {word: math.log((len(rows) + 1) / (count + 0.5)) for word, count in frequency.items()}
    index: dict[str, list[int]] = {}
    for position, found in enumerate(words):
        for word in found:
            index.setdefault(word, []).append(position)
    models = [row["model"] for row in rows]
    vectors = None
    if np is not None:
        model_counts: dict[str, int] = {}
        for model in models:
            if model:
                model_counts[model] = model_counts.get(model, 0) + 1
        main = max(model_counts, key=model_counts.get) if model_counts else None
        if main:
            dimension = None
            matrix = []
            for row in rows:
                vector = json.loads(row["vector_json"]) if row["model"] == main and row["vector_json"] else None
                if vector is not None and dimension is None:
                    dimension = len(vector)
                matrix.append(vector)
            if dimension:
                vectors = np.zeros((len(rows), dimension))
                usable = np.zeros(len(rows), dtype=bool)
                for position, vector in enumerate(matrix):
                    if vector is not None and len(vector) == dimension:
                        vectors[position] = vector
                        usable[position] = True
                norms = np.linalg.norm(vectors, axis=1)
                norms[norms == 0] = 1
                vectors = (vectors / norms[:, None], usable)
    made = examined = 0
    for position, row in enumerate(rows):
        if int(row["id"]) in done:
            continue
        if examined >= max_memories or time.monotonic() - started > budget_seconds:
            return made, examined, False
        examined += 1
        mine = words[position]
        candidates = {other for word in mine for other in index.get(word, ()) if other != position}
        scored = []
        for other in candidates:
            if rows[other]["task_id"] and rows[other]["task_id"] == row["task_id"]:
                continue
            union = sum(rarity[word] for word in mine | words[other])
            lexical = sum(rarity[word] for word in mine & words[other]) / union if union else 0.0
            if vectors is not None and vectors[1][position] and vectors[1][other]:
                cosine = float(vectors[0][position] @ vectors[0][other])
                score = 0.65 * lexical + 0.35 * max(0.0, (cosine - 0.6) / 0.4)
            else:
                # Without meaning vectors, words decide alone: half the weighted overlap above, half how much of
                # the shorter goal the other covers ("Close notepad" is all of "close notepad without saving").
                cosine = None
                shared = sum(rarity[word] for word in mine & words[other])
                smaller = min(sum(rarity[word] for word in mine), sum(rarity[word] for word in words[other]))
                score = 0.5 * lexical + 0.5 * (shared / smaller if smaller else 0.0)
            if score >= GOAL_LINK_THRESHOLD:
                scored.append((score, int(rows[other]["id"]), lexical, cosine))
        scored.sort(reverse=True)
        with memory.connect() as db:
            for score, other_id, lexical, cosine in scored[:GOAL_LINKS_PER_MEMORY]:
                pair = (int(row["id"]), other_id)
                if pair in already or pair[::-1] in already:
                    continue
                evidence = {"method": "goal_consolidation", "score": round(score, 4), "goal_words": round(lexical, 4)}
                if cosine is not None:
                    evidence["meaning"] = round(cosine, 4)
                if _upsert(db, pair[0], pair[1], "related", min(1.0, score), evidence):
                    already.add(pair)
                    made += 1
            db.execute("INSERT OR REPLACE INTO memory_consolidation(memory_id,examined_at) VALUES(?,?)",
                       (int(row["id"]), _now()))
    return made, examined, True


def consolidate_quietly(memory: Any, **options: Any) -> dict[str, Any] | None:
    try:
        return consolidate(memory, **options)
    except Exception:  # the next start tries again; recall works without the new links
        log_swallowed(_log, "consolidating the memory network")
        return None


# --------------------------------------------------------------------------- report


def network_report(memory: Any, *, strongest: int = 5) -> dict[str, Any]:
    """Measured state of the memory network: memories, links by kind, coverage, what learned from use."""
    from app.memory.database import MEMORY_RELATIONS

    with memory.connect() as db:
        total = int(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        by_type = {row[0]: int(row[1]) for row in db.execute(
            "SELECT memory_type,COUNT(*) FROM memories GROUP BY memory_type").fetchall()}
        links = {row[0]: {"links": int(row[1]), "mean_weight": round(float(row[2] or 0), 3),
                          "uses": int(row[3] or 0)} for row in db.execute(
            "SELECT relation,COUNT(*),AVG(weight),SUM(use_count) FROM memory_links GROUP BY relation").fetchall()}
        connected = int(db.execute(
            "SELECT COUNT(*) FROM (SELECT source_id id FROM memory_links UNION SELECT target_id FROM memory_links)"
        ).fetchone()[0])
        provider = getattr(memory, "embedding_provider", None)
        identifier = getattr(provider, "identifier", None) if provider is not None and getattr(provider, "available", False) else None
        current = int(db.execute("SELECT COUNT(*) FROM memory_neural_vectors WHERE model=?",
                                 (identifier,)).fetchone()[0]) if identifier else None
        learned = db.execute(
            "SELECT l.source_id,l.target_id,l.relation,l.weight,l.use_count,a.content ac,b.content bc FROM memory_links l "
            "JOIN memories a ON a.id=l.source_id JOIN memories b ON b.id=l.target_id WHERE l.use_count>0 "
            "ORDER BY l.use_count DESC,l.weight DESC LIMIT ?", (max(0, int(strongest)),)).fetchall()
        used = db.execute(
            "SELECT COALESCE(SUM(json_extract(metadata_json,'$.successful_uses')),0),"
            "COALESCE(SUM(json_extract(metadata_json,'$.failed_uses')),0) FROM memories WHERE json_valid(metadata_json)"
        ).fetchone()
    count = sum(item["links"] for item in links.values())
    return {
        "memories": total, "by_type": by_type, "links": count,
        "links_by_kind": {name: links[name] for name in sorted(links) if name in MEMORY_RELATIONS},
        "connected_memories": connected, "unconnected_memories": max(0, total - connected),
        "average_links_per_memory": round(2 * count / total, 2) if total else 0.0,
        "meaning_vectors_on_active_model": current, "active_embedding_model": identifier,
        "recalls_that_helped": int(used[0] or 0), "recalls_that_did_not": int(used[1] or 0),
        "strongest_learned_links": [
            {"from": int(row["source_id"]), "to": int(row["target_id"]), "relation": row["relation"],
             "weight": round(float(row["weight"]), 3), "uses": int(row["use_count"]),
             "from_text": str(row["ac"])[:90], "to_text": str(row["bc"])[:90]} for row in learned],
        "fading": {"relations": list(FADING_RELATIONS), "half_life_days": HALF_LIFE_DAYS},
    }


def describe(report: dict[str, Any]) -> str:
    """Plain-words summary of :func:`network_report`."""
    kinds = report.get("links_by_kind") or {}
    names = {"related": "similar purpose", "reflects_on": "lesson to its task", "followed_by": "one task after another",
             "co_recalled": "learned from use", "supports": "supports", "contradicts": "contradicts",
             "supersedes": "replaces", "depends_on": "depends on", "caused_by": "caused by",
             "part_of": "part of", "same_entity": "same thing"}
    lines = [
        f"Memory network: {report['memories']} memories, {report['links']} links "
        f"({report['average_links_per_memory']} per memory); {report['connected_memories']} memories are connected "
        f"and {report['unconnected_memories']} stand alone.",
    ]
    if kinds:
        lines.append("Links: " + "; ".join(
            f"{names.get(name, name)} {item['links']}" + (f" (used {item['uses']}×)" if item["uses"] else "")
            for name, item in kinds.items()) + ".")
    if report.get("active_embedding_model"):
        lines.append(f"Recall by meaning covers {report['meaning_vectors_on_active_model']} of {report['memories']} "
                     f"memories with {report['active_embedding_model']}.")
    lines.append(f"Learning from use: recalled memories helped {report['recalls_that_helped']} times and did not "
                 f"help {report['recalls_that_did_not']} times.")
    for link in report.get("strongest_learned_links") or []:
        lines.append(f"- {link['from_text'][:60]!r} ↔ {link['to_text'][:60]!r}: {names.get(link['relation'], link['relation'])}, "
                     f"used {link['uses']}×, weight {link['weight']}")
    lines.append(f"Similarity, sequence and learned links fade to half strength after {int(HALF_LIFE_DAYS)} days "
                 "unused and return to full strength when used; a lesson linked to its task never fades.")
    return "\n".join(lines)


class MemoryNetwork:
    """The memory network as a runtime part (``runtime.memory_network``), so Brain 5D can check it like any other."""

    def __init__(self, memory: Any) -> None:
        self.memory = memory
        self.last_consolidation: dict[str, Any] | None = None

    def consolidate(self, **options: Any) -> dict[str, Any] | None:
        self.last_consolidation = consolidate_quietly(self.memory, **options)
        return self.last_consolidation

    def status(self) -> dict[str, Any]:
        report = network_report(self.memory, strongest=0)
        kinds = report["links_by_kind"]
        result = {
            "memories": report["memories"], "links": report["links"],
            "links_per_memory": report["average_links_per_memory"],
            "standing_alone": report["unconnected_memories"],
            "kinds": ", ".join(f"{name} {item['links']}" for name, item in kinds.items()) or "none",
            "helped": report["recalls_that_helped"], "did_not_help": report["recalls_that_did_not"],
        }
        if self.last_consolidation:
            done = self.last_consolidation
            result["last_consolidation"] = (f"{done.get('reflects_on', 0)} lessons bound, {done.get('followed_by', 0)} "
                                            f"sequences, {done.get('related', 0)} same-purpose links in "
                                            f"{done.get('seconds', 0)} s")
        return result
