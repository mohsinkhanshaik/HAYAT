"""Setup builds HAYAT's brain on a model this computer can actually run (no download in these tests)."""
from __future__ import annotations

import bootstrap


def test_an_installed_full_brain_is_kept_whatever_the_memory():
    key, reason = bootstrap.choose_brain(16, {bootstrap.BRAINS["35b"]["base"]})
    assert key == "35b" and "already installed" in reason


def test_memory_decides_for_a_new_install():
    assert bootstrap.choose_brain(39.7, set())[0] == "35b"
    key, reason = bootstrap.choose_brain(15.8, set())
    assert key == "4b" and "16 GB" in reason and "HAYAT_BRAIN=35b" in reason
    assert bootstrap.choose_brain(None, set())[0] == "4b"  # unknown memory: the safe start


def test_the_owner_can_override_the_choice():
    assert bootstrap.choose_brain(8, set(), "35b") == ("35b", "you chose it (HAYAT_BRAIN=35b)")
    assert bootstrap.choose_brain(64, set(), "4B")[0] == "4b"
    assert bootstrap.choose_brain(64, set(), "huge")[0] == "35b"  # an unknown value is ignored


def test_the_small_brain_keeps_hayats_identity_and_changes_only_the_foundation(tmp_path, monkeypatch):
    source = tmp_path / "models/hayat/Modelfile"
    source.parent.mkdir(parents=True)
    source.write_text("# identity\nFROM qwen3.6-35b-a3b-uncensored:q4_k_m\nFROM projector.gguf\n"
                      'SYSTEM """You are HAYAT."""\nPARAMETER repeat_penalty 1.1\n', encoding="utf-8")
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    assert bootstrap.brain_modelfile("35b") == source
    small = bootstrap.brain_modelfile("4b").read_text(encoding="utf-8")
    assert small.count("FROM ") == 1 and "FROM hf.co/Mohsinkhanshaik/HAYAT-Open-4B:Q4_K_M" in small
    assert 'SYSTEM """You are HAYAT."""' in small and "repeat_penalty 1.1" in small


def test_the_real_modelfile_has_the_full_brain_as_its_foundation():
    lines = (bootstrap.ROOT / "models/hayat/Modelfile").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith("FROM ")] == [f"FROM {bootstrap.BRAINS['35b']['base']}"]


def test_installed_models_reads_ollama_list(monkeypatch):
    listing = ("NAME                                   ID      SIZE   MODIFIED\n"
               "hayat:latest                           abc     21 GB  1 day ago\n"
               "qwen3.6-35b-a3b-uncensored:q4_k_m      def     21 GB  1 day ago\n")
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: type("Done", (), {"stdout": listing})())
    names = bootstrap.installed_models()
    assert {"hayat:latest", "hayat", "qwen3.6-35b-a3b-uncensored:q4_k_m"} <= names


def test_memory_is_read_on_this_machine():
    ram = bootstrap.total_ram_gib()
    assert ram is None or 1 < ram < 4096
