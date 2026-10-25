"""Whether this process is elevated, and what that permits.

Administrator access is available to this system, but it is not a mode the
agent switches itself into. Two separate facts are kept apart deliberately:

  *Am I elevated?* -- an observable property of the process, answered by
  Windows, never guessed. An agent that assumes it is admin writes plans that
  fail confusingly halfway through; one that assumes it is not refuses work it
  could do. Either way the fix is to look.

  *May I use it?* -- an authority question, answered by the same kernel that
  answers every other one, at HIGH_CONSEQUENCE. An elevated shell can switch
  off the controls that supervise the agent, so it stays one human answer
  away permanently rather than being folded into owner mode.

Nothing here elevates anything. Relaunching elevated is a decision for the
person who owns the machine, and this module's job is to tell them plainly
what is and is not currently possible.
"""
from __future__ import annotations

import ctypes
import os
import sys
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def is_elevated() -> bool:
    """True when this process is running with Administrator rights.

    Cached: the answer cannot change for the lifetime of a process, and this
    is consulted on planning paths where a syscall per check would be waste.
    """
    if os.name != "nt":
        try:
            return os.geteuid() == 0  # type: ignore[attr-defined]
        except AttributeError:
            return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


@lru_cache(maxsize=1)
def integrity_level() -> str:
    """A coarse description of what this process can reach."""
    if os.name != "nt":
        return "ROOT" if is_elevated() else "USER"
    return "HIGH" if is_elevated() else "MEDIUM"


def status() -> dict[str, Any]:
    """Elevation facts for /doctor, provider status, and the agent's own model of itself."""
    elevated = is_elevated()
    return {
        "elevated": elevated,
        "integrity_level": integrity_level(),
        "platform": sys.platform,
        "executable": sys.executable,
        # Said plainly so the agent can tell the user what to do rather than
        # failing at the last step of a plan it should never have made.
        "explanation": (
            "Running as Administrator. Elevated actions are possible, and each one "
            "still asks for approval before it runs."
            if elevated else
            "Running as a standard user. Actions needing Administrator will be refused "
            "by Windows; restart HAYAT from an elevated terminal to make them possible."
        ),
        "relaunch_hint": None if elevated else (
            'Right-click Windows Terminal and choose "Run as administrator", then start HAYAT there.'
        ),
    }


def requires_elevation(command: str) -> bool:
    """Whether a shell command plainly needs Administrator to succeed.

    A conservative, evidence-based list rather than an attempt at completeness:
    these are the operations that fail with access-denied for a standard user.
    Used to answer *before* running something, so the agent can say "this needs
    Administrator and I am not elevated" instead of producing a confusing
    permission error three steps into a task.
    """
    lowered = f" {command.lower()} "
    markers = (
        "new-service", "remove-service", "set-service", "sc create", "sc delete",
        "sc config", "set-executionpolicy -scope localmachine", "bcdedit",
        "diskpart", "dism ", "sfc /scannow", "net localgroup administrators",
        "new-localuser", "remove-localuser", "set-localuser",
        "reg add hklm", "reg delete hklm", "set-itemproperty -path hklm",
        "new-netfirewallrule", "set-netfirewallprofile", "netsh advfirewall",
        "takeown", "icacls c:\\windows", "install-windowsfeature",
        "enable-windowsoptionalfeature", "stop-computer", "restart-computer",
    )
    return any(marker in lowered for marker in markers)


def preflight(command: str) -> tuple[bool, str]:
    """Can this command work right now? Returns (runnable, explanation).

    The point is to fail early and legibly. A refusal that names the reason and
    the remedy is useful; an access-denied buried in stderr after a plan has
    already half-executed is not.
    """
    if not requires_elevation(command):
        return True, "does not require Administrator"
    if is_elevated():
        return True, "requires Administrator, and this process is elevated"
    return False, (
        "This needs Administrator rights and HAYAT is running as a standard user, "
        "so Windows would refuse it. Restart HAYAT from an elevated terminal to run it."
    )
