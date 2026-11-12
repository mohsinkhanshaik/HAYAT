"""The 3D Earth in the Global intelligence map (2026-11-08).

The globe is a second way to draw the same map: the same observations, clusters, selection, filters,
saved views and legend. The flat map stays one key away (G) as the 2D alternative, and every
observation stays reachable from the keyboard through "In view" and the Events table.

What is new on the globe, and where it comes from:
* the planet, the sun's real position and city lights at night (app/earth_globe.py);
* countries you can point at and click: outline, Natural Earth figures with their year, and the
  loaded observations inside them, with a way back to the previous view;
* colouring countries by people per km², GDP per person or income group, with a legend that says
  where the figures come from and that they are estimates for a past year, not live data;
* "Go to place": countries from the bundled outlines and towns from the GeoNames list already on
  this computer (nothing is looked up online).

Motion follows the owner's animation setting: with motion off, flights jump, nothing coasts or
turns by itself. A still globe is drawn once; a moving one is drawn smaller and sharpened when it stops.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import tkinter as tk
from datetime import UTC, datetime
from tkinter import ttk

from app.gui_intelligence_parts import MAP_TEXT, blend, round_rect
from app.gui_resources import BooleanVar, StringVar
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

SPACE = '#03060B'
DEFAULT_GLOBE = {'quality': 'auto', 'lighting': True, 'borders': True, 'layer': '', 'spin': False,
                 'longitude': 50.0, 'latitude': 20.0, 'zoom': 1.0}
GLOBE_HINT = 'Drag to turn · Wheel zoom · Click a country · Ctrl+G go to place · G flat map'
GRID_COLOUR, BORDER_COLOUR, HOVER_COLOUR, SELECTED_COLOUR = '#29445E', '#6C8BA6', '#E8F1FA', '#FFD166'
LABEL_COLOUR, CONTINENT_COLOUR, SHADOW = '#DDE7F1', '#A9C1D8', '#04070C'
CONTINENT_LABELS = ((-100, 45, 'NORTH AMERICA'), (-60, -15, 'SOUTH AMERICA'), (15, 52, 'EUROPE'),
                    (20, 5, 'AFRICA'), (90, 45, 'ASIA'), (134, -25, 'OCEANIA'), (0, -80, 'ANTARCTICA'))
#: Spin speed for "Turn slowly", degrees of longitude per second.
SPIN = 4.0
_places_lock = threading.Lock()
_places: list | None = None


def load_places() -> list:
    """Towns from the GeoNames list the location-privacy tools installed (empty when not installed)."""
    global _places
    with _places_lock:
        if _places is not None:
            return _places
        rows = []
        try:
            from app.location_privacy import PLACES_FILE, country_name

            if PLACES_FILE.is_file():
                names = {}
                with PLACES_FILE.open(encoding='utf-8') as handle:
                    handle.readline()
                    for line in handle:
                        parts = line.rstrip('\n').split(',')
                        if len(parts) < 6:
                            continue
                        try:
                            lat, lon = float(parts[0]), float(parts[1])
                        except ValueError:
                            continue
                        code = parts[5]
                        if code not in names:
                            names[code] = country_name(code)
                        rows.append((parts[2].casefold(), parts[2], parts[3], names[code], lat, lon))
                rows.sort(key=lambda row: row[0])
        except Exception:
            log_swallowed(_log, 'reading the local GeoNames places for Go to place')
        _places = rows
        return rows


def find_places(text: str, limit: int = 12) -> list:
    """Towns whose name starts with the text (binary search on the sorted list)."""
    import bisect

    needle = ' '.join(str(text).split()).casefold()
    rows = load_places()
    if len(needle) < 2 or not rows:
        return []
    start = bisect.bisect_left(rows, (needle,))
    found = []
    for row in rows[start:start + 400]:
        if not row[0].startswith(needle):
            break
        found.append(row)
    found.sort(key=lambda row: (row[0] != needle, len(row[0])))
    return found[:limit]


class IntelligenceGlobeViews:
    """Mixin for IntelligenceWindow: the globe's state, drawing and interaction."""

    # ------------------------------------------------------------------ state and preferences
    def build_globe_state(self, saved):
        globe = {**DEFAULT_GLOBE, **(saved.get('globe') or {})}
        self.map_mode = StringVar(self, saved.get('map_mode') or 'globe')
        self.globe_quality = StringVar(self, globe['quality'])
        self.globe_lighting = BooleanVar(self, globe['lighting'])
        self.globe_borders = BooleanVar(self, globe['borders'])
        self.globe_layer = StringVar(self, globe['layer'])
        self.globe_spin = BooleanVar(self, globe['spin'])
        from app.earth_globe import GlobeCamera

        self.globe_camera = GlobeCamera(globe['longitude'], globe['latitude'], globe['zoom']).clamp()
        self._globe_earth = self._globe_renderer = None
        self._globe_problem = ''
        self._globe_photo = self._globe_motion = self._globe_flight = self._globe_settle = None
        self._globe_photo_size = None
        self._globe_moving = False
        self._globe_velocity = (0.0, 0.0)
        self._globe_samples = []
        self._globe_last_tick = time.perf_counter()
        self._globe_history = []
        self._globe_pointer_inside = False
        self._globe_pin = None
        self._globe_scale = 1.0
        self.country_hover = self.selected_country = None
        self._country_rows = []

    def globe_settings(self):
        camera = self.globe_camera
        return {'quality': self.globe_quality.get(), 'lighting': bool(self.globe_lighting.get()),
                'borders': bool(self.globe_borders.get()), 'layer': self.globe_layer.get(),
                'spin': bool(self.globe_spin.get()), 'longitude': round(camera.longitude, 4),
                'latitude': round(camera.latitude, 4), 'zoom': round(camera.zoom, 4)}

    def save_globe_preferences(self):
        """Remember the mode, globe options and camera for the next time the window opens."""
        try:
            self.manager.preferences('save', {'map_mode': self.map_mode.get(), 'globe': self.globe_settings()})
        except Exception:
            log_swallowed(_log, 'saving the globe preferences')

    def apply_globe_options(self, options):
        if options.get('map_mode') in ('globe', 'flat'):
            self.map_mode.set(options['map_mode'])
        globe = options.get('globe')
        if isinstance(globe, dict):
            from app.earth_globe import GlobeCamera

            merged = {**DEFAULT_GLOBE, **globe}
            for variable, key in ((self.globe_quality, 'quality'), (self.globe_lighting, 'lighting'),
                                  (self.globe_borders, 'borders'), (self.globe_layer, 'layer'), (self.globe_spin, 'spin')):
                variable.set(merged[key])
            self.globe_camera = GlobeCamera(merged['longitude'], merged['latitude'], merged['zoom']).clamp()
        self.update_map_mode_controls()

    def globe_ready(self):
        """Load the planet once; any failure leaves the flat map working and says why."""
        if self._globe_renderer is not None:
            return True
        if self._globe_problem:
            return False
        try:
            from app.earth_globe import Earth, EarthRenderer

            self._globe_earth = Earth.shared()
            self._globe_renderer = EarthRenderer(self._globe_earth)
            from PIL import ImageTk  # noqa: F401  (the Tk bridge must exist too)
            return True
        except Exception as exc:
            self._globe_problem = f'The 3D globe could not start ({type(exc).__name__}: {exc}); showing the flat map.'
            log_swallowed(_log, 'starting the 3D globe')
            self.feedback.set(self._globe_problem)
            return False

    def globe_on(self):
        return self.map_mode.get() == 'globe' and self.globe_ready()

    # ------------------------------------------------------------------ switching
    def build_globe_tools(self):
        """The mode switch at the front of the map toolbar and the Globe menu in Map options."""
        self.mode_switch = tk.Frame(self.map_toolbar, bg=self.palette.BLACK)
        for value, label in (('globe', '3D globe'), ('flat', 'Flat map')):
            ttk.Radiobutton(self.mode_switch, text=label, value=value, variable=self.map_mode,
                            command=self.map_mode_changed, style='Segment.Toolbutton').pack(side='left', padx=(0, 2))
        self.map_toolbar.add(self.mode_switch, index=0)
        from app.gui_help import Tooltip

        self.mode_switch._intel_tip = Tooltip(self.mode_switch, 'Show the observations on a 3D globe or a flat map (G).',
                                              self.palette)
        p = self.palette
        menu = tk.Menu(self.map_menu, tearoff=False, bg=p.PANEL, fg=p.WHITE, activebackground=p.GOLD, activeforeground=p.BLACK)
        menu.add_checkbutton(label='Day and night (the sun now)', variable=self.globe_lighting, command=self.globe_option_changed)
        menu.add_checkbutton(label='Country borders', variable=self.globe_borders, command=self.globe_option_changed)
        menu.add_checkbutton(label='Turn slowly', variable=self.globe_spin, command=self.globe_option_changed)
        menu.add_separator()
        from app.earth_globe import COUNTRY_LAYERS, QUALITY_LABELS

        menu.add_radiobutton(label='Countries: no colouring', value='', variable=self.globe_layer, command=self.globe_option_changed)
        for key, (title, _unit, _basis) in COUNTRY_LAYERS.items():
            menu.add_radiobutton(label='Countries by ' + title[0].lower() + title[1:], value=key, variable=self.globe_layer,
                                 command=self.globe_option_changed)
        menu.add_separator()
        for key, label in QUALITY_LABELS.items():
            menu.add_radiobutton(label='Quality: ' + label, value=key, variable=self.globe_quality, command=self.globe_option_changed)
        menu.add_separator()
        menu.add_command(label='Back to previous view', command=self.globe_back, accelerator='Backspace')
        menu.add_command(label='Rendering details', command=self.globe_details)
        self.globe_menu = menu
        self.map_menu.insert_command(0, label='Go to place…', command=self.go_to_place, accelerator='Ctrl+G')
        self.map_menu.add_separator()
        self.map_menu.add_cascade(label='Globe', menu=menu)
        self.bind('<Control-g>', lambda _event: (self.go_to_place(), 'break')[1], add='+')
        self.map.bind('<KeyPress-g>', lambda _event: (self.toggle_map_mode(), 'break')[1])
        self.map.bind('<BackSpace>', lambda _event: (self.globe_back(), 'break')[1])
        self.map.bind('<Enter>', lambda _event: self._pointer(True), add='+')
        self.map.bind('<Leave>', lambda _event: self._pointer(False), add='+')
        self.update_map_mode_controls()

    def _pointer(self, inside):
        self._globe_pointer_inside = inside
        if not inside:
            self.map.delete('country-hover')
            self.country_hover = None
            self.globe_kick()

    def update_map_mode_controls(self):
        if not hasattr(self, 'map_hint'):
            return
        globe = self.map_mode.get() == 'globe'
        self.map_hint.set(GLOBE_HINT if globe else self.MAP_HINT)

    def toggle_map_mode(self):
        self.map_mode.set('flat' if self.map_mode.get() == 'globe' else 'globe')
        self.map_mode_changed()

    def map_mode_changed(self):
        """Carry the view across, so switching never loses the place being looked at."""
        width, height = self.size()
        from app.earth_globe import GlobeCamera

        self.globe_stop()
        if self.map_mode.get() == 'globe':
            if not self.globe_ready():
                self.map_mode.set('flat')
            else:
                self.globe_camera = GlobeCamera.from_flat(self.viewport.longitude, self.viewport.latitude,
                                                          self.viewport.zoom, width, height)
                self.feedback.set('3D globe: NASA imagery with the sun as it is now. Drag to turn, click a country, '
                                  'G for the flat map.')
        else:
            camera = self.globe_camera
            self.viewport.longitude, self.viewport.latitude = camera.longitude, camera.latitude
            self.viewport.zoom = camera.flat_zoom(width, height)
            self.viewport.clamp()
            self.feedback.set('Flat map. G returns to the 3D globe.')
        self.update_map_mode_controls()
        self.save_globe_preferences()
        self.view_changed()
        self.globe_kick()

    def globe_option_changed(self):
        if self.map_mode.get() != 'globe':
            self.map_mode.set('globe')
            return self.map_mode_changed()
        self.save_globe_preferences()
        self.request_draw()
        self.globe_kick()

    # ------------------------------------------------------------------ geometry helpers
    def globe_project(self, lon, lat, wrap=True):
        width, height = self.size()
        x, y, depth = self.globe_camera.project(lon, lat, width, height)
        return (x, y) if depth > 0 else (-10_000.0, -10_000.0)

    def _lines(self, xs, ys, depth, starts, mask=None):
        """Canvas polylines for each ring, broken wherever it passes behind the planet."""
        canvas_lines = []
        visible = depth > 0 if mask is None else (depth > 0) & mask
        for begin, end in zip(starts[:-1], starts[1:]):
            shown = visible[begin:end]
            if not shown.any():
                continue
            if shown.all():
                runs = [(begin, end)]
            else:
                edges = (shown[1:] != shown[:-1]).nonzero()[0] + 1
                bounds = [0, *edges.tolist(), end - begin]
                runs = [(begin + a, begin + b) for a, b in zip(bounds[:-1], bounds[1:]) if shown[a]]
            for a, b in runs:
                if b - a >= 2:
                    coordinates = [None] * (2 * (b - a))
                    coordinates[0::2] = xs[a:b].tolist()
                    coordinates[1::2] = ys[a:b].tolist()
                    canvas_lines.append(coordinates)
        return canvas_lines

    # ------------------------------------------------------------------ drawing
    def draw_globe(self):
        import numpy as np
        from PIL import ImageTk

        from app.earth_globe import scales_for, subsolar_point

        canvas = self.map
        canvas.delete('all')
        if str(canvas.cget('bg')) != SPACE:
            canvas.configure(bg=SPACE, highlightbackground=SPACE)
        width, height = self.size()
        camera, earth, renderer = self.globe_camera, self._globe_earth, self._globe_renderer
        settings = scales_for(self.globe_quality.get(), renderer, width, height)
        moving = self._globe_moving
        scale = settings['moving'] if moving else settings['still'] * settings['supersample']
        bilinear = (settings['bilinear'] if settings['bilinear'] is not None else camera.zoom > 2.5) and not moving
        layer = self.globe_layer.get() or None
        table, legend = earth.layer_table(layer, selected=self.selected_country)
        moment = datetime.now(UTC)
        lighting = bool(self.globe_lighting.get())
        image = renderer.render(camera, width, height, scale=scale, lighting=lighting, moment=moment,
                                table=table, bilinear=bilinear)
        self._globe_scale = scale
        if self._globe_photo is not None and self._globe_photo_size == image.size:
            self._globe_photo.paste(image)
        else:
            self._globe_photo = ImageTk.PhotoImage(image, master=canvas)
            self._globe_photo_size = image.size
        canvas.create_image(0, 0, image=self._globe_photo, anchor='nw', tags=('globe-surface',))
        if self.show_grid.get():
            self._draw_graticule(width, height)
        if self.globe_borders.get() and earth.countries:
            xs, ys, depth = camera.project_many(earth.ring_lons, earth.ring_lats, width, height)
            mask = ~((np.abs(earth.ring_lons) >= 179.99) | (earth.ring_lats <= -89.9))
            # Softer when the whole planet shows, where borders crowd together; clearer close up.
            colour = blend(BORDER_COLOUR, SPACE, .35 if camera.zoom < 1.6 else .1)
            for coordinates in self._lines(xs, ys, depth, earth.ring_starts, mask):
                canvas.create_line(*coordinates, fill=colour, width=1, tags=('borders',))
        if self.selected_country is not None:
            self._draw_country_outline(self.selected_country, SELECTED_COLOUR, 2, 'country-selected')
        if self.show_labels.get():
            self._draw_globe_labels(width, height)
        if lighting:
            sun_lat, sun_lon = subsolar_point(moment)
            x, y, depth = camera.project(sun_lon, sun_lat, width, height)
            if depth > .05:
                canvas.create_oval(x - 6, y - 6, x + 6, y + 6, outline='#FFD166', width=2, tags=('sun',))
                canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill='#FFD166', outline='', tags=('sun',))
                if camera.zoom < 3:
                    self._shadow_text(x + 10, y, 'sun overhead', '#FFD166', ('Segoe UI', 8), 'w', ('sun',))
        visible, in_view = [], []
        for row in self.map_rows.values():
            x, y, depth = camera.project(row['longitude'], row['latitude'], width, height)
            if depth > 0 and -12 <= x <= width + 12 and -12 <= y <= height + 12:
                visible.append((row, x, y))
                if 0 <= x <= width and 0 <= y <= height:
                    in_view.append(row)
        self._draw_markers(visible, camera.flat_zoom(width, height))
        selected = self.selected_record['event']['id'] if self.selected_record else None
        if selected in self.map_rows:
            self._draw_callout(self.globe_project, width, height)
        if self._globe_pin:
            lon, lat, label = self._globe_pin
            x, y, depth = camera.project(lon, lat, width, height)
            if depth > 0:
                canvas.create_oval(x - 9, y - 9, x + 9, y + 9, outline=SELECTED_COLOUR, width=2, tags=('pin',))
                canvas.create_line(x, y - 9, x, y - 22, fill=SELECTED_COLOUR, width=2, tags=('pin',))
                self._shadow_text(x + 8, y - 24, label, SELECTED_COLOUR, ('Segoe UI Semibold', 9), 'w', ('pin',))
        self.update_map_summary(in_view)
        self.draw_map_empty(in_view)
        heading = f'{self.region.get()} · 3D globe · {camera.zoom:.1f}×'
        if lighting and width > 620:
            heading += f" · daylight for {moment:%H:%M} UTC"
        self._shadow_text(12, 12, heading, MAP_TEXT, ('Segoe UI', 9), 'nw', ())
        top = 34
        if self.selected_country is not None:
            top = self._draw_country_card(width, height, top)
        bottom = self._draw_globe_info(width, height, moment, lighting)
        if legend:
            self._draw_layer_legend(legend, bottom)
        self._draw_hint()

    def _shadow_text(self, x, y, text, colour, font, anchor, tags):
        self.map.create_text(x + 1, y + 1, text=text, fill=SHADOW, font=font, anchor=anchor, tags=tags)
        return self.map.create_text(x, y, text=text, fill=colour, font=font, anchor=anchor, tags=tags)

    def _draw_graticule(self, width, height):
        import numpy as np

        camera = self.globe_camera
        step = 30 if camera.zoom < 3 else 10 if camera.zoom < 10 else 5
        lons, lats, starts = [], [], [0]
        for lon in range(-180, 180, step):
            samples = np.arange(-80, 80.1, 2.0)
            lons.extend([lon] * len(samples))
            lats.extend(samples.tolist())
            starts.append(len(lons))
        for lat in range(-60, 61, step):
            samples = np.arange(-180, 180.1, 2.0)
            lons.extend(samples.tolist())
            lats.extend([lat] * len(samples))
            starts.append(len(lons))
        xs, ys, depth = camera.project_many(lons, lats, width, height)
        for coordinates in self._lines(xs, ys, depth, np.array(starts)):
            self.map.create_line(*coordinates, fill=GRID_COLOUR, width=1, tags=('graticule',))

    def _draw_country_outline(self, index, colour, line_width, tag):
        import numpy as np

        earth, camera = self._globe_earth, self.globe_camera
        width, height = self.size()
        rings = np.nonzero(earth.ring_owners == index)[0]
        for ring in rings.tolist():
            begin, end = int(earth.ring_starts[ring]), int(earth.ring_starts[ring + 1])
            lons, lats = earth.ring_lons[begin:end], earth.ring_lats[begin:end]
            xs, ys, depth = camera.project_many(lons, lats, width, height)
            mask = ~((np.abs(lons) >= 179.99) | (lats <= -89.9))
            for coordinates in self._lines(xs, ys, depth, np.array([0, end - begin]), mask):
                self.map.create_line(*coordinates, fill=colour, width=line_width, tags=(tag,))

    def _draw_globe_labels(self, width, height):
        import numpy as np

        camera, earth = self.globe_camera, self._globe_earth
        taken = []

        def free(x, y, text):
            box = (x - len(text) * 3.4 - 4, y - 8, x + len(text) * 3.4 + 4, y + 8)
            if any(box[0] < other[2] and box[2] > other[0] and box[1] < other[3] and box[3] > other[1] for other in taken):
                return False
            taken.append(box)
            return True

        if camera.zoom < 1.8:
            for lon, lat, label in CONTINENT_LABELS:
                x, y, depth = camera.project(lon, lat, width, height)
                if depth > .35 and 40 < x < width - 40 and 30 < y < height - 30 and free(x, y, label):
                    self._shadow_text(x, y, label, CONTINENT_COLOUR, ('Segoe UI Semibold', 8), 'center', ('geography',))
            return
        if not earth.countries:
            return
        order = sorted(range(len(earth.countries)), key=lambda index: -earth.countries[index].get('area_km2', 0))
        lons = np.array([earth.countries[index]['label'][0] for index in order])
        lats = np.array([earth.countries[index]['label'][1] for index in order])
        xs, ys, depth = camera.project_many(lons, lats, width, height)
        shown = 0
        radius = camera.radius(width, height)
        for position, index in enumerate(order):
            country = earth.countries[index]
            # A country gets a name once it is at least about 60 px across on screen.
            across = math.sqrt(country.get('area_km2', 0)) / 6371.0 * radius
            if depth[position] <= .3 or across < 60 or shown >= 45:
                continue
            x, y = float(xs[position]), float(ys[position])
            if 30 < x < width - 30 and 30 < y < height - 30 and free(x, y, country['name']):
                colour = SELECTED_COLOUR if index == self.selected_country else LABEL_COLOUR
                self._shadow_text(x, y, country['name'], colour, ('Segoe UI', 8), 'center', ('geography',))
                shown += 1

    def _draw_globe_info(self, width, height, moment, lighting):
        """Bottom-left: where the view faces, how wide it is, and what the picture is made of."""
        camera = self.globe_camera
        from app.intelligence.view import format_point

        across = camera.across_km(width, height)
        lines = [f'Facing {format_point(camera.latitude, camera.longitude)} · about {across:,.0f} km across']
        if self._globe_earth.imagery:
            lines.append('NASA Blue Marble (cloud-free) & Black Marble 2012 lights · public domain'
                         + (f' · sun for {moment:%H:%M} UTC' if lighting else ''))
        else:
            lines.append(self._globe_earth.problem)
        if width < 560:
            lines = lines[:1]
        for index, line in enumerate(lines):
            self.map.create_text(16, 15 * index, text=line, anchor='nw', fill=MAP_TEXT if index == 0 else '#7D93A8',
                                 font=('Segoe UI', 8), width=max(160, width - 48), tags=('globe-info',))
        return self._backed('globe-info', bottom=height - 8)

    def _backed(self, tag, bottom, outline='#203547', pad=8):
        """Move a group of items so it ends at bottom, then put a panel behind it; returns the panel's top."""
        canvas = self.map
        box = canvas.bbox(tag)
        if not box:
            return bottom
        canvas.move(tag, 0, bottom - pad - box[3])
        left, top, right, lower = canvas.bbox(tag)
        panel = round_rect(canvas, left - pad, top - pad + 2, right + pad, lower + pad - 2, 6,
                           fill=blend(self.palette.PANEL, SPACE, .3), outline=outline, tags=(tag,))
        canvas.tag_lower(panel, tag)
        return top - pad + 2

    def _draw_layer_legend(self, legend, bottom):
        canvas, p = self.map, self.palette
        rows = len(legend['labels'])
        tag = ('layer-legend',)
        canvas.create_text(16, 0, text=legend['title'] + (f" · {legend['years']} estimates" if legend['years'] else ''),
                           anchor='nw', fill=p.WHITE, font=('Segoe UI Semibold', 9), tags=tag)
        for index, (label, colour) in enumerate(zip(legend['labels'], legend['colours'])):
            y = 26 + 16 * index
            canvas.create_rectangle(16, y - 5, 30, y + 5, fill='#%02x%02x%02x' % colour, outline='', tags=tag)
            canvas.create_text(38, y, text=label + (f" {legend['unit']}" if legend['unit'] and index == rows - 1 else ''),
                               anchor='w', fill=MAP_TEXT, font=('Segoe UI', 8), tags=tag)
        canvas.create_text(16, 20 + 16 * rows, text='Historical, not live · ' + legend['basis'], anchor='nw', width=234,
                           fill='#7D93A8', font=('Segoe UI', 7), tags=tag)
        return self._backed('layer-legend', bottom=bottom - 6)

    # ------------------------------------------------------------------ countries
    def country_summary(self, index):
        """The facts the card shows, with the loaded observations that fall inside the country."""
        earth = self._globe_earth
        country = earth.countries[index]
        rows = [row for row in self.map_rows.values()
                if earth.country_at(row['longitude'], row['latitude']) == index]
        rows.sort(key=lambda row: (-row.get('severity_rank', 0), str(row.get('observed_at') or '')), reverse=False)
        from collections import Counter

        providers = Counter(row.get('provider') or row.get('feed', '') for row in rows)
        newest = max((str(row.get('observed_at') or '') for row in rows), default='')
        people = country.get('population') or 0
        gdp = country.get('gdp_musd') or 0
        return {'name': country['name'], 'long_name': country['long_name'], 'continent': country['continent'],
                'subregion': country['subregion'], 'population': people, 'population_year': country.get('population_year'),
                'gdp_musd': gdp, 'gdp_year': country.get('gdp_year'), 'income_group': country.get('income_group'),
                'area_km2': country.get('area_km2'), 'per_person': gdp * 1e6 / people if people and gdp else None,
                'rows': rows, 'providers': providers.most_common(3), 'newest': newest}

    def _draw_country_card(self, width, height, top):
        from app.intelligence.view import relative_time

        summary = self.country_summary(self.selected_country)
        canvas, p = self.map, self.palette
        number = lambda value: (f'{value / 1e9:,.2f} billion' if value >= 1e9 else f'{value / 1e6:,.1f} million'
                                if value >= 1e6 else f'{value:,.0f}')
        lines = [(summary['long_name'], ('Segoe UI Semibold', 11), p.WHITE),
                 (f"{summary['continent']} · {summary['subregion']}", ('Segoe UI', 8), MAP_TEXT)]
        facts = []
        if summary['population']:
            facts.append(f"{number(summary['population'])} people ({summary['population_year']} est.)")
        if summary['gdp_musd']:
            facts.append(f"GDP ${number(summary['gdp_musd'] * 1e6)} ({summary['gdp_year']})")
        if facts:
            lines.append((' · '.join(facts), ('Segoe UI', 8), MAP_TEXT))
        if summary['per_person']:
            lines.append((f"≈ ${summary['per_person']:,.0f} per person · {summary['income_group']}", ('Segoe UI', 8), MAP_TEXT))
        count = len(summary['rows'])
        if count:
            sources = ', '.join(f'{name} ({value})' for name, value in summary['providers'] if name)
            lines.append((f"{count} loaded observation{'s' if count != 1 else ''} here · newest "
                          f"{relative_time(summary['newest']) or 'time unknown'}", ('Segoe UI Semibold', 8), SELECTED_COLOUR))
            if sources:
                lines.append(('Sources: ' + sources, ('Segoe UI', 8), MAP_TEXT))
        else:
            lines.append(('No loaded observations inside this country with the current filters.', ('Segoe UI', 8), MAP_TEXT))
        lines.append(('Natural Earth (public domain): estimates for the year shown, not live.', ('Segoe UI', 7), '#7D93A8'))
        box_width = min(340, width - 24)
        if box_width < 220 or height < 260:
            return top
        # Lines wrap rather than being cut; each one is measured, so the card fits what it says.
        y = top + 10
        for text, font, colour in lines:
            item = canvas.create_text(22, y, text=text, anchor='nw', width=box_width - 20, fill=colour, font=font,
                                      tags=('country-card',))
            y = canvas.bbox(item)[3] + 3
        x = 22
        y += 6
        for action, label, enabled in (('list', 'List observations', bool(count)), ('back', 'Back', bool(self._globe_history)),
                                       ('close', 'Close', True)):
            colour = p.GOLD if enabled else '#56687A'
            item = canvas.create_text(x, y, text=label, anchor='nw', fill=colour, font=('Segoe UI Semibold', 9),
                                      tags=('country-card', 'country-action', f'country-action:{action}' if enabled else 'disabled'))
            left, _top, right, bottom = canvas.bbox(item)
            canvas.create_line(left, bottom, right, bottom, fill=colour, tags=('country-card',))
            x = right + 16
        bottom = canvas.bbox('country-card')[3] + 10
        panel = round_rect(canvas, 12, top, 12 + box_width, bottom, 8, fill=blend(p.PANEL, SPACE, .15),
                           outline=SELECTED_COLOUR, tags=('country-card',))
        canvas.tag_lower(panel, 'country-card')
        return bottom + 8

    def country_action_at(self, x, y):
        for item in reversed(self.map.find_overlapping(x - 2, y - 2, x + 2, y + 2)):
            tags = self.map.gettags(item)
            for tag in tags:
                if tag.startswith('country-action:'):
                    return tag.split(':', 1)[1]
            if 'country-card' in tags:
                return 'card'
        return None

    def select_country(self, index, fly=True):
        width, height = self.size()
        earth = self._globe_earth
        self.selected_country = index
        target = self.globe_camera.copy()
        target.fit(earth.country_bbox(index), width, height)
        target.zoom = min(target.zoom, 14.0)
        summary = self.country_summary(index)
        self._country_rows = summary['rows']
        count = len(summary['rows'])
        self.feedback.set(f"{summary['name']}: {count} loaded observation{'s' if count != 1 else ''} inside. "
                          'Backspace returns to the previous view.')
        if fly:
            self.globe_fly(target, remember=True)
        else:
            self.request_draw()

    def clear_country(self):
        if self.selected_country is not None:
            self.selected_country = None
            self._country_rows = []
            self.request_draw()

    def country_action(self, action):
        if action == 'list' and self._country_rows:
            return self.browse_map(self._country_rows)
        if action == 'back':
            return self.globe_back()
        if action == 'close':
            return self.clear_country()
        return None

    # ------------------------------------------------------------------ motion
    def globe_fly(self, target, remember=False, duration=.85):
        """Fly to a camera; instant when the owner has turned motion off."""
        from app.gui_motion import MOTION

        if remember:
            self._globe_history = (self._globe_history + [self.globe_camera.copy()])[-20:]
        self._globe_velocity = (0.0, 0.0)
        target = target.copy().clamp()
        if not MOTION['enabled'] or not self.winfo_viewable():
            self.globe_camera = target
            self._globe_flight = None
            self._globe_moving = False
            return self.view_changed()
        self._globe_flight = (self.globe_camera.copy(), target, time.perf_counter(), duration)
        self.globe_kick()
        return None

    def globe_back(self):
        if not self._globe_history:
            self.feedback.set('No earlier view to return to.')
            return None
        target = self._globe_history.pop()
        if self.map_mode.get() != 'globe':
            return None
        self.globe_fly(target)
        return target

    def globe_stop(self):
        self._globe_flight = None
        self._globe_velocity = (0.0, 0.0)
        if self._globe_motion is not None:
            self.after_cancel(self._globe_motion)
            self._globe_motion = None
        if self._globe_settle is not None:
            self.after_cancel(self._globe_settle)
            self._globe_settle = None
        self._globe_moving = False

    def _spinning(self):
        from app.gui_motion import MOTION

        try:
            shown = self.winfo_viewable() and self.state() != 'iconic'
        except tk.TclError:
            return False
        return (bool(self.globe_spin.get()) and MOTION['enabled'] and shown and not self._drag
                and not self._globe_pointer_inside and self.map_mode.get() == 'globe' and not self._closed)

    def globe_kick(self):
        """Start the motion loop when something should move and it is not already running."""
        if self._globe_motion is None and not self._closed and self.map_mode.get() == 'globe' and (
                self._globe_flight or any(self._globe_velocity) or self._spinning()):
            self._globe_last_tick = time.perf_counter()
            self._globe_motion = self.after(16, self._globe_tick)

    def _globe_tick(self):
        self._globe_motion = None
        if self._closed or not self.globe_on():
            return
        from app.earth_globe import between

        now = time.perf_counter()
        elapsed = min(.1, now - self._globe_last_tick)
        self._globe_last_tick = now
        width, height = self.size()
        frame = 16
        if self._globe_flight:
            start, end, began, duration = self._globe_flight
            fraction = (now - began) / duration
            self.globe_camera = between(start, end, fraction)
            if fraction >= 1:
                self.globe_camera = end.copy()
                self._globe_flight = None
        elif any(self._globe_velocity):
            vx, vy = self._globe_velocity
            self.globe_camera.drag(vx * elapsed, vy * elapsed, width, height)
            decay = math.exp(-elapsed / .35)
            vx, vy = vx * decay, vy * decay
            self._globe_velocity = (vx, vy) if math.hypot(vx, vy) > 25 else (0.0, 0.0)
        elif self._spinning():
            self.globe_camera.longitude += SPIN * elapsed
            self.globe_camera.clamp()
            frame = 50
        moving = bool(self._globe_flight or any(self._globe_velocity) or self._spinning())
        self._globe_moving = moving
        self.draw()
        if moving:
            self._globe_motion = self.after(frame, self._globe_tick)
        else:
            self.view_changed()

    def globe_settle_later(self, delay=180):
        """After a burst of wheel steps, draw the sharp picture once the wheel stops."""
        if self._globe_settle is not None:
            self.after_cancel(self._globe_settle)

        def settle():
            self._globe_settle = None
            if not (self._globe_flight or any(self._globe_velocity) or self._drag):
                self._globe_moving = False
                self.view_changed()
        self._globe_settle = self.after(delay, settle)

    # ------------------------------------------------------------------ input
    def globe_wheel(self, factor, x, y):
        self._globe_flight = None
        self.globe_camera.zoom_at(factor, x, y, *self.size())
        self._globe_moving = True
        self.request_draw()
        self.globe_settle_later()

    def globe_zoom(self, factor):
        target = self.globe_camera.copy()
        target.zoom *= factor
        self.globe_fly(target, duration=.3)

    def globe_turn(self, dx, dy):
        target = self.globe_camera.copy()
        target.drag(dx, dy, *self.size())
        self.globe_fly(target, duration=.25)

    def globe_drag_start(self, event):
        self.globe_stop()
        self._globe_samples = [(time.perf_counter(), event.x, event.y)]

    def globe_drag(self, dx, dy, event):
        self.globe_camera.drag(dx, dy, *self.size())
        now = time.perf_counter()
        self._globe_samples = [sample for sample in self._globe_samples if now - sample[0] < .09] + [(now, event.x, event.y)]
        self._globe_moving = True
        self.request_draw()

    def globe_drag_end(self, moved):
        from app.gui_motion import MOTION

        samples = self._globe_samples
        self._globe_samples = []
        if moved and MOTION['enabled'] and len(samples) >= 2 and samples[-1][0] - samples[0][0] > .005:
            span = samples[-1][0] - samples[0][0]
            vx, vy = (samples[-1][1] - samples[0][1]) / span, (samples[-1][2] - samples[0][2]) / span
            # A flick coasts; a slow, deliberate drag stops where it was left.
            if math.hypot(vx, vy) > 250:
                self._globe_velocity = (max(-4000, min(4000, vx)), max(-4000, min(4000, vy)))
                return self.globe_kick()
        self._globe_moving = False
        return self.view_changed() if moved else None

    def globe_click(self, event):
        """A marker opens its observation, a cluster comes closer, land opens its country, space clears."""
        action = self.country_action_at(event.x, event.y)
        if action:
            return self.country_action(action) if action != 'card' else None
        identifier = self.marker_at(event.x, event.y)
        if identifier:
            return self.select_event(identifier)
        cluster = self.cluster_at(event.x, event.y)
        width, height = self.size()
        if cluster:
            if self.globe_camera.flat_zoom(width, height) >= 8:
                return self.browse_map(cluster[2])
            point = self.globe_camera.unproject(cluster[0], cluster[1], width, height)
            if point:
                target = self.globe_camera.copy()
                target.longitude, target.latitude = point
                target.zoom *= 2.5
                return self.globe_fly(target, remember=True)
            return None
        point = self.globe_camera.unproject(event.x, event.y, width, height)
        index = self._globe_earth.country_at(*point) if point else None
        if index is None:
            self.clear_country()
            return None
        if index == self.selected_country:
            return None
        return self.select_country(index)

    def globe_hover(self, event):
        """Hint text, the pointer, and the outline of the country under it."""
        from collections import Counter

        from app.gui_intelligence_parts import clip
        from app.intelligence.classify import CATEGORIES, SEVERITY_LABEL
        from app.intelligence.view import format_point

        width, height = self.size()
        action = self.country_action_at(event.x, event.y)
        identifier = None if action else self.marker_at(event.x, event.y)
        cluster = None if action or identifier else self.cluster_at(event.x, event.y)
        point = self.globe_camera.unproject(event.x, event.y, width, height)
        country = None
        if point and not (action or identifier or cluster):
            country = self._globe_earth.country_at(*point)
        self.map.configure(cursor='hand2' if (action not in (None, 'card')) or identifier or cluster or country is not None else '')
        self.draw_map_hover(event, identifier, cluster)
        if country != self.country_hover:
            self.country_hover = country
            self.map.delete('country-hover')
            if country is not None and country != self.selected_country:
                self._draw_country_outline(country, HOVER_COLOUR, 1.5, 'country-hover')
        place = format_point(point[1], point[0]) if point else 'Space'
        label = ''
        if cluster:
            kinds = Counter(CATEGORIES[row['category']][0].lower() for row in cluster[2])
            label = f" · {len(cluster[2])} reports: " + ', '.join(f'{count} {name}' for name, count in kinds.most_common(3))
        elif identifier:
            row = self.map_rows[identifier]
            label = f" · {row['headline']} · {SEVERITY_LABEL[row['severity']]}"
        elif country is not None:
            info = self._globe_earth.countries[country]
            label = f" · {info['name']}" + (f" · {info['population'] / 1e6:,.1f} M people ({info['population_year']} est.)"
                                           if info.get('population') else '') + ' · click for details'
        self.map_hint.set(f'{place} · {self.globe_camera.zoom:.1f}×' + (clip(label, 700) if label else ' · ' + GLOBE_HINT))

    def globe_details(self):
        renderer = self._globe_renderer
        if renderer is None:
            return self.feedback.set(self._globe_problem or 'The globe has not drawn yet.')
        from app.earth_globe import QUALITY_LABELS

        cost = renderer.cost_per_megapixel
        text = (f"Globe quality {QUALITY_LABELS.get(self.globe_quality.get(), '?')}: last frame {renderer.last_ms:.0f} ms at "
                f"{self._globe_scale:.2f}× of the canvas" + (f", about {cost:.0f} ms per megapixel on this computer" if cost else '')
                + '. Drawn on the CPU only when the view changes; no graphics memory is used.')
        self.feedback.set(text)
        return text

    def globe_fit_points(self, rows):
        from app.earth_globe import GlobeCamera
        from app.intelligence.view import MapViewport

        temporary = MapViewport()
        if not temporary.fit_points(rows):
            return False
        width, height = self.size()
        target = GlobeCamera.from_flat(temporary.longitude, temporary.latitude, temporary.zoom, width, height)
        self.globe_fly(target, remember=True)
        return True

    def globe_fit_bbox(self, bbox, remember=True):
        target = self.globe_camera.copy()
        target.fit(bbox, *self.size())
        self.globe_fly(target, remember=remember)

    def globe_focus(self, lon, lat, zoom=None, remember=True):
        target = self.globe_camera.copy()
        target.longitude, target.latitude = lon, lat
        if zoom is not None:
            target.zoom = zoom
        self.globe_fly(target, remember=remember)

    def globe_follow_selection(self, row):
        """Turn a selected observation toward the middle unless it is already near it."""
        if row.get('latitude') is None or row.get('longitude') is None:
            return
        width, height = self.size()
        x, y, depth = self.globe_camera.project(row['longitude'], row['latitude'], width, height)
        radius = self.globe_camera.radius(width, height)
        near = depth > 0 and math.hypot(x - width / 2, y - height / 2) < min(radius, min(width, height) / 2) * .45
        if not near:
            self.globe_focus(row['longitude'], row['latitude'], max(self.globe_camera.zoom, 1.4))

    # ------------------------------------------------------------------ go to place
    def go_to_place(self):
        """Search countries (bundled) and towns (the GeoNames list on this computer) and fly there."""
        if getattr(self, '_place_dialog', None) is not None and self._place_dialog.winfo_exists():
            self._place_dialog.lift()
            self._place_entry.focus_set()
            return self._place_dialog
        p = self.palette
        dialog = tk.Toplevel(self)
        self._place_dialog = dialog
        dialog.title('Go to place · HAYAT')
        dialog.configure(bg=p.BLACK)
        dialog.transient(self)
        dialog.minsize(420, 320)
        tk.Label(dialog, text='Go to a country or town', bg=p.BLACK, fg=p.WHITE, anchor='w', padx=12, pady=(10),
                 font=('Segoe UI Semibold', 12)).pack(fill='x')
        query = StringVar(dialog, '')
        entry = ttk.Entry(dialog, textvariable=query)
        entry.pack(fill='x', padx=12)
        self._place_entry = entry
        note = tk.Label(dialog, text='Countries: Natural Earth outlines (bundled). Towns: the GeoNames list on this computer. '
                                     'Nothing is looked up online.', bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left',
                        padx=12, wraplength=400, font=('Segoe UI', 8))
        note.pack(fill='x', pady=(4, 4))
        listbox = tk.Listbox(dialog, height=11, bg=p.PANEL, fg=p.WHITE, selectbackground=p.ROYAL_SOFT if hasattr(p, 'ROYAL_SOFT') else p.GOLD,
                             selectforeground=p.WHITE, highlightthickness=1, highlightbackground=p.LINE, activestyle='none',
                             font=('Segoe UI', 10))
        listbox.pack(fill='both', expand=True, padx=12, pady=(0, 8))
        results = []
        dialog.results = results
        dialog.listbox = listbox
        dialog.query = query

        def refresh(*_):
            text = query.get()
            results.clear()
            listbox.delete(0, 'end')
            if not self.globe_ready():
                return
            for index in self._globe_earth.find(text)[:8]:
                country = self._globe_earth.countries[index]
                results.append(('country', index))
                listbox.insert('end', f"{country['name']} — country, {country['continent']}")
            for row in find_places(text):
                results.append(('place', row))
                listbox.insert('end', ', '.join(part for part in (row[1], row[2], row[3]) if part) + ' — town')
            if not results and text.strip():
                listbox.insert('end', 'No match. Towns need the GeoNames list (Location privacy installs it).'
                               if not load_places() else 'No country or town starts with that.')
            if results:
                listbox.selection_set(0)

        def choose(_event=None):
            selection = listbox.curselection()
            if not selection or selection[0] >= len(results):
                return 'break'
            kind, value = results[selection[0]]
            dialog.destroy()
            self.go_to(kind, value)
            return 'break'

        def move(step):
            selection = listbox.curselection()
            index = max(0, min(listbox.size() - 1, (selection[0] if selection else -1) + step))
            listbox.selection_clear(0, 'end')
            listbox.selection_set(index)
            listbox.see(index)
            return 'break'

        query.trace_add('write', refresh)
        entry.bind('<Return>', choose)
        entry.bind('<Down>', lambda _event: move(1))
        entry.bind('<Up>', lambda _event: move(-1))
        listbox.bind('<Double-1>', choose)
        listbox.bind('<Return>', choose)
        dialog.bind('<Escape>', lambda _event: (dialog.destroy(), 'break')[1])
        actions = tk.Frame(dialog, bg=p.BLACK, padx=12, pady=8)
        actions.pack(fill='x')
        ttk.Button(actions, text='Go', command=choose, style='HayatAccent.TButton').pack(side='left')
        ttk.Button(actions, text='Close', command=dialog.destroy, style='Hayat.TButton').pack(side='right')
        dialog.choose = choose
        threading.Thread(target=load_places, daemon=True, name='hayat-places-load').start()
        from app.gui_windows import place_over

        place_over(dialog, self, (460, 420))
        entry.focus_set()
        return dialog

    def go_to(self, kind, value):
        """Fly to a country (and open its card) or a town (and pin it), on either map."""
        width, height = self.size()
        if kind == 'country':
            if self.map_mode.get() == 'globe' and self.globe_ready():
                return self.select_country(value)
            self.viewport.fit(self._globe_earth.country_bbox(value))
            self.feedback.set(f"Showing {self._globe_earth.countries[value]['name']}.")
            return self.view_changed()
        _key, name, region, country, lat, lon = value
        label = ', '.join(part for part in (name, region, country) if part)
        self._globe_pin = (lon, lat, name)
        self.feedback.set(f'Showing {label} ({lat:.3f}, {lon:.3f}) from the GeoNames list on this computer.')
        if self.map_mode.get() == 'globe' and self.globe_ready():
            return self.globe_focus(lon, lat, max(self.globe_camera.zoom, 18.0))
        self.viewport.longitude, self.viewport.latitude, self.viewport.zoom = lon, lat, max(self.viewport.zoom, 12)
        self.viewport.clamp()
        return self.view_changed()
