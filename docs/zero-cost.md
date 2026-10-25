# Zero-cost operation

The default `ZERO-COST` policy requires no paid API. Local Ollama/LM Studio endpoints, local speech, local embeddings, SQLite, Windows APIs, and free public research form the normal path.

```text
/cost
```

Expected policy fields are all false: paid API, cloud inference, paid search, cloud embeddings, cloud TTS, and cloud STT. Runtime provider discovery is filtered by policy, so an OpenAI/Anthropic/Gemini key cannot cause a silent paid fallback.

HAYAT now accepts only `ZERO-COST` mode. Unknown modes, enabled paid flags,
and ambiguous values such as the text `"false"` stop initialization. Keys in
the environment cannot enable paid model or subscription-search fallback.
Direct model adapters and embedding requests also enforce loopback HTTP(S),
reject credentials in URLs, and ignore environment proxies. Remote endpoints
cannot become local simply by being named Ollama or LM Studio.

Ollama inventories exclude cloud models and remote aliases, and explicit cloud
model tags are blocked. HAYAT-owned Ollama launches set `OLLAMA_NO_CLOUD=1`,
the [documented cloud-disable switch](https://github.com/ollama/ollama/blob/main/docs/faq.mdx).
An already-running server is left alone. A separately configured localhost
proxy can itself contact another service; local endpoint admission cannot audit
that server's internals or an external account's billing.

Runtime connector calls with unverified billing are blocked. MCP execution
admits only the exact project-local public research server manifest, without
secret environment mappings. Other services must be used manually outside
HAYAT; approval of a tool call does not override the cost guard.

Optional startup is manual: `background_services.autostart: false` prevents
automatic schedules, event triggers, continuous research, code monitoring,
intelligence feeds, discovery, job watching, price alerts, model preloading,
phone/browser server startup, and business briefs. Stored schedules remain
saved. Start required capabilities deliberately using their existing controls.
Automatic adapter preparation is also off. Chat and explicitly requested
local/public tools remain available. Microphone startup still asks each session.

`$0 API cost` is a policy estimate, not a provider billing receipt. Electricity,
hardware, internet access, storage and your time still have real costs. These
checks do not inspect subscriptions or promise that actions you take manually
on another service are free. Private memories are never automatically sent to
cloud embedding services.

The source of truth is `config/settings.yaml` under `cost_policy` and
`background_services`; enforcement is in request adapters, model routing,
research-provider construction, integration gateways and runtime doctor checks.

The verification report records the
free/local checks and the remaining ONNX package metadata warnings.
