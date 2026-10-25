from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from app.video_workflow import (VideoWorkflow, candidate_windows, direct_video_plan,
                                executable, quality_selector, register_video_workflow_tools, subtitle_text)


@pytest.fixture
def workflow(tmp_path):
    engine = VideoWorkflow(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path),
                           SimpleNamespace(scan=Mock(return_value={'clean': True})), root=tmp_path / 'download/videos')
    engine.analyzer.cache_root = tmp_path / 'cache'
    return engine


@pytest.fixture
def source(tmp_path):
    if not executable('ffmpeg'):
        pytest.skip('FFmpeg is not installed')
    from app.video_workflow import run_process
    path = tmp_path / 'source.mp4'
    run_process([executable('ffmpeg'), '-v', 'error', '-n', '-f', 'lavfi', '-i',
                 'testsrc2=size=320x180:rate=10:duration=15', '-f', 'lavfi', '-i',
                 'sine=frequency=440:sample_rate=44100:duration=15', '-c:v', 'libx264',
                 '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)])
    return path


def test_quality_ceiling_and_concrete_requests_route_to_downloading_not_generation():
    assert '[height<=?720]' in quality_selector('720p')
    assert '[height<=?1080]' in quality_selector('auto')
    with pytest.raises(ValueError):
        quality_selector('dangerous arbitrary format')
    names = ['download_video', 'make_video_clips']
    plan = direct_video_plan('Download this video https://example.com/watch?v=12 in 720p', names)
    assert plan['tool'] == 'download_video'
    assert plan['arguments']['quality'] == '720p'
    plan = direct_video_plan('Make 3 clips, 30 seconds each, from this video https://example.com/video', names)
    assert plan['tool'] == 'make_video_clips' and plan['arguments']['clip_seconds'] == 30
    assert direct_video_plan('Make a video of a dancing cat', names) is None
    from app.intent.router import IntentRouter
    intent = IntentRouter().route('Make a video clip from https://example.com/video')
    assert intent.is_action and intent.entities['modality'] == 'video_editing'


def test_candidates_rank_observed_hooks_without_inventing_probabilities():
    segments = [{'start': 0, 'end': 12, 'text': 'Hello everyone, welcome back to the channel.'},
                {'start': 12, 'end': 24, 'text': 'Why does this mistake happen? Here is a solution.'},
                {'start': 24, 'end': 38, 'text': 'Try this step instead because it saves your result.'}]
    rows = candidate_windows(segments, 38, 20)
    assert rows[0]['start'] == 12
    assert all(0 <= r['start'] < r['end'] <= 38 for r in rows)
    assert all(r['confidence'] == 'low' for r in rows)
    visual = candidate_windows([], 15, 10)
    assert all(r['score'] is None for r in visual)
    assert 'Cannot assess' in visual[0]['weaknesses'][0]


def test_captions_are_clipped_retimed_and_keep_unicode():
    result = subtitle_text([{'start': 8, 'end': 12, 'text': 'Hello <b>world</b>'},
                            {'start': 14, 'end': 25, 'text': 'مرحبا'}], 10, 20)
    assert '00:00:00,000 --> 00:00:02,000' in result
    assert '00:00:04,000 --> 00:00:10,000' in result
    assert 'Hello world' in result and 'مرحبا' in result


def test_real_export_verifies_duration_dimensions_audio_and_original_hash(workflow, source):
    from app.video_workflow import sha256
    before = sha256(source)
    result = workflow.create_clip(str(source), 2.3, 7.3, aspect='vertical', quality='standard')
    assert result['complete'] and Path(result['path']).is_file()
    assert result['metadata']['duration_seconds'] == pytest.approx(5, abs=.25)
    assert (result['metadata']['width'], result['metadata']['height']) == (720, 1280)
    assert result['metadata']['audio']
    assert sha256(source) == before
    assert Path(result['path']).parent.is_relative_to(workflow.root / 'clips')


def test_captioned_export_requires_matching_evidence_and_burns_subtitles(workflow, source, tmp_path):
    from app.video_workflow import sha256
    report = tmp_path / 'analysis.json'
    report.write_text(json.dumps({'source_sha256': sha256(source), 'audio': {'segments': [
        {'start': 0, 'end': 5, 'text': 'A real local clip caption'}]}}), encoding='utf-8')
    result = workflow.create_clip(str(source), 0, 3, aspect='original', analysis_path=str(report))
    assert result['captions_burned']
    assert Path(result['subtitle_path']).read_text(encoding='utf-8').startswith('1\n')
    data = json.loads(report.read_text())
    data['source_sha256'] = 'unrelated'
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='different or modified'):
        workflow.create_clip(str(source), 0, 3, analysis_path=str(report))


@pytest.mark.parametrize('start,end', [(5, 2), (-1, 2), (0, 99), (float('nan'), 2)])
def test_bad_clip_ranges_are_refused(workflow, source, start, end):
    with pytest.raises(ValueError, match='Clip times'):
        workflow.create_clip(str(source), start, end)


def test_failed_admission_never_runs_decoder(workflow, source, monkeypatch):
    workflow.scanner.scan.return_value = {'clean': False}
    probe = Mock()
    monkeypatch.setattr(workflow, 'probe', probe)
    with pytest.raises(ValueError, match='admission'):
        workflow.create_clip(str(source), 0, 3)
    probe.assert_not_called()


def test_pipeline_rechecks_nested_network_and_filesystem_policy(workflow, source):
    workflow.permissions.policies['filesystem_read'] = 'DENY'
    registry = ToolRegistry(workflow.permissions)
    register_video_workflow_tools(registry, workflow)
    result = registry.execute('make_video_clips', {'path': str(source)})
    assert not result.success
    workflow.scanner.scan.assert_not_called()
    workflow.permissions.policies['public_download'] = 'DENY'
    result = registry.execute('make_video_clips', {'url': 'https://example.com/video.mp4'})
    assert not result.success


def test_model_can_only_rank_real_candidates_and_invalid_indices_are_discarded(workflow, source, monkeypatch):
    from app.video_workflow import sha256
    workflow.analyzer.analyze = Mock(return_value={'sha256': sha256(source), 'frames': [],
       'audio_status': 'transcribed', 'audio': {'segments': [{'start': 0, 'end': 15,
       'text': 'Why does this work? Here is the practical solution.'}]}})
    workflow.model = SimpleNamespace(generate=Mock(return_value=SimpleNamespace(content=json.dumps({'rankings': [
        {'index': 999, 'score': 100, 'title': 'fake', 'reasons': [], 'weaknesses': []},
        {'index': 0, 'score': 72, 'title': 'Real observed question', 'reasons': ['An opening question'],
         'weaknesses': ['Audience relevance is uncertain']}]}))))
    report = workflow.analyze(str(source), clip_seconds=10, max_clips=2)
    assert report['ranking_method'] == 'local_model_editorial_review'
    assert len(report['candidates']) == 1
    assert report['candidates'][0]['title'] != 'fake'
    assert 'not a probability' in report['virality']


def test_public_worker_connection_guard_blocks_redirects_and_actual_private_connections(monkeypatch):
    from app.video_download_worker import guard_public_network
    original_socket, original_resolve = socket.socket, socket.getaddrinfo
    monkeypatch.setattr(socket, 'getaddrinfo', original_resolve)
    monkeypatch.setattr(socket, 'socket', original_socket)
    guard_public_network()
    with pytest.raises(PermissionError, match='public internet'):
        socket.getaddrinfo('127.0.0.1', 80)
    with socket.socket() as connection:
        with pytest.raises(PermissionError):
            connection.connect(('127.0.0.1', 80))


def test_video_action_reply_exposes_files_in_chat_instead_of_generic_done():
    from app.assistant import AssistantService
    outcome = SimpleNamespace(history=[{'tool': 'make_video_clips', 'result': {'success': True,
              'output': {'text': 'Created clip: D:/AI/download/videos/clips/clip.mp4'}}}])
    assert 'clip.mp4' in AssistantService._generated_media_reply(outcome)


def test_selection_returns_fewer_clips_instead_of_overlapping_candidates(workflow, source, monkeypatch):
    from app.video_workflow import sha256
    monkeypatch.setattr(workflow, 'probe', lambda _path: {'duration_seconds': 33, 'width': 320, 'height': 240})
    workflow.analyzer.analyze = Mock(return_value={'sha256': sha256(source), 'frames': [],
        'audio_status': 'unavailable', 'audio': {'segments': []}})
    report = workflow.analyze(str(source), clip_seconds=15, max_clips=10)
    intervals = sorted((c['start'], c['end']) for c in report['candidates'])
    assert intervals == [(0.0, 15.0), (20.0, 33)]
    assert all(a[1] <= b[0] for a, b in zip(intervals, intervals[1:]))
    assert all(c['score'] is None for c in report['candidates'])


def test_numeral_only_transcription_does_not_receive_editorial_scores():
    from app.video_workflow import candidate_windows
    candidates = candidate_windows([{'start': 0, 'end': 30, 'text': '1. 1. 1.'}], 33, 15)
    assert all(c['score'] is None and not c['transcript'] for c in candidates)


@pytest.mark.parametrize('speed', [.5, 1.5, 2])
def test_edit_trim_speed_picture_and_audio_preserve_source(workflow, source, speed):
    from app.video_workflow import sha256
    before = sha256(source)
    result = workflow.edit_clip(str(source), start_seconds=2, end_seconds=8,
                                aspect='square', quality='standard', speed=speed,
                                enhance=True, normalize_audio=True)
    assert abs(result['metadata']['duration_seconds'] - 6 / speed) < .5
    assert result['metadata']['audio']
    assert (result['metadata']['width'], result['metadata']['height']) == (720, 720)
    assert result['picture_adjusted'] and result['audio_normalized']
    assert result['speed'] == speed
    assert sha256(source) == before


def test_edit_rechecks_nested_export_permission(workflow, source):
    registry = ToolRegistry(workflow.permissions)
    register_video_workflow_tools(registry, workflow)
    workflow.permissions.policies['filesystem_write_project'] = 'DENY'
    with pytest.raises(ValueError, match='Policy'):
        workflow.edit_clip(str(source), end_seconds=4)


@pytest.mark.parametrize('speed', [0, float('inf'), 2.5])
def test_invalid_export_speed_is_refused(workflow, source, speed):
    with pytest.raises(ValueError, match='speed'):
        workflow.create_clip(str(source), 0, 5, speed=speed)
