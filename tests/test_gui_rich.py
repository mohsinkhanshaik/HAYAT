"""Readable replies, files shown in the chat, and every running operation visible (2026-10-04).

The owner asked for answers that are clear and presentable, images and documents shown and openable in the
chat, the working shown while HAYAT uses the computer, and nothing in the background hidden.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.gui_rich import (WorkingView, activity_line, clean_math, document_preview, find_documents, find_images,
                          markdown_segments)
from tests.conftest import close_window, open_window


def test_maths_markup_reads_as_plain_symbols():
    reply = r"Marked price: $\$80 \times 1.5 = \$120$ and $\frac{1}{2}$ of x^{2} is $x^{2}/2$"
    assert clean_math(reply) == "Marked price: $80 × 1.5 = $120 and (1)/(2) of x^{2} is x²/2"


def test_paths_and_money_are_not_mistaken_for_maths():
    assert clean_math(r"Saved to D:\tools\text\file.txt") == r"Saved to D:\tools\text\file.txt"
    assert clean_math("Costs $5 and $10 each.") == "Costs $5 and $10 each."


def test_markdown_becomes_styled_pieces_without_its_symbols():
    pieces = markdown_segments("## Plan\n- **Step one** uses `pip`\n2. Then this\n```\ncode\n```")
    text = "".join(piece.text for piece in pieces)
    assert "**" not in text and "##" not in text and "```" not in text and "`" not in text
    tags = {tag for piece in pieces for tag in piece.tags}
    assert {"md_heading", "md_bullet", "md_bold", "md_code", "md_codeblock"} <= tags
    assert "• " in text and "2. " in text


def test_web_source_labels_keep_clickable_http_links_without_active_markup():
    segments = markdown_segments('Source: [Official docs](https://example.com/docs) [bad](javascript:alert)')
    links = [segment for segment in segments if segment.href]
    assert len(links) == 1
    assert links[0].text == 'Official docs' and links[0].href == 'https://example.com/docs'


def test_every_named_image_and_document_is_found_once(tmp_path):
    first, second = tmp_path / "a.png", tmp_path / "b photo.jpg"
    notes = tmp_path / "notes.txt"
    for path in (first, second, notes):
        path.write_bytes(b"x")
    text = f'See {first}, "{second}" and {notes}. Also {first} again and {tmp_path / "missing.png"}.'
    assert find_images(text) == [first, second]
    assert find_documents(text) == [notes]


def test_document_cards_preview_text_pdf_and_word(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.filesystem import FilesystemTools

    notes = tmp_path / "notes.txt"
    notes.write_text("Monthly notes\nDay 1: sold 3 logos\n", encoding="utf-8")
    assert document_preview(notes)["lines"][:2] == ["Monthly notes", "Day 1: sold 3 logos"]
    files = FilesystemTools(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    pdf = document_preview(__import__("pathlib").Path(files.write_pdf("plan.pdf", "# Business plan\n\nLogo studio.")["path"]))
    assert pdf["kind"] == "PDF" and pdf["pages"] == 1 and any("Business plan" in line for line in pdf["lines"])
    assert pdf["thumbnail"] is not None  # the top of page one, shown in the chat
    import docx

    document = docx.Document()
    document.add_paragraph("Quarterly report")
    word = tmp_path / "report.docx"
    document.save(word)
    assert document_preview(word)["lines"] == ["Quarterly report"]


def test_bundle_card_lists_members_without_extracting_them(tmp_path):
    import zipfile

    bundle = tmp_path / 'delivery.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('README.md', '# A real bundle')
        archive.writestr('tool.py', 'print("inspect before running")')
    assert find_documents(str(bundle)) == [bundle]
    preview = document_preview(bundle)
    assert preview['kind'] == 'ZIP'
    assert preview['lines'][0] == '2 files in bundle'
    assert any('README.md' in line for line in preview['lines'])
    assert not (tmp_path / 'README.md').exists()
    assert not (tmp_path / 'tool.py').exists()


def test_running_operations_read_in_plain_words():
    assert activity_line({"kind": "tool", "label": "browser_navigate", "detail": "example.com", "seconds": 4}) == \
        "⚙ Opening a web page: example.com  4s"
    assert activity_line({"kind": "learning", "label": "deep learning", "detail": "How do vaccines work?",
                          "seconds": 130}).startswith("📚 Background learning: How do vaccines work?")
    assert "Thinking (hayat:latest)" in activity_line({"kind": "model", "detail": "ollama/hayat:latest", "seconds": 1})


def test_the_working_view_ticks_steps_and_reports_finished_computer_actions(shared_tk):
    window = open_window(shared_tk, hidden=True)
    palette = SimpleNamespace(PANEL_2="#111", LINE="#333", GOLD="#fc0", WHITE="#fff", MUTED="#888")
    try:
        view = WorkingView(window, palette)
        view.begin()
        click = {"activity_id": "a", "kind": "tool", "label": "computer_click", "detail": "Save", "seconds": 1}
        think = {"activity_id": "b", "kind": "model", "label": "thinking", "detail": "ollama/hayat", "seconds": 1}
        assert view.update_from([click, think]) == []
        assert view.update_from([think]) == ["computer_click"]
        shown = view.steps.get("1.0", "end")
        assert "✓ ⚙ Clicking: Save" in shown and "… ✦ Thinking" in shown
        view.end()
        assert not view.winfo_manager()
    finally:
        close_window(window)
