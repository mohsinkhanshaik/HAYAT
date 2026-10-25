"""Editable video widgets and bounded fallback playback inside chat."""
from __future__ import annotations

import io
import json
import queue
import re
import struct
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from types import SimpleNamespace

from app.gui_intelligence_parts import FlowRow
from app.gui_panels import ScrollableColumn
from app.gui_resources import StringVar
from app.video import timestamp
from app.config import PROJECT_ROOT


class VideoPreview(tk.Frame):
    def __init__(self, parent, gui, path):
        super().__init__(parent, bg=gui.PANEL_2, padx=8, pady=8,
                         highlightthickness=1, highlightbackground=gui.LINE)
        self.gui, self.path = gui, Path(path)
        self._closed = False
        self._process = None
        self._playing = False
        self._generation = 0
        self._photo = None
        self._updates = queue.Queue()
        self._frames = queue.Queue(maxsize=1)
        self._after = None
        self._seek_after = None
        self._position = 0.0
        self._duration = 0.0
        self._moving_slider = False
        self._slider_dragging = False
        tk.Label(self, text=self.path.name, bg=gui.PANEL_2, fg=gui.WHITE,
                 font=('Segoe UI Semibold', 10), anchor='w').pack(fill='x')
        self.screen = tk.Label(self, text='Preparing video preview…', bg=gui.BLACK, fg=gui.MUTED)
        self.screen.pack(pady=6)
        self.detail = StringVar(self, value='Silent preview · Open with audio for sound')
        tk.Label(self, textvariable=self.detail, bg=gui.PANEL_2, fg=gui.MUTED).pack(fill='x')
        controls = tk.Frame(self, bg=gui.PANEL_2)
        controls.pack(fill='x', pady=(5, 0))
        self.play_button = ttk.Button(controls, text='Play', command=self.toggle, state='disabled')
        self.play_button.pack(side='left')
        ttk.Button(controls, text='Open with audio', command=lambda: gui._open_file(self.path)).pack(side='right')
        ttk.Button(controls, text='Edit / result link', command=lambda: gui._open_video_editor(
            str(self.path), self._position, self._duration)).pack(side='right', padx=5)
        # A Scale command also fires for programmatic updates on some Tk
        # builds. Only user input may seek; otherwise each frame restarted
        # its decoder and silently undid Pause.
        self.slider = ttk.Scale(self, from_=0, to=1)
        self.slider.pack(fill='x', pady=(5, 0))
        self.slider.bind('<ButtonPress-1>', self._begin_seek)
        self.slider.bind('<ButtonRelease-1>', self._end_seek)
        self.slider.bind('<KeyRelease>', lambda _event: self._seek(self.slider.get()))
        self.bind('<Destroy>', self._destroy, add='+')
        self._start(poster=True, admit=True)
        self._drain()

    def _publish(self, generation, kind, value):
        if self._closed or generation != self._generation:
            return
        updates = self._frames if kind == 'frame' else self._updates
        try:
            updates.put_nowait((generation, kind, value))
        except queue.Full:
            try:
                updates.get_nowait()
            except queue.Empty:
                pass
            try:
                updates.put_nowait((generation, kind, value))
            except queue.Full:
                pass

    def _start(self, poster=False, admit=True):
        self._stop()
        generation = self._generation
        position = self._position
        self._playing = not poster
        self.play_button.configure(text='Pause' if self._playing else 'Play')

        def work():
            process = None
            try:
                if admit:
                    result = self.gui.runtime.registry.execute('video_preview', {'path': str(self.path)})
                    if not result.success:
                        raise ValueError(result.error)
                    self._publish(generation, 'metadata', result.output['metadata'])
                if self._closed or generation != self._generation:
                    return
                process = subprocess.Popen([sys.executable, '-m', 'app.video_preview_worker'],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          cwd=str(PROJECT_ROOT), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                # A replaced/closed card cannot leave a decoder behind.
                if self._closed or generation != self._generation:
                    process.kill()
                    return
                self._process = process
                request = {'path': str(self.path), 'start': position, 'poster': poster}
                process.stdin.write((json.dumps(request) + '\n').encode())
                process.stdin.close()
                from PIL import Image
                while not self._closed and generation == self._generation:
                    header = process.stdout.read(12)
                    if not header:
                        break
                    if len(header) != 12:
                        raise ValueError('Incomplete video preview header')
                    size, seconds = struct.unpack('!Id', header)
                    if size > 1024*1024:
                        raise ValueError('Video preview frame exceeded its limit')
                    raw = process.stdout.read(size)
                    if len(raw) != size:
                        raise ValueError('Incomplete video preview frame')
                    with Image.open(io.BytesIO(raw)) as image:
                        self._publish(generation, 'frame', (seconds, image.copy()))
                if process.wait(timeout=3):
                    raise ValueError('Video decoder could not produce a preview')
                self._publish(generation, 'done', None)
            except Exception as exc:
                self._publish(generation, 'error', str(exc)[:250])
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3)
                    process.stdout.close()
                    if self._process is process:
                        self._process = None
        threading.Thread(target=work, name='hayat-video-preview', daemon=True).start()

    def _stop(self):
        self._generation += 1
        self._playing = False
        if self._seek_after is not None:
            self.after_cancel(self._seek_after)
            self._seek_after = None
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.kill()

    def toggle(self):
        if self._playing:
            self._stop()
            self.play_button.configure(text='Play')
        else:
            if self._duration and self._position >= self._duration - .2:
                self._position = 0
            self._start()

    def _begin_seek(self, _event):
        self._slider_dragging = True
        self._resume_drag = self._playing
        self._stop()

    def _end_seek(self, _event):
        self._slider_dragging = False
        self._seek(self.slider.get(), resume=getattr(self, '_resume_drag', False))

    def _seek(self, value, resume=None):
        if self._moving_slider:
            return
        if not self._duration:
            return
        self._resume_after_seek = self._playing if resume is None else resume
        self._stop()
        self._position = float(value)
        if self._seek_after is not None:
            self.after_cancel(self._seek_after)
        self._seek_after = self.after(200, self._seek_done)

    def _seek_done(self):
        self._seek_after = None
        if not self._closed:
            self._start(poster=not self._resume_after_seek)

    def _drain(self):
        if self._closed:
            return
        if self._after is not None:
            self.after_cancel(self._after)
            self._after = None
        from PIL import ImageTk
        for _ in range(10):
            try:
                generation, kind, value = self._updates.get_nowait()
            except queue.Empty:
                try:
                    generation, kind, value = self._frames.get_nowait()
                except queue.Empty:
                    break
            if generation != self._generation:
                continue
            if kind == 'metadata':
                self._duration = float(value['duration_seconds'])
                self.slider.configure(to=self._duration)
                self.play_button.configure(state='normal')
            elif kind == 'frame':
                self._position, image = value
                self._photo = ImageTk.PhotoImage(image, master=self)
                self.screen.configure(image=self._photo, text='')
                self._moving_slider = True
                if not self._slider_dragging:
                    self.slider.set(self._position)
                self._moving_slider = False
                self.detail.set(f'{timestamp(self._position)} / {timestamp(self._duration)} · Silent preview')
            elif kind == 'error':
                self.detail.set('Preview unavailable: ' + value)
                self.play_button.configure(state='disabled', text='Play')
                self._playing = False
            elif kind == 'done':
                self._playing = False
                self.play_button.configure(text='Play')
        self._after = self.after(80, self._drain)

    def _destroy(self, event):
        if event.widget is self:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop()
        if self._after:
            self.after_cancel(self._after)
        if self._seek_after:
            self.after_cancel(self._seek_after)
        self.screen.configure(image='')
        self._photo = None


class VideoStudio(tk.Frame):
    def __init__(self, gui, parent=None):
        super().__init__(parent if parent is not None else gui.root)
        self.gui = gui
        self._closed = False
        self.configure(bg=gui.PANEL)
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Watch, understand & create', font=('Segoe UI Semibold', 14)).pack(anchor='w', pady=(0, 8))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill='both', expand=True)
        body = ttk.Frame(self.tabs, padding=8)
        self.generation_tab = ttk.Frame(self.tabs)
        self.tabs.add(body, text='Watch & edit')
        self.tabs.add(self.generation_tab, text='Generate locally')
        ttk.Label(body, text='Public supported video links · files saved inside HAYAT’s download/videos folder',
                  wraplength=590).pack(anchor='w', pady=(5, 12))
        self.source = StringVar(self)
        self.quality = StringVar(self, value='auto')
        self.audience = StringVar(self, value='general')
        self.aspect = StringVar(self, value='vertical')
        self.count = StringVar(self, value='3')
        self.length = StringVar(self, value='40')
        self.status = StringVar(self, value='Enter a title such as Baby Shark, paste a link, or choose a local video.')
        ttk.Label(body, text='Video title, public link or local path').pack(anchor='w')
        ttk.Entry(body, textvariable=self.source).pack(fill='x', pady=5)
        actions = ttk.Frame(body)
        actions.pack(fill='x')
        ttk.Button(actions, text='Choose local video', command=self.choose).pack(side='left')
        ttk.Button(actions, text='Inspect link / qualities', command=self.inspect).pack(side='left', padx=8)
        ttk.Button(actions, text='Play in chat', command=self.preview).pack(side='left')
        fields = ttk.Frame(body)
        fields.pack(fill='x', pady=12)
        from app.video_workflow import QUALITIES
        for label, variable, choices in [('Download quality', self.quality, list(QUALITIES)),
                                         ('Clip layout', self.aspect, ['vertical', 'square', 'original']),
                                         ('Number of clips', self.count, [str(i) for i in range(1, 11)]),
                                         ('Clip seconds', self.length, ['15', '30', '40', '60', '90'])]:
            row = ttk.Frame(fields)
            row.pack(fill='x', pady=3)
            ttk.Label(row, text=label, width=20).pack(side='left')
            ttk.Combobox(row, textvariable=variable, values=choices, state='readonly', width=18).pack(side='left')
        ttk.Label(body, text='Audience / topic').pack(anchor='w')
        ttk.Entry(body, textvariable=self.audience).pack(fill='x', pady=5)
        ttk.Label(body, text='Auto downloads up to 1080p. Clips preserve the full frame by default. '
                  'Speech captions are included when local transcription succeeds. '
                  'Scores explain editorial potential; they do not predict view counts.', wraplength=590).pack(anchor='w', pady=8)
        ttk.Label(body, textvariable=self.status, wraplength=590).pack(anchor='w', pady=8)
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=5)
        self.download_button = ttk.Button(buttons, text='Download only', command=self.download)
        self.download_button.pack(side='left')
        self.make_button = ttk.Button(buttons, text='Analyze & make clips', command=self.make)
        self.make_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(buttons, text='Stop video job', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left')
        ttk.Button(buttons, text='Close', command=self.close).pack(side='right')
        self._busy = False
        self._cancel = threading.Event()
        self._build_generation()
        self.tabs.bind('<<NotebookTabChanged>>', self._tab_changed)
        self.bind('<Destroy>', self._studio_destroyed, add='+')

    def _build_generation(self):
        self._models_loading = self._models_loaded = self._generation_ready = False
        self._model_request = 0
        self._generated_path = None
        self._running_tool = None
        self._motion_choices = ['auto']
        self.video_prompt = StringVar(self)
        self.motion_module = StringVar(self, value='auto')
        self.video_quality = StringVar(self, value='ultra')
        self.video_resolution = StringVar(self, value='512')
        self.video_frames = StringVar(self, value='16')
        self.video_steps = StringVar(self, value='10')
        self.model_status = StringVar(self, value='Open this tab or press Refresh models to check installed local models.')
        self.generation_status = StringVar(self, value='Describe a short scene, choose settings, then Generate. No generation or download starts automatically.')

        self.generation_actions = FlowRow(self.generation_tab, self.gui.PANEL, gap=6)
        self.generation_actions.pack(side='bottom', fill='x', padx=8, pady=(2, 8))
        self.generate_button = self.generation_actions.add(ttk.Button(
            self.generation_actions, text='Generate clip', command=self.generate, state='disabled'))
        self.stop_generation_button = self.generation_actions.add(ttk.Button(
            self.generation_actions, text='Stop generation', command=self.cancel, state='disabled'))
        self.open_generated_button = self.generation_actions.add(ttk.Button(
            self.generation_actions, text='Open result', command=self.open_generated, state='disabled'))
        self.generation_actions.add(ttk.Button(self.generation_actions, text='Close', command=self.close))
        self.generation_status_label = ttk.Label(self.generation_tab, textvariable=self.generation_status,
                                                wraplength=500, justify='left')
        self.generation_status_label.pack(side='bottom', fill='x', padx=8, pady=6)
        self.generation_scroll = ScrollableColumn(self.generation_tab,
                                                  SimpleNamespace(BLACK=self.gui.PANEL), width=520)
        self.generation_scroll.pack(fill='both', expand=True)
        fields = ttk.Frame(self.generation_scroll.body, padding=8)
        fields.pack(fill='both', expand=True)
        self.generation_intro = ttk.Label(fields, text='Create a short clip with an installed SD 1.5 checkpoint and AnimateDiff. '
                                           'Generation runs locally; no subscription or upload.', wraplength=500, justify='left')
        self.generation_intro.pack(fill='x', pady=(0, 8))
        ttk.Label(fields, text='Describe the scene and movement').pack(anchor='w')
        self.prompt_entry = ttk.Entry(fields, textvariable=self.video_prompt)
        self.prompt_entry.pack(fill='x', pady=(4, 8))
        options = ttk.Frame(fields)
        options.pack(fill='x')
        options.columnconfigure(1, weight=1)
        self.generation_fields = {}
        for row, (key, title, variable, choices) in enumerate([
            ('motion', 'Motion module', self.motion_module, ['auto']),
            ('quality', 'Finish', self.video_quality, ['auto', 'fast', 'balanced', 'quality', 'ultra']),
            ('resolution', 'Frame size', self.video_resolution, ['256', '384', '512']),
            ('frames', 'Frames', self.video_frames, [str(i) for i in range(2, 33)]),
            ('steps', 'Steps', self.video_steps, ['2', '4', '10', '20', '25', '35', '50']),
        ]):
            ttk.Label(options, text=title).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=4)
            combo = ttk.Combobox(options, textvariable=variable, values=choices, state='readonly', width=12)
            combo.grid(row=row, column=1, sticky='ew', pady=4)
            self.generation_fields[key] = combo
        self.motion_combo = self.generation_fields['motion']
        self.generation_help = ttk.Label(fields, text='Auto preserves the default motion module. Optional v3 requires explicit selection. '
                                        '16 frames is the trained context. The quick default is 10 steps; reference examples use 20–25. '
                                        'A larger frame or step budget takes longer.', wraplength=500, justify='left')
        self.generation_help.pack(fill='x', pady=8)
        self.model_status_label = ttk.Label(fields, textvariable=self.model_status, wraplength=500, justify='left')
        self.model_status_label.pack(fill='x', pady=(0, 6))
        self.refresh_models_button = ttk.Button(fields, text='Refresh models', command=self.refresh_models)
        self.refresh_models_button.pack(anchor='w', pady=(0, 6))
        self.generation_scroll.adopt(self.generation_scroll.body)
        self.generation_tab.bind('<Configure>', self._generation_fit, add='+')

    def _generation_fit(self, event):
        if event.widget is self.generation_tab:
            width = max(100, event.width - 40)
            for label in (self.generation_intro, self.generation_help, self.model_status_label, self.generation_status_label):
                label.configure(wraplength=width)

    def _tab_changed(self, _event=None):
        if (not self._closed and self.tabs.select() == str(self.generation_tab)
                and not self._models_loaded and not self._models_loading):
            self.refresh_models()

    def refresh_models(self):
        if self._closed or self._models_loading or self._busy:
            return
        self._models_loading = True
        self._model_request += 1
        request = self._model_request
        self.model_status.set('Checking installed models. No generation or download is started.')
        self._generation_buttons()

        def work():
            try:
                result = self.gui.runtime.registry.execute('image_generation_status', {})
            except Exception as exc:
                result = SimpleNamespace(success=False, error=str(exc), output=None)
            if not self._closed:
                self.gui._post(self._models_complete, result, request)

        threading.Thread(target=work, name='hayat-video-model-status', daemon=True).start()

    def _models_complete(self, result, request):
        if self._closed or request != self._model_request:
            return
        self._models_loading = False
        output = result.output if result.success and isinstance(result.output, dict) else {}
        video = output.get('video') if isinstance(output.get('video'), dict) else {}
        modules = video.get('motion_modules', [])
        self._motion_choices = ['auto'] + list(dict.fromkeys(
            module for module in modules if isinstance(module, str) and module and module != 'auto'
        )) if isinstance(modules, list) else ['auto']
        self.motion_combo.configure(values=self._motion_choices)
        if self.motion_module.get() not in self._motion_choices:
            self.motion_module.set('auto')
        self._models_loaded = bool(video)
        self._generation_ready = video.get('available') is True and len(self._motion_choices) > 1
        if self._generation_ready:
            self.model_status.set(f"Checkpoint: {video.get('model', 'unknown')}\n"
                                  f"Auto uses: {video.get('motion_module', 'unknown')}. Optional modules require selection.")
        else:
            reason = ('no installed motion modules were reported' if video.get('available') is True
                      else str(video.get('reason') or result.error or 'local video status was not returned'))
            message = 'Generation unavailable: ' + reason
            self.model_status.set(message)
            self.generation_status.set(message)
        self._generation_buttons()

    def _generation_buttons(self):
        if not hasattr(self, 'generate_button') or self._closed:
            return
        idle = not self._busy and not self._models_loading
        self.generate_button.configure(state='normal' if idle and self._generation_ready else 'disabled')
        self.refresh_models_button.configure(state='normal' if idle else 'disabled')
        self.stop_generation_button.configure(state='normal' if self._busy and self._running_tool == 'generate_video' else 'disabled')
        self.open_generated_button.configure(state='normal' if idle and self._generated_path else 'disabled')
        self.prompt_entry.configure(state='normal' if idle else 'disabled')
        for combo in self.generation_fields.values():
            combo.configure(state='readonly' if idle else 'disabled')

    def generate(self):
        if self._closed or self._busy or self._models_loading:
            return
        if not self._generation_ready:
            self.generation_status.set('Check the installed models first. Generation remains unavailable until the required local components are ready.')
            return
        prompt = self.video_prompt.get().strip()
        if not prompt:
            self.generation_status.set('Describe the scene and its movement before generating a clip.')
            return
        try:
            frames, steps, size = int(self.video_frames.get()), int(self.video_steps.get()), int(self.video_resolution.get())
            if not 2 <= frames <= 32 or not 1 <= steps <= 50 or size not in {256, 384, 512}:
                raise ValueError
            module = self.motion_module.get()
            if module not in self._motion_choices:
                raise ValueError
        except ValueError:
            self.generation_status.set('Choose an installed motion module, 2–32 frames, 1–50 steps and a listed frame size.')
            return
        self._run('generate_video', {'prompt': prompt, 'motion_module': module,
                                    'quality': self.video_quality.get(), 'frames': frames, 'steps': steps,
                                    'width': size, 'height': size})

    def _generated_complete(self, result):
        output = result.output if result.success and isinstance(result.output, dict) else {}
        video = output.get('video') if isinstance(output.get('video'), dict) else {}
        path = Path(str(output.get('path') or ''))
        if not (result.success and output.get('ok') is True and video.get('decoded_verified') is True and path.is_file()):
            reason = str(output.get('error') or output.get('detail') or result.error or 'No verified video file was returned.')
            self.generation_status.set('Generation did not produce a verified clip: ' + reason[:500])
            self.gui._append('error', 'Video generation\n' + reason[:500])
            self._generation_buttons()
            return
        self._generated_path = path
        self.generation_status.set(f"Saved {output.get('width')}×{output.get('height')}, {video.get('fps')} fps, "
                                   f"{video.get('duration_seconds')}s. Motion: {video.get('motion_module')}")
        self.gui._append('assistant', 'HAYAT\nVideo ready: ' + str(path) + '\n' + str(output.get('detail', '')))
        self._generation_buttons()

    def open_generated(self):
        if self._busy or not self._generated_path:
            return
        if not self._generated_path.is_file():
            self._generated_path = None
            self.generation_status.set('The saved result file is no longer available.')
            self._generation_buttons()
            return
        self.gui._open_file(self._generated_path)

    def _studio_destroyed(self, event):
        if event.widget is self:
            self._closed = True
            self._cancel.set()

    def choose(self):
        selected = filedialog.askopenfilename(parent=self, title='Select a video',
                    filetypes=[('Video', '*.mp4 *.mkv *.mov *.webm *.m4v *.avi')])
        if selected:
            self.source.set(selected)

    def _run(self, tool, arguments, inspect=False):
        if self._busy or getattr(self, '_closed', False):
            return
        self._busy = True
        self._running_tool = tool
        self.download_button.configure(state='disabled')
        self.make_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self._cancel = threading.Event()
        self.status.set('Working in the background. Results and video previews will appear in chat.')
        if tool == 'generate_video':
            self.generation_status.set('Generating locally. The GPU render and CPU finish run in the background; Stop generation cancels this job.')
        if hasattr(self, 'generate_button'):
            self._generation_buttons()
        self.gui.show_view('chat')
        def work():
            from app.models.streaming import answer_stream
            try:
                with answer_stream(lambda *_args: None, self._cancel):
                    result = self.gui.runtime.registry.execute(tool, arguments)
            except Exception as exc:
                result = SimpleNamespace(tool=tool, success=False, error=str(exc), output=None)
            if not getattr(self, '_closed', False):
                self.gui._post(self._complete, result, inspect)
        threading.Thread(target=work, name='hayat-video-studio', daemon=True).start()

    def _complete(self, result, inspect):
        if getattr(self, '_closed', False):
            return
        if self.winfo_exists():
            self._busy = False
            self.download_button.configure(state='normal')
            self.make_button.configure(state='normal')
            self.cancel_button.configure(state='disabled')
            self.status.set(str(result.output.get('text', 'Complete'))[:1400] if result.success else str(result.error))
            if getattr(self, '_running_tool', None) == 'generate_video':
                self._generated_complete(result)
                return
            if hasattr(self, 'generate_button'):
                self._generation_buttons()
        if result.success:
            session = getattr(self.gui.runtime, 'session', None)
            if session:
                session.remember_video_result(result.tool, result.output)
            text = str(result.output.get('text', 'Video workflow complete.'))
            self.gui._append('assistant', 'HAYAT\n' + text)
            if inspect:
                return
        else:
            self.gui._append('error', 'Video workflow\n' + str(result.error))

    def inspect(self):
        self._run('inspect_video_link', {'url': self.source.get().strip()}, inspect=True)

    def preview(self):
        source = self.source.get().strip()
        if source.startswith(('http://', 'https://')):
            self._run('play_video', {'url': source, 'quality': self.quality.get()})
        elif Path(source).is_file() or re.match(r'^[A-Za-z]:[\\/]|^[/\\]', source):
            self._run('play_video', {'path': source})
        else:
            self._run('search_play_video', {'query': source, 'quality': self.quality.get()})

    def close(self):
        self._closed = True
        self._cancel.set()
        self.destroy()

    def cancel(self):
        self._cancel.set()
        self.status.set('Stopping the video job. A running transcription or model review may need to finish its current step.')
        self.cancel_button.configure(state='disabled')
        if hasattr(self, 'stop_generation_button'):
            self.stop_generation_button.configure(state='disabled')
            if self._running_tool == 'generate_video':
                self.generation_status.set('Stopping this generation and its owned video workers. Please wait for cancellation to finish.')

    def download(self):
        self._run('download_video', {'url': self.source.get().strip(), 'quality': self.quality.get()})

    def make(self):
        source = self.source.get().strip()
        self._run('make_video_clips', {'url' if source.startswith(('http://', 'https://')) else 'path': source,
                  'quality': self.quality.get(), 'audience': self.audience.get(), 'aspect': self.aspect.get(),
                  'max_clips': int(self.count.get()), 'clip_seconds': int(self.length.get())})


class VideoLinkCard(VideoStudio):
    """Compact actions next to a pasted video link, using the same job gateway."""
    def __init__(self, parent, gui, url):
        tk.Frame.__init__(self, parent, bg=gui.PANEL_2, padx=10, pady=10,
                          highlightthickness=1, highlightbackground=gui.LINE)
        self.gui = gui
        self.source = StringVar(self, value=url)
        self.quality = StringVar(self, value='auto')
        self.status = StringVar(self, value='Inspect qualities, play with audio, download, or make clips.')
        tk.Label(self, text='Video link', anchor='w', bg=gui.PANEL_2, fg=gui.WHITE,
                 font=('Segoe UI Semibold', 11)).pack(fill='x')
        tk.Label(self, text=url[:115], anchor='w', wraplength=530,
                 bg=gui.PANEL_2, fg=gui.MUTED).pack(fill='x', pady=4)
        actions = ttk.Frame(self)
        actions.pack(fill='x')
        ttk.Button(actions, text='Inspect', command=self.inspect, width=8).pack(side='left')
        ttk.Button(actions, text='Play', command=self.preview, width=8).pack(side='left', padx=3)
        self.download_button = ttk.Button(actions, text='Download', command=self.download, width=10)
        self.download_button.pack(side='left')
        self.make_button = ttk.Button(actions, text='Clips', command=lambda: gui._open_video_studio(url), width=8)
        self.make_button.pack(side='left', padx=3)
        from app.video_workflow import QUALITIES
        ttk.Combobox(actions, textvariable=self.quality, values=list(QUALITIES), state='readonly', width=8).pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Stop job', command=self.cancel, state='disabled', width=9)
        self.cancel_button.pack(side='left', padx=3)
        tk.Label(self, textvariable=self.status, anchor='w', wraplength=530,
                 bg=gui.PANEL_2, fg=gui.MUTED).pack(fill='x', pady=(6, 0))
        ttk.Button(self, text='Open source ↗', command=lambda: gui._open_source(url)).pack(anchor='w', pady=(6, 0))
        self._busy = False
        self._cancel = threading.Event()


class VideoEditorCard(VideoStudio):
    """Exact export settings stay editable next to the chat's player."""
    def __init__(self, parent, gui, path, position=0, duration=0):
        tk.Frame.__init__(self, parent, bg=gui.PANEL_2, padx=14, pady=12,
                          highlightthickness=1, highlightbackground=gui.LINE)
        self.gui = gui
        self.source = StringVar(self, value=str(path))
        self.start = StringVar(self, value=f'{position:.2f}')
        self.end = StringVar(self, value=f'{min(duration, position + 40):.2f}' if duration else '')
        self.aspect = StringVar(self, value='vertical')
        self.layout = StringVar(self, value='fit')
        self.quality = StringVar(self, value='high')
        self.speed = StringVar(self, value='1')
        self.title = StringVar(self, value='')
        self.enhance = tk.BooleanVar(self, value=False)
        self.normalize = tk.BooleanVar(self, value=False)
        self.captions = tk.BooleanVar(self, value=True)
        self.status = StringVar(self, value='Export a new video. Source stays unchanged. Maximum three minutes per export.')
        self.result_path = str(path)
        tk.Label(self, text='Edit video · exact trim & export', bg=gui.PANEL_2, fg=gui.WHITE,
                 font=('Segoe UI Semibold', 12), anchor='w').pack(fill='x')
        tk.Label(self, text=str(path), bg=gui.PANEL_2, fg=gui.MUTED, wraplength=525,
                 anchor='w').pack(fill='x', pady=(4, 8))
        fields = ttk.Frame(self)
        fields.pack(fill='x')
        for label, variable, values in [
            ('Start seconds', self.start, None), ('End seconds', self.end, None),
            ('Frame shape', self.aspect, ['vertical', 'square', 'original']),
            ('Framing', self.layout, ['fit', 'crop']),
            ('Export quality', self.quality, ['high', 'standard']),
            ('Export speed', self.speed, ['0.5', '0.75', '1', '1.25', '1.5', '2']),
            ('Export title', self.title, None)]:
            row = ttk.Frame(fields)
            row.pack(fill='x', pady=3)
            ttk.Label(row, text=label, width=19).pack(side='left')
            if values:
                ttk.Combobox(row, textvariable=variable, values=values, state='readonly', width=25).pack(side='left')
            else:
                ttk.Entry(row, textvariable=variable, width=28).pack(side='left')
        for label, variable in [('Mild contrast and colour adjustments', self.enhance),
                                ('Normalize audio loudness', self.normalize),
                                ('Captions from matching analysis, when available', self.captions)]:
            ttk.Checkbutton(self, text=label, variable=variable).pack(anchor='w', pady=3)
        ttk.Label(self, text='Fit keeps the full image; crop removes edges. Adjustments cannot '
                  'restore missing detail or guarantee trends.', wraplength=525).pack(anchor='w', pady=6)
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=5)
        self.download_button = ttk.Button(actions, text='Export edit', command=self.export)
        self.download_button.pack(side='left')
        self.make_button = ttk.Button(actions, text='Create result link', command=self.share)
        self.make_button.pack(side='left', padx=6)
        self.cancel_button = ttk.Button(actions, text='Stop job', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left')
        ttk.Button(actions, text='Close', command=self.close).pack(side='right')
        ttk.Label(self, textvariable=self.status, wraplength=525).pack(anchor='w', pady=6)
        self._busy = False
        self._cancel = threading.Event()

    def export(self):
        try:
            arguments = {'path': self.source.get(), 'start_seconds': float(self.start.get()),
                         'aspect': self.aspect.get(), 'layout': self.layout.get(),
                         'quality': self.quality.get(), 'speed': float(self.speed.get()),
                         'title': self.title.get(), 'enhance': self.enhance.get(),
                         'normalize_audio': self.normalize.get(), 'captions': self.captions.get()}
            if self.end.get().strip():
                arguments['end_seconds'] = float(self.end.get())
            context = getattr(getattr(self.gui.runtime, 'session', None), 'last_video', {})
            if context.get('path') == self.source.get() and context.get('analysis_path'):
                arguments['analysis_path'] = context['analysis_path']
            self._run('edit_video_clip', arguments)
        except ValueError:
            self.status.set('Enter start and end as seconds, for example 12.5 and 42.5.')

    def share(self):
        self._run('share_video_result', {'path': self.result_path, 'title': self.title.get()})

    def _complete(self, result, inspect):
        if result.success and result.tool == 'edit_video_clip':
            self.result_path = result.output['path']
        super()._complete(result, inspect)
