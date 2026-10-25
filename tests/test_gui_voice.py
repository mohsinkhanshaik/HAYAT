from types import SimpleNamespace

import pytest

from app.gui_voice import change_voice, voice_summary


def fake_voice(*, enabled=False, error=""):
    state = {"enabled": enabled, "mode": "push_to_talk", "last_error": error}
    calls = []

    def start():
        calls.append("start")
        state["enabled"] = not bool(error)
        return state["enabled"]

    return SimpleNamespace(
        status=lambda: state.copy(), set_mode=lambda mode: state.update(mode=mode),
        start_background=start, stop=lambda: state.update(enabled=False), calls=calls,
    )


@pytest.mark.parametrize("mode", ["push_to_talk", "continuous", "wake_word"])
def test_mode_selection_keeps_microphone_off(mode):
    voice = fake_voice()
    assert "Press Start voice" in change_voice(voice, mode)
    assert voice.status()["mode"] == mode
    assert voice.calls == []


def test_failed_start_preserves_real_error():
    voice = fake_voice(error="No input device")
    with pytest.raises(RuntimeError, match="No input device"):
        change_voice(voice, "start_continuous")
    assert not voice.status()["enabled"]


def test_hands_free_start_is_idempotent_and_toggle_stops():
    voice = fake_voice()
    assert "Hands-free" in change_voice(voice, "start_continuous")
    change_voice(voice, "start_continuous")
    assert voice.calls == ["start"]
    assert "Mic off" in change_voice(voice, "toggle")


def test_privacy_status_explains_block():
    assert "blocked by privacy" in voice_summary({"privacy_blocked": True})
