"""Inference the system owns, instead of a service the user must install.

These run against fakes rather than the real 21GB model -- loading it takes
22 seconds and 20GB of page cache, which does not belong in a suite that runs
on every change. The real model was verified separately and the numbers are
recorded in the commit; what is pinned here are the decisions that would
silently break it.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.models.base import ModelError
from app.models.llama_cpp import LlamaCppEngine, LlamaCppProvider, find_engine


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


def _reply(content="", reasoning="", tokens=10):
    message = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return {
        "choices": [{"message": message, "finish_reason": "stop"}],
        "model": "test", "usage": {"completion_tokens": tokens},
    }


# ------------------------------------------------------- the thinking channel


def test_thinking_is_disabled_by_default(monkeypatch):
    """The finding that decides whether this model appears to work at all.

    Asked a two-sentence question with a 100-token budget and thinking on, the
    real model produced 100 tokens of private reasoning and an empty answer --
    a successful-looking request that said nothing. With thinking off the same
    question answered correctly in 3.8s.
    """
    sent = {}

    def capture(url, json=None, timeout=None):
        sent.update(json)
        return FakeResponse(_reply(content="hello"))

    monkeypatch.setattr(httpx, "post", capture)
    LlamaCppProvider("http://127.0.0.1:1").generate([{"role": "user", "content": "hi"}])

    assert sent["chat_template_kwargs"]["enable_thinking"] is False


def test_thinking_can_be_turned_on_deliberately(monkeypatch):
    sent = {}
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: (
        sent.update(json), FakeResponse(_reply(content="x")))[1])

    LlamaCppProvider("http://127.0.0.1:1", enable_thinking=True).generate(
        [{"role": "user", "content": "hi"}])
    assert sent["chat_template_kwargs"]["enable_thinking"] is True


def test_an_answer_that_is_all_reasoning_is_a_failure_not_a_blank_success(monkeypatch):
    """A response with no content is not an answer, however well it went."""
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: FakeResponse(
        _reply(content="", reasoning="thinking very hard", tokens=100)))

    with pytest.raises(ModelError) as caught:
        LlamaCppProvider("http://127.0.0.1:1").generate([{"role": "user", "content": "hi"}])

    from app.failures import FailureClass

    assert caught.value.failure_class is FailureClass.RESOURCE_EXHAUSTED
    assert "max_tokens" in str(caught.value)


def test_reasoning_is_kept_apart_from_the_reply(monkeypatch):
    """Working-out is not an answer, but it is worth keeping."""
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: FakeResponse(
        _reply(content="The answer is 4.", reasoning="2+2...")))

    result = LlamaCppProvider("http://127.0.0.1:1").generate([{"role": "user", "content": "2+2"}])

    assert result.content == "The answer is 4."
    assert "2+2..." in str(result.raw.get("reasoning_content"))


# --------------------------------------------------------- failure evidence


def test_an_unreachable_engine_reports_a_downed_dependency(monkeypatch):
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("[WinError 10061] actively refused")

    monkeypatch.setattr(httpx, "post", refuse)
    with pytest.raises(ModelError) as caught:
        LlamaCppProvider("http://127.0.0.1:1").generate([{"role": "user", "content": "hi"}])

    assert caught.value.dependency == "llama.cpp"
    assert caught.value.failure_fields()["endpoint"].startswith("http://127.0.0.1:1")


def test_a_malformed_response_is_a_data_integrity_failure(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: FakeResponse({"nope": 1}))
    from app.failures import FailureClass

    with pytest.raises(ModelError) as caught:
        LlamaCppProvider("http://127.0.0.1:1").generate([{"role": "user", "content": "hi"}])
    assert caught.value.failure_class is FailureClass.DATA_INTEGRITY


# ------------------------------------------------------------ hardware sizing


GB = 1024**3


def _fake_model(tmp_path: Path) -> Path:
    """A real but tiny file. Machine size is varied through the profile below.

    Deliberately not created with truncate(): asking NTFS for a 900GB file
    makes Windows zero-fill it, which hung the suite while proving nothing
    about the code under test.
    """
    path = tmp_path / "model.gguf"
    path.write_bytes(b"GGUF" + b"\x00" * 64)
    return path


def _machine(monkeypatch, *, vram_free_gb=6.0, ram_available_gb=32.0, has_gpu=True):
    from app import hardware
    from app.hardware import GPUInfo, HardwareProfile

    gpu = GPUInfo(name="Test GPU", total_bytes=8 * GB,
                  used_bytes=int((8 - vram_free_gb) * GB)) if has_gpu else GPUInfo()
    fixed = HardwareProfile(
        gpu=gpu, total_ram_bytes=40 * GB, available_ram_bytes=int(ram_available_gb * GB),
        cpu_count=32, disk_free_bytes=500 * GB,
    )
    monkeypatch.setattr(hardware, "profile", lambda **kwargs: fixed)
    return fixed


def test_a_model_too_large_for_the_gpu_is_placed_on_the_cpu(tmp_path, monkeypatch):
    """The real case here: a 20GB model on an 8GB card offloads no layers."""
    _machine(monkeypatch, vram_free_gb=0.1)
    engine = LlamaCppEngine(_fake_model(tmp_path), engine_path=tmp_path / "llama-server.exe")
    assert engine.gpu_layers == 0
    assert engine.verdict["placement"] == "CPU_OR_SPLIT"


def test_a_small_model_is_offloaded_to_the_gpu_when_it_fits(tmp_path, monkeypatch):
    _machine(monkeypatch, vram_free_gb=7.0)
    engine = LlamaCppEngine(_fake_model(tmp_path), engine_path=tmp_path / "llama-server.exe")
    assert engine.gpu_layers > 0
    assert engine.verdict["placement"] == "GPU"


def test_context_and_threads_are_chosen_from_the_machine(tmp_path, monkeypatch):
    _machine(monkeypatch)
    engine = LlamaCppEngine(_fake_model(tmp_path), engine_path=tmp_path / "llama-server.exe")
    assert engine.context >= 4096
    assert 4 <= engine.threads <= 16


def test_a_model_the_machine_cannot_hold_is_refused_before_launching(tmp_path, monkeypatch):
    """Refusing early beats a confusing failure after a long load."""
    _machine(monkeypatch, vram_free_gb=0.05, ram_available_gb=0.001, has_gpu=False)
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"x")
    engine = LlamaCppEngine(_fake_model(tmp_path), engine_path=binary)

    started, detail = engine.start()

    assert not started
    assert "exceeds" in detail
    assert engine.pid is None, "nothing should have been launched"


# ------------------------------------------------------------------ ownership


def test_nothing_is_stopped_that_was_not_started(tmp_path):
    engine = LlamaCppEngine(_fake_model(tmp_path), engine_path=tmp_path / "llama-server.exe")
    stopped, detail = engine.stop()
    assert not stopped
    assert "no owned engine process" in detail


def test_a_missing_engine_binary_is_reported_plainly(tmp_path):
    engine = LlamaCppEngine(_fake_model(tmp_path), engine_path=tmp_path / "absent.exe")
    started, detail = engine.start()
    assert not started
    assert "llama-server" in detail


def test_a_missing_model_is_reported_plainly(tmp_path):
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"x")
    engine = LlamaCppEngine(tmp_path / "absent.gguf", engine_path=binary)
    started, detail = engine.start()
    assert not started
    assert "does not exist" in detail


# ----------------------------------------------------- the fetched engine


def test_the_engine_binary_was_fetched_and_is_recorded():
    """The real binary on this machine, with where it came from."""
    from app.models.llama_cpp import engine_provenance

    found = find_engine()
    if found is None:
        pytest.skip("no engine fetched on this machine")
    assert found.is_file()

    provenance = engine_provenance()
    assert provenance.get("sha256"), "a downloaded binary must not be anonymous"
    assert "github.com/ggml-org/llama.cpp" in provenance.get("url", "")


def test_find_engine_returns_none_rather_than_guessing(tmp_path):
    assert find_engine(tmp_path) is None
