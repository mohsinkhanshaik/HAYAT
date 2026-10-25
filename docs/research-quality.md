# Research quality benchmark

The 10-case public-web suite at `data/benchmarks/research_quality/cases.json` covers current software documentation, a stable historical fact, a current stack comparison, GitHub, an original paper, a disputed claim, API documentation, a release, scope-sensitive contradictions, and knowledge reuse.

## Measured before and after

| Metric | Before | After |
|---|---:|---:|
| Completion | 100% | 100% |
| Expected-answer correctness | 70% | 100% |
| Preferred-source ratio | 21.67% | 82.41% |
| Retrieval precision@3 | not recorded | 90% |
| Retrieval recall proxy | not recorded | 100% |
| Retrieval MRR | not recorded | 0.95 |
| Median end-to-end latency | 1,558 ms | 3,307 ms |
| Mean searches | 1.8 | 2.6 |
| Mean pages opened | 1.7 | 3.4 |
| Model calls | 0 | 0 |

Correctness improved by resolving all three previously missed cases: Apollo 11, a vLLM/TensorRT-LLM comparison, and coffee/dehydration evidence. The higher cold-run latency is the measured cost of more primary-source queries, more fetched pages, claim validation, and independence checking.

The recorded after artifact reports citation correctness 57.24% and completeness 56.88%. Manual inspection found the benchmark evaluator counted all candidate evidence, including claims omitted from the answer. The evaluator is now restricted to presented claims. The implementation itself fail-closes each citation against fetched URL, content hash, verbatim source content, and claim support. Regression tests specifically reject an absent excerpt, URL/hash mix-up, search-snippet substitution, injection text, and unsupported claims. These tests establish validator behavior; they do not prove every future web answer is true.

## Reuse measurement

The independent cold/warm/local-only benchmark measured:

| Path | Latency | Searches | Pages | Cache/knowledge behavior |
|---|---:|---:|---:|---|
| Cold web research | 2,588 ms | 2 | 1 | fetched and validated |
| Warm repeated query | 28 ms | 0 | 0 | 3 cache hits, 2 knowledge hits |
| Local-only reuse | 20 ms | 0 | 0 | fresh local knowledge |

Warm reuse reduced latency by 98.92% and eliminated network searches for the repeated query.

## Interpretation

These are operational measurements from one machine and one public-web run on 2026-08-13. Search indexes and pages change, so results are reproducible in method, not guaranteed identical in value. Preferred-source and expected-term metrics are query-specific proxies. Source independence is conservatively grouped by registrable domain and near-duplicate content; it is more honest than the old title-based diversity score, which inflated the before result.

Run from PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_research_quality.py --label after
.\.venv\Scripts\python.exe scripts\benchmark_research_reuse.py
```

Checked-in raw reports are under `data/benchmarks/research_quality/`.
