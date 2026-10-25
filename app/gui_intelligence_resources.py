"""Public camera browsing, resource discovery and source-aware point context."""
import tkinter as tk
from tkinter import ttk

from app.gui_intelligence_explore import IntelligenceExploreViews
from app.gui_resources import BooleanVar, StringVar


CAMERA_MEDIA = {'All media': '', 'Refreshing stills': 'image', 'Video / clips': 'video',
                'HLS streams': 'stream', 'Publisher player': 'page'}
CAMERA_SORT = {'Name': 'title', 'Playable first': 'media', 'Newest catalog': 'freshness', 'Nearest event': 'nearest'}
RESOURCE_ACCESS = {'Any access': '', 'Connected': 'connected', 'On demand': 'on_demand',
                   'Linked directory': 'directory', 'Manual / key': 'key_required', 'Bundled': 'bundled'}


def media_label(details):
    from app.intelligence.providers import camera_media_profile
    return camera_media_profile(details)['label']


class IntelligenceResourceViews(IntelligenceExploreViews):
    def build_resource_views(self):
        from app.gui_intelligence import text_box
        self.camera_offset = 0
        self.camera_next_offset = None
        self.camera_rows = {}
        self.camera_selection = None
        self._camera_request_filters = None
        self.camera_available = BooleanVar(self, False)
        self.camera_media = StringVar(self, 'All media')
        self.camera_sort = StringVar(self, 'Name')
        self.camera_radius = StringVar(self, '150')
        self.camera_near = None
        self._camera_photo = None
        self._camera_timer = None
        self.camera_refresh = BooleanVar(self, False)
        for key, title in (('cameras', 'Cameras'), ('resources', 'Resources'), ('context', 'Context')):
            frame = tk.Frame(self.notebook, bg=self.palette.BLACK)
            self.tabs[key] = frame
            self.notebook.add(frame, text=title)
        self.notebook.bind('<<NotebookTabChanged>>', self.resource_tab_changed, add='+')
        controls = tk.Frame(self.tabs['cameras'], bg=self.palette.BLACK)
        controls.pack(fill='x', pady=5)
        self.camera_buttons = {}
        for index, (title, action) in enumerate((('Load cameras', self.all_cameras), ('Watch video', self.watch_camera_video),
                    ('Load snapshot', self.load_camera_frame), ('Previous', lambda: self.camera_page(-1)),
                    ('Next', lambda: self.camera_page(1)), ('Update catalogs', self.refresh_camera_catalogs))):
            button = ttk.Button(controls, text=title, command=action, style='Compact.Hayat.TButton')
            button.grid(row=index//3, column=index%3, sticky='ew', padx=2, pady=2)
            self.camera_buttons[title] = button
        for title in ('Previous', 'Next'):
            self.camera_buttons[title].configure(state='disabled')
        controls.columnconfigure((0, 1, 2), weight=1)
        self.camera_more = ttk.Menubutton(controls, text='More camera actions', style='Compact.Hayat.TButton')
        menu = tk.Menu(self.camera_more, tearoff=False, bg=self.palette.PANEL, fg=self.palette.WHITE)
        for title, action in (('Find streams', self.find_camera_streams), ('Near selected event', self.cameras_near_selection),
                              ('Open in atlas', self.camera_atlas), ('Focus on map', self.camera_focus),
                              ('Open publisher page', self.open_publisher_page), ('Count vehicles', self.count_vehicles)):
            menu.add_command(label=title, command=action)
        self.camera_more.configure(menu=menu)
        self.camera_more.grid(row=2, column=0, sticky='ew', padx=2, pady=2)
        self.camera_sort_choice = ttk.Combobox(controls, textvariable=self.camera_sort, values=list(CAMERA_SORT), state='readonly', width=16)
        self.camera_sort_choice.grid(row=2, column=1, sticky='ew', padx=2, pady=2)
        self.camera_sort_choice.bind('<<ComboboxSelected>>', lambda _event: self.camera_page(0))
        media = ttk.Combobox(controls, textvariable=self.camera_media, values=list(CAMERA_MEDIA), state='readonly', width=16)
        media.grid(row=2, column=2, sticky='ew', padx=2, pady=2)
        media.bind('<<ComboboxSelected>>', lambda _event: self.camera_page(0))
        ttk.Checkbutton(controls, text='Publisher available only', variable=self.camera_available,
                        command=lambda: self.camera_page(0)).grid(row=3, column=0, sticky='w')
        ttk.Checkbutton(controls, text='Refresh selected still', variable=self.camera_refresh,
                        command=self.toggle_camera_refresh).grid(row=3, column=1, sticky='w')
        radius = tk.Frame(controls, bg=self.palette.BLACK)
        radius.grid(row=3, column=2, sticky='ew')
        ttk.Label(radius, text='Near radius km').pack(side='left', padx=2)
        self.camera_radius_entry = ttk.Entry(radius, textvariable=self.camera_radius, width=6)
        self.camera_radius_entry.pack(side='left', fill='x', expand=True)
        self.camera_radius_entry.bind('<Return>', lambda _event: self.camera_page(0))
        from app.intelligence.providers import CAMERA_COVERAGE
        self.camera_message = StringVar(self, 'Public camera networks: ' + CAMERA_COVERAGE + '. Search/region filters above apply. Select a camera; imagery loads only on request.')
        ttk.Label(self.tabs['cameras'], textvariable=self.camera_message, wraplength=700).pack(fill='x', pady=4)
        preview = tk.Frame(self.tabs['cameras'], bg=self.palette.PANEL)
        preview.pack(side='bottom', fill='x', pady=4)
        self.camera_preview = tk.Label(preview, text='No image loaded.', bg=self.palette.PANEL, fg=self.palette.WHITE)
        self.camera_preview.pack(side='left', padx=6)
        self.camera_preview_info = StringVar(self)
        tk.Label(preview, textvariable=self.camera_preview_info, bg=self.palette.PANEL, fg=self.palette.MUTED, wraplength=370, justify='left').pack(side='left', fill='x', expand=True)
        self.camera_tree = self.new_table('cameras', [('provider', 'Network', 170), ('media', 'Media', 120), ('available', 'Available', 80),
                                                      ('country', 'Country', 110), ('distance', 'Distance', 80)])
        self.camera_tree.bind('<<TreeviewSelect>>', lambda _event: self.camera_selected())
        self.camera_tree.bind('<Double-1>', lambda _event: self.watch_camera_video())
        resource_controls = tk.Frame(self.tabs['resources'], bg=self.palette.BLACK)
        resource_controls.pack(side='bottom', fill='x', pady=5)
        ttk.Button(resource_controls, text='Open selected publisher', command=self.open_resource).pack(anchor='w')
        self.resource_text = text_box(resource_controls, self.palette, 5)
        resource_filters = tk.Frame(self.tabs['resources'], bg=self.palette.BLACK)
        resource_filters.pack(fill='x', pady=5)
        self.resource_query = StringVar(self, '')
        self.resource_category = StringVar(self, 'All categories')
        self.resource_access = StringVar(self, 'Any access')
        self.resource_region = StringVar(self, 'World')
        self.resource_count = StringVar(self, '')
        catalog = self.manager.resources()
        self.resource_search = ttk.Entry(resource_filters, textvariable=self.resource_query)
        self.resource_search.grid(row=0, column=0, columnspan=2, sticky='ew', padx=2, pady=2)
        ttk.Button(resource_filters, text='Clear filters', command=self.clear_resource_filters,
                   style='Compact.Hayat.TButton').grid(row=0, column=2, sticky='ew', padx=2, pady=2)
        from app.intelligence.view import REGIONS
        for column, (variable, values) in enumerate((
                (self.resource_category, ['All categories', *catalog.get('categories', [])]),
                (self.resource_access, list(RESOURCE_ACCESS)), (self.resource_region, list(REGIONS)))):
            choice = ttk.Combobox(resource_filters, textvariable=variable, values=values, state='readonly', width=14)
            choice.grid(row=1, column=column, sticky='ew', padx=2, pady=2)
            resource_filters.columnconfigure(column, weight=1, uniform='resource_filter')
        self.resource_count_label = tk.Label(resource_filters, textvariable=self.resource_count, bg=self.palette.BLACK,
                                             fg=self.palette.MUTED, anchor='w', justify='left', wraplength=500)
        self.resource_count_label.grid(row=2, column=0, columnspan=3, sticky='ew', padx=2, pady=2)
        self.resource_count_label.bind('<Configure>', lambda event: self.resource_count_label.configure(wraplength=max(120, event.width - 8)))
        self.resource_tree = self.new_table('resources', [('category', 'Category', 120), ('state', 'Integration', 145), ('region', 'Scope', 140)])
        self.resource_tree.heading('#0', text='Publisher / resource')
        self.resource_rows = {}
        self.resource_tree.bind('<<TreeviewSelect>>', lambda _event: self.resource_selected())
        self.resource_tree.bind('<Return>', lambda _event: self.open_resource())
        self.resource_tree.bind('<Double-1>', lambda _event: self.open_resource())
        for variable in (self.resource_query, self.resource_category, self.resource_access, self.resource_region):
            variable.trace_add('write', lambda *_args: self.filter_resources())
        self.filter_resources()
        context_controls = tk.Frame(self.tabs['context'], bg=self.palette.BLACK, pady=8)
        context_controls.pack(fill='x')
        self.context_lat = StringVar(self, str(self.viewport.latitude))
        self.context_lon = StringVar(self, str(self.viewport.longitude))
        for index, (label, variable) in enumerate((('Latitude', self.context_lat), ('Longitude', self.context_lon))):
            ttk.Label(context_controls, text=label).grid(row=0, column=index*2, padx=3)
            ttk.Entry(context_controls, textvariable=variable, width=12).grid(row=0, column=index*2+1, padx=3)
        for index, (label, action) in enumerate((('Use selected point', self.context_selection), ('Use map center', self.context_center),
                    ('Get point weather', self.context_weather), ('Recall observations', self.context_recall))):
            ttk.Button(context_controls, text=label, command=action, style='Compact.Hayat.TButton').grid(row=1+index//2, column=index%2*2, columnspan=2, sticky='ew', padx=3, pady=3)
        self.context_text = text_box(self.tabs['context'], self.palette)
        self.context_text.configure(state='normal')
        self.context_text.insert('1.0', 'Point weather is model output, not a camera measurement. Retained observations include source times and stale flags. Connected feed evidence also enters Hayat’s research source cache with expiry; it is not promoted into independently verified claims.')
        self.context_text.configure(state='disabled')
        self.build_explore_view()

    def resource_tab_changed(self, _event=None):
        selected = self.notebook.select()
        if selected != str(self.tabs['cameras']):
            self.stop_camera_refresh()
        if selected == str(self.tabs['assessment']):
            self.after_idle(self.draw_chart)
        if selected == str(self.tabs['cameras']) and not self.camera_rows and not self._busy:
            self.load_cameras()

    def camera_filters(self):
        return (self.query.get(), self.feed_key(), str(self.bounds()), self.camera_available.get(), self.camera_media.get(),
                self.camera_near, self.camera_sort.get(), self.camera_radius.get())

    def load_cameras(self):
        if self._closed:
            return
        arguments = {'query': self.query.get(), 'feed': self.feed_key(), 'limit': 200, 'offset': self.camera_offset, 'available_only': self.camera_available.get()}
        arguments['sort'] = CAMERA_SORT.get(self.camera_sort.get(), 'title')
        if arguments['sort'] == 'nearest' and not self.camera_near:
            self.feedback.set('Use More camera actions → Near selected event after selecting an event on the map.')
            return
        if CAMERA_MEDIA.get(self.camera_media.get()):
            arguments['media'] = CAMERA_MEDIA[self.camera_media.get()]
        if self.camera_near:
            # Nearest-first search around an event ignores the text/region filters on purpose.
            try:
                radius = float(self.camera_radius.get())
                if not 0 < radius <= 2000:
                    raise ValueError('radius')
            except ValueError:
                self.feedback.set('Near radius must be a finite number greater than 0 and at most 2000 km.')
                return
            arguments = {**arguments, 'query': '', 'feed': '', 'near_event': self.camera_near, 'radius_km': radius}
        elif self.bounds() is not None:
            arguments['bbox'] = self.bounds()
        if self.work('intelligence_cameras', arguments):
            self._camera_request_filters = self.camera_filters()

    def all_cameras(self):
        self.camera_near = None
        if self.camera_sort.get() == 'Nearest event':
            self.camera_sort.set('Name')
        self.camera_page(0)

    def refresh_camera_catalogs(self):
        from app.intelligence.camera_catalog import refresh_candidates
        feeds = refresh_candidates(self.query.get(), self.feed_key(), self.manager.store.states())
        if not feeds:
            self.feedback.set('No matching catalog is due. Select a camera network in Layers, or clear the search; publisher cooldowns still apply.')
            return
        def done():
            if not self._closed:
                self.camera_offset = 0
                self.load_cameras()
        if self.work('intelligence_refresh', {'feeds': feeds}, done):
            self.camera_message.set(f'Updating {len(feeds)} registered camera catalogs. Repeat Update catalogs to load other due networks. Images load only on request.')

    def cameras_near_selection(self):
        row = (self.selected_record or {}).get('event') or {}
        if row.get('latitude') is None or row.get('category') == 'cameras':
            self.feedback.set('Select an event with a known location first (not a camera).')
            return
        self.camera_near = row['id']
        self.camera_sort.set('Nearest event')
        self.notebook.select(self.tabs['cameras'])
        self.camera_page(0)

    def open_publisher_page(self):
        row = self.camera_rows.get(self.camera_selection) if self.camera_selection else None
        page = (row or {}).get('details', {}).get('page_url')
        if page:
            self.gui._open_source(page)
        else:
            self.feedback.set('Select a camera with a Publisher player media label.')

    def find_camera_streams(self):
        self.stop_camera_refresh()
        self.camera_near = None
        self.camera_media.set('HLS streams')
        self.camera_page(0)

    def watch_camera_video(self):
        row = self.camera_rows.get(self.camera_selection)
        if not row:
            self.feedback.set('Select a camera first; Find streams lists HLS video cameras.')
        elif not row['details'].get('available'):
            self.feedback.set((row['details']['availability_basis'] + '. Use More camera actions → Open publisher page.')
                              if row['details'].get('availability_basis') else 'The publisher marks this camera unavailable.')
        elif row['details'].get('video_url') or row['details'].get('live_page'):
            # Played here, in HAYAT's own player. It used to open the web atlas, and an
            # official livestream (Makkah, Madinah) went to the publisher page in the browser.
            self.stop_camera_refresh()
            from app.gui_video_player import open_camera_window
            from app.intelligence.camera_chat import playback_mode
            camera = {'event_id': row['id'], 'title': row['title'], 'mode': playback_mode(row['details']),
                      'source_url': row.get('url') or row['details'].get('page_url') or '', 'media_label': '',
                      'provider': row['details'].get('provider', ''), 'alternatives': self.camera_alternatives(row['id'])}
            self.camera_player = open_camera_window(self, self.gui, camera)[0]
            self.feedback.set(f"Playing {row['title']} in HAYAT's player · no recording")
        elif row['details'].get('page_url'):
            self.open_publisher_page()
        else:
            self.feedback.set('This publisher supplies still images. Enable Refresh selected still, or use Find streams.')

    def camera_alternatives(self, identifier):
        """The other playable cameras in the current list, for Next camera."""
        return [{'id': key, 'title': row['title']} for key, row in self.camera_rows.items()
                if key != identifier and row['details'].get('available')
                and (row['details'].get('video_url') or row['details'].get('live_page'))][:15]

    def stop_camera_refresh(self):
        if self._camera_timer is not None:
            self.after_cancel(self._camera_timer)
            self._camera_timer = None
        self.camera_refresh.set(False)

    def toggle_camera_refresh(self):
        if not self.camera_refresh.get():
            self.stop_camera_refresh()
            return
        row = self.camera_rows.get(self.camera_selection)
        if not row or not row['details'].get('image_url') or not row['details'].get('available'):
            self.stop_camera_refresh()
            self.feedback.set('Select an available camera with a refreshing still image first.')
            return
        self._refresh_camera_tick()

    def _refresh_camera_tick(self):
        self._camera_timer = None
        if self._closed or not self.camera_refresh.get() or self.notebook.select() != str(self.tabs['cameras']):
            self.stop_camera_refresh()
            return
        row = self.camera_rows.get(self.camera_selection)
        if not row:
            self.stop_camera_refresh()
            return
        if self._busy:
            self._camera_timer = self.after(1000, self._refresh_camera_tick)
            return
        self.load_camera_frame()
        seconds = max(30, min(3600, row['details'].get('refresh_seconds', 120)))
        self._camera_timer = self.after(int(seconds * 1000), self._refresh_camera_tick)

    def open_camera(self, identifier):
        """Show one camera in the best form its publisher offers: snapshot, live video or player page."""
        row = self.manager.event_record(identifier)['event']
        details = row['details']
        if details.get('video_url'):
            self.world_atlas(identifier)
        elif details.get('image_url'):
            self.notebook.select(self.tabs['cameras'])
            self.camera_rows[identifier] = row
            self.camera_selection = identifier
            self._camera_photo = None
            self.camera_preview.configure(image='', text='Loading snapshot…')
            self.camera_preview_info.set(row['title'] + '\n' + details.get('provider', ''))
            self.load_camera_frame()
        elif details.get('page_url'):
            self.gui._open_source(details['page_url'])
        else:
            self.feedback.set('This camera publishes no viewable media.')

    def camera_page(self, direction):
        if self._busy or direction > 0 and self.camera_next_offset is None:
            return
        self.camera_offset = max(0, self.camera_offset + direction*200) if direction else 0
        self.load_cameras()

    def invalidate_cameras(self):
        self.stop_camera_refresh()
        self.camera_offset = 0
        self.camera_next_offset = None
        self.camera_selection = None
        self.camera_near = None
        if self.camera_sort.get() == 'Nearest event':
            self.camera_sort.set('Name')
        self.camera_rows = {}
        self._camera_photo = None
        self.camera_tree.delete(*self.camera_tree.get_children())
        self.camera_preview.configure(image='', text='No image loaded.')
        self.camera_preview_info.set('')
        self.camera_message.set('Filters changed. Press Load cameras to browse matching public catalogs.')
        for title in ('Previous', 'Next'):
            self.camera_buttons[title].configure(state='disabled')

    def camera_selected(self):
        selected = self.camera_tree.selection()
        if not selected or selected[0] not in self.camera_rows:
            return
        identifier = selected[0]
        if identifier != self.camera_selection:
            self.stop_camera_refresh()
            self.camera_selection = identifier
            self._camera_photo = None
            self.camera_preview.configure(image='', text='No image loaded for this camera.')
        row = self.camera_rows[identifier]
        availability = row['details'].get('availability_basis') or str(row['details']['available'])
        self.camera_preview_info.set(row['title'] + '\n' + row['details']['provider'] + ' · ' + media_label(row['details']) + '\nPublisher availability: ' + availability + '\nCatalog fetched: ' + (row.get('retrieved_at') or 'unknown'))
        self.camera_buttons['Load snapshot'].configure(state='normal' if row['details'].get('image_url') else 'disabled')

    def load_camera_frame(self):
        if self.camera_selection:
            self.work('intelligence_camera_frame', {'event_id': self.camera_selection})
        else:
            self.feedback.set('Select a camera first.')

    def world_atlas(self, event_id=''):
        self.work('intelligence_map', {'event_id': event_id} if event_id else {},
                  lambda: self.gui._open_source(self._last_output['map_url']))

    def camera_atlas(self):
        self.world_atlas(self.camera_selection or '')

    def camera_focus(self):
        if self.camera_selection:
            self.show_record(self.manager.event_record(self.camera_selection))
            self.focus_selected()

    def resource_selected(self):
        from app.gui_intelligence import set_text
        selected = self.resource_tree.selection()
        if selected and selected[0] in self.resource_rows:
            row = self.resource_rows[selected[0]]
            set_text(self.resource_text, f"{row['title']}\n{row['integration']} · {row['region']}\n\n{row['notes']}\n\n{row['url']}\n\nA linked directory is not a connected or verified live camera feed.")

    def open_resource(self):
        selected = self.resource_tree.selection()
        if selected and selected[0] in self.resource_rows:
            self.gui._open_source(self.resource_rows[selected[0]]['url'])

    def clear_resource_filters(self):
        self.resource_query.set('')
        self.resource_category.set('All categories')
        self.resource_access.set('Any access')
        self.resource_region.set('World')

    def filter_resources(self):
        """Local metadata only; retain selection and scroll during stable refreshes."""
        from app.gui_intelligence import set_text
        try:
            result = self.manager.resources(query=self.resource_query.get(),
                category='' if self.resource_category.get() == 'All categories' else self.resource_category.get(),
                region=self.resource_region.get(), integration=RESOURCE_ACCESS.get(self.resource_access.get(), ''))
        except ValueError as error:
            self.feedback.set(str(error))
            return
        selected = self.resource_tree.selection()
        position = self.resource_tree.yview()
        self.resource_rows = {row['id']: row for row in result['resources']}
        for key in self.resource_tree.get_children():
            if key not in self.resource_rows:
                self.resource_tree.delete(key)
        for index, row in enumerate(self.resource_rows.values()):
            values = (row['category'], row['integration'], row['region'])
            if self.resource_tree.exists(row['id']):
                self.resource_tree.item(row['id'], text=row['title'], values=values)
                self.resource_tree.move(row['id'], '', index)
            else:
                self.resource_tree.insert('', index, iid=row['id'], text=row['title'], values=values)
        if selected and selected[0] in self.resource_rows:
            self.resource_tree.selection_set(selected[0])
            if position:
                self.resource_tree.yview_moveto(position[0])
            self.resource_selected()
        else:
            set_text(self.resource_text, 'Select a resource to inspect access requirements and publisher details.' if self.resource_rows
                     else 'No resources match these filters. Clear filters or use fewer search terms; no network request was made.')
        self.resource_count.set(f"{result['count']} / {result.get('total_registered', result['count'])} resources · Links are not verified live coverage")

    def context_selection(self):
        if self.selected_record and self.selected_record['event'].get('latitude') is not None:
            row = self.selected_record['event']
            self.context_lat.set(str(row['latitude']))
            self.context_lon.set(str(row['longitude']))
        else:
            self.feedback.set('Select an observation with known coordinates first.')

    def context_center(self):
        self.context_lat.set(str(round(self.viewport.latitude, 5)))
        self.context_lon.set(str(round(self.viewport.longitude, 5)))

    def context_weather(self):
        try:
            self.work('intelligence_weather', {'latitude': float(self.context_lat.get()), 'longitude': float(self.context_lon.get())})
        except ValueError:
            self.feedback.set('Latitude and longitude must be finite world coordinates.')

    def context_recall(self):
        self.work('intelligence_recall', {'query': self.query.get(), 'limit': 20})

    def weather_here(self):
        self.context_center()
        self.notebook.select(self.tabs['context'])
        self.context_weather()

    def handle_resource_result(self, result):
        from app.gui_intelligence import set_text
        from app.intelligence.explore import EXPLORE_TOOLS
        if result.tool == 'intelligence_cameras':
            if self._camera_request_filters != self.camera_filters():
                self.camera_message.set('Filters changed during loading. Loading the current view…')
                self.camera_offset = 0
                self.after_idle(self.load_cameras)
                return
            selected = self.camera_tree.selection()
            position = self.camera_tree.yview()[0], self.camera_tree.xview()[0]
            self.camera_rows = {row['id']: row for row in result.output['cameras']}
            for identifier in self.camera_tree.get_children():
                if identifier not in self.camera_rows:
                    self.camera_tree.delete(identifier)
            self.camera_next_offset = result.output['next_offset']
            for title, enabled in (('Previous', self.camera_offset > 0), ('Next', self.camera_next_offset is not None)):
                self.camera_buttons[title].configure(state='normal' if enabled else 'disabled')
            if self.camera_selection not in self.camera_rows:
                self.camera_selection = None
                self._camera_photo = None
                self.camera_preview.configure(image='', text='Select a camera to load its image.')
                self.camera_preview_info.set('')
            for index, row in enumerate(self.camera_rows.values()):
                details = row['details']
                values = (
                    details.get('provider', ''), media_label(details), 'unverified' if details.get('availability_basis') else 'yes' if details['available'] else 'no', details.get('country', ''),
                    f"{row['distance_km']} km" if 'distance_km' in row else '')
                if self.camera_tree.exists(row['id']):
                    self.camera_tree.item(row['id'], text=row['title'], values=values)
                    self.camera_tree.move(row['id'], '', index)
                else:
                    self.camera_tree.insert('', index, iid=row['id'], text=row['title'], values=values)
            if selected and selected[0] in self.camera_rows:
                self.camera_tree.selection_set(selected[0])
            self.camera_tree.yview_moveto(position[0])
            self.camera_tree.xview_moveto(position[1])
            output = result.output
            networks = ', '.join(f'{name} {count}' for name, count in list(output.get('networks', {}).items())[:8])
            self.camera_message.set(
                f"Showing {len(self.camera_rows)} of {output['total_matching']} matching public cameras"
                + (' nearest the selected event' if self.camera_near else f' · offset {self.camera_offset}')
                + f" · {output.get('with_still_images', 0)} stills, {output.get('with_live_streams', 0)} HLS streams, {output.get('with_video_clips', 0)} clips, "
                f"{output.get('with_publisher_player', 0)} publisher players" + (f'\nNetworks: {networks}' if networks else ''))
        elif result.tool == 'intelligence_camera_frame':
            value = result.output
            if value['event_id'] != self.camera_selection:
                return
            from PIL import Image, ImageTk
            with Image.open(value['path']) as image:
                preview = image.copy()
                preview.thumbnail((340, 210))
            self._camera_photo = ImageTk.PhotoImage(preview, master=self)
            self.camera_preview.configure(image=self._camera_photo, text='')
            self.camera_preview_info.set('Fetched: ' + value['fetched_at'] + '\nCached preview: ' + str(value['cached']) + '\nCapture time unverified. Provider images may be delayed.\n' + value['image_url'])
        elif result.tool in EXPLORE_TOOLS or result.tool == 'intelligence_deep_look':
            self.handle_explore_result(result)
        elif result.tool in ('intelligence_weather', 'intelligence_recall'):
            from app.intelligence.tools import report
            from app.plain_output import describe

            written = report([{'tool': result.tool, 'result': {'success': True, 'output': result.output}, 'verification': {'passed': True}}])
            set_text(self.context_text, written or describe(result.output))
