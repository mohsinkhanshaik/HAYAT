from app.agent.verifier import Verifier
from app.tools.registry import ToolResult


def test_verifies_stdout_and_failure():
    verifier = Verifier()
    good = ToolResult("python", True, {"stdout": "Hello from my AI\n", "stderr": "", "exit_code": 0})
    assert verifier.check({"type": "stdout_contains", "value": "Hello from my AI"}, good).passed
    bad = ToolResult("python", True, {"stdout": "", "stderr": "boom", "exit_code": 1})
    assert not verifier.check({"type": "exit_code_zero"}, bad).passed


def test_missing_expected_values_fail_closed(tmp_path):
    verifier = Verifier()
    read = ToolResult("read_file", True, {"path": str(tmp_path / "missing.md"), "content": "anything"})
    assert not verifier.check({"type": "content_contains"}, read).passed
    assert not verifier.check({"type": "file_exists"}, read).passed
    assert not verifier.check({"type": "url_matches"}, ToolResult("browser", True, {"url": "https://example.com"})).passed
