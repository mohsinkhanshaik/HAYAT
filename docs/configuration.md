# Configuration reference

Configuration is layered from committed YAML plus optional `.env` values. Never commit `.env` or real credentials.

## `config/settings.yaml`

| Option | Type/default | Effect and safe guidance |
|---|---|---|
| `max_steps` | integer / `20` | Hard plan-step bound; keep bounded. |
| `max_retries_per_step` | integer / `2` | Prevents infinite recovery loops. |
| `command_timeout_seconds` | integer / `60` | Tool process timeout. |
| `model_timeout_seconds` | integer / `120` | Local model request timeout. |
| `active_mode` | string / `AUTO` | Initial composable operating mode. |
| `proactive_assistance` | string / `SUGGEST` | Offers help; does not silently act. |
| `resources.automatic_model_eviction` | boolean / `true` | For explicit image tasks, ask idle Ollama models to unload; model files/services remain intact and reload on demand. |
| `model_routing.prefer_non_qwen_within_score` | number / `0.03` | Prefer a non-Qwen local candidate only when its measured score is within this bounded margin. |
| `model_routing.prefer_identity_within_score` | number / `0.02` | For identity-facing tasks, prefer `hayat:latest` only when its verified quality is within this margin of the task leader. |
| `email.smtp_host`, `email.smtp_port`, `email.security`, `email.username`, `email.from_address` | strings / empty, `587`, `starttls` | The owner's own mail account for `email_send` (Gmail: `smtp.gmail.com`, 587, starttls, with an app password). The password is never in this file: store it with `python main.py --secret-set email_smtp_password --secret-scope email:send`. Empty host means the account saved from **Business -> Email setup** is used instead (`data/memory/email-account.json`, address and server only); if neither exists, the tool says how to set it up. |
| `owner_mode.enabled` | boolean / `true` | Pre-authorize passive through recoverable local-owner work; high-consequence and prohibited categories remain protected; send/submit/post the owner's current message asked for runs without a second prompt. |
| `training.private_data.enabled` | boolean / `true` | Permit explicitly consented and verified private-local training examples. |
| `training.private_data.local_only` | boolean / `true` | Keep private training records and use on this machine. |
| `training.private_data.explicit_consent_required` | boolean / `true` | Reject private samples without a direct owner-consent flag. |
| `training.private_data.cloud_export_allowed` | boolean / `false` | Hard boundary for private corpus export. |
| `training.training_base_checkpoint` | string / empty | Owner-selected local Hugging Face-format checkpoint; empty keeps adapter training blocked and never triggers a download. |
| `storage.maximum_total_bytes` | integer / `3000000000000` | Maximum project allocation, not preallocation. |
| `storage.category_quotas.learning` | integer / `1000000000000` | Dedicated durable-learning ceiling within the project allocation. |
| `storage.reserve_free_space_bytes` | integer / `300000000000` | Disk space the project must preserve. |
| `browser.headless` | boolean / `true` | Isolated research browser visibility. |
| `research.max_concurrency` | integer / `3` | Bounded public requests. |
| `learning.continuous.research_level` | string / `deep` | Evidence depth for idle research; still bounded by its cycle/request budgets. |
| `learning.continuous.anticipate_followups` | boolean / `true` | Queue bounded likely follow-up angles for safe public-world questions. |
| `learning.continuous.anticipated_per_request` | integer / `2` | Prediction candidates per accepted request; implementation clamps this to 0-3. |
| `learning.continuous.idle_reflection` | boolean / `true` | When otherwise idle, queue evidence refreshes for real conflicts and stale knowledge. |
| `learning.continuous.reflection_candidates` | integer / `2` | Maximum deterministic reflection topics proposed per empty-queue cycle; implementation clamps this to 1-5. |
| `learning.continuous.budget.min_idle_seconds` | integer / `60` | Tiebreaker only, consulted when the CPU is already busy. Turn-scoped pause/resume, not keyboard idle time, keeps background research off the foreground response path. |
| `learning.continuous.budget.idle_retry_seconds` | integer / `15` | Recheck a newly woken queue promptly without bypassing the idle gate. |
| `scheduler.enabled` | boolean / `true` | Run enabled jobs under restricted background authority. |
| `proactive.event_triggers_enabled` | boolean / `true` | Subscribe enabled, cooldown/rate-limited event rules. |
| `browser_extension_bridge.enabled` | boolean / `true` | Start the token-authenticated localhost extension bridge. |
| `connectors.enabled` | boolean / `true` | Make installed connector manifests available; accounts still require setup. |
| `mcp_gateway.enabled` | boolean / `true` | Allow explicitly registered MCP servers. |
| `mcp_gateway.builtin_web_research.enabled` | boolean / `true` | Register the built-in zero-cost, read-only web research MCP server. |
| `mcp_gateway.builtin_web_research.google_results_page_automation` | boolean / `false` | Documentary invariant: unattended Google result-page scraping remains disabled. |
| `computer_control.enabled` | boolean / `true` | Still subject to permissions. |
| `vision.enabled` | boolean / `true` | Requires configured capable local model. |
| `camera.enabled` | boolean / `true` | Allows explicit use; does not activate camera. |
| `camera.device` | integer / `0` | OpenCV camera index. |
| `camera.retain_frames` | boolean / `false` | Temporary observation privacy default. |

`cost_policy` defaults every paid/cloud switch to false. `memory_retrieval.weights` controls measured hybrid ranking; do not call new weights optimal until benchmarked. `research.budgets` bound searches/pages/time/bytes for fast, normal, and deep modes. Voice subsections select local STT/TTS, devices, VAD, wake phrase, approvals, and retention.

## `config/models.yaml`

Role entries (`default_model`, conversation, classification, reasoning, coding, research synthesis, embedding, and vision) select provider/model. `identity_model` records HAYAT's package, foundation, and truthful non-independent weight provenance. `auto` uses policy-filtered capability routing. Current embeddings use local Ollama `all-minilm:latest`; default/conversation use `hayat:latest`, vision uses `qwen3.5:latest` (`gemma4:latest` stays installed as a fallback), reasoning/coding/research synthesis use llama3 when selected by benchmarks, and Qwen remains a classification/fallback candidate. Provider blocks define base URLs and environment substitutions.

## `config/permissions.yaml`

Maps categories to `ALLOW`, `ASK`, or `DENY`, approved roots, and protected paths. Changes here alter the real security boundary. Prefer adding a narrow permission category over weakening a broad one.

## `.env`

Copy `.env.example` only when an override is needed. Supported values include:

```dotenv
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=hayat:latest
LM_STUDIO_BASE_URL=http://localhost:1234/v1
LM_STUDIO_API_KEY=lm-studio
LM_STUDIO_MODEL=
AI_MODEL_PROVIDER=auto
AI_CONVERSATION_PROVIDER=
AI_CLASSIFICATION_PROVIDER=
AI_REASONING_PROVIDER=
AI_VISION_PROVIDER=
AI_PROJECT_ROOT=
AI_SEARCH_PROVIDER=auto
BRAVE_SEARCH_API_KEY=
AI_VOICE_STT_MODEL=base.en
AI_VOICE_LANGUAGE=auto
HAYAT_PARENT_PIN=
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=
EMBEDDING_MODEL=
```

OpenAI/Anthropic/Gemini variables exist for optional adapters but are blocked by default policy. Use placeholders such as `YOUR_API_KEY_HERE` in documentation—never real secrets.

## Sanitized full example

```yaml
active_mode: AUTO
cost_policy:
  mode: ZERO-COST
  paid_api_allowed: false
  cloud_inference_allowed: false
  paid_search_allowed: false
  cloud_embeddings_allowed: false
  cloud_tts_allowed: false
  cloud_stt_allowed: false
camera:
  enabled: true
  device: 0
  retain_frames: false
scheduler:
  enabled: true
proactive:
  event_triggers_enabled: true
browser_extension_bridge:
  enabled: true
  host: 127.0.0.1
  port: 8765
voice:
  enabled: true
  version: 2
  mode: push_to_talk
  stt:
    vad_filter: false
    normalize_audio: true
    maximum_gain: 8.0
  microphone:
    minimum_push_seconds: 0.4
    minimum_utterance_seconds: 1.5
    pre_roll_seconds: 0.5
  vad:
    energy_threshold: 0.0035
    silence_seconds: 1.4
    speech_start_timeout: 8.0
  privacy:
    retain_audio: false
    log_transcriptions: false
```
