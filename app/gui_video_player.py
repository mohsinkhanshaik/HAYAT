"""A native video player card with audio; decoding stays in a child process.

The same card plays a registered public camera in chat: a live HLS stream or
publisher clip through the libVLC worker, or a refreshing still image for the
networks that only publish stills. Camera cards can step through the other
matching cameras with Next camera.
"""
from __future__ import annotations
import json
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from app.config import PROJECT_ROOT
from app.gui_resources import StringVar
from app.video import timestamp
from app.video_player_worker import native_player_available

#: A startup failure moves on to the next matching camera this many times before stopping.
AUTO_ADVANCE = 2
#: A stream that has not shown one frame after this many reconnects is given up on (about 10-15 s):
#: the camera's still image if it has one, else the next camera. It used to reconnect for ever.
GIVE_UP_RECONNECTS = 3
#: Nor is one still opening after this long (a Missouri stream sat at "attempt 1" for 45 s).
GIVE_UP_SECONDS = 20


def create_video_preview(parent, gui, path):
    if native_player_available():
        return NativeVideoPreview(parent, gui, path)
    from app.gui_video import VideoPreview
    return VideoPreview(parent, gui, path)


LIVE_COLOUR, CLIP_COLOUR, STILL_COLOUR, WAIT_COLOUR = '#F05D5E', '#4FC3F7', '#93A3B5', '#F2A541'
MODE_BADGES = {'stream': ('● LIVE', LIVE_COLOUR), 'clip': ('CLIP', CLIP_COLOUR), 'still': ('STILL', STILL_COLOUR)}


def changed(widget, **options):
    """Configure only the options whose value differs: a no-op configure still costs a redraw."""
    different = {key: value for key, value in options.items() if str(widget.cget(key)) != str(value)}
    if different:
        widget.configure(**different)


def state_badge(mode, event, muted):
    """The badge for a player state: LIVE only while live video is actually playing."""
    if event.get('reconnecting'):
        return 'RECONNECTING', WAIT_COLOUR
    if event.get('ended'):
        return 'CLIP ENDED', muted
    if not event.get('playing'):
        return 'PAUSED', muted
    return MODE_BADGES.get(mode, ('', muted))


def blend_badge(colour, background):
    """A dark tint of the badge colour behind its word."""
    try:
        from app.gui_intelligence_parts import blend
        return blend(colour, background, .78)
    except (ValueError, TypeError):
        return background


def open_camera_window(parent, gui, camera, max_width=1280):
    """A public camera in its own resizable window, played in HAYAT (not the browser)."""
    from app.gui_windows import place_over
    window = tk.Toplevel(parent)
    window.title('HAYAT · ' + str(camera.get('title') or 'Public camera'))
    window.configure(bg=gui.BLACK)
    place_over(window, parent, (1180, 840))
    preview = NativeVideoPreview(window, gui, camera=camera, max_width=max_width)
    preview.pack(fill='both', expand=True, padx=10, pady=10)

    def close():
        preview.close()
        window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)
    window.bind('<Escape>', lambda _event: close())
    return window, preview


def signed_address_expired(camera):
    """True when the camera's stream address carried a token that has now run out."""
    from datetime import UTC, datetime
    expiry = (camera or {}).get('expires_at')
    return bool(expiry) and datetime.fromisoformat(expiry) <= datetime.now(UTC)


class NativeVideoPreview(tk.Frame):
    def __init__(self, parent, gui, path=None, *, camera=None, max_width=520):
        super().__init__(parent, bg=gui.PANEL_2, padx=10, pady=10,
                         highlightthickness=1, highlightbackground=gui.LINE)
        self.gui, self.path = gui, Path(path) if path else None
        #: The card in chat is 520 px wide; a camera window shows up to 1280 px.
        self.max_width = max_width
        self.camera = dict(camera) if camera else None
        self._closed = False
        self._process = None
        self._playing = False
        self._dragging = False
        self._muted = bool(camera)
        self._ready = False
        self._autoplay = False
        self._position = self._duration = 0.0
        self._commands = queue.Queue(maxsize=50)
        self._updates = queue.Queue(maxsize=20)
        self._states = queue.Queue(maxsize=1)
        self._timer = None
        # Each launch has a generation; a replaced player's late messages are ignored.
        self._generation = 0
        self._alternatives = list((camera or {}).get('alternatives') or [])
        self._auto_advances = 0
        self._renewed = False
        self._shown_frame = False
        self._still_fallback = False
        self._still_timer = None
        self._still_label = None
        self._photo = None
        heading = tk.Frame(self, bg=gui.PANEL_2)
        heading.pack(fill='x')
        # LIVE / CLIP / STILL in one word, so a still or an old clip is never taken for live video.
        self.badge = tk.Label(heading, text='', bg=gui.PANEL_2, fg=gui.MUTED, font=('Segoe UI Semibold', 8), padx=6, pady=1)
        if camera:
            self.badge.pack(side='right')
        self.title_label = tk.Label(heading, text='▶ ' + (camera['title'] if camera else self.path.name), bg=gui.PANEL_2, fg=gui.WHITE,
                                    anchor='w', font=('Segoe UI Semibold', 11))
        self.title_label.pack(side='left', fill='x', expand=True)
        self.screen = tk.Frame(self, bg=gui.BLACK, width=min(520, max_width), height=292)
        self.screen.pack(pady=(8, 6))
        self.screen.pack_propagate(False)
        self._resize_binding = parent.bind('<Configure>', self._fit_screen, add='+')
        self._fit_screen()
        self.detail = StringVar(self, value='Connecting to public publisher…' if camera else 'Preparing player with audio…')
        tk.Label(self, textvariable=self.detail, bg=gui.PANEL_2, fg=gui.MUTED).pack(fill='x')
        controls = ttk.Frame(self)
        controls.pack(fill='x', pady=5)
        self.play_button = ttk.Button(controls, text='Play', command=self.toggle, state='disabled', width=9)
        self.play_button.pack(side='left')
        ttk.Button(controls, text='↶ 10s', command=lambda: self.seek(max(0, self._position - 10)), width=7).pack(side='left', padx=3)
        ttk.Button(controls, text='10s ↷', command=lambda: self.seek(min(self._duration, self._position + 10)), width=7).pack(side='left')
        self.mute_button = ttk.Button(controls, text='Unmute' if camera else 'Mute', command=self.mute, width=7)
        self.mute_button.pack(side='left', padx=3)
        self.volume = ttk.Scale(controls, from_=0, to=100, length=85)
        self.volume.set(60)
        self.volume.pack(side='left')
        self.volume.bind('<ButtonRelease-1>', lambda _event: self._send('volume', round(self.volume.get())))
        self.volume.bind('<KeyRelease>', lambda _event: self._send('volume', round(self.volume.get())))
        self.speed = StringVar(self, value='1×')
        speed = ttk.Combobox(controls, textvariable=self.speed, values=['0.5×', '0.75×', '1×', '1.25×', '1.5×', '2×'],
                             width=5, state='readonly')
        speed.pack(side='left', padx=5)
        speed.bind('<<ComboboxSelected>>', lambda _event: self._send('rate', float(self.speed.get().rstrip('×'))))
        self.slider = ttk.Scale(self, from_=0, to=1)
        self.slider.pack(fill='x')
        self.slider.bind('<ButtonPress-1>', lambda _event: setattr(self, '_dragging', True))
        self.slider.bind('<ButtonRelease-1>', self._finish_seek)
        self.slider.bind('<KeyRelease>', lambda _event: self.seek(self.slider.get()))
        if camera:
            from app.gui_intelligence_parts import FlowRow
            # Wraps instead of clipping "Open publisher" off the right edge.
            actions = FlowRow(self, gui.PANEL_2, gap=5, height=34)
        else:
            actions = ttk.Frame(self)
        actions.pack(fill='x', pady=(6, 0))
        if camera:
            for widget in controls.winfo_children():
                if widget not in (self.play_button, self.mute_button, self.volume):
                    widget.pack_forget()
            self.slider.pack_forget()

            def button(text, command, **options):
                return actions.add(ttk.Button(actions, text=text, command=command, style='Compact.Hayat.TButton', **options))
            button('Stop stream', self.stop)
            self.retry_button = button('Retry connection', self.retry, state='disabled')
            self.next_button = button('Next camera ▸', self.next_camera, state='normal' if self._alternatives else 'disabled')
            if camera.get('mode') == 'stream':
                button('Go live', lambda: self._send('live_edge'))
            if max_width <= 520:
                self.large_button = button('Watch large ⤢', self.watch_large)
            button('Open publisher', lambda: gui._open_source(self.camera['source_url']))
            self.info = StringVar(self, value=self._camera_info())
            tk.Label(self, textvariable=self.info, bg=gui.PANEL_2, fg=gui.MUTED, anchor='w', justify='left',
                     wraplength=520).pack(fill='x', pady=(5, 0))
        else:
            ttk.Button(actions, text='Analyze / make clips', command=lambda: gui._open_video_studio(str(self.path))).pack(side='left')
            ttk.Button(actions, text='Edit / result link', command=lambda: gui._open_video_editor(
                str(self.path), self._position, self._duration)).pack(side='left', padx=5)
            ttk.Button(actions, text='Open file', command=lambda: gui._open_file(self.path)).pack(side='right')
        self.bind('<Destroy>', lambda event: self.close() if event.widget is self else None, add='+')
        self._launch_timer = self.after(60, self._launch)
        self._drain()

    def _set_badge(self, text, colour):
        if self.camera and str(self.badge.cget('text')) != text:
            self.badge.configure(text=text, fg=colour, bg=blend_badge(colour, self.gui.PANEL_2) if text else self.gui.PANEL_2)

    def watch_large(self):
        """The same camera in its own larger window; this card lets go, so the publisher serves one connection."""
        if self._closed or not self.camera:
            return None
        camera = {**self.camera, 'alternatives': list(self._alternatives)}
        self.close()
        self.play_button.configure(state='disabled', text='Moved')
        self.detail.set('Playing in the larger window · close it to end the connection.')
        self._set_badge('', self.gui.MUTED)
        return open_camera_window(self.winfo_toplevel(), self.gui, camera)[1]

    def _camera_info(self):
        camera = self.camera
        lines = [' · '.join(part for part in (camera.get('media_label'), camera.get('provider'), camera.get('place')) if part)]
        if camera.get('notice'):
            lines.append(camera['notice'])
        lines.append('Still images refresh at the publisher\'s pace · no recording' if camera.get('mode') == 'still'
                     else 'Publisher video may be delayed · no recording')
        return '\n'.join(lines)

    def _fit_screen(self, _event=None):
        if self._closed:
            return
        width = max(240, min(self.max_width, self.master.winfo_width() - 64))
        height = max(90, min(round(width * 9 / 16), self.master.winfo_height() - 190, round(self.max_width * 9 / 16)))
        self.screen.configure(width=width, height=height)

    def _publish(self, message, generation=None):
        if self._closed:
            return
        if generation is not None:
            message = {**message, 'generation': generation}
        updates = self._states if message.get('kind') == 'state' else self._updates
        try:
            updates.put_nowait(message)
        except queue.Full:
            if message.get('kind') == 'state':
                try:
                    updates.get_nowait()
                except queue.Empty:
                    pass
                updates.put_nowait(message)
            else:
                updates.put(message, timeout=1)

    def _launch(self):
        self._launch_timer = None
        self._launched_at = time.monotonic()
        window_id = self.screen.winfo_id()
        generation = self._generation
        def publish(message):
            self._publish(message, generation)
        def current():
            return not self._closed and generation == self._generation
        def work():
            process = None
            initialized = threading.Event()
            try:
                tool = (self.camera.get('tool') or 'intelligence_camera_play') if self.camera else 'video_preview'
                if tool == 'my_camera_play':  # the owner's own camera: no still, no alternatives
                    arguments = {'event_id': self.camera['event_id']}
                else:
                    arguments = {'event_id': self.camera['event_id'], 'live_only': False, 'refresh': False,
                                 **({'still_only': True} if self._still_fallback else {})} if self.camera else {'path': str(self.path)}
                result = self.gui.runtime.registry.execute(tool, arguments)
                if not result.success:
                    raise ValueError(result.error)
                if self.camera:
                    publish({'kind': 'camera', 'camera': result.output})
                    if result.output.get('mode') == 'still':
                        try:
                            self._fetch_still(generation)
                        except Exception as exc:
                            publish({'kind': 'still_retry', 'error': str(exc)[:300]})
                        return
                    if not native_player_available():
                        raise ValueError('Camera video needs libVLC. On Windows run python scripts/setup_video_player.py and restart HAYAT.')
                metadata = {'duration_seconds': 0, 'audio': False} if self.camera else result.output['metadata']
                publish({'kind': 'metadata', 'metadata': metadata})
                if not current():
                    return
                process = subprocess.Popen([sys.executable, '-m', 'app.video_player_worker'], cwd=str(PROJECT_ROOT),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if not current():
                    process.kill()
                    return
                self._process = process
                def watchdog():
                    if not initialized.wait(35) and current() and process.poll() is None:
                        publish({'kind': 'error', 'error': 'Player startup timed out.'})
                        process.kill()
                threading.Thread(target=watchdog, name='hayat-video-startup', daemon=True).start()
                request = {'window_id': window_id, 'duration': metadata['duration_seconds']}
                request.update({'camera_url': result.output['video_url'], 'feed': result.output['feed'],
                                'live_page': result.output.get('live_page', '')} if self.camera else {'path': result.output['path']})
                if self.camera and result.output.get('login'):
                    # The owner's camera login: from the credential store straight to the player's private input,
                    # never into the address, a tool result or the chat.
                    username, password = self.gui.runtime.intelligence.my_cameras.login(self.camera['event_id'])
                    request.update(username=username, password=password)
                process.stdin.write(json.dumps(request) + '\n')
                process.stdin.flush()
                def write():
                    while current() and process.poll() is None:
                        try:
                            command = self._commands.get(timeout=.2)
                            process.stdin.write(json.dumps(command) + '\n')
                            process.stdin.flush()
                        except queue.Empty:
                            continue
                        except (OSError, ValueError):
                            return
                threading.Thread(target=write, name='hayat-video-controls', daemon=True).start()
                error_seen = False
                for line in process.stdout:
                    message = json.loads(line[:64000])
                    error_seen = error_seen or message.get('kind') == 'error'
                    if message.get('kind') in {'ready', 'error'} or message.get('reconnecting') or message.get('paused'):
                        initialized.set()
                    publish(message)
                if current() and not error_seen:
                    publish({'kind': 'error', 'error': 'Video player stopped; reopen the preview to retry.'})
            except Exception as exc:
                publish({'kind': 'error', 'error': str(exc)[:300]})
            finally:
                if process is not None:
                    initialized.set()
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                    process.stdout.close()
                    process.stdin.close()
                    if self._process is process:
                        self._process = None
        threading.Thread(target=work, name='hayat-native-video', daemon=True).start()

    def _fetch_still(self, generation):
        """The publisher's latest still, through the frame tool and its cooldowns (worker thread)."""
        frame = self.gui.runtime.registry.execute('intelligence_camera_frame', {'event_id': self.camera['event_id']})
        if not frame.success:
            raise ValueError(frame.error)
        self._publish({'kind': 'still', 'path': frame.output['path'], 'fetched_at': frame.output.get('fetched_at', ''),
                       'cached': bool(frame.output.get('cached'))}, generation)

    def _refresh_still(self):
        self._still_timer = None
        if self._closed or not self._playing:
            return
        generation = self._generation
        def work():
            try:
                self._fetch_still(generation)
            except Exception as exc:
                self._publish({'kind': 'still_retry', 'error': str(exc)[:300]}, generation)
        threading.Thread(target=work, name='hayat-camera-still', daemon=True).start()

    def _show_still(self, event):
        self._set_badge('STILL', STILL_COLOUR)
        from PIL import Image, ImageTk
        with Image.open(event['path']) as picture:
            size = picture.size  # The publisher's size; the card scales it to fit.
            picture.thumbnail((520, 292))
            self._photo = ImageTk.PhotoImage(picture.convert('RGB'))
        if self._still_label is None:
            self._still_label = tk.Label(self.screen, bg=self.gui.BLACK, bd=0)
            self._still_label.place(relx=.5, rely=.5, anchor='center')
        self._still_label.configure(image=self._photo)
        if not self._ready:
            self._playing = True
        self._ready = True
        self._auto_advances = 0
        self.retry_button.configure(state='disabled')
        self.mute_button.configure(state='disabled')
        self.play_button.configure(state='normal', text='Pause' if self._playing else 'Resume')
        fetched = str(event.get('fetched_at') or '')[11:19]
        refresh = int(self.camera.get('refresh_seconds') or 60)
        self.detail.set(f'Still image · {size[0]} × {size[1]} · fetched {fetched} UTC'
                        + (' (publisher cooldown)' if event.get('cached') else '') + f' · refreshes every {refresh} s')
        if self._still_timer is not None:
            self.after_cancel(self._still_timer)
        self._still_timer = self.after(refresh * 1000, self._refresh_still) if self._playing else None

    def _send(self, action, value=None):
        if self._closed:
            return
        try:
            self._commands.put_nowait({'action': action, 'value': value})
        except queue.Full:
            self.detail.set('Player is busy; wait a moment and retry.')

    def _still_mode(self):
        return bool(self.camera and self.camera.get('mode') == 'still')

    def toggle(self):
        if not self._ready:
            if self.camera and not self._still_mode():
                self.control('play' if getattr(self, '_connecting_paused', False) else 'pause')
            return
        if self._still_mode():
            self._set_still_refresh(not self._playing)
        else:
            self._send('pause' if self._playing else 'play')

    def _set_still_refresh(self, on):
        self._playing = on
        self.play_button.configure(text='Pause' if on else 'Resume')
        if self._still_timer is not None:
            self.after_cancel(self._still_timer)
            self._still_timer = None
        if on:
            self._refresh_still()
        else:
            self.detail.set('Still refresh paused · press Resume for the latest image')

    def request_play(self):
        if self._ready:
            self._send('play')
        else:
            self._autoplay = True

    def pause(self):
        if not self._ready:
            if self.camera and not self._still_mode():
                self.control('pause')
            return
        if self._still_mode():
            self._set_still_refresh(False)
        else:
            self._send('pause')

    def seek(self, position):
        if self._ready:
            self._send('seek', max(0, min(self._duration, float(position))))

    def _finish_seek(self, _event):
        self._dragging = False
        self.seek(self.slider.get())

    def mute(self):
        self._muted = not self._muted
        self._send('mute', self._muted)
        self.mute_button.configure(text='Unmute' if self._muted else 'Mute')

    def control(self, action):
        """A chat command for this camera card; returns what changed, or raises why it can't."""
        if action == 'stop':
            self.stop()
            return {}
        if action == 'next':
            chosen = self.next_camera()
            if chosen is None:
                raise ValueError('There is no other matching camera; ask for cameras in another place.')
            return {'event_id': chosen['id'], 'title': chosen.get('title', '')}
        if not self._ready:
            if action in ('play', 'pause') and not self._still_mode():
                self._connecting_paused = action == 'pause'
                self._autoplay = action == 'play'
                self._send(action)
                return {'connecting': True, 'paused': self._connecting_paused}
            raise ValueError('Camera is still connecting; try again when its player is ready.')
        if action in ('mute', 'unmute'):
            if self._still_mode():
                raise ValueError('This camera shows still images; it has no sound.')
            if self._muted != (action == 'mute'):
                self.mute()
        elif self._still_mode():
            self._set_still_refresh(action == 'play')
        else:
            self._send(action)
        return {}

    def _drain(self):
        if self._closed:
            return
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        for _ in range(20):
            try:
                event = self._updates.get_nowait()
            except queue.Empty:
                try:
                    event = self._states.get_nowait()
                except queue.Empty:
                    break
            if event.get('generation', self._generation) != self._generation:
                continue  # From a player this card already replaced.
            kind = event.get('kind')
            if kind == 'camera':
                self.camera.update({key: value for key, value in event['camera'].items() if key != 'alternatives'})
                self.title_label.configure(text='▶ ' + self.camera['title'])
                self.info.set(self._camera_info())
            elif kind == 'still':
                self._show_still(event)
            elif kind == 'still_retry':
                if self._still_label is None:
                    # No picture at all yet: not "retrying; last image retained" over a black screen.
                    reason = event['error'].split(': ', 1)[-1]
                    if self._alternatives and self._auto_advances < AUTO_ADVANCE:
                        self._auto_advances += 1
                        failed = self.camera['title']
                        self.next_camera()
                        self.detail.set(f'{failed} has no current image ({reason[:80]}); trying {self.camera["title"]}…')
                        continue
                    self._ready = self._playing = False
                    self.retry_button.configure(state='normal')
                    self.play_button.configure(state='disabled', text='Play')
                    self._set_badge('NO IMAGE', self.gui.MUTED)
                    self.detail.set(f"{self.camera['title']} has no current image from the publisher ({reason[:100]}). "
                                    'Retry later, or ask for another camera.')
                    continue
                if not self._ready:
                    self._ready = self._playing = True
                    self.play_button.configure(state='normal', text='Pause')
                self.detail.set('Snapshot interrupted · retrying; last image retained. ' + event['error'])
                if self._playing and self._still_timer is None:
                    self._still_timer = self.after(max(30, int(self.camera.get('refresh_seconds') or 60)) * 1000,
                                                  self._refresh_still)
            elif kind == 'metadata':
                self._duration = float(event['metadata']['duration_seconds'])
                self._has_audio = bool(event['metadata'].get('audio'))
                self.slider.configure(to=self._duration)
            elif kind == 'ready':
                self._ready = True
                if self.camera:
                    self._auto_advances = 0
                    self._renewed = False
                    self._set_badge(*MODE_BADGES.get(self.camera.get('mode'), ('', self.gui.MUTED)))
                    self.retry_button.configure(state='disabled')
                    self.mute_button.configure(state='normal' if event.get('audio') else 'disabled')
                self.play_button.configure(state='normal')
                if self._autoplay:
                    self._autoplay = False
                    self._send('play')
            elif kind == 'state':
                self._video_size = (event.get('width', 0), event.get('height', 0))
                self._position = min(self._duration, float(event['seconds']))
                self._playing = bool(event['playing'])
                self._muted = bool(event.get('muted', False))
                # Ten updates a second: widgets change only when their value does, so the chat
                # around a playing video is not re-laid out continuously.
                if self.camera and self._ready and 'audio_tracks' in event:
                    changed(self.mute_button, state='normal' if event['audio_tracks'] > 1 else 'disabled')
                changed(self.mute_button, text='Unmute' if self._muted else 'Mute')
                if event.get('volume', -1) >= 0 and abs(self.volume.get() - event['volume']) > .5:
                    self.volume.set(event['volume'])
                ended = bool(self.camera and event.get('ended'))
                changed(self.play_button, text='Pause' if self._playing else ('Replay' if ended else 'Play'))
                if not self._dragging and abs(float(self.slider.get()) - self._position) > .05:
                    self.slider.set(self._position)
                if self.camera:
                    badge = state_badge(self.camera.get('mode'), event, self.gui.MUTED)
                    if badge != getattr(self, '_badge_shown', None):
                        self._badge_shown = badge
                        self._set_badge(*badge)
                    # A picture counts once frames are really shown; a frozen stream says "Playing" with none.
                    self._shown_frame = self._shown_frame or bool(event.get('playing') and event.get('width')
                                                                  and event.get('frames', 1))
                    waited = time.monotonic() - getattr(self, '_launched_at', time.monotonic())
                    if not self._shown_frame and self.camera.get('mode') != 'still' and (
                            int(event.get('reconnecting') or 0) >= GIVE_UP_RECONNECTS or waited > GIVE_UP_SECONDS):
                        self._give_up_stream()
                        continue
                if event.get('reconnecting'):
                    self.play_button.configure(state='normal', text='Pause')
                    self.detail.set(f"Reconnecting to the live stream · attempt {event['reconnecting']}"
                                    + f" · retry in {event.get('retry_after_seconds', 0)} s")
                elif self.camera and not self._ready and event.get('paused'):
                    self.play_button.configure(state='normal', text='Resume')
                    self.detail.set('Connection paused · press Resume to reconnect')
                elif ended:
                    self.detail.set('Clip ended · press Replay for the publisher\'s latest clip · ' + self.camera['media_label'])
                elif self.camera:
                    state = 'Playing' if self._playing else 'Paused / buffering'
                    rate = event.get('fps')
                    speed = '' if rate is None or not self._playing else (
                        ' · picture not changing' if rate == 0 and event.get('frames') else f' · {rate:.0f} fps')
                    text = (state + ' · ' + self.camera['media_label']
                            + f" · {event.get('width', 0)} × {event.get('height', 0)}" + speed)
                    if self.detail.get() != text:
                        self.detail.set(text)
                else:
                    self.detail.set(('Playing · ' if self._playing else 'Paused / ready · ')
                                    + f'{timestamp(self._position)} / {timestamp(self._duration)} · '
                                    + ('Video + audio' if getattr(self, '_has_audio', False) else 'Video · no audio track'))
            elif kind == 'error':
                self._ready = self._playing = False
                if self.camera and signed_address_expired(self.camera) and not self._renewed:
                    # The publisher's signed address ran out (Kansas: five minutes); the same
                    # camera with a fresh address, not a different camera.
                    self._renewed = True
                    self.retry()
                    self.detail.set('The stream address expired · fetching a fresh one from the publisher…')
                    continue
                if self.camera and self._alternatives and self._auto_advances < AUTO_ADVANCE:
                    # A publisher that stopped answering is skipped, as a person would switch channels.
                    self._auto_advances += 1
                    failed = self.camera['title']
                    self.next_camera()
                    self.detail.set(f'{failed} did not play ({event["error"][:80]}); trying {self.camera["title"]}…')
                    continue
                if self.camera:
                    self.retry_button.configure(state='normal')
                self.play_button.configure(state='disabled', text='Play')
                self.detail.set('Player unavailable: ' + event['error'])
        self._timer = self.after(100, self._drain)

    def _give_up_stream(self):
        """A live stream that never showed a frame: its still image, else the next camera, else a plain reason."""
        title = self.camera['title']
        if self.camera.get('has_still') and not self._still_fallback:
            self._still_fallback = True
            self._replace_player()
            self.detail.set(f'{title}: the live stream sent no video, so showing its latest still image…')
            self._launch_timer = self.after(60, self._launch)
        elif self._alternatives and self._auto_advances < AUTO_ADVANCE:
            self._auto_advances += 1
            self.next_camera()
            self.detail.set(f'{title} sent no video; trying {self.camera["title"]}…')
        else:
            self._replace_player()
            self.retry_button.configure(state='normal')
            self.play_button.configure(state='disabled', text='Play')
            self._set_badge('NO VIDEO', self.gui.MUTED)
            self.detail.set(f'{title} is not sending video right now. Retry later, or ask for another camera.')

    def _replace_player(self):
        """End the current player or still refresh so the card can start again."""
        self._generation += 1
        self._ready = self._playing = False
        if self._process is not None and self._process.poll() is None:
            self._process.kill()
        if self._still_timer is not None:
            self.after_cancel(self._still_timer)
            self._still_timer = None
        if self._still_label is not None:
            self._still_label.destroy()
            self._still_label = self._photo = None
        while not self._commands.empty():
            self._commands.get_nowait()

    def retry(self):
        if self._closed or self._ready or self._launch_timer is not None:
            return
        self._replace_player()
        self.retry_button.configure(state='disabled')
        self.detail.set('Reconnecting to public publisher…')
        self._launch_timer = self.after(60, self._launch)

    def next_camera(self):
        """Switch this card to the next matching camera; the current one rejoins the end of the list."""
        if self._closed or not self._alternatives:
            return None
        chosen = self._alternatives.pop(0)
        self._alternatives.append({'id': self.camera['event_id'], 'title': self.camera['title']})
        self._replace_player()
        if self._launch_timer is not None:
            self.after_cancel(self._launch_timer)
        self.camera.update(event_id=chosen['id'], title=chosen.get('title') or chosen['id'], notice='', has_still=False)
        self._shown_frame = self._still_fallback = False
        self.title_label.configure(text='▶ ' + self.camera['title'])
        self.retry_button.configure(state='disabled')
        self.play_button.configure(state='disabled', text='Play')
        self.detail.set('Switching to ' + self.camera['title'] + '…')
        self._launch_timer = self.after(60, self._launch)
        return chosen

    def stop(self):
        self.close()
        self._set_badge('STOPPED', self.gui.MUTED)
        self._ready = self._playing = False
        self.play_button.configure(state='disabled', text='Stopped')
        if self.camera:
            self.retry_button.configure(state='disabled')
            self.next_button.configure(state='disabled')
        self.detail.set('Stopped · connection closed. Ask HAYAT to play the camera again.')

    def close(self):
        if self._closed:
            return
        self._closed = True
        binding = getattr(self, '_resize_binding', None)
        if binding:
            self.master.unbind('<Configure>', binding)
            self._resize_binding = None
        for timer in (self._timer, self._launch_timer, self._still_timer):
            if timer is not None:
                self.after_cancel(timer)
        self._timer = self._launch_timer = self._still_timer = None
        if self._process is not None and self._process.poll() is None:
            self._process.kill()
