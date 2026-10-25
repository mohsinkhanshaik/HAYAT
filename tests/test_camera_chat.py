"""Public camera playback from chat: follow-ups, place ranking, stream checks and the chat card.

The owner listed London cameras and replied "play"; nothing matched, the model
answered "Playing…" and no player opened. These tests pin the fixes.
"""
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.intelligence.camera_chat import camera_request, rank_cameras
from app.intelligence.feeds import BY_KEY
from app.intelligence.manager import IntelligenceManager
from app.intelligence.tools import TOOL_NAMES, direct_plan, register_intelligence_tools, report
from app.intent.router import IntentRouter
from app.safety.permissions import PermissionManager
from app.session import SessionState
from app.tools.registry import ToolRegistry
from tests.test_intelligence_resources import ExtendedFetcher
from tests.test_public_intelligence import make_due

DELAWARE = 'https://video.deldot.gov:443/live/{}.stream/playlist.m3u8'


def camera_row(identifier, title, feed='cameras_london', **details):
    return {'id': f'{feed}:{identifier}', 'feed': feed, 'title': title, 'details': {'available': True, **details}}


def listed(*rows):
    session = SessionState()
    session.remember_camera_result('intelligence_cameras', {'cameras': list(rows)})
    return session


LONDON = [camera_row('A', 'A406 Billet Upass E', video_url='https://x/a.mp4', image_url='https://x/a.jpg'),
          camera_row('B', 'Romford Rd / Tennyson Rd', video_url='https://x/b.mp4'),
          camera_row('C', 'Piccadilly Circus', image_url='https://x/c.jpg')]


def planned(text, session=None):
    plan = direct_plan(text, TOOL_NAMES, session)
    return plan and (plan['tool'].removeprefix('intelligence_'), plan['arguments'])


def test_follow_ups_resolve_against_the_last_verified_camera_list():
    session = listed(*LONDON)
    assert planned('play') is None  # Without a list there is nothing to refer to.
    for phrase in ('play', 'play it', 'Hayat play it please', 'watch it', 'play the first one'):
        assert planned(phrase, session) == ('camera_play', {'event_id': 'cameras_london:A', 'live_only': False}), phrase
    for phrase in ('play the second one', 'play camera 2', 'play #2', 'play 2nd one'):
        assert planned(phrase, session)[1]['event_id'] == 'cameras_london:B', phrase
    assert planned('play last one', session)[1]['event_id'] == 'cameras_london:C'
    assert planned('play piccadilly circus', session)[1]['event_id'] == 'cameras_london:C'
    assert planned('play camera 9', session) is None
    assert IntentRouter().route('play', session).entities['modality'] == 'public_intelligence'
    # The owner's own devices, voice controls and other media are not public cameras.
    for phrase in ('open camera', 'start camera', 'play my camera', 'play front camera', 'play the camera in my office',
                   'play music', 'watch youtube stream', 'show cameras in london', 'stop', 'mute', 'unmute'):
        assert camera_request(phrase, session) is None, phrase
    session.camera_context_at = time.monotonic() - 3600
    assert planned('play it', session) is None  # An hour-old list is not "it".


def test_playing_camera_takes_pause_next_and_stop_and_a_newer_video_takes_over():
    session = listed(*LONDON)
    session.remember_camera_result('intelligence_camera_play', {'event_id': 'cameras_london:A'})
    assert session.active_camera == 'cameras_london:A' and session.camera_context == 'play'
    for phrase, action in [('pause', 'pause'), ('resume', 'play'), ('play', 'play'), ('play it again', 'play'),
                           ('next', 'next'), ('another one', 'next'), ('stop it', 'stop'), ('mute the camera', 'mute')]:
        assert planned(phrase, session) == ('camera_control', {'action': action}), phrase
    for phrase in ('stop', 'mute', 'unmute'):
        assert camera_request(phrase, session) is None  # Still "stop speaking" and the voice mute.
    session.remember_camera_result('intelligence_camera_control', {'camera_control': 'stop'})
    assert session.active_camera is None
    session.remember_camera_result('intelligence_camera_play', {'event_id': 'cameras_london:B'})
    session.camera_context_at = time.monotonic() - 3600
    assert planned('next', session) is None  # An hour later "next" may mean a lesson, not this camera.
    assert planned('next camera', session) == ('camera_control', {'action': 'next'})
    session.remember_camera_result('intelligence_camera_control', {'camera_control': 'pause'})
    assert planned('next', session) == ('camera_control', {'action': 'next'})
    session.remember_video_result('play_video', {'path': 'D:/AI/clip.mp4'})
    assert session.last_cameras == [] and session.active_camera is None
    assert planned('pause', session) is None


@pytest.mark.parametrize('phrase,expected', [
    ('play london camera', ('camera_play', {'live_only': False, 'query': 'london'})),
    ('Play the London camera', ('camera_play', {'live_only': False, 'query': 'london'})),
    ('show me live camera in Iowa', ('camera_play', {'live_only': True, 'query': 'iowa'})),
    ('play live traffic camera in Delaware', ('camera_play', {'live_only': True, 'query': 'delaware'})),
    ('play delaware live traffic cam', ('camera_play', {'live_only': True, 'query': 'delaware'})),
    ('play live cameras in New York', ('camera_play', {'live_only': True, 'query': 'new york'})),
    ('play a live stream', ('camera_play', {'live_only': True})),
    ('next camera', ('camera_control', {'action': 'next'})),
    ('stop the camera', ('camera_control', {'action': 'stop'})),
    ('unmute camera', ('camera_control', {'action': 'unmute'})),
    ('Show live CCTV', ('cameras', {'media': 'stream', 'available_only': True})),
    ('Find public cameras in London', ('cameras', {'query': 'London'})),
])
def test_chat_phrasings_need_no_model(phrase, expected):
    assert planned(phrase) == expected


def test_place_ranking_keeps_the_named_region_ahead_of_street_names():
    titles = {key: feed.title for key, feed in BY_KEY.items()}
    rows = [camera_row('iowa-ave', 'I-215 : (64) 0.3 Mi N/O Iowa', 'cameras_caltrans_8', state='California'),
            camera_row('cb', 'CB - I-680 @ MM 1.1', 'cameras_iowa', state='Iowa'),
            camera_row('trabuco', 'SR-133 : S/O Trabuco Road Overcross', 'cameras_caltrans_12', state='California'),
            camera_row('KCAM002', 'W NORTH ST @ WEST DOVER CONNECTOR', 'cameras_delaware', state='Delaware'),
            camera_row('antelope', 'Antelope Mtn. (Lassen) 1', 'cameras_alertca', state='California'),
            camera_row('friars', 'SR-163 : Friars', 'cameras_caltrans_11', state='California', county='San Diego')]
    def feeds(query):
        return [row['feed'] for row in rank_cameras(rows, query, titles)]
    assert feeds('Iowa') == ['cameras_iowa']  # Not the California camera on Iowa Avenue.
    assert feeds('dover') == ['cameras_delaware']  # Not "Roa(d Over)cross".
    assert feeds('san diego') == ['cameras_caltrans_11']  # Not ALERTCalifornia's operator "(UC San Diego)".
    assert feeds('los angeles') == [] and len(feeds('')) == len(rows)


@pytest.fixture
def cameras(tmp_path):
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    manager = IntelligenceManager(tmp_path / 'intelligence.db', ExtendedFetcher(), registry, extended=True)
    register_intelligence_tools(registry, manager)
    manager.refresh()
    yield manager
    manager.close()


def play(manager, **arguments):
    return manager.registry.execute('intelligence_camera_play', arguments)


def test_playback_skips_dead_streams_and_shows_what_each_publisher_really_offers(cameras):
    catalog = cameras.fetcher.payloads['cameras_delaware']['videoCameras']
    catalog.append({**catalog[0], 'id': 'KCAM002', 'title': 'W NORTH ST @ WEST DOVER CONNECTOR',
                    'urls': {'m3u8s': DELAWARE.format('KCAM002')}})
    make_due(cameras, 'cameras_delaware')
    cameras.refresh(['cameras_delaware'])
    cameras.fetcher.dead_streams = {DELAWARE.format('KCAM001')}
    live = play(cameras, query='Delaware')
    assert live.success and live.output['event_id'] == 'cameras_delaware:KCAM002' and live.output['mode'] == 'stream'
    assert live.output['alternatives'] == []  # The stream that just failed is not offered as "next".
    retrying = play(cameras, event_id='cameras_delaware:KCAM001')
    assert retrying.success and 'keep reconnecting' in retrying.output['notice']
    cameras.fetcher.dead_streams.add(DELAWARE.format('KCAM002'))
    cameras._stream_checks.clear()
    dead = play(cameras, query='Delaware')
    assert dead.success and dead.output['mode'] == 'stream' and 'keep reconnecting' in dead.output['notice']

    london = play(cameras, query='London')
    assert london.success and london.output['mode'] == 'clip' and not london.output['live']
    assert 'short recorded clips' in london.output['notice']
    text = report([{'tool': london.tool, 'result': london.to_dict(), 'verification': {'passed': True}}])
    assert text.startswith('Hayat intelligence\nOpening public camera in desktop chat:') and 'latest recorded clip' in text
    still = play(cameras, query='New York')
    assert still.success and still.output['mode'] == 'still' and still.output['refresh_seconds'] >= 30
    assert 'refreshing still images' in still.output['notice'] and still.output['video_url'] == ''
    player_only = play(cameras, query='Maryland')
    assert not player_only.success and 'own web player' in player_only.error
    assert cameras.fetcher.calls.count('playlist') <= 6  # Checks are cached, not repeated per request.


def test_session_follows_only_verified_camera_results(cameras):
    found = cameras.registry.execute('intelligence_cameras', {'query': 'London'})
    session = SessionState()
    session.update_from_outcome(SimpleNamespace(history=[
        {'tool': 'intelligence_cameras', 'result': found.to_dict(), 'verification': {'passed': False}}]))
    assert session.last_cameras == []
    session.update_from_outcome(SimpleNamespace(history=[
        {'tool': 'intelligence_cameras', 'result': found.to_dict(), 'verification': {'passed': True}}]))
    assert session.last_cameras[0] == {'id': 'cameras_london:JamCams_test', 'title': 'Public test road', 'mode': 'clip'}
    assert planned('play', session) == ('camera_play', {'event_id': 'cameras_london:JamCams_test', 'live_only': False})


def test_publisher_availability_flag_does_not_block_owner_playback(cameras, monkeypatch):
    row = cameras.event_record('cameras_delaware:KCAM001')
    row['event']['details']['available'] = False
    monkeypatch.setattr(cameras, 'event_record', lambda _identifier: row)
    result = play(cameras, event_id='cameras_delaware:KCAM001')
    assert result.success and result.output['mode'] == 'stream'


def card_gui(execute=None):
    from app.gui import HayatGUI
    gui = SimpleNamespace(**{name: getattr(HayatGUI, name) for name in ('PANEL_2', 'LINE', 'WHITE', 'BLACK', 'MUTED')})
    gui._open_source = Mock()
    gui.runtime = SimpleNamespace(registry=SimpleNamespace(execute=execute or Mock()))
    return gui


CAMERA = {'event_id': 'cameras_delaware:KCAM001', 'title': 'First camera', 'live': True, 'mode': 'stream',
          'media_label': 'HLS stream', 'provider': 'Delaware DOT', 'source_url': 'https://deldot.gov/map/',
          'alternatives': [{'id': 'cameras_delaware:KCAM002', 'title': 'Second camera'}]}


def wait_until(root, condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        root.update()
        time.sleep(.02)
    return condition()


def test_still_camera_card_shows_the_publisher_image_and_pauses_refresh(shared_tk, tmp_path):
    from PIL import Image
    from app.gui_video_player import NativeVideoPreview
    picture = tmp_path / 'still.jpg'
    Image.new('RGB', (640, 360), '#336699').save(picture)
    still = {**CAMERA, 'event_id': 'cameras_nyc:x', 'title': 'Central Park West', 'mode': 'still', 'live': False,
             'media_label': 'Refreshing still', 'refresh_seconds': 30, 'alternatives': []}
    calls = []
    def execute(tool, arguments):
        calls.append(tool)
        if tool == 'intelligence_camera_play':
            return SimpleNamespace(success=True, output=still)
        return SimpleNamespace(success=True, output={'path': str(picture), 'fetched_at': '2026-10-05T20:00:00+00:00'})
    card = NativeVideoPreview(shared_tk, card_gui(execute), camera=still)
    try:
        assert wait_until(shared_tk, lambda: card._ready)
        assert calls == ['intelligence_camera_play', 'intelligence_camera_frame']  # No libVLC worker for a still.
        assert card._process is None and card._still_label is not None and card._still_timer is not None
        assert 'Still image · 640 × 360 · fetched 20:00:00 UTC · refreshes every 30 s' == card.detail.get()
        assert str(card.mute_button['state']) == 'disabled' and str(card.next_button['state']) == 'disabled'
        with pytest.raises(ValueError, match='no sound'):
            card.control('mute')
        card.control('pause')
        assert not card._playing and card._still_timer is None and card.play_button['text'] == 'Resume'
        card.control('play')
        assert wait_until(shared_tk, lambda: calls.count('intelligence_camera_frame') == 2)
    finally:
        card.destroy()
    assert card._closed and card._still_timer is None


def test_next_camera_switches_skips_failures_and_ignores_the_replaced_player(shared_tk):
    from app.gui_video_player import AUTO_ADVANCE, NativeVideoPreview
    card = NativeVideoPreview(shared_tk, card_gui(), camera=CAMERA)
    def hold_launch():
        card.after_cancel(card._launch_timer)
        card._launch_timer = None
    try:
        hold_launch()
        card._publish({'kind': 'ready'})
        card._drain()
        replaced = card._generation
        assert card.control('next') == {'event_id': 'cameras_delaware:KCAM002', 'title': 'Second camera'}
        hold_launch()
        assert card.camera['event_id'] == 'cameras_delaware:KCAM002' and 'Second camera' in card.title_label['text']
        assert card._alternatives == [{'id': 'cameras_delaware:KCAM001', 'title': 'First camera'}]
        card._publish({'kind': 'error', 'error': 'Video player stopped'}, replaced)
        card._drain()
        assert 'Switching to Second camera' in card.detail.get()  # The old player's exit is not this camera's error.
        for attempt in range(AUTO_ADVANCE):
            card._publish({'kind': 'error', 'error': 'Player startup timed out.'})
            card._drain()
            hold_launch()
            assert 'did not play' in card.detail.get() and card._auto_advances == attempt + 1
        card._publish({'kind': 'error', 'error': 'Player startup timed out.'})
        card._drain()
        assert str(card.retry_button['state']) == 'normal' and 'Player unavailable' in card.detail.get()
        card._publish({'kind': 'ready'})
        card._publish({'kind': 'state', 'seconds': 11, 'playing': False, 'ended': True, 'muted': True})
        card._drain()
        assert card.play_button['text'] == 'Replay' and 'latest clip' in card.detail.get()
        card.control('stop')
        assert card._closed and str(card.next_button['state']) == 'disabled'
        with pytest.raises(ValueError):
            card.control('next')
    finally:
        card.destroy()


def test_control_tool_accepts_the_new_actions_and_reaches_the_desktop_player(cameras):
    for action in ('next', 'mute', 'unmute'):
        refused = cameras.registry.execute('intelligence_camera_control', {'action': action})
        assert not refused.success and 'desktop chat player' in refused.error  # Schema allowed it; no card is open.
    assert 'desktop chat player' not in cameras.registry.execute('intelligence_camera_control', {'action': 'rewind'}).error
    cameras.player_dispatch = lambda action: {'accepted': True, 'event_id': 'cameras_delaware:KCAM002', 'title': 'Second camera'}
    switched = cameras.registry.execute('intelligence_camera_control', {'action': 'next'})
    assert 'switching to Second camera' in report([{'tool': switched.tool, 'result': switched.to_dict(), 'verification': {'passed': True}}])


class FakeVlc:
    """Enough of libVLC to script a publisher dropping a live stream."""
    class State:
        NothingSpecial, Opening, Playing, Paused, Stopped, Ended, Error = range(7)

    def __init__(self, drop_forever=False):
        self.player = SimpleNamespace(state=self.State.NothingSpecial, plays=0, media=[], muted=1, volume=60)
        player, state = self.player, self.State
        def play():
            player.plays += 1
            player.state = state.Ended if drop_forever and player.plays > 1 else state.Playing
            return 0
        player.play, player.get_state = play, lambda: player.state
        player.stop = lambda: setattr(player, 'state', state.Stopped)
        player.set_media = player.media.append
        player.video_get_size = lambda _index=0: (640, 360)
        player.get_time, player.get_rate, player.audio_get_track_count = (lambda: 5000), (lambda: 1.0), (lambda: 0)
        player.audio_get_mute, player.audio_get_volume = (lambda: player.muted), (lambda: player.volume)
        player.audio_set_mute = lambda value: setattr(player, 'muted', int(value))
        player.audio_set_volume = lambda value: setattr(player, 'volume', value)
        player.set_pause = player.set_rate = player.set_time = lambda *_args: None
        self.Instance = lambda *_flags: SimpleNamespace(
            media_player_new=lambda: player,
            media_new_location=lambda url: SimpleNamespace(url=url, options=[], add_option=lambda option: None))


def run_worker(monkeypatch, url, feed, vlc, drop_after=4, quit_after=8, quit_reconnects=0):
    """Run the worker loop in-process; the stream drops once after `drop_after` states."""
    import threading
    from app import video_player_worker as worker
    messages, done = [], threading.Event()
    def emit(message):
        messages.append(message)
        states = [m for m in messages if m['kind'] == 'state' and not m.get('reconnecting')]
        if len(states) == drop_after and vlc.player.plays == 1:
            vlc.player.state = vlc.State.Ended
        if len(states) >= quit_after or quit_reconnects and message.get('reconnecting', 0) >= quit_reconnects:
            done.set()
    class Stdin:
        def readline(self, _limit=-1):
            return json.dumps({'camera_url': url, 'feed': feed, 'headless': True}) + '\n'
        def __iter__(self):
            done.wait(20)
            return iter(())
    monkeypatch.setattr(worker, 'load_vlc', lambda: vlc)
    monkeypatch.setattr(worker, 'emit', emit)
    monkeypatch.setattr(worker.sys, 'stdin', Stdin())
    try:
        worker.main()
    finally:
        done.set()
    return messages


def test_live_worker_reopens_a_dropped_stream_and_clips_still_end(monkeypatch):
    live = FakeVlc()
    messages = run_worker(monkeypatch, DELAWARE.format('KCAM001'), 'cameras_delaware', live)
    assert {m['reconnecting'] for m in messages if m.get('reconnecting')} == {1}
    assert live.player.plays == 2 and len(live.player.media) == 2  # Reopened at the live edge, still muted.
    assert live.player.muted and messages[-1]['playing'] and not any(m.get('ended') for m in messages)

    from app import video_player_worker as worker
    assert worker.reconnect_delay(1000) == 60
    monkeypatch.setattr(worker, 'reconnect_delay', lambda _attempt: .01)
    dropping = FakeVlc(drop_forever=True)
    messages = run_worker(monkeypatch, DELAWARE.format('KCAM001'), 'cameras_delaware', dropping,
                          quit_after=99, quit_reconnects=8)
    assert dropping.player.plays >= 8
    assert max(m.get('reconnecting', 0) for m in messages) >= 8
    assert not any(m['kind'] == 'error' for m in messages)

    clip = FakeVlc()
    messages = run_worker(monkeypatch, 'https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/test.mp4', 'cameras_london', clip)
    assert clip.player.plays == 1 and messages[-1]['ended'] and not any(m.get('reconnecting') for m in messages)


def test_live_worker_recovers_initial_connection_failure_and_can_close(monkeypatch):
    from app import video_player_worker as worker
    monkeypatch.setattr(worker, 'reconnect_delay', lambda _attempt: .01)
    failing = FakeVlc()
    original = failing.player.play
    def play():
        original()
        if failing.player.plays <= 3:
            failing.player.state = failing.State.Error
            return -1
        return 0
    failing.player.play = play
    messages = run_worker(monkeypatch, DELAWARE.format('KCAM001'), 'cameras_delaware', failing, drop_after=99)
    assert failing.player.plays == 4
    assert any(m['kind'] == 'ready' for m in messages) and messages[-1]['playing']


def test_still_retry_keeps_image_and_owner_can_pause(shared_tk, tmp_path):
    from PIL import Image
    from app.gui_video_player import NativeVideoPreview
    picture = tmp_path / 'last-still.jpg'
    Image.new('RGB', (64, 36)).save(picture)
    still = {**CAMERA, 'mode': 'still', 'refresh_seconds': 30, 'alternatives': []}
    card = NativeVideoPreview(shared_tk, card_gui(), camera=still)
    try:
        card.after_cancel(card._launch_timer)
        card._launch_timer = None
        card._publish({'kind': 'still', 'path': str(picture)})
        card._drain()
        last_image = card._photo
        card.after_cancel(card._still_timer)
        card._still_timer = None
        card._publish({'kind': 'still_retry', 'error': 'temporary disconnect'})
        card._drain()
        assert card._playing and card._photo is last_image and card._still_timer is not None
        assert 'retrying' in card.detail.get()
        card.pause()
        assert not card._playing and card._still_timer is None
    finally:
        card.destroy()
