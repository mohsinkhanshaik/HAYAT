"""Bounded public web-app source collection for REA's static JavaScript analyzer."""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

from bs4 import BeautifulSoup

from app.tools.file_editing import atomic_save
from app.rea_source_discovery import discover_javascript, summarize_source_map

_CONTROLS = ContextVar('rea_public_source_controls', default={})


@contextmanager
def source_controls(*, cancel=None, check=None):
    """Add per-job controls while retaining normal registry authorization."""
    previous = _CONTROLS.get()
    inherited = previous.get('check')
    def combined():
        if inherited is not None:
            inherited()
        if check is not None and check is not inherited:
            check()
    token = _CONTROLS.set({**previous, 'check': combined,
                           'cancel': cancel if cancel is not None else previous.get('cancel')})
    try:
        yield
    finally:
        _CONTROLS.reset(token)


def _check_controls():
    from app.models.streaming import check_cancelled
    check_cancelled()
    controls = _CONTROLS.get()
    if controls.get('check') is not None:
        controls['check']()
    if controls.get('cancel') is not None and controls['cancel'].is_set():
        raise PermissionError('Public source inspection cancelled; an in-flight static call may finish, but its result is discarded.')


def _origin(url):
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or '').lower(), parts.port or (443 if parts.scheme == 'https' else 80)

NATIVE_SUFFIXES = {'.exe', '.dll', '.so', '.dylib', '.bin', '.elf', '.apk', '.aab', '.ipa', '.wasm',
                   '.jar', '.class', '.msi', '.dmg', '.pkg', '.asar', '.zip', '.app', '.pyc', '.o', '.a',
                   '.sys', '.node', '.hex', '.sqlite', '.db', '.hap', '.msix', '.appx', '.hsp'}


def native_link(url: str) -> bool:
    return Path(urlsplit(url).path).suffix.lower() in NATIVE_SUFFIXES


class PublicAppLink:
    MAX_RESOURCES = 8
    MAX_TOTAL = 5_000_000
    MAX_DEPTH = 2
    MAX_MAPS = 2

    def __init__(self, fetcher, analyze, folder: Path, allowed=lambda: True):
        self.fetcher, self.analyze, self.folder, self.allowed = fetcher, analyze, folder, allowed

    def _check(self):
        _check_controls()
        if not self.allowed():
            raise PermissionError('Link inspection is blocked by privacy or network policy.')

    def _fetch(self, url, maximum, *, timeout=30, source_map=False):
        self._check()
        return self.fetcher.fetch(url, max_bytes=maximum, accept=(
            'text/html', 'text/javascript', 'application/javascript', 'application/x-javascript',
            'text/plain', 'application/octet-stream', *(['application/json', 'application/source-map+json'] if source_map else [])),
            timeout=max(.1, min(30, timeout)))

    def inspect(self, url: str):
        from app.research.safety import validate_public_url
        from app.privacy_masks import ROUTE

        validator = getattr(self.fetcher, 'validate_url', None)
        if callable(validator):
            validator(url)
        elif ROUTE.proxy is not None:
            raise PermissionError('Masked link inspection requires a route-aware public fetcher.')
        else:
            validate_public_url(url)
        if native_link(url):
            raise ValueError('Use Open target for a native app/package download link; it prepares the existing confirmation flow.')
        # Structural URL validation first (a private/loopback target is rejected regardless of
        # privacy state); only then the fail-fast privacy/network check before any fetch.
        self._check()
        route = ROUTE.snapshot()
        def guard():
            self._check()
            if ROUTE.snapshot() != route:
                raise PermissionError('Privacy route changed during source inspection; start a new request.')
        deadline = time.monotonic() + 90
        initial = self._fetch(url, 1_000_000)
        guard()
        text = initial.content.decode('utf-8-sig')
        html = 'html' in initial.headers.get('content-type', '').lower() or text.lstrip().lower().startswith(('<!doctype html', '<html'))
        title, scripts, skipped, dependencies, maps = '', [], [], [], []
        origin = _origin(initial.final_url)
        queue, map_queue, seen, seen_maps = deque(), deque(), set(), set()
        total, attempts = len(initial.content), 0

        def notice(reason, candidate='', **extra):
            if len(skipped) < 200:
                skipped.append({'reason': reason, **({'url': candidate} if candidate else {}), **extra})

        def enqueue(reference, base, depth, kind, source):
            if not isinstance(reference, str) or not reference.strip() or len(reference) > 4096:
                notice('invalid or oversized source reference', source=source)
                return
            reference = reference.strip()
            if kind != 'script' and not reference.startswith(('.', '/', 'http://', 'https://')):
                notice('bare or unsupported module specifier; import maps/package resolution were not applied', reference, source=source)
                return
            candidate = urljoin(base, reference)
            parts = urlsplit(candidate)
            candidate = urlunsplit(parts._replace(fragment=''))
            edge = {'from': source, 'to': candidate, 'kind': kind, 'depth': depth, 'status': 'discovered'}
            dependencies.append(edge)
            try:
                valid_origin = _origin(candidate) == origin
            except ValueError:
                valid_origin = False
            if not valid_origin:
                edge['status'] = 'not_collected'
                notice('cross-origin script or module not collected', candidate, source=source)
            elif native_link(candidate):
                edge['status'] = 'not_collected'
                notice('native/resource link is not a JavaScript module', candidate, source=source)
            elif depth > self.MAX_DEPTH:
                edge['status'] = 'depth_limit'
                notice('module depth limit', candidate, source=source)
            elif candidate not in seen:
                seen.add(candidate)
                queue.append((candidate, depth, kind, source))

        def add_script(source, raw, depth, kind, source_resource=None, base=None):
            scripts.append({'url': source, 'raw': raw, 'depth': depth, 'kind': kind,
                            'retrieved_at': getattr(source_resource, 'retrieved_at', None) or datetime.now(UTC).isoformat(),
                            'status_code': getattr(source_resource, 'status_code', None)})
            guard()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                notice('collection time limit; dependency parsing not started', source)
                return
            discovery = discover_javascript(raw, timeout=remaining)
            guard()
            scripts[-1]['dependency_parser'] = discovery.get('parser', 'unavailable')
            for message in discovery.get('errors', [])[:4]:
                notice(message, source)
            for item in discovery.get('dependencies', [])[:64]:
                enqueue(item['specifier'], base or source, depth + 1, item['kind'], source)
            headers = getattr(source_resource, 'headers', {})
            map_ref = headers.get('sourcemap') or headers.get('x-sourcemap') or discovery.get('source_map')
            if map_ref:
                candidate = urljoin(base or source, map_ref)
                try:
                    same = _origin(candidate) == origin
                except ValueError:
                    same = False
                if not same:
                    notice('cross-origin, inline-data or unsupported source map not collected', candidate, source=source)
                elif candidate not in seen_maps:
                    seen_maps.add(candidate)
                    map_queue.append((candidate, source))

        if html:
            soup = BeautifulSoup(text, 'html.parser')
            title = soup.title.get_text(' ', strip=True)[:300] if soup.title else ''
            base_tag = soup.find('base', href=True)
            document_base = urljoin(initial.final_url, str(base_tag['href'])) if base_tag else initial.final_url
            for tag in soup.find_all('script', limit=64):
                guard()
                if time.monotonic() >= deadline:
                    notice('collection time limit')
                    break
                if str(tag.get('type', '')).lower() not in {'', 'module', 'text/javascript', 'application/javascript'}:
                    continue
                if len(scripts) >= self.MAX_RESOURCES:
                    notice('eight-script collection limit')
                    break
                source = tag.get('src')
                if not source:
                    body = tag.string or tag.get_text()
                    raw = body.encode('utf-8')
                    if raw.strip() and len(raw) <= 500_000 and total + len(raw) <= self.MAX_TOTAL:
                        add_script(initial.final_url + '#inline-' + str(len(scripts)), raw, 0, 'inline', initial, document_base)
                        total += len(raw)
                    elif raw.strip():
                        notice('inline script size limit')
                    continue
                enqueue(str(source), document_base, 0, 'script', initial.final_url)
        else:
            # Download pages and native binaries are not JavaScript source.
            if not (urlsplit(initial.final_url).path.lower().endswith(('.js', '.mjs', '.cjs'))
                    or 'javascript' in initial.headers.get('content-type', '').lower()):
                raise ValueError('The link did not return an HTML page or JavaScript source. Use a direct native package link with Open target.')
            seen.add(initial.final_url)
            add_script(initial.final_url, initial.content, 0, 'direct', initial)
        while queue and len(scripts) < self.MAX_RESOURCES and attempts < self.MAX_RESOURCES:
            guard()
            if time.monotonic() >= deadline or total >= self.MAX_TOTAL:
                notice('collection time or total byte limit')
                break
            candidate, depth, kind, source = queue.popleft()
            attempts += 1
            try:
                resource = self._fetch(candidate, min(500_000, self.MAX_TOTAL - total), timeout=deadline - time.monotonic())
                guard()
                if _origin(resource.final_url) != origin:
                    notice('cross-origin redirect not retained', candidate)
                    continue
                decoded = resource.content.decode('utf-8-sig')
                if ('html' in resource.headers.get('content-type', '').lower()
                        or decoded.lstrip().lower().startswith(('<!doctype', '<html', '<head', '<body'))):
                    notice('script request returned HTML; page/challenge content was not analyzed as JavaScript', candidate)
                    continue
                total += len(resource.content)
                add_script(resource.final_url, resource.content, depth, kind, resource)
            except PermissionError:
                raise
            except Exception as error:
                notice(type(error).__name__, candidate)
        for candidate, _depth, _kind, _source in list(queue)[:64]:
            notice('script count, request, byte or time collection limit', candidate)
        map_attempts = 0
        while map_queue and map_attempts < self.MAX_MAPS and total < self.MAX_TOTAL:
            guard()
            if time.monotonic() >= deadline:
                notice('collection time limit; source map not retrieved')
                break
            candidate, source = map_queue.popleft()
            map_attempts += 1
            try:
                resource = self._fetch(candidate, min(500_000, self.MAX_TOTAL - total),
                                       timeout=deadline - time.monotonic(), source_map=True)
                guard()
                if _origin(resource.final_url) != origin:
                    notice('cross-origin source-map redirect not retained', candidate)
                    continue
                total += len(resource.content)
                details = summarize_source_map(resource.content)
                maps.append({'url': resource.final_url, 'source_url': source, 'raw': resource.content,
                             'retrieved_at': getattr(resource, 'retrieved_at', None) or datetime.now(UTC).isoformat(),
                             'sha256': hashlib.sha256(resource.content).hexdigest(), **details})
            except PermissionError:
                raise
            except Exception as error:
                notice('source map unavailable: ' + type(error).__name__, candidate, source=source)
        for candidate, source in list(map_queue)[:64]:
            notice('source-map count, byte or time collection limit', candidate, source=source)
        guard()
        folder = self.folder.resolve() / uuid4().hex
        folder.resolve().relative_to(self.folder.resolve())
        folder.mkdir(parents=True)
        sources = folder / 'sources'
        sources.mkdir()
        resources = []
        for index, script in enumerate(scripts):
            guard()
            source, raw = script['url'], script['raw']
            path = sources / f'source-{index:02d}.js'
            atomic_save(path, raw, backup=False)
            resources.append({'url': source, 'path': str(path), 'bytes': len(raw),
                              'sha256': hashlib.sha256(raw).hexdigest(),
                              **{key: script.get(key) for key in ('kind', 'depth', 'retrieved_at', 'status_code', 'dependency_parser')}})
        source_maps = []
        for index, source_map in enumerate(maps):
            guard()
            path = folder / 'source-maps' / f'map-{index:02d}.json'
            path.parent.mkdir(exist_ok=True)
            atomic_save(path, source_map['raw'], backup=False)
            source_maps.append({key: value for key, value in source_map.items() if key != 'raw'}
                               | {'path': str(path), 'bytes': len(source_map['raw'])})
        collected = {item['url'] for item in resources}
        for edge in dependencies:
            if edge['status'] == 'discovered':
                edge['status'] = 'collected' if edge['to'] in collected else 'not_collected'
        scope = ('Static public frontend source only. No page scripts, apps or installers were executed. '
                 'Backend/private code and native app binaries are not recovered from a landing or app-store page. '
                 'At most eight same-origin/inline scripts and two advertised same-origin source maps; '
                 'literal imports are parsed to depth two, not executed. Bare imports/import maps and computed dependencies are unresolved. '
                 'Source-map contents are publisher-provided evidence, not recovered backend code. Numbered source copies do not recreate a runnable application.')
        metadata = {'requested_url': url, 'final_url': initial.final_url, 'page_sha256': initial.sha256,
                    'title': title, 'resources': resources, 'skipped_resources': skipped,
                    'source_maps': source_maps, 'dependencies': dependencies,
                    'retrieved_at': getattr(initial, 'retrieved_at', None) or datetime.now(UTC).isoformat(),
                    'collection': {'script_attempts': attempts, 'source_map_attempts': map_attempts,
                                   'max_scripts': self.MAX_RESOURCES, 'max_source_maps': self.MAX_MAPS,
                                   'max_module_depth': self.MAX_DEPTH, 'max_bytes': self.MAX_TOTAL,
                                   'accepted_bytes': total, 'max_seconds': 90},
                    'code_executed': False, 'scope': scope, 'analysis_status': 'not run'}
        manifest = folder / 'hayat-link-source.json'
        atomic_save(manifest, json.dumps(metadata, indent=2).encode(), backup=False)
        output = None
        if scripts:
            guard()
            atomic_save(sources / 'package.json', b'{"name":"hayat-public-source","private":true}', backup=False)
            try:
                output = self.analyze(str(sources))
                guard()
                metadata['analysis_status'] = 'REA static analysis completed'
                analysis_bytes = json.dumps(output, indent=2).encode('utf-8')
                analysis_path = folder / 'analysis.json'
                atomic_save(analysis_path, analysis_bytes, backup=False)
                metadata['analysis_path'] = str(analysis_path)
                metadata['analysis_sha256'] = hashlib.sha256(analysis_bytes).hexdigest()
                if len(analysis_bytes) > 500_000:
                    output = {'complete_report_path': str(analysis_path), 'returned_excerpt': True,
                              'excerpt': analysis_bytes.decode('utf-8')[:100_000]}
            except Exception:
                metadata['analysis_status'] = 'REA analysis failed; sources retained for inspection'
                raise
            finally:
                guard()
                atomic_save(manifest, json.dumps(metadata, indent=2).encode(), backup=False)
        guard()
        return {'reverse_engineer': True, 'link_analysis': True, 'source_url': url, 'manifest': str(manifest),
                'workspace': str(folder), 'resources': resources, 'skipped_resources': skipped, 'code_executed': False,
                'source_maps': source_maps, 'dependencies': dependencies, 'source_evidence': metadata,
                'report': json.dumps({'public_link': metadata, 'analysis': output or
                    'No JavaScript was exposed in the fetched page; no native or backend analysis was performed.'}, indent=2),
                'scope': scope}
