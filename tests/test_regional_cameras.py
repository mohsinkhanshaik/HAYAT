"""Regional publishers, renewing signed media, and honest source coverage."""
import threading
import time
import json
import queue
import subprocess
import sys

import pytest

from app.intelligence import live
from app.intelligence.camera_chat import camera_request, rank_cameras, playback_mode
from app.intelligence.catalog import feed_types, missing_types, provider_profile
from app.intelligence.feeds import BY_KEY
from app.intelligence.providers import normalize_extended
from app.video_player_worker import BroadcastRefresh
from app.config import PROJECT_ROOT


def test_indian_views_are_located_and_do_not_include_saudi_or_nasa_broadcasts():
    rows, rejected = normalize_extended(BY_KEY['livestreams'], live.livestream_records())
    assert rejected == 0
    indian = rank_cameras(list(rows.values()), 'India', {'livestreams': BY_KEY['livestreams'].title})
    assert {r['id'] for r in indian} == {f'livestreams:{key}' for key in ('arunachala', 'shirdi', 'somnath', 'siddhivinayak')}
    assert all(r['latitude'] and r['longitude'] and r['details']['state'] for r in indian)
    assert all(r['url'] == r['details']['page_url'] and r['url'] for r in indian)
    assert playback_mode(rows['livestreams:arunachala']['details']) == 'still'
    assert rows['livestreams:arunachala']['details']['image_url'].endswith('/live/video.jpg')
    assert camera_request('play somnath live')[1]['event_id'] == 'livestreams:somnath'
    assert camera_request('shirdi live')[1]['event_id'] == 'livestreams:shirdi'


def test_publisher_embeds_are_read_as_data_and_unrelated_iframes_are_ignored():
    page = next(r['page'] for r in live.LIVESTREAMS if r['id'] == 'somnath')
    body = b'<iframe src="https://evil.example/embed/D8CrYeUfOPQ"></iframe><iframe src="https://www.youtube.com/embed/D8CrYeUfOPQ"></iframe>'
    assert live.publisher_stream(body, page) == 'https://www.youtube.com/watch?v=D8CrYeUfOPQ'
    with pytest.raises(ValueError):
        live.publisher_stream(b'<iframe src="http://www.youtube.com/embed/D8CrYeUfOPQ"></iframe>', page)
    with pytest.raises(ValueError):
        live.publisher_stream(body, 'https://evil.example/')


def test_shirdi_json_signed_playlist_is_freshly_extracted_without_old_fallback():
    page = next(r['page'] for r in live.LIVESTREAMS if r['id'] == 'shirdi')
    body = br'{"url":"https:\/\/cam.pc.cdn.bitgravity.com\/cam\/live\/feed008\/Saibaba\/temple\/feed\/playlist.m3u8?e=123\u0026h=new"}'
    assert live.publisher_stream(body, page).endswith('?e=123&h=new')
    with pytest.raises(ValueError):
        live.publisher_stream(body.replace(b'cam.pc.cdn.bitgravity.com', b'evil.example'), page)


def test_signed_broadcast_resolution_never_blocks_controls_and_renews_the_url():
    release = threading.Event()
    calls = []
    def resolver(page):
        calls.append(page)
        assert release.wait(2)
        return 'https://manifest.googlevideo.com/fresh-' + str(len(calls)) + '.m3u8'
    page = live.LIVESTREAMS[0]['page']
    refresh = BroadcastRefresh(page, resolver)
    assert refresh.poll() is None
    assert refresh.poll() is None
    assert refresh.pending
    release.set()
    deadline = time.monotonic() + 2
    while refresh.results.empty() and time.monotonic() < deadline:
        time.sleep(.01)
    first = refresh.poll()
    assert first[0].endswith('fresh-1.m3u8') and len(calls) == 1
    assert refresh.poll() is None
    deadline = time.monotonic() + 2
    while refresh.results.empty() and time.monotonic() < deadline:
        time.sleep(.01)
    assert refresh.poll()[0].endswith('fresh-2.m3u8')
    with pytest.raises(ValueError):
        BroadcastRefresh('https://www.youtube.com/watch?v=unregistered')


def test_native_pause_resume_and_close_work_during_a_blocked_broadcast_resolver():
    script = '''
from types import SimpleNamespace as N
import time
from app import video_player_worker as w
from app.intelligence import live
live.resolve_livestream = lambda *args, **kwargs: (time.sleep(20), '')[1]
class Player:
    def __init__(self): self.muted=True; self.volume=60
    def get_state(self): return 'Stopped'
    def audio_set_volume(self, value): self.volume=value
    def audio_set_mute(self, value): self.muted=value
    def audio_get_volume(self): return 0
    def audio_get_mute(self): return False
    def get_time(self): return 0
    def video_get_size(self, index): return (0,0)
    def set_pause(self, value): pass
    def stop(self): pass
w.load_vlc = lambda: N(Instance=lambda *args: N(media_player_new=Player),
                       State=N(Playing='Playing',Ended='Ended',Error='Error',Stopped='Stopped'))
w.main()
'''
    process = subprocess.Popen([sys.executable, '-c', script], cwd=PROJECT_ROOT,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8')
    events = queue.Queue()
    def read():
        for line in process.stdout:
            events.put(json.loads(line))
    threading.Thread(target=read, daemon=True).start()
    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()
    def until(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            event = events.get(timeout=2)
            if predicate(event): return
        raise AssertionError('Native player controls did not respond')
    try:
        send({'live_page': live.LIVESTREAMS[0]['page'], 'headless': True})
        until(lambda event: event.get('reconnecting'))
        send({'action': 'pause'})
        until(lambda event: event.get('paused'))
        send({'action': 'volume', 'value': 41})
        until(lambda event: event.get('paused') and event.get('volume') == 41 and event.get('muted'))
        send({'action': 'live_edge'})
        until(lambda event: event.get('reconnecting') and not event.get('paused'))
        send({'action': 'close'})
        process.wait(timeout=3)
        assert process.returncode == 0, process.stderr.read()
    finally:
        if process.poll() is None: process.kill()
        process.communicate(timeout=3)


def test_catalog_reports_real_news_connections_and_community_provenance():
    assert feed_types('news_bbc_world') == ['NEWS', 'EVENT']
    assert feed_types('gdelt_security') == ['NEWS', 'EVENT']
    assert 'NEWS' not in missing_types([{'types': ['NEWS']}])
    assert 'WEATHER' in missing_types([{'types': ['NEWS']}])
    assert not provider_profile('adsb', live.PROVIDERS['adsb'], {})['official']
    assert provider_profile('met', live.PROVIDERS['met'], {})['official']


def test_advertisement_embeds_are_ignored_and_the_live_program_gets_separate_audio(monkeypatch):
    from types import SimpleNamespace
    import yt_dlp
    from app.research import fetcher, safety
    page = next(r['page'] for r in live.LIVESTREAMS if r['id'] == 'somnath')
    body = b'<div class="ad-container"><iframe src="https://www.youtube.com/embed/Advert00001"></iframe></div><iframe src="https://www.youtube.com/embed/Recorded001"></iframe><iframe src="https://www.youtube.com/embed/LiveNow0001"></iframe>'
    assert live.publisher_streams(body, page) == ['https://www.youtube.com/watch?v=Recorded001',
                                                'https://www.youtube.com/watch?v=LiveNow0001']
    class Fetcher:
        def __init__(self, *args, **kwargs): pass
        def fetch(self, *args, **kwargs): return SimpleNamespace(content=body)
        def close(self): pass
    calls = []
    class Client:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, address, download=False):
            calls.append(address)
            return {'is_live': address.endswith('LiveNow0001'), 'formats': [
                {'protocol': 'm3u8', 'vcodec': 'h264', 'acodec': 'none', 'height': 720,
                 'url': 'https://manifest.googlevideo.com/live-video.m3u8'},
                {'protocol': 'm3u8', 'vcodec': 'none', 'acodec': None, 'abr': 128,
                 'url': 'https://manifest.googlevideo.com/live-audio.m3u8'}]}
    monkeypatch.setattr(fetcher, 'PublicFetcher', Fetcher)
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Client)
    monkeypatch.setattr(safety, 'validate_public_url', lambda url: url)
    media = live.resolve_livestream(page, include_audio=True)
    assert media == {'video_url': 'https://manifest.googlevideo.com/live-video.m3u8',
                     'audio_url': 'https://manifest.googlevideo.com/live-audio.m3u8'}
    assert all('Advert' not in address for address in calls)
    assert len(calls) == 2
