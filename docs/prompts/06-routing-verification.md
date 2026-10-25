# Routing and verification fixes

**Intent:** Stop greetings/state questions from causing actions and stop tools from declaring the overall goal complete prematurely.

**Requirements:** Structured intent, false-action golden tests, success criteria, exact runtime-state answers, tool selection evaluation, final-state verifier, bounded recovery.

**Introduced:** `app/intent`, authoritative `app/state.py`, structured plan critics, explicit goal verification, and outcome-based training/evaluation records.

**Current status:** These boundaries remain release-critical golden behavior.
