from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from app.safety.container_sandbox import ContainerSandbox, SandboxUnavailable


def _fake(info_code=0, info_os="linux", run_code=0):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return SimpleNamespace(returncode=info_code, stdout=info_os + "\n", stderr="")
        return SimpleNamespace(returncode=run_code, stdout="45\n", stderr="")
    return run, calls


def test_nothing_runs_when_docker_is_not_running(monkeypatch):
    """No fallback to running untrusted code on the host."""
    monkeypatch.setattr(shutil, "which", lambda name: "docker")
    run, calls = _fake(info_code=1)
    with pytest.raises(SandboxUnavailable, match="not running"):
        ContainerSandbox(run=run).run_python("print(1)")
    assert all(call[:2] == ["docker", "info"] for call in calls)


def test_the_container_is_isolated_on_every_axis(tmp_path):
    argv = ContainerSandbox().command(tmp_path, "hayat-sandbox-x", timeout=9)
    joined = " ".join(argv)
    for flag in ("--rm", "--network none", "--read-only", "--cap-drop ALL", "no-new-privileges",
                 "--user 65534:65534", "--memory 512m", "--pids-limit 128", f"{tmp_path}:/work:ro"):
        assert flag in joined, flag
    assert argv[-7:] == ["timeout", "-k", "2", "9", "python", "-I", "/work/main.py"]


def test_a_timeout_inside_the_container_is_reported_as_one(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "docker")
    run, _ = _fake(run_code=124)
    result = ContainerSandbox(run=run).run_python("while True: pass", timeout_seconds=1)
    assert result["timed_out"] is True and result["exit_code"] is None


def _docker_ready():
    ready, _ = ContainerSandbox().available()
    if not ready:
        pytest.skip("Docker Desktop is not running")
    return ContainerSandbox()


@pytest.mark.timeout(600)
def test_live_the_code_runs_but_cannot_reach_the_network_or_write():
    sandbox = _docker_ready()
    assert sandbox.run_python("print(sum(range(10)))")["stdout"].strip() == "45"
    network = sandbox.run_python(
        "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 53), timeout=3); print('open')\n"
        "except OSError:\n    print('blocked')", timeout_seconds=30)
    assert network["stdout"].strip() == "blocked"
    write = sandbox.run_python("open('/etc/x', 'w')", timeout_seconds=30)
    assert write["exit_code"] != 0 and "Read-only file system" in write["stderr"]


@pytest.mark.timeout(600)
def test_live_the_time_limit_is_enforced():
    sandbox = _docker_ready()
    assert sandbox.run_python("import time\ntime.sleep(30)", timeout_seconds=2)["timed_out"] is True
