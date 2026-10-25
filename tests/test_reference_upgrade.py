"""Behavioral regressions for the October reference-driven workspace upgrade."""
from types import SimpleNamespace
import threading

import numpy as np
import pytest

from app.agent.response_guidance import guidance
from app.business_model import DEFAULTS, evaluate
from app.gui_brain import LAYERS, neighborhood
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger
from app.voice.manager import VoiceManager
from app.voice.models import AudioData, Transcription
from app.voice.vad import EnergyVoiceActivityDetector


def assumptions(**changes):
    return {**DEFAULTS, "customer": "Small local shops", "problem": "Outdated product photos",
            "price": "500", "direct_cost": "50", "acquisition_cost": "25", "hours_per_sale": "4",
            "hourly_value": "25", "capacity_hours": "40", "monthly_leads": "100",
            "conversion_percent": "20", "fixed_cost": "200", "target_profit": "2000", **changes}


def test_economics_prices_owner_time_caps_demand_and_rounds_break_even_up():
    result = evaluate(assumptions())
    base = result["scenarios"][1]
    assert base["demand"] == 20 and base["capacity"] == base["sales"] == 10
    assert base["contribution"] == "325.00 USD"
    assert base["profit"] == "3,050.00 USD"
    assert base["cash_surplus"] == "4,050.00 USD"
    assert base["break_even_sales"] == 1 and base["target_sales"] == 7
    assert base["target_feasible"] and base["capacity_limited"]
    assert result["scenarios"][0]["profit"] == "2,550.00 USD"
    assert result["scenarios"][2]["profit"] == "3,550.00 USD"
    assert evaluate(assumptions(fixed_cost="326"))["scenarios"][1]["break_even_sales"] == 2


@pytest.mark.parametrize("changes", [{"price": "NaN"}, {"fixed_cost": "Infinity"}, {"price": "-1"},
                                     {"hours_per_sale": "0"}, {"hours_per_sale": "1e-10000"},
                                     {"monthly_leads": "1.2"}, {"conversion_percent": "101"},
                                     {"currency": "US"}, {"unknown": "field"}])
def test_bad_assumptions_are_actionable_errors(changes):
    with pytest.raises(ValueError):
        evaluate(assumptions(**changes))


def test_nonpositive_unit_contribution_cannot_claim_break_even_or_target():
    base = evaluate(assumptions(price="100"))["scenarios"][1]
    assert base["break_even_sales"] is None and base["target_sales"] is None
    assert not base["target_feasible"]
    base = evaluate(assumptions(monthly_leads="0"))["scenarios"][1]
    assert base["sales"] == 0 and base["profit"] == "-200.00 USD"


def test_saving_plan_survives_reopen_without_recording_fake_income(tmp_path):
    def ledger():
        return BusinessLedger(tmp_path / "business.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
    first = ledger()
    before = first.report("all")
    first.save_business_model(assumptions())
    second = ledger()
    assert second.business_model()["assumptions"]["price"] == "500"
    assert second.report("all") == before
    assert "business_model" not in second.profile()
    preview = second.business_model({"price": "600"})
    assert preview["assumptions"]["monthly_leads"] == "100"
    assert second.business_model()["assumptions"]["price"] == "500"


def test_confidence_contract_accepts_optional_listener_and_rejects_invalid_scores():
    assert VoiceManager._recognition_confidence(Transcription("hello", confidence=0.8)) == 0.8
    assert VoiceManager._recognition_confidence(SimpleNamespace(probability=0.9)) == 0.9
    assert VoiceManager._recognition_confidence(SimpleNamespace(confidence=float("nan"))) == 0
    assert VoiceManager._recognition_confidence(Transcription("hello")) is None


@pytest.mark.parametrize("text", ["你好，帮我查一下", "नमस्ते दुनिया", "Привет мир", "مرحبا كيف حالك"])
def test_multilingual_words_are_not_rejected_as_empty_noise(text):
    voice = VoiceManager.__new__(VoiceManager)
    voice.config = {}
    assert not voice._probably_noise(text, 0.9)


def test_hands_free_accepts_real_stt_confidence_contract():
    voice = VoiceManager.__new__(VoiceManager)
    voice.config = {"vad": {}}
    voice.cancellation = SimpleNamespace(shutdown=threading.Event())
    voice.recorder = SimpleNamespace(record_until_silence=lambda *args: AudioData(np.ones(160), 16000))
    voice.tts = SimpleNamespace(is_speaking=False)
    voice.set_state = lambda state: None
    voice.transcribe = lambda audio: Transcription("Explain this clearly", "en", 0.9)
    accepted = []
    voice.submit_transcript = accepted.append
    voice._continuous_cycle()
    assert accepted == ["Explain this clearly"]


def test_listening_tuning_changes_actual_detector_and_rejects_partial_bad_settings():
    voice = VoiceManager.__new__(VoiceManager)
    voice.config = {"vad": {"silence_seconds": 0.8}}
    voice.vad = EnergyVoiceActivityDetector(0.02)
    voice.recorder = SimpleNamespace(vad=voice.vad)
    assert not voice.vad.analyze([0.005] * 100).is_speech
    voice.configure_listening(silence_seconds=1.4, energy_threshold=0.003)
    assert voice.vad.analyze([0.005] * 100).is_speech
    assert voice.config["vad"]["silence_seconds"] == 1.4
    with pytest.raises(ValueError):
        voice.configure_listening(silence_seconds=0.1, energy_threshold=0.004)
    assert voice.vad.minimum_threshold == 0.003
    assert voice.config["vad"]["silence_seconds"] == 1.4


def test_dependency_depth_expands_only_along_real_contracts():
    one, two, three = (neighborhood("interface", hops) for hops in (1, 2, 3))
    assert {"interface"} < one <= two <= three <= set(LAYERS)
    assert two != one
    assert neighborhood("invented", 1) == set()


def test_beast_critique_is_distinct_and_voice_guidance_is_speakable():
    deep = guidance("Compare these plans", {"DEEP"})
    beast = guidance("Compare these plans", {"DEEP", "ADVERSARIAL", "VOICE"})
    assert "counterexample" not in deep and "counterexample" in beast
    assert "spoken conversation" in beast
    assert "counterexample" not in guidance("thanks", {"DEEP", "ADVERSARIAL"})
