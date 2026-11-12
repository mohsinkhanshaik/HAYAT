"""Task-oriented desktop guidance uses real Tk widgets and isolated runtime data."""
import os
import time
import json
from types import SimpleNamespace

import pytest

from tests.test_gui import _window, gui  # noqa: F401
from tests.test_job_hunting_upgrade import hunting, jobs_window, pump  # noqa: F401
from tests.test_gui_image_research import studio, image_panel  # noqa: F401


@pytest.fixture(scope='module', autouse=True)
def _establish_shared_interpreter(shared_tk):
    # Establish display/scaling before a main window imports native libraries.
    yield shared_tk


def settle(root, seconds=.15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for _ in range(30):
            if not root.tk.dooneevent(2):
                break
        time.sleep(.005)


def wait_layout(root, ready):
    deadline = time.monotonic() + 3
    while not ready() and time.monotonic() < deadline:
        settle(root, .05)
    assert ready(), 'The requested layout did not settle.'


def test_home_has_real_task_entry_points_and_keeps_the_3d_view(gui, monkeypatch):  # noqa: F811
    gui._show_home()
    gui._set_home_mode('tasks')
    assert gui.task_home.winfo_manager() and not gui.depth_nav.winfo_manager()
    called = []
    monkeypatch.setattr(gui, '_open_jobs', lambda: called.append('jobs'))
    monkeypatch.setattr(gui, '_open_public_cameras', lambda: called.append('cameras'))
    monkeypatch.setattr(gui, '_add_files', lambda: called.append('files'))
    for key in ('jobs', 'cameras', 'files'):
        gui.task_home.buttons[key].invoke()
    assert called == ['jobs', 'cameras', 'files']
    gui.home_mode_button.invoke()
    assert gui.depth_nav.winfo_manager() and not gui.task_home.winfo_manager()
    gui.home_mode_button.invoke()
    assert gui.task_home.winfo_manager() and not gui.depth_nav.winfo_manager()


def test_home_and_chat_navigation_keep_an_unsent_message(gui):  # noqa: F811
    gui.input.delete('1.0', 'end')
    gui.input.insert('1.0', 'Keep my unfinished question.')
    gui._show_home()
    gui._set_home_mode('tasks')
    gui.task_home.buttons['chat'].invoke()
    assert not gui._welcome.winfo_manager()
    assert gui.core_button.cget('text') == 'Home'
    assert gui.input.get('1.0', 'end-1c') == 'Keep my unfinished question.'
    gui.core_button.invoke()
    assert gui._welcome.winfo_manager()
    assert gui.input.get('1.0', 'end-1c') == 'Keep my unfinished question.'


def test_page_guides_cover_every_main_view_and_reuse_the_open_window(gui):  # noqa: F811
    from app.gui_guidance import guide_for
    for key, _icon, _title, _subtitle in gui.VIEWS:
        title, intro, steps, result = guide_for(key)
        assert title and intro and len(steps) == 3 and result
    guide = gui._open_page_guide('invention')
    try:
        assert gui._open_page_guide('invention') is guide
        gui.show_view('invention')
        menu = gui.root.nametowidget(gui.header_more_button.cget('menu'))
        entry = next(index for index in range(menu.index('end') + 1)
                     if menu.type(index) == 'command' and menu.entrycget(index, 'label') == 'How to use this page')
        menu.invoke(entry)
        assert gui.root._hayat_page_guides['invention'] is guide
        assert any('does not prove' in label.cget('text') for label in guide.labels)
        assert guide.bind('<Escape>')
    finally:
        guide.destroy()


def test_resizing_an_invention_workspace_keeps_room_for_it_and_preserves_the_draft(gui):  # noqa: F811
    gui.root.deiconify()
    gui.root.geometry('1100x740+20+20')
    settle(gui.root)
    gui.input.delete('1.0', 'end')
    gui.input.insert('1.0', 'Keep this while I inspect my invention.')
    gui.show_view('invention')
    settle(gui.root)
    gui.root.geometry('1440x900+20+20')
    wait_layout(gui.root, lambda: gui.root.winfo_width() == 1440 and
                gui._explorer_panels['invention'].winfo_width() > 980)
    assert not gui.right_rail.winfo_manager()
    assert gui._explorer_panels['invention'].winfo_width() > 980
    assert gui.input.get('1.0', 'end-1c') == 'Keep this while I inspect my invention.'
    gui.home_button.invoke()
    assert gui.current_view == 'chat' and gui._welcome.winfo_manager()
    assert gui.input.get('1.0', 'end-1c') == 'Keep this while I inspect my invention.'


@pytest.mark.parametrize('width,columns', [(470, 1), (800, 2), (1120, 3)])
def test_task_cards_reflow_and_keyboard_focus_reveals_the_last_card(shared_tk, width, columns):
    import tkinter as tk
    from app.gui_guidance import TaskHome
    from app.gui_theme import Palette
    root = tk.Toplevel(shared_tk)
    root.geometry(f'{width}x360+30+30')
    home = TaskHome(root, Palette, lambda _key: None)
    home.pack(fill='both', expand=True)
    try:
        settle(root)
        assert home._columns == columns
        for card in home.cards:
            assert card.winfo_x() >= 0
            assert card.winfo_x() + card.winfo_width() <= home.body.winfo_width() + 1
        home.canvas.yview_moveto(0)
        home._reveal(home.cards[-1])
        settle(root)
        assert home.cards[-1].winfo_y() + home.cards[-1].winfo_height() <= home.canvas.canvasy(home.canvas.winfo_height()) + 3
    finally:
        root.destroy()


def test_jobs_guidance_follows_the_tab_and_does_not_change_the_profile(jobs_window):  # noqa: F811
    from app.gui_jobs import TAB_GUIDANCE, TABS
    window = jobs_window
    before = window.center.profile.load()
    window.guide_button.invoke()
    opened = window._hayat_page_guides['jobs']
    try:
        for key in ('Profile', 'Matches', 'Applications'):
            window.tabs.select(TABS.index(key))
            pump(window, lambda: window.introduction.cget('text') == TAB_GUIDANCE[key])
        assert window.center.profile.load() == before
        assert any('does not send' in label.cget('text') for label in opened.labels)
    finally:
        opened.destroy()


def test_invention_case_builder_works_with_json_hidden_and_does_not_erase_it(shared_tk, tmp_path):
    from app.gui_invention import InventionPanel
    from app.gui_theme import Palette
    from tests.test_executive_invention import bench
    from tests.test_gui_invention import register_invention_tools, PermissionManager, ToolRegistry
    import tkinter as tk
    workbench = bench(tmp_path, None)
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    root = tk.Toplevel(shared_tk)
    panel = InventionPanel(root, Palette, SimpleNamespace(registry=registry, invention=workbench,
                           session=SimpleNamespace(session_id='friendly-invention-fixture')), lambda _text: None)
    panel.pack(fill='both', expand=True)
    try:
        from app.gui_theme import apply_text_scale
        for geometry, scale in (('1100x740+30+30', 1.0), ('1100x740+30+30', 1.5), ('640x740+30+30', 1.5)):
            root.geometry(geometry)
            apply_text_scale(root, scale)
            settle(root, .6)
            for field in (panel.case_input_entry, panel.case_expected_entry, panel.add_case_button):
                assert field.winfo_width() >= 32
                assert field.winfo_x() + field.winfo_width() <= field.master.winfo_width(), (geometry, scale, field)
        apply_text_scale(root, 1.0)
        assert not panel.tests.frame.winfo_manager()
        panel.goal.insert('1.0', 'Double the input')
        panel.case_inputs.set('x=2')
        panel.case_expected.set('4')
        assert panel.add_case()
        assert 'x=2' in panel.case_preview.cget('text') and 'expected 4' in panel.case_preview.cget('text')
        original = json.loads(panel.tests.get('1.0', 'end'))
        panel.cases_toggle.invoke()
        assert panel.tests.frame.winfo_manager()
        panel.cases_toggle.invoke()
        assert not panel.tests.frame.winfo_manager() and json.loads(panel.tests.get('1.0', 'end')) == original
        panel.create_button.invoke()
        deadline = time.monotonic() + 5
        while panel._pending and time.monotonic() < deadline:
            settle(root, .03)
        assert panel.data is not None, panel.status.get()
        assert panel.data['tests'] == original and panel.data['state'] == 'DRAFT'
        assert panel.state_badge.cget('text') == 'Ready to start'
    finally:
        apply_text_scale(root, 1.0)
        panel.destroy()
        root.destroy()


def test_activity_feedback_does_not_present_an_unknown_duration_as_a_countdown():
    from app.gui import format_activity_countdown
    assert format_activity_countdown({'running': []}, 10, now=65) == 'Thinking · 55.0s elapsed'
    result = format_activity_countdown({'running': [{'label': 'Read sources', 'seconds': 45,
                                                   'estimated_seconds': 20, 'remaining_seconds': 0}]}, None)
    assert 'taking longer than estimated' in result and '~0s left' not in result


@pytest.mark.skipif(os.environ.get('HAYAT_FRIENDLY_SHOTS') != 'final', reason='opt-in workflow visual capture')
def test_capture_job_workflow(jobs_window):  # noqa: F811
    from tests.test_gui_screenshots import _grab
    window = jobs_window
    window.geometry('1120x780+30+30')
    window.deiconify()
    window.tabs.select(2)
    settle(window, .5)
    _grab(window.gui, 'friendly-jobs-profile', widget=window)
    window.tabs.select(0)
    settle(window, .5)
    _grab(window.gui, 'friendly-jobs-matches', widget=window)
    guide = window.open_guide()
    settle(window, .2)
    _grab(window.gui, 'friendly-jobs-guide', widget=guide)
    guide.destroy()


@pytest.mark.skipif(os.environ.get('HAYAT_FRIENDLY_SHOTS') != 'final', reason='opt-in image layout visual capture')
def test_capture_readable_image_research(image_panel):  # noqa: F811
    from tests.test_gui_screenshots import _grab
    from app.gui_theme import apply_text_scale
    panel, _image, _calls = image_panel
    for size, scale, name in (('560x460+30+30', 1.0, 'friendly-image-compact'),
                               ('720x680+30+30', 1.5, 'friendly-image-large-text')):
        apply_text_scale(panel, scale)
        panel.geometry(size)
        settle(panel, .6)
        _grab(panel.gui, name, widget=panel)
        assert panel.evidence.winfo_height() >= (100 if scale > 1 else 60)


@pytest.mark.skipif(os.environ.get('HAYAT_FRIENDLY_SHOTS') != 'baseline', reason='opt-in baseline visual capture')
def test_capture_baseline_home(gui):  # noqa: F811
    from tests.test_gui_screenshots import _grab
    gui.root.geometry('1440x900+20+20')
    gui.root.deiconify()
    gui._welcome_pending = True
    gui.show_view('chat')
    deadline = time.monotonic() + .8
    while time.monotonic() < deadline:
        gui.root.update()
        time.sleep(.02)
    _grab(gui, 'friendly-before-home')


@pytest.mark.skipif(os.environ.get('HAYAT_FRIENDLY_SHOTS') != 'final', reason='opt-in final visual capture')
def test_capture_friendly_home_and_context_guides(gui):  # noqa: F811
    from tests.test_gui_screenshots import _grab
    from app.gui_theme import apply_text_scale
    gui.root.deiconify()
    gui._show_home()
    gui._set_home_mode('tasks')
    try:
        for geometry, name, scale in (('1440x900+20+20', 'friendly-home-wide', 1.0),
                                       ('960x620+20+20', 'friendly-home-compact', 1.0),
                                       ('1180x800+20+20', 'friendly-home-large-text', 1.3)):
            gui.root.geometry(geometry)
            apply_text_scale(gui.root, scale)
            settle(gui.root, .6)
            _grab(gui, name)
        guide = gui._open_page_guide('invention')
        settle(gui.root, .3)
        _grab(gui, 'friendly-invention-guide', widget=guide)
        guide.destroy()
        apply_text_scale(gui.root, 1.0)
        gui.root.geometry('1440x900+20+20')
        gui.show_view('invention')
        panel = gui._explorer_panels['invention']
        deadline = time.monotonic() + 8
        while panel._pending and time.monotonic() < deadline:
            settle(gui.root, .05)
        panel.load_example()
        settle(gui.root, .4)
        _grab(gui, 'friendly-invention-workbench')
    finally:
        apply_text_scale(gui.root, 1.0)
