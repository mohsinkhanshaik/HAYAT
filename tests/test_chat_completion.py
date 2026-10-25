import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.video_workflow import VIDEO_TOOL_NAMES, VideoWorkflow, direct_video_plan
from app.intent.router import IntentRouter
from app.session import SessionState
from app.tools.registry import ToolRegistry
from app.safety.permissions import PermissionManager
from app.video_workflow import register_video_workflow_tools


@pytest.mark.parametrize('utterance', ['play baby shark', 'please play Baby Shark',
                                     'can you play Baby Shark on youtube', 'watch the video Baby Shark'])
def test_named_title_reaches_tool_even_when_an_unrelated_local_video_is_selected(utterance):
    state = SessionState()
    state.last_video = {'path': 'D:/AI/unrelated.mp4', 'selected_path': 'D:/AI/unrelated.mp4'}
    intent = IntentRouter().route(utterance, state)
    assert intent.is_action and intent.entities['modality'] == 'video_editing'
    plan = direct_video_plan(intent.goal, VIDEO_TOOL_NAMES, state)
    assert plan['tool'] == 'search_play_video'
    assert plan['arguments']['query'].lower() == 'baby shark'


@pytest.mark.parametrize('utterance', ['play chess', "don't play baby shark", 'How do I play a video?', 'play it'])
def test_title_search_does_not_invent_targets_for_nonvideo_commands(utterance):
    assert direct_video_plan(utterance, VIDEO_TOOL_NAMES, SessionState()) is None


def test_explicit_correction_can_execute_the_corrected_video_request():
    intent = IntentRouter().route('No, I meant play Baby Shark', SessionState())
    assert intent.goal == 'play Baby Shark' and intent.requires_tools
    assert direct_video_plan(intent.goal, VIDEO_TOOL_NAMES)['arguments'] == {'query': 'Baby Shark'}


@pytest.mark.parametrize('utterance,path,quality', [
    ('play it in chat', 'D:/AI/first.mp4', 'auto'),
    ('play this video', 'D:/AI/first.mp4', 'auto'),
    ('play the second clip in chat', 'D:/AI/second.mp4', 'auto'),
    ('play it in 720p', 'D:/AI/first.mp4', '720p'),
    ('play the video again', 'D:/AI/first.mp4', 'auto'),
])
def test_playback_followups_keep_verified_files_instead_of_searching(utterance, path, quality):
    from tests.test_video_chat import media_session
    state = media_session()
    plan = direct_video_plan(utterance, VIDEO_TOOL_NAMES, state)
    assert plan['tool'] == 'play_video'
    assert plan['arguments'] == {'path': path, 'quality': quality}
    assert direct_video_plan(utterance, VIDEO_TOOL_NAMES, SessionState()) is None


@pytest.mark.parametrize('utterance', ['play Baby Shark in chat', 'play Baby Shark on youtube'])
def test_named_video_search_retains_delivery_wording_support(utterance):
    plan = direct_video_plan(utterance, VIDEO_TOOL_NAMES, SessionState())
    assert plan['tool'] == 'search_play_video'
    assert plan['arguments'] == {'query': 'Baby Shark'}


def engine(tmp_path):
    permissions = PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path)
    result = VideoWorkflow(permissions, SimpleNamespace(scan=lambda _: {'clean': True}), root=tmp_path)
    registry = ToolRegistry(permissions)
    register_video_workflow_tools(registry, result)
    return result, registry


def test_search_failure_is_a_source_card_not_a_fake_local_file(tmp_path, monkeypatch):
    workflow, registry = engine(tmp_path)
    monkeypatch.setattr('app.video_workflow.run_process', lambda *a, **k: json.dumps({'ok': True, 'results': [
        {'title': 'Baby Shark Dance', 'url': 'https://www.youtube.com/watch?v=XqZsoesa55w'}]}))
    monkeypatch.setattr('app.video_workflow.validate_public_url', lambda url: url)
    workflow.download = Mock(side_effect=ValueError('Publisher rejected this download'))
    # Exercise the nested registry's permissions instead of permitting a fake download.
    workflow.permissions.policies['public_download'] = 'DENY'
    result = registry.execute('search_play_video', {'query': 'baby shark'})
    assert result.success and not result.output['playback_ready']
    assert 'path' not in result.output
    assert 'has not started' in result.output['text']
    assert result.output['url'].endswith('XqZsoesa55w')
    state = SessionState()
    state.last_video = {'path': 'old.mp4', 'selected_path': 'old.mp4'}
    state.remember_video_result('search_play_video', result.output)
    assert state.last_video == {'url': result.output['url']}


def test_unrelated_search_result_is_not_played(tmp_path, monkeypatch):
    workflow, _ = engine(tmp_path)
    monkeypatch.setattr('app.video_workflow.run_process', lambda *a, **k: json.dumps({'ok': True, 'results': [
        {'title': 'Rust tutorial', 'url': 'https://www.youtube.com/watch?v=abcdefghijk'}]}))
    workflow._call = Mock()
    result = workflow.search_play('baby shark')
    assert not result['playback_ready'] and 'No close title match' in result['text']
    workflow._call.assert_not_called()


def test_ready_does_not_mean_playback_has_started(tmp_path):
    workflow, _ = engine(tmp_path)
    workflow._call = Mock(return_value={'path': str(tmp_path / 'video.mp4'), 'metadata': {}})
    result = workflow.play(path=str(tmp_path / 'video.mp4'))
    assert result['playback_ready'] and not result['playback_started']
    assert result['text'].startswith('Video ready in chat:')


def test_chat_correction_is_local_with_context_and_replacement_query(tmp_path):
    from app.memory.database import MemoryDatabase
    from app.chat_feedback import remember_correction
    from app.agent.conversation import recall_query, refers_to_previous_reply
    memory = MemoryDatabase(tmp_path / 'feedback.db')
    memory.initialize()
    memory.record_message('session', 'user', 'Explain Python memory management')
    memory.record_message('session', 'assistant', 'Python uses reference counting.')
    correction = 'u got me wrong. I meant Rust memory management'
    assert refers_to_previous_reply(correction)
    record = remember_correction(memory, 'session', correction)
    assert record is not None
    assert recall_query(correction, memory.conversation_history('session')) == 'Rust memory management'
    recalled = memory.retrieve('Rust memory management', memory_type='reflection', session_id='session')
    assert recalled and 'owner_feedback_only' in recalled[0]['content']
    assert IntentRouter().route(correction).classification_source == 'chat_correction'


@pytest.mark.parametrize('claim', ['I am playing Baby Shark now.', 'The video is now playing.', 'I played the video.'])
def test_text_reply_cannot_claim_playback_without_tools(claim):
    from app.action_claims import unbacked_claims
    assert any('played media' in item for item in unbacked_claims(claim))
