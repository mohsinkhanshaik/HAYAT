"""Read-only, loopback video result links. Never exposes the chat or filesystem."""
from __future__ import annotations

import html
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class VideoResultServer:
    def __init__(self):
        self._results = {}
        self._lock = threading.RLock()
        self._server = None

    def share(self, path: Path, title: str = '', note: str = '') -> str:
        with self._lock:
            if self._server is None:
                owner = self

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        owner._respond(self)

                    def do_HEAD(self):
                        owner._respond(self, head=True)

                    def log_message(self, *_args):
                        pass

                self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                self._server.daemon_threads = True
                threading.Thread(target=self._server.serve_forever, name='hayat-result-links', daemon=True).start()
            # A link admits exactly one immutable file identity, not a directory.
            token = secrets.token_urlsafe(24)
            stat = path.stat()
            self._results[token] = (path, stat.st_size, stat.st_mtime_ns, title[:120], note[:1500])
            while len(self._results) > 32:
                del self._results[next(iter(self._results))]
            return f'http://127.0.0.1:{self._server.server_port}/result/{token}'

    def _respond(self, request, head=False):
        # Reject DNS rebinding and cross-site embedding of local media.
        server = self._server
        if server is None:
            request.send_error(503)
            return
        expected_host = f'127.0.0.1:{server.server_port}'
        if request.headers.get('Host') != expected_host:
            request.send_error(403)
            return
        route = urlsplit(request.path).path.strip('/').split('/')
        if len(route) not in (2, 3) or route[0] != 'result' or len(route) == 3 and route[2] != 'video':
            request.send_error(404)
            return
        with self._lock:
            item = self._results.get(route[1])
        if item is None:
            request.send_error(404)
            return
        path, size, modified, title, note = item
        try:
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (size, modified):
                request.send_error(410, 'Video changed; create a new result link.')
                return
            if len(route) == 2:
                title = html.escape(title or 'HAYAT video result')
                payload = (f'<!doctype html><html lang="en"><meta charset="utf-8">'
                           f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>'
                           '<style>body{margin:0;background:#0d1119;color:#eef2fa;font:16px system-ui}'
                           'main{max-width:900px;margin:40px auto;padding:24px}header{color:#74dfb4}'
                           'section{background:#18202e;border:1px solid #344158;border-radius:18px;padding:24px}'
                           'video{width:100%;max-height:65vh;border-radius:12px;background:#000}'
                           'a{display:inline-block;color:#0d1119;background:#74dfb4;border-radius:8px;'
                           'padding:12px 20px;text-decoration:none}p{line-height:1.6;white-space:pre-wrap}'
                           'small{color:#aab6ca}</style><main><header>HAYAT · VIDEO STUDIO</header>'
                           f'<h1>{title}</h1><section><video controls preload="metadata" src="{route[1]}/video"></video>'
                           f'<p>{html.escape(note)}</p><a href="{route[1]}/video?download=1" download="video{html.escape(path.suffix.lower())}">Download video</a>'
                           '<p><small>This local result link works on this computer while HAYAT is running. '
                           'Editorial potential does not guarantee views or trends.</small></p></section></main></html>').encode()
                self._headers(request, 200, 'text/html; charset=utf-8', len(payload))
                if not head:
                    request.wfile.write(payload)
                return
            start, stop, status = 0, size - 1, 200
            range_value = request.headers.get('Range')
            if range_value:
                if len(range_value) > 128:
                    self._range_error(request, size)
                    return
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_value)
                if not match or not any(match.groups()):
                    self._range_error(request, size)
                    return
                if match[1]:
                    start = int(match[1])
                    stop = min(size - 1, int(match[2])) if match[2] else size - 1
                else:
                    start = max(0, size - int(match[2]))
                if start > stop or start >= size or not size or not match[1] and int(match[2]) == 0:
                    self._range_error(request, size)
                    return
                status = 206
            content_type = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime'}.get(path.suffix.lower(), 'application/octet-stream')
            headers = {'Accept-Ranges': 'bytes'}
            if status == 206:
                headers['Content-Range'] = f'bytes {start}-{stop}/{size}'
            if urlsplit(request.path).query == 'download=1':
                headers['Content-Disposition'] = 'attachment; filename="video' + path.suffix.lower() + '"'
            # Open before headers so missing/unreadable media returns an error.
            with path.open('rb') as source:
                self._headers(request, status, content_type, stop - start + 1, headers)
                if not head:
                    source.seek(start)
                    remaining = stop - start + 1
                    while remaining:
                        block = source.read(min(256 * 1024, remaining))
                        if not block:
                            break
                        request.wfile.write(block)
                        remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            request.send_error(410, 'Video is no longer available.')

    @staticmethod
    def _headers(request, status, kind, length, extra=None):
        request.send_response(status)
        request.send_header('Content-Type', kind)
        request.send_header('Content-Length', str(length))
        request.send_header('Cache-Control', 'no-store')
        request.send_header('X-Content-Type-Options', 'nosniff')
        request.send_header('Referrer-Policy', 'no-referrer')
        request.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        request.send_header('Content-Security-Policy', "default-src 'none'; media-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
        for name, value in (extra or {}).items():
            request.send_header(name, value)
        request.end_headers()

    def _range_error(self, request, size):
        self._headers(request, 416, 'text/plain', 0, {'Content-Range': f'bytes */{size}'})

    def close(self):
        with self._lock:
            server, self._server = self._server, None
            self._results.clear()
        if server:
            server.shutdown()
            server.server_close()
