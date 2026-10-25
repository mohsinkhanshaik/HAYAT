from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.security.scanner import DefenderFileScanner


def test_defender_scanner_uses_custom_scan_ignoring_exclusions(tmp_path, monkeypatch):
    engine = tmp_path / "MpCmdRun.exe"
    engine.write_bytes(b"test executable placeholder")
    target = tmp_path / "document.txt"
    target.write_text("safe fixture", encoding="utf-8")
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="found no threats", stderr="")

    monkeypatch.setattr("app.security.scanner.subprocess.run", run)
    result = DefenderFileScanner(engine).scan(target)

    assert result["clean"] is True and result["status"] == "clean"
    assert result["fail_closed"] is True and result["counterattack"] is False
    assert observed["command"][1:] == [
        "-Scan", "-ScanType", "3", "-File", str(target.resolve()), "-DisableRemediation",
    ]


def test_defender_scanner_fails_closed_on_detection_or_error(tmp_path, monkeypatch):
    engine = tmp_path / "MpCmdRun.exe"
    engine.write_bytes(b"test executable placeholder")
    target = tmp_path / "unknown.bin"
    target.write_bytes(b"fixture")
    monkeypatch.setattr(
        "app.security.scanner.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout="threat", stderr=""),
    )

    result = DefenderFileScanner(engine).scan(target)

    assert result["clean"] is False and result["status"] == "blocked"
    assert result["returncode"] == 2


def test_missing_scanner_never_marks_a_file_clean(tmp_path):
    target = tmp_path / "unknown.bin"
    target.write_bytes(b"fixture")
    scanner = DefenderFileScanner.__new__(DefenderFileScanner)
    scanner.executable = None

    result = scanner.scan(target)

    assert result["clean"] is False
    assert result["status"] == "scanner_unavailable"


def test_browser_download_scans_before_releasing_file(tmp_path):
    from types import SimpleNamespace

    from app.tools.browser import BrowserTools

    class Download:
        suggested_filename = "report.pdf"

        def save_as(self, output):
            Path(output).write_bytes(b"safe report")

    class Expect:
        def __enter__(self):
            return SimpleNamespace(value=Download())

        def __exit__(self, *_args):
            return False

    page = SimpleNamespace(
        expect_download=lambda timeout: Expect(),
        locator=lambda selector: SimpleNamespace(
            first=SimpleNamespace(click=lambda timeout: None)),
    )
    permissions = SimpleNamespace(
        validate_project_path=lambda _path, write=False: tmp_path / "quarantine")
    scanner = SimpleNamespace(
        scan=lambda path: {"clean": True, "status": "clean", "path": str(path)})
    tools = SimpleNamespace(
        _page=lambda: page, permissions=permissions, scanner=scanner)

    result = BrowserTools.download(tools, "a.download")

    assert result["antimalware_scan"]["clean"] is True
    assert result["executed"] is False
    assert Path(result["path"]).read_bytes() == b"safe report"


def test_browser_download_retains_detection_as_blocked(tmp_path):
    from types import SimpleNamespace

    from app.tools.browser import BrowserTools

    class Download:
        suggested_filename = "payload.exe"

        def save_as(self, output):
            Path(output).write_bytes(b"untrusted")

    class Expect:
        def __enter__(self):
            return SimpleNamespace(value=Download())

        def __exit__(self, *_args):
            return False

    page = SimpleNamespace(
        expect_download=lambda timeout: Expect(),
        locator=lambda selector: SimpleNamespace(
            first=SimpleNamespace(click=lambda timeout: None)),
    )
    permissions = SimpleNamespace(
        validate_project_path=lambda _path, write=False: tmp_path / "quarantine")
    scanner = SimpleNamespace(
        scan=lambda path: {"clean": False, "status": "blocked", "path": str(path)})
    tools = SimpleNamespace(
        _page=lambda: page, permissions=permissions, scanner=scanner)

    with pytest.raises(PermissionError, match="blocked before use"):
        BrowserTools.download(tools, "a.download")

    retained = list((tmp_path / "quarantine").glob("*.blocked"))
    assert len(retained) == 1 and retained[0].read_bytes() == b"untrusted"
    assert not (tmp_path / "quarantine" / "payload.exe").exists()
