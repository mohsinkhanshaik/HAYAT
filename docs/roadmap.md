# Capability status and remaining boundaries

## October 2026 status and upgrade priorities

The dated milestones below describe earlier states. The separate HAYAT-Open
4B package has now completed authorized local QLoRA training and held-out
evaluation; automatic promotion to the active HAYAT writer remains disabled.
Self-practice retains independently verified lessons and does not automatically
update weights. See training results.

Conversation/context recovery, evidence-aware world updates, compact GUI
repairs and the October 10 audit are implemented. The optional Colibri backend
has completed a 12-case full-size trial; it is not the default. Source:
Colibri results.

The audit and upgrade review distinguishes
implemented repairs from proposed additions and gives acceptance criteria:
source freshness monitoring, broader model holdouts, GUI accessibility and
visual checks, model promotion/rollback, and reproducible release evidence.

On October 14, 2026 those were built and checked; see
pending items completed. Targeted
retraining remains the owner's decision.

## 2026-08-13 zero-cost multimodal milestone

Implemented and verified: fail-closed `$0` API defaults; explicit OpenCV camera lifecycle; local change detection and vision; real multi-monitor/window state; ephemeral clipboard context; RAM/VRAM-aware route gating; local observability; durable events/task checkpoints/job states; read-only infrastructure incidents; safe simulated labs; governed authoritative-source downloads; and consistent SQLite backups. The live registry is dynamic; `/tools` and the runtime doctor report the exact count for the active model and vision configuration.

Windows Media OCR is available. Tesseract remains an optional unavailable fallback. Narrow model unloading for an explicit image task is enabled and reloadable; destructive model eviction, unsupervised fine-tuning, and hidden core self-modification remain intentionally disabled pending evidence and explicit authorization.

## 2026-08-17 owner-control and reliability milestone

Implemented: authority evaluation on every tool invocation; principal propagation; privacy-controlled voice/camera/network behavior; Administrator command preflight; task-scoped call budgets; rollback-safe self-repair that preserves a dirty working tree; GUI/voice/CLI startup modes; substantive deep-answer retry; live operational trace; deterministic natural-language image routing; verified PNG completion; retained image manifests and legacy discovery; and persistent queued idle/budget-aware continuous research that stores verified knowledge and reuses fresh local evidence before another research/model pass.

## 2026-08-13 state, modes, and learning milestone

Implemented and regression-tested: authoritative runtime self-state, AUTO/composable execution modes, persistent active-project context, learning topics/quizzes/weaknesses/declarative labs, hybrid retrieval with optional local embeddings, procedure experience statistics, report-only consolidation, verified training-data capture, and reusable evaluation reports. That milestone left neural embeddings inactive pending an explicit installed model; the following milestone resolved it. Automatic fine-tuning remains disabled.

The neural-intelligence phase activated local `all-minilm` embeddings, benchmark-selected hybrid retrieval, adaptive measured model routing, learner mastery and spaced review, permanent golden evaluation commands, and staged/redacted/contamination-checked datasets. Optional reranking remains disabled pending evidence. Actual fine-tuning and candidate promotion remain intentionally disabled.

## Implemented and tested

- Unified meaning-first request routing prevents greetings, questions, and failure reports from entering computer planning.
- Explicit user-goal criteria, plan criticism, final-state verification, and contradiction benchmarks.
- Shared typed/voice application and exact-window context with verified Notepad write/add/close behavior.
- Quota-aware 3 TB project storage manager, dedicated 1 TB learning ceiling, 300 GB physical free-space reserve, and protected knowledge/artifact categories.
- Measured local-model role routing and 100-case intent, tool-selection, verification, and integrated-intelligence benchmarks.

- Windows environment discovery, project-local setup, startup, and non-elevated scheduler daemon.
- Persistent natural conversation plus explicit goal execution.
- Ollama, LM Studio/OpenAI-compatible, OpenAI, Anthropic, and Gemini provider adapters.
- Separate text/reasoning/vision routing and real multimodal request plumbing.
- Dynamic typed tools, project filesystem confinement, command classification, and approvals.
- Secret-filtered bounded subprocess execution with timeout process-tree cleanup.
- SQLite tasks, actions, conversations, hybrid vector/keyword memory, logs, and reflection.
- Deterministic/model planning, structured output, bounded retries, recovery, and fail-closed verification.
- Playwright DOM and accessibility automation, screenshots, and download quarantine.
- Windows screenshots plus approval-gated mouse/keyboard primitives.
- Declarative skills, untrusted learned candidates, and explicit trust promotion.
- Persisted once/daily/weekly/interval/cron jobs and durable one-time approval reruns.
- Manager routing and explicit researcher/coder/computer/reviewer delegation.
- Local Faster Whisper STT, Piper TTS by default with Windows SAPI available as an alternative, device selection, VAD, push-to-talk, interruption, concise speech, explicit spoken approvals, optional wake phrase, and tray.
- Approved application discovery/launch and voice-driven, approval-gated Windows input.
- Provider-neutral public search with free fallback providers and optional Brave configuration.
- Safe HTML/JSON/PDF retrieval, structured table extraction, bounded crawl, resilient Playwright fallback, strict claim citations, query-specific ranking, independence scoring, temporal freshness, contradictions, caches, and durable graph-oriented local knowledge.
- Public GitHub API deep research, original-paper extraction, explain/export/import/change-monitor workflows, and review-only learned research skill candidates.

## Configuration-dependent

- Screen understanding is configured for the detected local `qwen3.5:latest` vision model. It still disables rather than simulates interpretation if that model or Ollama becomes unavailable.
- Cloud providers require user-supplied API keys. Contract tests run without secrets; live billing-bearing calls are not made automatically.
- LM Studio support is ready, but its local API server must be started by the user.
- Background execution after sign-in requires the user to approve `scripts/install_scheduler_task.ps1`.
- Voice startup after sign-in requires explicit execution of `scripts/install_startup.ps1`.
- Wake mode supports the bundled openWakeWord engine; download its optional models with `scripts/setup_wakeword.py` and select `voice.wake_word.engine: openwakeword`. Local phrase matching after STT remains available.
- Search engines and public sites can rate-limit or change markup. Failures are reported; no source is cited unless retrieval succeeds.

## Deliberate safety boundaries

- Core self-modification remains proposal/review/commit based; the agent cannot silently rewrite its own foundation.
- Generated Python is bounded and secret-filtered but not treated as hostile-code-safe. Code HAYAT did not write runs through `sandbox_run_python`, a disposable Docker container with no network, a read-only file system, no host files, dropped capabilities, an unprivileged user, and memory/CPU/process/time limits (verified live: network blocked, writes refused, 900 MB allocation killed at 512 MB, time limit enforced). Windows Sandbox is not part of Windows 11 Home; Docker Desktop must be running, and when it is not the tool refuses rather than running the code on the host. A container is strong isolation, not a VM: a kernel or Docker escape is out of its scope.
- Specialist delegation is bounded and shares the central permission/memory system; recursive autonomous agent spawning is intentionally absent.
- Financial transactions, credentials, permission bypasses, and critical Windows mutation remain denied. Administrator commands are available only when the owner launched an elevated process and explicitly approves that exact high-consequence action.
- No claim is made that stored experience trains neural model weights.
- Source-quality and contradiction scores are heuristics. High-stakes claims still require human review and authoritative primary evidence.
- Unsaved-window close/save decisions remain conservative; the assistant will not infer permission to discard work.
