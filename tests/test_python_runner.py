
import pytest

from app.safety.permissions import PermissionManager
from app.tools.python_runner import PythonRunner


def test_execute_file_is_limited_to_the_project_even_when_another_root_is_allowed(tmp_path):
    extra = tmp_path.parent / f"py-extra-{tmp_path.name}"
    extra.mkdir()
    outsider = extra / "payload.py"
    outsider.write_text("print('should not run')\n", encoding="utf-8")
    runner = PythonRunner(
        PermissionManager(
            {
                "default": "ALLOW",
                "permissions": {"python_project": "ALLOW"},
                "allowed_roots": [".", str(extra)],
                "protected_paths": [],
            },
            tmp_path,
        )
    )
    runner.work_dir = tmp_path / "data" / "work" / "python"
    runner.work_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PermissionError, match="project"):
        runner.execute_file(str(outsider))


def test_execute_project_file(tmp_path):
    script = tmp_path / "hello.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    runner = PythonRunner(PermissionManager({"default": "ALLOW", "permissions": {}, "protected_paths": []}, tmp_path))
    runner.work_dir = tmp_path / "data" / "work" / "python"
    runner.work_dir.mkdir(parents=True, exist_ok=True)
    result = runner.execute_file(str(script))
    assert result["exit_code"] == 0
    assert "ok" in result["stdout"]
