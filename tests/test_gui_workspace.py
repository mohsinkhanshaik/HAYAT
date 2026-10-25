import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest

from app.gui_workspace import CommandPalette, DraftController, DraftStore, WorkspaceCommand, matching_commands


def test_drafts_are_scoped_bounded_and_removed_on_send(tmp_path):
    store = DraftStore(tmp_path / "workspace.db")
    store.save("first", "unfinished question")
    store.save("second", "different conversation")
    assert store.load("first") == "unfinished question"
    assert store.load("unknown") == ""
    store.save("first", "")
    assert store.load("first") == ""
    assert store.load("second") == "different conversation"
    with pytest.raises(ValueError):
        store.save("first", "x" * (store.MAX_CHARACTERS + 1))


def test_expired_drafts_are_deleted_and_history_is_bounded(tmp_path):
    store = DraftStore(tmp_path / "workspace.db")
    store.save("expired", "old text")
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("UPDATE chat_drafts SET updated=0")
    assert store.load("expired") == ""
    for n in range(store.MAX_DRAFTS + 3):
        store.save(str(n), "draft")
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM chat_drafts").fetchone()[0] == store.MAX_DRAFTS


def test_palette_filters_declared_commands_and_never_executes_search_text():
    callback = Mock()
    commands = [WorkspaceCommand("Show memory", callback, "knowledge recall"),
                WorkspaceCommand("Stop current work", callback, "cancel")]
    assert matching_commands(commands, "KNOWLEDGE memory") == commands[:1]
    assert matching_commands(commands, "run arbitrary shell") == []
    callback.assert_not_called()


@pytest.fixture
def root(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, hidden=True)
    yield window
    close_window(window)


def test_palette_enter_calls_existing_handler_once(root):
    callback = Mock()
    palette = CommandPalette(root, [WorkspaceCommand("Show memory", callback)])
    palette.withdraw()
    palette.query.set("memory")
    palette.execute()
    callback.assert_called_once_with()
    assert not palette.winfo_exists()


def test_draft_restores_without_sending_and_privacy_prevents_persistence(root, tmp_path):
    import tkinter as tk
    store = DraftStore(tmp_path / "workspace.db")
    store.save("session", "recover me")
    widget = tk.Text(root)
    allowed = [True]
    controller = DraftController(widget, store, lambda: "session", lambda: allowed[0], Mock())
    assert widget.get("1.0", "end-1c") == "recover me"
    widget.insert("end", " please")
    controller.flush()
    assert store.load("session") == "recover me please"
    allowed[0] = False
    widget.insert("end", " private addition")
    controller.close()
    assert store.load("session") == "recover me please"


def test_corrupt_draft_database_cannot_crash_composer(root, tmp_path):
    import tkinter as tk
    path = tmp_path / "workspace.db"
    path.write_bytes(b"not a sqlite database")
    status = Mock()
    widget = tk.Text(root)
    controller = DraftController(widget, DraftStore(path), lambda: "session", lambda: True, status)
    widget.insert("1.0", "can still type")
    controller.close()
    assert widget.get("1.0", "end-1c") == "can still type"
    status.assert_any_call("Draft recovery unavailable")


def test_previous_launch_draft_requires_explicit_recovery_and_never_overwrites_input(root, tmp_path):
    import tkinter as tk
    store = DraftStore(tmp_path / "workspace.db")
    store.save("previous", "Unsent request")
    widget = tk.Text(root)
    allowed = [True]
    controller = DraftController(widget, store, lambda: "new", lambda: allowed[0], Mock())
    assert not widget.get("1.0", "end-1c")
    widget.insert("1.0", "Current request")
    controller.recover_previous()
    assert widget.get("1.0", "end-1c") == "Current request"
    widget.delete("1.0", "end")
    allowed[0] = False
    controller.recover_previous()
    assert not widget.get("1.0", "end-1c")
    allowed[0] = True
    controller.recover_previous()
    assert widget.get("1.0", "end-1c") == "Unsent request"
    assert store.load("new") == "Unsent request"
    controller.close()


def test_stream_preview_appends_without_losing_selection_or_reading_position(root):
    from app.gui import HayatGUI
    from app.gui_workspace import StreamingPreview
    preview = StreamingPreview(root, HayatGUI)
    preview.publish("start", "local/hayat")
    preview.publish("delta", "".join(f"Line {i}\n" for i in range(100)))
    preview.drain()
    root.update_idletasks()
    preview.text.yview_moveto(0)
    preview.text.tag_add("sel", "2.0", "2.4")
    preview.publish("delta", "New line\n")
    preview.drain()
    assert preview.text.get("sel.first", "sel.last") == "Line"
    assert preview.text.yview()[0] < .05
    assert preview.text.get("1.0", "end-1c").endswith("New line\n")
    preview.publish("clear")
    preview.drain()
    preview.publish("start", "local/hayat")
    preview.publish("delta", "Fresh reply")
    preview.drain()
    assert preview.text.get("1.0", "end-1c") == "Fresh reply"
