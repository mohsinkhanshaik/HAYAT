"""Read-only public camera proof, isolated from the owner's intelligence database.

Run with .venv/Scripts/python scripts/verify_camera_playback.py. Downloads only
publisher catalogs and preview media; decodes a frame with the installed FFmpeg.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def native_probe(media):
    """Exercise the exact isolated VLC worker used by the GUI, including pause/resume."""
    process = subprocess.Popen([sys.executable, '-m', 'app.video_player_worker'], cwd=ROOT,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    events = queue.Queue()
    def read():
        for line in process.stdout:
            try:
                events.put(json.loads(line))
            except ValueError:
                continue
    threading.Thread(target=read, daemon=True).start()
    def send(command):
        process.stdin.write(json.dumps(command) + '\n')
        process.stdin.flush()
    def until(predicate, seconds=35):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=.2)
            except queue.Empty:
                if process.poll() is not None:
                    raise ValueError(f'Native worker exited: {process.returncode}') from None
                continue
            if event.get('kind') == 'error':
                raise ValueError(event.get('error'))
            if predicate(event):
                return event
        raise ValueError('Native worker did not reach the expected playback state')
    try:
        send({'camera_url': media['video_url'], 'feed': media['feed'], 'headless': True, 'duration': 0})
        until(lambda e: e.get('kind') == 'ready')
        send({'action': 'play'})
        shown = until(lambda e: e.get('playing') and e.get('width', 0) > 0 and e.get('frames', 0) > 0)
        send({'action': 'pause'})
        until(lambda e: e.get('kind') == 'state' and not e.get('playing'), 8)
        send({'action': 'play'})
        until(lambda e: e.get('playing'), 8)
        send({'action': 'close'})
        process.wait(timeout=8)
        return {'decoded': True, 'size': [shown['width'], shown['height']], 'frames': shown['frames'],
                'pause_resume': True, 'exit_code': process.returncode}
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=8)


def embedded_probe(manager, media, folder, watch_seconds=0):
    """Exercise the visible Tk card, real registry and embedded decoder together."""
    import tkinter as tk
    from types import SimpleNamespace

    from PIL import ImageGrab

    from app.gui import HayatGUI
    from app.gui_video_player import NativeVideoPreview
    from app.intelligence.tools import register_intelligence_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, folder))
    register_intelligence_tools(registry, manager)
    gui = SimpleNamespace(**{name: getattr(HayatGUI, name) for name in ('PANEL_2', 'LINE', 'WHITE', 'BLACK', 'MUTED')},
                          runtime=SimpleNamespace(registry=registry, intelligence=manager), _open_source=lambda _url: None)
    root = tk.Tk()
    root.title('HAYAT · isolated camera playback verification')
    root.geometry('1000x740+30+30')
    root.configure(bg=gui.BLACK)
    preview = NativeVideoPreview(root, gui, camera=media, max_width=950)
    preview.pack(fill='both', expand=True, padx=12, pady=12)

    def until(predicate, seconds=45):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for _ in range(30):
                if not root.tk.dooneevent(tk._tkinter.DONT_WAIT):
                    break
            if predicate():
                return
            time.sleep(.03)
        raise ValueError('Embedded card did not reach the requested state: ' + preview.detail.get())

    try:
        until(lambda: preview._ready and (preview._shown_frame or preview._photo is not None)
              and (sys.platform != 'win32' or preview._photo is not None))
        mode = preview.camera['mode']
        size = list(preview._video_size) if mode != 'still' else None
        observed = time.monotonic()
        last_picture, last_change = preview._pictures_shown, observed
        while time.monotonic() - observed < watch_seconds:
            for _ in range(30):
                if not root.tk.dooneevent(tk._tkinter.DONT_WAIT):
                    break
            if preview.camera['mode'] != mode:
                raise ValueError('The requested video fell back during sustained playback.')
            if preview._pictures_shown != last_picture:
                last_picture, last_change = preview._pictures_shown, time.monotonic()
            if mode == 'stream' and time.monotonic() - last_change > 15:
                raise ValueError('No new visible video picture arrived for fifteen seconds.')
            time.sleep(.03)
        observed_seconds = round(time.monotonic() - observed, 1)
        screenshot = folder / (media['feed'] + '-embedded.png')
        root.update()
        if sys.platform == 'win32':
            ImageGrab.grab(window=int(root.wm_frame(), 16)).save(screenshot)
        preview.pause()
        until(lambda: not preview._playing, 8)
        previous = preview._still_updates if mode == 'still' else preview._pictures_shown
        preview.request_play()
        until(lambda: preview._playing and (
              preview._still_updates > previous if mode == 'still' else
              preview._pictures_shown > previous and preview.badge.cget('text') in {'● LIVE', 'CLIP'}
              and preview._video_size[0] > 0), 20)
        badge, detail = preview.badge.cget('text'), preview.detail.get()
        process = preview._process
        from app.process import _descendants
        children = _descendants(process.pid) if process is not None else []
        preview.stop()
        if process is not None:
            process.wait(timeout=8)
        if children:
            import psutil
            _gone, alive = psutil.wait_procs(children, timeout=8)
            if alive:
                raise ValueError('A decoder child is still running after the player was stopped.')
        return {'decoded': True, 'mode': mode, 'size': size, 'badge': badge, 'detail': detail,
                'tk_picture': preview._photo is not None,
                'pictures_shown': preview._pictures_shown, 'still_updates': preview._still_updates,
                'observed_seconds': observed_seconds,
                'decoder_children_stopped': len(children),
                'pause_resume': True, 'closed': preview._closed,
                'screenshot': str(screenshot) if screenshot.is_file() else None}
    finally:
        preview.close()
        root.destroy()


def main():
    from app.intelligence.manager import IntelligenceManager
    from app.intelligence.camera_eyes import ffmpeg_frame
    from app.research.fetcher import PublicFetcher
    from app.video_player_worker import native_player_available, load_vlc
    from PIL import Image

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feeds', nargs='+', default=['cameras_london', 'cameras_delaware', 'cameras_norway'])
    parser.add_argument('--output', default='output/camera-playback-verification')
    parser.add_argument('--gui', action='store_true', help='Also operate the real embedded camera card and capture its window.')
    parser.add_argument('--watch-seconds', type=float, default=0, help='Observe the embedded picture for up to 60 seconds before pause/resume.')
    args = parser.parse_args()
    if not 0 <= args.watch_seconds <= 60:
        parser.error('--watch-seconds must be between 0 and 60.')
    folder = (ROOT / args.output).resolve()
    folder.relative_to(ROOT)
    folder.mkdir(parents=True, exist_ok=True)
    fetcher = PublicFetcher('HAYAT-CameraPlaybackVerification/1.0', timeout=15)
    manager = IntelligenceManager(folder / 'intelligence.db', fetcher, None, extended=True, enabled=False)
    report = {'at': datetime.now(UTC).isoformat(), 'native_player': native_player_available(), 'networks': []}
    if report['native_player']:
        report['vlc'] = load_vlc().libvlc_get_version().decode()
    try:
        for feed in args.feeds:
            item = {'feed': feed, 'attempts': []}
            report['networks'].append(item)
            try:
                manager.refresh([feed])
                rows = manager.cameras(feed=feed, limit=30, sort='media')['cameras']
                item['loaded'] = len(rows)
                for row in rows[:3]:
                    attempt = {'id': row['id']}
                    item['attempts'].append(attempt)
                    try:
                        media = manager.camera_play(event_id=row['id'], refresh=False)
                        attempt['mode'] = media['mode']
                        if media['mode'] == 'still':
                            frame = manager.camera_frame(row['id'], fresh=True)
                            if frame.get('publisher_card'):
                                raise ValueError('Publisher unavailable card')
                            target = Path(frame['path'])
                        else:
                            target = folder / (feed + '-decoded.jpg')
                            ffmpeg_frame(media['video_url'], target, timeout=35)
                        with Image.open(target) as image:
                            image.load()
                            attempt['size'] = list(image.size)
                        attempt['decoded'] = True
                        if media['mode'] != 'still' and report['native_player']:
                            attempt['native'] = native_probe(media)
                        if args.gui:
                            attempt['embedded'] = embedded_probe(manager, media, folder, args.watch_seconds)
                            if attempt['embedded']['mode'] != media['mode']:
                                raise ValueError('The requested video fell back to a different media mode; video playback was not established.')
                        break
                    except Exception as exc:
                        attempt['error'] = f'{type(exc).__name__}: {exc}'[:350]
            except Exception as exc:
                item['error'] = f'{type(exc).__name__}: {exc}'[:350]
            print(json.dumps(item), flush=True)
    finally:
        manager.close()
        fetcher.close()
        (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if all(any(a.get('decoded') and not a.get('error') for a in n['attempts']) for n in report['networks']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
