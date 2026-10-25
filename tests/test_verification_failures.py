from pathlib import Path

from app.agent.verifier import Verifier
from app.tools.registry import ToolResult
from app.agent.core import TaskOutcome
from app.voice.manager import SpokenResponseFormatter


def test_incomplete_download_is_not_success(tmp_path: Path):
    partial = tmp_path / "partial.bin"
    partial.write_bytes(b"partial")
    checked = Verifier().check(
        {"type": "download_complete", "bytes": 100},
        ToolResult("download", True, {"path": str(partial), "complete": False, "bytes_downloaded": 7}),
    )
    assert not checked.passed


def test_visible_target_after_close_is_not_success():
    checked = Verifier().check(
        {"type": "target_window_absent", "application": "notepad"},
        ToolResult("close", True, {"application": "notepad", "closed": True, "target_window_exists": True}),
    )
    assert not checked.passed


def test_voice_speaks_unsaved_work_question_instead_of_generic_failure():
    outcome = TaskOutcome("task", "Close Notepad", False, "choice required", [], [], needs_user_input=True)
    response = SpokenResponseFormatter.format(outcome, "Close it")
    assert "save them, discard them, or cancel" in response.spoken_response
