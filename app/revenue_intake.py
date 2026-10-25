"""Bounded, source-labelled input for revenue work. Never infer payments from files."""
import json

from app.attachments import AttachmentReader, DOCUMENT_KINDS, IMAGE_KINDS, TEXT_KINDS


def prepare(workspace, data, context, attachments):
    if not isinstance(data, dict):
        raise ValueError('Manual data must be a JSON object.')
    if not isinstance(attachments, list) or len(attachments) > 5:
        raise ValueError('Attach at most five files.')
    data = dict(data)
    evidence = []
    reader = AttachmentReader()
    for supplied in attachments:
        path = workspace.ledger.permissions.validate_project_path(supplied, write=False)
        if path.suffix.lower() not in TEXT_KINDS | DOCUMENT_KINDS | IMAGE_KINDS:
            raise ValueError(f'Unsupported attachment: {path.name}. Use a document, image, CSV or text file.')
        attachment = reader.read(path, max_read_bytes=12 * 1024 * 1024, max_pdf_pages=30)
        if not attachment.ok:
            raise ValueError(f'{path.name}: {attachment.error or "No readable text; paste the facts manually."}')
        evidence.append({'path': str(path), 'sha256': attachment.sha256,
                         'text': attachment.text[:10000], 'truncated': len(attachment.text) > 10000,
                         'trust': 'untrusted_attachment_data'})
    if evidence:
        data['attachments'] = evidence
    if len(json.dumps(data, allow_nan=False)) > 100000:
        raise ValueError('Workflow inputs exceed 100 KB; select a smaller scope.')
    return data, context
