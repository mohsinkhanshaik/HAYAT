from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass


@dataclass(slots=True)
class CameraDevice:
    name: str
    status: str
    pnp_class: str
    index_hint: int | None = None


def discover_windows_cameras() -> list[dict]:
    """Read Windows PnP camera metadata without opening a video stream."""
    if os.name != "nt":
        # Opening devices just to enumerate them would activate the camera.
        # The actual OpenCV capture path remains portable and probes only after
        # the owner requests camera use.
        return []
    script = r"""
    $rows = Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPClass -eq 'Camera' -or ($_.PNPClass -eq 'Image' -and $_.Name -match 'camera|webcam') } | Select-Object Name,Status,PNPClass
    @($rows) | ConvertTo-Json -Compress
    """
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10, check=True, encoding="utf-8", errors="replace",
        )
        raw = json.loads(result.stdout.strip() or "[]")
        rows = raw if isinstance(raw, list) else [raw]
        return [asdict(CameraDevice(str(row.get("Name") or "Camera"), str(row.get("Status") or "Unknown"), str(row.get("PNPClass") or "Camera"), index)) for index, row in enumerate(rows)]
    except Exception as exc:
        return [{"name": "Camera discovery error", "status": f"{type(exc).__name__}: {exc}", "pnp_class": "error", "index_hint": None}]
