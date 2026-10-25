from __future__ import annotations
import json
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.config import PROJECT_ROOT
from app.session import SessionState
from app.video_workflow import VIDEO_TOOL_NAMES, VideoWorkflow, direct_video_plan, executable, register_video_workflow_tools
from app.tools.registry import ToolRegistry
from app.safety.permissions import PermissionManager


def media_session():
    state = SessionState()
    state.remember_video_result('make_video_clips', {'path': 'D:/AI/source.mp4',
        'download': {'url': 'https://www.youtube.com/watch?v=example'},
        'clips': [{'path': 'D:/AI/first.mp4'}, {'path': 'D:/AI/second.mp4'}]})
    return state


@pytest.mark.parametrize('utterance,tool,arguments', [
    ('play it', 'play_video', {'path': 'D:/AI/first.mp4', 'quality': 'auto'}),
    ('play the second clip', 'play_video', {'path': 'D:/AI/second.mp4', 'quality': 'auto'}),
    ('play clip 2', 'play_video', {'path': 'D:/AI/second.mp4', 'quality': 'auto'}),
    ('download it in 720p', 'download_video', {'url': 'https://www.youtube.com/watch?v=example', 'quality': '720p'}),
    ('analyze this video', 'analyze_video_clips', {'path': 'D:/AI/source.mp4'}),
    ('make 3 square clips from that video, 30 seconds each', 'make_video_clips',
     {'path': 'D:/AI/source.mp4', 'quality': 'auto', 'max_clips': 3, 'clip_seconds': 30, 'aspect': 'square'}),
    ('make five clips from that video', 'make_video_clips',
     {'path': 'D:/AI/source.mp4', 'quality': 'auto', 'max_clips': 5, 'aspect': 'vertical'}),
    ('pause it', 'video_player_control', {'path': 'D:/AI/first.mp4', 'action': 'pause'}),
    ('resume', 'video_player_control', {'path': 'D:/AI/first.mp4', 'action': 'play'}),
    ('seek the video to 1:20', 'video_player_control', {'path': 'D:/AI/first.mp4', 'action': 'seek', 'value': 80}),
    ('set video volume to 40', 'video_player_control', {'path': 'D:/AI/first.mp4', 'action': 'volume', 'value': 40}),
    ('video speed 1.5', 'video_player_control', {'path': 'D:/AI/first.mp4', 'action': 'rate', 'value': 1.5}),
])
def test_followups_resolve_verified_media_and_select_the_correct_tool(utterance, tool, arguments):
    session = media_session()
    plan = direct_video_plan(utterance, VIDEO_TOOL_NAMES, session)
    assert plan['tool'] == tool and plan['arguments'] == arguments
    from app.intent.router import IntentRouter
    intent = IntentRouter().route(utterance, session)
    assert intent.is_action and intent.entities['modality'] == 'video_editing'


@pytest.mark.parametrize('utterance', ['What tools do you use for video?', 'How can I download it?',
                                     "Don't download this video", 'open notepad', 'play the fourth clip'])
def test_nonactions_and_missing_clip_references_do_not_invent_targets(utterance):
    assert direct_video_plan(utterance, VIDEO_TOOL_NAMES, media_session()) is None


def test_bare_link_and_url_words_do_not_turn_download_into_play():
    url = 'https://www.youtube.com/watch?v=example'
    assert direct_video_plan(url, VIDEO_TOOL_NAMES)['tool'] == 'inspect_video_link'
    assert direct_video_plan('Download this video ' + url, VIDEO_TOOL_NAMES)['tool'] == 'download_video'
    plan = direct_video_plan('Play "D:/AI/my video.mp4"', VIDEO_TOOL_NAMES)
    assert plan['arguments']['path'] == 'D:/AI/my video.mp4'
    assert direct_video_plan('play it', VIDEO_TOOL_NAMES, SessionState()) is None


def test_failed_results_do_not_replace_video_context_and_inspection_clears_unrelated_source():
    session = media_session()
    original = dict(session.last_video)
    session.update_from_outcome(SimpleNamespace(history=[{'tool': 'download_video',
        'result': {'success': False, 'output': {'path': 'fake.mp4'}}}]))
    assert session.last_video == original
    session.remember_video_result('inspect_video_link', {'url': 'https://youtu.be/new'})
    assert session.last_video == {'url': 'https://youtu.be/new'}


def test_new_play_tool_honors_nested_download_and_file_policy(tmp_path):
    permissions = PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path)
    engine = VideoWorkflow(permissions, SimpleNamespace(scan=Mock(return_value={'clean': True})), root=tmp_path)
    registry = ToolRegistry(permissions)
    register_video_workflow_tools(registry, engine)
    permissions.policies['public_download'] = 'DENY'
    engine.download = Mock()
    assert not registry.execute('play_video', {'url': 'https://youtu.be/example'}).success
    engine.download.assert_not_called()
    permissions.policies['filesystem_read'] = 'DENY'
    assert not registry.execute('play_video', {'path': str(tmp_path / 'x.mp4')}).success


def test_native_card_updates_do_not_seek_and_release_player(shared_tk, tmp_path):
    from app.gui import HayatGUI
    from app.gui_video_player import NativeVideoPreview
    gui = SimpleNamespace(**{name: getattr(HayatGUI, name) for name in ('PANEL_2', 'LINE', 'WHITE', 'BLACK', 'MUTED')})
    gui._open_file = gui._open_video_studio = Mock()
    card = NativeVideoPreview(shared_tk, gui, tmp_path / 'x.mp4')
    card.after_cancel(card._launch_timer)
    card._launch_timer = None
    while not card._commands.empty():
        card._commands.get_nowait()
    try:
        card._updates.put({'kind': 'metadata', 'metadata': {'duration_seconds': 12}})
        card._updates.put({'kind': 'ready'})
        card._updates.put({'kind': 'state', 'seconds': 3, 'playing': False, 'muted': False})
        card._drain()
        shared_tk.update()
        assert card._position == 3 and card._commands.empty()
        card.request_play()
        assert card._commands.get_nowait()['action'] == 'play'
        card.seek(99)
        assert card._commands.get_nowait()['value'] == 12
        process = Mock(poll=Mock(return_value=None))
        card._process = process
        card.close()
        process.kill.assert_called_once()
    finally:
        card.destroy()


def test_public_video_links_get_widgets_without_fetching_or_accepting_credentials():
    from app.gui_rich import find_video_links
    assert find_video_links('Play https://youtu.be/example and https://youtu.be/example.') == ['https://youtu.be/example']
    assert not find_video_links('https://youtube.com.evil.test/watch?v=x https://user:secret@youtube.com/watch?v=x')
    assert find_video_links('https://example.com/test.mp4') == ['https://example.com/test.mp4']


def test_camera_card_uses_verified_results_and_stop_closes_connection(shared_tk):
    import tkinter as tk
    from app.gui import HayatGUI
    gui = SimpleNamespace(**{name: getattr(HayatGUI, name) for name in ('PANEL_2', 'LINE', 'WHITE', 'BLACK', 'MUTED')})
    gui._closed, gui._video_cards = False, {}
    gui.chat = tk.Text(shared_tk)
    gui._chat_follows = lambda: False
    gui._open_source = Mock()
    camera = {'event_id': 'cameras_delaware:KCAM001', 'title': 'Public traffic camera', 'live': True,
              'media_label': 'HLS stream', 'provider': 'Delaware DOT', 'source_url': 'https://deldot.gov/map/'}
    event = {'tool': 'intelligence_camera_play', 'result': {'success': True, 'output': camera},
             'verification': {'passed': False}}
    outcome = SimpleNamespace(history=[event])
    try:
        HayatGUI._embed_camera_outcome(gui, outcome)
        assert not gui._video_cards  # Model text and unverified results cannot start a network player.
        event['verification']['passed'] = True
        HayatGUI._embed_camera_outcome(gui, outcome)
        card = next(iter(gui._video_cards.values()))
        card.after_cancel(card._launch_timer)
        card._launch_timer = None
        assert not card.slider.winfo_manager()
        card._publish({'kind': 'ready'})
        card._publish({'kind': 'state', 'seconds': 5, 'playing': True, 'muted': True, 'width': 1280, 'height': 720})
        card._drain()
        assert 'Playing' in card.detail.get() and '1280' in card.detail.get()
        assert card.mute_button['text'] == 'Unmute'
        card._publish({'kind': 'error', 'error': 'Publisher temporarily unavailable'})
        card._drain()
        assert str(card.retry_button['state']) == 'normal'
        assert 'Publisher temporarily unavailable' in card.detail.get()
        card.retry()
        assert card._launch_timer is not None
        card.after_cancel(card._launch_timer)
        card._launch_timer = None
        card._publish({'kind': 'ready'})
        card._drain()
        process = Mock(poll=Mock(return_value=None))
        card._process = process
        pending = queue.Queue()
        gui._post = lambda callback: pending.put(callback)
        results = []
        worker = threading.Thread(target=lambda: results.append(HayatGUI._dispatch_camera_control(gui, 'stop')))
        worker.start()
        pending.get(timeout=2)()
        worker.join(timeout=2)
        assert results == [{'accepted': True}]
        process.kill.assert_called_once()
        assert card._closed and card._timer is None
        assert 'connection closed' in card.detail.get()
    finally:
        for card in gui._video_cards.values():
            card.destroy()
        gui.chat.destroy()


def test_player_tool_dispatches_on_ui_thread_and_refuses_unready_card(tmp_path):
    from app.gui import HayatGUI
    permissions = PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path)
    engine = VideoWorkflow(permissions, SimpleNamespace(scan=Mock(return_value={'clean': True})), root=tmp_path)
    registry = ToolRegistry(permissions)
    register_video_workflow_tools(registry, engine)
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'local placeholder; no decoder is invoked by this dispatch test')
    pending = queue.Queue()
    called = []
    card = SimpleNamespace(_ready=True, _send=lambda *args: called.append((threading.get_ident(), args)))
    gui = SimpleNamespace(_closed=False, _video_cards={str(source.resolve()): card},
                          _post=lambda callback: pending.put(callback))
    engine.player_dispatch = lambda *args: HayatGUI._dispatch_video_control(gui, *args)
    result = []
    def execute():
        result.append(registry.execute('video_player_control', {'path': str(source), 'action': 'pause'}))
    worker = threading.Thread(target=execute)
    worker.start()
    pending.get(timeout=3)()
    worker.join(timeout=3)
    assert result[0].success and result[0].output['accepted']
    assert called == [(threading.get_ident(), ('pause',))]
    assert not registry.execute('video_player_control', {'path': str(source), 'action': 'volume', 'value': 200}).success
    card._ready = False
    worker = threading.Thread(target=execute)
    worker.start()
    pending.get(timeout=3)()
    worker.join(timeout=3)
    assert not result[-1].success and 'not ready' in result[-1].error


def test_real_isolated_libvlc_player_pause_seek_rate_and_audio_track(tmp_path):
    from app.gui_video_player import native_player_available
    if not native_player_available() or not executable('ffmpeg'):
        pytest.skip('Native playback engines are unavailable')
    source = tmp_path / 'test.mp4'
    subprocess.run([executable('ffmpeg'), '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
        'testsrc2=size=320x180:rate=20', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
        '-t', '6', '-c:v', 'libx264', '-threads', '1', '-c:a', 'aac', str(source)], check=True)
    process = subprocess.Popen([sys.executable, '-m', 'app.video_player_worker'], cwd=str(PROJECT_ROOT),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    events = queue.Queue()
    def read():
        for line in process.stdout:
            events.put(json.loads(line))
    threading.Thread(target=read, daemon=True).start()
    def send(command):
        process.stdin.write(json.dumps(command) + '\n')
        process.stdin.flush()
    def until(predicate, timeout=25):
        stop = time.monotonic() + timeout
        while time.monotonic() < stop:
            try:
                event = events.get(timeout=.2)
                assert event.get('kind') != 'error', event
                if predicate(event):
                    return event
            except queue.Empty:
                assert process.poll() is None, process.stderr.read()
        raise AssertionError('Player did not report the requested state')
    try:
        send({'path': str(source), 'duration': 6, 'headless': True})
        until(lambda e: e['kind'] == 'ready')
        send({'action': 'play'})
        event = until(lambda e: e.get('playing') and e.get('seconds', 0) > .2)
        assert event['audio_tracks'] >= 2  # VLC also lists a disabled-audio track.
        send({'action': 'pause'})
        until(lambda e: e['kind'] == 'state' and not e['playing'])
        send({'action': 'seek', 'value': 3})
        until(lambda e: abs(e.get('seconds', -99) - 3) < .5)
        send({'action': 'rate', 'value': 1.5})
        until(lambda e: e.get('rate') == 1.5)
        send({'action': 'close'})
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
