# Colibri integration for HAYAT

Reviewed 2026-10-08 at upstream commit `bf2442915d6e3dd4cdfd2eb9c2a3d2aa44a25850`.
Repository: [JustVugg/colibri](https://github.com/JustVugg/colibri).

## Decision

Use Colibri as an optional local inference backend, initially for a controlled
text-only comparison. Keep HAYAT's session handling, source checks, tool registry,
verification and answer writer in charge. Replacing an engine alone would not fix
the conversation failures in the supplied transcript.

The connection is implemented in `app/models/colibri.py` and `app/models/router.py`.
It is disabled in `config/models.yaml`. When enabled, it requires a loopback URL
ending in `/v1`, an advertised exact model id and a configured context budget.
The Models workspace reports advertised models separately from installed or loaded
Ollama models. The installed v2.0.0 engine now passes a live protocol check with
random toy Qwen weights. This does not establish full-model speed or accuracy.

## What the source establishes

Colibri streams routed mixture-of-experts weights through memory and disk caches.
Placement can reduce resident memory requirements; it does not add neurons or
train the model. Disk throughput and cache misses can become the bottleneck.
See the [upstream explanation](https://github.com/JustVugg/colibri/blob/bf2442915d6e3dd4cdfd2eb9c2a3d2aa44a25850/README.md#what-colibri-is-and-why).

The Qwen engine identifies `qwen3_5_moe` and `qwen3_5_moe_text` geometries. Its
documented path uses original checkpoint tensors or converted Colibri containers.
The documented 35B int4 container is about 23 GB; the original bf16 checkpoint is
about 70 GB. Those are upstream estimates, not downloads measured on this machine.
HAYAT's current artifact is an uncensored GGUF variant. Matching a family label
does not establish weight, tokenizer or template equivalence. The reviewed Qwen
path does not establish direct GGUF loading or hot-loading a PEFT LoRA adapter.
See [Qwen engine documentation](https://github.com/JustVugg/colibri/blob/bf2442915d6e3dd4cdfd2eb9c2a3d2aa44a25850/docs/qwen36.md)
and [conversion source](https://github.com/JustVugg/colibri/blob/bf2442915d6e3dd4cdfd2eb9c2a3d2aa44a25850/c/tools/convert_qwen36.py).

The HTTP gateway supports model discovery, text completions, chat, streaming,
usage and completion limits. Tool support depends on the engine and generated
syntax. In particular, Qwen's forced `tool_choice` is not a reliable execution
constraint. A declared tool call must still pass HAYAT's schema and authority
checks. The gateway's GLM strict-tool mode is not a universal Qwen feature.
Thinking and prefix-cache reuse also depend on the engine and template.
See [API contract](https://github.com/JustVugg/colibri/blob/bf2442915d6e3dd4cdfd2eb9c2a3d2aa44a25850/docs/api.md).

Windows has native launch and GPU backend paths. Avoid assuming that a driver
update installs PyTorch CUDA, a compiler or a Colibri engine. See
[Windows instructions](https://github.com/JustVugg/colibri/blob/bf2442915d6e3dd4cdfd2eb9c2a3d2aa44a25850/docs/windows.md).

## This machine and the sensible first trial

Observed hardware: RTX 5060 Laptop GPU with approximately 8 GB VRAM, approximately
39.7 GiB system RAM and ample workspace disk capacity. Free RAM was only about
7.9 GiB during the first check. These are a snapshot, not a permanent allocation.
After the driver's update the CUDA driver API initialized successfully and found
one device. `nvidia-smi` then returned a permissions error; that result is kept
separate from successful CUDA initialization.

Start with the supported 35B Qwen geometry rather than a hundreds-of-billions model.
This choice is an inference from the observed memory budget and the documented
containers. It needs an actual storage and resident-memory measurement before
being called a fit. Do not run two large resident engines together to compare them:
that would distort latency and could force paging. Unload only the trial's owned
model/process between runs and leave other applications alone.

Colibri cannot turn 8 GB VRAM into enough GPU memory for ordinary 35B fine-tuning.
Expert streaming during inference and autograd during training are different
workloads. The large-model training task still needs the matching trainable
checkpoint, approved examples, and a measured training memory plan.

## Staged implementation and completion criteria

1. **Compatibility inventory.** Record the engine release/source hash, artifact
   origin and license, exact model configuration, tokenizer hash, template,
   quantization and context settings. Verify every converted shard. Check that the
   chosen converted weights actually derive from the intended HAYAT foundation.
   If they do not, label the experiment a model comparison rather than an engine
   comparison. Completion: reproducible artifact manifest and successful engine
   doctor output. No such converted HAYAT artifact is currently established.
2. **Owned local server.** Use `coli.cmd serve` on `127.0.0.1:8000` with an explicit
   model directory and model id. Start with default quality-preserving routing.
   Inspect an existing installation before downloading or rebuilding anything.
   Completion: `/v1/models` advertises the selected id and a bounded real completion
   succeeds. Record startup time and peak RAM/VRAM, then confirm clean shutdown.
3. **HAYAT protocol checks.** Enable `providers.colibri.enabled`, set its model id
   and actual server context limit, and explicitly select the backend for a trial
   role. Keep the default HAYAT answer writer until comparisons pass. Test final
   responses, empty outputs, length stops, cancellation, malformed JSON, tool-call
   arguments, server refusal, streaming disconnect and context overflow. Confirm
   that research still goes through the source-aware path. Completion: integration
   tests against the real server, in addition to the mocked protocol tests already
   passing. The present connector is text-only; vision needs a separate assessment.
4. **Equivalent evaluation.** Compare identical prompts, output ceilings and
   sampling settings. Where artifacts differ, record that confound explicitly.
   Use interleaved runs and repeat cold-start and warm-cache measurements. Record
   time to first token, prompt processing, decode tokens/second, total latency,
   process RAM, VRAM, cache behavior and errors. Use externally checked answer
   criteria; a larger parameter count or successful HTTP response is not a quality
   score. Completion: raw outputs and machine-readable benchmark, including failed
   and truncated runs, with no unsupported speed claim.
5. **Task-specific advisory routing.** If Colibri's model beats the current model
   on a verified task without unacceptable latency or resource use, add its
   measured inventory to the specialist router. Preserve provider+model identity
   in outcomes so equal model names on different engines do not share health
   evidence accidentally. Use bounded concurrency and resource reservations, and
   retain HAYAT's verified fallback behavior. Completion: regression, safety,
   latency and resource gates pass on the intended tasks. This advisory promotion
   is future work; adding the optional connection does not silently promote it.
6. **Fine-tuned weights, later.** Train only against the exact local foundation
   and verified, separated corpus. Reserve a held-out test. Evaluate the adapter
   before merging; verify that the resulting tensor names and geometry fit the
   selected inference engine before converting or packaging. Re-quantization may
   change outputs, so repeat task and tool benchmarks after conversion. Completion:
   reproducible weight provenance, measured improvement and a working rollback.

## Evaluation corpus focused on the reported failures

Use distinct training, validation and held-out conversations rather than treating
the provided transcript itself as proof of correct answers. Include:

- A request for currently listed, struggling businesses: actual listing URLs and
  dates, seller claims distinguished from verified evidence, explicit unknown
  failure causes and fair value. Directories must not count as individual deals.
- "I didn't ask you that": preserve the original acquisition subject and repair
  the answer instead of giving startup ideas.
- "Look deep in it and all sources": continue the business topic, query relevant
  sources and show what was actually fetched. Do not route to computer inventory.
- "What did you look at?": return the recorded research audit. Do not search for
  the grammar or song lyrics of the question.
- Empty, stale, conflicting or unavailable listings: report the evidence gap;
  never fill it with remembered prices or a supposed universal multiple.
- A hypothetical valuation: calculate only from explicit inputs and label the
  scenario. Do not assert the asking price is below fair value without evidence.
- Required and malformed tool calls: schemas and the shared tool boundary must
  remain authoritative even when the gateway/model ignores a tool-choice hint.

For these cases, score topic retention, retrieval relevance, factual support,
provenance, correct abstention and tool validity separately. Track latency as a
second dimension. A fluent answer must not receive credit for an invented deal.

## Tuning after the baseline

Measure storage throughput before tuning cache sizes. Test CPU, Vulkan and a
supported Windows CUDA engine under the same workload. Start with the engine's
default placement and routing semantics. Do not enable lossy expert pruning or
cache-aware route changes merely to produce a higher tokens/second figure; those
need a separate quality comparison. Tune one factor at a time and record the
command, environment, warm/cold state and repeat count. The upstream
[resource policy](https://github.com/JustVugg/colibri/blob/bf2442915d6e3dd4cdfd2eb9c2a3d2aa44a25850/docs/tuning.md)
is a starting point for experiments, not a measured recommendation for this GPU.

The brain mesh can eventually display observed backend latency, queue depth and
resource state at the Model Router node. It should not draw expert activations
unless actual engine telemetry is collected and mapped with provenance. The
current cymatics surface is explicitly illustrative and leaves the real subsystem
topology, health evidence and running-work pulses intact.

## Current completion boundary

The later full-size model trial
supersedes the fixture-only status below: a pinned 23 GB original-Qwen container
is now installed and verified, and real CPU/Vulkan comparisons completed.
Full-size CUDA startup exceeded the measured RAM guard. No model promotion or
full HAYAT fine-tuning was performed.

Completed: upstream source review, optional loopback connector, exact model
selection checks, model-page provenance and pagination, improved 3D mesh controls,
training-file preflight, and actual GPU LoRA/QLoRA fixture training with adapter
reload. Colibri v2.0.0 and its Windows CUDA overlay are installed under
`runtime/colibri-v2.0.0`; both release archives matched the SHA-256 digests
published in the GitHub release API. The installation manifest is beside the
launcher.

A random tiny Qwen fixture was built locally with the reviewed source's
`make_qwen36_tiny.py`, converted, and passed `coli doctor --deep`. HAYAT's
connector then passed exact model discovery, bounded generation, streaming,
cancellation and unknown-model rejection against the real gateway/engine.
The same check also passed with the CUDA expert tier active on the RTX 5060;
the engine log confirmed all 64 toy experts were loaded into VRAM. The owned
test processes were stopped afterward. These are protocol checks,
not a quality benchmark or a converted HAYAT foundation. Reports are in
`.test-runs/colibri-live-20261008.json` and
`.test-runs/colibri-live-cuda-20261008.json`, with the deep container check in
`.test-runs/colibri-fixture-doctor-20261008.json`. Fixture file hashes and source
provenance are in `.test-runs/colibri-fixture-manifest-20261008.json`.

Still pending: an equivalent container of HAYAT's modified foundation,
broader task benchmarks sufficient for advisory promotion, and a trained 35B
candidate. The installed original-Qwen comparison model has established
download provenance but is not that equivalent foundation. The owner registry currently contains zero approved
conversation examples (91 are RAW), against a minimum of 1,000, and the matching
trainable checkpoint is not configured. Installing an engine and passing a tiny
fixture cannot satisfy these missing inputs. The default answer writer remains
the existing HAYAT model. The optional Colibri provider remains disabled in the
default configuration; its installed comparison candidate can be started with
`scripts/start_colibri.ps1`. Repeatable measurements are available through
`scripts/benchmark_colibri.py`, with results and usage in the full-size trial.
