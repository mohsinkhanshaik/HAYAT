"""Actual Tk viewer and registry events, without clicking or typing in another app."""
import ctypes
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from app.emergency import ExecutionInterlock
from app.gui_operation_monitor import OperationPreviewMixin
from app.gui_theme import Palette, style_controls, style_hayat_buttons
from app.operation_monitor import OperationMonitor
from app.safety.permissions import PermissionManager
from tests.conftest import open_window, close_window
from tests.test_operation_monitor import event


class GUI(Palette, OperationPreviewMixin):
    def __init__(self, root, tmp_path):
        self.root, self._closed = root, False
        self.runtime = SimpleNamespace(registry=SimpleNamespace(interlock=ExecutionInterlock(),
                                permissions=PermissionManager({'default': 'ALLOW'}, tmp_path)),
                                authority=SimpleNamespace(privacy_mode=False), session=SimpleNamespace(session_id='local'))
        self.stops = []
        self._operation_monitor = OperationMonitor(capture=lambda: Image.new('RGB', (640, 400), '#235ACA'),
                                                  allowed=self._preview_allowed,
                                                  version=lambda: self.runtime.registry.interlock.epoch, interval=.1)

    def _stop_and_correct(self):
        self.stops.append('correct')

    def _emergency_stop(self):
        self.stops.append('stop')
        self.runtime.registry.interlock.halt()


@pytest.fixture
def preview(shared_tk, tmp_path):
    from tkinter import ttk
    root = open_window(shared_tk, '720x560-4000-4000')
    style = ttk.Style(root)
    previous_theme = style.theme_use()
    style.theme_use('clam')
    style_controls(root, Palette)
    style_hayat_buttons(root, Palette)
    gui = GUI(root, tmp_path)
    try:
        yield gui
    finally:
        gui._close_operation_monitor()
        style.theme_use(previous_theme)
        close_window(root)


def pump(gui, predicate):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        gui.root.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def test_view_shows_actual_lifecycle_frame_pause_and_emergency_controls(preview):
    gui = preview
    window = gui._open_operation_preview()
    gui._operation_event(event('TOOL_STARTED', session_id='local'))
    pump(gui, lambda: window._image is not None and bool(window.events.get_children()))
    assert 'Running' in window.status.get()
    window.paused.set(True)
    window.pause()
    assert window._image is None and 'paused' in window.status.get()
    window.paused.set(False)
    window.pause()
    window.observe()
    pump(gui, lambda: window._image is not None)
    gui._emergency_stop()
    window.poll(reschedule=False)
    assert window._image is None and 'blocked' in window.status.get()
    window.close()
    assert gui._operation_auto_open is False
    assert not gui._operation_monitor.snapshot()['has_frame']


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows native input-transparent preview')
def test_auto_view_does_not_capture_mouse_or_keyboard_and_manual_open_restores_controls(preview):
    gui = preview
    foreground = ctypes.windll.user32.GetForegroundWindow()
    gui._operation_event(event('TOOL_STARTED', session_id='local'))
    window = gui._operation_preview
    gui.root.update()
    assert ctypes.windll.user32.GetForegroundWindow() == foreground
    handle = int(window.wm_frame(), 16)
    from ctypes import wintypes
    native = ctypes.WinDLL('user32')
    native.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    native.GetWindowLongW.restype = ctypes.c_long
    assert native.GetWindowLongW(handle, -20) & 0x08080020 == 0x08080020
    native.IsWindowVisible.argtypes = [wintypes.HWND]
    assert native.IsWindowVisible(handle)
    native.IsWindowVisible.argtypes = [wintypes.HWND]
    assert native.IsWindowVisible(window.canvas.winfo_id()), 'Automatic preview canvas must be visible'
    assert window.canvas.winfo_width() > 100
    pump(gui, lambda: window._image is not None)
    window._capture_exclusion(False)
    from tests.test_gui_screenshots import _grab
    _grab(SimpleNamespace(root=window), '99-operation-view-automatic')
    assert not window.controls.winfo_ismapped()
    gui._open_operation_preview()
    gui.root.update()
    assert not native.GetWindowLongW(handle, -20) & 0x08000020
    assert window.controls.winfo_ismapped()


def test_native_window_setup_failure_leaves_no_orphan_and_disables_auto(preview, monkeypatch):
    from app.gui_operation_monitor import OperationPreviewWindow
    gui = preview
    children = tuple(gui.root.winfo_children())
    monkeypatch.setattr(OperationPreviewWindow, '_show_without_activation',
                        lambda _self: (_ for _ in ()).throw(OSError('native setup failed')))
    with pytest.raises(OSError, match='native setup failed'):
        gui._operation_event(event('TOOL_STARTED', session_id='local'))
    assert tuple(gui.root.winfo_children()) == children
    assert not gui._operation_monitor.snapshot()['visible']
    assert not gui._operation_auto_open


def test_other_session_event_is_ignored_and_close_disables_auto_reopen(preview):
    gui = preview
    gui._operation_event(event('TOOL_STARTED', session_id='other'))
    assert not hasattr(gui, '_operation_preview')
    window = gui._open_operation_preview()
    window.close()
    gui._operation_event(event('TOOL_STARTED', session_id='local'))
    assert not window.winfo_exists()


def test_withdraw_and_direct_destroy_stop_observation_and_release_poll(preview):
    gui = preview
    window = gui._open_operation_preview()
    gui.root.update()
    window.withdraw()
    gui.root.update()
    assert not gui._operation_monitor.snapshot()['visible']
    assert not gui._operation_monitor.observe_once()
    window.deiconify()
    gui.root.update()
    assert gui._operation_monitor.snapshot()['visible']
    timer = window._timer
    window.destroy()
    assert window.closed and window._timer is None
    assert timer not in gui.root.tk.call('after', 'info')
    assert not gui._operation_monitor.snapshot()['visible']
    assert not gui._operation_auto_open


def test_observation_and_stop_controls_fit_after_compact_resizes(preview):
    window = preview._open_operation_preview()
    for width, height in ((390, 610), (320, 610), (390, 450), (320, 450)):
        window.geometry(f'{width}x{height}+20+20')
        preview.root.update()
        assert window.canvas.winfo_height() >= 40
        assert window.events.winfo_height() >= 40
        for item in window.canvas.find_all():
            left, top, right, bottom = window.canvas.bbox(item)
            assert left >= 0 and top >= 0
            assert right <= window.canvas.winfo_width() and bottom <= window.canvas.winfo_height()
        for row in (window.controls, window.actions):
            for control in row.items:
                assert control.winfo_ismapped()
                assert control.winfo_x() + control.winfo_width() <= row.winfo_width(), control.cget('text')
                assert control.winfo_y() + control.winfo_height() <= row.winfo_height(), control.cget('text')
                assert control.winfo_rooty() + control.winfo_height() <= window.winfo_rooty() + window.winfo_height()
    window._capture_exclusion(False)
    from tests.test_gui_screenshots import _grab
    _grab(SimpleNamespace(root=window), '101-operation-view-compact')


def test_real_owned_window_capture_is_displayed_without_retaining_a_file(preview, monkeypatch):
    from PIL import ImageGrab
    gui = preview
    gui.root.geometry('720x560+20+20')
    gui.root.update()
    target = int(gui.root.wm_frame(), 16)
    gui._operation_monitor.capture = lambda: ImageGrab.grab(window=target)
    window = gui._open_operation_preview()
    window.observe()
    pump(gui, lambda: window._image is not None)
    assert window._image.width > 100 and window._image.height > 100
    style_controls(window, Palette)
    style_hayat_buttons(window, Palette)
    window.geometry('390x610+760+20')
    window._capture_exclusion(False)  # Explicit screenshot of this owned test UI.
    window.update()
    from tests.test_gui_screenshots import _grab
    _grab(SimpleNamespace(root=window), '97-operation-view')
