# Data locations

All default mutable data remains under `D:\AI\data`.

| Path | Purpose |
|---|---|
| `data/memory/agent.db` | tasks, actions, conversations, memories, checkpoints |
| `data/memory/learning.db` | mastery and learning activity |
| `data/memory/topics.db` | conversations, topics and messages with ids and times; how each message related to the one before; the owner's topic fixes (topics) |
| `data/memory/development.db` | hourly snapshots of the twelve measured development stages |
| `data/memory/retention.db` | spaced retention practice: each memory's review schedule and every review's cue and rank (cognitive growth) |
| `data/memory/scorecard.db` | dated scorecard measurements (accuracy, hallucination, calibration, retention, latency, resources) |
| `data/memory/forecasts.db` | HAYAT's own forecasts, their check dates and your verdicts |
| `data/memory/robustness.db` | every robustness suite run with its metric and full result (research lab) |
| `data/memory/code-defense.db` | confirmed security findings and their status, scans, and every patch tested in isolation |
| `data/memory/model-discovery.db` | every model discovery run: source, target, candidates, verdict |
| `data/benchmarks/cognitive/baseline.json` | the accepted baseline the keep-only-gains gate compares with |
| `data/memory/events.db` | durable local event bus |
| `data/memory/business.db` (wallet desk tables) | HAYAT Wallet pots, pot movements, set-aside rules, bills, bill payments and every money decision with its reasons and your answer |
| `data/memory/portfolio.db` | portfolio holdings, recorded moves, watched public addresses and daily net-worth snapshots (USD) |
| `data/memory/jobs.db` | schedules and execution state |
| `data/knowledge/knowledge.db` | sources, claims, evidence, caches |
| `data/models/` | project-local speech/model assets |
| `data/embeddings/` | embedding/index assets when produced |
| `data/downloads/` | governed downloads and audit registry |
| `data/logs/` | structured runtime logs, including the rotating process log `hayat.log` |
| `data/artifacts/` | user-requested reports and exports |
| `data/screenshots/` | screen evidence |
| `data/training/` | gated training records and datasets |
| `data/datasets/` | approved datasets |
| `data/cache/` | disposable local caches |
| `data/work/` | controlled execution and labs |
| `data/benchmarks/` | measured evaluation/hardware artifacts |
| `data/nltk_data/` | NLTK language data for text analysis (sentence splitter, stop words, VADER lexicon, tagger), downloaded by "set up language tools"; see [Text analysis](text-analysis.md) |
| `data/network/oui.csv` | IEEE MAC-prefix registry, downloaded on request so device makers can be named; see [Router and home network](network.md) |
| `data/sensing/csi/` | Wi-Fi CSI recordings (`recordings/*.jsonl`) and still-room baselines (`baselines.json`); see [Wi-Fi sensing](csi.md) |

`.env`, databases, logs, downloads, and transient/private artifacts are excluded from normal Git publication as configured by `.gitignore`. Check the actual ignore file before adding new data categories.
