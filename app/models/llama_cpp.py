"""Inference this system owns, rather than a service the user must install.

Ollama was the only way this project could reason, which made a separate
application a hard dependency of the assistant. llama.cpp ships prebuilt
Windows binaries, so the engine can be fetched once, owned, and started by
D:\\AI itself -- and it loads GGUF files from wherever they already are. The
22GB of weights on this machine are used in place; nothing is copied to claim
independence.

Two findings from running the real 21GB model shaped this module.

Reasoning models answer into a separate channel. Asked a two-sentence question
with a 100-token budget, Qwen3.6 produced 100 tokens of private reasoning and
an empty answer -- the request looked successful and said nothing. Thinking is
therefore off by default here, which turned that same question into a correct
answer in 3.8s instead of 41s of silence. When it is enabled the reasoning is
kept apart from the reply, because it is working-out, not an answer.

The engine is a child process, not a service. It is started by this runtime,
recorded by PID, and stopped by this runtime -- the same ownership rule the
supervisor applies to Ollama, for the same reason: never terminate something
you did not start.
"""
from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT
from app.models.base import ModelError, ModelProvider, ModelResponse, normalize_messages


#: Where fetched engines live. One directory per release, so an upgrade never
#: overwrites a build that currently works.
ENGINE_ROOT = PROJECT_ROOT / "data" / "engines"

#: Measured on this machine: a 21GB Q4_K_M MoE memory-maps and answers its
#: first request about 30s after launch. A generous ceiling avoids declaring
#: failure on a larger model or a colder page cache.
DEFAULT_LOAD_TIMEOUT = 420.0


def find_engine(root: Path | None = None) -> Path | None:
    """Locate a llama-server binary this project has fetched.

    Returns None rather than a guessed path: an engine that is not there is a
    fact worth reporting plainly, not something to paper over with a default
    that fails later and less clearly.
    """
    base = root or ENGINE_ROOT
    if not base.is_dir():
        return None
    candidates = sorted(base.glob("*/llama-server.exe")) + sorted(base.glob("*/*/llama-server.exe"))
    if not candidates:
        candidates = sorted(base.glob("*/llama-server")) + sorted(base.glob("*/*/llama-server"))
    return candidates[-1] if candidates else None


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class LlamaCppProvider(ModelProvider):
    """Talks to a llama-server over its OpenAI-compatible API."""

    name = "llama_cpp"

    def __init__(self, base_url: str, model: str = "", timeout: float = 300.0,
                 enable_thinking: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Off by default. See the module docstring: a reasoning model given an
        # ordinary token budget spends all of it thinking and returns nothing.
        self.enable_thinking = enable_thinking

    def _error(self, message: str, exc: BaseException | None = None, *, endpoint: str,
               status: int | None = None) -> ModelError:
        from app.failures import FailureClass, classify_exception, classify_http_status

        if status is not None:
            failure_class = classify_http_status(status)
        elif exc is not None:
            failure_class = classify_exception(exc)
        else:
            failure_class = FailureClass.UNKNOWN
        return ModelError(
            message, provider=self.name, endpoint=endpoint, dependency="llama.cpp",
            failure_class=failure_class, http_status=status, model=self.model,
        )

    def list_models(self) -> list[str]:
        from app.models.local import discovery_timeout

        endpoint = f"{self.base_url}/v1/models"
        try:
            response = httpx.get(endpoint, timeout=discovery_timeout(self.base_url))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._error(
                f"Could not reach the local engine at {self.base_url}: {exc}",
                exc.__cause__ or exc, endpoint=endpoint,
            ) from exc
        return [item["id"] for item in response.json().get("data", [])]

    def generate(self, messages, tools=None, temperature=None, response_schema=None,
                 max_tokens=None) -> ModelResponse:
        endpoint = f"{self.base_url}/v1/chat/completions"
        payload: dict[str, Any] = {
            "messages": normalize_messages(messages),
            "temperature": 0.2 if temperature is None else temperature,
            # See the note in app/models/local.py: 512 truncated real answers
            # mid-sentence. High enough to finish, bounded so a runaway
            # generation cannot occupy the machine indefinitely.
            "max_tokens": max_tokens if max_tokens is not None else 4096,
            # Passed through the chat template rather than as a sampling flag,
            # which is how llama.cpp exposes it for Qwen-family models.
            "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
        }
        if tools:
            payload["tools"] = tools
        if response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "agent_response", "strict": True, "schema": response_schema},
            }
        from app.activity import ACTIVITY

        try:
            with ACTIVITY.track("model", "thinking", f"llama.cpp/{self.model or 'local'}"):
                response = httpx.post(endpoint, json=payload, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._error(
                f"The local engine answered {exc.response.status_code}.",
                exc, endpoint=endpoint, status=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._error(
                f"The local engine did not answer: {exc}", exc.__cause__ or exc, endpoint=endpoint,
            ) from exc

        try:
            data = response.json()
            message = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as exc:
            from app.failures import FailureClass

            raise ModelError(
                "The local engine returned a response in an unexpected shape.",
                provider=self.name, endpoint=endpoint, dependency="llama.cpp",
                failure_class=FailureClass.DATA_INTEGRITY,
            ) from exc

        choice = data["choices"][0]
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        if not content and reasoning:
            # The failure this module exists to prevent, surfaced rather than
            # returned as a blank success: the budget went entirely on private
            # reasoning and no answer was produced.
            from app.failures import FailureClass

            raise ModelError(
                "The model spent its whole token budget reasoning and produced no answer. "
                "Raise max_tokens or keep thinking disabled.",
                provider=self.name, endpoint=endpoint, dependency="llama.cpp",
                failure_class=FailureClass.RESOURCE_EXHAUSTED, model=self.model,
            )
        return ModelResponse(
            content=content,
            model=data.get("model", self.model),
            provider=self.name,
            tool_calls=message.get("tool_calls", []),
            usage=data.get("usage", {}),
            # Kept, but apart from the answer: it is working-out, not a reply.
            raw=data | ({"reasoning_content": reasoning} if reasoning else {}),
            # OpenAI-shaped: "stop" when the model ended it, "length" when the
            # token budget did.
            finish_reason=str(choice.get("finish_reason") or ""),
        )


class LlamaCppEngine:
    """An owned llama-server process serving one GGUF file.

    Ownership is the same rule the runtime supervisor applies to Ollama: this
    class stops only a process it started, addressed by the PID it recorded.
    """

    def __init__(self, model_path: Path, *, engine_path: Path | None = None,
                 context: int | None = None, threads: int | None = None,
                 gpu_layers: int | None = None, port: int | None = None) -> None:
        self.model_path = Path(model_path)
        self.engine_path = engine_path or find_engine()
        self.port = port or free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen | None = None
        self.pid: int | None = None
        self.load_seconds: float | None = None

        # Sized against the machine rather than against a constant, so a model
        # that will not fit is not asked to fit anyway.
        from app.hardware import profile

        hardware = profile()
        size = self.model_path.stat().st_size if self.model_path.is_file() else 0
        self.context = context or hardware.recommended_context(size)
        self.threads = threads or max(4, min(16, (hardware.cpu_count or 8) // 2))
        self.gpu_layers = gpu_layers if gpu_layers is not None else (
            999 if hardware.fits_in_vram(size) else 0
        )
        self.verdict = hardware.can_run(size)

    @property
    def available(self) -> bool:
        return bool(self.engine_path and self.engine_path.is_file() and self.model_path.is_file())

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, timeout: float = DEFAULT_LOAD_TIMEOUT) -> tuple[bool, str]:
        if not self.engine_path or not self.engine_path.is_file():
            return False, "no llama-server binary has been fetched into data/engines"
        if not self.model_path.is_file():
            return False, f"the model file does not exist: {self.model_path}"
        if not self.verdict["runnable"]:
            return False, self.verdict["reason"]

        command = [
            str(self.engine_path), "-m", str(self.model_path),
            "--host", "127.0.0.1", "--port", str(self.port),
            "-c", str(self.context), "-t", str(self.threads), "--no-warmup",
        ]
        if self.gpu_layers:
            command += ["-ngl", str(self.gpu_layers)]

        # Refuse before spawning rather than after. A model that cannot fit
        # takes minutes to fail and takes the machine's responsiveness with
        # it; saying so up front costs a second.
        from app.resources import GATE, NotEnoughMemory

        model_bytes = self.model_path.stat().st_size
        started = time.perf_counter()
        try:
            with GATE.heavy(f"load {self.model_path.stem[:40]}", needs_bytes=model_bytes, wait=30.0):
                self._process = subprocess.Popen(
                    command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except NotEnoughMemory as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"could not start the engine: {exc}"
        self.pid = self._process.pid

        while time.perf_counter() - started < timeout:
            if self._process.poll() is not None:
                return False, f"the engine exited during load with code {self._process.returncode}"
            try:
                if httpx.get(f"{self.base_url}/health", timeout=3).status_code == 200:
                    self.load_seconds = time.perf_counter() - started
                    return True, f"loaded in {self.load_seconds:.1f}s"
            except httpx.HTTPError:
                pass
            time.sleep(1.0)
        self.stop()
        return False, f"the model did not become ready within {timeout:.0f}s"

    def stop(self) -> tuple[bool, str]:
        """Stop the engine this object started, workers included.

        llama-server spawns children, and terminating only the parent left
        orphans holding memory and a port after shutdown reported success --
        two were found running on this machine after a "clean" test run. The
        tree is rooted at the PID recorded at spawn, so this can never reach a
        process this runtime did not start.
        """
        if self._process is None:
            return False, "no owned engine process is recorded"
        from app.process import stop_owned_process

        stopped, detail = stop_owned_process(self._process, self.pid)
        self._process = None
        self.pid = None
        return stopped, f"stopped the engine this runtime started ({detail})"

    def provider(self, **kwargs: Any) -> LlamaCppProvider:
        return LlamaCppProvider(self.base_url, model=self.model_path.stem, **kwargs)

    def status(self) -> dict[str, Any]:
        return {
            "engine": str(self.engine_path) if self.engine_path else None,
            "model": str(self.model_path), "model_gb": round(
                self.model_path.stat().st_size / 1024**3, 2) if self.model_path.is_file() else None,
            "endpoint": self.base_url, "pid": self.pid, "running": self.running,
            "context": self.context, "threads": self.threads, "gpu_layers": self.gpu_layers,
            "load_seconds": self.load_seconds, "placement": self.verdict.get("placement"),
            "expect": self.verdict.get("expect"), "available": self.available,
        }


def engine_provenance() -> dict[str, Any]:
    """Where the engine binary came from, so it is never anonymous."""
    record = ENGINE_ROOT / "provenance.json"
    try:
        return json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
