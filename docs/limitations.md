# Known limitations

- Local language models can hallucinate or return invalid structured output; routing and verification reduce but cannot eliminate this.
- Screen/camera vision can misidentify controls or objects, especially with poor lighting/resolution.
- Local enrolled-face similarity is not a calibrated identity probability or liveness check. Ambiguity rejection cannot eliminate false matches; unknown people are not identified through the web.
- Speech recognition can mishear names, commands, or approval phrases.
- Local OCR uses Windows.Media.Ocr with a detected Tesseract fallback, but stylized, blurred, or low-contrast text can still be wrong.
- Research source quality, independence, and contradiction scoring are heuristics; citations prove retrieval, not universal truth.
- Website DOMs and Windows interfaces change; automation may need updated selectors.
- Clipboard content can be sensitive. It is intentionally not durable, but explicit reads still expose it to the current local task/model.
- Multi-monitor move/capture depends on Win32 window behavior and application constraints.
- Wake-word mode may react to environmental audio and is optional/off by default.
- Large local models may exceed 8 GB VRAM and spill into RAM, sharply increasing latency.
- Automatic model eviction is limited to explicit image-generation tasks and never removes model files or stops Ollama.
- Scheduled work is opt-in; a sleeping/offline PC cannot execute it.
- HAYAT is its own local Ollama identity package, but its current neural weights inherit a Gemma foundation; this is not a foundation model trained from scratch.
- Private training-data preparation and an offline LoRA/QLoRA candidate script are available, but the live corpus/checkpoint gates currently block training. Model promotion is never autonomous.
- Background learning operates only while the HAYAT runtime and computer are running; it is bounded public research, not permission to evade robots rules, authentication, paywalls, or site terms.
- Operational self-monitoring and reflection are not evidence of subjective consciousness.
- Telling the system to remember something does not retrain model weights.
- Image-source discovery covers the configured index and selected public pages,
  not every website. Commons currently returns HTTP 403 here. Exact bytes/pixels
  establish a copy, not a person's identity. Local hash distances and sharpness
  measurements are heuristics. See actual checks.
- The optional AnimateDiff v3 module works with the installed engine. A two-step
  smoke clip and an inspected 20-step short sample passed; they do not establish
  visual superiority, long-form reliability or guaranteed temporal consistency.
