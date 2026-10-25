"""Isolated yt-dlp extraction and bounded public-network video downloads."""
from __future__ import annotations

import ipaddress
import json
import socket
import sys
from pathlib import Path

from app.research.safety import validate_public_url


def guard_public_network():
    """Check the actual connection too, including extractor/CDN redirects.

    Installed only in this disposable process, never in HAYAT's network stack.
    """
    original_resolve = socket.getaddrinfo
    original_socket = socket.socket

    def resolve(*args, **kwargs):
        rows = original_resolve(*args, **kwargs)
        for row in rows:
            if not ipaddress.ip_address(row[4][0].split('%', 1)[0]).is_global:
                raise PermissionError('Video links must resolve to public internet addresses')
        return rows

    class PublicSocket(original_socket):
        def _check(self, address):
            if self.family not in (socket.AF_INET, socket.AF_INET6):
                raise PermissionError('Only public internet video connections are permitted')
            resolve(address[0], address[1], self.family, self.type)

        def connect(self, address):
            self._check(address)
            return super().connect(address)

        def connect_ex(self, address):
            self._check(address)
            return super().connect_ex(address)

    socket.getaddrinfo = resolve
    socket.socket = PublicSocket


def extract(request):
    from yt_dlp import YoutubeDL
    guard_public_network()
    searching = request.get('operation') == 'search'
    query = ' '.join(str(request.get('query', '')).split())
    if searching and not 2 <= len(query) <= 200:
        raise ValueError('A bounded video search title is required')
    count = max(1, min(int(request.get('limit', 3)), 3))
    url = f'ytsearch{count}:{query}' if searching else validate_public_url(request['url'])
    limit = int(request.get('max_bytes', 1024**3))
    directory = Path(request.get('directory', '.')).resolve()

    class QuietLogger:
        def debug(self, message):
            pass
        def warning(self, message):
            pass
        def error(self, message):
            pass

    def bound(progress):
        if progress.get('status') in {'downloading', 'finished'}:
            total = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
            if total > limit:
                raise ValueError('Video download exceeded its total byte limit')

    options = {
        'quiet': True, 'no_warnings': True, 'logger': QuietLogger(),
        'noplaylist': True, 'playlistend': 1, 'extract_flat': False,
        'socket_timeout': 20, 'retries': 2, 'fragment_retries': 2,
        'concurrent_fragment_downloads': 1, 'proxy': '',
        'geo_bypass': False, 'allow_unplayable_formats': False,
        'hls_prefer_native': True, 'enable_file_urls': False,
        'outtmpl': str(directory / 'source.%(ext)s'), 'overwrites': False,
        'max_filesize': limit, 'progress_hooks': [bound],
        'ffmpeg_location': request.get('ffmpeg'), 'merge_output_format': 'mp4',
        'js_runtimes': {'node': {'path': request['node']}} if request.get('node') else {},
        'remote_components': set(),
    }
    if request.get('format'):
        options['format'] = request['format']
    if searching:
        options.update(extract_flat=True, playlistend=count, skip_download=True)
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if searching:
            import re
            results = []
            for entry in (info or {}).get('entries', []):
                if len(results) >= count:
                    break
                if not entry or not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(entry.get('id', ''))):
                    continue
                results.append({'title': str(entry.get('title') or 'Untitled video')[:250],
                                'url': 'https://www.youtube.com/watch?v=' + entry['id'],
                                'uploader': str(entry.get('uploader') or entry.get('channel') or '')[:150],
                                'duration': entry.get('duration'), 'is_live': bool(entry.get('is_live'))})
            return {'query': query, 'results': results, 'downloaded': False}
        if not info or info.get('_type') in {'playlist', 'multi_video'} or info.get('entries'):
            raise ValueError('Use a single video link, not a playlist')
        if info.get('is_live') or info.get('live_status') in {'is_live', 'is_upcoming'}:
            raise ValueError('Choose a completed video; live streams are not downloaded')
        if info.get('has_drm'):
            raise ValueError('This video is protected by DRM and cannot be downloaded here')
        formats = [f for f in info.get('formats', []) if f.get('vcodec') != 'none'
                   and not f.get('has_drm') and f.get('protocol') in
                   {'https', 'http', 'http_dash_segments', 'm3u8_native'}]
        result = {key: info.get(key) for key in
                  ('id', 'title', 'duration', 'uploader', 'view_count', 'like_count', 'upload_date', 'license')}
        result['formats'] = [{key: f.get(key) for key in
                              ('format_id', 'height', 'width', 'fps', 'ext', 'filesize',
                               'filesize_approx', 'vcodec', 'acodec', 'protocol')}
                             for f in formats][-100:]
        if request['operation'] == 'download':
            # Restrict transports to yt-dlp's guarded native HTTP downloaders.
            info['formats'] = [f for f in info.get('formats', []) if not f.get('has_drm')
                               and f.get('protocol') in {'https', 'http', 'http_dash_segments', 'm3u8_native'}]
            downloaded = downloader.process_ie_result(info, download=True)
            paths = [Path(item['filepath']) for item in downloaded.get('requested_downloads', [])
                     if item.get('filepath')]
            paths += [Path(downloaded['filepath'])] if downloaded.get('filepath') else []
            paths += list(directory.glob('source.*'))
            video = next((p for p in paths if p.is_file() and p.suffix.lower() in
                          {'.mp4', '.mkv', '.webm', '.mov', '.m4v', '.avi'}), None)
            if video is None:
                raise ValueError('No complete video file was produced (quality or size limit may have excluded it)')
            video.resolve().relative_to(directory)
            result['path'] = str(video.resolve())
        return result


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, **extract(json.loads(sys.stdin.read(64000)))}, ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:1200]}'}))
