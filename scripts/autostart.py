"""Install/remove a per-user login launcher without administrator access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "hayat-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
ICON = ROOT / "app" / "assets" / "hayat.ico"


def launcher_path():
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup" / (IDENTIFIER + ".lnk")
    if sys.platform == "darwin":
        return Path.home() / "Library/LaunchAgents" / ("local." + IDENTIFIER + ".plist")
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "autostart" / (IDENTIFIER + ".desktop")


def install():
    target = launcher_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    interpreter = Path(sys.executable)
    if sys.platform == "win32":
        windowless = interpreter.with_name("pythonw.exe")
        if windowless.exists():
            interpreter = windowless
        env = {**os.environ, "HAYAT_STARTUP_LINK": str(target), "HAYAT_STARTUP_PYTHON": str(interpreter),
               "HAYAT_STARTUP_ROOT": str(ROOT), "HAYAT_STARTUP_ICON": str(ICON)}
        script = """$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($env:HAYAT_STARTUP_LINK)
$link.TargetPath = $env:HAYAT_STARTUP_PYTHON
$link.Arguments = '-m scripts.supervise'
$link.WorkingDirectory = $env:HAYAT_STARTUP_ROOT
$link.WindowStyle = 7
$link.Description = 'HAYAT: start at login with bounded crash recovery'
$link.IconLocation = $env:HAYAT_STARTUP_ICON
$link.Save()
"""
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       env=env, check=True, capture_output=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif sys.platform == "darwin":
        target.write_bytes(plistlib.dumps({"Label": "local." + IDENTIFIER,
            "ProgramArguments": [str(interpreter), "-m", "scripts.supervise"], "WorkingDirectory": str(ROOT),
            "RunAtLoad": True, "KeepAlive": False, "LimitLoadToSessionType": "Aqua"}))
    else:
        def quote(value):
            return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
        target.write_text("[Desktop Entry]\nType=Application\nName=HAYAT\nTerminal=false\n"
                          f"Exec={quote(interpreter)} -m scripts.supervise\nPath={ROOT}\n"
                          "X-GNOME-Autostart-enabled=true\n", encoding="utf-8")
    (ROOT / "data/supervision/disabled").unlink(missing_ok=True)
    return target


def remove():
    target = launcher_path()
    target.unlink(missing_ok=True)
    marker = ROOT / "data/supervision/disabled"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("Disabled by owner\n", encoding="utf-8")
    return target


def status():
    state = {}
    try:
        state = json.loads((ROOT / "data/supervision/status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"installed": launcher_path().is_file(), "launcher": str(launcher_path()), "last_run": state}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "remove", "status"))
    action = parser.parse_args().action
    if action == "install":
        install()
    elif action == "remove":
        remove()
    print(json.dumps(status(), indent=2))
