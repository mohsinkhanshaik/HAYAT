# Development guide

Preserve the shared core: new inputs must enter `AssistantService`; new actions must use `ToolRegistry`, permissions, observed results, and verification.

## Add a tool

Inspect `app/tools/registry.py` and an existing registration such as `app/downloads/manager.py`. Register a unique name, plain description, JSON Schema, risk, permission category, and handler. The handler should return structured evidence and never claim unobserved success. Add permission policy, planner exposure, tests, command/docs if user-facing.

## Add a model provider

Implement `ModelProvider` from `app/models/base.py`: `generate`, `list_models`, and optionally `generate_vision`. Return `ModelResponse` with actual provider/model. Register through `ModelRouter`, capability inventory, cost policy, resource fit, and fallback tests. Do not mutate a shared provider's model role.

## Add a mode

Extend `ExecutionMode`/profiles in `app/modes.py`, intent resolution tests, router behavior, `/mode` help, docs, and evaluation cases. Modes describe constraints/priorities; they must not bypass permissions.

## Add a skill

Skills under `data/skills` are declarative YAML. Validate inputs/tools/success criteria with the existing loader. Generated candidates stay untrusted until `/skill promote NAME`.

## Add research, camera, or embedding backends

- Search provider: implement structured `search(query, limit, recency, domains)` and register in the policy-filtered chain.
- Camera: implement enumerate/open/read/release with explicit state and privacy cleanup.
- Embedding: detect actual endpoint capability/dimension/max input, version hashes, cache unchanged content, forbid cloud for private memory by default.

## Testing

```powershell
.\.venv\Scripts\python.exe -m compileall -q app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\doctor.py
```

Unit tests mock external state. Integration tests use real SQLite/tool boundaries. Hardware tests must say what hardware was actually exercised. Evaluations cover behavior/quality rather than only code execution. Add real failures as high-value golden regressions.

## Documentation completion

Every user-visible feature needs code, automated tests, README/deeper docs, and actual verification. Run `tests/test_documentation.py`; check all examples against current CLI/config. Keep commits scoped and rollbackable.
