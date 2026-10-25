"""Map workspace controls, coverage, and accessible observation browsing."""
from collections import Counter
import tkinter as tk
from tkinter import ttk

from app.gui_intelligence_parts import MAP_COAST, MAP_OCEAN, MAP_TEXT, blend, round_rect
from app.gui_resources import BooleanVar
from app.intelligence.classify import CATEGORIES, SEVERITY_LABEL
from app.intelligence.view import DEFAULT_VIEW, relative_time


CONTINENTS = ((-105, 49, 'NORTH AMERICA'), (-59, -20, 'SOUTH AMERICA'),
              (18, 51, 'EUROPE'), (20, 1, 'AFRICA'), (88, 42, 'ASIA'), (135, -25, 'OCEANIA'))


class IntelligenceMapViews:
    def build_map_tools(self):
        """Keep common navigation visible; put occasional actions in one menu."""
        p = self.palette
        self._map_focus = False
        self._focus_layout = None
        self.show_grid = BooleanVar(self, True)
        self.show_labels = BooleanVar(self, True)
        self._visible_map_rows = []
        self._browse_dialog = None
        self._hover_fonts = {}
        self.map_actions = []
        for label, action in (('Zoom +', lambda: self.zoom(1.4)), ('Zoom −', lambda: self.zoom(1 / 1.4)),
                              ('Reset', self.reset_map), ('Fit results', self.fit_results),
                              ('In view', self.browse_map), ('Expand map', self.toggle_map_focus)):
            button = ttk.Button(self.map_toolbar, text=label, command=action, style='Compact.Hayat.TButton', width=0)
            self.map_actions.append(self.map_toolbar.add(button))
        self.map_focus_button = self.map_actions[-1]
        more = ttk.Menubutton(self.map_toolbar, text='Map options', style='Compact.Hayat.TButton')
        self.map_menu = tk.Menu(more, tearoff=False, bg=p.PANEL, fg=p.WHITE,
                                activebackground=p.GOLD, activeforeground=p.BLACK)
        for label, command in (('Fit region', self.fit_region), ('World atlas', self.world_atlas),
                               ('Weather here', self.weather_here), ('Watch this area', self.watch_view),
                               ('Save view', self.save_view), ('Saved views…', self.saved_views),
                               ('Customize', self.customize)):
            self.map_menu.add_command(label=label, command=command)
        self.map_menu.add_separator()
        self.map_menu.add_checkbutton(label='Coordinate grid', variable=self.show_grid, command=self.request_draw)
        self.map_menu.add_checkbutton(label='Geographic labels', variable=self.show_labels, command=self.request_draw)
        more.configure(menu=self.map_menu)
        self.map_toolbar.add(more)
        self.map_options_button = more
        self.bind('<Control-f>', self.focus_map_search, add='+')
        self.bind('<F11>', lambda _event: (self.toggle_map_focus(), 'break')[1], add='+')

    def build_map_summary(self, parent):
        p = self.palette
        self.map_summary = tk.Frame(parent, bg=p.PANEL, padx=8, pady=4)
        self.map_summary.pack(fill='x', before=self.map_toolbar, pady=(0, 4))
        self.map_metrics = {}
        self._summary_layout = None
        self.map_summary.bind('<Configure>', self.layout_map_summary)
        for index, (key, caption) in enumerate((('visible', 'In view'), ('loaded', 'Map loaded'),
                                                 ('unknown', 'No coordinates'), ('sources', 'Feeds current'))):
            self.map_summary.columnconfigure(index, weight=1, uniform='coverage')
            cell = tk.Frame(self.map_summary, bg=p.PANEL)
            cell.grid(row=0, column=index, sticky='ew', padx=5)
            value = tk.Label(cell, text='—', anchor='w', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 12))
            value.pack(side='left')
            tk.Label(cell, text=caption, anchor='w', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left', padx=(6, 0))
            self.map_metrics[key] = value
        from app.gui_help import Tooltip
        for key, explanation in (
            ('visible', 'Loaded observations whose coordinates fall inside this map view.'),
            ('loaded', 'Displayed subset / all located matches in enabled layers. Dense views load at most 1,000 markers.'),
            ('unknown', 'Matching events without coordinates. They remain available in Events.'),
            ('sources', 'Feeds whose last successful fetch is current. This does not establish that an event is still active.'),
        ):
            self.map_metrics[key]._intel_tip = Tooltip(self.map_metrics[key], explanation, p)

    def layout_map_summary(self, event):
        narrow = event.width < 650
        stacked = event.width < 1000
        layout = (narrow, stacked)
        if layout == self._summary_layout:
            return
        self._summary_layout = layout
        for index, key in enumerate(('visible', 'loaded', 'unknown', 'sources')):
            value = self.map_metrics[key]
            cell = value.master
            if narrow and index >= 2:
                cell.grid_remove()
                self.map_summary.columnconfigure(index, weight=0, minsize=0, uniform='')
                continue
            cell.grid()
            self.map_summary.columnconfigure(index, weight=1, uniform='coverage')
            for widget in cell.winfo_children():
                widget.pack_configure(side='top' if stacked else 'left', fill='x' if stacked else 'none', padx=0 if stacked else (0, 6))

    def update_map_summary(self, visible):
        self._visible_map_rows = list(visible)
        value = self.current_dashboard or {}
        sources = value.get('sources', [])
        current = sum(not source['stale'] for source in sources)
        loaded, total = len(self.map_rows), value.get('mapped_total', len(self.map_rows))
        compact = self.size()[0] < 520
        self.map_metrics['visible'].configure(text=f'{len(visible):,}')
        self.map_metrics['loaded'].configure(text=f'{loaded:,}/{total:,}' if compact else f'{loaded:,} / {total:,}',
                                              fg=self.palette.GOLD if loaded < total else self.palette.WHITE)
        self.map_metrics['unknown'].configure(text=f"{value.get('aggregates', {}).get('unknown_location', 0):,}")
        self.map_metrics['sources'].configure(text=f'{current}/{len(sources)}',
                                               fg=self.palette.GOLD if current < len(sources) else self.palette.WHITE)
        self.map_actions[3].configure(state='normal' if self.map_rows else 'disabled')
        self.map_actions[4].configure(state='normal' if visible else 'disabled')
        self.legend_title.configure(text=f'Layers · {len(self.map_categories())}/{len(CATEGORIES)}')

    def toggle_map_focus(self):
        self.notebook.select(self.tabs['map'])
        self._map_focus = not self._map_focus
        if self._map_focus:
            self._focus_layout = (self._compact, self.map_split.sash_coord(0), getattr(self, '_sash_user', False))
        self.map_split.paneconfigure(self.cards_frame, hide=self._map_focus)
        hide = self._map_focus or self.winfo_width() < 1180
        self.body.paneconfigure(self.live, hide=hide)
        self._live_hidden = hide
        self.map_focus_button.configure(text='Show details' if self._map_focus else 'Expand map')
        if hasattr(self.map_focus_button, '_intel_tip'):
            # The button keeps its stable expand icon in compact mode.
            self.map_focus_button._intel_tip.text = 'Expand map / show details (F11, Escape to restore)'
        if not self._map_focus and self._focus_layout:
            compact, (x, y), dragged = self._focus_layout
            self.update_idletasks()
            if compact == self._compact:
                self.map_split.sash_place(0, x, y)
                self._sash_user = dragged
            else:
                self._sash_user = False
                self._place_map_sash()
        self.map_toolbar.arrange()
        self.request_draw()

    def escape_map(self, _event=None):
        if self._map_focus:
            self.toggle_map_focus()
        else:
            self.close()
        return 'break'

    def focus_map_search(self, _event=None):
        self.show_filter_bar()
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, 'end')
        return 'break'

    def clear_map_filters(self):
        self._resetting_filters = True
        try:
            self.query.set('')
            self.layer.set('All layers')
            self.category.set('All events')
            self.severity.set('All severities')
            self.time_range.set('Any time')
            self.region.set('World')
            self.only_viewport.set(False)
            for key, variable in self.layer_vars.items():
                variable.set(key not in DEFAULT_VIEW['hidden_layers'])
            self.viewport.fit([-180, -90, 180, 90])
        finally:
            self._resetting_filters = False
        self.filter_changed()
        self.feedback.set('Filters cleared. Public cameras remain available in Cameras and the Layers control.')

    def fit_results(self):
        if self.viewport.fit_points(self.map_rows.values()):
            self.view_changed()
            self.feedback.set('Fitted the loaded map observations. Map loaded shows whether further matching records are omitted.')

    def browse_map(self, rows=None):
        """A keyboard-accessible route to each report, including coincident markers."""
        rows = list(self._visible_map_rows if rows is None else rows)
        if not rows:
            return None
        if self._browse_dialog is not None and self._browse_dialog.winfo_exists():
            self._browse_dialog.destroy()
        rows.sort(key=lambda row: (-row.get('severity_rank', 0), str(row.get('headline') or row.get('title') or '')))
        p = self.palette
        dialog = tk.Toplevel(self)
        self._browse_dialog = dialog
        dialog.title(f'{len(rows):,} map observations · HAYAT')
        dialog.configure(bg=p.BLACK)
        dialog.transient(self)
        dialog.minsize(480, 280)
        heading = tk.Label(dialog, text=f'{len(rows):,} observations in this selection', bg=p.BLACK, fg=p.WHITE,
                           anchor='w', padx=12, pady=10, font=('Segoe UI Semibold', 13))
        heading.pack(fill='x')
        note = tk.Label(dialog, text='Choose a report to inspect its source and location. This list reflects the loaded map subset.',
                        bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', padx=12, wraplength=570)
        note.pack(fill='x')
        note.bind('<Configure>', lambda event: note.configure(wraplength=max(200, event.width-24)))
        frame = tk.Frame(dialog, bg=p.BLACK, padx=12, pady=10)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=('severity', 'source', 'age'), selectmode='browse', height=10)
        tree.heading('#0', text='Observation')
        tree.column('#0', width=280, minwidth=150)
        for key, label, width in (('severity', 'Priority', 75), ('source', 'Source', 150), ('age', 'Reported', 90)):
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=65, stretch=False)
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        scroll.pack(side='right', fill='y')
        bottom = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        bottom.pack(side='bottom', fill='x')
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=bottom.set)
        tree.pack(fill='both', expand=True)
        for row in rows:
            tree.insert('', 'end', iid=row['id'], text=row.get('headline') or row['title'],
                        values=(SEVERITY_LABEL[row['severity']], row.get('provider') or row.get('feed', ''),
                                relative_time(row.get('observed_at')) or 'Unknown'))
        def choose(_event=None):
            if selected := tree.selection():
                identifier = selected[0]
                dialog.destroy()
                self.select_event(identifier)
            return 'break'
        actions = tk.Frame(dialog, bg=p.BLACK, padx=12, pady=8)
        actions.pack(side='bottom', fill='x', before=frame)
        ttk.Button(actions, text='Inspect selected', command=choose, style='HayatAccent.TButton').pack(side='left')
        ttk.Button(actions, text='Close', command=dialog.destroy, style='Hayat.TButton').pack(side='right')
        tree.bind('<Return>', choose)
        tree.bind('<Double-1>', choose)
        dialog.bind('<Escape>', lambda _event: (dialog.destroy(), 'break')[1])
        tree.selection_set(rows[0]['id'])
        tree.focus(rows[0]['id'])
        from app.gui_windows import place_over
        place_over(dialog, self, (760, 460))
        tree.focus_set()
        dialog.tree = tree
        dialog.actions = actions
        dialog.choose = choose
        return dialog

    def draw_geographic_labels(self, project, width, height):
        if not self.show_labels.get() or self.viewport.zoom > 3:
            return
        for lon, lat, label in CONTINENTS:
            x, y = project(lon, lat)
            if 65 < x < width - 65 and 45 < y < height - 40:
                self.map.create_text(x, y, text=label, fill='#6F8CA5', font=('Segoe UI Semibold', 8), tags='geography')

    def draw_map_empty(self, visible):
        if visible:
            return
        width, height = self.size()
        if height < 160:
            return
        if self.map_rows:
            title, hint = 'No loaded observations in this view', 'Use View only to search here, or Fit results.'
        elif not self.map_categories():
            title, hint = 'All map layers are hidden', 'Open Layers to choose observations.'
        else:
            title, hint = 'No matching located observations', 'Clear filters or check Sources. Coverage is partial.'
        box_width = min(350, width - 32)
        x, y = width / 2, height / 2
        round_rect(self.map, x - box_width/2, y-45, x+box_width/2, y+45, 10,
                   fill=blend(self.palette.PANEL, MAP_OCEAN, .2), outline=MAP_COAST, tags='empty-map')
        self.map.create_text(x, y-15, text=title, width=box_width-24, fill=self.palette.WHITE,
                             font=('Segoe UI Semibold', 10), tags='empty-map')
        self.map.create_text(x, y+18, text=hint, width=box_width-24, fill=MAP_TEXT, font=('Segoe UI', 9), tags='empty-map')

    def draw_map_hover(self, event, identifier=None, cluster=None):
        self.map.delete('hover-card')
        if not identifier and not cluster:
            return
        width, height = self.size()
        if width < 320 or height < 180:
            return
        if identifier:
            row = self.map_rows[identifier]
            heading = row.get('headline') or row['title']
            detail = f"{SEVERITY_LABEL[row['severity']]} · {row.get('provider') or row.get('feed', '')}"
            hint = 'Source stale' if row.get('stale') else relative_time(row.get('observed_at')) or 'Event time unknown'
        else:
            heading = f'{len(cluster[2]):,} reports in this cluster'
            kinds = Counter(CATEGORIES[row['category']][0] for row in cluster[2])
            detail = ' · '.join(f'{count} {kind}' for kind, count in kinds.most_common(2))
            hint = 'Click to inspect reports' if self.viewport.zoom >= 8 else 'Click to zoom · Right-click to inspect'
        box_width, box_height = min(300, width-24), 94
        left = max(12, min(event.x+18, width-box_width-12))
        top = event.y+18 if event.y+box_height+30 < height else event.y-box_height-18
        top = max(40, min(top, height-box_height-12))
        round_rect(self.map, left, top, left+box_width, top+box_height, 8, fill='#152534', outline=MAP_COAST, tags='hover-card')
        from tkinter import font as tkfont
        from app.gui_intelligence_parts import fit
        for offset, text, font, colour in ((17, heading, ('Segoe UI Semibold', 10), '#F3F6FC'),
                                          (45, detail, ('Segoe UI', 9), '#B7C9DD'), (73, hint, ('Segoe UI', 9), '#DDBB68')):
            if font not in self._hover_fonts:
                self._hover_fonts[font] = tkfont.Font(self, font=font)
            measured = fit(text, self._hover_fonts[font], box_width-24)
            self.map.create_text(left+12, top+offset, text=measured, anchor='w', fill=colour, font=font, tags='hover-card')

    def map_context(self, event):
        if cluster := self.cluster_at(event.x, event.y):
            self.browse_map(cluster[2])
        elif identifier := self.marker_at(event.x, event.y):
            self.select_event(identifier)
        return 'break'
