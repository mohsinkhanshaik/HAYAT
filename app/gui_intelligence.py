"""Global intelligence workspace: live map, event inspector, assessment and feed."""
import json
from collections import Counter
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from app.gui_intelligence_parts import (MAP_COAST, MAP_GRID, MAP_LAND, MAP_OCEAN, MAP_TEXT, FlowRow, LiveFeed,
                                        blend, category_glyph, clip, draw_globe, glyph, round_rect)
from app.gui_intelligence_resources import IntelligenceResourceViews
from app.gui_intelligence_map import IntelligenceMapViews
from app.gui_resources import BooleanVar, StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import MARGIN, place_over, placement, work_area
from app.intelligence.classify import CATEGORIES, SEVERITY_COLOURS, SEVERITY_LABEL
from app.intelligence.feeds import ALL_FEEDS, configured
from app.intelligence.view import REGIONS, TIME_RANGES, MapViewport, format_point, relative_time

SEVERITY_FILTERS = {'All severities': '', 'High only': 'high', 'Medium and above': 'medium', 'Low and above': 'low'}
CATEGORY_FILTERS = {'All events': '', **{title: key for key, (title, _colour, _shape) in CATEGORIES.items()}}
LEVEL_COLOURS = {'confidence': {'high': '#4ADE80', 'medium': '#F2A541', 'low': '#F05D5E'},
                 'relevance': {'high': '#F5C451', 'medium': '#F2A541', 'low': '#93A3B5'}}
DETAIL_LABELS = (('magnitude', 'Magnitude'), ('depth_km', 'Depth (km)'), ('alert', 'PAGER alert'), ('alert_level', 'Alert level'),
                 ('severity', 'Provider severity'), ('magnitude_unit', 'Unit'), ('vendor', 'Vendor'), ('product', 'Product'),
                 ('description', 'Description'), ('required_action', 'Required action'), ('due_date', 'Due date'),
                 ('ransomware_use', 'Ransomware use'), ('country', 'Country'), ('city', 'City'), ('county', 'County'),
                 ('road', 'Road'), ('direction', 'Direction'), ('available', 'Publisher available'), ('start_time', 'Start'),
                 ('end_time', 'End'), ('location_basis', 'Location basis'), ('limitation', 'Limitation'))


def text_box(parent, palette, height=8):
    box = DarkScrolledText(parent, height=height, width=38, wrap='word', bg=palette.PANEL, fg=palette.WHITE,
                           font=('Segoe UI', 10), padx=10, pady=8, state='disabled')
    box.pack(fill='both', expand=True)
    return box


def set_text(box, content):
    if box.get('1.0', 'end-1c') != content:
        box.configure(state='normal')
        box.delete('1.0', 'end')
        box.insert('1.0', content)
        box.configure(state='disabled')


def add_scrollbars(tree, parent):
    vertical = ttk.Scrollbar(parent, command=tree.yview)
    horizontal = ttk.Scrollbar(parent, orient='horizontal', command=tree.xview)
    vertical.pack(side='right', fill='y')
    horizontal.pack(side='bottom', fill='x')
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.pack(in_=parent, fill='both', expand=True)


def cluster_markers(visible, zoom, selected_id=None, cell=52, minimum=3):
    """Group dense markers into clusters; the selected marker stays single.

    About 1,000 markers drew as overlapping halos over the US and Europe, hiding
    the rest. A cluster holding a high-severity report is ringed in its colour.
    Returns (singles, clusters) with clusters as (x, y, rows).
    """
    if zoom >= 8:
        cell, minimum = 22, 2  # Coincident reports still need a route to every member.
    singles, groups = [], {}
    for row, x, y in visible:
        if row['id'] == selected_id:
            singles.append((row, x, y))
        else:
            groups.setdefault((int((x + cell / 2) // cell), int((y + cell / 2) // cell)), []).append((row, x, y))
    clusters = []
    for members in groups.values():
        if len(members) < minimum:
            singles.extend(members)
        else:
            clusters.append((sum(x for _row, x, _y in members) / len(members), sum(y for _row, _x, y in members) / len(members),
                             [row for row, _x, _y in members]))
    return singles, clusters


def without_provider(subtitle, provider):
    """A subtitle without the provider the Source column already shows."""
    text = str(subtitle or '')
    if provider and text.startswith(provider):
        text = text[len(provider):].lstrip(' ·')
    return text


def bounds_text(bbox):
    """[west, south, east, north] as '39.0–43.0°N, 26.4–31.6°E'."""
    if not bbox or len(bbox) != 4:
        return ''
    west, south, east, north = bbox
    lat = lambda value: f"{abs(value):.1f}°{'N' if value >= 0 else 'S'}"
    lon = lambda value: f"{abs(value):.1f}°{'E' if value >= 0 else 'W'}"
    return f"{lat(south)}–{lat(north)}, {lon(west)}–{lon(east)}"


def readable_time(value):
    """'3 h ago · 2026-10-10 22:04 UTC' instead of '2026-10-10T22:04:41.340355+00:00'."""
    if not value:
        return 'Unknown'
    return ' · '.join(part for part in (relative_time(value), str(value)[:16].replace('T', ' ') + ' UTC') if part)


def alert_title(event):
    """USGS titles name only the place; the magnitude is what the watch was about."""
    magnitude = (event.get('details') or {}).get('magnitude')
    title = str(event.get('title') or event.get('id') or 'Alert')
    if isinstance(magnitude, (int, float)) and not title.upper().startswith('M'):
        return f"M{magnitude:.1f} · {title}"
    return title


def source_order(source):
    return (0 if source['status'] == 'failed' else 1 if source['status'] == 'needs_key' else 2 if source['stale'] else 3,
            str(source.get('title') or source['key']).casefold())


def sources_summary(sources):
    """What the Sources tab says before a feed is chosen."""
    failed = [source for source in sources if source['status'] == 'failed']
    keyed = [source for source in sources if source['status'] == 'needs_key']
    stale = [source for source in sources if source['stale'] and source not in failed and source not in keyed]
    current = len(sources) - len(failed) - len(keyed) - len(stale)
    lines = [f"{current} of {len(sources)} public feeds are current."]
    if failed:
        lines.append(f"{len(failed)} failed at the last attempt (listed first): "
                     + '; '.join(f"{source['title']} — {str(source.get('error') or 'no detail')[:120]}" for source in failed[:6]))
    if keyed:
        lines.append(f"{len(keyed)} need a free key (add it in Studios → Keys) before they can connect: "
                     + ', '.join(source['title'] for source in keyed[:8]) + ('…' if len(keyed) > 8 else ''))
    if stale:
        lines.append(f"{len(stale)} are older than their refresh interval: " + ', '.join(source['title'] for source in stale[:8])
                     + ('…' if len(stale) > 8 else ''))
    lines.append('Select a feed for its publisher, licence, item count, last change and next check.')
    return '\n\n'.join(lines)


def source_detail(source):
    """One feed in words, with the full record after it (as plain lines, not JSON)."""
    rows = [('Feed', f"{source['title']} ({source['key']})"),
            ('State', source['status'] + (' · stale' if source['stale'] else '')),
            ('Items', source.get('count', 0)),
            ('Last success', ' · '.join(part for part in (relative_time(source.get('last_success_at')),
                                                         str(source.get('last_success_at') or '')[:16].replace('T', ' ')) if part) or 'never'),
            ('Next check', str(source.get('next_attempt_at') or '—')[:16].replace('T', ' ') + (' UTC' if source.get('next_attempt_at') else '')),
            ('Interval', f"{source.get('interval_seconds', 0) // 60} min"),
            ('Publisher', source.get('source_url') or source.get('url') or ''),
            ('Licence', source.get('license') or source.get('licence') or ''),
            ('Error', source.get('error') or '')]
    change = source.get('change') or {}
    if change:
        rows.append(('Last change', f"{change.get('added_count', 0)} added, {change.get('changed_count', 0)} revised, "
                                    f"{change.get('missing_count', 0)} no longer in the feed"))
    text = '\n'.join(f"{label}: {value}" for label, value in rows if value not in ('', None))
    from app.plain_output import describe_full

    return text + '\n\nFull record\n' + describe_full(source)


def nice_distance(km):
    """Largest 1/2/5 × 10ⁿ distance not above km."""
    if km <= 0:
        return 0
    power = 10 ** math.floor(math.log10(km))
    return max(step * power for step in (1, 2, 5) if step * power <= km)


class IntelligenceWindow(IntelligenceMapViews, IntelligenceResourceViews, tk.Toplevel):
    def __init__(self, gui):
        tk.Toplevel.__init__(self, gui.root)
        self.withdraw()
        self.gui, self.manager, self.palette = gui, gui.runtime.intelligence, type(gui)
        p = self.palette
        self.updates = queue.Queue()
        self._closed = self._busy = False
        self._draw_handle = self._poll_id = self._operation_done = self._drag = None
        self._signal_id = None
        self._window_drag = None
        self.filters_visible = BooleanVar(self, True)
        self._filter_bar_manual = False
        self._record_token, self._record_signature, self._render_key, self._data_signature = 0, None, None, None
        self._last_output, self._hover_id = None, None
        self._clusters = []
        self._analyses = {}
        self.rows, self.map_rows, self.alert_rows, self.watches, self.sources = {}, {}, {}, {}, {}
        self.selected_record, self.current_dashboard, self.offset = None, None, 0
        self.polygons = []
        from pathlib import Path
        basemap = Path(__file__).parent / 'assets/world-land.json'
        if basemap.exists():
            self.polygons = json.loads(basemap.read_text(encoding='utf-8'))['polygons']
        saved = self.manager.preferences()['preferences']
        self.viewport = MapViewport(saved['zoom'], saved['center_lon'], saved['center_lat'])
        self.viewport.clamp()
        self.query = StringVar(self, saved['query'])
        self.layer = StringVar(self, saved['layer'] or 'All layers')
        self.region = StringVar(self, saved['region'])
        self.only_viewport = BooleanVar(self, saved['only_viewport'])
        self.category = StringVar(self, next(title for title, key in CATEGORY_FILTERS.items() if key == saved['category']))
        self.severity = StringVar(self, next(title for title, key in SEVERITY_FILTERS.items() if key == saved['min_severity']))
        self.time_range = StringVar(self, saved['time_range'])
        self.layer_vars = {key: BooleanVar(self, key not in saved['hidden_layers']) for key in CATEGORIES}
        self.assessment_view = StringVar(self, 'summary')
        self.feedback = StringVar(self, 'Direct source observations · unknown locations are not plotted · Natural Earth public-domain basemap')
        self.status = StringVar(self)
        self.title('HAYAT · Global intelligence')
        self.configure(bg=p.BLACK)
        # A workspace needs normal minimize/maximize and taskbar controls;
        # a transient dialog stays over HAYAT and loses those controls on Windows.
        self.resizable(True, True)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', self.escape_map)
        self._styles()
        self._build_header()
        self._build_filters()
        self._build_footer()
        self._build_body()
        self.build_resource_views()
        for variable in (self.query, self.layer, self.category, self.severity, self.time_range):
            variable.trace_add('write', lambda *_: self.filter_changed())
        self.region.trace_add('write', lambda *_: self.region_changed())
        self.bind('<Configure>', self._responsive, add='+')
        self.body.bind('<Configure>', self._responsive, add='+')
        place_over(self, gui.root, self._window_size(work_area(gui.root)))
        self.deiconify()
        self.render(force=True)
        from app.gui_icons import apply_icons
        apply_icons(self, p)
        self._icon_overrides()
        self._poll_id = self.after(1000, self.poll)
        self._animate_signal()

    def _window_size(self, area):
        width, height = area[2] - area[0], area[3] - area[1]
        available_width = max(120, width - 2 * MARGIN)
        available_height = max(80, height - 2 * MARGIN - 40)
        minimum = (min(760, available_width), min(580, available_height))
        self.minsize(*minimum)
        return (max(minimum[0], min(1200, int(width * .85))),
                max(minimum[1], min(780, int(height * .82))))

    def fit_window(self):
        """Bring the whole window into the usable part of its current monitor."""
        area = work_area(self)
        size = self._window_size(area)
        frame = (self.winfo_x(), self.winfo_y(), self.winfo_width(), self.winfo_height())
        self.state('normal')
        self.geometry(placement(size, frame, area))

    def show_filter_bar(self, visible=True, *, manual=True):
        self.filters_visible.set(visible)
        if manual:
            self._filter_bar_manual = True
        if visible:
            self.filter_row.pack(fill='x', padx=16, pady=(0, 6), before=self.body)
        else:
            self.filter_row.pack_forget()

    def toggle_window_size(self, _event=None):
        self.state('normal' if self.state() == 'zoomed' else 'zoomed')
        return 'break'

    def _start_window_drag(self, event):
        if self.state() not in {'normal', 'zoomed'}:
            return 'break'
        self._window_drag = (event.x_root, event.y_root, self.winfo_x(), self.winfo_y())
        return 'break'

    def _move_window(self, event):
        if self._window_drag is not None:
            pointer_x, pointer_y, x, y = self._window_drag
            if self.state() == 'zoomed':
                if abs(event.x_root - pointer_x) + abs(event.y_root - pointer_y) < 4:
                    return 'break'
                share = min(1.0, max(0.0, (pointer_x - x) / self.winfo_width()))
                offset_y = min(pointer_y - y, self.header.winfo_height() + 40)
                self.state('normal')
                self.update_idletasks()
                x = event.x_root - round(share * self.winfo_width())
                y = event.y_root - offset_y
                self._window_drag = (event.x_root, event.y_root, x, y)
                pointer_x, pointer_y = event.x_root, event.y_root
            # An explicit '+' keeps negative desktop coordinates absolute;
            # bare negative offsets anchor a window to the screen's far edge.
            self.geometry(f'+{x + event.x_root - pointer_x}+{y + event.y_root - pointer_y}')
        return 'break'

    def _end_window_drag(self, _event=None):
        self._window_drag = None
        return 'break'

    def _animate_signal(self):
        """Animate only small decorative overlays; evidence and marker positions stay fixed."""
        self._signal_id = None
        if self._closed:
            return
        moving = getattr(getattr(self.gui, 'depth_nav', None), 'motion', True)
        self.globe.delete('signal')
        self.map.delete('signal')
        if moving and self.winfo_ismapped():
            phase = time.monotonic() * .8
            x, y = 23 + 19 * math.cos(phase), 23 + 19 * math.sin(phase)
            self.globe.create_oval(x-2, y-2, x+2, y+2, fill=self.palette.GOLD, outline='', tags='signal')
            row = (self.selected_record or {}).get('event') or {}
            if row.get('latitude') is not None and row.get('id') in self.map_rows:
                x, y = self.viewport.project(row['longitude'], row['latitude'], *self.size())
                radius = 13 + 6 * (phase % 1)
                self.map.create_oval(x-radius, y-radius, x+radius, y+radius,
                                     outline=blend(self.palette.GOLD, MAP_OCEAN, phase % 1), tags='signal')
        self._signal_id = self.after(100 if moving and self.winfo_ismapped() else 600, self._animate_signal)

    # ----- construction -------------------------------------------------
    ICONS = {'Fit results': 'globe', 'In view': 'grid', 'Expand map': 'expand', 'Show details': 'expand',
             'Clear filters': 'refresh', 'Zoom +': 'zoom_in', 'Zoom −': 'zoom_out', 'Reset': 'refresh', 'Fit region': 'globe', 'Watch view': 'eye',
             'Save view': 'file', 'Saved views…': 'file', 'Customize': 'settings', 'World atlas': 'globe', 'Weather here': 'cloud',
             'Focus on map': 'pin', 'Open source': 'link', 'Generate brief': 'file', 'Ask Hayat in chat': 'chat',
             'Full evidence': 'code', 'Copy location': 'copy', 'Add watch': 'eye', 'Analyze with HAYAT': 'brain',
             'Refresh feeds': 'refresh', 'Pause monitor': 'stop', 'Start monitor': 'play', 'Load cameras': 'refresh',
             'Load snapshot': 'image', 'Open in atlas': 'globe', 'Open publisher page': 'link', 'Near selected event': 'pin',
             'Watch video': 'play', 'Find streams': 'eye',
             'Previous': 'history', 'Next': 'play', 'Previous 200': 'history', 'Next 200': 'play',
             'View all events →': 'grid', 'Use selected point': 'pin', 'Use map center': 'globe',
             'Get point weather': 'cloud', 'Recall observations': 'history', 'Analyze current filters': 'brain',
             'Generate brief for current filters': 'file', 'Show saved monitor brief': 'file',
             'Edit selected': 'settings', 'Delete selected': 'stop'}

    def _icon_overrides(self, buttons=None):
        """Distinct icons for this window, so icon-only compact toolbars stay readable."""
        from app.gui_icons import icon_image
        p = self.palette
        toolbars = {*self.header_buttons, *self.map_actions, *self.card_actions.items}
        for button in buttons or self._buttons(self):
            kind = self.ICONS.get(str(button.cget('text')))
            if kind:
                accent = str(button.cget('style')).endswith('HayatAccent.TButton')
                colour = p.BLACK if accent else p.ALERT if kind == 'stop' else p.OLIVE_SOFT if kind == 'play' else p.ROYAL_SOFT
                # Remembered on the button: HAYAT re-applies icons app-wide and would otherwise
                # replace these with its text guess (a generic "tool" icon for "Add watch").
                button._hayat_icon = kind
                button._hayat_compound = 'image' if self._compact and button in toolbars else 'left'
                button.configure(image=icon_image(button, kind, colour), compound=button._hayat_compound)

    @classmethod
    def _buttons(cls, widget):
        for child in widget.winfo_children():
            if isinstance(child, (tk.Button, ttk.Button)):
                yield child
            yield from cls._buttons(child)

    def _styles(self):
        p = self.palette
        style = ttk.Style(self)
        for name, padding in (('Chip.Toolbutton', (9, 3)), ('Segment.Toolbutton', (12, 5))):
            style.configure(name, background=p.PANEL_2, foreground=p.WHITE, padding=padding, relief='flat',
                            borderwidth=1, bordercolor=p.LINE, font=('Segoe UI Semibold', 9))
            style.map(name, background=[('selected', p.GOLD), ('active', p.PANEL_HOVER)],
                      foreground=[('selected', p.BLACK)])
        style.configure('Intelligence.TNotebook.Tab', padding=(12, 7), font=('Segoe UI Semibold', 9))
        style.configure('Intel.Treeview', rowheight=26, background=p.PANEL, fieldbackground=p.PANEL,
                        foreground=p.WHITE, bordercolor=p.LINE, font=('Segoe UI', 9))
        style.map('Intel.Treeview', background=[('selected', p.GOLD)], foreground=[('selected', p.BLACK)])
        style.configure('Intel.Treeview.Heading', background=p.PANEL_2, foreground=p.WHITE,
                        font=('Segoe UI Semibold', 9))

    def _build_header(self):
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill='x')
        self.header = header
        globe = tk.Canvas(header, width=46, height=46, bg=p.BLACK, highlightthickness=0)
        globe.pack(side='left', padx=(0, 12))
        self.globe = globe
        draw_globe(globe, p.ROYAL_SOFT, p.GOLD)
        words = tk.Frame(header, bg=p.BLACK)
        words.pack(side='left', fill='x', expand=True)
        self.title_label = tk.Label(words, text='Global Intelligence', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 19), anchor='w')
        self.title_label.pack(fill='x')
        self.subtitle = tk.Label(words, text='Real-time world events, natural hazards, cyber threats and space weather from public agencies',
                                 bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w')
        self.subtitle.pack(fill='x')
        controls = tk.Frame(header, bg=p.BLACK)
        controls.pack(side='right')
        live = tk.Frame(controls, bg=p.BLACK)
        live.pack(side='left', padx=(0, 12))
        self.live_label = tk.Label(live, text='● Live feeds', bg=p.BLACK, fg='#4ADE80', font=('Segoe UI Semibold', 9), anchor='w')
        self.live_label.pack(fill='x')
        self.updated_label = tk.Label(live, text='Last updated —', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8), anchor='w')
        self.updated_label.pack(fill='x')
        self.refresh_button = ttk.Button(controls, text='Refresh feeds', command=self.fetch, style='HayatAccent.TButton')
        self.refresh_button.pack(side='left', padx=3)
        add = ttk.Button(controls, text='Add watch', command=self.add_watch, style='Hayat.TButton')
        add.pack(side='left', padx=3)
        self.control = ttk.Button(controls, text='Pause monitor', command=self.toggle, style='Hayat.TButton')
        self.control.pack(side='left', padx=3)
        self.header_buttons = (self.refresh_button, add, self.control)
        self.window_button = ttk.Menubutton(controls, text='Window ▾', style='Compact.Hayat.TButton')
        self.window_button.pack(side='left', padx=(6, 0))
        menu = tk.Menu(self.window_button, tearoff=False, bg=p.PANEL, fg=p.WHITE,
                       activebackground=p.GOLD, activeforeground=p.BLACK)
        menu.add_command(label='Fit window to screen', command=self.fit_window)
        menu.add_command(label='Maximize / restore', command=self.toggle_window_size)
        menu.add_command(label='Minimize', command=self.iconify)
        menu.add_separator()
        menu.add_checkbutton(label='Show filter bar', variable=self.filters_visible,
                             command=lambda: self.show_filter_bar(self.filters_visible.get()))
        menu.add_separator()
        menu.add_command(label='Close', command=self.close)
        self.window_button.configure(menu=menu)
        from app.gui_help import Tooltip
        for widget in (header, globe, words, self.title_label, self.subtitle):
            widget.configure(cursor='fleur')
            widget.bind('<ButtonPress-1>', self._start_window_drag)
            widget.bind('<B1-Motion>', self._move_window)
            widget.bind('<ButtonRelease-1>', self._end_window_drag)
            widget.bind('<Double-Button-1>', self.toggle_window_size)
        self.title_label._intel_tip = Tooltip(self.title_label,
            'Drag this header to move the window. Double-click to maximize or restore. Resize from an edge or bottom corner.', p)

    def _build_filters(self):
        p = self.palette
        self.filter_row = FlowRow(self, p.BLACK, gap=8, height=56, padx=0)
        self.filter_row.pack(fill='x', padx=16, pady=(0, 6))
        self.filter_captions = []
        def field(label, widget_factory):
            box = tk.Frame(self.filter_row, bg=p.BLACK)
            caption = tk.Label(box, text=label, bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8), anchor='w')
            caption.pack(fill='x')
            self.filter_captions.append(caption)
            widget = widget_factory(box)
            widget.pack(fill='x')
            self.filter_row.add(box)
            return widget
        self.search_entry = field('Search', lambda box: ttk.Entry(box, textvariable=self.query, width=26))
        field('Category', lambda box: ttk.Combobox(box, textvariable=self.category, values=list(CATEGORY_FILTERS), state='readonly', width=15))
        field('Region', lambda box: ttk.Combobox(box, textvariable=self.region, values=list(REGIONS), state='readonly', width=14))
        field('Severity', lambda box: ttk.Combobox(box, textvariable=self.severity, values=list(SEVERITY_FILTERS), state='readonly', width=16))
        field('Time range', lambda box: ttk.Combobox(box, textvariable=self.time_range, values=list(TIME_RANGES), state='readonly', width=13))
        field('Source feed', lambda box: ttk.Combobox(box, textvariable=self.layer, values=['All layers', *[feed.key for feed in ALL_FEEDS if configured(feed)]],
                                                      state='readonly', width=19))
        self.clear_filters_button = self.filter_row.add(ttk.Button(self.filter_row, text='Clear filters',
            command=self.clear_map_filters, style='Compact.Hayat.TButton'))
        from app.gui_help import Tooltip
        self.search_entry._intel_tip = Tooltip(self.search_entry, 'Search observations (Ctrl+F)', p)

    def _build_footer(self):
        p = self.palette
        footer = tk.Frame(self, bg=p.PANEL, padx=14, pady=5)
        footer.pack(side='bottom', fill='x')
        self.footer = footer
        self.resize_grip = ttk.Sizegrip(footer)
        self.resize_grip.pack(side='right', anchor='se', padx=(8, 0))
        self.status_label = tk.Label(footer, textvariable=self.status, bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9), anchor='w')
        self.status_label.pack(side='left')
        self.feedback_label = tk.Label(footer, textvariable=self.feedback, bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9),
                                       anchor='e', justify='right')
        self.feedback_label.pack(side='right', fill='x', expand=True)
        footer.bind('<Configure>', lambda event: self.feedback_label.configure(
            wraplength=max(200, event.width - (30 if self._compact else 520))))

    def _build_body(self):
        p = self.palette
        self.body = tk.PanedWindow(self, orient='horizontal', bg=p.BLACK, sashwidth=6, bd=0, showhandle=False)
        self.body.pack(fill='both', expand=True, padx=(16, 10), pady=(0, 8))
        left = tk.Frame(self.body, bg=p.BLACK)
        self.live = LiveFeed(self.body, p, self.select_event, lambda: self.notebook.select(self.tabs['events']))
        self.body.add(left, minsize=480, stretch='always')
        self.body.add(self.live, minsize=270, width=340, stretch='never')
        self._live_hidden = self._compact = False
        self.notebook = ttk.Notebook(left, style='Intelligence.TNotebook')
        self.notebook.pack(fill='both', expand=True)
        self.tabs = {}
        for key, label in (('map', 'Map'), ('events', 'Events'), ('alerts', 'Alerts'), ('watches', 'Watches'),
                           ('sources', 'Sources'), ('assessment', 'Assessment'), ('reports', 'Reports')):
            self.tabs[key] = tk.Frame(self.notebook, bg=p.BLACK)
            self.notebook.add(self.tabs[key], text=label)
        self._build_map_tab()
        self._build_tables()
        self._build_assessment_tab()
        self._build_reports_tab()

    def _build_map_tab(self):
        p = self.palette
        tab = self.tabs['map']
        split = tk.PanedWindow(tab, orient='vertical', bg=p.BLACK, sashwidth=6, bd=0)
        split.pack(fill='both', expand=True, pady=(6, 0))
        self.map_split = split
        map_area = tk.Frame(split, bg=p.BLACK)
        cards = tk.Frame(split, bg=p.BLACK)
        self.map_area = map_area
        split.add(map_area, minsize=170, stretch='always')
        split.add(cards, minsize=220, stretch='never', height=340)
        self.map_toolbar = FlowRow(map_area, p.BLACK, height=34)
        self.map_toolbar.pack(fill='x')
        self.build_map_tools()
        self.build_map_summary(map_area)
        # The toggle joins the toolbar and the hint is drawn on the map: a row of its own
        # cost the map about 30 px on a 900 px screen.
        self.map_view_toggle = self.map_toolbar.add(ttk.Checkbutton(self.map_toolbar, text='Filter to visible map',
                                                                    variable=self.only_viewport, command=self.filter_changed))
        self.map_hint = StringVar(self, self.MAP_HINT)
        self.map_hint.trace_add('write', lambda *_: self._draw_hint())
        frame = tk.Frame(map_area, bg=p.LINE, padx=1, pady=1)
        frame.pack(fill='both', expand=True, pady=(4, 2))
        self.map = tk.Canvas(frame, bg=MAP_OCEAN, height=250, highlightthickness=1, highlightbackground=MAP_OCEAN,
                             highlightcolor=p.GOLD, takefocus=True)
        self.map.pack(fill='both', expand=True)
        self._build_legend()
        self._bind_map()
        self.cards_frame = cards
        actions = FlowRow(cards, p.BLACK, height=34)
        actions.pack(fill='x', pady=(4, 4))
        self.card_actions = actions
        self.focus_button = ttk.Button(actions, text='Focus on map', command=self.focus_selected, state='disabled', style='Compact.Hayat.TButton')
        self.source_button = ttk.Button(actions, text='Open source', command=self.open_source, state='disabled', style='Compact.Hayat.TButton')
        self.brief_button = ttk.Button(actions, text='Generate brief', command=self.brief, style='Compact.Hayat.TButton')
        self.ask_button = ttk.Button(actions, text='Ask Hayat in chat', command=self.ask_hayat, state='disabled', style='Compact.Hayat.TButton')
        self.raw_button = ttk.Button(actions, text='Full evidence', command=self.details, style='Compact.Hayat.TButton')
        self.copy_button = ttk.Button(actions, text='Copy location', command=self.copy_location, state='disabled', style='Compact.Hayat.TButton')
        for button in (self.focus_button, self.source_button, self.brief_button, self.ask_button, self.raw_button, self.copy_button):
            actions.add(button)
        # Compact windows show one card at a time beside the map.
        self.card_choice = StringVar(self, 'event')
        self.card_switch = tk.Frame(cards, bg=p.BLACK)
        for value, label in (('event', 'Selected event'), ('assessment', 'AI assessment')):
            ttk.Radiobutton(self.card_switch, text=label, value=value, variable=self.card_choice, command=self.show_card,
                            style='Segment.Toolbutton').pack(side='left', padx=(0, 4))
        row = tk.Frame(cards, bg=p.BLACK)
        row.pack(fill='both', expand=True)
        self.card_row = row
        row.columnconfigure(0, weight=1, uniform='cards')
        row.columnconfigure(1, weight=1, uniform='cards')
        row.rowconfigure(0, weight=1)
        self._build_event_card(row)
        self._build_assessment_card(row)
        split.bind('<Map>', lambda _event: self._place_map_sash(), add='+')
        split.bind('<Configure>', lambda _event: self.after_idle(self._place_map_sash), add='+')
        split.bind('<ButtonRelease-1>', lambda _event: setattr(self, '_sash_user', True), add='+')

    MAP_HINT = 'Wheel zoom · Drag pan · Enter browse · F11 expand · Ctrl+F search'

    @staticmethod
    def _cards_height(height):
        """Keep 38% (230-340 px) for scrollable details; expand the map when needed.

        A fixed 380 px for the cards left the map a 130 px strip on a 900 px screen;
        40% still left it about 250 px (2026-10-10 screenshots on real feeds).
        """
        return max(230, min(340, int(height * .38)))

    def _place_map_sash(self):
        height = self.map_split.winfo_height()
        # The split follows the window (the header folds after the first layout, and the
        # pane re-lays itself out once), until the owner drags the sash themselves.
        # 220 px of map + 230 px of cards + the sash.
        if self._closed or self._map_focus or getattr(self, '_sash_user', False):
            return
        if self._compact:
            width = self.map_split.winfo_width()
            if width > 610:
                target = max(304, min(width - 306, int(width * .52)))
                if abs(self.map_split.sash_coord(0)[0] - target) > 8:
                    self.map_split.sash_place(0, target, 0)
            return
        if height < 456:
            return
        target = max(220, height - self._cards_height(height))
        if abs(self.map_split.sash_coord(0)[1] - target) > 8:
            self._sash_placed = True
            self.map_split.sash_place(0, 0, target)

    def _build_legend(self):
        p = self.palette
        legend = tk.Frame(self.map, bg=blend(p.PANEL, MAP_OCEAN, .2), padx=8, pady=6, highlightthickness=1, highlightbackground=MAP_COAST)
        self.legend, self._legend_chosen = legend, False
        top = tk.Frame(legend, bg=legend['bg'])
        top.pack(fill='x')
        self.legend_title = tk.Label(top, text='Layers', bg=legend['bg'], fg=p.WHITE, font=('Segoe UI Semibold', 9))
        self.legend_title.pack(side='left')
        self.legend_toggle = ttk.Button(top, text='+', width=2, command=self.toggle_legend, style='Compact.Hayat.TButton')
        self.legend_toggle.pack(side='right', padx=(10, 0))
        self.legend_body = tk.Frame(legend, bg=legend['bg'])
        self.legend_canvas = tk.Canvas(self.legend_body, width=175, height=240, bg=legend['bg'], highlightthickness=0)
        scroll = ttk.Scrollbar(self.legend_body, command=self.legend_canvas.yview)
        scroll.pack(side='right', fill='y')
        self.legend_canvas.configure(yscrollcommand=scroll.set)
        self.legend_canvas.pack(side='left', fill='both', expand=True)
        layers = tk.Frame(self.legend_canvas, bg=legend['bg'])
        self.legend_layers = layers
        self.legend_canvas.create_window(0, 0, window=layers, anchor='nw')
        layers.bind('<Configure>', lambda _event: self.legend_canvas.configure(scrollregion=self.legend_canvas.bbox('all')))
        def scroll_layers(event):
            self.legend_canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
            return 'break'
        self.legend_canvas.bind('<MouseWheel>', scroll_layers)
        def reveal_control(event):
            y = event.widget.winfo_rooty() - layers.winfo_rooty()
            bottom = y + event.widget.winfo_height()
            top = self.legend_canvas.canvasy(0)
            height = self.legend_canvas.winfo_height()
            total = max(1, layers.winfo_reqheight())
            if y < top:
                self.legend_canvas.yview_moveto(y / total)
            elif bottom > top + height:
                self.legend_canvas.yview_moveto((bottom - height) / total)
        for key, (title, _colour, _shape) in CATEGORIES.items():
            line = tk.Frame(layers, bg=legend['bg'])
            line.pack(fill='x')
            category_glyph(line, key, legend['bg'], 16).pack(side='left', padx=(0, 4))
            tk.Checkbutton(line, text=title, variable=self.layer_vars[key], command=self.layer_toggled, bg=legend['bg'],
                           fg=p.WHITE, selectcolor=p.PANEL_2, activebackground=legend['bg'], activeforeground=p.WHITE,
                           font=('Segoe UI', 9), anchor='w', bd=0, highlightthickness=0).pack(side='left', fill='x')
            for child in (line, *line.winfo_children()):
                child.bind('<MouseWheel>', scroll_layers)
                child.bind('<FocusIn>', reveal_control)
        legend.place(relx=1, x=-10, y=10, anchor='ne')

    def fit_legend(self, height, width=None):
        # Start folded so the controls do not obscure Asia. If opened, the list
        # scrolls inside the available map height, including compact windows.
        self.legend_canvas.configure(height=max(60, min(330, height - 100)))

    def toggle_legend(self, chosen=True):
        self._legend_chosen = self._legend_chosen or chosen
        if self.legend_body.winfo_manager():
            self.legend_body.pack_forget()
            self.legend_toggle.configure(text='+')
        else:
            self.legend_body.pack(fill='x', pady=(4, 0))
            self.legend_toggle.configure(text='−')

    def _card(self, parent, column, title):
        p = self.palette
        card = tk.Frame(parent, bg=p.PANEL, padx=12, pady=8, highlightthickness=1, highlightbackground=p.LINE)
        card.grid(row=0, column=column, sticky='nsew', padx=(0, 6) if column == 0 else (6, 0))
        head = tk.Frame(card, bg=p.PANEL)
        head.pack(fill='x')
        tk.Label(head, text=title, bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 12)).pack(side='left')
        return card, head

    def _build_event_card(self, parent):
        p = self.palette
        card, head = self._card(parent, 0, 'Selected Event')
        self.event_card = card
        self.severity_badge = tk.Label(head, text='', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 8), padx=6, pady=1)
        self.severity_badge.pack(side='left', padx=8)
        # Navigation packs before the badge so a narrow card squeezes the badge first.
        self.severity_badge.pack_forget()
        self.next_button = ttk.Button(head, text='›', width=2, command=lambda: self.step_event(1), style='Compact.Hayat.TButton')
        self.next_button.pack(side='right')
        self.position = StringVar(self, '')
        tk.Label(head, textvariable=self.position, bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9)).pack(side='right', padx=4)
        self.prev_button = ttk.Button(head, text='‹', width=2, command=lambda: self.step_event(-1), style='Compact.Hayat.TButton')
        self.prev_button.pack(side='right')
        self.severity_badge.pack(side='left', padx=8)
        title_row = tk.Frame(card, bg=p.PANEL)
        title_row.pack(fill='x', pady=(6, 2))
        self.event_icon = tk.Canvas(title_row, width=34, height=34, bg=p.PANEL, highlightthickness=0)
        self.event_icon.pack(side='left', padx=(0, 8))
        words = tk.Frame(title_row, bg=p.PANEL)
        words.pack(side='left', fill='x', expand=True)
        self.detail_title = StringVar(self, 'Select an observation')
        self.detail_subtitle = StringVar(self, 'Click a marker, a feed card or an event row.')
        self.detail_title_label = tk.Label(words, textvariable=self.detail_title, bg=p.PANEL, fg=p.WHITE, anchor='w', justify='left',
                                           font=('Segoe UI Semibold', 13))
        self.detail_title_label.pack(fill='x')
        tk.Label(words, textvariable=self.detail_subtitle, bg=p.PANEL, fg=p.MUTED, anchor='w', justify='left', font=('Segoe UI', 9)).pack(fill='x')
        words.bind('<Configure>', lambda event: self.detail_title_label.configure(wraplength=max(150, event.width - 4)))
        self.chip_row = tk.Frame(card, bg=p.PANEL)
        self.chip_row.pack(fill='x', pady=(0, 4))
        self.detail_text = DarkScrolledText(card, height=6, width=38, wrap='word', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9),
                                            padx=4, pady=4, state='disabled', bd=0, highlightthickness=0, tabs=('125',), spacing1=2, spacing3=2)
        self.detail_text.pack(fill='both', expand=True)
        self.detail_text.tag_configure('key', foreground=p.MUTED)
        self.detail_text.tag_configure('row', lmargin2=125)
        self.detail_text.tag_configure('good', foreground='#4ADE80')
        self.detail_text.tag_configure('warn', foreground='#F2A541')
        self.detail_text.tag_configure('link', foreground=p.ROYAL_SOFT, underline=True)
        self.detail_text.tag_bind('link', '<Button-1>', lambda _event: self.open_source())
        self.detail_text.tag_bind('link', '<Enter>', lambda _event: self.detail_text.configure(cursor='hand2'))
        self.detail_text.tag_bind('link', '<Leave>', lambda _event: self.detail_text.configure(cursor=''))
        for level, colour in SEVERITY_COLOURS.items():
            self.detail_text.tag_configure('sev_' + level, foreground=colour)

    def _build_assessment_card(self, parent):
        p = self.palette
        card, head = self._card(parent, 1, 'AI Assessment')
        self.assessment_card = card
        self.analyze_button = ttk.Button(head, text='Analyze with HAYAT', command=self.analyze_selected, state='disabled',
                                         style='HayatAccent.TButton')
        self.analyze_button.pack(side='right')
        segments = FlowRow(card, p.PANEL, height=30)
        segments.pack(fill='x', pady=(6, 4))
        for value, label in (('summary', 'Summary'), ('risk', 'Risk analysis'), ('context', 'Context'), ('related', 'Related events')):
            segments.add(ttk.Radiobutton(segments, text=label, value=value, variable=self.assessment_view,
                                         command=self.render_assessment, style='Segment.Toolbutton'))
        tiles = tk.Frame(card, bg=p.PANEL)
        tiles.pack(fill='x', pady=(0, 4))
        self.tiles = {}
        for index, (key, title) in enumerate((('risk', 'Risk level'), ('relevance', 'Relevance'), ('confidence', 'Confidence'))):
            tiles.columnconfigure(index, weight=1, uniform='tiles')
            tile = tk.Frame(tiles, bg=p.PANEL_2, padx=8, pady=4)
            tile.grid(row=0, column=index, sticky='ew', padx=(0 if index == 0 else 4, 0))
            tk.Label(tile, text=title, bg=p.PANEL_2, fg=p.MUTED, font=('Segoe UI', 8), anchor='w').pack(fill='x')
            value = tk.Label(tile, text='—', bg=p.PANEL_2, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w')
            value.pack(fill='x')
            self.tiles[key] = value
        self.assessment_box = DarkScrolledText(card, height=6, width=38, wrap='word', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9),
                                               padx=4, pady=4, state='disabled', bd=0, highlightthickness=0, spacing1=1, spacing3=2)
        self.assessment_box.pack(fill='both', expand=True)
        self.assessment_box.tag_configure('heading', foreground=p.GOLD, font=('Segoe UI Semibold', 9))
        self.assessment_box.tag_configure('muted', foreground=p.MUTED)
        self.assessment_box.tag_configure('action', foreground=p.ROYAL_SOFT, underline=True)
        self.assessment_box.tag_bind('action', '<Enter>', lambda _event: self.assessment_box.configure(cursor='hand2'))
        self.assessment_box.tag_bind('action', '<Leave>', lambda _event: self.assessment_box.configure(cursor=''))
        self.assessment_box.tag_bind('action', '<Button-1>', self.assessment_clicked)

    def new_table(self, tab, columns):
        body = tk.Frame(self.tabs[tab], bg=self.palette.BLACK)
        body.pack(fill='both', expand=True, pady=(6, 0))
        tree = ttk.Treeview(body, columns=tuple(key for key, _, _ in columns), selectmode='browse', style='Intel.Treeview')
        tree.heading('#0', text='Observation / rule')
        tree.column('#0', width=380, minwidth=170)
        for key, label, width in columns:
            tree.heading(key, text=label)
            # The first column takes any extra width; the others keep theirs.
            tree.column(key, width=width, minwidth=60, stretch=False)
        add_scrollbars(tree, body)
        return tree

    def _build_tables(self):
        p = self.palette
        self.tree = self.new_table('events', [('severity', 'Severity', 90), ('category', 'Category', 130),
                                              ('time', 'Event time', 150), ('source', 'Source', 120)])
        self.tree.heading('#0', text='Observation (double-click to show on map)')
        for level in ('high', 'medium'):
            self.tree.tag_configure(level, foreground=SEVERITY_COLOURS[level])
        self.tree.tag_configure('stale', foreground=p.MUTED)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.event_selected())
        self.tree.bind('<Double-1>', lambda _event: self.show_map_selection())
        self.tree.bind('<Return>', lambda _event: (self.show_map_selection(), 'break')[1])
        pages = tk.Frame(self.tabs['events'], bg=p.BLACK, pady=5)
        pages.pack(fill='x')
        self.previous_button = ttk.Button(pages, text='Previous 200', command=lambda: self.page(-1))
        self.next_page_button = ttk.Button(pages, text='Next 200', command=lambda: self.page(1))
        self.previous_button.pack(side='left')
        self.next_page_button.pack(side='left', padx=6)
        self.page_label = StringVar(self)
        ttk.Label(pages, textvariable=self.page_label).pack(side='left')
        ttk.Label(self.tabs['alerts'], text='Local watch alerts · up to 1,000 retained · an alert can describe a past event',
                  foreground=p.MUTED).pack(fill='x', pady=(6, 0))
        self.alert_tree = self.new_table('alerts', [('watch', 'Watch', 180), ('time', 'Recorded', 230), ('feed', 'Layer', 130)])
        self.alert_tree.bind('<<TreeviewSelect>>', lambda _event: self.alert_selected())
        self.watch_tree = self.new_table('watches', [('state', 'Enabled', 80), ('feeds', 'Layers / keywords', 230), ('bounds', 'Bounds / magnitude', 230)])
        watch_actions = tk.Frame(self.tabs['watches'], bg=p.BLACK, pady=5)
        watch_actions.pack(fill='x')
        for title, callback in (('Add watch', self.add_watch), ('Edit selected', self.edit_watch), ('Delete selected', self.delete_watch)):
            ttk.Button(watch_actions, text=title, command=callback, style='Compact.Hayat.TButton').pack(side='left', padx=(0, 6))
        source_actions = tk.Frame(self.tabs['sources'], bg=p.BLACK)
        source_actions.pack(side='bottom', fill='x')
        self.source_text = text_box(source_actions, p, 6)
        self.source_tree = self.new_table('sources', [('status', 'State', 120), ('count', 'Items', 70), ('time', 'Last success', 120),
                                                      ('next', 'Next check', 120), ('every', 'Interval', 80)])
        self.source_tree.tag_configure('ok', foreground='#4ADE80')
        self.source_tree.tag_configure('failed', foreground=SEVERITY_COLOURS['high'])
        self.source_tree.tag_configure('stale', foreground=SEVERITY_COLOURS['medium'])
        self.source_tree.bind('<<TreeviewSelect>>', lambda _event: self.source_selected())

    def _build_assessment_tab(self):
        p = self.palette
        tab = self.tabs['assessment']
        top = tk.Frame(tab, bg=p.BLACK)
        top.pack(fill='x', pady=6)
        ttk.Button(top, text='Analyze current filters', command=self.brief, style='HayatAccent.TButton').pack(side='left')
        tk.Label(top, text='Counts and severity mix for every matching item; severity follows published provider fields.',
                 bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9)).pack(side='left', padx=10)
        self.chart = tk.Canvas(tab, height=150, bg=p.PANEL, highlightthickness=0)
        self.chart.pack(fill='x', pady=(0, 6))
        self.chart.bind('<Configure>', lambda _event: self.draw_chart())
        self.assessment_text = text_box(tab, p)
        set_text(self.assessment_text, 'Opening this tab reads the current filters: counts, the most severe reports, '
                                       'stale feeds and what the sources cannot show.')
        self._assessed_key = None
        self.notebook.bind('<<NotebookTabChanged>>', self._assessment_opened, add='+')

    def _assessment_opened(self, _event=None):
        """The assessment is code, not a model call, so it runs on arrival instead of leaving an empty page."""
        if self._closed or self.notebook.select() != str(self.tabs['assessment']):
            return
        key = self._render_key
        if key != self._assessed_key and self.work('intelligence_assess', {**self.filter_arguments(), 'region': self.region.get()}):
            self._assessed_key = key

    def _build_reports_tab(self):
        p = self.palette
        tab = self.tabs['reports']
        top = tk.Frame(tab, bg=p.BLACK)
        top.pack(fill='x', pady=6)
        ttk.Button(top, text='Generate brief for current filters', command=self.generate_report, style='HayatAccent.TButton').pack(side='left')
        ttk.Button(top, text='Show saved monitor brief', command=self.show_saved_brief, style='Hayat.TButton').pack(side='left', padx=6)
        self.report_text = text_box(tab, p)
        set_text(self.report_text, 'The monitor rewrites one local brief after each scan (latest-brief.md beside the intelligence database). '
                                   'Generate a brief for the current filters, or show the saved one.')

    def _responsive(self, event=None):
        # Driven by the window and its body pane, using the window's real size, so a
        # missed or replaced toplevel <Configure> binding cannot leave a stale layout.
        if event is not None and event.widget not in (self, self.body) or self._closed:
            return
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        if not self._filter_bar_manual and (height >= 560) != self.filters_visible.get():
            self.show_filter_bar(height >= 560, manual=False)
        hide = self._map_focus or width < 1180
        if hide != self._live_hidden:
            self._live_hidden = hide
            self.body.paneconfigure(self.live, hide=hide)
        compact = height < 760 or width < 1000
        if compact != self._compact:
            self.apply_compact(compact)
        # The subtitle repeats the title; below 960 px its row goes to the map and cards.
        tight = compact or height < 960
        if tight != getattr(self, '_tight', None):
            self._tight = tight
            if tight:
                self.subtitle.pack_forget()
            else:
                self.subtitle.pack(fill='x')

    def apply_compact(self, compact):
        """Short or narrow windows put the inspector beside the map and use icon-only toolbars."""
        self._compact = compact
        self._sash_user = False
        self.map_options_button.configure(text='Options' if compact else 'Map options')
        self.map_view_toggle.configure(text='View only' if compact else 'Filter to visible map')
        if compact:
            self.severity_badge.pack_forget()
            # Chips repeat the category shown by the icon; their row is given to the details.
            self.chip_row.pack_forget()
            self.card_actions.pack_configure(pady=(2, 2))
            for caption in self.filter_captions:
                caption.pack_forget()
        else:
            self.severity_badge.pack(side='left', padx=8)
            self.chip_row.pack(fill='x', pady=(0, 4), before=self.detail_text.frame)
            self.card_actions.pack_configure(pady=(4, 4))
            for caption in self.filter_captions:
                caption.pack(fill='x', before=caption.master.winfo_children()[-1])
        from app.gui_help import Tooltip
        # Compact: one-line footer (monitor state stays in the header) and a slimmer header.
        if compact:
            self.status_label.pack_forget()
            self.feedback_label.configure(anchor='w', justify='left')
            self.globe.pack_forget()
            self.title_label.configure(font=('Segoe UI Semibold', 14))
            self.header.configure(pady=4)
        else:
            self.status_label.pack(side='left', before=self.feedback_label)
            self.feedback_label.configure(anchor='e', justify='right')
            self.globe.pack(side='left', padx=(0, 12), before=self.title_label.master)
            self.title_label.configure(font=('Segoe UI Semibold', 19))
            self.header.configure(pady=10)
        for button in (*self.header_buttons, *self.map_actions, *self.card_actions.items):
            if str(button.cget('image')):
                button._hayat_compound = 'image' if compact else 'left'
                button.configure(compound=button._hayat_compound)
                if not hasattr(button, '_intel_tip'):
                    button._intel_tip = Tooltip(button, str(button.cget('text')), self.palette)
        self.map_toolbar.arrange()
        self.card_actions.arrange()
        self.filter_row.arrange()
        split = self.map_split
        split.configure(orient='horizontal' if compact else 'vertical')
        if compact:
            split.paneconfigure(self.map_area, minsize=304)
            split.paneconfigure(self.cards_frame, minsize=300, width=max(300, self.winfo_width() // 2 - 20), height='')
            self.card_switch.pack(fill='x', pady=(0, 4), before=self.card_row)
        else:
            split.paneconfigure(self.map_area, minsize=170)
            split.paneconfigure(self.cards_frame, minsize=220, height=340, width='')
            self.card_switch.pack_forget()
            self._sash_placed = False
            self.after_idle(self._place_map_sash)
        split.paneconfigure(self.cards_frame, hide=self._map_focus)
        self.after_idle(self._place_map_sash)
        self.show_card()

    def show_card(self):
        both = not self._compact
        for name, card in (('event', self.event_card), ('assessment', self.assessment_card)):
            column = 0 if name == 'event' else 1
            if both:
                card.grid(row=0, column=column, columnspan=1, sticky='nsew', padx=(0, 6) if column == 0 else (6, 0))
            elif self.card_choice.get() == name:
                card.grid(row=0, column=0, columnspan=2, sticky='nsew', padx=0)
            else:
                card.grid_remove()

    # ----- filters ------------------------------------------------------
    def feed_key(self):
        return '' if self.layer.get() == 'All layers' else self.layer.get()

    def category_key(self):
        return CATEGORY_FILTERS.get(self.category.get(), '')

    def severity_key(self):
        return SEVERITY_FILTERS.get(self.severity.get(), '')

    def since_hours(self):
        return TIME_RANGES.get(self.time_range.get())

    def bounds(self):
        return self.viewport.bounds() if self.only_viewport.get() else (REGIONS[self.region.get()] if self.region.get() != 'World' else None)

    def map_categories(self):
        return {key for key, variable in self.layer_vars.items() if variable.get()}

    def filter_changed(self):
        if getattr(self, '_resetting_filters', False):
            return
        if len(self.query.get()) > 500:
            self.query.set(self.query.get()[:500])
            self.feedback.set('Search is limited to 500 characters.')
        self.offset = 0
        self._record_token += 1
        self.invalidate_cameras()
        self._record_signature = None
        self.selected_record = None
        self.detail_title.set('Select an observation')
        self.detail_subtitle.set('Click a marker, a feed card or an event row.')
        self.severity_badge.configure(text='', bg=self.palette.PANEL)
        self.position.set('')
        self.event_icon.delete('all')
        for child in self.chip_row.winfo_children():
            child.destroy()
        set_text(self.detail_text, 'Loading matching observations…')
        set_text(self.assessment_box, 'Select a matching observation to see its evidence and assessment.')
        for tile in self.tiles.values():
            tile.configure(text='—', fg=self.palette.WHITE)
        for button in (self.focus_button, self.ask_button, self.source_button, self.copy_button, self.analyze_button):
            button.configure(state='disabled')
        self.render(force=True)

    def region_changed(self):
        if getattr(self, '_resetting_filters', False):
            return
        self.viewport.fit(REGIONS[self.region.get()])
        self.filter_changed()

    def layer_toggled(self):
        self.render(force=True)

    # ----- rendering ----------------------------------------------------
    def render(self, force=False):
        if self._closed:
            return
        key = (self.query.get(), self.feed_key(), str(self.bounds()), self.category_key(), self.severity_key(), self.since_hours(),
               self.offset, tuple(sorted(self.map_categories())), self.manager.store.revision, int(time.time() // 30))
        if not force and key == self._render_key:
            return
        self._render_key = key
        value = self.manager.view(self.query.get(), self.feed_key(), self.bounds(), self.category_key(), self.severity_key(),
                                  self.since_hours(), offset=self.offset, map_categories=self.map_categories())
        self.current_dashboard = value
        running = value['monitor']['running']
        label = 'Pause monitor' if running else 'Start monitor'
        if str(self.control.cget('text')) != label:
            self.control.configure(text=label)
            if str(self.control.cget('image')):
                self._icon_overrides((self.control,))
        current = sum(not source['stale'] for source in value['sources'])
        successes = [source.get('last_success_at') for source in value['sources'] if source.get('last_success_at')]
        latest = max(successes, default=None)
        self.live_label.configure(text='● Live feeds' if running else '● Monitor paused', fg='#4ADE80' if running else self.palette.MUTED)
        self.updated_label.configure(text='Last updated ' + (relative_time(latest) or 'never'))
        cameras = value.get('cameras_total') or 0
        self.status.set(f"{'Monitor running' if running else 'Monitor stopped'} · {current}/{len(value['sources'])} feeds current · "
                        f"{value['total_matching']:,} events" + (f" (+{cameras:,} public cameras in Cameras)" if cameras else '')
                        + f" · map {len(value['mapped']):,}/{value['mapped_total']:,} located · "
                        f"{value['aggregates']['unknown_location']:,} without a location · {value['alert_total_matching']} alerts")
        self.map_rows = {row['id']: row for row in value['mapped']}
        self.rows = {**self.map_rows, **{row['id']: row for row in value['live']}, **{row['id']: row for row in value['events']}}
        stable_sources = [{name: item for name, item in source.items() if name != 'age_seconds'} for source in value['sources']]
        signature = json.dumps([value['events'], value['alerts'], value['watches'], stable_sources], sort_keys=True, default=str)
        if signature != self._data_signature:
            self._data_signature = signature
            self._render_tables(value)
        self.page_label.set(f"Showing {self.offset + 1 if value['events'] else 0}–{self.offset + len(value['events'])} of {value['total_matching']}")
        self.previous_button.configure(state='normal' if self.offset else 'disabled')
        self.next_page_button.configure(state='normal' if value['next_offset'] is not None else 'disabled')
        self.live.set_rows(value['live'], value['live_total'], value['aggregates']['counts_by_category'], running)
        if self.selected_record:
            self.load_event(self.selected_record['event']['id'])
        elif value.get('spotlight') or value['live'] or value['events']:
            # Open on the day's most severe report, not whichever row sorts first.
            self.load_event(value.get('spotlight') or (value['live'] or value['events'])[0]['id'])
        else:
            set_text(self.detail_text, 'No matching observations. Refresh feeds or widen the search, region, severity and time range. '
                                       'Source availability is shown in Sources.')
        self.draw()
        self.draw_chart()

    def _render_tables(self, value):
        selected, scroll = self.tree.selection(), self.tree.yview()
        self.tree.delete(*self.tree.get_children())
        for row in value['events']:
            when = row.get('observed_at')
            detail = without_provider(row['subtitle'], row['provider'])
            self.tree.insert('', 'end', iid=row['id'], text=row['headline'] + (f" — {detail}" if detail else ''),
                             values=(row['badge'] if row['category'] in ('earthquakes', 'space_weather') else SEVERITY_LABEL[row['severity']],
                                     row['category_title'], (relative_time(when) + ' · ' + when[:16].replace('T', ' ')) if when else 'Unknown',
                                     row['provider'] + (' · stale' if row['stale'] else '')),
                             tags=('stale',) if row['stale'] else (row['severity'],) if row['severity'] in ('high', 'medium') else ())
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected)
        if scroll:
            self.tree.yview_moveto(scroll[0])
        saved = [(tree, tree.selection(), tree.yview()) for tree in (self.alert_tree, self.watch_tree, self.source_tree)]
        self.alert_tree.delete(*self.alert_tree.get_children())
        self.alert_rows = {row['id']: row for row in value['alerts']}
        for alert in value['alerts']:
            self.alert_tree.insert('', 'end', iid=alert['id'], text=alert_title(alert['event']),
                                   values=(alert['watch_title'], readable_time(alert['created_at']), alert['event']['feed']))
        self.watch_tree.delete(*self.watch_tree.get_children())
        self.watches = {watch['key']: watch for watch in value['watches']}
        for watch in value['watches']:
            self.watch_tree.insert('', 'end', iid=watch['key'], text=watch['title'], values=('On' if watch['enabled'] else 'Paused',
                ', '.join(watch['feeds'] + watch['keywords']) or 'Every layer in the area',
                ' · '.join(part for part in (bounds_text(watch.get('bbox')),
                                             f"M{watch['min_magnitude']}+" if watch.get('min_magnitude') is not None else '') if part)
                or 'Worldwide'))
        self.source_tree.delete(*self.source_tree.get_children())
        self.sources = {source['key']: source for source in value['sources']}
        # Problems first: a failing feed was row 60 of 89, below every healthy camera catalog.
        for source in sorted(value['sources'], key=source_order):
            tag = 'failed' if source['status'] == 'failed' else 'stale' if source['stale'] else 'ok'
            self.source_tree.insert('', 'end', iid=source['key'], text=source['title'], tags=(tag,), values=(
                source['status'] + (' · stale' if source['stale'] else ''), source.get('count', 0),
                relative_time(source.get('last_success_at')) or 'Never',
                (source.get('next_attempt_at') or '')[11:16] + (' UTC' if source.get('next_attempt_at') else ''),
                f"{source['interval_seconds'] // 60} min"))
        for tree, selection, position in saved:
            if selection and tree.exists(selection[0]):
                tree.selection_set(selection)
            if position:
                tree.yview_moveto(position[0])
        if not self.source_tree.selection():
            set_text(self.source_text, sources_summary(value['sources']))

    def draw_chart(self):
        value, canvas, p = self.current_dashboard, self.chart, self.palette
        if value is None or canvas.winfo_width() <= 1:
            return
        canvas.delete('all')
        width = max(300, canvas.winfo_width())
        counts = value['aggregates']['counts_by_category']
        canvas.create_text(14, 14, text='Matching items by category', anchor='w', fill=p.WHITE, font=('Segoe UI Semibold', 9))
        largest = max(counts.values(), default=1)
        half = width * .58
        for index, (key, count) in enumerate(sorted(counts.items(), key=lambda item: -item[1])[:5]):
            y = 36 + index * 22
            canvas.create_text(14, y, text=CATEGORIES[key][0], anchor='w', fill=p.MUTED, font=('Segoe UI', 9))
            bar = (half - 170) * count / largest
            canvas.create_rectangle(120, y - 6, 120 + max(2, bar), y + 6, fill=CATEGORIES[key][1], outline='')
            canvas.create_text(126 + bar, y, text=str(count), anchor='w', fill=p.WHITE, font=('Segoe UI', 9))
        mix = value['aggregates']['counts_by_severity']
        total = sum(mix.values()) or 1
        canvas.create_text(half + 10, 14, text='Severity mix (source-derived)', anchor='w', fill=p.WHITE, font=('Segoe UI Semibold', 9))
        left = half + 10
        for level in ('high', 'medium', 'low', 'info'):
            span = (width - half - 30) * mix.get(level, 0) / total
            if span:
                canvas.create_rectangle(left, 34, left + span, 54, fill=SEVERITY_COLOURS[level], outline='')
                left += span
        for index, level in enumerate(('high', 'medium', 'low', 'info')):
            y = 72 + index * 18
            canvas.create_rectangle(half + 10, y - 5, half + 20, y + 5, fill=SEVERITY_COLOURS[level], outline='')
            canvas.create_text(half + 28, y, text=f'{SEVERITY_LABEL[level]}: {mix.get(level, 0)}', anchor='w', fill=p.MUTED, font=('Segoe UI', 9))

    # ----- map ----------------------------------------------------------
    def _bind_map(self):
        self.map.bind('<Configure>', lambda event: (self.fit_legend(event.height, event.width), self.request_draw()))
        self.map.bind('<MouseWheel>', self.wheel)
        self.map.bind('<Button-4>', lambda event: self.wheel(event, 1))
        self.map.bind('<Button-5>', lambda event: self.wheel(event, -1))
        self.map.bind('<ButtonPress-1>', self.start_drag)
        self.map.bind('<B1-Motion>', self.drag)
        self.map.bind('<ButtonRelease-1>', self.end_drag)
        self.map.bind('<Motion>', self.hover)
        self.map.bind('<Leave>', lambda _event: self.map.delete('hover-card'))
        self.map.bind('<Button-3>', self.map_context)
        self.map.bind('<Return>', lambda _event: (self.browse_map(), 'break')[1])
        self.map.bind('<slash>', self.focus_map_search)
        for key, factor in (('plus', 1.4), ('equal', 1.4), ('minus', 1 / 1.4)):
            self.map.bind('<KeyPress-' + key + '>', lambda _event, value=factor: (self.zoom(value), 'break')[1])
        self.map.bind('<Home>', lambda _event: (self.reset_map(), 'break')[1])
        for key, dx, dy in (('Left', 70, 0), ('Right', -70, 0), ('Up', 0, 50), ('Down', 0, -50)):
            self.map.bind('<' + key + '>', lambda _event, x=dx, y=dy: (self.move_map(x, y), 'break')[1])

    def size(self):
        return max(1, self.map.winfo_width()), max(1, self.map.winfo_height())

    def request_draw(self):
        if self._draw_handle is None and not self._closed:
            def run():
                self._draw_handle = None
                self.draw()
            self._draw_handle = self.after(16, run)

    def draw(self):
        if self._closed:
            return
        canvas = self.map
        canvas.delete('all')
        width, height = self.size()
        project = lambda lon, lat, wrap=True: self.viewport.project(lon, lat, width, height, wrap=wrap)
        step = 30 if self.viewport.zoom < 3 else 10 if self.viewport.zoom < 10 else 2
        for lon in range(-180, 180, step) if self.show_grid.get() else ():
            x, _ = project(lon, 0)
            if 0 <= x <= width:
                canvas.create_line(x, 0, x, height, fill=MAP_GRID)
        for lat in range(-90, 91, step) if self.show_grid.get() else ():
            _, y = project(self.viewport.longitude, lat)
            if 0 <= y <= height:
                canvas.create_line(0, y, width, y, fill=MAP_GRID)
        for polygon in self.polygons:
            for shift in (-360, 0, 360):
                pairs = [project(lon + shift, lat, False) for lon, lat in polygon]
                if max(x for x, _ in pairs) < 0 or min(x for x, _ in pairs) > width or max(y for _, y in pairs) < 0 or min(y for _, y in pairs) > height:
                    continue
                canvas.create_polygon([coordinate for pair in pairs for coordinate in pair], fill=MAP_LAND, outline=MAP_COAST, width=.6)
        self.draw_geographic_labels(project, width, height)
        selected_id = self.selected_record['event']['id'] if self.selected_record else None
        visible = []
        for row in self.map_rows.values():
            x, y = project(row['longitude'], row['latitude'])
            if -12 <= x <= width + 12 and -12 <= y <= height + 12:
                visible.append((row, x, y))
        singles, self._clusters = cluster_markers(visible, self.viewport.zoom, selected_id)
        # Low and medium markers, then clusters (their counts must stay readable), then
        # high-severity and selected markers on top.
        ordered = sorted(singles, key=lambda item: (item[0]['severity'] == 'high' or item[0]['id'] == selected_id,
                                                    item[0]['severity_rank']))
        clusters_at = next((index for index, (row, _x, _y) in enumerate(ordered)
                            if row['severity'] == 'high' or row['id'] == selected_id), len(ordered))
        for position, (row, x, y) in enumerate(ordered):
            if position == clusters_at:
                self._draw_clusters()
            title, colour, shape = CATEGORIES[row['category']]
            colour = blend(colour, MAP_OCEAN, .5) if row['stale'] else colour
            radius = {'high': 6.5, 'medium': 5, 'low': 3.8, 'info': 3}[row['severity']]
            if row['severity'] in ('high', 'medium') and not row['stale']:
                for ring, fade in ((radius + 7, .78), (radius + 3.5, .55)):
                    canvas.create_oval(x - ring, y - ring, x + ring, y + ring, outline=blend(colour, MAP_OCEAN, fade), width=2, tags=('halo',))
            glyph(canvas, shape, x, y, radius, colour, tags=('event', row['id']),
                  outline=self.palette.WHITE if row['id'] == selected_id else '')
        if clusters_at == len(ordered):
            self._draw_clusters()
        if selected_id in self.map_rows:
            self._draw_callout(project, width, height)
        in_view = [row for row, x, y in visible if 0 <= x <= width and 0 <= y <= height]
        self.update_map_summary(in_view)
        self.draw_map_empty(in_view)
        heading = f'{self.region.get()} · {self.viewport.zoom:.1f}×'
        if width > 620:
            heading += f' · {len(self._clusters)} clusters' if self._clusters else ' · Select an observation'
        canvas.create_text(12, 12, text=heading,
                           fill=MAP_TEXT, anchor='nw', font=('Segoe UI', 9))
        self._draw_scale(width, height)
        self._draw_hint()

    def _draw_clusters(self):
        for index, (x, y, members) in enumerate(self._clusters):
            self._draw_cluster(index, x, y, members)

    def _draw_cluster(self, index, x, y, members):
        """A bubble for many nearby low-severity reports, coloured by the commonest kind; a click zooms in."""
        canvas, p = self.map, self.palette
        kinds = Counter(row['category'] for row in members)
        colour = CATEGORIES[kinds.most_common(1)[0][0]][1]
        radius = min(18, 7 + 2.5 * math.log2(len(members)))
        tag = f'cluster:{index}'
        severe = any(row['severity'] == 'high' for row in members)
        canvas.create_oval(x - radius - 4, y - radius - 4, x + radius + 4, y + radius + 4,
                           outline=SEVERITY_COLOURS['high'] if severe else blend(colour, MAP_OCEAN, .6),
                           width=2, tags=('cluster', tag))
        canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=blend(colour, MAP_OCEAN, .45), outline=colour,
                           width=1.5, tags=('cluster', tag))
        canvas.create_text(x, y, text=f'{len(members):,}', fill=p.WHITE, font=('Segoe UI Semibold', 8), tags=('cluster', tag))

    def cluster_at(self, x, y):
        for item in reversed(self.map.find_overlapping(x - 3, y - 3, x + 3, y + 3)):
            for tag in self.map.gettags(item):
                if tag.startswith('cluster:'):
                    index = int(tag[8:])
                    if index < len(self._clusters):
                        return self._clusters[index]
        return None

    def _draw_hint(self):
        canvas = self.map
        canvas.delete('hint')
        width, height = self.size()
        if width < 420:
            return
        text = clip(self.map_hint.get(), max(120, width - 420), 6.3)
        item = canvas.create_text(width - 12, height - 14, text=text, anchor='e', fill=MAP_TEXT, font=('Segoe UI', 8), tags=('hint',))
        left, top, right, bottom = canvas.bbox(item)
        backing = canvas.create_rectangle(left - 8, top - 4, right + 8, bottom + 4, fill=blend(self.palette.PANEL, MAP_OCEAN, .25),
                                          outline=MAP_COAST, tags=('hint',))
        canvas.tag_lower(backing, item)

    def _draw_callout(self, project, width, height):
        row, canvas, p = self.selected_record['event'], self.map, self.palette
        x, y = project(row['longitude'], row['latitude'])
        canvas.create_oval(x - 12, y - 12, x + 12, y + 12, outline=p.GOLD, width=2, tags=('focus',))
        # Small maps keep only the ring; a callout would hide most of the map.
        if not (0 <= x <= width and 0 <= y <= height) or width < 560 or height < 200:
            return
        lines = [clip(row.get('headline') or row['title'], 230), clip(row.get('subtitle') or '', 230, 6.4), relative_time(row.get('observed_at'))]
        box_width, box_height = 250, 64
        # Keep clear of the layers legend in the top-right corner.
        reserved = self.legend.winfo_width() + 24 if y < self.legend.winfo_height() + 30 else 6
        left = x + 18 if x + 18 + box_width < width - reserved else x - 18 - box_width
        left = max(12, min(left, width - box_width - 12))
        top = min(max(6, y - box_height / 2), height - box_height - 30)
        canvas.create_line(x + (12 if left > x else -12), y, left if left > x else left + box_width, top + box_height / 2, fill=p.GOLD)
        round_rect(canvas, left, top, left + box_width, top + box_height, 8, fill=blend(p.PANEL, MAP_OCEAN, .15), outline=p.GOLD, tags=('callout',))
        canvas.create_text(left + 10, top + 14, text=lines[0], anchor='w', fill=p.WHITE, font=('Segoe UI Semibold', 9), tags=('callout',))
        canvas.create_text(left + 10, top + 32, text=lines[1], anchor='w', fill=MAP_TEXT, font=('Segoe UI', 8), tags=('callout',))
        canvas.create_text(left + 10, top + 49, text=lines[2], anchor='w', fill=MAP_TEXT, font=('Segoe UI', 8), tags=('callout',))

    def _draw_scale(self, width, height):
        canvas = self.map
        km_per_pixel = 360 / (self.viewport.zoom * width) * 111.32 * max(.05, math.cos(math.radians(self.viewport.latitude)))
        distance = nice_distance(km_per_pixel * 110)
        if not distance:
            return
        bar = distance / km_per_pixel
        # Bottom-left keeps clear of the layers legend on the right.
        right, bottom = 160 + bar, height - 12
        label = f'{distance:,.0f} km' if distance >= 1 else f'{distance * 1000:,.0f} m'
        point = format_point(self.viewport.latitude, self.viewport.longitude)
        round_rect(canvas, 8, bottom - 22, right + 8, bottom + 6, 6, fill=blend(self.palette.PANEL, MAP_OCEAN, .25), outline=MAP_COAST)
        canvas.create_text(16, bottom - 8, text=point, anchor='w', fill=MAP_TEXT, font=('Segoe UI', 8))
        canvas.create_line(right - bar, bottom - 4, right - bar, bottom, right, bottom, right, bottom - 4, fill=MAP_TEXT, width=1.5)
        canvas.create_text(right - bar / 2, bottom - 13, text=label, fill=MAP_TEXT, font=('Segoe UI', 8))

    def view_changed(self):
        self.filter_changed() if self.only_viewport.get() else self.request_draw()

    def wheel(self, event, direction=None):
        direction = direction if direction is not None else (1 if event.delta > 0 else -1)
        self.viewport.zoom_at(1.2 if direction > 0 else 1 / 1.2, event.x, event.y, *self.size())
        self.view_changed()
        return 'break'

    def zoom(self, factor):
        width, height = self.size()
        self.viewport.zoom_at(factor, width / 2, height / 2, width, height)
        self.view_changed()

    def move_map(self, dx, dy):
        self.viewport.pan(dx, dy, *self.size())
        self.view_changed()

    def start_drag(self, event):
        self.map.focus_set()
        self._drag = [event.x, event.y, event.x, event.y, False]
        self.map.configure(cursor='fleur')

    def drag(self, event):
        if self._drag:
            x, y, start_x, start_y, moved = self._drag
            self.viewport.pan(event.x - x, event.y - y, *self.size())
            self._drag = [event.x, event.y, start_x, start_y, moved or abs(event.x - start_x) + abs(event.y - start_y) > 4]
            self.request_draw()

    def end_drag(self, event):
        moved = self._drag and self._drag[4]
        self._drag = None
        self.map.configure(cursor='')
        self.view_changed() if moved else self.select_marker(event)

    def reset_map(self):
        self.viewport = MapViewport()
        self.view_changed()

    def fit_region(self):
        self.viewport.fit(REGIONS[self.region.get()])
        self.view_changed()

    def marker_at(self, x, y):
        for item in reversed(self.map.find_overlapping(x - 7, y - 7, x + 7, y + 7)):
            tags = self.map.gettags(item)
            if 'event' in tags and tags[1] in self.map_rows:
                return tags[1]
        return None

    def hover(self, event):
        lon, lat = self.viewport.unproject(event.x, event.y, *self.size())
        identifier = self.marker_at(event.x, event.y)
        cluster = None if identifier else self.cluster_at(event.x, event.y)
        self.map.configure(cursor='hand2' if identifier or cluster else '')
        self.draw_map_hover(event, identifier, cluster)
        label = ''
        if cluster:
            kinds = Counter(CATEGORIES[row['category']][0].lower() for row in cluster[2])
            label = f" · {len(cluster[2])} reports: " + ', '.join(f'{count} {name}' for name, count in kinds.most_common(3)) + (' · click to inspect' if self.viewport.zoom >= 8 else ' · click to zoom in')
        elif identifier:
            row = self.map_rows[identifier]
            label = f" · {row['headline']}" + (f" — {row['subtitle']}" if row['subtitle'] else '') + f" · {SEVERITY_LABEL[row['severity']]}"
        self.map_hint.set(f'{format_point(lat, lon)} · {self.viewport.zoom:.1f}×' + (clip(label, 700) if label else ' · Wheel zoom · Drag pan · Home reset'))

    def select_marker(self, event):
        identifier = self.marker_at(event.x, event.y)
        if identifier:
            self.select_event(identifier)
        elif cluster := self.cluster_at(event.x, event.y):
            if self.viewport.zoom >= 8:
                self.browse_map(cluster[2])
            else:
                self.viewport.zoom_at(2.5, cluster[0], cluster[1], *self.size())
                self.view_changed()

    # ----- selection and inspector -------------------------------------
    def select_event(self, identifier):
        if self._map_focus:
            self.toggle_map_focus()
        if self.tree.exists(identifier):
            self.tree.selection_set(identifier)
            self.tree.see(identifier)
        self.load_event(identifier)
        self.notebook.select(self.tabs['map'])

    def event_selected(self):
        selected = self.tree.selection()
        if selected:
            self.load_event(selected[0])

    def show_map_selection(self):
        self.event_selected()
        self.notebook.select(self.tabs['map'])

    def ordered_ids(self):
        value = self.current_dashboard or {}
        return [row['id'] for row in value.get('events', [])]

    def step_event(self, direction):
        ids = self.ordered_ids()
        if not ids:
            return
        current = self.selected_record['event']['id'] if self.selected_record else None
        index = ids.index(current) + direction if current in ids else 0
        if 0 <= index < len(ids):
            self.select_event(ids[index])
        elif direction > 0 and self.current_dashboard['next_offset'] is not None or direction < 0 and self.offset:
            self.page(direction)
            ids = self.ordered_ids()
            if ids:
                self.select_event(ids[0] if direction > 0 else ids[-1])

    def load_event(self, identifier):
        signature = (identifier, json.dumps(self.rows.get(identifier), sort_keys=True, default=str))
        if signature == self._record_signature:
            return
        self._record_signature = signature
        self._record_token += 1
        token = self._record_token
        def run():
            try:
                self.updates.put(('record', token, self.manager.event_record(identifier)))
            except Exception as exc:
                self.updates.put(('record_error', token, str(exc)))
        threading.Thread(target=run, daemon=True, name='hayat-intelligence-details').start()

    def show_record(self, value):
        p = self.palette
        self.selected_record = value
        row = value['event']
        self.detail_title.set(row['headline'])
        self.detail_subtitle.set(row['subtitle'] or row['title'])
        tone = SEVERITY_COLOURS[row['severity']]
        label = SEVERITY_LABEL[row['severity']] + ('' if self._compact else ' priority')
        self.severity_badge.configure(text=label if row['severity'] != 'info' else 'Information',
                                      bg=blend(tone, p.PANEL, .75) if p.PANEL.startswith('#') and len(p.PANEL) == 7 else p.PANEL_2, fg=tone)
        ids = self.ordered_ids()
        total = (self.current_dashboard or {}).get('total_matching', 0)
        self.position.set(f"{self.offset + ids.index(row['id']) + 1} of {total}" if row['id'] in ids else f'{total} matching')
        self.event_icon.delete('all')
        title, colour, shape = CATEGORIES[row['category']]
        self.event_icon.create_oval(3, 3, 31, 31, outline=colour, width=2)
        glyph(self.event_icon, shape, 17, 17, 7, colour)
        for child in self.chip_row.winfo_children():
            child.destroy()
        for text, fg in ((title, colour), (row['provider'], p.WHITE), (row['feed'], p.MUTED)):
            tk.Label(self.chip_row, text=text, bg=p.PANEL_2, fg=fg, font=('Segoe UI', 8), padx=6, pady=1).pack(side='left', padx=(0, 4))
        self._render_detail_table(value)
        self.focus_button.configure(state='normal' if row.get('latitude') is not None else 'disabled')
        self.copy_button.configure(state='normal' if row.get('latitude') is not None else 'disabled')
        for button in (self.ask_button, self.source_button, self.analyze_button):
            button.configure(state='normal')
        self.live.mark(row['id'])
        self.render_assessment()
        self.draw()

    def _render_detail_table(self, value):
        row, details = value['event'], value['event'].get('details') or {}
        assessment = value['assessment']
        lines = []
        if row.get('latitude') is not None:
            lines.append(('Location', format_point(row['latitude'], row['longitude']), None))
        else:
            lines.append(('Location', 'Not supplied by the provider; not plotted.', 'warn'))
        lines.append(('Severity', f"{SEVERITY_LABEL[row['severity']]} — {row['severity_basis']}", 'sev_' + row['severity']))
        for key, label in DETAIL_LABELS:
            item = details.get(key)
            if item is not None and item != '':
                lines.append((label, str(item)[:600], None))
        if row['feed'] == 'space_weather' and details.get('message'):
            lines.append(('Bulletin', ' '.join(details['message'].split())[:600], None))
        when = row.get('observed_at')
        lines.append(('Event time (UTC)', (when[:19].replace('T', ' ') + f'  ({relative_time(when)})') if when else 'Unknown', None))
        lines.append(('Source', f"{row['provider']} · {row['url']}", 'link'))
        lines.append(('Last fetched', row.get('retrieved_at') or 'unknown', 'warn' if row.get('stale') else None))
        confidence = assessment['confidence']
        lines.append(('Verification', f"{confidence['label']} confidence — {confidence['basis']}", 'good' if confidence['level'] == 'high' else 'warn'))
        lines.append(('Matching watches', ', '.join(watch['title'] for watch in value['matching_watches']) or 'None', None))
        lines.append(('Retained revisions', f"{len(value['retained_revisions'])} distinct", None))
        lines.append(('Feed status', 'Present in the current feed' if value['in_current_feed'] else
                      'Retained; absent from the current feed (not proof it ended)', None if value['in_current_feed'] else 'warn'))
        box = self.detail_text
        box.configure(state='normal')
        box.delete('1.0', 'end')
        for label, text, tag in lines:
            box.insert('end', label + ':\t', ('key', 'row'))
            box.insert('end', text + '\n', ('row', tag) if tag else ('row',))
        box.configure(state='disabled')

    def render_assessment(self):
        if not self.selected_record:
            return
        value = self.selected_record
        judged = value['assessment']
        for key in ('risk', 'relevance', 'confidence'):
            level = judged[key]['level']
            colour = SEVERITY_COLOURS[level] if key == 'risk' else LEVEL_COLOURS[key][level]
            self.tiles[key].configure(text=judged[key]['label'], fg=colour)
        box = self.assessment_box
        box.configure(state='normal')
        box.delete('1.0', 'end')
        view = self.assessment_view.get()
        self._action_targets = {}
        if view == 'summary':
            box.insert('end', judged['summary'] + '\n\n')
            box.insert('end', 'Recommended actions\n', 'heading')
            for index, action in enumerate(judged['actions']):
                box.insert('end', '☐  ')
                if action['action']:
                    tag = f'act{index}'
                    self._action_targets[tag] = action['action']
                    box.insert('end', action['text'] + '\n', ('action', tag))
                else:
                    box.insert('end', action['text'] + '\n')
            analysis = self._analyses.get(value['event']['id'])
            if analysis:
                box.insert('end', '\nHAYAT analysis · local model · inference, not verification\n', 'heading')
                box.insert('end', analysis + '\n')
            else:
                box.insert('end', '\nPress Analyze with HAYAT for a local-model reading of this evidence.\n', 'muted')
        elif view == 'risk':
            for key in ('risk', 'relevance', 'confidence'):
                box.insert('end', f"{key.capitalize()}: {judged[key]['label']}\n", 'heading')
                box.insert('end', judged[key]['basis'] + '\n\n')
            box.insert('end', 'Severity follows fields the provider publishes (magnitude, alert level, NOAA scale, ransomware use, '
                              'wind speed or burned area). It is not an impact forecast.', 'muted')
        elif view == 'context':
            box.insert('end', '\n'.join(judged['context']) + '\n')
            if value['retained_revisions']:
                box.insert('end', '\nRevision timeline (latest first)\n', 'heading')
                for revision in value['retained_revisions']:
                    event = revision['event']
                    magnitude = (event.get('details') or {}).get('magnitude')
                    box.insert('end', f"{revision['captured_at'][:19]} · {event['title']}" + (f' · M{magnitude}' if magnitude is not None else '') + '\n')
            box.insert('end', '\n' + value['basis'], 'muted')
        else:
            corroborating = {item['id'] for item in value['corroborating']}
            if not value['nearby_events']:
                box.insert('end', 'No other retained observation lies within 300 km.\n', 'muted')
            for index, item in enumerate(value['nearby_events'][:15]):
                tag = f'rel{index}'
                self._action_targets[tag] = 'select:' + item['id']
                box.insert('end', ('✓ ' if item['id'] in corroborating else '• '))
                box.insert('end', item['headline'], ('action', tag))
                box.insert('end', f"  · {item['distance_km']} km · {item['feed']} · {relative_time(item.get('observed_at')) or 'time unknown'}\n")
            box.insert('end', '\n✓ marks another source describing the same kind of event within 72 h. Nearby does not mean related.\n', 'muted')
            if value.get('nearest_cameras'):
                box.insert('end', '\nNearest public cameras\n', 'heading')
                for index, camera in enumerate(value['nearest_cameras']):
                    tag = f'cam{index}'
                    self._action_targets[tag] = 'camera:' + camera['id']
                    medium = 'snapshot' if camera.get('image_url') else 'live video' if camera.get('video_url') else 'publisher player'
                    box.insert('end', '📷 ')
                    box.insert('end', camera['title'][:80], ('action', tag))
                    box.insert('end', f"  · {camera['distance_km']} km · {camera.get('provider') or camera['feed']} · {medium}\n")
        box.configure(state='disabled')

    def assessment_clicked(self, event):
        index = self.assessment_box.index(f'@{event.x},{event.y}')
        for tag in self.assessment_box.tag_names(index):
            if tag in self._action_targets:
                return self.run_action(self._action_targets[tag])

    def run_action(self, action):
        if action.startswith('select:'):
            return self.select_event(action[7:])
        if action.startswith('camera:'):
            return self.open_camera(action[7:])
        if action == 'open_source':
            return self.open_source()
        if action == 'watch_area':
            return self.watch_area()
        if action == 'weather':
            self.context_selection()
            self.notebook.select(self.tabs['context'])
            return self.context_weather()
        if action == 'camera':
            return self.notebook.select(self.tabs['cameras'])
        if action == 'related':
            self.assessment_view.set('related')
            return self.render_assessment()

    def analyze_selected(self):
        if self.selected_record:
            identifier = self.selected_record['event']['id']
            if self.work('intelligence_analyze', {'event_id': identifier}):
                self.analyze_button.configure(state='disabled')
                self.feedback.set('HAYAT is reading the evidence with the local model…')

    def details(self):
        if self.selected_record:
            from app.plain_output import describe_full

            return self.show_text('Full event evidence, revisions and source metadata', describe_full(self.selected_record))
        self.feedback.set('Select an event and wait for its evidence to load.')

    def show_alerts(self):
        self.notebook.select(self.tabs['alerts'])

    def focus_selected(self):
        if self.selected_record and self.selected_record['event'].get('latitude') is not None:
            row = self.selected_record['event']
            self.viewport.longitude, self.viewport.latitude = row['longitude'], row['latitude']
            self.viewport.zoom = max(8, self.viewport.zoom)
            self.viewport.clamp()
            self.view_changed()
            self.notebook.select(self.tabs['map'])

    def copy_location(self):
        if self.selected_record and self.selected_record['event'].get('latitude') is not None:
            row = self.selected_record['event']
            self.clipboard_clear()
            self.clipboard_append(f"{row['latitude']:.5f}, {row['longitude']:.5f}")
            self.feedback.set('Coordinates copied.')

    def ask_hayat(self):
        if self.selected_record:
            self.gui.use_example('Explain intelligence event ' + self.selected_record['event']['id'])
            self.gui.show_view('chat')
            self.gui.send()

    def open_source(self):
        if self.selected_record:
            from urllib.parse import urlsplit
            url = self.selected_record['event']['url']
            if urlsplit(url).scheme in ('http', 'https'):
                self.gui._open_source(url)

    def alert_selected(self):
        selected = self.alert_tree.selection()
        if selected and selected[0] in self.alert_rows:
            self.select_event(self.alert_rows[selected[0]]['event']['id'])

    def source_selected(self):
        selected = self.source_tree.selection()
        if selected and selected[0] in self.sources:
            set_text(self.source_text, source_detail(self.sources[selected[0]]))

    # ----- operations ---------------------------------------------------
    def filter_arguments(self):
        arguments = {'query': self.query.get(), 'feed': self.feed_key()}
        for key, value in (('category', self.category_key()), ('min_severity', self.severity_key()), ('since_hours', self.since_hours())):
            if value:
                arguments[key] = value
        if self.bounds() is not None:
            arguments['bbox'] = self.bounds()
        return arguments

    def brief(self):
        self.notebook.select(self.tabs['assessment'])
        self.work('intelligence_assess', {**self.filter_arguments(), 'region': self.region.get()})

    def generate_report(self):
        self.notebook.select(self.tabs['reports'])
        arguments = {key: value for key, value in self.filter_arguments().items() if key in ('query', 'feed', 'bbox')}
        self.work('intelligence_brief', arguments)

    def show_saved_brief(self):
        path = self.manager.store.path.parent / 'intelligence' / 'latest-brief.md'
        set_text(self.report_text, path.read_text(encoding='utf-8') if path.exists() else
                 'No saved brief yet. The monitor writes one after its first scan.')

    def work(self, tool, arguments, on_success=None):
        if self._busy:
            self.feedback.set('A workspace operation is running; your view and form are retained.')
            return False
        self._busy = True
        self._operation_done = on_success
        self.refresh_button.configure(state='disabled')
        self.feedback.set('Running ' + tool + ' · recorded in Activity')
        def run():
            from app.gui_features import run_declared_tool
            from app.tools.registry import ToolResult
            try:
                result = run_declared_tool(self.gui.runtime, tool, arguments)
            except Exception as exc:
                result = ToolResult(tool, False, error=str(exc))
            self.updates.put(result)
        threading.Thread(target=run, daemon=True, name='hayat-intelligence-ui').start()
        return True

    def fetch(self):
        self.work('intelligence_refresh', {})

    def toggle(self):
        self.work('intelligence_monitor', {'action': 'stop' if self.manager.status()['running'] else 'start'})

    def page(self, direction):
        self.offset = max(0, self.offset + direction * 200)
        self.render(force=True)

    def view_options(self):
        return {'region': self.region.get(), 'layer': self.feed_key(), 'query': self.query.get(), 'zoom': self.viewport.zoom,
                'center_lon': self.viewport.longitude, 'center_lat': self.viewport.latitude, 'only_viewport': self.only_viewport.get(),
                'category': self.category_key(), 'min_severity': self.severity_key(), 'time_range': self.time_range.get(),
                'hidden_layers': [key for key, variable in self.layer_vars.items() if not variable.get()]}

    def apply_view_options(self, options):
        """Show a saved view: the inverse of view_options()."""
        self.query.set(options.get('query', ''))
        self.region.set(options.get('region', 'World'))
        self.layer.set(options.get('layer') or 'All layers')
        self.category.set(next((title for title, key in CATEGORY_FILTERS.items() if key == options.get('category', '')),
                               'All events'))
        self.severity.set(next((title for title, key in SEVERITY_FILTERS.items()
                                if key == options.get('min_severity', '')), 'All severities'))
        self.time_range.set(options.get('time_range', 'Any time'))
        self.only_viewport.set(bool(options.get('only_viewport')))
        hidden = set(options.get('hidden_layers', []))
        for key, variable in self.layer_vars.items():
            variable.set(key not in hidden)
        self.viewport = MapViewport(options.get('zoom', 1.0), options.get('center_lon', 0.0), options.get('center_lat', 0.0))
        self.viewport.clamp()
        self.offset = 0
        self.view_changed()

    def saved_views(self):
        from app.gui_intelligence_views import SavedViewsWindow
        return SavedViewsWindow(self)

    def save_view(self):
        self.work('intelligence_preferences', {'action': 'save', 'data': self.view_options()}, self.refresh_home)

    def refresh_home(self):
        card = getattr(self.gui, '_intelligence_home', None)
        if card is not None and card.winfo_exists():
            card.request_refresh()

    def watch_view(self):
        return self.add_watch({'Bounds (optional)': ', '.join(str(round(value, 3)) for value in self.viewport.bounds())})

    def watch_area(self):
        if not self.selected_record or self.selected_record['event'].get('latitude') is None:
            return self.feedback.set('Select an observation with known coordinates first.')
        row = self.selected_record['event']
        lat, lon = row['latitude'], row['longitude']
        bounds = [max(-180, lon - 2), max(-90, lat - 2), min(180, lon + 2), min(90, lat + 2)]
        return self.add_watch({'Title': 'Near ' + clip(row['subtitle'] or row['headline'], 500, 1)[:120],
                               'Bounds (optional)': ', '.join(f'{value:.3f}'.rstrip('0').rstrip('.') for value in bounds)}, feeds=[row['feed']])

    def add_watch(self, initial=None, feeds=None):
        p = self.palette
        window = tk.Toplevel(self)
        window.withdraw()
        window.title('HAYAT · Add intelligence watch')
        window.configure(bg=p.BLACK)
        tk.Label(window, text='Title, keyword phrases and optional region. Bounds: west, south, east, north.',
                 bg=p.BLACK, fg=p.WHITE, wraplength=550).pack(padx=16, pady=12)
        fields = {}
        for label in ('Title', 'Keywords (comma separated)', 'Bounds (optional)'):
            tk.Label(window, text=label, bg=p.BLACK, fg=p.MUTED, anchor='w').pack(fill='x', padx=16)
            entry = ttk.Entry(window)
            entry.pack(fill='x', padx=16, pady=(2, 8))
            fields[label] = entry
            if initial and label in initial:
                entry.insert(0, initial[label])
        error = StringVar(window)
        tk.Label(window, textvariable=error, bg=p.BLACK, fg=p.GOLD, wraplength=550).pack(fill='x', padx=16)
        def save():
            try:
                data = {'title': fields['Title'].get(), 'keywords': [word.strip() for word in fields['Keywords (comma separated)'].get().split(',') if word.strip()]}
                if feeds:
                    data['feeds'] = list(feeds)
                elif self.layer.get() != 'All layers':
                    data['feeds'] = [self.layer.get()]
                bounds = fields['Bounds (optional)'].get().strip()
                if bounds:
                    data['bbox'] = [float(value.strip()) for value in bounds.split(',')]
                from app.intelligence.manager import validate_watch
                validate_watch(data)
                import uuid
                if self.work('intelligence_watch', {'action': 'save', 'key': uuid.uuid4().hex, 'data': data},
                             on_success=lambda: window.winfo_exists() and window.destroy()):
                    error.set('Saving watch…')
                else:
                    error.set('A workspace operation is running. Save the watch when it finishes.')
            except ValueError as exc:
                error.set(str(exc))
        ttk.Button(window, text='Save watch', command=save, style='HayatAccent.TButton').pack(pady=12)
        window.bind('<Escape>', lambda _event: (window.destroy(), 'break')[1])
        place_over(window, self, (600, 350))
        window.deiconify()
        window.fields, window.save = fields, save
        return window

    def edit_watch(self):
        selected = self.watch_tree.selection()
        if selected and selected[0] in self.watches:
            watch = self.watches[selected[0]]
            data = {key: value for key, value in watch.items() if key != 'key'}
            return self.json_editor('Edit watch · ' + watch['title'], data, 'intelligence_watch', {'action': 'save', 'key': watch['key']})

    def delete_watch(self):
        selected = self.watch_tree.selection()
        if selected:
            self.work('intelligence_watch', {'action': 'delete', 'key': selected[0]})

    def json_editor(self, title, data, tool, extra):
        window = tk.Toplevel(self)
        window.title('HAYAT · ' + title)
        window.configure(bg=self.palette.BLACK, padx=14, pady=12)
        ttk.Label(window, text='Edit local rule fields. Save validates the complete rule; source text is never executable.', wraplength=520).pack(fill='x')
        editor = tk.Text(window, height=17, width=65, bg=self.palette.PANEL, fg=self.palette.WHITE,
                         insertbackground=self.palette.WHITE, font=('Consolas', 10))  # a JSON rule editor
        editor.pack(fill='both', expand=True, pady=8)
        editor.insert('1.0', json.dumps(data, indent=2))
        error = StringVar(window)
        ttk.Label(window, textvariable=error, wraplength=520).pack(fill='x')
        def save():
            try:
                value = json.loads(editor.get('1.0', 'end-1c'))
                from app.intelligence.manager import validate_watch
                validate_watch(value)
                self.work(tool, {**extra, 'data': value}, lambda: window.winfo_exists() and window.destroy())
            except ValueError as exc:
                error.set(str(exc))
        ttk.Button(window, text='Save rule', command=save).pack(fill='x')
        window.bind('<Escape>', lambda _event: (window.destroy(), 'break')[1])
        place_over(window, self, (570, 520))
        window.editor, window.save = editor, save
        return window

    def customize(self):
        saved = self.manager.preferences()['preferences']
        window = tk.Toplevel(self)
        window.withdraw()
        window.title('HAYAT · Customize intelligence')
        window.configure(bg=self.palette.BLACK, padx=14, pady=12)
        options = {key: BooleanVar(window, saved[key]) for key in ('show_home', 'show_alerts')}
        options.update({'home_detail': StringVar(window, saved['home_detail']), 'home_alert_limit': StringVar(window, str(saved['home_alert_limit']))})
        ttk.Checkbutton(window, text='Show intelligence on the front page', variable=options['show_home']).pack(anchor='w', pady=5)
        ttk.Checkbutton(window, text='Show compact front-page alerts', variable=options['show_alerts']).pack(anchor='w', pady=5)
        ttk.Label(window, text='Front-page detail').pack(anchor='w')
        ttk.Combobox(window, textvariable=options['home_detail'], values=['compact', 'detailed'], state='readonly').pack(fill='x', pady=5)
        ttk.Label(window, text='Front-page alert count (0–5)').pack(anchor='w')
        ttk.Spinbox(window, from_=0, to=5, textvariable=options['home_alert_limit']).pack(fill='x', pady=5)
        ttk.Label(window, text='Apply also remembers this region, search, filters, map layers and map position.', wraplength=420).pack(pady=8)
        error = StringVar(window)
        ttk.Label(window, textvariable=error, wraplength=420).pack(fill='x')
        def save():
            try:
                data = {**self.view_options(), **{key: value.get() for key, value in options.items()}}
                data['home_alert_limit'] = int(data['home_alert_limit'])
                from app.intelligence.view import validate_view
                validate_view(data)
                def done():
                    self.refresh_home()
                    if window.winfo_exists():
                        window.destroy()
                self.work('intelligence_preferences', {'action': 'save', 'data': data}, done)
            except ValueError as exc:
                error.set(str(exc))
        ttk.Button(window, text='Apply customization', command=save).pack(fill='x', pady=8)
        window.bind('<Escape>', lambda _event: (window.destroy(), 'break')[1])
        place_over(window, self, (480, 350))
        window.deiconify()
        window.options, window.save = options, save
        return window

    def show_text(self, title, content):
        window = tk.Toplevel(self)
        window.withdraw()
        window.title('HAYAT · ' + title)
        window.configure(bg=self.palette.BLACK)
        text = DarkScrolledText(window, bg=self.palette.PANEL, fg=self.palette.WHITE, wrap='word', font=('Segoe UI', 10), padx=12, pady=12)
        text.pack(fill='both', expand=True)
        text.insert('1.0', content)
        text.configure(state='disabled')
        ttk.Button(window, text='Close', command=window.destroy).pack(pady=8)
        window.bind('<Escape>', lambda _event: (window.destroy(), 'break')[1])
        place_over(window, self, (720, 540))
        window.deiconify()
        return window

    def finish(self, value):
        from app.intelligence.tools import report
        self._busy = False
        self.refresh_button.configure(state='normal')
        if value.tool == 'intelligence_analyze':
            self.analyze_button.configure(state='normal' if self.selected_record else 'disabled')
        if not value.success:
            if value.tool == 'intelligence_camera_frame':
                self.stop_camera_refresh()
            self.feedback.set('Operation issue: ' + str(value.error))
            if value.tool == 'intelligence_analyze' and self.selected_record:
                self._analyses[self.selected_record['event']['id']] = 'Analysis unavailable: ' + str(value.error)
                self.assessment_view.set('summary')
                self.render_assessment()
        else:
            self._last_output = value.output
            self.handle_resource_result(value)
            self.feedback.set(value.tool + ' completed · recorded in Activity')
            text = report([{'tool': value.tool, 'result': {'success': True, 'output': value.output}, 'verification': {'passed': True}}])
            if value.tool == 'intelligence_assess':
                set_text(self.assessment_text, text)
            elif value.tool == 'intelligence_brief':
                set_text(self.report_text, text)
            elif value.tool == 'intelligence_analyze':
                self._analyses[value.output['event_id']] = value.output['analysis'] + (
                    '\n(The model reached its length limit; this may be incomplete.)' if value.output.get('truncated') else '')
                self.assessment_view.set('summary')
                self.render_assessment()
            if self._operation_done:
                self._operation_done()
        self._operation_done = None

    def poll(self):
        if self._closed:
            return
        while True:
            try:
                value = self.updates.get_nowait()
            except queue.Empty:
                break
            if isinstance(value, tuple):
                kind, token, record = value
                if token == self._record_token:
                    self.show_record(record) if kind == 'record' else set_text(self.detail_text, record)
            else:
                self.finish(value)
        self.render()
        self._poll_id = self.after(1000, self.poll)

    def close(self):
        self._closed = True
        self._window_drag = None
        self.stop_camera_refresh()
        for handle in (self._draw_handle, self._poll_id, self._signal_id):
            if handle:
                self.after_cancel(handle)
        self._draw_handle = self._poll_id = self._signal_id = None
        self.destroy()
