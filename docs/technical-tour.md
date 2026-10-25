# Deep technical tour

1. `main.py` selects CLI, voice, combined, tray, or one-goal operation.
2. `app/runtime.py` builds one dependency graph: config, cost policy, providers, memory, tools, permissions, research, perception, learning, jobs, events, and assistant.
3. `app/assistant.py` receives both text and voice transcriptions. `app/intent/router.py` classifies conversation, state, memory, learning, diagnosis, research, coding, and action intent. False-action guards keep greetings/statements out of tools.
4. `app/modes.py` resolves AUTO or explicit composition. `app/models/intelligent_router.py` maps capability/modality/privacy/resource constraints to a primary and bounded local fallback.
5. `app/agent/context.py` combines goal, session, live state, retrieved memory, knowledge, and history. `planner.py` builds typed steps/success criteria and critiques complex plans.
6. `app/safety/permissions.py` returns ALLOW, ASK, or DENY before `executor.py` invokes a registry handler.
7. The tool returns structured stdout/state/evidence. `verifier.py` checks the step; the core also checks final goal criteria. Failed steps classify/replan with bounded retries.
8. `app/memory/database.py` stores sanitized actions, task state, checkpoints, experience, and reflection. `app/events.py` emits local durable events. Ephemeral clipboard content is redacted before these boundaries.
9. `app/assistant.py` formats a complete display response and a shorter spoken response.

## Perception order

```text
Windows/DOM structure > OCR > pixels > local vision inference
```

Displays use Win32 geometry; Windows actions prefer UI Automation; browser research prefers APIs/HTML/DOM; camera uses cheap frame difference before vision.

## Memory retrieval

`app/memory/retrieval.py` indexes multiple persisted object types with metadata/content hashes. Query candidates combine lexical, Ollama embeddings, hashed-semantic fallback, entities, active project, recency, confidence, importance, and verified successful use. Reranking stays disabled unless its benchmark justifies latency.

## Durable operations

SQLite task checkpoints capture the plan and last redacted event after every action. The job repository records QUEUED/RUNNING/PAUSED/WAITING/COMPLETED/FAILED/CANCELLED executions. Restart never implies automatic approval or unsafe resumption.
