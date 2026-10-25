"""Working with the computer's files: names found where they are, folders and files told apart, file names never taken
for websites, and a call that cannot succeed not repeated (all seen on 2026-09-29 with "settings.yaml")."""
import pytest

from app.agent.core import DETERMINISTIC_ERRORS
from app.research.safety import UnsafeUrlError, validate_public_url
from app.tools.filesystem import FilesystemTools
from app.tools.registry import ToolResult
from tests.test_brain_accuracy_upgrade import make_agent


class Permissions:
    def __init__(self, root):
        self.root, self.project_root = root, root

    def validate_project_path(self, path, write=False):
        from pathlib import Path
        value = Path(path)
        return (value if value.is_absolute() else self.root / value).resolve()

    def allowed_roots(self):
        return [self.root]


@pytest.fixture
def tools(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("project_name: AI\n", encoding="utf-8")
    (tmp_path / "docs" / "audit").mkdir(parents=True)
    (tmp_path / "docs" / "audit" / "report.md").write_text("# Report\n", encoding="utf-8")
    for folder in ("app", "tests"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "settings.yaml").write_text("ignored", encoding="utf-8")
    return FilesystemTools(Permissions(tmp_path))


def test_a_file_named_without_its_folder_is_found_and_the_answer_says_where(tools):
    result = tools.read_file("settings.yaml")
    assert result["content"] == "project_name: AI\n" and result["note"] == "settings.yaml was not where it was asked for; found it at config/settings.yaml"
    assert "note" not in tools.read_file("config/settings.yaml")  # asked right: nothing to say
    assert tools.list_folder("audit")["note"].endswith("found it at docs/audit")
    assert tools.read_file("audit/report.md")["content"] == "# Report\n"  # a partial path matches its tail


def test_several_matches_are_offered_never_guessed_and_a_missing_name_says_so(tools):
    with pytest.raises(FileNotFoundError, match="Did you mean: app/__init__.py; tests/__init__.py"):
        tools.read_file("__init__.py")
    with pytest.raises(FileNotFoundError, match="No file named nowhere.txt"):
        tools.read_file("nowhere.txt")


def test_files_and_folders_are_told_apart(tools):
    with pytest.raises(NotADirectoryError, match="is a file, not a folder: read it with read_file"):
        tools.list_folder("config/settings.yaml")
    with pytest.raises(IsADirectoryError, match="is a folder: list it with list_folder"):
        tools.read_file("config")


def test_a_file_name_is_not_taken_for_a_website():
    for value in ("https://settings.yaml", "http://notes.txt/", "https://report.pdf"):
        with pytest.raises(UnsafeUrlError, match="looks like a file name"):
            validate_public_url(value)
    assert {"py", "md"}.isdisjoint(__import__("app.research.safety", fromlist=["FILE_ENDINGS"]).FILE_ENDINGS)  # real countries


def test_a_call_that_cannot_succeed_is_not_repeated(tmp_path):
    from app.agent.planner import PlanStep
    agent = make_agent(tmp_path, [PlanStep("Read the settings", "read_file", {"path": "settings.yaml"}, max_retries=2)], 10)
    agent.executor.execute.return_value = ToolResult("read_file", False, error="FileNotFoundError: No file named settings.yaml")
    agent.run("Read settings.yaml")
    assert agent.executor.execute.call_count == 1 and agent.planner.replan.call_count == 1  # straight to re-planning
    agent = make_agent(tmp_path, [PlanStep("Fetch", "web", {}, max_retries=2)], 10)
    agent.executor.execute.return_value = ToolResult("web", False, error="ReadTimeout: timed out")
    agent.run("Fetch it")
    assert agent.executor.execute.call_count == 3  # a passing failure still gets its retries
    assert "FileNotFoundError" in DETERMINISTIC_ERRORS
