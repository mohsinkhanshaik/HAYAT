"""Speech that belongs to this project rather than to the operating system.

Windows SAPI never sent audio anywhere, but it tied the assistant's voice to
Microsoft's speech stack and spawned a PowerShell process for every sentence.
Piper is MIT, the voices are open weights on this disk, and synthesis happens
in-process. These tests pin the properties that make that swap real: it is the
default, it needs no system speech, and it is honest when a voice is missing.
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.voice.tts import (
    DEFAULT_PIPER_VOICE,
    PiperTTS,
    WindowsSapiTTS,
    create_tts_provider,
)


def _piper(tmp_path: Path | None = None, **overrides) -> PiperTTS:
    config = {"provider": "piper", "voice": DEFAULT_PIPER_VOICE}
    if tmp_path is not None:
        config["voice_root"] = tmp_path
    return PiperTTS(config | overrides)


# ------------------------------------------------------------------ defaults


def test_the_local_engine_is_the_default_not_system_speech():
    provider = create_tts_provider({})
    assert isinstance(provider, PiperTTS)
    assert provider.name == "piper"


def test_system_speech_remains_reachable_by_configuration():
    """Replacing SAPI must not mean removing the fallback that works."""
    assert isinstance(create_tts_provider({"provider": "windows_sapi"}), WindowsSapiTTS)


def test_auto_falls_back_only_when_no_local_voice_exists(tmp_path):
    provider = create_tts_provider({"provider": "auto", "voice_root": tmp_path})
    assert isinstance(provider, WindowsSapiTTS), "with no voice downloaded, silence is worse"


def test_auto_prefers_the_local_engine_when_a_voice_is_present(tmp_path):
    (tmp_path / f"{DEFAULT_PIPER_VOICE}.onnx").write_bytes(b"stub")
    provider = create_tts_provider({"provider": "auto", "voice_root": tmp_path})
    assert isinstance(provider, PiperTTS)


def test_an_unknown_provider_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        create_tts_provider({"provider": "some_cloud_service"})


# -------------------------------------------------------------- voice files


def test_a_missing_voice_is_reported_honestly_without_downloading(tmp_path):
    ok, detail = _piper(tmp_path).ensure_voice(download=False)
    assert not ok
    assert "not downloaded" in detail


def test_voices_are_listed_from_disk(tmp_path):
    for name in ("en_US-lessac-medium", "en_GB-alan-low"):
        (tmp_path / f"{name}.onnx").write_bytes(b"stub")
    assert _piper(tmp_path).list_voices() == ["en_GB-alan-low", "en_US-lessac-medium"]


def test_status_states_plainly_that_nothing_leaves_the_machine(tmp_path):
    status = _piper(tmp_path).status()
    assert status["local_only"] is True
    assert status["system_speech"] is False
    assert status["provider"] == "piper"


def test_the_voice_lives_where_the_owner_can_see_it():
    """A voice should be a file that can be inspected, copied, or deleted."""
    from app.voice.tts import VOICE_ROOT

    assert VOICE_ROOT.name == "voices"
    assert VOICE_ROOT.parent.name == "data"


# ------------------------------------------------- real synthesis, if present


def _installed() -> PiperTTS:
    provider = _piper()
    if not provider.installed:
        pytest.skip("no Piper voice downloaded on this machine")
    return provider


def test_real_synthesis_produces_playable_audio(tmp_path):
    provider = _installed()
    out = provider.synthesize_to_file("Local speech, no system dependency.", tmp_path / "s.wav")

    assert out.is_file() and out.stat().st_size > 1000
    with wave.open(str(out)) as handle:
        assert handle.getnframes() > 0
        assert handle.getframerate() >= 16000


def test_synthesis_is_faster_than_real_time(tmp_path):
    """It has to outrun speech to be usable in conversation.

    Measured at 4.2x on this machine; the assertion sits well below that so a
    busy machine does not fail the suite.
    """
    import time

    provider = _installed()
    text = ("This sentence is long enough to measure a meaningful ratio between "
            "the time taken to synthesise it and the time taken to say it aloud.")
    started = time.perf_counter()
    out = provider.synthesize_to_file(text, tmp_path / "speed.wav")
    elapsed = time.perf_counter() - started

    with wave.open(str(out)) as handle:
        audio_seconds = handle.getnframes() / handle.getframerate()

    assert audio_seconds > 3.0, "the sample should be long enough to time"
    assert elapsed < audio_seconds, f"synthesis took {elapsed:.2f}s for {audio_seconds:.2f}s of speech"


def test_the_model_is_loaded_once_and_reused(tmp_path):
    """Reloading 63MB per sentence would make conversation stutter."""
    provider = _installed()
    provider.synthesize_to_file("First.", tmp_path / "a.wav")
    first = provider._voice
    provider.synthesize_to_file("Second.", tmp_path / "b.wav")
    assert provider._voice is first


def test_empty_text_says_nothing_and_does_not_raise():
    provider = _piper()
    provider.speak("   ")
    assert not provider.is_speaking


def test_synthesis_needs_no_system_speech_process(tmp_path, monkeypatch):
    """The point of the change: no PowerShell, no SAPI, per utterance."""
    import subprocess

    provider = _installed()

    def forbidden(*args, **kwargs):
        raise AssertionError("local speech must not spawn a system process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    provider.synthesize_to_file("No system speech here.", tmp_path / "clean.wav")
