from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from typing import Any

from app.config import PROJECT_ROOT


SECRET_PATTERN = re.compile(r"(?i)(?<![a-z0-9])(?:sk-[a-z0-9_-]{20,}|gh[pousr]_[a-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
PLACEHOLDER_PATTERN = re.compile(r"(?i)(example|placeholder|dummy|fake|fabricated|fixture|another-secret|redact)")


class DefensiveSecurityAuditor:
    """Local, read-only defensive checks for the user's current project and PC."""

    def __init__(self, permissions, dependencies, scanner=None) -> None:
        self.permissions = permissions; self.dependencies = dependencies; self.scanner = scanner

    def audit(self) -> dict[str, Any]:
        pip_check = subprocess.run([str(PROJECT_ROOT / ".venv/Scripts/python.exe"), "-m", "pip", "check"], capture_output=True, text=True, timeout=60)
        findings = []
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10,
        )
        for relative in completed.stdout.splitlines():
            path = PROJECT_ROOT / relative
            if relative.lower() == ".env" or not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            try: text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError): continue
            unsafe = []
            for match in SECRET_PATTERN.finditer(text):
                # Test fixtures should exercise real credential shapes. Treat
                # one as intentional only when its immediate source context is
                # explicitly labelled example/fake/fabricated; a blanket
                # exclusion for tests would hide an accidentally committed key.
                context = text[max(0, match.start() - 300):min(len(text), match.end() + 300)]
                if not PLACEHOLDER_PATTERN.search(context):
                    unsafe.append(match.group(0))
            if unsafe:
                findings.append({"type": "credential_shaped_value", "path": relative})
        denied = sorted(name for name, value in self.permissions.policies.items() if value == "DENY")
        ask = sorted(name for name, value in self.permissions.policies.items() if value == "ASK")
        return {
            "observed_at": datetime.now(UTC).isoformat(), "scope": str(PROJECT_ROOT), "read_only": True,
            "pip_check": {"passed": pip_check.returncode == 0, "output": (pip_check.stdout or pip_check.stderr).strip()},
            "secret_scan": {"passed": not findings, "findings": findings, "scanned": "Git-tracked and untracked non-ignored text files; .env excluded"},
            "permission_review": {"deny": denied, "ask": ask, "protected_paths": len(self.permissions.config.get("protected_paths", []))},
            "download_defense": self.scanner.status() if self.scanner else {"available": False, "fail_closed": True},
            "prohibited_capabilities": ["credential theft", "malware deployment", "stealth persistence", "covert surveillance", "unauthorized intrusion"],
        }
