"""A product workspace for drafts, native deliverables and checked delivery ZIPs."""
from __future__ import annotations

import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, ttk

from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over

PRESET_LABELS = {'Weekly planner': 'weekly_planner', 'Content calendar': 'content_calendar', 'Research report': 'research_report'}


class ProductsTab:
    def __init__(self, studio, switcher):
        self.studio, self.gui, self.palette = studio, studio.gui, studio.palette
        self.registry = self.gui.runtime.registry
        self._closed, self._busy = False, False
        self._events = queue.SimpleQueue()
        self._workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hayat-products')
        self._catalog, self.current = [], None
        self.frame = tk.Frame(studio.body, bg=self.palette.PANEL, padx=12, pady=10)
        studio.tab_frames['Products'] = self.frame
        button = ttk.Button(switcher, text='Products', style='Hayat.TButton', command=lambda: studio.show_tab('Products'))
        button.pack(side='left', padx=(0, 4))
        studio.tab_buttons['Products'] = button
        p = self.palette
        tk.Label(self.frame, text='Create → refine → package → verify', bg=p.PANEL, fg=p.GOLD_SOFT,
                 font=('Segoe UI Semibold', 14), anchor='w').pack(fill='x')
        tk.Label(self.frame, text='Your local product catalog. Starter files are editable; review them before release.',
                 bg=p.PANEL, fg=p.MUTED, anchor='w', wraplength=780).pack(fill='x', pady=(3, 9))
        search = tk.Frame(self.frame, bg=p.PANEL)
        search.pack(fill='x', pady=(0, 8))
        self.query = StringVar(self.frame, '')
        ttk.Entry(search, textvariable=self.query).pack(side='left', fill='x', expand=True)
        self.actions = []
        self._button(search, 'New product', self.new_product)
        self._button(search, 'Refresh catalog', self.refresh)
        split = ttk.Panedwindow(self.frame, orient='horizontal')
        split.pack(fill='both', expand=True)
        catalog = tk.Frame(split, bg=p.PANEL)
        self.tree = ttk.Treeview(catalog, columns=('price',), show='tree headings', selectmode='browse', height=7)
        self.tree.heading('#0', text='Product draft')
        self.tree.heading('price', text='Price')
        self.tree.column('#0', width=220, minwidth=120)
        self.tree.column('price', width=110, minwidth=70)
        scroll = ttk.Scrollbar(catalog, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        split.add(catalog, weight=1)
        detail = tk.Frame(split, bg=p.PANEL, padx=10)
        split.add(detail, weight=2)
        self.detail = DarkScrolledText(detail, wrap='word', height=7, bg=p.PANEL_2, fg=p.WHITE,
                                       font=('Segoe UI', 10), state='disabled', padx=10, pady=10)
        self.detail.pack(fill='both', expand=True)
        generate = tk.Frame(detail, bg=p.PANEL)
        generate.pack(fill='x', pady=7)
        self.preset = StringVar(self.frame, 'Weekly planner')
        ttk.Combobox(generate, textvariable=self.preset, values=list(PRESET_LABELS), state='readonly', width=18).pack(side='left')
        self._button(generate, 'Create starter files', self.build_starter)
        self._button(generate, 'Edit listing', self.edit_listing)
        self.files = ttk.Treeview(detail, columns=('state', 'size'), show='tree headings', height=4, selectmode='extended')
        for name, title, width in (('#0', 'Deliverable', 220), ('state', 'State', 75), ('size', 'KB', 60)):
            self.files.heading(name, text=title)
            self.files.column(name, width=width, minwidth=50)
        file_scroll = ttk.Scrollbar(detail, command=self.files.yview)
        self.files.configure(yscrollcommand=file_scroll.set)
        file_scroll.pack(side='right', fill='y')
        self.files.pack(fill='both', expand=True)
        file_actions = tk.Frame(self.frame, bg=p.PANEL)
        file_actions.pack(fill='x', pady=(9, 4))
        for text, action in [('Add files', self.add_files), ('Remove selected', self.remove_files),
                             ('Preview / open', self.open_file), ('Edit file', self.edit_file), ('Refine with HAYAT', self.refine)]:
            self._button(file_actions, text, action)
        delivery = tk.Frame(self.frame, bg=p.PANEL)
        delivery.pack(fill='x', pady=(0, 4))
        for text, action in [('Build delivery ZIP', self.package), ('Verify delivery', self.verify), ('Show delivery folder', self.open_folder)]:
            self._button(delivery, text, action)
        self.state = StringVar(self.frame, 'Loading catalog…')
        tk.Label(self.frame, textvariable=self.state, bg=p.PANEL, fg=p.HOLO, anchor='w', justify='left', wraplength=830).pack(fill='x')
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.select())
        self.query.trace_add('write', lambda *_args: self.filter())
        self.frame.bind('<Destroy>', self._destroy, add='+')
        self._timer = self.frame.after(40, self._drain)
        self.refresh()

    def _button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command, style='Hayat.TButton')
        button.pack(side='left', padx=(6, 0))
        self.actions.append(button)

    def _destroy(self, event):
        if event.widget is self.frame:
            self._closed = True
            self.frame.after_cancel(self._timer)
            self._workers.shutdown(wait=False, cancel_futures=True)

    def _run(self, name, arguments, callback):
        if self._closed or self._busy:
            return False
        self._busy = True
        self.state.set('Working… ' + name.replace('product_', '').replace('_', ' '))
        for button in self.actions:
            button.configure(state='disabled')
        def work():
            try:
                result = self.registry.execute(name, arguments)
                self._events.put((callback, result.output if result.success else None, result.error if not result.success else ''))
            except Exception as exc:
                self._events.put((callback, None, f'{type(exc).__name__}: {exc}'))
        self._workers.submit(work)
        return True

    def _drain(self):
        if self._closed:
            return
        self._timer = self.frame.after(40, self._drain)
        try:
            callback, output, error = self._events.get_nowait()
        except queue.Empty:
            return
        self._busy = False
        for button in self.actions:
            button.configure(state='normal')
        if error:
            self.state.set(error)
        else:
            callback(output)
        if not self._busy and self.tree.selection() and (not self.current or self.current['product']['id'] != self.tree.selection()[0]):
            self.select()

    def refresh(self):
        self._run('product_catalog', {}, self._loaded_catalog)

    def _loaded_catalog(self, output):
        self._catalog = output['products']
        self.filter()
        self.state.set(f'{len(self._catalog)} local product draft(s). Select one to work on its files.')

    def filter(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        needle = self.query.get().casefold()
        for product in self._catalog:
            if needle in (product['title'] + ' ' + product['audience']).casefold():
                self.tree.insert('', 'end', iid=product['id'], text=product['title'],
                                 values=(f"{Decimal(product['price_cents']) / 100:.2f} {product['currency']}",))
        rows = self.tree.get_children()
        if rows:
            self.tree.selection_set(selected[0] if selected and selected[0] in rows else rows[0])
            self.select()
        else:
            self.current = None
            self.files.delete(*self.files.get_children())
            self._text('No matching products. Create a draft to begin.')

    def select(self):
        selected = self.tree.selection()
        if selected:
            self._run('product_details', {'product_id': selected[0]}, self._show)

    def _text(self, value):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', value)
        self.detail.configure(state='disabled')

    def _show(self, details):
        if self.tree.selection() != (details['product']['id'],):
            return
        self.current = details
        product = details['product']
        price = f"{Decimal(product['price_cents']) / 100:.2f} {product['currency']}"
        self._text(f"{product['title']}\nFor {product['audience']} · Draft price {price}\n\n{product['description']}\n\nLicense\n{product['license_text']}")
        self.files.delete(*self.files.get_children())
        for index, asset in enumerate(details['assets']):
            self.files.insert('', 'end', iid=str(index), text=asset['name'], values=('Ready' if asset['exists'] else 'Missing', f"{asset['bytes'] / 1024:.1f}"))
        self.state.set('Deliverables loaded. ' + ('Delivery ZIP exists; verify after changes.' if details['bundle'] else 'Edit your files, then build a delivery ZIP.'))

    def _need_product(self):
        selected = self.tree.selection()
        if self._busy or not self.current or not selected or self.current['product']['id'] != selected[0]:
            self.state.set('Select a product and wait for its details to load.')
            return False
        return True

    def new_product(self):
        ProductForm(self)

    def edit_listing(self):
        if self._need_product():
            ProductForm(self, self.current)

    def build_starter(self):
        if self._need_product():
            self._run('product_build_starter', {'product_id': self.current['product']['id'], 'kind': PRESET_LABELS[self.preset.get()]}, self._show)

    def add_files(self):
        if self._need_product():
            chosen = filedialog.askopenfilenames(parent=self.studio, title='Choose product deliverables')
            if chosen:
                paths = list(dict.fromkeys([item['path'] for item in self.current['assets']] + list(chosen)))
                self._run('product_set_files', {'product_id': self.current['product']['id'], 'files': paths}, self._show)

    def remove_files(self):
        if self._need_product():
            selected = {int(index) for index in self.files.selection()}
            paths = [item['path'] for index, item in enumerate(self.current['assets']) if index not in selected]
            self._run('product_set_files', {'product_id': self.current['product']['id'], 'files': paths}, self._show)

    def _selected_path(self):
        if not self._need_product():
            return None
        selected = self.files.selection()
        if not selected:
            self.state.set('Select a deliverable file first.')
            return None
        return Path(self.current['assets'][int(selected[0])]['path'])

    def open_file(self):
        path = self._selected_path()
        if path:
            action = self.gui._open_pdf_viewer if path.suffix.lower() == '.pdf' else self.gui._open_file
            action(path)

    def edit_file(self):
        path = self._selected_path()
        if path:
            self.gui._edit_file(path)

    def refine(self):
        if self._need_product():
            product = self.current['product']
            self.gui.use_example(f"Read product_details for product {product['id']} ({product['title']}). Improve the selected deliverables for {product['audience']} using the existing file editing tools. Keep backups and verify the content. My requested improvements: ")
            self.gui.root.lift()

    def package(self):
        if self._need_product():
            self._run('product_package', {'product_id': self.current['product']['id'], 'files': [item['path'] for item in self.current['assets']]}, lambda _result: self.select())

    def verify(self):
        if self._need_product():
            def checked(result):
                if result['errors']:
                    self.state.set('Delivery verification failed: ' + '; '.join(result['errors']))
                elif result['needs_rebuild']:
                    self.state.set('ZIP integrity passed. Rebuild delivery: ' + ', '.join(result['sources_changed'] + (['listing changed'] if result['listing_changed'] else [])))
                else:
                    self.state.set('Delivery integrity verified; files and listing match. Review content and license before release.')
            self._run('product_verify_bundle', {'product_id': self.current['product']['id']}, checked)

    def open_folder(self):
        if self._need_product():
            bundle = self.current['bundle']
            if bundle:
                self.gui._open_file(Path(bundle['path']).parent)
            else:
                self.state.set('Build a delivery ZIP first.')


class ProductForm(tk.Toplevel):
    def __init__(self, tab, details=None):
        super().__init__(tab.studio)
        self.withdraw()
        self.tab, self.details, self._saving = tab, details, False
        p = tab.palette
        self.title('Edit product listing' if details else 'New product draft')
        self.transient(tab.studio)
        self.configure(bg=p.BLACK, padx=16, pady=12)
        self.columnconfigure(1, weight=1)
        product = details['product'] if details else {}
        self.values, self.controls = {}, []
        for row, (key, label) in enumerate([('title', 'Title'), ('audience', 'Audience'), ('price', 'Draft price'), ('currency', 'Currency')]):
            tk.Label(self, text=label, bg=p.BLACK, fg=p.WHITE).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=5)
            initial = str(Decimal(product['price_cents']) / 100) if key == 'price' and product else product.get(key, 'USD' if key == 'currency' else '')
            value = self.values[key] = StringVar(self, initial)
            widget = ttk.Combobox(self, textvariable=value, values=['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'NZD'], state='readonly') if key == 'currency' else ttk.Entry(self, textvariable=value)
            widget.grid(row=row, column=1, sticky='ew', pady=5)
            self.controls.append(widget)
        self.multiline = {}
        for row, key, label in [(4, 'description', 'Product brief'), (5, 'license_text', 'Your license terms')]:
            tk.Label(self, text=label, bg=p.BLACK, fg=p.WHITE).grid(row=row, column=0, sticky='nw', padx=(0, 12), pady=5)
            text = self.multiline[key] = DarkScrolledText(self, height=4, wrap='word', bg=p.PANEL_2, fg=p.WHITE, insertbackground=p.GOLD, font=('Segoe UI', 10))
            text.grid(row=row, column=1, sticky='nsew', pady=5)
            text.insert('1.0', product.get(key, ''))
            self.rowconfigure(row, weight=1)
            self.controls.append(text)
        self.message = StringVar(self, 'Local draft. Add explicit license terms; checkout setup can wait.')
        tk.Label(self, textvariable=self.message, bg=p.BLACK, fg=p.MUTED, wraplength=550, anchor='w').grid(row=6, column=0, columnspan=2, sticky='ew', pady=7)
        self.save_button = ttk.Button(self, text='Save product', command=self.save, style='HayatAccent.TButton')
        self.save_button.grid(row=7, column=1, sticky='e')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        place_over(self, tab.studio, (650, 520))
        self.deiconify()

    def close(self):
        if not self._saving:
            self.destroy()

    def save(self):
        if self._saving:
            return
        arguments = {key: value.get().strip() for key, value in self.values.items()}
        arguments.update({key: text.get('1.0', 'end-1c').strip() for key, text in self.multiline.items()})
        if self.details:
            arguments.update(product_id=self.details['product']['id'], expected_sha256=self.details['metadata_sha256'])
        def saved(_output):
            if self.winfo_exists():
                self.destroy()
            self.tab.refresh()
        if self.tab._busy:
            self.message.set('Wait for the current product action to finish.')
            return
        # Use a callback for both success and error so a failed form remains editable.
        original = self.tab.registry.execute
        def done(output):
            if output.get('_form_error'):
                self._saving = False
                if self.winfo_exists():
                    self.message.set(output['_form_error'])
                    self.save_button.configure(state='normal')
                    for widget in self.controls:
                        widget.configure(state='readonly' if isinstance(widget, ttk.Combobox) else 'normal')
            else:
                saved(output)
        name = 'product_update' if self.details else 'product_create'
        self._saving = True
        self.save_button.configure(state='disabled')
        for widget in self.controls:
            widget.configure(state='disabled')
        # This job returns errors to the form instead of closing it.
        self.tab._busy = True
        for button in self.tab.actions:
            button.configure(state='disabled')
        self.tab.state.set('Saving product draft…')
        def work():
            try:
                result = original(name, arguments)
                output = result.output if result.success else {'_form_error': result.error}
            except Exception as exc:
                output = {'_form_error': f'{type(exc).__name__}: {exc}'}
            self.tab._events.put((done, output, ''))
        self.tab._workers.submit(work)


def attach(studio, switcher):
    runtime = studio.gui.runtime
    if getattr(runtime, 'products', None) is None or getattr(runtime, 'registry', None) is None:
        return None
    return ProductsTab(studio, switcher)
