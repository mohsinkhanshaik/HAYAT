"""Audit regressions: coherent prompts, bounded repairs, XML and speech cleanup."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.context_budget import fit_model_messages, message_tokens
from app.models.base import Message, ModelError


@pytest.mark.parametrize('as_dict', [False, True])
def test_history_keeps_recent_exchanges_whole_without_jumping_over_a_large_turn(as_dict):
    messages = [Message('system', 'Essential instructions.'),
                Message('user', 'Old question'), Message('assistant', 'Old answer'),
                Message('user', 'large question ' * 1000), Message('assistant', 'small answer'),
                Message('user', 'Recent question'), Message('assistant', 'Recent answer'),
                Message('user', 'Explain the recent answer.')]
    if as_dict:
        messages = [{'role': item.role, 'content': item.content} for item in messages]
    expected = [messages[0], *messages[5:]]
    budget = sum(map(message_tokens, expected)) + sum(map(message_tokens, messages[1:3]))
    assert fit_model_messages(messages, budget) == expected


def test_history_tool_results_stay_with_their_question_across_system_instructions():
    messages = [Message('system', 'Follow evidence'), Message('user', 'Lookup'),
                Message('assistant', 'tool call'), Message('tool', 'huge result ' * 1000),
                Message('system', 'Use qualified language'), Message('assistant', 'Conclusion'),
                Message('user', 'New request')]
    assert fit_model_messages(messages, 100) == [messages[0], messages[4], messages[-1]]
    assert fit_model_messages(messages, 10000) == messages


def test_optional_context_cannot_leave_an_orphan_answer_or_change_stored_messages():
    orphan = Message('assistant', 'An answer whose question is no longer present.')
    core = Message('system', 'Required instructions')
    recap = Message('system', 'Earlier conversation recap.', optional_context=True)
    question = Message('user', 'Current question')
    snapshot = Message('system', 'large state ' * 1000, optional_context=True)
    messages = [orphan, core, recap, question, snapshot]
    original = list(messages)
    assert fit_model_messages(messages, 100) == [core, recap, question]
    assert messages == original


@pytest.mark.parametrize('budget', range(30, 301, 30))
def test_prompt_budget_keeps_a_contiguous_suffix_of_complete_exchanges(budget):
    messages = [Message('system', 'Core')]
    for index in range(8):
        messages.extend([Message('user', f'Question {index} ' * (index + 1)),
                         Message('assistant', f'Answer {index} ' * (index + 1))])
    messages.append(Message('user', 'Current'))
    fitted = fit_model_messages(messages, budget)
    assert sum(map(message_tokens, fitted)) <= budget
    assert fitted[0] == messages[0] and fitted[-1] == messages[-1]
    history = fitted[1:-1]
    assert len(history) % 2 == 0
    if history:
        start = messages.index(history[0])
        assert history == messages[start:-1]


@pytest.mark.parametrize('failure', ['deadline', 'provider', 'cancel'])
def test_greeting_repair_failure_is_honest_and_cancellation_propagates(tmp_path, failure):
    from app.agent.conversation import ConversationService
    from app.executive import ExecutiveCortex, ExecutiveLimit
    from app.memory.database import MemoryDatabase
    from app.models.streaming import GenerationCancelled
    from tests.test_executive_invention import ScriptedModel

    memory = MemoryDatabase(tmp_path / 'conversation.db')
    memory.initialize()
    error = GenerationCancelled('Response cancelled') if failure == 'cancel' else ModelError('Backend unavailable')
    model = ScriptedModel('Hello!', error)
    chat = ConversationService(model, memory, 'audit-test')

    class ExpiredRepair(ExecutiveCortex):
        def checkpoint(self, frame, stage, **kwargs):
            if stage == 'greeting_repair':
                raise ExecutiveLimit('Executive time budget reached.')
            return super().checkpoint(frame, stage, **kwargs)

    chat.executive = ExpiredRepair() if failure == 'deadline' else ExecutiveCortex()
    if failure == 'cancel':
        with pytest.raises(GenerationCancelled):
            chat.reply('Who created Python?')
        assert not [item for item in memory.conversation_history('audit-test') if item['role'] == 'assistant']
    else:
        answer = chat.reply('Who created Python?')
        assert 'failed to answer' in answer and 'repair could not complete' in answer
        assert 'twice' not in answer
        assert not chat.last_assessment.complete
        assert len(model.calls) == (1 if failure == 'deadline' else 2)


def _xml_parser(name, content, monkeypatch):
    if name == 'search':
        from app.research.search import BingRssSearch
        request = httpx.Request('GET', 'https://www.bing.com/search')
        monkeypatch.setattr(httpx, 'get', lambda *a, **kw: httpx.Response(200, content=content, request=request))
        return BingRssSearch().search('anything')
    if name == 'records':
        from app.intelligence.manager import xml_records
        return xml_records(content)
    if name == 'rss':
        from app.intelligence.reports import parse_rss
        return parse_rss(content)
    if name == 'faa':
        from app.intelligence.reports import faa_records
        return faa_records(content, {})
    from app.intelligence.providers import datex_cameras, kml_cameras
    return (datex_cameras if name == 'datex' else kml_cameras)(content)


@pytest.mark.parametrize('parser', ['search', 'records', 'rss', 'faa', 'datex', 'kml'])
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-be'])
@pytest.mark.parametrize('declaration', ['<!DOCTYPE rss [<!ENTITY a "expanded">]>',
                                      '<!DOCTYPE rss SYSTEM "https://example.invalid/external.dtd">'])
def test_all_provider_xml_paths_reject_encoded_and_late_declarations(parser, encoding, declaration, monkeypatch):
    # The old ASCII byte scan missed UTF-16; its first-4096-byte DTD check
    # also missed declarations after a valid long prolog.
    text = '<?xml version="1.0"?>' + ' ' * 5000 + declaration + '<rss><channel/></rss>'
    content = text.encode(encoding)
    with pytest.raises(ValueError, match='document type or entity'):
        _xml_parser(parser, content, monkeypatch)


@pytest.mark.parametrize('parser', ['search', 'records', 'rss', 'faa', 'datex', 'kml'])
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
def test_benign_provider_xml_still_parses_in_supported_encodings(parser, encoding, monkeypatch):
    assert isinstance(_xml_parser(parser, '<rss><channel/></rss>'.encode(encoding), monkeypatch), list)


@pytest.mark.parametrize('outcome', ['empty', 'audio', 'failure', 'stopped'])
def test_windows_speech_uses_reserved_file_and_cleans_every_outcome(outcome, monkeypatch):
    from app.voice.tts import WindowsSapiTTS
    tts = WindowsSapiTTS({}, devices=SimpleNamespace(resolve=lambda *a: None))
    paths, played = [], []

    def synthesize(text, path):
        paths.append(path)
        assert path.exists() and path.stat().st_size == 0
        if outcome == 'failure':
            raise RuntimeError('Synthesis failed')
        if outcome in ('audio', 'stopped'):
            path.write_bytes(b'audio fixture')
        if outcome == 'stopped':
            tts._stop.set()

    monkeypatch.setattr(tts, '_synthesize', synthesize)
    monkeypatch.setattr(tts, '_play', played.append)
    if outcome == 'failure':
        with pytest.raises(RuntimeError, match='Synthesis failed'):
            tts._worker('test')
    else:
        tts._worker('test')
    assert played == (paths if outcome == 'audio' else [])
    assert paths and all(not path.exists() for path in paths)
