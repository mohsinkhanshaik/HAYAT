from __future__ import annotations

import json
import hashlib
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import PROJECT_ROOT, load_settings
from app.linguistics import defined_acronyms, expand_query, query_acronyms
from app.memory.state_policy import classify, excluded_from_conversation
from app.memory.embeddings import content_hash, optional_embedding
from app.networking_privacy import PRIVATE_TEXT, contains_private_tools, is_private_tool, redact_private_payload


SCHEMA_VERSION = 7
VECTOR_DIMENSIONS = 128

# “DNA” means Dynamic Neural Associations in this project.  These relations
# are retrieval edges, not model-weight training and not a biological claim.
MEMORY_RELATIONS = frozenset({
    "related", "supports", "contradicts", "depends_on", "caused_by",
    "part_of", "same_entity", "followed_by", "supersedes",
})


class MemoryDatabase:
    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or load_settings()["memory_database"]
        self.path = Path(configured)
        if not self.path.is_absolute():
            self.path = PROJECT_ROOT / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_provider = None

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        # NORMAL keeps WAL durable across process crashes without forcing a
        # physical disk flush for every short memory write. A busy timeout is
        # important because voice, GUI and background learning can all reach
        # memory at once.
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '[]',
                    outcome TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    tool TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    verification_json TEXT NOT NULL,
                    retry INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    source TEXT NOT NULL,
                    task_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    content_digest TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
                CREATE INDEX IF NOT EXISTS idx_memories_task ON memories(task_id);
                CREATE TABLE IF NOT EXISTS procedures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    definition_yaml TEXT NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    average_latency_ms REAL NOT NULL DEFAULT 0,
                    last_used_at TEXT,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    trusted INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_vectors (
                    memory_id INTEGER PRIMARY KEY,
                    vector_json TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS memory_neural_vectors (
                    memory_id INTEGER PRIMARY KEY,
                    model TEXT NOT NULL,
                    embedding_version TEXT NOT NULL DEFAULT '1',
                    content_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT,
                    vector_json TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS memory_links (
                    source_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    relation TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 0.5,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    last_reinforced_at TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(source_id, target_id, relation),
                    CHECK(source_id <> target_id),
                    FOREIGN KEY(source_id) REFERENCES memories(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES memories(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_links_source ON memory_links(source_id);
                CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_id);
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    provider TEXT NOT NULL, model TEXT NOT NULL, embedding_version TEXT NOT NULL,
                    content_hash TEXT NOT NULL, dimension INTEGER NOT NULL, created_at TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    PRIMARY KEY(provider,model,embedding_version,content_hash)
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    model TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id, id);
                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_checkpoints_task ON task_checkpoints(task_id, id);
                """
            )
            procedure_columns = {row[1] for row in db.execute("PRAGMA table_info(procedures)").fetchall()}
            for name, declaration in {
                "average_latency_ms": "REAL NOT NULL DEFAULT 0",
                "last_used_at": "TEXT",
                "confidence": "REAL NOT NULL DEFAULT 0.5",
            }.items():
                if name not in procedure_columns:
                    db.execute(f"ALTER TABLE procedures ADD COLUMN {name} {declaration}")
            memory_columns = {row[1] for row in db.execute("PRAGMA table_info(memories)").fetchall()}
            # Without session provenance a new session retrieves another
            # session's transient observations (screenshots, camera frames,
            # window state embedded in episodic task records) and cannot tell
            # they are history rather than current state.
            if "session_id" not in memory_columns:
                db.execute("ALTER TABLE memories ADD COLUMN session_id TEXT")
            # How a memory relates to the present. Transient snapshots (camera,
            # screen, window, resources) must not describe "now" for a session
            # that did not observe them; see app/memory/state_policy.py.
            if "state_class" not in memory_columns:
                db.execute("ALTER TABLE memories ADD COLUMN state_class TEXT")
                # Backfill by inspecting substance. Rows whose payload is a
                # sensor snapshot are marked TRANSIENT so the existing database
                # is covered too; anything genuinely ambiguous stays
                # LEGACY_UNTYPED, which is never treated as current.
                for row in db.execute("SELECT id, memory_type, content FROM memories").fetchall():
                    db.execute(
                        "UPDATE memories SET state_class=? WHERE id=?",
                        (classify(str(row[1] or ""), str(row[2] or "")).value, row[0]),
                    )
            if "content_digest" not in memory_columns:
                db.execute("ALTER TABLE memories ADD COLUMN content_digest TEXT NOT NULL DEFAULT ''")
            # Exact-content digests make offline corruption or direct database
            # edits visible before a row can enter model context. This is
            # tamper evidence, not a claim that a compromised OS is harmless.
            for row in db.execute(
                "SELECT id,content FROM memories WHERE content_digest='' OR content_digest IS NULL"
            ).fetchall():
                db.execute(
                    "UPDATE memories SET content_digest=? WHERE id=?",
                    (_memory_digest(str(row["content"])), int(row["id"])),
                )
            vector_columns = {row[1] for row in db.execute("PRAGMA table_info(memory_neural_vectors)").fetchall()}
            for name, declaration in {
                "embedding_version": "TEXT NOT NULL DEFAULT '1'",
                "content_hash": "TEXT NOT NULL DEFAULT ''",
                "created_at": "TEXT",
            }.items():
                if name not in vector_columns:
                    db.execute(f"ALTER TABLE memory_neural_vectors ADD COLUMN {name} {declaration}")
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, now_iso()),
            )
            self._initialize_full_text_search(db)
            missing = db.execute(
                "SELECT id, content FROM memories WHERE id NOT IN (SELECT memory_id FROM memory_vectors)"
            ).fetchall()
            db.executemany(
                "INSERT OR IGNORE INTO memory_vectors(memory_id, vector_json) VALUES(?,?)",
                [(row["id"], json.dumps(text_vector(row["content"]))) for row in missing],
            )

    @staticmethod
    def _initialize_full_text_search(db: sqlite3.Connection) -> bool:
        """Create a content-synchronised FTS5 index when SQLite provides it.

        The old lexical prefilter expanded every query into a set of
        ``content LIKE '%word%'`` scans. That is correct, but latency grows
        linearly with the lifetime of the assistant. FTS5 is bundled with the
        normal Windows Python SQLite build and turns the same lookup into an
        inverted-index query. The fallback in ``_candidates`` preserves
        compatibility with unusual SQLite builds that omit FTS5.
        """
        existed = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'"
        ).fetchone()
        try:
            db.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    content,
                    content='memories',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TRIGGER IF NOT EXISTS memories_fts_insert AFTER INSERT ON memories BEGIN
                    INSERT INTO memory_fts(rowid,content) VALUES (new.id,new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_fts_delete AFTER DELETE ON memories BEGIN
                    INSERT INTO memory_fts(memory_fts,rowid,content)
                    VALUES ('delete',old.id,old.content);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_fts_update AFTER UPDATE OF content ON memories BEGIN
                    INSERT INTO memory_fts(memory_fts,rowid,content)
                    VALUES ('delete',old.id,old.content);
                    INSERT INTO memory_fts(rowid,content) VALUES (new.id,new.content);
                END;
                """
            )
            if not existed:
                db.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")
            return True
        except sqlite3.OperationalError:
            return False

    def create_task(self, task_id: str, goal: str, plan: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> None:
        timestamp = now_iso()
        if contains_private_tools(plan):
            goal, plan = PRIVATE_TEXT, redact_private_payload(plan)
            metadata = redact_private_payload(metadata or {})
        with self.connect() as db:
            db.execute(
                "INSERT INTO tasks(id, created_at, updated_at, goal, status, plan_json, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, timestamp, timestamp, goal, "running", json.dumps(plan), json.dumps(metadata or {})),
            )

    def update_task(self, task_id: str, status: str, outcome: str | None = None, plan: list[dict[str, Any]] | None = None) -> None:
        private_networking = self._private_networking_task(task_id) or contains_private_tools(plan)
        if private_networking:
            outcome = PRIVATE_TEXT if outcome is not None else None
            plan = redact_private_payload(plan) if plan is not None else None
        with self.connect() as db:
            if private_networking:
                db.execute("UPDATE tasks SET goal=? WHERE id=?", (PRIVATE_TEXT, task_id))
            if plan is None:
                db.execute("UPDATE tasks SET updated_at=?, status=?, outcome=? WHERE id=?", (now_iso(), status, outcome, task_id))
            else:
                db.execute("UPDATE tasks SET updated_at=?, status=?, outcome=?, plan_json=? WHERE id=?", (now_iso(), status, outcome, json.dumps(plan), task_id))

    def record_action(self, task_id: str, step_index: int, tool: str, parameters: dict[str, Any], result: dict[str, Any], verification: dict[str, Any], retry: int = 0, duration_ms: int = 0) -> None:
        if is_private_tool(tool):
            self._mark_private_networking_task(task_id)
        if is_private_tool(tool) or self._private_networking_task(task_id):
            parameters, result, verification = (redact_private_payload(value) for value in (parameters, result, verification))
        with self.connect() as db:
            db.execute(
                "INSERT INTO actions(task_id,timestamp,step_index,tool,parameters_json,result_json,verification_json,retry,duration_ms) VALUES(?,?,?,?,?,?,?,?,?)",
                (task_id, now_iso(), step_index, tool, json.dumps(parameters, default=str), json.dumps(result, default=str), json.dumps(verification, default=str), retry, duration_ms),
            )

    def record_checkpoint(self, task_id: str, step_index: int, state: str, checkpoint: dict[str, Any]) -> int:
        if self._private_networking_task(task_id) or contains_private_tools(checkpoint):
            checkpoint = redact_private_payload(checkpoint)
        with self.connect() as db:
            cursor = db.execute("INSERT INTO task_checkpoints(task_id,timestamp,step_index,state,checkpoint_json) VALUES(?,?,?,?,?)", (task_id, now_iso(), step_index, state, json.dumps(checkpoint, default=str)))
            return int(cursor.lastrowid)

    def checkpoints(self, task_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM task_checkpoints WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, max(1, min(limit, 200)))).fetchall()
        return [dict(row) | {"checkpoint": json.loads(row["checkpoint_json"])} for row in rows]

    def _private_networking_task(self, task_id: str | None) -> bool:
        if not task_id:
            return False
        with self.connect() as db:
            row = db.execute("SELECT plan_json,metadata_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        return bool(row and contains_private_tools([json.loads(row["plan_json"]), json.loads(row["metadata_json"])]))

    def _mark_private_networking_task(self, task_id: str) -> None:
        with self.connect() as db:
            row = db.execute("SELECT plan_json,metadata_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row:
                plan = redact_private_payload(json.loads(row["plan_json"]))
                metadata = redact_private_payload(json.loads(row["metadata_json"]))
                db.execute("UPDATE tasks SET goal=?,plan_json=?,metadata_json=? WHERE id=?",
                           (PRIVATE_TEXT, json.dumps(plan), json.dumps(metadata), task_id))

    def store_memory(self, memory_type: str, content: str, importance: float = 0.5, source: str = "agent", task_id: str | None = None, metadata: dict[str, Any] | None = None, session_id: str | None = None, *, defer_neural: bool = False) -> int:
        if memory_type not in {"working", "episodic", "semantic", "procedural", "reflection", "project", "learning"}:
            raise ValueError(f"Unsupported memory type: {memory_type}")
        # SECRET material must never reach durable memory, including its vectors.
        from app.safety.sensitive import redact as redact_sensitive, redact_text

        if self._private_networking_task(task_id) or contains_private_tools(metadata):
            content, metadata = PRIVATE_TEXT, redact_private_payload(metadata or {})
        content, _ = redact_text(content)
        metadata = redact_sensitive(metadata) if metadata else metadata
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO memories(timestamp,memory_type,content,importance,source,task_id,metadata_json,session_id,state_class,content_digest) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), memory_type, content, max(0.0, min(1.0, importance)), source, task_id, json.dumps(metadata or {}, default=str), session_id, classify(memory_type, content, metadata).value, _memory_digest(content)),
            )
            memory_id = int(cursor.lastrowid)
            db.execute("INSERT INTO memory_vectors(memory_id,vector_json) VALUES(?,?)", (memory_id, json.dumps(text_vector(content))))
        if not defer_neural and self.embedding_provider is not None and self.embedding_provider.available:
            vector = self._cached_embedding(content)
            if vector is not None:
                with self.connect() as db:
                    db.execute("INSERT INTO memory_neural_vectors(memory_id,model,embedding_version,content_hash,created_at,vector_json) VALUES(?,?,?,?,?,?)", (memory_id, self.embedding_provider.identifier, self.embedding_provider.version, content_hash(content), now_iso(), json.dumps(vector)))
        dna = load_settings().get("memory_retrieval", {}).get("dna", {})
        # High-value durable knowledge forms links immediately. High-volume
        # transient/episodic streams are intentionally excluded here: linking
        # every log or sensor frame would create a noisy graph and turn ingest
        # into quadratic work over months of 24/7 operation.
        auto_link_types = {"semantic", "procedural", "project", "learning", "reflection"}
        if (dna.get("enabled", True) and dna.get("auto_link", True)
                and memory_type in auto_link_types
                and importance >= float(dna.get("auto_link_min_importance", 0.7))
                and _trusted_for_automatic_links(source, metadata)):
            self.form_associations(
                memory_id,
                max_links=max(0, min(int(dna.get("auto_link_max", 3)), 12)),
                minimum_similarity=max(0.0, min(float(dna.get("auto_link_min_similarity", 0.58)), 1.0)),
            )
        return memory_id

    def link_memories(
        self,
        source_id: int,
        target_id: int,
        relation: str = "related",
        weight: float = 0.5,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or reinforce a durable, provenance-bearing association.

        A contradiction edge must be explicitly supplied by a caller that has
        evidence. Automatic similarity linking never invents contradictions.
        """
        relation = relation.strip().lower()
        if relation not in MEMORY_RELATIONS:
            raise ValueError(f"Unsupported memory relation: {relation}")
        if int(source_id) == int(target_id):
            raise ValueError("A memory cannot be linked to itself.")
        bounded_weight = max(0.0, min(float(weight), 1.0))
        from app.safety.sensitive import redact as redact_sensitive

        safe_evidence = redact_sensitive(evidence or {})
        timestamp = now_iso()
        with self.connect() as db:
            found = int(db.execute(
                "SELECT COUNT(*) FROM memories WHERE id IN (?,?)", (int(source_id), int(target_id))
            ).fetchone()[0])
            if found != 2:
                raise KeyError("Both memory IDs must exist before they can be linked.")
            db.execute(
                """INSERT INTO memory_links(source_id,target_id,relation,weight,evidence_json,created_at,last_reinforced_at,use_count)
                   VALUES(?,?,?,?,?,?,?,0)
                   ON CONFLICT(source_id,target_id,relation) DO UPDATE SET
                     weight=MAX(memory_links.weight,excluded.weight),
                     evidence_json=excluded.evidence_json,
                     last_reinforced_at=excluded.last_reinforced_at,
                     use_count=memory_links.use_count+1""",
                (int(source_id), int(target_id), relation, bounded_weight,
                 json.dumps(safe_evidence, default=str), timestamp, timestamp),
            )
        return {
            "source_id": int(source_id), "target_id": int(target_id),
            "relation": relation, "weight": bounded_weight, "evidence": safe_evidence,
        }

    def form_associations(self, memory_id: int, *, max_links: int = 3, minimum_similarity: float = 0.58) -> list[dict[str, Any]]:
        """Form a few deterministic local similarity edges for one memory.

        This deliberately uses the local hashed vector and lexical overlap, so
        storing a memory never blocks on Ollama or a network service.
        """
        if max_links <= 0:
            return []
        with self.connect() as db:
            source = db.execute(
                "SELECT id,content FROM memories WHERE id=?", (int(memory_id),)
            ).fetchone()
            if source is None:
                raise KeyError(f"Memory {memory_id} does not exist.")
            tokens = {item for item in tokenize(source["content"]) if len(item) >= 3}
            candidates: list[sqlite3.Row] = []
            if tokens and db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'"
            ).fetchone():
                match = " OR ".join(f'"{item}"' for item in sorted(tokens)[:12])
                try:
                    candidates = db.execute(
                        "SELECT m.id,m.content FROM memories m JOIN memory_fts f ON f.rowid=m.id "
                        "WHERE m.id<>? AND f.content MATCH ? ORDER BY bm25(memory_fts) LIMIT 24",
                        (int(memory_id), match),
                    ).fetchall()
                except sqlite3.OperationalError:
                    candidates = []
            if not candidates:
                candidates = db.execute(
                    "SELECT id,content FROM memories WHERE id<>? ORDER BY id DESC LIMIT 24", (int(memory_id),)
                ).fetchall()

            source_vector = text_vector(source["content"])
            scored: list[tuple[float, sqlite3.Row]] = []
            for candidate in candidates:
                other_tokens = set(tokenize(candidate["content"]))
                lexical = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
                semantic = max(0.0, cosine_similarity(source_vector, text_vector(candidate["content"])))
                similarity = 0.65 * lexical + 0.35 * semantic
                if similarity >= minimum_similarity:
                    scored.append((similarity, candidate))
            scored.sort(key=lambda item: (item[0], int(item[1]["id"])), reverse=True)
            linked: list[dict[str, Any]] = []
            for similarity, candidate in scored[:max_links]:
                target_id = int(candidate["id"])
                timestamp = now_iso()
                evidence = {"method": "local_similarity", "score": round(similarity, 6)}
                db.execute(
                    """INSERT INTO memory_links(source_id,target_id,relation,weight,evidence_json,created_at,last_reinforced_at,use_count)
                       VALUES(?,?,?,?,?,?,?,0)
                       ON CONFLICT(source_id,target_id,relation) DO UPDATE SET
                         weight=MAX(memory_links.weight,excluded.weight),
                         last_reinforced_at=excluded.last_reinforced_at,
                         use_count=memory_links.use_count+1""",
                    (int(memory_id), target_id, "related", min(1.0, similarity),
                     json.dumps(evidence), timestamp, timestamp),
                )
                linked.append({"source_id": int(memory_id), "target_id": target_id,
                               "relation": "related", "weight": round(min(1.0, similarity), 6),
                               "evidence": evidence})
        return linked

    def associations(self, memory_id: int, *, max_depth: int = 2, limit: int = 24) -> list[dict[str, Any]]:
        """Traverse one seed through the indexed association graph."""
        return self.associations_many([memory_id], max_depth=max_depth, limit=limit)

    def associations_many(self, memory_ids: list[int], *, max_depth: int = 2, limit: int = 24) -> list[dict[str, Any]]:
        """Traverse several seeds in one indexed recursive SQLite query.

        Earlier recall opened a connection and queried every frontier node.
        This version keeps traversal inside SQLite, uses both link indexes, and
        returns all seed paths through one connection without losing cycle
        control or the hard depth/node bounds.
        """
        max_depth = max(1, min(int(max_depth), 4))
        limit = max(1, min(int(limit), 200))
        seeds = list(dict.fromkeys(int(item) for item in memory_ids))[:12]
        if not seeds:
            return []
        seed_values = ",".join("(?)" for _ in seeds)
        with self.connect() as db:
            rows = db.execute(
                f"""WITH RECURSIVE
                    seeds(seed_id) AS (VALUES {seed_values}),
                    edges(from_id,to_id,relation,weight,evidence_json,direction) AS (
                        SELECT source_id,target_id,relation,weight,evidence_json,'outgoing' FROM memory_links
                        UNION ALL
                        SELECT target_id,source_id,relation,weight,evidence_json,'incoming' FROM memory_links
                    ),
                    walk(seed_id,memory_id,from_id,relation,weight,path_weight,depth,path,direction,evidence_json) AS (
                        SELECT s.seed_id,e.to_id,e.from_id,e.relation,e.weight,e.weight,1,
                               ',' || s.seed_id || ',' || e.to_id || ',',e.direction,e.evidence_json
                        FROM seeds s JOIN edges e ON e.from_id=s.seed_id
                        UNION ALL
                        SELECT w.seed_id,e.to_id,w.memory_id,e.relation,e.weight,w.path_weight*e.weight,w.depth+1,
                               w.path || e.to_id || ',',e.direction,e.evidence_json
                        FROM walk w JOIN edges e ON e.from_id=w.memory_id
                        WHERE w.depth < ? AND instr(w.path, ',' || e.to_id || ',')=0
                    )
                    SELECT * FROM walk ORDER BY depth ASC,path_weight DESC LIMIT ?""",
                [*seeds, max_depth, limit * max(1, len(seeds))],
            ).fetchall()
        best: dict[tuple[int, int], dict[str, Any]] = {}
        for row in rows:
            path = [int(item) for item in str(row["path"]).strip(",").split(",") if item]
            item = {
                "seed_id": int(row["seed_id"]), "memory_id": int(row["memory_id"]),
                "from_id": int(row["from_id"]), "relation": row["relation"],
                "weight": float(row["weight"]), "path_weight": float(row["path_weight"]),
                "depth": int(row["depth"]), "path": path, "direction": row["direction"],
                "evidence": json.loads(row["evidence_json"] or "{}"),
                "contradiction": row["relation"] == "contradicts",
            }
            key = (item["seed_id"], item["memory_id"])
            previous = best.get(key)
            if previous is None or item["path_weight"] > previous["path_weight"]:
                best[key] = item
        return sorted(best.values(), key=lambda item: (item["depth"], -item["path_weight"]))[:limit]

    SELECT_MEMORY = (
        "SELECT m.*,v.vector_json,n.vector_json neural_vector_json,n.model neural_model "
        "FROM memories m "
        "LEFT JOIN memory_vectors v ON v.memory_id=m.id "
        "LEFT JOIN memory_neural_vectors n ON n.memory_id=m.id"
    )

    def _candidates(self, where: str, parameters: list[Any], tokens: set[str], candidate_limit: int,
                    acronyms: tuple[str, ...] = ()) -> list[Any]:
        """Union candidate generation: lexical matches, then importance, then recency.

        Selecting purely by recency meant a memory older than `candidate_limit`
        newer records could never be scored, however well it matched. The lexical
        pass runs across the whole table so an old but relevant memory still
        reaches the ranker; the recency pass is preserved so behaviour for recent
        material is unchanged.
        """
        collected: dict[int, Any] = {}

        def absorb(rows) -> None:
            for row in rows:
                collected.setdefault(int(row["id"]), row)

        terms = sorted({token for token in tokens if len(token) >= 3})[:12]
        with self.connect() as db:
            if terms:
                fts = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'"
                ).fetchone()
                if fts:
                    # tokenize() only returns [a-z0-9_], so quoting each term
                    # is enough to keep user punctuation out of MATCH syntax.
                    match = " OR ".join(f'"{term}"' for term in terms)
                    try:
                        lexical_sql = self.SELECT_MEMORY.replace(
                            "FROM memories m ",
                            "FROM memories m JOIN memory_fts f ON f.rowid=m.id ",
                        )
                        absorb(
                            db.execute(
                                f"{lexical_sql} WHERE {where} AND f.content MATCH ? "
                                "ORDER BY bm25(memory_fts),m.importance DESC,m.timestamp DESC LIMIT ?",
                                [*parameters, match, candidate_limit],
                            ).fetchall()
                        )
                    except sqlite3.OperationalError:
                        fts = None
                if fts:
                    # "FSDP" never occurs in "Fully Sharded Data Parallel", so
                    # the pass above cannot find the memory that defines it.
                    # Words starting with each letter, all present, can; the
                    # ranker then keeps only an exact initials match.
                    for acronym in acronyms[:3]:
                        if not 3 <= len(acronym) <= 6:
                            continue
                        prefixes = " AND ".join(f"{letter}*" for letter in dict.fromkeys(acronym))
                        try:
                            absorb(
                                db.execute(
                                    f"{lexical_sql} WHERE {where} AND f.content MATCH ? "
                                    "ORDER BY m.importance DESC,m.timestamp DESC LIMIT ?",
                                    [*parameters, prefixes, max(50, candidate_limit // 10)],
                                ).fetchall()
                            )
                        except sqlite3.OperationalError:
                            break
                if not fts:
                    clause = " OR ".join("m.content LIKE ? ESCAPE '\\'" for _ in terms)
                    like_parameters = [
                        "%" + term.replace("%", r"\%").replace("_", r"\_") + "%" for term in terms
                    ]
                    absorb(
                        db.execute(
                            f"{self.SELECT_MEMORY} WHERE {where} AND ({clause}) "
                            "ORDER BY m.importance DESC, m.timestamp DESC LIMIT ?",
                            [*parameters, *like_parameters, candidate_limit],
                        ).fetchall()
                    )
            absorb(
                db.execute(
                    f"{self.SELECT_MEMORY} WHERE {where} ORDER BY m.importance DESC, m.timestamp DESC LIMIT ?",
                    [*parameters, max(50, candidate_limit // 4)],
                ).fetchall()
            )
            absorb(
                db.execute(
                    f"{self.SELECT_MEMORY} WHERE {where} ORDER BY m.timestamp DESC LIMIT ?",
                    [*parameters, candidate_limit],
                ).fetchall()
            )
        return list(collected.values())

    def retrieve(self, query: str, memory_type: str | None = None, limit: int = 8, *, strategy: str = "hybrid", project: str | None = None, weights: dict[str, float] | None = None, session_id: str | None = None, include_foreign_transient: bool = False) -> list[dict[str, Any]]:
        """Retrieve ranked memories.

        `session_id` enables the current-state policy: transient snapshots
        (camera frames, screen contents, window and resource state) recorded by
        a *different* session are excluded outright rather than labelled and
        hoped about, because they describe a world that has since moved on.
        Pass `include_foreign_transient=True` only for explicitly historical
        questions ("what was on my screen earlier?").
        """
        retrieval_started = time.perf_counter()
        expansion = expand_query(query)
        tokens = set(tokenize(expansion.text))
        where = "1=1"
        parameters: list[Any] = []
        if memory_type:
            where = "m.memory_type=?"
            parameters.append(memory_type)
        settings = load_settings().get("memory_retrieval", {})
        requested_limit = max(1, int(limit))
        # Ordinary chat stays on the configured fast candidate set. Explicit
        # later recall pages widen the ranked set as needed, so page two is not
        # just an empty view of a hidden fixed 500-row ceiling.
        candidate_limit = max(50, int(settings.get("candidate_limit", 500)),
                              min(100_000, requested_limit * 4))
        exact_identifier = bool(re.search(r"\b[A-Za-z]{2,}[\s._-]*\d{2,}\b", query))
        if exact_identifier:
            # FTS retrieves identifier matches across the entire database, so
            # a smaller scoring set retains old exact results while avoiding
            # unnecessary tokenization of hundreds of unrelated recent rows.
            candidate_limit = (min(candidate_limit, 160) if requested_limit <= 160
                               else candidate_limit)
        acronyms = query_acronyms(query)
        rows = self._candidates(where, parameters, tokens, candidate_limit, acronyms)
        # A digest mismatch is quarantined from retrieval. Never reward a
        # modified row by embedding/ranking it and hoping the model notices.
        rows = [row for row in rows if _memory_digest(str(row["content"])) == str(row["content_digest"] or "")]
        if not include_foreign_transient:
            # Enforced here, before ranking and before the model sees anything:
            # a snapshot from another session cannot be retrieved as context at
            # all, so it cannot be mistaken for the present no matter how the
            # prompt is worded.
            rows = [row for row in rows if not excluded_from_conversation(dict(row), session_id=session_id)]
        query_vector = text_vector(expansion.text)
        # Persist query embeddings in the same model/version/content-addressed
        # cache as documents. Repeated recall after a restart now avoids a
        # local-model round trip while retaining exact invalidation semantics.
        # Exact technical identifiers (RFC 8785, CVE-2026-1234, Windows 11)
        # are represented more faithfully by lexical/entity/hashed matching.
        # Avoid waking a cold local embedding model for those queries: it adds
        # seconds of latency and can dilute the exact identifier match.
        neural_query = self._cached_embedding(query) if (
            strategy in {"semantic", "hybrid"}
            and not exact_identifier
            and self.embedding_provider is not None
            and self.embedding_provider.available
        ) else None
        query_entities = set(re.findall(r"\b[A-Z][A-Za-z0-9_.-]+\b", query))
        # A capital that only starts the sentence ("Which", "Retrieve") marks
        # grammar, not a name; counting it halved the credit for real ones.
        opening = re.match(r"\s*([A-Z][a-z]+)\b", query)
        if opening and len(query_entities) > 1:
            query_entities.discard(opening.group(1))
        configured = load_settings().get("memory_retrieval", {}).get("weights", {})
        score_weights = {"neural": .34, "hashed_semantic": .10, "lexical": .22, "entity": .08, "project": .08, "recency": .05, "confidence": .05, "importance": .05, "successful_use": .03} | configured | (weights or {})
        ranked = []
        # Expand the query once, not every candidate document. Query-side
        # expansion already contains each canonical term and alias; doing
        # the same regex work over hundreds of long chunks is redundant
        # and dominated recall latency for downloaded documents.
        row_token_sets = []
        for row in rows:
            row_tokens = set(tokenize(row["content"]))
            spelled = set(acronyms) & defined_acronyms(str(row["content"])) if acronyms else set()
            # A memory that spells an acronym out matches it as if it used it.
            row_token_sets.append((row_tokens | spelled, spelled))
        # Rare words decide; common ones barely count. "FSDP" in one memory
        # out of fifty says far more than "the" in forty of them. Words no
        # candidate contains cannot tell candidates apart and are left out.
        frequency = {token: sum(token in row_tokens for row_tokens, _ in row_token_sets) for token in tokens}
        rarity = {token: math.log((len(rows) + 1) / (count + .5)) for token, count in frequency.items() if count}
        rarity_total = sum(rarity.values())
        for row, (row_tokens, spelled) in zip(rows, row_token_sets):
            lexical = (sum(rarity.get(token, 0.0) for token in tokens & row_tokens) / rarity_total
                       if rarity_total > 0 else 0.0)
            vector = json.loads(row["vector_json"]) if row["vector_json"] else text_vector(row["content"])
            semantic = cosine_similarity(query_vector, vector)
            neural_compatible = neural_query is not None and row["neural_vector_json"] and row["neural_model"] == getattr(self.embedding_provider, "identifier", None)
            neural = cosine_similarity(neural_query, json.loads(row["neural_vector_json"])) if neural_compatible else semantic
            row_entities = set(re.findall(r"\b[A-Z][A-Za-z0-9_.-]+\b", row["content"]))
            row_entities |= {entity for entity in query_entities if entity.lower() in spelled}
            entity = len(query_entities & row_entities) / max(1, len(query_entities)) if query_entities else 0.0
            try:
                age_days = max(0.0, (datetime.now(UTC) - datetime.fromisoformat(row["timestamp"])).total_seconds() / 86400)
                recency = 1.0 / (1.0 + age_days / 30.0)
            except Exception:
                recency = 0.0
            metadata = json.loads(row["metadata_json"])
            project_score = 1.0 if project and str(metadata.get("project", "")).lower() == project.lower() else 0.0
            confidence = float(metadata.get("confidence", row["importance"]))
            uses = float(metadata.get("successful_uses", 0)); failures = float(metadata.get("failed_uses", 0))
            successful_use = (uses + 1) / (uses + failures + 2)
            if strategy == "lexical":
                score = lexical
            elif strategy == "semantic":
                score = neural
            elif strategy == "hashed_semantic":
                score = semantic
            elif strategy == "hybrid":
                features = {"neural": neural, "hashed_semantic": semantic, "lexical": lexical, "entity": entity, "project": project_score, "recency": recency, "confidence": confidence, "importance": float(row["importance"]), "successful_use": successful_use}
                total_weight = sum(max(0.0, float(value)) for value in score_weights.values()) or 1.0
                score = sum(features[name] * max(0.0, float(score_weights[name])) for name in features) / total_weight
            else:
                raise ValueError(f"Unknown retrieval strategy: {strategy}")
            item = {key: row[key] for key in row.keys() if key not in {"vector_json", "neural_vector_json"}}
            item["metadata"] = metadata
            item["relevance"] = round(score, 6)
            item["retrieval"] = {"strategy": strategy, "neural_available": bool(neural_compatible), "neural": round(neural, 4), "hashed_semantic": round(semantic, 4), "lexical": round(lexical, 4), "entity": round(entity, 4), "project": project_score, "recency": round(recency, 4), "confidence": round(confidence, 4), "successful_use": round(successful_use, 4)}
            item["retrieval"]["query_concepts"] = list(expansion.concepts)
            ranked.append(item)
        ranked = sorted(ranked, key=lambda item: (item["relevance"], item["timestamp"]), reverse=True)
        if tokens:
            ranked = [item for item in ranked if item["relevance"] >= 0.15]
        dna = settings.get("dna", {})
        if strategy == "hybrid" and dna.get("enabled", True) and ranked:
            ranked = self._expand_dna(
                ranked,
                memory_type=memory_type,
                session_id=session_id,
                include_foreign_transient=include_foreign_transient,
                seed_limit=max(1, min(int(dna.get("seed_limit", 4)), 12)),
                max_depth=max(1, min(int(dna.get("max_depth", 2)), 4)),
                max_nodes=max(1, min(int(dna.get("max_nodes", 24)), 200)),
                edge_decay=max(0.1, min(float(dna.get("edge_decay", 0.72)), 1.0)),
                minimum_score=max(0.0, min(float(dna.get("minimum_score", 0.08)), 1.0)),
            )
        elapsed_ms = round((time.perf_counter() - retrieval_started) * 1000, 3)
        for item in ranked:
            item["retrieval"]["latency_ms"] = elapsed_ms
        return ranked[:limit]

    def retrieve_page(self, query: str, *, page: int = 1, page_size: int = 12,
                      memory_type: str | None = None, strategy: str = "hybrid",
                      project: str | None = None, session_id: str | None = None,
                      include_foreign_transient: bool = False) -> dict[str, Any]:
        """Recall one bounded page while allowing the caller to continue.

        A model window is necessarily finite.  Paging keeps the durable store
        searchable beyond that window without loading the whole database into
        RAM or silently pretending all memories fit in one prompt.
        """
        page_number = max(1, int(page))
        size = max(1, min(int(page_size), 100))
        stop = page_number * size
        rows = self.retrieve(
            query, memory_type=memory_type, limit=stop + 1, strategy=strategy,
            project=project, session_id=session_id,
            include_foreign_transient=include_foreign_transient,
        )
        start = (page_number - 1) * size
        items = rows[start:stop]
        has_more = len(rows) > stop
        return {
            "query": query, "page": page_number, "page_size": size,
            "items": items, "returned": len(items), "has_more": has_more,
            "next_page": page_number + 1 if has_more else None,
            "model_window_independent": True,
            "note": "Request next_page to continue recall without expanding one prompt.",
        }

    def verify_memory_integrity(self, *, limit: int = 100_000) -> dict[str, Any]:
        """Read-only integrity audit for durable memory content."""
        bounded = max(1, min(int(limit), 1_000_000))
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,content,content_digest FROM memories ORDER BY id LIMIT ?", (bounded,)
            ).fetchall()
        mismatches = [
            int(row["id"]) for row in rows
            if _memory_digest(str(row["content"])) != str(row["content_digest"] or "")
        ]
        return {
            "checked": len(rows), "valid": len(rows) - len(mismatches),
            "mismatches": mismatches, "tamper_evident": True,
        }

    def _expand_dna(
        self,
        ranked: list[dict[str, Any]],
        *,
        memory_type: str | None,
        session_id: str | None,
        include_foreign_transient: bool,
        seed_limit: int,
        max_depth: int,
        max_nodes: int,
        edge_decay: float,
        minimum_score: float,
    ) -> list[dict[str, Any]]:
        """Expand strong recall seeds through bounded Dynamic Neural Associations."""
        by_id = {int(item["id"]): item for item in ranked}
        discoveries: dict[int, tuple[float, dict[str, Any], int]] = {}
        seeds = ranked[:seed_limit]
        seed_scores = {int(seed["id"]): float(seed["relevance"]) for seed in seeds}
        for association in self.associations_many(
            list(seed_scores), max_depth=max_depth, limit=max_nodes,
        ):
            seed_id = int(association["seed_id"])
            associated_id = int(association["memory_id"])
            score = seed_scores[seed_id] * float(association["path_weight"])
            score *= edge_decay ** int(association["depth"])
            # Association context supports a matching seed; it must not
            # overtake that seed merely because an edge is strong.
            score = min(score, seed_scores[seed_id] * 0.90)
            if score < minimum_score:
                continue
            previous = discoveries.get(associated_id)
            if previous is None or score > previous[0]:
                discoveries[associated_id] = (score, association, seed_id)

        existing_ids = set(by_id)
        missing_ids = [memory_id for memory_id in discoveries if memory_id not in existing_ids]
        if missing_ids:
            placeholders = ",".join("?" for _ in missing_ids)
            with self.connect() as db:
                rows = db.execute(
                    f"{self.SELECT_MEMORY} WHERE m.id IN ({placeholders})", missing_ids
                ).fetchall()
            for row in rows:
                if memory_type and row["memory_type"] != memory_type:
                    continue
                if not include_foreign_transient and excluded_from_conversation(dict(row), session_id=session_id):
                    continue
                memory_id = int(row["id"])
                score, association, seed_id = discoveries[memory_id]
                metadata = json.loads(row["metadata_json"] or "{}")
                item = {key: row[key] for key in row.keys() if key not in {"vector_json", "neural_vector_json"}}
                item["metadata"] = metadata
                item["relevance"] = round(score, 6)
                item["retrieval"] = {
                    "strategy": "dna_associative",
                    "seed_id": seed_id,
                    "relation": association["relation"],
                    "direction": association["direction"],
                    "depth": association["depth"],
                    "path": association["path"],
                    "path_weight": round(float(association["path_weight"]), 6),
                    "contradiction": bool(association["contradiction"]),
                    "evidence": association["evidence"],
                }
                by_id[memory_id] = item

        # Existing direct hits keep their measured hybrid score and strategy,
        # but expose linked context so callers can distinguish support from a
        # contradiction instead of flattening both into one apparent fact.
        for memory_id, (_, association, seed_id) in discoveries.items():
            if memory_id in existing_ids:
                dna_score = discoveries[memory_id][0]
                direct_score = float(by_id[memory_id]["relevance"])
                if dna_score > direct_score:
                    by_id[memory_id]["relevance"] = round(dna_score, 6)
                    by_id[memory_id]["retrieval"]["dna_boost"] = round(dna_score - direct_score, 6)
                links = by_id[memory_id]["retrieval"].setdefault("associations", [])
                links.append({
                    "seed_id": seed_id, "relation": association["relation"],
                    "direction": association["direction"],
                    "depth": association["depth"], "path": association["path"],
                    "path_weight": round(float(association["path_weight"]), 6),
                    "contradiction": bool(association["contradiction"]),
                    "evidence": association["evidence"],
                })
        return sorted(by_id.values(), key=lambda item: (item["relevance"], item["timestamp"]), reverse=True)

    def reindex_embeddings(self, *, force: bool = False, batch_size: int = 32) -> dict[str, Any]:
        provider = self.embedding_provider
        if provider is None or not provider.available:
            return {"available": False, "indexed": 0, "skipped": 0, "reason": "No configured local embedding provider is available."}
        with self.connect() as db:
            rows = db.execute("SELECT id,content,memory_type FROM memories WHERE memory_type IN ('semantic','episodic','procedural','learning','project') ORDER BY id").fetchall()
            existing = {int(row["memory_id"]): dict(row) for row in db.execute("SELECT memory_id,model,embedding_version,content_hash FROM memory_neural_vectors").fetchall()}
        pending = []
        skipped = 0
        for row in rows:
            digest = content_hash(row["content"])
            cached = existing.get(int(row["id"]))
            if not force and cached and cached["model"] == provider.identifier and cached["embedding_version"] == provider.version and cached["content_hash"] == digest:
                skipped += 1
                continue
            pending.append((int(row["id"]), row["content"], digest))
        indexed = 0
        for offset in range(0, len(pending), max(1, batch_size)):
            batch = pending[offset:offset + batch_size]
            vectors = provider.embed_documents([item[1] for item in batch])
            with self.connect() as db:
                db.executemany(
                    "INSERT INTO memory_neural_vectors(memory_id,model,embedding_version,content_hash,created_at,vector_json) VALUES(?,?,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET model=excluded.model,embedding_version=excluded.embedding_version,content_hash=excluded.content_hash,created_at=excluded.created_at,vector_json=excluded.vector_json",
                    [(item[0], provider.identifier, provider.version, item[2], now_iso(), json.dumps(vector)) for item, vector in zip(batch, vectors, strict=True)],
                )
            indexed += len(batch)
        return {"available": True, "provider": provider.identifier, "dimension": provider.dimension, "indexed": indexed, "skipped": skipped, "total": len(rows), "force": force}

    def _cached_embedding(self, content: str) -> list[float] | None:
        provider = self.embedding_provider
        digest = content_hash(content)
        with self.connect() as db:
            row = db.execute("SELECT vector_json FROM embedding_cache WHERE provider=? AND model=? AND embedding_version=? AND content_hash=?", (provider.name, provider.model, provider.version, digest)).fetchone()
        if row:
            return json.loads(row["vector_json"])
        vector = optional_embedding(lambda: provider.embed_documents([content])[0])
        if vector is None:
            return None
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO embedding_cache(provider,model,embedding_version,content_hash,dimension,created_at,vector_json) VALUES(?,?,?,?,?,?,?)", (provider.name, provider.model, provider.version, digest, len(vector), now_iso(), json.dumps(vector)))
        return vector

    def record_message(self, session_id: str, role: str, content: str, model: str | None = None, metadata: dict[str, Any] | None = None) -> int:
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"Unsupported conversation role: {role}")
        from app.safety.sensitive import redact as redact_sensitive, redact_text

        content, _ = redact_text(content)
        metadata = redact_sensitive(metadata) if metadata else metadata
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO conversations(session_id,timestamp,role,content,model,metadata_json) VALUES(?,?,?,?,?,?)",
                (session_id, now_iso(), role, content, model, json.dumps(metadata or {}, default=str)),
            )
            return int(cursor.lastrowid)

    def conversation_history(self, session_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM (SELECT id,timestamp,role,content,model,metadata_json FROM conversations WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (session_id, limit),
            ).fetchall()
        return [dict(row) | {"metadata": json.loads(row["metadata_json"])} for row in rows]

    def latest_session(self, max_age_seconds: float | None = None) -> dict[str, Any] | None:
        """The most recently active conversation, for resuming a thread.

        A new run mints a fresh session id and so starts a blank thread even
        though the last conversation is sitting on disk -- which is how "what
        were we talking about?" gets lost across a shutdown. This finds the
        session id of the newest recorded message so the runtime can adopt it
        and carry the thread over.

        Returns None when there is nothing to resume, or -- when a max age is
        given -- when the newest conversation is older than it. A thread from
        last month should not silently reopen as if no time had passed; a
        recency window keeps "resume" meaning "pick up where we just were".
        """
        with self.connect() as db:
            row = db.execute(
                "SELECT session_id, timestamp FROM conversations ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row or not row["session_id"]:
            return None
        if max_age_seconds is not None:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(row["timestamp"])).total_seconds()
            except (ValueError, TypeError):
                return None
            if age < 0 or age > max_age_seconds:
                return None
        return {"session_id": row["session_id"], "last_active": row["timestamp"]}

    def recent_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id,created_at,updated_at,goal,status,outcome FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as db:
            tasks = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            memories = db.execute("SELECT memory_type, COUNT(*) count FROM memories GROUP BY memory_type").fetchall()
            actions = db.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
            conversations = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            associations = db.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]
        return {"database": str(self.path), "tasks": tasks, "actions": actions, "conversations": conversations, "memories": {row[0]: row[1] for row in memories}, "associations": associations}

    def health(self) -> dict[str, Any]:
        """Report durable-memory and recall-index coverage without changing data."""
        with self.connect() as db:
            integrity = str(db.execute("PRAGMA quick_check").fetchone()[0])
            memories = int(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
            hashed = int(db.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0])
            neural = int(db.execute("SELECT COUNT(*) FROM memory_neural_vectors").fetchone()[0])
            associations = int(db.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0])
            contradiction_links = int(db.execute(
                "SELECT COUNT(*) FROM memory_links WHERE relation='contradicts'"
            ).fetchone()[0])
            fts = bool(db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'"
            ).fetchone())
            fts_rows = int(db.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]) if fts else 0
        return {
            "healthy": integrity == "ok" and hashed >= memories and (not fts or fts_rows >= memories),
            "integrity": integrity, "memories": memories,
            "hashed_vectors": hashed, "neural_vectors": neural,
            "dna_associations": associations, "contradiction_links": contradiction_links,
            "fts_available": fts, "fts_rows": fts_rows,
            "missing_hashed_vectors": max(0, memories - hashed),
            "missing_fts_rows": max(0, memories - fts_rows) if fts else memories,
        }

    def maintain_indexes(self) -> dict[str, Any]:
        """Rebuild derived recall indexes; never delete or rewrite memories."""
        with self.connect() as db:
            self._initialize_full_text_search(db)
            missing = db.execute(
                "SELECT id,content FROM memories WHERE id NOT IN "
                "(SELECT memory_id FROM memory_vectors)"
            ).fetchall()
            db.executemany(
                "INSERT OR IGNORE INTO memory_vectors(memory_id,vector_json) VALUES(?,?)",
                [(row["id"], json.dumps(text_vector(row["content"]))) for row in missing],
            )
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'").fetchone():
                db.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")
        return {"maintained": True, "hashed_vectors_added": len(missing), "health": self.health()}

    def consolidation_report(self) -> dict[str, Any]:
        """Identify safe consolidation candidates without silently deleting memory."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,timestamp,memory_type,content,source,metadata_json FROM memories ORDER BY id"
            ).fetchall()
        groups: dict[tuple[str, str], list[int]] = {}
        for row in rows:
            normalized = " ".join(tokenize(row["content"]))
            groups.setdefault((row["memory_type"], normalized), []).append(int(row["id"]))
        duplicates = [
            {"memory_type": key[0], "normalized_content": key[1][:240], "ids": ids, "keep_id": ids[-1]}
            for key, ids in groups.items() if key[1] and len(ids) > 1
        ]
        return {
            "mode": "report_only",
            "memories_scanned": len(rows),
            "exact_duplicate_groups": duplicates,
            "duplicate_records": sum(len(item["ids"]) - 1 for item in duplicates),
            "records_deleted": 0,
            "note": "No memory was modified. Contradictions require evidence-aware review and are not auto-merged.",
        }


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _memory_digest(content: str) -> str:
    """Exact SHA-256 digest used to quarantine modified durable content."""
    return hashlib.sha256(str(content).encode("utf-8", "surrogatepass")).hexdigest()


def _trusted_for_automatic_links(source: str, metadata: dict[str, Any] | None) -> bool:
    """Keep unverified external material from autonomously shaping DNA edges."""
    provenance = str(source or "").strip().casefold()
    attributes = metadata or {}
    if attributes.get("verified") is True or attributes.get("trusted") is True:
        return True
    untrusted = {
        "web", "browser", "research", "crawler", "external",
        "background_learner", "untrusted_external_content",
        "attachment", "document", "video",
    }
    return provenance not in untrusted


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) > 1]


def text_vector(text: str, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / magnitude for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))
