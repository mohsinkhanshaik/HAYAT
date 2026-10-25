"""Files handed over by hand, and still known about next week."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.attachments import STORED_CHARACTERS, AttachmentReader, _entities, _numbers


@pytest.fixture
def invoice(tmp_path):
    path = tmp_path / "acme-invoice-2026.txt"
    path.write_text(
        "ACME Industrial Supplies\nInvoice INV-88421\nDate: 2026-08-14\n"
        "Bill to: Mohsin Khan Shaik\n\nHydraulic pump assembly $12,450.00\n"
        "TOTAL DUE $17,351.25\nPayment terms: net 30 days.\n", encoding="utf-8")
    return path


class Memory:
    def __init__(self) -> None:
        self.stored: list[dict] = []

    def store_memory(self, kind, content, importance=0.5, source="", metadata=None,
                     session_id=None):
        self.stored.append({"memory_type": kind, "content": content,
                            "metadata": metadata or {}, "importance": importance})
        return len(self.stored)

    def retrieve(self, query, limit=8, session_id=None):
        wanted = {word for word in query.lower().split() if len(word) > 3}
        found = []
        for index, item in enumerate(self.stored, 1):
            body = item["content"].lower()
            if any(word in body for word in wanted):
                found.append(dict(item) | {"id": index})
        return found[:limit]


# ------------------------------------------------------------------ reading


def test_a_text_file_is_read_and_understood(invoice):
    attachment = AttachmentReader().read(invoice)
    assert attachment.ok and attachment.kind == "text"
    assert "17,351.25" in attachment.text
    assert attachment.sha256 and attachment.bytes_read == invoice.stat().st_size


def test_image_reading_combines_visible_text_and_description_without_face_identification(tmp_path, monkeypatch):
    from PIL import Image
    from app.perception.camera.ocr import LocalOCR
    target = tmp_path / "photo.png"
    Image.new("RGB", (32, 32), "white").save(target)
    monkeypatch.setattr(LocalOCR, "read", lambda *_: {"text": "Museum entrance"})
    prompts = []
    def analyze(path, prompt):
        prompts.append(prompt)
        assert path == str(target)
        return {"interpretation": "A person standing beside a sign."}
    reader = AttachmentReader(SimpleNamespace(vision=SimpleNamespace(analyze=analyze), model=None))
    image = reader.read(target)
    assert image.ok and "Museum entrance" in image.text and "beside a sign" in image.text
    assert "Do not identify people from their faces" in prompts[0]
    assert "untrusted content" in prompts[0]


def test_a_missing_file_is_reported_rather_than_raised(tmp_path):
    attachment = AttachmentReader().read(tmp_path / "nope.txt")
    assert not attachment.ok
    assert "does not exist" in attachment.error


def test_an_enormous_file_is_refused_with_a_reason(tmp_path):
    """A stray 2GB log should not take the process with it."""
    from app import attachments

    big = tmp_path / "huge.log"
    big.write_text("x" * 2048, encoding="utf-8")
    original = attachments.MAX_READ_BYTES
    attachments.MAX_READ_BYTES = 1024
    try:
        attachment = AttachmentReader().read(big)
    finally:
        attachments.MAX_READ_BYTES = original
    assert not attachment.ok and "too large" in attachment.error


def test_a_file_with_a_bad_byte_still_reads(tmp_path):
    path = tmp_path / "messy.log"
    path.write_bytes(b"good line\n\xff\xfe broken byte\nmore text\n")
    attachment = AttachmentReader().read(path)
    assert attachment.ok and "good line" in attachment.text


def test_dragged_file_is_scanned_before_any_parser_reads_it(tmp_path):
    path = tmp_path / "untrusted.txt"
    path.write_text("content a parser must never receive", encoding="utf-8")

    class Scanner:
        def scan(self, target):
            assert target == path
            return {"clean": False, "status": "blocked", "provider": "test"}

    attachment = AttachmentReader(scanner=Scanner()).read(path)

    assert not attachment.ok
    assert "blocked by antimalware before parsing" in attachment.error
    assert attachment.text == ""
    assert path.read_text(encoding="utf-8").startswith("content")


def test_clean_dragged_file_keeps_scan_evidence(invoice):
    class Scanner:
        def scan(self, _target):
            return {"clean": True, "status": "clean", "provider": "test"}

    attachment = AttachmentReader(scanner=Scanner()).read(invoice)

    assert attachment.ok
    assert attachment.to_dict()["antimalware_scan"]["status"] == "clean"


# -------------------------------------------------------- what gets kept


def test_the_understanding_is_stored_not_the_file(invoice):
    """A 40-page PDF becomes a few hundred characters. That is the trade that
    makes remembering everything possible at all."""
    reader = AttachmentReader(SimpleNamespace(memory=Memory(), model=None, vision=None))
    attachment = reader.ingest(invoice)

    assert attachment.memory_id is not None
    kept = attachment.memory_text()
    assert len(kept) < len(attachment.text) + 800
    assert len(kept) <= STORED_CHARACTERS + 900


def test_what_is_stored_is_searchable_not_just_readable(invoice):
    """A summary written purely as prose is one nothing can find later."""
    reader = AttachmentReader(SimpleNamespace(memory=Memory(), model=None, vision=None))
    kept = reader.ingest(invoice).memory_text()

    assert "acme-invoice-2026.txt" in kept          # the filename
    assert str(invoice) in kept                     # a path back to the original
    assert "17,351.25" in kept                      # the number anyone would ask about
    assert "INV-88421" in kept                      # the reference


def test_the_original_is_never_copied(invoice, tmp_path):
    reader = AttachmentReader(SimpleNamespace(memory=Memory(), model=None, vision=None))
    before = set(tmp_path.iterdir())
    reader.ingest(invoice)
    assert set(tmp_path.iterdir()) == before


def test_a_file_that_could_not_be_read_is_not_remembered(tmp_path):
    memory = Memory()
    reader = AttachmentReader(SimpleNamespace(memory=memory, model=None, vision=None))
    reader.ingest(tmp_path / "absent.txt")
    assert memory.stored == []


# ----------------------------------------------------------------- recalling


def test_mentioning_it_later_finds_it_without_handing_it_over_again(invoice):
    """Storing the understanding and never retrieving it is the same as not
    storing it."""
    reader = AttachmentReader(SimpleNamespace(memory=Memory(), model=None, vision=None))
    reader.ingest(invoice)

    found = reader.recall("how much do I owe acme in total")
    assert found, "the invoice was stored but could not be found again"
    assert "17,351.25" in found[0]["content"]
    assert found[0]["metadata"]["kind"] == "attachment"


def test_an_unrelated_question_does_not_drag_files_in(invoice):
    reader = AttachmentReader(SimpleNamespace(memory=Memory(), model=None, vision=None))
    reader.ingest(invoice)
    assert reader.recall("what is the capital of Peru") == []


def test_recall_survives_a_runtime_with_no_memory():
    assert AttachmentReader(SimpleNamespace()).recall("anything") == []


# ------------------------------------------------------- picking out details


def test_the_numbers_anyone_would_ask_about_are_pulled_out():
    found = _numbers("Invoice INV-88421 dated 2026-08-14 for $17,351.25 with VAT at 5%")
    assert "$17,351.25" in found and "2026-08-14" in found
    assert "INV-88421" in found and "5%" in found


def test_names_are_pulled_out_for_searching_on_later():
    found = _entities("ACME Industrial Supplies billed Mohsin Khan for the pump")
    assert any("ACME" in item for item in found)


def test_the_reader_is_wired_into_the_runtime():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False)
    try:
        assert runtime.attachments is not None
        assert runtime.conversation.attachments is runtime.attachments
    finally:
        runtime.close()
