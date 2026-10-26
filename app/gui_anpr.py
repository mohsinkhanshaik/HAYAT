"""Plate recognition dashboard: live preview, records, review queue, vehicles, parking events, privacy."""
from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app.gui_resources import BooleanVar, StringVar
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY

SCOPE = ('For your own vehicles, authorized cameras, parking sites and test footage. HAYAT reads plate text only: '
         'no owner lookup, no addresses, no police or government databases, no fines or notices.')
PERIODS = {'Any time': None, 'Last 24 hours': 1, 'Last 7 days': 7, 'Last 30 days': 30}
VEHICLE_FILTERS = {'All vehicles': None, 'Authorized': True, 'Not on the list': False}


class AnprDashboard(tk.Toplevel):
    def __init__(self, gui, store=None, pipeline=None):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        p = self.palette
        self.store, self.pipeline = store, pipeline
        self._closed = False
        self._stop = threading.Event()
        self._worker = None
        self._updates: queue.Queue = queue.Queue()
        self._photo = self._crop_photo = None
        self.status = StringVar(self, 'Loading the recogniser…')
        self.progress = StringVar(self, 'Open an image or video, or start a webcam or registered camera.')
        self.title('HAYAT · Plate recognition (ANPR)')
        self.configure(bg=p.BLACK)
        self.minsize(1100, 700)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self._build()
        place_over(self, gui.root, (1500, 920))
        self.deiconify()
        if self.store is None or self.pipeline is None:
            threading.Thread(target=self._open_system, name='hayat-anpr-open', daemon=True).start()
        else:
            self._show_status()
            self.load_settings()
            self.refresh_tables()
        self._tick_id = self.after(80, self._tick)

    # ------------------------------------------------------------------ layout
    def _build(self):
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill='x')
        tk.Label(header, text='Plate recognition', bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 19)).pack(side='left')
        tk.Label(header, textvariable=self.status, bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9)).pack(side='right')
        tk.Label(self, text=SCOPE, bg=p.PANEL, fg=p.GOLD_SOFT, font=('Segoe UI', 9), anchor='w', padx=16, pady=5,
                 wraplength=1400, justify='left').pack(fill='x', padx=16)
        bar = tk.Frame(self, bg=p.BLACK, padx=16, pady=8)
        bar.pack(fill='x')
        self.buttons = {}
        for label, command in (('Open image…', self.open_image), ('Open video…', self.open_video)):
            self.buttons[label] = ttk.Button(bar, text=label, style='Compact.Hayat.TButton', command=command)
            self.buttons[label].pack(side='left', padx=(0, 6))
        tk.Label(bar, text='Webcam', bg=p.BLACK, fg=p.MUTED).pack(side='left', padx=(10, 4))
        self.webcam_index = StringVar(self, '0')
        ttk.Spinbox(bar, from_=0, to=16, width=3, textvariable=self.webcam_index).pack(side='left')
        self.buttons['Start webcam'] = ttk.Button(bar, text='Start webcam', style='Compact.Hayat.TButton', command=self.start_webcam)
        self.buttons['Start webcam'].pack(side='left', padx=6)
        tk.Label(bar, text='Camera', bg=p.BLACK, fg=p.MUTED).pack(side='left', padx=(10, 4))
        self.camera_choice = StringVar(self, '')
        self.camera_box = ttk.Combobox(bar, textvariable=self.camera_choice, width=34, state='readonly')
        self.camera_box.pack(side='left')
        self.buttons['Start camera'] = ttk.Button(bar, text='Start camera', style='Compact.Hayat.TButton', command=self.start_camera)
        self.buttons['Start camera'].pack(side='left', padx=6)
        self.stop_button = ttk.Button(bar, text='Stop', style='Compact.Hayat.TButton', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=6)
        ttk.Button(bar, text='Make test samples', style='Compact.Hayat.TButton', command=self.make_samples).pack(side='right')

        body = tk.PanedWindow(self, orient='horizontal', bg=p.BLACK, sashwidth=6, bd=0)
        body.pack(fill='both', expand=True, padx=16, pady=(0, 8))
        left = tk.Frame(body, bg=p.PANEL)
        self.preview = tk.Label(left, bg=p.BLACK, fg=p.MUTED, text='No source running.', font=('Segoe UI', 10))
        self.preview.pack(fill='both', expand=True, padx=8, pady=8)
        tk.Label(left, textvariable=self.progress, bg=p.PANEL, fg=p.WHITE, anchor='w', justify='left', wraplength=760,
                 font=('Segoe UI', 9), padx=10, pady=6).pack(fill='x')
        body.add(left, minsize=480, stretch='always')
        right = tk.Frame(body, bg=p.BLACK)
        body.add(right, minsize=520, width=640, stretch='never')
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill='both', expand=True)
        self.tabs = {}
        for key, title in (('records', 'Records'), ('review', 'Review queue'), ('vehicles', 'Vehicles'),
                           ('events', 'Parking events'), ('settings', 'Settings & privacy')):
            self.tabs[key] = tk.Frame(self.notebook, bg=p.BLACK)
            self.notebook.add(self.tabs[key], text=title)
        self._build_records()
        self._build_review()
        self._build_vehicles()
        self._build_events()
        self._build_settings()
        self.notebook.bind('<<NotebookTabChanged>>', lambda _event: self.refresh_tables())

    def _tree(self, parent, columns, height=14):
        frame = tk.Frame(parent, bg=self.palette.BLACK)
        frame.pack(fill='both', expand=True, pady=4)
        tree = ttk.Treeview(frame, columns=[key for key, _label, _width in columns[1:]], height=height)
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=50, stretch=key == '#0')
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)
        return tree

    def _build_records(self):
        p, tab = self.palette, self.tabs['records']
        filters = tk.Frame(tab, bg=p.BLACK, pady=6)
        filters.pack(fill='x')
        self.search = StringVar(self, '')
        self.status_filter = StringVar(self, 'All')
        self.period = StringVar(self, 'Any time')
        self.vehicle_filter = StringVar(self, 'All vehicles')
        ttk.Entry(filters, textvariable=self.search, width=16).pack(side='left')
        for variable, values, width in ((self.status_filter, ['All', 'new', 'confirmed', 'corrected', 'rejected'], 10),
                                        (self.period, list(PERIODS), 13), (self.vehicle_filter, list(VEHICLE_FILTERS), 15)):
            ttk.Combobox(filters, textvariable=variable, values=values, width=width, state='readonly').pack(side='left', padx=4)
        for variable in (self.search, self.status_filter, self.period, self.vehicle_filter):
            variable.trace_add('write', lambda *_: self.refresh_records())
        self.record_count = tk.Label(tab, text='', bg=p.BLACK, fg=p.MUTED, anchor='w', font=('Segoe UI', 8))
        self.record_count.pack(fill='x')
        self.records = self._tree(tab, [('#0', 'Plate', 130), ('time', 'Seen', 135), ('confidence', 'Conf.', 60),
                                        ('status', 'Status', 80), ('vehicle', 'Vehicle', 110), ('source', 'Source', 140)])
        actions = tk.Frame(tab, bg=p.BLACK, pady=4)
        actions.pack(fill='x')
        for label, command in (('Delete selected', self.delete_selected), ('Delete all of this plate', self.delete_plate)):
            ttk.Button(actions, text=label, style='Compact.Hayat.TButton', command=command).pack(side='left', padx=(0, 4))
        for label, kind in (('SQLite', 'sqlite'), ('CSV', 'csv'), ('JSON', 'json')):
            ttk.Button(actions, text=label, style='Compact.Hayat.TButton', command=lambda value=kind: self.export(value)).pack(side='right', padx=(4, 0))
        tk.Label(actions, text='Export', bg=p.BLACK, fg=p.MUTED).pack(side='right', padx=(8, 2))

    def _build_review(self):
        p, tab = self.palette, self.tabs['review']
        tk.Label(tab, text='Readings below the confidence threshold, or that fit no plate format, wait here for a person.',
                 bg=p.BLACK, fg=p.MUTED, anchor='w', font=('Segoe UI', 9)).pack(fill='x', pady=(6, 0))
        # Bottom first, so the plate image and the buttons keep their room when the window is short.
        row = tk.Frame(tab, bg=p.BLACK)
        row.pack(side='bottom', fill='x', pady=4)
        holder = tk.Frame(tab, bg=p.PANEL, height=150)
        holder.pack(side='bottom', fill='x', pady=4)
        holder.pack_propagate(False)
        self.crop = tk.Label(holder, bg=p.PANEL, text='Select a reading to see its plate image.', fg=p.MUTED)
        self.crop.pack(fill='both', expand=True)
        self.queue_tree = self._tree(tab, [('#0', 'Read as', 130), ('confidence', 'Conf.', 60), ('valid', 'Format', 70),
                                           ('time', 'Seen', 135), ('source', 'Source', 150)], height=10)
        self.queue_tree.bind('<<TreeviewSelect>>', lambda _event: self.show_review_item())
        tk.Label(row, text='Correct plate', bg=p.BLACK, fg=p.WHITE).pack(side='left')
        self.correction = StringVar(self, '')
        ttk.Entry(row, textvariable=self.correction, width=16).pack(side='left', padx=6)
        for label, action in (('Confirm', 'confirm'), ('Save correction', 'correct'), ('Reject', 'reject')):
            ttk.Button(row, text=label, style='Compact.Hayat.TButton', command=lambda value=action: self.review(value)).pack(side='left', padx=3)

    def _build_vehicles(self):
        p, tab = self.palette, self.tabs['vehicles']
        tk.Label(tab, text='Your own vehicles and permit holders. Only plates are stored here: no owner details.',
                 bg=p.BLACK, fg=p.MUTED, anchor='w', font=('Segoe UI', 9)).pack(fill='x', pady=(6, 0))
        self.vehicle_tree = self._tree(tab, [('#0', 'Plate', 130), ('label', 'Label', 150), ('group', 'Group', 110),
                                             ('notes', 'Notes', 200)], height=12)
        form = tk.Frame(tab, bg=p.BLACK, pady=4)
        form.pack(fill='x')
        self.vehicle_fields = {}
        for key, width in (('plate', 12), ('label', 16), ('group', 12), ('notes', 22)):
            tk.Label(form, text=key.title(), bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left', padx=(4, 2))
            self.vehicle_fields[key] = StringVar(self, '')
            ttk.Entry(form, textvariable=self.vehicle_fields[key], width=width).pack(side='left')
        ttk.Button(form, text='Add', style='Compact.Hayat.TButton', command=self.add_vehicle).pack(side='left', padx=6)
        ttk.Button(form, text='Remove selected', style='Compact.Hayat.TButton', command=self.remove_vehicle).pack(side='left')

    def _build_events(self):
        p, tab = self.palette, self.tabs['events']
        tk.Label(tab, text='Internal review events from parking mode. They are not fines or notices: any charge must go '
                           'through your own authorized process.', bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left',
                 wraplength=600, font=('Segoe UI', 9)).pack(fill='x', pady=(6, 0))
        self.event_tree = self._tree(tab, [('#0', 'Plate', 120), ('kind', 'Event', 110), ('time', 'Created', 135),
                                           ('note', 'Note', 260)], height=14)
        row = tk.Frame(tab, bg=p.BLACK, pady=4)
        row.pack(fill='x')
        ttk.Button(row, text='Mark resolved', style='Compact.Hayat.TButton', command=lambda: self.resolve('resolved')).pack(side='left')
        ttk.Button(row, text='Dismiss', style='Compact.Hayat.TButton', command=lambda: self.resolve('dismissed')).pack(side='left', padx=4)

    def _build_settings(self):
        p, tab = self.palette, self.tabs['settings']
        grid = tk.Frame(tab, bg=p.BLACK, padx=8, pady=8)
        grid.pack(fill='x')
        self.setting_vars = {}
        fields = (('region', 'Plate format', ('IN', 'US', 'UK', 'EU')), ('retention_days', 'Keep records (days)', None),
                  ('review_threshold', 'Review below confidence', None), ('frame_stride', 'Read every Nth frame', None),
                  ('ocr_engine', 'OCR engine', ('auto', 'fast-plate-ocr', 'easyocr', 'paddleocr')),
                  ('detector', 'Detector', ('yolo', 'contour')), ('use_gpu', 'GPU', ('auto', 'on', 'off')),
                  ('max_stay_hours', 'Overstay after (hours, 0 = off)', None))
        for row, (key, label, values) in enumerate(fields):
            tk.Label(grid, text=label, bg=p.BLACK, fg=p.WHITE, anchor='w').grid(row=row, column=0, sticky='w', pady=2)
            variable = StringVar(self, '')
            self.setting_vars[key] = variable
            widget = (ttk.Combobox(grid, textvariable=variable, values=list(values), state='readonly', width=16) if values
                      else ttk.Entry(grid, textvariable=variable, width=18))
            widget.grid(row=row, column=1, sticky='w', padx=8)
            if key == 'region':
                self.region_box = widget
        self.store_crops = BooleanVar(self, True)
        self.parking_mode = BooleanVar(self, False)
        ttk.Checkbutton(grid, text='Keep plate images for review', variable=self.store_crops).grid(row=len(fields), column=0, columnspan=2, sticky='w')
        ttk.Checkbutton(grid, text='Parking mode: review event for plates not on the vehicle list',
                        variable=self.parking_mode).grid(row=len(fields) + 1, column=0, columnspan=2, sticky='w')
        tk.Label(tab, text='Authorized RTSP cameras (on your own network)', bg=p.BLACK, fg=p.WHITE, anchor='w',
                 font=('Segoe UI Semibold', 10)).pack(fill='x', padx=8, pady=(8, 0))
        cameras = tk.Frame(tab, bg=p.BLACK, padx=8)
        cameras.pack(fill='x')
        self.camera_list = tk.Listbox(cameras, height=4, bg=p.PANEL, fg=p.WHITE, highlightthickness=0)
        self.camera_list.pack(fill='x')
        self.new_camera = StringVar(self, 'rtsp://')
        row = tk.Frame(cameras, bg=p.BLACK)
        row.pack(fill='x', pady=4)
        ttk.Entry(row, textvariable=self.new_camera, width=48).pack(side='left')
        ttk.Button(row, text='Register camera', style='Compact.Hayat.TButton', command=self.register_camera).pack(side='left', padx=4)
        ttk.Button(row, text='Remove', style='Compact.Hayat.TButton', command=self.remove_camera).pack(side='left')
        tk.Label(tab, text='Custom plate format: code, label and masks (L letter, D digit, A either), e.g. PK, Pakistan, LLLDDDD',
                 bg=p.BLACK, fg=p.MUTED, anchor='w', font=('Segoe UI', 8)).pack(fill='x', padx=8, pady=(8, 0))
        self.custom_spec = StringVar(self, '')
        custom = tk.Frame(tab, bg=p.BLACK, padx=8)
        custom.pack(fill='x')
        ttk.Entry(custom, textvariable=self.custom_spec, width=40).pack(side='left')
        ttk.Button(custom, text='Add format', style='Compact.Hayat.TButton', command=self.add_format).pack(side='left', padx=4)
        actions = tk.Frame(tab, bg=p.BLACK, padx=8, pady=10)
        actions.pack(fill='x')
        ttk.Button(actions, text='Save settings', style='HayatAccent.TButton', command=self.save_settings).pack(side='left')
        ttk.Button(actions, text='Delete old records now', style='Compact.Hayat.TButton', command=self.purge).pack(side='left', padx=6)
        ttk.Button(actions, text='Delete everything', style='Compact.Hayat.TButton', command=self.delete_everything).pack(side='left')

    # ------------------------------------------------------------------ data
    def _open_system(self):
        try:
            from app.anpr import open_system
            store, pipeline = open_system()
            self._updates.put(('opened', (store, pipeline)))
        except Exception as exc:  # shown in the window
            self._updates.put(('error', f'Could not start the recogniser: {type(exc).__name__}: {exc}'))

    def _tick(self):
        if self._closed:
            return
        while not self._updates.empty():
            kind, value = self._updates.get_nowait()
            if kind == 'opened':
                self.store, self.pipeline = value
                kind = 'ready'
            if kind == 'ready':
                self._show_status()
                self.load_settings()
                self.refresh_tables()
            elif kind == 'frame':
                self._show_frame(value)
            elif kind == 'done':
                self._set_running(False)
                self.progress.set(value)
                self._show_status()  # the OCR engine loads on first use
                self.refresh_tables()
            elif kind == 'error':
                self._set_running(False)
                self.progress.set(value)
                self.status.set('Ready' if self.pipeline else 'Unavailable')
        self._tick_id = self.after(80, self._tick)

    def _show_status(self):
        info = self.pipeline.status()
        self.status.set(f"Detector: {info['detector']} ({info['detector_device']}) · OCR: {info['ocr']}")

    def _show_frame(self, result):
        from PIL import Image, ImageTk
        image = Image.fromarray(result.annotated[:, :, ::-1])
        box = (max(320, self.preview.winfo_width() - 16), max(200, self.preview.winfo_height() - 16))
        image.thumbnail(box)
        self._photo = ImageTk.PhotoImage(image, master=self.preview)
        self.preview.configure(image=self._photo, text='')
        plates = ', '.join(f"{plate.get('voted') or plate['plate']} ({plate['confidence']:.0%})" for plate in result.plates)
        self.progress.set(f"Frame {result.frame_index} · {result.seen_at} · " + (plates or 'no plate in view'))

    def _set_running(self, running):
        for button in self.buttons.values():
            button.configure(state='disabled' if running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')

    def _run(self, label, work):
        if self.pipeline is None or self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._set_running(True)
        self.progress.set(f'Reading {label}…')

        def run():
            try:
                message = work()
            except Exception as exc:  # shown in the window
                self._updates.put(('error', f'{label}: {type(exc).__name__}: {exc}'))
                return
            self._updates.put(('done', message))
        self._worker = threading.Thread(target=run, name='hayat-anpr-work', daemon=True)
        self._worker.start()

    def _on_frame(self, result):
        # The latest frame only: a backlog of frames would make the preview lag behind the source.
        if self._updates.qsize() < 3:
            self._updates.put(('frame', result))

    # ------------------------------------------------------------------ sources
    def open_image(self, path=None):
        path = path or filedialog.askopenfilename(parent=self, title='Open an image',
                                                  filetypes=[('Images', '*.jpg *.jpeg *.png *.bmp *.webp'), ('All files', '*.*')])
        if not path:
            return

        def work():
            import cv2
            stored = self.pipeline.process_image(path)
            frame = cv2.imread(path)
            from app.anpr.pipeline import FrameResult, annotate
            self._on_frame(FrameResult(0, Path(path).name, stored, annotate(frame, stored)))
            return f"{Path(path).name}: {len(stored)} plate(s) · " + ', '.join(f"{row['plate']} ({row['confidence']:.0%})" for row in stored)
        self._run(Path(path).name, work)

    def open_video(self, path=None):
        path = path or filedialog.askopenfilename(parent=self, title='Open a video',
                                                  filetypes=[('Videos', '*.mp4 *.avi *.mov *.mkv *.m4v'), ('All files', '*.*')])
        if path:
            self._start(path, Path(path).name)

    def start_webcam(self):
        self._start(int(self.webcam_index.get() or 0), f'webcam {self.webcam_index.get()}')

    def start_camera(self):
        if not self.camera_choice.get():
            self.progress.set('Register a camera on your own network in Settings & privacy first.')
            return
        index = self.camera_box.current()
        self._start(self.store.settings()['authorized_cameras'][index], self.camera_choice.get())

    def _start(self, source, label):
        def work():
            result = self.pipeline.process_video(source, on_frame=self._on_frame, stop=self._stop)
            plates = ', '.join(f"{row['plate']} ({row['confidence']:.0%}, {row['readings']} readings)" for row in result['stored'])
            return f"{label}: {result['frames']} frames, {len(result['stored'])} plate(s) recorded · {plates}"
        self._run(label, work)

    def stop(self):
        self._stop.set()
        self.progress.set('Stopping…')

    def make_samples(self):
        from app.anpr.samples import write_samples
        folder = Path(self.store.folder if self.store else '.') / 'samples'
        truth = write_samples(folder)
        self.progress.set(f"{len(truth)} test samples in {folder} (known plates). Open one with Open image or Open video.")

    # ------------------------------------------------------------------ tables
    def refresh_tables(self):
        if self.store is None:
            return
        # A refresh keeps what the owner had selected (a rebuild used to drop it mid-review).
        kept = {tree: tree.selection() for tree in (self.records, self.queue_tree, self.vehicle_tree, self.event_tree)}
        self.refresh_records()
        self.refresh_queue()
        self.refresh_vehicles()
        self.refresh_events()
        for tree, selection in kept.items():
            alive = [item for item in selection if tree.exists(item)]
            if alive:
                tree.selection_set(alive)

    def refresh_records(self):
        if self.store is None:
            return
        days = PERIODS.get(self.period.get())
        result = self.store.search(text=self.search.get().strip(), status='' if self.status_filter.get() == 'All' else self.status_filter.get(),
                                   since=(datetime.now(UTC) - timedelta(days=days)).isoformat() if days else '',
                                   authorized=VEHICLE_FILTERS.get(self.vehicle_filter.get()), limit=500)
        self.records.delete(*self.records.get_children())
        for row in result['rows']:
            plate = row['corrected_plate'] or row['plate']
            self.records.insert('', 'end', iid=str(row['id']), text=plate + ('' if row['format_valid'] else ' ?'),
                                values=(row['seen_at'][:19].replace('T', ' '), f"{(row['confidence'] or 0):.0%}", row['status'],
                                        row.get('vehicle_label') or '—', row['source']))
        self.record_count.configure(text=f"{len(result['rows'])} shown of {result['total']} matching")

    def refresh_queue(self):
        self.queue_tree.delete(*self.queue_tree.get_children())
        self._queue = {str(row['id']): row for row in self.store.review_queue()}
        for key, row in self._queue.items():
            self.queue_tree.insert('', 'end', iid=key, text=row['plate'], values=(f"{(row['confidence'] or 0):.0%}",
                                   'fits' if row['format_valid'] else 'no match', row['seen_at'][:19].replace('T', ' '), row['source']))

    def refresh_vehicles(self):
        self.vehicle_tree.delete(*self.vehicle_tree.get_children())
        for row in self.store.vehicles():
            self.vehicle_tree.insert('', 'end', iid=row['plate'], text=row['plate'],
                                     values=(row['label'] or '', row['group_name'] or '', row['notes'] or ''))

    def refresh_events(self):
        self.event_tree.delete(*self.event_tree.get_children())
        for row in self.store.events('open'):
            self.event_tree.insert('', 'end', iid=str(row['id']), text=row['plate'],
                                   values=(row['kind'].replace('_', ' '), row['created_at'][:19].replace('T', ' '), row['note']))

    # ------------------------------------------------------------------ actions
    def show_review_item(self):
        selected = self.queue_tree.selection()
        if not selected:
            return
        row = self._queue[selected[0]]
        self.correction.set(row['plate'])
        if row.get('crop_path') and Path(row['crop_path']).exists():
            from PIL import Image, ImageTk
            with Image.open(row['crop_path']) as image:
                image.thumbnail((560, 140))
                self._crop_photo = ImageTk.PhotoImage(image.convert('RGB'), master=self.crop)
            self.crop.configure(image=self._crop_photo, text='')
        else:
            self.crop.configure(image='', text='No plate image kept for this reading.')

    def review(self, action):
        selected = self.queue_tree.selection()
        if not selected:
            return
        try:
            self.store.review(int(selected[0]), action, self.correction.get())
        except ValueError as exc:
            self.progress.set(str(exc))
            return
        self.crop.configure(image='', text='Select a reading to see its plate image.')
        self.refresh_tables()

    def delete_selected(self):
        identities = [int(item) for item in self.records.selection()]
        if identities and messagebox.askyesno('Delete records', f'Delete {len(identities)} record(s) and their images?', parent=self):
            self.progress.set(f'{self.store.delete(identities)} record(s) deleted.')
            self.refresh_tables()

    def delete_plate(self):
        selected = self.records.selection()
        if not selected:
            return
        plate = self.records.item(selected[0], 'text').rstrip(' ?')
        if messagebox.askyesno('Delete a plate', f'Delete every record, image and event of {plate}?', parent=self):
            self.progress.set(f'{self.store.delete_plate(plate)} record(s) of {plate} deleted.')
            self.refresh_tables()

    def export(self, kind):
        target = filedialog.asksaveasfilename(parent=self, title='Export records', defaultextension='.' + ('db' if kind == 'sqlite' else kind),
                                              initialfile=f"plates-{datetime.now():%Y%m%d-%H%M}.{'db' if kind == 'sqlite' else kind}")
        if target:
            days = PERIODS.get(self.period.get())
            self.store.export(target, kind, text=self.search.get().strip(),
                              status='' if self.status_filter.get() == 'All' else self.status_filter.get(),
                              since=(datetime.now(UTC) - timedelta(days=days)).isoformat() if days else '',
                              authorized=VEHICLE_FILTERS.get(self.vehicle_filter.get()))
            self.progress.set(f'Exported the matching records to {target}.')

    def add_vehicle(self):
        values = {key: variable.get().strip() for key, variable in self.vehicle_fields.items()}
        try:
            self.store.add_vehicle(values['plate'], values['label'], values['group'], values['notes'])
        except ValueError as exc:
            self.progress.set(str(exc))
            return
        for variable in self.vehicle_fields.values():
            variable.set('')
        self.refresh_tables()

    def remove_vehicle(self):
        for plate in self.vehicle_tree.selection():
            self.store.remove_vehicle(plate)
        self.refresh_tables()

    def resolve(self, status):
        for identity in self.event_tree.selection():
            self.store.resolve_event(int(identity), status)
        self.refresh_tables()

    # ------------------------------------------------------------------ settings
    def load_settings(self):
        settings = self.store.settings()
        for key, variable in self.setting_vars.items():
            variable.set(str(settings[key]))
        # Built-in formats plus the owner's own.
        self.region_box.configure(values=['IN', 'US', 'UK', 'EU', *(item['code'] for item in settings['custom_formats'])])
        self.store_crops.set(bool(settings['store_crops']))
        self.parking_mode.set(bool(settings['parking_mode']))
        self.camera_list.delete(0, 'end')
        from app.anpr.sources import redact
        labels = [redact(url) for url in settings['authorized_cameras']]
        for label in labels:
            self.camera_list.insert('end', label)
        self.camera_box.configure(values=labels)
        if labels and not self.camera_choice.get():
            self.camera_choice.set(labels[0])

    def save_settings(self):
        values = {key: variable.get().strip() for key, variable in self.setting_vars.items()}
        try:
            changes = {'region': values['region'], 'retention_days': int(values['retention_days']),
                       'review_threshold': float(values['review_threshold']), 'frame_stride': max(1, int(values['frame_stride'])),
                       'ocr_engine': values['ocr_engine'], 'detector': values['detector'], 'use_gpu': values['use_gpu'],
                       'max_stay_hours': float(values['max_stay_hours'] or 0), 'store_crops': bool(self.store_crops.get()),
                       'parking_mode': bool(self.parking_mode.get())}
            self.store.save_settings(changes)
        except ValueError as exc:
            self.progress.set(f'Settings not saved: {exc}')
            return
        self.progress.set('Settings saved. Detector, OCR and GPU changes apply when the window is reopened.')

    def register_camera(self):
        from app.anpr.sources import check_source
        url = self.new_camera.get().strip()
        cameras = list(self.store.settings()['authorized_cameras'])
        try:
            check_source(url, (*cameras, url))
        except (PermissionError, ValueError) as exc:
            self.progress.set(f'Not registered: {exc}')
            return
        if url not in cameras:
            self.store.save_settings({'authorized_cameras': [*cameras, url]})
        self.new_camera.set('rtsp://')
        self.load_settings()
        self.progress.set('Camera registered as authorized. It can be started from the toolbar.')

    def remove_camera(self):
        selected = self.camera_list.curselection()
        if not selected:
            return
        cameras = list(self.store.settings()['authorized_cameras'])
        cameras.pop(selected[0])
        self.store.save_settings({'authorized_cameras': cameras})
        self.camera_choice.set('')
        self.load_settings()

    def add_format(self):
        from app.anpr.formats import custom_format
        parts = [part.strip() for part in self.custom_spec.get().split(',')]
        if len(parts) < 3:
            self.progress.set('Give a code, a label and at least one mask, separated by commas.')
            return
        code, label, spec = parts[0], parts[1], ','.join(parts[2:])
        try:
            made = custom_format(code, label, spec)
        except (ValueError, re.error) as exc:  # a bad regular expression raises re.error
            self.progress.set(f'Format not added: {exc}')
            return
        formats = [item for item in self.store.settings()['custom_formats'] if item.get('code') != made.code]
        self.store.save_settings({'custom_formats': [*formats, {'code': made.code, 'label': made.label, 'spec': spec}]})
        self.load_settings()
        self.progress.set(f'Plate format {made.code} ({made.label}) saved; choose it under Plate format and save to use it.')

    def purge(self):
        self.progress.set(f'{self.store.purge()} record(s) older than the retention period deleted.')
        self.refresh_tables()

    def delete_everything(self):
        if messagebox.askyesno('Delete everything', 'Delete every record, plate image and parking event? Vehicles and '
                                                    'settings are kept.', parent=self):
            self.store.delete_everything()
            self.progress.set('All records, images and events deleted.')
            self.refresh_tables()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            self.after_cancel(self._tick_id)
        except (tk.TclError, AttributeError):
            pass
        self.destroy()
