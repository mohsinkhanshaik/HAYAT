import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent.conversation import ConversationService
from app.memory.database import MemoryDatabase
from app.models.base import ModelError, ModelResponse
from app.models.intelligent_router import BoundedFallbackProvider
from app.models.local import OllamaProvider, OpenAICompatibleProvider
from app.models.streaming import GenerationCancelled, PublicTextFilter, answer_stream


def transport(monkeypatch, lines):
    closed = Mock()
    @contextmanager
    def stream(*args, **kwargs):
        assert kwargs["json"]["stream"] is True
        try:
            yield SimpleNamespace(raise_for_status=lambda: None, iter_lines=lambda: iter(lines), close=closed)
        finally:
            closed()
    monkeypatch.setattr("app.models.streaming.httpx.stream", stream)
    return closed


def test_ollama_emits_content_before_completion_and_preserves_usage(monkeypatch):
    closed = transport(monkeypatch, [json.dumps(packet) for packet in [
        {"model": "local", "message": {"thinking": "private reasoning", "content": "Hello "}},
        {"model": "local", "message": {"content": "there."}, "done": True,
         "done_reason": "length", "eval_count": 2}]])
    chunks = []
    result = OllamaProvider("http://localhost:11434", "local").generate_stream([], on_chunk=chunks.append)
    assert chunks == ["Hello ", "there."]
    assert result.content == "Hello there." and result.truncated
    assert result.usage["completion_tokens"] == 2
    assert "private" not in result.content
    assert closed.called


def test_compatible_sse_stream_handles_usage_finish_and_reasoning(monkeypatch):
    transport(monkeypatch, [
        'data: {"model":"local","choices":[{"delta":{"reasoning":"hidden","content":"Answer"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"completion_tokens":1}}',
        'data: [DONE]',
    ])
    result = OpenAICompatibleProvider("lm_studio", "http://localhost:1234/v1", "local").generate_stream(
        [], on_chunk=Mock(), max_tokens=80)
    assert result.content == "Answer"
    assert result.model == "local" and result.finish_reason == "stop"
    assert result.usage["completion_tokens"] == 1


def test_split_reasoning_tags_are_never_sent_to_ui():
    filter = PublicTextFilter()
    visible = [filter.feed(part) for part in ("<thi", "nk>secret", "</th", "ink>Here", " is the answer.")]
    assert "".join(visible) == "Here is the answer."


def test_stream_without_completion_marker_is_not_a_success(monkeypatch):
    transport(monkeypatch, ['{"message":{"content":"unfinished"}}'])
    with pytest.raises(ModelError, match="before completion"):
        OllamaProvider("http://localhost:11434", "local").generate_stream([], on_chunk=Mock())


def test_cancellation_closes_stream_and_never_attempts_fallback(monkeypatch):
    closed = transport(monkeypatch, ['{"message":{"content":"partial"}}', '{"done":true}'])
    cancel = threading.Event()
    fallback = SimpleNamespace(model="backup", generate=Mock())
    provider = BoundedFallbackProvider([OllamaProvider("http://localhost:11434", "local"), fallback])
    with pytest.raises(GenerationCancelled):
        provider.generate_stream([], on_chunk=lambda text: cancel.set(), cancel=cancel)
    fallback.generate.assert_not_called()
    assert closed.called


def test_partial_stream_is_not_replaced_by_unannounced_fallback():
    def broken(messages, on_chunk, **kwargs):
        on_chunk("partial")
        raise ModelError("lost connection")
    fallback = SimpleNamespace(model="backup", generate=Mock())
    first = SimpleNamespace(model="first", generate_stream=broken)
    with pytest.raises(ModelError, match="incomplete output was not saved"):
        BoundedFallbackProvider([first, fallback]).generate_stream([], on_chunk=Mock())
    fallback.generate.assert_not_called()


def test_cancelled_conversation_never_commits_assistant_preview(tmp_path):
    cancel = threading.Event()
    def generate_stream(messages, on_chunk, **kwargs):
        on_chunk("unfinished response")
        cancel.set()
        return ModelResponse("unfinished response", "local", "ollama")
    model = SimpleNamespace(name="ollama", model="local", generate_stream=generate_stream)
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    conversation = ConversationService(model, memory, "session")
    with answer_stream(Mock(), cancel), pytest.raises(GenerationCancelled):
        conversation.reply("hello")
    assert all(row["role"] != "assistant" for row in memory.conversation_history("session"))


def test_successful_stream_uses_existing_answer_checks_before_commit(tmp_path):
    def generate_stream(messages, on_chunk, **kwargs):
        on_chunk("Hello naturally.")
        return ModelResponse("Hello naturally.", "local", "ollama")
    model = SimpleNamespace(name="ollama", model="local", generate_stream=generate_stream)
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    conversation = ConversationService(model, memory, "session")
    callback = Mock()
    with answer_stream(callback, threading.Event()):
        assert conversation.reply("hello") == "Hello naturally."
    assert memory.conversation_history("session")[-1]["content"] == "Hello naturally."
    callback.assert_any_call("delta", "Hello naturally.")


def test_cancelled_turn_stops_before_oversized_prompt_validation():
    from app.models.streaming import generate_answer
    from app.models.base import Message
    cancel = threading.Event()
    cancel.set()
    provider = SimpleNamespace(context_length=1024, generate=Mock())
    with answer_stream(Mock(), cancel), pytest.raises(GenerationCancelled):
        generate_answer(provider, [Message('user', 'oversized ' * 5000)], max_tokens=128)
    provider.generate.assert_not_called()


def test_structured_generation_uses_schema_capable_method_during_public_stream():
    from app.models.streaming import generate_answer
    schema = {'type': 'object'}
    provider = SimpleNamespace(name='fixture', model='local',
        generate=Mock(return_value=ModelResponse('{}', 'local', 'fixture')), generate_stream=Mock())
    callback = Mock()
    with answer_stream(callback, threading.Event()):
        assert generate_answer(provider, [], response_schema=schema).content == '{}'
    provider.generate.assert_called_once_with([], response_schema=schema)
    provider.generate_stream.assert_not_called()
    callback.assert_not_called()


def test_private_review_keeps_cancel_and_restores_public_stream():
    from app.models.streaming import generate_answer, private_answer_stream
    def streaming(messages, on_chunk, cancel):
        on_chunk('visible text')
        return ModelResponse('visible text', 'local', 'fixture')
    provider = SimpleNamespace(name='fixture', model='local', generate_stream=streaming)
    callback, cancel = Mock(), threading.Event()
    with answer_stream(callback, cancel):
        with private_answer_stream():
            generate_answer(provider, [], response_schema=None)
            callback.assert_not_called()
            cancel.set()
            with pytest.raises(GenerationCancelled):
                generate_answer(provider, [])
            cancel.clear()
        generate_answer(provider, [], response_schema=None)
    callback.assert_any_call('delta', 'visible text')
