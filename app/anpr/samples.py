"""Synthetic test vehicles with known plates: for checking the system without anyone's real car.

Each sample is a simple vehicle front with a plate in the regional style (white
Indian or US plate with black characters, yellow UK rear plate) and a known
ground truth, so recognition accuracy can be measured. A short video moves the
vehicle across the frame to exercise tracking.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

FONT_CANDIDATES = (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\Arial.ttf", "DejaVuSans-Bold.ttf")
SAMPLE_PLATES = (("MH12AB1234", "IN"), ("DL3CAF0001", "IN"), ("KA05MN4321", "IN"), ("7ABC123", "US"), ("HAYAT01", "US"),
                 ("AB12CDE", "UK"))


def _font(size: int) -> Any:
    from PIL import ImageFont
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def plate_image(text: str, style: str = "IN", width: int = 360) -> Any:
    """A plate as a PIL image: border, characters, regional colours."""
    from PIL import Image, ImageDraw
    height = int(width / 4.4)
    background = "#F6C445" if style == "UK" else "#F4F4F0"
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle([2, 2, width - 3, height - 3], outline="#111111", width=4)
    spaced = text if style == "US" else " ".join([text[:2], text[2:4], text[4:-4], text[-4:]]) if style == "IN" and len(text) >= 8 else text
    # Shrink the characters until they leave a margin, as on a real plate (text touching the
    # border made the last character unreadable).
    size = int(height * .62)
    font = _font(size)
    while size > 10 and draw.textbbox((0, 0), spaced, font=font)[2] > width * .86:
        size -= 2
        font = _font(size)
    box = draw.textbbox((0, 0), spaced, font=font)
    draw.text(((width - (box[2] - box[0])) / 2 - box[0], (height - (box[3] - box[1])) / 2 - box[1]), spaced, fill="#111111", font=font)
    return image


def vehicle_scene(text: str, style: str = "IN", size: tuple[int, int] = (960, 640), offset: int = 0) -> Any:
    """A BGR frame of a simple car front with the plate on its bumper."""
    import numpy as np
    from PIL import Image, ImageDraw
    width, height = size
    scene = Image.new("RGB", size, "#6F7F8C")
    draw = ImageDraw.Draw(scene)
    draw.rectangle([0, int(height * .62), width, height], fill="#3B3F44")          # road
    left = 180 + offset
    draw.rounded_rectangle([left, 170, left + 600, 520], radius=40, fill="#8E1F2B")  # body
    draw.rectangle([left + 70, 190, left + 530, 300], fill="#9FC3D9")                # windscreen
    draw.ellipse([left + 40, 330, left + 130, 400], fill="#F2F2E8")                  # lamps
    draw.ellipse([left + 470, 330, left + 560, 400], fill="#F2F2E8")
    plate = plate_image(text, style, 300)
    scene.paste(plate, (left + 150, 420))
    return np.array(scene)[:, :, ::-1].copy()


def write_samples(folder: Path | str) -> dict[str, Any]:
    """Sample images and a short tracking video; returns {path: expected plate}."""
    import cv2
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    truth = {}
    for text, style in SAMPLE_PLATES:
        path = folder / f"sample_{text}.png"
        cv2.imwrite(str(path), vehicle_scene(text, style))
        truth[str(path)] = text
    video = folder / "sample_drive_MH12AB1234.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 15, (960, 640))
    for step in range(45):
        writer.write(vehicle_scene("MH12AB1234", "IN", offset=-120 + step * 5))
    writer.release()
    truth[str(video)] = "MH12AB1234"
    return truth
