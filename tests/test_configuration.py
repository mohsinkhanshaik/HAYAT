from app.config import PROJECT_ROOT, load_settings, load_yaml


def test_configuration_files_load():
    settings = load_settings()
    permissions = load_yaml("config/permissions.yaml")
    models = load_yaml("config/models.yaml")
    assert settings["project_name"] == "AI"
    assert settings["background_services"]["autostart"] is False
    assert settings["training"]["automatic_training"] is False
    # Granted outright on 2026-08-21, at Mohsin's explicit instruction. This
    # assertion is kept rather than deleted because it is the record of a
    # decision: an elevated shell can switch off the controls that supervise
    # the agent, so if this ever reads ASK again it should be because someone
    # chose that, not because it drifted.
    assert permissions["permissions"]["powershell_admin"] == "ALLOW"
    # Carved out of that grant: admin work runs, ending the session asks.
    assert permissions["permissions"]["restart_computer"] == "ASK"
    assert permissions["permissions"]["shutdown_computer"] == "ASK"
    assert permissions["permissions"]["delete_system_file"] == "DENY"
    assert permissions["permissions"]["credential_access"] == "DENY"
    assert "ollama" in models["providers"]
    # hayat:latest is the main model at Mohsin's instruction: default plus all
    # the generative roles. Its foundation also does vision,
    # all-minilm embeds -- so the backend can change while "who are you" does
    # not, because HAYAT is the agent, not the weights.
    # hayat:latest is built on Qwen3.6-35B-A3B Uncensored since 2026-10-04
    # (qwen3.5 before that), a coherent instruction-tuned foundation that also
    # does vision, replacing the Qwythos "Mythos-heretic" creative merge that
    # hallucinated on casual chat. Uncensored behaviour comes from HAYAT's
    # SYSTEM preamble plus the runtime refusal guard, not from a broken base.
    # HAYAT is the agent, not the weights.
    assert models["default_model"] == {"provider": "ollama", "model": "hayat:latest", "temperature": 0.2}
    assert models["conversation_model"] == {"provider": "ollama", "model": "hayat:latest", "temperature": 0.3}
    assert models["reasoning_model"]["model"] == "hayat:latest"
    assert models["research_synthesis_model"]["model"] == "hayat:latest"
    assert models["coding_model"]["model"] == "hayat:latest"
    assert models["identity_model"]["model"] == "hayat:latest"
    assert models["vision_model"]["model"] == "qwen3.6-35b-a3b-uncensored:q4_k_m"
    assert models["identity_model"]["base_model"] == "qwen3.6-35b-a3b-uncensored:q4_k_m"
    assert models["identity_model"]["foundation_independent"] is False
    assert models["vision_model"] == {"provider": "ollama", "model": "qwen3.6-35b-a3b-uncensored:q4_k_m"}
    assert settings["vision"]["enabled"] is True
    assert settings["owner_mode"]["enabled"] is True
    assert settings["training"]["private_data"]["enabled"] is True
    assert settings["training"]["private_data"]["cloud_export_allowed"] is False
    assert settings["web"]["host"] == "lan"
    assert settings["cost_policy"] == {
        "mode": "ZERO-COST",
        "paid_api_allowed": False,
        "cloud_inference_allowed": False,
        "paid_search_allowed": False,
        "cloud_embeddings_allowed": False,
        "cloud_tts_allowed": False,
        "cloud_stt_allowed": False,
    }
    assert (PROJECT_ROOT / "app").is_dir() and (PROJECT_ROOT / "config").is_dir()  # any clone folder
