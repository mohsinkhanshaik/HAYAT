from types import SimpleNamespace

from app.gui_icons import CameraEye, apply_icons, icon_kind
from app.gui_studio import VoiceFocus
from app.gui_theme import Palette


def test_eye_reports_camera_state_and_buttons_keep_labels(shared_tk):
    import tkinter as tk
    from tkinter import ttk
    window = tk.Toplevel(shared_tk)
    try:
        eye = CameraEye(window, Palette)
        eye.pack()
        closed = len(eye.find_all())
        eye.set_active(True)
        assert eye.active and len(eye.find_all()) != closed
        eye.set_active(False)
        assert not eye.active and len(eye.find_all()) == closed
        button = ttk.Button(window, text="Read current screen")
        button.pack()
        apply_icons(window, Palette)
        assert button.cget("text") == "Read current screen"
        assert button.cget("image")
        assert icon_kind(button.cget("text")) == "eye"
    finally:
        window.destroy()


def test_common_actions_have_distinct_icons_and_keep_their_labels(shared_tk):
    import tkinter as tk
    from tkinter import ttk

    labels = {"Generate image": "image", "Analyze video": "video", "Appearance": "theme",
              "Health": "check", "Security": "lock", "Gallery": "gallery",
              "Conversations": "history", "Brain 4D": "brain", "Settings": "settings"}
    assert {label: icon_kind(label) for label in labels} == labels
    assert icon_kind("Stop camera") == "stop"
    assert icon_kind("Open Outlook") != "eye"
    window = tk.Toplevel(shared_tk)
    try:
        buttons = [ttk.Button(window, text=label) for label in labels]
        for button in buttons:
            button.pack()
        apply_icons(window, Palette)
        assert len({str(button.cget("image")) for button in buttons}) == len(buttons)
        assert [button.cget("text") for button in buttons] == list(labels)
    finally:
        window.destroy()


def test_focused_voice_view_does_not_open_microphone_until_clicked(shared_tk):
    import tkinter as tk
    window = tk.Toplevel(shared_tk)
    window.geometry("650x300-4000-4000")
    actions = []
    try:
        scene = VoiceFocus(window, Palette, lambda key: None, actions.append)
        scene.pack(fill="both", expand=True)
        scene.set_motion(False)
        window.update()
        scene.set_status({"running": []}, {"enabled": False, "state": "idle"}, False)
        assert actions == []
        assert scene._voice_state == "off"
        scene._digit(SimpleNamespace(char="3", state=0))
        scene._step_key(1)
        assert actions == []
        x, y, _ = scene._voice_box
        scene._press(SimpleNamespace(x=x, y=y))
        scene._release(SimpleNamespace(x=x, y=y))
        assert actions == ["listening"]
    finally:
        window.destroy()
