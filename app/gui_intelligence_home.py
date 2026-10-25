"""Compact, locally cached intelligence on the workspace front page."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from app.gui_resources import StringVar
from app.intelligence.view import MapViewport, REGIONS


class IntelligenceHomeCard(tk.Frame):
    def __init__(self, parent, gui, pack=None, show_map=False, movable=False):
        super().__init__(gui.root if movable else parent, bg=gui.PANEL, padx=10, pady=5, highlightthickness=1,
                         highlightbackground=gui.LINE)
        self.gui = gui
        self.dock_parent = parent
        self.panel_layout = None
        self._closed = False
        self._busy = False
        self._next_refresh = 0
        self._timer = None
        self.updates = queue.Queue()
        self.alerts = []
        header = self.header = tk.Frame(self, bg=gui.PANEL)
        header.pack(fill='x')
        self.headline = StringVar(self, 'Global intelligence · reading local snapshots…')
        # Equal shares that wrap when the column is narrow (in the main window's right column "Deep look" was cut).
        from app.gui_panels import ButtonRow
        controls = self.header_controls = ButtonRow(header, gui)
        controls.pack(side='right')
        # Everything about where you are, reasoned into findings (Explore -> Deep look).
        self.deep_button = controls.add('Deep look', lambda: getattr(self.gui, '_open_explore', lambda **_options: None)(deep_look=True),
                                        style='Compact.Hayat.TButton')
        self.deep_button._hayat_icon = 'brain'
        self.customize_button = controls.add('Customize', self.customize, style='Compact.Hayat.TButton')
        self.open_button = controls.add('Open intelligence', self.open, style='Compact.Hayat.TButton')
        title_parent = tk.Frame(header, bg=gui.PANEL) if movable else header
        if movable:
            title_parent.pack(side='top', fill='x', before=controls)
            controls.pack_configure(side='bottom', fill='x')
        title = self.title_label = tk.Label(title_parent, textvariable=self.headline, bg=gui.PANEL, fg=gui.WHITE,
                        anchor='w', font=('Segoe UI Semibold', 10))
        title.pack(side='left', fill='x', expand=True)
        def arrange(event):
            if event.width < 560:
                controls.pack_configure(side='bottom', fill='x')
                title.pack_configure(side='top')
            else:
                controls.pack_configure(side='right', fill='none')
                title.pack_configure(side='left')
        if movable:
            from app.gui_inline_panel import InlinePanel
            self.panel_layout = InlinePanel(self, parent, gui)
            self.panel_layout.install_header(title_parent, title)
        else:
            header.bind('<Configure>', arrange)
        body = self.panel_layout.content if movable else self
        self.summary = StringVar(self)
        self.summary_label = tk.Label(body, textvariable=self.summary, bg=gui.PANEL, fg=gui.MUTED,
                                     anchor='w', justify='left', font=('Segoe UI', 9), wraplength=700)
        self.summary_label.pack(fill='x')
        # A small world map of the newest located events, drawn from local snapshots only.
        self.map = tk.Canvas(body, height=140, bg='#0A1520', highlightthickness=0, cursor='hand2')
        if show_map:
            self.map.pack(fill='x', pady=(6, 4))
        self.map.bind('<Configure>', lambda _event: self.draw_map())
        self.map.bind('<Button-1>', lambda _event: self.open())
        if self.panel_layout is not None:
            for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                self.map.bind(sequence, self.panel_layout.scroller.wheel)
        self._map_rows, self._map_signature, self._land = [], None, None
        self.latest_frame = tk.Frame(body, bg=gui.PANEL)
        self.latest_frame.pack(fill='x', pady=(2, 0))
        self.latest = []
        self.alert_frame = tk.Frame(body, bg=gui.PANEL)
        self.alert_frame.pack(fill='x')
        body.bind('<Configure>', lambda event: self.summary_label.configure(wraplength=max(160, event.width-24)), add='+')
        self._pack = pack or {'side': 'bottom', 'fill': 'x', 'padx': 4, 'pady': 3}
        self.pack(in_=parent, **self._pack)
        self.bind('<Destroy>', lambda event: self.close() if event.widget is self else None, add='+')
        self.poll()

    def open(self, event_id=None):
        self.gui._open_intelligence()
        window = getattr(self.gui, '_intelligence_window', None)
        if event_id and window is not None and window.winfo_exists():
            window.select_event(event_id)

    def customize(self):
        self.open()
        window = getattr(self.gui, '_intelligence_window', None)
        if window is not None and window.winfo_exists():
            window.customize()

    def request_refresh(self):
        self._next_refresh = 0

    def read(self):
        # Display updates read cached evidence only; the independent monitor
        # owns all network fetching and the registry's separate call budget.
        manager = self.gui.runtime.intelligence
        preferences = manager.preferences()['preferences']
        viewport = MapViewport(preferences['zoom'], preferences['center_lon'], preferences['center_lat'])
        viewport.clamp()
        bounds = viewport.bounds() if preferences['only_viewport'] else (
            REGIONS[preferences['region']] if preferences['region'] != 'World' else None)
        from app.intelligence.view import TIME_RANGES
        from app.intelligence.classify import CATEGORIES
        value = manager.view(preferences['query'], preferences['layer'], bounds, preferences['category'],
                             preferences['min_severity'], TIME_RANGES[preferences['time_range']], limit=1, map_limit=150, live_limit=3,
                             map_categories=set(CATEGORIES) - {'cameras'})
        return preferences, value

    def render(self, preferences, value):
        if not preferences['show_home']:
            if self.panel_layout is not None:
                self.panel_layout.hide()
            else:
                self.pack_forget()
            return
        if not self.winfo_manager():
            if self.panel_layout is not None:
                self.panel_layout.show()
            else:
                self._restore_pack()
        selected = [source for source in value['sources'] if not preferences['layer'] or source['key'] == preferences['layer']]
        ready = sum(not source['stale'] for source in selected)
        monitor = 'monitor running' if value['monitor']['running'] else 'monitor stopped'
        self.headline.set(f"Global intelligence · {preferences['region']} · {value['total_matching']} items")
        summary = f"{monitor} · {ready}/{len(selected)} selected feeds current · {value['alert_total_matching']} matching retained alerts"
        if preferences['home_detail'] == 'detailed':
            counts = value['aggregates']['counts_by_feed']
            summary += '\n' + ' · '.join(f'{key}: {count}' for key, count in counts.items())
            summary += f" · {value['aggregates']['unknown_location']} without coordinates"
            if value['events']:
                row = value['events'][0]
                summary += f"\nLatest dated item: {row['title'][:140]} · {row.get('observed_at') or 'date unknown'}"
        summary += '\nProvider events, disaster reports and public camera catalogs; coverage is limited to connected feeds.'
        self.summary.set(summary)
        self.render_latest(value.get('live', []))
        self._map_rows = value.get('mapped', [])
        self.draw_map()
        self.alerts = value['alerts'][:preferences['home_alert_limit']] if preferences['show_alerts'] else []
        signature = [(alert['id'], alert['created_at']) for alert in self.alerts]
        if signature != getattr(self, '_alert_signature', None):
            self._alert_signature = signature
            for child in self.alert_frame.winfo_children():
                child.destroy()
            for alert in self.alerts:
                row = alert['event']
                # Short enough for the right column (the whole line was cut mid-word); the tooltip has all of it.
                title = f"{alert['watch_title']} · {row['title']}"
                title = title if len(title) <= 46 else title[:45].rstrip(' ·,') + '…'
                label = f"{title} · {alert['created_at'][11:16]} UTC"
                from app.gui_icons import icon_image
                from app.gui_help import Tooltip
                button = ttk.Button(self.alert_frame, text=label, style='Compact.Hayat.TButton',
                                    image=icon_image(self, 'alert', self.gui.GOLD), compound='left',
                                    command=lambda identifier=row['id']: self.open(identifier))
                button.pack(fill='x', pady=1)
                Tooltip(button, f"{alert['watch_title']}\n{row['title'][:250]}\nLayer: {row['feed']}\nRecorded: {alert['created_at']}\nClick to inspect the full source evidence.", type(self.gui))

    def draw_map(self):
        if not self.map.winfo_manager():
            return
        width, height = max(120, self.map.winfo_width()), int(self.map['height'])
        signature = (width, [(row['id'], row['severity']) for row in self._map_rows])
        if signature == self._map_signature:
            return
        self._map_signature = signature
        from app.gui_intelligence_parts import MAP_COAST, MAP_LAND, glyph
        from app.intelligence.classify import CATEGORIES
        canvas = self.map
        canvas.delete('all')
        if self._land is None:
            import json
            from pathlib import Path
            path = Path(__file__).parent / 'assets/world-land.json'
            polygons = json.loads(path.read_text(encoding='utf-8'))['polygons'] if path.exists() else []
            # Every third vertex is plenty at this size and keeps the redraw cheap.
            self._land = [polygon[::3] for polygon in polygons if len(polygon) >= 9]
        project = lambda lon, lat: ((lon + 180) / 360 * width, (90 - lat) / 180 * height)
        for polygon in self._land:
            points = [value for lon, lat in polygon for value in project(lon, lat)]
            canvas.create_polygon(points, fill=MAP_LAND, outline=MAP_COAST, width=.5)
        for row in sorted(self._map_rows, key=lambda item: item['severity_rank']):
            x, y = project(row['longitude'], row['latitude'])
            _title, colour, shape = CATEGORIES[row['category']]
            radius = {'high': 4.5, 'medium': 3.5}.get(row['severity'], 2.5)
            if row['severity'] in ('high', 'medium'):
                canvas.create_oval(x - radius - 3, y - radius - 3, x + radius + 3, y + radius + 3, outline=colour, width=1)
            glyph(canvas, shape, x, y, radius, colour)
        canvas.create_text(6, height - 6, text=f"{len(self._map_rows)} located events · click to open", anchor='sw',
                           fill='#8FA6BA', font=('Segoe UI', 8))

    def _restore_pack(self):
        options = dict(self._pack)
        depth = getattr(self.gui, 'depth_nav', None)
        if 'before' not in options and 'after' not in options and depth is not None and depth.master is self.master:
            options['before'] = depth
        self.pack(**options)

    def render_latest(self, rows):
        from app.gui_intelligence_parts import category_glyph
        from app.intelligence.classify import SEVERITY_COLOURS
        from app.intelligence.view import relative_time
        signature = [(row['id'], relative_time(row.get('observed_at')), row['badge']) for row in rows]
        if signature == getattr(self, '_latest_signature', None):
            return
        self._latest_signature, self.latest = signature, rows
        for child in self.latest_frame.winfo_children():
            child.destroy()
        gui = self.gui
        for row in rows:
            line = tk.Frame(self.latest_frame, bg=gui.PANEL, cursor='hand2')
            line.pack(fill='x', pady=1)
            parts = [category_glyph(line, row['category'], gui.PANEL, 16)]
            if self.panel_layout is not None:
                for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                    parts[0].bind(sequence, self.panel_layout.scroller.wheel)
            parts.append(tk.Label(line, text=row['badge'], bg=gui.PANEL_2, fg=SEVERITY_COLOURS[row['severity']],
                                  font=('Segoe UI Semibold', 8), padx=5))
            parts.append(tk.Label(line, text=relative_time(row.get('observed_at')), bg=gui.PANEL, fg=gui.MUTED, font=('Segoe UI', 8)))
            title = row['headline'] + (f" · {row['subtitle']}" if row['subtitle'] else '')
            parts.append(tk.Label(line, text=title[:150], bg=gui.PANEL, fg=gui.WHITE, font=('Segoe UI', 9), anchor='w'))
            parts[0].pack(side='left', padx=(0, 6))
            parts[1].pack(side='right', padx=(6, 0))
            parts[2].pack(side='right')
            parts[3].pack(side='left', fill='x', expand=True)
            for widget in (line, *parts):
                widget.bind('<Button-1>', lambda _event, identifier=row['id']: self.open(identifier))

    def poll(self):
        if self._closed:
            return
        while True:
            try:
                value = self.updates.get_nowait()
            except queue.Empty:
                break
            self._busy = False
            if isinstance(value, Exception):
                self.summary.set('Local intelligence display unavailable: ' + str(value)[:160])
            else:
                self.render(*value)
        if not self._busy and time.monotonic() >= self._next_refresh:
            self._busy = True
            self._next_refresh = time.monotonic() + 10
            def run():
                try:
                    self.updates.put(self.read())
                except Exception as exc:
                    self.updates.put(exc)
            threading.Thread(target=run, daemon=True, name='hayat-intelligence-home').start()
        self._timer = self.after(500, self.poll)

    def close(self):
        self._closed = True
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
