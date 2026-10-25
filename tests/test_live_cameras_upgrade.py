"""Live cameras that play instead of spinning (2026-10-10, measured on real publishers).

Kansas stream addresses expired five minutes after each hourly catalog read; the
Cameras tab sent Makkah and Madinah to the browser; "mekkah", "madina cam" and
"mekkah and madina cam" were not understood; a dead stream reconnected for ever.
"""
import base64
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.intelligence.camera_chat import camera_request, livestream_requests
from app.intelligence.live import LIVESTREAMS
from app.intelligence.providers import camera_url
from app.intelligence.services import livestream_alternatives, token_expired, token_expiry
from tests.test_camera_chat import CAMERA, card_gui, play
from tests.test_intelligence_cameras import cameras  # noqa: F401 -- fixture


def signed(url, seconds):
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    now = int(time.time())
    return f"{url}?token={part({'alg': 'HS256'})}.{part({'iat': now, 'exp': now + seconds})}.c2lnbmF0dXJl"


@pytest.mark.parametrize(('text', 'streams'), [
    ('play mekkah live', ['makkah']), ('mekkah cam', ['makkah']), ('madina cam', ['madinah']),
    ('mekkah and madina cam', ['makkah', 'madinah']), ('live camera of kaaba', ['makkah']), ('kabah live', ['makkah']),
    ('show me the masjid nabawi live', ['madinah']), ('watch madeena live', ['madinah']), ('mecca webcam', ['makkah']),
    ('open masjid e nabawi cam', ['madinah']), ('play al jazeera live', ['news_aljazeera']), ('play live news', ['news_aljazeera']),
    ('show me the bear cam', ['explore_nature']), ('kilauea volcano live', ['kilauea']), ('play nasa live', ['nasa_iss']),
])
def test_owner_spellings_reach_the_official_livestreams(text, streams):
    assert livestream_requests(text) == streams
    tool, arguments = camera_request(text)
    assert tool == 'intelligence_camera_play' and arguments['event_id'] == 'livestreams:' + streams[0]
    if len(streams) > 1:
        assert arguments['query'] == 'then ' + streams[1]


@pytest.mark.parametrize('text', ['makkah', 'tell me about makkah', 'what is the history of the kaaba', 'open camera',
                                  'show me the earthquakes', 'what is the volcano doing in iceland'])
def test_a_place_or_topic_alone_is_not_a_stream_request(text):
    assert livestream_requests(text) == []


def test_next_camera_follows_the_pair_and_keeps_news_with_news():
    assert livestream_alternatives('livestreams:makkah')[0]['id'] == 'livestreams:madinah'
    assert livestream_alternatives('livestreams:madinah')[0]['id'] == 'livestreams:makkah'
    assert livestream_alternatives('livestreams:somnath', 'then shirdi')[0]['id'] == 'livestreams:shirdi'
    news = [item['id'] for item in livestream_alternatives('livestreams:news_sky')]
    first_other = next(index for index, key in enumerate(news) if not key.startswith('livestreams:news_'))
    assert all(key.startswith('livestreams:news_') for key in news[:first_other]) and first_other >= 10


def test_every_official_livestream_is_a_registered_publisher_page():
    keys = [item['id'] for item in LIVESTREAMS]
    assert len(keys) == len(set(keys)) and len(keys) >= 26
    for item in LIVESTREAMS:
        assert item['page'].startswith('https://') and item['provider']
    assert next(item for item in LIVESTREAMS if item['id'] == 'nasa_iss')['page'] == 'https://www.youtube.com/@NASA/live'


def test_signed_stream_addresses_know_when_they_expire():
    fresh, stale = signed('https://kdot.example/live.m3u8', 300), signed('https://kdot.example/live.m3u8', -10)
    assert token_expiry('https://video.deldot.gov/live/x.m3u8') == '' and not token_expired('https://video.deldot.gov/x.m3u8')
    assert datetime.fromisoformat(token_expiry(fresh)) > datetime.now(UTC) + timedelta(seconds=250)
    assert not token_expired(fresh) and token_expired(stale)
    assert token_expired(signed('https://kdot.example/live.m3u8', 20))  # too close to expiry to start a player


def test_expired_addresses_are_renewed_at_most_every_four_minutes(cameras):  # noqa: F811
    refreshed = []
    cameras.refresh = lambda feeds: refreshed.append(list(feeds))
    assert cameras._renew_stream_tokens({'cameras_cars_ks'}) is True
    assert cameras._renew_stream_tokens({'cameras_cars_ks'}) is False  # throttled: one catalog request per token life
    assert refreshed == [['cameras_cars_ks']]
    assert cameras.store.states()['cameras_cars_ks']['next_attempt_at'] is None  # the hourly cooldown set aside once


def test_a_silent_stream_shows_the_same_cameras_still_instead_of_spinning(cameras):  # noqa: F811
    row = next(item for item in cameras.cameras(media='stream', limit=200)['cameras']
               if item['details'].get('image_url') and item['feed'] != 'livestreams')
    cameras.fetcher.dead_streams = {camera_url(row['details']['video_url'], row['feed'], video=True)}
    result = play(cameras, event_id=row['id'])
    assert result.success and result.output['mode'] == 'still' and result.output['video_url'] == ''
    assert 'not sending video' in result.output['notice'] and result.output['has_still']
    cameras.fetcher.dead_streams = set()
    cameras._stream_checks.clear()
    live = play(cameras, event_id=row['id'])
    assert live.output['mode'] == 'stream' and live.output['has_still']
    assert play(cameras, event_id=row['id'], still_only=True).output['mode'] == 'still'


def test_a_viewer_only_camera_says_where_to_watch_it(cameras):  # noqa: F811
    row = next(item for item in cameras.cameras(feed='cameras_maryland', limit=5)['cameras'])
    result = play(cameras, event_id=row['id'])
    assert not result.success and 'own web viewer' in result.error and row['details']['page_url'] in result.error


def test_a_channel_added_in_an_update_is_read_before_it_plays(cameras):  # noqa: F811
    refreshed = []
    original = cameras.refresh
    cameras.refresh = lambda feeds: (refreshed.append(list(feeds)), original(feeds))
    cameras.store.save('livestreams', datetime.now(UTC).isoformat(), {})  # an older list without the channel
    result = play(cameras, event_id='livestreams:news_dw', refresh=True)
    assert ['livestreams'] in refreshed
    assert result.success or 'not broadcasting' in str(result.error)


def card(shared_tk, camera, execute=None):
    from app.gui_video_player import NativeVideoPreview
    preview = NativeVideoPreview(shared_tk, card_gui(execute), camera=camera)
    preview.after_cancel(preview._launch_timer)
    preview._launch_timer = None
    return preview


def test_a_stream_that_never_shows_a_frame_falls_back_to_its_still(shared_tk):
    from app.gui_video_player import GIVE_UP_RECONNECTS
    preview = card(shared_tk, {**CAMERA, 'has_still': True})
    try:
        preview._launched_at = time.monotonic()
        preview._publish({'kind': 'state', 'seconds': 0, 'playing': False, 'reconnecting': GIVE_UP_RECONNECTS})
        preview._drain()
        assert preview._still_fallback and 'latest still image' in preview.detail.get()
        assert preview._launch_timer is not None  # relaunches as a still
    finally:
        preview.destroy()


def test_with_nothing_else_to_show_the_card_says_so_in_time(shared_tk):
    from app.gui_video_player import GIVE_UP_SECONDS
    preview = card(shared_tk, {**CAMERA, 'alternatives': []})
    try:
        preview._launched_at = time.monotonic() - GIVE_UP_SECONDS - 1  # "attempt 1" for too long
        preview._publish({'kind': 'state', 'seconds': 0, 'playing': False, 'reconnecting': 1})
        preview._drain()
        assert 'not sending video right now' in preview.detail.get() and preview.badge['text'] == 'NO VIDEO'
        assert str(preview.retry_button['state']) == 'normal'
    finally:
        preview.destroy()


def test_a_still_that_never_arrives_is_not_called_retained(shared_tk):
    preview = card(shared_tk, {**CAMERA, 'mode': 'still', 'alternatives': []})
    try:
        preview._publish({'kind': 'still_retry', 'error': 'ValueError: TxDOT returned no snapshot'})
        preview._drain()
        text = preview.detail.get()
        assert 'has no current image' in text and 'retained' not in text and preview.badge['text'] == 'NO IMAGE'
    finally:
        preview.destroy()


def test_an_expired_signed_stream_reloads_the_same_camera_first(shared_tk):
    expired = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    preview = card(shared_tk, {**CAMERA, 'expires_at': expired})
    try:
        preview._publish({'kind': 'error', 'error': '401 Unauthorized'})
        preview._drain()
        assert preview.camera['event_id'] == CAMERA['event_id'] and 'fresh one' in preview.detail.get()
        assert preview._launch_timer is not None and preview._renewed
    finally:
        preview.destroy()


def test_badges_say_live_only_while_live_video_plays(shared_tk):
    from app.gui_video_player import state_badge
    assert state_badge('stream', {'playing': True}, 'grey')[0] == '● LIVE'
    assert state_badge('stream', {'playing': False}, 'grey')[0] == 'PAUSED'
    assert state_badge('stream', {'reconnecting': 2}, 'grey')[0] == 'RECONNECTING'
    assert state_badge('clip', {'playing': True}, 'grey')[0] == 'CLIP'
    assert state_badge('clip', {'ended': True}, 'grey')[0] == 'CLIP ENDED'


def test_watch_large_moves_the_camera_to_its_own_window(shared_tk):
    preview = card(shared_tk, CAMERA, execute=lambda tool, arguments: SimpleNamespace(success=False, error='offline'))
    try:
        large = preview.watch_large()
        try:
            assert large.max_width == 1280 and large.camera['event_id'] == CAMERA['event_id']
            assert large._alternatives == CAMERA['alternatives'] and preview._closed  # one connection, not two
            assert 'larger window' in preview.detail.get()
        finally:
            large.winfo_toplevel().destroy()
    finally:
        preview.destroy()


def test_the_eye_picture_follows_its_window():
    from app.camera_view import CameraView
    view = SimpleNamespace(stream=SimpleNamespace(width=560), LARGEST_WIDTH=CameraView.LARGEST_WIDTH)
    CameraView._fit_picture(view, SimpleNamespace(width=1100, height=700))
    assert view.stream.width == 1092
    CameraView._fit_picture(view, SimpleNamespace(width=1600, height=500))
    assert view.stream.width == int(492 * 16 / 9)  # limited by height, 16:9
    CameraView._fit_picture(view, SimpleNamespace(width=3000, height=3000))
    assert view.stream.width == 1280


def test_estonia_joins_its_two_datex_documents():
    from app.intelligence.providers import tarktee_cameras
    from tests.test_intelligence_resources import ESTONIA_IMAGES, ESTONIA_LOCATIONS
    rows = tarktee_cameras(ESTONIA_LOCATIONS, ESTONIA_IMAGES)
    assert rows == [{'id': 'ee-1', 'name': 'Kolgakula', 'latitude': 59.52601, 'longitude': 25.69951,
                     'image': 'https://tarktee.transpordiamet.ee/images/987/987_202610110806.jpg',
                     'time': '2026-10-11T08:06:39.000+03:00'}]
    # An image on any other host is not joined.
    foreign = ESTONIA_IMAGES.replace(b'https://tarktee.transpordiamet.ee/images/', b'https://elsewhere.example/images/')
    assert tarktee_cameras(ESTONIA_LOCATIONS, foreign) == []


def test_norway_stills_and_live_video_come_only_from_vegvesen(cameras):  # noqa: F811
    row = cameras.cameras(feed='cameras_norway')['cameras'][0]
    details = row['details']
    assert details['image_url'] == 'https://kamera.atlas.vegvesen.no/api/images/3000061_1'
    assert details['video_url'] == 'https://kamera.vegvesen.no/public/3000061_1/manifest.m3u8'
    assert row['title'] == 'E18 · Oslo sentrum' and details['country'] == 'Norway'
    assert camera_url('https://kamera.atlas.vegvesen.no/api/images/../../etc', 'cameras_norway') == ''
    assert camera_url('https://evil.example/api/images/1', 'cameras_norway') == ''
    played = play(cameras, query='Oslo')
    assert played.success and played.output['mode'] == 'stream'


def test_tallinn_ships_with_hayat_and_needs_no_fetch(cameras):  # noqa: F811
    from app.intelligence.live import static_records
    records = static_records('cameras_tallinn')
    assert len(records) == 255 and all(item['image'].startswith('https://ristmikud.tallinn.ee/last/') for item in records)
    rows = cameras.cameras(feed='cameras_tallinn', limit=300)['cameras']
    assert len(rows) == 255 and rows[0]['details']['city'] == 'Tallinn'
    assert 'gods-eye-view' in rows[0]['details']['catalog_credit']


def test_sources_left_out_on_purpose_stay_out():
    from app.intelligence.feeds import BY_KEY
    # Warendorf is plain HTTP; the "Shinjuku" pack is demo video, not cameras.
    # NSW now has a verified official public catalog and an ordinary JPEG path;
    # other NSW links can still return HTML, which the decoder must reject.
    assert not any(key in BY_KEY for key in ('cameras_warendorf', 'cameras_shinjuku'))
    assert BY_KEY['cameras_nsw'].url == 'https://data.livetraffic.com/cameras/traffic-cam.json'
    assert not BY_KEY['cameras_nsw'].key_env
    assert BY_KEY['cameras_511_ontario'].key_env


# ------------------------------------------------------------------ smooth playback (2026-10-11)
def frame_rate(shown):
    """A FrameRate over a fake libVLC whose displayed-picture counter follows `shown` (a list we change)."""
    from app.video_player_worker import FrameRate
    vlc = SimpleNamespace(MediaStats=lambda: SimpleNamespace(displayed_pictures=0, lost_pictures=0))
    media = SimpleNamespace(get_stats=lambda stats: (setattr(stats, 'displayed_pictures', shown[0]),
                                                     setattr(stats, 'lost_pictures', shown[1])))
    rate = FrameRate(vlc)
    rate.reset(media)
    return rate


def test_frame_rate_counts_pictures_really_shown_and_spots_a_frozen_picture():
    from app.video_player_worker import FROZEN_SECONDS
    counters = [0, 0]
    rate = frame_rate(counters)
    rate.update(100.0, True)
    counters[:] = [30, 2]
    rate.update(101.0, True)
    assert rate.fps == 30 and rate.lost_per_second == 2 and rate.total == 30 and not rate.frozen(101.0)
    rate.update(101.5, True)  # less than a second later: not measured again
    assert rate.fps == 30
    for second in range(2, FROZEN_SECONDS + 4):
        rate.update(100.0 + second, True)  # the counter no longer moves
    assert rate.fps == 0 and rate.frozen(100.0 + FROZEN_SECONDS + 3)
    counters[0] = 45
    rate.update(100.0 + FROZEN_SECONDS + 4, True)
    assert not rate.frozen(100.0 + FROZEN_SECONDS + 4) and rate.fps == 15
    for second in range(FROZEN_SECONDS + 5, 2 * FROZEN_SECONDS + 8):
        rate.update(100.0 + second, False)  # paused: a still picture is expected
    assert not rate.frozen(100.0 + 2 * FROZEN_SECONDS + 8)


def test_a_libvlc_without_statistics_is_never_called_frozen():
    from app.video_player_worker import FrameRate
    rate = FrameRate(SimpleNamespace())  # no MediaStats at all
    rate.reset(SimpleNamespace())
    for second in range(60):
        rate.update(float(second), True)
    assert not rate.frozen(60.0) and rate.fps == 0


def test_the_worker_decodes_on_the_gpu_and_plays_separate_sound_on_its_own():
    from pathlib import Path
    import app.video_player_worker as worker
    source = Path(worker.__file__).read_text(encoding='utf-8')
    assert '--avcodec-hw=dxva2' in source and '--avcodec-hw=none' not in source
    assert ':input-slave=' not in source  # attached sound froze the picture at 5-16 fps
    assert ':network-caching=1500' in source and "'4000'" not in source


def test_the_card_shows_the_real_frame_rate_and_a_frozen_picture(shared_tk):
    preview = card(shared_tk, CAMERA)
    try:
        preview._ready = True
        preview._launched_at = time.monotonic()
        base = {'kind': 'state', 'seconds': 3, 'playing': True, 'width': 640, 'height': 360, 'audio_tracks': 0}
        preview._publish({**base, 'fps': 14.6, 'frames': 140})
        preview._drain()
        assert preview.detail.get().endswith('640 × 360 · 15 fps') and preview._shown_frame
        preview._publish({**base, 'fps': 0.0, 'frames': 140})
        preview._drain()
        assert preview.detail.get().endswith('picture not changing')
    finally:
        preview.close()
        preview.destroy()


def test_playing_without_a_single_picture_is_not_a_shown_frame(shared_tk):
    from app.gui_video_player import GIVE_UP_SECONDS
    preview = card(shared_tk, {**CAMERA, 'alternatives': []})
    try:
        preview._launched_at = time.monotonic() - GIVE_UP_SECONDS - 1
        # Alabama on 2026-10-11: libVLC said "Playing" at 720 × 480 and showed no picture at all.
        preview._publish({'kind': 'state', 'seconds': 0, 'playing': True, 'width': 720, 'height': 480, 'fps': 0.0, 'frames': 0})
        preview._drain()
        assert not preview._shown_frame and 'not sending video right now' in preview.detail.get()
    finally:
        preview.close()
        preview.destroy()


def test_unchanged_widgets_are_not_reconfigured(shared_tk):
    import tkinter as tk
    from app.gui_video_player import changed
    button = tk.Button(shared_tk, text='Play')
    calls = []
    original = button.configure
    button.configure = lambda **options: (calls.append(options), original(**options))
    changed(button, text='Play')
    changed(button, text='Pause', state='normal')
    assert calls == [{'text': 'Pause'}]
    button.destroy()
