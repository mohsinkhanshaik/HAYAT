# Speed and reliability upgrade

**Intent:** Make normal conversation fast while preventing accidental actions and fake success.

**Requirements:** Deterministic controls, structured intent, concise responses, bounded planning/retries, real self-state, observed final state, latency measurement, failure-driven tests.

**Introduced:** Conversation/action boundary, fast paths, session grounding, verification evidence, and regression metrics.

**Superseded by:** Current AUTO/composable modes and intelligent local model router choose the lightest sufficient path.
