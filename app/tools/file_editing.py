"""Conflict-checked text editing and native spreadsheet/presentation creation."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

_LOCK = threading.RLock()


def atomic_save(path: Path, data: bytes, expected_sha256: str | None = None, backup: bool = True) -> dict:
    """Replace only a complete file; keep the previous bytes for recovery."""
    with _LOCK:
        previous = path.read_bytes() if path.is_file() else None
        digest = hashlib.sha256(previous).hexdigest() if previous is not None else None
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError('File changed since it was read. Reload before saving; no changes made.')
        path.parent.mkdir(parents=True, exist_ok=True)
        saved_backup = None
        if backup and previous is not None:
            saved_backup = path.with_name(path.name + '.hayat-' + uuid4().hex + '.bak')
            # Copy metadata as well as content before replacing the original.
            shutil.copy2(path, saved_backup)
        fd, temporary = tempfile.mkstemp(prefix='.hayat-save-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                shutil.copymode(path, temporary)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'backup': str(saved_backup) if saved_backup else None, 'exists': path.is_file()}


class FileEditing:
    MAX_BYTES = 5 * 1024 * 1024
    BINARY_FORMATS = {'.pdf', '.docx', '.xlsx', '.pptx', '.zip', '.png', '.jpg', '.jpeg', '.gif', '.mp4', '.mp3', '.wav', '.exe', '.dll', '.db'}

    def __init__(self, filesystem):
        self.filesystem = filesystem

    @staticmethod
    def _validate_cell_value(value):
        import math
        if isinstance(value, str) and len(value) > 32767:
            raise ValueError('Excel text cells are limited to 32767 characters; no truncation was saved.')
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError('Excel numeric cells require finite values.')

    def read_editable_file(self, path: str) -> dict:
        target = self.filesystem._path(path)
        if target.suffix.lower() in self.BINARY_FORMATS or target.stat().st_size > self.MAX_BYTES:
            raise ValueError('Use a format-specific tool for binary documents, or an external editor for files over 5 MiB.')
        with target.open('rb') as handle:
            data = handle.read(self.MAX_BYTES + 1)
        if len(data) > self.MAX_BYTES:
            raise ValueError('File exceeds the 5 MiB editor limit.')
        if b'\x00' in data:
            raise ValueError('Binary files cannot be edited as UTF-8 text.')
        # Preserve a UTF-8 BOM and exact newline bytes through an edit.
        encoding = 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'utf-8'
        return {'path': str(target), 'content': data.decode(encoding), 'encoding': encoding,
                'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}

    def save_edited_file(self, path: str, content: str, expected_sha256: str) -> dict:
        with _LOCK:
            current = self.read_editable_file(path)
            if current['sha256'] != expected_sha256:
                raise ValueError('File changed since it was read. Reload before saving; no changes made.')
            target = self.filesystem._path(path, write=True)
            data = content.encode(current['encoding'])
            if len(data) > self.MAX_BYTES:
                raise ValueError('Editor saves are limited to 5 MiB.')
            self.filesystem._ensure_capacity(len(data))  # backup plus the replacement
            return atomic_save(target, data, expected_sha256)

    def edit_file(self, path: str, old_text: str, new_text: str, expected_sha256: str,
                  expected_matches: int = 1) -> dict:
        if not old_text or expected_matches < 1:
            raise ValueError('Specify non-empty old text and a positive match count.')
        with _LOCK:
            current = self.read_editable_file(path)
            if current['content'].count(old_text) != expected_matches:
                raise ValueError('Match count differs from expected_matches; no changes made.')
            return self.save_edited_file(path, current['content'].replace(old_text, new_text), expected_sha256)

    def write_spreadsheet(self, path: str, rows: list[list], sheet_name: str = 'Sheet1') -> dict:
        import io
        import openpyxl

        if not rows or len(rows) > 10000 or any(len(row) > 200 for row in rows):
            raise ValueError('Provide 1 to 10000 rows with at most 200 columns each.')
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = sheet_name
        for row in rows:
            for value in row:
                self._validate_cell_value(value)
            sheet.append(row)
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        output = io.BytesIO()
        book.save(output)
        return self._native_save(path, '.xlsx', output.getvalue())

    def write_presentation(self, path: str, slides: list[dict]) -> dict:
        import io
        from pptx import Presentation

        if not 1 <= len(slides) <= 100:
            raise ValueError('Provide 1 to 100 slides.')
        deck = Presentation()
        for item in slides:
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = item['title']
            slide.placeholders[1].text = item['body']
        output = io.BytesIO()
        deck.save(output)
        return self._native_save(path, '.pptx', output.getvalue())

    def _native_save(self, path: str, suffix: str, data: bytes) -> dict:
        target = self.filesystem._path(self.filesystem._stored_path(path, suffix), write=True)
        self.filesystem._ensure_capacity(len(data))
        return atomic_save(target, data)

    def _document(self, path: str):
        import io
        target = self.filesystem._path(path)
        if target.stat().st_size > 50 * 1024 * 1024:
            raise ValueError('Native document edits are limited to 50 MiB.')
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if target.suffix.lower() == '.docx':
            from docx import Document
            document = Document(io.BytesIO(data))
            paragraphs = list(document.paragraphs)
            for table in document.tables:
                seen = set()
                for row in table.rows:
                    for cell in row.cells:
                        if cell._tc not in seen:
                            seen.add(cell._tc)
                            paragraphs.extend(cell.paragraphs)
        elif target.suffix.lower() == '.pptx':
            from pptx import Presentation
            document = Presentation(io.BytesIO(data))
            paragraphs = []
            for slide in document.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        paragraphs.extend(shape.text_frame.paragraphs)
        else:
            raise ValueError('Text replacement supports DOCX main-body paragraphs/tables and PPTX text boxes. Use spreadsheet cell editing for XLSX.')
        return target, document, paragraphs, digest

    def read_document_for_edit(self, path: str) -> dict:
        target, _, paragraphs, digest = self._document(path)
        return {'path': str(target), 'sha256': digest,
                'paragraphs': [p.text[:2000] for p in paragraphs[:200]],
                'truncated': len(paragraphs) > 200 or any(len(p.text) > 2000 for p in paragraphs[:200])}

    def edit_document_text(self, path: str, old_text: str, new_text: str,
                           expected_sha256: str, expected_matches: int = 1) -> dict:
        import io
        if not old_text or expected_matches < 1:
            raise ValueError('Specify non-empty old text and a positive match count.')
        with _LOCK:
            target, document, paragraphs, digest = self._document(path)
            self.filesystem._path(path, write=True)
            if digest != expected_sha256:
                raise ValueError('File changed since it was read; no changes made.')
            if sum(p.text.count(old_text) for p in paragraphs) != expected_matches:
                raise ValueError('Match count differs from expected_matches; no changes made.')
            if any(old_text in p.text and ''.join(run.text for run in p.runs) != p.text for p in paragraphs):
                raise ValueError('The matching paragraph contains unsupported hyperlink/field runs; use a native editor.')
            for paragraph in paragraphs:
                # Replace right-to-left across runs, preserving unrelated formatting.
                starts = []
                position = paragraph.text.find(old_text)
                while position >= 0:
                    starts.append(position)
                    position = paragraph.text.find(old_text, position + len(old_text))
                for start in reversed(starts):
                    end, offset = start + len(old_text), 0
                    for run in paragraph.runs:
                        text = run.text
                        stop = offset + len(text)
                        if offset < end and stop > start:
                            prefix = text[:max(0, start - offset)]
                            suffix = text[max(0, end - offset):]
                            run.text = prefix + (new_text if offset <= start < stop else '') + suffix
                        offset = stop
            output = io.BytesIO()
            document.save(output)
            self.filesystem._ensure_capacity(len(output.getvalue()))
            return atomic_save(target, output.getvalue(), expected_sha256)

    def read_spreadsheet_for_edit(self, path: str, sheet_name: str | None = None, cell_range: str = 'A1:F30') -> dict:
        import io
        import openpyxl
        from openpyxl.utils.cell import range_boundaries

        target = self.filesystem._path(path)
        if target.suffix.lower() != '.xlsx' or target.stat().st_size > 50 * 1024 * 1024:
            raise ValueError('Use an XLSX workbook of at most 50 MiB.')
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
        if (None in (min_col, min_row, max_col, max_row) or min_col < 1 or min_row < 1
                or max_col < min_col or max_row < min_row or max_col > 16384 or max_row > 1048576
                or (max_col - min_col + 1) * (max_row - min_row + 1) > 2000):
            raise ValueError('Choose a valid cell range containing at most 2000 cells.')
        data = target.read_bytes()
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
        try:
            sheet = book[sheet_name] if sheet_name else book.worksheets[0]
            cells = [{'cell': cell.coordinate, 'value': cell.value, 'type': cell.data_type}
                     for row in sheet.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col)
                     for cell in row if cell.value is not None]
            return {'path': str(target), 'sha256': hashlib.sha256(data).hexdigest(),
                    'sheets': book.sheetnames, 'sheet': sheet.title, 'range': cell_range, 'cells': cells}
        finally:
            book.close()

    def edit_spreadsheet_cells(self, path: str, cells: list[dict], expected_sha256: str) -> dict:
        import io
        import openpyxl
        target = self.filesystem._path(path, write=True)
        if target.suffix.lower() != '.xlsx' or target.stat().st_size > 50 * 1024 * 1024:
            raise ValueError('Use an XLSX workbook of at most 50 MiB.')
        if not 1 <= len(cells) <= 1000:
            raise ValueError('Provide 1 to 1000 cell changes.')
        with _LOCK:
            data = target.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected_sha256:
                raise ValueError('File changed since it was read; no changes made.')
            book = openpyxl.load_workbook(io.BytesIO(data))
            try:
                for item in cells:
                    self._validate_cell_value(item['value'])
                    coordinate = item['cell']
                    from openpyxl.utils.cell import coordinate_to_tuple
                    row, column = coordinate_to_tuple(coordinate)
                    if not 1 <= row <= 1048576 or not 1 <= column <= 16384:
                        raise ValueError('Cell is outside Excel limits.')
                    cell = book[item['sheet']][coordinate]
                    cell.value = item['value']
                    if item.get('value_type') == 'text':
                        if not isinstance(item['value'], str):
                            raise ValueError('A text cell value must be a string.')
                        cell.data_type = 's'
                output = io.BytesIO()
                book.save(output)
            finally:
                book.close()
            self.filesystem._ensure_capacity(len(output.getvalue()))
            return atomic_save(target, output.getvalue(), expected_sha256)


def register_editing_tools(registry, filesystem):
    editor = FileEditing(filesystem)
    def schema(properties, required):
        return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
    text = {'type': 'string'}
    digest = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
    scalar = {'type': ['string', 'number', 'boolean', 'null']}
    for name, description, properties, required, handler, permission in (
        ('read_editable_file', 'Read a UTF-8 text/code/config file with its SHA256 for a later conflict-checked edit.', {'path': text}, ['path'], editor.read_editable_file, 'filesystem_read'),
        ('save_edited_file', 'Save complete edited UTF-8 text only if the file SHA256 still matches. Keeps a recoverable backup.', {'path': text, 'content': text, 'expected_sha256': digest}, ['path', 'content', 'expected_sha256'], editor.save_edited_file, 'filesystem_write_project'),
        ('edit_file', 'Replace exact text in a UTF-8 file with expected match count and SHA256, preserving a backup. Read with read_editable_file first.', {'path': text, 'old_text': text, 'new_text': text, 'expected_sha256': digest, 'expected_matches': {'type': 'integer', 'minimum': 1}}, ['path', 'old_text', 'new_text', 'expected_sha256'], editor.edit_file, 'filesystem_write_project'),
        ('write_spreadsheet', 'Create a real Excel XLSX workbook from rows of cells; numbers and formulas are supported.', {'path': text, 'sheet_name': text, 'rows': {'type': 'array', 'items': {'type': 'array', 'items': {'type': ['string', 'number', 'boolean', 'null']}}}}, ['path', 'rows'], editor.write_spreadsheet, 'filesystem_write_project'),
        ('write_presentation', 'Create a real PowerPoint PPTX from slides with title and body text.', {'path': text, 'slides': {'type': 'array', 'items': schema({'title': text, 'body': text}, ['title', 'body'])}}, ['path', 'slides'], editor.write_presentation, 'filesystem_write_project'),
        ('read_document_for_edit', 'Read DOCX main-body paragraphs/tables or PPTX text boxes and SHA256 before editing.', {'path': text}, ['path'], editor.read_document_for_edit, 'filesystem_read'),
        ('read_spreadsheet_for_edit', 'Read a bounded XLSX cell range, formulas, sheet names and SHA256 before native editing. At most 2000 cells; defaults to A1:F30.', {'path': text, 'sheet_name': {'type': ['string', 'null']}, 'cell_range': text}, ['path'], editor.read_spreadsheet_for_edit, 'filesystem_read'),
        ('edit_document_text', 'Replace exact text in DOCX main-body paragraphs/tables or PPTX text boxes while preserving other runs and creating a backup. Requires the SHA256 from read_document_for_edit.', {'path': text, 'old_text': text, 'new_text': text, 'expected_sha256': digest, 'expected_matches': {'type': 'integer', 'minimum': 1}}, ['path', 'old_text', 'new_text', 'expected_sha256'], editor.edit_document_text, 'filesystem_write_project'),
        ('edit_spreadsheet_cells', 'Change selected XLSX cells while retaining other sheets and formulas. Requires SHA256 from get_file_info or read_spreadsheet_for_edit. Optional value_type=text stores strings literally, including a leading equals sign.', {'path': text, 'expected_sha256': digest, 'cells': {'type': 'array', 'items': schema({'sheet': text, 'cell': text, 'value': scalar, 'value_type': {'type': 'string', 'enum': ['text']}}, ['sheet', 'cell', 'value'])}}, ['path', 'cells', 'expected_sha256'], editor.edit_spreadsheet_cells, 'filesystem_write_project'),
    ):
        registry.register(name, description, schema(properties, required), 'low' if permission == 'filesystem_read' else 'medium', permission, handler)
    return editor
