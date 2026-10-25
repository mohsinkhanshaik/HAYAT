---
license: apache-2.0
base_model: huihui-ai/Huihui-Qwen3-4B-Instruct-2507-abliterated
datasets:
- OpenAssistant/oasst1
language:
- en
tags:
- gguf
- qwen3
- ollama
- hayat
- local
pipeline_tag: text-generation
---

# HAYAT Open 4B (experimental)

A small local model from [HAYAT](https://github.com/mohsinkhanshaik/HAYAT), a private, local-first AI assistant
for Windows. It is a QLoRA continuation of
[huihui-ai/Huihui-Qwen3-4B-Instruct-2507-abliterated](https://huggingface.co/huihui-ai/Huihui-Qwen3-4B-Instruct-2507-abliterated)
(itself derived from [Qwen/Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)), merged
and exported to GGUF.

**This is an experimental model, not HAYAT's main brain.** HAYAT's default writer is a 35B model; this 4B
model was trained to test local fine-tuning on a laptop GPU.

## Files

| File | Size | Use |
| --- | --- | --- |
| `hayat-open-Q4_K_M.gguf` | 2.5 GB | Smaller and faster; runs on most 6–8 GB GPUs or CPU |
| `hayat-open-Q8_0.gguf` | 4.3 GB | Closer to the merged weights |
| `Modelfile` | — | Ollama recipe (4,096-token context) |

```
ollama create hayat-open -f Modelfile
ollama run hayat-open
```

## Training

- Data: the public [OpenAssistant/oasst1](https://huggingface.co/datasets/OpenAssistant/oasst1) corpus only —
  1,558 training, 180 validation and 93 held-out examples. No private conversations were used.
- Method: QLoRA, rank 16, alpha 32 on `q_proj` and `v_proj`, learning rate 5e-6, batch 1 with 8-step
  accumulation, 1,024-token maximum. 585 optimizer steps in the original run, plus 24 in the 23 October 2026
  continuation (best checkpoint 16).
- Hardware: one RTX 5060 Laptop GPU (8 GB).

## Results, measured honestly

On the same 93 untouched public examples and 14 repeated exact-answer checks:

| Metric | Previous adapter | This model |
| --- | ---: | ---: |
| Held-out token loss | 1.2589 | 1.2587 |
| Exact answers | 10/14 | 10/14 |

The change is small, and exact-answer accuracy did not change. An earlier strict test set scored 54 of 154
exact-output checks. These results do not show better general reasoning. Treat this as a small experimental
model.

## Behaviour

The base model is abliterated (refusal behaviour reduced). It can be wrong, can invent facts, and has no
built-in safety layer. You are responsible for how you use it.

## Licence

Apache-2.0, inherited from the base models. Modifications: QLoRA fine-tuning as described above, merged into
the base weights and quantized to GGUF.
