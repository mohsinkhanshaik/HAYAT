"""Shared voice controls and readable status for the desktop interface."""

VOICE_MODES = {
    "push_to_talk": "Push-to-talk",
    "continuous": "Hands-free",
    "wake_word": "Wake word",
}


def voice_summary(status: dict) -> str:
    mode = VOICE_MODES.get(status.get("mode"), "Voice")
    if status.get("privacy_blocked"):
        return f"Mic blocked by privacy · {mode}"
    if not status.get("enabled"):
        return f"Mic off · {mode}"
    if status.get("mode") == "push_to_talk":
        return "Push-to-talk · Hold Ctrl+Space"
    return f"{mode} · {str(status.get('state', 'ready')).replace('_', ' ')}"


def change_voice(voice, action: str) -> str:
    """Run on a worker; a mode selection alone never opens the microphone."""
    if voice is None:
        raise RuntimeError("Voice is unavailable in this runtime.")
    if action in VOICE_MODES:
        voice.set_mode(action)
    elif action == "quiet":
        voice.tts.stop()
        return "Speech playback stopped."
    elif action == "toggle" and voice.status().get("enabled"):
        voice.stop()
    elif action in {"toggle", "start", "start_continuous"}:
        if action == "start_continuous":
            voice.set_mode("continuous")
        if not voice.status().get("enabled") and not voice.start_background():
            raise RuntimeError(voice.status().get("last_error") or "Voice could not start. Check the microphone in Voice settings.")
    else:
        raise ValueError(f"Unknown voice action: {action}")
    status = voice.status()
    detail = voice_summary(status)
    if not status.get("enabled") and action in VOICE_MODES:
        detail += ". Press Start voice to use this mode."
    return detail
