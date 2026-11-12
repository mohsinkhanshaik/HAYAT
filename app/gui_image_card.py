"""What a picture in the chat can do: a preview sized to the conversation, and its actions.

A generated 2048 px picture was shown as a 380x260 stamp with one link. The
preview now follows the conversation's width, and the picture can be shown in
its folder, copied to the clipboard as an image, or made again from the prompt
recorded when it was generated (the request is put in the message box; nothing
is sent until the owner presses Send).
"""
from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)
#: The preview never exceeds this, however wide the window: it is a preview.
MAX_PREVIEW = (760, 480)
MIN_PREVIEW = (380, 260)


def preview_box(chat_width: int) -> tuple[int, int]:
    """The thumbnail bound for a conversation this wide (padding and scrollbar left free)."""
    usable = int(chat_width or 0) - 140
    width = max(MIN_PREVIEW[0], min(MAX_PREVIEW[0], usable))
    height = max(MIN_PREVIEW[1], min(MAX_PREVIEW[1], round(width * 0.632)))
    return width, height


def generation_record(path: Path | str, limit: int = 400) -> dict[str, Any]:
    """The manifest entry HAYAT wrote when it generated this picture, or {} for any other image."""
    target = Path(path)
    manifest = target.parent / "manifest.jsonl"
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if Path(str(entry.get("path", ""))).name == target.name:
            return entry
    return {}


def copy_image(path: Path | str) -> str:
    """Put the picture on the Windows clipboard as an image; returns what happened, in words."""
    if sys.platform != "win32":
        return "Copying images is available on Windows only."
    try:
        import win32clipboard
        from PIL import Image

        with Image.open(path) as image:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "BMP")
        data = buffer.getvalue()[14:]  # a DIB is the bitmap without its 14-byte file header
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        finally:
            win32clipboard.CloseClipboard()
        return "Image copied: paste it anywhere with Ctrl+V."
    except Exception:
        log_swallowed(_log, "copying an image to the clipboard", level=logging.WARNING)
        return "Couldn't copy that image to the clipboard."


def reveal(path: Path | str) -> None:
    """Open the picture's folder with the file selected."""
    target = Path(path)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(target)])  # noqa: S603,S607
        else:
            from app.platform_support import open_path

            open_path(target.parent)
    except Exception:
        log_swallowed(_log, "showing an image in its folder")
