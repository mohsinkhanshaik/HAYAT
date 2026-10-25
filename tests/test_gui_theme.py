"""GUI-owned fonts must never call Tcl during background garbage collection."""
from __future__ import annotations

import threading

import pytest

tk = pytest.importorskip("tkinter")


def test_resource_finalizers_never_call_tcl_from_a_worker(shared_tk):
    from app.gui_resources import StringVar
    from app.gui_theme import apply_text_scale

    root = tk.Toplevel(shared_tk)
    root.withdraw()
    try:
        label = tk.Label(root, text="Resizable", font=("Segoe UI", 10))
        variable = StringVar(label, "Ready")
        variable.trace_add("write", lambda *_args: None)
        variable_name = str(variable)
        heading = tk.Label(root, text="Heading", font=("Segoe UI", 20))
        original_names = set(root.tk.call("font", "names"))
        apply_text_scale(root, 1.3)
        font = label._hayat_fonts["widget"][0]
        assert font.actual("size") == 13
        assert heading.cget("font") == "{Segoe UI} 20"
        apply_text_scale(root, 1.0)
        assert font.actual("size") == 10
        assert set(root.tk.call("font", "names")) - original_names == {font.name}
        calls = []
        font._call = lambda *args: calls.append(threading.get_ident())
        # Exercise the finalizers while their Tcl resources still exist. A
        # normal Tk Variable would block here waiting for the main event loop.
        worker = threading.Thread(target=lambda: (font.__del__(), variable.__del__()), daemon=True)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive(), "resource finalizers must never wait for Tk"
        assert calls == []
        label.destroy()
        assert font.name not in root.tk.call("font", "names")
        assert not root.tk.call("info", "exists", variable_name)
        assert variable._tk is None
        assert label._hayat_owned_fonts == []
        assert label._hayat_fonts == {}
    finally:
        root.destroy()


def test_polling_variable_skips_duplicate_traces_but_normal_set_still_notifies(shared_tk):
    from app.gui_resources import StringVar
    root = tk.Toplevel(shared_tk)
    root.withdraw()
    try:
        value = StringVar(root, value="Ready")
        calls = []
        value.trace_add("write", lambda *args: calls.append(value.get()))
        value.set_if_changed("Ready")
        assert calls == []
        value.set_if_changed("Working")
        value.set_if_changed("Working")
        assert calls == ["Working"]
        value.set("Working")
        assert calls == ["Working", "Working"]
    finally:
        root.destroy()
