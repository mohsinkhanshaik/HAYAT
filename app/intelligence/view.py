"""Offline region presets, persisted display choices and reversible map geometry."""
from dataclasses import dataclass
import math

from app.intelligence.feeds import BY_KEY, finite


REGIONS = {
    'World': [-180, -90, 180, 90],
    'North America': [-170, 5, -50, 75],
    'South America': [-85, -60, -30, 15],
    'Europe': [-25, 34, 45, 72],
    'Africa': [-20, -36, 55, 38],
    'Middle East': [25, 10, 65, 45],
    'South Asia': [60, 0, 100, 40],
    'East Asia': [95, 5, 155, 60],
    'Oceania': [110, -55, -150, 15],
}
TIME_RANGES = {'Any time': None, 'Last hour': 1, 'Last 6 hours': 6, 'Last 24 hours': 24,
               'Last 7 days': 168, 'Last 30 days': 720}
DEFAULT_VIEW = {'show_home': True, 'home_alert_limit': 3, 'home_detail': 'compact',
                'region': 'World', 'layer': '', 'query': '', 'zoom': 1.0,
                'center_lon': 0.0, 'center_lat': 0.0, 'show_alerts': True,
                'only_viewport': False, 'category': '', 'min_severity': '', 'time_range': 'Any time',
                'hidden_layers': ['cameras']}


def relative_time(value, moment=None):
    """'5 min ago' style age of an ISO timestamp; empty when unknown."""
    from datetime import UTC, datetime
    from app.intelligence.feeds import timestamp
    stamp = timestamp(value)
    if not stamp:
        return ''
    seconds = ((moment or datetime.now(UTC)) - datetime.fromisoformat(stamp)).total_seconds()
    if seconds < 0:
        return 'upcoming' if seconds < -120 else 'just now'
    for size, unit in ((86400 * 365, 'y'), (86400 * 30, 'mo'), (86400, 'd'), (3600, 'h'), (60, 'min')):
        if seconds >= size:
            return f'{int(seconds // size)} {unit} ago'
    return 'just now'


def format_point(latitude, longitude):
    return f"{abs(latitude):.3f}°{'N' if latitude >= 0 else 'S'}, {abs(longitude):.3f}°{'E' if longitude >= 0 else 'W'}"


def validate_view(data):
    from app.intelligence.classify import CATEGORIES, SEVERITY_RANK
    if not isinstance(data, dict) or set(data) - set(DEFAULT_VIEW):
        raise ValueError('Use only registered intelligence display options.')
    if 'category' in data and data['category'] not in ('', *CATEGORIES):
        raise ValueError('Choose a registered event category.')
    if 'min_severity' in data and data['min_severity'] not in ('', *SEVERITY_RANK):
        raise ValueError('Choose a registered minimum severity.')
    if 'time_range' in data and data['time_range'] not in TIME_RANGES:
        raise ValueError('Choose a registered time range.')
    if 'hidden_layers' in data and (not isinstance(data['hidden_layers'], list)
                                    or any(item not in CATEGORIES for item in data['hidden_layers'])):
        raise ValueError('Hidden map layers must be registered categories.')
    for key in ('show_home', 'show_alerts', 'only_viewport'):
        if key in data and type(data[key]) is not bool:
            raise ValueError(key + ' must be true or false.')
    if 'home_alert_limit' in data and (type(data['home_alert_limit']) is not int or not 0 <= data['home_alert_limit'] <= 5):
        raise ValueError('Show 0–5 front-page alerts.')
    if 'home_detail' in data and data['home_detail'] not in ('compact', 'detailed'):
        raise ValueError('Choose compact or detailed home cards.')
    if 'region' in data and (not isinstance(data['region'], str) or data['region'] not in REGIONS):
        raise ValueError('Choose a registered region preset.')
    if 'layer' in data and data['layer'] not in ('', *BY_KEY):
        raise ValueError('Choose a registered layer.')
    if 'query' in data and (not isinstance(data['query'], str) or len(data['query']) > 500):
        raise ValueError('Search text must be at most 500 characters.')
    result = dict(data)
    for key, low, high in (('zoom', 1, 32), ('center_lon', -180, 180), ('center_lat', -90, 90)):
        if key in data:
            number = finite(data[key])
            if number is None or not low <= number <= high:
                raise ValueError(f'{key} must be a finite number between {low} and {high}.')
            result[key] = number
    return result


def brief_event(row):
    details = row.get('details') or {}
    facts = [row['title'], f"Layer: {row['feed']} · Event time: {row.get('observed_at') or 'unknown'}"]
    if row.get('severity'):
        facts.append(f"Severity: {row['severity'].capitalize()} ({row.get('category_title', '')}) — {row.get('severity_basis', '')}")
    if row.get('latitude') is not None:
        facts.append(f"Location: {row['latitude']:.3f}, {row['longitude']:.3f}")
    else:
        facts.append('Location: not supplied; this item is not plotted.')
    for key, label in (('magnitude', 'Magnitude'), ('depth_km', 'Depth (km)'), ('vendor', 'Vendor'),
                       ('product', 'Product'), ('description', 'Description'), ('required_action', 'Provider action'),
                       ('due_date', 'Provider due date'), ('message', 'Bulletin')):
        if details.get(key) is not None and details[key] != '':
            facts.append(f'{label}: {details[key]}')
    for key in ('provider', 'country', 'city', 'state', 'alert_level', 'severity', 'location_basis', 'available', 'direction', 'media_kind', 'catalog_modified_at', 'limitation'):
        if details.get(key) is not None:
            facts.append(f"{key.replace('_', ' ').capitalize()}: {details[key]}")
    if row.get('stale'):
        facts.append('Source is stale; the last successful observation is retained.')
    facts.extend([f"Last fetched: {row.get('retrieved_at') or 'unknown'}", 'Source: ' + row['url']])
    return '\n\n'.join(facts)


@dataclass
class MapViewport:
    zoom: float = 1.0
    longitude: float = 0.0
    latitude: float = 0.0

    def clamp(self):
        self.zoom = max(1, min(32, self.zoom))
        self.longitude = (self.longitude + 180) % 360 - 180
        edge = 90 - 90 / self.zoom
        self.latitude = max(-edge, min(edge, self.latitude))

    def project(self, lon, lat, width, height, *, wrap=True):
        delta = lon - self.longitude
        if wrap:
            delta = (delta + 180) % 360 - 180
        return width / 2 + delta / 360 * width * self.zoom, height / 2 + (self.latitude - lat) / 180 * height * self.zoom

    def unproject(self, x, y, width, height):
        lon = self.longitude + (x / max(1, width) - .5) * 360 / self.zoom
        lat = self.latitude - (y / max(1, height) - .5) * 180 / self.zoom
        return (lon + 180) % 360 - 180, max(-90, min(90, lat))

    def zoom_at(self, factor, x, y, width, height):
        lon, lat = self.unproject(x, y, width, height)
        self.zoom = max(1, min(32, self.zoom * factor))
        self.longitude = lon - (x / max(1, width) - .5) * 360 / self.zoom
        self.latitude = lat + (y / max(1, height) - .5) * 180 / self.zoom
        self.clamp()

    def pan(self, dx, dy, width, height):
        self.longitude -= dx / max(1, width) * 360 / self.zoom
        self.latitude += dy / max(1, height) * 180 / self.zoom
        self.clamp()

    def fit(self, bbox):
        west, south, east, north = bbox
        span = east - west if east >= west else 360 + east - west
        self.longitude = west + span / 2
        self.latitude = (south + north) / 2
        self.zoom = min(360 / max(1, span), 180 / max(1, north - south)) * .9
        self.clamp()

    def fit_points(self, rows):
        """Fit the shortest longitude arc, so Pacific results stay together."""
        points = [(finite(row.get('longitude')), finite(row.get('latitude'))) for row in rows]
        points = [(lon, lat) for lon, lat in points if lon is not None and lat is not None
                  and -180 <= lon <= 180 and -90 <= lat <= 90]
        if not points:
            return False
        lons = sorted(set(lon % 360 for lon, _lat in points))
        gaps = [((lons[(index+1) % len(lons)] - lon) % 360, index) for index, lon in enumerate(lons)]
        _gap, index = max(gaps)
        west, east = lons[(index+1) % len(lons)], lons[index]
        west, east = (west+180) % 360-180, (east+180) % 360-180
        self.fit([west, min(lat for _lon, lat in points), east, max(lat for _lon, lat in points)])
        return True

    def bounds(self):
        if self.zoom == 1:
            return [-180, -90, 180, 90]
        west = (self.longitude - 180 / self.zoom + 180) % 360 - 180
        east = (self.longitude + 180 / self.zoom + 180) % 360 - 180
        return [west, max(-90, self.latitude - 90 / self.zoom), east, min(90, self.latitude + 90 / self.zoom)]


def distance_km(left, right):
    if any(row.get('latitude') is None or row.get('longitude') is None for row in (left, right)):
        return None
    lat1, lat2 = map(math.radians, (left['latitude'], right['latitude']))
    dlat = lat2 - lat1
    dlon = math.radians(right['longitude'] - left['longitude'])
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(max(0, min(1, value))))
