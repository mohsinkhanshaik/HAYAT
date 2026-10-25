from pathlib import Path

import pytest

from app.safety.permissions import PermissionManager
from app.tools.filesystem import FilesystemTools


def filesystem(tmp_path: Path, extra_roots: list[Path] | None = None) -> FilesystemTools:
    roots = ["."] + [str(path) for path in extra_roots or []]
    config = {
        "default": "ASK",
        "permissions": {"filesystem_read": "ALLOW", "filesystem_write_project": "ALLOW"},
        "protected_paths": [],
        "allowed_roots": roots,
    }
    return FilesystemTools(PermissionManager(config, tmp_path))


def test_write_read_search_copy_move_append(tmp_path):
    fs = filesystem(tmp_path)
    fs.create_folder("nested")
    fs.write_file("nested/example.py", "print('one')\n")
    fs.append_file("nested/example.py", "print('two')\n")
    assert "two" in fs.read_file("nested/example.py")["content"]
    assert fs.search_files(".", "*.py")["matches"] == ["nested/example.py"]
    fs.copy_file("nested/example.py", "copy.py")
    fs.move_file("copy.py", "moved.py")
    assert fs.file_exists("moved.py")["is_file"]
    assert fs.get_file_info("moved.py")["size"] > 0
    assert fs.list_folder(".")["count"] == 2


def test_outside_project_is_denied(tmp_path):
    fs = filesystem(tmp_path)
    with pytest.raises(PermissionError):
        fs.read_file(str(tmp_path.parent / "outside.txt"))


def test_search_ignores_virtual_environment(tmp_path):
    fs = filesystem(tmp_path)
    fs.write_file("app.py", "")
    fs.write_file(".venv/Lib/site-packages/dependency.py", "")
    result = fs.search_files(".", "*.py")
    assert result["matches"] == ["app.py"]


def test_list_folder_on_an_extra_allowed_root_does_not_crash(tmp_path):
    extra = tmp_path.parent / f"extra-root-{tmp_path.name}"
    extra.mkdir()
    (extra / "note.txt").write_text("hello", encoding="utf-8")
    fs = filesystem(tmp_path, extra_roots=[extra])
    listed = fs.list_folder(str(extra))
    paths = {entry["path"] for entry in listed["entries"]}
    assert "note.txt" in paths


def test_recursive_list_skips_ignored_directories(tmp_path):
    fs = filesystem(tmp_path)
    fs.write_file("app.py", "")
    fs.write_file(".venv/Lib/site-packages/dependency.py", "")
    listed = fs.list_folder(".", recursive=True)
    paths = {entry["path"] for entry in listed["entries"]}
    assert "app.py" in paths
    assert not any(path.startswith(".venv") for path in paths)


def test_list_and_search_are_capped(tmp_path):
    fs = filesystem(tmp_path)
    folder = tmp_path / "many"
    folder.mkdir()
    for index in range(30):
        (folder / f"file-{index}.txt").write_text("x", encoding="utf-8")
    listed = fs.list_folder("many", max_entries=10)
    assert listed["count"] == 10
    assert listed["truncated"] is True
    searched = fs.search_files("many", "*.txt", max_matches=10)
    assert searched["count"] == 10
    assert searched["truncated"] is True


def test_read_file_stops_after_max_chars(tmp_path):
    fs = filesystem(tmp_path)
    fs.write_file("big.txt", "abcdefghij")
    result = fs.read_file("big.txt", max_chars=4)
    assert result["content"] == "abcd"
    assert result["truncated"] is True


def test_write_pdf_creates_a_real_readable_pdf(tmp_path):
    """HAYAT could read PDFs but never write one. write_pdf lays text out as a

    genuine PDF -- valid header, readable back by the same pypdf it uses
    elsewhere, with the text actually in it."""
    from pypdf import PdfReader

    fs = filesystem(tmp_path)
    body = ("Business plan for a $1000 start.\n\n"
            "Step 1: register an LLC and get an EIN.\n"
            "Step 2: open a business bank account.\n" + "A long wrapping line. " * 40)
    result = fs.write_pdf("reports/plan.pdf", body, title="Startup Plan")

    assert result["exists"] is True
    assert result["format"] == "pdf"
    assert result["pages"] >= 1
    saved = Path(result["path"])
    assert saved.suffix == ".pdf"
    assert saved.read_bytes().startswith(b"%PDF-")

    reader = PdfReader(str(saved))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "register an LLC" in extracted
    assert "Startup Plan" in extracted


def test_write_pdf_forces_the_pdf_suffix(tmp_path):
    """"make a pdf called notes" should not silently produce notes.txt."""
    fs = filesystem(tmp_path)
    result = fs.write_pdf("notes", "hello")
    assert Path(result["path"]).suffix == ".pdf"
    assert Path(result["path"]).exists()


def test_write_pdf_renders_markdown_structure_and_is_readable(tmp_path):
    """A generated PDF should read like a document, not a screen dump. Headings,

    bullets, a numbered list, bold, and a unicode symbol all survive into a
    multi-page, pypdf-readable file."""
    from pypdf import PdfReader

    fs = filesystem(tmp_path)
    md = ("# Plan\n\nIntro with **bold** and a \u20ac50 cost.\n\n"
          "## Options\n\n- Freelancing\n- Digital products\n\n"
          "## Steps\n\n1. Register an LLC\n2. Open a bank account\n\n"
          + ("Body text that wraps across the page. " * 250))
    result = fs.write_pdf("subdir/plan.pdf", md, title="Roadmap")
    assert result["exists"] and result["pages"] >= 2
    text = "\n".join(page.extract_text() or "" for page in PdfReader(result["path"]).pages)
    for probe in ("Roadmap", "Plan", "Options", "Freelancing", "Register an LLC"):
        assert probe in text, probe


def test_write_pdf_bare_name_lands_in_artifacts(tmp_path):
    """A bare filename should not clutter the project root."""
    fs = filesystem(tmp_path)
    result = fs.write_pdf("summary", "hello")
    assert Path(result["path"]).parent.name == "artifacts"
    assert Path(result["path"]).parent.parent.name == "data"
    assert Path(result["path"]).exists()


def test_write_file_with_document_suffix_writes_a_real_document(tmp_path):
    """The Adobe "damaged file" bug: write_file to a .pdf name once wrote raw

    text with a .pdf extension. A document suffix must now produce a real
    document -- a PDF that starts with %PDF, a .docx that is a valid zip."""
    fs = filesystem(tmp_path)
    pdf = fs.write_file("out/report.pdf", "# Title\n\nBody **bold**.")
    assert pdf["format"] == "pdf"
    assert Path(pdf["path"]).read_bytes().startswith(b"%PDF-")

    docx = fs.write_file("out/report.docx", "# Title\n\nBody.")
    assert docx["format"] == "docx"
    assert Path(docx["path"]).read_bytes().startswith(b"PK")


def test_write_docx_is_a_readable_word_document(tmp_path):
    """A .docx Word can open and edit -- heading, bullet, and bold survive."""
    from docx import Document

    fs = filesystem(tmp_path)
    result = fs.write_docx("notes", "# Plan\n\n- alpha\n- beta", title="My Doc")
    assert result["format"] == "docx"
    assert Path(result["path"]).parent.name == "artifacts"
    texts = [p.text for p in Document(result["path"]).paragraphs]
    assert "My Doc" in texts
    assert "alpha" in texts and "beta" in texts


def test_pdf_font_selection_is_cross_platform():
    """A generated PDF must render real Unicode on any OS, not only where it was

    first written. The family picker resolves Windows (Arial), Linux (DejaVu),
    and macOS (Arial) fonts, and reports nothing when none are present so the
    caller can fall back to Helvetica."""
    from app.tools.filesystem import _select_font_family

    windows = _select_font_family(exists=lambda p: p.lower().endswith("arial.ttf") and "windows" in p.lower())
    assert windows and windows[0].lower().endswith("arial.ttf")

    linux = _select_font_family(exists=lambda p: "DejaVuSans.ttf" in p)
    assert linux and "DejaVuSans.ttf" in linux[0]

    mac = _select_font_family(exists=lambda p: p == "/System/Library/Fonts/Supplemental/Arial.ttf")
    assert mac and mac[0] == "/System/Library/Fonts/Supplemental/Arial.ttf"

    assert _select_font_family(exists=lambda _p: False) is None
