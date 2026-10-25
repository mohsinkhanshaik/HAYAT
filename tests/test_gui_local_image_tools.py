"""Real offline image packages and registered handlers drive the desktop view."""
from pathlib import Path
import json
import os
import shutil
import threading
import time
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from app.gui_image_tools import LocalImageToolsWindow
from app.gui_theme import Palette, style_controls, style_hayat_buttons
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


class GUI(Palette):
    def __init__(self, root, registry):
        self.root = root
        self.runtime = SimpleNamespace(registry=registry)


def finish(panel, predicate=None, timeout=15):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        panel.update()
        if predicate() if predicate is not None else not panel.busy:
            return
        time.sleep(.01)
    assert predicate() if predicate is not None else not panel.busy, panel.status.get()


@pytest.fixture
def studio(shared_tk, tmp_path):
    from tkinter import ttk
    from app.tools.image_tools import register_local_image_tools
    from tests.conftest import close_window, open_window
    picture = Image.new('RGB', (128, 96), '#204060')
    draw = ImageDraw.Draw(picture)
    for index in range(7):
        draw.rectangle((index * 17, index * 9, index * 17 + 12, index * 9 + 22),
                       fill=(30 * index, 255 - 20 * index, 40 + 25 * index))
    picture.save(tmp_path / 'first.png')
    shutil.copyfile(tmp_path / 'first.png', tmp_path / 'exact.png')
    picture.save(tmp_path / 'same-pixels.bmp')
    different = picture.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    ImageDraw.Draw(different).ellipse((3, 2, 72, 85), fill='yellow')
    different.save(tmp_path / 'different.png')
    exif = Image.Exif()
    exif[305] = 'HAYAT synthetic test'
    exif[315] = 'private-author-sentinel'
    exif[37510] = b'private-comment-sentinel'
    picture.save(tmp_path / 'technical.jpg', exif=exif)
    (tmp_path / 'broken.png').write_bytes(b'not a real image')
    registry = ToolRegistry(PermissionManager({'default': 'DENY', 'permissions': {
        'filesystem_read': 'ALLOW', 'system_read': 'ALLOW'}}, tmp_path))
    register_local_image_tools(registry)
    calls = []
    execute = registry.execute
    def tracked(name, arguments):
        calls.append((name, arguments, threading.get_ident()))
        return execute(name, arguments)
    registry.execute = tracked
    parent = open_window(shared_tk, '1000x780-4000-4000')
    style = ttk.Style(parent)
    previous_theme = style.theme_use()
    style.theme_use('clam')
    style_controls(parent, Palette)
    style_hayat_buttons(parent, Palette)
    panel = LocalImageToolsWindow(GUI(parent, registry))
    panel.geometry('860x680-4000-4000')
    panel.update()
    yield panel, registry, calls, tmp_path
    if panel.winfo_exists():
        panel.close()
    style.theme_use(previous_theme)
    close_window(parent)


def test_open_and_file_pickers_never_start_analysis_or_download(studio, monkeypatch):
    panel, _registry, calls, folder = studio
    assert not calls and not panel.last_result
    assert panel.buttons['inspect'].instate(['disabled']) and panel.buttons['compare'].instate(['disabled'])
    monkeypatch.setattr('app.gui_image_tools.filedialog.askopenfilename', lambda **_kw: str(folder / 'first.png'))
    panel.choose_image('path')
    assert panel.path.get() == str(folder / 'first.png') and not calls
    monkeypatch.setattr('app.gui_image_tools.filedialog.askopenfilename', lambda **_kw: str(folder / 'exact.png'))
    panel.choose_image('other')
    assert panel.other.get() == str(folder / 'exact.png') and not calls
    monkeypatch.setattr('app.gui_image_tools.filedialog.askopenfilename', lambda **_kw: '')
    panel.choose_image('other')
    assert panel.other.get() == str(folder / 'exact.png') and not calls
    assert 'No camera or gallery enrollment' in panel.report.get('1.0', 'end')


def test_inspect_uses_real_metadata_quality_and_registry_off_tk(studio):
    panel, _registry, calls, folder = studio
    panel.path.set(str(folder / 'technical.jpg'))
    assert panel.inspect()
    finish(panel)
    value = panel.last_result
    assert value is not None, panel.status.get()
    assert value['width'] == 128 and value['height'] == 96
    assert value['file_bytes'] > 0 and len(value['file_sha256']) == 64
    assert value['local_only'] is True and value['identity_inference'] is False
    assert 'quality' in value and 'heuristic' in json.dumps(value).lower()
    assert 'private-author-sentinel' not in json.dumps(value)
    assert 'private-comment-sentinel' not in json.dumps(value)
    assert '128' in panel.report.get('1.0', 'end') and 'SHA' in panel.report.get('1.0', 'end')
    assert calls[0][:2] == ('local_image_inspect', {'path': str(folder / 'technical.jpg')})
    assert calls[0][2] != threading.get_ident()


@pytest.mark.parametrize('other,byte_equal,pixel_equal', [
    ('exact.png', True, True), ('same-pixels.bmp', False, True), ('different.png', False, False)])
def test_compare_real_files_reports_byte_pixel_and_actual_hamming_distances(studio, other, byte_equal, pixel_equal):
    panel, _registry, calls, folder = studio
    panel.path.set(str(folder / 'first.png'))
    panel.other.set(str(folder / other))
    assert panel.compare()
    finish(panel)
    value = panel.last_result
    assert value is not None, panel.status.get()
    assert value['byte_equal'] is byte_equal and value['pixel_equal'] is pixel_equal
    assert value['hash_bits'] == 64
    if pixel_equal:
        assert value['phash_distance'] == 0 and value['dhash_distance'] == 0
    else:
        assert max(value['phash_distance'], value['dhash_distance']) > 0
    assert calls[0][0] == 'local_image_compare'
    text = panel.report.get('1.0', 'end').lower()
    assert 'hamming' in text and 'identity' in text and '%' not in text


def test_toolkit_status_reports_real_project_packages_and_versions(studio):
    panel, _registry, calls, folder = studio
    assert panel.check_toolkit()
    finish(panel)
    value = panel.last_result
    assert value is not None, panel.status.get()
    assert value['available'] is True
    assert Path(value['project_root']).resolve() == Path(__file__).resolve().parents[1]
    packages = {row['name'].lower(): row for row in value['packages']}
    assert any('imagehash' in name and row['available'] is True for name, row in packages.items())
    assert any('exifread' in name and row['available'] is True for name, row in packages.items())
    assert all(row.get('version') and row.get('module_path') for row in packages.values() if row['available'])
    assert calls[0][0] == 'local_image_toolkit_status'
    assert not any('download' in call[0] or 'setup' in call[0] for call in calls)
    assert not any(folder.glob('*.onnx'))


def test_missing_second_image_does_not_dispatch(studio):
    panel, _registry, calls, folder = studio
    panel.path.set(str(folder / 'first.png'))
    assert not panel.compare() and not calls
    assert 'second' in panel.status.get()


@pytest.mark.parametrize('value', ['https://example.org/image.png', r'\\server\share\image.png', '//server/share/image.png'])
def test_network_paths_rejected_before_tool_dispatch(studio, value):
    panel, _registry, calls, _folder = studio
    panel.path.set(value)
    assert not panel.inspect() and not calls
    assert 'URLs and network shares' in panel.status.get()


def test_invalid_image_and_permission_failure_never_fabricate_report(studio):
    panel, registry, _calls, folder = studio
    initial = panel.report.get('1.0', 'end')
    panel.path.set(str(folder / 'broken.png'))
    assert panel.inspect()
    finish(panel)
    assert panel.last_result is None and panel.report.get('1.0', 'end') == initial
    assert 'ready' not in panel.status.get().lower()
    registry.permissions.policies['filesystem_read'] = 'DENY'
    panel.path.set(str(folder / 'first.png'))
    assert panel.inspect()
    finish(panel)
    assert panel.last_result is None and 'DENY' in panel.status.get()


def test_stop_blocks_new_dispatch_and_late_delivery(studio):
    panel, registry, calls, folder = studio
    registry.interlock.halt()
    assert not panel.check_toolkit() and not calls
    registry.interlock.resume()
    panel.path.set(str(folder / 'first.png'))
    assert panel.inspect()
    panel._worker.join(timeout=12)
    assert not panel._worker.is_alive()
    registry.interlock.halt()
    finish(panel)
    assert panel.last_result is None
    assert 'cancelled' in panel.status.get().lower() or 'stop' in panel.status.get().lower()


def test_cancelled_worker_cannot_overlap_or_replace_the_reading_pane(studio):
    panel, registry, _calls, folder = studio
    panel.path.set(str(folder / 'first.png'))
    spec = registry._tools['local_image_inspect']
    original = spec.handler
    started, release = threading.Event(), threading.Event()
    def delayed(**arguments):
        started.set()
        release.wait(3)
        return original(**arguments)
    spec.handler = delayed
    initial = panel.report.get('1.0', 'end')
    try:
        assert panel.inspect() and started.wait(1)
        panel.cancel()
        assert not panel.busy and panel.buttons['inspect'].instate(['disabled'])
        assert not panel.check_toolkit()
        release.set()
        finish(panel, lambda: panel._worker is None)
        assert panel.last_result is None and panel.report.get('1.0', 'end') == initial
        assert not panel.buttons['inspect'].instate(['disabled'])
    finally:
        release.set()


@pytest.mark.parametrize('operation,value', [
    ('inspect', {'faces': [], 'local_only': True, 'identity_inference': False}),
    ('compare', {'operation': 'compare', 'local_only': False, 'identity_inference': True}),
    ('status', {'available': True, 'packages': 'not package metadata'}),
])
def test_formatter_rejects_wrong_domain_and_missing_local_only_flags(operation, value):
    with pytest.raises(ValueError):
        LocalImageToolsWindow._accept_report(operation, value)


def test_close_cleans_polling_and_blocks_late_native_callbacks(studio):
    panel = studio[0]
    timer = panel._poll_job
    panel.close()
    assert panel._closed and panel._cancel.is_set()
    assert timer not in panel.tk.call('after', 'info')


def test_compact_window_keeps_paths_actions_and_report_visible(studio):
    panel, _registry, _calls, folder = studio
    panel.geometry('590x440-4000-4000')
    panel.update()
    assert panel.report.winfo_height() >= 120
    for widget in (panel.entries['path'], panel.entries['other'], panel.buttons['inspect'], panel.buttons['compare'], panel.buttons['status']):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx() + widget.winfo_width() <= panel.winfo_rootx() + panel.winfo_width()
        assert widget.winfo_rooty() + widget.winfo_height() <= panel.winfo_rooty() + panel.winfo_height()
    if os.getenv('HAYAT_SHOTS') == '1':
        from tests.test_gui_screenshots import _grab, _pump
        panel.path.set(str(folder / 'first.png'))
        panel.other.set(str(folder / 'same-pixels.bmp'))
        assert panel.compare()
        finish(panel)
        panel.geometry('860x680+25+25')
        panel.deiconify()
        panel.lift()
        panel.attributes('-topmost', True)
        _pump(SimpleNamespace(root=panel), .6)
        for size, name in [('860x680+25+25', '107-local-image-tools'), ('590x440+25+25', '108-local-image-tools-compact')]:
            panel.geometry(size)
            _pump(SimpleNamespace(root=panel), .25)
            _grab(SimpleNamespace(root=panel), name)


def test_larger_text_keeps_actions_and_a_scrollable_report(studio):
    from app.gui_theme import apply_text_scale
    panel = studio[0]
    try:
        apply_text_scale(panel, 1.4)
        panel.geometry('820x700-4000-4000')
        panel.update()
        assert panel.report.winfo_height() >= 160
        assert panel.report.cget('yscrollcommand')
        for widget in (panel.choosers['path'], panel.buttons['inspect'], panel.buttons['status']):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() + widget.winfo_width() <= panel.winfo_rootx() + panel.winfo_width()
    finally:
        apply_text_scale(panel, 1.0)
