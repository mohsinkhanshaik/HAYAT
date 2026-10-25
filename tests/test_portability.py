from __future__ import annotations

import json
from pathlib import Path

import bootstrap
import run
from app.integrations.secrets import EncryptedFileBackend, FallbackSecretBackend, SecretBroker
from app.observability.manager import ObservabilityManager
from app.perception.displays import DisplayManager


class BrokenKeyring:
    def set_password(self, *_args) -> None:
        raise RuntimeError("no desktop keyring")

    def get_password(self, *_args) -> str | None:
        raise RuntimeError("no desktop keyring")

    def delete_password(self, *_args) -> None:
        raise RuntimeError("no desktop keyring")


def test_current_venv_probe_matches_host_and_has_only_portable_fields():
    if not bootstrap.VENV_PYTHON.is_file():
        import pytest

        pytest.skip("this checkout has no .venv yet; setup.ps1 creates it")
    compatible, detected = bootstrap.inspect_venv()
    assert compatible is True
    assert detected == bootstrap.host_fingerprint()
    assert set(detected) == {"system", "machine", "python"}


def test_missing_or_copied_wrong_layout_is_not_trusted(tmp_path: Path):
    compatible, detected = bootstrap.inspect_venv(tmp_path / "bin" / "python")
    assert compatible is False
    assert detected is None


def test_universal_launcher_selects_cli_on_a_headless_host(monkeypatch):
    monkeypatch.setattr(run, "gui_available", lambda: False)
    assert run.normalized_arguments([]) == ["--cli-only"]
    assert run.normalized_arguments(["--goal", "hello"]) == ["--goal", "hello"]


def test_universal_launcher_selects_gui_when_a_display_exists(monkeypatch):
    monkeypatch.setattr(run, "gui_available", lambda: True)
    assert run.normalized_arguments([]) == ["--gui"]
    assert run.normalized_arguments(["--cli-only"]) == ["--cli-only"]


def test_encrypted_secret_fallback_survives_missing_keyring(tmp_path: Path):
    fallback = EncryptedFileBackend(tmp_path)
    broker = SecretBroker(
        tmp_path / "secrets.db",
        backend=FallbackSecretBackend(BrokenKeyring(), fallback),
    )
    value = "portable-but-never-plaintext-secret"
    broker.store("example", value, scopes=["connector:test"])
    lease = broker.issue_lease("example", "connector:test")
    assert broker.redeem(lease, "connector:test") == value
    assert value.encode() not in fallback.path.read_bytes()
    assert json.loads(json.dumps(broker.status()))["values_exposed"] is False


def test_deleting_with_an_unavailable_keyring_clears_the_fallback(tmp_path: Path):
    fallback = EncryptedFileBackend(tmp_path)
    backend = FallbackSecretBackend(BrokenKeyring(), fallback)
    backend.set_password("HAYAT", "example", "gone-after-delete")
    backend.delete_password("HAYAT", "example")
    assert fallback.get_password("HAYAT", "example") is None


def test_a_keyring_that_keeps_the_secret_is_not_reported_as_deleted(tmp_path: Path):
    class StuckKeyring:
        stored = "still-in-the-keyring"

        def get_password(self, *_args) -> str | None:
            return self.stored

        def delete_password(self, *_args) -> None:
            raise RuntimeError("access denied")

    fallback = EncryptedFileBackend(tmp_path)
    fallback.set_password("HAYAT", "example", "fallback-copy")
    broker = SecretBroker(tmp_path / "secrets.db", backend=FallbackSecretBackend(StuckKeyring(), fallback))
    try:
        broker.backend.delete_password("HAYAT", "example")
    except RuntimeError as exc:
        assert "access denied" in str(exc)
    else:
        raise AssertionError("a failed keyring deletion was reported as done")
    assert fallback.get_password("HAYAT", "example") is None


def test_encrypted_store_files_can_move_together(tmp_path: Path):
    first = EncryptedFileBackend(tmp_path / "first")
    first.set_password("HAYAT", "service", "copied-secret")
    destination = tmp_path / "copied"
    destination.mkdir()
    (destination / first.path.name).write_bytes(first.path.read_bytes())
    (destination / first.key_path.name).write_bytes(first.key_path.read_bytes())
    second = EncryptedFileBackend(destination)
    assert second.get_password("HAYAT", "service") == "copied-secret"


def test_non_windows_monitor_adapter_degrades_without_windll(monkeypatch):
    import app.perception.displays as module

    monkeypatch.setattr(module.os, "name", "posix")
    manager = DisplayManager()
    assert manager.user32 is None
    assert isinstance(manager.status()["displays"], list)


def test_non_windows_observability_uses_psutil(monkeypatch):
    import app.observability.manager as module
    import psutil  # noqa: F401 -- imported first: psutil refuses to load while os.name is patched on Windows

    class Storage:
        maximum = 10_000_000
        reserve = 1

    monkeypatch.setattr(module.os, "name", "posix")
    module.ObservabilityManager._installed_cache = None
    report = ObservabilityManager(object(), Storage()).system()
    assert report["ram"]["total_bytes"] > 0
    assert 0 <= report["cpu_percent"] <= 100


def test_the_tested_version_lock_applies_only_to_the_host_it_was_recorded_on(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    assert bootstrap.lock_for_this_host() is None, "no lock: install from the ranges"

    tag = f"{sys.platform}-py{sys.version_info.major}.{sys.version_info.minor}"
    lock = tmp_path / "requirements-lock.txt"
    lock.write_text(f"# host: {tag}\nhttpx==0.28.1\n", encoding="utf-8")
    assert bootstrap.lock_for_this_host() == lock

    lock.write_text("# host: linux-py3.13\nhttpx==0.28.1\n", encoding="utf-8")
    assert bootstrap.lock_for_this_host() is None, "another host's pins must not constrain this one"


def test_the_committed_lock_names_its_host_and_pins_every_requirement():
    import re
    from pathlib import Path

    root = Path(bootstrap.__file__).resolve().parent
    lock = (root / "requirements-lock.txt").read_text(encoding="utf-8")
    assert re.search(r"(?m)^# host: \w+-py\d+\.\d+$", lock)
    pinned = {line.split("==")[0].lower().replace("_", "-") for line in lock.splitlines() if "==" in line}
    for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or ("sys_platform" in line and "win32" not in line):
            continue
        name = re.split(r"[<>=!;\[ ]", line, maxsplit=1)[0].lower().replace("_", "-")
        assert name in pinned, f"{name} is required but not pinned in the lock"
