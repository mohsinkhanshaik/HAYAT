"""The Earth behind the Global intelligence globe: camera, sun, surface and countries (no Tk).

Everything drawn is measured or labelled for what it is (2026-11-08):

* the surface is NASA's cloud-free Blue Marble composite, and the night side shows NASA's 2012 Black
  Marble city lights (both public domain, bundled in app/assets/earth so the globe works offline);
* the line between day and night is the sun's real position for the moment drawn, from NOAA's
  approximate solar-position equations;
* country outlines, names, population and GDP are Natural Earth's (public domain), each with its year.

Rendering is numpy on the CPU into one image, only when the view changes. A still globe costs
nothing and no GPU memory is used, so it never competes with a local model for the graphics card.
"""
from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ASSETS = Path(__file__).parent / 'assets' / 'earth'
LAND = Path(__file__).parent / 'assets' / 'world-land.json'
SPACE_INNER, SPACE_OUTER = (12, 24, 38), (3, 6, 11)
ATMOSPHERE = np.array([92, 168, 255], dtype=np.float32)
CITY_LIGHT = np.array([255, 205, 140], dtype=np.float32)
#: How far the glow reaches past the edge of the planet, as a share of its radius.
GLOW = 0.075
#: Brightness kept on the night side so land stays readable; city lights are added on top.
NIGHT_FLOOR = 0.17
#: Shares of the canvas the still and moving image are computed at, per rendering preset.
QUALITY = {
    'performance': {'still': .6, 'moving': .35, 'bilinear': False, 'supersample': 1.0},
    'balanced': {'still': 1.0, 'moving': .5, 'bilinear': None, 'supersample': 1.0},
    'ultra': {'still': 1.0, 'moving': .66, 'bilinear': True, 'supersample': 1.4},
}
QUALITY_LABELS = {'auto': 'Automatic', 'performance': 'Performance', 'balanced': 'Balanced', 'ultra': 'Ultra'}
#: Country colouring layers: label, unit, and how the figure is worked out.
COUNTRY_LAYERS = {
    'density': ('People per km²', 'people per km²',
                'Natural Earth population estimate ÷ the outline\'s area on this globe'),
    'gdp_per_person': ('GDP per person', 'US$ per person', 'Natural Earth GDP estimate ÷ population estimate'),
    'income': ('Income group', '', 'World Bank income group as recorded by Natural Earth'),
}
INCOME_ORDER = ('Low income', 'Lower middle income', 'Upper middle income', 'High income: nonOECD', 'High income: OECD')
#: Sequential colours readable on the dark globe (light = more), never the only cue: the legend lists ranges.
SEQUENTIAL = ((48, 38, 112), (38, 92, 160), (30, 150, 155), (96, 196, 106), (232, 222, 90))


# ------------------------------------------------------------------------------------------ the sun

def subsolar_point(moment: datetime | None = None) -> tuple[float, float]:
    """(latitude, longitude) where the sun is overhead, NOAA's approximation (about 0.2° accuracy)."""
    moment = (moment or datetime.now(UTC)).astimezone(UTC)
    start = datetime(moment.year, 1, 1, tzinfo=UTC)
    days = 366 if moment.year % 4 == 0 and (moment.year % 100 or moment.year % 400 == 0) else 365
    hours = moment.hour + moment.minute / 60 + moment.second / 3600
    gamma = 2 * math.pi / days * ((moment - start).days + (hours - 12) / 24)
    equation = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                         - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    declination = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
                   - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
                   - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    longitude = -15 * (hours - 12 + equation / 60)
    return math.degrees(declination), (longitude + 180) % 360 - 180


# --------------------------------------------------------------------------------------- the camera

@dataclass
class GlobeCamera:
    """Which point faces the viewer and how close the viewer is (1 = the whole planet fits)."""
    longitude: float = 50.0
    latitude: float = 20.0
    zoom: float = 1.0
    MIN_ZOOM = .8
    MAX_ZOOM = 48.0

    def clamp(self) -> 'GlobeCamera':
        self.longitude = (self.longitude + 180) % 360 - 180
        self.latitude = max(-82.0, min(82.0, self.latitude))
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom))
        return self

    def copy(self) -> 'GlobeCamera':
        return GlobeCamera(self.longitude, self.latitude, self.zoom)

    @staticmethod
    def base_radius(width: float, height: float) -> float:
        return .42 * max(1.0, min(width, height))

    def radius(self, width: float, height: float) -> float:
        return self.base_radius(width, height) * self.zoom

    def project_many(self, lons, lats, width, height):
        """Screen x, y and depth (1 at the centre of the face, 0 at the edge, below 0 behind) for arrays."""
        lam, phi = np.radians(np.asarray(lons, dtype=np.float64)), np.radians(np.asarray(lats, dtype=np.float64))
        lam0, phi0 = math.radians(self.longitude), math.radians(self.latitude)
        radius = self.radius(width, height)
        cos_phi = np.cos(phi)
        delta = lam - lam0
        x = cos_phi * np.sin(delta)
        y = math.cos(phi0) * np.sin(phi) - math.sin(phi0) * cos_phi * np.cos(delta)
        depth = math.sin(phi0) * np.sin(phi) + math.cos(phi0) * cos_phi * np.cos(delta)
        return width / 2 + x * radius, height / 2 - y * radius, depth

    def project(self, lon: float, lat: float, width: float, height: float) -> tuple[float, float, float]:
        x, y, depth = self.project_many([lon], [lat], width, height)
        return float(x[0]), float(y[0]), float(depth[0])

    def unproject(self, x: float, y: float, width: float, height: float) -> tuple[float, float] | None:
        """(lon, lat) under a screen point, or None when the point is off the planet."""
        radius = self.radius(width, height)
        u, v = (x - width / 2) / radius, (height / 2 - y) / radius
        r2 = u * u + v * v
        if r2 > 1:
            return None
        z = math.sqrt(1 - r2)
        phi0, lam0 = math.radians(self.latitude), math.radians(self.longitude)
        lat = math.degrees(math.asin(max(-1.0, min(1.0, v * math.cos(phi0) + z * math.sin(phi0)))))
        lon = math.degrees(lam0 + math.atan2(u, z * math.cos(phi0) - v * math.sin(phi0)))
        return (lon + 180) % 360 - 180, lat

    def visible_half_angle(self, width: float, height: float) -> float:
        """Degrees from the centre of the view to the nearest canvas edge, as measured on the planet."""
        reach = min(width, height) / 2 / self.radius(width, height)
        return 90.0 if reach >= 1 else math.degrees(math.asin(reach))

    def across_km(self, width: float, height: float) -> float:
        reach = min(1.0, width / 2 / self.radius(width, height))
        return 2 * math.asin(reach) * 6371.0

    def drag(self, dx: float, dy: float, width: float, height: float) -> None:
        """Turn the planet so the surface follows the pointer at the centre of the view."""
        degrees = math.degrees(1 / self.radius(width, height))
        self.longitude -= dx * degrees / max(.3, math.cos(math.radians(self.latitude)))
        self.latitude += dy * degrees
        self.clamp()

    def zoom_at(self, factor: float, x: float, y: float, width: float, height: float) -> None:
        """Zoom, and on the way in drift toward the point under the pointer (as a map would)."""
        point = self.unproject(x, y, width, height) if factor > 1 else None
        self.zoom *= factor
        if point:
            share = 1 - 1 / factor
            self.longitude += ((point[0] - self.longitude + 180) % 360 - 180) * share
            self.latitude += (point[1] - self.latitude) * share
        self.clamp()

    def fit(self, bbox, width: float, height: float) -> None:
        """Face the middle of [west, south, east, north] and come close enough to fill most of the view."""
        west, south, east, north = bbox
        span = east - west if east >= west else 360 + east - west
        self.longitude = west + span / 2
        self.latitude = (south + north) / 2
        across = max(span * math.cos(math.radians(self.latitude)), north - south, .5)
        if across >= 150:
            self.zoom = 1.0
        else:
            reach = math.sin(math.radians(across / 2) * 1.12)
            self.zoom = max(1.0, .5 * min(width, height) / (self.base_radius(width, height) * max(.01, reach)))
        self.clamp()

    def bounds(self, width: float, height: float) -> list[float]:
        """[west, south, east, north] around what is visible (the whole world when the poles show)."""
        radius = self.radius(width, height)
        across = lambda pixels: 90.0 if pixels / 2 >= radius else math.degrees(math.asin(pixels / 2 / radius))
        half_y, half_x = across(height), across(width)
        south, north = self.latitude - half_y, self.latitude + half_y
        if north >= 89 or south <= -89:
            return [-180.0, max(-90.0, south), 180.0, min(90.0, north)]
        reach = min(180.0, half_x / max(.05, math.cos(math.radians(max(abs(south), abs(north))))))
        west, east = ((self.longitude - reach + 180) % 360 - 180, (self.longitude + reach + 180) % 360 - 180)
        return [-180.0, south, 180.0, north] if reach >= 180 else [west, south, east, north]

    def flat_zoom(self, width: float, height: float) -> float:
        """The flat map zoom that shows about the same width of the world."""
        across = 2 * math.degrees(math.asin(min(1.0, width / 2 / self.radius(width, height))))
        return max(1.0, min(32.0, 360 / max(1.0, across / max(.2, math.cos(math.radians(self.latitude))) * 1.0)))

    @classmethod
    def from_flat(cls, longitude: float, latitude: float, zoom: float, width: float, height: float) -> 'GlobeCamera':
        camera = cls(longitude, latitude, 1.0)
        span = 360 / max(1.0, zoom) * max(.2, math.cos(math.radians(latitude)))
        if span < 170:
            reach = math.sin(math.radians(span / 2))
            camera.zoom = max(1.0, width / 2 / (cls.base_radius(width, height) * max(.01, reach)))
        return camera.clamp()


def ease(fraction: float) -> float:
    """Ease in and out (cubic), so a flight starts and lands gently."""
    fraction = max(0.0, min(1.0, fraction))
    return 4 * fraction ** 3 if fraction < .5 else 1 - (-2 * fraction + 2) ** 3 / 2


def between(start: GlobeCamera, end: GlobeCamera, fraction: float) -> GlobeCamera:
    """A camera part of the way along the shortest turn from start to end; zoom changes geometrically."""
    t = ease(fraction)
    turn = (end.longitude - start.longitude + 180) % 360 - 180
    zoom = math.exp(math.log(start.zoom) + (math.log(end.zoom) - math.log(start.zoom)) * t)
    # Pull back a little mid-flight on long journeys, so the eye keeps its bearings.
    distance = math.hypot(turn, end.latitude - start.latitude)
    if distance > 25:
        zoom /= 1 + .35 * math.sin(math.pi * t) * min(1.0, distance / 120)
    return GlobeCamera(start.longitude + turn * t, start.latitude + (end.latitude - start.latitude) * t, zoom).clamp()


# ----------------------------------------------------------------------------- surface and countries

class Earth:
    """The bundled imagery and countries, loaded once per process and shared by every window."""

    _lock = threading.Lock()
    _shared: 'Earth | None' = None

    def __init__(self, assets: Path = ASSETS) -> None:
        self.assets = Path(assets)
        self.problem = ''
        self.imagery = True
        try:
            self.day = np.asarray(Image.open(self.assets / 'earth-day.jpg').convert('RGB'))
            self.night = np.asarray(Image.open(self.assets / 'earth-night.jpg').convert('L'))
        except (OSError, ValueError) as exc:
            self.problem = f'Bundled imagery unavailable ({type(exc).__name__}); drawing land from Natural Earth outlines.'
            self.imagery = False
            self.day, self.night = self._land_texture(), None
        self.countries: list[dict] = []
        self.country_source = ''
        try:
            data = json.loads((self.assets / 'countries.json').read_text(encoding='utf-8'))
            self.countries = data['countries']
            self.country_source = data.get('license', '')
        except (OSError, ValueError, KeyError) as exc:
            self.problem = (self.problem + ' ' if self.problem else '') + f'Country outlines unavailable ({type(exc).__name__}).'
        # Smaller copies for a far view: sampling the full image at a fraction of its size shimmers.
        self.day_levels = self._levels(self.day)
        self.night_levels = self._levels(self.night) if self.night is not None else None
        self.raster = self._country_raster()
        self._areas()
        self._outline_arrays()

    @staticmethod
    def _levels(texture: np.ndarray) -> list[np.ndarray]:
        levels, image = [texture], Image.fromarray(texture)
        while image.width > 900:
            image = image.resize((image.width // 2, image.height // 2), Image.BOX)
            levels.append(np.asarray(image))
        return levels

    def level(self, levels: list[np.ndarray], circumference: float) -> np.ndarray:
        """The smallest copy still at least 80% as wide as the planet's circumference on screen."""
        chosen = levels[0]
        for texture in levels[1:]:
            if texture.shape[1] >= circumference * .8:
                chosen = texture
        return chosen

    @classmethod
    def shared(cls) -> 'Earth':
        with cls._lock:
            if cls._shared is None:
                cls._shared = cls()
            return cls._shared

    # The offline fallback when the imagery files are missing: Natural Earth land on a plain ocean.
    @staticmethod
    def _land_texture(width: int = 2048, height: int = 1024) -> np.ndarray:
        image = Image.new('RGB', (width, height), (14, 40, 72))
        draw = ImageDraw.Draw(image)
        try:
            polygons = json.loads(LAND.read_text(encoding='utf-8'))['polygons']
        except (OSError, ValueError, KeyError):
            polygons = []
        for polygon in polygons:
            draw.polygon([((lon + 180) / 360 * width, (90 - lat) / 180 * height) for lon, lat in polygon], fill=(58, 82, 66))
        return np.asarray(image)

    def _country_raster(self, width: int = 2048, height: int = 1024) -> np.ndarray:
        """Each pixel holds 1 + the country's index (0 = sea or unclaimed): instant picking and colouring."""
        image = Image.new('L' if len(self.countries) < 255 else 'I', (width, height), 0)
        draw = ImageDraw.Draw(image)
        point = lambda lon, lat: ((lon + 180) / 360 * width, (90 - lat) / 180 * height)
        for index, country in enumerate(self.countries):
            for ring, holes in zip(country['polygons'], country.get('holes') or [[]] * len(country['polygons'])):
                if len(ring) >= 3:
                    draw.polygon([point(lon, lat) for lon, lat in ring], fill=index + 1)
                for hole in holes:
                    if len(hole) >= 3:
                        draw.polygon([point(lon, lat) for lon, lat in hole], fill=0)
        return np.asarray(image)

    def _areas(self) -> None:
        """Each country's area in km², summed from the raster (cos-latitude weighted)."""
        height, width = self.raster.shape
        lat = np.radians(90 - (np.arange(height) + .5) / height * 180)
        cell = (2 * math.pi / width) * (math.pi / height) * 6371.0 ** 2 * np.cos(lat)
        weights = np.repeat(cell[:, None], width, axis=1)
        sums = np.bincount(self.raster.ravel().astype(np.int64), weights=weights.ravel(), minlength=len(self.countries) + 1)
        for index, country in enumerate(self.countries):
            country['area_km2'] = float(sums[index + 1])

    def _outline_arrays(self) -> None:
        """All rings as flat arrays, with where each ring starts and which country it belongs to."""
        lons, lats, starts, owners = [], [], [], []
        for index, country in enumerate(self.countries):
            for ring in country['polygons']:
                starts.append(len(lons))
                owners.append(index)
                for lon, lat in ring:
                    lons.append(lon)
                    lats.append(lat)
        self.ring_lons, self.ring_lats = np.array(lons, dtype=np.float64), np.array(lats, dtype=np.float64)
        self.ring_starts = np.array(starts + [len(lons)], dtype=np.int64)
        self.ring_owners = np.array(owners, dtype=np.int64)

    def country_at(self, lon: float, lat: float) -> int | None:
        height, width = self.raster.shape
        column = int((lon + 180) / 360 * width) % width
        row = min(height - 1, max(0, int((90 - lat) / 180 * height)))
        value = int(self.raster[row, column])
        return value - 1 if value else None

    def country_bbox(self, index: int) -> list[float]:
        """[west, south, east, north] of the country's main land, across the date line when needed.

        Overseas parts far from the country's label point are left out: France's outline includes
        French Guiana, and fitting all of it flew the view to the middle of the Atlantic.
        """
        country = self.countries[index]
        label_lon, label_lat = country.get('label') or country['polygons'][0][0]

        def size(ring):
            lat = sum(point[1] for point in ring) / len(ring)
            area = 0.0
            for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
                area += x1 * y2 - x2 * y1
            return abs(area) * math.cos(math.radians(lat))

        def near(ring):
            lon = sum(point[0] for point in ring) / len(ring)
            lat = sum(point[1] for point in ring) / len(ring)
            turn = math.radians((lon - label_lon + 180) % 360 - 180)
            a, b = math.radians(lat), math.radians(label_lat)
            return math.degrees(math.acos(max(-1.0, min(1.0, math.sin(a) * math.sin(b)
                                                          + math.cos(a) * math.cos(b) * math.cos(turn))))) < 30

        rings = [ring for ring in country['polygons'] if len(ring) >= 3]
        largest = max(rings, key=size)
        points = [point for ring in rings if ring is largest or near(ring) for point in ring]
        south, north = min(lat for _lon, lat in points), max(lat for _lon, lat in points)
        lons = sorted({lon % 360 for lon, _lat in points})
        gaps = [((lons[(i + 1) % len(lons)] - lon) % 360, i) for i, lon in enumerate(lons)]
        _gap, i = max(gaps)
        west, east = lons[(i + 1) % len(lons)], lons[i]
        return [(west + 180) % 360 - 180, south, (east + 180) % 360 - 180, north]

    def find(self, text: str) -> list[int]:
        """Countries whose name starts with (then contains) the text, ignoring case."""
        needle = ' '.join(str(text).split()).casefold()
        if not needle:
            return []
        names = [(index, (country['name'].casefold(), country['long_name'].casefold(), country['code'].casefold()))
                 for index, country in enumerate(self.countries)]
        exact = [index for index, keys in names if needle in keys]
        starts = [index for index, keys in names if index not in exact and any(key.startswith(needle) for key in keys)]
        within = [index for index, keys in names if index not in exact and index not in starts and any(needle in key for key in keys)]
        return exact + starts + within

    # ------------------------------------------------------------------- country colouring layers

    def layer_value(self, layer: str, country: dict):
        if layer == 'density':
            area = country.get('area_km2') or 0
            return country['population'] / area if area > 50 and country.get('population', 0) > 0 else None
        if layer == 'gdp_per_person':
            people = country.get('population') or 0
            return country['gdp_musd'] * 1e6 / people if people > 0 and country.get('gdp_musd', 0) > 0 else None
        if layer == 'income':
            group = country.get('income_group')
            return INCOME_ORDER.index(group) if group in INCOME_ORDER else None
        raise ValueError('Unknown country layer.')

    def layer_classes(self, layer: str) -> tuple[list[float], list[str]]:
        """Class edges and legend labels: quintiles for numbers, the five groups for income."""
        if layer == 'income':
            return [0, 1, 2, 3, 4], ['Low', 'Lower middle', 'Upper middle', 'High (non-OECD)', 'High (OECD)']
        values = sorted(value for country in self.countries if (value := self.layer_value(layer, country)) is not None)
        edges = [values[min(len(values) - 1, int(len(values) * share))] for share in (.2, .4, .6, .8)] if values else []
        short = (lambda value: f'{value:,.0f}') if layer == 'density' else (lambda value: f'${value / 1000:,.1f}k' if value >= 1000 else f'${value:,.0f}')
        bounds = [None, *edges, None]
        labels = [(f'under {short(bounds[1])}' if low is None else f'{short(low)}+' if high is None else f'{short(low)}–{short(high)}')
                  for low, high in zip(bounds[:-1], bounds[1:])] if edges else []
        return edges, labels

    def layer_table(self, layer: str | None, highlight: int | None = None, selected: int | None = None):
        """(lookup table of RGBA per raster value, legend) for the renderer, or (None, None)."""
        if not layer and highlight is None and selected is None:
            return None, None
        table = np.zeros((max(256, len(self.countries) + 1), 4), dtype=np.float32)
        legend = None
        if layer:
            edges, labels = self.layer_classes(layer)
            for index, country in enumerate(self.countries):
                value = self.layer_value(layer, country)
                if value is None:
                    continue
                band = int(value) if layer == 'income' else sum(value >= edge for edge in edges)
                table[index + 1] = (*SEQUENTIAL[min(band, 4)], .58)
            title, unit, basis = COUNTRY_LAYERS[layer]
            years = sorted({country.get('population_year' if layer == 'density' else 'gdp_year') for country in self.countries} - {None})
            legend = {'title': title, 'unit': unit, 'basis': basis, 'labels': labels, 'colours': SEQUENTIAL[:len(labels)],
                      'years': f'{years[0]}–{years[-1]}' if len(years) > 1 else str(years[0]) if years else ''}
        if selected is not None:
            table[selected + 1] = (255, 214, 102, .42)
        return table, legend


# ------------------------------------------------------------------------------------- the renderer

class EarthRenderer:
    """Draws the planet into an RGB image of the canvas, background and atmosphere included."""

    def __init__(self, earth: Earth | None = None) -> None:
        self.earth = earth or Earth.shared()
        self._backgrounds: dict[tuple[int, int], np.ndarray] = {}
        self.last_ms = 0.0
        self.cost_per_megapixel = None  # milliseconds, measured on this computer

    def _background(self, width: int, height: int) -> np.ndarray:
        key = (width, height)
        if key not in self._backgrounds:
            if len(self._backgrounds) > 6:
                self._backgrounds.clear()
            ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
            distance = np.hypot((xs - width / 2) / max(1, width), (ys - height / 2) / max(1, height)) * 1.6
            share = np.clip(distance, 0, 1)[..., None]
            inner, outer = np.array(SPACE_INNER, np.float32), np.array(SPACE_OUTER, np.float32)
            self._backgrounds[key] = (inner * (1 - share) + outer * share).astype(np.uint8)
        return self._backgrounds[key]

    def render(self, camera: GlobeCamera, width: int, height: int, *, scale: float = 1.0, lighting: bool = True,
               moment: datetime | None = None, table: np.ndarray | None = None, bilinear: bool = False) -> Image.Image:
        started = time.perf_counter()
        w, h = max(8, int(width * scale)), max(8, int(height * scale))
        image = self._background(w, h).copy()
        radius = camera.radius(width, height) * scale
        cx, cy = w / 2, h / 2
        reach = radius * (1 + GLOW)
        x0, x1 = max(0, int(cx - reach)), min(w, int(cx + reach) + 1)
        y0, y1 = max(0, int(cy - reach)), min(h, int(cy + reach) + 1)
        if x1 > x0 and y1 > y0:
            xs = ((np.arange(x0, x1, dtype=np.float32) + .5 - cx) / radius)
            ys = ((cy - (np.arange(y0, y1, dtype=np.float32) + .5)) / radius)
            X, Y = np.meshgrid(xs, ys)
            r2 = X * X + Y * Y
            region = image[y0:y1, x0:x1].astype(np.float32)
            sun = subsolar_point(moment) if lighting else None
            disc = r2 <= 1.0
            if disc.any():
                region[disc] = self._surface(camera, X[disc], Y[disc], r2[disc], sun, table, bilinear,
                                             2 * math.pi * radius)
            ring = (~disc) & (r2 < (1 + GLOW) ** 2)
            if ring.any():
                self._glow(camera, region, ring, X[ring], Y[ring], r2[ring], sun)
            image[y0:y1, x0:x1] = np.clip(region, 0, 255).astype(np.uint8)
        result = Image.fromarray(image, 'RGB')
        if (w, h) != (width, height):
            result = result.resize((width, height), Image.LANCZOS if scale > 1 else Image.BILINEAR)
        self.last_ms = (time.perf_counter() - started) * 1000
        megapixels = max(.01, w * h / 1e6)
        sample = self.last_ms / megapixels
        self.cost_per_megapixel = sample if self.cost_per_megapixel is None else .7 * self.cost_per_megapixel + .3 * sample
        return result

    def _surface(self, camera, X, Y, r2, sun, table, bilinear, circumference):
        earth = self.earth
        Z = np.sqrt(np.maximum(0, 1 - r2))
        phi0, lam0 = math.radians(camera.latitude), math.radians(camera.longitude)
        sin_lat = np.clip(Y * math.cos(phi0) + Z * math.sin(phi0), -1, 1)
        lat = np.arcsin(sin_lat)
        lon = lam0 + np.arctan2(X, Z * math.cos(phi0) - Y * math.sin(phi0))
        u = (lon / (2 * math.pi) + .5) % 1.0
        v = np.clip(.5 - lat / math.pi, 0, .999999)
        colour = self._sample(earth.level(earth.day_levels, circumference), u, v, bilinear)
        day = None
        if sun is not None:
            sun_lat, sun_lon = math.radians(sun[0]), math.radians(sun[1])
            mu = sin_lat * math.sin(sun_lat) + np.cos(lat) * math.cos(sun_lat) * np.cos(lon - sun_lon)
            day = np.clip((mu + .1) / .2, 0, 1)
            day = day * day * (3 - 2 * day)  # smooth twilight about ±6° either side of the line
            light = (.34 + .66 * np.sqrt(np.clip(mu, 0, 1))) * day + NIGHT_FLOOR * (1 - day)
            colour = colour * light[:, None]
            if earth.night_levels is not None:
                night = earth.level(earth.night_levels, circumference)
                glow = (self._sample(night[..., None], u, v, False)[:, 0] / 255.0) ** 1.5
                colour = colour + glow[:, None] * CITY_LIGHT * ((1 - day) * .95)[:, None]
            haze_light = .25 + .75 * day
        else:
            haze_light = np.ones_like(Z)
        if table is not None:
            # Data colours go on after the light, so a layer stays readable on the night side.
            rh, rw = earth.raster.shape
            ids = earth.raster[(v * rh).astype(np.int64), (u * rw).astype(np.int64) % rw]
            tint = table[ids]
            alpha = tint[:, 3:4]
            shade = 1.0 if day is None else (.72 + .28 * day)[:, None]
            colour = colour * (1 - alpha) + tint[:, :3] * shade * alpha
        colour = colour * (.74 + .26 * Z)[:, None]  # the planet's edge darkens: it reads as a sphere
        haze = ((1 - Z) ** 2.4 * .6 * haze_light)[:, None]
        return colour * (1 - haze) + ATMOSPHERE * haze

    def _glow(self, camera, region, ring, X, Y, r2, sun):
        distance = np.sqrt(r2)
        alpha = ((1 + GLOW - distance) / GLOW).clip(0, 1) ** 2.2 * .8
        if sun is not None:
            phi0, lam0 = math.radians(camera.latitude), math.radians(camera.longitude)
            ux, uy = X / distance, Y / distance  # the limb point straight below this pixel
            sin_lat = np.clip(uy * math.cos(phi0), -1, 1)
            lon = lam0 + np.arctan2(ux, -uy * math.sin(phi0))
            sun_lat, sun_lon = math.radians(sun[0]), math.radians(sun[1])
            mu = sin_lat * math.sin(sun_lat) + np.sqrt(1 - sin_lat ** 2) * math.cos(sun_lat) * np.cos(lon - sun_lon)
            alpha = alpha * (.18 + .82 * np.clip(mu * 1.6 + .55, 0, 1))
        alpha = alpha[:, None]
        region[ring] = region[ring] * (1 - alpha) + ATMOSPHERE * alpha

    @staticmethod
    def _sample(texture, u, v, bilinear):
        th, tw = texture.shape[:2]
        if not bilinear:
            return texture[(v * th).astype(np.int64), (u * tw).astype(np.int64) % tw].astype(np.float32)
        x, y = u * tw - .5, v * th - .5
        x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
        fx, fy = (x - x0)[:, None].astype(np.float32), (y - y0)[:, None].astype(np.float32)
        x0, x1 = x0 % tw, (x0 + 1) % tw
        y0, y1 = np.clip(y0, 0, th - 1), np.clip(y0 + 1, 0, th - 1)
        top = texture[y0, x0].astype(np.float32) * (1 - fx) + texture[y0, x1].astype(np.float32) * fx
        bottom = texture[y1, x0].astype(np.float32) * (1 - fx) + texture[y1, x1].astype(np.float32) * fx
        return top * (1 - fy) + bottom * fy


def scales_for(quality: str, renderer: EarthRenderer, width: int, height: int) -> dict:
    """The still and moving image scales for a preset; Automatic sizes them from measured cost."""
    if quality in QUALITY:
        return dict(QUALITY[quality])
    cost = renderer.cost_per_megapixel
    settings = dict(QUALITY['balanced'])
    if cost:
        megapixels = max(.05, width * height / 1e6)
        # Aim for about 110 ms for a still frame and 30 ms for a moving one on this computer.
        settings['still'] = max(.6, min(1.0, math.sqrt(110 / (cost * megapixels))))
        settings['moving'] = max(.3, min(.75, math.sqrt(30 / (cost * megapixels))))
    return settings
