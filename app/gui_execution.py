"""Immediate owner controls and non-destructive conversation branching."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from app.gui_empty import show_empty
from app.gui_windows import place_over
from app.gui_theme import DarkScrolledText


class ExecutionControlsMixin:
    def _emergency_stop(self):
        # Blocking admission is immediate, before any worker teardown.
        self._emergency_stopping = True
        self.runtime.registry.interlock.halt()
        self._cancel_reply()
        self.emergency_button.configure(text='STOPPED', state='disabled')
        self.resume_tools_button.configure(state='normal')
        self.resume_tools_button.pack(side='right', padx=(6, 0), before=self._transparency_words)
        self._append('system', 'Emergency stop active. Tools are blocked until Resume tools. '
                     'Calls already in progress may finish; completed actions are not undone.')
        def stop():
            from app.emergency import stop_runtime
            report = stop_runtime(self.runtime)
            self._post(setattr, self, '_emergency_stopping', False)
            if report['errors']:
                self._post(self._append, 'error', 'Some workers could not stop: ' + '; '.join(report['errors']))
        threading.Thread(target=stop, name='hayat-emergency-stop', daemon=True).start()

    def _resume_tools(self):
        if getattr(self, '_emergency_stopping', False) or getattr(self, '_working_since', None) is not None or self.runtime.turns.current() is not None:
            self.draft_status.set('Wait for the stopped turn to finish before resuming.')
            return
        self.runtime.registry.interlock.resume()
        self.emergency_button.configure(text='Emergency stop', state='normal')
        self.resume_tools_button.configure(state='disabled')
        self.resume_tools_button.pack_forget()
        self._append('system', 'Tools resumed. Background services remain stopped; start them manually when needed.')

    def _stop_and_correct(self):
        self._cancel_reply()
        self.runtime.agent.cancel_current()
        voice = getattr(self.runtime, 'voice', None)
        if voice:
            voice.tts.stop()
            voice.approvals.cancel()
        for key in ('pending_action', 'pending_offer', 'pending_install'):
            setattr(self.runtime.session, key, None)
        self.runtime.registry.approvals.revoke_all()
        lifecycle = getattr(self.runtime.authority, 'approvals_lifecycle', None)
        if lifecycle and self.runtime.session.active_turn_id:
            lifecycle.cancel_for_turn(self.runtime.session.active_turn_id, reason='owner stopped to correct the request')
        self.input.delete('1.0', 'end')
        text = (getattr(self, '_last_sent_text', self.runtime.session.last_user_input)
                if self._working_since is not None else self.runtime.session.last_user_input)
        self.input.insert('1.0', text)
        self.input.focus_set()
        self.draft_status.set('Edit your request here. Send it after the current reply stops.')

    def _open_chat_rewind(self):
        existing = getattr(self, '_rewind_window', None)
        if existing and existing.winfo_exists():
            existing.lift()
            return
        self._rewind_window = ChatRewind(self)


class ChatRewind(tk.Toplevel):
    def __init__(self, gui):
        super().__init__(gui.root)
        self.gui = gui
        self.configure(bg=gui.BLACK)
        self.title('HAYAT · Continue from earlier')
        self.minsize(540, 400)
        self.transient(gui.root)
        self.bind('<Escape>', lambda _e: self.destroy())
        tk.Label(self, text='Choose a message. The original conversation is kept; later messages are excluded '
                 'from the new conversation. Saved long-term knowledge remains available.',
                 bg=gui.BLACK, fg=gui.WHITE, wraplength=620, justify='left').pack(fill='x', padx=12, pady=12)
        self.tree = ttk.Treeview(self, columns=('role', 'text'), show='headings', height=7)
        self.tree.heading('role', text='Speaker')
        self.tree.heading('text', text='Message')
        self.tree.column('role', width=75, stretch=False)
        self.tree.column('text', width=500)
        self.tree.pack(fill='both', expand=True, padx=12)
        self.rows = gui.runtime.memory.conversation_history(gui.runtime.conversation.session_id, 2000)
        for row in self.rows:
            self.tree.insert('', 'end', iid=str(row['id']), values=(row['role'], ' '.join(row['content'].split())[:220]))
        show_empty(self.tree, 'This conversation has no messages yet, so there is nothing to continue from.', gui)
        self.preview = DarkScrolledText(self, height=5, wrap='word', bg=gui.PANEL, fg=gui.WHITE, state='disabled')
        self.preview.pack(fill='both', expand=True, padx=12, pady=8)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        bar = tk.Frame(self, bg=gui.BLACK)
        bar.pack(fill='x', padx=12, pady=8)
        self.continue_button = ttk.Button(bar, text='Continue after selected', command=lambda: self.branch(False))
        self.continue_button.pack(side='left')
        self.edit_button = ttk.Button(bar, text='Edit selected request', command=lambda: self.branch(True))
        self.edit_button.pack(side='left', padx=8)
        self.status = tk.Label(self, text='Select a message to preview it.', bg=gui.BLACK, fg=gui.GOLD_SOFT,
                               wraplength=620, justify='left')
        self.status.pack(fill='x', padx=12, pady=(0, 12))
        self.select()
        place_over(self, gui.root, (720, 600))

    def selected(self):
        ids = self.tree.selection()
        return next((row for row in self.rows if ids and str(row['id']) == ids[0]), None)

    def select(self, _event=None):
        row = self.selected()
        self.continue_button.configure(state='normal' if row else 'disabled')
        self.edit_button.configure(state='normal' if row and row['role'] == 'user' else 'disabled')
        self.preview.configure(state='normal')
        self.preview.delete('1.0', 'end')
        if row:
            self.preview.insert('end', row['content'])
        self.preview.configure(state='disabled')

    def branch(self, edit):
        row = self.selected()
        if not row:
            return
        if self.gui._working_since is not None:
            self.status.configure(text='Stop the running reply and wait for it to finish first.')
            return
        try:
            from app.emergency import branch_conversation
            result = branch_conversation(self.gui.runtime, row['id'], edit=edit)
        except ValueError as error:
            self.status.configure(text=str(error))
            return
        # Do not call clear_chat: that deliberately summarizes the old tail.
        gui = self.gui
        gui.chat_details.reset()
        for card in gui._evidence_cards:
            card.destroy()
        gui._evidence_cards.clear()
        for card in gui._video_cards.values():
            card.close()
            card.destroy()
        gui._video_cards.clear()
        for card in gui._video_link_cards.values():
            card.close()
        gui._video_link_cards.clear()
        gui._inline_images.clear()
        gui._transcript.seek(0)
        gui._transcript.truncate()
        gui.chat.configure(state='normal')
        gui.chat.delete('1.0', 'end')
        gui.chat.configure(state='disabled')
        for message in gui.runtime.memory.conversation_history(result['session_id'], 2000):
            gui._append(message['role'], ('You' if message['role'] == 'user' else 'HAYAT') + '\n' + message['content'])
        gui._append('system', 'Started a branch from the selected message. Original history retained.')
        gui.input.delete('1.0', 'end')
        gui.input.insert('1.0', result['draft'])
        gui.input.focus_set()
        self.destroy()
