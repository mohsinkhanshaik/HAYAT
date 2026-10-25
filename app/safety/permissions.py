from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, load_yaml
from app.safety.command_policy import CommandPolicy, Decision
from app.safety.elevation import requires_elevation


class PermissionDecision(StrEnum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


_STRICTNESS = {PermissionDecision.ALLOW: 0, PermissionDecision.ASK: 1, PermissionDecision.DENY: 2}


def strictest(*decisions: PermissionDecision) -> PermissionDecision:
    """Return the most restrictive decision. Policy may tighten, never loosen."""
    return max(decisions, key=lambda item: _STRICTNESS[item])


@dataclass(slots=True)
class PermissionResult:
    decision: PermissionDecision
    reason: str
    category: str
    classification: dict[str, Any] | None = None


class PermissionManager:
    def __init__(self, config: dict[str, Any] | None = None, project_root: Path = PROJECT_ROOT) -> None:
        self.config = config or load_yaml("config/permissions.yaml")
        self.project_root = project_root.resolve()
        self.policies = self.config.get("permissions", {})
        self.default = PermissionDecision(self.config.get("default", "ASK"))
        self.blocked_patterns = [re.compile(item) for item in self.config.get("blocked_command_patterns", [])]
        self.ask_patterns = [re.compile(item) for item in self.config.get("ask_command_patterns", [])]
        self.command_policy = CommandPolicy(self.config)

    SHELL_CATEGORIES = {"terminal", "powershell", "powershell_safe", "powershell_unknown", "powershell_admin"}

    #: Commands that end the session everyone else is in the middle of, and the
    #: policy key each one answers to.
    #:
    #: These used to be indistinguishable from any other elevated command, so
    #: granting powershell_admin made `Restart-Computer` silent -- nothing
    #: destroyed that was saved, and everything that was not gone. Mohsin asked
    #: for these two to keep asking while the rest of admin work stays open,
    #: which needs them to be their own categories rather than a shade of
    #: "elevated".
    #:
    #: Checked before the elevation branch, so the answer comes from
    #: restart_computer/shutdown_computer rather than powershell_admin.
    POWER_COMMANDS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
        ("restart_computer", re.compile(
            r"(?i)(?:^|[\s;|&])(?:restart-computer\b"
            r"|shutdown(?:\.exe)?\b[^;|&]*(?:\s[/-]r\b|\s[/-]-reboot\b)"
            r"|reboot\b"
            r"|wmic\b[^;|&]*\bos\b[^;|&]*\breboot\b)")),
        ("shutdown_computer", re.compile(
            r"(?i)(?:^|[\s;|&])(?:stop-computer\b"
            r"|shutdown(?:\.exe)?\b[^;|&]*(?:\s[/-]s\b|\s[/-]-poweroff\b|\s[/-]p\b)"
            r"|poweroff\b"
            r"|wmic\b[^;|&]*\bos\b[^;|&]*\bshutdown\b)")),
    )

    def _power_category(self, command: str) -> str | None:
        """Whether this command restarts or shuts the machine down."""
        for category, pattern in self.POWER_COMMANDS:
            if pattern.search(str(command or "")):
                return category
        return None

    def check(self, category: str, arguments: dict[str, Any] | None = None) -> PermissionResult:
        arguments = arguments or {}
        if category in self.SHELL_CATEGORIES or category.startswith("powershell"):
            return self._check_command(category, str(arguments.get("command", "")))
        value = self.policies.get(category, self.default.value)
        return PermissionResult(PermissionDecision(value), f"Policy for {category} is {value}.", category)

    def _check_command(self, category: str, command: str) -> PermissionResult:
        """Classify shell text, then apply the configured policy for the resulting class.

        The classifier chooses which policy key applies; the policy can only make
        the result stricter. `powershell_admin` is denied before classification so
        an elevated category can never be downgraded by a benign-looking command.
        """
        classification = self.command_policy.classify(command)
        if classification.decision is Decision.DENY:
            return PermissionResult(
                PermissionDecision.DENY, classification.reason, category, classification.to_dict()
            )

        # Before the elevation branch. Restarting is an elevated action, so
        # checking after it would file these under powershell_admin and hand
        # back whatever that says -- which is the behaviour being replaced.
        power = self._power_category(command)
        if power is not None:
            value = self.policies.get(power, PermissionDecision.ASK.value)
            return PermissionResult(
                PermissionDecision(value), f"Policy for {power} is {value}.",
                power, classification.to_dict(),
            )

        if category == "powershell_admin" or requires_elevation(command):
            category = "powershell_admin"
            value = self.policies.get(category, PermissionDecision.DENY.value)
            return PermissionResult(
                PermissionDecision(value), f"Policy for {category} is {value}.",
                category, classification.to_dict(),
            )

        resolved_category = "powershell_safe" if classification.decision is Decision.ALLOW else "powershell_unknown"
        configured = PermissionDecision(self.policies.get(resolved_category, self.default.value))
        baseline = PermissionDecision(classification.decision.value)
        decision = strictest(baseline, configured)
        reason = classification.reason
        if decision is not baseline:
            reason = f"{reason} Policy for {resolved_category} tightens this to {decision.value}."
        return PermissionResult(decision, reason, resolved_category, classification.to_dict())

    def allowed_roots(self) -> list[Path]:
        """Directories the agent may work inside.

        This used to be hard-coded to the project root while `allowed_roots`
        sat unread in permissions.yaml -- the config described a behaviour the
        code did not have. It is real now, because an assistant that cannot
        open a file on your Desktop is not an assistant, and because a limit
        the owner can see and edit is worth more than one buried in a method.

        Widening where the agent may work does not widen *what* it may do
        there: protected_paths still refuses Windows, Program Files, SSH keys
        and browser profiles, and every write still passes the risk tier and
        approval path above.
        """
        configured = self.config.get("allowed_roots") or ["."]
        roots: list[Path] = []
        for entry in configured:
            text = str(entry).strip()
            if not text:
                continue
            if text == ".":
                roots.append(self.project_root)
                continue
            expanded = os.path.expandvars(os.path.expanduser(text))
            if "%" in expanded:  # an unset Windows variable; skip rather than guess
                continue
            roots.append(Path(expanded).resolve(strict=False))
        return roots or [self.project_root]

    def validate_project_path(self, path: str | Path, write: bool = False) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve(strict=False)

        roots = self.allowed_roots()
        inside = next(
            (root for root in roots if resolved == root or root in resolved.parents), None
        )
        if inside is None:
            raise PermissionError(
                f"Path is outside every allowed root: {resolved}. "
                f"Allowed: {', '.join(str(root) for root in roots)}"
            )

        # Credential-file patterns are matched relative to whichever root the
        # path fell inside, so a .env is protected wherever it lives rather
        # than only in the project directory.
        relative = resolved.relative_to(inside).as_posix()
        for pattern in self.config.get("protected_project_files", []):
            normalized_pattern = str(pattern).replace("\\", "/").lower()
            if normalized_pattern.startswith("./"):
                normalized_pattern = normalized_pattern[2:]
            if fnmatch.fnmatch(relative.lower(), normalized_pattern) or fnmatch.fnmatch(
                resolved.name.lower(), normalized_pattern
            ):
                raise PermissionError(f"Protected file is unavailable to agent file tools: {relative}")
        normalized = str(resolved).lower()
        for pattern in self.config.get("protected_paths", []):
            pattern_text = os.path.expandvars(str(pattern)).lower()
            if fnmatch.fnmatch(normalized, pattern_text) or fnmatch.fnmatch(
                normalized, f"{pattern_text}\\*"
            ):
                raise PermissionError(f"Path is protected: {resolved}")
        return resolved
