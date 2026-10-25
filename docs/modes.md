# Execution modes

Modes change routing, context depth, and model role without creating a second agent or bypassing permissions.

- `AUTO`: chooses from intent and complexity.
- `FAST`: small history/retrieval budget, fast model role, direct answer capped at 100 words.
- `BALANCED`: normal execution.
- `DEEP`: larger context, decomposition, alternative explanation, and self-check. Questions and requests are answered by the escalation model in `config/models.yaml` first, with HAYAT as the fallback; pleasantries stay on HAYAT, and choosing DEEP preloads the model (see the README section "The DEEP-mode escalation model").
- `RESEARCH`, `CODER`, `COMPUTER`, `VISION`, `VOICE`: capability-focused routing.
- `LOCAL_ONLY`, `PRIVATE`: preserve local/private routing policy.
- `ADVERSARIAL`: deeper contradiction checking.
- `TEACH`, `LAB`, `MENTOR`: learning behavior.
- `INCIDENT`: read-only evidence, hypothesis testing, and verified troubleshooting before any proposed repair.

Modes compose with `+`, except `AUTO`, which must stand alone. Examples:

```text
/mode auto
/mode deep + research
Switch to deep researcher mode.
Stay local.
```

`/response concise` changes display verbosity and is separate from execution mode. Configuration persists in the ignored `data/memory/runtime_profile.json` file. The mode benchmark measures AUTO decisions, router latency, memory retrieval, local generation, live public research, and real screenshot analysis.

`INCIDENT` can compose with `DEEP`; like every mode, it changes priorities but cannot grant tool permission.

`/mode BEAST` (also `beast mode`) selects `DEEP + ADVERSARIAL`. The chat's
**Beast mode** control toggles this preset while preserving local/privacy and
specialist modes. `GOD` remains accepted for compatibility.
