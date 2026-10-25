# Project-development prompt history

These are cleaned requirement summaries, not raw private chat logs. They explain why each phase existed, the major architecture introduced, and what later work superseded. Code/tests/config are authoritative when wording conflicts.

| File | Purpose | Major result / current relationship |
|---|---|---|
| [01-initial-agent.md](01-initial-agent.md) | Build a provider-neutral Windows agent | Core plan/permission/tool/verify/memory loop remains foundational |
| [02-voice.md](02-voice.md) | Add local voice around the same agent | Push-to-talk, SAPI, sessions, interruption; still current |
| [03-speed-reliability.md](03-speed-reliability.md) | Reduce false actions and latency | Superseded by structured intent, self-state, modes, final verification |
| [04-internet-research.md](04-internet-research.md) | Active public-web knowledge acquisition | Bounded research and provenance pipeline remains current |
| [05-knowledge-engine.md](05-knowledge-engine.md) | Validate/reuse claims and freshness | Claim/evidence database and caches remain current |
| [06-routing-verification.md](06-routing-verification.md) | Ground model/tool selection and success | Current router/evaluation implement stronger form |
| [07-learning-models.md](07-learning-models.md) | Multi-model teaching/evaluation/training readiness | Current neural retrieval/teaching/training gates supersede details |
| [08-multimodal-zero-cost.md](08-multimodal-zero-cost.md) | Camera/world state/clipboard/observability at $0 API | Current phase; see tests and doctor evidence |
| [09-download-authorization.md](09-download-authorization.md) | Permit safe free dependencies without constant questions | Governed HTTPS/hash/quota pipeline implements boundary |
| [10-documentation.md](10-documentation.md) | Make operation understandable from beginner to developer | This documentation system and drift tests |
