from app.config import load_settings


def test_voice_configuration_is_safe_and_local_first():
    voice = load_settings()["voice"]
    assert voice["mode"] == "push_to_talk"
    assert voice["stt"]["provider"] == "faster_whisper"
    assert voice["stt"]["model"] == "base.en"
    assert voice["stt"]["vad_filter"] is False
    assert voice["stt"]["normalize_audio"] is True
    assert voice["microphone"]["minimum_push_seconds"] >= 0.4
    assert voice["microphone"]["minimum_utterance_seconds"] >= 1.5
    assert voice["microphone"]["pre_roll_seconds"] >= 0.5
    assert voice["vad"]["energy_threshold"] <= 0.0035
    # Piper rather than Windows SAPI: both keep audio on the machine, but only
    # one is this project's own. Speech no longer depends on the operating
    # system's stack or on spawning PowerShell per sentence.
    assert voice["tts"]["provider"] == "piper"
    assert voice["wake_word"]["enabled"] is False
    assert voice["privacy"]["retain_audio"] is False
    assert voice["privacy"]["log_transcriptions"] is False
