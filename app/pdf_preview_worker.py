"""Render one bounded PDF page in a child process, outside Tk and its threads."""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from pathlib import Path


def render_pdf(path: Path, *, page: int = 0, thumbnail: bool = False, lines: int = 6):
    executable = Path(sys.executable)
    if executable.name.lower() == 'pythonw.exe':
        executable = executable.with_name('python.exe')
    request = {'path': str(path.resolve()), 'page': page, 'thumbnail': thumbnail, 'lines': lines}
    completed = subprocess.run(
        [str(executable), '-m', 'app.pdf_preview_worker'],
        input=json.dumps(request).encode('utf-8'), capture_output=True, timeout=15,
        cwd=Path(__file__).resolve().parents[1],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True,
    )
    result = json.loads(completed.stdout)
    if result.get('error'):
        raise ValueError(result['error'])
    pixels = result.pop('png', None)
    result['image'] = None
    if pixels:
        from PIL import Image

        with Image.open(io.BytesIO(base64.b64decode(pixels, validate=True))) as image:
            result['image'] = image.convert('RGB')
    return result


def _render(request):
    path = Path(request['path'])
    page_number = int(request.get('page', 0))
    line_limit = max(0, min(50, int(request.get('lines', 6))))
    try:
        import pymupdf
    except ImportError:
        if not request.get('thumbnail'):
            raise
        from pypdf import PdfReader

        document = PdfReader(path)
        text = document.pages[page_number].extract_text() or ''
        return {'pages': len(document.pages), 'lines': [line.strip()[:160] for line in text.splitlines() if line.strip()][:line_limit]}

    with pymupdf.open(path) as document:
        page = document.load_page(page_number)
        rect = page.rect
        if request.get('thumbnail'):
            rect = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * .4)
        scale = min(700 / max(1, rect.width), 1800 / max(1, rect.height), .6 if request.get('thumbnail') else 1.5)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect,
                                 alpha=False, colorspace=pymupdf.csRGB)
        return {
            'pages': document.page_count,
            'lines': [line.strip()[:160] for line in page.get_text().splitlines() if line.strip()][:line_limit],
            'png': base64.b64encode(pixels.tobytes('png')).decode('ascii'),
        }


def main():
    try:
        request = json.loads(sys.stdin.buffer.read(8192))
        result = _render(request)
    except Exception as exc:
        result = {'error': f'{type(exc).__name__}: {exc}'}
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    main()
