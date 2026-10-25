# Verified training-data pipeline

October 23 update: a new isolated 24-step continuation and Q4/Q8 exports have
completed. Training now binds tokenizer/template/system assets, rejects shared
prompts across splits and reserved evaluation prompts, and writes completion
metadata atomically. The new exact-answer score did not improve; no main-model
promotion occurred. See the measurements and hard-test record.

This project never trains or promotes language-model weights automatically. Learning normally means retrieval, correction memory, procedure statistics, researched knowledge, and reviewed skills. HAYAT now also has an executable local adapter-candidate lane, but every weight-changing step remains gated.

Records now move through explicit `RAW`, `VALIDATED`, `REJECTED`, `TRAINING_READY`, and `HELD_OUT` states. Recursive secret redaction precedes hashing. Exact and normalized-semantic hashes prevent duplicates from entering different splits. Validation requires passed verification, deterministic correctness, or human approval; promotion is a separate operation. A stable content hash assigns the 80/10/10 train/validation/held-out split. Generated JSONL and the private registry database are ignored by Git.

## Reviewing collected examples

Automatic collection only ever creates `RAW` records. The **Training** view in the desktop sidebar shows them one at a time -- the request, what it should have been understood as or done with, and for preferences the reply given and the better one -- with **Keep for training**, **Reject** and **Skip**. Keep is human approval: the record is validated and promoted into its train/validation/held-out split. Reject keeps it as `REJECTED`, so the same example is not collected again. The view shows how many records are ready against the training gate. `TrainingDataPipeline.pending_review()` and `review()` are the same operations for scripts.

## Comparing training runs

**Compare runs** in the Training view puts two recorded evaluation runs side by side: held-out token loss, exact answers, evaluation time and each task family, with whether the second run is better, worse or the same. Runs come from the evaluation files training already writes (`data/models/<model>/evaluation*.json` and `ollama-evaluation.json`); each file gives its baseline and candidate. Nothing is re-measured. Every comparison ends with where the numbers came from: the file, when it was saved, its SHA-256 and the adapter weights' SHA-256. When two runs used a different number of held-out examples, exact-answer tasks or a different test set, the metric reads "not like-for-like" and a caveat explains why, instead of ranking them. Files are read on a worker thread; closing the window drops a result still on its way. `app.training.run_compare` offers the same `discover_runs`, `compare_runs` and `describe` to scripts.

## Private local training data

`training.private_data.enabled` permits private examples, but only through
`TrainingDataPipeline.add_private(...)` with both `owner_consent=True` and a
verified result. These records are tagged `PRIVATE_LOCAL`, marked local-only,
and exported beneath `data/training/<category>/private/` so they cannot be
mistaken for the standard corpus. Samples and provenance pass through the same
recursive credential redactor. Cloud export and automatic collection are
disabled, and a malformed private record without consent/local-only metadata
fails dataset validation.

This lane is for high-quality input/ideal-output or preference examples. A PDF,
conversation log, or private codebase is not automatically a useful supervised
example and is not silently copied into the corpus. Those materials remain in
local retrieval memory unless the owner explicitly constructs and verifies a
training example. `/training private` reports the live boundary without
revealing any sample.

Explicit user corrections create preference examples. A successful, verified repair can create a planning example and an untrusted procedural candidate. Promotion still requires review.

`/training status`, `dataset`, `validate`, `private`, and `estimate PROFILE` report readiness without launching training. `/training candidate [PROFILE METHOD]` writes a deterministic candidate manifest and evaluates the gate; it does not train. The gate counts only promoted `TRAINING_READY`/`HELD_OUT` records from the candidate's exact privacy scope and category, so standard and private examples cannot be added together to fake readiness. The pipeline benchmark processed 62 records, redacted secrets, rejected one unsupported example, detected a duplicate, produced 52 train, 4 validation, and 5 held-out records, and found zero cross-split contamination or secret leaks. These are isolated benchmark records, not production training data.

Profiles are router, planner, conversation, coding, and teaching. `requirements-training.txt` records the local PyTorch, Transformers, PEFT, TRL, bitsandbytes, Datasets, Accelerate, and Safetensors stack. `scripts/train_hayat_adapter.py` accepts only existing local directories, forces offline mode, disables Hub/W&B publishing, requires train/validation/test files, uses LoRA or QLoRA, and writes a candidate adapter plus metrics. It runs through an explicit command or explicitly launched local workflow; the application does not start it automatically.

The configured `training_base_checkpoint` must be an owner-reviewed local Hugging Face-format directory matching the foundation that Ollama will later use with the adapter. An Ollama quantized inference blob is not treated as a trainable checkpoint. No model/checkpoint download is inferred from a training request.

After explicit training, the lifecycle accepts an adapter only when `adapter_config.json` exists. It then requires a held-out baseline/candidate comparison, no quality regression, latency within 25%, a passing golden suite, and a passing safety suite. Promotion requires a separate owner approval and preserves the previous active candidate as the rollback target. At the live 2026-08-20 audit, the libraries were installed and the RTX 5060 had about 7.2 GiB VRAM free, but only four standard training records existed, with zero private-local, validation, or test records and no configured trainable checkpoint. Training was therefore correctly blocked.

## Review and export quality

Training review now filters by category, privacy scope and literal text, with
oldest/newest ordering. Promotion rechecks proof and content integrity; the runner
checks exported split contents, answer targets and cross-split duplicates before
allocating a model. Regenerate older exports to include proof fields. See the
quality upgrade.

## CUDA training environment verified on October 8, 2026

The separate `.venv-cuda` environment contains PyTorch `2.14.0+cu132`,
Transformers `5.17.0`, PEFT `0.20.0`, TRL `1.14.0`, bitsandbytes `0.50.2`,
Datasets `5.0.1`, Accelerate `1.15.0` and Safetensors `0.8.0`. The RTX 5060
Laptop GPU completed a real CUDA matrix calculation. `pip check` found no
broken requirements. The application continues to use its existing `.venv`.

Both LoRA and 4-bit QLoRA completed two optimizer steps through
`scripts/train_hayat_adapter.py` using a tiny random GPT-2 fixture and 1,000
deterministically checked synthetic examples. Each adapter saved, reloaded,
and produced finite logits; the held-out split remained reserved. This verifies
the training machinery, not HAYAT's quality or the memory fit of a 35B model.
Reports are `.test-runs/adapter-stack-cuda-20261008/report.json` and
`.test-runs/adapter-stack-qlora-20261008/report.json`. Installed versions are
recorded in `.test-runs/cuda-training-environment-20261008.txt`.

For an already prepared, matching checkpoint and approved corpus, inspect the
real inputs with the GPU environment before training:

```powershell
.\.venv-cuda\Scripts\python.exe scripts/train_hayat_adapter.py --model-dir PATH_TO_MATCHING_CHECKPOINT --dataset-dir PATH_TO_APPROVED_CORPUS --output-dir PATH_TO_NEW_CANDIDATE --method QLORA --check-only
```

At the October 8 audit, the owner registry had 91 RAW conversation examples and
zero approved conversation examples. Following the owner's training approval
and laptop-only constraint on October 9, 18 unsuitable targets were rejected
without deleting their original content. Seventy-three original conversations
remain RAW. A separate private-local seed corpus now contains 52 validated
synthetic examples (42 train, 2 validation, 8 test). These cover limited
behavior and arithmetic contracts; they do not establish general accuracy.
The 1,000-example minimum and checkpoint requirement still block training.
The earlier random-model CUDA fixtures remain outside the owner corpus and
do not count toward this gate. See the
local preparation report
for consent, provenance, resource limits and the runtime identity mismatch.

The runner additionally reads checkpoint tensor shapes without materializing
weights and checks an optimistic CUDA weight-memory floor before model loading.
It rejects pre-quantized inference containers, and refuses a model whose minimum
weight storage plus 1 GiB headroom exceeds free VRAM. This is a necessary bound,
not a guarantee that activations and optimizer state will fit. The current runner
places the whole base on device 0 and does not implement CPU/disk offloading
of base weights. `--activation-offloading` now moves saved activations to host
RAM using TRL while preserving the base-weight fit and corpus gates. See the
separate HAYAT Open training run for
the real 4B memory probe, reviewed public corpus, checkpoints and live status
locations. This does not resolve the 35B weight-memory limit.
See the full-size trial for
the measured hardware limits and source-checkpoint availability.
