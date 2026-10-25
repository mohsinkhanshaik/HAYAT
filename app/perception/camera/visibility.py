"""Whether a camera frame shows anything, and which "read" text is real.

A near-black frame went to the vision model, which described "a dark,
abstract background with a textured, mottled appearance", and the OCR added
"WtuCOMtTO" and "auz". Both are the model and the OCR engine doing their best
with nothing. This measures the frame first and says plainly when there is
nothing to see, and keeps only text that reads like words.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

#: Mean brightness (0-255) below which a frame is too dark to describe.
DARK = 38.0
#: Mean brightness above which it is washed out.
BRIGHT = 235.0
#: Contrast (standard deviation) below which the frame is one flat tone:
#: a covered lens, a closed privacy shutter, a blank wall filling the view.
FLAT = 9.0

MESSAGES = {
    "dark": ("I looked, but the picture is too dark to make anything out. The room may be unlit, "
             "or the lens is covered. Turn on a light (or uncover the camera) and ask me again."),
    "covered": ("I looked, but the picture is one flat colour with nothing in it. The lens is probably "
                "covered or the camera's privacy shutter is closed. Open it and ask me again."),
    "overexposed": ("I looked, but the picture is washed out by bright light. Move the camera away from "
                    "the window or lamp and ask me again."),
}


def assess(gray: Any) -> dict[str, Any]:
    """Brightness, contrast and a verdict for one greyscale frame."""
    import numpy as np

    values = np.asarray(gray, dtype=np.float32)
    brightness, contrast = float(values.mean()), float(values.std())
    if brightness < DARK:
        state = "dark"
    elif contrast < FLAT:
        state = "covered"
    elif brightness > BRIGHT:
        state = "overexposed"
    else:
        state = "clear"
    return {"state": state, "brightness": round(brightness, 1), "contrast": round(contrast, 1),
            "message": MESSAGES.get(state, "")}


def assess_file(path: Any) -> dict[str, Any]:
    import cv2

    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return {"state": "unknown", "brightness": None, "contrast": None, "message": ""}
    return assess(gray)


def settle(read: Callable[[], Any], *, timeout: float = 1.5, interval: float = .1,
           clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Read frames until auto-exposure has settled, or ``timeout`` passes.

    A webcam's first frames after switching on are dark while it adjusts.
    Capturing at once gave the vision model a near-black picture to invent
    a scene from. Settled means two readings in a row within 3 levels of
    each other, above black.
    """
    import numpy as np

    started, last, steady, reads = clock(), None, 0, 0
    while clock() - started < timeout:
        try:
            frame = read()
        except Exception:
            break
        reads += 1
        level = float(np.asarray(frame, dtype=np.float32).mean())
        if last is not None and abs(level - last) < 3.0 and level > 12.0:
            steady += 1
            if steady >= 2:
                return {"settled": True, "reads": reads, "seconds": round(clock() - started, 2), "brightness": level}
        else:
            steady = 0
        last = level
        sleep(interval)
    return {"settled": False, "reads": reads, "seconds": round(clock() - started, 2), "brightness": last}


_WORD = re.compile(r"[A-Za-z]+")


def _wordlike(token: str) -> bool:
    """A real word, not an OCR smear: has a vowel, and no mid-word case flips."""
    if len(token) < 2:
        return False
    if not re.search(r"[aeiouyAEIOUY]", token):
        return len(token) <= 4 and token.isupper()          # short acronyms: TV, USB, PDF
    flips = sum(1 for a, b in zip(token, token[1:]) if a.islower() and b.isupper())
    return flips == 0


def readable_text(text: str) -> str:
    """Only the lines that read like text; near-duplicate lines once."""
    kept: list[str] = []
    for line in str(text or "").splitlines():
        line = " ".join(line.split())
        tokens = _WORD.findall(line)
        letters = sum(len(token) for token in tokens)
        if letters < .6 * len(line.replace(" ", "")):
            continue
        # One fragment of three letters or fewer ("auz", "-n") is a smear.
        if letters < 4 and len(tokens) < 2:
            continue
        if sum(_wordlike(token) for token in tokens) < max(1, .7 * len(tokens)):
            continue
        folded = line.casefold()
        if any(folded in other.casefold() or other.casefold() in folded for other in kept):
            continue
        kept.append(line)
    return "\n".join(kept)
