"""Colibri 2.1 profiles: HAYAT's own weights read in place, engine choice, real engine counters (no live engine)."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx
import pytest

from app.models.colibri import parse_metrics, summarize_metrics
from app.models.colibri_service import ENGINES, PROFILES, ColibriService, describe_status

METRICS = """# HELP colibri_scheduler_active Requests running
# TYPE colibri_scheduler_active gauge
colibri_scheduler_active 1
colibri_scheduler_queued 0
colibri_scheduler_capacity 1
colibri_scheduler_completed_total 12
colibri_scheduler_failed_total 0
colibri_scheduler_first_output_seconds_bucket{le="+Inf"} 12
colibri_scheduler_first_output_seconds_sum 46.8
colibri_scheduler_first_output_seconds_count 12
colibri_scheduler_slot_duration_seconds_sum 62.3 1731300000000
colibri_scheduler_slot_duration_seconds_count 12
broken line without a number x
"""


def _runtime(root, version: str) -> None:
    folder = root / f"runtime/colibri-v{version}"
    folder.mkdir(parents=True)
    (folder / "coli").write_text("", encoding="utf-8")
    (folder / "version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")


def _brain(root, digest: str = "sha256-abc") -> object:
    blobs = root / "runtime/ollama-models/blobs"
    blobs.mkdir(parents=True)
    blob = blobs / digest
    blob.write_bytes(b"GGUF fake weights")
    manifest = root / "runtime/ollama-models/manifests/registry.ollama.ai/library/hayat/latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"layers": [
        {"mediaType": "application/vnd.ollama.image.projector", "digest": "sha256:other"},
        {"mediaType": "application/vnd.ollama.image.model", "digest": digest.replace("-", ":", 1)}]}),
        encoding="utf-8")
    return blob


def _import(root, gguf) -> None:
    folder = root / PROFILES["foundation"]["model_dir"]
    folder.mkdir(parents=True)
    (folder / "gguf.json").write_text(json.dumps({"gguf": str(gguf), "mode": "link", "arch": "qwen35moe"}),
                                      encoding="utf-8")


def test_metrics_are_parsed_from_the_engines_prometheus_text_and_summarized_plainly():
    values = parse_metrics(METRICS)
    assert values["colibri_scheduler_completed_total"] == 12
    assert values['colibri_scheduler_first_output_seconds_bucket{le="+Inf"}'] == 12
    assert values["colibri_scheduler_slot_duration_seconds_sum"] == 62.3  # a trailing timestamp is not the value
    assert not any(key.startswith("broken") for key in values)
    summary = summarize_metrics(values)
    assert summary["completed_total"] == 12 and summary["active"] == 1 and summary["failed_total"] == 0
    assert summary["mean_first_output_seconds"] == 3.9
    assert summary["timed_out_total"] is None  # absent counters stay absent, never zero


def test_the_foundation_profile_is_hayats_brain_only_when_it_is_the_same_file_on_disk(tmp_path):
    blob = _brain(tmp_path)
    link = tmp_path / "models/colibri/hayat-foundation-gguf/brain.gguf"
    link.parent.mkdir(parents=True)
    os.link(blob, link)  # the setup script's hard link: same file, a .gguf name
    _import(tmp_path, link)
    service = ColibriService(tmp_path)
    assert service.brain_blob() == blob
    check = service.weights_check("foundation")
    assert check["same_as_brain"] and "same file" in check["detail"]
    assert not service.weights_check("original")["same_as_brain"]


def test_a_copy_with_the_same_bytes_is_not_called_the_brains_weights(tmp_path):
    blob = _brain(tmp_path)
    copy = tmp_path / "elsewhere.gguf"
    copy.write_bytes(blob.read_bytes())
    _import(tmp_path, copy)
    check = ColibriService(tmp_path).weights_check("foundation")
    assert not check["same_as_brain"] and "model comparison" in check["detail"]


def test_the_newest_engine_that_supports_a_profile_is_chosen(tmp_path):
    _runtime(tmp_path, "2.0.0")
    service = ColibriService(tmp_path)
    assert service.runtime_dir("original").name == "colibri-v2.0.0"
    _runtime(tmp_path, "2.1.0")
    assert service.runtime_dir("foundation").name == "colibri-v2.1.0"
    assert service.runtime_dir("original").name == "colibri-v2.1.0"


def test_foundation_start_refuses_an_engine_too_old_to_read_gguf(tmp_path, monkeypatch):
    _runtime(tmp_path, "2.0.0")
    _import(tmp_path, tmp_path / "x.gguf")
    monkeypatch.setattr("app.models.colibri_service.subprocess.Popen", lambda *_a, **_k: pytest.fail("no launch"))
    with pytest.raises(FileNotFoundError, match="2.1.0 or newer"):
        ColibriService(tmp_path).start("cuda", "foundation")
    with pytest.raises(ValueError):
        ColibriService(tmp_path).start("cpu", "someone-elses-model")


def test_the_cuda_command_bounds_gpu_and_ram_caches_and_names_the_exact_model(tmp_path):
    _runtime(tmp_path, "2.1.0")
    command, env = ColibriService(tmp_path).command("foundation", "cuda", 8123)
    assert command[command.index("--model-id") + 1] == PROFILES["foundation"]["model_id"]
    assert command[command.index("--gpu") + 1] == "0"
    assert command[command.index("--vram") + 1] == str(ENGINES["cuda"]["vram_gb"])
    assert command[command.index("--cap") + 1] == "96"
    assert command[command.index("--host") + 1] == "127.0.0.1" and "--no-think" in command
    assert "COLI_CUDA" not in ENGINES["cuda"]["env"]  # coli itself switches CUDA on for --gpu 0
    cpu, cpu_env = ColibriService(tmp_path).command("original", "cpu")
    assert cpu[cpu.index("--gpu") + 1] == "none" and cpu_env["COLI_CUDA"] == "0" and "--vram" not in cpu


def test_preflight_refuses_beside_the_loaded_brain_and_without_memory(monkeypatch):
    monkeypatch.setattr("app.models.colibri_service.httpx.get", lambda url, **_: httpx.Response(
        200, request=httpx.Request("GET", url), json={"models": [{"size": 21 * 1024**3}]}))
    with pytest.raises(RuntimeError, match="don't fit|do not fit"):
        ColibriService().preflight("cuda")

    def absent(*_a, **_k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr("app.models.colibri_service.httpx.get", absent)
    monkeypatch.setattr("psutil.virtual_memory", lambda: SimpleNamespace(available=8 * 1024**3))
    with pytest.raises(RuntimeError, match="16 GiB.*8.0 GiB"):
        ColibriService().preflight("cuda")


def test_status_reports_which_profile_is_served_with_the_engines_own_counters(monkeypatch):
    def get(url, **_):
        if url.endswith("/metrics"):
            return httpx.Response(200, request=httpx.Request("GET", url), text=METRICS)
        return httpx.Response(200, request=httpx.Request("GET", url),
                              json={"data": [{"id": PROFILES["foundation"]["model_id"]}]})
    monkeypatch.setattr("app.models.colibri_service.httpx.get", get)
    status = ColibriService().status()
    assert status["ready"] and status["serving"] == "foundation"
    assert status["telemetry"]["available"] and status["telemetry"]["completed_total"] == 12
    text = describe_status(status)
    assert "HAYAT's own brain weights" in text and "12 answered" in text and "3.9 s" in text


def test_status_words_never_invent_activity():
    assert "Not running" in describe_status({"ready": False, "profiles_installed": {"original": True}})
    assert "loading" in describe_status({"ready": False, "owned": True})
    assert "exit code 3" in describe_status({"ready": False, "exit_code": 3, "log": "x.log"})
    text = describe_status({"ready": True, "serving": "original", "telemetry": {"available": False}})
    assert "no counters" in text and "answered" not in text


def test_the_brain_view_has_a_colibri_neuron_whose_check_reads_the_engine():
    from app.brain_components import Component, check, service_components

    found = [component for component in service_components(["colibri_service"])
             if component.component_id == "svc:colibri_service"]
    assert found and found[0].parent == "router"
    runtime = SimpleNamespace(colibri_service=SimpleNamespace(status=lambda: {
        "ready": True, "serving": "foundation", "engine": "cuda",
        "telemetry": {"available": True, "completed_total": 3, "failed_total": 1, "active": 0, "queued": 0}}))
    component = Component("svc:colibri_service", "Colibri engine", "service", "router", "", probe="colibri_service")
    text = check(component, runtime)
    assert "CUDA" in text and "3 answered" in text and "1 failed" in text
