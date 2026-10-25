"""Main-window launches share the real encrypted networking backend and events."""
import time

from tests.test_gui import own_gui as own_gui
from tests.test_gui_networking import finish, save


def test_main_launchers_reuse_workspace_and_tool_results_share_saved_contacts(own_gui):
    gui = own_gui
    gui.root.deiconify()
    gui._open_networking()
    window = gui._networking_window
    finish(window)
    contact_id = save(window, 'Integration Example', 'Python, documentation')
    gui._open_networking()
    assert gui._networking_window is window
    result = gui.runtime.registry.execute('networking_match', {'goal': 'Python documentation'})
    assert result.success, result.error
    assert any(row['id'] == contact_id for row in result.output['contacts'])
    gui._open_onion_navigation()
    navigator = gui._onion_navigation_window
    gui._open_onion_navigation()
    assert gui._onion_navigation_window is navigator
    gui._open_operation_preview()
    preview = gui._operation_preview
    gui._open_operation_preview()
    assert gui._operation_preview is preview
    # Real synchronous privacy events invalidate even a rapid on/off pair.
    controller = gui._operation_monitor
    generation = controller._generation
    gui.runtime.authority.set_privacy_mode(True)
    gui.runtime.authority.set_privacy_mode(False)
    assert controller._generation >= generation + 2
    assert not controller.snapshot()['has_frame']
    # Exercise the live registry -> GUI listener -> worker -> frame path with
    # an owned-window read probe, without sending input to another application.
    from PIL import ImageGrab
    handle = int(gui.root.wm_frame(), 16)
    controller.capture = lambda: ImageGrab.grab(window=handle)
    gui.runtime.registry.register('computer_preview_probe', 'Read this test window geometry.',
                                  {'type': 'object', 'properties': {}}, 'low', 'computer_observe',
                                  lambda: {'width': gui.root.winfo_width()})
    result = gui.runtime.registry.execute('computer_preview_probe')
    assert result.success, result.error
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        gui.root.update()
        if preview._image is not None and len(preview.events.get_children()) >= 2:
            break
        time.sleep(.01)
    assert preview._image is not None
    phases = [preview.events.item(row, 'values')[0] for row in preview.events.get_children()]
    assert 'Running' in phases and 'Returned' in phases
    preview.close()
    navigator.close()
    window.close()
    gui.runtime.networking.delete(contact_id)
    # Let queued tool events flush before the fixture tears down the real GUI.
    deadline = time.monotonic() + .15
    while time.monotonic() < deadline:
        gui.root.update()
        time.sleep(.01)
