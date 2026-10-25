"""HAYAT's own folder: its Python, its Ollama on its own port, only its models."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import launcher, portable


def _model(store: Path, name: str, blobs: dict[str, bytes]) -> None:
    layers = [{"digest": digest, "size": len(data)} for digest, data in blobs.items()]
    manifest = portable.manifest_path(store, name)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"config": layers[0], "layers": layers[1:]}), encoding="utf-8")
    for digest, data in blobs.items():
        blob = portable.blob_path(store, digest)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(data)


def _complete_root(root: Path) -> Path:
    (portable.python_home(root)).mkdir(parents=True)
    (portable.python_home(root) / "python.exe").write_bytes(b"")
    (portable.python_home(root) / "bin").mkdir()
    portable.ollama_executable(root).parent.mkdir(parents=True)
    portable.ollama_executable(root).write_bytes(b"")
    for index, name in enumerate(portable.MODELS):
        _model(portable.models_dir(root), name, {f"sha256:{index}a": b"cfg", "sha256:shared": b"weights"})
    return root


def test_a_complete_folder_reports_nothing_missing(tmp_path):
    assert portable.missing(_complete_root(tmp_path)) == []
    assert portable.complete(tmp_path)


def test_a_half_copied_blob_counts_as_missing(tmp_path):
    root = _complete_root(tmp_path)
    portable.blob_path(portable.models_dir(root), "sha256:shared").write_bytes(b"wei")
    gaps = portable.missing(root)
    assert gaps and all("model" in gap for gap in gaps)


def test_an_empty_folder_names_every_piece_it_lacks(tmp_path):
    gaps = portable.missing(tmp_path)
    assert any(gap.startswith("Python") for gap in gaps)
    assert any(gap.startswith("Ollama") for gap in gaps)
    assert {f"model {name}" for name in portable.MODELS} <= set(gaps)


def test_the_switch_overrides_detection(tmp_path, monkeypatch):
    root = _complete_root(tmp_path)
    monkeypatch.delenv(portable.SWITCH, raising=False)
    assert portable.active(root)
    monkeypatch.setenv(portable.SWITCH, "0")
    assert not portable.active(root)
    monkeypatch.setenv(portable.SWITCH, "1")
    assert portable.active(tmp_path / "elsewhere")


def test_hayat_uses_its_own_port_and_store(tmp_path):
    env = portable.environment(tmp_path)
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert env["OLLAMA_NO_CLOUD"] == "1"
    assert env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11435"
    assert env["OLLAMA_MODELS"] == str(tmp_path / "runtime" / "ollama-models")
    assert env[portable.SWITCH] == "1"


def test_the_venv_is_repointed_at_the_folder_python_keeping_everything_else(tmp_path):
    _complete_root(tmp_path)
    config = tmp_path / ".venv" / "pyvenv.cfg"
    config.parent.mkdir()
    config.write_text("home = C:\\Python314\ninclude-system-site-packages = false\nversion = 3.14.2\n"
                      "executable = C:\\Python314\\python.exe\n", encoding="utf-8")
    assert portable.repair_venv(tmp_path)
    text = config.read_text(encoding="utf-8")
    assert f"home = {portable.python_home(tmp_path)}" in text
    assert "executable = " + str(portable.python_home(tmp_path)) in text
    assert "version = 3.14.2" in text and "include-system-site-packages = false" in text
    assert "C:\\Python314" not in text
    assert not portable.repair_venv(tmp_path)          # already right: untouched


def test_the_venv_is_left_alone_without_a_folder_python(tmp_path):
    config = tmp_path / ".venv" / "pyvenv.cfg"
    config.parent.mkdir()
    config.write_text("home = C:\\Python314\n", encoding="utf-8")
    assert not portable.repair_venv(tmp_path)
    assert config.read_text(encoding="utf-8") == "home = C:\\Python314\n"


# ------------------------------------------------------------ the copy


def test_only_hayats_models_are_copied_and_shared_layers_once(tmp_path):
    sys.path.insert(0, str(portable.ROOT))
    from scripts import make_portable

    source, target = tmp_path / "machine", tmp_path / "copy"
    for index, name in enumerate(portable.MODELS):
        _model(source, name, {f"sha256:{index}c": b"cfg", "sha256:shared": b"weights"})
    _model(source, "someone-elses:latest", {"sha256:other": b"not hayat's"})
    first = make_portable.copy_models(source, target)
    assert portable.blob_path(target, "sha256:shared").read_bytes() == b"weights"
    assert not portable.blob_path(target, "sha256:other").exists()
    assert not portable.manifest_path(target, "someone-elses:latest").exists()
    assert portable.blob_path(source, "sha256:other").exists()      # copied, never moved
    assert first == len(b"weights") + sum(len(b"cfg") + portable.manifest_path(source, n).stat().st_size
                                          for n in portable.MODELS)
    assert make_portable.copy_models(source, target) == 0           # a re-run resumes, copies nothing


def test_a_model_missing_from_the_machine_stops_the_copy_with_a_reason(tmp_path):
    from scripts import make_portable

    with pytest.raises(SystemExit, match="not installed"):
        make_portable.copy_models(tmp_path / "empty", tmp_path / "copy")


def test_a_model_already_whole_in_hayats_store_is_not_needed_from_the_machine(tmp_path):
    """HAYAT's foundation was imported straight into its own store; the machine's Ollama never had it."""
    from scripts import make_portable

    source, target = tmp_path / "machine", tmp_path / "copy"
    imported, *others = portable.MODELS
    _model(target, imported, {"sha256:imp": b"own weights"})
    for index, name in enumerate(others):
        _model(source, name, {f"sha256:{index}c": b"cfg"})
    make_portable.copy_models(source, target)
    assert all(not portable.model_gap(target, name) for name in portable.MODELS)
    assert not portable.manifest_path(source, imported).exists()


def test_hayats_foundation_is_required_and_does_its_vision():
    """The identity model's foundation must be in the folder: it is also the vision and DEEP model."""
    import yaml

    models = yaml.safe_load((portable.ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    foundation = models["identity_model"]["base_model"]
    assert foundation in portable.MODELS
    assert models["vision_model"]["model"] == foundation
    assert models["escalation_model"]["model"] == foundation
    modelfile = (portable.ROOT / "models" / "hayat" / "Modelfile").read_text(encoding="utf-8")
    assert f"FROM {foundation}" in modelfile.splitlines()


# ------------------------------------------------------------ HAYAT's Ollama


class _Process:
    pid = 4242

    def __init__(self) -> None:
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.stopped = True


def test_an_ollama_already_on_hayats_port_is_used_and_never_stopped(tmp_path):
    started = []
    own = launcher.OwnOllama(tmp_path, popen=lambda *a, **k: started.append(a), answers=lambda url: True)
    assert own.start()
    assert started == [] and not own.owned
    own.stop()                                          # nothing of ours to stop


def test_hayats_ollama_starts_from_the_folder_on_its_own_port(tmp_path):
    calls, process = [], _Process()
    answers = iter([False, False, True])

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    own = launcher.OwnOllama(tmp_path, popen=popen, answers=lambda url: next(answers), timeout=5)
    assert own.start() and own.owned
    command, kwargs = calls[0]
    assert command == [str(portable.ollama_executable(tmp_path)), "serve"]
    assert kwargs["env"]["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert kwargs["env"]["OLLAMA_NO_CLOUD"] == "1"
    assert kwargs["env"]["OLLAMA_MODELS"] == str(portable.models_dir(tmp_path))


def test_an_ollama_that_dies_while_starting_is_reported_quickly(tmp_path):
    process = _Process()
    process.stopped = True
    own = launcher.OwnOllama(tmp_path, popen=lambda *a, **k: process, answers=lambda url: False, timeout=30)
    began = time.monotonic()
    assert not own.start()
    assert time.monotonic() - began < 5
    assert "did not answer" in own.detail


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_closing_the_job_ends_what_was_started_in_it():
    job = launcher.Job()
    assert job.handle is not None
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             creationflags=launcher.NO_WINDOW)
    try:
        assert job.add(child)
        job.close()
        assert child.wait(timeout=10) is not None
    finally:
        if child.poll() is None:
            child.kill()


# ------------------------------------------------------------ the rest of HAYAT


def test_the_supervisor_restarts_hayats_ollama_not_the_machines(tmp_path, monkeypatch):
    from app.supervisor import OllamaService

    exe = tmp_path / "runtime" / "ollama" / "ollama.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(portable, "ollama_executable", lambda root=None: exe)
    monkeypatch.setenv(portable.SWITCH, "1")
    service = OllamaService("http://127.0.0.1:11435", "hayat:latest")
    assert service.record.executable == str(exe)
    assert service._extra_env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert "ollama-models" in service._extra_env["OLLAMA_MODELS"]


def test_without_the_folder_runtime_the_supervisor_is_unchanged(monkeypatch):
    from app.supervisor import OllamaService

    monkeypatch.delenv(portable.SWITCH, raising=False)
    service = OllamaService("http://localhost:11434", "m")
    assert service._extra_env == {}
    assert service.record.executable != str(portable.ollama_executable())


def test_the_incident_check_asks_the_configured_ollama(monkeypatch):
    import httpx
    from app.incidents.manager import IncidentManager

    asked = []

    def fake_get(url, **kwargs):
        asked.append(url)
        raise httpx.ConnectError("no")

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("shutil.which", lambda name: None)
    IncidentManager._components()
    assert asked == ["http://127.0.0.1:11435/api/tags"]


# ------------------------------------------------------------ the shared start step


def test_an_incomplete_folder_runs_on_the_machines_ollama_and_says_why(tmp_path, monkeypatch):
    monkeypatch.delenv(portable.SWITCH, raising=False)
    said = []
    monkeypatch.setattr(launcher, "OwnOllama", lambda *a, **k: pytest.fail("must not start an Ollama"))
    with launcher.folder_runtime(said.append, root=tmp_path) as env:
        assert env[portable.SWITCH] == "0"
        assert "OLLAMA_MODELS" not in env or env["OLLAMA_MODELS"] == os.environ.get("OLLAMA_MODELS")
    assert said and "Missing" in said[0]


def test_switching_it_off_leaves_the_environment_alone(tmp_path, monkeypatch):
    root = _complete_root(tmp_path)
    monkeypatch.setenv(portable.SWITCH, "0")
    monkeypatch.setattr(launcher, "OwnOllama", lambda *a, **k: pytest.fail("must not start an Ollama"))
    with launcher.folder_runtime(root=root) as env:
        assert env == dict(os.environ)


def test_a_complete_folder_runs_hayats_ollama_for_exactly_as_long_as_hayat(tmp_path, monkeypatch):
    root = _complete_root(tmp_path)
    monkeypatch.delenv(portable.SWITCH, raising=False)
    events = []

    class Fake:
        owned = True
        detail = "started"

        def __init__(self, *args, **kwargs):
            events.append("made")

        def start(self):
            events.append("start")
            return True

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(launcher, "OwnOllama", Fake)
    with launcher.folder_runtime(root=root) as env:
        assert env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11435"
        events.append("hayat runs")
    assert events == ["made", "start", "hayat runs", "stop"]
    assert (root / "data" / "logs" / "ollama-hayat.log").is_file()


def test_tesseract_is_copied_without_its_manual_or_uninstaller(tmp_path):
    from scripts import make_portable

    source, target = tmp_path / "Tesseract-OCR", tmp_path / "copy"
    for name in ("tesseract.exe", "libtesseract-5.dll", "tessdata/eng.traineddata",
                 "doc/README.md", "tesseract-uninstall.exe"):
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_bytes(b"x")
    make_portable.copy_tree(source, target, make_portable.TESSERACT_SKIP)
    assert (target / "tesseract.exe").is_file() and (target / "tessdata" / "eng.traineddata").is_file()
    assert not (target / "doc").exists() and not (target / "tesseract-uninstall.exe").exists()
    assert (source / "tesseract-uninstall.exe").is_file()           # copied, never moved


def test_ocr_prefers_the_folder_copy_of_tesseract(tmp_path, monkeypatch):
    from app.perception.camera.ocr import TesseractOcr

    own = portable.tesseract_executable(tmp_path)
    own.parent.mkdir(parents=True)
    own.write_bytes(b"")
    monkeypatch.setattr(portable, "tesseract_executable", lambda root=portable.ROOT: own)
    assert TesseractOcr().executable == str(own)
    own.unlink()
    assert TesseractOcr().executable != str(own)


def test_hayats_ollama_fits_its_model_on_the_gpu_by_default(tmp_path, monkeypatch):
    """Flash attention and an 8-bit KV cache: measured 100% on GPU and 29% faster on the owner's 8 GB card."""
    monkeypatch.delenv("OLLAMA_FLASH_ATTENTION", raising=False)
    monkeypatch.delenv("OLLAMA_KV_CACHE_TYPE", raising=False)
    env = portable.environment(tmp_path)
    assert env["OLLAMA_FLASH_ATTENTION"] == "1" and env["OLLAMA_KV_CACHE_TYPE"] == "q8_0"
    # The owner can still choose otherwise.
    monkeypatch.setenv("OLLAMA_KV_CACHE_TYPE", "f16")
    monkeypatch.setenv("OLLAMA_FLASH_ATTENTION", "0")
    env = portable.environment(tmp_path)
    assert env["OLLAMA_KV_CACHE_TYPE"] == "f16" and env["OLLAMA_FLASH_ATTENTION"] == "0"


def test_the_launcher_starts_ollama_with_those_settings(tmp_path, monkeypatch):
    from app import launcher

    monkeypatch.delenv("OLLAMA_KV_CACHE_TYPE", raising=False)
    monkeypatch.delenv("OLLAMA_FLASH_ATTENTION", raising=False)
    started = {}
    answers = iter([False, True])

    class Process:
        pid = 1

        def poll(self):
            return None

    def fake_popen(args, **kwargs):
        started.update(kwargs["env"])
        return Process()
    service = launcher.OwnOllama(tmp_path, popen=fake_popen, answers=lambda _url: next(answers, True), timeout=2)
    assert service.start() and service.owned
    assert started["OLLAMA_KV_CACHE_TYPE"] == "q8_0" and started["OLLAMA_FLASH_ATTENTION"] == "1"
