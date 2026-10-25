"""Installing a dependency HAYAT decided it needs, without installing a lie.

Mohsin asked for this twice, and the second time was a decision rather than a
question, so it is built. The risk is worth stating precisely, because it
shapes every check below: `pip install` runs arbitrary code from the package
at install time. A name that is wrong therefore does not fail politely -- it
executes.

Two ways the name is wrong that matter here.

A model asked for a library it half-remembers will produce a plausible name
that does not exist. Left alone that is a failed install; but PyPI is an open
index, and a name that does not exist today can be registered by anyone
tomorrow, specifically because assistants keep asking for it. So a package
that cannot be found is refused rather than attempted.

And a name one character from a real one -- `requsts`, `python-dateutils` --
is usually a typosquat sitting there waiting for exactly this mistake. Those
are refused too, with the real name offered, because "did you mean requests?"
is a better outcome than a silent install of something else.

What this does not do is judge whether a package is any good. It checks that
the thing being installed is the thing that was asked for. Everything past
that is the owner's call, which is why the install itself still travels the
normal authority path as `install_package_project`.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


JOURNAL = PROJECT_ROOT / "data" / "packages" / "installed.jsonl"

#: PEP 508 project names. Anything else -- a URL, a git ref, a path, a flag --
#: is not a package name and is never passed to pip, because those are the
#: forms that fetch code from somewhere other than the index.
_VALID_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$")

#: Argument shapes that would change where pip looks or what it trusts.
_FORBIDDEN = (
    "--index-url", "--extra-index-url", "--find-links", "--trusted-host",
    "--pre", "--editable", "-e ", "git+", "http://", "https://", "file:",
    "..", "/", "\\", ";", "&", "|", "$", "`",
)

#: Names close to these are almost certainly meant to be these. Kept short and
#: high-traffic on purpose: a long list produces false alarms, and the point is
#: to catch the handful of names worth squatting.
_POPULAR = (
    "requests", "urllib3", "numpy", "pandas", "scipy", "pillow", "httpx",
    "flask", "django", "fastapi", "pydantic", "sqlalchemy", "pytest",
    "beautifulsoup4", "lxml", "matplotlib", "scikit-learn", "torch",
    "tensorflow", "transformers", "openai", "anthropic", "boto3", "click",
    "rich", "typer", "attrs", "certifi", "cryptography", "jinja2", "pyyaml",
    "setuptools", "wheel", "pip", "python-dateutil", "six", "colorama",
    "tqdm", "packaging", "protobuf", "aiohttp", "websockets", "psutil",
)

#: How many installs one session may perform before stopping to be asked
#: again. An assistant that installs forty packages unattended has stopped
#: being helpful and started being a supply chain.
SESSION_LIMIT = 10


def _distance(left: str, right: str) -> int:
    """Edit distance counting a swap of neighbours as one mistake.

    Damerau-Levenshtein rather than plain, because transposition is one of the
    commonest ways a name comes out wrong -- "pandsa" for "pandas" -- and
    plain Levenshtein scores it as two edits, which is far enough away to slip
    past a squat check. Written out rather than pulled in, since a dependency
    to vet dependencies is the wrong shape.
    """
    if left == right:
        return 0
    rows, columns = len(left), len(right)
    grid = [[0] * (columns + 1) for _ in range(rows + 1)]
    for i in range(rows + 1):
        grid[i][0] = i
    for j in range(columns + 1):
        grid[0][j] = j
    for i in range(1, rows + 1):
        for j in range(1, columns + 1):
            cost = int(left[i - 1] != right[j - 1])
            grid[i][j] = min(grid[i - 1][j] + 1, grid[i][j - 1] + 1, grid[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and left[i - 1] == right[j - 2] and left[i - 2] == right[j - 1]:
                grid[i][j] = min(grid[i][j], grid[i - 2][j - 2] + 1)
    return grid[rows][columns]


@dataclass
class InstallResult:
    name: str
    ok: bool = False
    version: str | None = None
    reason: str = ""
    detail: str = ""
    refused_because: str | None = None
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "ok": self.ok, "version": self.version,
            "reason": self.reason[:300], "detail": self.detail[:400],
            "refused_because": self.refused_because, "at": self.at,
        }


class PackageInstaller:
    """Checks that a name means what it says, then installs it here only."""

    def __init__(self, journal: Path | None = None, http: Any = None,
                 session_limit: int = SESSION_LIMIT) -> None:
        self.journal = journal or JOURNAL
        self._http = http
        self.session_limit = session_limit
        self._installed_this_session = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------- checking

    def validate_name(self, name: str) -> str | None:
        """Why this is not a package name, or None if it is one."""
        candidate = str(name or "").strip()
        if not candidate:
            return "no package was named"
        lowered = candidate.lower()
        for marker in _FORBIDDEN:
            if marker in lowered:
                return (f"{candidate!r} is not a plain package name. I only install from the "
                        "package index, never from a URL, a repository, or a path.")
        if not _VALID_NAME.match(candidate):
            return f"{candidate!r} is not a valid package name."
        return None

    def typosquat_risk(self, name: str) -> str | None:
        """The popular package this name is probably a misspelling of."""
        lowered = name.strip().lower()
        if lowered in _POPULAR:
            return None
        for popular in _POPULAR:
            # One edit on a short name is coincidence; on a long one it is a
            # squat. Requiring length keeps "six" from matching "sax".
            if len(popular) >= 5 and _distance(lowered, popular) <= 1:
                return popular
        return None

    def exists_on_index(self, name: str) -> dict[str, Any] | None:
        """Ask the index whether this package is real, and what it is."""
        client = self._http
        if client is None:
            import httpx

            client = httpx
        try:
            response = client.get(f"https://pypi.org/pypi/{name}/json", timeout=20.0,
                                  follow_redirects=True)
            if response.status_code != 200:
                return None
            payload = response.json()
        except Exception:
            return None
        info = payload.get("info") or {}
        return {
            "name": info.get("name", name),
            "version": info.get("version"),
            "summary": (info.get("summary") or "")[:200],
            "home_page": info.get("home_page") or info.get("project_url"),
            "requires_python": info.get("requires_python"),
        }

    def review(self, name: str) -> dict[str, Any]:
        """Everything known about a name before anything is run."""
        invalid = self.validate_name(name)
        if invalid:
            return {"installable": False, "refused_because": invalid}
        squat = self.typosquat_risk(name)
        if squat:
            return {
                "installable": False,
                "refused_because": (
                    f"{name!r} is one character from {squat!r}, which is how typosquatting "
                    f"works. If you meant {squat}, ask for that by name."
                ),
                "did_you_mean": squat,
            }
        found = self.exists_on_index(name)
        if found is None:
            return {
                "installable": False,
                "refused_because": (
                    f"There is no package called {name!r} on the index. I will not install a "
                    "name I cannot find -- an unregistered name is exactly what someone else "
                    "can register later."
                ),
            }
        return {"installable": True, "package": found}

    # ------------------------------------------------------------ installing

    def install(self, name: str, reason: str = "", *, timeout: float = 600.0) -> InstallResult:
        """Install one checked package into this project's environment."""
        with self._lock:
            if self._installed_this_session >= self.session_limit:
                return InstallResult(
                    name=name, reason=reason,
                    refused_because=(
                        f"I've already installed {self._installed_this_session} packages this "
                        "session. Ask me again if that was intended."
                    ),
                )

        verdict = self.review(name)
        if not verdict["installable"]:
            result = InstallResult(name=name, reason=reason,
                                   refused_because=verdict["refused_because"])
            self._record(result)
            return result

        package = verdict["package"]
        # sys.executable, so it lands in this project's virtual environment and
        # nowhere else. --no-input because nothing here can answer a prompt.
        command = [sys.executable, "-m", "pip", "install", "--no-input", name]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            result = InstallResult(name=name, reason=reason,
                                   detail=f"pip did not finish within {timeout:.0f}s")
            self._record(result)
            return result

        ok = completed.returncode == 0
        tail = " ".join((completed.stdout + completed.stderr).strip().splitlines()[-3:])[:400]
        result = InstallResult(
            name=package["name"], ok=ok, version=package.get("version"),
            reason=reason, detail=tail,
        )
        if ok:
            with self._lock:
                self._installed_this_session += 1
        self._record(result)
        return result

    def outdated(self) -> list[dict[str, Any]]:
        """Packages with a newer release, straight from pip."""
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
                capture_output=True, text=True, timeout=300,
            )
            return json.loads(completed.stdout or "[]")
        except Exception:
            return []

    def upgrade(self, name: str, *, verify: Any = None, timeout: float = 900.0) -> InstallResult:
        """Upgrade one package, and put it back if it broke something.

        This is the part that makes self-upgrading safe to allow at all. A
        newer version is a guess that nothing depended on the old behaviour,
        and the only way to find out is to run the tests. If they come back
        worse, the previous version is reinstalled -- so the worst outcome is
        time spent, not a runtime that no longer works.

        `verify` is a callable returning True when the system is still
        healthy. Without one, the upgrade is refused rather than performed
        blind: an unverified self-upgrade is the failure mode this exists to
        prevent.
        """
        invalid = self.validate_name(name)
        if invalid:
            return InstallResult(name=name, refused_because=invalid)
        if verify is None:
            return InstallResult(
                name=name,
                refused_because=("I will not upgrade myself without a way to check the result. "
                                 "Give me a verification step and I will run it before and after."),
            )

        before = self._installed_version(name)
        if before is None:
            return InstallResult(name=name, refused_because=f"{name} is not installed here")
        def checked():
            try:
                return bool(verify())
            except Exception:
                return False

        if not checked():
            return InstallResult(
                name=name, version=before,
                refused_because=("Something is already failing before I start, so I cannot tell "
                                 "whether an upgrade made it worse. Fix that first."),
            )

        install_error = ""
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-input", "--upgrade", name],
                capture_output=True, text=True, timeout=timeout,
            )
            if completed.returncode != 0:
                install_error = "pip refused the upgrade"
        except Exception as exc:
            install_error = f"Upgrade interrupted: {type(exc).__name__}"
        after = self._installed_version(name)
        # Verify even when the requested version did not change: pip can change dependencies.
        healthy = checked()
        if not install_error and healthy:
            result = InstallResult(name=name, ok=True, version=after, reason="upgrade",
                                   detail="already at the newest version, verified" if after == before else f"upgraded {before} -> {after}, verified")
            self._record(result)
            return result

        # It broke something. Put it back and say so.
        rollback_error = ""
        try:
            rollback = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-input", f"{name}=={before}"],
                capture_output=True, text=True, timeout=timeout,
            )
            if rollback.returncode:
                rollback_error = "pip could not restore the previous version"
        except Exception as exc:
            rollback_error = f"Restore interrupted: {type(exc).__name__}"
        actual = self._installed_version(name)
        restored = checked()
        detail = (install_error or f"{after} broke verification") + "; "
        if not rollback_error and actual == before:
            detail += f"{before} was reinstalled"
        else:
            detail += rollback_error or f"the previous version was not confirmed (installed: {actual or 'unknown'})"
        if not restored or rollback_error or actual != before:
            detail += " -- recovery is not verified, which needs you"
        result = InstallResult(
            name=name, ok=False, version=actual, reason="upgrade", detail=detail,
        )
        self._record(result)
        return result

    def _installed_version(self, name: str) -> str | None:
        from importlib import metadata

        try:
            return metadata.version(name)
        except Exception:
            return None

    def uninstall(self, name: str) -> InstallResult:
        """Undo an install. Anything installed can be taken back out."""
        invalid = self.validate_name(name)
        if invalid:
            return InstallResult(name=name, refused_because=invalid)
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "--yes", name],
            capture_output=True, text=True, timeout=300,
        )
        result = InstallResult(
            name=name, ok=completed.returncode == 0, reason="uninstall requested",
            detail=" ".join((completed.stdout + completed.stderr).strip().splitlines()[-2:])[:300],
        )
        self._record(result)
        return result

    # ------------------------------------------------------------- the record

    def _record(self, result: InstallResult) -> None:
        """Every attempt, installed or refused, in one place Mohsin can read."""
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result.to_dict(), default=str) + "\n")
        except Exception:
            log_swallowed(_log, "writing the package install journal", level=logging.WARNING)

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.journal.is_file():
            return []
        rows = []
        for line in self.journal.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows[-max(1, limit):]

    def status(self) -> dict[str, Any]:
        rows = self.history(200)
        return {
            "installed_this_session": self._installed_this_session,
            "session_limit": self.session_limit,
            "installed_total": sum(1 for row in rows if row.get("ok")),
            "refused_total": sum(1 for row in rows if row.get("refused_because")),
            "environment": sys.executable,
            "journal": str(self.journal),
            "recent": rows[-5:],
        }
