#!/usr/bin/env python3
"""Portable HAYAT launcher: ``python run.py`` on Windows, macOS, or Linux."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import bootstrap


ROOT = Path(__file__).resolve().parent


def gui_available() -> bool:
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return False
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return True
    except Exception:
        return False


def normalized_arguments(arguments: list[str]) -> list[str]:
    explicit_modes = {
        "--gui", "--cli-only", "--goal", "--web", "--voice", "--combined",
        "--tray", "--scheduler-daemon", "--browser-bridge-token", "--secret-set",
        "--secret-delete",
    }
    if any(item in explicit_modes or any(item.startswith(mode + "=") for mode in explicit_modes)
           for item in arguments):
        return arguments
    return ["--gui" if gui_available() else "--cli-only", *arguments]


def ensure_environment() -> Path:
    compatible, _ = bootstrap.inspect_venv()
    if not compatible:
        print("HAYAT is preparing native dependencies for this operating system...")
        result = subprocess.run([sys.executable, str(ROOT / "bootstrap.py")], cwd=str(ROOT))
        if result.returncode:
            raise SystemExit(result.returncode)
    compatible, detected = bootstrap.inspect_venv()
    if not compatible:
        raise SystemExit(f"The new environment is not compatible with this host: {detected}")
    return bootstrap.VENV_PYTHON


def main() -> int:
    from app import launcher, portable

    # The folder may have moved: the venv must find the Python beside it.
    portable.repair_venv()
    python = ensure_environment()
    with launcher.folder_runtime(print) as env:
        initialized = subprocess.run([str(python), "-m", "scripts.init_db"], cwd=str(ROOT), env=env)
        if initialized.returncode:
            return initialized.returncode
        command = [str(python), str(ROOT / "main.py"), *normalized_arguments(sys.argv[1:])]
        try:
            return subprocess.run(command, cwd=str(ROOT), env=env).returncode
        except KeyboardInterrupt:
            return 130


if __name__ == "__main__":
    raise SystemExit(main())
