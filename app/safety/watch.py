"""Noticing when something else on this machine is watching Mohsin.

HAYAT already refuses to be a surveillance tool itself: the camera needs
consent, privacy mode kills the sensors, and nothing leaves the machine. This
is the other half of that promise -- checking whether some *other* program is
doing what HAYAT will not.

Strictly defensive, and strictly local. It reads the state of this machine and
reports it. It does not scan a network, touch another host, or act on what it
finds; the answer to "something is using your webcam" is to tell Mohsin, not
to start killing processes, because the thing using the webcam is very often
the video call he is on.

The honest limit, stated here rather than discovered later: this catches
ordinary software behaving observably -- a program holding the camera, an
unexpected listener on a port, a suspicious autostart entry. It does not
detect a competent attacker with kernel access, and nothing that runs as an
ordinary user process can. Presenting it as more than that would be the exact
kind of false assurance this project exists to avoid.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


#: Registry keys Windows uses to start programs at login. A surveillance tool
#: has to survive a reboot somehow, and this is where most of them do it.
_AUTOSTART_KEYS = (
    r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Run",
    r"HKLM:\Software\Microsoft\Windows\CurrentVersion\Run",
    r"HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
)

#: Windows records which applications have used the camera and microphone,
#: with a LastUsedTimeStop of 0 meaning "still using it right now".
_CAPABILITY_KEYS = {
    "camera": r"HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam",
    "microphone": r"HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone",
}

#: Names that are ordinary on a developer's machine. Flagging these would
#: train Mohsin to ignore the report, which is worse than not having one.
_EXPECTED = {
    "ollama", "ollama app", "llama-server", "python", "pythonw", "code",
    "windowsterminal", "powershell", "pwsh", "explorer", "svchost", "chrome",
    "msedge", "firefox", "teams", "zoom", "discord", "obs64", "nvcontainer",
    "steam", "spotify", "git", "node",
}

#: Windows itself. These hold listening sockets on every healthy machine, and
#: the first version of this check flagged all of them -- 36 warnings, none
#: actionable. A security report that cries wolf is worse than no report,
#: because the one real finding arrives in a list the reader has learned to
#: skip.
_SYSTEM_PROCESSES = {
    "system", "idle", "lsass", "wininit", "services", "svchost", "spoolsv",
    "smss", "csrss", "winlogon", "dwm", "fontdrvhost", "sihost", "taskhostw",
    "searchindexer", "wudfhost", "dasHost", "dashost", "msmpeng",
}

#: Dynamic RPC range. Windows assigns these to system services at boot; a
#: specific number here carries no meaning.
_EPHEMERAL_PORT_FLOOR = 49152

#: Ports where a listener genuinely changes Mohsin's exposure. RDP and VNC
#: mean someone can drive this machine; the database ports mean data is
#: reachable from the network.
_SENSITIVE_PORTS = {
    3389: "Remote Desktop -- this machine can be controlled remotely",
    5900: "VNC -- this machine's screen is shareable",
    5901: "VNC -- this machine's screen is shareable",
    23: "Telnet -- unencrypted remote shell",
    5432: "PostgreSQL is reachable", 3306: "MySQL is reachable",
    27017: "MongoDB is reachable", 6379: "Redis is reachable",
    9200: "Elasticsearch is reachable",
}


@dataclass
class Finding:
    """One observation, with enough context for a person to judge it."""

    kind: str
    subject: str
    detail: str = ""
    severity: str = "notice"          # notice | attention
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "subject": self.subject, "detail": self.detail,
            "severity": self.severity, "evidence": self.evidence,
        }


def _powershell(command: str, timeout: float = 25.0) -> str:
    if os.name != "nt":
        raise OSError("Windows observation is unavailable on this platform")
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("The local Windows observation could not finish") from exc
    if completed.returncode != 0:
        raise RuntimeError("The local Windows observation returned an error")
    return completed.stdout


def sensor_users() -> list[Finding]:
    """Applications Windows records as having used the camera or microphone.

    A LastUsedTimeStop of 0 means the application has it open right now, which
    is the case worth surfacing: something is watching or listening at this
    moment.
    """
    findings: list[Finding] = []
    for sensor, key in _CAPABILITY_KEYS.items():
        output = _powershell(
            f"Get-ChildItem -Path '{key}' -ErrorAction SilentlyContinue | ForEach-Object {{"
            "  $p = $_.PSChildName;"
            "  $stop = (Get-ItemProperty $_.PSPath -Name LastUsedTimeStop -ErrorAction SilentlyContinue).LastUsedTimeStop;"
            "  if ($null -ne $stop) { \"$p|$stop\" } }"
        )
        for line in output.splitlines():
            if "|" not in line:
                continue
            name, _, stop = line.partition("|")
            app = name.strip().replace("#", "\\")
            in_use = stop.strip() in {"0", ""}
            findings.append(Finding(
                kind=f"{sensor}_in_use" if in_use else f"{sensor}_recent",
                subject=app,
                detail=(f"is using the {sensor} right now" if in_use
                        else f"has used the {sensor} recently"),
                severity="attention" if in_use else "notice",
                evidence={"sensor": sensor, "active": in_use},
            ))
    return findings


def listening_ports() -> list[Finding]:
    """Programs accepting inbound connections on this machine.

    A listener reachable from outside the loopback interface is how a remote
    tool gets in, so those are separated from the ordinary local ones.
    """
    output = _powershell(
        "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ForEach-Object {"
        "  $p = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName;"
        "  \"$($_.LocalAddress)|$($_.LocalPort)|$p\" }"
    )
    findings: list[Finding] = []
    for line in output.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        address, port, process = parts
        if not port.isdigit():
            continue
        number = int(port)
        local_only = address in {"127.0.0.1", "::1"}
        name = (process or "unknown").lower()

        # Severity is about exposure, not about the existence of a socket.
        if number in _SENSITIVE_PORTS and not local_only:
            severity, detail = "attention", _SENSITIVE_PORTS[number]
        elif local_only:
            severity, detail = "notice", "listening for local connections only"
        elif name in _SYSTEM_PROCESSES or number >= _EPHEMERAL_PORT_FLOOR:
            severity, detail = "notice", f"Windows service listening on {address}"
        elif name in _EXPECTED:
            severity, detail = "notice", f"known application listening on {address}"
        else:
            severity, detail = "attention", f"unrecognised program accepting connections on {address}"

        findings.append(Finding(
            kind="listening_port", subject=f"{process or 'unknown'}:{port}",
            detail=detail, severity=severity,
            evidence={"address": address, "port": number, "process": process,
                      "local_only": local_only},
        ))
    return findings


def autostart_entries() -> list[Finding]:
    """Programs configured to launch at login."""
    findings: list[Finding] = []
    for key in _AUTOSTART_KEYS:
        output = _powershell(
            f"$k = Get-Item -Path '{key}' -ErrorAction SilentlyContinue;"
            "if ($k) { $k.GetValueNames() | ForEach-Object { \"$_|$($k.GetValue($_))\" } }"
        )
        for line in output.splitlines():
            name, _, command = line.partition("|")
            if not name.strip():
                continue
            reason = _suspicious_autostart(command)
            findings.append(Finding(
                kind="autostart", subject=name.strip(),
                detail=reason or command.strip()[:200],
                # Informational by default. Almost every login entry is
                # software Mohsin installed on purpose -- OneDrive, Docker,
                # a game launcher -- and calling those a security concern is
                # how the two that matter get lost.
                severity="attention" if reason else "notice",
                evidence={"key": key, "command": command.strip()[:400]},
            ))
    return findings


#: How a persistence mechanism actually looks, as opposed to how ordinary
#: installed software looks: launched from a temp or cache directory, or
#: routed through an interpreter so the real payload is a script.
_SUSPICIOUS_LAUNCHERS = ("powershell", "cmd.exe /c", "mshta", "wscript", "cscript",
                         "rundll32", "regsvr32", "certutil", "bitsadmin", "curl ", "wget ")
_SUSPICIOUS_LOCATIONS = (r"\appdata\local\temp", r"\windows\temp", r"\programdata\temp",
                         r"\downloads\\", r"\public\\")


def _suspicious_autostart(command: str) -> str:
    """Why a login entry deserves attention, or empty if it does not."""
    lowered = command.lower()
    if any(marker in lowered for marker in _SUSPICIOUS_LOCATIONS):
        return "starts at login from a temporary directory, which installed software does not do"
    if any(marker in lowered for marker in _SUSPICIOUS_LAUNCHERS):
        return "starts at login through a script interpreter rather than as a program"
    if "-enc" in lowered or "frombase64" in lowered:
        return "starts at login with an encoded command"
    return ""


def remote_access_tools() -> list[Finding]:
    """Running processes whose whole purpose is remote control of this machine.

    These are legitimate software -- Mohsin may well have installed one on
    purpose. The point is that he should know it is running, not that it is
    malicious.
    """
    known = {
        "teamviewer": "TeamViewer", "anydesk": "AnyDesk", "rustdesk": "RustDesk",
        "vncserver": "VNC", "winvnc": "VNC", "tightvnc": "TightVNC",
        "ammyy": "Ammyy Admin", "supremo": "Supremo", "quickassist": "Windows Quick Assist",
        "dwagent": "DWService", "logmein": "LogMeIn", "splashtop": "Splashtop",
        "screenconnect": "ScreenConnect", "atera": "Atera", "syncro": "Syncro",
    }
    output = _powershell("Get-Process | ForEach-Object { $_.ProcessName }")
    running = {line.strip().lower() for line in output.splitlines() if line.strip()}
    findings: list[Finding] = []
    for marker, label in known.items():
        matched = [name for name in running if marker in name]
        if matched:
            findings.append(Finding(
                kind="remote_access", subject=label,
                detail="a remote-access tool is running; it can see and control this machine",
                severity="attention",
                evidence={"processes": sorted(matched)},
            ))
    return findings


def scan() -> dict[str, Any]:
    """Look at this machine and report what is observably watching it."""
    findings: list[Finding] = []
    completed_checks: list[str] = []
    failed_checks: list[str] = []
    for collector in (sensor_users, remote_access_tools, listening_ports, autostart_entries):
        try:
            findings.extend(collector())
            completed_checks.append(collector.__name__)
        except Exception:
            failed_checks.append(collector.__name__)
            # A check that cannot run is reported as absent evidence, never as
            # a clean result.
            findings.append(Finding(
                kind="check_failed", subject=collector.__name__,
                detail="this check could not run, so its area is unverified",
                severity="notice",
            ))
    attention = [item for item in findings if item.severity == "attention"]
    return {
        "at": datetime.now(UTC).isoformat(),
        "findings": [item.to_dict() for item in findings],
        "needs_attention": [item.to_dict() for item in attention],
        "complete": not failed_checks,
        "completed_checks": completed_checks,
        "failed_checks": failed_checks,
        "counts": {
            "total": len(findings), "attention": len(attention),
            "sensors_active": sum(1 for item in findings if item.kind.endswith("_in_use")),
            "remote_access": sum(1 for item in findings if item.kind == "remote_access"),
        },
        "limits": (
            "This reports software behaving observably: a program holding the camera, an "
            "unexpected listener, a remote-access tool, a login entry. It cannot detect an "
            "attacker with kernel access, and no ordinary user process can."
        ),
    }


def summary() -> str:
    """What HAYAT should say when asked whether anything is watching."""
    report = scan()
    attention = report["needs_attention"]
    lines: list[str] = []
    active = report["counts"]["sensors_active"]
    if active:
        lines.append(f"{active} application(s) are using the camera or microphone right now.")
    if not attention and report["failed_checks"]:
        lines.append("No attention findings in the completed checks; observation is incomplete.")
    elif not attention:
        lines.append("Nothing on this machine is observably watching you.")
    else:
        lines.append("Worth a look:")
        for item in attention[:10]:
            lines.append(f"  - {item['subject']}: {item['detail']}")
    if report["failed_checks"]:
        lines.append("Unverified checks: " + ", ".join(report["failed_checks"]) + ".")
    lines.append(report["limits"])
    return "\n".join(lines)
