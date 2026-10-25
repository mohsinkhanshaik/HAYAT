"""A local professional contact book with evidence and unsent outreach drafts.

The workspace keeps owner-entered contacts separate from researched facts. It
never searches faces, sends messages, or predicts someone's willingness to help.
"""
from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from app.gui_empty import show_empty
from app.gui_intelligence_parts import FlowRow
from app.gui_panels import ScrollableColumn
from app.gui_resources import BooleanVar, StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over


CHANNEL_STATUS = {
    'Owner provided / unverified': 'provided',
    'Public professional contact': 'public_professional',
    'Explicit permission to contact': 'consent',
}
SORTS = ('Name', 'Next follow-up', 'Goal relevance')


def evidence_text(record: dict[str, Any], goal: str = '') -> str:
    """Describe recorded evidence without inferring personality or friendship."""
    if not record:
        return ('Save someone you already know, or research a known professional name.\n\n'
                'Add what they publicly work on, the source, and what you could contribute. '
                'A match means recorded topics overlap; it does not establish willingness to help.')
    lines = [str(record.get('name', 'Contact')),
             ' · '.join(str(record.get(key, '')) for key in ('role', 'organization') if record.get(key)), '']
    if record.get('revoked') or record.get('revoked_at'):
        return '\n'.join(lines + ['Permission revoked. Contact routes are withheld; this record cannot be used for outreach.'])
    lines += ['RECORDED TOPICS', ', '.join(record.get('expertise', [])) or 'No expertise recorded.',
              'Shared goals: ' + (', '.join(record.get('goals', [])) or 'Not recorded.'), '',
              'EVIDENCE', 'Source: ' + (record.get('source_url') or 'Owner provided; not independently verified.')]
    for channel in record.get('channels', []):
        status = next((label for label, value in CHANNEL_STATUS.items() if value == channel.get('status')), 'Unverified')
        lines += [f"{channel.get('kind', 'route')}: {channel.get('value', '')}",
                  f"  {status}; source: {channel.get('source_url') or record.get('source_url') or 'owner provided'}"]
    if goal:
        lines += ['', 'GOAL MATCH', 'Your goal: ' + goal]
        relevance = record.get('relevance', {})
        if isinstance(relevance, dict):
            matched = relevance.get('matched_expertise', []) + relevance.get('matched_goals', [])
            lines += ['Recorded topic overlap: ' + (', '.join(map(str, matched)) if matched else 'No explicit overlap found.')]
            if relevance.get('unmatched_terms'):
                lines += ['Not supported by recorded topics: ' + ', '.join(relevance['unmatched_terms'])]
        else:
            lines += ['Relevance reflects recorded topic overlap only.']
    lines += ['', 'MEETING / OUTREACH',
              'Use a recorded public professional route or a route they authorized. Ask if a short conversation '
              'would be welcome; offer a specific contribution. Agree a meeting time and place together.',
              'Expertise and source claims are owner-entered until you review the evidence. '
              'Willingness, availability, personal traits, and a friendship outcome are unknown.']
    if record.get('notes'):
        lines += ['', 'YOUR NOTES', record['notes']]
    if record.get('follow_up'):
        lines += ['', 'Next follow-up: ' + record['follow_up']]
    return '\n'.join(lines)


class NetworkingWindow(tk.Toplevel):
    """Responsive, account-encrypted contact workspace backed by NetworkingBook."""

    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette, self.book = gui, type(gui), gui.runtime.networking
        p = self.palette
        self.title('HAYAT · Private networking')
        self.configure(bg=p.BLACK)
        self.minsize(650, 580)
        self.transient(gui.root)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        self.bind('<Destroy>', self._destroyed, add='+')
        self._closed, self._pending = False, False
        self._results: queue.Queue = queue.Queue()
        self._poll_job = None
        self._layout_job = None
        self._refresh_after = False
        self._selecting = False
        self._rows: dict[str, dict] = {}
        self.current_id = ''
        self.current: dict = {}
        self.channels: list[dict] = []
        self._loaded: dict = {}
        self.draft: dict = {}
        self.query, self.goal = StringVar(self), StringVar(self)
        self.sort = StringVar(self, SORTS[0])
        self.due_only, self.include_revoked = BooleanVar(self), BooleanVar(self)
        self.status = StringVar(self, 'Local contacts and unsent drafts. No search or outreach starts automatically.')
        self.fields = {key: StringVar(self) for key in
                       ('name', 'role', 'organization', 'expertise', 'goals', 'source_url', 'follow_up')}
        self._build()
        self._loaded = self._snapshot()
        self.bind('<Configure>', self._schedule_fit, add='+')
        place_over(self, gui.root, (1120, 800))
        self.deiconify()
        self.lift()
        self._poll_job = self.after(100, self._poll)
        self.refresh()

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=14, pady=10)
        header.pack(fill='x')
        tk.Label(header, text='Private networking', bg=p.BLACK, fg=p.WHITE,
                 font=('Segoe UI Semibold', 17)).pack(side='left')
        ttk.Button(header, text='Close', command=self.close).pack(side='right')
        self.intro = tk.Label(self, text='Your private professional contact book. Record sources and permission, '
                              'find shared work, and prepare an introduction for your review.',
                              bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=1000)
        self.intro.pack(fill='x', padx=14)
        actions = FlowRow(self, p.BLACK, gap=5, height=36)
        actions.pack(fill='x', padx=14, pady=6)
        self.buttons = {}
        for key, label, command in (
            ('new', 'New contact', self.new_contact), ('save', 'Save contact', self.save_contact),
            ('delete', 'Delete', self.delete_contact), ('revoke', 'Revoke permission', self.revoke_contact),
            ('discover', 'Find professional contacts', self.discover_contacts),
            ('research', 'Research known name', self.research_contact), ('image', 'Image sources', self.image_research),
            ('export', 'Export all JSON', self.export),
            ('restore', 'Restore from export', self.restore_export),
        ):
            button = ttk.Button(actions, text=label, command=command,
                                style='HayatAccent.TButton' if key == 'save' else 'Hayat.TButton')
            actions.add(button)
            self.buttons[key] = button
        self.body = tk.PanedWindow(self, orient='horizontal', bg=p.BLACK, sashwidth=7, bd=0)
        self.body.pack(fill='both', expand=True, padx=14, pady=(0, 6))
        self.list_panel = tk.Frame(self.body, bg=p.BLACK)
        self.workspace = tk.Frame(self.body, bg=p.BLACK)
        self.body.add(self.list_panel, minsize=240, width=360)
        self.body.add(self.workspace, minsize=300)
        self._build_list()
        self.tabs = ttk.Notebook(self.workspace)
        self.tabs.pack(fill='both', expand=True)
        self.editor = ScrollableColumn(self.tabs, p, width=500)
        self.tabs.add(self.editor, text='Contact')
        self._build_editor(self.editor.body)
        evidence = tk.Frame(self.tabs, bg=p.BLACK)
        self.evidence = self._text(evidence)
        self.evidence.pack(fill='both', expand=True)
        self.tabs.add(evidence, text='Evidence & fit')
        self._set_text(self.evidence, evidence_text({}))
        self._build_outreach()
        self.editor.adopt(self.editor.body)
        self.status_label = tk.Label(self, textvariable=self.status, bg=p.PANEL, fg=p.GOLD_SOFT,
                                     anchor='w', justify='left', wraplength=1000, padx=10, pady=7)
        self.status_label.pack(fill='x', padx=14, pady=(0, 10))
        self.progress = ttk.Progressbar(self, mode='indeterminate')

    def _build_list(self) -> None:
        parent, p = self.list_panel, self.palette
        self._field(parent, 'Search saved contacts', self.query)
        self.query_entry.bind('<Return>', lambda _event: self.refresh())
        self._field(parent, 'Your work / learning goal', self.goal)
        self.goal_entry.bind('<Return>', lambda _event: self.refresh())
        row = FlowRow(parent, p.BLACK, gap=4, height=34)
        row.pack(fill='x', pady=4)
        self.refresh_button = ttk.Button(row, text='Find / refresh', command=self.refresh)
        row.add(self.refresh_button)
        order = ttk.Combobox(row, textvariable=self.sort, values=SORTS, state='readonly', width=16)
        row.add(order)
        order.bind('<<ComboboxSelected>>', lambda _event: self._paint_rows())
        row.add(ttk.Checkbutton(row, text='Due follow-ups', variable=self.due_only, command=self._paint_rows))
        row.add(ttk.Checkbutton(row, text='Revoked summaries', variable=self.include_revoked, command=self.refresh))
        frame = tk.Frame(parent, bg=p.BLACK)
        frame.pack(fill='both', expand=True)
        self.contacts = ttk.Treeview(frame, columns=('organization', 'follow_up'), show='tree headings',
                                     selectmode='browse', height=5)
        for key, title, width in (('#0', 'Contact', 180), ('organization', 'Organization', 150),
                                  ('follow_up', 'Follow-up', 100)):
            self.contacts.heading(key, text=title)
            self.contacts.column(key, width=width, minwidth=90, stretch=key == '#0')
        self._table_scrollbars(frame, self.contacts)
        self.contacts.bind('<<TreeviewSelect>>', self.select_contact)
        self.list_note = tk.Label(parent, text='No contacts loaded.', bg=p.BLACK, fg=p.MUTED, anchor='w')
        self.list_note.pack(fill='x', pady=4)

    def _field(self, parent: tk.Misc, label: str, variable: StringVar) -> ttk.Entry:
        p = self.palette
        tk.Label(parent, text=label, bg=p.BLACK, fg=p.MUTED, anchor='w').pack(fill='x', pady=(5, 2))
        entry = ttk.Entry(parent, textvariable=variable)
        entry.pack(fill='x')
        if variable is self.query:
            self.query_entry = entry
        elif variable is self.goal:
            self.goal_entry = entry
        return entry

    def _build_editor(self, parent: tk.Misc) -> None:
        p = self.palette
        for key, label in (('name', 'Name *'), ('role', 'Professional role'), ('organization', 'Organization'),
                           ('expertise', 'Expertise / topics (comma separated)'), ('goals', 'Shared goals (comma separated)'),
                           ('source_url', 'Public professional source URL'), ('follow_up', 'Follow-up date (YYYY-MM-DD)')):
            self._field(parent, label, self.fields[key])
        tk.Label(parent, text='Your notes / what you can offer', bg=p.BLACK, fg=p.MUTED, anchor='w').pack(fill='x', pady=(7, 2))
        self.notes = self._text(parent, editable=True, height=4)
        self.notes.pack(fill='x')
        tk.Label(parent, text='Contact routes', bg=p.BLACK, fg=p.WHITE, anchor='w',
                 font=('Segoe UI Semibold', 10)).pack(fill='x', pady=(12, 2))
        self.channel_kind = StringVar(self, 'profile')
        self.channel_value, self.channel_source = StringVar(self), StringVar(self)
        self.channel_status = StringVar(self, next(iter(CHANNEL_STATUS)))
        self.channel_consent = BooleanVar(self, False)
        row = tk.Frame(parent, bg=p.BLACK)
        row.pack(fill='x', pady=2)
        ttk.Combobox(row, textvariable=self.channel_kind, values=('profile', 'website', 'email', 'phone'),
                     width=9, state='readonly').pack(side='left')
        ttk.Entry(row, textvariable=self.channel_value).pack(side='left', fill='x', expand=True, padx=(5, 0))
        ttk.Combobox(parent, textvariable=self.channel_status, values=tuple(CHANNEL_STATUS),
                     state='readonly', width=28).pack(fill='x', pady=3)
        self._field(parent, 'Route source URL (or use contact source above)', self.channel_source)
        ttk.Checkbutton(parent, text='Person authorized use of this contact route',
                        variable=self.channel_consent).pack(anchor='w', pady=4)
        row = tk.Frame(parent, bg=p.BLACK)
        row.pack(fill='x', pady=4)
        ttk.Button(row, text='Add route', command=self.add_channel).pack(side='left')
        ttk.Button(row, text='Remove selected route', command=self.remove_channel).pack(side='left', padx=5)
        routes = tk.Frame(parent, bg=p.BLACK)
        routes.pack(fill='x')
        self.route_table = ttk.Treeview(routes, columns=('kind', 'status'), show='tree headings', height=4)
        for key, label, width in (('#0', 'Contact route', 280), ('kind', 'Kind', 70), ('status', 'Evidence', 150)):
            self.route_table.heading(key, text=label)
            self.route_table.column(key, width=width, minwidth=65)
        self._table_scrollbars(routes, self.route_table)
        self._paint_channels()
        tk.Label(parent, text='Public routes require a source. Permission must be explicit. '
                 'Owner-provided routes stay marked unverified.', bg=p.BLACK, fg=p.MUTED,
                 anchor='w', justify='left', wraplength=460).pack(fill='x', pady=8)

    def _build_outreach(self) -> None:
        p = self.palette
        page = tk.Frame(self.tabs, bg=p.BLACK)
        self.tabs.add(page, text='Outreach draft')
        self.purpose, self.sender, self.shared_context = StringVar(self), StringVar(self), StringVar(self)
        for label, value in (('What do you want to discuss? *', self.purpose), ('Your name (optional)', self.sender),
                             ('A real shared context (optional)', self.shared_context)):
            self._field(page, label, value)
        actions = FlowRow(page, p.BLACK, gap=5, height=36)
        actions.pack(fill='x', pady=6)
        self.draft_button = ttk.Button(actions, text='Prepare unsent draft', command=self.prepare_draft)
        actions.add(self.draft_button)
        actions.add(ttk.Button(actions, text='Copy draft', command=self.copy_draft))
        actions.add(ttk.Button(actions, text='Copy contact route', command=self.copy_route))
        self.draft_text = self._text(page, height=8)
        self.draft_text.pack(fill='both', expand=True)
        self._set_text(self.draft_text, 'Select a saved contact and describe your purpose. Review and send the draft yourself in your chosen app.')

    def _text(self, parent: tk.Misc, editable: bool = False, **options: Any) -> DarkScrolledText:
        p = self.palette
        return DarkScrolledText(parent, bg=p.PANEL, fg=p.WHITE, insertbackground=p.GOLD, wrap='word',
                                font=('Segoe UI', 10), state='normal' if editable else 'disabled', **options)

    @staticmethod
    def _table_scrollbars(parent: tk.Misc, table: ttk.Treeview) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        table.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(parent, orient='vertical', command=table.yview)
        horizontal = ttk.Scrollbar(parent, orient='horizontal', command=table.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

    @staticmethod
    def _set_text(box: tk.Text, text: str) -> None:
        if box.get('1.0', 'end-1c') == text:
            return
        scroller = getattr(box, '_smooth_scroller', None)
        if scroller:
            scroller.cancel()
        box.configure(state='normal')
        box.delete('1.0', 'end')
        box.insert('1.0', text)
        box.configure(state='disabled')

    def _ticket(self) -> tuple[Any, Any]:
        interlock = getattr(getattr(self.gui.runtime, 'registry', None), 'interlock', None)
        return (interlock, interlock.ticket()) if interlock else (None, None)

    def _run(self, title: str, operation: Callable, complete: Callable) -> bool:
        if self._closed or self._pending:
            self.status.set('Wait for the current contact operation to finish.')
            return False
        try:
            interlock, ticket = self._ticket()
        except Exception as exc:
            self.status.set(str(exc))
            return False
        self._pending = True
        self.status.set(title)
        self.progress.pack(fill='x', padx=14, before=self.status_label)
        self.progress.start(15)
        for button in self.buttons.values():
            button.configure(state='disabled')
        self.draft_button.configure(state='disabled')
        def work() -> None:
            try:
                if interlock:
                    interlock.check(ticket)
                value = operation()
                if interlock:
                    interlock.check(ticket)
                self._results.put((complete, value, None))
            except Exception as exc:
                self._results.put((complete, None, str(exc)))
        threading.Thread(target=work, name='hayat-networking', daemon=True).start()
        return True

    def _poll(self) -> None:
        if self._closed:
            return
        for _ in range(5):
            try:
                complete, value, error = self._results.get_nowait()
            except queue.Empty:
                break
            self._pending = False
            self.progress.stop()
            self.progress.pack_forget()
            for button in self.buttons.values():
                button.configure(state='normal')
            self.draft_button.configure(state='normal')
            if error:
                self.status.set(error[:500])
            else:
                complete(value)
            if self._refresh_after:
                self._refresh_after = False
                self.refresh()
        self._poll_job = self.after(100, self._poll)

    def refresh(self, note: str = '') -> None:
        """Reload the list; `note` (what just happened) leads the status instead of being overwritten."""
        if self._pending:
            self._refresh_after = True
            return
        query, goal, revoked = self.query.get().strip(), self.goal.get().strip(), self.include_revoked.get()
        def loaded(rows: list[dict]) -> None:
            self._rows = {str(row['id']): row for row in rows}
            self._paint_rows()
            self.status.set((note + ' ' if note else '')
                            + (f'{len(rows)} saved contacts found. Goal matches use recorded topics only.' if goal
                               else f'{len(rows)} saved contacts found. Select one to review or prepare an introduction.'))
        self._run('Reading your local contact book…', lambda: self.book.all(query=query, goal=goal, include_revoked=revoked), loaded)

    def _paint_rows(self) -> None:
        rows = list(self._rows.values())
        if self.due_only.get():
            today = date.today().isoformat()
            rows = [row for row in rows if row.get('follow_up') and row['follow_up'] <= today]
        if self.sort.get() == 'Next follow-up':
            rows.sort(key=lambda row: (row.get('follow_up') or '9999-12-31', row.get('name', '').casefold()))
        elif self.sort.get() == 'Name':
            rows.sort(key=lambda row: row.get('name', '').casefold())
        # Goal relevance preserves the backend's evidence-based rank.
        position = self.contacts.yview()
        self._selecting = True
        self.contacts.delete(*self.contacts.get_children())
        for row in rows:
            name = row.get('name', '') + (' [revoked]' if row.get('revoked') or row.get('revoked_at') else '')
            self.contacts.insert('', 'end', iid=str(row['id']), text=name,
                                 values=(row.get('organization', ''), row.get('follow_up', '')))
        filtered = bool(self._rows) or self.query.get().strip() or self.goal.get().strip()
        show_empty(self.contacts, 'No saved contact matches these filters or due dates.' if filtered else
                   'Your private contact book is empty.\nUse New contact, or Find professional contacts '
                   'to review public sources before saving one.', self.palette)
        if self.current_id in self.contacts.get_children():
            self.contacts.selection_set(self.current_id)
            self.contacts.focus(self.current_id)
            if not self._dirty():
                updated = self._rows[self.current_id]
                old_facts = {key: value for key, value in self.current.items() if key != 'relevance'}
                new_facts = {key: value for key, value in updated.items() if key != 'relevance'}
                if old_facts != new_facts:
                    self._load(updated)
                else:
                    self.current = dict(updated)
                    self._set_text(self.evidence, evidence_text(updated, self.goal.get().strip()))
        if position:
            self.contacts.yview_moveto(position[0])
        self._selecting = False
        self.list_note.configure(text=f'{len(rows)} shown · {len(self._rows)} matching saved contacts')

    def _snapshot(self) -> dict:
        return {**{key: value.get().strip() for key, value in self.fields.items()},
                'notes': self.notes.get('1.0', 'end-1c').strip(), 'channels': [dict(channel) for channel in self.channels]}

    def _dirty(self) -> bool:
        return self._snapshot() != self._loaded or bool(self.channel_value.get().strip())

    def _discard_allowed(self) -> bool:
        return not self._dirty() or messagebox.askyesno('Unsaved contact changes',
            'Discard your unsaved edits to this contact?', parent=self)

    def select_contact(self, _event: Any = None) -> None:
        if self._selecting or self._pending:
            return
        selection = self.contacts.selection()
        if not selection or selection[0] == self.current_id:
            return
        if not self._discard_allowed():
            self._selecting = True
            self.contacts.selection_set(self.current_id) if self.current_id else self.contacts.selection_remove(*selection)
            self._selecting = False
            return
        self._load(self._rows[selection[0]])

    def _load(self, record: dict) -> None:
        self.current, self.current_id = dict(record), str(record.get('id', ''))
        for key, variable in self.fields.items():
            value = record.get(key, '')
            variable.set(', '.join(value) if isinstance(value, list) else str(value or ''))
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', record.get('notes', ''))
        self.channels = [dict(channel) for channel in record.get('channels', [])]
        self.channel_value.set('')
        self.channel_source.set('')
        self.channel_consent.set(False)
        self._paint_channels()
        self._loaded = self._snapshot()
        self.draft = {}
        self._set_text(self.draft_text, 'Prepare a new unsent draft for this contact. No message has been sent.')
        self._set_text(self.evidence, evidence_text(record, self.goal.get().strip()))

    def new_contact(self) -> bool:
        if self._pending or not self._discard_allowed():
            return False
        self._selecting = True
        self.contacts.selection_remove(*self.contacts.selection())
        self._load({})
        self._selecting = False
        self.tabs.select(0)
        self.editor.canvas.yview_moveto(0)
        self.status.set('Add a name, recorded expertise and source. Contact routes are optional.')
        return True

    def add_channel(self) -> bool:
        value = self.channel_value.get().strip()
        if not value:
            self.status.set('Enter a contact route first.')
            return False
        status = CHANNEL_STATUS[self.channel_status.get()]
        source = self.channel_source.get().strip() or self.fields['source_url'].get().strip()
        if status == 'public_professional' and not source:
            self.status.set('A public professional route needs its public source URL.')
            return False
        if status == 'consent' and not self.channel_consent.get():
            self.status.set('Confirm the person explicitly authorized this contact route, or choose owner provided.')
            return False
        channel = {'kind': self.channel_kind.get(), 'value': value, 'status': status, 'source_url': source}
        if status == 'consent':
            channel['consent'] = True
        if any(row['kind'] == channel['kind'] and row['value'].casefold() == value.casefold() for row in self.channels):
            self.status.set('That route is already listed. Remove it first to change its evidence status.')
            return False
        self.channels.append(channel)
        self._paint_channels()
        self.channel_value.set('')
        self.channel_consent.set(False)
        self.status.set('Route added to the editor. Save contact to keep it.')
        return True

    def remove_channel(self) -> bool:
        selection = self.route_table.selection()
        if not selection:
            self.status.set('Select a route to remove.')
            return False
        self.channels.pop(int(selection[0]))
        self._paint_channels()
        return True

    def _paint_channels(self) -> None:
        self.route_table.delete(*self.route_table.get_children())
        for index, channel in enumerate(self.channels):
            self.route_table.insert('', 'end', iid=str(index), text=channel['value'],
                                    values=(channel['kind'], channel.get('status', 'provided')))
        show_empty(self.route_table, 'No contact routes yet. Add one with Add route.', self.palette)

    def save_contact(self) -> bool:
        payload, contact_id = self._snapshot(), self.current_id
        if not payload['name']:
            self.status.set('A contact name is required.')
            return False
        if self.current.get('revoked') or self.current.get('revoked_at'):
            self.status.set('Permission was revoked. Delete the summary or create a fresh contact with new permission.')
            return False
        if self.channel_value.get().strip():
            self.status.set('Add or clear the contact route currently in the route editor before saving.')
            return False
        def saved(record: dict) -> None:
            self._load(record)
            self._refresh_after = True
            self.status.set('Contact saved locally with account encryption.')
        return self._run('Saving contact…', lambda: self.book.save(**payload, contact_id=contact_id), saved)

    def delete_contact(self) -> bool:
        contact_id = self.current_id
        if not contact_id:
            self.status.set('Select a saved contact first.')
            return False
        if not messagebox.askyesno('Delete contact', 'Permanently delete this local contact and its saved routes?', parent=self):
            return False
        def deleted(_value: Any) -> None:
            self._load({})
            self._refresh_after = True
        return self._run('Deleting local contact…', lambda: self.book.delete(contact_id), deleted)

    def revoke_contact(self) -> bool:
        contact_id = self.current_id
        if not contact_id:
            self.status.set('Select a saved contact first.')
            return False
        if not messagebox.askyesno('Revoke permission', 'Withhold this contact and its saved routes from reading, drafts and export? '
                                  'Re-entering a contact later requires new permission.', parent=self):
            return False
        def revoked(_value: Any) -> None:
            self._load({})
            self._refresh_after = True
            self.status.set('Contact permission revoked. Routes are no longer available for drafts or export.')
        return self._run('Revoking contact permission…', lambda: self.book.revoke(contact_id), revoked)

    def prepare_draft(self) -> bool:
        contact_id, purpose = self.current_id, self.purpose.get().strip()
        if not contact_id or not purpose:
            self.status.set('Select a saved contact and enter your discussion purpose first.')
            return False
        if self._dirty():
            self.status.set('Save your contact edits before preparing outreach from them.')
            return False
        sender, context = self.sender.get().strip(), self.shared_context.get().strip()
        def ready(draft: dict) -> None:
            self.draft = draft
            self._set_text(self.draft_text, f"Subject: {draft.get('subject', '')}\n\n{draft.get('body', '')}\n\nUnsent draft. Review facts and contact permission before sending.")
            self.status.set('Draft prepared locally. Nothing has been sent.')
        return self._run('Preparing an unsent introduction…',
                         lambda: self.book.draft(contact_id, purpose, sender=sender, shared_context=context), ready)

    def copy_draft(self) -> bool:
        if not self.draft:
            self.status.set('Prepare a draft first.')
            return False
        contact_id, draft = self.current_id, dict(self.draft)
        def copied(_record: dict) -> None:
            self.clipboard_clear()
            self.clipboard_append(f"Subject: {draft.get('subject', '')}\n\n{draft.get('body', '')}")
            self.status.set('Draft copied to your system clipboard. It has not been sent.')
        return self._run('Checking contact permission…', lambda: self.book.get(contact_id), copied)

    def copy_route(self) -> bool:
        if self.current.get('revoked') or self.current.get('revoked_at') or not self.current_id:
            self.status.set('Select an active saved contact first.')
            return False
        selection = self.route_table.selection()
        channels = self.current.get('channels', [])
        if self._dirty():
            self.status.set('Save contact edits before copying a contact route.')
            return False
        if not selection and len(channels) != 1:
            self.status.set('Select a route in the Contact tab first.')
            return False
        if not channels:
            self.status.set('No contact routes recorded.')
            return False
        channel = channels[int(selection[0])] if selection else channels[0]
        contact_id, selected_value = self.current_id, channel['value']
        def copied(record: dict) -> None:
            if not any(route['value'] == selected_value for route in record.get('channels', [])):
                self.status.set('This contact route changed. Refresh and review its current permission first.')
                return
            self.clipboard_clear()
            self.clipboard_append(selected_value)
            self.status.set('Contact route copied. Review its evidence and permission before using it.')
        return self._run('Checking contact permission…', lambda: self.book.get(contact_id), copied)

    def export(self) -> bool:
        plaintext = messagebox.askyesno('Export format',
            'Export a readable plaintext JSON file? It will contain contact details.\n\n'
            'Choose No for an account-encrypted JSON backup, which is readable only through this Windows account.', parent=self)
        path = filedialog.asksaveasfilename(parent=self, title='Export private contact book', defaultextension='.json',
                                           initialfile='hayat-contacts-readable.json' if plaintext else 'hayat-contacts-encrypted.json',
                                           filetypes=[('JSON', '*.json')])
        if not path:
            return False
        if plaintext and not messagebox.askyesno('Plaintext contact details',
            'This file will contain readable contact details. Save it only somewhere you trust. Continue?', parent=self):
            return False
        try:
            interlock, ticket = self._ticket()
        except Exception as exc:
            self.status.set(str(exc))
            return False
        def write() -> str:
            data = self.book.export(plaintext=plaintext)
            target = Path(path)
            # Atomic replacement avoids a partially written contact export.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                                 prefix=target.name + '.', suffix='.tmp', delete=False) as stream:
                    temporary = stream.name
                    json.dump(data, stream, ensure_ascii=False, indent=2)
                    stream.write('\n')
                if interlock:
                    interlock.check(ticket)
                os.replace(temporary, target)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            return str(target)
        return self._run('Exporting local contact book…', write,
                         lambda value: self.status.set(('Readable' if plaintext else 'Account-encrypted') + ' JSON saved: ' + value))

    def restore_export(self) -> bool:
        """Add contacts from an export file; saved contacts are never changed (NetworkingBook.restore)."""
        path = filedialog.askopenfilename(parent=self, title='Restore contacts from a HAYAT export',
                                          filetypes=[('JSON', '*.json')])
        if not path:
            return False
        from app.tools.networking import restore_file

        def restored(result: dict) -> None:
            skipped, rejected = result.get('skipped_existing') or [], result.get('rejected') or []
            self.refresh(f"Restored {result.get('restored', 0)} contact(s)."
                         + (f" {len(skipped)} already saved, left unchanged." if skipped else '')
                         + (f" {len(rejected)} not restored: " + '; '.join(rejected[:3]) + '.' if rejected else '')
                         + ' Nothing was sent.')
        return self._run('Restoring contacts from the export…', lambda: restore_file(self.book, path), restored)

    def research_contact(self) -> bool:
        name = self.fields['name'].get().strip()
        if not name:
            self.status.set('Enter a known name before opening professional research.')
            return False

        try:
            self._ticket()
            self.gui._open_searches()
            research = self.gui._research_studio
            research.open_person_studio()
            panel = research._person_studio
            panel.query.delete(0, 'end')
            panel.query.insert(0, name)
            panel.context.set(' '.join(self.fields[key].get().strip() for key in ('role', 'organization')).strip())
            profile = next((row['value'] for row in self.channels if row['kind'] == 'profile'), '')
            panel.profile.set(profile)
            panel.lift()
            self.status.set('Professional research opened with this known name. Review the input and press Research there.')
            return True
        except Exception as exc:
            self.status.set('Could not open professional research: ' + str(exc)[:300])
            return False

    def discover_contacts(self) -> bool:
        return self.find_professional_contacts()

    def find_professional_contacts(self, *, name: str = '', organization: str = '', official_url: str = '') -> bool:
        try:
            self._ticket()
            from app.gui_contact_discovery import ContactDiscoveryWindow
            window = getattr(self, '_contact_discovery', None)
            if window is not None and window.winfo_exists():
                window.deiconify()
                window.lift()
            else:
                window = self._contact_discovery = ContactDiscoveryWindow(self)
            if name or organization or official_url:
                if window.busy:
                    window.status.set('Cancel the current discovery before changing its known name and context.')
                else:
                    window.name.set(name)
                    window.organization.set(organization)
                    window.official_url.set(official_url)
            self.status.set('Professional contact discovery opened. Review the known name and context, then press Find there.')
            return True
        except Exception as exc:
            self.status.set('Could not open professional contact discovery: ' + str(exc)[:300])
            return False

    def image_research(self) -> bool:
        try:
            self._ticket()
            self.gui._research_image()
            self.status.set('Image research opened for image sources, metadata and context. Unknown faces are not identified.')
            return True
        except Exception as exc:
            self.status.set('Could not open image research: ' + str(exc)[:300])
            return False

    def _schedule_fit(self, event: Any) -> None:
        if event.widget is self and self._layout_job is None:
            self._layout_job = self.after_idle(self._fit)

    def _fit(self) -> None:
        self._layout_job = None
        if self._closed:
            return
        # A stacked list needs room for its search controls AND actual rows.
        # Short laptop windows keep two scrollable columns rather than reduce
        # the contact table to a zero-height strip.
        stacked = self.winfo_width() < 900 and self.winfo_height() >= 900
        orientation = 'vertical' if stacked else 'horizontal'
        if str(self.body.cget('orient')) != orientation:
            self.body.configure(orient=orientation)
            self.body.paneconfigure(self.list_panel, minsize=310 if stacked else 240)
            self.body.paneconfigure(self.workspace, minsize=260 if stacked else 300)
            self.body.sash_place(0, 0 if stacked else 300, 340 if stacked else 0)
        self.intro.configure(wraplength=max(300, self.winfo_width() - 40))
        self.status_label.configure(wraplength=max(300, self.winfo_width() - 50))

    def close(self) -> bool:
        if not self._discard_allowed():
            return False
        self.destroy()
        return True

    def _destroyed(self, event: Any) -> None:
        if event.widget is not self:
            return
        self._closed = True
        for handle in (self._poll_job, self._layout_job):
            if handle:
                self.after_cancel(handle)
        # Children are destroyed before this handler runs on a parent close; stopping
        # an already-destroyed bar raised a TclError on every window close.
        if self.progress.winfo_exists():
            self.progress.stop()
