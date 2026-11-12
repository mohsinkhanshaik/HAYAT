"""The owner's ChatGPT and Claude exports become memories (app/chat_import.py)."""
from __future__ import annotations

import json

import pytest

from app.chat_import import import_conversations, memory_text, read_export
from app.memory.database import MemoryDatabase

CHATGPT = [{
    "id": "c-1", "title": "Arabic verbs", "create_time": 1767225600,
    "mapping": {
        "a": {"message": {"author": {"role": "system"}, "content": {"parts": ["You are ChatGPT"]}, "create_time": 1}},
        "b": {"message": {"author": {"role": "user"}, "content": {"parts": ["How do past tense verbs work in Arabic?"]},
                          "create_time": 2}},
        "c": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["They use patterns..."]},
                          "create_time": 3}},
        "d": {"message": {"author": {"role": "user"}, "content": {"parts": ["Give me ten examples with screenshots"]},
                          "create_time": 4}},
        "e": {"message": None},
    }}]
CLAUDE = [{"uuid": "u-9", "name": "Cash flow plan", "created_at": "2026-02-03T10:00:00Z",
           "chat_messages": [{"sender": "human", "text": "Plan my shop's cash flow for March"},
                             {"sender": "assistant", "text": "Here is a plan..."},
                             {"sender": "human", "text": "", "content": [{"type": "text", "text": "Add rent of 900"}]}]}]


@pytest.fixture
def memory(tmp_path):
    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    return database


def write(tmp_path, name, data):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "conversations.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_both_exports_are_read_keeping_only_what_the_owner_asked(tmp_path):
    [chat] = read_export(write(tmp_path, "gpt", CHATGPT))
    assert chat == {"origin": "ChatGPT", "id": "c-1", "title": "Arabic verbs", "date": "2026-01-01",
                    "asked": ["How do past tense verbs work in Arabic?", "Give me ten examples with screenshots"]}
    [claude] = read_export(write(tmp_path, "claude", CLAUDE) / "conversations.json")
    assert claude["origin"] == "Claude" and claude["asked"] == ["Plan my shop's cash flow for March", "Add rent of 900"]
    text = memory_text(chat)
    assert text.startswith("Conversation with ChatGPT on 2026-01-01: Arabic verbs.") and "They use patterns" not in text


def test_conversations_become_recallable_history_and_are_never_imported_twice(tmp_path, memory):
    folder = write(tmp_path, "gpt", CHATGPT)
    first = import_conversations(memory, folder)
    assert first["imported"] == 1 and first["by_origin"] == {"ChatGPT": 1}
    again = import_conversations(memory, folder)
    assert again["imported"] == 0 and again["already_imported"] == 1
    import_conversations(memory, write(tmp_path, "claude", CLAUDE))
    [row] = memory.retrieve("arabic past tense verbs", limit=1, session_id="a-later-session")
    assert row["source"] == "imported_chat" and "Arabic verbs" in row["content"]
    assert row["state_class"] == "HISTORICAL"  # "screenshots" in it does not make it a snapshot of this machine
    assert memory.retrieve("shop cash flow rent", limit=1)[0]["metadata"]["origin"] == "Claude"


def test_a_file_that_is_not_an_export_is_refused_plainly(tmp_path, memory):
    with pytest.raises(FileNotFoundError, match="Unzip the export"):
        import_conversations(memory, tmp_path)
    (tmp_path / "conversations.json").write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a ChatGPT or Claude"):
        import_conversations(memory, tmp_path)


def test_the_tool_imports_through_the_registry(tmp_path, memory):
    from app.chat_import import register_chat_import_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_chat_import_tools(registry, memory)
    result = registry.execute("import_chat_history", {"path": str(write(tmp_path, "gpt", CHATGPT))})
    assert result.success and result.output["imported"] == 1
