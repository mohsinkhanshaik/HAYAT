"""Visible, bounded runtime activity. Event callbacks never touch Tk."""
from collections import Counter, deque
import json
import re
import threading
import tkinter as tk
from tkinter import ttk

from app.events import EVENT_TYPES
from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.safety.sensitive import redact


VISIBLE_EVENTS = {name for name in EVENT_TYPES if name.startswith((
    'TOOL_', 'MODEL_', 'RESEARCH_', 'SERVICE_', 'AUTHORITY_', 'APPROVAL_',
    'PROACTIVE_', 'CONNECTOR_', 'MCP_', 'BROWSER_', 'PRIVACY_', 'GRANT_',
    'CAMERA_', 'VOICE_', 'TASK_', 'GUI_TOOL_'))} | {'INTELLIGENCE_ALERT', 'FAILURE_RECORDED', 'FILE_CHANGED'}


def event_summary(event):
    kind, payload = event.get('event_type', ''), event.get('payload') or {}
    name = payload.get('tool') or payload.get('name') or payload.get('component') or event.get('source', 'Hayat')
    state = kind.lower().replace('_', ' ')
    return f'{name}: {state}'


class ActivityMonitor:
    def __init__(self, events):
        self.rows = deque(maxlen=500)
        self.active = Counter()
        self.lock = threading.RLock()
        self.unsubscribers = []
        self.available = events is not None
        if events:
            self.unsubscribers = [events.subscribe(name, self.receive) for name in sorted(VISIBLE_EVENTS)]

    def receive(self, event):
        safe = redact(event)
        # Keep details useful but bounded even when a tool carries a large file.
        rendered = json.dumps(safe, ensure_ascii=False, indent=2, default=str)  # exported as data; shown as plain lines
        row = {'id': safe.get('event_id') or str(safe.get('id')),
               'timestamp': safe.get('timestamp', ''), 'event_type': safe.get('event_type', ''),
               'summary': event_summary(safe), 'details': rendered[:24000]}
        with self.lock:
            self.rows.appendleft(row)
            kind = safe.get('event_type')
            key = (safe.get('task_id'), safe.get('turn_id'), (safe.get('payload') or {}).get('tool'))
            if kind == 'TOOL_STARTED':
                self.active[key] += 1
            elif kind in {'TOOL_COMPLETED', 'TOOL_FAILED'} and self.active[key]:
                self.active[key] -= 1
                if not self.active[key]:
                    del self.active[key]

    def snapshot(self):
        with self.lock:
            return list(self.rows), sum(self.active.values())

    def close(self):
        for unsubscribe in self.unsubscribers:
            unsubscribe()
        self.unsubscribers.clear()


def live_state(gui):
    """Read cached properties only: no device probes or database calls."""
    parts = _live_parts(gui)
    return (f'Camera: {parts["camera"]} | Mic: {parts["mic"]} | Wi-Fi: {parts["wifi"]} | Public feeds: {parts["monitor"]} | '
            f'Learning: {parts["learning"]} | Privacy: {parts["privacy"]}')


def live_state_brief(gui):
    """The same state in one short line for the strip under the header; the tooltip keeps the full sentence."""
    parts = _live_parts(gui)
    short = {'stopped': 'off', 'running': 'on'}
    return ' · '.join((f'Camera {parts["camera"]}', f'Mic {parts["mic"]}', f'Wi-Fi {parts["wifi"]}',
                       f'Feeds {short.get(parts["monitor"], parts["monitor"])}',
                       f'Learning {short.get(parts["learning"], parts["learning"])}', f'Privacy {parts["privacy"]}'))


def plain_activity(summary):
    """An activity line with its leading tool name in words: "intelligence_radar: tool completed" -> "Intelligence radar: completed"."""
    text = str(summary or '')
    match = re.match(r'([a-z][a-z0-9]*(?:_[a-z0-9]+)+): (?:tool )?', text)
    if not match:
        return text
    return match[1].replace('_', ' ').capitalize() + ': ' + text[match.end():]


def _live_parts(gui):
    runtime = gui.runtime
    privacy = bool(runtime.authority.privacy_mode)
    voice = getattr(runtime, 'voice', None)
    state = voice.status() if voice else {}
    learner = getattr(runtime, 'learner', None)
    intelligence = getattr(runtime, 'intelligence', None)
    camera = 'on' if gui._camera_on else 'off'
    mic = 'on' if state.get('enabled') and state.get('state') in {'listening', 'capturing', 'recording'} else ('enabled' if state.get('enabled') else 'off')
    if state.get('muted') or state.get('privacy_blocked'):
        mic = 'paused'
    learning = 'paused' if learner and learner.paused else ('running' if learner and learner.running else 'stopped')
    monitor = 'running' if intelligence and intelligence.status()['running'] else 'stopped'
    # Wi-Fi sensing from the last background sensor poll ("Wi-Fi CSI 98/s", "Wi-Fi off"): cached, never probed here.
    wifi_text, _live = (getattr(gui, '_sensor_reading', None) or {}).get('wifi', ('Wi-Fi off', False))
    wifi = wifi_text.removeprefix('Wi-Fi ').strip() or 'off'
    return {'camera': camera, 'mic': mic, 'wifi': wifi, 'monitor': monitor, 'learning': learning,
            'privacy': 'on' if privacy else 'off'}


class ActivityWindow(tk.Toplevel):
    # None means the configured artifact folder and process log; tests point
    # these at a temporary folder so they never write the owner's data.
    incident_folder = None
    log_path = None

    def __init__(self, gui):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.monitor = gui, gui.activity_monitor
        self.title('HAYAT · Activity and transparency')
        self.configure(bg=gui.BLACK)
        self.minsize(740, 500)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: (self.close(), 'break')[1])
        header = tk.Frame(self, bg=gui.BLACK, padx=16, pady=12)
        header.pack(fill='x')
        tk.Label(header, text='What Hayat is doing', bg=gui.BLACK, fg=gui.WHITE,
                 font=('Segoe UI Semibold', 17)).pack(side='left')
        ttk.Button(header, text='Stop current task', command=gui._stop).pack(side='right')
        self.state = StringVar(self)
        tk.Label(self, textvariable=self.state, bg=gui.BLACK, fg=gui.GOLD_SOFT,
                 anchor='w', wraplength=900, justify='left').pack(fill='x', padx=16)
        controls = tk.Frame(self, bg=gui.BLACK, padx=16, pady=8)
        controls.pack(fill='x')
        self.query = StringVar(self)
        ttk.Label(controls, text='Find activity').pack(side='left')
        ttk.Entry(controls, textvariable=self.query).pack(side='left', fill='x', expand=True, padx=8)
        self.export_button = ttk.Button(controls, text='Export incident bundle', command=self.export_incident)
        self.export_button.pack(side='right', padx=(6, 0))
        ttk.Button(controls, text='Toggle privacy', command=gui._toggle_privacy).pack(side='right')
        ttk.Button(controls, text='Background learning', command=lambda: gui.show_view('background')).pack(side='right', padx=6)
        self.feed_button = ttk.Button(controls, text='Pause public feeds', command=self.toggle_feeds)
        self.feed_button.pack(side='right')
        listing = tk.Frame(self, bg=gui.BLACK)
        listing.pack(fill='both', expand=True, padx=16, pady=8)
        self.tree = ttk.Treeview(listing, columns=('time', 'kind'), selectmode='browse')
        self.tree.heading('#0', text='Recorded action')
        self.tree.heading('time', text='Time (UTC)')
        self.tree.heading('kind', text='Event')
        self.tree.column('#0', width=470)
        self.tree.column('time', width=90, stretch=False)
        self.tree.column('kind', width=210)
        scrollbar = ttk.Scrollbar(listing, command=self.tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(fill='both', expand=True)
        self.tree.tag_configure('failed', foreground=gui.ALERT)
        self.tree.tag_configure('blocked', foreground=gui.GOLD_SOFT)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.details())
        self.body = DarkScrolledText(self, height=7, wrap='word', bg=gui.PANEL,
                                    fg=gui.WHITE, font=('Segoe UI', 10))
        self.body.pack(fill='x', padx=16, pady=(0, 8))
        self.note = StringVar(self, 'Live events since this window session began · latest 500 retained on screen · credentials redacted. Select an action for recorded arguments and scope. Stop current task does not pause background monitors.')
        tk.Label(self, textvariable=self.note, bg=gui.BLACK, fg=gui.MUTED,
                 wraplength=900, justify='left', anchor='w').pack(fill='x', padx=16, pady=(0, 12))
        self.query.trace_add('write', lambda *_: self.refresh())
        self._version = None
        self._timer = None
        self._export_cancel = None
        self.shown = {}
        place_over(self, gui.root, (1020, 730))
        self.deiconify()
        self.refresh()
        self.poll()
        from app.gui_icons import apply_icons
        apply_icons(self, type(gui))

    def toggle_feeds(self):
        manager = getattr(self.gui.runtime, 'intelligence', None)
        if manager:
            action = 'stop' if manager.status()['running'] else 'start'
            def run():
                from app.gui_features import run_declared_tool
                result = run_declared_tool(self.gui.runtime, 'intelligence_monitor', {'action': action})
                if not result.success:
                    self.gui._post(self.gui._report, 'Feed monitor issue', str(result.error))
            threading.Thread(target=run, daemon=True, name='hayat-activity-control').start()

    def refresh(self):
        rows, active = self.monitor.snapshot()
        self.state.set(f'{active} live tool calls | ' + live_state(self.gui))
        manager = getattr(self.gui.runtime, 'intelligence', None)
        self.feed_button.configure(text='Pause public feeds' if manager and manager.status()['running'] else 'Start public feeds',
                                   state='normal' if manager else 'disabled')
        from app.gui_icons import apply_icons
        apply_icons(self.feed_button, type(self.gui))
        query = self.query.get().casefold().split()
        version = (tuple(row['id'] for row in rows), tuple(query))
        if version == self._version:
            return
        self._version = version
        selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.shown = {}
        for index, row in enumerate(rows):
            if not all(word in (row['summary'] + row['details']).casefold() for word in query):
                continue
            key = f'row{index}'
            self.shown[key] = row
            stamp = row['timestamp'][11:19]
            tag = 'failed' if 'FAILED' in row['event_type'] else ('blocked' if any(word in row['event_type'] for word in ('DENIED', 'REFUSED', 'APPROVAL_REQUIRED')) else '')
            self.tree.insert('', 'end', iid=key, text=row['summary'], values=(stamp, row['event_type']), tags=(tag,))
        # IDs use event identities in selection preservation, not row offsets.
        if selection and getattr(self, '_selected_id', None):
            for key, row in self.shown.items():
                if row['id'] == self._selected_id:
                    self.tree.selection_set(key)
                    break
        if not rows:
            self.set_body('No recorded activity yet.' if self.monitor.available else 'Activity events are unavailable in this runtime.')
        elif not self.shown:
            self.set_body('No activity matches this search. Try a tool name, background component or failed.')

    def set_body(self, text):
        self.body.configure(state='normal')
        self.body.delete('1.0', 'end')
        self.body.insert('1.0', text)
        self.body.configure(state='disabled')

    def details(self):
        selected = self.tree.selection()
        if selected and selected[0] in self.shown:
            row = self.shown[selected[0]]
            self._selected_id = row['id']
            from app.plain_output import describe_full

            self.set_body(describe_full(row['details']))

    def incident_parts(self):
        """Read Tk-side state now; slower parts are read on the export thread."""
        from app.config import PROJECT_ROOT, load_settings
        from app.incident_bundle import log_tail
        runtime = self.gui.runtime
        def parsed(details):
            try:
                return json.loads(details)
            except ValueError:  # cut at 24,000 characters for the screen
                return details
        rows = [dict(row, details=parsed(row['details'])) for row in self.shown.values()]
        state = live_state(self.gui)
        failures = getattr(runtime, 'failures', None)
        incidents = getattr(runtime, 'incidents', None)
        log = self.log_path or PROJECT_ROOT / load_settings().get('log_directory', 'data/logs') / 'hayat.log'
        parts = {
            'activity.json': ('Activity window: events seen this window session, as filtered by the search box',
                              lambda: rows),
            'state.txt': ('Activity window: live camera, microphone, feed, learning and privacy state',
                          lambda: state),
            'failures.json': ('Failure log: the latest 50 recorded failures',
                              (lambda: failures.recent(50)) if failures else (lambda: [])),
            'hayat.log': (f'Process log {log.name}: the last 200 KB', lambda: log_tail(log)),
        }
        if incidents:
            parts['system.json'] = ('incident_snapshot (read-only resources and AI components)',
                                    lambda: incidents.snapshot('ai'))
        return parts

    def export_incident(self):
        if self._export_cancel is not None:
            self._export_cancel.set()
            self.export_button.configure(text='Cancelling...', state='disabled')
            return
        from app.config import PROJECT_ROOT, load_settings
        from app.incident_bundle import ExportCancelled, describe, export_bundle
        parts = self.incident_parts()
        folder = self.incident_folder or PROJECT_ROOT / load_settings().get('artifact_directory', 'data/artifacts') / 'incidents'
        cancel = self._export_cancel = threading.Event()
        query = self.query.get().strip()
        self.export_button.configure(text='Cancel export')
        self.set_body('Collecting the incident bundle: activity, failures, log tail and a read-only system check...')

        def run():
            try:
                text = describe(export_bundle(folder, parts, query=query, cancelled=cancel.is_set))
            except ExportCancelled:
                text = 'Export cancelled. No bundle was saved.'
            except Exception as exc:
                text = f'The incident bundle could not be saved: {type(exc).__name__}: {exc}'
            self.gui._post(self._export_done, text)
        threading.Thread(target=run, daemon=True, name='hayat-incident-export').start()

    def _export_done(self, text):
        self._export_cancel = None
        if self.winfo_exists():
            self.export_button.configure(text='Export incident bundle', state='normal')
            self.set_body(text)

    def poll(self):
        self.refresh()
        self._timer = self.after(700, self.poll)

    def close(self):
        if self._timer:
            self.after_cancel(self._timer)
        self.destroy()
