"""Loopback-only, token-addressed public-evidence atlas; no arbitrary file routes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from app.intelligence.feeds import ALL_FEEDS
from app.intelligence.view import REGIONS

ASSETS = Path(__file__).resolve().parents[1] / 'assets/intelligence-map'


def video_sources():
    """CSP sources for every allowlisted publisher video host (HLS needs connect + media)."""
    from app.intelligence.providers import MEDIA_HOSTS, MEDIA_PORTS
    sources = set()
    for network, (_images, videos, _pages) in MEDIA_HOSTS.items():
        ports = [port for port in MEDIA_PORTS.get(network, (None,)) if port not in (None, 443)]
        for host in videos:
            sources.add('https://' + host)
            sources.update(f'https://{host}:{port}' for port in ports)
    sources.update({'https://*.googlevideo.com', 'https://cam.pc.cdn.bitgravity.com'})
    return ' '.join(sorted(sources))


VIDEO_SOURCES = video_sources()


class IntelligenceMapServer:
    def __init__(self, manager):
        self.manager = manager
        self.token = secrets.token_hex(24)
        service = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                prefix = '/' + service.token + '/'
                expected = '127.0.0.1:' + str(self.server.server_port)
                if self.headers.get('Host') != expected or not parsed.path.startswith(prefix):
                    self.respond(404, b'Not found', 'text/plain')
                    return
                origin = self.headers.get('Origin')
                if origin and origin != 'http://' + expected:
                    self.respond(403, b'Origin not allowed', 'text/plain')
                    return
                name = parsed.path[len(prefix):]
                query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                try:
                    if name in ('', 'index.html', 'map.css', 'map.js', 'favicon.svg', 'vendor/leaflet.js', 'vendor/leaflet.css', 'vendor/hls.js'):
                        target = ASSETS / (name or 'index.html')
                        mime = 'text/html' if target.suffix == '.html' else 'text/css' if target.suffix == '.css' else 'image/svg+xml' if target.suffix == '.svg' else 'application/javascript'
                        self.respond(200, target.read_bytes(), mime)
                    elif name == 'land':
                        self.respond(200, (ASSETS.parent / 'world-land.json').read_bytes(), 'application/json')
                    elif name == 'config':
                        self.json({'regions': REGIONS, 'feeds': [{'key': feed.key, 'title': feed.title} for feed in ALL_FEEDS]})
                    elif name == 'data':
                        bbox = json.loads(query['bbox']) if query.get('bbox') else None
                        value = manager.dashboard(query=query.get('query', ''), feed=query.get('feed', ''), bbox=bbox,
                                                  limit=1000, offset=int(query.get('offset', '0')), located_only=True)
                        if query.get('media'):
                            cameras = manager.cameras(query=query.get('query', ''), feed=query.get('feed', ''), bbox=bbox,
                                                      limit=1000, offset=int(query.get('offset', '0')), media=query['media'],
                                                      located_only=True)
                            value.update(events=cameras['cameras'], total_matching=cameras['total_matching'],
                                         next_offset=cameras['next_offset'], basis=cameras['basis'])
                        self.json({key: value[key] for key in ('events', 'total_matching', 'next_offset', 'sources', 'monitor', 'aggregates', 'basis')})
                    elif name == 'points':
                        self.json(manager.points(query=query.get('query', ''), feed=query.get('feed', ''),
                                                 media=query.get('media', '')))
                    elif name == 'live':
                        self.json(service.live_layer(query))
                    elif name == 'deep':
                        deep = getattr(service.manager, 'deep', None)
                        if deep is None:
                            raise ValueError('The deep look is not available in this session.')
                        latitude, longitude = float(query.get('lat', 'nan')), float(query.get('lon', 'nan'))
                        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                            raise ValueError('Give a point with lat and lon.')
                        self.json(deep.look(radius_km=float(query.get('radius_km', '25')), reason=query.get('reason') == '1',
                                            latitude=latitude, longitude=longitude))
                    elif name == 'place':
                        self.json(service.place(query.get('name', '')))
                    elif name == 'fires':
                        explore = getattr(service.manager, 'explore', None)
                        if explore is None:
                            raise ValueError('Fire hotspots are not available in this session.')
                        bounds = [float(query.get(key, 'nan')) for key in ('s', 'w', 'n', 'e')]
                        if not all(-180 <= value <= 180 for value in bounds):
                            raise ValueError('Give the map bounds s, w, n, e in degrees.')
                        items = explore.hotspots_in(*bounds, limit=2500)
                        self.json({'items': items, 'count': len(items), 'source': 'NASA FIRMS · VIIRS NOAA-20, last 24 hours'})
                    elif name == 'trade':
                        from app.trade_routes import chokepoints
                        self.json(chokepoints(service.manager, getattr(service.manager, 'markets', None)))
                    elif name == 'iss':
                        self.json(service.require_live().iss(track_minutes=95))
                    elif name == 'around':
                        fusion = getattr(service.manager, 'fusion', None)
                        if fusion is None:
                            raise ValueError('Place fusion is not available in this session.')
                        self.json(fusion.around(latitude=float(query.get('lat', 'nan')), longitude=float(query.get('lon', 'nan')),
                                                radius_km=float(query.get('radius_km', '16'))))
                    elif name == 'resources':
                        self.json(manager.resources(query=query.get('query', '')))
                    elif name == 'objects':
                        self.json(service.tool('intelligence_objects', {'since_hours': 72, 'limit': 50}))
                    elif name == 'correlate':
                        self.json(service.tool('intelligence_correlate', {'object_id': query.get('id', '')}))
                    elif name == 'search':
                        self.json(service.tool('intelligence_search', {'query': query.get('query', ''), 'limit': 30}))
                    elif name == 'event':
                        result = service.tool('intelligence_event', {'event_id': query.get('id', '')})
                        self.json(result)
                    elif name == 'frame':
                        result = service.tool('intelligence_camera_frame', {'event_id': query.get('id', '')})
                        content = Path(result['path']).read_bytes()
                        self.respond(200, content, 'image/png' if content.startswith(b'\x89PNG') else 'image/jpeg',
                                     {'X-Frame-Fetched-At': result['fetched_at'], 'X-Frame-Cached': str(result['cached'])})
                    elif name == 'camera/play':
                        self.json(service.tool('intelligence_camera_play', {'event_id': query.get('id', '')}))
                    else:
                        self.respond(404, b'Not found', 'text/plain')
                except Exception as exc:
                    self.respond(400, json.dumps({'error': str(exc)[:1000]}).encode(), 'application/json')

            def json(self, value):
                self.respond(200, json.dumps(value, allow_nan=False).encode(), 'application/json')

            def respond(self, status, content, mime, extra=None):
                self.send_response(status)
                self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                 "img-src 'self' blob: https://tile.openstreetmap.org https://mesonet.agron.iastate.edu https://gibs.earthdata.nasa.gov; "
                                 f"connect-src 'self' {VIDEO_SOURCES}; media-src 'self' blob: {VIDEO_SOURCES}; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'")
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(content)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.httpd.server_port}/{self.token}/'
        self.thread = threading.Thread(target=self.httpd.serve_forever, name='hayat-world-atlas', daemon=True)
        self.thread.start()

    def place(self, name):
        """Where a typed place is, so the search box can take the map there; found=False when it names none."""
        import re
        name = ' '.join(str(name or '').split())[:120]
        match = re.fullmatch(r'(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)', name)
        if match:
            latitude, longitude = float(match[1]), float(match[2])
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                return {'found': True, 'name': f'{latitude:.4f}, {longitude:.4f}', 'latitude': latitude, 'longitude': longitude}
        if not name:
            return {'found': False}
        fusion = getattr(self.manager, 'fusion', None)
        try:
            if fusion is not None:
                found = fusion.resolve(name)
            else:
                geocoded = self.require_live().geocode(name)
                if not geocoded:
                    return {'found': False}
                found = {'name': ', '.join(part for part in (geocoded['name'], geocoded.get('region'), geocoded.get('country')) if part),
                         'latitude': geocoded['latitude'], 'longitude': geocoded['longitude']}
        except ValueError:
            return {'found': False}
        return {'found': True, 'name': found['name'], 'latitude': found['latitude'], 'longitude': found['longitude']}

    def require_live(self):
        live = getattr(self.manager, 'live', None)
        if live is None:
            raise ValueError('Live layers are not available in this session.')
        return live

    #: Live map layers: what each draws, and the widest radius one request may ask for.
    LIVE_LAYERS = {'aircraft': 460.0, 'vessels': 300.0, 'transit': 40.0}

    def live_layer(self, query):
        """Moving things around the map centre for the atlas's live layers (rate-limited and cached upstream)."""
        kind = query.get('kind', '')
        if kind not in self.LIVE_LAYERS:
            raise ValueError('kind must be aircraft, vessels or transit.')
        latitude, longitude = float(query.get('lat', 'nan')), float(query.get('lon', 'nan'))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError('lat and lon must be a real map position.')
        radius = max(1.0, min(float(query.get('radius_km', '100')), self.LIVE_LAYERS[kind]))
        items = getattr(self.require_live(), kind)(latitude, longitude, radius)
        return {'kind': kind, 'items': items[:500], 'count': len(items), 'radius_km': radius,
                'source': {'aircraft': 'adsb.lol (ADS-B)', 'vessels': 'Fintraffic Digitraffic AIS (Baltic)',
                           'transit': 'MBTA; Digitraffic trains (Finland)'}[kind]}

    def tool(self, name, arguments):
        if self.manager.registry is None:
            raise ValueError('Camera/evidence actions require the normal tool registry.')
        from app.gui_features import run_declared_tool
        result = run_declared_tool(SimpleNamespace(registry=self.manager.registry), name, arguments)
        if not result.success:
            raise ValueError(result.error)
        return result.output

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
