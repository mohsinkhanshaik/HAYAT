"""Find and install Windows software with winget -- after explaining, and only on a yes.

HAYAT could install Python packages but not a program a task needed (a
media player, 7-Zip, ffmpeg). winget, Windows' own package manager, installs
signed packages from Microsoft's community repository. This wraps it:

* ``plan`` searches, picks the best match and says what would be installed --
  name, id, version, publisher, licence, where it comes from.
* ``approve`` records that Mohsin said yes to that exact package. It is called
  only from his own reply to the plan, never by the planner.
* ``install`` refuses a package that was not approved in the last ten
  minutes, so no automatic step can install software on its own -- even in
  Trusted Owner Mode, where this category would otherwise be pre-approved.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT
from app.logging import log_swallowed
from app.identity import IDENTITY, pronouns as _pronouns

_P = _pronouns(IDENTITY.owner_pronoun)

_log = logging.getLogger(__name__)
APPROVAL_SECONDS = 600
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{1,120}$")


def parse_table(output: str) -> list[dict[str, str]]:
    """Rows of winget's fixed-width table, cut at the header's column positions."""
    lines = [line.rstrip("\r") for line in output.splitlines()]
    header_index = next((i for i, line in enumerate(lines) if re.search(r"\bName\s+Id\s+Version\b", line)), None)
    if header_index is None:
        return []
    header = lines[header_index]
    header = header[header.index("Name"):]
    offset = len(lines[header_index]) - len(header)
    names = re.findall(r"\S+", header)
    starts = [header.index(name) for name in names]
    rows = []
    for line in lines[header_index + 2:]:
        line = line[offset:] if len(line) > offset else line
        if not line.strip() or line.startswith("<") or set(line.strip()) <= {"-"}:
            continue
        cells = {}
        for index, name in enumerate(names):
            end = starts[index + 1] if index + 1 < len(starts) else None
            cells[name.lower()] = line[starts[index]:end].strip()
        if cells.get("id"):
            rows.append(cells)
    return rows


def parse_details(output: str) -> dict[str, str]:
    details: dict[str, str] = {}
    found = re.search(r"Found (.+?) \[(.+?)\]", output)
    if found:
        details["name"], details["id"] = found.group(1).strip(), found.group(2).strip()
    description: list[str] = []
    in_description = False
    for line in output.splitlines():
        field = re.match(r"^([A-Z][A-Za-z ]+):\s*(.*)$", line)
        if field and not line.startswith(" "):
            key = field.group(1).strip().lower().replace(" ", "_")
            in_description = key == "description"
            if field.group(2):
                details[key] = field.group(2).strip()
            continue
        if in_description and line.strip():
            description.append(line.strip())
    if description:
        details["description"] = " ".join(description[:3])
    return details


class SoftwareInstaller:
    def __init__(self, *, run: Callable[..., Any] = subprocess.run, journal: Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.run = run
        self.journal = journal or PROJECT_ROOT / "data" / "logs" / "software-installs.jsonl"
        self.clock = clock
        self._approved: dict[str, float] = {}

    def available(self) -> bool:
        return shutil.which("winget") is not None

    def _winget(self, *arguments: str, timeout: float = 90) -> subprocess.CompletedProcess:
        return self.run(["winget", *arguments, "--accept-source-agreements", "--disable-interactivity"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)

    def search(self, query: str, count: int = 5) -> list[dict[str, str]]:
        result = self._winget("search", query, "--source", "winget", "--count", str(count))
        return parse_table(result.stdout or "")

    def details(self, package_id: str) -> dict[str, str]:
        if not _ID.match(package_id):
            raise ValueError(f"Not a winget package id: {package_id!r}")
        result = self._winget("show", "--id", package_id, "--exact", "--source", "winget")
        return parse_details(result.stdout or "")

    def plan(self, query: str) -> dict[str, Any]:
        query = " ".join(str(query or "").split()).strip(" .!?")
        if not query:
            return {"found": False, "text": "Tell me which program to install."}
        if not self.available():
            return {"found": False, "text": "winget, Windows' package manager, isn't available here, so I can't install programs."}
        candidates = self.search(query)
        if not candidates:
            return {"found": False, "candidates": [],
                    "text": f"I couldn't find \"{query}\" in the winget repository. Tell me the exact program name or its website."}
        wanted = query.lower()

        def score(row: dict[str, str]) -> tuple[int, int]:
            match = row.get("match", "").lower()
            exact = row.get("name", "").lower() == wanted or row.get("id", "").lower().endswith("." + wanted.replace(" ", ""))
            return (0 if f"moniker: {wanted}" == match or exact else 1 if wanted in row.get("name", "").lower() else 2,
                    candidates.index(row))

        choice = min(candidates, key=score)
        details = self.details(choice["id"])
        name = details.get("name") or choice.get("name")
        lines = [f"I'd install {name} ({choice['id']}), version {details.get('version') or choice.get('version')}"
                 f"{', by ' + details['publisher'] if details.get('publisher') else ''}, from the winget community repository."]
        if details.get("description"):
            lines.append(details["description"])
        if details.get("license"):
            lines.append(f"Licence: {details['license']}.")
        lines.append("It installs silently with its default options; some installers ask Windows for administrator "
                     "permission, and you'd see that prompt.")
        others = [row for row in candidates if row is not choice][:3]
        if others:
            lines.append("Other matches: " + "; ".join(f"{row['name']} ({row['id']})" for row in others) + ".")
        return {"found": True, "id": choice["id"], "name": name, "details": details, "candidates": candidates,
                "text": " ".join(lines)}

    def approve(self, package_id: str) -> None:
        """Mohsin said yes to this package. Called only from his reply to the plan."""
        self._approved[package_id.lower()] = self.clock()

    def approved(self, package_id: str) -> bool:
        at = self._approved.get(package_id.lower())
        return at is not None and self.clock() - at <= APPROVAL_SECONDS

    def install(self, package_id: str) -> dict[str, Any]:
        if not _ID.match(package_id or ""):
            return {"ok": False, "error": f"Not a winget package id: {package_id!r}"}
        if not self.approved(package_id):
            return {"ok": False, "error": f"Not approved. Explain the plan to {IDENTITY.owner_short} (find_software) and "
                                          f"install only after {_P['he']} says yes."}
        started = self.clock()
        try:
            result = self._winget("install", "--id", package_id, "--exact", "--source", "winget", "--silent",
                                  "--accept-package-agreements", timeout=1800)
        except subprocess.TimeoutExpired:
            return self._record({"ok": False, "id": package_id, "error": "The installer did not finish within 30 minutes."})
        output = (result.stdout or "") + (result.stderr or "")
        already = "already installed" in output.lower() or "no available upgrade" in output.lower()
        ok = result.returncode == 0 or already
        self._approved.pop(package_id.lower(), None)
        tail = " ".join(line.strip() for line in output.splitlines() if line.strip() and not set(line.strip()) <= set("-\\|/ █▒"))[-600:]
        return self._record({"ok": ok, "id": package_id, "already_installed": already, "exit_code": result.returncode,
                             "seconds": round(self.clock() - started, 1), "output": tail,
                             **({} if ok else {"error": f"winget exited with {result.returncode}: {tail[-300:]}"})})

    def _record(self, entry: dict[str, Any]) -> dict[str, Any]:
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"at": datetime.now(UTC).isoformat(), **entry}) + "\n")
        except OSError:
            log_swallowed(_log, "writing the software install journal", level=logging.WARNING)
        return entry


def register_software_tools(registry, installer: SoftwareInstaller | None = None) -> SoftwareInstaller:
    software = installer or SoftwareInstaller()

    def find_software(query: str) -> dict[str, Any]:
        return software.plan(query)

    def install_software(package_id: str) -> dict[str, Any]:
        return software.install(package_id)

    registry.register(
        "find_software",
        "Find a Windows program in the winget repository and explain exactly what installing it would do. "
        "Always show this plan to the owner and wait for their yes before installing.",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        "low", "dependency_inspect", find_software,
    )
    registry.register(
        "install_software",
        "Install a Windows program with winget by its exact package id. Refused unless the owner approved that "
        "package after seeing the plan from find_software.",
        {"type": "object", "properties": {"package_id": {"type": "string"}}, "required": ["package_id"]},
        "medium", "install_system_software", install_software,
    )
    return software


if os.name != "nt":                      # pragma: no cover - winget is Windows-only
    SoftwareInstaller.available = lambda self: False
