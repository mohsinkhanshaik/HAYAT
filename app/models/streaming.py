"""Opt-in public-answer streaming; partial text never becomes durable memory."""
from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

from app.models.base import ModelError, ModelResponse
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class GenerationCancelled(ModelError):
    pass


class PublicTextFilter:
    """Withhold reasoning tags even when a provider splits them across chunks."""

    TAGS = ("<think>", "</think>", "<analysis>", "</analysis>")
    PATTERN = re.compile(r"</?(?:think|analysis)>", re.IGNORECASE)

    def __init__(self):
        self.pending = ""
        self.hidden = 0

    def feed(self, text: str, final=False) -> str:
        self.pending += text
        output = []
        cursor = 0
        # Process spans in one pass. Repeatedly slicing/lowercasing the full
        # remainder made a large provider chunk quadratic in CPU and copies.
        for match in self.PATTERN.finditer(self.pending):
            if not self.hidden:
                output.append(self.pending[cursor:match.start()])
            tag = match.group()
            self.hidden = max(0, self.hidden - 1) if tag.startswith("</") else self.hidden + 1
            cursor = match.end()
        tail = self.pending[cursor:]
        withheld = 0
        if not final:
            for length in range(1, min(len(tail), max(map(len, self.TAGS)) - 1) + 1):
                if any(tag.startswith(tail[-length:].lower()) for tag in self.TAGS):
                    withheld = length
        visible = tail[:-withheld] if withheld else tail
        if not self.hidden:
            output.append(visible)
        self.pending = tail[-withheld:] if withheld else ""
        return "".join(output)


_stream: ContextVar[tuple | None] = ContextVar("hayat_answer_stream", default=None)


@contextmanager
def answer_stream(callback, cancel: threading.Event):
    token = _stream.set((callback, cancel))
    try:
        yield
    finally:
        _stream.reset(token)


def check_cancelled(cancel=None):
    active = _stream.get()
    event = cancel if cancel is not None else active[1] if active else None
    if event is not None and event.is_set():
        raise GenerationCancelled("Response cancelled")


@contextmanager
def private_answer_stream(cancel=None):
    """Keep internal critic/revision text out of public streaming callbacks."""
    active = _stream.get()
    event = cancel if cancel is not None else active[1] if active else None
    token = _stream.set((lambda *_: None, event) if event is not None else None)
    try:
        yield
    finally:
        _stream.reset(token)


def generate_answer(provider, messages, **kwargs):
    check_cancelled()
    if kwargs.get('response_schema') is None:
        kwargs.pop('response_schema', None)
    from app.context_budget import fit_model_messages, message_tokens, required_message_indices
    from app.models.base import model_context_limit
    window = model_context_limit(provider)
    if window is not None:
        headroom = min(1024, window // 4)
        output = int(kwargs.get('max_tokens') or min(4096, window // 4))
        essential = sum(message_tokens(messages[i]) for i in required_message_indices(messages))
        # Reserve what the real instructions/request need before giving the
        # remainder to the answer. Never resize a provider or cut the request.
        minimum_output = min(output, 256)
        if essential + minimum_output + 128 <= window:
            # The default 1k headroom is an estimate, not an instruction. A
            # small declared window can need a smaller margin to carry the
            # actual instructions intact, still reserving at least 128.
            headroom = min(headroom, window - essential - minimum_output)
        available = window - headroom - essential
        if available >= min(output, 256):
            output = min(output, available)
        messages = fit_model_messages(messages, window - output - headroom)
        kwargs['max_tokens'] = output
    active = _stream.get()
    check_cancelled()
    if (active and kwargs.get('response_schema') is None
            and callable(getattr(provider, "generate_stream", None))):
        callback, cancel = active
        callback("start", f"{provider.name}/{getattr(provider, 'model', '')}")
        result = provider.generate_stream(messages, on_chunk=lambda text: callback("delta", text),
                                          cancel=cancel, **kwargs)
    else:
        result = provider.generate(messages, **kwargs)
    check_cancelled()
    return result


def stream_local(provider, payload, *, compatible=False, on_chunk, cancel=None):
    """Consume a local stream after enforcing the request boundary."""
    from app.policy.cost import require_local_endpoint
    require_local_endpoint(provider.base_url)
    check_cancelled(cancel)
    endpoint = provider.base_url + ("/chat/completions" if compatible else "/api/chat")
    payload = dict(payload, stream=True)
    headers = provider.headers if compatible else {}
    content = []
    public = PublicTextFilter()
    done = False
    final = {}
    size = 0
    stop_monitor = threading.Event()
    try:
        with httpx.stream("POST", endpoint, json=payload, headers=headers, timeout=provider.timeout, trust_env=False) as response:
            response.raise_for_status()

            def watch_cancel():
                while not stop_monitor.wait(.1):
                    if cancel is not None and cancel.is_set():
                        try:
                            response.close()
                        except Exception:
                            log_swallowed(_log, "closing a cancelled model stream")
                        return

            monitor = threading.Thread(target=watch_cancel, name="hayat-stream-cancel", daemon=True)
            monitor.start()
            try:
                for line in response.iter_lines():
                    check_cancelled(cancel)
                    if not line:
                        continue
                    if compatible:
                        if not line.startswith("data:"):
                            continue
                        line = line[5:].strip()
                        if line == "[DONE]":
                            done = True
                            break
                    packet = json.loads(line)
                    if packet.get("error"):
                        raise ModelError("Local provider reported a generation error")
                    if compatible:
                        choices = packet.get("choices") or []
                        choice = choices[0] if choices else {}
                        delta = choice.get("delta") or {}
                        text = delta.get("content") or ""
                        if delta.get("tool_calls"):
                            raise ModelError("Unexpected tool proposal in conversation stream")
                        if choice.get("finish_reason"):
                            final["finish_reason"] = choice["finish_reason"]
                        if packet.get("usage"):
                            final["usage"] = packet["usage"]
                    else:
                        message = packet.get("message") or {}
                        text = message.get("content") or ""
                        if message.get("tool_calls"):
                            raise ModelError("Unexpected tool proposal in conversation stream")
                        final = packet
                        done = bool(packet.get("done"))
                    if packet.get("model"):
                        final["model"] = packet["model"]
                    size += len(text)
                    if size > 1_000_000:
                        raise ModelError("Stream exceeded the bounded response size")
                    visible = public.feed(text)
                    if visible:
                        content.append(visible)
                        on_chunk(visible)
                    if done:
                        break
            finally:
                stop_monitor.set()
                monitor.join(timeout=.2)
    except GenerationCancelled:
        raise
    except (httpx.HTTPError, ValueError, OSError) as exc:
        check_cancelled(cancel)
        raise ModelError("Local response stream failed", provider=provider.name, endpoint=endpoint) from exc
    finally:
        stop_monitor.set()
    check_cancelled(cancel)
    if not done:
        raise ModelError("Local response stream ended before completion")
    tail = public.feed("", final=True)
    if tail:
        content.append(tail)
        on_chunk(tail)
    return ModelResponse("".join(content), final.get("model", payload["model"]), provider.name,
        usage=final.get("usage", {}) if compatible else {
            "prompt_tokens": final.get("prompt_eval_count"), "completion_tokens": final.get("eval_count")},
        finish_reason=str(final.get("finish_reason" if compatible else "done_reason") or "stop"))
