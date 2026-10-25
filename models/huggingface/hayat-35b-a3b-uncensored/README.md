---
license: apache-2.0
base_model: HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive
tags:
- gguf
- qwen3.6
- moe
- uncensored
- ollama
- hayat
- vision
pipeline_tag: image-text-to-text
---

# HAYAT 35B-A3B Uncensored (GGUF)

The main brain of [HAYAT](https://github.com/mohsinkhanshaik/HAYAT), a private, local-first AI assistant for
Windows: the uncensored Qwen3.6-35B-A3B build by HauhauCS, packaged with HAYAT's Ollama recipe (system
instructions and sampling settings).

**The weights are unchanged** from
[HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive](https://huggingface.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive),
which is based on [Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B). All credit for the
model goes to Qwen and HauhauCS. What HAYAT adds is the `Modelfile`.

## Files

| File | Size | Use |
| --- | --- | --- |
| `Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | 21.2 GB | Language model, Q4_K_M |
| `mmproj-Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-f16.gguf` | 0.9 GB | Vision projector (image input) |
| `Modelfile` | — | HAYAT's instructions and sampling settings for Ollama |

```
ollama create hayat -f Modelfile
ollama run hayat
```

## Hardware, measured on one laptop

It is a mixture-of-experts model: 35B parameters, about 3B active per token. On an RTX 5060 Laptop GPU
(8 GB VRAM) with 42 GB RAM it ran at about 31 tokens per second, with about 22 GB RAM in use and a cold load of
about 23 seconds. Expect different numbers on other hardware.

## Behaviour

The model is uncensored, and HAYAT's instructions tell it to answer every topic fully without disclaimers. They
also tell it to say plainly when something is unknown, and never to invent numbers, laws, citations or completed
actions. It can still be wrong. You are responsible for how you use it.

The instructions in the `Modelfile` were written for one person's own assistant and address him by name. Edit
the `SYSTEM` block to make it your own.

## Licence

Apache-2.0, inherited from Qwen3.6-35B-A3B and the HauhauCS build. HAYAT's `Modelfile` is also offered under
Apache-2.0.
