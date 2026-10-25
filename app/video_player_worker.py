"""Isolated libVLC player: admitted local files and registered public camera video."""
from __future__ import annotations
import json
import importlib.util
import os
import queue
import sys
import threading
import time
from pathlib import Path
from ctypes.util import find_library
from urllib.parse import urlsplit
from app.config import PROJECT_ROOT

def reconnect_delay(attempt):
    """Keep a selected live camera reconnecting without hammering its publisher."""
    return min(60, 2 ** min(max(0, attempt - 1), 6))


class BroadcastRefresh:
    """Resolve signed broadcast URLs off the control loop, one request at a time."""

    def __init__(self, page, resolver=None):
        from app.intelligence.live import broadcast_source, resolve_livestream
        broadcast_source(page)
        self.page = page
        self.resolver = resolver or (lambda address: resolve_livestream(address, include_audio=True))
        self.pending = False
        self.results = queue.Queue(maxsize=1)

    def poll(self):
        try:
            value = self.results.get_nowait()
        except queue.Empty:
            if not self.pending:
                self.pending = True
                def resolve():
                    try:
                        self.results.put((self.resolver(self.page), ''))
                    except Exception as exc:
                        self.results.put(('', type(exc).__name__))
                threading.Thread(target=resolve, name='hayat-broadcast-resolve', daemon=True).start()
            return None
        self.pending = False
        return value


def vlc_directory():
    local = PROJECT_ROOT / 'runtime' / 'vlc'
    if (local / 'libvlc.dll').is_file():
        return local
    if sys.platform == 'win32':
        installed = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'VideoLAN' / 'VLC'
        if (installed / 'libvlc.dll').is_file():
            return installed
    return None


def native_player_available():
    return importlib.util.find_spec('vlc') is not None and sys.platform in ('win32', 'linux') and (
        vlc_directory() is not None if sys.platform == 'win32' else bool(find_library('vlc')))


def load_vlc():
    directory = vlc_directory()
    if directory:
        os.environ['PYTHON_VLC_LIB_PATH'] = str(directory / 'libvlc.dll')
        os.environ['PYTHON_VLC_MODULE_PATH'] = str(directory)
        os.environ['VLC_PLUGIN_PATH'] = str(directory / 'plugins')
        if hasattr(os, 'add_dll_directory'):
            # Keep the DLL search handle alive for the lifetime of the worker.
            global _dll_directory
            _dll_directory = os.add_dll_directory(str(directory))
    import vlc
    return vlc


#: A live picture that stops changing for this long is frozen, even while libVLC says "Playing".
FROZEN_SECONDS = 12


class FrameRate:
    """Pictures actually shown per second, from libVLC's own counters, and how long the picture has not changed."""

    def __init__(self, vlc):
        self.vlc, self.media, self.at, self.shown, self.lost = vlc, None, 0.0, 0, 0
        self.fps, self.lost_per_second, self.total, self.unchanged_since = 0.0, 0.0, 0, None

    def reset(self, media):
        self.media, self.at, self.shown, self.lost = media, 0.0, 0, 0
        self.fps, self.lost_per_second, self.unchanged_since = 0.0, 0.0, None

    def update(self, moment, playing):
        if self.media is None or moment - self.at < 1:
            return
        try:
            stats = self.vlc.MediaStats()
            self.media.get_stats(stats)
        except Exception:  # an older libVLC without statistics: never call a picture frozen
            self.media = None
            return
        if self.at:
            span = moment - self.at
            shown = max(0, stats.displayed_pictures - self.shown)
            self.fps, self.lost_per_second = shown / span, max(0, stats.lost_pictures - self.lost) / span
            if shown or not playing:
                self.unchanged_since = None
            elif self.unchanged_since is None:
                self.unchanged_since = self.at
        self.at, self.shown, self.lost, self.total = moment, stats.displayed_pictures, stats.lost_pictures, stats.displayed_pictures

    def frozen(self, moment):
        return self.unchanged_since is not None and moment - self.unchanged_since > FROZEN_SECONDS


def emit(data):
    print(json.dumps(data), flush=True)


def main():
    request = json.loads(sys.stdin.readline(64000))
    broadcast = BroadcastRefresh(request['live_page']) if request.get('live_page') else None
    remote = bool(request.get('camera_url') or broadcast)
    url = ''
    audio_url = ''
    own = request.get('feed') == 'my_cameras'
    if remote and not broadcast and own:
        # The owner's camera: exactly an address they registered (my_cameras.py), nothing else.
        from app.intelligence.my_cameras import registered
        url = registered(request['camera_url'])
        if not url:
            raise ValueError('That address is not one of your registered cameras')
    elif remote and not broadcast:
        from app.intelligence.providers import camera_url
        url = camera_url(request['camera_url'], request.get('feed', ''), video=True)
        if not url:
            raise ValueError('Choose a registered public publisher video URL')
    elif not remote:
        path = Path(request['path']).resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in {'.mp4', '.mkv', '.webm', '.mov', '.m4v', '.avi'}:
            raise ValueError('Choose a local video file')
    vlc = load_vlc()
    # Hardware decoding (DXVA2 suits the Direct3D 9 output) leaves the CPU to HAYAT's model; libVLC falls
    # back to software by itself when the GPU cannot decode a stream. Measured 2026-10-11: same frame rate,
    # no lost pictures, on live 720p and six traffic-camera networks.
    flags = ['--quiet', '--no-video-title-show', '--no-media-library', '--avcodec-hw=dxva2']
    if request.get('headless'):
        flags += ['--vout=dummy', '--aout=dummy']
    elif sys.platform == 'win32':
        flags += ['--vout=direct3d9']
    instance = vlc.Instance(*flags)
    player = instance.media_player_new()
    desired_volume, desired_muted, audio_configured = 60, True, False
    mute_requested = False
    # A live HLS camera never ends. When a publisher drops one segment download
    # (Delaware serves 9-second segments), libVLC reports Ended and the card used
    # to say "Clip ended" seventeen seconds in. Live streams reopen at the live edge.
    live = bool(broadcast) or remote and (own or urlsplit(url).path.lower().endswith('.m3u8'))
    frames = FrameRate(vlc)
    # YouTube sends some broadcasts (Makkah, Madinah) as separate picture and sound. Attached to the picture
    # as an input-slave, the sound froze it: 5 to 16 fps with long stops, against a steady 30 fps alone
    # (measured 2026-10-11). The sound now has its own player, started only when the owner unmutes.
    sound = None
    def sound_stop():
        if sound is not None:
            sound.stop()
    def sound_sync():
        nonlocal sound
        if not audio_url or desired_muted or paused:
            sound_stop()
            return
        if sound is None:
            sound = instance.media_player_new()
        if sound.get_state() not in (vlc.State.Playing, vlc.State.Opening, vlc.State.Buffering):
            audio = instance.media_new_location(audio_url)
            audio.add_option(':network-caching=1500')
            sound.set_media(audio)
            sound.play()
        sound.audio_set_volume(desired_volume)
        sound.audio_set_mute(False)
    def open_media():
        nonlocal audio_configured
        media = instance.media_new_location(url) if remote else instance.media_new_path(str(path))
        if remote:
            # 1.5 s instead of 4 s: the picture is closer to live, and the same cameras stalled no more.
            media.add_option(':network-caching=1500')
            media.add_option(':http-reconnect')
            if own and (request.get('username') or request.get('password')):
                prefix = 'rtsp' if url.startswith('rtsp') else 'http'
                media.add_option(f":{prefix}-user={request.get('username', '')}")
                media.add_option(f":{prefix}-pwd={request.get('password', '')}")
            if own and url.startswith('rtsp'):
                media.add_option(':rtsp-tcp')  # home cameras over Wi-Fi drop UDP packets
        sound_stop()  # a reconnect may bring a new sound address
        player.set_media(media)
        frames.reset(media)
        audio_configured = False
    if not broadcast:
        open_media()
    if not request.get('headless'):
        handle = int(request['window_id'])
        if sys.platform == 'win32':
            player.set_hwnd(handle)
        elif sys.platform.startswith('linux'):
            player.set_xwindow(handle)
        else:
            raise ValueError('Embedded VLC is supported on Windows and Linux; use the frame preview on this platform')
        player.video_set_mouse_input(False)
        player.video_set_key_input(False)
    commands = queue.Queue(maxsize=100)
    def read():
        for line in sys.stdin:
            try:
                commands.put(json.loads(line[:64000]), timeout=1)
            except (ValueError, queue.Full):
                continue
        commands.put({'action': 'close'})
    threading.Thread(target=read, daemon=True).start()
    duration = float(request.get('duration', 0))
    player.audio_set_volume(desired_volume)
    player.audio_set_mute(desired_muted)
    if not broadcast and player.play() == -1 and not live:
        raise ValueError('libVLC could not open this video')
    started = time.monotonic()
    initialized = False
    reconnects, stable_since, stalled_since, retry_at = 0, None, None, None
    if broadcast:
        reconnects, retry_at = 1, started
    paused = False
    try:
        while True:
            # Controls remain responsive during initial connection and backoff.
            for _ in range(20):
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                action = command.get('action')
                if action == 'close':
                    sound_stop()
                    return
                if action == 'play':
                    paused = False
                    if retry_at is not None:
                        retry_at = time.monotonic()
                    elif player.get_state() in (vlc.State.Ended, vlc.State.Stopped):
                        player.stop()
                        player.play()
                        started = time.monotonic()
                    else:
                        player.set_pause(0)
                    sound_sync()
                elif action == 'pause':
                    paused = True
                    player.set_pause(1)
                    sound_stop()
                elif action == 'live_edge' and live:
                    paused = False
                    reconnects = max(1, reconnects)
                    retry_at = time.monotonic()
                    player.stop()
                    sound_stop()
                elif action == 'seek' and not remote:
                    player.set_time(round(max(0, min(duration, float(command['value']))) * 1000))
                elif action == 'volume':
                    desired_volume = max(0, min(100, int(command['value'])))
                    player.audio_set_volume(desired_volume)
                    if sound is not None:
                        sound.audio_set_volume(desired_volume)
                elif action == 'mute':
                    mute_requested = True
                    desired_muted = bool(command['value'])
                    player.audio_set_mute(desired_muted)
                    sound_sync()
                elif action == 'rate':
                    rate = float(command['value'])
                    if .5 <= rate <= 2:
                        player.set_rate(rate)
            state = player.get_state()
            moment = time.monotonic()
            if state == vlc.State.Playing and not audio_configured and player.audio_get_track_count() > 1:
                player.audio_set_volume(desired_volume)
                player.audio_set_mute(desired_muted)
                audio_configured = True
            frames.update(moment, state == vlc.State.Playing and not paused)
            if live and frames.frozen(moment):
                # "Playing", but the picture has not changed: a publisher stream that froze.
                frames.unchanged_since = None
                stalled_since = moment - 31
            elif state == vlc.State.Playing and player.video_get_size(0)[0] > 0:
                stalled_since = None
                stable_since = stable_since if stable_since is not None else moment
                if moment - stable_since > 120:
                    reconnects = 0
            else:
                stable_since = None
                stalled_since = stalled_since if stalled_since is not None else moment
            if live and not paused and retry_at is None and (state in (vlc.State.Ended, vlc.State.Error)
                    or stalled_since is not None and moment - stalled_since > 30
                    or not initialized and moment - started > 30):
                reconnects += 1
                retry_at = moment + reconnect_delay(reconnects)
                player.stop()
                sound_stop()
            if retry_at is not None:
                emit({'kind': 'state', 'seconds': max(0, player.get_time() / 1000), 'width': 0, 'height': 0,
                      'playing': False, 'ended': False, 'reconnecting': 0 if paused else reconnects,
                      'paused': paused,
                      'retry_after_seconds': max(0, round(retry_at - moment)), 'muted': desired_muted,
                      'volume': desired_volume})
                if not paused and moment >= retry_at:
                    if broadcast:
                        resolved = broadcast.poll()
                        if resolved is None:
                            time.sleep(.1)
                            continue
                        media_urls, error = resolved
                        if error:
                            reconnects += 1
                            retry_at = moment + reconnect_delay(reconnects)
                            emit({'kind': 'notice', 'message': 'Official broadcast unavailable (' + error + '); retrying.'})
                            continue
                        if isinstance(media_urls, dict):
                            url, audio_url = media_urls['video_url'], media_urls.get('audio_url', '')
                        else:
                            url, audio_url = media_urls, ''
                    open_media()
                    player.play()
                    player.audio_set_mute(desired_muted)
                    player.audio_set_volume(desired_volume)
                    sound_sync()
                    started, stalled_since, retry_at = moment, None, None
                time.sleep(.1)
                continue
            if state == vlc.State.Error and not live:
                raise ValueError('libVLC could not decode this video')
            if not initialized:
                if (player.get_state() == vlc.State.Playing and time.monotonic() - started > .25
                        and (not remote or player.video_get_size(0)[0] > 0)):
                    if not remote:
                        player.set_pause(1)
                        player.set_time(0)
                        desired_muted = desired_muted if mute_requested else False
                        player.audio_set_mute(desired_muted)
                    initialized = True
                    emit({'kind': 'ready', 'engine': 'libVLC', 'audio': bool(audio_url) or player.audio_get_track_count() > 1})
                elif not live and time.monotonic() - started > (30 if remote else 15):
                    raise ValueError('Video player startup timed out')
            else:
                emit({'kind': 'state', 'seconds': max(0, player.get_time() / 1000),
                      'width': player.video_get_size(0)[0], 'height': player.video_get_size(0)[1],
                      'playing': player.get_state() == vlc.State.Playing,
                      'ended': player.get_state() == vlc.State.Ended, 'rate': player.get_rate(),
                      'volume': desired_volume if audio_url else player.audio_get_volume(),
                      'muted': desired_muted if audio_url else bool(player.audio_get_mute()),
                      # "2" = a sound track and "off", as libVLC counts them, so the card offers Unmute.
                      'audio_tracks': 2 if audio_url else player.audio_get_track_count(), 'fps': round(frames.fps, 1),
                      'frames': frames.total, 'lost_per_second': round(frames.lost_per_second, 1)})
            time.sleep(.1)
    finally:
        # The disposable process owns all VLC allocations. Some Windows
        # plugins race native teardown after a paused seek. The OS stops
        # playback and reclaims all allocations when this worker exits.
        pass


if __name__ == '__main__':
    status = 0
    try:
        main()
    except Exception as exc:
        emit({'kind': 'error', 'error': str(exc)[:300]})
        status = 1
    sys.stdout.flush()
    os._exit(status)
