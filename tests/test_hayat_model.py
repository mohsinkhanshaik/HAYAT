from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.models.hayat import HayatModelManager


def _package(root: Path) -> None:
    package = root / "models" / "hayat"
    package.mkdir(parents=True)
    (package / "Modelfile").write_text("FROM gemma4:latest\nSYSTEM You are HAYAT.\n", encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps({
        "kind": "ollama_custom_identity_model",
        "weights": "inherited_foundation_weights",
    }), encoding="utf-8")


def test_model_status_is_truthful_about_foundation_provenance(tmp_path, monkeypatch):
    _package(tmp_path)
    manager = HayatModelManager(tmp_path)
    monkeypatch.setattr(manager, "_installed_models", lambda: {"gemma4:latest"})
    status = manager.status()
    assert not status["installed"] and status["base_installed"]
    assert status["weights"] == "inherited_foundation_weights"
    assert status["foundation_independent"] is False
    assert status["automatic_training"] is False


def test_build_never_downloads_a_missing_foundation_model(tmp_path, monkeypatch):
    _package(tmp_path)
    manager = HayatModelManager(tmp_path)
    monkeypatch.setattr(manager, "_installed_models", lambda: set())
    monkeypatch.setattr("app.models.hayat.shutil.which", lambda _name: "ollama.exe")
    with pytest.raises(RuntimeError, match="never downloads"):
        manager.build()


def test_modelfile_is_more_authoritative_than_stale_descriptive_manifest(tmp_path):
    _package(tmp_path)
    manifest = tmp_path / "models" / "hayat" / "manifest.json"
    manifest.write_text(json.dumps({
        "kind": "ollama_custom_identity_model",
        "base_model": "an-old-model:latest",
    }), encoding="utf-8")

    manager = HayatModelManager(tmp_path)
    assert manager.base_model == "gemma4:latest"


def test_build_uses_inspected_server_and_verifies_installed_instructions(tmp_path, monkeypatch):
    _package(tmp_path)
    manager = HayatModelManager(tmp_path, base_url="http://localhost:11435")
    monkeypatch.setattr(manager, "_installed_models", lambda: {"gemma4:latest", "hayat:latest"})
    monkeypatch.setattr("app.models.hayat.shutil.which", lambda _: "ollama.exe")
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="success", stderr="")
    monkeypatch.setattr("app.models.hayat.subprocess.run", run)
    monkeypatch.setattr("app.models.hayat.httpx.post", lambda *a, **kw: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"system": "You are HAYAT."}))
    status = manager.build()
    assert status["package_current"] and status["system_current"]
    assert calls[0][0][1:3] == ["cp", "hayat:latest"]
    assert calls[1][0][1] == "create"
    assert all(kwargs["env"]["OLLAMA_HOST"] == manager.base_url for _, kwargs in calls)
    assert status["build"]["rollback_model"].startswith("hayat:rollback-")
    other = HayatModelManager(tmp_path, base_url="http://localhost:11434")
    monkeypatch.setattr(other, "_installed_models", manager._installed_models)
    assert other.build()["package_current"]
    assert manager.status()["package_current"], "building on another server must preserve this server's build record"


def test_failed_post_build_verification_restores_previous_package(tmp_path, monkeypatch):
    _package(tmp_path)
    manager = HayatModelManager(tmp_path)
    monkeypatch.setattr(manager, "_installed_models", lambda: {"gemma4:latest", "hayat:latest"})
    monkeypatch.setattr("app.models.hayat.shutil.which", lambda _: "ollama.exe")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="success", stderr="")
    monkeypatch.setattr("app.models.hayat.subprocess.run", run)
    monkeypatch.setattr("app.models.hayat.httpx.post", lambda *a, **kw: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"system": "Old instructions"}))
    with pytest.raises(RuntimeError, match="previous package was restored"):
        manager.build()
    assert calls[-1][1] == "cp" and calls[-1][-1] == "hayat:latest"
    assert calls[-1][2] == calls[0][-1]
    assert not manager.build_record.exists()


def test_changed_package_is_not_reported_as_current(tmp_path, monkeypatch):
    _package(tmp_path)
    manager = HayatModelManager(tmp_path)
    monkeypatch.setattr(manager, "_installed_models", lambda: {"gemma4:latest", "hayat:latest"})
    monkeypatch.setattr("app.models.hayat.httpx.post", lambda *a, **kw: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"system": "Different instructions"}))
    assert not manager.status()["package_current"]


def test_failed_backup_prevents_replacing_the_active_model(tmp_path, monkeypatch):
    _package(tmp_path)
    manager = HayatModelManager(tmp_path)
    monkeypatch.setattr(manager, "_installed_models", lambda: {"gemma4:latest", "hayat:latest"})
    monkeypatch.setattr("app.models.hayat.shutil.which", lambda _: "ollama.exe")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="copy failed")
    monkeypatch.setattr("app.models.hayat.subprocess.run", run)
    with pytest.raises(RuntimeError, match="preserve"):
        manager.build()
    assert len(calls) == 1 and calls[0][1] == "cp"
