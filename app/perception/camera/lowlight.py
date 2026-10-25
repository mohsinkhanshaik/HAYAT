"""Brightening dark footage -- the real version of "night vision" for an ordinary camera.

A normal camera sensor only sees visible light, so this cannot conjure detail
that the sensor never captured (and it cannot see through anything -- that is
not physically possible with a webcam). What it *can* do is pull usable detail
out of an under-exposed, noisy frame: lift the shadows, even out the contrast
locally, and clean up the grain that lifting shadows always exaggerates.

The steps, in order:

* a gamma lift whose strength is chosen from how dark the frame actually is,
  so a nearly-black frame is lifted hard and an already-bright one is left
  almost alone;
* CLAHE (contrast-limited local histogram equalisation) on the luminance
  channel only, so colour is not distorted while local detail comes back;
* an optional denoise pass, because raising the shadows raises the noise with
  them.

Pure OpenCV/numpy, on this machine, no model and no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Enhancement:
    image: Any  # the brightened BGR frame
    mean_before: float
    mean_after: float
    gamma: float
    denoised: bool


def _mean_luminance(image: Any) -> float:
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(gray.mean())


def enhance(image: Any, strength: float = 1.0, denoise: bool = True) -> Enhancement:
    """A brightened copy of a dark BGR frame, plus before/after luminance."""
    import cv2
    import numpy as np

    strength = max(0.0, min(2.0, float(strength)))
    before = _mean_luminance(image)

    # Gamma < 1 brightens; the darker the frame, the smaller the gamma. A frame
    # already around mid-grey (mean ~128) is barely touched.
    darkness = max(0.0, min(1.0, 1.0 - before / 160.0))
    gamma = 1.0 - 0.6 * darkness * strength
    gamma = max(0.35, min(1.0, gamma))
    lut = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)], dtype=np.uint8)
    lifted = cv2.LUT(image, lut)

    # Local contrast on luminance only, so colours are not pushed around.
    lab = cv2.cvtColor(lifted, cv2.COLOR_BGR2LAB)
    l_channel, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0 + 1.5 * strength, tileGridSize=(8, 8))
    lab = cv2.merge([clahe.apply(l_channel), a, b])
    result = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    did_denoise = False
    if denoise and strength > 0 and result.ndim == 3:
        # Bounded: only on frames small enough that this stays quick.
        if result.shape[0] * result.shape[1] <= 1920 * 1080:
            h = 3 + 4 * strength
            result = cv2.fastNlMeansDenoisingColored(result, None, h, h, 7, 21)
            did_denoise = True

    return Enhancement(result, before, _mean_luminance(result), round(gamma, 3), did_denoise)
