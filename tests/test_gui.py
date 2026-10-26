"""The desktop window, which shipped without a single test.

A GUI is the one surface where a mistake is invisible to the suite and
obvious to the person using it, so the properties worth pinning are the ones
that decide whether the window works at all: it builds, its background work
never touches widgets directly, and closing it releases the runtime.

Everything here runs hidden. The window is withdrawn immediately and the
event loop is pumped by hand rather than entered, so the suite never blocks
in mainloop() and nothing flashes on screen.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

tkinter = pytest.importorskip("tkinter")


def _build_window():
    """A hidden window over a real runtime."""
    try:
        # The shared GUI-test interpreter already establishes display access.
        # Starting/discarding another interpreter after it can exhaust Tcl's
        # initialization state on Windows and hide real failures as skips.
        if tkinter._default_root is None or not tkinter._default_root.winfo_exists():
            probe = tkinter.Tk()
            probe.destroy()
            del probe
            import gc
            gc.collect()
    except tkinter.TclError as error:  # pragma: no cover - headless CI
        if 'no display name' in str(error).lower() or "couldn't connect to display" in str(error).lower():
            pytest.skip(f"Tk display unavailable: {error}")
        raise

    from app.gui import HayatGUI, reserve_window
    from app.runtime import build_runtime

    # Mirror production startup: reserve TkDND's STA-bound root before the
    # runtime imports Windows automation libraries.
    reserve_window()
    runtime = build_runtime(use_model=False, interactive_approvals=False)
    # UI lifecycle tests do not activate microphones or import native STT.
    if runtime.voice is not None:
        runtime.voice.config = {**runtime.voice.config, "enabled": False}
    # GUI tests must not overwrite a live user's recoverable draft.
    import tempfile
    from pathlib import Path
    from app.gui_workspace import DraftStore
    runtime.draft_store = DraftStore(Path(tempfile.mkdtemp(prefix="hayat-gui-test-")) / "workspace.db")
    window = HayatGUI(runtime)
    window.root.withdraw()
    return window


def _release(window) -> None:
    try:
        window.runtime.learner.request_stop("test")
        window.runtime.learner.stop()
    except Exception:
        pass
    if not window._closed:
        window.close()


@pytest.fixture(scope="module")
def _window():
    """One window for the module.

    Building a Tk root per test exhausts Tk part-way through the file -- it
    starts raising `invalid command name "tcl_findLibrary"` and every
    remaining test errors for a reason that has nothing to do with the window.
    Each test still gets a clean transcript and a runtime back at its
    defaults, which is what they were really relying on.
    """
    window = _build_window()
    try:
        yield window
    finally:
        _release(window)


@pytest.fixture
def gui(_window):
    yield _window
    if _window._closed:
        return
    # Put back anything a test may have changed, so the next one starts level.
    try:
        _window.chat.configure(state="normal")
        _window.chat.delete("1.0", "end")
        _window.chat.configure(state="disabled")
        _window._transcript.seek(0)
        _window._transcript.truncate()
        _window.console.clear()
        _window.input.delete("1.0", "end")
        # Several dispatch tests replace _work with a stub, so production's
        # finally block never removes its progress/stream widgets. Complete
        # that UI lifecycle before measuring a later test's idle layout.
        _window._finish_reply()
        _window._stream_preview.publish("clear")
        _window._stream_preview.drain()
        _window._close_chat_find()
        _window.runtime.authority.set_privacy_mode(False)
        _window.show_view("chat")
        _window.root.withdraw()
        _window.root.update()
    except Exception:
        pass


@pytest.fixture
def own_gui():
    """A window of its very own, for tests that close or destroy it."""
    window = _build_window()
    try:
        yield window
    finally:
        _release(window)


def _pump(window, times: int = 10) -> None:
    for _ in range(times):
        window.root.update()
        time.sleep(0.01)


def test_status_paint_never_probes_context_hardware(gui, monkeypatch):
    def forbidden():
        raise AssertionError("hardware probe on the UI thread")
    monkeypatch.setattr(gui.runtime.context_budget, "status", forbidden)
    monkeypatch.setattr(gui, "_slow", {"context_budget": {"machine_ceiling": 8192}})
    gui._paint_metrics()
    assert gui.brain_card_vars["context"][0].get() == "8k"


def test_chat_image_research_action_opens_and_reuses_its_draft(gui):
    gui.root.deiconify()
    gui._research_image()
    gui.root.update()
    research = gui._research_studio
    panel = research._image_research
    try:
        panel.topic.insert(0, 'Research topic draft')
        panel.withdraw()
        gui._research_image()
        gui.root.update()
        assert research._image_research is panel
        assert panel.topic.get() == 'Research topic draft'
        assert panel.search_button.instate(['disabled'])
    finally:
        research.close()


def test_brain_dimensions_switch_releases_space_and_restores_timeline(gui):
    gui.show_view("operator")
    gui._set_brain_dimensions("3D")
    assert gui.brain_graph.view_mode == "3D"
    assert gui.brain_title.cget("text") == "Brain 3D"
    assert not gui.brain_timeline.winfo_manager()
    assert not gui._brain_time_controls.winfo_manager()
    assert not gui.brain_graph.evidence_on and not gui.brain_graph.heat
    gui._set_brain_dimensions("5D")
    assert gui.brain_title.cget("text") == "Brain 5D"
    assert gui.brain_timeline.winfo_manager() == "pack"
    assert gui._brain_time_controls.winfo_manager() == "pack"
    assert gui.brain_graph.evidence_on
    gui.brain_layer.set("knowledge")
    gui.brain_graph.set_layer("knowledge")
    gui.brain_hops.set("2 hops")
    gui._set_brain_hops()
    assert gui.brain_graph.connection_hops == 2
    assert gui.brain_graph.active in gui.brain_graph._positions
    gui.brain_layer.set("All layers")
    gui.brain_graph.set_layer("All layers")


def test_customer_chat_menu_runs_brief_and_work_through_the_real_chat(gui, tmp_path, monkeypatch):
    monkeypatch.setattr(gui.runtime.revenue, 'output', tmp_path / 'customer-chat')
    monkeypatch.setattr(gui.runtime.revenue.ledger, 'output', tmp_path / 'customer-billing')
    monkeypatch.setattr(gui.runtime.session, 'active_customer', None)
    def wait_reply():
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            gui.root.update()
            if gui._working_since is None:
                return
            time.sleep(.01)
        assert gui._working_since is None
    labels = {gui.chat_actions_menu.entrycget(index, 'label'): index
              for index in range(gui.chat_actions_menu.index('end') + 1)
              if gui.chat_actions_menu.type(index) == 'command'}
    gui.chat_actions_menu.invoke(labels['Understand a customer'])
    assert 'Customer NAME:' in gui.input.get('1.0', 'end')
    gui.use_example('Customer GUI-TEST Acme: We need content for our newsletter.')
    gui.send()
    wait_reply()
    assert 'Saved customer brief' in gui.chat.get('1.0', 'end')
    gui.chat_actions_menu.invoke(labels['Prepare next customer action'])
    assert gui.input.get('1.0', 'end-1c') == 'Decide and do the next step'
    gui.send()
    wait_reply()
    text = gui.chat.get('1.0', 'end')
    assert 'prepared locally' in text and 'Downloadable bundle:' in text
    assert gui.runtime.session.active_customer == 'GUI-TEST Acme'
    assert 'Error\n' not in text
    gui.use_example('Their audience is Local shop owners')
    gui.send()
    wait_reply()
    assert gui.runtime.customers.decide('GUI-TEST Acme')['facts']['audience'] == 'Local shop owners'
    gui.chat_actions_menu.invoke(labels['Update selected customer'])
    assert 'Customer update:' in gui.input.get('1.0', 'end')
    gui.chat_actions_menu.invoke(labels['Prepare customer deliverable'])
    assert gui.input.get('1.0', 'end-1c') == 'Prepare their deliverable'
    gui.chat_actions_menu.invoke(labels['Prepare customer quote'])
    assert 'AMOUNT CURRENCY' in gui.input.get('1.0', 'end')
    gui.use_example('Prepare a quote for this customer for 15 USD')
    gui.send()
    wait_reply()
    quote = next(doc for doc in gui.runtime.revenue.ledger.invoices('quotes')['documents'] if doc['client'] == 'GUI-TEST Acme')
    assert quote['number'] in gui.chat.get('1.0', 'end') and quote['total'] == '15.00 USD'
    gui.chat_actions_menu.invoke(labels['Invoice customer quote'])
    assert 'Q-YEAR-NUMBER' in gui.input.get('1.0', 'end')
    gui.use_example('Prepare an invoice for this customer from quote ' + quote['number'])
    gui.send()
    wait_reply()
    invoice = next(doc for doc in gui.runtime.revenue.ledger.invoices()['documents'] if doc['client'] == 'GUI-TEST Acme')
    assert invoice['number'] in gui.chat.get('1.0', 'end') and invoice['status'] == 'unpaid'


def test_preload_admission_does_not_block_the_interface(gui, monkeypatch):
    called = threading.Event()
    release = threading.Event()
    threads = []
    def preload():
        threads.append(threading.get_ident())
        called.set()
        release.wait(2)
    monkeypatch.setattr(gui._resource_controller, "preload_brain", preload)
    try:
        gui._preload_brain()
        assert called.wait(1)
        assert len(threads) == 1 and threads[0] != gui._ui_thread_id
        gui.input.insert("1.0", "Still responsive")
        assert gui.input.get("1.0", "end-1c") == "Still responsive"
    finally:
        release.set()


def test_reply_controls_cancel_without_stopping_voice(gui, monkeypatch):
    calls = []
    monkeypatch.setattr(gui.runtime.agent, "cancel_current", lambda: calls.append("cancel"))
    monkeypatch.setattr(gui.runtime.voice.tts, "stop", lambda: calls.append("voice"))
    gui._working_since = time.monotonic()
    gui._generation_cancel = threading.Event()
    gui._paint_reply_controls()
    assert "disabled" in gui.send_button.state()
    assert "disabled" not in gui.reply_stop_button.state()
    gui._cancel_reply()
    assert gui._generation_cancel.is_set()
    assert calls == ["cancel"]
    assert gui.reply_stop_button.cget("text") == "Stopping…"
    gui._finish_reply()
    assert "disabled" not in gui.send_button.state()
    assert "disabled" in gui.reply_stop_button.state()


def test_ui_queue_yields_after_time_budget_and_keeps_remaining_callbacks(gui, monkeypatch):
    import queue
    callbacks = []
    now = [10.0]
    pending = queue.Queue()
    def slow_callback(value):
        callbacks.append(value)
        now[0] += .010
    for i in range(3):
        pending.put((slow_callback, (i,)))
    monkeypatch.setattr(gui, "_ui_queue", pending)
    monkeypatch.setattr(gui, "_schedule", lambda *args: None)
    monkeypatch.setattr("app.gui.time.monotonic", lambda: now[0])
    gui._drain_ui()
    assert callbacks == [0]
    assert pending.qsize() == 2
    gui._drain_ui()
    gui._drain_ui()
    assert callbacks == [0, 1, 2]


def test_copy_chat_contains_voice_turns_and_unicode(gui, monkeypatch):
    copied = []
    monkeypatch.setattr(gui.root, "clipboard_clear", lambda: None)
    monkeypatch.setattr(gui.root, "clipboard_append", copied.append)
    gui._output_event("voice_transcript", "You: Speak with me مرحبا 🫡")
    gui._voice_response(SimpleNamespace(display_response="I'm here.\nSecond line."))
    _pump(gui)
    gui.copy_chat()
    assert "Speak with me مرحبا 🫡" in copied[-1]
    assert "HAYAT" in copied[-1] and "I'm here.\nSecond line." in copied[-1]
    assert copied[-1].index("Speak with me") < copied[-1].index("I'm here.")
    assert gui.chat.cget("state") == "disabled"


def test_new_assistant_reply_preserves_reading_position_and_counts_unread(gui, monkeypatch):
    seen = []
    restored = []
    monkeypatch.setattr(gui.chat, "yview", lambda *args: restored.append(args) if args else (0.1, 0.5))
    monkeypatch.setattr(gui.chat, "see", seen.append)
    gui._append("assistant", "HAYAT\nHere is the answer.")
    assert seen == [] and restored
    assert gui._unread_messages == 1
    assert gui.latest_button.cget("text") == "Latest ↓ (1 new)"


def test_latest_button_returns_to_newest_chat_reply(gui, monkeypatch):
    seen = []
    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view("operator")
    finally:
        gui._operator_suppress_auto_refresh = False
    gui._open_chat_find()
    monkeypatch.setattr(gui.chat, "see", seen.append)
    gui.latest_button.invoke()
    assert gui.current_view == "chat"
    assert gui.chat.winfo_manager() == "pack"
    assert not gui._welcome.winfo_manager()
    assert not gui._chat_find_open
    assert seen == ["end"]


def test_copy_selection_is_exact_and_survives_focus_change(gui, monkeypatch):
    copied = []
    monkeypatch.setattr(gui.root, "clipboard_clear", lambda: None)
    monkeypatch.setattr(gui.root, "clipboard_append", copied.append)
    gui._append("assistant", "HAYAT\nFirst line\nSecond line")
    gui.chat.tag_add("sel", "2.0", "3.end")
    gui.copy_button.focus_set()
    gui.copy_selection()
    assert copied == ["First line\nSecond line"]
    assert not int(gui.chat.cget("exportselection"))


def test_find_in_chat_highlights_and_navigates_visible_messages(gui):
    gui._append("user", "You\nRemember this comet")
    gui._append("assistant", "HAYAT\nThe comet is bright")
    gui._open_chat_find()
    gui._chat_find_var.set("comet")
    gui._refresh_chat_find()
    assert len(gui._chat_find_matches) == 2
    assert gui._chat_find_status.get().startswith("1 of 2")
    gui._step_chat_find(1)
    assert gui._chat_find_status.get().startswith("2 of 2")
    assert gui.chat.tag_ranges("find_active")
    gui._close_chat_find()
    assert not gui._chat_find_bar.winfo_manager()
    assert not gui.chat.tag_ranges("find_active")


def test_copy_and_save_preserve_messages_trimmed_from_display(gui, monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "MAX_TRANSCRIPT_LINES", 8)
    gui._append("user", "You\nEARLIEST MESSAGE")
    for i in range(12):
        gui._append("assistant", f"HAYAT\nMessage {i}")
    assert "EARLIEST MESSAGE" not in gui.chat.get("1.0", "end")
    copied = []
    monkeypatch.setattr(gui.root, "clipboard_clear", lambda: None)
    monkeypatch.setattr(gui.root, "clipboard_append", copied.append)
    gui.copy_chat()
    assert "EARLIEST MESSAGE" in copied[-1] and "Message 11" in copied[-1]
    path = tmp_path / "conversation.txt"
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", lambda **kw: str(path))
    gui.save_transcript()
    assert path.read_text(encoding="utf-8") == copied[-1]
    monkeypatch.setattr(gui.runtime, "conversation", None)
    gui.clear_chat()
    assert "EARLIEST MESSAGE" not in gui._transcript_text()


def test_voice_start_is_background_deduplicated_and_reports_failure(gui, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def start():
        calls.append(threading.current_thread().name)
        entered.set()
        release.wait(3)
        return False

    voice = gui.runtime.voice
    monkeypatch.setattr(voice, "start_background", start)
    monkeypatch.setattr(voice, "status", lambda: {"enabled": False, "last_error": "No microphone"})
    monkeypatch.setattr(voice, "set_mode", lambda mode: None)
    try:
        gui._continuous_voice()
        assert entered.wait(2)
        gui._continuous_voice()
        assert len(calls) == 1 and calls[0] != threading.current_thread().name
    finally:
        release.set()
    _wait_until(gui, lambda: not gui._voice_action_busy, "voice result")
    assert "No microphone" in gui._transcript_text()
    assert "conversation is active" not in gui._transcript_text()


def _wait_until(gui, predicate, description, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        gui.root.update_idletasks()
        # A continuously animated canvas can keep update() draining events
        # for seconds. Pump a bounded batch so the deadline stays meaningful.
        for _ in range(20):
            if not gui.root.tk.dooneevent(tkinter._tkinter.DONT_WAIT):
                break
        if predicate():
            return
        time.sleep(.01)
    pytest.fail(f"Timed out waiting for {description() if callable(description) else description}")


def _wait_for_window(gui, window, *, focus=False):
    """Wait for the window manager to map and settle a real top-level."""
    previous = (window.winfo_width(), window.winfo_height())
    stable_since = time.monotonic()

    def settled():
        nonlocal previous, stable_since
        geometry = (window.winfo_width(), window.winfo_height())
        visible = window.winfo_viewable() and min(geometry) > 1
        if not visible or geometry != previous:
            previous, stable_since = geometry, time.monotonic()
            return False
        return time.monotonic() - stable_since >= .1

    _wait_until(gui, settled, lambda: f"{window.title()} to become visible and settle "
                f"(state={window.state()}, visible={window.winfo_viewable()}, "
                f"geometry={window.geometry()}, page={getattr(window, 'current_page', '')})")
    if focus:
        window.focus_force()
        _wait_until(gui, lambda: window.focus_get() is not None and
                    window.focus_get().winfo_toplevel() == window,
                    f"{window.title()} to receive keyboard focus")


# ------------------------------------------------------------- it builds


def test_the_window_builds_and_survives_its_own_scheduled_callbacks(gui):
    """Construction schedules work with after(); if any of it raises, the
    window is broken in a way only a person would notice."""
    _pump(gui, 25)
    assert gui.root.title().startswith("HAYAT")


def test_it_takes_over_approvals_so_prompts_appear_in_the_window(gui):
    """Otherwise an approval would be printed to a console nobody is reading
    while the window sits there looking idle."""
    _pump(gui, 25)
    assert gui.runtime.registry.approval_callback == gui._approval


def test_messages_reach_the_transcript(gui):
    gui._append("system", "a message for the probe")
    gui.root.update()
    assert "a message for the probe" in gui.chat.get("1.0", "end")


def test_the_transcript_is_not_editable_by_typing(gui):
    """The chat log is a record; typing into it would let the window disagree
    with what actually happened."""
    assert str(gui.chat.cget("state")) == "disabled"


def test_status_shows_real_measurements(gui):
    """The health line is filled by a background sampler, so this waits for
    it rather than reading before it has run."""
    for _ in range(200):
        gui._tick()
        gui.root.update()
        if "CPU" in gui.health_var.get():
            break
        time.sleep(0.02)

    health = gui.health_var.get()
    assert "CPU" in health and "RAM" in health, f"health line never populated: {health!r}"
    assert gui.activity_var.get(), "the activity line should always say something"


def test_activity_line_restores_phase_countdown_and_elapsed_time():
    from app.gui import activity_progress, format_activity_countdown

    status = format_activity_countdown({"running": [{
        "kind": "research", "label": "web_search", "seconds": 12.5,
        "remaining_seconds": 47.5,
    }]}, None)
    assert status.startswith("Researching:")
    assert "left" in status and "12.5s elapsed" in status

    thinking = format_activity_countdown({"running": []}, 90.0, now=100.0,
                                         fallback_estimate=30.0)
    assert thinking == "Thinking · ~20s left · 10.0s elapsed"
    assert activity_progress({"running": [{
        "seconds": 25.0, "remaining_seconds": 75.0,
    }]}, None) == 25.0
    assert activity_progress({"running": []}, None) == 0.0


# ------------------------------------------------- background work discipline


def test_a_turn_is_dispatched_to_a_worker_not_the_ui_thread(gui, monkeypatch):
    """The bug that freezes a window: a slow turn on the Tk thread stops
    redraw, so the app looks hung while it is working correctly.

    Checked by watching where _work is dispatched rather than by running a
    full turn, because completing one needs root.after() from the worker and
    that requires a live mainloop, which a test harness does not have.
    """
    ui_thread = threading.current_thread()
    started: dict[str, Any] = {}
    finished = threading.Event()

    def observe(text):
        started["thread"] = threading.current_thread()
        finished.set()

    monkeypatch.setattr(gui, "_work", observe)
    gui.input.insert("1.0", "do something")
    gui.send()

    assert finished.wait(timeout=5), "the turn was never dispatched"
    assert started["thread"] is not ui_thread, "a turn on the UI thread freezes the window"


def test_the_input_box_is_cleared_when_a_turn_is_sent(gui, monkeypatch):
    """Otherwise the next message carries the previous one along with it."""
    monkeypatch.setattr(gui, "_work", lambda text: None)
    gui.input.insert("1.0", "first message")
    gui.send()
    assert gui.input.get("1.0", "end").strip() == ""


def test_an_empty_message_is_not_sent(gui, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(gui, "_work", lambda text: sent.append(text))
    gui.input.insert("1.0", "   \n  ")
    gui.send()
    assert sent == []


def test_approval_waits_for_an_answer_rather_than_assuming_one(gui):
    """A dialog that never gets answered must time out into a refusal, not a
    silent yes."""
    import inspect

    source = inspect.getsource(gui._approval)
    assert "result.wait" in source, "it must wait for the human"
    assert "timeout" in source, "and give up rather than block forever"
    assert '"value": False' in source, "an unanswered prompt must default to no"


# ------------------------------------------------------------- shutting down


def test_closing_releases_the_runtime_once(own_gui):
    """A window that closes without releasing the runtime leaves the event
    writer, the learner and any owned engine running after it is gone.

    Takes a window of its own: closing the shared one would leave every test
    after it working against a destroyed interpreter.
    """
    closed: list[int] = []
    original = own_gui.runtime.close
    own_gui.runtime.close = lambda: closed.append(1) or original()

    own_gui.close()
    own_gui.close()  # a second close must not raise or double-release

    assert closed == [1]
    assert own_gui._closed is True


# ------------------------------------------------ every capability reachable


def test_every_declared_control_exists_and_is_wired(gui):
    """The controls are declared as data so this can iterate them.

    A button whose handler was renamed away is a dead button, and the window
    gives no sign of it until Mohsin presses it and nothing happens.
    """
    from app.gui import HayatGUI

    for label, method, _group in HayatGUI.CONTROLS:
        assert callable(getattr(gui, method, None)), f"{label} -> {method} is missing"
        assert label in gui.buttons, f"{label} was never placed in the window"


def test_operator_center_renders_every_brain_node_and_team_state(gui):
    from app.brain_mesh import BrainMesh

    # The hidden test harness pumps update() but does not own a real Tk
    # mainloop, so paint the completed read-only probe on the UI thread. The
    # production button dispatches this same probe off-thread.
    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view("operator")
    finally:
        gui._operator_suppress_auto_refresh = False
    gui._paint_operator(gui.runtime.brain_mesh.self_check())
    gui.root.update()

    assert gui.current_view == "operator"
    assert set(gui.operator_nodes) == {node.node_id for node in BrainMesh.NODES}
    assert "team PASS" in gui.operator_summary.get()
    assert "Models cannot approve tools" in gui.operator_summary.get()
    assert all(item["state"].get() != "waiting" for item in gui.operator_nodes.values())


def test_operator_cards_reflow_and_open_a_node_at_the_start_of_its_explanation(gui):
    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view("operator")
    finally:
        gui._operator_suppress_auto_refresh = False
    gui._toggle_operator_layout()
    try:
        gui._layout_operator_cards(440)
        assert gui.operator_nodes["intent"]["card"].grid_info()["row"] == 1
        gui._layout_operator_cards(700)
        assert gui.operator_nodes["intent"]["card"].grid_info()["column"] == 1
        gui._layout_operator_cards(1000)
        assert gui.operator_nodes["context"]["card"].grid_info()["column"] == 2

        gui.brain_node_detail.set("Old details\n" * 100)
        gui.root.update()
        gui.brain_detail_text.yview_moveto(1.0)
        gui.operator_nodes["context"]["inspect"].invoke()
        gui.root.update()
        assert gui.brain_graph.active == "context"
        assert gui._brain_row.winfo_manager() == "pack"
        assert gui.brain_detail_text.get("1.0", "1.end").startswith("Context Budget")
        assert gui.brain_detail_text.yview()[0] == 0.0
    finally:
        if gui._operator_cards.winfo_manager():
            gui._toggle_operator_layout()
        gui.show_view("chat")


def test_3d_navigation_rotates_zooms_and_opens_a_view(gui):
    from types import SimpleNamespace

    # The 3D globe lives in the welcome overlay; make it visible so its canvas is laid out and node positions
    # are real (the shared module window may arrive here in any view from a previous test).
    gui.root.geometry('1280x900-4000-4000')
    gui.root.deiconify()
    gui.show_view("chat")
    if not gui._welcome.winfo_manager():
        gui._toggle_core()
    scene = gui.depth_nav
    gui.root.update()
    old_angle, old_zoom = scene.angle, scene.zoom
    scene._press(SimpleNamespace(x=80, y=40))
    scene._drag(SimpleNamespace(x=120, y=40))
    scene._release(SimpleNamespace(x=120, y=40))
    assert scene.angle != old_angle
    scene._wheel(SimpleNamespace(delta=120))
    assert scene.zoom > old_zoom

    opened = []
    original = scene.on_open
    original_motion = scene.motion
    try:
        scene.on_open = opened.append
        # Capability cards are painted above the globe. Bring Chat to the front
        # before clicking, rather than targeting a node hidden behind a card.
        scene.set_motion(False)
        scene.face('chat')
        x, y, _z = scene._positions["chat"]
        assert scene._hit(x, y) == 'chat'
        scene._press(SimpleNamespace(x=x, y=y))
        scene._release(SimpleNamespace(x=x, y=y))
        assert opened == ["chat"]
        scene.select("operator")
        assert scene.active == "operator"
    finally:
        scene.on_open = original
        scene.select(gui.current_view)
        scene.set_motion(original_motion)


def test_operator_probes_run_off_the_ui_thread(gui):
    import inspect

    source = inspect.getsource(gui._run_mesh_task)
    assert "threading.Thread(" in source and "daemon=True" in source
    # _post is the single marshalling helper: it hands the result to the Tk
    # thread and drops it if the window has closed. It replaced thirteen bare
    # root.after(0, ...) calls whose callbacks could outlive the interpreter.
    assert "self._post(" in source


def test_operator_controls_use_the_shared_runtime_not_a_gui_backdoor(gui):
    assert gui.runtime.brain_mesh.runtime is gui.runtime
    assert getattr(gui.runtime.registry, "authority", None) is gui.runtime.authority
    assert {"brain_mesh_status", "brain_mesh_self_check", "brain_mesh_benchmark"} <= set(
        gui.runtime.registry.names()
    )


def test_image_styles_shown_by_the_gui_are_real_generator_styles(gui):
    from app.models.diffusion import PROMPT_STYLES

    assert set(gui.IMAGE_STYLES) == set(PROMPT_STYLES)


def test_the_sensors_are_shown_rather_than_only_askable(gui):
    """"Is the camera on?" used to be answerable only by trusting a sentence.
    A light you can look at is a different kind of answer."""
    gui._refresh_sensors()
    gui._paint_sensors()
    gui.root.update()

    for sensor in ("camera", "microphone", "screen", "clipboard", "vision", "browser"):
        assert sensor in gui.sensor_vars, sensor
        assert gui.sensor_vars[sensor].get().strip(), f"{sensor} shows nothing"


def test_privacy_mode_greys_the_sensing_lights(gui):
    """A light still reading "live" while privacy is on is a lie."""
    gui._refresh_sensors()
    gui.runtime.authority.set_privacy_mode(True)
    try:
        gui._paint_sensors()
        gui.root.update()
        for sensor in ("camera", "microphone", "screen", "clipboard"):
            assert gui.sensor_labels[sensor].cget("fg") == gui.GOLD, sensor
    finally:
        gui.runtime.authority.set_privacy_mode(False)


def test_the_tick_stays_far_inside_its_own_interval(gui):
    """Measured, because this was got wrong once.

    camera.status() takes 610ms on this machine and clipboard.status() 207ms.
    Reading them on the 250ms tick meant the window owed the next refresh
    before finishing the last, and it stopped rather than stuttered.
    """
    import time

    gui._refresh_sensors()  # warm the cache off-thread work would fill
    worst = 0.0
    for _ in range(15):
        started = time.perf_counter()
        gui._tick()
        worst = max(worst, (time.perf_counter() - started) * 1000)
    assert worst < 60, f"a UI tick took {worst:.0f}ms of its 250ms budget"


def test_a_missing_capability_is_explained_not_thrown(gui):
    """describe_screen registers only when a vision model is reachable.
    "KeyError: 'describe_screen'" says nothing about what to switch on."""
    answer = gui._tool("describe_screen")
    if "describe_screen" not in gui.runtime.registry.names():
        assert "vision model" in answer or "not registered" in answer
        assert "KeyError" not in answer


def test_a_slow_action_runs_off_the_ui_thread(gui):
    """Opening a camera or describing an image takes seconds. On the Tk loop
    that is a frozen window, which reads as a hung assistant."""
    import inspect

    source = inspect.getsource(gui._background)
    assert "threading.Thread(" in source and "daemon=True" in source
    assert "self._post(" in source  # and the result comes back onto the loop


def test_the_window_uses_mohsins_palette(gui):
    """Olive green, gold, royal blue, black and white -- assigned by meaning."""
    from app.gui import HayatGUI
    from app.gui_theme import tokens

    # A near-black surface, while allowing the owner's palette to evolve.
    assert max(int(tokens('Dark')['BLACK'][i:i + 2], 16) for i in (1, 3, 5)) <= 18
    assert HayatGUI.BLACK == tokens(gui._theme_name)['BLACK']
    from app.gui_glass import GLASS_KEY, live_glass_active

    # In live Glass the backdrop is the colour Windows frosts; otherwise it is the theme's black.
    assert gui.root.cget("bg") == (GLASS_KEY if live_glass_active() else HayatGUI.BLACK)
    # Gold is HAYAT, royal blue is Mohsin.
    assert gui.chat.tag_cget("user", "foreground") == HayatGUI.ROYAL_SOFT
    assert gui.chat.tag_cget("report", "foreground") == HayatGUI.GOLD_SOFT
    # An error must not blend into the theme.
    assert gui.chat.tag_cget("error", "foreground") == HayatGUI.ALERT
    assert HayatGUI.ALERT not in {HayatGUI.GOLD, HayatGUI.ROYAL, HayatGUI.OLIVE}


def test_neural_command_deck_and_owner_learning_controls_are_visible(gui):
    assert set(gui.brain_card_vars) == {
        "memory", "knowledge", "context", "prediction", "skills",
        "operators", "voice", "speed",
    }
    assert set(gui.media_rows) == {"speech", "capture", "vision", "operator"}
    assert set(gui.mindstream_rows) == {"focus", "learning", "prediction", "verification"}
    assert set(gui.mindstream_pages) == {"feed", "learning", "prediction"}
    assert set(gui.mindstream_tab_buttons) == {"feed", "learning", "prediction"}
    assert gui.mindstream_feed.winfo_exists()
    assert gui.mindstream_pause_button.winfo_exists()
    assert gui.mindstream_progress.winfo_exists()
    assert gui.focus_mode_button.winfo_exists()
    assert callable(gui._toggle_focus_mode)
    assert gui.learning_toggle_button.winfo_exists()
    assert callable(gui._toggle_background_learning)
    assert callable(gui._learn_next_now)
    assert callable(gui._copy_remote_login)
    assert callable(gui._show_different_network)
    assert callable(gui._deep_recall_search)
    assert callable(gui._multi_frame_look)
    assert callable(gui._adapt_learning)


def test_the_transcript_does_not_grow_without_bound(gui):
    """A Tk text widget keeps everything ever put into it. A window left open
    all day gets slow to scroll and then slow to draw. The conversation itself
    lives in memory; this is only the view of it."""
    for index in range(gui.MAX_TRANSCRIPT_LINES + 500):
        gui._append("system", f"line {index}")
    gui.root.update()

    lines = int(gui.chat.index("end-1c").split(".")[0])
    assert lines <= gui.MAX_TRANSCRIPT_LINES + 2
    # The newest is what survives, not the oldest.
    assert f"line {gui.MAX_TRANSCRIPT_LINES + 499}" in gui.chat.get("end-3l", "end")


def test_the_trace_panel_is_trimmed_too(gui):
    for index in range(gui.MAX_TRACE_LINES + 200):
        gui._append_trace(f"event {index}")
    gui.root.update()
    assert int(gui.trace.index("end-1c").split(".")[0]) <= gui.MAX_TRACE_LINES + 2


def test_the_shortcuts_are_bound_to_real_handlers(gui):
    from app.gui import HayatGUI

    for sequence, method in HayatGUI.SHORTCUTS:
        assert callable(getattr(gui, method, None)), f"{sequence} -> {method} is missing"
        assert gui.root.bind_all(sequence), f"{sequence} was never bound"


def test_features_and_activity_are_reachable_in_focus_mode_and_cover_the_live_registry(gui):
    gui.root.geometry('1180x760-4000-4000')
    gui.root.deiconify()
    try:
        _wait_for_window(gui, gui.root)
        gui._toggle_focus_mode()
        gui.features_button.invoke()
        feature = gui._features_window
        _wait_for_window(gui, feature)
        assert {row.tool.name for row in feature.inventory if row.tool} == set(gui.runtime.registry.names())
        feature.query.set('intelligence_dashboard')
        gui.root.update()
        assert list(feature.shown) == ['tool:intelligence_dashboard']
        assert feature.current.tool.name == 'intelligence_dashboard'
        feature.destroy()
        gui.activity_button.invoke()
        activity = gui._activity_window
        _wait_for_window(gui, activity)
        gui._paint_transparency()
        assert 'Camera:' in gui.transparency_state.get()
        assert 'Public feeds:' in gui.transparency_state.get()
        assert gui.features_button.winfo_viewable() and gui.activity_button.winfo_viewable()
        assert gui.transparency_stop.winfo_viewable()
        activity.close()
    finally:
        if gui._focus_mode:
            gui._toggle_focus_mode()
        for name in ('_features_window', '_activity_window'):
            window = getattr(gui, name, None)
            if window is not None and window.winfo_exists():
                window.close() if hasattr(window, 'close') else window.destroy()
        gui.root.withdraw()


def test_focus_mode_expands_the_workspace_and_restores_both_rails(gui):
    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    try:
        gui.show_view("chat")
        gui.root.update()
        ordinary_width = gui._centre.winfo_width()
        assert gui._sidebar.winfo_ismapped()
        assert gui.right_rail.winfo_ismapped()

        gui._toggle_focus_mode()
        gui.root.update()
        assert gui._focus_mode is True
        assert not gui._sidebar.winfo_ismapped()
        assert not gui.right_rail.winfo_ismapped()
        assert gui._centre.winfo_width() > ordinary_width
        assert gui.focus_mode_button.cget("text") == "Restore Rails"
        assert gui.current_view == "chat"

        gui._toggle_focus_mode()
        gui.root.update()
        assert gui._focus_mode is False
        assert gui._sidebar.winfo_ismapped()
        assert gui.right_rail.winfo_ismapped()
        assert gui.preview.winfo_ismapped()
        assert gui.focus_mode_button.cget("text") == "Focus Mode"
        assert gui.current_view == "chat"
    finally:
        if gui._focus_mode:
            gui._toggle_focus_mode()
        gui.root.withdraw()


def test_the_camera_view_opens_and_closes_with_the_window(gui, monkeypatch):
    """Owned by the GUI, so closing the main window takes it along rather than
    leaving a live preview orphaned on screen."""
    opened = {}

    class FakeView:
        def __init__(self, *args, **kwargs):
            opened["made"] = True
            self.alive = True
            self.window = gui.root

        def close(self):
            self.alive = False
            opened["closed"] = True

    monkeypatch.setattr("app.camera_view.CameraView", FakeView)
    gui.open_camera_view()
    assert opened.get("made")

    gui.open_camera_view()  # a second press lifts rather than making another
    assert gui.camera_view is not None

    gui._close_camera_view()
    assert opened.get("closed") and gui.camera_view is None


# --------------------------------------------- the layout, and what it shows


def test_every_panel_is_actually_on_screen(gui):
    """Which panels appeared used to depend on pack order rather than on
    anything meaningful. Packing the expanding centre before the right column
    made Tk hand it every remaining pixel, and the whole right side -- camera,
    sensors, dials, console, models -- silently never drew.
    """
    # Laid out for real, but far off any monitor. A withdrawn window never
    # negotiates geometry for its expanding children -- the transcript and the
    # input box both come back one pixel tall -- so "did this panel get any
    # space" is not a question that means anything until the window is mapped.
    # Placing it at -4000 gets a genuine layout with nothing on screen.
    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    try:
        # Diagnostics panels open folded; this test is about layout, so every panel is opened first.
        gui._folds.unfold_all()
        gui.root.update_idletasks()
        gui.root.update()

        for name, widget in (("camera preview", gui.preview), ("console", gui.console),
                             ("models", gui.model_chips), ("input", gui.input),
                             ("transcript", gui.chat)):
            assert widget.winfo_ismapped(), f"{name} is not on screen"
            assert widget.winfo_height() > 10, f"{name} was squeezed to nothing"
        for key, gauge in gui.gauges.items():
            assert gauge.winfo_ismapped(), f"the {key} dial is not on screen"
    finally:
        gui.root.withdraw()


def test_compact_workspace_keeps_chat_readable_and_overview_does_not_squeeze_it(gui):
    gui.root.geometry("1180x700-4000-4000")
    gui.root.deiconify()
    try:
        gui.show_view("chat")
        _wait_for_window(gui, gui.root)
        assert gui.chat.winfo_height() >= 120
        gui._paint_transparency()
        assert int(gui._transparency_state_label.cget("height")) == 2
        assert gui._transparency_state_tip.text == gui.transparency_state.get()
        # A queued event describing the former wide/tall size must not restore
        # its rails and status rows over the actual short-window layout.
        gui._on_window_resize(SimpleNamespace(widget=gui.root, width=1540, height=920))
        gui.root.update_idletasks()
        assert gui._layout_state == (True, True)
        assert gui.chat.winfo_height() >= 120
        assert gui.chat.winfo_width() >= 700
        assert not gui.right_rail.winfo_ismapped()
        before = gui.chat.winfo_width()
        gui._toggle_overview()
        gui.root.update()
        assert gui.resource_charts.winfo_ismapped()
        assert gui.chat.winfo_width() == before
        gui._toggle_overview()
        gui._toggle_tools_drawer()
        gui.root.update()
        assert gui._tools_window.winfo_ismapped()
        assert gui.chat.winfo_height() >= 120
        assert gui.buttons["Hands-free"].winfo_ismapped()
    finally:
        if gui._tools_expanded:
            gui._toggle_tools_drawer()
        gui.root.geometry("1540x920-4000-4000")
        gui.root.update()
        gui.root.withdraw()


def test_neural_workspace_toggle_preserves_conversation_and_unsent_draft(gui):
    gui.show_view("chat")
    gui._append("assistant", "HAYAT\nKeep this answer.")
    gui.input.delete("1.0", "end")
    gui.input.insert("1.0", "Keep this draft.")
    transcript = gui.chat.get("1.0", "end-1c")
    assert not gui._welcome.winfo_manager()
    gui._toggle_core()
    assert gui._welcome.winfo_manager() == "place"
    gui._append("system", "Background service is ready.")
    assert gui._welcome.winfo_manager() == "place"
    transcript = gui.chat.get("1.0", "end-1c")
    gui._toggle_core()
    assert not gui._welcome.winfo_manager()
    assert gui.chat.get("1.0", "end-1c") == transcript
    assert gui.input.get("1.0", "end-1c") == "Keep this draft."
    gui._toggle_core()
    gui._append("assistant", "HAYAT\nA new answer.")
    assert not gui._welcome.winfo_manager()
    assert gui.core_button.cget("text") == "3D workspace"
    gui.input.delete("1.0", "end")


def test_capability_cards_route_to_real_handlers(gui, monkeypatch):
    opened = []
    monkeypatch.setattr(gui, "_open_control_studio", opened.append)
    monkeypatch.setattr(gui, "_open_vision_controls", lambda: opened.append("vision"))
    monkeypatch.setattr(gui, "_research", lambda: opened.append("research"))
    monkeypatch.setattr(gui, "show_view", opened.append)
    for key, *_rest in gui.depth_nav.CAPABILITIES:
        gui._open_capability(key)
    assert opened == ["voice", "vision", "research", "task_graph", "tasks", "toolbox"]


def test_vision_workspace_opens_without_starting_capture_and_can_reopen(gui):
    before = gui._camera_on
    gui._open_vision_controls()
    gui.root.update()
    window = gui._vision_window
    try:
        assert window.winfo_exists()
        assert gui._camera_on == before
        assert window.grab_current() is None
        window.withdraw()
        gui._open_vision_controls()
        assert gui._vision_window is window
    finally:
        window.withdraw()


def test_brain_inspection_gives_graph_space_and_restores_draft(gui):
    gui.input.delete("1.0", "end")
    gui.input.insert("1.0", "Draft survives brain inspection")
    gui.show_view("operator")
    assert gui._inspection_active
    assert not gui._compose_surface.winfo_manager()
    assert gui.brain_range_buttons[900].master is not gui.brain_replay_button.master
    gui.show_view("chat")
    assert gui._compose_surface.winfo_manager() == "pack"
    assert gui.input.get("1.0", "end-1c") == "Draft survives brain inspection"
    gui.input.delete("1.0", "end")


@pytest.mark.parametrize("view", ["training", "models", "memory", "history", "tasks", "task_graph", "gallery"])
def test_dedicated_workspaces_release_composer_height_and_preserve_the_draft(gui, view):
    gui.input.delete("1.0", "end")
    gui.input.insert("1.0", "Unsent draft stays here")
    gui.show_view(view)
    assert gui._inspection_active and not gui._compose_surface.winfo_manager()
    assert not gui._chat_tabs.winfo_manager()
    gui.draft_status.set("Draft saved")
    assert not gui._draft_label.winfo_manager()
    gui.show_view("chat")
    assert gui._compose_surface.winfo_manager() == "pack"
    assert gui.input.get("1.0", "end-1c") == "Unsent draft stays here"
    gui.input.delete("1.0", "end")
    gui.draft_status.set("")


def test_operator_compact_layout_keeps_graph_and_details_accessible(gui, monkeypatch):
    gui._operator_pane()
    width = [1000]
    monkeypatch.setattr(gui._brain_row, "winfo_width", lambda: width[0])
    gui._layout_brain_workspace()
    assert gui._brain_column.winfo_manager() and gui._brain_inspector.winfo_manager()
    width[0] = 560
    gui._layout_brain_workspace()
    assert gui._brain_column.winfo_manager() and not gui._brain_inspector.winfo_manager()
    gui._select_brain_node("memory")
    assert gui._brain_inspector.winfo_manager() and not gui._brain_column.winfo_manager()
    assert gui.brain_inspector_button.cget("text") == "Show graph"
    gui._toggle_brain_inspector()
    assert gui._brain_column.winfo_manager() and not gui._brain_inspector.winfo_manager()
    focus = gui.brain_timeline.focus_node
    gui._paint_operator({})
    assert gui.brain_timeline.focus_node == focus
    assert gui._brain_column.winfo_manager() and not gui._brain_inspector.winfo_manager()
    gui._brain_tour_step = 0
    gui.brain_node_detail.set("Current tour instructions")
    try:
        gui._paint_operator({})
        assert gui.brain_node_detail.get() == "Current tour instructions"
    finally:
        gui._brain_tour_step = None
    width[0] = 1000
    gui._layout_brain_workspace()
    assert gui._brain_column.winfo_manager() and gui._brain_inspector.winfo_manager()


def test_operator_model_picker_switches_local_geometry_and_exposes_credits(gui, monkeypatch):
    gui._operator_pane()
    from app.brain_asset import MODEL_CREDITS
    captured = []
    monkeypatch.setattr("tkinter.messagebox.showinfo", lambda title, body, **_: captured.append((title, body)))
    gui.brain_geometry.set("Procedural")
    gui.brain_geometry_picker.event_generate("<<ComboboxSelected>>")
    assert not gui.brain_graph.asset_loaded
    gui.brain_geometry.set("Sketchfab brain")
    gui.brain_geometry_picker.event_generate("<<ComboboxSelected>>")
    assert gui.brain_graph.asset_loaded
    row = gui.brain_geometry_picker.master
    credit_button = next(child for child in row.winfo_children()
                         if child.winfo_class() == "TButton" and child.cget("text") == "Model credits")
    credit_button.invoke()
    assert captured == [("3D model credits", MODEL_CREDITS)]


def test_voice_startup_waits_for_this_launch_approval(gui, monkeypatch):
    voice = gui.runtime.voice
    original = voice.config
    starts = []
    monkeypatch.setattr(voice, "start_background", lambda: starts.append(True) or True)
    voice.config = {**original, "enabled": True}
    gui.root.deiconify()
    try:
        _wait_for_window(gui, gui.root)
        gui._offer_voice_startup()
        dialog = gui._voice_startup_dialog
        _wait_for_window(gui, dialog)
        assert starts == []
        dialog.dismiss_button.invoke()
        assert starts == [] and not dialog.winfo_exists()

        gui._offer_voice_startup()
        dialog = gui._voice_startup_dialog
        _wait_for_window(gui, dialog)
        dialog.allow_button.invoke()
        _wait_until(gui, lambda: not gui._voice_action_busy, "voice startup to finish")
        assert starts == [True] and not dialog.winfo_exists()
    finally:
        dialog = getattr(gui, "_voice_startup_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        voice.config = original
        gui.root.withdraw()


def test_voice_studio_controls_fit_and_opening_it_does_not_start_voice(gui):
    enabled = gui.runtime.voice.status()["enabled"] if gui.runtime.voice else False
    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    try:
        _wait_for_window(gui, gui.root)
        gui._open_control_studio("voice")
        studio = gui._control_studio
        studio.update_live({"running": []}, gui.runtime.voice.status() if gui.runtime.voice else {}, False)
        _wait_for_window(gui, studio)
        _wait_until(gui, lambda: studio.voice_toggle.winfo_viewable(), "voice controls to map")
        assert studio.voice_toggle.winfo_ismapped()
        bottom = studio.voice_toggle.winfo_rooty() + studio.voice_toggle.winfo_height()
        assert bottom < studio.winfo_rooty() + studio.winfo_height()
        assert (gui.runtime.voice.status()["enabled"] if gui.runtime.voice else False) == enabled
        assert studio.grab_current() is None
        assert set(studio.voice_mode_buttons) == {"push_to_talk", "continuous", "wake_word"}
        assert "voice" in gui.depth_shortcuts
    finally:
        gui._control_studio.withdraw()
        gui.root.withdraw()


def test_powershell_shortcut_prepares_an_explicit_command(gui):
    gui.input.delete("1.0", "end")
    gui.depth_shortcuts["powershell"].invoke()
    assert gui.input.get("1.0", "end-1c") == "Run PowerShell: "
    gui.input.delete("1.0", "end")
    gui.depth_shortcuts["cmd"].invoke()
    assert gui.input.get("1.0", "end-1c") == "Run CMD: "


def test_compact_chat_actions_keep_secondary_commands_available(gui):
    labels = [gui.shortcut_menu.entrycget(index, "label")
              for index in range(gui.shortcut_menu.index("end") + 1)
              if gui.shortcut_menu.type(index) != "separator"]
    # The quick-action row folds away in a running conversation, so its actions live here too.
    assert labels == ["PowerShell", "CMD", "Research", "Vision",
                      "Attach file", "Create image", "Start voice", "All tools & controls"]
    assert not gui.depth_shortcuts["powershell"].winfo_manager()
    assert gui.depth_shortcuts["voice"].winfo_manager()
    gui.input.delete("1.0", "end")
    gui.shortcut_menu.invoke(0)
    assert gui.input.get("1.0", "end-1c") == "Run PowerShell: "


def test_every_sidebar_view_opens(gui, monkeypatch):
    """A nav item whose handler was renamed away is a dead row, and the window
    gives no sign of it until it is clicked."""
    ran: list[str] = []
    monkeypatch.setattr(gui, "_background", lambda title, work, then=None: ran.append(title))
    monkeypatch.setattr(gui, "_refresh_operator", lambda: ran.append("Operator"))

    from app.gui import HayatGUI

    for key, _icon, _title, _subtitle in HayatGUI.VIEWS:
        gui.show_view(key)
        gui.root.update()
        assert gui.current_view == key
        assert gui.nav[key].selected
        assert sum(item.selected for item in gui.nav.values()) == 1


def test_opening_a_view_never_asks_a_question(gui, monkeypatch):
    """Views report; they do not prompt.

    "research" used to open a modal dialog asking what to look into, so
    clicking a sidebar item stopped the whole window until someone answered
    -- and in a test, with nobody to answer, forever. One of these left a
    pytest process running for two and a half hours before it was noticed.
    """
    from tkinter import simpledialog

    from app.gui import HayatGUI

    def refuse(*args, **kwargs):
        raise AssertionError("a sidebar view opened a modal dialog")

    monkeypatch.setattr(simpledialog, "askstring", refuse)
    monkeypatch.setattr(gui, "_background", lambda title, work, then=None: None)
    monkeypatch.setattr(gui, "_refresh_operator", lambda: None)
    monkeypatch.setattr(gui, "_tool", lambda name, arguments=None: "")

    for key, _icon, _title, _subtitle in HayatGUI.VIEWS:
        gui.show_view(key)
        gui.root.update()


def test_the_dials_and_cards_show_measurements_not_invented_numbers(gui):
    """Before any measurement lands, every reading must be absent rather than
    a plausible zero."""
    gui._metrics = {}
    gui._paint_metrics()
    assert all(gauge.value is None for gauge in gui.gauges.values())
    assert gui.cards["temp"].primary.get() == "--"

    gui._refresh_health()
    gui._paint_metrics()
    gui.root.update()
    assert gui.gauges["cpu"].value is not None
    assert gui.gauges["ram"].value is not None
    assert "%" in gui.metric_vars["cpu"].get()


def test_the_status_bar_reports_the_session_rather_than_guessing(gui):
    gui._refresh_slow()
    gui._paint_statusbar({"state": "idle", "enabled": False})
    gui.root.update()
    assert "Voice:" in gui.pills["voice"].value.get()
    assert "Uptime:" in gui.pills["uptime"].value.get()
    # Tasks come from the database, so the count is a fact rather than a guess.
    assert gui.pills["tasks"].value.get().split(": ")[1].isdigit()


def test_learning_research_rail_uses_live_durable_counts(gui):
    gui._refresh_slow()
    gui._paint_metrics()
    gui.root.update()
    assert "summaries" in gui.intelligence_rows["research"].get()
    assert "queued" in gui.intelligence_rows["learning"].get()
    assert "eligible" in gui.intelligence_rows["training"].get()
    assert "gaps" in gui.intelligence_rows["control"].get()
    assert "protected" in gui.intelligence_rows["authority"].get()


def test_training_view_reports_private_local_boundary(gui, monkeypatch):
    reports = []
    monkeypatch.setattr(gui, "_background", lambda title, work, then=None: reports.append((title, work())))
    gui._show_training()
    title, body = reports[-1]
    assert title == "Training"
    assert "PRIVATE DATA BOUNDARY" in body
    assert "local only" in body
    # The view must report the configured setting, not a fixed reassurance.
    # Asserting the literal "False" here hid the real defect for as long as
    # private_status() returned False no matter what the configuration said.
    collection = gui.runtime.training.private_status()["automatic_collection"]
    assert f"automatic collection {collection}" in body


def test_a_turn_is_stamped_with_the_time_it_happened(gui):
    gui._append("user", "You\nwhat time is it")
    gui.root.update()
    body = gui.chat.get("1.0", "end")
    assert "what time is it" in body
    assert ("AM" in body) or ("PM" in body)


def test_clearing_the_chat_clears_the_view_and_summarizes_in_the_background(gui, monkeypatch):
    """The view clears immediately, and the summarize-and-reset runs off the UI

    thread (stubbed here so the test needs no model). The summarize/rotate/store
    logic itself is covered directly in test_conversation."""
    calls = []
    monkeypatch.setattr(gui.runtime.conversation, "summarize_and_reset",
                        lambda: calls.append(1) or {"summarized": True})
    gui._append("user", "You\nremember this")

    gui.clear_chat()
    gui.root.update()  # the view clears synchronously
    text = gui.chat.get("1.0", "end")
    assert "remember this" not in text
    assert "Cleared the chat" in text

    _pump(gui, 15)  # let the background thread and its callback run
    assert calls, "summarize_and_reset was not invoked"


def test_the_console_is_where_operational_lines_land(gui):
    gui._output_event("thinking", "planning the next step")
    gui.root.update()
    assert "planning the next step" in gui.console.text.get("1.0", "end")
    # The old attribute still points somewhere real, so nothing that writes
    # to it is quietly writing into a void.
    assert gui.trace is gui.console.text


def test_live_mindstream_shows_real_events_and_has_working_controls(gui):
    gui._clear_mindstream()
    gui.runtime.events.publish(
        "RESEARCH_STARTED", "research", {"query": "contradiction-aware retrieval"})
    _pump(gui, 10)
    feed = gui.mindstream_feed.text.get("1.0", "end")
    assert "researching: contradiction-aware retrieval" in feed

    gui._toggle_mindstream()
    assert gui._mindstream_paused is True
    assert gui.mindstream_pause_button.cget("text") == "Resume Feed"
    assert gui.mindstream_status_var.get() == "VIEW PAUSED · WORK CONTINUES"
    before = gui.mindstream_feed.text.get("1.0", "end")
    gui.runtime.events.publish("MODEL_STARTED", "model", {})
    _pump(gui, 5)
    assert gui.mindstream_feed.text.get("1.0", "end") == before

    gui._toggle_mindstream()
    assert "resumed" in gui.mindstream_feed.text.get("1.0", "end").lower()
    gui._clear_mindstream()
    assert not gui.mindstream_feed.text.get("1.0", "end").strip()

    gui._show_mindstream_tab("learning")
    assert gui._mindstream_active_tab == "learning"
    assert gui.mindstream_tab_buttons["learning"].cget("bg") == gui.ROYAL
    gui._show_mindstream_tab("prediction")
    assert gui._mindstream_active_tab == "prediction"
    gui._show_mindstream_tab("not-a-tab")
    assert gui._mindstream_active_tab == "prediction"


def test_live_mindstream_rows_are_evidence_backed(gui):
    gui._slow = {
        "learning": {
            "running": True,
            "paused": False,
            "queue": {
                "counts": {"pending": 3},
                "topics": [{
                    "topic": "likely next question", "source": "anticipated_follow_up",
                    "status": "pending",
                }],
                "predictions": {
                    "prepared": 7, "precision": .75, "matched": 6,
                    "different_question": 2,
                },
            },
            "research_level": "deep",
        },
    }
    gui._paint_mindstream({"headline": "Checking sources", "count": 1})
    assert gui.mindstream_rows["focus"].get() == "Checking sources"
    assert "running" in gui.mindstream_rows["learning"].get()
    assert "7 prepared" in gui.mindstream_rows["prediction"].get()
    assert "75% verified" in gui.mindstream_rows["prediction"].get()
    assert "3 pending" in gui.mindstream_learning_detail.get()
    assert "DEEP" in gui.mindstream_learning_detail.get()
    assert "6 matched" in gui.mindstream_prediction_detail.get()
    assert "likely next question" in gui.mindstream_prediction_detail.get()
    assert gui.mindstream_activity_var.get() == "1 active"
    assert gui.mindstream_status_var.get().startswith("LIVE ·")


def test_the_camera_panel_never_changes_size(gui):
    """Flicker, measured rather than eyeballed.

    A Label's `height` counts text lines when it holds text and pixels when it
    holds an image, so `height=7` meant about a hundred pixels between frames
    and seven during them. The panel snapped between two sizes fifteen times a
    second and dragged the whole scrolling column through a relayout each time.
    """
    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    try:
        gui.root.update()
        before = (gui.preview_box.winfo_width(), gui.preview_box.winfo_height())
        assert before == gui.PREVIEW_PIXELS

        # Whatever the label holds -- a message or an image -- the box is the
        # same size, so nothing else on screen can move.
        from PIL import Image, ImageTk

        # master is explicit: PhotoImage otherwise attaches to tkinter's
        # default root, which is not this window once a drag-and-drop capable
        # root is in play -- and the image then belongs to an interpreter the
        # label cannot see ("image pyimage1 doesn't exist").
        photo = ImageTk.PhotoImage(Image.new("RGB", gui.PREVIEW_PIXELS), master=gui.root)
        gui.preview.configure(image=photo, text="")
        gui.root.update()
        assert (gui.preview_box.winfo_width(), gui.preview_box.winfo_height()) == before

        gui.preview.configure(image="", text="Camera is off.")
        gui.root.update()
        assert (gui.preview_box.winfo_width(), gui.preview_box.winfo_height()) == before
    finally:
        # Drop the image before leaving. The window is shared across this
        # module, and a PhotoImage that goes out of scope while a widget still
        # points at it leaves the next test with `image "pyimage1" doesn't
        # exist`.
        try:
            gui.preview.configure(image="", text="Camera is off.")
        except Exception:
            pass
        gui.root.withdraw()


def test_installed_memory_is_reported_alongside_what_windows_offers(gui):
    """Windows offers 39.7 of this machine's 48 GiB; the rest is reserved for
    hardware before it sees any of it. Showing only the smaller number made
    HAYAT look like it could not read its own machine."""
    gui._refresh_health()
    gui._paint_metrics()
    gui.root.update()

    installed = gui._metrics.get("ram_installed")
    if installed:
        assert "installed" in gui.cards["ram"].primary.get()
        assert "usable" in gui.cards["ram"].secondary.get()
    assert gui.cards["gpu"].secondary.get().endswith("GB VRAM") or not gui._metrics.get("vram_total")


# --------------------------------------------------- files handed over by hand


def test_a_dropped_path_with_spaces_stays_one_file(gui):
    """tkdnd hands over a single string, and any path containing a space
    arrives wrapped in braces. Splitting on whitespace turns one real file
    into two that do not exist."""
    from app.gui import HayatGUI

    parsed = HayatGUI._parse_dropped(r"{C:\Users\m\notes on margins.txt} C:\second.txt")
    assert parsed == [r"C:\Users\m\notes on margins.txt", r"C:\second.txt"]


def test_a_single_plain_path_needs_no_braces(gui):
    from app.gui import HayatGUI

    assert HayatGUI._parse_dropped(r"C:\one.txt") == [r"C:\one.txt"]
    assert HayatGUI._parse_dropped("") == []


def test_the_plus_and_the_drop_go_through_the_same_reader(gui, monkeypatch):
    """Two ways in, one path afterwards -- so remembering a dropped file and
    remembering a chosen one cannot drift apart."""
    seen: list[list[str]] = []
    monkeypatch.setattr(gui, "_ingest_files", lambda paths: seen.append(list(paths)))

    gui._on_drop(SimpleNamespace(data=r"{C:\a b.txt}"))
    assert seen == [[r"C:\a b.txt"]]


def test_dropping_nothing_does_nothing(gui, monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(gui, "_ingest_files", lambda paths: called.append(1))
    gui._on_drop(SimpleNamespace(data=""))
    assert called == []


def test_the_window_opens_whether_or_not_drag_and_drop_is_available(gui):
    """Dropping files is worth having and is not worth the window failing to
    open over."""
    assert isinstance(gui.drag_and_drop, bool)
    assert gui.root.winfo_exists()


def test_every_displayed_value_belongs_to_this_window(gui):
    """A silent, total failure worth a test of its own.

    Importing tkinterdnd2 creates a Tk root of its own, so `tk._default_root`
    stops being this window. A StringVar built without a master attaches to
    that other interpreter, and a label on this window then displays nothing
    at all -- greeting, clock, sensors, dials, status bar, every value blank
    while the layout looked perfect.
    """
    import tkinter as tk

    variables = [
        gui.greeting_var, gui.header_clock, gui.online_var, gui.clock_var,
        gui.view_title, gui.health_var, gui.activity_var, gui.command_var,
        gui.preview_detail, gui.attachment_status,
        gui.mindstream_status_var, gui.mindstream_activity_var,
        gui.mindstream_learning_detail, gui.mindstream_prediction_detail,
        *gui.metric_vars.values(), *gui.sensor_vars.values(),
        *gui.info_rows.values(),
        *(card.primary for card in gui.cards.values()),
        *(pill.value for pill in gui.pills.values()),
    ]
    for variable in variables:
        assert isinstance(variable, tk.StringVar)
        assert variable._tk is gui.root.tk, (
            f"{variable} is bound to a different interpreter and will never display")


def test_the_header_says_who_it_belongs_to(gui):
    gui._tick()
    gui.root.update()
    from app.identity import IDENTITY

    assert IDENTITY.owner_short in gui.greeting_var.get()
    assert any(part in gui.greeting_var.get()
               for part in ("morning", "afternoon", "evening"))
    assert gui.header_clock.get()


def test_the_system_info_panel_names_the_machine(gui):
    """The dials say how busy it is. This says what it is, which is the
    question anyone actually asks about a machine they cannot see."""
    gui._refresh_health()
    gui._paint_info()
    gui.root.update()

    assert gui.info_rows["cpu"].get() not in {"", "--"}
    assert gui.info_rows["uptime"].get()
    if gui._metrics.get("vram_total"):
        assert "VRAM" in gui.info_rows["gpu"].get()


def test_a_command_typed_in_the_console_reaches_the_same_handler(gui, monkeypatch):
    """Slash commands existed and were reachable only from the terminal, so
    the window could show what HAYAT was doing but not tell it to do any."""
    ran: list[str] = []
    monkeypatch.setattr(gui, "_background", lambda title, work, then=None: ran.append(title))

    gui.command_var.set("/doctor")
    gui.run_command()
    assert ran == ["/doctor"]
    assert gui.command_var.get() == ""
    assert "/doctor" in gui.console.text.get("1.0", "end")


def test_plain_text_in_the_console_is_treated_as_something_said(gui, monkeypatch):
    """Typing a sentence into a box and having nothing happen is the worse
    surprise."""
    sent: list[str] = []
    monkeypatch.setattr(gui, "send", lambda: sent.append(gui.input.get("1.0", "end").strip()))

    gui.command_var.set("what is the weather")
    gui.run_command()
    assert sent == ["what is the weather"]


def test_an_output_listener_can_be_removed_again():
    """Without this, every console command leaves another listener behind and
    the same line is delivered once more each time."""
    from app.output import get_output

    seen: list[str] = []
    listener = lambda source, text: seen.append(text)
    output = get_output()
    output.add_listener(listener)
    output.emit("first", source="test")
    output.remove_listener(listener)
    output.emit("second", source="test")
    output.remove_listener(listener)  # removing twice must not raise

    assert "first" in seen and "second" not in seen


# ----------------------------------------------------- toolbox and workspace


def test_the_toolbox_replaces_the_transcript_and_gives_it_back(gui):
    """A toolbox is a place to press things, not an answer, so it takes the
    centre rather than reporting into the conversation."""
    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    try:
        gui.show_view("toolbox")
        gui.root.update()
        assert gui._toolbox_frame.winfo_ismapped()
        assert not gui.chat.winfo_ismapped()
        assert gui._toolbox_frame.winfo_width() > 100, "the pane drew at no size"

        gui.show_view("chat")
        gui.root.update()
        assert gui.chat.winfo_ismapped()
        assert not gui._toolbox_frame.winfo_ismapped()
    finally:
        gui.root.withdraw()


def test_every_tile_runs_the_same_handler_as_its_button(gui):
    """One implementation behind both, so a tile and a button cannot drift."""
    from app.gui import HayatGUI

    buttons = {label for label, _method, _group in HayatGUI.CONTROLS}
    for key, _icon, _title, _subtitle in HayatGUI.TOOLBOX:
        assert key in buttons, f"the {key} tile has no handler"


def test_pinning_keeps_it_and_unpinning_lets_it_go(gui):
    gui.clear_workspace()
    gui.pin_tool("Camera")
    gui.pin_tool("Research")
    gui.pin_tool("Camera")  # pinning twice must not duplicate it
    gui.root.update()

    assert gui._pinned == ["Camera", "Research"]
    assert len(gui.workspace.pinned) == 2

    gui.unpin_tool("Camera")
    gui.root.update()
    assert gui._pinned == ["Research"]
    gui.clear_workspace()
    assert gui._pinned == []


def test_the_workspace_is_remembered_between_sessions(gui):
    """A workspace that forgets is a toolbar."""
    gui.clear_workspace()
    gui.pin_tool("Memory")
    assert gui._load_workspace() == ["Memory"]
    gui.clear_workspace()
    assert gui._load_workspace() == []


def test_an_empty_workspace_says_so_rather_than_looking_broken(gui):
    gui.clear_workspace()
    gui.show_view("toolbox")
    gui.root.update()
    text = " ".join(
        child.cget("text") for child in gui.workspace.body.winfo_children()
        if isinstance(child, tkinter.Label))
    assert "Nothing pinned" in text
    gui.show_view("chat")


def test_an_unknown_tile_says_so_instead_of_failing_quietly(gui):
    gui.run_tool("Nonexistent")
    gui.root.update()
    assert "no tool called" in gui.chat.get("1.0", "end")


def test_the_whole_window_takes_a_drop_not_just_the_transcript(gui):
    """Limiting it to the conversation was the tidier idea and it did not work
    in practice: the transcript is a disabled Text, the input box is small,
    and a file dropped an inch either side landed on a frame that accepted
    nothing and silently did nothing."""
    if gui.drag_and_drop:
        for widget in (gui.root, gui.chat, gui.input, gui.drop_zone):
            assert "<<Drop>>" in widget.bind(), f"{widget} does not accept drops"
    else:
        # A full-suite process may already have initialized COM through an
        # unrelated Windows test. Production and the isolated COM test reserve
        # TkDND before that can happen; this branch verifies the honest,
        # functional picker fallback instead of skipping the test entirely.
        assert "USE" in gui.drop_capability.cget("text")
        assert "+ Add file" in gui.buttons


def test_file_drop_zone_reports_retention_state(gui):
    assert "Drop files or images" in gui.attachment_status.get()
    gui._set_attachment_status("Remembered 2 files · ready for questions", "success")
    assert "Remembered 2" in gui.attachment_status.get()
    assert gui.drop_status_label.cget("fg") == gui.OLIVE_SOFT


def test_research_button_opens_the_depth_workspace_and_preserves_its_draft(gui, monkeypatch):
    def no_modal(*args, **kwargs):
        pytest.fail('Research should open its workspace without blocking on a modal prompt')
    monkeypatch.setattr("app.gui.simpledialog.askstring", no_modal)
    before = len(gui.runtime.research_jobs.recent(30))
    gui._research()
    studio = gui._research_studio
    try:
        assert studio.winfo_exists() and studio.depth.get() == 'Deep'
        studio.query.insert(0, 'Widget API')
        gui._research()
        assert gui._research_studio is studio and studio.query.get() == 'Widget API'
        assert len(gui.runtime.research_jobs.recent(30)) == before
    finally:
        studio.close()


def test_web_clip_button_requests_a_rendered_remembered_capture(gui, monkeypatch):
    answers = iter(["https://example.com/guide", "Widget guide"])
    seen = []
    monkeypatch.setattr("app.gui.simpledialog.askstring", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(gui, "_tool", lambda name, arguments=None: seen.append((name, arguments)) or "ok")
    monkeypatch.setattr(gui, "_background", lambda title, work, then=None: work())
    gui._capture_web_page()
    assert seen[0][0] == "capture_web_page"
    assert seen[0][1]["remember"] is True
    assert seen[0][1]["screenshot"] is True


def test_every_tool_run_reaches_the_console_now_that_none_of_them_ask(gui):
    """The approval prompt was doing a second job nobody designed.

    One was authorising the action. The other was telling Mohsin it was
    happening at all. Turning the default policy to ALLOW removes the first,
    and would silently remove the second unless the tool events -- published
    since the beginning, subscribed to by nothing -- are put on screen.
    """
    gui.console.clear()
    events = gui.runtime.events
    events.publish("TOOL_STARTED", "tools", {"tool": "web_search"})
    events.publish("TOOL_COMPLETED", "tools", {"tool": "web_search"})
    events.publish("TOOL_FAILED", "tools", {"tool": "browser_navigate", "error": "timed out"})
    _pump(gui, 20)

    console = gui.console.text.get("1.0", "end")
    assert "web_search running" in console
    assert "web_search done" in console
    assert "browser_navigate failed" in console and "timed out" in console


def test_the_tools_view_lists_the_whole_registry_not_a_prose_sample(gui):
    """The old view was the model's prose about a handful of tools. This reads
    the whole registry so "what can it do" has a complete, real answer."""
    from app import capabilities

    overview = gui._tools_overview()
    total = len(gui.runtime.registry.names())
    assert f"I have {total} tools, grouped by what they are for" in overview
    # Grouped by plain-language area, every tool named.
    assert "Browse websites for you" in overview and "See through the camera and screen" in overview
    assert all(name in overview for name in gui.runtime.registry.names())
    assert capabilities.classify(gui.runtime.registry.names())[1] == []
    assert "Reliability:" in overview


def test_a_generated_image_gets_a_clickable_open_link(gui, tmp_path):
    """The inline thumbnail is a preview; the real 1080 file opens on a click."""
    from PIL import Image

    png = tmp_path / "made.png"
    Image.new("RGB", (64, 64), (80, 120, 200)).save(png)  # a real, decodable PNG
    before = len(gui._inline_images)
    gui._append("assistant", f"HAYAT\nYour image is ready. Saved to {png}")
    _pump(gui, 6)
    assert len(gui._inline_images) > before
    assert "Open full size" in gui.chat.get("1.0", "end")


def test_image_button_result_appears_inline_in_chat(gui, tmp_path, monkeypatch):
    from PIL import Image

    from app.tools.registry import ToolResult

    png = tmp_path / "button.png"
    Image.new("RGB", (48, 32), (90, 130, 210)).save(png)
    monkeypatch.setattr(gui.runtime.registry, "execute", lambda name, args: ToolResult(
        name, True, {"ok": True, "path": str(png)}))
    monkeypatch.setattr("app.gui.threading", SimpleNamespace(Thread=lambda target, **_kwargs:
                        SimpleNamespace(start=target)))
    monkeypatch.setattr(gui, "_post", lambda callback, *args: callback(*args))

    gui._background("Image", lambda: gui._tool("generate_image", {"prompt": "blue"}),
                    show_in_chat=True)

    transcript = gui.chat.get("1.0", "end")
    assert len(gui._inline_images) == len(gui.chat.image_names()), transcript
    assert str(png) in transcript
    assert "Open full size" in transcript


# ----------------------------------------------------- in-app PDF viewer


def _make_pdf(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.filesystem import FilesystemTools

    fs = FilesystemTools(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    return fs.write_pdf("viewer-doc.pdf", "# Title\n\nBody text.\n- one\n- two")["path"]


def test_a_generated_pdf_reply_offers_an_in_app_viewer_link(gui, tmp_path):
    """Naming a PDF is not enough; the reply gets a clickable in-app link."""
    path = _make_pdf(tmp_path)
    gui._append("assistant", f"Your PDF is ready. Saved to {path}")
    gui.root.update()
    assert "Open PDF in viewer" in gui.chat.get("1.0", "end")


def test_slow_document_preview_does_not_block_chat_or_resurrect_cleared_cards(gui, tmp_path, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    def slow_preview(path):
        started.set()
        release.wait(5)
        return {'lines': ['Late preview result']}
    monkeypatch.setattr('app.gui_rich.document_preview', slow_preview)
    source = tmp_path / 'slow.txt'
    source.write_text('hello')
    try:
        gui._append('assistant', f'HAYAT\nSaved to {source}')
        assert started.wait(1)
        assert 'Loading preview' in gui.chat.get('1.0', 'end')
        # A second message and a cleared transcript work while parsing is blocked.
        gui._append('assistant', 'HAYAT\nStill responsive')
        gui.chat.configure(state='normal')
        gui.chat.delete('1.0', 'end')
        gui.chat.configure(state='disabled')
    finally:
        release.set()
    _pump(gui, 12)
    assert 'Late preview result' not in gui.chat.get('1.0', 'end')


def test_worker_results_are_delivered_on_the_gui_thread_and_timer_tracking_stays_bounded(gui):
    import threading
    received = []
    thread = threading.Thread(target=lambda: gui._post(lambda: received.append(threading.get_ident())))
    thread.start()
    thread.join(1)
    assert received == []
    _pump(gui, 8)
    assert received == [threading.get_ident()]
    initial = len(gui._after_handles)
    for _ in range(30):
        gui._schedule(0, lambda: None)
    gui.root.update()
    assert len(gui._after_handles) <= initial + 2


def test_file_editor_saves_a_backup_and_rejects_a_stale_save(gui, tmp_path):
    from app.gui_files import FileEditor
    path = tmp_path / 'editable.md'
    path.write_text('Before\n', encoding='utf-8')
    editor = FileEditor(gui, path)
    try:
        _wait_until(gui, lambda: editor._digest is not None, 'file editor load')
        editor.text.delete('1.0', 'end')
        editor.text.insert('1.0', 'After\n')
        editor.save()
        _wait_until(gui, lambda: not editor._busy, 'file editor save', timeout=20)
        assert path.read_text() == 'After\n'
        assert list(tmp_path.glob('editable.md.hayat-*.bak'))
        path.write_text('External change\n')
        editor.text.delete('1.0', 'end')
        editor.text.insert('1.0', 'Stale\n')
        editor.save()
        _wait_until(gui, lambda: not editor._busy, 'stale file editor save')
        assert path.read_text() == 'External change\n'
        assert 'changed since' in editor.status.get()
    finally:
        editor.destroy()


def test_the_pdf_viewer_opens_renders_pages_and_closes(gui, tmp_path):
    """Clicking opens a scrollable viewer with the pages rendered, and it can

    be closed again -- the whole point of reading it inside HAYAT."""
    import tkinter as tk

    path = _make_pdf(tmp_path)
    before = set(gui.root.winfo_children())
    gui._open_pdf_viewer(path)
    opened = [w for w in gui.root.winfo_children()
              if w not in before and isinstance(w, tk.Toplevel)]
    assert opened, "the viewer window did not open"
    viewer = opened[0]
    _wait_until(gui, lambda: bool(viewer._pdf_photos), 'PDF viewer page render')
    assert getattr(viewer, "_pdf_photos", None), "no pages were rendered"
    assert len(viewer._pdf_photos) == 1
    viewer.destroy()
    assert viewer._pdf_photos == []
    gui.root.update()


def test_image_links_keep_their_targets_after_old_previews_are_trimmed(gui, tmp_path):
    from PIL import Image

    paths = [tmp_path / f'preview-{number}.png' for number in range(3)]
    for path in paths:
        Image.new('RGB', (20, 20), 'blue').save(path)
    gui.chat.configure(state='normal')
    gui._embed_image_file(paths[0])
    gui._embed_image_file(paths[1])
    second_tag = next(tag for tag in gui.chat.tag_names('4.2') if tag.startswith('openimg_'))
    gui.chat.delete('1.0', '3.0')
    gui._trim(gui.chat, 1000)
    assert len(gui._inline_images) == 1
    before = tuple(str(index) for index in gui.chat.tag_ranges(second_tag))
    gui._embed_image_file(paths[2])
    assert tuple(str(index) for index in gui.chat.tag_ranges(second_tag)) == before
    gui.chat.configure(state='disabled')


def test_the_phone_pill_shows_the_connected_device_count(gui):
    """The main system can see phone/browser access at a glance on the status

    bar: off when not serving, 'ready' when up with nobody on, 'N on' when
    devices are attached."""
    from types import SimpleNamespace

    try:
        assert "phone" in gui.pills
        gui._web_server = None
        gui._tick(); gui.root.update()
        assert gui.pills["phone"].value.get() == "Phone: off"

        gui._web_server = SimpleNamespace(active_users=lambda: 2)
        gui._tick(); gui.root.update()
        assert "2 on" in gui.pills["phone"].value.get()

        gui._web_server = SimpleNamespace(active_users=lambda: 0)
        gui._tick(); gui.root.update()
        assert "ready" in gui.pills["phone"].value.get()
    finally:
        gui._web_server = None


def test_sending_from_a_workspace_returns_to_the_visible_conversation(gui, monkeypatch):
    monkeypatch.setattr(gui, "_work", lambda text: None)
    gui.show_view("history")
    gui.input.insert("1.0", "Explain this result")
    gui.send()
    gui.root.update_idletasks()
    assert gui.current_view == "chat"
    assert gui.chat.winfo_manager() == "pack"
    assert not gui._explorer_panels["history"].winfo_manager()


def test_safe_mode_window_still_closes_its_runtime(own_gui):
    own_gui.runtime.safe_mode = True
    own_gui.close()
    assert own_gui._closed
    assert own_gui.runtime._closed


def test_system_map_switches_between_3d_and_list_and_explains_a_part(gui):
    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view("operator")
    finally:
        gui._operator_suppress_auto_refresh = False
    gui._paint_operator(gui.runtime.brain_mesh.self_check())
    gui.root.update()
    assert gui.brain_graph.checks
    gui._select_brain_node("authority")
    assert "Authority" in gui.brain_node_detail.get()
    assert "Last check: " in gui.brain_node_detail.get()
    assert "Not checked yet" not in gui.brain_node_detail.get()
    gui._toggle_operator_layout()
    assert gui._operator_cards.winfo_manager() and not gui._brain_row.winfo_manager()
    gui._toggle_operator_layout()
    assert gui._brain_row.winfo_manager() and not gui._operator_cards.winfo_manager()
    gui.show_view("chat")


def test_operator_check_completion_is_logged_not_recorded_as_a_failure(gui, monkeypatch):
    recorded = []
    monkeypatch.setattr(gui, "_record_failure", lambda *args, **kwargs: recorded.append((args, kwargs)))
    ran = []

    class Immediate:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    # Replace only the operator module's binding: mutating the shared threading
    # module also intercepts subprocess pipe readers in unrelated GUI workers.
    monkeypatch.setattr("app.gui_operator.threading", SimpleNamespace(Thread=Immediate))
    gui._run_mesh_task("Probe", lambda: ran.append(1) or gui.runtime.brain_mesh.self_check())
    gui.root.update()
    assert ran and recorded == []
    gui.show_view("chat")


def test_workspace_search_restores_navigation_from_focus_mode_without_touching_draft(gui):
    gui.input.insert("1.0", "Keep this unsent draft")
    original = gui.input.get("1.0", "end-1c")
    view = gui.current_view
    gui._focus_mode = True
    gui._arrange_rails()
    gui._focus_workspace_search()
    gui.workspace_navigation.query.set("lora")
    gui.root.update()
    assert not gui._focus_mode and gui._sidebar.winfo_manager() == "pack"
    assert gui.workspace_navigation.visible_keys == ["training"]
    assert gui.current_view == view
    assert gui.input.get("1.0", "end-1c") == original
    gui.workspace_navigation.clear()


def test_large_text_on_a_short_window_keeps_the_transcript_and_actions_reachable(gui):
    from app.gui_theme import apply_text_scale

    geometry = gui.root.geometry()
    try:
        gui.root.deiconify()
        gui.root.geometry("1180x760-4000-4000")
        gui._jump_to_latest()
        gui.input.insert("1.0", "\n".join(f"Line {n}" for n in range(8)))
        apply_text_scale(gui.root, 1.5)
        _wait_for_window(gui, gui.root)
        assert gui.chat.winfo_viewable() and gui.chat.winfo_height() >= 100
        assert int(gui.input.cget("height")) == 3
        assert gui.header_more_button.winfo_manager()
        assert gui._composer_more.winfo_manager()
        assert gui.status_more_button.winfo_manager()
        gui._populate_status_menu()
        assert gui.status_more_menu.entrycget(4, "label") == gui.pills["context"].value.get()
        assert not gui.pills["context"].winfo_manager()
        assert not gui._chat_tabs.winfo_manager()
        for row in (gui._conversation_actions, gui._header_actions, gui._composer_actions):
            for item in row.items:
                if item.winfo_manager():
                    assert item.winfo_x() + item.winfo_width() <= row.winfo_width() + 2
        labels = [gui.chat_actions_menu.entrycget(index, "label")
                  for index in range(gui.chat_actions_menu.index("end") + 1)
                  if gui.chat_actions_menu.type(index) != "separator"]
        assert {"Find in chat", "Commands", "Jump to latest", "Copy chat"} <= set(labels)
    finally:
        apply_text_scale(gui.root, 1.0)
        gui.input.delete("1.0", "end")
        gui.root.geometry(geometry)
        gui.root.update()
        gui.root.withdraw()


def test_voice_pill_and_voice_shell_use_the_same_words(gui):
    gui._paint_statusbar({"state": "idle", "enabled": False})
    assert gui.pills["voice"].value.get() == "Voice: Off"
    gui._paint_statusbar({"state": "approval_required", "enabled": True})
    assert gui.pills["voice"].value.get() == "Voice: Waiting for your approval"
    assert gui.depth_nav.voice_levels is None or callable(gui.depth_nav.voice_levels)


def test_a_data_reply_is_shown_as_a_block_and_its_steps_open_on_demand(gui):
    from types import SimpleNamespace
    from app.observed_reply import steps_trace

    gui._append("assistant", "HAYAT\napp/voice has 2 items:\n  - tts.py\n  - vad.py")
    block = gui.chat.tag_ranges("block")
    assert block and "tts.py" in gui.chat.get(block[-2], block[-1])

    failed = SimpleNamespace(history=[{
        "tool": "read_file", "description": "Read missing.txt.",
        "result": {"success": False, "error": "FileNotFoundError: no such file", "duration_ms": 3},
        "verification": {"passed": False, "reason": "Tool failed: FileNotFoundError: no such file"}, "retry": 2,
    }])
    steps = steps_trace(SimpleNamespace(intent=SimpleNamespace(value="ACTION")), failed)
    assert steps[0] == "Understood as action"
    assert steps[1].startswith("✗ 1. Read missing.txt — read file, 3 ms, 2 retries")
    assert steps[-1] == "0 of 1 step verified · 3 ms of tool time"

    gui._append_steps(steps)
    key = f"steps-{len(gui._steps)}"
    toggle = gui.chat.tag_ranges(key)
    assert "How I got this (1 step)" in gui.chat.get(toggle[0], toggle[1])
    gui._toggle_steps(key)
    opened = gui.chat.tag_ranges(f"{key}-detail")
    assert opened and "FileNotFoundError" in gui.chat.get(opened[0], opened[1])
    gui._toggle_steps(key)
    assert not gui.chat.tag_ranges(f"{key}-detail")
    toggle = gui.chat.tag_ranges(key)
    assert gui.chat.get(toggle[0], toggle[1]).startswith("▸")
    assert steps_trace(None, SimpleNamespace(history=[])) == []


def test_the_window_opens_where_it_was_left_or_sized_to_the_screen():
    from app.gui import window_placement

    # First run on a large screen: most of it, centred -- not a fixed 1540x920.
    assert window_placement(None, (1920, 1200)) == ("1690x1056+115+52", False)
    # A small screen gets the full-size minimum width, and a height clear of the taskbar.
    assert window_placement(None, (1280, 720)) == ("1180x660+50+10", False)
    # 200 % display scaling on a 1920x1080 panel: the window still fits on screen.
    assert window_placement(None, (960, 540)) == ("940x480+10+10", False)
    assert window_placement(None, (1180, 700), minimum=(1180, 700)) == ("1180x700+0+0", False)
    # A remembered placement comes back, maximised if it was.
    assert window_placement({"geometry": "1400x900+60+40", "zoomed": True}, (1920, 1200)) == ("1400x900+60+40", True)
    # One from a monitor that is gone, or parked off screen, is not trusted.
    assert window_placement({"geometry": "1400x900+3000+40"}, (1920, 1200))[0] == "1690x1056+115+52"
    assert window_placement({"geometry": "1540x920-4000-4000"}, (1920, 1200))[0] == "1690x1056+115+52"
    assert window_placement({"geometry": "garbage"}, (1920, 1200))[0] == "1690x1056+115+52"


def test_a_hidden_window_is_never_remembered(gui, monkeypatch):
    saved = []
    monkeypatch.setattr(gui, "_save_appearance", lambda **changes: saved.append(changes) or True)
    gui.root.withdraw()
    gui._remember_window()
    assert saved == []


def test_notices_behind_the_workspace_are_counted_and_the_idle_bar_is_hidden(gui):
    gui._welcome_pending = False
    gui._core_requested = True
    gui.show_view("chat")
    gui.root.update()
    gui._append("system", "A background notice.")
    gui._append("system", "Another one.")
    assert gui.core_button.cget("text") == "Conversation (2 new)"
    gui._toggle_core()
    assert gui.core_button.cget("text") == "3D workspace"
    gui._core_requested = False
    gui.show_view("chat")

    gui.activity_progress_var.set(0)
    assert not gui._activity_progress.winfo_manager()
    gui.activity_progress_var.set(40)
    assert gui._activity_progress.winfo_manager()
    gui.activity_progress_var.set(100)
    assert not gui._activity_progress.winfo_manager()


def test_the_system_map_travels_through_time(gui, monkeypatch):
    from datetime import UTC, datetime, timedelta
    import json as _json
    from app.brain_history import reconstruct

    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view("operator")
    finally:
        gui._operator_suppress_auto_refresh = False
    gui.root.update()
    start = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
    stamp = lambda seconds: (start + timedelta(seconds=seconds)).isoformat()
    history = reconstruct([
        (stamp(10), "TOOL_STARTED", "tools", _json.dumps({"tool": "browser_navigate"}), "t1"),
        (stamp(20), "TOOL_FAILED", "tools", _json.dumps({"tool": "browser_navigate", "error": "timed out"}), "t1"),
    ], start.timestamp(), start.timestamp() + 60)
    gui._show_brain_history(history)
    gui.brain_timeline.seek(start.timestamp() + 15)
    detail = gui.brain_node_detail.get()
    assert detail.startswith("At ") and "browser navigate" in detail
    assert gui.brain_graph.moment is not None and gui.brain_graph.flows == ["research"]
    gui.brain_timeline.seek(start.timestamp() + 20)
    assert "✗ browser navigate: timed out" in gui.brain_node_detail.get()
    assert gui.brain_graph._moment_marks == {"tools": "failure"}

    gui.brain_graph.set_motion(False)
    gui._replay_brain()
    assert gui.brain_timeline._replay is None and "replay is off" in gui.brain_node_detail.get()
    gui.brain_graph.set_motion(True)

    # One button: Replay, then Pause, then Resume; speed cycles; Fit unzooms.
    gui._replay_brain()
    assert str(gui.brain_replay_button.cget("text")) == "Pause"
    gui._replay_brain()
    assert str(gui.brain_replay_button.cget("text")) == "Resume"
    gui.brain_speed_button.invoke()
    assert str(gui.brain_speed_button.cget("text")) == "Fast"
    gui.brain_timeline.stop_replay()
    assert str(gui.brain_replay_button.cget("text")) == "Replay"
    gui.brain_timeline.zoom(.2)
    assert gui.brain_timeline.zoomed

    loads = []
    monkeypatch.setattr(gui, "_load_brain_history", lambda: loads.append(gui._brain_window_seconds))
    gui._set_brain_window(21600)
    assert loads == [21600] and gui.brain_graph.moment is None
    assert str(gui.brain_range_buttons[21600].cget("style")) == "HayatChosen.TButton"
    gui.show_view("chat")


def test_late_brain_history_cannot_overwrite_a_newer_snapshot_or_range(gui, monkeypatch):
    from app.brain_history import reconstruct
    monkeypatch.setattr(gui, '_load_brain_history', lambda: None)
    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view('operator')
    finally:
        gui._operator_suppress_auto_refresh = False
    old_history = reconstruct([], 0, 60)
    new_history = reconstruct([], 120, 180)
    generation = gui._brain_history_serial
    gui._brain_history_loading_generation = generation
    gui._show_brain_history(new_history)
    gui._show_brain_history(old_history, generation, gui._brain_window_seconds)
    assert gui._brain_history is new_history
    loads = []
    monkeypatch.setattr(gui, '_load_brain_history', lambda: loads.append(gui._brain_window_seconds))
    generation = gui._brain_history_serial
    previous_window = gui._brain_window_seconds
    gui._brain_history_loading_generation = generation
    gui._brain_history_loading = True
    gui._set_brain_window(21600 if previous_window != 21600 else 3600)
    loads.clear()
    gui._show_brain_history(old_history, generation, previous_window)
    assert gui._brain_history is new_history
    assert loads == [gui._brain_window_seconds] and not gui._brain_history_loading


def test_closing_vision_before_placement_settles_does_not_reopen_it(gui):
    gui.root.deiconify()
    _wait_for_window(gui, gui.root)
    try:
        gui._open_vision_controls()
        window = gui._vision_window
        gui._close_vision_controls()
        settled = []
        gui.root.after(150, lambda: settled.append(True))
        _wait_until(gui, lambda: bool(settled), 'pending placement callbacks')
        assert window.state() == 'withdrawn'
    finally:
        gui.root.withdraw()


def test_the_vision_chooser_can_always_be_closed(gui):
    ran = []
    original = gui._read_screen
    gui._read_screen = lambda: ran.append("read_screen")
    try:
        gui.root.deiconify()
        _wait_for_window(gui, gui.root)
        gui._open_vision_controls()
        window = gui._vision_window
        _wait_for_window(gui, window)
        assert window.state() == "normal"
        assert window.winfo_height() > 200               # sized from its content
        gui.vision_close_button.invoke()                 # its own button, not the title bar
        assert window.state() == "withdrawn"
        gui._open_vision_controls()
        _wait_for_window(gui, window, focus=True)
        window.event_generate("<Escape>", when="tail")
        _wait_until(gui, lambda: window.state() == "withdrawn", "Escape to close Vision")
        assert window.state() == "withdrawn"
        gui._open_vision_controls()
        _wait_for_window(gui, window)
        gui.vision_buttons["_read_screen"].invoke()      # choosing gets it out of the way
        assert window.state() == "withdrawn" and ran == ["read_screen"]
    finally:
        gui._read_screen = original
        gui.root.withdraw()


def test_brain_inspection_preserves_draft_and_separates_controls(gui):
    gui.input.insert("1.0", "Keep my draft")
    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view("operator")
        gui.root.update_idletasks()
        assert not gui._compose_surface.winfo_manager()
        assert gui.brain_replay_button.master != next(iter(gui.brain_range_buttons.values())).master
        gui.show_view("chat")
        assert gui._compose_surface.winfo_manager() == "pack"
        assert gui.input.get("1.0", "end").strip() == "Keep my draft"
    finally:
        gui._operator_suppress_auto_refresh = False


def test_studio_controls_fit_and_background_results_keep_their_page(gui, monkeypatch):
    from app.models.desktop import DesktopModels
    from tkinter import ttk
    monkeypatch.setattr(DesktopModels, "inventory", lambda self: [])
    starts = []
    monkeypatch.setattr(gui.runtime.voice, "start_background", lambda: starts.append(True))
    gui.root.deiconify()
    _wait_for_window(gui, gui.root)
    gui._open_control_studio("voice")
    studio = gui._control_studio
    _wait_for_window(gui, studio)
    studio.geometry("840x700")
    _wait_for_window(gui, studio)
    chosen_geometry = studio.geometry()
    def buttons(widget):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Button):
                yield child
            yield from buttons(child)
    try:
        for page in ("voice", "models", "self", "osint"):
            studio.show(page)
            _wait_for_window(gui, studio)
            page_buttons = list(buttons(studio.pages[page]))
            _wait_until(gui, lambda: all(button.winfo_viewable() for button in page_buttons),
                        f"{page} controls to map")
            assert studio.geometry() == chosen_geometry, "Changing tabs must keep the owner's window layout"
            assert studio.grab_current() is None
            for button in page_buttons:
                assert button.winfo_viewable()
                assert button.winfo_rooty() + button.winfo_height() <= studio.winfo_rooty() + studio.winfo_height()
        assert starts == []
        studio.results.put(("models", studio.paint_models, [], None))
        studio.update_live({}, {}, False)
        assert studio.status.get() == "Ready. Enter a public source to inspect."
        assert "0 installed" in studio.page_status["models"]
    finally:
        studio.withdraw()
        gui.root.withdraw()


def test_the_brain_explains_itself_step_by_step(gui):
    from app.brain_mesh import BrainMesh
    from app.gui_brain import TOUR

    # The panels around the brain can be tall enough, after earlier tests
    # fill them, to leave a 600x220 canvas -- too short for region names to
    # clear the part names. Give the view room, as a real window has.
    gui.root.geometry("1540x1300-4000-4000")
    gui.root.deiconify()
    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view("operator")
    finally:
        gui._operator_suppress_auto_refresh = False
    gui.root.update()
    graph = gui.brain_graph
    graph.set_motion(False)
    graph.reset()
    graph.set_geometry("Procedural")
    graph.redraw()
    # The folded shell has 1,536 facets; those hidden behind the near side are skipped.
    assert len(type(graph)._shell_facets()) == 1536
    assert 700 < len(graph.find_withtag("mesh")) < 1536
    assert len(graph.find_withtag("fissure")) == 8
    before = graph.coords(graph.find_withtag("mesh")[0])
    graph.angle += .4
    graph.redraw()
    assert graph.coords(graph.find_withtag("mesh")[0]) != before
    graph.set_geometry("Sketchfab brain")
    assert graph.asset_loaded
    assert not graph.find_withtag("fissure")
    graph.reset()
    assert str(gui.brain_motion_button.cget("text")) == "Move 3D"
    gui.brain_motion_button.invoke()
    assert graph.motion and str(gui.brain_motion_button.cget("text")) == "Pause 3D"
    graph.set_motion(False)
    # Regions are named on the 3D view (at least five of seven at any
    # rotation), and never printed over a part's name.
    layers = {graph.itemcget(item, "text") for item in graph.find_withtag("layer")}
    assert layers <= {node.layer.upper() for node in BrainMesh.NODES} and len(layers) >= 5, (
        layers, graph.winfo_width(), graph.winfo_height(), graph.angle, graph.zoom)
    names = [graph.bbox(item) for item in graph.find_withtag("name")]
    for item in graph.find_withtag("layer"):
        a = graph.bbox(item)
        assert not any(a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1 for b in names)

    gui._toggle_brain_tour()
    assert graph.tour_edge == TOUR[0]
    detail = gui.brain_node_detail.get()
    assert f"step 1 of {len(TOUR)}" in detail and "Interface → Intent" in detail
    assert gui.brain_tour_controls.winfo_manager() and str(gui.brain_tour_button.cget("text")) == "End tour"
    assert "disabled" in gui.brain_tour_back.state()          # nothing before step 1
    for _ in range(len(TOUR) - 1):
        gui.brain_tour_next.invoke()
    assert graph.tour_edge == TOUR[-1] and str(gui.brain_tour_next.cget("text")) == "Finish"
    gui.brain_tour_back.invoke()
    assert graph.tour_edge == TOUR[-2]
    gui.brain_tour_next.invoke()
    gui.brain_tour_next.invoke()                               # Finish
    assert graph.tour_edge is None and not gui.brain_tour_controls.winfo_manager()
    assert str(gui.brain_tour_button.cget("text")) == "How it works"

    gui._toggle_brain_tour()
    gui._select_brain_node("memory")                           # clicking a part ends the tour and explains it
    assert graph.tour_edge is None and "Memory" in gui.brain_node_detail.get()
    gui._toggle_brain_tour()
    gui._toggle_brain_tour()                                    # the header button also ends it
    assert graph.tour_edge is None
    graph.set_motion(True)
    gui.show_view("chat")
    gui.root.withdraw()


def test_watch_mode_is_visible_and_its_notes_reach_the_chat(gui, tmp_path, monkeypatch):
    import time as _time
    from app.perception.camera.watch import WatchMode
    from tests.test_watch_mode import Camera

    # A fake camera: this test must never open the real webcam.
    monkeypatch.setattr("app.perception.camera.visibility.settle", lambda read, **kw: {"settled": True})
    camera = Camera(tmp_path)
    previous = getattr(gui.runtime, "watch", None)
    gui.runtime.watch = WatchMode(camera, privacy=lambda: False)
    try:
        mode = gui._watch()
        assert mode is gui.runtime.watch and mode.on_note is not None
        gui._toggle_watch_mode()
        deadline = _time.time() + 5
        while not mode.active and _time.time() < deadline:
            gui.root.update()
            _time.sleep(.02)
        gui.root.update()
        assert mode.active and camera.opened == 1
        gui._paint_watch_state()
        assert gui.watch_badge.cget("text") == "● WATCHING"
        assert str(gui.watch_button.cget("text")) == "Stop watching"
        mode.on_note("HAYAT noticed: A person sat down at the desk.")
        gui.root.update()
        assert "HAYAT noticed: A person sat down at the desk." in gui.chat.get("1.0", "end")
        gui._toggle_watch_mode()
        gui.root.update()
        assert not mode.active and camera.closed == 1
        assert gui.watch_badge.cget("text") == "" and str(gui.watch_button.cget("text")) == "Watch mode"
    finally:
        if gui.runtime.watch.active:
            gui.runtime.watch.stop()
        gui.runtime.watch = previous


def test_a_parts_own_history_and_the_brains_heat(gui, monkeypatch):
    from datetime import UTC, datetime, timedelta
    import json as _json
    from app.brain_history import reconstruct

    # This test supplies a fixed historical fixture. Live polling must not
    # replace it with the current event log while the event loop is pumped.
    monkeypatch.setattr(gui, '_load_brain_history', lambda: None)

    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view("operator")
    finally:
        gui._operator_suppress_auto_refresh = False
    start = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
    stamp = lambda seconds: (start + timedelta(seconds=seconds)).isoformat()
    history = reconstruct([
        (stamp(10), "TOOL_STARTED", "tools", _json.dumps({"tool": "read_file"}), "t1"),
        (stamp(130), "TOOL_COMPLETED", "tools", _json.dumps({"tool": "read_file"}), "t1"),
        (stamp(200), "CAMERA_OBSERVATION", "camera", _json.dumps({"source": "watch"}), None),
    ], start.timestamp(), start.timestamp() + 3600)
    gui._show_brain_history(history)
    gui.root.update()
    assert gui.brain_graph.heat.get("tools") == 1.0            # the busiest part glows fullest

    gui._select_brain_node("tools")
    assert gui.brain_timeline.focus_node == "tools" and gui.brain_timeline.focus_flows == {"tool"}
    detail = gui.brain_node_detail.get()
    assert "Busy for about 2.0 min in the last 1 hour" in detail and "click Tool Bus again" in detail
    gui._select_brain_node("tools")                            # again: everything
    assert gui.brain_timeline.focus_node is None

    gui._toggle_brain_heat()
    assert gui.brain_graph.heat == {} and str(gui.brain_heat_button.cget("text")) == "Heat off"
    gui._toggle_brain_heat()
    assert gui.brain_graph.heat and str(gui.brain_heat_button.cget("text")) == "Heat on"
    gui.show_view("chat")


def test_studio_keyboard_actions_and_failed_work_are_visible(gui, monkeypatch):
    calls = []
    failures = []
    gui.root.deiconify()
    _wait_for_window(gui, gui.root)
    gui._open_control_studio("osint")
    studio = gui._control_studio
    monkeypatch.setattr(studio, "inspect_source", lambda: calls.append("inspect"))
    monkeypatch.setattr(gui, "_record_failure", lambda title, exc: failures.append((title, str(exc))))
    try:
        _wait_for_window(gui, studio, focus=True)
        studio.source_entry.focus_set()
        _wait_until(gui, lambda: studio.focus_get() == studio.source_entry, "source input focus")
        studio.source_entry.event_generate("<Return>", when="tail")
        _wait_until(gui, lambda: calls == ["inspect"], "Enter to inspect the source")
        studio._next_page(1)
        assert studio.current_page == "self"

        def fail():
            raise RuntimeError("Provider unavailable")

        studio.run_job("Read source", fail, lambda result: None)
        assert studio.progress.winfo_manager() == "pack"

        def completed():
            studio.update_live({}, {}, False)
            return not studio.busy

        _wait_until(gui, completed, "failed studio work to finish")
        assert "Provider unavailable" in studio.status.get()
        assert not studio.progress.winfo_manager()
        assert failures == [("Read source", "Provider unavailable")]
    finally:
        studio.withdraw()
        gui.root.withdraw()


def test_status_polling_records_errors_without_flooding(monkeypatch):
    from app.gui import HayatGUI

    gui = object.__new__(HayatGUI)
    records = []
    gui._record_failure = lambda component, exc, **kwargs: records.append((component, kwargs["stage"]))
    clock = [10.0]
    monkeypatch.setattr("app.gui.time.monotonic", lambda: clock[0])
    for _ in range(4):
        gui._record_background_failure("Sensors", RuntimeError("unavailable"))
    gui._record_background_failure("Models", RuntimeError("unavailable"))
    assert records == [("Sensors", "gui_status"), ("Models", "gui_status")]
    clock[0] += 61
    gui._record_background_failure("Sensors", RuntimeError("unavailable"))
    assert len(records) == 3


def test_appearance_reuses_its_window_and_escape_stays_local(gui):
    escaped = []
    old_binding = gui.root.bind_all("<Escape>")
    gui.root.bind_all("<Escape>", lambda event: escaped.append(True), add="+")
    gui.root.deiconify()
    _wait_for_window(gui, gui.root)
    try:
        gui._show_appearance()
        window = gui._appearance_window
        _wait_for_window(gui, window, focus=True)
        gui._show_appearance()
        assert gui._appearance_window is window
        window.event_generate("<Escape>", when="tail")
        _wait_until(gui, lambda: not window.winfo_exists(), "Appearance to close")
        assert escaped == [], "Closing a dialog must not trigger the global Stop shortcut"
    finally:
        if gui._appearance_window.winfo_exists():
            gui._appearance_window.destroy()
        gui.root.tk.call("bind", "all", "<Escape>", old_binding)
        gui.root.withdraw()


def test_appearance_previews_then_restores_or_saves_theme_and_motion(gui, monkeypatch):
    from app.gui_theme import apply_theme, tokens
    from tkinter import font as tkfont

    saved = []
    monkeypatch.setattr(gui, "_save_appearance", lambda **changes: saved.append(changes) or True)
    original_theme = gui._theme_name
    original_motion = gui.depth_nav.motion
    original_scale = gui._text_scale
    original_chat_size = tkfont.Font(root=gui.root, font=gui.chat.cget("font")).actual("size")
    gui._operator_pane()
    gui.root.deiconify()
    try:
        dialog = gui._show_appearance() or gui._appearance_window
        dialog._appearance_choice.set("Light")
        dialog._appearance_motion.set(True)
        dialog._appearance_text_size.set("Large")
        assert gui._text_scale == 1.3
        assert tkfont.Font(root=gui.root, font=gui.chat.cget("font")).actual("size") > original_chat_size
        assert tkfont.Font(root=gui.root, font=gui.input.cget("font")).actual("size") > 12
        dialog._appearance_cancel()
        assert gui.BLACK == tokens(original_theme)["BLACK"]
        assert gui.depth_nav.motion == original_motion
        assert gui.brain_graph.motion == original_motion
        assert gui._text_scale == original_scale
        assert tkfont.Font(root=gui.root, font=gui.chat.cget("font")).actual("size") == original_chat_size
        assert saved == []

        gui._show_appearance()
        dialog = gui._appearance_window
        dialog._appearance_choice.set("High contrast")
        dialog._appearance_motion.set(True)
        dialog._appearance_text_size.set("Comfortable")
        dialog._appearance_save()
        dialog._appearance_cancel()
        assert gui.BLACK == "#000000"
        assert gui.depth_nav.motion is False
        assert gui.brain_graph.motion is False
        assert gui._text_scale == 1.15
        assert len(saved) == 1 and {key: saved[0][key] for key in ("theme", "motion", "text_scale")} ==             {"theme": "High contrast", "motion": False, "text_scale": 1.15}
        assert 0.4 <= saved[0]["glass_opacity"] <= 1.0  # the Glass transparency slider is saved too
    finally:
        if gui._appearance_window.winfo_exists():
            gui._appearance_window.destroy()
        gui._theme_name = original_theme
        gui._set_text_scale(original_scale)
        apply_theme(gui.root, type(gui), original_theme)
        gui.depth_nav.set_motion(original_motion)
        gui.root.withdraw()


def test_text_size_applies_to_new_windows_without_compounding(gui):
    from tkinter import font as tkfont

    old_scale = gui._text_scale
    popup = None
    try:
        gui._set_text_scale(1.5)
        gui.root.deiconify()
        popup = tkinter.Toplevel(gui.root)
        label = tkinter.Label(popup, text="Readable text", font=("Segoe UI", 9))
        label.pack()
        popup.update()
        size = lambda: tkfont.Font(root=gui.root, font=label.cget("font")).actual("size")
        assert size() == 14
        gui._set_text_scale(1.15)
        assert size() == 10
        gui._set_text_scale(1.5)
        assert size() == 14
    finally:
        if popup is not None and popup.winfo_exists():
            popup.destroy()
        gui._set_text_scale(old_scale)
        gui.root.withdraw()


def test_the_learn_window_teaches_arabic(gui):
    from app.learning.courses.arabic import all_items

    gui.root.deiconify()
    _wait_for_window(gui, gui.root)
    gui._open_learn()
    studio = gui._learn_studio
    try:
        _wait_for_window(gui, studio)
        _wait_until(gui, lambda: studio.exercise is not None and not studio.busy,
                    lambda: f"the first exercise (status={studio.status_var.get()!r}, text={studio.prompt_var.get()!r})")
        assert "Unit" in studio.unit_var.get() and studio.prompt_var.get()
        exercise, item = studio.exercise, all_items()[studio.exercise["item_id"]]
        answer = item.arabic if exercise["skill"] == "writing" else item.transliteration[0]
        studio.answer.insert(0, answer)
        studio.check()
        _wait_until(gui, lambda: not studio.busy and studio.feedback_var.get(), "the answer to be checked")
        assert any(word in studio.feedback_var.get() for word in ("Correct", "Right", "Well done", "Yes", "Exactly"))
        if not studio.keyboard.winfo_manager():
            studio.toggle_keyboard()
        studio.answer.configure(state="normal")
        studio.answer.delete(0, "end")
        studio._type("ب")
        assert studio.answer.get() == "ب"
        studio.show_alphabet()
        _wait_for_window(gui, studio._alphabet)
        cells = [w for w in studio._alphabet.winfo_children()[1].winfo_children()]
        assert len(cells) == 28
    finally:
        studio.close()
        gui.runtime.session.pending_learning = None
        gui.root.withdraw()
    assert not studio.winfo_exists()


def test_the_languages_window_shows_plans_before_installing(gui):
    gui.root.deiconify()
    gui._open_languages()
    studio = gui._languages_studio
    try:
        _wait_for_window(gui, studio)
        assert len(studio.tree.get_children()) == 53
        studio.tree.selection_set("ur")
        studio.plan_language()
        assert "Urdu" in studio.plan_text.get("1.0", "end") and studio.language_install.winfo_manager()
        studio.persona_var.set(True)
        studio.toggle_persona()
        assert gui.runtime.persona.enabled
        studio.reply_var.set("Spanish")
        studio.set_reply_language()
        assert gui.runtime.session.reply_language == "es"
    finally:
        gui.runtime.persona.set(False)
        gui.runtime.session.reply_language = "en"
        studio.destroy()
        gui.root.withdraw()


def test_the_searches_window_lists_and_cancels_searches(gui):
    import threading
    from types import SimpleNamespace

    release = threading.Event()

    class SlowResearch:
        def research(self, query, level="normal", remember=True):
            release.wait(5)
            return SimpleNamespace(answer="Found it [1].", confidence=0.8, sources=[1], claims=[], errors=[])

        def close(self):
            pass

    original = gui.runtime.research
    gui.runtime.research = SlowResearch()
    gui.root.deiconify()
    gui._open_searches()
    studio = gui._research_studio
    try:
        _wait_for_window(gui, studio)
        studio.query.insert(0, "shams al marif")
        studio.search()
        job_id = studio.tree.selection()[0]
        assert studio.tree.item(job_id, "values")[0] == "Searching…"
        studio.cancel()
        assert gui.runtime.research_jobs.get(job_id).status == "cancelled"
        assert studio.tree.item(job_id, "values")[0] == "Cancelled"
    finally:
        release.set()
        gui.runtime.research = original
        studio.close()
        gui.root.withdraw()


def test_the_security_window_lists_bans_and_lifts_them(gui, monkeypatch):
    from app.security.intrusion import BAN_THRESHOLD, IntrusionShield

    sh = IntrusionShield(None)
    for _ in range(BAN_THRESHOLD):
        sh.note_failed_login("203.0.113.7")
    monkeypatch.setattr("app.gui_security.SecurityStudio._shield", lambda self: sh)
    monkeypatch.setattr("app.safety.watch.summary", lambda: "Nothing on this machine is observably watching you.")
    gui.root.deiconify()
    gui._open_security()
    studio = gui._security_studio
    try:
        _wait_for_window(gui, studio)
        _wait_until(gui, lambda: studio.bans.get_children() == ("203.0.113.7",), "the async ban snapshot")
        studio.bans.selection_set("203.0.113.7")
        studio.unban()
        _wait_until(gui, lambda: sh.banned_for("203.0.113.7") == 0, "the async unban")
        _wait_until(gui, lambda: studio.bans.get_children() == (), "the ban list to clear")
    finally:
        studio.destroy()
        gui.root.withdraw()


def test_the_studios_menu_holds_every_launcher_and_the_row_stays_compact(gui):
    menu = gui._studios_menu
    labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]
    assert labels == ["What can HAYAT do?", "Switch window… (Ctrl+`)", "Command Center (everything live)", "Voice", "Models", "Learn a language", "Learning atlas", "Languages & persona",
                      "Searches", 'Reverse engineering (REA)', 'Screen & computer', 'Operation view', 'Private networking', 'Onion page navigator', 'Person detection', 'Enrolled people (local)', 'Local image tools', 'Replica app studio', 'Global intelligence', 'Explore & deep look', 'World atlas & video wall', 'My cameras',
                      'Wi-Fi sensing (CSI, movement, camera check)', 'Router & home network', 'Around me (hazard radar)', 'Location privacy (who can see where you are)', 'Keys (unlock cameras and stock prices)',
                      'Trade Center (crypto, stocks, world events)', 'Web3 explorer & agentic web (Web 4.0)',
                      'Accounts & sign-in (Google, Microsoft, GitHub…)', 'Jobs (search, match, apply)', 'HAYAT Wallet (decisions, pots, bills)', 'Portfolio & net worth (USD)', 'Plate recognition (ANPR)',
                      "Knowledge Library", "Security",
                      "Business", "OSINT research"]
    # Every entry invokes a real method without raising.
    for method in ("_open_control_studio", "_open_learn", "_open_languages", "_open_searches", "_open_knowledge", "_open_security",
                   "_open_business", '_open_intelligence', '_open_explore', '_open_atlas', '_open_my_cameras', '_open_keys', '_open_markets', '_open_web3', '_open_accounts', '_open_command_center', '_open_anpr', '_open_portfolio', '_open_money_desk', '_open_sensing', '_open_router', '_open_radar', '_open_location_privacy', '_open_enrolled_faces', '_open_local_image_tools'):
        assert callable(getattr(gui, method))
    # Studio launchers remain in one menu; the compact camera menu replaces
    # the old Reset button and exposes the shared 3D presets.
    menubuttons = [w for w in gui.depth_motion_button.master.winfo_children()
                   if type(w).__name__ == "Menubutton"]
    assert {button.cget('text') for button in menubuttons} == {'3D view ▾', 'Studios ▾'}


def test_knowledge_library_launcher_reuses_one_window(gui):
    gui._open_knowledge()
    window = gui._knowledge_studio
    try:
        gui._open_knowledge()
        assert gui._knowledge_studio is window
        assert window.title() == "HAYAT · Knowledge Library"
    finally:
        window.close()


def test_double_clicking_a_brain_part_opens_it(gui, monkeypatch):
    from app.brain_mesh import BrainMesh

    gui._operator_suppress_auto_refresh = True
    try:
        gui.show_view("operator")
    finally:
        gui._operator_suppress_auto_refresh = False
    # Every brain node maps to a destination, and each fires without error.
    opened = []
    for name in ("show_view", "_open_control_studio", "_open_security", "_show_memory",
                 "_open_searches", "_show_learning", "_show_remote", "open_camera_view", "_open_team_workspace",
                 "_show_doctor", "_run_self_check"):
        monkeypatch.setattr(gui, name, lambda *a, _n=name, **k: opened.append(_n))
    for node in BrainMesh.NODES:
        assert node.node_id in gui._BRAIN_DESTINATIONS, node.node_id
        gui._drill_brain_node(node.node_id)
    assert len(opened) == len(BrainMesh.NODES)   # every part opened something
    assert "open_camera_view" in opened and "_open_searches" in opened and "_open_security" in opened


def test_the_navigator_drills_on_double_click_only_on_a_node():
    from types import SimpleNamespace

    import app.gui_depth as depth

    calls = []
    nav = object.__new__(depth.DepthNavigator)
    nav.on_drill = calls.append
    nav._hit = lambda x, y: "memory" if x > 0 else None
    nav.select = lambda key: calls.append(f"select:{key}")
    nav.reset = lambda: calls.append("reset")
    nav._double(SimpleNamespace(x=10.0, y=5.0))
    assert calls == ["select:memory", "memory"]
    calls.clear()
    nav._double(SimpleNamespace(x=-10.0, y=5.0))    # empty space still resets the camera
    assert calls == ["reset"]


def test_report_views_say_what_is_coming_instead_of_sitting_blank(gui, monkeypatch):
    posted = []
    monkeypatch.setattr(gui, "_post", lambda callback, *args: posted.append((callback, args)))
    release = threading.Event()
    gui._background("Health", lambda: (release.wait(5), "all good")[1])
    text = gui.chat.get("1.0", "end")
    assert "Gathering Health…" in text
    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(posted) < 2:
        time.sleep(.02)
    for callback, args in posted:
        callback(*args)
    text = gui.chat.get("1.0", "end")
    assert "Gathering Health…" not in text and "all good" in text
    # The placeholder never reaches the saved transcript.
    gui._transcript.seek(0)
    assert "Gathering" not in gui._transcript.read()


def test_the_header_shows_what_can_be_withdrawn_and_opens_the_books(gui, tmp_path, monkeypatch):
    from app.safety.permissions import PermissionManager
    from app.tools.business import BusinessLedger

    ledger = BusinessLedger(tmp_path / "b.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
    monkeypatch.setattr(gui.runtime, "business", ledger)
    gui.refresh_wallet_badge()
    assert gui.wallet_var.get() == "Wallet 0.00"
    ledger.record_income("80", "Fiverr", "Gig")
    gui.refresh_wallet_badge()
    assert gui.wallet_var.get() == "Wallet 80.00 USD"
    opened = []
    monkeypatch.setattr(gui, "_open_business", lambda: opened.append(True))
    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    gui.root.update()
    try:
        gui.wallet_badge.event_generate("<Button-1>", x=4, y=4)
        gui.root.update()
    finally:
        gui.root.withdraw()
    assert opened


def test_help_guide_lists_every_area_and_tries_an_example(gui):
    from app import capabilities

    gui._open_help()
    guide = gui._help_guide
    try:
        assert len(guide.areas.get_children()) == len(capabilities.CATALOG)
        guide.query.set("wallet")
        guide.refresh()
        assert guide.areas.get_children() == ("money",)
        assert "Earn and manage money" in guide.title_var.get()
        guide.try_example("Show my wallet")
        assert gui.input.get("1.0", "end").strip() == "Show my wallet"
        assert gui.current_view == "chat"
    finally:
        if guide.winfo_exists():
            guide.destroy()
        gui.input.delete("1.0", "end")


def test_the_guide_opens_the_window_for_an_area(gui, monkeypatch):
    opened = []
    monkeypatch.setattr(gui, "_open_business", lambda: opened.append("business"))
    gui._open_help()
    guide = gui._help_guide
    try:
        guide.areas.selection_set("money")
        guide.show("money")
        guide.open_window()
        assert opened == ["business"]
    finally:
        guide.destroy()


def test_help_is_one_key_away_and_the_main_buttons_explain_themselves(gui):
    assert ("<F1>", "_open_help") in gui.SHORTCUTS
    gui._attach_tooltips()
    assert getattr(gui.help_button, "_hayat_tooltip", None) is not None
    assert gui.help_button.cget("text") == "Help"


def test_the_brain_status_is_one_line_with_the_full_summary_on_hover(gui):
    gui._operator_pane()
    gui.operator_summary.set("MESH READY  ·  17 nodes  ·  25 contracts  ·  team PASS  ·  1.20 ms\nTruth: x\nAuthority: y")
    line = gui.operator_status_line.get()
    assert "\n" not in line and line.startswith("MESH READY · 17 nodes")
    labels = [w for w in gui._operator_frame.winfo_children()[0].winfo_children()
              if w.winfo_class() == "Label" and getattr(w, "_hayat_tooltip", None) is not None]
    assert labels and "Authority: y" in labels[0]._hayat_tooltip.text


def test_the_status_bar_never_shows_bare_dashes(gui):
    gui._paint_statusbar({"state": "off", "mode": "off"})
    shown = " ".join(pill.value.get() for pill in gui.pills.values())
    assert ": --" not in shown and "Unknown" not in shown


# ------------------------------------------- readable replies, files and live work (2026-10-04)


def test_a_reply_shows_its_formatting_not_markdown_or_maths_symbols(gui):
    gui._append("assistant", "HAYAT\n## Working\n- Price: " + r"$\$80 \times 1.5 = \$120$" + "\n\n**The profit is $16.**")
    _pump(gui, 3)
    shown = gui.chat.get("1.0", "end")
    assert "$80 × 1.5 = $120" in shown and "The profit is $16." in shown
    assert "**" not in shown and "\times" not in shown and "## " not in shown


def test_an_image_named_in_the_owners_message_is_shown_in_the_chat(gui, tmp_path):
    from PIL import Image

    picture = tmp_path / "receipt.png"
    Image.new("RGB", (60, 40), (200, 200, 200)).save(picture)
    before = len(gui._inline_images)
    gui._append("user", f"You\nDescribe the image {picture}")
    _pump(gui, 3)
    assert len(gui._inline_images) == before + 1
    assert "Open full size" in gui.chat.get("1.0", "end")


def test_a_document_named_in_a_reply_gets_a_card_with_a_preview(gui, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("Monthly notes\nDay 1: sold 3 logos\n", encoding="utf-8")
    gui._append("assistant", f"HAYAT\nMy notes are in {notes}")
    deadline = time.monotonic() + 3
    while "Day 1: sold 3 logos" not in gui.chat.get("1.0", "end") and time.monotonic() < deadline:
        _pump(gui, 1)
    shown = gui.chat.get("1.0", "end")
    assert "notes.txt" in shown and "Day 1: sold 3 logos" in shown and "Show in folder" in shown


def test_everything_running_is_listed_in_plain_words(gui):
    from app.activity import ACTIVITY

    click = ACTIVITY.start("tool", "computer_click", "Save button")
    try:
        gui._paint_running(ACTIVITY.summary())
        assert "Clicking: Save button" in gui.running_text.get("1.0", "end")
    finally:
        ACTIVITY.finish(click)
    gui._paint_running(ACTIVITY.summary())
    assert "Nothing running in the foreground" in gui.running_text.get("1.0", "end")


def test_workspace_tabs_suggestion_chips_and_intelligence_card_in_the_rail(gui):
    opened = []
    for name in ("_open_intelligence", "_open_business"):
        setattr(gui, name, lambda name=name: opened.append(name))
    gui.nav_buttons["Global Intelligence"].configure(command=gui._open_intelligence)
    gui.nav_buttons["Business"].configure(command=gui._open_business)
    gui.nav_buttons["Global Intelligence"].invoke()
    gui.nav_buttons["Business"].invoke()
    assert opened == ["_open_intelligence", "_open_business"]
    assert set(gui.nav_buttons) == {"Global Intelligence", "Business", "Voice & Models", "Research", "Automation"}
    gui.suggestion_buttons["World now"].invoke()
    assert gui.input.get("1.0", "end-1c") == "What is happening around the world?"
    gui.input.delete("1.0", "end")
    card = getattr(gui, "_intelligence_home", None)
    if card is not None:
        assert card.dock_parent is gui.right_rail.body and card.map.winfo_manager() == "pack"
        assert card.panel_layout is not None and not card.panel_layout.floating
    for name in ("_open_intelligence", "_open_business"):
        delattr(gui, name)


def test_inline_intelligence_can_float_while_chat_remains_editable(gui):
    import os
    card = gui._intelligence_home
    panel = card.panel_layout
    gui.root.geometry('1536x900-4000-4000')
    gui.root.deiconify()
    gui._append('user', 'You\nTesting the inline intelligence panel')
    gui.root.update()
    try:
        if os.environ.get('HAYAT_SHOTS') == '1':
            from tests.test_gui_screenshots import _grab, _pump
            gui.root.geometry('1536x900+0+0')
            gui.root.attributes('-topmost', True)
            gui.root.lift()
            gui.right_rail.canvas.yview_moveto(max(0, card.winfo_rooty() - gui.right_rail.body.winfo_rooty())
                                              / max(1, gui.right_rail.body.winfo_height()))
            _pump(gui, .6)
            _grab(gui, '39-main-chat-inline-intelligence')
        panel.float()
        panel._place((260, 120, 430, 350))
        gui.input.insert('1.0', 'My unsent message')
        gui.root.update()
        assert card.winfo_toplevel() is gui.root and card.winfo_ismapped()
        assert panel.dock_button.winfo_width() >= panel.dock_button.winfo_reqwidth()
        assert gui.input.get('1.0', 'end-1c') == 'My unsent message'
        assert gui.send_button.winfo_ismapped() and gui.chat.winfo_ismapped()
        if os.environ.get('HAYAT_SHOTS') == '1':
            _pump(gui, .6)
            _grab(gui, '40-main-chat-floating-intelligence')
        panel.dock()
        gui.root.update()
        assert card in gui.right_rail.body.pack_slaves()
    finally:
        if panel.floating:
            panel.dock()
        gui.root.withdraw()


def test_a_running_conversation_folds_the_starting_points_and_keeps_them_reachable(gui):
    """Owner's screen, maximized: the transcript had 300 of 900 pixels under five button rows."""
    chips, controls = gui._suggestion_chips, gui._compose_controls
    try:
        gui._layout_state, gui._window_height = (False, False), 900
        gui._welcome_pending, gui._core_requested = True, False
        gui._show_welcome()
        assert not chips.winfo_manager() and not controls.winfo_manager()  # Home has its own cards and shortcuts.
        assert not gui._chat_tabs.winfo_manager()
        assert not gui._conversation_header.winfo_manager()
        gui._append("user", "You\nhello")
        assert not chips.winfo_manager() and not controls.winfo_manager()
        assert gui._chat_tabs.winfo_manager() and gui._conversation_header.winfo_manager()
        gui._window_height = gui.TALL_WINDOW + 100
        gui._fit_conversation()
        assert controls.winfo_manager() and not chips.winfo_manager()  # a tall window has room for the quick row
        labels = [gui.shortcut_menu.entrycget(index, "label") for index in range(gui.shortcut_menu.index("end") + 1)
                  if gui.shortcut_menu.type(index) != "separator"]
        assert {"Attach file", "Create image", "Start voice", "All tools & controls"} <= set(labels)
    finally:
        gui._window_height = 900
        gui._welcome_pending = True
        gui._show_welcome()


def test_the_status_bar_is_one_line(gui):
    bar = gui.pills["voice"].master.master
    first = bar.pack_slaves()[0]
    assert str(first.cget("textvariable")) == str(gui.health_var)  # packed before the pills, so it shares their row
    assert gui.pills["voice"].master.pack_info()["side"] == "left"


def test_starting_chips_put_the_owners_own_thread_first(gui):
    for text in ("read surah yasin", "search the web for GPU prices", "continue quran"):
        gui.runtime.situation.observe_turn(text)
    gui._refresh_suggestions()
    chips = gui._suggestion_chips
    assert chips.max_items == 6
    assert [button.cget("text") for button in chips.items[:2]] == ["Where we left off", "Continue Quran"]
    gui.input.delete("1.0", "end")
    gui.suggestion_buttons["Where we left off"].invoke()
    assert gui.input.get("1.0", "end-1c") == "Where did we leave off?"
    gui._refresh_suggestions()  # refreshing replaces, never duplicates
    assert [button.cget("text") for button in chips.items].count("Where we left off") == 1


def test_the_running_card_names_agent_teams_and_opens_their_workspace(gui, monkeypatch):
    from types import SimpleNamespace

    from app.activity import ACTIVITY
    from app.agents.capacity import TeamCapacity

    capacity = TeamCapacity(16384, 34, 90.0, 0, 4, "test machine", [])
    running = [{'current_worker': 'Job Search Agent', 'done': 1, 'total': 4}]
    monkeypatch.setattr(gui.runtime, "teams", SimpleNamespace(status=lambda: {'running': running, 'recent': []},
                                                              capacity=lambda refresh=False: capacity), raising=False)
    gui._running_painted = ""
    gui._paint_running(ACTIVITY.summary())
    text = gui.running_text.get("1.0", "end")
    assert "Agent teams: 1 running (Job Search Agent 1/4) · up to 34 workers each · 4 at once" in text
    # Synthetic clicks do not reach text-tag bindings off screen; the binding itself is checked.
    assert gui.running_text.tag_ranges("teams") and gui.running_text.tk.call(gui.running_text._w, "tag", "bind", "teams", "<Button-1>")


def test_a_standing_instruction_shows_above_the_composer_and_can_be_ended(gui):
    gui.runtime.session.standing_instruction = {"rule": "reply with exactly one word", "remaining": 2}
    gui._refresh_standing_chip()
    gui.root.update_idletasks()
    assert gui.standing_chip.winfo_manager() == "pack"
    assert gui.standing_text.cget("text") == "Standing instruction: reply with exactly one word · 2 answers left"
    gui._end_standing_instruction()
    assert gui.runtime.session.standing_instruction == {}
    assert not gui.standing_chip.winfo_manager()


def test_right_column_panels_fold_by_clicking_their_title_and_remember_it(gui):
    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    try:
        folds = gui._folds
        assert set(gui.FOLDED_BY_DEFAULT) <= set(folds.panels)
        folds.state.clear()
        for title, show in folds.panels.items():
            show(folds.folded(title))
        gui.root.update()
        assert not gui.console.winfo_ismapped()  # Live Console starts folded
        assert not folds.folded("Around me · hazard radar") and not folds.folded("Wi-Fi sensing")
        folds.panels["Live Console"](False)
        folds.set("Live Console", False)
        gui.root.update()
        assert gui.console.winfo_ismapped()
        reopened = type(folds)(folds.path, gui.FOLDED_BY_DEFAULT)
        assert reopened.folded("Live Console") is False and reopened.folded("System Info") is True
    finally:
        gui._folds.unfold_all()
        gui.root.withdraw()


def test_a_folded_panel_says_its_news_beside_its_title_and_folds_from_the_keyboard(gui):
    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    # Freeze the live health poll: it runs off-thread and would overwrite the
    # injected metrics (the folded summary is repainted from self._metrics),
    # which made this assertion race the real CPU/RAM reading.
    saved_next_health = gui._next_health_check
    gui._next_health_check = time.monotonic() + 1e9
    for worker in list(threading.enumerate()):
        if worker.name == "hayat-gui-health" and worker.is_alive():
            worker.join(timeout=5)
    try:
        folds = gui._folds
        folds.set("System Status", True)
        folds.panels["System Status"](True)
        gui._metrics = {"cpu": 12.4, "ram": 58.0, "gpu": 3.0, "disk": 70.2}
        gui._paint_metrics()
        gui.root.update()
        header = gui.gauges["cpu"].master.master.master.winfo_children()[0]
        peeks = [folds.peeks["System Status"]]
        assert peeks[0].cget("text") == "CPU 12% · RAM 58% · GPU 3% · Disk 70%"
        assert peeks[0].winfo_ismapped()

        # Open, the line goes away (the dials say it); Enter on the focused title folds it again.
        heading = next(child for child in header.winfo_children()
                       if isinstance(child, tkinter.Label) and child.cget("text") == "System Status")
        heading.focus_force()
        gui.root.update()
        heading.event_generate("<Return>")
        gui.root.update()
        assert not folds.folded("System Status") and not peeks[0].winfo_ismapped()
        assert gui.gauges["cpu"].winfo_ismapped() or gui.gauges["cpu"].master.winfo_manager()
        heading.event_generate("<Return>")
        gui.root.update()
        assert folds.folded("System Status") and peeks[0].winfo_ismapped()
        assert str(heading.cget("takefocus")) == "1"

        long = "x" * 120
        folds.summarise("System Status", long)
        assert peeks[0].cget("text").endswith("…") and len(peeks[0].cget("text")) <= 46
    finally:
        gui._next_health_check = saved_next_health
        gui._folds.unfold_all()
        gui.root.withdraw()


def test_collapse_all_and_expand_all_fold_every_panel_at_once(gui):
    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    gui.root.update()
    try:
        folds = gui._folds
        assert folds.panels, "the right column should have registered fold-aware panels"

        gui._collapse_all_panels()
        gui.root.update()
        assert all(folds.folded(title) for title in folds.panels), "collapse all should fold every panel"

        gui._expand_all_panels()
        gui.root.update()
        assert not any(folds.folded(title) for title in folds.panels), "expand all should open every panel"

        # Both controls are keyboard-reachable (Tab) and act on Enter, like the panel titles.
        assert str(gui._collapse_all_button.cget("takefocus")) == "1"
        assert str(gui._expand_all_button.cget("takefocus")) == "1"
        gui._collapse_all_button.focus_force()
        gui.root.update()
        gui._collapse_all_button.event_generate("<Return>")
        gui.root.update()
        assert all(folds.folded(title) for title in folds.panels)
    finally:
        gui._folds.unfold_all()
        gui.root.withdraw()


def test_keyboard_shortcut_sheet_lists_every_binding_in_plain_words(gui):
    from app.gui import HayatGUI

    # The readable key formatting is exact, not Tk event syntax.
    assert HayatGUI._pretty_key("<Control-Shift-P>") == "Ctrl+Shift+P"
    assert HayatGUI._pretty_key("<Control-slash>") == "Ctrl+/"
    assert HayatGUI._pretty_key("<Escape>") == "Esc"
    assert HayatGUI._pretty_key("<F11>") == "F11"

    gui.root.geometry("1540x920-4000-4000")
    gui.root.deiconify()
    gui.root.update()
    try:
        gui._show_shortcuts()
        gui.root.update()
        window = gui._shortcuts_window
        assert window.winfo_exists()
        # Every bound shortcut appears, with a readable key and a plain-words label.
        keys = {key for key, _label in window._rows}
        labels = {label for _key, label in window._rows}
        assert len(window._rows) == len(HayatGUI.SHORTCUTS)
        for sequence, method in HayatGUI.SHORTCUTS:
            assert HayatGUI._pretty_key(sequence) in keys
        assert "Command palette" in labels and "Privacy mode on / off" in labels
        assert all(not label.startswith("_") for label in labels)  # no raw method names leak through

        # Escape closes it, and opening again reuses the one window.
        window.event_generate("<Escape>")
        gui.root.update()
        assert not window.winfo_exists()
    finally:
        existing = getattr(gui, "_shortcuts_window", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()
        gui.root.withdraw()


def test_no_folded_panel_cuts_its_news_short(gui):
    """With a button in the header (Details, Refresh) the news goes under the title; nowhere is it clipped."""
    gui.root.geometry("1536x900-4000-4000")
    gui.root.deiconify()
    try:
        folds = gui._folds
        for title in gui.FOLDED_BY_DEFAULT:
            folds.set(title, True)
            folds.panels[title](True)
            folds.summarise(title, "Remote off · 0 paired · Balanced memory")
        gui.root.update()
        for title in gui.FOLDED_BY_DEFAULT:
            peek = folds.peeks[title]
            assert peek.winfo_manager(), title
            assert peek.winfo_width() >= peek.winfo_reqwidth(), f"{title}: its folded line is cut off"
    finally:
        gui._folds.unfold_all()
        gui.root.withdraw()


def test_no_button_in_the_right_column_is_cut_short(gui):
    """Rows of buttons wrap rather than clip ("Anywh", "Discor", "Learr" and a missing Adaptive Max before)."""
    gui.root.geometry("1536x900-4000-4000")
    gui.root.deiconify()
    try:
        gui._folds.unfold_all()
        gui.root.update()

        def buttons(widget):
            for child in widget.winfo_children():
                if isinstance(child, tkinter.ttk.Button):
                    yield child
                yield from buttons(child)

        found = [button for button in buttons(gui.right_rail.body) if button.winfo_ismapped()]
        assert len(found) > 15
        cut = [button.cget("text") for button in found if button.winfo_width() < button.winfo_reqwidth() - 1]
        assert not cut, f"cut short: {cut}"
        labels = {button.cget("text") for button in found}
        assert {"Adaptive Max", "Anywhere mTLS", "Disconnect All", "Analyze 5 Frames"} <= labels
    finally:
        gui._folds.unfold_all()
        gui.root.withdraw()


def test_a_button_row_wraps_instead_of_cutting_and_fills_its_last_row(gui):
    from app.gui_panels import ButtonRow

    top = tkinter.Toplevel(gui.root)
    top.geometry("260x200-4000-4000")
    try:
        row = ButtonRow(top, type(gui))
        row.pack(fill="x")
        buttons = [row.add(text, lambda: None) for text in ("Off", "Same Wi-Fi", "Anywhere mTLS")]
        top.update()
        assert all(button.winfo_width() >= button.winfo_reqwidth() - 1 for button in buttons)
        assert len({int(button.grid_info()["row"]) for button in buttons}) > 1  # wrapped
        last = buttons[-1].grid_info()
        assert int(last["column"]) + int(last["columnspan"]) == row._columns  # no hole beside the last one
        top.geometry("900x200-4000-4000")
        top.update()
        assert {int(button.grid_info()["row"]) for button in buttons} == {0}  # wide enough: one row again
    finally:
        top.destroy()


def _tab_spans(notebook):
    """Vertical extent of each tab, found by asking the notebook which tab is under each point."""
    spans = {}
    for x in range(0, notebook.winfo_width(), 2):
        for y in range(0, 80):
            try:
                index = notebook.index(f"@{x},{y}")
            except tkinter.TclError:
                continue
            if index == "":
                continue
            low, high = spans.get(index, (y, y))
            spans[index] = (min(low, y), max(high, y))
    return spans


def test_the_selected_tab_is_as_tall_as_the_others(gui):
    """clam shrank the selected tab (padding "6 4 6 2"), so the chosen tab sat lower and the others looked active."""
    top = tkinter.Toplevel(gui.root)
    top.geometry("520x160-4000-4000")
    try:
        for style in ("TNotebook", "Side.TNotebook"):
            notebook = tkinter.ttk.Notebook(top, style=style)
            notebook.pack(fill="x")
            for text in ("Details", "Media", "Sources"):
                notebook.add(tkinter.ttk.Frame(notebook), text=text)
            top.update()
            spans = _tab_spans(notebook)
            assert len(spans) == 3 and len(set(spans.values())) == 1, (style, spans)
            notebook.destroy()
    finally:
        top.destroy()


def test_conversation_details_title_and_counts_sit_on_the_panel_colour(gui):
    """They were ttk labels in the window's black, two black boxes on the Conversation panel."""
    details = gui.chat_details
    labels = [child for child in details.winfo_children() if isinstance(child, tkinter.Label)]
    texts = {str(label.cget("textvariable")) for label in labels}
    assert {str(details.subject), str(details.stats)} <= texts
    assert all(label.cget("bg") == details.cget("bg") for label in labels)
    details.reset()
    assert details.stats.get() == "0 messages · 0 media cards · 0 links"


def test_the_slow_status_poll_survives_a_runtime_without_a_conversation(gui):
    """A missing piece used to raise past the reset, so the slow poll never ran again."""
    from types import SimpleNamespace

    original = gui.runtime
    gui.runtime = SimpleNamespace()
    try:
        gui._slow_poll_running = True
        gui._refresh_slow()
        assert gui._slow_poll_running is False
        assert gui._slow["answer_cache"] == {"exact": 0, "paraphrase": 0}
    finally:
        gui.runtime = original


def test_the_location_privacy_window_lists_findings_and_protections(gui, monkeypatch):
    """Studios → Location privacy runs the check off the Tk thread and shows what to do about each finding."""
    from app import location_privacy as lp

    _stand_in_masks(gui, monkeypatch)
    monkeypatch.setattr(lp.LocationPrivacy, "public_ip_view", lambda self: {
        "checked": True, "city": "Detroit", "region": "Michigan", "country": "United States", "network": "T-Mobile",
        "latitude": 42.33, "longitude": -83.05})
    monkeypatch.setattr(lp, "photos_with_gps", lambda folders, limit=600: {"scanned": 0, "limit": limit,
                                                                          "with_location": [], "count": 0, "folders": []})
    gui.root.deiconify()  # a window attached to a hidden main window stays hidden on Windows
    gui._open_location_privacy()
    window = gui._location_privacy_window
    try:
        deadline = time.monotonic() + 30
        while window.report is None and time.monotonic() < deadline:
            gui.root.update()
            time.sleep(0.02)
        assert window.report is not None
        findings = window.findings.get("1.0", "end")
        assert "Any website sees your internet address and can place you near Detroit" in findings
        assert "masked browser" in findings and "VPN app" not in findings
        assert "never written to disk" in window.protections.get("1.0", "end")
        assert window.chips["guard"].cget("text") == "Guard on"
        assert window.chips["places"].cget("text").startswith("Place names:")
        gui._open_location_privacy()  # a second open raises the same window
        assert gui._location_privacy_window is window
        _wait_for_window(gui, window, focus=True)
        window.event_generate("<Escape>", when="tail")
        _wait_until(gui, lambda: not window.winfo_exists(), "Escape to close Location privacy")
    finally:
        if window.winfo_exists():
            window.destroy()
        gui.root.withdraw()


def test_location_privacy_findings_carry_a_button_that_opens_their_setting(gui, monkeypatch):
    from app import location_privacy as lp

    _stand_in_masks(gui, monkeypatch)
    report = {"kind": "location_privacy", "protections": ["Place names come from GeoNames on this computer; x."],
              "guard": {"guard_on": True, "fix_known": False, "recent": []}, "findings": [
                  {"level": "watch", "what": "In the last 30 days these apps read your location: msedge.exe (today 10:00).",
                   "fix": "Settings → Privacy & security → Location.", "action": "windows"},
                  {"level": "watch", "what": "Chrome lets https://chatgpt.com see your location.", "fix": "Remove it.",
                   "action": "chrome"},
                  {"level": "info", "what": "The Windows location service is on.", "fix": "Settings.", "action": "windows"},
                  {"level": "watch", "what": "Any website sees your internet address.", "fix": "A VPN."}]}
    # The registry holds the check bound at registration, so the tool itself is replaced.
    monkeypatch.setattr(gui.runtime.registry.get("location_privacy_check"), "handler", lambda scan_photos=True: report)
    opened = []
    monkeypatch.setattr(lp, "open_setting", lambda target: opened.append(target) or {"opened": True, "note": "Opened."})
    gui.root.deiconify()
    gui._open_location_privacy()
    window = gui._location_privacy_window
    try:
        _wait_until(gui, lambda: window.report is not None, "the location privacy check")
        labels = [button.cget("text") for button in window.action_buttons]
        assert labels == ["Open Windows location settings", "Open Chrome location permissions"]  # one per page
        window.action_buttons[1].invoke()
        _wait_until(gui, lambda: opened == ["chrome"] and window.status.get() == "Opened.", "opening Chrome's page")
        window.show(report)  # a refresh replaces the buttons instead of piling them up
        assert len(window.action_buttons) == 2 and all(button.winfo_exists() for button in window.action_buttons)
    finally:
        window.destroy()
        gui.root.withdraw()


def test_tick_boxes_and_spin_boxes_are_dark_like_the_fields_beside_them(gui):
    """clam drew a white square with a dark cross, and a white spin box, on every dark panel."""
    style = tkinter.ttk.Style(gui.root)
    palette = type(gui)
    assert style.lookup("TCheckbutton", "indicatorbackground") == palette.PANEL
    assert style.lookup("TCheckbutton", "indicatorforeground") == palette.GOLD
    assert style.lookup("TSpinbox", "fieldbackground") == palette.PANEL  # recessed field, raised buttons
    assert style.lookup("TSpinbox", "arrowcolor") == palette.WHITE


def _masks_report(masked=False, installed=True):
    """A masks report like app/privacy_masks.PrivacyMasks.status gives, with no VPN connected."""
    return {"kind": "privacy_masks", "masks_on": 2 if masked else 1, "vpn": {"connected": False, "adapters": []},
            "layers": [{"layer": "Tor for HAYAT's web reading (3 relays)", "on": masked,
                        "detail": "On: research, world feeds, market data and pages go through Tor" if masked else "Off"},
                       {"layer": "Masked browser for your browsing", "on": False,
                        "detail": "None open: your Edge or Chrome through Tor" if masked else "Needs Tor on first"},
                       {"layer": "HAYAT's location guard", "on": True, "detail": "Messages never carry your location"}],
            "leaks": [{"level": "watch", "what": "Your browsers reach websites with your own address.",
                       "fix": "Browse in HAYAT's masked browser."}],
            "tor": {"installed": installed, "masked": masked},
            "computer_seen_as": {"checked": True, "place": "Detroit, Michigan, United States", "network": "T-Mobile"},
            "hayat_seen_as": {"checked": masked, "place": "Stockholm, Sweden"},
            "free_vpns": {}, "limits": "Masks hide where a connection comes from, not who you are."}


def _stand_in_masks(gui, monkeypatch, state=None):
    """The masks check asks Windows and a website; window tests use a fixed report instead."""
    state = state if state is not None else {"masked": False}
    monkeypatch.setattr(gui.runtime.registry.get("privacy_masks_status"), "handler",
                        lambda check_internet=True: _masks_report(masked=state["masked"]))
    return state


def test_blocked_tor_without_installation_still_offers_explicit_unmask(gui, monkeypatch):
    report = _masks_report(installed=False)
    report["tor"].update(routing_required=True, state="blocked")
    report["coverage"] = {"not_verified": ["ordinary browser automation", "native media players"]}
    monkeypatch.setattr(gui.runtime.registry.get("privacy_masks_status"), "handler", lambda check_internet=True: report)
    monkeypatch.setattr(gui.runtime.registry.get("location_privacy_check"), "handler", lambda scan_photos=True: {
        "kind": "location_privacy", "findings": [], "protections": [], "guard": {"guard_on": True, "recent": []}})
    calls = []
    monkeypatch.setattr(gui.runtime.registry.get("hayat_mask_off"), "handler", lambda: calls.append("unmask") or {"masked": False})
    gui.root.deiconify()
    gui._open_location_privacy()
    window = gui._location_privacy_window
    try:
        _wait_until(gui, lambda: getattr(window, "masks_report", None) is not None, "the blocked Tor snapshot")
        buttons = window.mask_cards["tor"]["buttons"].winfo_children()
        assert [button.cget("text") for button in buttons] == ["Unmask HAYAT", "Set up Tor (21 MB)"]
        assert "ordinary browser automation" in window.seen.get()
        assert not window.mask_cards["browser"]["buttons"].winfo_children()
        buttons[0].invoke()
        _wait_until(gui, lambda: calls == ["unmask"], "the explicit unmask action")
    finally:
        window.destroy()
        gui.root.withdraw()


def test_the_masks_cards_show_each_layer_and_mask_and_unmask_hayat(gui, monkeypatch):
    state = _stand_in_masks(gui, monkeypatch)
    calls = []

    def mask(on):
        def run():
            calls.append(on)
            state["masked"] = on
            return {"masked": on, "note": "Masked." if on else "Unmasked."}
        return run
    monkeypatch.setattr(gui.runtime.registry.get("hayat_mask_on"), "handler", mask(True))
    monkeypatch.setattr(gui.runtime.registry.get("hayat_mask_off"), "handler", mask(False))
    monkeypatch.setattr(gui.runtime.registry.get("location_privacy_check"), "handler", lambda scan_photos=True: {
        "kind": "location_privacy", "findings": [], "protections": [], "guard": {"guard_on": True, "fix_known": False,
                                                                               "recent": []}})
    gui.root.deiconify()
    gui._open_location_privacy()
    window = gui._location_privacy_window

    def card_buttons(key):
        return [child.cget("text") for child in window.mask_cards[key]["buttons"].winfo_children()]
    try:
        _wait_until(gui, lambda: getattr(window, "masks_report", None) is not None, "the masks check")
        assert window.masks_count.cget("text") == "1 of 3 on"
        assert window.mask_cards["guard"]["light"].cget("text").startswith("● ")
        assert window.mask_cards["browser"]["light"].cget("text").startswith("○ ")
        assert window.mask_cards["browser"]["state"].cget("text") == "Needs Tor on first"
        assert card_buttons("browser") == []  # nothing to open without Tor, and no third-party VPN app is ever offered
        assert window.masks_test_button.cget("text") == "Test again"
        assert card_buttons("tor") == ["Mask HAYAT with Tor"]
        assert "Websites see your computer near Detroit" in window.seen.get()
        window.mask_cards["tor"]["buttons"].winfo_children()[0].invoke()
        _wait_until(gui, lambda: card_buttons("tor") == ["Unmask HAYAT"], "the Tor card to offer Unmask")
        assert calls == [True] and window.masks_count.cget("text") == "2 of 3 on"
        assert "and HAYAT near Stockholm, Sweden, through Tor" in window.seen.get()
        assert card_buttons("browser") == ["Open masked Edge", "Open masked Chrome"]
        window.mask_cards["tor"]["buttons"].winfo_children()[0].invoke()
        _wait_until(gui, lambda: card_buttons("tor") == ["Mask HAYAT with Tor"], "the Tor card to offer Mask again")
        assert calls == [True, False]
    finally:
        window.destroy()
        gui.root.withdraw()
