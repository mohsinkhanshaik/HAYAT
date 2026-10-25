import sys

from app.safety.sandbox import ProcessSandbox


def test_process_sandbox_limits_output_and_filters_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_API_KEY", "do-not-leak")
    sandbox = ProcessSandbox(output_limit=20)
    result = sandbox.run([sys.executable, "-c", "import os; print(os.getenv('DEMO_API_KEY')); print('x'*100)"], tmp_path, 10)
    assert result["exit_code"] == 0
    assert "do-not-leak" not in result["stdout"]
    assert result["stdout_truncated"]


def test_process_sandbox_times_out(tmp_path):
    sandbox = ProcessSandbox()
    result = sandbox.run([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, 1)
    assert result["timed_out"]
    assert result["exit_code"] == -1
