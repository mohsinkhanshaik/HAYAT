"""Image-file provenance, never face recognition or identity search."""
from __future__ import annotations

from datetime import UTC, datetime
import codecs
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
import warnings

from PIL import Image, ImageOps, ExifTags


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_DIMENSION = 16_384
MAX_PAGE_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/tiff', 'image/bmp'}


def _bounded_image(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Image source analysis is limited to 20 MiB.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        picture = Image.open(BytesIO(data))
    if (picture.width < 1 or picture.height < 1 or picture.width * picture.height > MAX_IMAGE_PIXELS
            or max(picture.size) > MAX_IMAGE_DIMENSION):
        picture.close()
        raise ValueError('Image source analysis supports at most 20 million pixels and 16,384 pixels per side.')
    return picture


def _read_image(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    with _bounded_image(data) as picture:
        picture.verify()
    return data


def image_thumbnail(path, size=(140, 80)):
    """Bound file and decoded dimensions before allocating a display preview."""
    data = _read_image(path)
    with _bounded_image(data) as picture:
        # Shrink before orientation/conversion, which can otherwise copy a full
        # decompressed image merely to display an 11,200-pixel thumbnail.
        picture.thumbnail(size)
        result = ImageOps.exif_transpose(picture).convert('RGB')
        result.thumbnail(size)
        return result


def _pixel_fingerprint(data):
    with _bounded_image(data) as picture:
        if getattr(picture, 'n_frames', 1) != 1:
            return None  # one matching animation frame cannot verify the animation
        oriented = ImageOps.exif_transpose(picture).convert('RGBA')
        try:
            digest = hashlib.sha256()
            digest.update(f'{oriented.width}x{oriented.height}:RGBA:'.encode('ascii'))
            digest.update(oriented.tobytes())
            return digest.hexdigest()
        finally:
            oriented.close()


def inspect_image(path):
    path = Path(path)
    data = _read_image(path)
    with _bounded_image(data) as picture:
        # Never disclose GPS, serial numbers, owner/artist names or identifiers.
        allowed = {'Software', 'DateTime', 'DateTimeOriginal', 'ColorSpace', 'Orientation'}
        exif = {ExifTags.TAGS.get(key, str(key)): str(value)[:200] for key, value in picture.getexif().items()
                if ExifTags.TAGS.get(key) in allowed}
        return {'filename': path.name, 'bytes': len(data), 'format': picture.format,
                'width': picture.width, 'height': picture.height, 'metadata': exif,
                'sha256': hashlib.sha256(data).hexdigest(), 'sha1': hashlib.sha1(data).hexdigest(),
                'authenticity': 'Unknown. Metadata and matching bytes cannot establish authenticity or detect all manipulation.',
                'privacy': 'GPS and identifying EXIF fields omitted. No face descriptors are calculated.'}


class _PageImages(HTMLParser):
    """Read declared image links only; never execute scripts or follow pages."""
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.urls, self.seen = base, [], set()
        self.declarations = {}
        self._base_selected = False
        self.truncated = False

    def _add(self, value, declaration):
        if not value or len(value) > 4096:
            return
        try:
            parts = urlsplit(urljoin(self.base, value.strip()))
            if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
                return
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
        except ValueError:
            return
        if url in self.seen:
            declarations = self.declarations[url]
            if declaration not in declarations and len(declarations) < 8:
                declarations.append(declaration)
            return
        if len(self.urls) >= 32:
            self.truncated = True
            return
        self.seen.add(url)
        self.declarations[url] = [declaration]
        self.urls.append(url)

    def handle_starttag(self, tag, attrs):
        attrs = {key: value or '' for key, value in attrs}
        if tag == 'base' and not self._base_selected and attrs.get('href'):
            # Honor a declared public base without doing DNS while parsing. The
            # fetcher still validates every resulting URL and redirect on its
            # actual route. Invalid/private bases do not redirect later reads.
            from app.research.safety import validate_public_url
            try:
                self.base = validate_public_url(urljoin(self.base, attrs['href']), via_tor=True)
            except ValueError:
                pass
            else:
                self._base_selected = True
        elif tag == 'meta' and (attrs.get('property', '') or attrs.get('name', '')).lower() in {
                'og:image', 'og:image:url', 'og:image:secure_url', 'twitter:image', 'twitter:image:src'}:
            self._add(attrs.get('content'), 'meta:' + (attrs.get('property') or attrs.get('name')).lower())
        elif tag == 'link':
            relations = set(attrs.get('rel', '').lower().split())
            if 'image_src' in relations or ('preload' in relations and attrs.get('as', '').lower() == 'image'):
                self._add(attrs.get('href'), 'link:href')
                for value in _srcset_urls(attrs.get('imagesrcset', '')):
                    self._add(value, 'link:imagesrcset')
        elif tag in {'img', 'source'}:
            for key in ('src', 'data-src', 'data-original', 'data-lazy-src'):
                self._add(attrs.get(key), tag + ':' + key)
            for key in ('srcset', 'data-srcset'):
                for value in _srcset_urls(attrs.get(key, '')):
                    self._add(value, tag + ':' + key)


def _srcset_urls(value):
    """Bounded URL/descriptor parsing; commas inside URL tokens are preserved."""
    if not value or len(value) > 65536 or 'data:' in value.lower():
        return
    # Keep embedded data entirely out of external URL discovery. In particular,
    # splitting a base64 comma must never manufacture a same-origin request.
    offset = 0
    whitespace = ' \t\n\r\f'
    for _ in range(32):
        while offset < len(value) and value[offset] in whitespace + ',':
            offset += 1
        if offset >= len(value):
            return
        start = offset
        while offset < len(value) and value[offset] not in whitespace:
            offset += 1
        url = value[start:offset]
        if url.endswith(','):
            yield url.rstrip(',')
            continue
        start = offset
        while offset < len(value) and value[offset] != ',':
            offset += 1
        descriptors = value[start:offset].split()
        valid = not descriptors
        if len(descriptors) == 1:
            descriptor = descriptors[0]
            if re.fullmatch(r'[1-9][0-9]*w', descriptor):
                valid = True
            elif re.fullmatch(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?x', descriptor):
                density = float(descriptor[:-1])
                valid = 0 < density < float('inf')
        if valid:
            yield url


def _page_text(resource):
    """Decode bounded HTML declarations instead of corrupting non-UTF8 URLs."""
    data = resource.content
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode('utf-16', errors='replace')
    if data.startswith(codecs.BOM_UTF8):
        return data.decode('utf-8-sig', errors='replace')
    headers = getattr(resource, 'headers', {}) or {}
    content_type = next((str(value) for key, value in headers.items() if str(key).lower() == 'content-type'), '')
    match = re.search(r'(?i)charset\s*=\s*["\x27]?([a-z0-9._-]{1,40})', content_type)
    if not match:
        # HTML charset declarations occur early; only inspect the first KiB.
        prefix = data[:1024].decode('ascii', errors='ignore')
        match = re.search(r'(?i)<meta\b[^>]{0,500}?charset\s*=\s*["\x27]?([a-z0-9._-]{1,40})', prefix)
    encoding = match.group(1) if match else 'utf-8'
    if encoding.lower() in {'latin1', 'latin-1', 'iso-8859-1', 'us-ascii', 'ascii'}:
        encoding = 'windows-1252'
    try:
        # Without a BOM, accept text codecs that preserve HTML's ASCII syntax.
        # Binary transforms and UTF-16/32 labels cannot silently turn ordinary
        # ASCII markup into unrelated decoded text.
        marker = '<meta charset="utf-8">+\n'
        if marker.encode(encoding) != marker.encode('ascii'):
            encoding = 'utf-8'
        return data.decode(encoding, errors='replace')
    except (LookupError, UnicodeError):
        return data.decode('utf-8', errors='replace')


def _route_report(route):
    return {'mode': 'tor' if route[0] else 'direct', 'epoch': route[1]}


def _failure_detail(error, detail):
    # Provider exception text can contain request secrets and local paths.
    status = getattr(getattr(error, 'response', None), 'status_code', None)
    suffix = f' (HTTP {status})' if type(status) is int and 100 <= status <= 599 else ''
    if isinstance(error, PermissionError) and 'robots.txt' in str(error).lower():
        suffix = ' (publisher robots.txt policy)'
    return f'{type(error).__name__}: {detail}{suffix}'


def _fetch_on_route(fetcher, url, route, **options):
    from app.research.fetcher import PublicFetcher
    if isinstance(fetcher, PublicFetcher) and route[0]:
        # Close the checkpoint -> fetch race: a Tor-only operation must not
        # open a direct connection if Tor is switched off at that boundary.
        options['require_tor'] = True
    return fetcher.fetch(url, **options)


def check_source_page(path, page_url, fetcher, *, max_candidates=8, check=None):
    """Verify whether a supplied public page publishes this file or its pixels.

    This is a bounded one-page comparison, not reverse-face search, a claim of
    original authorship, or a crawl. Images are downloaded, never uploaded.
    """
    if type(max_candidates) is not int or not 1 <= max_candidates <= 12:
        raise ValueError('Check between 1 and 12 image candidates per source page.')
    metadata = inspect_image(path)
    local_data = _read_image(path)
    if hashlib.sha256(local_data).hexdigest() != metadata['sha256']:
        raise ValueError('The local image changed during inspection; choose it again.')
    local_pixels = _pixel_fingerprint(local_data)
    from app.privacy_masks import ROUTE
    from app.research.safety import validate_public_url
    route = ROUTE.snapshot()
    def checkpoint():
        if ROUTE.snapshot() != route:
            raise PermissionError('Privacy route changed during the image check; start a new check.')
        if check is not None:
            check()
    validate = getattr(fetcher, 'validate_url', validate_public_url)
    # Validate before reporting any URL as a retrieved source. PublicFetcher
    # validates every redirect too and retains the active Tor routing policy.
    checkpoint()
    safe_url = validate(page_url)
    report = {'provider': 'User-supplied public source page', 'requested_url': safe_url,
              'retrieved_at': datetime.now(UTC).isoformat(), 'metadata': metadata,
              'route': _route_report(route),
              'page': None, 'candidates': [], 'matches': [], 'errors': [],
              'coverage': {'candidates_found': 0, 'candidates_attempted': 0, 'bytes_received': 0,
                           'candidates_verified': 0, 'candidates_failed': 0,
                           'candidate_limit': max_candidates, 'byte_limit': MAX_SOURCE_BYTES, 'limited': False},
              'scope': 'Exact file or exact oriented RGBA pixels only. No person identity, original authorship, '
                       'authenticity, similar-image ranking or complete web coverage is established.',
              'privacy': 'The image is not uploaded. Only the supplied page and its declared image URLs are requested.'}
    checkpoint()
    try:
        page = _fetch_on_route(fetcher, safe_url, route, max_bytes=MAX_PAGE_BYTES,
                               accept={'text/html', 'application/xhtml+xml'})
        checkpoint()
        if len(page.content) > MAX_PAGE_BYTES:
            raise ValueError('Source page exceeded the 1 MiB HTML limit.')
        final_url = validate(page.final_url)
        report['page'] = {'url': final_url, 'retrieved_at': page.retrieved_at,
                          'sha256': hashlib.sha256(page.content).hexdigest(), 'bytes': len(page.content)}
        report['coverage']['bytes_received'] = len(page.content)
        parser = _PageImages(final_url)
        parser.feed(_page_text(page))
        parser.close()
    except Exception as error:
        # Cancellation must reach the owner instead of becoming an ordinary
        # provider failure. Preserve a bounded failed report for route changes.
        if check is not None:
            check()
        report['errors'].append(_failure_detail(error, 'source page could not be verified'))
        report['outcome'] = 'failed'
        return report
    coverage = report['coverage']
    coverage['candidates_found'] = len(parser.urls)
    coverage['limited'] = parser.truncated or len(parser.urls) > max_candidates
    for candidate in parser.urls[:max_candidates]:
        checkpoint()
        remaining = coverage['byte_limit'] - coverage['bytes_received']
        if remaining <= 0:
            coverage['limited'] = True
            break
        coverage['candidates_attempted'] += 1
        declarations = list(parser.declarations[candidate])
        evidence = {'requested_url': candidate, 'declarations': declarations, 'status': 'failed'}
        report['candidates'].append(evidence)
        try:
            candidate = validate(candidate)
            resource = _fetch_on_route(fetcher, candidate, route, max_bytes=min(MAX_IMAGE_BYTES, remaining), accept=IMAGE_TYPES)
            checkpoint()
            coverage['bytes_received'] += len(resource.content)
            if len(resource.content) > min(MAX_IMAGE_BYTES, remaining):
                raise ValueError('Candidate exceeded the remaining image byte budget.')
            final_image = validate(resource.final_url)
            digest = hashlib.sha256(resource.content).hexdigest()
            evidence.update({'image_url': final_image, 'sha256': digest, 'retrieved_at': resource.retrieved_at,
                             'bytes': len(resource.content), 'status': 'different'})
            match = ''
            if digest == metadata['sha256']:
                match = 'verified_exact_bytes'
            elif local_pixels is not None and _pixel_fingerprint(resource.content) == local_pixels:
                match = 'verified_exact_pixels'
            coverage['candidates_verified'] += 1
            if match:
                evidence['status'] = match
                report['matches'].append({'page': final_url, 'image_url': final_image, 'match': match,
                                          'sha256': digest, 'retrieved_at': resource.retrieved_at,
                                          'page_sha256': report['page']['sha256'],
                                          'page_retrieved_at': report['page']['retrieved_at'],
                                          'declarations': declarations})
        except Exception as error:
            checkpoint()
            coverage['candidates_failed'] += 1
            evidence['status'] = 'failed'
            evidence['error'] = _failure_detail(error, 'image could not be verified')
            report['errors'].append(evidence['error'])
    checkpoint()
    report['outcome'] = 'matched' if report['matches'] else 'unknown'
    return report


def exact_sources(path, fetcher, *, check=None):
    metadata = inspect_image(path)
    from app.privacy_masks import ROUTE
    route = ROUTE.snapshot()
    def checkpoint():
        if ROUTE.snapshot() != route:
            raise PermissionError('Privacy route changed during the image check; start a new check.')
        if check is not None:
            check()
    params = {'action': 'query', 'format': 'json', 'list': 'allimages', 'aisha1': metadata['sha1'],
              'aiprop': 'url|sha1|timestamp', 'ailimit': 10}
    endpoint = 'https://commons.wikimedia.org/w/api.php?' + urlencode(params)
    report = {'provider': 'Wikimedia Commons', 'endpoint': endpoint,
              'retrieved_at': datetime.now(UTC).isoformat(), 'metadata': metadata, 'matches': [],
              'errors': [], 'candidates': [], 'index': None, 'outcome': 'unknown', 'route': _route_report(route),
              'coverage': {'candidates_found': 0, 'candidates_attempted': 0, 'candidates_verified': 0,
                           'candidates_failed': 0, 'bytes_received': 0, 'candidate_limit': 10,
                           'byte_limit': MAX_SOURCE_BYTES, 'limited': False},
              'privacy': 'The image is not uploaded. Its file hash is sent to the Commons API; candidate files are downloaded.',
              'scope': 'Exact file source lookup in Wikimedia Commons only. Description-page links come from the index; '
                       'their page contents are not verified by this lookup. No face matching, person identification, '
                       'dark-web search or exhaustive web coverage.',
              'similar_image_search': 'Not configured. No paid reverse-image service is invoked.'}
    def unavailable(detail):
        checkpoint()
        report['errors'].append(detail)
        report['outcome'] = 'failed'
        return report
    checkpoint()
    try:
        from app.research.fetcher import PublicFetcher
        if isinstance(fetcher, PublicFetcher):
            resource = _fetch_on_route(fetcher, endpoint, route, max_bytes=MAX_PAGE_BYTES,
                                       accept={'application/json', 'application/*+json', 'text/json'})
            checkpoint()
            if len(resource.content) > MAX_PAGE_BYTES:
                raise ValueError('Image index exceeded the 1 MiB limit.')
            if _commons_url(resource.final_url, 'commons.wikimedia.org') is None:
                raise ValueError('Image index redirected away from its publisher.')
            listing = json.loads(resource.content)
            report['coverage']['bytes_received'] = len(resource.content)
            report['index'] = {'url': resource.final_url, 'retrieved_at': resource.retrieved_at,
                               'sha256': hashlib.sha256(resource.content).hexdigest(), 'bytes': len(resource.content)}
        else:
            # Compatibility for injected transports which provide decoded JSON.
            # No raw response digest/timestamp is fabricated for those adapters.
            listing = fetcher.get_json(endpoint)
        checkpoint()
    except Exception as error:
        return unavailable(_failure_detail(error, 'provider unavailable'))
    if not isinstance(listing, dict):
        return unavailable('Provider returned malformed image metadata.')
    if listing.get('error'):
        error = listing['error']
        return unavailable('Wikimedia API error: ' + str(error.get('code', 'unknown') if isinstance(error, dict) else 'unknown')[:80])
    query = listing.get('query')
    if not isinstance(query, dict) or not isinstance(query.get('allimages'), list):
        return unavailable('Provider returned malformed image metadata.')
    coverage, seen = report['coverage'], set()
    rows = query['allimages']
    coverage['candidates_found'] = len(rows)
    coverage['limited'] = bool(listing.get('continue')) or len(rows) > coverage['candidate_limit']
    for row in rows[:coverage['candidate_limit']]:
        checkpoint()
        if not isinstance(row, dict):
            report['errors'].append('Malformed candidate omitted.')
            continue
        page = _commons_url(row.get('descriptionurl'), 'commons.wikimedia.org')
        media = _commons_url(row.get('url'), 'upload.wikimedia.org')
        if page is None or media is None:
            report['errors'].append('Invalid publisher candidate omitted.')
            continue
        if (page, media) in seen:
            continue
        seen.add((page, media))
        remaining = coverage['byte_limit'] - coverage['bytes_received']
        if remaining <= 0:
            coverage['limited'] = True
            break
        coverage['candidates_attempted'] += 1
        evidence = {'page': page, 'requested_url': media, 'status': 'failed'}
        report['candidates'].append(evidence)
        try:
            resource = _fetch_on_route(fetcher, media, route, max_bytes=min(MAX_IMAGE_BYTES, remaining), accept=IMAGE_TYPES)
            checkpoint()
            coverage['bytes_received'] += len(resource.content)
            if len(resource.content) > min(MAX_IMAGE_BYTES, remaining):
                raise ValueError('Candidate exceeded the remaining image byte budget.')
            digest = hashlib.sha256(resource.content).hexdigest()
            if digest != metadata['sha256']:
                report['errors'].append('Publisher result did not match the selected local file bytes.')
                evidence['status'] = 'different'
                continue
            final_image = _commons_url(resource.final_url, 'upload.wikimedia.org')
            if final_image is None:
                raise ValueError('Candidate redirected away from the publisher image host.')
            evidence.update({'status': 'verified_exact_bytes', 'image_url': final_image, 'sha256': digest,
                             'bytes': len(resource.content), 'retrieved_at': resource.retrieved_at})
            coverage['candidates_verified'] += 1
            report['matches'].append({'page': page, 'image_url': final_image, 'match': 'verified_exact_bytes',
                                      'retrieved_at': resource.retrieved_at, 'sha256': metadata['sha256'],
                                      'page_verified': False, 'index': report['index']})
        except Exception as error:
            checkpoint()
            coverage['candidates_failed'] += 1
            evidence['error'] = _failure_detail(error, 'image verification failed')
            report['errors'].append(evidence['error'])
    checkpoint()
    report['outcome'] = 'matched' if report['matches'] else 'unknown'
    return report


def _commons_url(value, hostname):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    from app.research.safety import validate_public_url
    try:
        normalized = validate_public_url(value, via_tor=True)
        parts = urlsplit(normalized)
        if parts.scheme != 'https' or parts.hostname != hostname or parts.port not in {None, 443}:
            return None
        return normalized
    except ValueError:
        return None
