"""Plain-language task entry points and offline, page-specific help."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.gui_panels import ScrollableColumn
from app.gui_resources import StringVar
from app.gui_windows import place_over


TASKS = (
    ('jobs', 'Find a job', 'Search postings, understand your fit, and prepare applications.', 'Search → review → prepare'),
    ('invention', 'Develop an invention', 'Turn an idea into a proposal with clear tests and recorded results.', 'Define → test → improve'),
    ('chat', 'Talk or make a plan', 'Ask a question, think through a decision, or continue your conversation.', 'Start with your own words'),
    ('cameras', 'Watch public cameras', 'Choose a place and camera, then open its picture or video.', 'Find a place → choose a camera'),
    ('files', 'Work with a file', 'Attach a document, image, or spreadsheet and explain what you need.', 'Choose a file → ask a question'),
    ('research', 'Research or learn', 'Explore a topic, read sources, and build your understanding.', 'Choose a topic → inspect sources'),
    ('systems', 'Connected tools', 'Find connected workspaces, inspect their links, and open the tool you need.', 'Explore tools → inspect connections'),
    ('lab', 'Research lab', 'Test robustness, review code security, and compare predictive models.', 'Measure → inspect evidence → improve'),
    ('brain', 'Brain map', 'Explore the runtime’s components and recorded activity in the system view.', 'Inspect components → follow the evidence'),
)

# Search is local navigation. It never starts a job, opens a camera, or runs a model.
TASK_GROUPS = {'jobs': 'Work', 'files': 'Work', 'invention': 'Create', 'chat': 'Create',
               'cameras': 'Explore', 'research': 'Explore', 'systems': 'System', 'lab': 'System', 'brain': 'System'}
TASK_ALIASES = {'jobs': 'career cv resume hiring application', 'files': 'document pdf spreadsheet image attachment',
                'invention': 'idea prototype formula experiment design', 'chat': 'question conversation decision planning',
                'cameras': 'traffic weather webcam video still public', 'research': 'sources web study knowledge learning',
                'systems': 'features capabilities integrations tools connections',
                'lab': 'prediction models injection guard security cybersecurity robustness ghidra',
                'brain': 'mind neurons memory runtime operator system 3d'}
SCIENCE_TERMS = 'scientific workbench science csv data statistics distribution histogram correlation analysis numeric relationships measure evidence'

GUIDES = {
    'chat': ('Chat & getting started', 'Tell HAYAT the result you want. You can write naturally; special commands are optional.',
             ('Write your question or goal in the message box. Add a budget, deadline, or other limits when they matter.',
              'Attach a file if the answer should use it. Enter sends your message; Shift+Enter adds a new line.',
              'Read the result and any sources or checks. Ask a follow-up, or use Wrong to explain what needs correcting.'),
             'A reply or a checked task result. A suggested action has not happened until the app records its execution.'),
    'jobs': ('Find a job, step by step', 'Your profile, saved postings, and applications each have their own place.',
             ('Profile: add your CV and review the skills, location, work authorization, and roles HAYAT understood.',
              'Matches: enter a role and location, then search. Select a posting to read its requirements, missing skills, source, and listing evidence.',
              'Prepare a packet for a role you want. Review the cover letter and CV, then use the employer’s page or the email review. Track what you actually submitted in Applications.'),
             'A fit score is a reading of the posting and your profile, not a hiring probability. Recently seen does not mean employer-verified open. Preparing a packet does not send it.'),
    'invention': ('Develop an invention, step by step', 'Start with the problem you want to solve. An example is a useful way to learn the workbench.',
             ('Describe the goal and constraints. For a first try, choose an example and press Load example; this fills the form without creating a project.',
              'Add numeric examples if you know the expected results, then create the project. Its target cases are fixed so later attempts are judged consistently.',
              'Use Find a formula for numeric examples, or Run rounds for model proposals. Inspect Overview and Evidence, then test the idea with genuinely new measurements.'),
             'Passing example cases supports that numeric model on those cases. It does not prove a physical invention works or that it is new. Advanced JSON is another way to edit the same case inputs.'),
    'cameras': ('Watch cameras & understand their status', 'Public cameras depend on what their publishers make available.',
             ('Open Cameras, enter a place, and choose a source or camera from the results.',
              'Open the selected camera. LIVE is a video stream, CLIP is a publisher recording, and STILL is an image that refreshes at the publisher’s pace.',
              'If it fails, read the reason and try Retry connection or another camera. Retry video attempts video again after a still fallback. Stop closes the player and decoder processes.'),
             'A visible picture with its publisher and media type. A camera can be offline, delayed, or available only on the publisher’s own website.'),
    'research': ('Research a topic', 'Use sources to build an answer you can inspect.',
             ('Describe the question and any place, date, or source restrictions.',
              'Use Web search or the search tools to gather material. Opening this page alone does not start a new search.',
              'Check publication dates, source links, disagreements, and unanswered questions before relying on the conclusion.'),
             'A source-backed answer with the coverage and uncertainty of the sources actually read.'),
    'world': ('World events & public sources', 'Explore recorded public observations and the sources behind them.',
             ('Choose a region, category, or source. Use the list or map to select an observation.',
              'Open its details to inspect the publisher, location, timestamps, and evidence. Nearby events do not establish a shared cause.',
              'Use Refresh feeds for newer source data. Cameras has its own search and player; the monitor has separate start and pause controls.'),
             'Coverage is limited to connected sources. A feed refresh time is different from the time an event occurred.'),
    'files': ('Work with documents & files', 'Attach a file to ask about its contents, or open Files to browse your project.',
             ('Choose Attach or drop a file into the window.',
              'Explain what you want: a summary, answers, an edit, a comparison, or a new document.',
              'Inspect the result and saved location. Editors keep a backup and reject a save if the original changed after it was read.'),
             'An answer about the supplied content or a file you can open and review. Selecting a file alone does not describe the changes you want.'),
}

_OTHER_GUIDES = {
    'history': ('Conversations', 'Search saved conversations and select one to read or continue.'),
    'tasks': ('Tasks & progress', 'Inspect what ran, what is still running, and the recorded outcome of each task.'),
    'task_graph': ('Plan editor', 'Arrange a task’s steps and dependencies before running the plan.'),
    'gallery': ('Image gallery', 'Browse created and added images, then open an image or its saved location.'),
    'models': ('Models & capabilities', 'Inspect installed models and their actual availability before choosing one.'),
    'background': ('Background learning', 'Review the queue, limits, failures, and pause controls for background work.'),
    'operator': ('Brain & system details', 'Inspect the runtime’s recorded state, routes, and reasoning controls.'),
    'memory': ('Memory & knowledge', 'Find remembered material and inspect where it came from.'),
    'learning': ('Learning', 'Choose what to learn, review progress, and inspect the evidence behind a lesson.'),
    'training': ('Training examples', 'Review examples and evaluation results before promoting a model change.'),
    'health': ('System health', 'Read component checks, their scope, and any item that needs attention.'),
    'improve': ('Improvement & diagnostics', 'Review measured problems and proposed improvements before changing the system.'),
    'toolbox': ('Your toolbox', 'Choose the tools you use often and keep their entry points close at hand.'),
    'tools': ('Available tools', 'Inspect registered tools and open the workspace for the task you want to do.'),
    'settings': ('Settings', 'Adjust appearance, text size, motion, and your working preferences.'),
}


def guide_for(key):
    if key in GUIDES:
        return GUIDES[key]
    title, intro = _OTHER_GUIDES.get(key, ('Using this page', 'Choose an item to inspect its details.'))
    return title, intro, ('Read the current state or choose an item from the list.',
                          'Use the labelled actions to inspect details or make a deliberate change.',
                          'Review the result. Use the status message or System health if something is unavailable.'), \
        'The page describes recorded state. Availability and measurements are shown separately from proposed actions.'


def open_page_guide(parent, palette, key):
    guides = getattr(parent, '_hayat_page_guides', None)
    if guides is None:
        parent._hayat_page_guides = guides = {}
    old = guides.get(key)
    if old is not None and old.winfo_exists():
        old.deiconify()
        old.lift()
        return old
    window = guides[key] = PageGuide(parent, palette, key)
    return window


class PageGuide(tk.Toplevel):
    def __init__(self, parent, palette, key):
        super().__init__(parent.winfo_toplevel())
        self.withdraw()
        self.transient(parent.winfo_toplevel())
        self.configure(bg=palette.BLACK, padx=16, pady=14)
        title, intro, steps, outcome = guide_for(key)
        self.title('HAYAT · ' + title)
        self.bind('<Escape>', lambda _event: (self.destroy(), 'break')[1])
        head = tk.Frame(self, bg=palette.BLACK)
        head.pack(fill='x', pady=(0, 10))
        self.close_button = ttk.Button(head, text='Close guide', command=self.destroy)
        self.close_button.pack(side='right', padx=(8, 0))
        self.title_label = tk.Label(head, text=title, bg=palette.BLACK, fg=palette.WHITE, anchor='w',
                                    font=('Segoe UI Semibold', 16), wraplength=480, justify='left')
        self.title_label.pack(side='left', fill='x', expand=True)
        self.column = ScrollableColumn(self, palette, width=600)
        self.column.pack(fill='both', expand=True)
        self.labels = []
        self._paragraph(intro, palette.MUTED)
        for index, step in enumerate(steps, 1):
            self._paragraph(f'{index}.  {step}', palette.WHITE, pady=10)
        self._paragraph('What the result means', palette.GOLD_SOFT)
        self._paragraph(outcome, palette.MUTED)
        self.column.adopt(self.column.body)
        self.bind('<Configure>', self._fit, add='+')
        place_over(self, parent.winfo_toplevel(), (700, 560))
        self.deiconify()

    def _paragraph(self, text, colour, pady=6):
        label = tk.Label(self.column.body, text=text, bg=self.cget('bg'), fg=colour,
                         font=('Segoe UI', 11), anchor='w', justify='left', wraplength=590)
        label.pack(fill='x', padx=4, pady=pady)
        self.labels.append(label)

    def _fit(self, event):
        if event.widget is self:
            width = max(160, self.winfo_width() - 64)
            title_width = max(140, self.winfo_width() - self.close_button.winfo_reqwidth() - 64)
            if int(self.title_label.cget('wraplength')) != title_width:
                self.title_label.configure(wraplength=title_width)
            for label in self.labels:
                if int(label.cget('wraplength')) != width:
                    label.configure(wraplength=width)


class TaskHome(ScrollableColumn):
    def __init__(self, parent, palette, on_open):
        super().__init__(parent, palette, width=700)
        self.palette, self._columns = palette, None
        self._compact_cards = None
        self._on_open = on_open
        self.buttons, self.cards, self.labels = {}, [], []
        self._content, self._glyphs, self._category_labels = {}, {}, {}
        self._cards_by_key = {}
        self._science_visible = True
        self.visible_keys = tuple(task[0] for task in TASKS)
        self.query = StringVar(self, value='')
        self.category = StringVar(self, value='All tasks')
        self.result_count = StringVar(self, value=f'{len(TASKS) + 1} workspaces')
        self.body.configure(bg=palette.PANEL)
        self.canvas.configure(bg=palette.PANEL)

        # The filter stays in place while the cards scroll, including on short screens.
        self.toolbar = tk.Frame(self, bg=palette.PANEL, padx=5, pady=4)
        self.toolbar.pack(side='top', fill='x', before=self.canvas)
        self.toolbar.columnconfigure(0, weight=1)
        self.heading = tk.Frame(self.toolbar, bg=palette.PANEL)
        self.heading.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        tk.Label(self.heading, text='CHOOSE A WORKSPACE', bg=palette.PANEL, fg=palette.ROYAL_SOFT,
                 font=('Segoe UI Semibold', 9), anchor='w').pack(side='left')
        self.count_label = tk.Label(self.heading, textvariable=self.result_count, bg=palette.PANEL,
                                    fg=palette.MUTED, font=('Segoe UI', 9), anchor='e')
        self.count_label.pack(side='right')
        self.filters = tk.Frame(self.toolbar, bg=palette.PANEL)
        self.filters.grid(row=1, column=0, sticky='ew')
        self.filters.columnconfigure(1, weight=1)
        self.search_label = tk.Label(self.filters, text='Find a task', bg=palette.PANEL, fg=palette.WHITE,
                                     font=('Segoe UI', 9), anchor='w')
        self.search_label.grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.search_entry = ttk.Entry(self.filters, textvariable=self.query, width=18)
        self.search_entry.grid(row=0, column=1, sticky='ew')
        self.clear_button = ttk.Button(self.filters, text='Clear', command=self.clear_filters,
                                        style='Compact.Hayat.TButton', width=6)
        self.clear_button.grid(row=0, column=2, padx=(6, 0))
        self.category_picker = ttk.Combobox(self.filters, textvariable=self.category, state='readonly',
                                             values=('All tasks', 'Work', 'Create', 'Explore', 'System'), width=13)
        self.category_picker.grid(row=0, column=3, sticky='ew', padx=(8, 0))
        self.search_entry.bind('<Return>', self._open_first)
        self.search_entry.bind('<Down>', self._focus_first)
        self.search_entry.bind('<Escape>', self.clear_filters)
        self.category_picker.bind('<<ComboboxSelected>>', self._filter_tasks)
        self.category_picker.bind('<Escape>', self.clear_filters)
        self.query.trace_add('write', self._filter_tasks)
        self.empty = tk.Label(self.body, text='No task matches. Try a different word or clear the filters.',
                               bg=palette.PANEL, fg=palette.MUTED, font=('Segoe UI', 11),
                               justify='left', anchor='w', wraplength=400, padx=18, pady=24)

        from app.gui_science_launch import ScienceLaunch
        self.science_launch = ScienceLaunch(self.body, palette, self._on_open)
        self.science_launch.open_button.bind('<FocusIn>',
            lambda _event: self._reveal(self.science_launch), add='+')

        from app.gui_task_icons import task_glyph
        from app.gui_help import Tooltip
        Tooltip(self.search_entry, 'Filter workspace names and descriptions. Enter opens the first match; Escape clears filters.', palette)
        for index, (key, title, description, hint) in enumerate(TASKS, 1):
            group = TASK_GROUPS[key]
            accent = palette.OLIVE_SOFT if group == 'Work' else palette.GOLD_SOFT if group == 'Create' else palette.ROYAL_SOFT
            card = tk.Frame(self.body, bg=palette.PANEL_2,
                            highlightthickness=1, highlightbackground=palette.LINE)
            tk.Frame(card, bg=accent, width=3).pack(side='left', fill='y')
            content = tk.Frame(card, bg=palette.PANEL_2, padx=10, pady=9)
            content.pack(fill='both', expand=True)
            header = tk.Frame(content, bg=palette.PANEL_2)
            header.pack(fill='x')
            glyph = task_glyph(header, key, palette, accent)
            glyph.pack(side='left', padx=(0, 6))
            button = ttk.Button(header, text=title + '  →', command=lambda key=key: self._on_open(key),
                                style='HayatCard.TButton', takefocus=True)
            button.pack(side='left', fill='x', expand=True)
            button.bind('<FocusIn>', lambda _event, card=card: self._reveal(card))
            button.bind('<FocusIn>', lambda _event, card=card: card.configure(highlightbackground=palette.GOLD), add='+')
            button.bind('<FocusOut>', lambda _event, card=card: card.configure(highlightbackground=palette.LINE), add='+')
            button.bind('<Escape>', self.clear_filters)
            button.bind('<Enter>', lambda _event, card=card: card.configure(highlightbackground=palette.ROYAL_SOFT), add='+')
            button.bind('<Leave>', lambda _event, card=card: card.configure(highlightbackground=palette.LINE), add='+')
            Tooltip(button, description, palette)
            category = tk.Label(content, text=f'{index:02d}  /  {group.upper()}', bg=palette.PANEL_2, fg=accent,
                                  font=('Segoe UI Semibold', 8), anchor='w')
            category.pack(fill='x', pady=(5, 1))
            detail = tk.Label(content, text=description, bg=palette.PANEL_2, fg=palette.WHITE,
                              font=('Segoe UI', 10), anchor='w', justify='left', wraplength=300)
            detail.pack(fill='x', pady=(3, 5))
            note = tk.Label(content, text=hint, bg=palette.PANEL_2, fg=palette.MUTED,
                            font=('Segoe UI', 9), anchor='w', justify='left', wraplength=300)
            note.pack(fill='x')
            self.buttons[key] = button
            self._cards_by_key[key] = card
            self._content[key], self._glyphs[key], self._category_labels[key] = content, glyph, category
            self.cards.append(card)
            self.labels.append((detail, note))
        self.canvas.bind('<Configure>', self._arrange_tasks, add='+')
        self.adopt(self.body)

    def clear_filters(self, _event=None):
        self.category.set('All tasks')
        self.query.set('')
        self.search_entry.focus_set()
        return 'break'

    def focus_search(self):
        self.search_entry.focus_set()

    def _focus_first(self, _event=None):
        if self._science_visible and (self.query.get().strip() or not self.visible_keys):
            self.science_launch.open_button.focus_set()
            self._reveal(self.science_launch)
            return 'break'
        if self.visible_keys:
            key = self.visible_keys[0]
            self.buttons[key].focus_set()
            self._reveal(self._cards_by_key[key])
        return 'break'

    def _open_first(self, _event=None):
        if self._science_visible and (self.query.get().strip() or not self.visible_keys):
            self._on_open('science')
        elif self.visible_keys:
            self._on_open(self.visible_keys[0])
        return 'break'

    def _filter_tasks(self, *_args):
        words = self.query.get().casefold().split()
        group = self.category.get()
        self._science_visible = group in ('All tasks', 'Explore') and all(word in SCIENCE_TERMS for word in words)
        self.visible_keys = tuple(key for key, title, description, hint in TASKS
                                  if group in ('All tasks', TASK_GROUPS[key])
                                  and all(word in ' '.join((title, description, hint, TASK_ALIASES[key])).casefold()
                                          for word in words))
        count, total = len(self.visible_keys) + int(self._science_visible), len(TASKS) + 1
        self.result_count.set(f'{count} of {total} workspaces' if count != total else f'{count} workspaces')
        self._columns = None  # visible cards are re-packed without gaps
        self._arrange_tasks()
        self.canvas.yview_moveto(0)

    def _arrange_tasks(self, event=None):
        width = max(1, event.width if event is not None else self.canvas.winfo_width())
        height = event.height if event is not None else self.canvas.winfo_height()
        compact = height < 280
        self.science_launch.set_compact(compact)
        if compact != self._compact_cards:
            self._compact_cards = compact
            for (key, _title, _description, _hint), (detail, note) in zip(TASKS, self.labels):
                self._content[key].configure(pady=3 if compact else 9)
                category = self._category_labels[key]
                if compact:
                    detail.pack_forget()
                    note.pack_forget()
                    category.pack_forget()
                else:
                    category.pack(fill='x', pady=(5, 1))
                    detail.pack(fill='x', pady=(3, 5))
                    note.pack(fill='x')
        columns = 3 if width >= 1000 else 2 if width >= 550 else 1
        # A larger text setting may need fewer columns than the window's width suggests.
        needed = max((self.buttons[key].winfo_reqwidth() + 70 for key in self.visible_keys), default=240)
        columns = min(columns, max(1, width // needed))
        if width < 620:
            self.category_picker.grid(row=1, column=1, columnspan=2, sticky='ew', padx=0, pady=(6, 0))
        else:
            self.category_picker.grid(row=0, column=3, columnspan=1, sticky='ew', padx=(8, 0), pady=0)
        if self.winfo_height() < 280:
            self.heading.grid_remove()
        else:
            self.heading.grid()
        if columns != self._columns:
            for column in range(3):
                self.body.columnconfigure(column, weight=1 if column < columns else 0, uniform='tasks' if column < columns else '')
            for card in self.cards:
                card.grid_remove()
            if self._science_visible:
                self.science_launch.grid(row=0, column=0, columnspan=columns, sticky='ew', padx=5, pady=(4, 10))
            else:
                self.science_launch.grid_remove()
            for index, key in enumerate(self.visible_keys):
                card = self._cards_by_key[key]
                card.grid(row=1 + index // columns, column=index % columns, sticky='nsew', padx=5, pady=4)
            self._columns = columns
        if self.visible_keys or self._science_visible:
            self.empty.grid_remove()
        else:
            self.empty.configure(wraplength=max(180, width - 45))
            self.empty.grid(row=1, column=0, columnspan=columns, sticky='ew')
        wrap = max(140, width // columns - 50)
        for labels in self.labels:
            for label in labels:
                if int(label.cget('wraplength')) != wrap:
                    label.configure(wraplength=wrap)

    def _reveal(self, card):
        if not card.winfo_ismapped():
            return
        top, bottom = card.winfo_y(), card.winfo_y() + card.winfo_height()
        visible = self.canvas.canvasy(0)
        height, total = self.canvas.winfo_height(), max(1, self.body.winfo_height())
        if top < visible:
            self.canvas.yview_moveto(top / total)
        elif bottom > visible + height:
            self.canvas.yview_moveto(max(0, bottom - height) / total)
