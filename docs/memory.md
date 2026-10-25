# Memory and experience

SQLite data lives in `data/memory/agent.db` and initializes idempotently.

Ephemeral inputs such as clipboard text and raw audio are not ordinary memory candidates. Clipboard content may be used during the current request, but persistent action/log/reflection records contain only type, length, and hash metadata.

- Working memory: in-process current goal, recent observations, and task history.
- Episodic memory: completed and failed task summaries with outcomes and histories.
- Semantic memory: facts explicitly stored with source and importance.
- Procedural memory: YAML procedure candidates, success/failure counts, mean latency, last use, empirical confidence, and a trust flag.
- Reflection memory: what worked, what failed, reusable-skill candidacy, and next-time guidance.

Core tables include `tasks`, `actions`, `memories`, `memory_fts`, `memory_vectors`, `memory_neural_vectors`, `memory_links`, `conversations`, `procedures`, `scheduled_jobs`, `approval_requests`, and `schema_migrations`. Synchronized FTS5 indexes generate lexical candidates without scanning every stored memory; custom SQLite builds without FTS5 fall back safely to bounded `LIKE` queries. Retrieval then combines deterministic hashed semantics, lexical overlap, named entities, recency, and importance. If a configured local embedding model becomes available, neural cosine similarity is added without replacing provenance. `/memory consolidate` only reports exact duplicates; it never silently deletes memories or merges contradictions.

## Dynamic Neural Associations (DNA)

DNA here means **Dynamic Neural Associations**, not biological DNA and not self-training model weights. After normal hybrid ranking, the strongest seeds can traverse a durable `memory_links` graph. Traversal is cycle-safe and bounded by seed count, depth, nodes, minimum score, and per-edge decay. Linked context cannot outrank the matching seed merely because its edge is strong.

Multi-seed traversal executes as one recursive SQLite query over indexed source and target edges rather than opening a connection for every graph node. Query embeddings use the durable provider/model/version/content-hash cache, so repeating a recall after restarting HAYAT does not call the local embedding model again. Direct candidates receive a bounded DNA boost when a strong evidence-bearing edge connects them; this prevents unrelated hash collisions from crowding linked context out of the final result.

Relations include `related`, `supports`, `contradicts`, `depends_on`, `caused_by`, `part_of`, `same_entity`, `followed_by`, and `supersedes`. Each edge stores direction, weight, evidence, timestamps, and reinforcement count. Only neutral `related` links may be formed automatically from strong deterministic local similarity, and only for high-value durable knowledge. Contradictions must be explicitly written with evidence; retrieval labels them as contradictions instead of flattening them into factual support. Foreign-session transient state remains excluded before graph results can enter model context.

Unverified web, browser, crawler, research, background-learner, and other external rows cannot form automatic DNA edges. They remain searchable evidence and may become eligible only after an explicit `verified` or `trusted` provenance flag. A bounded concept lexicon expands technical acronyms, long forms, and recall synonyms during candidate generation and scoring only; it never rewrites a command or permission decision. Expansion results are compiled and cached for ordinary-size notes so repeated recall does not repeatedly run the lexicon.

Expansion is performed once on the query; candidate documents are tokenized
directly. Exact technical identifiers such as `RFC 8785` and CVE numbers use a
smaller whole-database FTS candidate set and skip the cold neural-query call,
because deterministic lexical/entity/hashed matching preserves those tokens
more reliably. A live eight-run RFC 8785 recall benchmark ranked the correct
document index first every time at 309.84 ms mean, compared with 3.84 seconds
before removing redundant per-document expansion.

Every durable memory row carries an exact SHA-256 content digest. Retrieval checks it before ranking and quarantines mismatches from model context; `verify_memory_integrity()` reports affected IDs. Authorized compaction updates the digest and deterministic vector atomically and invalidates any stale neural vector for normal reindexing. This detects corruption or direct database edits, but it is not cryptographic protection against an attacker who already controls the operating system and application files.

The deterministic DNA benchmark stores 2,002 memories and 201 associations, then performs 100 linked recalls. The current measured result is 100% complete linked recall, 53.45 ms mean latency, 61.65 ms p95, and 88.95 ms maximum on this machine, including the concept-expansion layer. The benchmark artifact is `data/benchmarks/retrieval/dna.json`; these are point-in-time local measurements, not universal hardware guarantees.

`MemoryDatabase.health()` checks SQLite integrity plus full-text and vector
coverage. Safe self-improvement may rebuild these derived indexes when coverage
is incomplete; it does not rewrite or delete the source memories. Behavior,
dependency, source-code, and permission changes remain separately gated and
test-verified.

Neural retrieval is now active through the capability-detected local Ollama `all-minilm:latest` model: 384 dimensions and a 512-token input limit. `retrieval_objects`, `retrieval_objects_fts`, and `retrieval_object_vectors` add a replaceable cross-object index while keeping SQLite authoritative. The full-text table stays synchronized through insert/update/delete triggers; vectors record provider, model, version, normalized content hash, dimension, and timestamp. `/memory sync` discovers eligible objects; `/memory reindex` recomputes only missing, changed, or model-version-mismatched vectors. Raw logs, binaries, oversized text, duplicates, and secret-like content are excluded.

The 100-case benchmark selected semantic-heavy hybrid weights from measured results: Precision@1 0.85, Recall@5 0.95, MRR 0.8942, nDCG@5 0.9085, and 0.15 top-result false-memory rate. Lexical-only Precision@1 was 0.32 and neural-only was 0.82. In the final full evaluation, the selected hybrid averaged 478.5 ms (585.7 ms p95); lexical-only averaged 5.9 ms after query-embedding bypass. Local model load and contention make neural latency variable, so quality—not one latency sample—selected the default. These finite synthetic cases guide defaults; the weights are not claimed globally optimal.

Task actions store the selected tool, parameters, real result, verifier evidence, retry number, and duration. Future embedding search can be added behind the existing memory interfaces.

`/chat` records user and assistant turns under a session ID. `/remember` stores a semantic fact and `/recall` returns compact ranked evidence.

Typed and voice turns now share the same session identifier and contextual state. The session remembers the exact active window handle, application, file, browser tab, last target, prior task, pending unsaved-work action, and last correction. User problem reports are stored as structured reflections instead of being treated as computer commands. A later verified repair may produce an untrusted procedural candidate, which still requires the normal review/promotion boundary before trusted reuse.

Researched knowledge uses `data/knowledge/knowledge.db` so source versions and claim evidence are not flattened into conversation memory. Its graph-oriented SQLite tables preserve research runs, content-hashed source versions, claims and history, evidence edges, entities, relationships, temporal scope, freshness/status, caches, usage, failures, and source/provider performance. Successful research is also summarized through normal task memory. Repeated task success/failure updates claim usefulness or dispute state; it never retrains model weights.

## Candidate generation and retrieval scale

Scoring is only as good as the candidate set it is given. Candidates were previously selected with `ORDER BY timestamp DESC LIMIT 500`, so the nine-signal ranker only ever saw the 500 most *recent* memories. Once the store passed that size, an older memory could not be retrieved however well it matched — a fact stored in week one became invisible in week three.

`MemoryDatabase._candidates` now unions three passes before ranking:

1. **Lexical** — FTS5 rows matching any query term of three characters or more, across the whole table, ranked by BM25 with importance and recency tie-breakers. This is what allows an old but relevant memory to reach the ranker without a table scan.
2. **Importance** — the highest-importance rows regardless of age, so deliberately stored facts (`/remember`, confidence 0.9) are always considered.
3. **Recency** — the previous behaviour, preserved unchanged so results for recent material do not move.

Duplicates are collapsed by memory id, and the existing hybrid scoring is applied to the union.

`memory_retrieval.candidate_limit` in `config/settings.yaml` now controls the bound on each pass. It was previously decorative: the value was declared but the limit was hardcoded. The floor is 50.

Measured on a store where the correct answer was inserted first and then buried:

| Newer records burying the answer | Retrieved | Rank | Latency |
|---|---|---|---|
| 600 | yes | 1 | 20 ms |
| 2000 | yes | 1 | 27 ms |

Before the change, the answer was not retrieved at all at 600 and the top result was an unrelated log entry.

This keeps SQLite authoritative, adds no dependency, sends nothing off the machine, and leaves the vector format and reindex path unchanged. A dedicated vector index remains a reasonable future option; it is not required to remove the correctness defect.
