from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from tkinter import ttk

import httpx
import pytest

from app.connected_ai import ConnectedAI
from app.gui_connected_ai import ConnectedAIStudio
from app.gui_theme import Palette, apply_text_scale, apply_theme, style_controls, style_hayat_buttons
from app.integrations.secrets import EncryptedFileBackend, SecretBroker
from tests.conftest import close_window, open_window
from tests.test_connected_ai import final, tool_response


@pytest.fixture
def desk(shared_tk, tmp_path):
    owner = open_window(shared_tk, "1100x790+10+10")

    class GUI(Palette):
        root = owner

    ttk.Style(owner).theme_use("clam")
    style_controls(owner, GUI)
    style_hayat_buttons(owner, GUI)
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "large-research-model"}, {"id": "another-model"}]})
        return httpx.Response(200, json=final("openai"))

    broker = SecretBroker(tmp_path / "secrets" / "metadata.db", backend=EncryptedFileBackend(tmp_path / "secrets"))
    manager = ConnectedAI(tmp_path / "settings", broker=broker, transport=httpx.MockTransport(handler))
    window = ConnectedAIStudio(GUI(), manager=manager)

    def settle(done=None):
        deadline, quiet = time.monotonic() + 8, time.monotonic() + .15
        while time.monotonic() < deadline:
            shared_tk.update()
            if done() if done else time.monotonic() >= quiet:
                return
            time.sleep(.005)
        pytest.fail("Connected AI did not settle")

    settle()
    try:
        yield SimpleNamespace(window=window, manager=manager, requests=requests, settle=settle, tk=shared_tk, palette=GUI)
    finally:
        apply_text_scale(owner, 1.0)
        if not window._closed:
            window.close()
        close_window(owner)


def configure(desk):
    window = desk.window
    window.key.set("gui-test-secret")
    window.save_key()
    desk.settle(lambda: not window._busy)
    window.enabled.set(True)
    window.model.set("large-research-model")
    assert window.save_settings()


def test_connections_discovery_chat_evidence_and_export(desk, tmp_path):
    window = desk.window
    assert not desk.requests and not desk.manager.settings()["enabled"]
    configure(desk)
    assert window.key.get() == "" and "Key saved" in window.key_status.get()
    window.discover()
    desk.settle(lambda: not window._busy)
    assert window.model.get() == "large-research-model"
    assert set(window.model_box.cget("values")) == {"large-research-model", "another-model"}
    source = tmp_path / "notes.md"
    source.write_text("Measured response: 4.\nOnly one sample.", encoding="utf-8")
    window.attach_path(source)
    desk.settle(lambda: not window._busy)
    assert window.sources.get_children() == ("D1",)
    window.prompt.insert("1.0", "Explain the measurement")
    assert window.send()
    desk.settle(lambda: not window._busy)
    assert len(window.history) == 2
    assert "selected-version" in window.transcript.get("1.0", "end")
    assert "input_tokens" in window.status.get()
    assert "sha256" in window.evidence.get("1.0", "end")
    exported = tmp_path / "export.json"
    window.export_to(exported)
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["answers"][0]["requested_model"] == "large-research-model"
    assert "gui-test-secret" not in exported.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        window.export_to(source)
    with pytest.raises(ValueError):
        window.export_to(desk.manager.path)
    window.prompt.insert("1.0", "What about uncertainty?")
    window.send()
    desk.settle(lambda: not window._busy)
    body = json.loads(desk.requests[-1].content)
    assert len(body["input"]) == 4  # system + prior pair + follow-up
    window.clear()
    assert window.history == [] and window.records == [] and window.sources.get_children()
    window.remove_sources()
    assert not window.research.documents and not window.sources.get_children()
    window.forget_key()
    desk.settle(lambda: not window._busy)
    assert not desk.manager.has_key("openai")


def test_provider_failure_preserves_prompt_without_fake_response(desk):
    configure(desk)
    desk.manager.transport = httpx.MockTransport(lambda _: httpx.Response(429, json={"error": "gui-test-secret"}))
    window = desk.window
    window.prompt.insert("1.0", "Research this question")
    window.send()
    desk.settle(lambda: not window._busy)
    assert "429" in window.status.get() and "No fallback" in window.status.get()
    assert "Research this question" in window.prompt.get("1.0", "end")
    assert not window.history and not window.records
    assert "gui-test-secret" not in window.transcript.get("1.0", "end")


def test_stop_and_close_do_not_publish_late_answers_or_touch_tk_from_worker(desk):
    configure(desk)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    ids = []

    def handler(_request):
        ids.append(threading.get_ident())
        started.set()
        release.wait(3)
        finished.set()
        return httpx.Response(200, json=final("openai"))

    desk.manager.transport = httpx.MockTransport(handler)
    window = desk.window
    window.prompt.insert("1.0", "Slow request")
    assert window.send()
    desk.settle(started.is_set)
    assert not window.send()
    window.stop()
    release.set()
    desk.settle(lambda: not window._busy)
    assert "Stopped" in window.status.get() and not window.history
    assert ids[0] != threading.get_ident()
    release.clear()
    started.clear()
    finished.clear()
    window.send()
    desk.settle(started.is_set)
    window.close()
    release.set()
    desk.settle(finished.is_set)
    assert window._closed and window._poll_handle is None


@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_compact_layout_keeps_send_and_connection_controls_reachable(desk, scale):
    window = desk.window
    window.geometry("760x560+20+20")
    apply_text_scale(window, scale)
    desk.settle()
    bottom = window.winfo_rooty() + window.winfo_height()
    right = window.winfo_rootx() + window.winfo_width()
    assert window.transcript.winfo_height() > 50
    for widget in (window.prompt, window.send_button, window.stop_button):
        assert widget.winfo_viewable()
        assert widget.winfo_rooty() + widget.winfo_height() <= bottom
        assert widget.winfo_rootx() + widget.winfo_width() <= right
    window.tabs.select(window.pages["Connections"])
    desk.settle()
    assert window.save_button.winfo_viewable()
    assert window.save_button.winfo_rooty() + window.save_button.winfo_height() <= bottom
    assert window.connection_canvas.winfo_height() > 150
    window.connection_canvas.yview_moveto(1)
    desk.settle()
    if scale == 1.5:
        assert window.connection_canvas.yview()[0] > 0


def test_synthetic_research_and_connections_visuals(desk):
    if os.environ.get("HAYAT_CONNECTED_AI_SHOTS") != "1":
        pytest.skip("Opt-in local screenshots")
    from PIL import ImageGrab

    window = desk.window
    apply_theme(window, desk.palette, "Nebula")
    configure(desk)
    request_count = 0

    def handler(_request):
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=tool_response("openai") if request_count == 1 else final(
            "openai", "SYNTHETIC TEST RESPONSE\n\nThe measured answer is 4. The calculator confirms 2 + 2 = 4.\n\n"
            "Evidence\nThe tool result is available in Evidence, along with API usage and the requested and returned model IDs.\n\n"
            "Limitations\nThis is a mocked API response for interface testing. No live API request was made."))

    desk.manager.transport = httpx.MockTransport(handler)
    window.prompt.insert("1.0", "Calculate 2 + 2 and show the evidence.")
    window.send()
    desk.settle(lambda: not window._busy)
    output = Path(".test-runs/gui-shots")
    output.mkdir(parents=True, exist_ok=True)
    for tab in ("Research", "Connections", "Evidence"):
        window.tabs.select(window.pages[tab])
        desk.settle()
        ImageGrab.grab(window=int(window.wm_frame(), 16)).save(output / f"connected-ai-{tab.lower()}.png")
