"""Conversation details derived from the messages and media actually displayed."""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from app.gui_resources import StringVar


class ChatDetails(tk.Frame):
    EMPTY_STATS = '0 messages · 0 media cards · 0 links'

    def __init__(self, parent, gui):
        super().__init__(parent, bg=gui.PANEL, padx=12, pady=12,
                         highlightthickness=1, highlightbackground=gui.LINE)
        self.gui = gui
        self.messages = 0
        self.sources = []
        self.subject = StringVar(self, value='Your conversation')
        self.stats = StringVar(self, value=self.EMPTY_STATS)
        self.expanded = False
        header = tk.Frame(self, bg=gui.PANEL)
        header.pack(fill='x')
        self.toggle_button = ttk.Button(header, text='Details ▾', style='Compact.Hayat.TButton', command=self.toggle)
        self.toggle_button.pack(side='right')
        tk.Label(header, text='Conversation', bg=gui.PANEL, fg=gui.GOLD,
                 font=('Segoe UI Semibold', 11), anchor='w').pack(side='left')
        # tk labels on the panel colour: ttk labels carry the window's black and drew two black boxes here.
        self.subject_label = tk.Label(self, textvariable=self.subject, wraplength=335, justify='left', anchor='w',
                                     bg=gui.PANEL, fg=gui.WHITE, font=('Segoe UI Semibold', 11))
        self.stats_label = tk.Label(self, textvariable=self.stats, anchor='w', bg=gui.PANEL, fg=gui.MUTED,
                                   font=('Segoe UI', 9), justify='left')
        self.stats_label.pack(fill='x', pady=(5, 0))
        tabs = self.tabs = ttk.Notebook(self, height=220)
        tabs.pack(fill='both', expand=True)
        details, media, sources = (ttk.Frame(tabs, padding=8) for _ in range(3))
        for label, frame in [('Details', details), ('Media', media), ('Sources', sources)]:
            tabs.add(frame, text=label)
        from app.gui_panels import ScrollableColumn
        self.detail_scroll = ScrollableColumn(details, gui, width=315)
        self.detail_scroll.pack(fill='both', expand=True)
        details = self.detail_scroll.body
        self.model = StringVar(self, value='Model: checking runtime')
        self.model_label = ttk.Label(details, textvariable=self.model, wraplength=315)
        self.model_label.pack(anchor='w')
        ttk.Label(details, text='How the last answer was made', foreground=gui.GOLD_SOFT,
                  font=('Segoe UI Semibold', 9)).pack(anchor='w', pady=(8, 0))
        self.provenance = StringVar(self, value='No answer yet.')
        self.provenance_label = ttk.Label(details, textvariable=self.provenance, wraplength=315, justify='left')
        self.provenance_label.pack(anchor='w')
        self.evidence_label = ttk.Label(details, text='Corrections stay with the conversation. Web learning requires source evidence.',
                                        wraplength=315)
        self.evidence_label.pack(anchor='w', pady=10)
        for label, command in [('Summarize this conversation', lambda: gui.use_example('Summarize our conversation and the decisions we made.')),
                               ('Correct the last reply', lambda: gui.use_example('You got me wrong. I meant ')),
                               ('Save transcript', gui.save_transcript)]:
            ttk.Button(details, text=label, command=command).pack(fill='x', pady=3)
        self.media_list = tk.Listbox(media, height=6, bg=gui.PANEL_2, fg=gui.WHITE,
                                    relief='flat', selectbackground=gui.ROYAL)
        self.media_list.pack(fill='x')
        self.media_list.bind('<Double-Button-1>', self.open_media)
        ttk.Label(media, text='Double-click a file to open its video controls.', wraplength=315).pack(pady=6)
        self.source_list = tk.Listbox(sources, height=6, bg=gui.PANEL_2, fg=gui.WHITE,
                                     relief='flat', selectbackground=gui.ROYAL)
        self.source_list.pack(fill='x')
        self.source_list.bind('<Double-Button-1>', self.open_source)
        ttk.Label(sources, text='Links mentioned in chat. A link alone does not verify a claim.', wraplength=315).pack(pady=6)
        self.media_paths = []
        self.tabs.pack_forget()
        self.bind('<Configure>', self._fit, add='+')
        self.detail_scroll.adopt(self.detail_scroll.body)

    def toggle(self):
        self.expanded = not self.expanded
        self.toggle_button.configure(text='Hide details ▴' if self.expanded else 'Details ▾')
        if self.expanded:
            self.subject_label.pack(fill='x', pady=(8, 5), before=self.stats_label)
            self.tabs.pack(fill='both', expand=True, pady=(8, 0))
        else:
            self.subject_label.pack_forget()
            self.tabs.pack_forget()

    def _fit(self, event):
        if event.widget is self:
            width = max(160, event.width - 26)
            self.subject_label.configure(wraplength=width)
            self.stats_label.configure(wraplength=width)
            for label in (self.model_label, self.provenance_label, self.evidence_label):
                label.configure(wraplength=max(120, width - 32))

    @staticmethod
    def _sync_list(widget, values, labels):
        old = getattr(widget, '_chat_row_keys', ())
        if tuple(values) == old:
            return
        selection = {old[index] for index in widget.curselection() if index < len(old)}
        top = old[widget.nearest(0)] if old else None
        widget.delete(0, 'end')
        for index, (value, label) in enumerate(zip(values, labels, strict=True)):
            widget.insert('end', label)
            if value in selection:
                widget.selection_set(index)
        if top in values:
            widget.yview(values.index(top))
        widget._chat_row_keys = tuple(values)

    def observe(self, tag, text):
        if tag not in {'user', 'assistant'}:
            return
        self.messages += 1
        if tag == 'user':
            self.subject.set(text.partition('\n')[2][:100] or text[:100])
        for url in re.findall(r'https?://[^\s<>"\']+', text):
            url = url.rstrip('.,;!)')
            if url not in self.sources:
                self.sources.append(url)
        self.sources = self.sources[-20:]
        self._sync_list(self.source_list, self.sources, self.sources)
        self.media_paths = [str(card.path) for card in self.gui._video_cards.values()
                            if getattr(card, 'path', None)]
        self._sync_list(self.media_list, self.media_paths, [Path(path).name for path in self.media_paths])
        count = len(self.gui._video_cards) + len(self.gui._video_link_cards) + len(self.gui._inline_images)
        self.stats.set(f'{self.messages} messages · {count} media cards · {len(self.sources)} links')
        model = getattr(self.gui.runtime, 'model', None)
        self.model.set('Reply model: ' + str(getattr(model, 'model', None) or 'Unavailable'))
        if tag == 'assistant':
            from app.answer_provenance import describe

            self.provenance.set('\n'.join('• ' + line for line in describe(self.gui.runtime)))

    def reset(self):
        self.messages = 0
        self.sources.clear()
        self.media_paths.clear()
        self.source_list.delete(0, 'end')
        self.media_list.delete(0, 'end')
        self.source_list._chat_row_keys = self.media_list._chat_row_keys = ()
        self.subject.set('Your conversation')
        self.stats.set(self.EMPTY_STATS)
        self.provenance.set('No answer yet.')

    def open_media(self, _event=None):
        selection = self.media_list.curselection()
        if selection and selection[0] < len(self.media_paths):
            self.gui._open_video_studio(self.media_paths[selection[0]])

    def open_source(self, _event=None):
        selection = self.source_list.curselection()
        if selection and selection[0] < len(self.sources):
            self.gui._open_source(self.sources[selection[0]])
