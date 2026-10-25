"""Explicit image-file provenance searches, never face or identity searches."""
from __future__ import annotations

import re

from app.tools.image_tools import image_tool_controls

_ASK = re.compile(r'(?i)\b(?:find|search|check|where|look up)\b.{0,120}'
                  r'(?:\b(?:image|photo|picture)s?\b.{0,80}\b(?:sources?|copies|published|reposts?|online|web)\b|'
                  r'\b(?:sources?|copies)\b.{0,60}\b(?:image|photo|picture)\b)')
_PATH = re.compile(r'(?:"([^"\r\n]+\.(?:png|jpe?g|webp|bmp|gif|tiff?))"|\x27([^\x27\r\n]+\.(?:png|jpe?g|webp|bmp|gif|tiff?))\x27)', re.I)


def image_source_request(text, path_hint=None):
    value = str(text or '').strip()
    found = _PATH.search(value)
    file_search = found and re.search(r'(?i)\b(?:find|search|check|look up)\b', value) and re.search(
        r'(?i)\b(?:web|online|sources?|copies|published|reposts?)\b|https?://', value)
    if len(value) > 4500 or not (_ASK.search(value) or file_search):
        return None
    if re.search(r'(?i)\b(?:identify|who is|face match|identity match|find anyone)\b', value):
        return None
    path = (found.group(1) or found.group(2)) if found else path_hint
    if not path or not re.search(r'(?i)\.(?:png|jpe?g|webp|bmp|gif|tiff?)$', str(path)):
        return 'needs_image', {}
    options = {'tor_only': True} if re.search(r'(?i)\b(?:through|via|over) tor[.!?]*$', value) else {}
    page = re.search(r'https?://[^\s"<>]+', value)
    if page:
        return 'image_source_check_page', {'path': str(path), 'url': page.group().rstrip("'.,;!?)"), **options}
    return 'image_source_search', {'path': str(path), **options}


def direct_image_source_plan(text, names, path_hint=None):
    request = image_source_request(text, path_hint)
    if request is None or request[0] not in names:
        return None
    return [{'description': 'Check source evidence for the selected image file without uploading the photo or identifying anyone.',
             'tool': request[0], 'arguments': request[1], 'expected': {'type': 'tool_success'}, 'max_retries': 0}]


def register_image_source_tools(registry, research):
    def call(path, url=None, tor_only=False):
        from app.safety.permissions import PermissionDecision
        from app.tools.image_tools import _CONTROLS
        from app.research.image_sources import exact_sources, check_source_page
        ticket, control = registry.interlock.ticket(), _CONTROLS.get()
        from app.privacy_masks import ROUTE
        route = ROUTE.snapshot()
        if tor_only:
            from urllib.parse import urlsplit
            proxy = urlsplit(route[0] or '')
            if (proxy.scheme not in {'socks5', 'socks5h'} or proxy.hostname not in {'127.0.0.1', '::1', 'localhost'}
                    or proxy.username or proxy.password or proxy.port is None):
                raise PermissionError('Start a local Tor route manually; this check has no direct fallback.')
        def check():
            registry.interlock.check(ticket)
            if control is not None:
                control()
            if (getattr(getattr(research, 'authority', None), 'privacy_mode', False)
                    or getattr(getattr(registry, 'authority', None), 'privacy_mode', False)):
                raise PermissionError('Privacy mode blocks internet image-source checks.')
            if ROUTE.snapshot() != route:
                raise PermissionError('Privacy route changed. Start a fresh image-source request.')
            if tor_only and ROUTE.proxy is None:
                raise PermissionError('This image-source check requires Tor. Start Tor manually; direct fallback is disabled.')
        check()
        if str(path).startswith(('\\\\', '//')) or '://' in str(path):
            raise ValueError('Use a local image file, not a URL or network share.')
        permission = registry.permissions.check('filesystem_read', {'path': path})
        tool = 'image_source_search' if url is None else 'image_source_check_page'
        if (permission.decision is PermissionDecision.DENY
                or not registry._authorize(registry.get(tool), tool, permission, {'path': path})):
            raise PermissionError('Reading the selected image was not authorized.')
        selected = registry.permissions.validate_project_path(path, write=False)
        fetcher = research.fetcher
        owned = None
        if tor_only:
            from app.research.onion import TorRequiredFetcher
            settings = getattr(research, 'settings', {})
            owned = fetcher = TorRequiredFetcher(str(settings.get('user_agent', 'HAYAT-ImageResearch/1.0')),
                timeout=float(settings.get('request_timeout_seconds', 15)), check=check)
        try:
            with image_tool_controls(check=check):
                result = (exact_sources(selected, fetcher, check=check) if url is None else
                          check_source_page(selected, url, fetcher, check=check))
            check()
        finally:
            if owned is not None:
                owned.close()
        return {**result, 'image_source_result': True, 'trust': 'untrusted_external_data',
                'identity_inference': False, 'path': str(selected), 'tor_required': tor_only or route[0] is not None}

    path = {'type': 'string', 'minLength': 1, 'maxLength': 2048}
    registry.register('image_source_search', 'Look for an exact selected image file on Wikimedia Commons by file hash; '
        'verify retrieved candidate bytes and return source URLs/timestamps. The photo is not uploaded. '
        'Provider coverage is limited; missing results do not mean the photo is absent from the web. No face identity search.',
        {'type': 'object', 'properties': {'path': path, 'tor_only': {'type': 'boolean'}}, 'required': ['path'], 'additionalProperties': False},
        'low', 'public_web_read', lambda path, tor_only=False: call(path, tor_only=tor_only))
    registry.register('image_source_check_page', 'Read one explicitly supplied public source page and compare bounded '
        'published images with the selected local file or exact oriented pixels. No upload, face matching or identity inference.',
        {'type': 'object', 'properties': {'path': path, 'url': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                                        'tor_only': {'type': 'boolean'}},
         'required': ['path', 'url'], 'additionalProperties': False},
        'low', 'public_web_read', lambda path, url, tor_only=False: call(path, url, tor_only))


def format_source_report(value):
    lines = ['Image-source evidence — no face identification',
             'Provider: ' + str(value.get('provider', 'Selected public source page')),
             'The image stayed local. Search coverage is limited; missing matches do not prove it is absent online.']
    if value.get('route'):
        route = value['route']
        lines.append('Route: ' + ('Public websites through Tor' if route.get('mode') == 'tor' else 'Public web')
                     if isinstance(route, dict) else 'Route: ' + str(route))
    coverage = value.get('coverage') or {}
    if coverage:
        lines += [f"Checked {coverage.get('candidates_attempted', 0)} of {coverage.get('candidates_found', 0)} declared/indexed candidates; "
                  f"received {coverage.get('bytes_received', 0):,} bytes.",
                  'Coverage limited: ' + str(bool(coverage.get('limited')))]
    metadata = value.get('metadata') or {}
    if metadata.get('filename'):
        lines.append('Local file: ' + str(metadata['filename']))
    if metadata.get('width') and metadata.get('height'):
        lines.append(f"Image: {metadata['width']} × {metadata['height']} pixels; format {metadata.get('format', 'unknown')}; {metadata.get('bytes', 0):,} bytes.")
    if metadata.get('sha256'):
        lines.append('Local image SHA-256: ' + str(metadata['sha256']))
    for row in value.get('matches', []):
        lines += ['', 'Verified image copy: ' + str(row.get('page') or row.get('page_url') or (value.get('page') or {}).get('url', '')),
                  'Image: ' + str(row.get('image_url', '')), 'Retrieved: ' + str(row.get('retrieved_at', '')),
                  'Evidence: ' + str(row.get('match', 'exact image comparison'))]
    if not value.get('matches'):
        lines.append('No verified copy: retrieval was incomplete; presence elsewhere remains unknown.' if value.get('errors') else
                     'No verified copy in the checked provider/page. Other websites and edited copies remain unknown.')
    if value.get('page'):
        lines.append('Page checked: ' + str(value['page'].get('url', '')))
    lines.extend('Source issue: ' + str(error) for error in value.get('errors', []))
    lines.append(str(value.get('scope', '')))
    return '\n'.join(lines)
