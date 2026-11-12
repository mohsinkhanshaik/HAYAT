"""The GUI shows the day's new abilities where the owner looks (2026-11-08).

* the Knowledge Library imports a ChatGPT or Claude export (app/chat_import.py);
* each reply in the chat ends with what it sent out of this computer (app/outbound.py);
* the conversation details say how misspelt words were read (app/intent/spelling.py).
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

from app.gui_knowledge import KnowledgeStudio
from app.gui_theme import Palette
from app.memory.database import MemoryDatabase


class FakeGUI(Palette):
    def __init__(self, root, store, memory):
        self.root = root
        self.runtime = SimpleNamespace(research=SimpleNamespace(knowledge=store), memory=memory,
                                       authority=SimpleNamespace(privacy_mode=False))

    def show_view(self, _view):
        pass


def pump(window, predicate, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        window.update()
        if predicate():
            return True
        time.sleep(.01)
    return predicate()


def test_the_knowledge_library_imports_a_chatgpt_export(shared_tk, tmp_path):
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    export = tmp_path / "export"
    export.mkdir()
    (export / "conversations.json").write_text(json.dumps([{
        "id": "c-1", "title": "Arabic verbs", "create_time": 1767225600,
        "mapping": {"b": {"message": {"author": {"role": "user"}, "content": {"parts": ["Past tense in Arabic?"]},
                                      "create_time": 2}}}}]), encoding="utf-8")
    store = SimpleNamespace(search_claims=lambda *a, **k: [], stats=lambda: {"claims": 0, "sources": 0})
    window = KnowledgeStudio(FakeGUI(shared_tk, store, memory))
    window.withdraw()
    try:
        assert window.import_button.cget("text") == "Import ChatGPT / Claude history…"
        window.import_chat_history(str(export))
        assert pump(window, lambda: "Imported 1 conversation (1 from ChatGPT)" in window.status.get())
        assert str(window.import_button.cget("state")) == "normal"
        window.import_chat_history(str(tmp_path / "missing"))
        assert pump(window, lambda: window.status.get().startswith("Could not import"))
    finally:
        window.destroy()
        shared_tk.withdraw()
    assert memory.retrieve("arabic past tense", limit=1)[0]["source"] == "imported_chat"


def test_each_reply_in_the_chat_says_what_left_this_computer(shared_tk):
    from tests.test_gui import _build_window, _release

    gui = _build_window()
    try:
        gui._append("assistant", "HAYAT\nParis is the capital of France.")
        gui._append_privacy_line({"nothing_sent": True, "model_runs_on": "this computer"})
        gui._append("assistant", "HAYAT\nIt is 31 °C in Dubai.")
        gui._append_privacy_line({"nothing_sent": False, "model_runs_on": "this computer", "pages": 2,
                                  "searches": [{"query": "weather in dubai", "engines": ["bing"]}],
                                  "sites": {"www.weather.gov": 2}, "actions": []})
        gui.root.update()
        text = gui.chat.get("1.0", "end")
        assert "🔒 Answered on this computer; nothing was sent out." in text
        assert "🌐 The reply model ran on this computer. Sent out: 1 web search (“weather in dubai”)" in text
        privacy = gui.chat.tag_ranges("privacy")
        assert len(privacy) >= 4  # both lines carry the quiet style
    finally:
        _release(gui)


def test_the_details_panel_says_how_misspelt_words_were_read():
    from app.answer_provenance import describe

    runtime = SimpleNamespace(session=SimpleNamespace(
        last_answer_source="exact", last_spelling=[["reserach", "research"], ["quantam", "quantum"]],
        last_outbound={"nothing_sent": True, "model_runs_on": "this computer"}))
    lines = describe(runtime)
    assert "Read your words “reserach” as “research”, “quantam” as “quantum”." in lines
    assert lines[-1] == "Answered on this computer; nothing was sent out."


def test_the_invention_research_tab_lists_the_papers_and_the_look_back(shared_tk, tmp_path):
    from app.gui_invention import InventionPanel
    from app.safety.permissions import PermissionManager
    from app.tools.invention import register_invention_tools
    from app.tools.registry import ToolRegistry
    from tests.conftest import close_window, open_window
    from tests.test_executive_invention import ScriptedModel, accepted, bench, candidate

    workbench = bench(tmp_path, ScriptedModel(candidate(), accepted()))
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    runtime = SimpleNamespace(registry=registry, invention=workbench, session=SimpleNamespace(session_id='gui'))
    root = open_window(shared_tk, '960x700-4000-4000')
    panel = InventionPanel(root, Palette, runtime, lambda *_: None)
    panel.pack(fill='both', expand=True)
    try:
        panel.data = {'research': None,
                      'literature': {'question': 'Pendulum period', 'at': '2026-11-08', 'papers': [
                          {'title': 'Large-amplitude pendulum', 'year': 2009, 'citations': 41,
                           'url': 'https://doi.org/10.1/abc', 'abstract': 'The period grows with amplitude.'}]},
                      'literature_followup': {'question': 'Pendulum period large theta correction beyond the simple model',
                                              'why': 'Two proposals in a row did not beat the best formula.',
                                              'papers': []}}
        panel.paint_research()
        text = panel.research_text.get('1.0', 'end')
        assert 'Published work read before proposing' in text and 'cited 41×' in text
        assert 'Back to the literature' in text and 'large theta' in text
        assert 'never evidence that a formula works' in text
        rows = panel.source_list.get_children()
        assert rows and panel.source_list.item(rows[0], 'text') == 'Paper: Large-amplitude pendulum'
        panel.source_list.selection_set(rows[0])
        panel.source_selected()
        assert 'The period grows with amplitude.' in panel.research_text.get('1.0', 'end')
    finally:
        close_window(root)
