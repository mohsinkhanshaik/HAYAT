# Voice upgrade

**Intent:** Make speech an interface around the existing agent rather than a second agent.

**Requirements:** Local-first STT/TTS, push-to-talk, devices, VAD, interruption/barge-in, concise spoken responses, shared session context, voice approvals, optional wake word/tray, hardware tests, no audio retention by default.

**Introduced:** `app/voice`, shared `AssistantService`, local Faster Whisper, Windows SAPI, session references, cancellation, and test commands.

**Current status:** Push-to-talk/hardware flow is verified; wake/tray are optional. Voice never bypasses permissions.
