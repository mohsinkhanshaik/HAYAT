from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app.config import PROJECT_ROOT
from app.memory.database import cosine_similarity, text_vector
from app.research.freshness import is_stale
from app.research.models import Claim, ClaimStatus, ClaimType, Evidence, ResearchResult, SourceDocument, SupportType
from app.research.text import extract_entities, jaccard, token_set
from app.research.verifier import EvidenceVerifier


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


#: Words that appear in every question and identify none of them. Dropped
#: before comparing, so "explain me in detail about X" and "tell me about X"
#: are recognised as the same question about X.
_ASKING_WORDS = frozenset({
    "explain", "tell", "show", "describe", "detail", "detailed", "about",
    "please", "can", "could", "would", "what", "whats", "which", "how", "why",
    "when", "where", "who", "give", "want", "know", "understand", "learn",
    "more", "some", "thing", "things", "stuff", "really", "actually", "just",
    "also", "even", "very", "much", "many", "make", "made", "does", "did",
})


def _stem(word: str) -> str:
    for suffix in ("ies", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _subject_tokens(text: str) -> set[str]:
    """What a question is actually about, stripped of how it was asked.

    Stemmed, because "tax return" and "tax returns" are the same subject and
    a plural should not be the difference between reusing an answer and
    searching the web for it again.
    """
    from app.research.text import token_set

    # Hyphens split, because "prompt-injection defenses for tool-using agents"
    # and "prompt injection defenses for tool using agents" are the same
    # question and were not recognised as one.
    flattened = str(text or "").replace("-", " ").replace("/", " ")
    return {_stem(word) for word in token_set(flattened)
            if word not in _ASKING_WORDS and len(word) > 2}


def answer_still_acceptable(content: str) -> bool:
    """Whether a stored answer would pass today's standards.

    Reuse only helps if what was learned was worth learning. The store holds
    answers written before claims had to be assertions, and the top line of
    the one for taxation is "Is the willful failure to file a return, by
    itself, enough to constitute the felony of tax evasion?" -- a question,
    filed as a validated claim.

    Serving that instantly is worse than searching again slowly, so an entry
    that today's extractor would have rejected is skipped and the question is
    researched afresh. Nothing is deleted: a better answer supersedes it on
    the next run.
    """
    body = str(content or "")
    if not body.strip():
        return False
    bullets = [line.strip(" -•\t") for line in body.splitlines()
               if line.strip().startswith(("-", "•"))]
    if not bullets:
        return True
    from app.research.claims import is_assertion

    good = sum(is_assertion(line) for line in bullets)
    # Half is generous. It only has to be better than a coin flip to be worth
    # keeping, and anything worse was never an answer.
    return good * 2 >= len(bullets)


def answer_covers(query: str, content: str) -> bool:
    """Whether a stored answer addresses everything the question asks.

    Reuse has to clear the same bar a fresh answer does. Asked to explain
    taxation, tax evasion and tax returns, the store had a 604-character reply
    consisting of one bullet about a late-filing penalty -- and serving that
    in three milliseconds is not learning, it is being wrong faster.
    """
    try:
        from app.answers import assess, required_topics

        topics = required_topics(query)
        if not topics:
            return True
        return not assess(content, question=query, topics=topics).missing
    except Exception:
        return True


class KnowledgeStore:
    def __init__(self, path: str | Path = "data/knowledge/knowledge.db") -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = PROJECT_ROOT / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_runs(
                    id TEXT PRIMARY KEY, query TEXT NOT NULL, started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL, level TEXT NOT NULL, complete INTEGER NOT NULL,
                    stop_reason TEXT NOT NULL, confidence REAL NOT NULL, answer TEXT NOT NULL,
                    metrics_json TEXT NOT NULL, errors_json TEXT NOT NULL, trace_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS sources(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_url TEXT NOT NULL,
                    final_url TEXT NOT NULL, title TEXT NOT NULL, domain TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL, published_at TEXT, updated_at TEXT,
                    content_hash TEXT NOT NULL, content_type TEXT NOT NULL, quality REAL NOT NULL,
                    metadata_json TEXT NOT NULL, excerpt TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'web_page', author TEXT, primary_source INTEGER NOT NULL DEFAULT 0,
                    independence_group TEXT NOT NULL DEFAULT '', lineage_json TEXT NOT NULL DEFAULT '[]', version TEXT,
                    UNIQUE(canonical_url, content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_sources_url ON sources(canonical_url);
                CREATE INDEX IF NOT EXISTS idx_sources_domain ON sources(domain);
                CREATE TABLE IF NOT EXISTS run_sources(
                    run_id TEXT NOT NULL, source_id INTEGER NOT NULL, rank INTEGER NOT NULL,
                    PRIMARY KEY(run_id, source_id),
                    FOREIGN KEY(run_id) REFERENCES research_runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES sources(id)
                );
                CREATE TABLE IF NOT EXISTS knowledge(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, query TEXT NOT NULL, content TEXT NOT NULL,
                    confidence REAL NOT NULL, freshness_class TEXT NOT NULL,
                    valid_until TEXT, status TEXT NOT NULL DEFAULT 'active',
                    run_id TEXT NOT NULL, vector_json TEXT NOT NULL,
                    retrieval_count INTEGER NOT NULL DEFAULT 0, successful_uses INTEGER NOT NULL DEFAULT 0,
                    failed_uses INTEGER NOT NULL DEFAULT 0, last_used TEXT, last_verified TEXT,
                    content_hash TEXT NOT NULL DEFAULT '', supersedes_id INTEGER,
                    resolution TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(run_id) REFERENCES research_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge(status, updated_at);
                CREATE TABLE IF NOT EXISTS claims(
                    id TEXT PRIMARY KEY, claim_text TEXT NOT NULL, claim_type TEXT NOT NULL,
                    confidence REAL NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, valid_from TEXT, valid_to TEXT, valid_until TEXT,
                    topic TEXT NOT NULL, entities_json TEXT NOT NULL, keywords_json TEXT NOT NULL,
                    scope TEXT NOT NULL, run_id TEXT NOT NULL, vector_json TEXT NOT NULL,
                    retrieval_count INTEGER NOT NULL DEFAULT 0, successful_uses INTEGER NOT NULL DEFAULT 0,
                    failed_uses INTEGER NOT NULL DEFAULT 0, last_used TEXT, last_verified TEXT,
                    FOREIGN KEY(run_id) REFERENCES research_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status, updated_at);
                CREATE TABLE IF NOT EXISTS claim_history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL, changed_at TEXT NOT NULL,
                    old_status TEXT, new_status TEXT NOT NULL, old_confidence REAL, new_confidence REAL NOT NULL,
                    reason TEXT NOT NULL, run_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence(
                    id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, source_id INTEGER NOT NULL,
                    excerpt TEXT NOT NULL, location TEXT NOT NULL, retrieved_at TEXT NOT NULL,
                    support_type TEXT NOT NULL, strength REAL NOT NULL, stance TEXT NOT NULL,
                    FOREIGN KEY(claim_id) REFERENCES claims(id), FOREIGN KEY(source_id) REFERENCES sources(id)
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_id);
                CREATE TABLE IF NOT EXISTS claim_conflicts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT NOT NULL, conflicting_claim_id TEXT NOT NULL,
                    detected_at TEXT NOT NULL, reason TEXT NOT NULL,
                    confidence REAL NOT NULL, resolution_status TEXT NOT NULL DEFAULT 'unresolved',
                    resolved_at TEXT, winning_claim_id TEXT, resolution TEXT NOT NULL DEFAULT '',
                    UNIQUE(claim_id, conflicting_claim_id),
                    FOREIGN KEY(claim_id) REFERENCES claims(id),
                    FOREIGN KEY(conflicting_claim_id) REFERENCES claims(id)
                );
                CREATE TABLE IF NOT EXISTS knowledge_meta(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_claim_conflicts_status
                    ON claim_conflicts(resolution_status, detected_at);
                CREATE TABLE IF NOT EXISTS claim_evidence(
                    knowledge_id INTEGER NOT NULL, source_id INTEGER NOT NULL,
                    stance TEXT NOT NULL, excerpt TEXT NOT NULL,
                    PRIMARY KEY(knowledge_id, source_id, stance),
                    FOREIGN KEY(knowledge_id) REFERENCES knowledge(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES sources(id)
                );
                CREATE TABLE IF NOT EXISTS entities(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, normalized_name TEXT NOT NULL,
                    entity_type TEXT NOT NULL DEFAULT 'concept', UNIQUE(normalized_name, entity_type)
                );
                CREATE TABLE IF NOT EXISTS claim_entities(
                    claim_id TEXT NOT NULL, entity_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'subject',
                    PRIMARY KEY(claim_id, entity_id, role), FOREIGN KEY(claim_id) REFERENCES claims(id),
                    FOREIGN KEY(entity_id) REFERENCES entities(id)
                );
                CREATE TABLE IF NOT EXISTS relationships(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, subject_entity_id INTEGER NOT NULL,
                    predicate TEXT NOT NULL, object_entity_id INTEGER NOT NULL, claim_id TEXT NOT NULL,
                    confidence REAL NOT NULL, valid_from TEXT, valid_to TEXT,
                    UNIQUE(subject_entity_id,predicate,object_entity_id,claim_id)
                );
                CREATE TABLE IF NOT EXISTS query_cache(
                    cache_key TEXT PRIMARY KEY, query TEXT NOT NULL, provider TEXT NOT NULL,
                    results_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS source_cache(
                    canonical_url TEXT PRIMARY KEY, source_json TEXT NOT NULL, cached_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, etag TEXT, last_modified TEXT, content_hash TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS source_stats(
                    domain TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0, total_latency_ms INTEGER NOT NULL DEFAULT 0,
                    quality_total REAL NOT NULL DEFAULT 0, rate_limits INTEGER NOT NULL DEFAULT 0,
                    last_failure TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_stats(
                    provider TEXT PRIMARY KEY, searches INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0, total_latency_ms INTEGER NOT NULL DEFAULT 0,
                    results_total INTEGER NOT NULL DEFAULT 0, duplicate_total INTEGER NOT NULL DEFAULT 0,
                    quality_total REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_failures(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL, failure_type TEXT NOT NULL,
                    detail TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS monitored_sources(
                    url TEXT PRIMARY KEY, label TEXT NOT NULL, content_hash TEXT NOT NULL,
                    last_checked TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            self._migrate_columns(db)
            self._backfill_claim_conflicts(db)
            self._retire_invalid_claims(db)
            self._reconcile_stored_conflicts(db)

    @staticmethod
    def _migrate_columns(db: sqlite3.Connection) -> None:
        additions = {
            "research_runs": {"trace_json": "TEXT NOT NULL DEFAULT '{}'"},
            "sources": {
                "source_type": "TEXT NOT NULL DEFAULT 'web_page'", "author": "TEXT",
                "primary_source": "INTEGER NOT NULL DEFAULT 0", "independence_group": "TEXT NOT NULL DEFAULT ''",
                "lineage_json": "TEXT NOT NULL DEFAULT '[]'", "version": "TEXT",
            },
            "knowledge": {
                "retrieval_count": "INTEGER NOT NULL DEFAULT 0", "successful_uses": "INTEGER NOT NULL DEFAULT 0",
                "failed_uses": "INTEGER NOT NULL DEFAULT 0", "last_used": "TEXT", "last_verified": "TEXT",
                "content_hash": "TEXT NOT NULL DEFAULT ''", "supersedes_id": "INTEGER",
                "resolution": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, columns in additions.items():
            existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        for row in db.execute("SELECT id,content FROM knowledge WHERE content_hash='' OR content_hash IS NULL").fetchall():
            db.execute("UPDATE knowledge SET content_hash=? WHERE id=?", (KnowledgeStore._content_hash(row[1]), row[0]))
        db.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_hash ON knowledge(content_hash,status)")

    def store_result(self, result: ResearchResult, level: str) -> int | None:
        completed = now_iso()
        metrics = {name: getattr(result.metrics, name) for name in result.metrics.__slots__}
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO research_runs(id,query,started_at,completed_at,level,complete,stop_reason,confidence,answer,metrics_json,errors_json,trace_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (result.run_id, result.query, result.trace.get("started_at", completed), completed, level, int(result.complete),
                 result.stop_reason, result.confidence, result.answer, json.dumps(metrics), json.dumps(result.errors), json.dumps(result.trace, default=str)),
            )
            source_ids = self._store_sources(db, result)
            self._supersede_reprocessed_document_fragments(db, result, source_ids)
            for claim in result.claims:
                self._store_claim(db, claim, result, source_ids)
            if not result.complete or not result.answer.strip() or not result.sources:
                return None
            freshness_class = result.claims[0].freshness_class if result.claims else "slow_changing"
            valid_until = min((claim.validity_horizon for claim in result.claims if claim.validity_horizon), default=None)
            digest = self._content_hash(result.answer)
            has_conflicts = any(claim.status == ClaimStatus.DISPUTED for claim in result.claims)
            exact = db.execute(
                "SELECT * FROM knowledge WHERE content_hash=? ORDER BY confidence DESC,updated_at DESC LIMIT 1",
                (digest,),
            ).fetchone()
            if exact:
                db.execute(
                    "UPDATE knowledge SET updated_at=?,confidence=MAX(confidence,?),run_id=?,last_verified=?,"
                    "status=?,resolution=?,freshness_class=?,valid_until=? WHERE id=?",
                    (completed, result.confidence, result.run_id, completed,
                     "disputed" if has_conflicts else "active",
                     "Unresolved cross-run claim conflict" if has_conflicts else "exact evidence refresh",
                     freshness_class, valid_until,
                     exact["id"]),
                )
                for claim in result.claims:
                    for evidence in [*claim.evidence, *claim.contradictions]:
                        source_id = source_ids.get(evidence.source_url)
                        if source_id:
                            db.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?)", (exact["id"], source_id, evidence.stance, evidence.excerpt[:2000]))
                return int(exact["id"])
            existing = self._find_similar_knowledge(db, result.query, result.answer)
            status, supersedes_id, resolution = (
                ("disputed", None, "Unresolved cross-run claim conflict")
                if has_conflicts else ("active", None, "")
            )
            if not has_conflicts and existing and float(existing["confidence"]) <= result.confidence:
                supersedes_id = int(existing["id"])
                resolution = "newer or stronger summary superseded the prior version"
                db.execute("UPDATE knowledge SET status='superseded',updated_at=?,resolution=? WHERE id=?", (completed, resolution, existing["id"]))
            elif not has_conflicts and existing:
                status = "superseded"
                resolution = "lower-confidence alternate retained as history"
            cursor = db.execute(
                "INSERT INTO knowledge(created_at,updated_at,query,content,confidence,freshness_class,valid_until,status,run_id,vector_json,last_verified,content_hash,supersedes_id,resolution) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (completed, completed, result.query, result.answer, result.confidence, freshness_class, valid_until, status,
                 result.run_id, json.dumps(text_vector(result.query + " " + result.answer)), completed, digest, supersedes_id, resolution),
            )
            knowledge_id = int(cursor.lastrowid)
            for claim in result.claims:
                for evidence in [*claim.evidence, *claim.contradictions]:
                    source_id = source_ids.get(evidence.source_url)
                    if source_id:
                        db.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?)", (knowledge_id, source_id, evidence.stance, evidence.excerpt[:2000]))
            if status == "active":
                self._mark_older_stale(db, result.query, knowledge_id)
            return knowledge_id

    @staticmethod
    def _content_hash(content: str) -> str:
        normalized = " ".join(str(content).casefold().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _store_sources(self, db: sqlite3.Connection, result: ResearchResult) -> dict[str, int]:
        source_ids: dict[str, int] = {}
        for rank, source in enumerate(result.sources, 1):
            canonical = source.canonical_url or source.final_url
            db.execute(
                "INSERT OR IGNORE INTO sources(canonical_url,final_url,title,domain,retrieved_at,published_at,updated_at,content_hash,content_type,quality,metadata_json,excerpt,source_type,author,primary_source,independence_group,lineage_json,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (canonical, source.final_url, source.title, source.domain, source.retrieved_at, source.published_at,
                 source.updated_at, source.content_hash, source.content_type, source.quality, json.dumps(source.metadata, default=str),
                 source.text[:4000], source.source_type, source.author, int(source.primary), source.independence_group,
                 json.dumps(source.lineage_urls), source.version),
            )
            row = db.execute("SELECT id FROM sources WHERE canonical_url=? AND content_hash=?", (canonical, source.content_hash)).fetchone()
            source_id = int(row[0])
            source_ids[source.final_url] = source_id
            db.execute("INSERT OR IGNORE INTO run_sources(run_id,source_id,rank) VALUES(?,?,?)", (result.run_id, source_id, rank))
        return source_ids

    @staticmethod
    def _supersede_reprocessed_document_fragments(
        db: sqlite3.Connection, result: ResearchResult, source_ids: dict[str, int],
    ) -> None:
        """Retire obsolete fragments when the same document is parsed better.

        Only derived document claims from the identical retained source are
        eligible, and only when they fail today's completeness rules or are a
        strict substring of a newly extracted complete claim. Evidence and
        history remain stored.
        """
        if not str(result.mode).startswith("DOCUMENT") or not source_ids:
            return
        from app.research.claims import is_complete_assertion

        new_text = {" ".join(claim.text.casefold().split()): claim.claim_id for claim in result.claims}
        placeholders = ",".join("?" for _ in source_ids)
        rows = db.execute(
            f"SELECT DISTINCT c.id,c.claim_text,c.status,c.confidence,c.run_id FROM claims c "
            f"JOIN evidence e ON e.claim_id=c.id WHERE e.source_id IN ({placeholders}) "
            "AND c.run_id<>? AND c.run_id LIKE 'document-%' "
            "AND c.status IN ('SUPPORTED','PARTIALLY_SUPPORTED','DISPUTED')",
            [*source_ids.values(), result.run_id],
        ).fetchall()
        for row in rows:
            old = " ".join(str(row["claim_text"]).casefold().split())
            contained = any(old != current and len(old) >= 24 and old in current for current in new_text)
            if is_complete_assertion(str(row["claim_text"])) and not contained:
                continue
            changed = now_iso()
            identifier = str(row["id"])
            db.execute(
                "INSERT INTO claim_history(claim_id,changed_at,old_status,new_status,old_confidence,"
                "new_confidence,reason,run_id) VALUES(?,?,?,?,?,?,?,?)",
                (identifier, changed, str(row["status"]), "SUPERSEDED", float(row["confidence"]),
                 float(row["confidence"]), "Same retained document was reprocessed with stricter extraction",
                 result.run_id),
            )
            db.execute("UPDATE claims SET status='SUPERSEDED',updated_at=? WHERE id=?", (changed, identifier))
            conflicts = db.execute(
                "SELECT id,claim_id,conflicting_claim_id FROM claim_conflicts "
                "WHERE resolution_status='unresolved' AND (claim_id=? OR conflicting_claim_id=?)",
                (identifier, identifier),
            ).fetchall()
            for conflict in conflicts:
                other = str(conflict["conflicting_claim_id"] if conflict["claim_id"] == identifier else conflict["claim_id"])
                winner = other if other in new_text.values() else None
                db.execute(
                    "UPDATE claim_conflicts SET resolution_status='resolved',resolved_at=?,"
                    "winning_claim_id=?,resolution=? WHERE id=?",
                    (changed, winner, "Obsolete fragment retired after same-source reprocessing", int(conflict["id"])),
                )
                if winner:
                    db.execute(
                        "UPDATE claims SET status=CASE WHEN EXISTS(SELECT 1 FROM evidence "
                        "WHERE claim_id=? AND support_type='DIRECT') THEN 'SUPPORTED' ELSE 'PARTIALLY_SUPPORTED' END,"
                        "updated_at=? WHERE id=?", (winner, changed, winner),
                    )

    def _store_claim(self, db: sqlite3.Connection, claim: Claim, result: ResearchResult, source_ids: dict[str, int]) -> None:
        similar = self._find_similar_claim(db, claim)
        claim_id = claim.claim_id
        if similar:
            claim_id = str(similar["id"])
            old_status, old_confidence = str(similar["status"]), float(similar["confidence"])
            stronger = claim.confidence >= old_confidence or claim.updated_at > str(similar["updated_at"])
            if stronger:
                db.execute(
                    "UPDATE claims SET claim_text=?,claim_type=?,confidence=?,status=?,updated_at=?,valid_from=?,valid_to=?,valid_until=?,topic=?,entities_json=?,keywords_json=?,scope=?,run_id=?,vector_json=?,last_verified=? WHERE id=?",
                    (claim.text, claim.claim_type.value, claim.confidence, claim.status.value, claim.updated_at, claim.valid_from,
                     claim.valid_to, claim.validity_horizon, result.query, json.dumps(claim.entities), json.dumps(claim.keywords),
                     claim.scope, result.run_id, json.dumps(text_vector(claim.text)), now_iso(), claim_id),
                )
                db.execute("INSERT INTO claim_history(claim_id,changed_at,old_status,new_status,old_confidence,new_confidence,reason,run_id) VALUES(?,?,?,?,?,?,?,?)",
                           (claim_id, now_iso(), old_status, claim.status.value, old_confidence, claim.confidence, "Newer or stronger evidence merged", result.run_id))
        else:
            self._supersede_temporal_conflicts(db, claim, result.run_id)
            # claim_id is a stable hash of the claim sentence, so researching
            # the same topic twice regenerates the same id. The similarity
            # search above is vector-based and can miss that exact match, which
            # dropped us here and made the INSERT collide with the row from the
            # earlier run -- an IntegrityError that aborted the whole research
            # call *after* the answer had already been assembled. Re-observing a
            # claim is corroboration, not an error: refresh its verification
            # timestamp and keep the stronger confidence.
            db.execute(
                "INSERT INTO claims(id,claim_text,claim_type,confidence,status,created_at,updated_at,valid_from,valid_to,valid_until,topic,entities_json,keywords_json,scope,run_id,vector_json,last_verified) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " confidence=MAX(confidence,excluded.confidence), status=excluded.status,"
                " updated_at=excluded.updated_at, run_id=excluded.run_id, last_verified=excluded.last_verified,"
                " valid_until=excluded.valid_until, valid_from=excluded.valid_from, valid_to=excluded.valid_to,"
                " scope=excluded.scope, topic=excluded.topic, entities_json=excluded.entities_json,"
                " keywords_json=excluded.keywords_json, vector_json=excluded.vector_json",
                (claim_id, claim.text, claim.claim_type.value, claim.confidence, claim.status.value, claim.created_at or now_iso(),
                 claim.updated_at or now_iso(), claim.valid_from, claim.valid_to, claim.validity_horizon, result.query,
                 json.dumps(claim.entities), json.dumps(claim.keywords), claim.scope, result.run_id, json.dumps(text_vector(claim.text)), now_iso()),
            )
        # Contradicting evidence belongs to the opposing claim. Copying it to
        # this claim creates cross-* rows; later runs copy those copies again,
        # growing exponentially while adding no independent source. Durable
        # claim_conflicts links retain both sides and their original evidence.
        for evidence in claim.evidence:
            source_id = source_ids.get(evidence.source_url)
            if source_id:
                db.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?)",
                           (evidence.evidence_id, claim_id, source_id, evidence.excerpt[:2000], evidence.location,
                            evidence.retrieved_at, evidence.support_type.value, evidence.strength, evidence.stance))
        if self._link_cross_run_conflicts(db, claim_id, claim, result.run_id):
            claim.status = ClaimStatus.DISPUTED
        self._store_entities(db, claim_id, claim)

    @staticmethod
    def _store_entities(db: sqlite3.Connection, claim_id: str, claim: Claim) -> None:
        for entity in claim.entities:
            normalized = entity.lower().strip()
            db.execute("INSERT OR IGNORE INTO entities(name,normalized_name,entity_type) VALUES(?,?,?)", (entity, normalized, "concept"))
            row = db.execute("SELECT id FROM entities WHERE normalized_name=? AND entity_type='concept'", (normalized,)).fetchone()
            db.execute("INSERT OR IGNORE INTO claim_entities VALUES(?,?,?)", (claim_id, int(row[0]), "subject"))
        if len(claim.entities) >= 2:
            first = db.execute("SELECT id FROM entities WHERE normalized_name=?", (claim.entities[0].lower(),)).fetchone()
            second = db.execute("SELECT id FROM entities WHERE normalized_name=?", (claim.entities[1].lower(),)).fetchone()
            if first and second:
                db.execute("INSERT OR IGNORE INTO relationships(subject_entity_id,predicate,object_entity_id,claim_id,confidence,valid_from,valid_to) VALUES(?,?,?,?,?,?,?)",
                           (first[0], "related_to", second[0], claim_id, claim.confidence, claim.valid_from, claim.valid_to))

    def _find_similar_claim(self, db: sqlite3.Connection, claim: Claim) -> sqlite3.Row | None:
        rows = db.execute("SELECT * FROM claims WHERE status IN ('SUPPORTED','PARTIALLY_SUPPORTED','DISPUTED') ORDER BY updated_at DESC LIMIT 500").fetchall()
        query_vector = text_vector(claim.text)
        best: tuple[float, sqlite3.Row] | None = None
        for row in rows:
            if (claim.scope or str(row["scope"])) and claim.scope != str(row["scope"]):
                continue
            candidate = self._claim_from_row(row)
            # Opposite propositions often have extremely high lexical
            # similarity. They must become two linked claims, never one row
            # overwritten by the other.
            if EvidenceVerifier.contradiction_reason(claim, candidate):
                continue
            score = cosine_similarity(query_vector, json.loads(row["vector_json"])) * 0.6 + jaccard(claim.text, row["claim_text"]) * 0.4
            if score >= 0.86 and (best is None or score > best[0]):
                best = (score, row)
        return best[1] if best else None

    @staticmethod
    def _claim_from_row(row: sqlite3.Row) -> Claim:
        try:
            claim_type = ClaimType(str(row["claim_type"]))
        except ValueError:
            claim_type = ClaimType.FACT
        try:
            status = ClaimStatus(str(row["status"]))
        except ValueError:
            status = ClaimStatus.UNVERIFIED
        return Claim(
            str(row["id"]), str(row["claim_text"]), claim_type=claim_type,
            status=status, confidence=float(row["confidence"]),
            created_at=str(row["created_at"]), updated_at=str(row["updated_at"]),
            valid_from=row["valid_from"], valid_to=row["valid_to"],
            validity_horizon=row["valid_until"], scope=str(row["scope"] or ""),
            entities=json.loads(row["entities_json"]),
            keywords=json.loads(row["keywords_json"]),
        )

    def _link_cross_run_conflicts(self, db: sqlite3.Connection, claim_id: str,
                                  claim: Claim, run_id: str) -> int:
        """Persist contradictions against claims learned in earlier runs."""
        candidates = db.execute(
            "SELECT * FROM claims WHERE id<>? AND status IN "
            "('SUPPORTED','PARTIALLY_SUPPORTED','DISPUTED') "
            "ORDER BY updated_at DESC LIMIT 1000", (claim_id,),
        ).fetchall()
        linked = 0
        for row in candidates:
            other = self._claim_from_row(row)
            reason = EvidenceVerifier.contradiction_reason(claim, other)
            if not reason:
                continue
            first, second = sorted((claim_id, str(row["id"])))
            confidence = round(min(claim.confidence, float(row["confidence"])), 3)
            cursor = db.execute(
                "INSERT OR IGNORE INTO claim_conflicts"
                "(claim_id,conflicting_claim_id,detected_at,reason,confidence) "
                "VALUES(?,?,?,?,?)", (first, second, now_iso(), reason, confidence),
            )
            if not cursor.rowcount:
                existing = db.execute(
                    "SELECT resolution_status FROM claim_conflicts "
                    "WHERE claim_id=? AND conflicting_claim_id=?", (first, second),
                ).fetchone()
                if existing and existing["resolution_status"] == "unresolved":
                    claim.status = ClaimStatus.DISPUTED
                    db.execute(
                        "UPDATE claims SET status='DISPUTED',updated_at=? WHERE id IN (?,?)",
                        (now_iso(), claim_id, str(row["id"])),
                    )
                    db.execute(
                        "UPDATE knowledge SET status='disputed',resolution=?,updated_at=? "
                        "WHERE run_id IN (?,?) AND status='active'",
                        (reason, now_iso(), run_id, str(row["run_id"])),
                    )
                continue
            linked += 1
            for identifier, old_status, old_confidence in (
                (claim_id, claim.status.value, claim.confidence),
                (str(row["id"]), str(row["status"]), float(row["confidence"])),
            ):
                if old_status != ClaimStatus.DISPUTED.value:
                    db.execute(
                        "INSERT INTO claim_history"
                        "(claim_id,changed_at,old_status,new_status,old_confidence,new_confidence,reason,run_id) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (identifier, now_iso(), old_status, ClaimStatus.DISPUTED.value,
                         old_confidence, old_confidence, reason, run_id),
                    )
                db.execute(
                    "UPDATE claims SET status='DISPUTED',updated_at=? WHERE id=?",
                    (now_iso(), identifier),
                )
            # Previously cached summaries containing either side must never be
            # served as settled answers while the conflict is unresolved.
            db.execute(
                "UPDATE knowledge SET status='disputed',resolution=?,updated_at=? "
                "WHERE run_id IN (?,?) AND status='active'",
                (reason, now_iso(), run_id, str(row["run_id"])),
            )
        return linked

    @staticmethod
    def _retire_invalid_claims(db: sqlite3.Connection) -> None:
        """Preserve but deactivate legacy rows rejected by current admission rules.

        Improving claim extraction must also stop old incomplete fragments from
        poisoning every later cross-check. Nothing is deleted: invalid rows are
        superseded, their conflicts are resolved with the valid counterpart,
        and the transition remains in claim history.
        """
        from app.research.claims import is_assertion, is_complete_assertion

        rows = db.execute(
            "SELECT id,claim_text,status,confidence,run_id FROM claims "
            "WHERE status IN ('SUPPORTED','PARTIALLY_SUPPORTED','DISPUTED')"
        ).fetchall()
        for row in rows:
            valid = is_assertion(str(row["claim_text"]))
            if str(row["run_id"]).startswith("document-"):
                valid = valid and is_complete_assertion(str(row["claim_text"]))
            if valid:
                continue
            identifier = str(row["id"])
            changed = now_iso()
            db.execute(
                "INSERT INTO claim_history(claim_id,changed_at,old_status,new_status,"
                "old_confidence,new_confidence,reason,run_id) VALUES(?,?,?,?,?,?,?,?)",
                (identifier, changed, str(row["status"]), "SUPERSEDED",
                 float(row["confidence"]), float(row["confidence"]),
                 "Claim no longer passes assertion admission", str(row["run_id"])),
            )
            conflicts = db.execute(
                "SELECT id,claim_id,conflicting_claim_id FROM claim_conflicts "
                "WHERE resolution_status='unresolved' AND (claim_id=? OR conflicting_claim_id=?)",
                (identifier, identifier),
            ).fetchall()
            db.execute(
                "UPDATE claims SET status='SUPERSEDED',updated_at=? WHERE id=?",
                (changed, identifier),
            )
            for conflict in conflicts:
                other = str(conflict["conflicting_claim_id"] if conflict["claim_id"] == identifier else conflict["claim_id"])
                db.execute(
                    "UPDATE claim_conflicts SET resolution_status='resolved',resolved_at=?,"
                    "winning_claim_id=?,resolution=? WHERE id=?",
                    (changed, other, "Invalid/incomplete claim retired by current admission rules", int(conflict["id"])),
                )
                remaining = db.execute(
                    "SELECT 1 FROM claim_conflicts WHERE resolution_status='unresolved' "
                    "AND (claim_id=? OR conflicting_claim_id=?) LIMIT 1", (other, other),
                ).fetchone()
                if remaining is None:
                    direct = db.execute(
                        "SELECT 1 FROM evidence WHERE claim_id=? AND support_type='DIRECT' LIMIT 1",
                        (other,),
                    ).fetchone()
                    db.execute(
                        "UPDATE claims SET status=?,updated_at=? WHERE id=? AND status='DISPUTED'",
                        ("SUPPORTED" if direct else "PARTIALLY_SUPPORTED", changed, other),
                    )
                    winning = db.execute(
                        "SELECT run_id FROM claims WHERE id=?", (other,),
                    ).fetchone()
                    if winning:
                        db.execute(
                            "UPDATE knowledge SET status='active',updated_at=?,resolution=? "
                            "WHERE run_id=? AND status='disputed'",
                            (changed, "Conflict resolved after invalid/incomplete claim retirement",
                             str(winning["run_id"])),
                        )

    def _reconcile_stored_conflicts(self, db: sqlite3.Connection) -> None:
        """Re-judge unresolved conflicts whenever the rules that found them improve.

        A conflict is a judgement, and judgements made by an older, worse rule
        should not outlive it. Six sources agreeing exactly about a 22% bracket
        and a $1,000 deduction were recorded as contradicting one another
        because the ones phrased "saves you $220, not $1,000" read as negative
        while "saves you $220" read as positive. Fixing the polarity test does
        nothing for rows already written, and `_backfill_claim_conflicts` is
        marked one-time, so without this the store keeps reporting
        contradictions its own current rules would never draw.

        Only unresolved rows are touched, and only to clear ones that are no
        longer conflicts. A resolution Mohsin or a later run recorded stands.
        """
        from app.research.verifier import EvidenceVerifier

        rows = db.execute(
            "SELECT id, claim_id, conflicting_claim_id FROM claim_conflicts "
            "WHERE resolution_status != 'resolved'"
        ).fetchall()
        if not rows:
            return
        cleared = 0
        for row in rows:
            left = db.execute("SELECT * FROM claims WHERE id=?", (row["claim_id"],)).fetchone()
            right = db.execute("SELECT * FROM claims WHERE id=?",
                               (row["conflicting_claim_id"],)).fetchone()
            if left is None or right is None:
                continue
            if EvidenceVerifier.contradiction_reason(
                self._claim_from_row(left), self._claim_from_row(right)
            ):
                continue
            db.execute(
                "UPDATE claim_conflicts SET resolution_status='resolved', resolved_at=?, "
                "resolution=? WHERE id=?",
                (now_iso(), "not a conflict under current extraction rules", row["id"]),
            )
            cleared += 1
        if cleared:
            # A claim disputed only by conflicts that no longer exist is not
            # disputed. Leaving it marked would keep it out of every answer.
            db.execute(
                "UPDATE claims SET status='SUPPORTED' WHERE status='DISPUTED' AND id NOT IN ("
                "  SELECT claim_id FROM claim_conflicts WHERE resolution_status != 'resolved'"
                "  UNION"
                "  SELECT conflicting_claim_id FROM claim_conflicts WHERE resolution_status != 'resolved'"
                ")"
            )

    def _backfill_claim_conflicts(self, db: sqlite3.Connection) -> None:
        """One-time migration of pre-existing claims into the conflict graph."""
        marker = "claim_conflict_backfill_v1"
        if db.execute("SELECT 1 FROM knowledge_meta WHERE key=?", (marker,)).fetchone():
            return
        rows = db.execute(
            "SELECT * FROM claims WHERE status IN "
            "('SUPPORTED','PARTIALLY_SUPPORTED','DISPUTED') ORDER BY updated_at"
        ).fetchall()
        for row in rows:
            self._link_cross_run_conflicts(
                db, str(row["id"]), self._claim_from_row(row), str(row["run_id"]),
            )
        db.execute(
            "INSERT OR REPLACE INTO knowledge_meta(key,value,updated_at) VALUES(?,?,?)",
            (marker, str(len(rows)), now_iso()),
        )

    @staticmethod
    def _supersede_temporal_conflicts(db: sqlite3.Connection, claim: Claim, run_id: str) -> None:
        if claim.claim_type.value != "TEMPORAL" or not (claim.valid_from or claim.scope):
            return
        entities = {entity.lower() for entity in claim.entities}
        rows = db.execute("SELECT * FROM claims WHERE status IN ('SUPPORTED','PARTIALLY_SUPPORTED') AND claim_type='TEMPORAL'").fetchall()
        for row in rows:
            existing_entities = {entity.lower() for entity in json.loads(row["entities_json"])}
            related = bool(entities & existing_entities) and jaccard(claim.text, row["claim_text"]) >= 0.42
            if not related or row["claim_text"] == claim.text:
                continue
            boundary = claim.valid_from or claim.created_at or now_iso()
            db.execute("UPDATE claims SET status='SUPERSEDED',valid_to=?,updated_at=? WHERE id=?", (boundary, now_iso(), row["id"]))
            db.execute("INSERT INTO claim_history(claim_id,changed_at,old_status,new_status,old_confidence,new_confidence,reason,run_id) VALUES(?,?,?,?,?,?,?,?)",
                       (row["id"], now_iso(), row["status"], "SUPERSEDED", row["confidence"], row["confidence"], "Newer temporal claim preserved the earlier fact as history", run_id))

    def _find_similar_knowledge(self, db: sqlite3.Connection, query: str, content: str) -> sqlite3.Row | None:
        rows = db.execute("SELECT * FROM knowledge WHERE status='active' ORDER BY updated_at DESC LIMIT 200").fetchall()
        return next((row for row in rows if jaccard(query, row["query"]) >= 0.82 and jaccard(content, row["content"]) >= 0.65), None)

    @staticmethod
    def _mark_older_stale(db: sqlite3.Connection, query: str, new_id: int) -> None:
        tokens = token_set(query)
        if not tokens:
            return
        rows = db.execute("SELECT id,query,valid_until FROM knowledge WHERE id<>? AND status='active'", (new_id,)).fetchall()
        for row in rows:
            if jaccard(tokens, token_set(row["query"])) >= 0.72 and is_stale(row["valid_until"]):
                db.execute("UPDATE knowledge SET status='superseded',updated_at=? WHERE id=?", (now_iso(), row["id"]))

    def learned_since(self, *, days: int = 1, limit: int = 12) -> dict[str, Any]:
        """What was actually studied and stored in the last `days` days.

        Asked "what did you learn today?", HAYAT answered "Nothing new today.
        My knowledge base is stable" and went on to explain that it was
        recalling its training data -- on a day the store had taken in seven
        new entries. Nothing consulted the record, so the model answered from
        the only thing it had, which was its own imagination.

        Returns the record. An empty record is a real answer too; the point is
        that the answer comes from here rather than from a guess.
        """
        since = (datetime.now(UTC) - timedelta(days=max(1, int(days)))).isoformat()
        with self.connect() as db:
            topics = db.execute(
                "SELECT query, MAX(created_at) AS latest, COUNT(*) AS entries, "
                "AVG(confidence) AS confidence FROM knowledge "
                "WHERE created_at >= ? AND status = 'active' AND query IS NOT NULL "
                "GROUP BY query ORDER BY latest DESC LIMIT ?",
                (since, int(limit)),
            ).fetchall()
            claims = db.execute(
                "SELECT COUNT(*) FROM claims WHERE created_at >= ?", (since,)
            ).fetchone()
            sources = db.execute(
                "SELECT COUNT(DISTINCT canonical_url) FROM sources WHERE retrieved_at >= ?", (since,)
            ).fetchone()
            runs = db.execute(
                "SELECT COUNT(*) FROM research_runs WHERE started_at >= ?", (since,)
            ).fetchone()
        return {
            "since": since,
            "days": max(1, int(days)),
            "topics": [
                {
                    "query": str(row["query"]),
                    "entries": int(row["entries"]),
                    "confidence": round(float(row["confidence"] or 0.0), 2),
                    "latest": str(row["latest"]),
                }
                for row in topics
            ],
            "claims": int(claims[0] if claims else 0),
            "sources": int(sources[0] if sources else 0),
            "runs": int(runs[0] if runs else 0),
        }

    def answered_before(self, query: str, *, minimum_similarity: float = 0.62) -> dict[str, Any] | None:
        """A stored answer to this same question, if there is one.

        The reuse path that existed matched the question against individual
        claim sentences, and a question does not read like the sentences that
        answer it. Asking "explain me in detail about taxation and tax evasion
        and tax return" -- the exact words already researched and stored --
        scored 0.46 against a 0.68 bar and went back to the web. Every
        previously answered question missed, which is why nothing ever felt
        learned.

        This compares question to question, where near-identical wording
        actually looks near-identical, so asking the same thing twice costs a
        database read instead of a search.
        """
        wanted = _subject_tokens(str(query or ""))
        if len(wanted) < 2:
            return None
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, query, content, confidence, created_at, updated_at, "
                "valid_until, status, run_id "
                "FROM knowledge WHERE status = 'active' AND content IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 400"
            ).fetchall()

        best: tuple[float, dict[str, Any]] | None = None
        for row in rows:
            if is_stale(row["valid_until"]):
                continue
            content = str(row["content"] or "")
            if not answer_still_acceptable(content):
                continue
            if not answer_covers(query, content):
                continue
            stored = _subject_tokens(str(row["query"] or ""))
            if not stored:
                continue
            from app.question_match import question_facets
            requested_facets = question_facets(query)
            stored_facets = question_facets(str(row["query"] or ""))
            if requested_facets and not (requested_facets & stored_facets):
                # A broad stored topic is not automatically an answer to a
                # narrower security/performance/requirements question. Reuse
                # it only when the answer itself actually covers that facet.
                if not (requested_facets & question_facets(content)):
                    continue
            shared = wanted & stored
            # Containment, not Jaccard. The question is "does what I already
            # answered cover what is being asked", which is asymmetric: extra
            # words in the stored question are not evidence against a match.
            # Jaccard scored a reworded version of an identical question at
            # 0.375 and sent it back to the web.
            overlap = len(shared) / len(wanted)
            if len(shared) < 2:
                continue
            if overlap >= minimum_similarity and (best is None or overlap > best[0]):
                best = (overlap, dict(row) | {"similarity": round(overlap, 3)})
        return best[1] if best else None

    def supported_sources_for_answer(self, knowledge_id: int, run_id: str) -> list[dict[str, Any]]:
        """Original sources still backing a stored answer's current run.

        A summary's confidence is not evidence. Reuse must survive a later
        claim demotion, expiry, or an exact-content refresh with no verified
        claim. The source must be linked to both the summary and a currently
        supported claim from the run that now owns it.
        """
        with self.connect() as db:
            rows = db.execute(
                "SELECT DISTINCT s.* FROM claims c "
                "JOIN evidence e ON e.claim_id=c.id "
                "JOIN claim_evidence ce ON ce.source_id=e.source_id "
                "AND ce.knowledge_id=? AND ce.stance='supporting' "
                "JOIN sources s ON s.id=e.source_id "
                "WHERE c.run_id=? AND c.status='SUPPORTED' "
                "AND e.stance='supporting' AND e.support_type='DIRECT' "
                "AND (c.valid_until IS NULL OR c.valid_until>?) "
                "ORDER BY s.quality DESC LIMIT 8",
                (knowledge_id, run_id, now_iso()),
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, query: str, limit: int = 8, status: str | None = "active", min_confidence: float = 0.0, domain: str | None = None, after: str | None = None, before: str | None = None) -> list[dict[str, Any]]:
        where = ["confidence>=?"]
        params: list[Any] = [min_confidence]
        if status:
            where.append("status=?"); params.append(status)
        if domain:
            where.append("EXISTS(SELECT 1 FROM run_sources rs JOIN sources s ON s.id=rs.source_id WHERE rs.run_id=knowledge.run_id AND s.domain=?)"); params.append(domain.lower())
        if after:
            where.append("updated_at>=?"); params.append(after)
        if before:
            where.append("updated_at<=?"); params.append(before)
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM knowledge WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT 500", params).fetchall()
            ranked = self._rank_rows(query, rows, "content", "query")[:limit]
            if ranked:
                db.executemany("UPDATE knowledge SET retrieval_count=retrieval_count+1,last_used=? WHERE id=?", [(now_iso(), item["id"]) for item in ranked])
        return ranked

    def search_claims(self, query: str, limit: int = 20, include_history: bool = False, min_confidence: float = 0.0, *, lexical_only: bool = False) -> list[dict[str, Any]]:
        statuses = "('SUPPORTED','PARTIALLY_SUPPORTED','DISPUTED')" if not include_history else "('SUPPORTED','PARTIALLY_SUPPORTED','DISPUTED','SUPERSEDED')"
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM claims WHERE confidence>=? AND status IN {statuses} ORDER BY updated_at DESC LIMIT 1000", (min_confidence,)).fetchall()
            words = sorted(token_set(query), key=len, reverse=True)[:12]
            if words:
                conditions, arguments = [], [min_confidence]
                for word in words:
                    pattern = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    conditions.append("(lower(claim_text) LIKE ? ESCAPE '\\' OR lower(topic) LIKE ? ESCAPE '\\')")
                    arguments.extend((pattern, pattern))
                matches = db.execute(
                    f"SELECT * FROM claims WHERE confidence>=? AND status IN {statuses} AND ("
                    + " OR ".join(conditions) + ") ORDER BY updated_at DESC LIMIT 1000", arguments,
                ).fetchall()
                # Recent-only recall made older learned facts disappear once
                # the corpus grew beyond 1,000 claims. Keep semantic candidates
                # and add matching candidates from the entire retained corpus.
                rows = matches if lexical_only else list({row["id"]: row for row in [*rows, *matches]}.values())
            elif lexical_only and query.strip():
                rows = []
            ranked = self._rank_rows(query, rows, "claim_text", "topic", entity_weight=True)[:limit]
            for item in ranked:
                evidence = db.execute(
                    "SELECT e.*,s.final_url,s.title,s.domain,s.quality FROM evidence e "
                    "JOIN sources s ON s.id=e.source_id WHERE e.claim_id=? "
                    "AND e.id NOT LIKE 'cross-%' ORDER BY e.strength DESC LIMIT 12",
                    (item["id"],),
                ).fetchall()
                item["evidence"] = [dict(row) for row in evidence]
                item["entities"] = json.loads(item["entities_json"])
                item["keywords"] = json.loads(item["keywords_json"])
                item["stale"] = is_stale(item["valid_until"])
                item["conflicts"] = self._conflicts_for_claim(db, str(item["id"]))
                item["has_unresolved_conflict"] = any(
                    conflict["resolution_status"] == "unresolved"
                    for conflict in item["conflicts"]
                )
                db.execute("UPDATE claims SET retrieval_count=retrieval_count+1,last_used=? WHERE id=?", (now_iso(), item["id"]))
        return ranked

    @staticmethod
    def _conflicts_for_claim(db: sqlite3.Connection, claim_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            "SELECT cc.*,"
            "CASE WHEN cc.claim_id=? THEN cc.conflicting_claim_id ELSE cc.claim_id END AS other_id "
            "FROM claim_conflicts cc WHERE cc.claim_id=? OR cc.conflicting_claim_id=? "
            "ORDER BY cc.resolution_status='unresolved' DESC,cc.confidence DESC,cc.detected_at DESC",
            (claim_id, claim_id, claim_id),
        ).fetchall()
        conflicts: list[dict[str, Any]] = []
        for row in rows:
            other = db.execute(
                "SELECT id,claim_text,status,confidence,scope,valid_from,valid_to,run_id,last_verified "
                "FROM claims WHERE id=?", (row["other_id"],),
            ).fetchone()
            if other is None:
                continue
            evidence = db.execute(
                "SELECT e.stance,e.support_type,e.strength,e.excerpt,s.final_url,s.title,s.domain,s.quality "
                "FROM evidence e JOIN sources s ON s.id=e.source_id WHERE e.claim_id=? "
                "AND e.id NOT LIKE 'cross-%' ORDER BY e.strength DESC LIMIT 12", (row["other_id"],),
            ).fetchall()
            conflicts.append({
                "conflict_id": int(row["id"]), "other_claim": dict(other),
                "reason": row["reason"], "confidence": float(row["confidence"]),
                "detected_at": row["detected_at"],
                "resolution_status": row["resolution_status"],
                "resolved_at": row["resolved_at"], "winning_claim_id": row["winning_claim_id"],
                "resolution": row["resolution"],
                "evidence": [dict(item) for item in evidence],
            })
        return conflicts

    def conflicts(self, query: str = "", *, unresolved_only: bool = True,
                  limit: int = 50) -> list[dict[str, Any]]:
        """Return durable claim-to-claim disagreements with both evidence sets."""
        where = "WHERE cc.resolution_status='unresolved'" if unresolved_only else ""
        with self.connect() as db:
            rows = db.execute(
                f"SELECT cc.*,a.claim_text AS claim_text,a.status AS claim_status,a.confidence AS claim_confidence,"
                "b.claim_text AS conflicting_text,b.status AS conflicting_status,b.confidence AS conflicting_confidence "
                "FROM claim_conflicts cc JOIN claims a ON a.id=cc.claim_id "
                "JOIN claims b ON b.id=cc.conflicting_claim_id "
                f"{where} ORDER BY cc.confidence DESC,cc.detected_at DESC LIMIT 500"
            ).fetchall()
            results = [dict(row) for row in rows]
            if query.strip():
                terms = token_set(query)
                results.sort(
                    key=lambda item: len(terms & token_set(item["claim_text"] + " " + item["conflicting_text"])),
                    reverse=True,
                )
                results = [item for item in results if terms & token_set(item["claim_text"] + " " + item["conflicting_text"])]
            for item in results[:max(1, min(limit, 200))]:
                item["claim_evidence"] = [dict(row) for row in db.execute(
                    "SELECT e.*,s.final_url,s.title,s.domain,s.quality FROM evidence e "
                    "JOIN sources s ON s.id=e.source_id WHERE e.claim_id=? "
                    "AND e.id NOT LIKE 'cross-%' ORDER BY e.strength DESC LIMIT 12",
                    (item["claim_id"],),
                ).fetchall()]
                item["conflicting_evidence"] = [dict(row) for row in db.execute(
                    "SELECT e.*,s.final_url,s.title,s.domain,s.quality FROM evidence e "
                    "JOIN sources s ON s.id=e.source_id WHERE e.claim_id=? "
                    "AND e.id NOT LIKE 'cross-%' ORDER BY e.strength DESC LIMIT 12",
                    (item["conflicting_claim_id"],),
                ).fetchall()]
        return results[:max(1, min(limit, 200))]

    def annotate_stored_conflicts(self, claims: list[Claim]) -> list[dict[str, Any]]:
        """Attach opposing evidence learned in earlier runs before synthesis.

        Persistence happens later in ``store_result``. This read-only pass
        ensures the answer from the *current* run already acknowledges older
        contradictory knowledge rather than discovering the conflict only
        after telling the user one side.
        """
        source_rows: dict[int, dict[str, Any]] = {}
        with self.connect() as db:
            candidates = db.execute(
                "SELECT * FROM claims WHERE status IN "
                "('SUPPORTED','PARTIALLY_SUPPORTED','DISPUTED') "
                "ORDER BY updated_at DESC LIMIT 1000"
            ).fetchall()
            for claim in claims:
                for row in candidates:
                    if str(row["id"]) == claim.claim_id:
                        continue
                    reason = EvidenceVerifier.contradiction_reason(claim, self._claim_from_row(row))
                    if not reason:
                        continue
                    evidence_rows = db.execute(
                        "SELECT e.*,s.* FROM evidence e JOIN sources s ON s.id=e.source_id "
                        "WHERE e.claim_id=? AND e.id NOT LIKE 'cross-%' "
                        "ORDER BY e.strength DESC LIMIT 12", (row["id"],),
                    ).fetchall()
                    for evidence_row in evidence_rows:
                        try:
                            support_type = SupportType(str(evidence_row["support_type"]))
                        except ValueError:
                            support_type = SupportType.UNSUPPORTED
                        claim.contradictions.append(Evidence(
                            evidence_id=f"cross-{claim.claim_id}-{evidence_row['id']}",
                            claim_id=claim.claim_id,
                            source_url=str(evidence_row["final_url"]),
                            source_title=str(evidence_row["title"]),
                            excerpt=str(evidence_row["excerpt"]),
                            quality=float(evidence_row["quality"]),
                            retrieved_at=str(evidence_row["retrieved_at"]),
                            published_at=evidence_row["published_at"],
                            location=str(evidence_row["location"]),
                            support_type=support_type,
                            strength=float(evidence_row["strength"]),
                            stance="contradicting", source_hash=str(evidence_row["content_hash"]),
                            independence_group=str(evidence_row["independence_group"]),
                        ))
                        source_rows[int(evidence_row["source_id"])] = {
                            key: evidence_row[key] for key in evidence_row.keys()
                            if key in {
                                "canonical_url", "final_url", "title", "domain", "retrieved_at",
                                "published_at", "updated_at", "content_hash", "content_type", "quality",
                                "metadata_json", "excerpt", "source_type", "author", "primary_source",
                                "independence_group", "lineage_json", "version",
                            }
                        }
                    claim.status = ClaimStatus.DISPUTED
        return list(source_rows.values())

    @staticmethod
    def _rank_rows(query: str, rows: list[sqlite3.Row], content_field: str, topic_field: str, entity_weight: bool = False) -> list[dict[str, Any]]:
        vector = text_vector(query)
        query_tokens = token_set(query)
        query_entities = {entity.lower() for entity in extract_entities(query)}
        wants_requirements = bool(re.search(
            r"(?i)\b(?:require(?:s|d|ments?)?|must|shall|rule|conform|compliance)\w*\b",
            query,
        ))
        ranked: list[dict[str, Any]] = []
        for row in rows:
            content = f"{row[topic_field]} {row[content_field]}"
            lexical = len(query_tokens & token_set(content)) / max(1, len(query_tokens))
            semantic = cosine_similarity(vector, json.loads(row["vector_json"]))
            entities = {entity.lower() for entity in json.loads(row["entities_json"])} if entity_weight and "entities_json" in row.keys() else set()
            entity = len(query_entities & entities) / max(1, len(query_entities)) if query_entities else 0.0
            stale = is_stale(row["valid_until"])
            freshness = 0.0 if stale else 1.0
            usage = (float(row["successful_uses"]) + 1) / (float(row["successful_uses"]) + float(row["failed_uses"]) + 2)
            normative = 1.0 if wants_requirements and re.search(
                r"\b(?:MUST(?:\s+NOT)?|SHALL(?:\s+NOT)?|REQUIRED|RECOMMENDED|SHOULD(?:\s+NOT)?)\b",
                str(row[content_field]),
            ) else 0.0
            # Specific query coverage must outweigh a repeated general entity
            # such as "Python". Otherwise release notes can outrank an exact
            # match for "Python median statistics" because of their vectors.
            score = semantic * 0.25 + lexical * 0.45 + entity * 0.05 + freshness * 0.08 + float(row["confidence"]) * 0.10 + usage * 0.03 + normative * 0.04
            item = dict(row)
            item["stale"] = stale
            item["relevance"] = round(score, 4)
            ranked.append(item)
        return sorted(ranked, key=lambda item: (item["relevance"], item["updated_at"]), reverse=True)

    def record_use(self, item_id: int | str, success: bool, kind: str = "knowledge") -> None:
        table = "claims" if kind == "claim" else "knowledge"
        key = "id"
        column = "successful_uses" if success else "failed_uses"
        with self.connect() as db:
            db.execute(f"UPDATE {table} SET {column}={column}+1,last_used=? WHERE {key}=?", (now_iso(), item_id))
            if not success and kind == "claim":
                db.execute("UPDATE claims SET status='DISPUTED',updated_at=? WHERE id=? AND failed_uses>=2", (now_iso(), item_id))

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            sources = db.execute("SELECT s.* FROM sources s JOIN run_sources r ON r.source_id=s.id WHERE r.run_id=? ORDER BY r.rank", (run_id,)).fetchall()
            claims = db.execute("SELECT * FROM claims WHERE run_id=? ORDER BY confidence DESC", (run_id,)).fetchall()
        return dict(row) | {"sources": [dict(item) for item in sources], "claims": [dict(item) for item in claims]}

    def stale_entries(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM knowledge WHERE status='active' AND valid_until IS NOT NULL AND valid_until<=? ORDER BY valid_until LIMIT ?", (now_iso(), limit)).fetchall()
        return [dict(row) | {"stale": True} for row in rows]

    def retire(self, knowledge_id: int, reason: str) -> None:
        """Stop refreshing an entry whose query was never a real topic.

        Nothing is deleted: the entry, its sources and its run stay for
        provenance, marked stale with the reason, and can be set active again.
        """
        with self.connect() as db:
            db.execute("UPDATE knowledge SET status='stale', resolution=?, updated_at=? WHERE id=? AND status='active'",
                       (f"retired: {reason}"[:300], now_iso(), knowledge_id))

    def set_status(self, knowledge_id: int, status: str) -> None:
        if status not in {"active", "stale", "disputed", "superseded"}:
            raise ValueError("Unsupported knowledge status.")
        with self.connect() as db:
            db.execute("UPDATE knowledge SET status=?,updated_at=? WHERE id=?", (status, now_iso(), knowledge_id))

    def optimize(self) -> dict[str, Any]:
        """Select one active exact duplicate and prune only expired caches.

        Sources, evidence, runs and superseded summaries remain available for
        provenance. Conflicts are resolved, not erased from history.
        """
        now = now_iso()
        duplicates = 0
        with self.connect() as db:
            groups = db.execute(
                "SELECT content_hash FROM knowledge WHERE status='active' AND content_hash<>'' "
                "GROUP BY content_hash HAVING COUNT(*)>1"
            ).fetchall()
            for group in groups:
                rows = db.execute(
                    "SELECT id FROM knowledge WHERE status='active' AND content_hash=? "
                    "ORDER BY confidence DESC,last_verified DESC,updated_at DESC",
                    (group[0],),
                ).fetchall()
                winner = int(rows[0][0])
                for row in rows[1:]:
                    db.execute(
                        "UPDATE knowledge SET status='superseded',supersedes_id=?,resolution=?,updated_at=? WHERE id=?",
                        (winner, "exact duplicate resolved during optimization", now, row[0]),
                    )
                    duplicates += 1
            expired_queries = db.execute("DELETE FROM query_cache WHERE expires_at<=?", (now,)).rowcount
            expired_sources = db.execute("DELETE FROM source_cache WHERE expires_at<=?", (now,)).rowcount
            db.execute("PRAGMA optimize")
        return {
            "duplicates_resolved": duplicates,
            "expired_query_cache_removed": max(0, expired_queries),
            "expired_source_cache_removed": max(0, expired_sources),
            "evidence_deleted": 0,
            "history_preserved": True,
        }

    def cache_query(self, key: str, query: str, provider: str, results: list[dict[str, Any]], expires_at: str) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO query_cache VALUES(?,?,?,?,?,?,COALESCE((SELECT hit_count FROM query_cache WHERE cache_key=?),0))", (key, query, provider, json.dumps(results), now_iso(), expires_at, key))

    def get_cached_query(self, key: str) -> list[dict[str, Any]] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM query_cache WHERE cache_key=? AND expires_at>?", (key, now_iso())).fetchone()
            if not row:
                return None
            db.execute("UPDATE query_cache SET hit_count=hit_count+1 WHERE cache_key=?", (key,))
        return json.loads(row["results_json"])

    def cache_source(self, source: SourceDocument, expires_at: str, etag: str | None = None, last_modified: str | None = None) -> None:
        data = source.to_dict(True)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO source_cache VALUES(?,?,?,?,?,?,?,COALESCE((SELECT hit_count FROM source_cache WHERE canonical_url=?),0))",
                       (source.canonical_url or source.final_url, json.dumps(data, default=str), now_iso(), expires_at, etag, last_modified, source.content_hash, source.canonical_url or source.final_url))

    def get_cached_source(self, url: str) -> SourceDocument | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM source_cache WHERE canonical_url=? AND expires_at>?", (url, now_iso())).fetchone()
            if not row:
                return None
            db.execute("UPDATE source_cache SET hit_count=hit_count+1 WHERE canonical_url=?", (url,))
        data = json.loads(row["source_json"])
        data["cache_status"] = "HIT"
        return SourceDocument(**data)

    def source_cache_entry(self, url: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM source_cache WHERE canonical_url=?", (url,)).fetchone()
        return dict(row) if row else None

    def record_source_performance(self, domain: str, success: bool, latency_ms: int, quality: float = 0.0, failure: str = "") -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO source_stats(domain,updated_at) VALUES(?,?)", (domain, now_iso()))
            db.execute(f"UPDATE source_stats SET requests=requests+1,{'successes=successes+1' if success else 'failures=failures+1'},total_latency_ms=total_latency_ms+?,quality_total=quality_total+?,last_failure=?,updated_at=? WHERE domain=?",
                       (latency_ms, quality, failure or None, now_iso(), domain))

    def record_provider_performance(self, provider: str, success: bool, latency_ms: int, results: int, duplicates: int, quality: float) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO provider_stats(provider,updated_at) VALUES(?,?)", (provider, now_iso()))
            db.execute(f"UPDATE provider_stats SET searches=searches+1,{'successes=successes+1' if success else 'failures=failures+1'},total_latency_ms=total_latency_ms+?,results_total=results_total+?,duplicate_total=duplicate_total+?,quality_total=quality_total+?,updated_at=? WHERE provider=?",
                       (latency_ms, results, duplicates, quality, now_iso(), provider))

    def record_failure(self, route: str, failure_type: str, detail: str) -> None:
        with self.connect() as db:
            row = db.execute("SELECT id FROM research_failures WHERE route=? AND failure_type=? AND detail=?", (route, failure_type, detail[:1000])).fetchone()
            if row:
                db.execute("UPDATE research_failures SET count=count+1,last_seen=? WHERE id=?", (now_iso(), row[0]))
            else:
                db.execute("INSERT INTO research_failures(route,failure_type,detail,first_seen,last_seen) VALUES(?,?,?,?,?)", (route, failure_type, detail[:1000], now_iso(), now_iso()))

    def similar_run_count(self, query: str, limit: int = 100) -> int:
        with self.connect() as db:
            rows = db.execute("SELECT query FROM research_runs WHERE complete=1 ORDER BY completed_at DESC LIMIT ?", (limit,)).fetchall()
        return sum(jaccard(query, row["query"]) >= 0.68 for row in rows)

    def route_failure_count(self, route: str) -> int:
        with self.connect() as db:
            row = db.execute("SELECT COALESCE(SUM(count),0) FROM research_failures WHERE route=?", (route,)).fetchone()
        return int(row[0])

    def export_topic(self, query: str, format: str = "json") -> str:
        claims = self.search_claims(query, limit=100, include_history=True)
        payload = {"topic": query, "exported_at": now_iso(), "claims": claims}
        if format == "json":
            return json.dumps(payload, indent=2, default=str)
        if format != "markdown":
            raise ValueError("Format must be json or markdown.")
        lines = [f"# Knowledge export: {query}", "", f"Exported: {payload['exported_at']}", ""]
        for claim in claims:
            lines.extend([f"## {claim['claim_text']}", "", f"Status: {claim['status']} · Confidence: {claim['confidence']:.2f} · Verified: {claim.get('last_verified')}", ""])
            for evidence in claim.get("evidence", []):
                lines.append(f"- {evidence['support_type']} — {evidence['final_url']} ({evidence['location']})")
            lines.append("")
        return "\n".join(lines)

    def monitor_source(self, url: str, label: str, content_hash: str) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO monitored_sources(url,label,content_hash,last_checked,enabled) VALUES(?,?,?,?,1)", (url, label, content_hash, now_iso()))

    def monitored_source(self, url: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM monitored_sources WHERE url=? AND enabled=1", (url,)).fetchone()
        return dict(row) if row else None

    def update_monitored_hash(self, url: str, content_hash: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE monitored_sources SET content_hash=?,last_checked=? WHERE url=?", (content_hash, now_iso(), url))

    def audit_claim_evidence(self, *, repair: bool = False) -> dict[str, Any]:
        """Withdraw unsupported verification labels, retaining claims and history."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE" if repair else "BEGIN")
            rows = db.execute(
                "SELECT c.id,c.status,c.confidence,c.run_id FROM claims c "
                "WHERE c.status IN ('SUPPORTED','PARTIALLY_SUPPORTED') AND NOT EXISTS("
                "SELECT 1 FROM evidence e JOIN sources s ON s.id=e.source_id WHERE e.claim_id=c.id)"
            ).fetchall()
            expired = 0
            if repair:
                changed = now_iso()
                reason = "Verification withdrawn: no retained source evidence; fresh research required"
                for row in rows:
                    db.execute(
                        "INSERT INTO claim_history(claim_id,changed_at,old_status,new_status,old_confidence,new_confidence,reason,run_id) VALUES(?,?,?,?,?,?,?,?)",
                        (row["id"], changed, row["status"], "UNVERIFIED", row["confidence"], 0.0, reason, row["run_id"]),
                    )
                    db.execute("UPDATE claims SET status='UNVERIFIED',confidence=0,updated_at=?,valid_until=? WHERE id=?",
                               (changed, changed, row["id"]))
                for run_id in {row["run_id"] for row in rows}:
                    expired += db.execute(
                        "UPDATE knowledge SET valid_until=?,updated_at=?,resolution=? WHERE run_id=? AND status='active'",
                        (changed, changed, reason, run_id),
                    ).rowcount
            return {"missing_evidence": len(rows), "claim_ids": [row["id"] for row in rows],
                    "demoted": len(rows) if repair else 0, "expired_summaries": expired}

    def stats(self) -> dict[str, Any]:
        with self.connect() as db:
            providers = [dict(row) for row in db.execute("SELECT * FROM provider_stats ORDER BY searches DESC,provider LIMIT 10").fetchall()]
            sources = [dict(row) for row in db.execute("SELECT * FROM source_stats ORDER BY requests DESC,domain LIMIT 10").fetchall()]
            return {
                "database": str(self.path), "runs": db.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0],
                "sources": db.execute("SELECT COUNT(*) FROM sources").fetchone()[0], "knowledge": db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0],
                "claims": db.execute("SELECT COUNT(*) FROM claims").fetchone()[0], "evidence": db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
                "unsupported_claims": db.execute(
                    "SELECT COUNT(*) FROM claims c WHERE c.status IN ('SUPPORTED','PARTIALLY_SUPPORTED') AND NOT EXISTS("
                    "SELECT 1 FROM evidence e JOIN sources s ON s.id=e.source_id WHERE e.claim_id=c.id)"
                ).fetchone()[0],
                "unverified_claims": db.execute("SELECT COUNT(*) FROM claims WHERE status='UNVERIFIED'").fetchone()[0],
                "entities": db.execute("SELECT COUNT(*) FROM entities").fetchone()[0], "relationships": db.execute("SELECT COUNT(*) FROM relationships").fetchone()[0],
                # Only entries still in use can be stale. Counting superseded
                # and retired rows kept this number from ever going down, so
                # the self-review asked for a refresh that could not satisfy it.
                "stale": db.execute("SELECT COUNT(*) FROM knowledge WHERE status='active' AND valid_until IS NOT NULL AND valid_until<=?", (now_iso(),)).fetchone()[0],
                "superseded": db.execute("SELECT COUNT(*) FROM knowledge WHERE status='superseded'").fetchone()[0],
                "disputed_claims": db.execute("SELECT COUNT(*) FROM claims WHERE status='DISPUTED'").fetchone()[0],
                "unresolved_conflicts": db.execute("SELECT COUNT(*) FROM claim_conflicts WHERE resolution_status='unresolved'").fetchone()[0],
                "query_cache": db.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0], "source_cache": db.execute("SELECT COUNT(*) FROM source_cache").fetchone()[0],
                "failures": db.execute("SELECT COALESCE(SUM(count),0) FROM research_failures").fetchone()[0],
                "monitored_sources": db.execute("SELECT COUNT(*) FROM monitored_sources WHERE enabled=1").fetchone()[0],
                "provider_performance": providers, "source_performance": sources,
            }
