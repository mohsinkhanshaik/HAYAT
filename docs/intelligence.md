# Intelligence and routing

All typed and spoken input enters `AssistantService`. `IntentRouter` produces a structured request containing intent, confidence, normalized goal, tool/research/planning flags, complexity, entities, clarification state, and measurable success criteria.

The boundary is intentionally asymmetric: uncertain non-actions fail closed into conversation, while only clear state-changing requests reach the manager. A small deterministic fast path handles immediate controls and common observable Windows workflows. The configured classification model is a fallback for less obvious meaning, not a substitute for the safety boundary.

Planning receives the interpreted goal, shared session state, selected memories, current observations, relevant researched knowledge, available tool schemas, and final success criteria. Standard or more complex model-generated plans pass a structural critic. Execution is bounded. Step verification and final goal verification are separate.

User corrections are stored as structured reflections. When a corrected retry later succeeds, the procedure is saved as an untrusted candidate; it is never auto-activated as trusted code. This is experience-based learning, not neural-weight training.

Benchmarks live under `data/benchmarks/` and can be regenerated with the scripts documented in the README. Model selection is accuracy-first, then observed latency. These small local probes guide routing but do not claim general model intelligence.
