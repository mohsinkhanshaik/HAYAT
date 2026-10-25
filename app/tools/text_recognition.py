"""Tool: read the text in an image or screenshot (OCR), entirely on this machine.

A thin tool over app/perception/camera/ocr.LocalOCR, which prefers the OCR
engine built into Windows and falls back to a local Tesseract when installed.
It extracts the words from a picture the owner points it at -- a scanned page,
a screenshot, a photo of a document. It reads text; it does not identify or
look up any person, and it never calls a network service.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.registry import ToolRegistry

#: Picture formats the Windows OCR engine and Tesseract both accept.
_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp"}


def register_text_recognition_tools(registry: ToolRegistry) -> None:
    def read_text_from_image(path: str) -> dict[str, Any]:
        target = Path(path).expanduser()
        if not target.is_file():
            return {"available": False, "text": "", "reason": f"No file at {target}.", "path": str(target)}
        if target.suffix.lower() not in _SUFFIXES:
            return {"available": False, "text": "",
                    "reason": f"{target.suffix or 'That file'} is not an image. Supported: "
                              + ", ".join(sorted(_SUFFIXES)) + ".",
                    "path": str(target)}
        from app.perception.camera.ocr import LocalOCR

        result = LocalOCR().read(target)
        result.setdefault("path", str(target))
        return result

    registry.register(
        "read_text_from_image",
        "Read the text in a local image or screenshot using on-device OCR (the Windows OCR engine, or a local "
        "Tesseract when installed). Returns the extracted text and the lines. Local-only, no network call. For a "
        "scanned page, screenshot or photo of a document -- not for identifying or looking up a person.",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "low",
        "filesystem_read",
        read_text_from_image,
    )

    def ocr_status() -> dict[str, Any]:
        from app.perception.camera.ocr import LocalOCR

        return LocalOCR().status()

    registry.register(
        "ocr_status",
        "Report which on-device OCR backend is available (the built-in Windows OCR engine, or a local Tesseract) "
        "without reading any file.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "low",
        "system_read",
        ocr_status,
    )
