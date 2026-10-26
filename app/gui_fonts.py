"""HAYAT's type faces.

Text stays in Segoe UI, the Windows interface face. Large headings use the Display cut of
Segoe UI Variable, the Windows 11 face drawn for big sizes: tighter spacing and crisper
curves than Segoe UI scaled up. Tk draws through GDI, which lists that cut under a name
cut to 31 characters ("... Display Semib"). Where the face is not installed (Windows 10,
other systems) headings fall back to Segoe UI Semibold, the face they used before.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

UI = "Segoe UI"
SEMIBOLD = "Segoe UI Semibold"
EMOJI = "Segoe UI Emoji"


def _installed(name: str) -> bool:
    if sys.platform != "win32":
        return False
    folders = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    if os.environ.get("LOCALAPPDATA"):
        folders.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    return any((folder / name).exists() for folder in folders)


DISPLAY = "Segoe UI Variable Display Semib" if _installed("SegUIVar.ttf") else SEMIBOLD
