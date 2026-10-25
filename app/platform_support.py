"""Small, dependency-free host adapters shared by GUI and launchers."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def open_path(path: str | Path) -> bool:
    """Open a local path with the host default application, without a shell."""
    target = str(Path(path).expanduser().resolve())
    try:
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        command = "open" if sys.platform == "darwin" else "xdg-open"
        executable = shutil.which(command)
        if not executable:
            return False
        subprocess.Popen(
            [executable, target], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False
