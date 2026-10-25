"""Global Intelligence -> Map options -> Saved views.

Named views keep the filters and the sources that backed them when saved.
Opening one re-checks the same filters against today's cache, applies them to
the map and shows what changed. Tools run on a worker thread through the
declared tool path, so each action is recorded in Activity; closing the dialog
drops a result still on its way.
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over


def describe_view(payload):
    """Readable evidence for one view: as saved, and as checked now."""
    view = payload['view']
    saved = view['evidence']
    lines = [view['name'], f"Saved {saved['checked_at'][:16].replace('T', ' ')} UTC: {saved['headline']}"]
    current = payload.get('now')
    if current:
        lines.append(f"Checked now {current['checked_at'][:16].replace('T', ' ')} UTC: {current['headline']}")
        lines += ['', 'What changed since it was saved:'] + [f'- {change}' for change in payload['changes']]
    rows = (current or saved)['sources']
    lines += ['', 'Sources behind this view' + (' now:' if current else ':')]
    lines += [f"- {row['feed']}: {row['items']} items, last success {row['last_success_at'] or 'never'}"
              + (' (stale or unavailable)' if row['stale'] else '') for row in rows] or ['- No feed has matching items.']
    options = view['options']
    lines += ['', 'Filters: ' + ', '.join(f'{key} = {options[key]!r}' for key in
                                          ('region', 'query', 'category', 'min_severity', 'time_range', 'layer')
                                          if key in options)]
    return '\n'.join(lines)


class SavedViewsWindow(tk.Toplevel):
    def __init__(self, intelligence):
        super().__init__(intelligence)
        self.withdraw()
        self.intelligence = intelligence
        p = intelligence.palette
        self.title('HAYAT · Saved intelligence views')
        self.configure(bg=p.BLACK)
        self.minsize(560, 420)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: (self.close(), 'break')[1])
        self._results, self._closed, self._timer, self._busy = queue.Queue(), False, None, False
        tk.Label(self, text='Saved views', bg=p.BLACK, fg=p.WHITE, anchor='w',
                 font=('Segoe UI Semibold', 13)).pack(fill='x', padx=16, pady=(12, 2))
        tk.Label(self, text='Each view keeps its filters and the sources behind it. Opening one re-checks the '
                            'same filters now; old counts are never shown as current.',
                 bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=640).pack(fill='x', padx=16)
        save = tk.Frame(self, bg=p.BLACK, padx=16, pady=8)
        save.pack(fill='x')
        self.name = StringVar(self)
        ttk.Label(save, text='Name').pack(side='left')
        entry = ttk.Entry(save, textvariable=self.name)
        entry.pack(side='left', fill='x', expand=True, padx=8)
        entry.bind('<Return>', lambda _event: self.save())
        self.save_button = ttk.Button(save, text='Save current view', command=self.save)
        self.save_button.pack(side='right')
        middle = tk.Frame(self, bg=p.BLACK, padx=16)
        middle.pack(fill='both', expand=True)
        self.listing = tk.Listbox(middle, height=8, bg=p.PANEL, fg=p.WHITE, selectbackground=p.GOLD,
                                  selectforeground=p.BLACK, highlightthickness=0, relief='flat', exportselection=False)
        self.listing.pack(side='left', fill='y')
        self.listing.bind('<Double-Button-1>', lambda _event: self.open())
        self.listing.bind('<Return>', lambda _event: self.open())
        self.body = DarkScrolledText(middle, wrap='word', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 10), height=14)
        self.body.pack(side='left', fill='both', expand=True, padx=(8, 0))
        actions = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        actions.pack(fill='x')
        self.open_button = ttk.Button(actions, text='Open on the map', command=self.open)
        self.open_button.pack(side='left')
        self.delete_button = ttk.Button(actions, text='Delete', command=self.delete)
        self.delete_button.pack(side='left', padx=6)
        self.status = StringVar(self, 'Loading saved views...')
        ttk.Label(actions, textvariable=self.status).pack(side='left', padx=8)
        self.views = []
        place_over(self, intelligence.winfo_toplevel(), (760, 560))
        self.deiconify()
        self._call({'action': 'list'}, self._listed)
        self._poll()

    # ----- tool calls ------------------------------------------------------
    def _call(self, arguments, done):
        if self._busy:
            self.status.set('Still working on the last request.')
            return False
        self._busy = True

        def run():
            from app.gui_features import run_declared_tool
            try:
                result = run_declared_tool(self.intelligence.gui.runtime, 'intelligence_views', arguments)
                payload = result.output if result.success else {'error': str(result.error)}
            except Exception as exc:
                payload = {'error': f'{type(exc).__name__}: {exc}'}
            if not self._closed:
                self._results.put((done, payload))
        threading.Thread(target=run, daemon=True, name='hayat-intelligence-views').start()
        return True

    def _poll(self):
        try:
            done, payload = self._results.get_nowait()
        except queue.Empty:
            done = None
        if done is not None:
            self._busy = False
            if 'error' in payload:
                self.status.set(payload['error'])
            else:
                done(payload)
        if not self._closed:
            self._timer = self.after(60, self._poll)

    # ----- actions -----------------------------------------------------------
    def selected(self):
        chosen = self.listing.curselection()
        return self.views[chosen[0]]['name'] if chosen else ''

    def save(self):
        name = ' '.join(self.name.get().split())
        if not name:
            return self.status.set('Type a name for this view first.')
        self.status.set('Checking the sources behind this view...')
        self._call({'action': 'save', 'name': name, 'data': self.intelligence.view_options()},
                   lambda payload: (self._show(describe_view(payload)), self.status.set(f'Saved "{name}".'),
                                    self._call({'action': 'list'}, self._listed)))

    def open(self):
        name = self.selected()
        if not name:
            return self.status.set('Choose a saved view.')
        self.status.set(f'Re-checking "{name}" now...')
        self._call({'action': 'open', 'name': name}, self._opened)

    def delete(self):
        name = self.selected()
        if not name:
            return self.status.set('Choose a saved view.')
        self._call({'action': 'delete', 'name': name},
                   lambda payload: (self.status.set(f'Deleted "{name}".'), self._show(''),
                                    self._call({'action': 'list'}, self._listed)))

    # ----- painting ------------------------------------------------------------
    def _listed(self, payload):
        self.views = payload['views']
        self.listing.delete(0, 'end')
        for view in self.views:
            self.listing.insert('end', view['name'])
        if not self.views:
            self.status.set('No saved views yet. Set the filters on the map, name the view and save it.')
        elif not self.status.get().startswith(('Saved', 'Deleted')):
            self.status.set(f'{len(self.views)} saved views.')

    def _opened(self, payload):
        self.intelligence.apply_view_options(payload['view']['options'])
        self._show(describe_view(payload))
        self.status.set(f'Showing "{payload["view"]["name"]}" on the map.')

    def _show(self, text):
        self.body.configure(state='normal')
        self.body.delete('1.0', 'end')
        self.body.insert('1.0', text)
        self.body.configure(state='disabled')

    def close(self):
        self._closed = True
        if self._timer:
            self.after_cancel(self._timer)
        self.destroy()
