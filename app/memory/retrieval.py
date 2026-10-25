from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


from app.config import PROJECT_ROOT, load_settings
from app.linguistics import expand_query
from app.memory.database import cosine_similarity, text_vector, tokenize
from app.memory.embeddings import EmbeddingProvider, content_hash, normalized_content, optional_embedding


SECRET_PATTERN = re.compile(r"(?i)(?:api[_-]?key|password|passwd|secret|token|private[_-]?key|authorization)\s*[:=]\s*\S+")

# Retrieval eligibility now also uses the shared detector, so credential shapes
# with no key prefix (bare tokens, private-key blocks, JWTs) are excluded too.
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".py", ".ps1"}


class RetrievalIndex:
    """Lightweight cross-object index: SQLite metadata plus versioned local vectors."""

    def __init__(self, database, provider: EmbeddingProvider | None, *, project_root: Path = PROJECT_ROOT) -> None:
        self.database = database
        self.provider = provider
        self.project_root = project_root
        self.reranker_enabled = bool(load_settings().get("memory_retrieval", {}).get("reranker_enabled", False))
        self.initialize()

    def initialize(self) -> None:
        with self.database.connect() as db:
            existed = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='retrieval_objects_fts'"
            ).fetchone()
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS retrieval_objects(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_type TEXT NOT NULL, source_id TEXT NOT NULL, project TEXT,
                    title TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5, importance REAL NOT NULL DEFAULT 0.5,
                    successful_uses INTEGER NOT NULL DEFAULT 0, failed_uses INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(object_type,source_id)
                );
                CREATE INDEX IF NOT EXISTS idx_retrieval_objects_type ON retrieval_objects(object_type,project);
                CREATE TABLE IF NOT EXISTS retrieval_object_vectors(
                    object_id INTEGER PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
                    embedding_version TEXT NOT NULL, content_hash TEXT NOT NULL, dimension INTEGER NOT NULL,
                    created_at TEXT NOT NULL, vector_json TEXT NOT NULL,
                    FOREIGN KEY(object_id) REFERENCES retrieval_objects(id) ON DELETE CASCADE
                );
                """
            )
            try:
                db.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_objects_fts USING fts5(
                        title,content,
                        content='retrieval_objects',
                        content_rowid='id',
                        tokenize='unicode61 remove_diacritics 2'
                    );
                    CREATE TRIGGER IF NOT EXISTS retrieval_fts_insert
                    AFTER INSERT ON retrieval_objects BEGIN
                        INSERT INTO retrieval_objects_fts(rowid,title,content)
                        VALUES (new.id,new.title,new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS retrieval_fts_delete
                    AFTER DELETE ON retrieval_objects BEGIN
                        INSERT INTO retrieval_objects_fts(
                            retrieval_objects_fts,rowid,title,content
                        ) VALUES ('delete',old.id,old.title,old.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS retrieval_fts_update
                    AFTER UPDATE OF title,content ON retrieval_objects BEGIN
                        INSERT INTO retrieval_objects_fts(
                            retrieval_objects_fts,rowid,title,content
                        ) VALUES ('delete',old.id,old.title,old.content);
                        INSERT INTO retrieval_objects_fts(rowid,title,content)
                        VALUES (new.id,new.title,new.content);
                    END;
                    """
                )
                if not existed:
                    db.execute(
                        "INSERT INTO retrieval_objects_fts(retrieval_objects_fts) "
                        "VALUES ('rebuild')"
                    )
            except sqlite3.OperationalError:
                # MemoryDatabase has the same compatibility path: FTS5 is
                # standard on supported Windows Python builds, but retrieval
                # remains functional on custom SQLite builds without it.
                pass

    def upsert(self, object_type: str, source_id: str, title: str, content: str, *, project: str | None = None, confidence: float = .5, importance: float = .5, metadata: dict[str, Any] | None = None) -> bool:
        clean = normalized_content(content)
        if not self._eligible(clean):
            return False
        digest = content_hash(clean)
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as db:
            row = db.execute("SELECT id,content_hash FROM retrieval_objects WHERE object_type=? AND source_id=?", (object_type, source_id)).fetchone()
            db.execute(
                "INSERT INTO retrieval_objects(object_type,source_id,project,title,content,content_hash,confidence,importance,updated_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(object_type,source_id) DO UPDATE SET project=excluded.project,title=excluded.title,content=excluded.content,content_hash=excluded.content_hash,confidence=excluded.confidence,importance=excluded.importance,updated_at=excluded.updated_at,metadata_json=excluded.metadata_json",
                (object_type, source_id, project, title[:300], clean, digest, max(0, min(1, confidence)), max(0, min(1, importance)), now, json.dumps(metadata or {}, default=str)),
            )
            object_id = int(db.execute("SELECT id FROM retrieval_objects WHERE object_type=? AND source_id=?", (object_type, source_id)).fetchone()[0])
        changed = row is None or row["content_hash"] != digest
        if changed:
            vector = optional_embedding(lambda: self.provider.embed_documents([clean])[0]) if self.provider and self.provider.available else None
            with self.database.connect() as db:
                if vector is None:
                    db.execute("DELETE FROM retrieval_object_vectors WHERE object_id=?", (object_id,))
                else:
                    db.execute(
                        "INSERT INTO retrieval_object_vectors(object_id,provider,model,embedding_version,content_hash,dimension,created_at,vector_json) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(object_id) DO UPDATE SET provider=excluded.provider,model=excluded.model,embedding_version=excluded.embedding_version,content_hash=excluded.content_hash,dimension=excluded.dimension,created_at=excluded.created_at,vector_json=excluded.vector_json",
                        (object_id, self.provider.name, self.provider.model, self.provider.version, digest, len(vector), now, json.dumps(vector)),
                    )
        return changed

    def sync(self, *, knowledge_path: Path | None = None, learning_path: Path | None = None, document_limit: int = 100) -> dict[str, Any]:
        counts: dict[str, int] = {}
        with self.database.connect() as db:
            memories = db.execute("SELECT id,memory_type,content,importance,source,metadata_json FROM memories WHERE memory_type IN ('semantic','episodic','procedural','learning','project')").fetchall()
            procedures = db.execute("SELECT id,name,description,definition_yaml,confidence,trusted FROM procedures").fetchall()
        for row in memories:
            changed = self.upsert(f"memory:{row['memory_type']}", str(row["id"]), row["source"], row["content"], confidence=float(row["importance"]), importance=float(row["importance"]), metadata=json.loads(row["metadata_json"]))
            counts["memories"] = counts.get("memories", 0) + int(changed)
        for row in procedures:
            changed = self.upsert("procedure", str(row["id"]), row["name"], f"{row['description']}\n{row['definition_yaml']}", confidence=float(row["confidence"]), importance=.8 if row["trusted"] else .5, metadata={"trusted": bool(row["trusted"])})
            counts["procedures"] = counts.get("procedures", 0) + int(changed)
        self._sync_external_sqlite(knowledge_path or self.project_root / "data/knowledge/knowledge.db", learning_path or self.project_root / "data/memory/learning.db", counts)
        for root, object_type in ((self.project_root / "docs", "project_document"), (self.project_root / "data/skills", "skill")):
            if not root.is_dir():
                continue
            for path in list(self._documents(root))[:document_limit]:
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                changed = self.upsert(object_type, str(path.resolve()), path.name, content, project=self.project_root.name, importance=.65)
                counts[object_type] = counts.get(object_type, 0) + int(changed)
        with self.database.connect() as db:
            total = int(db.execute("SELECT COUNT(*) FROM retrieval_objects").fetchone()[0])
            vectors = int(db.execute("SELECT COUNT(*) FROM retrieval_object_vectors").fetchone()[0])
        return {"updated": counts, "objects": total, "vectors": vectors, "embedding": self.provider.identifier if self.provider and self.provider.available else None}

    def reindex(self, *, force: bool = False, batch_size: int = 32) -> dict[str, Any]:
        if not self.provider or not self.provider.available:
            return {"available": False, "indexed": 0}
        with self.database.connect() as db:
            rows = db.execute("SELECT o.*,v.model,v.provider,v.embedding_version,v.content_hash vector_hash FROM retrieval_objects o LEFT JOIN retrieval_object_vectors v ON v.object_id=o.id ORDER BY o.id").fetchall()
        pending = [row for row in rows if force or row["model"] != self.provider.model or row["provider"] != self.provider.name or row["embedding_version"] != self.provider.version or row["vector_hash"] != row["content_hash"]]
        indexed = 0
        for offset in range(0, len(pending), max(1, batch_size)):
            batch = pending[offset:offset + batch_size]
            vectors = self.provider.embed_documents([row["content"] for row in batch])
            now = datetime.now(UTC).isoformat()
            with self.database.connect() as db:
                db.executemany("INSERT INTO retrieval_object_vectors(object_id,provider,model,embedding_version,content_hash,dimension,created_at,vector_json) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(object_id) DO UPDATE SET provider=excluded.provider,model=excluded.model,embedding_version=excluded.embedding_version,content_hash=excluded.content_hash,dimension=excluded.dimension,created_at=excluded.created_at,vector_json=excluded.vector_json", [(row["id"], self.provider.name, self.provider.model, self.provider.version, row["content_hash"], len(vector), now, json.dumps(vector)) for row, vector in zip(batch, vectors, strict=True)])
            indexed += len(batch)
        return {"available": True, "indexed": indexed, "skipped": len(rows) - indexed, "total": len(rows), "force": force, "model": self.provider.identifier}

    def search(self, query: str, limit: int = 8, *, strategy: str = "hybrid", project: str | None = None) -> list[dict[str, Any]]:
        started = time.perf_counter()
        expansion = expand_query(query)
        query_tokens = set(tokenize(expansion.text)); query_vector = text_vector(expansion.text)
        candidate_limit = max(
            100, int(load_settings().get("memory_retrieval", {}).get("candidate_limit", 500))
        )
        rows = self._candidates(query_tokens, candidate_limit)
        neural_query = optional_embedding(lambda: self.provider.embed_query(query)) if self.provider and self.provider.available else None
        ranked = []
        for row in rows:
            tokens = set(tokenize(expand_query(row["content"] + " " + row["title"]).text))
            lexical = len(query_tokens & tokens) / max(1, len(query_tokens))
            hashed = cosine_similarity(query_vector, text_vector(row["content"]))
            neural = cosine_similarity(neural_query, json.loads(row["vector_json"])) if neural_query is not None and row["vector_json"] else hashed
            project_score = 1.0 if project and str(row["project"] or "").lower() == project.lower() else 0.0
            usage = (row["successful_uses"] + 1) / (row["successful_uses"] + row["failed_uses"] + 2)
            if strategy == "lexical": score = lexical
            elif strategy == "semantic": score = neural
            else: score = neural * .45 + lexical * .25 + hashed * .08 + project_score * .08 + row["confidence"] * .06 + row["importance"] * .05 + usage * .03
            item = dict(row); item.pop("vector_json", None)
            item["relevance"] = round(float(score), 6)
            item["retrieval"] = {"strategy": strategy, "neural": round(float(neural), 4), "lexical": round(lexical, 4), "hashed": round(hashed, 4), "project": project_score, "query_concepts": list(expansion.concepts)}
            ranked.append(item)
        result = sorted(ranked, key=lambda item: (item["relevance"], item["updated_at"]), reverse=True)[:limit]
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        for item in result:
            item["retrieval"]["latency_ms"] = elapsed_ms
        return self._rerank(query, result) if self.reranker_enabled else result

    def _candidates(self, tokens: set[str], candidate_limit: int) -> list[sqlite3.Row]:
        """Use full-text hits plus small importance/recency safety nets.

        This index contains memories, research claims, learned notes, project
        documents and skills. Scanning the newest 1,000 rows made an older
        document disappear and made recall slower as the store grew.
        """
        select = (
            "SELECT o.*,v.vector_json FROM retrieval_objects o "
            "LEFT JOIN retrieval_object_vectors v ON v.object_id=o.id"
        )
        collected: dict[int, sqlite3.Row] = {}

        def absorb(found: Iterable[sqlite3.Row]) -> None:
            for row in found:
                collected.setdefault(int(row["id"]), row)

        terms = sorted(token for token in tokens if len(token) >= 3)[:12]
        with self.database.connect() as db:
            fts = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='retrieval_objects_fts'"
            ).fetchone()
            if terms and fts:
                match = " OR ".join(f'"{term}"' for term in terms)
                try:
                    absorb(db.execute(
                        "SELECT o.*,v.vector_json FROM retrieval_objects o "
                        "JOIN retrieval_objects_fts f ON f.rowid=o.id "
                        "LEFT JOIN retrieval_object_vectors v ON v.object_id=o.id "
                        "WHERE retrieval_objects_fts MATCH ? "
                        "ORDER BY bm25(retrieval_objects_fts),o.importance DESC "
                        "LIMIT ?",
                        (match, candidate_limit),
                    ).fetchall())
                except sqlite3.OperationalError:
                    fts = None
            if terms and not fts:
                clause = " OR ".join(
                    "(o.title LIKE ? ESCAPE '\\' OR o.content LIKE ? ESCAPE '\\')"
                    for _ in terms
                )
                patterns = [
                    "%" + term.replace("%", r"\%").replace("_", r"\_") + "%"
                    for term in terms
                ]
                values = [value for pattern in patterns for value in (pattern, pattern)]
                absorb(db.execute(
                    f"{select} WHERE {clause} ORDER BY o.importance DESC LIMIT ?",
                    [*values, candidate_limit],
                ).fetchall())
            safety_limit = max(50, candidate_limit // 4)
            absorb(db.execute(
                f"{select} ORDER BY o.importance DESC,o.updated_at DESC LIMIT ?",
                (safety_limit,),
            ).fetchall())
            absorb(db.execute(
                f"{select} ORDER BY o.updated_at DESC LIMIT ?",
                (safety_limit,),
            ).fetchall())
        return list(collected.values())

    def _rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Deliberately conservative deterministic reranker; disabled until benchmarked better than hybrid.
        query_tokens = set(tokenize(query))
        for item in candidates:
            title_overlap = len(query_tokens & set(tokenize(item["title"]))) / max(1, len(query_tokens))
            item["rerank_score"] = round(item["relevance"] * .85 + title_overlap * .15, 6)
        return sorted(candidates, key=lambda item: item["rerank_score"], reverse=True)

    def _sync_external_sqlite(self, knowledge_path: Path, learning_path: Path, counts: dict[str, int]) -> None:
        if knowledge_path.is_file():
            with closing(sqlite3.connect(knowledge_path)) as db:
                db.row_factory = sqlite3.Row
                for row in db.execute("SELECT id,claim_text,confidence,status,topic FROM claims WHERE status IN ('SUPPORTED','PARTIALLY_SUPPORTED','DISPUTED')"):
                    counts["research_claims"] = counts.get("research_claims", 0) + int(self.upsert("research_claim", str(row["id"]), row["topic"] or "research claim", row["claim_text"], confidence=float(row["confidence"]), importance=.75, metadata={"status": row["status"]}))
                for row in db.execute("SELECT id,query,content,confidence,status FROM knowledge WHERE status='active'"):
                    counts["knowledge"] = counts.get("knowledge", 0) + int(self.upsert("knowledge_summary", str(row["id"]), row["query"], row["content"], confidence=float(row["confidence"]), importance=.75))
        if learning_path.is_file():
            with closing(sqlite3.connect(learning_path)) as db:
                db.row_factory = sqlite3.Row
                for row in db.execute("SELECT id,topic,event_type,score,details_json FROM learning_events"):
                    content = f"{row['topic']} {row['event_type']} {row['details_json']}"
                    counts["learning_notes"] = counts.get("learning_notes", 0) + int(self.upsert("learning_note", str(row["id"]), row["topic"], content, confidence=float(row["score"] or .5), importance=.65))

    @staticmethod
    def _documents(root: Path) -> Iterable[Path]:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and path.name.lower() != ".env":
                try:
                    if path.stat().st_size <= 64_000:
                        yield path
                except OSError:
                    continue

    @staticmethod
    def _eligible(content: str) -> bool:
        from app.safety.sensitive import scan

        if not 20 <= len(content) <= 64_000:
            return False
        return not SECRET_PATTERN.search(content) and not scan(content)
