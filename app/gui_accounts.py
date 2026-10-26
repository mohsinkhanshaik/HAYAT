"""Accounts & sign-in window: connect mail, social, messaging and work accounts, then work with them.

Left: every account, grouped (Mail & calendar, Social, Messaging, Work) with how many are connected, a
filter, HAYAT's other connections (email sending, keys, the credential store) and the platforms that
cannot be connected honestly, with why. Right, for the chosen account:

* Connect: the ways to sign in, easiest first. A code shown large with Copy and "Open the
  sign-in page", counting down while HAYAT waits for the approval; browser sign-in; a pasted
  token. Below, the one-time app setup with numbered steps and a button to its page.
* Work: read operations as buttons, results in a table (double-click opens the item); forms for
  sending or creating something, which only *prepare* it: the confirmation window shows every
  value, and nothing leaves until the owner confirms.

Passwords are typed only on the providers' own pages. Tokens go straight to the credential store.
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from datetime import datetime
import webbrowser
from tkinter import ttk
from typing import Any

from app.gui_intelligence_parts import blend
from app.gui_resources import StringVar
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY

UP, DOWN = '#4ADE80', '#F05D5E'
COLOURS = {'google': '#4285F4', 'microsoft': '#00A4EF', 'github': '#8B949E', 'linkedin': '#0A66C2', 'notion': '#A0A0A0',
           'slack': '#E01E5A', 'bluesky': '#1185FE', 'mastodon': '#6364FF', 'reddit': '#FF4500', 'x': '#9AA4AE',
           'telegram': '#26A5E4', 'discord': '#5865F2', 'facebook': '#1877F2', 'threads': '#B0B0B0'}
BLURBS = {'device': 'Easiest. HAYAT shows a short code; you sign in on the provider\'s page, type the code and approve. '
                    'Your password never reaches HAYAT.',
          'browser': 'The provider\'s sign-in page opens in your browser; after you approve, it comes back to HAYAT on '
                     'this computer only.',
          'token': 'Make {a_label} on the provider\'s site and paste it here. HAYAT checks it with one read, then keeps it in '
                   'Windows Credential Manager.',
          'webhook': 'The simplest way to post to one channel: the service gives you a web address that posts there. It cannot '
                     'read anything. Kept in Windows Credential Manager like a token.'}


def _local_time(stamp: str) -> str:
    """'2026-10-25T20:31:05+00:00' as this computer's time, '20:31'."""
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime('%H:%M')
    except (TypeError, ValueError):
        return ''


def _a(label: str) -> str:
    """'an app password', 'a bot token', 'a Page access token'."""
    return ('an ' if label[:1].lower() in 'aeiou' else 'a ') + label


class AccountsCenter(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p = gui, type(gui)
        self.runtime = gui.runtime
        self.manager = self.runtime.accounts
        p = self.p
        self._closed = False
        self._results: queue.Queue = queue.Queue()
        self._busy: set[str] = set()
        self.selected = 'google'
        self.data: dict | None = None
        self.device: dict | None = None
        self.device_deadline = 0.0
        self.next_poll = 0.0
        self.rows_data: list = []
        self.status = StringVar(self, '')
        self.token = StringVar(self, '')
        self.webhook = StringVar(self, '')
        self.token_values: dict[str, StringVar] = {}
        self.filter = StringVar(self, '')
        self.client_id = StringVar(self, '')
        self.client_secret = StringVar(self, '')
        self.return_address = StringVar(self, '')
        self.browser_key = ''
        self.write_op = StringVar(self, '')
        self.field_vars: dict[str, Any] = {}
        self.title('HAYAT · Accounts & sign-in')
        self.configure(bg=p.BLACK)
        self.minsize(960, 620)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        self._build()
        self.refresh()
        place_over(self, gui.root, (1360, 860))
        self.deiconify()
        self._tick_id = self.after(100, self._tick)
        if self.data and self.data.get('connected'):
            self.status.set(self.status.get() + ' · checking…')
            self._run('check_all', self.manager.check_all)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        p = self.p
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill='x')
        words = tk.Frame(header, bg=p.BLACK)
        words.pack(side='left', fill='x', expand=True)
        tk.Label(words, text='Accounts & sign-in', bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 19), anchor='w').pack(fill='x')
        tk.Label(words, text='Connect your own accounts, then read your mail, calendar, files, repositories and channels, and send '
                             'or create things once you confirm', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w').pack(fill='x')
        self.everywhere_button = ttk.Button(header, text='Post everywhere…', style='HayatAccent.TButton', command=self.post_everywhere)
        self.everywhere_button.pack(side='right')
        tk.Label(header, textvariable=self.status, bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9)).pack(side='right', padx=10)
        tk.Label(self, text='You sign in on each provider\'s own page; HAYAT never sees your password. Tokens stay in Windows '
                            'Credential Manager. Reading is direct; sending or changing anything waits for your confirmation.',
                 bg=blend(p.GOLD, p.BLACK, .85), fg=p.GOLD_SOFT, font=('Segoe UI', 9), anchor='w', padx=16, pady=5).pack(fill='x', padx=16)
        body = tk.PanedWindow(self, orient='horizontal', bg=p.BLACK, sashwidth=6, bd=0)
        body.pack(fill='both', expand=True, padx=16, pady=8)
        left = tk.Frame(body, bg=p.BLACK)
        find = tk.Frame(left, bg=p.BLACK)
        find.pack(fill='x', pady=(0, 6))
        tk.Label(find, text='Find', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9)).pack(side='left')
        self.filter_entry = ttk.Entry(find, textvariable=self.filter)
        self.filter_entry.pack(side='left', fill='x', expand=True, padx=(6, 0))
        self.filter.trace_add('write', lambda *_args: self.apply_filter())
        # Fifteen accounts and more do not fit one column: the list scrolls, grouped by what they are for.
        listing = tk.Frame(left, bg=p.BLACK)
        listing.pack(fill='both', expand=True)
        self.list_canvas = tk.Canvas(listing, bg=p.BLACK, highlightthickness=0)
        list_scroll = ttk.Scrollbar(listing, command=self.list_canvas.yview)
        self.list_canvas.configure(yscrollcommand=list_scroll.set)
        list_scroll.pack(side='right', fill='y')
        self.list_canvas.pack(side='left', fill='both', expand=True)
        inner = tk.Frame(self.list_canvas, bg=p.BLACK)
        self._list_window = self.list_canvas.create_window(0, 0, window=inner, anchor='nw')
        inner.bind('<Configure>', lambda _event: self.list_canvas.configure(scrollregion=self.list_canvas.bbox('all')))
        self.list_canvas.bind('<Configure>', lambda event: self.list_canvas.itemconfigure(self._list_window, width=event.width))
        self.cards = tk.Frame(inner, bg=p.BLACK)
        self.cards.pack(fill='x')
        self.card_widgets: dict[str, dict[str, Any]] = {}
        self.group_widgets: dict[str, dict[str, Any]] = {}
        tk.Label(inner, text='Other connections', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x', pady=(14, 4))
        self.others = tk.Frame(inner, bg=p.BLACK)
        self.others.pack(fill='x')
        tk.Label(inner, text='Not offered, and why', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x', pady=(14, 4))
        self.not_offered = tk.Frame(inner, bg=p.BLACK)
        self.not_offered.pack(fill='x')
        right = tk.Frame(body, bg=p.BLACK)
        self.title_label = tk.Label(right, text='', bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 16), anchor='w')
        self.title_label.pack(fill='x')
        self.state_label = tk.Label(right, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 10), anchor='w')
        self.state_label.pack(fill='x', pady=(0, 4))
        self.stepper = tk.Frame(right, bg=p.BLACK)
        self.stepper.pack(fill='x', pady=(0, 6))
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill='both', expand=True)
        holder = tk.Frame(self.tabs, bg=p.BLACK)
        self.connect_canvas = tk.Canvas(holder, bg=p.BLACK, highlightthickness=0)
        scroll = ttk.Scrollbar(holder, command=self.connect_canvas.yview)
        self.connect_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.connect_canvas.pack(side='left', fill='both', expand=True)
        self.connect_tab = tk.Frame(self.connect_canvas, bg=p.BLACK)
        self._connect_window = self.connect_canvas.create_window(0, 0, window=self.connect_tab, anchor='nw')
        self.connect_tab.bind('<Configure>', lambda _event: self.connect_canvas.configure(scrollregion=self.connect_canvas.bbox('all')))
        self.connect_canvas.bind('<Configure>', lambda event: self.connect_canvas.itemconfigure(self._connect_window, width=event.width))
        self.bind('<MouseWheel>', self.wheel)
        self.work_tab = tk.Frame(self.tabs, bg=p.BLACK)
        self.tabs.add(holder, text='Connect')
        self.tabs.add(self.work_tab, text='Work with it')
        self._build_work()
        body.add(left, minsize=300, width=340, stretch='never')
        body.add(right, minsize=560, stretch='always')

    def _group(self, name: str) -> dict[str, Any]:
        """A heading per group with how many of its accounts are connected; the cards sit under it."""
        if name not in self.group_widgets:
            p = self.p
            heading = tk.Label(self.cards, text=name, bg=p.BLACK, fg=p.GOLD_SOFT, font=('Segoe UI Semibold', 9), anchor='w')
            holder = tk.Frame(self.cards, bg=p.BLACK)
            heading.pack(fill='x', pady=(8 if self.group_widgets else 0, 3))
            holder.pack(fill='x')
            self.group_widgets[name] = {'heading': heading, 'holder': holder}
        return self.group_widgets[name]

    def apply_filter(self) -> None:
        """Show only the accounts whose name or purpose holds the typed words; empty groups hide their heading."""
        words = self.filter.get().strip().lower()
        if not self.data:
            return
        shown: dict[str, int] = {}
        for row in self.data['accounts']:
            widgets = self.card_widgets.get(row['key'])
            if widgets is None:
                continue
            visible = not words or words in f"{row['name']} {row['what']} {row.get('group', '')}".lower()
            widgets['card'].pack_forget()
            if visible:
                widgets['card'].pack(in_=self._group(row.get('group', 'Work'))['holder'], fill='x', pady=(0, 5))
                shown[row.get('group', 'Work')] = shown.get(row.get('group', 'Work'), 0) + 1
        for name, group in self.group_widgets.items():
            group['heading'].pack_forget()
            group['holder'].pack_forget()
        for name in self.data.get('groups') or list(self.group_widgets):
            if name in self.group_widgets and shown.get(name):
                self.group_widgets[name]['heading'].pack(fill='x', pady=(8, 3))
                self.group_widgets[name]['holder'].pack(fill='x')
        self.list_canvas.yview_moveto(0)

    def _card(self, row: dict) -> None:
        p = self.p
        card = tk.Frame(self._group(row.get('group', 'Work'))['holder'], bg=p.PANEL, padx=10, pady=6, cursor='hand2')
        card.pack(fill='x', pady=(0, 5))
        badge = tk.Canvas(card, width=30, height=30, bg=p.PANEL, highlightthickness=0)
        badge.create_oval(2, 2, 28, 28, fill=blend(COLOURS.get(row['key'], p.GOLD), p.PANEL, .35), outline='')
        badge.create_text(15, 15, text=row['name'][0], fill=p.WHITE, font=('Segoe UI Semibold', 12))
        badge.pack(side='left')
        words = tk.Frame(card, bg=p.PANEL)
        words.pack(side='left', fill='x', expand=True, padx=8)
        name = tk.Label(words, text=row['name'], bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w')
        name.pack(fill='x')
        state = tk.Label(words, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8), anchor='w')
        state.pack(fill='x')
        for widget in (card, badge, words, name, state):
            widget.bind('<Button-1>', lambda _event, key=row['key']: self.select(key))
        self.card_widgets[row['key']] = {'card': card, 'state': state, 'words': words, 'name': name, 'badge': badge}

    def _build_work(self) -> None:
        p = self.p
        frame = self.work_tab
        self.work_note = tk.Label(frame, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left', wraplength=900)
        self.work_note.pack(fill='x', pady=(8, 4))
        self.read_row = tk.Frame(frame, bg=p.BLACK)
        self.read_row.pack(fill='x')
        self._section(frame, 'Results')
        table = tk.Frame(frame, bg=p.BLACK)
        table.pack(fill='both', expand=True)
        self.results = ttk.Treeview(table, columns=('detail', 'when'), selectmode='browse', height=9)
        for key, label, width in (('#0', 'Item', 420), ('detail', 'From / where', 240), ('when', 'When', 180)):
            self.results.heading(key, text=label)
            self.results.column(key, width=width, stretch=key == '#0')
        self.results.tag_configure('unread', foreground=p.GOLD_SOFT)
        scroll = ttk.Scrollbar(table, command=self.results.yview)
        self.results.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.results.pack(side='left', fill='both', expand=True)
        self.results.bind('<<TreeviewSelect>>', lambda _event: self.result_selected())
        self.results.bind('<Double-1>', lambda _event: self.open_result())
        self.result_detail = tk.Label(frame, text='', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9), anchor='nw', justify='left',
                                      padx=10, pady=6, wraplength=900)
        self.result_detail.pack(fill='x', pady=(4, 0))
        self._section(frame, 'Send or create (you confirm before anything leaves)')
        top = tk.Frame(frame, bg=p.BLACK)
        top.pack(fill='x')
        self.write_box = ttk.Combobox(top, textvariable=self.write_op, state='readonly', width=40)
        self.write_box.pack(side='left')
        self.write_box.bind('<<ComboboxSelected>>', lambda _event: self.build_form())
        self.prepare_button = ttk.Button(top, text='Prepare for my confirmation', style='HayatAccent.TButton', command=self.prepare)
        self.prepare_button.pack(side='right')
        self.form = tk.Frame(frame, bg=p.BLACK)
        self.form.pack(fill='x', pady=(6, 0))
        self.write_note = tk.Label(frame, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left', wraplength=900)
        self.write_note.pack(fill='x', pady=(4, 6))

    def wheel(self, event: Any) -> None:
        """The mouse wheel scrolls the Connect tab while it is shown."""
        if self.tabs.index('current') == 0:
            self.connect_canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), 'units')

    def paint_stepper(self, row: dict) -> None:
        """① app setup → ② sign in → ③ work with it, each ticked when done."""
        p = self.p
        for child in self.stepper.winfo_children():
            child.destroy()
        app_state = ('not needed' if not row['needs_app'] or row['connected'] and row['method'] == 'token'
                     else 'done' if row['client_id_set'] else 'to do')
        steps = [('1', 'App setup', app_state), ('2', 'Sign in', 'done' if row['connected'] else 'to do'),
                 ('3', 'Work with it', 'ready' if row['connected'] else 'waiting')]
        for index, (number, label, state) in enumerate(steps):
            good = state in ('done', 'ready', 'not needed')
            colour = UP if good else p.GOLD if state == 'to do' else p.MUTED
            chip = tk.Label(self.stepper, text=f" {'✓' if good else number}  {label} · {state} ", bg=blend(colour, p.BLACK, .8), fg=colour,
                            font=('Segoe UI Semibold', 9), padx=6, pady=3)
            chip.pack(side='left')
            if index < len(steps) - 1:
                tk.Label(self.stepper, text='  →  ', bg=p.BLACK, fg=p.MUTED).pack(side='left')

    def _section(self, parent: Any, text: str) -> None:
        tk.Label(parent, text=text, bg=self.p.BLACK, fg=self.p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x', pady=(10, 4))

    # ------------------------------------------------------------------ background work
    def _run(self, kind: str, call: Any) -> None:
        if kind in self._busy:
            return
        self._busy.add(kind)

        def work():
            try:
                self._results.put((kind, call(), None))
            except Exception as exc:  # shown in the window, never raised into Tk
                self._results.put((kind, None, f'{type(exc).__name__}: {exc}'))
        threading.Thread(target=work, name='hayat-accounts', daemon=True).start()

    def _tick(self) -> None:
        if self._closed:
            return
        while not self._results.empty():
            kind, value, error = self._results.get_nowait()
            self._busy.discard(kind)
            getattr(self, 'done_' + kind)(value, error)
        if self.device and time.monotonic() >= self.next_poll and 'poll' not in self._busy:
            self.next_poll = time.monotonic() + self.device['interval']
            key = self.device['provider']
            self._run('poll', lambda: self.manager.poll_device(key))
        if self.device:
            left = max(0, int(self.device_deadline - time.monotonic()))
            self.countdown.configure(text=f'Waiting for your approval… the code works for {left // 60}:{left % 60:02d} more')
        self._tick_id = self.after(250, self._tick)

    # ------------------------------------------------------------------ status
    def refresh(self) -> None:
        self.data = self.manager.status()
        for name in self.data.get('groups') or ():  # headings in their intended order, before any card makes its own
            if any(row.get('group') == name for row in self.data['accounts']):
                self._group(name)
        for row in self.data['accounts']:
            if row['key'] not in self.card_widgets:
                self._card(row)
            widgets = self.card_widgets[row['key']]
            if row['connected'] and row.get('check_ok') is False:
                state, colour = '⚠ sign-in not working · check again or reconnect', DOWN
            elif row['connected']:
                state, colour = '● connected · ' + row['connected_as'], UP
            else:
                state, colour = '○ not connected', self.p.MUTED
            widgets['state'].configure(text=state, fg=colour)
            fill = blend(self.p.GOLD, self.p.PANEL, .82) if row['key'] == self.selected else self.p.PANEL
            for name in ('card', 'words', 'name', 'state', 'badge'):
                widgets[name].configure(bg=fill)
        failing = [row['name'] for row in self.data['accounts'] if row['connected'] and row.get('check_ok') is False]
        self.status.set(f"{len(self.data['connected'])} of {len(self.data['accounts'])} accounts connected"
                        + (f" · {', '.join(failing)} not working" if failing else ''))
        for name, group in self.group_widgets.items():
            rows = [row for row in self.data['accounts'] if row.get('group') == name]
            connected = sum(1 for row in rows if row['connected'])
            group['heading'].configure(text=f"{name.upper()}  ·  {connected} of {len(rows)} connected")
        social = [row for row in self.data['accounts'] if row.get('can_post') and row['connected']]
        self.everywhere_button.state(['!disabled'] if social else ['disabled'])
        self.paint_others()
        self.paint_not_offered()
        self.select(self.selected)

    def paint_others(self) -> None:
        p = self.p
        for child in self.others.winfo_children():
            child.destroy()
        email = getattr(self.runtime, 'email', None)
        broker = getattr(self.runtime, 'secret_broker', None)
        rows = []
        if email is not None:
            try:
                state = email.status()
                rows.append(('Email sending (SMTP)', ('● ready · ' + state['address']) if state['ready'] else '○ ' + (state['problem'] or 'not set up'),
                             state['ready'], 'Business → Email setup', '_open_business'))
            except Exception as exc:  # shown, never raised
                rows.append(('Email sending (SMTP)', f'○ {type(exc).__name__}', False, '', ''))
        rows.append(('Keys (traffic cameras, stock prices)', 'free keys you add yourself', True, 'Open Keys', '_open_keys'))
        if broker is not None:
            backend = broker.status().get('backend', '')
            rows.append(('Credential store', ('Windows Credential Manager' if 'Keyring' in backend else 'encrypted file') + ' · values never shown',
                         True, '', ''))
        for title, state, good, action, method in rows:
            frame = tk.Frame(self.others, bg=p.PANEL, padx=10, pady=6)
            frame.pack(fill='x', pady=(0, 4))
            tk.Label(frame, text=title, bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 9), anchor='w').pack(fill='x')
            tk.Label(frame, text=state, bg=p.PANEL, fg=UP if good and state.startswith('●') else p.MUTED, font=('Segoe UI', 8),
                     anchor='w').pack(fill='x')
            opener = getattr(self.gui, method, None) if method else None
            if callable(opener):
                link = tk.Label(frame, text=action + ' ↗', bg=p.PANEL, fg=p.GOLD_SOFT, font=('Segoe UI', 8, 'underline'), cursor='hand2',
                                anchor='w')
                link.pack(fill='x')
                link.bind('<Button-1>', lambda _event, opener=opener: opener())

    def paint_not_offered(self) -> None:
        p = self.p
        for child in self.not_offered.winfo_children():
            child.destroy()
        for item in (self.data or {}).get('not_offered') or []:
            frame = tk.Frame(self.not_offered, bg=p.PANEL, padx=10, pady=5)
            frame.pack(fill='x', pady=(0, 4))
            tk.Label(frame, text=item['name'], bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 9), anchor='w').pack(fill='x')
            tk.Label(frame, text=item['why'], bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8), anchor='w', justify='left',
                     wraplength=280).pack(fill='x')

    def post_everywhere(self) -> None:
        """One text for every connected social account; each post waits for its own confirmation."""
        p = self.p
        dialog = tk.Toplevel(self)
        dialog.title('HAYAT · Post everywhere')
        dialog.configure(bg=p.BLACK)
        dialog.transient(self)
        self.everywhere_dialog = dialog
        names = [row['name'] for row in self.data['accounts'] if row.get('can_post') and row['connected']]
        tk.Label(dialog, text='Post to: ' + ', '.join(names), bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11),
                 anchor='w', justify='left', wraplength=560).pack(fill='x', padx=14, pady=(12, 4))
        box = tk.Text(dialog, height=6, width=70, wrap='word', bg=p.PANEL, fg=p.WHITE, insertbackground=p.WHITE,
                      font=('Segoe UI', 10))
        box.pack(fill='both', expand=True, padx=14)
        self.everywhere_text = box
        counter = tk.Label(dialog, text='0 characters · Bluesky takes 300, X 280, Threads and Mastodon 500', bg=p.BLACK,
                           fg=p.MUTED, font=('Segoe UI', 8), anchor='w')
        counter.pack(fill='x', padx=14, pady=(4, 0))
        box.bind('<KeyRelease>', lambda _event: counter.configure(
            text=f"{len(box.get('1.0', 'end').strip())} characters · Bluesky takes 300, X 280, Threads and Mastodon 500"))
        tk.Label(dialog, text='Nothing is sent from here: each account\'s post opens in the confirmation window, and goes only when '
                              'you confirm it.', bg=p.BLACK, fg=p.GOLD_SOFT, font=('Segoe UI', 9), anchor='w', justify='left',
                 wraplength=560).pack(fill='x', padx=14, pady=(6, 4))
        buttons = tk.Frame(dialog, bg=p.BLACK)
        buttons.pack(fill='x', padx=14, pady=(0, 12))
        ttk.Button(buttons, text='Prepare the posts', style='HayatAccent.TButton',
                   command=lambda: self.prepare_everywhere(box.get('1.0', 'end').strip(), dialog)).pack(side='left')
        ttk.Button(buttons, text='Cancel', style='Hayat.TButton', command=dialog.destroy).pack(side='left', padx=6)
        place_over(dialog, self, (620, 300))
        box.focus_set()

    def prepare_everywhere(self, text: str, dialog: Any) -> None:
        if not text:
            return
        dialog.destroy()
        self.note('Preparing one post per account…')
        registry = self.runtime.registry
        self._run('everywhere', lambda: self._tool(registry, 'account_post_everywhere', {'text': text}))

    @staticmethod
    def _tool(registry: Any, name: str, arguments: dict) -> dict:
        result = registry.execute(name, arguments)
        if not result.success:
            raise RuntimeError(result.error or 'not done')
        return result.output

    def done_everywhere(self, value: dict | None, error: str | None) -> None:
        if error:
            self.note('Not prepared: ' + error.split(': ', 1)[-1], DOWN)
            return
        skipped = '; '.join(f"{item['name']}: {item['why']}" for item in value.get('skipped') or [])
        self.note(value['how_to_confirm'] + (f' Left out: {skipped}.' if skipped else ''), UP)

    def row(self, key: str | None = None) -> dict:
        return next(item for item in self.data['accounts'] if item['key'] == (key or self.selected))

    def select(self, key: str) -> None:
        changed = key != self.selected
        self.selected = key
        for name, widgets in self.card_widgets.items():
            fill = blend(self.p.GOLD, self.p.PANEL, .82) if name == key else self.p.PANEL
            for part in ('card', 'words', 'name', 'state', 'badge'):
                widgets[part].configure(bg=fill)
        row = self.row()
        if changed:
            self.token_values = {}
        self.title_label.configure(text=f"{row['name']} · {row['what']}")
        if row['connected']:
            self.state_label.configure(text=f"● Connected as {row['connected_as']} · {self.method_name(row['method'])} · since "
                                            f"{row['connected_at'][:16].replace('T', ' ')} UTC", fg=UP)
        else:
            self.state_label.configure(text='○ Not connected yet: choose a way to sign in below', fg=self.p.MUTED)
        self.paint_stepper(row)
        if changed or not self.device:
            self.paint_connect()
            self.connect_canvas.yview_moveto(0)
        self.paint_work(reset=changed)

    @staticmethod
    def method_name(method: str) -> str:
        return {'device': 'signed in with a code', 'browser': 'signed in with the browser', 'token': 'with a pasted token',
                'webhook': 'with an incoming webhook'}.get(method, method)

    # ------------------------------------------------------------------ connect tab
    def paint_connect(self) -> None:
        p = self.p
        for child in self.connect_tab.winfo_children():
            child.destroy()
        row = self.row()
        frame = self.connect_tab
        if row['connected']:
            box = tk.Frame(frame, bg=p.PANEL, padx=12, pady=10)
            box.pack(fill='x', pady=(8, 4))
            words = tk.Frame(box, bg=p.PANEL)
            words.pack(side='left', fill='x', expand=True)
            failed = row.get('check_ok') is False
            tk.Label(words, text=f"Connected as {row['connected_as']}" if not failed else 'Saved, but the sign-in is not working',
                     bg=p.PANEL, fg=DOWN if failed else UP, font=('Segoe UI Semibold', 12), anchor='w').pack(fill='x')
            when = _local_time(row.get('checked_at', ''))
            if failed:
                detail = f"Checked at {when}: {row.get('check_error') or 'no answer'}. Sign in again below." if when else \
                    f"{row.get('check_error') or 'No answer'}. Sign in again below."
            elif when:
                detail = f"✓ Checked at {when}: {row['name']} answered as this account."
            else:
                detail = 'Not checked yet: press Check now.'
            tk.Label(words, text=detail, bg=p.PANEL, fg=DOWN if failed else p.MUTED, font=('Segoe UI', 9), anchor='w',
                     justify='left', wraplength=640).pack(fill='x')
            ttk.Button(box, text='Sign out', style='Compact.Hayat.TButton', command=self.sign_out).pack(side='right')
            ttk.Button(box, text='Check now', style='Compact.Hayat.TButton', command=self.test).pack(side='right', padx=6)
            if row['key'] == 'telegram':
                ttk.Button(box, text='Link my chat', style='Compact.Hayat.TButton', command=self.link_telegram).pack(side='right')
                ttk.Button(box, text='Open my bot ↗', style='Compact.Hayat.TButton', command=self.open_telegram_bot).pack(side='right', padx=6)
                linked = ((self.manager._records().get('telegram') or {}).get('settings') or {}).get('chat_name', '')
                tk.Label(words, text=f'Messages go to: {linked}' if linked else 'Press Open my bot, tap Start, then Link my chat.',
                         bg=p.PANEL, fg=p.MUTED if linked else p.GOLD_SOFT, font=('Segoe UI', 9), anchor='w').pack(fill='x')
        setup_first = row['needs_app'] and not row['client_id_set'] and not row['connected'] and \
            not any(method['key'] == 'token' for method in row['methods'])
        if setup_first:
            self.paint_app_setup(row)
        self._section(frame, 'Ways to sign in, easiest first' if not row['connected'] else 'Sign in again another way')
        self.connect_note = tk.Label(frame, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left', wraplength=900)
        for number, method in enumerate(row['methods'], 1):
            card = tk.Frame(frame, bg=p.PANEL, padx=12, pady=8)
            card.pack(fill='x', pady=(0, 6))
            head = tk.Frame(card, bg=p.PANEL)
            head.pack(fill='x')
            if method['key'] == 'token':
                title = row.get('token_title') or f"Paste {_a(row.get('token_label', 'token'))}"
            elif method['key'] == 'browser':
                title = row.get('browser_title') or method['label']
            else:
                title = method['label']
            tk.Label(head, text=f"{number}. {title}", bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 11)).pack(side='left')
            if not method['ready']:
                missing = ('needs the app’s client secret (in the app setup)' if row['client_id_set'] and method['key'] == 'browser'
                           else 'needs the one-time app setup')
                tk.Label(head, text=missing, bg=p.PANEL, fg=p.GOLD_SOFT, font=('Segoe UI', 8)).pack(side='left', padx=8)
            tk.Label(card, text=BLURBS[method['key']].format(a_label=_a(row.get('token_label', 'token'))), bg=p.PANEL, fg=p.MUTED,
                     font=('Segoe UI', 9), anchor='w', justify='left', wraplength=820).pack(fill='x')
            if method['key'] == 'device':
                actions = tk.Frame(card, bg=p.PANEL)
                actions.pack(fill='x', pady=(6, 0))
                button = ttk.Button(actions, text='Get my code', style='HayatAccent.TButton', command=self.start_device)
                button.pack(side='left')
                button.state(['!disabled' if method['ready'] else 'disabled'])
                self.code_area = tk.Frame(card, bg=p.PANEL)
                self.code_area.pack(fill='x')
            elif method['key'] == 'browser':
                line = tk.Frame(card, bg=p.PANEL)
                line.pack(fill='x', pady=(6, 0))
                if row.get('dynamic_app'):  # Mastodon's server or Bluesky's handle comes first
                    first = (row.get('token_fields') or [{'name': 'server', 'label': 'Your server', 'value': ''}])[0]
                    tk.Label(line, text='Your handle' if first['name'] == 'handle' else 'Your server', bg=p.PANEL, fg=p.WHITE,
                             font=('Segoe UI', 9)).pack(side='left', padx=(0, 6))
                    server = self.token_values.setdefault(first['name'], StringVar(self, first.get('value', '')))
                    entry = ttk.Entry(line, textvariable=server, width=28)
                    entry.pack(side='left', padx=(0, 8))
                    entry.bind('<Return>', lambda _event: self.start_browser())
                # Never a dead button: without an app yet, it leads into the one-time setup instead.
                button = ttk.Button(line, text='Open the sign-in page' if method['ready'] else 'Set up, then sign in',
                                    style='HayatAccent.TButton', command=self.start_browser)
                button.pack(side='left')
                button.state(['disabled'] if row.get('browser_waiting') else ['!disabled'])
                if row.get('builtin_app'):
                    tk.Label(line, text="HAYAT's own app: just sign in", bg=p.PANEL, fg=UP, font=('Segoe UI', 9)).pack(side='left', padx=8)
                elif row.get('dynamic_app'):
                    tk.Label(line, text='No setup needed' if row['key'] == 'bluesky' else
                             'No setup: HAYAT registers itself with your server', bg=p.PANEL, fg=UP,
                             font=('Segoe UI', 9)).pack(side='left', padx=8)
                self.browser_area = tk.Frame(card, bg=p.PANEL)
                self.browser_area.pack(fill='x')
                if self.browser_key == row['key']:  # a repaint while waiting keeps the waiting panel
                    self._browser_tries = 0
                    self.after_idle(self.paint_browser_wait)
            elif method['key'] == 'webhook':
                actions = tk.Frame(card, bg=p.PANEL)
                actions.pack(fill='x', pady=(6, 0))
                entry = ttk.Entry(actions, textvariable=self.webhook, show='•', width=60)
                entry.pack(side='left', fill='x', expand=True)
                entry.bind('<Return>', lambda _event: self.save_webhook())
                ttk.Button(actions, text='Save webhook', style='HayatAccent.TButton', command=self.save_webhook).pack(side='left', padx=6)
                ttk.Button(actions, text='Make a webhook ↗', style='Compact.Hayat.TButton',
                           command=lambda: webbrowser.open(row['webhook_page'])).pack(side='left')
                for index, step in enumerate(row['webhook_steps'], 1):
                    tk.Label(card, text=f'{index}. {step}', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9), anchor='w', justify='left',
                             wraplength=820).pack(fill='x')
            else:
                first_empty = None
                # The values a token needs beside it: a Mastodon server, a Bluesky handle, a Facebook Page ID.
                for field in row.get('token_fields') or []:
                    line = tk.Frame(card, bg=p.PANEL)
                    line.pack(fill='x', pady=(6, 0))
                    tk.Label(line, text=field['label'], bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9), anchor='w').pack(anchor='w')
                    variable = self.token_values.setdefault(field['name'], StringVar(self, field.get('value', '')))
                    field_entry = ttk.Entry(line, textvariable=variable, width=60)
                    field_entry.pack(fill='x')
                    field_entry.bind('<Return>', lambda _event: self.save_token())
                    if not variable.get() and first_empty is None:
                        first_empty = field_entry
                label = row.get('token_label', 'token')
                actions = tk.Frame(card, bg=p.PANEL)
                actions.pack(fill='x', pady=(6, 0))
                tk.Label(actions, text=label[:1].upper() + label[1:], bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9)).pack(side='left', padx=(0, 6))
                entry = ttk.Entry(actions, textvariable=self.token, show='•', width=60)
                entry.pack(side='left', fill='x', expand=True)
                entry.bind('<Return>', lambda _event: self.save_token())
                ttk.Button(actions, text=f'Save {label}', style='HayatAccent.TButton', command=self.save_token).pack(side='left', padx=6)
                if not row['connected']:
                    # Typing can start at once; Tab moves on to the next box and Enter connects from any of them.
                    target = first_empty or entry
                    self.after_idle(lambda widget=target: widget.winfo_exists() and widget.focus_set())
                if label.lower() == 'app password':
                    tk.Label(card, text=f"This is an app password you make for HAYAT on {row['name']}'s site, not your "
                                        f"{row['name']} login password. You can remove it there at any time.",
                             bg=p.PANEL, fg=p.GOLD_SOFT, font=('Segoe UI', 9), anchor='w', justify='left',
                             wraplength=820).pack(fill='x', pady=(4, 0))
                if row['token_page']:
                    ttk.Button(actions, text='Make a token ↗', style='Compact.Hayat.TButton',
                               command=lambda: webbrowser.open(row['token_page'])).pack(side='left')
                for index, step in enumerate(row['token_steps'], 1):
                    tk.Label(card, text=f'{index}. {step}', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9), anchor='w', justify='left',
                             wraplength=820).pack(fill='x')
        self.connect_note.pack(fill='x', pady=(2, 4))
        if row['needs_app'] and not setup_first:
            self.paint_app_setup(row)

    def paint_app_setup(self, row: dict) -> None:
        p = self.p
        frame = self.connect_tab
        self._section(frame, 'One-time app setup' + (' ✓ saved' if row['client_id_set'] else ' (do this first)'
                                                   if not any(m['key'] == 'token' for m in row['methods']) else ''))
        setup = tk.Frame(frame, bg=p.PANEL, padx=12, pady=8)
        setup.pack(fill='x')
        tk.Label(setup, text='Signing in needs an app registered in your own account. You do this once; HAYAT cannot do it for you.',
                 bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left', wraplength=820).pack(fill='x')
        for index, step in enumerate(row['app_steps'], 1):
            tk.Label(setup, text=f'{index}. {step}', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9), anchor='w', justify='left',
                     wraplength=820).pack(fill='x')
        line = tk.Frame(setup, bg=p.PANEL)
        line.pack(fill='x', pady=(6, 0))
        ttk.Button(line, text='Open the setup page ↗', style='Compact.Hayat.TButton',
                   command=lambda: webbrowser.open(row['app_page'])).pack(side='left')
        tk.Label(line, text='Client ID', bg=p.PANEL, fg=p.WHITE).pack(side='left', padx=(12, 4))
        self.client_id_entry = ttk.Entry(line, textvariable=self.client_id, width=34)
        self.client_id_entry.pack(side='left')
        self.client_id_entry.bind('<Return>', lambda _event: self.save_app())
        if row['browser_needs_secret']:
            tk.Label(line, text='Client secret', bg=p.PANEL, fg=p.WHITE).pack(side='left', padx=(10, 4))
            ttk.Entry(line, textvariable=self.client_secret, show='•', width=22).pack(side='left')
        ttk.Button(line, text='Save app', style='HayatAccent.TButton', command=self.save_app).pack(side='left', padx=6)
        if row.get('redirect_address'):
            copy = tk.Frame(setup, bg=p.PANEL)
            copy.pack(fill='x', pady=(6, 0))
            tk.Label(copy, text=f"Redirect / callback address to paste there: {row['redirect_address']}", bg=p.PANEL,
                     fg=p.GOLD_SOFT, font=('Segoe UI', 9)).pack(side='left')
            ttk.Button(copy, text='Copy', style='Compact.Hayat.TButton',
                       command=lambda: self.copy_text(row['redirect_address'], 'Redirect address copied.')).pack(side='left', padx=6)

    def note(self, text: str, colour: str | None = None) -> None:
        if getattr(self, 'connect_note', None) is not None and self.connect_note.winfo_exists():
            self.connect_note.configure(text=text, fg=colour or self.p.MUTED)

    def save_app(self) -> None:
        try:
            self.manager.set_app(self.selected, self.client_id.get(), self.client_secret.get())
        except Exception as exc:
            self.note(str(exc), DOWN)
            return
        self.client_id.set('')
        self.client_secret.set('')
        self.refresh()
        self.paint_connect()
        if getattr(self, '_sign_in_after_setup', '') == self.selected and any(
                method['key'] == 'browser' and method['ready'] for method in self.row()['methods']):
            self._sign_in_after_setup = ''
            self.start_browser()  # the sign-in the owner pressed for, now that the app exists
            return
        self.note('App saved. Now choose a way to sign in above.', UP)

    def start_device(self) -> None:
        key = self.selected
        self.note('Asking for a code…')
        self._run('device', lambda: self.manager.start_device(key))

    def done_device(self, value: dict | None, error: str | None) -> None:
        if error:
            self.note('Could not start: ' + error.split(': ', 1)[-1], DOWN)
            return
        p = self.p
        self.device = value
        self.device_deadline = time.monotonic() + value['expires_in']
        self.next_poll = time.monotonic() + value['interval']
        for child in self.code_area.winfo_children():
            child.destroy()
        tk.Label(self.code_area, text='Your code', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w').pack(fill='x', pady=(8, 0))
        line = tk.Frame(self.code_area, bg=p.PANEL)
        line.pack(fill='x')
        tk.Label(line, text=value['user_code'], bg=p.PANEL, fg=p.GOLD, font=('Consolas', 28, 'bold')).pack(side='left')
        ttk.Button(line, text='Copy code', style='Compact.Hayat.TButton', command=self.copy_code).pack(side='left', padx=10)
        ttk.Button(line, text='Open the sign-in page ↗', style='HayatAccent.TButton',
                   command=lambda: webbrowser.open(value['verification_uri'])).pack(side='left')
        ttk.Button(line, text='Cancel', style='Compact.Hayat.TButton', command=self.cancel_device).pack(side='left', padx=10)
        tk.Label(self.code_area, text=f"Go to {value['verification_uri']}, type the code, sign in and approve HAYAT.", bg=p.PANEL,
                 fg=p.WHITE, font=('Segoe UI', 9), anchor='w').pack(fill='x')
        self.countdown = tk.Label(self.code_area, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w')
        self.countdown.pack(fill='x')
        self.copy_code()
        webbrowser.open(value['verification_uri'])
        self.note('The sign-in page is open and the code is copied: paste it there and approve.', p.GOLD_SOFT)

    def copy_code(self) -> None:
        if self.device:
            self.clipboard_clear()
            self.clipboard_append(self.device['user_code'])

    def cancel_device(self) -> None:
        if self.device:
            self.manager.cancel_device(self.device['provider'])
        self.device = None
        self.paint_connect()
        self.note('Sign-in cancelled.')

    def done_poll(self, value: dict | None, error: str | None) -> None:
        if not self.device:
            return
        if error:
            self.device = None
            self.paint_connect()
            self.note(error.split(': ', 1)[-1], DOWN)
            return
        if value['state'] == 'waiting':
            self.device['interval'] = value['interval']
            return
        self.device = None
        self.refresh()
        self.paint_connect()
        self.note(f"Connected as {value['connected_as']}. Open \"Work with it\" to use it.", UP)
        self.bell()

    def start_browser(self) -> None:
        key = self.selected
        row = self.manager.status(key)  # fresh: an app saved elsewhere (chat, another window) counts at once
        method = next((item for item in row['methods'] if item['key'] == 'browser'), {})
        if not method.get('ready'):
            # The one-time app setup first, then the same button signs in (Save app starts it).
            self._sign_in_after_setup = key
            self.note(f"{row['name']} needs a one-time app registration before browser sign-in: follow the steps in "
                      "One-time app setup below, paste the Client ID (and secret), and press Save app; the sign-in "
                      "then opens by itself.", self.p.GOLD_SOFT)
            entry = getattr(self, 'client_id_entry', None)
            if entry is not None and entry.winfo_exists():
                entry.focus_set()
            return
        field = ((row.get('token_fields') or [{'name': 'server'}])[0])['name']
        server = self.token_values[field].get().strip() if row.get('dynamic_app') and field in self.token_values else ''
        if row.get('dynamic_app') and not server:
            self.note('Type your handle first, such as name.bsky.social.' if field == 'handle'
                      else 'Type your server first, such as mastodon.social.', DOWN)
            return
        self.browser_key = key
        self.note('Your browser is open at the sign-in page. Sign in and approve there; it comes back to HAYAT, which '
                  'reads the account to confirm it and keeps the sign-in safely (up to five minutes)…', self.p.GOLD_SOFT)
        self._run('browser', lambda: self.manager.sign_in_browser(key, server=server))
        self._browser_tries = 0
        self.after(200, self.paint_browser_wait)

    def paint_browser_wait(self) -> None:
        """While the browser sign-in waits: open it again, copy it into any tab, paste the address back, or cancel."""
        key = self.browser_key
        area = getattr(self, 'browser_area', None)
        if self._closed or not key or key != self.selected or area is None or not area.winfo_exists():
            return
        link = self.manager.browser_link(key)
        if not link:
            self._browser_tries += 1
            if self._browser_tries < 25 and 'browser' in self._busy:
                self.after(200, self.paint_browser_wait)
            return
        p = self.p
        for child in area.winfo_children():
            child.destroy()
        name = self.row().get('name', key)
        tk.Label(area, text=f'Waiting for you to approve on {name}…', bg=p.PANEL, fg=p.GOLD_SOFT,
                 font=('Segoe UI Semibold', 10), anchor='w').pack(fill='x', pady=(8, 2))
        line = tk.Frame(area, bg=p.PANEL)
        line.pack(fill='x')
        ttk.Button(line, text='Open it again ↗', style='Compact.Hayat.TButton',
                   command=lambda: webbrowser.open(link)).pack(side='left')
        ttk.Button(line, text='Copy the sign-in link', style='Compact.Hayat.TButton',
                   command=lambda: self.copy_text(link, 'Sign-in link copied: paste it into a tab of the browser where you '
                                                        'are signed in to ' + name + '.')).pack(side='left', padx=6)
        ttk.Button(line, text='Cancel', style='Compact.Hayat.TButton', command=self.cancel_browser).pack(side='left')
        tk.Label(area, text='Approved, but the tab says it cannot reach this computer (127.0.0.1 or localhost)? Copy the whole '
                            'address from that tab\'s address bar and paste it here:',
                 bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left', wraplength=820).pack(fill='x', pady=(6, 2))
        back = tk.Frame(area, bg=p.PANEL)
        back.pack(fill='x')
        entry = ttk.Entry(back, textvariable=self.return_address, width=70)
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<Return>', lambda _event: self.finish_from_address())
        ttk.Button(back, text='Finish sign-in', style='HayatAccent.TButton', command=self.finish_from_address).pack(side='left', padx=6)

    def copy_text(self, text: str, message: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.note(message, self.p.GOLD_SOFT)

    def finish_from_address(self) -> None:
        address = self.return_address.get().strip()
        if not address or not self.browser_key:
            return
        try:
            ok = self.manager.finish_browser_from_address(self.browser_key, address)
        except Exception as exc:
            self.note(str(exc), DOWN)
            return
        self.return_address.set('')
        self.note('Got it: exchanging the code and reading the account to confirm…' if ok
                  else 'That address says the sign-in did not finish; HAYAT shows why in a moment.', self.p.GOLD_SOFT)

    def cancel_browser(self) -> None:
        if self.browser_key:
            self.manager.cancel_browser(self.browser_key)

    def done_browser(self, value: dict | None, error: str | None) -> None:
        self.browser_key = ''
        self.return_address.set('')
        if error:
            self.refresh()
            self.paint_connect()
            self.note('Not connected: ' + error.split(': ', 1)[-1], DOWN)
            return
        self.refresh()
        self.paint_connect()
        self.note(f"Connected and checked: {value['name']} answered as {value['connected_as']}.", UP)
        self.bell()

    def save_token(self) -> None:
        key, token = self.selected, self.token.get()
        extras = {name: variable.get() for name, variable in self.token_values.items()}
        self.token.set('')
        self.note(f"Checking the {self.row().get('token_label', 'token')} with one read…")
        self._run('token', lambda: self.manager.save_token(key, token, extras))

    def save_webhook(self) -> None:
        key, url = self.selected, self.webhook.get()
        self.webhook.set('')
        try:
            value = self.manager.save_webhook(key, url)
        except Exception as exc:
            self.note('Not saved: ' + str(exc), DOWN)
            return
        self.refresh()
        self.paint_connect()
        self.note(f"Saved: {value['name']} can now post to that channel, once you confirm each message.", UP)

    def done_token(self, value: dict | None, error: str | None) -> None:
        if error:
            self.note('Not saved: ' + error.split(': ', 1)[-1], DOWN)
            return
        self.refresh()
        self.paint_connect()
        self.note(f"Connected and checked: {value['name']} answered as {value['connected_as']}.", UP)

    def test(self) -> None:
        key = self.selected
        self.note('Checking: one read of whose account this is…')
        self._run('test', lambda: self.manager.check(key))

    def done_test(self, value: dict | None, error: str | None) -> None:
        self.refresh()
        self.paint_connect()
        if error:
            self.note('Could not check: ' + error.split(': ', 1)[-1], DOWN)
        elif value.get('check_ok'):
            self.note(f"Working: {value['name']} answered as {value['connected_as']}.", UP)
        else:
            self.note(f"Not working: {value.get('check_error') or 'no answer'}. Sign in again below.", DOWN)

    def done_check_all(self, value: dict | None, error: str | None) -> None:
        self.refresh()
        self.paint_connect()

    def open_telegram_bot(self) -> None:
        self._run('telegram_bot', self.manager.telegram_bot_link)

    def done_telegram_bot(self, value: str | None, error: str | None) -> None:
        if error or not value:
            self.note('Could not read the bot: ' + (error or 'no username').split(': ', 1)[-1], DOWN)
            return
        webbrowser.open(value)
        self.note('Telegram opens your bot: tap Start, then press Link my chat.', self.p.GOLD_SOFT)

    def link_telegram(self) -> None:
        self.note('Looking for the chat that tapped Start…')
        self._run('telegram_link', self.manager.telegram_link_chat)

    def done_telegram_link(self, value: dict | None, error: str | None) -> None:
        if error:
            self.note(error.split(': ', 1)[-1], DOWN)
            return
        self.paint_connect()
        self.note(f"Linked: HAYAT's Telegram messages now go to {value['linked_chat']}.", UP)

    def sign_out(self) -> None:
        key = self.selected
        self._run('sign_out', lambda: self.manager.sign_out(key))

    def done_sign_out(self, value: dict | None, error: str | None) -> None:
        self.refresh()
        self.paint_connect()
        self.note('Signed out: the token was deleted' + (' and revoked.' if not error else '.') if not error else error, None)

    # ------------------------------------------------------------------ work tab
    def paint_work(self, reset: bool = False) -> None:
        row = self.row()
        for child in self.read_row.winfo_children():
            child.destroy()
        if not row['connected']:
            self.work_note.configure(text=f"Connect {row['name']} first (the Connect tab). Then its mail, calendar, files or other items "
                                          'can be read here, and things sent once you confirm.')
        else:
            self.work_note.configure(text=f"Signed in as {row['connected_as']}. Read with a button; double-click a result to open it.")
        for operation in row['operations']:
            if operation['write'] or operation['fields']:
                continue
            button = ttk.Button(self.read_row, text=operation['label'], style='Compact.Hayat.TButton',
                                command=lambda name=operation['key']: self.read(name))
            button.pack(side='left', padx=(0, 6))
            button.state(['!disabled' if row['connected'] else 'disabled'])
        searchable = [operation for operation in row['operations'] if not operation['write'] and operation['fields']]
        for operation in searchable:
            variable = self.field_vars.setdefault(f"read:{row['key']}:{operation['key']}", StringVar(self, ''))
            entry = ttk.Entry(self.read_row, textvariable=variable, width=22)
            entry.pack(side='left', padx=(8, 4))
            button = ttk.Button(self.read_row, text=operation['label'], style='Compact.Hayat.TButton',
                                command=lambda name=operation['key'], variable=variable, field=operation['fields'][0]['name']:
                                self.read(name, {field: variable.get()}))
            button.pack(side='left')
            button.state(['!disabled' if row['connected'] else 'disabled'])
        writes = [operation for operation in row['operations'] if operation['write']]
        self.write_box.configure(values=[operation['label'] for operation in writes])
        if reset or self.write_op.get() not in [operation['label'] for operation in writes]:
            self.write_op.set(writes[0]['label'] if writes else '')
            self.results.delete(*self.results.get_children())
            self.result_detail.configure(text='')
        self.prepare_button.state(['!disabled' if row['connected'] and writes else 'disabled'])
        self.build_form()

    def build_form(self) -> None:
        p = self.p
        for child in self.form.winfo_children():
            child.destroy()
        row = self.row()
        operation = next((item for item in row['operations'] if item['write'] and item['label'] == self.write_op.get()), None)
        if not operation:
            self.write_note.configure(text=f"{row['name']} has nothing to send here; it is read-only." if row['connected'] else '')
            return
        for index, field in enumerate(operation['fields']):
            tk.Label(self.form, text=field['label'] + ('' if field['required'] else ' (optional)'), bg=p.BLACK, fg=p.WHITE,
                     anchor='w').grid(row=index, column=0, sticky='nw', padx=(0, 8), pady=2)
            variable = self.field_vars.setdefault(f"{row['key']}:{operation['key']}:{field['name']}", StringVar(self, ''))
            if field['name'] in ('body', 'text'):
                box = tk.Text(self.form, height=4, wrap='word', bg=p.PANEL, fg=p.WHITE, insertbackground=p.WHITE, font=('Segoe UI', 10))
                box.insert('1.0', variable.get())
                box.bind('<KeyRelease>', lambda _event, box=box, variable=variable: variable.set(box.get('1.0', 'end').strip()))
                box.grid(row=index, column=1, sticky='ew', pady=2)
            else:
                ttk.Entry(self.form, textvariable=variable).grid(row=index, column=1, sticky='ew', pady=2)
        self.form.columnconfigure(1, weight=1)
        self.write_note.configure(text='Preparing sends nothing: a confirmation window shows every value, and it is sent only when you '
                                       'confirm there (or say "confirm action N" at this computer).', fg=p.MUTED)

    def read(self, operation: str, params: dict | None = None) -> None:
        key = self.selected
        self.work_note.configure(text='Reading…', fg=self.p.MUTED)
        self._run('read', lambda: self.manager.read(key, operation, params or {}))

    def done_read(self, value: dict | None, error: str | None) -> None:
        tree = self.results
        tree.delete(*tree.get_children())
        if error:
            self.work_note.configure(text='Could not read: ' + error.split(': ', 1)[-1], fg=DOWN)
            return
        self.rows_data = value['rows']
        for index, item in enumerate(value['rows']):
            tree.insert('', 'end', iid=str(index), text=('● ' if item.get('unread') else '') + str(item.get('title', ''))[:120],
                        values=(str(item.get('detail', ''))[:80], str(item.get('when', ''))[:25].replace('T', ' ')),
                        tags=('unread',) if item.get('unread') else ())
        self.work_note.configure(text=f"{value['label']}: {len(value['rows'])} item{'s' if len(value['rows']) != 1 else ''} · read "
                                      f"{value['fetched_at'][11:16]} UTC" + (' · ● = unread' if any(item.get('unread') for item in value['rows']) else ''),
                                 fg=self.p.GOLD_SOFT)

    def result_selected(self) -> None:
        selection = self.results.selection()
        if selection:
            item = self.rows_data[int(selection[0])]
            self.result_detail.configure(text='\n'.join(str(part) for part in (item.get('title'), item.get('detail'), item.get('extra'),
                                                                               item.get('link')) if part))

    def open_result(self) -> None:
        selection = self.results.selection()
        if selection and self.rows_data[int(selection[0])].get('link'):
            webbrowser.open(self.rows_data[int(selection[0])]['link'])

    def prepare(self) -> None:
        row = self.row()
        operation = next((item for item in row['operations'] if item['write'] and item['label'] == self.write_op.get()), None)
        if not operation:
            return
        params = {field['name']: self.field_vars[f"{row['key']}:{operation['key']}:{field['name']}"].get() for field in operation['fields']}
        gate = getattr(self.runtime, 'confirmations', None)
        if gate is None:
            self.write_note.configure(text='The confirmation window is not available in this session, so nothing can be sent.', fg=DOWN)
            return
        from app.accounts import AccountTools
        try:
            proposal = AccountTools(self.manager, gate).prepare(row['key'], operation['key'], params)
        except Exception as exc:
            self.write_note.configure(text='Not prepared: ' + str(exc), fg=DOWN)
            return
        self.write_note.configure(text=f"Prepared as action #{proposal['action_id']}. Check every value in the confirmation window and confirm "
                                       f"there; nothing has been sent yet.", fg=self.p.GOLD_SOFT)

    # ------------------------------------------------------------------ closing
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.device:
            self.manager.cancel_device(self.device['provider'])
        try:
            self.after_cancel(self._tick_id)
        except (tk.TclError, AttributeError):
            pass
        self.destroy()
