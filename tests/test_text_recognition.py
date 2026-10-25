"""The OCR tool reads a local image's text and guards its input; it never looks anyone up."""
from __future__ import annotations

import app.perception.camera.ocr as ocr_module
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from app.tools.text_recognition import register_text_recognition_tools


class _FakeOCR:
    def read(self, path):
        return {"available": True, "text": "INVOICE 2026-10", "lines": ["INVOICE 2026-10"], "provider": "fake"}

    def status(self):
        return {"available": True, "active_backend": "fake", "provider": "fake"}


def _registry(tmp_path):
    reg = ToolRegistry(PermissionManager({"default": "DENY", "permissions": {"filesystem_read": "ALLOW", "system_read": "ALLOW"}}, tmp_path))
    register_text_recognition_tools(reg)
    return reg


def test_reads_the_text_of_a_real_image_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_module, "LocalOCR", _FakeOCR)
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG\r\n")  # contents never parsed by the fake
    result = _registry(tmp_path).execute("read_text_from_image", {"path": str(image)})
    assert result.success
    assert result.output["text"] == "INVOICE 2026-10"
    assert result.output["path"] == str(image)


def test_missing_file_is_a_clean_reason_not_a_crash(tmp_path):
    result = _registry(tmp_path).execute("read_text_from_image", {"path": str(tmp_path / "nope.png")})
    assert result.success and result.output["available"] is False
    assert "No file" in result.output["reason"]


def test_a_non_image_file_is_refused_before_ocr(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_module, "LocalOCR", _FakeOCR)
    doc = tmp_path / "notes.txt"
    doc.write_text("hello")
    result = _registry(tmp_path).execute("read_text_from_image", {"path": str(doc)})
    assert result.success and result.output["available"] is False
    assert "not an image" in result.output["reason"]


def test_status_reports_the_backend_without_reading_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_module, "LocalOCR", _FakeOCR)
    result = _registry(tmp_path).execute("ocr_status", {})
    assert result.success and result.output["available"] is True
