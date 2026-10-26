"""Searchable learning roadmap, connected to the learner's actual saved evidence."""
import time
import tkinter as tk
from tkinter import ttk

from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.learning.atlas import learning_atlas
from app.gui_fonts import DISPLAY


class LearningAtlas(tk.Toplevel):
    def __init__(self, gui):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.learning, self.palette = gui, gui.runtime.learning, type(gui)
        self._closed, self._timer = False, None
        self.rows, self._cards = {}, []
        self.title('HAYAT · Learning atlas')
        self.configure(bg=gui.BLACK)
        self.transient(gui.root)
        self.minsize(760, 640)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=18, pady=12)
        header.pack(fill='x')
        tk.Label(header, text='Your learning atlas', bg=p.BLACK, fg=p.WHITE,
                 font=(DISPLAY, 21)).pack(anchor='w')
        self.status = StringVar(self)
        tk.Label(header, textvariable=self.status, bg=p.BLACK, fg=p.MUTED,
                 font=('Segoe UI', 10)).pack(anchor='w', pady=4)
        self.sky = tk.Canvas(self, height=200, bg=p.PANEL, highlightthickness=0)
        self.sky.pack(fill='x', padx=18)
        self.sky.bind('<Configure>', lambda _event: self.draw_domains())
        filters = tk.Frame(self, bg=p.BLACK, padx=18, pady=10)
        filters.pack(fill='x')
        self.query, self.domain = StringVar(self), StringVar(self, 'All domains')
        self.search_label = ttk.Label(filters, text='Find a topic')
        self.query_entry = ttk.Entry(filters, textvariable=self.query, width=23)
        self.domain_picker = ttk.Combobox(filters, textvariable=self.domain, width=24, state='readonly')
        self.refresh_button = ttk.Button(filters, text='Refresh progress', command=self.refresh)
        self.motion_button = ttk.Button(filters, command=self.toggle_motion)
        filters.columnconfigure(1, weight=1)
        self._filters_compact = None
        filters.bind('<Configure>', lambda event: self.layout_filters(event.width))
        self.layout_filters(1080)
        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=18)
        listing = tk.Frame(body, bg=p.PANEL)
        body.add(listing, weight=3)
        self.tree = ttk.Treeview(listing, columns=('progress', 'path'), show='tree headings', height=10)
        for key, title, width in (('#0', 'Topic', 165), ('progress', 'Assessment', 125), ('path', 'Prerequisites', 120)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=80)
        scroll = ttk.Scrollbar(listing, command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.describe_selection())
        detail = tk.Frame(body, bg=p.PANEL, padx=10)
        body.add(detail, weight=2)
        self.detail = DarkScrolledText(detail, height=12, width=35, wrap='word', bg=p.PANEL, fg=p.WHITE,
                                       font=('Segoe UI', 11), padx=10, pady=10, state='disabled')
        self.detail.pack(fill='both', expand=True)
        actions = tk.Frame(self, bg=p.BLACK, padx=18, pady=10)
        actions.pack(fill='x')
        for label, kind in (('Prepare lesson', 'teach me '), ('Prepare quiz', 'quiz me on '), ('Research sources', 'research ')):
            ttk.Button(actions, text=label, command=lambda value=kind: self.prepare(value),
                       style='HayatAccent.TButton' if kind == 'teach me ' else 'Hayat.TButton').pack(side='left', padx=3)
        ttk.Button(actions, text='Due reviews', command=self.due_reviews).pack(side='right')
        tk.Label(self, text='Select a domain or topic. Actions prepare a chat request; press Send to begin. Motion is decorative.',
                 bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), wraplength=900).pack(fill='x', padx=18, pady=(0, 10))
        self.query.trace_add('write', lambda *_: self.filter_topics())
        self.domain.trace_add('write', lambda *_: self.filter_topics())
        self.refresh()
        place_over(self, gui.root, (1080, 830))
        self.deiconify()
        self.animate()

    def layout_filters(self, width):
        compact = width < 900
        if compact == self._filters_compact:
            return
        self._filters_compact = compact
        for widget in (self.search_label, self.query_entry, self.domain_picker, self.refresh_button, self.motion_button):
            widget.grid_forget()
        self.search_label.grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.query_entry.grid(row=0, column=1, columnspan=4 if compact else 1, sticky='ew')
        for column, widget in enumerate((self.domain_picker, self.refresh_button, self.motion_button), 1 if compact else 2):
            widget.grid(row=1 if compact else 0, column=column, sticky='ew', padx=(8, 0), pady=(6, 0) if compact else 0)

    def refresh(self):
        self.atlas = learning_atlas(self.learning)
        self.domain_picker.configure(values=['All domains'] + [row['title'] for row in self.atlas['domains']])
        self.status.set(f"{len(self.atlas['domains'])} fields · {self.atlas['total']} starting topics · "
                        f"{self.atlas['assessed']} assessed · progress comes from answers and exercises")
        self.filter_topics()
        self.draw_domains()

    def filter_topics(self):
        selection = self.tree.selection()
        selected = selection[0] if selection else None
        words = self.query.get().casefold().split()
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for domain in self.atlas['domains']:
            if self.domain.get() not in ('All domains', domain['title']):
                continue
            for row in domain['topics']:
                if not all(word in (row['topic'] + ' ' + domain['title']).casefold() for word in words):
                    continue
                self.rows[row['topic']] = {**row, 'domain': domain}
                progress = f"{row['mastery']:.0%} · {row['level'].lower()}" if row['tested'] else 'Not assessed'
                self.tree.insert('', 'end', iid=row['topic'], text=row['topic'], values=(progress,
                                 f"{len(row['prerequisite_gaps'])} to check" if row['prerequisite_gaps'] else 'Ready to explore'))
        if self.rows:
            target = selected if selected in self.rows else next(iter(self.rows))
            self.tree.selection_set(target)
            self.tree.see(target)
            self.describe_selection()
        else:
            self.set_detail('No matching topics. Try another search or choose All domains.')

    def set_detail(self, text):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def describe_selection(self):
        selection = self.tree.selection()
        row = self.rows.get(selection[0]) if selection else None
        if not row:
            return
        lines = [row['topic'], row['domain']['purpose'], '',
                 'Prerequisites: ' + (', '.join(row['prerequisites']) or 'No prerequisites in this roadmap.'),
                 'Check first: ' + (', '.join(row['prerequisite_gaps']) or 'No prerequisite gaps recorded.'), '',
                 'Learn → explain in your own words → apply to a new example → test → review.', '',
                 f"Assessment estimate: {row['mastery']:.0%}; confidence {row['confidence']:.0%}."
                 if row['tested'] else 'Understanding has not been assessed yet.',
                 'Next review: ' + (row['next_review'] or 'Complete an exercise to schedule a review.'), '',
                 self.atlas['basis']]
        self.set_detail('\n'.join(lines))

    def prepare(self, prefix):
        selection = self.tree.selection()
        if selection:
            self.gui.use_example(prefix + selection[0])
            self.close()

    def due_reviews(self):
        rows = self.learning.due_reviews()
        self.set_detail('Due for review\n\n' + ('\n'.join(f"{row['topic']} · {row['mastery']:.0%}" for row in rows)
                        if rows else 'No reviews due. Choose a topic and complete an exercise first.'))

    def draw_domains(self):
        self.sky.delete('all')
        self._cards = []
        width = max(760, self.sky.winfo_width())
        for index, domain in enumerate(self.atlas['domains']):
            col, row = index % 4, index // 4
            left, top = col * width / 4 + 10, row * 64 + 8
            right = (col + 1) * width / 4 - 10
            tag = f'domain-{index}'
            self.sky.create_rectangle(left, top, right, top+51, fill=self.palette.PANEL_2,
                                      outline=domain['colour'], tags=tag)
            self.sky.create_text(left+10, top+15, text=domain['title'], anchor='w', fill=domain['colour'],
                                 font=('Segoe UI Semibold', 10), tags=tag)
            self.sky.create_text(left+10, top+35, text=f"{domain['assessed']}/{domain['total']} assessed", anchor='w',
                                 fill=self.palette.MUTED, font=('Segoe UI', 9), tags=tag)
            self.sky.tag_bind(tag, '<Button-1>', lambda _event, title=domain['title']: self.domain.set(title))
            self._cards.append((left, top, right, domain['colour']))

    def toggle_motion(self):
        self.gui._toggle_depth_motion()
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        self.animate()

    def animate(self):
        self._timer = None
        if self._closed:
            return
        moving = getattr(getattr(self.gui, 'depth_nav', None), 'motion', True)
        self.motion_button.configure(text='Pause motion' if moving else 'Enable motion')
        self.sky.delete('pulse')
        if moving and self.winfo_ismapped():
            phase = time.monotonic() * .25
            for index, (left, top, right, colour) in enumerate(self._cards):
                x = left + (right-left) * ((phase + index/12) % 1)
                y = top + 51
                self.sky.create_oval(x-2, y-2, x+2, y+2, fill=colour, outline='', tags='pulse')
        self._timer = self.after(100 if moving and self.winfo_ismapped() else 600, self.animate)

    def close(self):
        self._closed = True
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        self.destroy()
