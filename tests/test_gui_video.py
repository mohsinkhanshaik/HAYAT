from __future__ import annotations

import threading
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.gui import HayatGUI
from app.gui_video import VideoPreview, VideoStudio
from app.gui_rich import find_videos


def test_video_paths_are_found_without_becoming_document_or_image_cards(tmp_path):
    video = tmp_path / 'sample video.mp4'
    video.write_bytes(b'sample')
    assert find_videos(f'Saved to "{video}"') == [video]


def test_inline_preview_uses_ui_thread_for_images_and_closes_decoder(shared_tk, tmp_path):
    from PIL import Image
    root = tk.Toplevel(shared_tk)
    root.withdraw()
    try:
        gui = SimpleNamespace(**{name: getattr(HayatGUI, name) for name in ('PANEL_2', 'LINE', 'WHITE', 'BLACK', 'MUTED')})
        gui._open_file = Mock()
        gui.runtime = SimpleNamespace(registry=SimpleNamespace(execute=Mock(return_value=SimpleNamespace(success=False, error='Test admission refused'))))
        preview = VideoPreview(root, gui, tmp_path / 'x.mp4')
        preview.pack()
        generation = preview._generation
        worker = threading.Thread(target=lambda: (preview._publish(generation, 'metadata', {'duration_seconds': 10}),
                    preview._publish(generation, 'frame', (1.0, Image.new('RGB', (100, 50), 'red')))))
        worker.start()
        worker.join()
        preview._drain()
        assert preview._photo is not None
        assert preview._duration == 10
        # Updating the progress thumb must not initiate an automatic seek,
        # restart the decoder, or undo a paused state on Windows Tk.
        generation = preview._generation
        root.update()
        assert preview._generation == generation
        assert preview._seek_after is None
        process = Mock(poll=Mock(return_value=None))
        preview._process = process
        preview.close()
        process.kill.assert_called_once()
        assert preview._photo is None and preview._closed
    finally:
        root.destroy()


def test_video_studio_requests_real_pipeline_off_thread_and_reports_to_chat(shared_tk):
    root = tk.Toplevel(shared_tk)
    root.withdraw()
    try:
        calls = []
        gui = SimpleNamespace(root=root, PANEL=HayatGUI.PANEL, show_view=Mock(), _append=Mock())
        posted = []
        gui._post = lambda callback, *args: posted.append((callback, args))
        def execute(tool, arguments):
            calls.append((tool, arguments, threading.get_ident()))
            return SimpleNamespace(success=True, output={'text': 'Created clip'}, error=None)
        gui.runtime = SimpleNamespace(registry=SimpleNamespace(execute=execute))
        studio = VideoStudio(gui)
        studio.source.set('https://example.com/video')
        studio.make()
        until = time.monotonic() + 2
        while not posted and time.monotonic() < until:
            time.sleep(.01)
        assert calls[0][0] == 'make_video_clips'
        assert calls[0][1]['url'] == 'https://example.com/video'
        assert calls[0][2] != threading.get_ident()
        for callback, args in posted:
            callback(*args)
        gui._append.assert_called_with('assistant', 'HAYAT\nCreated clip')
        studio.destroy()
    finally:
        root.destroy()


def test_local_play_button_uses_play_tool_to_start_chat_playback(shared_tk):
    gui = SimpleNamespace(root=shared_tk, PANEL=HayatGUI.PANEL)
    studio = VideoStudio(gui)
    try:
        studio.source.set('D:/AI/download/source.mp4')
        studio._run = Mock()
        studio.preview()
        studio._run.assert_called_once_with('play_video', {'path': 'D:/AI/download/source.mp4'})
    finally:
        studio.destroy()


def _generation_gui(root, status=None, generate=None):
    calls, posted = [], []
    status = status if status is not None else {
        'video': {'available': True, 'model': 'dreamshaper_8.safetensors',
                  'motion_module': 'mm_sd_v15_v2.ckpt',
                  'motion_modules': ['mm_sd_v15_v2.ckpt', 'optional/v3_sd15_mm.ckpt']}}

    def execute(tool, arguments):
        calls.append((tool, arguments, threading.get_ident()))
        if tool == 'image_generation_status':
            return SimpleNamespace(tool=tool, success=True, output=status, error=None)
        if tool == 'generate_video' and generate:
            return generate(arguments)
        return SimpleNamespace(tool=tool, success=True, output={'ok': False, 'error': 'Test engine not running'}, error=None)

    gui = SimpleNamespace(root=root, PANEL=HayatGUI.PANEL, show_view=Mock(), _append=Mock(), _open_file=Mock(),
                          _post=lambda callback, *args: posted.append((callback, args)),
                          runtime=SimpleNamespace(registry=SimpleNamespace(execute=execute)))
    return gui, calls, posted


def _pump_video(root, posted, condition=lambda: True, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        while posted:
            callback, args = posted.pop(0)
            callback(*args)
        if condition():
            root.update_idletasks()
            return
        time.sleep(.005)
    raise AssertionError('Video UI did not reach the expected state before its deadline')


def _open_generator(studio, root, posted):
    studio.tabs.select(studio.generation_tab)
    _pump_video(root, posted, lambda: studio._models_loaded and not studio._models_loading)


def test_generation_tab_loads_real_installed_choices_without_starting_generation(shared_tk):
    root = tk.Toplevel(shared_tk)
    root.withdraw()
    gui, calls, posted = _generation_gui(root)
    studio = VideoStudio(gui)
    try:
        root.update()
        assert calls == []  # opening the existing Watch & edit tab does no work
        _open_generator(studio, root, posted)
        assert [tool for tool, _, _ in calls] == ['image_generation_status']
        assert calls[0][2] != threading.get_ident()
        assert tuple(studio.motion_combo['values']) == ('auto', 'mm_sd_v15_v2.ckpt', 'optional/v3_sd15_mm.ckpt')
        assert studio.motion_module.get() == 'auto'
        assert 'mm_sd_v15_v2.ckpt' in studio.model_status.get()
        assert studio.video_quality.get() == 'ultra'
        assert str(studio.generate_button['state']) == 'normal'
        gui._append.assert_not_called()
    finally:
        studio.close()
        root.destroy()


def test_generation_passes_selected_motion_and_shows_only_verified_result(shared_tk, tmp_path):
    root = tk.Toplevel(shared_tk)
    root.withdraw()
    clip = tmp_path / 'verified.mp4'
    clip.write_bytes(b'test artifact admitted by a stubbed backend')

    def generate(arguments):
        return SimpleNamespace(tool='generate_video', success=True, error=None, output={
            'ok': True, 'path': str(clip), 'width': 768, 'height': 768, 'detail': 'Locally generated test fixture',
            'video': {'decoded_verified': True, 'motion_module': arguments['motion_module'],
                      'fps': 30, 'duration_seconds': 1.5}})

    gui, calls, posted = _generation_gui(root, generate=generate)
    studio = VideoStudio(gui)
    try:
        _open_generator(studio, root, posted)
        studio.video_prompt.set('A paper boat moving gently across a pond')
        studio.motion_module.set('optional/v3_sd15_mm.ckpt')
        studio.video_steps.set('25')
        studio.video_resolution.set('384')
        studio.generate_button.invoke()
        _pump_video(root, posted, lambda: not studio._busy)
        tool, arguments, worker = calls[-1]
        assert tool == 'generate_video' and worker != threading.get_ident()
        assert arguments == {'prompt': 'A paper boat moving gently across a pond',
                             'motion_module': 'optional/v3_sd15_mm.ckpt', 'quality': 'ultra',
                             'frames': 16, 'steps': 25, 'width': 384, 'height': 384}
        assert '768×768' in studio.generation_status.get() and '30 fps' in studio.generation_status.get()
        assert studio._generated_path == clip
        assert str(studio.open_generated_button['state']) == 'normal'
        studio.open_generated_button.invoke()
        gui._open_file.assert_called_once_with(clip)
        assert str(clip) in gui._append.call_args.args[1]
        clip.unlink()
        studio.open_generated()
        assert 'no longer available' in studio.generation_status.get()
        assert str(studio.open_generated_button['state']) == 'disabled'
    finally:
        studio.close()
        root.destroy()


@pytest.mark.parametrize('video', [
    {'available': False, 'reason': 'compatible SD 1.5 checkpoint missing', 'motion_modules': []},
    {'available': False, 'reason': 'FFprobe is required to verify output', 'motion_modules': ['mm_sd_v15_v2.ckpt']},
    {'available': True, 'reason': 'ready', 'motion_modules': []},
])
def test_missing_video_components_disable_generation_without_mock_choices(shared_tk, video):
    root = tk.Toplevel(shared_tk)
    root.withdraw()
    gui, calls, posted = _generation_gui(root, {'video': video})
    studio = VideoStudio(gui)
    try:
        _open_generator(studio, root, posted)
        assert str(studio.generate_button['state']) == 'disabled'
        assert 'Generation unavailable' in studio.model_status.get()
        assert studio.generation_status.get() == studio.model_status.get()
        assert 'optional/v3_sd15_mm.ckpt' not in tuple(studio.motion_combo['values'])
        studio.video_prompt.set('clouds')
        studio.generate()
        assert [tool for tool, _, _ in calls] == ['image_generation_status']
    finally:
        studio.close()
        root.destroy()


def test_generation_rejects_empty_prompt_and_reports_backend_failure(shared_tk):
    root = tk.Toplevel(shared_tk)
    root.withdraw()
    gui, calls, posted = _generation_gui(root)
    studio = VideoStudio(gui)
    try:
        _open_generator(studio, root, posted)
        studio.generate()
        assert 'Describe the scene' in studio.generation_status.get()
        assert len(calls) == 1
        studio.video_prompt.set('ocean waves')
        studio.generate()
        _pump_video(root, posted, lambda: not studio._busy)
        assert 'did not produce a verified clip' in studio.generation_status.get()
        assert 'Test engine not running' in studio.generation_status.get()
        assert studio._generated_path is None
        assert str(studio.open_generated_button['state']) == 'disabled'
        assert gui._append.call_args.args[0] == 'error'
    finally:
        studio.close()
        root.destroy()


def test_refresh_preserves_selection_then_clears_uninstalled_module(shared_tk):
    root = tk.Toplevel(shared_tk)
    root.withdraw()
    status = {'video': {'available': True, 'model': 'dreamshaper', 'motion_module': 'mm_sd_v15_v2.ckpt',
                        'motion_modules': ['mm_sd_v15_v2.ckpt', 'optional/v3_sd15_mm.ckpt']}}
    gui, _, posted = _generation_gui(root, status)
    studio = VideoStudio(gui)
    try:
        _open_generator(studio, root, posted)
        studio.motion_module.set('optional/v3_sd15_mm.ckpt')
        studio.refresh_models()
        _pump_video(root, posted, lambda: not studio._models_loading)
        assert studio.motion_module.get() == 'optional/v3_sd15_mm.ckpt'
        status['video']['motion_modules'] = ['mm_sd_v15_v2.ckpt']
        studio.refresh_models()
        _pump_video(root, posted, lambda: not studio._models_loading)
        assert studio.motion_module.get() == 'auto'
        assert tuple(studio.motion_combo['values']) == ('auto', 'mm_sd_v15_v2.ckpt')
    finally:
        studio.close()
        root.destroy()


def test_stop_and_close_cancel_inflight_generation_without_late_ui_writes(shared_tk):
    from app.models.streaming import check_cancelled

    root = tk.Toplevel(shared_tk)
    root.withdraw()
    entered, stopped = threading.Event(), threading.Event()

    def generate(_arguments):
        entered.set()
        try:
            while True:
                check_cancelled()
                time.sleep(.005)
        finally:
            stopped.set()

    gui, _, posted = _generation_gui(root, generate=generate)
    studio = VideoStudio(gui)
    try:
        _open_generator(studio, root, posted)
        studio.video_prompt.set('moving clouds')
        studio.generate()
        assert entered.wait(1)
        assert str(studio.stop_generation_button['state']) == 'normal'
        studio.stop_generation_button.invoke()
        assert studio._cancel.is_set()
        studio.close()
        assert stopped.wait(1)
        _pump_video(root, posted)
        gui._append.assert_not_called()
    finally:
        if studio.winfo_exists():
            studio.close()
        root.destroy()


@pytest.mark.parametrize('geometry', ['390x610', '320x610', '390x450'])
@pytest.mark.parametrize('theme', ['system', 'hayat'])
def test_generation_controls_remain_usable_at_compact_sizes(shared_tk, geometry, theme):
    from tkinter import ttk
    from app.gui_theme import style_controls

    root = tk.Toplevel(shared_tk)
    style = ttk.Style(root)
    previous_theme = style.theme_use()
    if theme == 'hayat':
        style.theme_use('clam')
        style_controls(root, HayatGUI)
    root.geometry(geometry)
    gui, _, posted = _generation_gui(root)
    studio = VideoStudio(gui)
    studio.pack(fill='both', expand=True)
    try:
        _open_generator(studio, root, posted)
        for button in studio.generation_actions.items:
            assert button.winfo_ismapped()
            assert button.winfo_rootx() >= root.winfo_rootx()
            assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
            assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        canvas = studio.generation_scroll.canvas
        assert canvas.winfo_height() > 30
        studio.generation_scroll.canvas.yview_moveto(1)
        root.update()
        assert studio.refresh_models_button.winfo_ismapped()
        assert studio.refresh_models_button.winfo_rooty() >= canvas.winfo_rooty()
        assert studio.refresh_models_button.winfo_rooty() + studio.refresh_models_button.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height() + 2
        # The embedded canvas body is restored after tab remapping.
        studio.tabs.select(0)
        root.update()
        studio.tabs.select(studio.generation_tab)
        root.update()
        assert canvas.itemcget(studio.generation_scroll._window, 'state') != 'hidden'
    finally:
        studio.close()
        root.destroy()
        style.theme_use(previous_theme)
