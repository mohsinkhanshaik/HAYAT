# Performance and observability

All telemetry is local. `/system` reports CPU, RAM, GPU/VRAM, disk, and timestamp. `/performance` summarizes measured action/tool/benchmark data. `/health` joins component health with resources.

Important terms:

- **Latency:** elapsed time before/result completion.
- **Tokens per second:** model generation throughput.
- **Cold model:** available but not loaded; first response can be slower.
- **Warm model:** recently loaded and ready.
- **VRAM/RAM:** GPU/system memory capacity used by models and applications.

The resource-aware router checks fit before loading and prefers a warm model where measured quality is comparable. Automatic eviction is currently disabled to avoid unverified unload behavior. Local event records include task and resource alerts without paid telemetry.

Downloaded-document recall is also profiled. Query-side synonym expansion runs
once instead of scanning the full linguistic rule set over every candidate
chunk. Exact identifiers use bounded FTS/hashed ranking without waking a cold
embedding model. A standards-requirement query over the retained RFC 8785
source returned five verified normative claims with no web or model call in a
ten-run mean of 86.293 ms. The earlier general document-recall benchmark was
309.84 ms, down from a 3.84-second pre-optimization profile that spent 2.89
seconds in redundant regex searches. These are workstation measurements, not
latency guarantees.

The same query through the complete assistant boundary—including intent
routing, turn bookkeeping, truth gates, and response formatting—measured a
110.391 ms ten-run mean (101.931–118.163 ms) and returned five cited claims.

Fast reuse is deliberately conditional. Exact-answer records still have to
meet the current confidence floor and match narrow facets such as requirements,
security, diagnosis, or performance. Conflicted, stale, low-confidence, or
facet-mismatched material takes the slower verification/research path rather
than producing a fast but unsupported answer.

The anticipatory-learning gate keeps up to 200 learned predicted questions in
a compact RAM index and uses deterministic subject/facet matching before any
retrieval. On this workstation, a 200-candidate benchmark measured 7.407 ms
mean for an unrelated question and 12.534 ms mean for an exact match including
the SQLite `FULL`-synchronous usage-counter commit. An accepted prepared answer
then uses verified local knowledge without a model or web call. Related but
different questions deliberately pay for fresh research rather than trading
correctness for latency.
