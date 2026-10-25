# Model providers

All providers implement `ModelProvider.generate(messages, tools=None, temperature=None)` and `list_models()`.

Default `ZERO-COST` policy exposes only local Ollama/LM Studio providers to runtime routing. Cloud adapters remain compatible but unavailable until policy is deliberately changed; no local failure can silently incur a paid fallback.

`ModelResourceManager` observes RAM and NVIDIA VRAM, records warm/cold/unavailable state, and rejects candidates that do not fit. Automatic eviction is disabled pending stronger hardware evidence.

## Ollama

`OllamaProvider` uses the native local endpoints `/api/tags`, `/api/show`, and `/api/chat`. Configure `OLLAMA_BASE_URL` and optionally `OLLAMA_MODEL`. HAYAT's own Ollama contains `hayat:latest`, its foundation `qwen3.6-35b-a3b-uncensored:q4_k_m` (also the vision and DEEP-mode model), the embedding-only `all-minilm:latest`, the previous foundation `qwen3.5:latest`, and the uncensored 27B model `hf.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF:Q4_K_M`. The active context follows the provider configuration and desktop resource mode; the budget reserves room for output and headroom within that window. Hardware memory estimates can reduce it further.

## HAYAT's own local model identity

`models/hayat/Modelfile` builds `hayat:latest` from the already installed
`qwen3.6-35b-a3b-uncensored:q4_k_m` instruction foundation. The package embeds HAYAT's direct-answer,
runtime-truth, contradiction-aware memory, public-research, and authority
contract. Vision remains a separately configured role served by
`qwen3.6-35b-a3b-uncensored:q4_k_m` directly (not through the HAYAT wrapper); an identity wrapper does not create capabilities the selected
foundation or runtime does not have. The model package reuses Ollama's existing
foundation blob rather than training or duplicating a foundation from scratch.

`escalation_model` in `config/models.yaml` names a larger advisor used only in
DEEP mode: `qwen3.6-35b-a3b-uncensored:q4_k_m` with thinking enabled.
Substantive DEEP turns consult it before HAYAT writes the reply;
pleasantries skip it. When the advisor is unavailable, HAYAT answers directly.
Advice is capped by `max_advice_tokens` (currently 4,000). `app/models/escalation.py` and `app/models/council.py`
hold this behavior.

Context limits pass through fallback and advisor wrappers dynamically. Prompt,
output and headroom share one declared window, including models configured below
4,096 tokens. The final answer and advisor prompts are checked after reference
material is added; older optional messages can be removed, while essential
instructions and the latest request cannot be silently truncated. Token counts
are conservative estimates, not provider-specific tokenizer measurements.
Cancellation stops fallback attempts, empty responses do not restore model health,
and incomplete advisor output is excluded. Routing evidence labels distinguish
direct benchmarks, inherited foundation measurements and unmeasured models;
successful task-quality updates require an explicit verification result.

This is a distinct deployed local model, but it is not an architecture trained
from scratch. `/hayat-model status` reports `weights:
inherited_foundation_weights` and `foundation_independent: false` instead of
pretending otherwise. `/hayat-model build` is explicit, checks that the base is
already installed, records the Modelfile SHA-256/build result, and never
downloads a model. An evaluated adapter can later be attached only through the
separate training lifecycle.

## LM Studio

`OpenAICompatibleProvider` uses `/v1/models` and `/v1/chat/completions`. Set `LM_STUDIO_BASE_URL`, optional `LM_STUDIO_API_KEY`, and optionally a model ID. Leaving the model blank asks the running server for its real IDs.

## Cloud and other compatible APIs

The OpenAI adapter also uses the compatible models and chat-completions shape. Set `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL`. No key is included in the repository. Additional compatible providers can reuse `OpenAICompatibleProvider` without changing agent logic.

Native Anthropic Messages and Gemini `generateContent` REST adapters are implemented. Both normalize provider-specific function calls into the common `ModelResponse.tool_calls` shape, support model discovery, structured planner output, and base64 image input. Set their blank model and key variables in `.env`; no model name is invented by configuration.

Vision is separately selected under `vision_model` in `config/models.yaml`, so a text planner and multimodal screen analyzer may use different providers. The current vision role uses Qwen3.6-35B-A3B Uncensored directly, sharing its foundation with the HAYAT identity model. Earlier Qwen 3.5/Gemma probes and the 0.0 s shared-weight vision reload measurement from 2026-10-03 are historical measurements, not the current role configuration. `AI_VISION_PROVIDER` can override that selection. Vision fails closed if the configured provider/model is unavailable.

Routing is configured in `config/models.yaml`. `AI_MODEL_PROVIDER=auto` prefers reachable local providers. Conversation, classification, reasoning, coding, research synthesis, and vision purposes have independent provider objects and environment overrides.

Question routing is deterministic before generation. Simple fact questions use
the low-latency `conversation` capability; why/how, mechanism, architecture,
comparison, diagnosis, proof, trade-off, compound, or explicitly detailed
questions use `reasoning`. Straightforward research renders validated claims
without synthesis, while explanatory research may call the research/reasoning
model. Stable exact questions use a 15-minute, 64-entry RAM-only answer cache.
Questions involving current/latest information or conversational references such
as “this,” “that,” “my,” or “your” bypass the cache.

Self-state routing is narrowly self-referential. It covers questions about the
active HAYAT runtime, model, process, permissions, tools, files, hardware,
memory, sensors, or actions HAYAT claims to have completed. Mentions of those
words in ordinary questions about external technologies or organizations do not
invoke the runtime-state service and receive normal direct answers.

`scripts/benchmark_model_routing.py` measures classification, conversation safety, strict JSON/instruction following, plan completeness, reasoning, coding repair, research strategy, throughput, loaded VRAM, and real image input when advertised. Embedding-only models are excluded from chat probes and have their own retrieval benchmark. Results and accuracy-first selections are stored under `data/benchmarks/models/`. On 2026-08-20, no tested model independently selected the complete two-step Notepad workflow, so critical common workflows remain deterministic and structurally verified. In the latest full model run HAYAT measured reasoning/conversation/coding accuracy 1.0, research strategy 0.667, JSON reliability 1.0, 4.324-second mean latency, 43.9 generated tokens/second, about 3.23 GB loaded VRAM at the time (5.5 GB at an 8k context on 2026-10-03), and a successful real-image plumbing probe. These are historical backend measurements. Current defaults use HAYAT for conversation, reasoning, coding and research synthesis, the 35B foundation for vision and DEEP advice, and deterministic classification unless an eligible measured pick is configured. A non-empty image answer proves request plumbing, not visual correctness.

Until HAYAT has a direct benchmark row, its router entry inherits its declared foundation's measurements with an explicit evidence label. Once measured, its own row wins. For conversation, summarization, and teaching, HAYAT becomes primary when its verified quality is within `model_routing.prefer_identity_within_score` of the leader; model warmth alone cannot silently replace the identity. A materially better specialist still wins, while classification, coding, and research retain task-first routing. When a Qwen candidate leads by less than `model_routing.prefer_non_qwen_within_score`, a comparable non-Qwen local model becomes primary and Qwen remains a bounded fallback. These are bounded evidence margins, not bans or model-name quality assumptions.

The runtime never downloads a model automatically. The embedding role uses the installed local `all-minilm:latest` model and never sends private memory to a cloud endpoint under the shipped policy.

The `/models` CLI command performs live discovery. The storage model inventory also reports size, quantization, advertised context, capabilities, vision support, last modification, and saved benchmark measurements. Downloads are never automatic.

`IntelligentModelRouter` additionally evaluates intent, complexity, modality, privacy, latency budget, output format, modes, measured quality, failure evidence, loaded/warm state, and VRAM. It returns primary plus at most two fallbacks. Simple Windows control stays deterministic. Verified task outcomes—not unverified model self-assessment—can adjust later routing. `/models route TEXT` explains a decision without executing the task.

The 200-case routing dataset passed 100% of its declared capability constraints at 5.966 ms mean during the final full evaluation. This is routing-policy accuracy, not generation-task accuracy. A verified-outcome probe moved coding selection to llama3 after repeated verified outcomes. First/warm request measurements were 9.237/0.485 seconds for qwen3.5 and 6.337/0.356 seconds for llama3, demonstrating why warmth is a routing input.

The embedding registry supports Ollama and opt-in OpenAI-compatible endpoints. External endpoints are never enabled for private memory by default. The installed [`all-minilm:latest`](https://ollama.com/library/all-minilm) route measured 384 dimensions, 512-token input, approximately 26.7 MB loaded VRAM, and MRR 0.8942 on the project benchmark.

## Measured on this machine (2026-10-03)

The computer has an NVIDIA RTX 5060 Laptop GPU with 8 GB of video memory and 42 GB of RAM. Measurements used HAYAT's own Ollama, a temporary copy on another port for the settings comparison, a fixed prompt, and temperature 0.

| HAYAT model at context | Setting | On GPU | Tokens per second |
| --- | --- | --- | --- |
| 8k | Ollama default | 88% | 44 |
| 8k | Flash attention + 8-bit KV cache | 100% | 57 |
| 16k | Ollama default | 84% | 33 |
| 16k | Flash attention + 8-bit KV cache | 88% | 45 |
| 32k | Ollama default | 76% | 23 |
| 32k | Flash attention + 8-bit KV cache | 81% | 30 |

HAYAT's own Ollama now starts with `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` (`app/portable.py`). At the usual 8k context the whole model fits on the GPU and replies come about 29% faster. In the same run, the vision check still read an image's name, invoice number and total exactly. Set either variable yourself to override it. The settings apply the next time the launcher starts HAYAT's Ollama; one that is already running keeps its settings until HAYAT is closed.

The DEEP-mode escalation model (27B, 17.7 GB) is more than twice the size of the GPU, so most of it runs from system memory. It is slow by nature and was not re-measured here.

## Models removed on 2026-10-03

Models that no code, configuration or route used were removed after their Modelfile, manifest and restore command were saved in `data/backups/removed-models-2026-10-03`. Every uncensored model was kept.

| Ollama | Removed | Kept |
| --- | --- | --- |
| HAYAT's own (`runtime/ollama-models`) | `gemma4:latest`, two older `hayat:rollback-*` snapshots | `hayat:latest`, `qwen3.5:latest`, `all-minilm:latest`, the 27B uncensored model, the newest rollback |
| The machine's | `gemma4:latest`, `llama3:latest`, `qwen:latest`, `qwen2.5-coder:1.5b-base`, three older rollbacks | `hayat:latest`, `qwen3.5:latest`, `all-minilm:latest`, `qwythos:latest` and its uncensored Qwythos source tag, the newest rollback |

The rollbacks shared their weights with `hayat:latest`, so removing them freed almost nothing; the space came from the four unused models. Gemma4 had not been loaded since vision moved to qwen3.5 on 2026-09-30.

## HAYAT's brain upgraded (2026-10-04)

On the owner's request for the best uncensored model, `hayat:latest` was rebuilt on Qwen3.6-35B-A3B Uncensored (HauhauCS "Aggressive" build, Q4_K_M, with its f16 vision projector). The GGUF files were already on the machine, unused; they were imported into HAYAT's store with [models/foundations/qwen3.6-35b-a3b-uncensored.Modelfile](../models/foundations/qwen3.6-35b-a3b-uncensored.Modelfile). HAYAT's SYSTEM prompt did not change. The previous package was kept as `hayat:rollback-5f4ec52abd7c`.

All runs used a temporary copy of HAYAT's Ollama with flash attention and the 8-bit KV cache, a 16K window, thinking off, and temperature 0.2. The machine's other Ollama was emptied first so it held no GPU memory.

| Test | qwen3.5 9B (old brain) | Qwen3.6 35B-A3B Uncensored (new brain) |
| --- | --- | --- |
| 12 check questions with HAYAT's prompt | 12 / 12 | 12 / 12 |
| 12 hard reasoning and knowledge questions | 9 / 12 | 11 / 12 |
| 4 coding tasks, code executed against tests | 4 / 4 | 3 / 4 |
| Writing speed | 44 tokens/s | 31 tokens/s |
| 4,687-token prompt read | 2,240 tokens/s, 2.1 s | 771 tokens/s, 6.1 s |
| Share on the GPU | 88% | 25-29% |
| Native tool call | correct | correct |
| Test invoice image | read exactly (via qwen3.5) | read exactly, itself |

The old brain's misses were the 2022 World Cup winner (it said France), the Python version released in October 2025 (it did not know), and a compound-interest total off by one dollar. The new brain's misses were one long calculation where it ran out of room, and an interval-merging function that failed one test.

**DEEP-mode advisor.** With eight check questions at the DEEP settings, the 35B answered 8 of 8 at 22-37 tokens/s, averaging 10 s per answer. The 27B answered 7 of 8 at 3.2 tokens/s, averaging 70 s, and ran out of room on one. The advisor is now the 35B foundation.

**One copy in memory.** The vision and DEEP roles name the foundation, whose weights `hayat:latest` shares. In the live check a vision request and a tool call straight after a chat turn both loaded in 0.0 s, and Ollama listed a single 22 GB model.

**Costs.** A cold load takes about 23 s instead of 6 s. After that the model stays loaded for 30 minutes. Long prompts take about three times longer to read, so a reply with a large context starts a few seconds later. The model uses about 22 GB of the 42 GB of RAM while loaded.

**Settings measured on the new brain (2026-10-04).** Same 12 hard questions plus 10 coding tasks whose code was executed against tests, on HAYAT's live Ollama at its 8K window:

| Setting | Reasoning | Code | Time for all 22 |
| --- | --- | --- | --- |
| As shipped (Qwen's recommended sampling) | 10 / 12 | 10 / 10 | 250 s |
| Repetition penalties off | 11 / 12 | 9 / 10 | 138 s |
| Thinking on | 12 / 12 | 10 / 10 | 728 s |

Turning the penalties off made no reliable difference, so the shipped settings stayed. Thinking was the only change that fixed every miss, at about three times the time, so it is on for the DEEP-mode advisor only (`escalation_model.think`). A live check through HAYAT's own advisor code answered the divisible-by-3-or-5 sum correctly (9,368) in 69 s, using 2,800 of its 4,000 tokens.

**Fixes found while checking the brain in the desktop app.** The resource mode resized every model except the DEEP advisor, which is a separate copy, and the DEEP preload asked for the configured window. With one shared 35B foundation, each mismatch would have reloaded it, about 23 s each time. The advisor is now resized with the others, and its preload uses its current window. The window also starts loading the brain in the background when it opens, so the first question does not wait for it.

**Reasoning rules added to HAYAT's instructions (2026-10-04).** On the owner's request for answers that are uncensored but truthful, that weigh evidence and that backtrack, the Modelfile gained two paragraphs. Uncensored means nothing true is hidden and nothing false is added. Multi-step problems are worked through and checked, and when a check fails HAYAT goes back to the step that broke; what the evidence shows, what is inferred, and what is unknown are kept apart. On the same 22-item set this scored 11 / 12 reasoning and 10 / 10 code in 190 s, against 10 / 12 and 10 / 10 in 250 s before. Hard questions in AUTO mode also go to DEEP, where the advisor thinks first. The previous package is `hayat:rollback-7c81f7a741e2`.
