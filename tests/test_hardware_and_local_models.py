"""The machine's own limits, and the weights that exist on its disk.

Two facts this system previously had no way to state: what hardware it runs on,
and which models it holds independently of whether Ollama is serving. Both are
measured rather than assumed, and both are allowed to be unknown -- a plan built
on an invented number fails later and more confusingly than one that admits it
does not know.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.hardware import (
    RUNTIME_OVERHEAD_FACTOR,
    GPUInfo,
    HardwareProfile,
    profile,
)
from app.models.gguf import (
    LocalModel,
    discover_local_models,
    find_model,
    ollama_store_root,
    read_gguf_metadata,
)


GB = 1024**3


def _machine(vram_free_gb=6.0, ram_available_gb=16.0, has_gpu=True) -> HardwareProfile:
    gpu = GPUInfo(name="Test GPU", total_bytes=8 * GB, used_bytes=int((8 - vram_free_gb) * GB)) if has_gpu else GPUInfo()
    return HardwareProfile(
        gpu=gpu, total_ram_bytes=40 * GB,
        available_ram_bytes=int(ram_available_gb * GB), cpu_count=32,
        disk_free_bytes=500 * GB,
    )


# --------------------------------------------------------------- verdicts


def test_a_model_that_fits_vram_is_placed_on_the_gpu():
    verdict = _machine().can_run(4 * GB)
    assert verdict["runnable"] and verdict["placement"] == "GPU"
    assert verdict["expect"] == "fast"


def test_a_model_too_big_for_vram_but_not_for_ram_is_runnable_and_slow():
    """The common case on an 8GB laptop, and worth saying out loud: a plan that
    knows it will be slow can warn; one that assumes fast simply disappoints."""
    verdict = _machine().can_run(9 * GB)
    assert verdict["runnable"]
    assert verdict["placement"] == "CPU_OR_SPLIT"
    assert verdict["expect"] == "slow"


def test_a_model_too_big_for_the_machine_is_refused_with_a_reason():
    verdict = _machine(ram_available_gb=8.0).can_run(40 * GB)
    assert not verdict["runnable"]
    assert verdict["placement"] == "NONE"
    assert "exceeds" in verdict["reason"]


def test_headroom_is_reserved_so_the_machine_stays_usable():
    """Taking every free byte is how a machine becomes unusable, not fast."""
    machine = _machine(vram_free_gb=6.0)
    assert not machine.fits_in_vram(int(5.9 * GB)), "must not consume all free VRAM"


def test_runtime_overhead_is_accounted_for_not_just_weights():
    machine = _machine(vram_free_gb=6.0)
    weights = int(5.0 * GB)
    assert weights < 6.0 * GB
    assert not machine.fits_in_vram(weights), "KV cache and buffers were ignored"
    assert weights * RUNTIME_OVERHEAD_FACTOR > 5.0 * GB


def test_a_machine_without_a_gpu_reports_cpu_placement():
    machine = _machine(has_gpu=False)
    assert not machine.gpu.available
    assert machine.can_run(2 * GB)["placement"] == "CPU_OR_SPLIT"
    assert any("No usable GPU" in note for note in machine.constraints())


def test_unknown_memory_is_never_treated_as_permission_to_run():
    """Unknown must not read as unlimited."""
    unknown = HardwareProfile(gpu=GPUInfo(), total_ram_bytes=None, available_ram_bytes=None)
    verdict = unknown.can_run(4 * GB)
    assert not verdict["runnable"]


def test_context_length_shrinks_when_memory_is_tight():
    roomy = _machine(vram_free_gb=7.5).recommended_context(int(0.5 * GB))
    tight = _machine(vram_free_gb=6.0, ram_available_gb=9.0).recommended_context(int(8.0 * GB))
    assert roomy > tight
    assert tight >= 4096, "a usable floor is still needed"


def test_parallelism_is_bounded_regardless_of_core_count():
    machine = _machine()
    machine.cpu_count = 256
    assert 1 <= machine.max_parallel_tasks() <= 4


def test_the_summary_states_limits_in_plain_words():
    text = _machine().summary()
    assert "VRAM" in text and "RAM" in text
    assert "noticeably slower" in text, "the practical consequence should be stated"


# ------------------------------------------------------- the real machine


def test_the_real_profile_is_measured_not_invented():
    hardware = profile(refresh=True)
    assert hardware.cpu_count and hardware.cpu_count > 0
    if hardware.total_ram_bytes is not None:
        assert hardware.total_ram_bytes > GB
        assert hardware.available_ram_bytes <= hardware.total_ram_bytes
    if hardware.gpu.available:
        assert hardware.gpu.free_bytes is not None
        assert hardware.gpu.free_bytes <= hardware.gpu.total_bytes


def test_the_profile_is_cached_so_it_is_cheap_to_consult():
    first, second = profile(), profile()
    assert first is second


# ----------------------------------------------- models without the service


def _fake_store(root: Path, name: str = "testmodel", tag: str = "latest", size: int = 2048) -> Path:
    blobs = root / "blobs"
    manifests = root / "manifests" / "registry.ollama.ai" / "library" / name
    blobs.mkdir(parents=True, exist_ok=True)
    manifests.mkdir(parents=True, exist_ok=True)

    digest = "sha256:" + "a" * 64
    weights = blobs / digest.replace(":", "-")
    weights.write_bytes(b"GGUF" + b"\x00" * (size - 4))

    (manifests / tag).write_text(json.dumps({
        "schemaVersion": 2,
        "layers": [
            {"mediaType": "application/vnd.ollama.image.model", "digest": digest, "size": size},
            {"mediaType": "application/vnd.ollama.image.license", "digest": "sha256:" + "b" * 64, "size": 10},
        ],
    }), encoding="utf-8")
    return root


def test_models_are_found_from_disk_with_no_service_running(tmp_path):
    """The half of Ollama-independence that can be finished today: the weights
    are ordinary files and stay readable if the service never starts again."""
    store = _fake_store(tmp_path)
    models = discover_local_models(store)

    assert len(models) == 1
    assert models[0].name == "testmodel:latest"
    assert models[0].is_gguf, "verified by magic bytes, not by extension"


def test_a_model_can_be_resolved_by_name_without_its_tag(tmp_path):
    store = _fake_store(tmp_path)
    assert find_model("testmodel", store) is not None
    assert find_model("testmodel:latest", store) is not None
    assert find_model("nonexistent", store) is None


def test_licence_and_parameter_layers_are_not_mistaken_for_weights(tmp_path):
    store = _fake_store(tmp_path)
    model = discover_local_models(store)[0]
    assert model.size_bytes == 2048, "the weights layer, not another layer"


def test_a_manifest_pointing_at_a_missing_blob_is_skipped(tmp_path):
    store = _fake_store(tmp_path)
    next((store / "blobs").iterdir()).unlink()
    assert discover_local_models(store) == []


def test_a_corrupt_manifest_does_not_break_discovery(tmp_path):
    store = _fake_store(tmp_path)
    bad = store / "manifests" / "registry.ollama.ai" / "library" / "broken"
    bad.mkdir(parents=True)
    (bad / "latest").write_text("{not json", encoding="utf-8")
    assert len(discover_local_models(store)) == 1, "one bad manifest must not hide the good ones"


def test_a_huge_file_among_the_manifests_is_skipped_not_read(tmp_path):
    """A real condition on this machine, not a hypothetical.

    Two raw GGUF files had been placed in the manifests directory, one of
    21GB. Reading it as UTF-8 took 142 seconds and pulled the file into
    memory -- a directory listing turning into an out-of-memory condition
    because a file was in the wrong folder. Discovery must skip it on size
    alone, without opening it.
    """
    store = _fake_store(tmp_path)
    intruder = store / "manifests" / "registry.ollama.ai" / "library" / "huge.gguf"
    intruder.write_bytes(b"GGUF" + b"\x00" * (2 * 1024 * 1024))

    import time

    started = time.perf_counter()
    models = discover_local_models(store)
    elapsed = time.perf_counter() - started

    assert len(models) == 1, "the real model is still found"
    assert elapsed < 1.0, "the oversized file must be skipped on size, not parsed"


def test_a_params_layer_pointing_at_something_enormous_is_ignored(tmp_path):
    """Size is judged from the file, never from a field inside it."""
    from app.models.gguf import _MAX_PARAMS_BYTES, _read_small_json

    big = tmp_path / "not-really-params"
    big.write_bytes(b"x" * (_MAX_PARAMS_BYTES + 1))
    assert _read_small_json(big, limit=_MAX_PARAMS_BYTES) == {}


def test_a_file_that_is_not_gguf_is_reported_honestly(tmp_path):
    model = LocalModel(name="fake", path=tmp_path / "x.bin", size_bytes=4)
    (tmp_path / "x.bin").write_bytes(b"NOPE")
    assert not model.is_gguf


def test_an_absent_store_yields_nothing_rather_than_raising(tmp_path):
    assert discover_local_models(tmp_path / "nothing-here") == []


# --------------------------------------------------------- gguf metadata


def test_gguf_metadata_reads_the_header_of_a_real_file():
    """Runs against whatever this machine genuinely has."""
    models = discover_local_models()
    if not models:
        pytest.skip("no local models on this machine")
    metadata = read_gguf_metadata(models[0].path)
    assert metadata.get("gguf_version"), "the header should identify its format version"
    assert any("architecture" in key for key in metadata), "architecture should be readable"


def test_metadata_of_a_non_gguf_file_is_empty_not_wrong(tmp_path):
    path = tmp_path / "not-a-model"
    path.write_bytes(b"just some bytes")
    assert read_gguf_metadata(path) == {}


def test_the_real_store_is_located_or_honestly_absent():
    root = ollama_store_root()
    if root is not None:
        assert root.is_dir()


# ------------------------------------------------------------- integration


def test_doctor_reports_hardware_and_what_it_can_run():
    from app.doctor import _hardware_check

    report = _hardware_check()
    assert "gpu" in report and "cpu_count" in report
    assert "local_models_found" in report
    for entry in report["local_models"]:
        assert "verdict" in entry and "runnable" in entry["verdict"]
