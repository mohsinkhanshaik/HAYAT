"""The Explore tab of Global Intelligence: launches, satellites, fires, weather maps, imagery, routes, bikes, aircraft.

Every button runs one registered tool (app/intelligence/explore.py) through the window's normal
`work()` path, so it is recorded in Activity and keeps the usual permission gates.
"""
import tkinter as tk
from tkinter import ttk

from app.gui_resources import StringVar

#: Icons from app/gui_icons.py, kept on each button so HAYAT's app-wide icon pass leaves them alone.
ICONS = {'Deep look': 'brain', 'Growing fires': 'alert', 'Launches': 'play', 'Recent launches': 'history', 'Satellites overhead': 'globe', 'ISS passes': 'eye',
         'Fires nearby': 'pin', 'Fires worldwide': 'alert', 'Weather map': 'cloud', 'Satellite image': 'image',
         'Directions': 'pin', 'Bike share': 'pin', 'Aircraft info': 'search', 'Use map center': 'globe'}
#: (button label, tool, how its arguments are built from the form)
ACTIONS = (
    ('Deep look', 'intelligence_deep_look', lambda form: {'place': form['place'], 'reason': True}),
    ('Growing fires', 'fire_clusters', lambda form: {'place': form['place'], 'radius_km': 500}),
    ('Launches', 'space_launches', lambda form: {'mode': 'upcoming'}),
    ('Recent launches', 'space_launches', lambda form: {'mode': 'recent'}),
    ('Satellites overhead', 'satellites_overhead', lambda form: {'place': form['place'], 'group': form['group']}),
    ('ISS passes', 'satellite_pass', lambda form: {'place': form['place'], 'name': 'ISS'}),
    ('Fires nearby', 'fire_hotspots', lambda form: {'place': form['place'], 'radius_km': 300}),
    ('Fires worldwide', 'fire_hotspots', lambda form: {}),
    ('Weather map', 'weather_map', lambda form: {'place': form['place'], 'kind': form['kind']}),
    ('Satellite image', 'satellite_image', lambda form: {'place': form['place']}),
    ('Directions', 'route_directions', lambda form: {'origin': form['place'], 'destination': form['destination'], 'mode': form['mode']}),
    ('Bike share', 'bike_share', lambda form: {'place': form['place']}),
    ('Aircraft info', 'aircraft_info', lambda form: {'query': form['aircraft']}),
)
WEATHER_KINDS = {'Clouds (infrared)': 'clouds', 'Visible': 'visible', 'Water vapour': 'water_vapor', 'Radar (US)': 'radar',
                 'Lightning (Americas)': 'lightning'}
SATELLITE_GROUPS = {'Brightest': 'visual', 'Space stations': 'stations', 'Starlink': 'starlink', 'GPS': 'gps-ops',
                    'Weather': 'weather', 'Science': 'science', 'Amateur radio': 'amateur'}
TRAVEL = {'Car': 'car', 'Bike': 'bike', 'Walk': 'foot'}


class IntelligenceExploreViews:
    def build_explore_view(self):
        from app.gui_intelligence import text_box
        from app.gui_intelligence_parts import FlowRow
        p = self.palette
        tab = tk.Frame(self.notebook, bg=p.BLACK)
        self.tabs['explore'] = tab
        self.notebook.add(tab, text='Explore')
        self._explore_photo = None
        self._explore_pending = None
        self._explore_image_path = None
        self.explore_place = StringVar(self, 'here')
        self.explore_destination = StringVar(self, '')
        self.explore_aircraft = StringVar(self, '')
        self.explore_kind = StringVar(self, next(iter(WEATHER_KINDS)))
        self.explore_group = StringVar(self, next(iter(SATELLITE_GROUPS)))
        self.explore_mode = StringVar(self, next(iter(TRAVEL)))
        form = tk.Frame(tab, bg=p.BLACK, pady=4)
        form.pack(fill='x')
        fields = (('Place', self.explore_place, 26), ('To', self.explore_destination, 20), ('Aircraft', self.explore_aircraft, 10))
        for index, (label, variable, width) in enumerate(fields):
            tk.Label(form, text=label, bg=p.BLACK, fg=p.MUTED).grid(row=0, column=index * 2, sticky='w', padx=(0 if index == 0 else 8, 3))
            ttk.Entry(form, textvariable=variable, width=width).grid(row=0, column=index * 2 + 1, sticky='ew')
        form.columnconfigure(1, weight=2)
        form.columnconfigure(3, weight=1)
        choices = tk.Frame(tab, bg=p.BLACK)
        choices.pack(fill='x', pady=(2, 0))
        for label, variable, values in (('Map', self.explore_kind, WEATHER_KINDS), ('Satellites', self.explore_group, SATELLITE_GROUPS),
                                        ('Travel', self.explore_mode, TRAVEL)):
            tk.Label(choices, text=label, bg=p.BLACK, fg=p.MUTED).pack(side='left', padx=(0, 3))
            ttk.Combobox(choices, textvariable=variable, values=list(values), state='readonly',
                         width=max(len(value) for value in values) + 1).pack(side='left', padx=(0, 10))
        button = ttk.Button(choices, text='Use map center', style='Compact.Hayat.TButton', command=self.explore_use_center)
        button.pack(side='right')
        button._hayat_icon = ICONS['Use map center']
        self.explore_buttons = {}
        row = FlowRow(tab, p.BLACK, height=64)
        row.pack(fill='x', pady=4)
        for label, tool, _build in ACTIONS:
            self.explore_buttons[label] = row.add(ttk.Button(row, text=label, style='Compact.Hayat.TButton',
                                                             command=lambda value=label: self.explore_run(value)))
            self.explore_buttons[label]._hayat_icon = ICONS[label]
        self.explore_status = StringVar(self, 'Pick a place ("here", a city, or "latitude, longitude") and a button. '
                                              'Each answer names its public source.')
        tk.Label(tab, textvariable=self.explore_status, wraplength=700, justify='left', anchor='w', bg=p.BLACK,
                 fg=p.WHITE).pack(fill='x')
        body = tk.PanedWindow(tab, orient='horizontal', bg=p.BLACK, sashwidth=5, bd=0)
        body.pack(fill='both', expand=True, pady=4)
        self.explore_body = body
        from app.gui_deep_look import DeepLookView
        self.deep_view = DeepLookView(tab, p, {'event': self.deep_event, 'camera': self.deep_camera, 'point': self.deep_point,
                                               'refresh': lambda value: self.deep_again(value, False),
                                               'reason': lambda value: self.deep_again(value, True),
                                               'atlas': lambda _value: self.world_atlas('')})
        left = tk.Frame(body, bg=p.BLACK)
        self.explore_text = text_box(left, p, 10)
        picture = tk.Frame(body, bg=p.PANEL)
        self.explore_image = tk.Label(picture, bg=p.PANEL, fg=p.MUTED, text='Weather maps, satellite images and\ncamera vehicle counts show here.')
        self.explore_image.pack(fill='both', expand=True)
        self.explore_image.bind('<Configure>', lambda _event: self._explore_fit())
        self.explore_image.bind('<Double-1>', lambda _event: self.explore_open_image())
        body.add(left, minsize=220, stretch='always')
        body.add(picture, minsize=200, width=380, stretch='always')
        if 'space_launches' not in self._explore_tools():
            self.explore_status.set('Explore tools are not loaded in this session.')
        self.notebook.bind('<Configure>', lambda _event: self._fit_tabs(), add='+')

    def _fit_tabs(self):
        """Eleven tab names at 760 px: narrow the side padding just enough that none is cut off."""
        from tkinter import font
        labels = [self.notebook.tab(tab, 'text') for tab in self.notebook.tabs()]
        measure = font.Font(self, font=('Segoe UI Semibold', 9)).measure
        text = sum(measure(label) + 12 for label in labels)
        room = (self.notebook.winfo_width() - text - 4) // max(1, 2 * len(labels))
        padding = max(3, min(12, room))
        if padding != getattr(self, '_tab_padding', None):
            self._tab_padding = padding
            ttk.Style(self).configure('Intelligence.TNotebook.Tab', padding=(padding, 7))

    def _explore_tools(self):
        registry = getattr(getattr(self.gui, 'runtime', None), 'registry', None)
        try:
            return set(registry.names()) if registry is not None else set()
        except Exception:
            return set()

    def explore_form(self):
        place = ' '.join(self.explore_place.get().split()) or 'here'
        return {'place': place, 'destination': ' '.join(self.explore_destination.get().split()),
                'aircraft': self.explore_aircraft.get().strip(), 'kind': WEATHER_KINDS[self.explore_kind.get()],
                'group': SATELLITE_GROUPS[self.explore_group.get()], 'mode': TRAVEL[self.explore_mode.get()]}

    def explore_arguments(self, label):
        """The tool and arguments a button runs, or a reason it cannot run yet."""
        tool, build = next((tool, build) for name, tool, build in ACTIONS if name == label)
        form = self.explore_form()
        if tool == 'route_directions' and not form['destination']:
            return tool, None, 'Type where to go in "To".'
        if tool == 'aircraft_info' and len(form['aircraft']) < 2:
            return tool, None, 'Type a callsign (UAE1), ICAO hex (896450) or registration (A6-EUA) in "Aircraft".'
        return tool, build(form), None

    def explore_run(self, label):
        tool, arguments, problem = self.explore_arguments(label)
        if tool not in self._explore_tools():
            problem = f'{label} is not loaded in this session.'
        if problem:
            self.explore_status.set(problem)
            return False
        if not self.work(tool, arguments):
            return False
        self._explore_pending = label
        self.explore_status.set(f'{label}: working… (public sources can take a few seconds)')
        self.after(300, self._explore_wait)
        return True

    # ----------------------------------------------------------------- the interactive deep look
    def show_deep_view(self, deep):
        """The finding cards replace the text and picture for a deep look; any other answer brings them back."""
        if deep and not self.deep_view.winfo_ismapped():
            self.explore_body.pack_forget()
            self.deep_view.pack(fill='both', expand=True, pady=4)
        elif not deep and self.deep_view.winfo_ismapped():
            self.deep_view.pack_forget()
            self.explore_body.pack(fill='both', expand=True, pady=4)

    def deep_event(self, target):
        try:
            self.show_record(self.manager.event_record(target['id']))
        except ValueError as error:
            self.feedback.set(str(error))
            return
        self.focus_selected()

    def deep_camera(self, target):
        try:
            row = self.manager.event_record(target['id'])['event']
        except ValueError as error:
            self.feedback.set(str(error))
            return
        self.camera_rows[target['id']] = row
        self.camera_selection = target['id']
        if row['details'].get('video_url') or row['details'].get('live_page'):
            self.watch_camera_video()
        else:
            self.open_camera(target['id'])

    def deep_point(self, target):
        self.viewport.longitude, self.viewport.latitude = target['longitude'], target['latitude']
        self.viewport.zoom = max(self.viewport.zoom, target.get('zoom', 8))
        self.viewport.clamp()
        self.view_changed()
        self.notebook.select(self.tabs['map'])

    def deep_again(self, value, reason):
        place = value.get('place') or {}
        arguments = {'place': place.get('name', ''), 'latitude': place['latitude'], 'longitude': place['longitude'],
                     'radius_km': value.get('radius_km', 25), 'reason': reason}
        if self.work('intelligence_deep_look', arguments):
            self._explore_pending = 'Deep look'
            self.explore_status.set('Deep look: ' + ('asking HAYAT\'s model to read the findings…' if reason else 'looking again…'))
            self.after(300, self._explore_wait)

    def _explore_wait(self):
        """`work` reports failures only in the footer; repeat them here so the tab never says "working" forever."""
        if self._closed:
            return
        if self._busy:
            self.after(300, self._explore_wait)
        elif self._explore_pending:
            self.explore_status.set(f'{self._explore_pending}: {self.feedback.get()}')
            self._explore_pending = None

    def explore_use_center(self):
        self.explore_place.set(f'{self.viewport.latitude:.4f}, {self.viewport.longitude:.4f}')

    def handle_explore_result(self, result):
        from app.gui_intelligence import set_text
        from app.intelligence.deep import deep_report
        from app.intelligence.explore import explore_report
        value = result.output or {}
        text = deep_report(value) or explore_report(value) or ''
        self.show_deep_view(bool(value.get('deep_look')))
        if value.get('deep_look'):
            self.deep_view.show(value)
        if value.get('explore') == 'traffic':
            self.camera_message.set(text.split('\n')[0])
        set_text(self.explore_text, text)
        self.explore_status.set(f"{self._explore_pending or 'Explore'}: done · "
                                + (f"situation {value['situation']}" if value.get('deep_look') else value.get('source', '')))
        self._explore_pending = None
        path = value.get('path') if isinstance(value.get('path'), str) else value.get('image')
        self._explore_image_path = path or None
        if path:
            self._explore_fit()
        else:  # a text answer: no earlier picture beside it
            self._explore_photo = None
            self._explore_size = None
            self.explore_image.configure(image='', text='No picture for this answer.')

    def _explore_fit(self):
        if not self._explore_image_path:
            return
        from PIL import Image, ImageTk
        width, height = max(80, self.explore_image.winfo_width() - 8), max(80, self.explore_image.winfo_height() - 8)
        size = (width, height)
        if getattr(self, '_explore_size', None) == (self._explore_image_path, size):
            return
        self._explore_size = (self._explore_image_path, size)
        try:
            with Image.open(self._explore_image_path) as image:
                picture = image.copy()
        except OSError:
            self.explore_image.configure(image='', text='The image could not be opened.')
            return
        picture.thumbnail(size)
        self._explore_photo = ImageTk.PhotoImage(picture, master=self)
        self.explore_image.configure(image=self._explore_photo, text='')

    def explore_open_image(self):
        if self._explore_image_path:
            import os
            os.startfile(self._explore_image_path)  # the owner's default viewer, full size

    def count_vehicles(self):
        """Vehicles in the selected public camera's current still: a count, never an identification."""
        if not self.camera_selection:
            self.feedback.set('Select a camera first.')
            return False
        if 'traffic_count' not in self._explore_tools():
            self.feedback.set('Vehicle counting is not loaded in this session.')
            return False
        if self.work('traffic_count', {'event_id': self.camera_selection}):
            self._explore_pending = 'Count vehicles'
            self.camera_message.set('Counting vehicles in the current still… (the first count loads a 120 MB detector)')
            return True
        return False
