"""Native PDF failures stay in their process; multiple previews retain their pages."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.pdf_preview_worker import render_pdf


def test_concurrent_pdf_previews_keep_distinct_pages_and_bounded_pixels(tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / 'two pages.pdf'
    document = canvas.Canvas(str(path), pagesize=(2000, 4000))
    for text in ('First customer page', 'Second customer page'):
        document.drawString(30, 3970, text)
        document.showPage()
    document.save()
    with ThreadPoolExecutor(max_workers=2) as pool:
        full = pool.submit(render_pdf, path, page=1)
        card = pool.submit(render_pdf, path, thumbnail=True)
        full, card = full.result(timeout=20), card.result(timeout=20)
    assert full['pages'] == card['pages'] == 2
    assert full['lines'] == ['Second customer page']
    assert card['lines'] == ['First customer page']
    for result in (full, card):
        assert result['image'].mode == 'RGB'
        assert result['image'].width <= 700 and result['image'].height <= 1800


def test_unreadable_pdf_reports_an_error_without_crashing_the_parent(tmp_path):
    path = tmp_path / 'broken.pdf'
    path.write_bytes(b'Not a PDF')
    with pytest.raises(ValueError):
        render_pdf(path)


def test_pdf_child_crash_and_timeout_are_reported(tmp_path, monkeypatch):
    import subprocess

    for error in (subprocess.CalledProcessError(1, 'pdf-worker'), subprocess.TimeoutExpired('pdf-worker', 15)):
        def fail(*args, **kwargs):
            raise error
        monkeypatch.setattr('app.pdf_preview_worker.subprocess.run', fail)
        with pytest.raises(type(error)):
            render_pdf(tmp_path / 'example.pdf')
