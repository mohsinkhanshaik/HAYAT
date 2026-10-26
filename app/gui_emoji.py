"""Colour emoji in Tk text.

Tk 8.6 draws text through GDI, which has no colour glyphs: "🎉" came out as a flat outline
and many emoji as empty boxes. Each emoji is drawn once with Pillow from the colour layers
of Segoe UI Emoji and placed in the text as a small picture. The character itself stays in
the text, hidden, so copying, searching and saving still see "🎉".

Skin-tone modifiers and zero-width joiners need a text shaper (raqm) that Pillow here does
not have; they are dropped, so 👋🏽 shows the default 👋 and a joined family shows its people.
Flags stay as text. When Pillow or the font is missing, the text is inserted unchanged.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

# Symbols below U+FFFF that are emoji by default (Unicode Emoji_Presentation). Others in that range,
# such as ✓ ★ ☑ ▸, are ordinary text symbols HAYAT uses as icons, and stay text.
_BMP_EMOJI = ("⌚⌛⏩⏪⏫⏬⏰⏳◽◾☔☕♈♉♊♋♌♍♎♏♐♑♒♓♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽"
              "✅✊✋✨❌❎❓❔❕❗➕➖➗➰➿⬛⬜⭐⭕")
EMOJI = re.compile("[\U0001F300-\U0001F3FA\U0001F400-\U0001F64F\U0001F680-\U0001F6FF"
                   "\U0001F7E0-\U0001F7EB\U0001F90C-\U0001F9FF\U0001FA70-\U0001FAFF" + _BMP_EMOJI + "]")
_JOINERS = re.compile("[\U0001F3FB-\U0001F3FF\u200d\ufe0f]")


def _font_path() -> Path | None:
    if sys.platform != "win32":
        return None
    path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "seguiemj.ttf"
    return path if path.exists() else None


@lru_cache(maxsize=512)
def emoji_image(char: str, size: int) -> Any:
    """A size x size RGBA picture of one emoji, or None when it cannot be drawn in colour."""
    font_file = _font_path()
    if font_file is None:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype(str(font_file), max(8, round(size * .88)))
        left, top, right, bottom = font.getbbox(char)
        if right <= left or bottom <= top:
            return None
        picture = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        # Centre the glyph's own ink in the square, whatever its bearing.
        x = (size - (right - left)) / 2 - left
        y = (size - (bottom - top)) / 2 - top
        ImageDraw.Draw(picture).text((x, y), char, font=font, embedded_color=True)
        return picture if picture.getbbox() else None
    except Exception:
        log_swallowed(_log, "drawing a colour emoji")
        return None


def has_emoji(text: str) -> bool:
    return bool(EMOJI.search(text))


def _photo(widget: Any, char: str, size: int) -> Any:
    cache = getattr(widget, "_hayat_emoji_photos", None)
    if cache is None:
        cache = widget._hayat_emoji_photos = {}
    key = (char, size)
    if key not in cache:
        picture = emoji_image(char, size)
        if picture is None:
            cache[key] = None
        else:
            from PIL import ImageTk

            cache[key] = ImageTk.PhotoImage(picture, master=widget)
    return cache[key]


def line_size(widget: Any) -> int:
    """The emoji size that sits in one line of this text's font."""
    from tkinter import font as tkfont

    try:
        height = tkfont.Font(root=widget, font=widget.cget("font")).metrics("linespace")
    except Exception:
        height = 20
    return max(12, min(48, int(height) - 2))


def insert(widget: Any, index: str, text: str, tags: Any = (), size: int | None = None) -> None:
    """`widget.insert(index, text, tags)`, with each emoji shown in colour."""
    if not has_emoji(text) or _font_path() is None:
        widget.insert(index, text, tags)
        return
    text = _JOINERS.sub("", text)
    size = size or line_size(widget)
    tags = (tags,) if isinstance(tags, str) else tuple(tags)
    try:
        widget.tag_configure("emoji_text", elide=True)
    except Exception:
        widget.insert(index, text, tags)
        return
    # Pieces go in one after another: "end" appends, and any other index becomes a mark that moves
    # past each piece (inserting each piece at a fixed index would put them in reverse order).
    at = index
    if index != "end":
        at = "hayat_emoji_at"
        widget.mark_set(at, index)
        widget.mark_gravity(at, "right")
    position = 0
    for match in EMOJI.finditer(text):
        if match.start() > position:
            widget.insert(at, text[position:match.start()], tags)
        photo = _photo(widget, match[0], size)
        if photo is None:
            widget.insert(at, match[0], tags)
        else:
            # The hidden character keeps the text whole for copy, search and saving; the picture shows it.
            widget.insert(at, match[0], tags + ("emoji_text",))
            widget.image_create(at, image=photo, align="center", padx=1)
        position = match.end()
    if position < len(text):
        widget.insert(at, text[position:], tags)
    if at != index:
        widget.mark_unset(at)
