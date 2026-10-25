"""Manual local image inspection and duplicate checks; no biometric search."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import re

from app.tools.registry import ToolRegistry

_CONTROLS = ContextVar('local_image_controls', default=None)


@contextmanager
def image_tool_controls(*, check=None):
    previous = _CONTROLS.get()
    def guard():
        for callback in (previous, check):
            if callback is not None and callback() is False:
                raise InterruptedError('Local image operation cancelled.')
    token = _CONTROLS.set(guard)
    try:
        yield
    finally:
        _CONTROLS.reset(token)


def local_image_request(text):
    value = str(text or '').strip()
    if len(value) > 4500:
        return None
    if re.fullmatch(r'(?i)(?:please )?(?:check local image toolkit|local image toolkit status)[.!?]?', value):
        return 'local_image_toolkit_status', {}
    quoted = r'(?:"([^"\r\n]+)"|\x27([^\x27\r\n]+)\x27)'
    pair = re.fullmatch(r'(?i)(?:please )?compare local images ' + quoted + r' and ' + quoted + r'[.!?]?', value)
    if pair:
        return 'local_image_compare', {'path': pair.group(1) or pair.group(2), 'other': pair.group(3) or pair.group(4)}
    single = re.fullmatch(r'(?i)(?:please )?(?:inspect|check|analyze) (?:the )?local image ' + quoted + r'[.!?]?', value)
    if single:
        return 'local_image_inspect', {'path': single.group(1) or single.group(2)}
    return None


def direct_image_tool_plan(text, names):
    request = local_image_request(text)
    if request is None or request[0] not in names:
        return None
    return [{'description': 'Read the explicitly selected local image data without network access or identity matching.',
             'tool': request[0], 'arguments': request[1], 'expected': {'type': 'tool_success'}, 'max_retries': 0}]


def register_local_image_tools(registry: ToolRegistry):
    def call_guard():
        ticket, control = registry.interlock.ticket(), _CONTROLS.get()
        def check():
            registry.interlock.check(ticket)
            if control is not None:
                control()
        return check

    def path_allowed(path):
        if (not isinstance(path, str) or path.startswith(('\\\\', '//')) or '://' in path
                or re.match(r'(?i)^(?:file|smb|data|https?|ftp):', path)):
            raise ValueError('Choose a local image file; URLs and network shares are not supported.')
        return registry.permissions.validate_project_path(path, write=False)

    def inspect(path):
        from app.perception.image_tools import inspect_image
        check = call_guard()
        check()
        result = inspect_image(path_allowed(path), check=check)
        check()
        return result

    def compare(path, other):
        from app.perception.image_tools import compare_images
        from app.safety.permissions import PermissionDecision
        check = call_guard()
        check()
        permission = registry.permissions.check('filesystem_read', {'path': other})
        if (permission.decision is PermissionDecision.DENY or not registry._authorize(
                registry.get('local_image_compare'), 'local_image_compare', permission, {'path': other})):
            raise PermissionError('Reading the second image was not authorized.')
        result = compare_images(path_allowed(path), path_allowed(other), check=check)
        check()
        return result

    def status():
        from app.perception.image_tools import toolkit_status
        check = call_guard()
        check()
        result = toolkit_status()
        check()
        return result

    image_path = {'type': 'string', 'minLength': 1, 'maxLength': 2048}
    registry.register('local_image_inspect',
        'Inspect one explicitly selected local image using Pillow/OpenCV and ExifRead: dimensions, file hash, '
        'technical metadata and image-quality heuristics. GPS, owner names, serials and arbitrary EXIF are omitted. '
        'Read only; no network, face identification, gallery access or authenticity guarantee.',
        {'type': 'object', 'properties': {'path': image_path}, 'required': ['path'], 'additionalProperties': False},
        'low', 'filesystem_read', inspect)
    registry.register('local_image_compare',
        'Compare two explicitly selected local image files: byte equality, orientation-normalized pixel equality, '
        'and ImageHash pHash/dHash Hamming distances. Image resemblance is not facial identity or a match probability. '
        'No web search, enrollment, file write or camera activation.',
        {'type': 'object', 'properties': {'path': image_path, 'other': image_path},
         'required': ['path', 'other'], 'additionalProperties': False},
        'low', 'filesystem_read', compare)
    registry.register('local_image_toolkit_status',
        'Report local Pillow/OpenCV/ImageHash/ExifRead toolkit versions, availability and project-folder setup. '
        'Does not download/install anything, read owner images or open the enrolled-face gallery.',
        {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
        'low', 'system_read', status)
