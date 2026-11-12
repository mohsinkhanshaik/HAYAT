"""Playback failures must remain retryable and camera entry points agree."""
from datetime import UTC, datetime
import os

import pytest

from app.intelligence.camera_health import failing, hls_state
from tests.test_gui_intelligence import explorer, pump_until  # noqa: F401
from tests.test_intelligence_cameras import cameras  # noqa: F401
from tests.test_live_cameras_upgrade import CAMERA, card


def test_hls_tries_other_video_renditions_and_nested_masters():
    base = 'https://publisher.example/'
    master = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000\nbroken.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=200000\nnested.m3u8\n'
    pages = {base+'main.m3u8': master, base+'broken.m3u8': '<html>offline</html>',
             base+'nested.m3u8': '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=200000\nvideo.m3u8\n',
             base+'video.m3u8': '#EXTM3U\n#EXTINF:4,\nsegment.ts\n'}
    assert hls_state(lambda url: pages[url].encode(), base+'main.m3u8') == ('live', '')
    pages[base+'video.m3u8'] = '#EXTM3U\n#EXTINF:4,\n'
    assert hls_state(lambda url: pages[url].encode(), base+'video.m3u8')[0] == 'empty'
    pages[base+'nested.m3u8'] = master
    assert hls_state(lambda url: pages[url].encode(), base+'main.m3u8')[0] == 'stream_dead'


def test_hls_variant_probes_are_bounded():
    calls = []
    master = '#EXTM3U\n' + ''.join(f'#EXT-X-STREAM-INF:BANDWIDTH={i+1}\n{i}.m3u8\n' for i in range(100))
    def fetch(url):
        calls.append(url)
        return (master if url.endswith('master.m3u8') else '<html>offline</html>').encode()
    assert hls_state(fetch, 'https://publisher.example/master.m3u8')[0] == 'stream_dead'
    assert len(calls) == 4


def test_old_timezone_free_health_records_do_not_crash_camera_lists():
    assert failing({'ok': False, 'at': datetime.now(UTC).replace(tzinfo=None).isoformat()})


def test_network_with_no_checkable_media_is_not_called_working(cameras):  # noqa: F811
    from app.intelligence.camera_health import check_cameras
    result = check_cameras(cameras, feed='cameras_maryland', sample=1, streams=0, decode=False)
    assert result['checked'] == 0
    assert {row['state'] for row in result['networks']} == {'nothing to check'}


def test_explicit_retry_reprobes_recovered_stream(cameras):  # noqa: F811
    from app.intelligence.providers import camera_url
    row = next(row for row in cameras.cameras(media='stream', limit=200)['cameras']
               if row['details'].get('image_url') and row['feed'] != 'livestreams')
    url = camera_url(row['details']['video_url'], row['feed'], video=True)
    cameras.fetcher.dead_streams = {url}
    assert cameras.camera_play(event_id=row['id'], refresh=False)['mode'] == 'still'
    cameras.fetcher.dead_streams.clear()
    assert cameras.camera_play(event_id=row['id'], refresh=True)['mode'] == 'stream'


def test_retry_after_still_fallback_attempts_video_again(shared_tk):
    preview = card(shared_tk, {**CAMERA, 'has_still': True})
    try:
        preview._still_fallback = True
        preview._shown_frame = True
        preview.retry()
        assert not preview._still_fallback and not preview._shown_frame
        assert preview._retry_generation == preview._generation
    finally:
        preview.destroy()


def test_refreshing_viewer_does_not_accept_a_publisher_card_as_camera_video(shared_tk):
    from types import SimpleNamespace
    preview = card(shared_tk, {**CAMERA, 'mode': 'still'}, execute=lambda *_: SimpleNamespace(
        success=True, output={'publisher_card': True}, error=''))
    try:
        with pytest.raises(ValueError, match='no current camera picture'):
            preview._fetch_still(preview._generation)
        assert preview._updates.empty()
    finally:
        preview.destroy()


def test_map_video_and_still_list_use_native_viewer_even_with_stale_publisher_flag(explorer, monkeypatch):  # noqa: F811
    import tkinter as tk
    from tests.test_intelligence_resources import ExtendedFetcher
    window, _gui = explorer
    window.manager.fetcher = ExtendedFetcher()
    window.manager.refresh(['cameras_london', 'cameras_nyc'])
    opened = []
    def open_view(parent, gui, camera):
        child = tk.Toplevel(parent)
        child.withdraw()
        opened.append((camera, child))
        return child, None
    monkeypatch.setattr('app.gui_video_player.open_camera_window', open_view)
    window.open_camera('cameras_london:JamCams_test')
    assert opened[-1][0]['event_id'] == 'cameras_london:JamCams_test'
    still = window.manager.cameras(feed='cameras_nyc', limit=1)['cameras'][0]
    still['details']['available'] = False
    window.camera_rows[still['id']] = still
    window.camera_selection = still['id']
    window.watch_camera_video()
    assert opened[-1][0]['mode'] == 'still'
    assert not opened[0][1].winfo_exists()
    assert 'Opening' in window.feedback.get()


def test_layer_shortcuts_restore_markers_without_resetting_search(explorer):  # noqa: F811
    window, _gui = explorer
    window.query.set('Earthquake')
    window.layer_buttons['Hide all'].invoke()
    assert not window.map_categories() and not window.map_rows
    window.layer_buttons['Show all'].invoke()
    assert len(window.map_categories()) == len(window.layer_vars)
    assert window.query.get() == 'Earthquake' and window.map_rows


@pytest.mark.skipif(os.getenv('HAYAT_REPAIR_SHOTS') != '1', reason='opt-in visual review')
def test_capture_repaired_camera_and_layer_controls(explorer, request):  # noqa: F811
    from tests.test_gui_screenshots import _grab
    from tests.test_intelligence_resources import ExtendedFetcher
    from app.gui_theme import style_controls, style_hayat_buttons
    from tkinter import ttk
    window, gui = explorer
    style = ttk.Style(window)
    previous = style.theme_use()
    style.theme_use('clam')
    request.addfinalizer(lambda: style.theme_use(previous))
    window.manager.fetcher = ExtendedFetcher()
    window.manager.refresh(['cameras_london'])
    style_controls(window, window.palette)
    style_hayat_buttons(window, window.palette)
    window.geometry('1100x850+30+30')
    window.update()
    window.toggle_legend()
    window.update()
    _grab(gui, 'repair-map-layers', widget=window)
    window.notebook.select(window.tabs['cameras'])
    window.layer.set('cameras_london')
    window.load_cameras()
    pump_until(window, lambda: not window._busy and bool(window.camera_rows))
    window.camera_tree.selection_set('cameras_london:JamCams_test')
    window.update()
    window.load_camera_frame()
    pump_until(window, lambda: not window._busy and window._camera_photo is not None)
    _grab(gui, 'repair-cameras', widget=window)
    window.geometry('760x580+30+30')
    window.update()
    assert window.camera_tree.winfo_height() >= 50
    _grab(gui, 'repair-cameras-compact', widget=window)
