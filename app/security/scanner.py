from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class DefenderFileScanner:
    """Fail-closed native antimalware scanning for quarantined downloads.

    Custom scans use ``-DisableRemediation`` so Defender ignores exclusions and
    reports a detection without silently destroying the retained evidence.
    HAYAT never parses, opens, or executes a file unless the scan returns clean.
    """

    def __init__(self, executable: str | Path | None = None) -> None:
        self.executable = Path(executable).resolve() if executable else self._discover()

    @staticmethod
    def _discover() -> Path | None:
        candidates: list[Path] = []
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            platform = Path(program_data) / "Microsoft/Windows Defender/Platform"
            if platform.is_dir():
                candidates.extend(
                    folder / "MpCmdRun.exe"
                    for folder in sorted(platform.iterdir(), key=lambda item: item.name, reverse=True)
                    if folder.is_dir()
                )
        program_files = os.environ.get("PROGRAMFILES")
        if program_files:
            candidates.append(Path(program_files) / "Windows Defender/MpCmdRun.exe")
        defender = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
        if defender:
            return defender
        clam = shutil.which("clamscan")
        return Path(clam).resolve() if clam else None

    def status(self) -> dict[str, Any]:
        available = bool(self.executable and self.executable.is_file())
        provider = ("clamav" if available and self.executable
                    and self.executable.name.casefold().startswith("clamscan")
                    else "microsoft_defender" if available else "unavailable")
        return {
            "available": available,
            "provider": provider,
            "executable": str(self.executable) if self.executable else None,
            "custom_scan": True,
            "exclusions_ignored": True,
            "fail_closed": True,
            "automatic_execution": False,
            "counterattack": False,
            "response": "block, quarantine, preserve evidence, and alert",
        }

    def scan(self, path: str | Path, *, timeout_seconds: int = 180) -> dict[str, Any]:
        target = Path(path).resolve()
        if not target.is_file():
            raise FileNotFoundError(target)
        status = self.status()
        if not status["available"]:
            return status | {
                "clean": False, "status": "scanner_unavailable", "path": str(target),
                "reason": "No supported antimalware scanner is available; access remains blocked.",
            }
        if status["provider"] == "clamav":
            command = [str(self.executable), "--no-summary", "--infected", str(target)]
        else:
            command = [
                str(self.executable), "-Scan", "-ScanType", "3", "-File", str(target),
                "-DisableRemediation",
            ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=max(10, min(int(timeout_seconds), 600)),
                encoding="utf-8", errors="replace",
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return status | {
                "clean": False, "status": "scan_error", "path": str(target),
                "reason": f"{type(exc).__name__}: {exc}",
            }
        output = "\n".join(filter(None, (completed.stdout.strip(), completed.stderr.strip())))[:4000]
        clean = completed.returncode == 0
        return status | {
            "clean": clean, "status": "clean" if clean else "blocked",
            "path": str(target), "returncode": int(completed.returncode),
            "output": output,
            "reason": "No threat detected." if clean else "Threat detected or scan failed; access remains blocked.",
        }
