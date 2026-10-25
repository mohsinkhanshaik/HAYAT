"""Exercise the real workspace tabs and worker delivery on the Tk thread."""
import threading
import time

from app.tools.registry import ToolResult


def pump(gui, predicate=lambda: False, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        gui.root.update()
        if predicate():
            return
        time.sleep(.02)


def test_workspace_tabs_worker_stop_and_compact_layout(monkeypatch, shared_tk):
    from tests.test_gui import _build_window, _release
    from tests.test_gui_screenshots import _grab
    gui = _build_window()
    done = threading.Event()
    try:
        gui._open_workspace_tools()
        window = gui._workspace_tools
        window.geometry("800x600+50+50")
        gui.root.update()
        assert len(window.tabs.tabs()) == 3
        for index, name in enumerate(("64-live-screen", "65-person-detection", "66-replica-studio")):
            window.tabs.select(index)
            pump(gui, seconds=.2)
            _grab(gui, name, window)
        window.replica_scroll.canvas.yview_moveto(1)
        gui.root.update()
        assert window.replica_scroll.canvas.yview()[1] > .99
        _grab(gui, "67-replica-checkers", window)
        main_thread = threading.get_ident()
        called = []
        def execute(tool, arguments):
            called.append(threading.get_ident())
            assert done.wait(5)
            return ToolResult(tool, True, {"text": "late read must be discarded"})
        monkeypatch.setattr(window.registry, "execute", execute)
        window.run("screen_read", {}, "screen")
        pump(gui, lambda: bool(called))
        assert called[0] != main_thread and window.busy
        window.stop_screen()
        done.set()
        pump(gui, lambda: not window.busy)
        assert "late read" not in window.screen_text.get("1.0", "end")
        window.destroy()
        assert window.closed
    finally:
        done.set()
        _release(gui)


def test_replica_form_pickers_checker_switching_and_actual_checker(monkeypatch, shared_tk, tmp_path):
    from tests.test_gui import _build_window, _release
    from app.replica import BUNDLE
    from tests.test_gui_screenshots import _grab
    import shutil

    gui = _build_window()
    try:
        gui._open_workspace_tools('replica')
        window = gui._workspace_tools
        window.geometry('800x600+50+50')
        window.generate()
        assert not window.busy and 'workflow goal' in window.status.cget('text')
        assert window.goal.get('1.0', 'end').strip() == ''
        monkeypatch.setattr('app.gui_workspace_tools.filedialog.askdirectory', lambda **_: str(tmp_path))
        window.choose_workspace()
        assert window.workspace.get() == str(tmp_path)
        tokens = tmp_path / 'custom-tokens.json'
        shutil.copy(BUNDLE / 'replica-design/tokens.json', tokens)
        window.checker.set('contrast')
        assert window.check_path.get() == 'replica/design/tokens.json'
        monkeypatch.setattr('app.gui_workspace_tools.filedialog.askopenfilename', lambda **_: str(tokens))
        window.choose_checker_file(window.check_path)
        assert window.check_path.get() == 'custom-tokens.json'
        window.checker.set('image_diff')
        assert str(window.diff_widget.cget('state')) == 'readonly'
        window.checker.set('brand')
        assert str(window._entries['Input file (relative)'].cget('state')) == 'disabled'
        assert str(window._browse_buttons['Second image (diff)'].cget('state')) == 'disabled'
        window.checker.set('contrast')
        assert window.check_path.get() == 'custom-tokens.json'
        # Real registry, subprocess and upstream checker, delivered to the Tk UI.
        window.check()
        assert window.busy and all(str(button.cget('state')) == 'disabled' for button in window._action_buttons)
        assert window.progress.winfo_manager() == 'pack'
        pump(gui, lambda: not window.busy, seconds=10)
        assert not window.busy
        assert window.progress.winfo_manager() == ''
        shown = window.replica_text.get('1.0', 'end')  # plain lines, not JSON
        assert 'Checker: contrast' in shown and 'Passed: yes' in shown
        tokens.write_text('{"color":{"bg":"#fff","text":"#fff"}}')
        window.check()
        pump(gui, lambda: not window.busy, seconds=10)
        assert 'Passed: no' in window.replica_text.get('1.0', 'end')
        assert 'needs attention' in window.status.cget('text')
        window.checker.set('image_diff')
        outside = tmp_path.parent / 'outside.png'
        monkeypatch.setattr('app.gui_workspace_tools.filedialog.askopenfilename', lambda **_: str(outside))
        window.choose_checker_file(window.check_path)
        assert 'inside the project' in window.status.cget('text') and window.check_path.get() == ''
        window.replica_scroll.canvas.yview_moveto(0)
        pump(gui, seconds=.2)
        _grab(gui, '68-replica-updated-form', window)
        window.replica_scroll.canvas.yview_moveto(1)
        pump(gui, seconds=.2)
        _grab(gui, '69-replica-updated-checker', window)
        window.close()
        assert window.closed
    finally:
        _release(gui)
