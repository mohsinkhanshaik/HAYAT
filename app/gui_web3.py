"""Web3 & agentic web window: networks with live blocks, an explorer, DeFi, and the Web 4.0 check.

* Networks: a card per chain (Bitcoin fees, mining, difficulty, Lightning; Ethereum, Optimism and
  zkSync gas and load), a fee history drawn while the window is open, and live block strips; a
  click on a block opens it in the explorer.
* Explorer: an address, transaction, block or ENS name; double-click a row to follow it, with back
  and forward like a browser.
* DeFi: value locked per chain as bars (hover for the figure), and the largest stablecoins.
* Agentic web (Web 4.0): what a site publishes for AI agents, and which AI crawlers it allows.

Read-only throughout: no wallet, no signing, no transaction, no login. Requests go one at a time
from a background thread, refreshed only for the tab on screen.
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from datetime import UTC, datetime
from tkinter import ttk
from typing import Any

from app.gui_intelligence_parts import blend
from app.gui_resources import StringVar
from app.gui_windows import place_over

UP, DOWN = '#4ADE80', '#F05D5E'
BTC_COLOUR, ETH_COLOUR = '#F7931A', '#8B9CFF'
REFRESH = {'networks': 30, 'blocks': 20, 'defi': 600, 'watch': 120}
EVM_CHAINS = (('ethereum', 'Ethereum'), ('optimism', 'Optimism'), ('zksync', 'zkSync'))
SHORTCUTS = 'Ctrl+1–5 tabs · Ctrl+L explorer search · Alt+← / Alt+→ back and forward · F5 refresh'
EXAMPLES = ('vitalik.eth', 'bitcoin block 900000', 'ethereum block 20000000')
SITES = ('stripe.com', 'github.com', 'nytimes.com')
READ_ONLY = ('Read-only. HAYAT reads public chain data and public website files. It has no wallet, never signs, never sends a '
             'transaction and never logs in.')


def ago(moment: Any) -> str:
    try:
        seconds = (datetime.now(UTC) - datetime.fromisoformat(str(moment).replace('Z', '+00:00'))).total_seconds()
    except ValueError:
        return ''
    if seconds < 90:
        return f'{max(0, int(seconds))} s ago'
    if seconds < 5400:
        return f'{int(seconds // 60)} min ago'
    if seconds < 172800:
        return f'{int(seconds // 3600)} h ago'
    return f'{int(seconds // 86400)} days ago'


def short(text: Any, keep: int = 10) -> str:
    text = str(text or '')
    return text if len(text) <= keep * 2 + 1 else f'{text[:keep]}…{text[-6:]}'


def dollars(value: Any) -> str:
    from app.web3_chat import dollars as format_dollars
    return format_dollars(value)


class Web3Center(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p = gui, type(gui)
        self.web3, self.agent_web = gui.runtime.web3, gui.runtime.agent_web
        p = self.p
        self._closed = False
        self._jobs: queue.Queue = queue.Queue()
        self._results: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._due = {key: 0.0 for key in REFRESH}
        self.networks_data: dict | None = None
        self.block_data: dict[str, list] = {'bitcoin': [], 'ethereum': [], 'optimism': [], 'zksync': []}
        self.evm_chain = StringVar(self, 'ethereum')
        self.watch_data: dict | None = None
        self.site_history: list = []
        self._seen_blocks: dict[str, int] = {}
        self.fee_history: list[tuple[float, float | None, float | None]] = []
        self.history: list[str] = []
        self.position = -1
        self.lookup_data: dict | None = None
        self.defi_data: dict | None = None
        self.inspect_data: dict | None = None
        self.query = StringVar(self, '')
        self.chain = StringVar(self, 'auto')
        self.site = StringVar(self, '')
        self.status = StringVar(self, 'Reading the networks…')
        self.title('HAYAT · Web3 & agentic web')
        self.configure(bg=p.BLACK)
        self.minsize(980, 640)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        for number in range(1, 6):
            self.bind(f'<Control-Key-{number}>', lambda _event, number=number: self.tabs.select(number - 1))
        self.bind('<Control-l>', lambda _event: (self.tabs.select(1), self.search.focus_set(), self.search.select_range(0, 'end')))
        self.bind('<Alt-Left>', lambda _event: self.step(-1))
        self.bind('<Alt-Right>', lambda _event: self.step(1))
        self.bind('<F5>', lambda _event: self.refresh())
        self._build()
        if getattr(self.web3, 'path', None) is not None:
            self.show_history(self.web3.sites())  # local file: the sites checked before
        place_over(self, gui.root, (1480, 920))
        self.deiconify()
        self._worker = threading.Thread(target=self._work, name='hayat-web3', daemon=True)
        self._worker.start()
        self._tick_id = self.after(50, self._tick)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        p = self.p
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill='x')
        words = tk.Frame(header, bg=p.BLACK)
        words.pack(side='left', fill='x', expand=True)
        tk.Label(words, text='Web3 & agentic web', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 19), anchor='w').pack(fill='x')
        tk.Label(words, text='Blockchain networks and live blocks · explorer for addresses, transactions, blocks and ENS · DeFi · '
                             'how ready a website is for AI agents (Web 4.0)', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9),
                 anchor='w').pack(fill='x')
        tk.Label(words, text='Keys: ' + SHORTCUTS, bg=p.BLACK, fg=blend(p.MUTED, p.BLACK, .35), font=('Segoe UI', 8),
                 anchor='w').pack(fill='x')
        controls = tk.Frame(header, bg=p.BLACK)
        controls.pack(side='right')
        tk.Label(controls, textvariable=self.status, bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9)).pack(side='left', padx=10)
        ttk.Button(controls, text='Refresh', style='HayatAccent.TButton', command=self.refresh).pack(side='left')
        tk.Label(self, text=READ_ONLY, bg=blend(p.GOLD, p.BLACK, .85), fg=p.GOLD_SOFT, font=('Segoe UI', 9), anchor='w',
                 padx=16, pady=5).pack(fill='x', padx=16)
        self.footer = tk.Label(self, text='Sources: mempool.space · Blockscout (Ethereum, zkSync) · Optimism explorer · DefiLlama · '
                                          "the sites' own public files", bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w',
                               padx=14, pady=5)
        self.footer.pack(side='bottom', fill='x')
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=16, pady=8)
        self.tabs.add(self._build_networks(self.tabs), text='Networks & blocks')
        self.tabs.add(self._build_explorer(self.tabs), text='Explorer')
        self.tabs.add(self._build_defi(self.tabs), text='DeFi')
        self.tabs.add(self._build_agent_web(self.tabs), text='Agentic web (Web 4.0)')
        self.tabs.add(self._build_watched(self.tabs), text='★ Watched addresses')
        self.tabs.bind('<<NotebookTabChanged>>', lambda _event: self.refresh(visible_only=True))

    def _section(self, parent: Any, text: str) -> None:
        tk.Label(parent, text=text, bg=self.p.BLACK, fg=self.p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x', pady=(8, 2))

    def _build_networks(self, parent: Any) -> tk.Frame:
        p = self.p
        frame = tk.Frame(parent, bg=p.BLACK)
        self.cards = tk.Frame(frame, bg=p.BLACK)
        self.cards.pack(fill='x', pady=(8, 0))
        self.card_widgets: dict[str, dict[str, Any]] = {}
        middle = tk.Frame(frame, bg=p.BLACK)
        middle.pack(fill='x')
        left = tk.Frame(middle, bg=p.BLACK)
        left.pack(side='left', fill='both', expand=True)
        self._section(left, 'Fees while this window is open')
        self.fee_chart = tk.Canvas(left, bg=p.PANEL, highlightthickness=0, height=120)
        self.fee_chart.pack(fill='x')
        self.fee_chart.bind('<Configure>', lambda _event: self.draw_fees())
        right = tk.Frame(middle, bg=p.BLACK, padx=12)
        right.pack(side='left', fill='both')
        self._section(right, 'Bitcoin mining pools, last week')
        self.pools = tk.Canvas(right, bg=p.PANEL, highlightthickness=0, height=120, width=360)
        self.pools.pack(fill='x')
        self.strips: dict[str, tk.Canvas] = {}
        for chain, label in (('bitcoin', 'Bitcoin blocks, newest first (click one to open it)'),
                             ('evm', 'Blocks, newest first (click one to open it)')):
            if chain == 'evm':
                heading = tk.Frame(frame, bg=p.BLACK)
                heading.pack(fill='x', pady=(8, 2))
                for key, name in EVM_CHAINS:
                    ttk.Radiobutton(heading, text=name, value=key, variable=self.evm_chain, style='Segment.Toolbutton',
                                    command=self.evm_switched).pack(side='left', padx=(0, 4))
                tk.Label(heading, text=label, bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11)).pack(side='left', padx=8)
            else:
                self._section(frame, label)
            strip = tk.Canvas(frame, bg=p.PANEL, highlightthickness=0, height=86, cursor='hand2')
            strip.pack(fill='x')
            strip.bind('<Configure>', lambda _event, chain=chain: self.draw_blocks(chain))
            strip.bind('<Button-1>', lambda event, chain=chain: self.block_clicked(chain, event))
            self.strips[chain] = strip
        self.refused_note = tk.Label(frame, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8), anchor='w', justify='left',
                                     wraplength=1300)
        self.refused_note.pack(fill='x', pady=(8, 0))
        return frame

    def _build_explorer(self, parent: Any) -> tk.Frame:
        p = self.p
        frame = tk.Frame(parent, bg=p.BLACK)
        row = tk.Frame(frame, bg=p.BLACK)
        row.pack(fill='x', pady=(10, 4))
        self.back_button = ttk.Button(row, text='◀', width=3, style='Compact.Hayat.TButton', command=lambda: self.step(-1))
        self.back_button.pack(side='left')
        self.forward_button = ttk.Button(row, text='▶', width=3, style='Compact.Hayat.TButton', command=lambda: self.step(1))
        self.forward_button.pack(side='left', padx=(4, 8))
        self.search = ttk.Entry(row, textvariable=self.query)
        self.search.pack(side='left', fill='x', expand=True)
        self.search.bind('<Return>', lambda _event: self.look_up())
        ttk.Combobox(row, textvariable=self.chain, values=['auto', 'bitcoin', 'ethereum', 'optimism', 'zksync'], width=10,
                     state='readonly').pack(side='left', padx=6)
        ttk.Button(row, text='Look up', style='HayatAccent.TButton', command=self.look_up).pack(side='left')
        hints = tk.Frame(frame, bg=p.BLACK)
        hints.pack(fill='x')
        tk.Label(hints, text='An address, transaction hash, ENS name or "bitcoin block N". Try:', bg=p.BLACK, fg=p.MUTED,
                 font=('Segoe UI', 8)).pack(side='left')
        for example in EXAMPLES:
            link = tk.Label(hints, text=example, bg=p.BLACK, fg=p.GOLD_SOFT, font=('Segoe UI', 8, 'underline'), cursor='hand2')
            link.pack(side='left', padx=6)
            link.bind('<Button-1>', lambda _event, example=example: self.look_up(example))
        self.summary = tk.Label(frame, text='Look something up to see it here.', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 10),
                                anchor='nw', justify='left', padx=12, pady=10)
        self.summary.pack(fill='x', pady=(8, 6))
        self.summary.bind('<Configure>', lambda event: self.summary.configure(wraplength=max(300, event.width - 24)))
        table = tk.Frame(frame, bg=p.BLACK)
        table.pack(fill='both', expand=True)
        self.rows = ttk.Treeview(table, columns=('when', 'amount', 'detail'), selectmode='browse')
        for key, label, width, anchor in (('#0', 'Hash or address', 330, 'w'), ('when', 'When', 150, 'w'), ('amount', 'Amount', 170, 'e'),
                                          ('detail', 'Detail', 380, 'w')):
            self.rows.heading(key, text=label)
            self.rows.column(key, width=width, anchor=anchor, stretch=key in ('#0', 'detail'))
        self.rows.tag_configure('in', foreground=UP)
        self.rows.tag_configure('out', foreground=DOWN)
        self.rows.tag_configure('token', foreground=self.p.GOLD_SOFT)
        scroll = ttk.Scrollbar(table, command=self.rows.yview)
        self.rows.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.rows.pack(side='left', fill='both', expand=True)
        self.rows.bind('<Double-1>', lambda _event: self.follow())
        actions = tk.Frame(frame, bg=p.BLACK)
        actions.pack(fill='x', pady=(6, 0))
        ttk.Button(actions, text='Open selected', style='Compact.Hayat.TButton', command=self.follow).pack(side='left')
        ttk.Button(actions, text='Copy selected', style='Compact.Hayat.TButton', command=self.copy_selected).pack(side='left', padx=6)
        self.watch_button = ttk.Button(actions, text='★ Watch this address', style='Compact.Hayat.TButton', command=self.watch_current)
        self.watch_button.pack(side='right')
        self.watch_button.state(['disabled'])
        tk.Label(actions, text='Double-click a row to follow it.', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left', padx=8)
        return frame

    def _build_defi(self, parent: Any) -> tk.Frame:
        p = self.p
        frame = tk.Frame(parent, bg=p.BLACK)
        self.defi_note = tk.Label(frame, text='Reading DeFi totals…', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w')
        self.defi_note.pack(fill='x', pady=(10, 4))
        body = tk.PanedWindow(frame, orient='horizontal', bg=p.BLACK, sashwidth=6, bd=0)
        body.pack(fill='both', expand=True)
        left = tk.Frame(body, bg=p.BLACK)
        self._section(left, 'Value locked per chain')
        self.tvl = tk.Canvas(left, bg=p.PANEL, highlightthickness=0)
        self.tvl.pack(fill='both', expand=True)
        self.tvl.bind('<Configure>', lambda _event: self.draw_tvl())
        self.tvl.bind('<Motion>', self.tvl_hover)
        self.tvl.bind('<Leave>', lambda _event: self.tvl.delete('hover'))
        right = tk.Frame(body, bg=p.BLACK)
        self._section(right, 'Largest stablecoins')
        self.stables = ttk.Treeview(right, columns=('supply', 'mechanism', 'chains'), selectmode='browse')
        for key, label, width, anchor in (('#0', 'Stablecoin', 170, 'w'), ('supply', 'Supply', 110, 'e'), ('mechanism', 'Backed by', 120, 'w'),
                                          ('chains', 'Chains', 60, 'e')):
            self.stables.heading(key, text=label)
            self.stables.column(key, width=width, anchor=anchor)
        self.stables.pack(fill='both', expand=True)
        self._section(right, 'Decentralised exchanges · 24 h volume')
        self.dex_note = tk.Label(right, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8), anchor='w')
        self.dex_note.pack(fill='x')
        self.dexes = ttk.Treeview(right, columns=('volume', 'change', 'chains'), selectmode='browse', height=8)
        for key, label, width, anchor in (('#0', 'Exchange', 170, 'w'), ('volume', '24 h volume', 110, 'e'), ('change', 'vs day before', 100, 'e'),
                                          ('chains', 'Chains', 60, 'e')):
            self.dexes.heading(key, text=label)
            self.dexes.column(key, width=width, anchor=anchor)
        self.dexes.tag_configure('up', foreground=UP)
        self.dexes.tag_configure('down', foreground=DOWN)
        self.dexes.pack(fill='both', expand=True)
        body.add(left, minsize=420, stretch='always')
        body.add(right, minsize=380, width=480, stretch='never')
        return frame

    def _build_agent_web(self, parent: Any) -> tk.Frame:
        p = self.p
        frame = tk.Frame(parent, bg=p.BLACK)
        tk.Label(frame, text='"Web 4.0" has no agreed definition. Here it means the agentic web: what a website publishes for AI agents, '
                             'and which AI crawlers it lets in. These are public conventions and proposals, not a standard.',
                 bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left', wraplength=1300).pack(fill='x', pady=(10, 4))
        row = tk.Frame(frame, bg=p.BLACK)
        row.pack(fill='x', pady=(2, 6))
        self.site_entry = ttk.Entry(row, textvariable=self.site)
        self.site_entry.pack(side='left', fill='x', expand=True)
        self.site_entry.bind('<Return>', lambda _event: self.inspect())
        ttk.Button(row, text='Check site', style='HayatAccent.TButton', command=self.inspect).pack(side='left', padx=6)
        for example in SITES:
            link = tk.Label(row, text=example, bg=p.BLACK, fg=p.GOLD_SOFT, font=('Segoe UI', 8, 'underline'), cursor='hand2')
            link.pack(side='left', padx=4)
            link.bind('<Button-1>', lambda _event, example=example: self.inspect(example))
        body = tk.PanedWindow(frame, orient='horizontal', bg=p.BLACK, sashwidth=6, bd=0)
        body.pack(fill='both', expand=True)
        left = tk.Frame(body, bg=p.BLACK)
        top = tk.Frame(left, bg=p.BLACK)
        top.pack(fill='x')
        self.ring = tk.Canvas(top, bg=p.BLACK, highlightthickness=0, width=130, height=130)
        self.ring.pack(side='left')
        self.verdict = tk.Label(top, text='Check a site to see what it publishes for AI agents.', bg=p.BLACK, fg=p.WHITE,
                                font=('Segoe UI', 10), anchor='w', justify='left', wraplength=420)
        self.verdict.pack(side='left', fill='x', expand=True, padx=8)
        self.checks = ttk.Treeview(left, columns=('note',), selectmode='browse', height=12)
        self.checks.heading('#0', text='Published for agents')
        self.checks.heading('note', text='Found')
        self.checks.column('#0', width=260)
        self.checks.column('note', width=300)
        self.checks.tag_configure('yes', foreground=UP)
        self.checks.tag_configure('no', foreground=p.MUTED)
        self.checks.pack(fill='both', expand=True, pady=(6, 0))
        self.checks.bind('<<TreeviewSelect>>', lambda _event: self.check_selected())
        right = tk.Frame(body, bg=p.BLACK)
        self._section(right, 'AI crawlers (robots.txt, site root)')
        self.crawlers = ttk.Treeview(right, columns=('owner', 'purpose', 'access'), selectmode='none', height=12)
        for key, label, width in (('#0', 'Agent', 150), ('owner', 'Run by', 100), ('purpose', 'For', 140), ('access', 'Access', 120)):
            self.crawlers.heading(key, text=label)
            self.crawlers.column(key, width=width)
        self.crawlers.tag_configure('allowed', foreground=UP)
        self.crawlers.tag_configure('blocked', foreground=DOWN)
        self.crawlers.pack(fill='x')
        self._section(right, 'What it means · preview')
        self.preview = tk.Text(right, height=10, wrap='word', bg=p.PANEL, fg=p.WHITE, font=('Consolas', 9), relief='flat',
                               padx=8, pady=6)
        self.preview.pack(fill='both', expand=True)
        self.preview.configure(state='disabled')
        tk.Label(left, text='Checked before (click one to check it again)', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8),
                 anchor='w').pack(fill='x', pady=(8, 0))
        self.history_tree = ttk.Treeview(left, columns=('score', 'position', 'at'), selectmode='browse', height=4)
        for key, label, width in (('#0', 'Site', 180), ('score', 'Surfaces', 70), ('position', 'AI crawlers', 260), ('at', 'Checked (UTC)', 110)):
            self.history_tree.heading(key, text=label)
            self.history_tree.column(key, width=width, stretch=key == 'position')
        self.history_tree.pack(fill='x')
        self.history_tree.bind('<<TreeviewSelect>>', lambda _event: self.history_selected())
        body.add(left, minsize=480, stretch='always')
        body.add(right, minsize=420, width=560, stretch='never')
        return frame

    # ------------------------------------------------------------------ data
    def refresh(self, visible_only: bool = False) -> None:
        for kind in REFRESH:
            self._due[kind] = 0.0

    def _work(self) -> None:
        while not self._closed:
            try:
                kind, call = self._jobs.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                self._results.put((kind, call(), None))
            except Exception as exc:  # shown in the window, never raised into Tk
                self._results.put((kind, None, f'{type(exc).__name__}: {exc}'))

    def _submit(self, kind: str, call: Any) -> None:
        if kind not in self._pending:
            self._pending.add(kind)
            self._jobs.put((kind, call))

    def _schedule(self) -> None:
        now = time.monotonic()
        tab = self.tabs.index('current') if self.tabs.tabs() else 0
        web3 = self.web3
        jobs = {}
        if tab == 0:
            jobs['networks'] = web3.networks
            evm = self.evm_chain.get()
            jobs['blocks'] = lambda: {chain: web3.blocks(chain, 12)['blocks'] for chain in ('bitcoin', evm)}
        if tab == 2:
            jobs['defi'] = lambda: web3.defi(20)
        if tab == 4 and getattr(web3, 'path', None):
            jobs['watch'] = lambda: web3.watchlist('check')
        for kind, call in jobs.items():
            if kind not in self._pending and now >= self._due[kind]:
                self._due[kind] = now + REFRESH[kind]
                self._submit(kind, call)

    def _tick(self) -> None:
        if self._closed:
            return
        self._schedule()
        while not self._results.empty():
            kind, value, error = self._results.get_nowait()
            self._pending.discard(kind)
            if error:
                if kind in ('lookup', 'inspect', 'watch'):
                    getattr(self, 'failed_' + kind)(error)
                else:
                    self.footer.configure(text=f'{kind}: {error[:200]} · retrying at the next refresh', fg=DOWN)
                continue
            getattr(self, 'show_' + kind)(value)
        self._tick_id = self.after(200, self._tick)

    # ------------------------------------------------------------------ networks
    def show_networks(self, value: dict) -> None:
        self.networks_data = value
        p = self.p
        for column, item in enumerate(value['networks']):
            widgets = self.card_widgets.get(item['key'])
            if widgets is None:
                card = tk.Frame(self.cards, bg=p.PANEL, padx=12, pady=8)
                card.grid(row=0, column=column, sticky='nsew', padx=(0, 8))
                self.cards.columnconfigure(column, weight=1, uniform='cards')
                colour = BTC_COLOUR if item['key'] == 'bitcoin' else ETH_COLOUR
                tk.Label(card, text=item['name'], bg=p.PANEL, fg=colour, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x')
                big = tk.Label(card, text='', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 18), anchor='w')
                big.pack(fill='x')
                facts = tk.Label(card, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left')
                facts.pack(fill='x')
                bar = tk.Canvas(card, bg=p.PANEL, highlightthickness=0, height=16)
                bar.pack(fill='x', pady=(6, 0))
                widgets = self.card_widgets[item['key']] = {'big': big, 'facts': facts, 'bar': bar}
            if item['key'] == 'bitcoin':
                widgets['big'].configure(text=f"block {item['block_height']:,}")
                lines = [f"Fees {item['fee_fast_sat_vb']} fastest · {item['fee_1h_sat_vb']} in 1 h · {item['fee_economy_sat_vb']} economy (sat/vB)",
                         f"{item['mempool_transactions']:,} transactions waiting"]
                if item.get('hashrate_eh_s'):
                    lines.append(f"Hashrate {item['hashrate_eh_s']:,} EH/s")
                if item.get('lightning'):
                    light = item['lightning']
                    lines.append(f"Lightning {light['nodes']:,} nodes · {light['channels']:,} channels · {light['capacity_btc']:,} BTC "
                                 f"({light['as_of']})")
                difficulty = item.get('difficulty')
                if difficulty:
                    lines.append(f"Next difficulty {difficulty['expected_change_pct']:+.2f}% in {difficulty['remaining_blocks']:,} blocks")
                    self.draw_bar(widgets['bar'], difficulty['progress_pct'], f"epoch {difficulty['progress_pct']:.0f}% done", BTC_COLOUR)
            else:
                widgets['big'].configure(text=f"{item['gas_average_gwei']} gwei")
                lines = [f"Gas {item['gas_slow_gwei']} slow · {item['gas_fast_gwei']} fast",
                         f"A block every {item['block_time_seconds']} s"
                         + (f" · {item['transactions_today']:,} transactions today" if item.get('transactions_today') else ''),
                         f"{item['total_addresses']:,} addresses" if item.get('total_addresses') else '']
                load = item.get('utilization_pct')
                if load is not None:
                    self.draw_bar(widgets['bar'], load, f'{load:.1f}% of capacity used', ETH_COLOUR)
            widgets['facts'].configure(text='\n'.join(line for line in lines if line))
        bitcoin = next((item for item in value['networks'] if item['key'] == 'bitcoin'), None)
        ethereum = next((item for item in value['networks'] if item['key'] == 'ethereum'), None)
        self.fee_history.append((time.time(), bitcoin and bitcoin['fee_fast_sat_vb'], ethereum and ethereum['gas_average_gwei']))
        self.fee_history = self.fee_history[-240:]
        self.draw_fees()
        self.draw_pools((bitcoin or {}).get('top_pools') or [])
        refused = value.get('refused') or []
        self.refused_note.configure(text=('Not read: ' + ', '.join(item['network'] for item in refused) + ' — their explorers answer '
                                          'automated requests with a browser challenge, and HAYAT does not get around it. Their DeFi '
                                          'totals are in the DeFi tab.' if refused else '')
                                    + ''.join('   Unavailable: ' + gap for gap in value.get('gaps') or []))
        self.status.set('Networks · updated ' + datetime.now().strftime('%H:%M:%S'))

    def draw_bar(self, canvas: tk.Canvas, percent: float, label: str, colour: str) -> None:
        canvas.delete('all')
        canvas.update_idletasks()
        width = max(120, canvas.winfo_width())
        canvas.create_rectangle(0, 3, width, 13, fill=blend(self.p.LINE, self.p.PANEL, .4), outline='')
        canvas.create_rectangle(0, 3, width * max(0.0, min(100.0, percent)) / 100, 13, fill=colour, outline='')
        canvas.create_text(4, 8, text=label, anchor='w', fill=self.p.WHITE, font=('Segoe UI', 7))

    def draw_fees(self) -> None:
        canvas, p = self.fee_chart, self.p
        canvas.delete('all')
        width, height = max(200, canvas.winfo_width()), max(80, canvas.winfo_height())
        points = self.fee_history
        if len(points) < 2:
            canvas.create_text(width / 2, height / 2, text='Drawn as the window refreshes (every 30 s)…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        start, end = points[0][0], points[-1][0]
        for index, (colour, label, unit) in enumerate(((BTC_COLOUR, 'Bitcoin fastest fee', 'sat/vB'), (ETH_COLOUR, 'Ethereum average gas', 'gwei'))):
            values = [(row[0], row[index + 1]) for row in points if row[index + 1] is not None]
            if len(values) < 2:
                continue
            low, high = min(value for _moment, value in values), max(value for _moment, value in values)
            span = (high - low) or max(high, 1) * .1
            line = []
            for moment, value in values:
                x = 10 + (moment - start) / max(1, end - start) * (width - 20)
                y = height - 14 - (value - low) / span * (height - 34)
                line += [x, y]
            canvas.create_line(*line, fill=colour, width=2, smooth=True)
            canvas.create_text(10 + index * 260, 10, anchor='w', fill=colour, font=('Segoe UI', 8),
                               text=f'{label}: {values[-1][1]:g} {unit} (range {low:g}–{high:g})')
        canvas.create_text(width - 8, 10, anchor='e', fill=p.MUTED, font=('Segoe UI', 7),
                           text=f'{len(points)} readings over {int((end - start) // 60)} min')

    def draw_pools(self, pools: list) -> None:
        canvas, p = self.pools, self.p
        canvas.delete('all')
        width = max(200, canvas.winfo_width())
        for index, pool in enumerate(pools[:6]):
            y = 10 + index * 18
            canvas.create_text(8, y, anchor='w', text=pool['name'][:18], fill=p.WHITE, font=('Segoe UI', 8))
            bar = (width - 170) * pool['share_pct'] / max(item['share_pct'] for item in pools)
            canvas.create_rectangle(120, y - 5, 120 + bar, y + 5, fill=blend(BTC_COLOUR, p.PANEL, .3), outline='')
            canvas.create_text(126 + bar, y, anchor='w', text=f"{pool['share_pct']}%", fill=p.MUTED, font=('Segoe UI', 8))

    def show_blocks(self, value: dict) -> None:
        for chain, rows in value.items():
            previous = self._seen_blocks.get(chain)
            self.block_data[chain] = rows
            fresh = {row['height'] for row in rows if previous is not None and row['height'] > previous}
            if chain == 'bitcoin' or chain == self.evm_chain.get():
                self.draw_blocks('bitcoin' if chain == 'bitcoin' else 'evm', fresh=fresh)
            if rows:
                self._seen_blocks[chain] = rows[0]['height']

    def evm_switched(self) -> None:
        self.draw_blocks('evm')
        self._due['blocks'] = 0.0

    def draw_blocks(self, strip: str, fresh: set | None = None) -> None:
        canvas, p = self.strips[strip], self.p
        chain = self.evm_chain.get() if strip == 'evm' else strip
        canvas.delete('all')
        width = max(300, canvas.winfo_width())
        rows = self.block_data.get(chain) or []
        if not rows:
            canvas.create_text(width / 2, 43, text='Reading blocks…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        tile = 150
        colour = BTC_COLOUR if chain == 'bitcoin' else ETH_COLOUR
        self._block_tiles = getattr(self, '_block_tiles', {})
        self._block_tiles[strip] = []
        for index, row in enumerate(rows[: max(1, int(width // (tile + 8)))]):
            x = 6 + index * (tile + 8)
            new = bool(fresh and row['height'] in fresh)
            fill = blend(colour, p.PANEL, .55 if new else .82)
            canvas.create_rectangle(x, 6, x + tile, 80, fill=fill, outline=colour if new else blend(colour, p.PANEL, .5), width=2 if new else 1)
            canvas.create_text(x + 8, 16, anchor='w', text=f"{row['height']:,}", fill=p.WHITE, font=('Segoe UI Semibold', 10))
            canvas.create_text(x + 8, 33, anchor='w', text=ago(row['at']) + (' · new' if new else ''), fill=p.GOLD_SOFT if new else p.MUTED,
                               font=('Segoe UI', 8))
            canvas.create_text(x + 8, 49, anchor='w', text=f"{row.get('transactions') or 0:,} transactions", fill=p.WHITE, font=('Segoe UI', 8))
            detail = (f"fees {row['fees_btc']} BTC" if row.get('fees_btc') is not None else f"{row.get('gas_used_pct')}% gas used")
            miner = row.get('miner') or ''
            canvas.create_text(x + 8, 65, anchor='w', text=detail + (' · ' + (short(miner, 4) if miner.startswith('0x') else miner[:14]) if miner else ''),
                               fill=p.MUTED, font=('Segoe UI', 8))
            self._block_tiles[strip].append((x, x + tile, row['height']))

    def block_clicked(self, strip: str, event: Any) -> None:
        chain = self.evm_chain.get() if strip == 'evm' else strip
        for left, right, height in getattr(self, '_block_tiles', {}).get(strip, []):
            if left <= event.x <= right:
                self.tabs.select(1)
                self.look_up(f'{chain} block {height}')
                return

    # ------------------------------------------------------------------ explorer
    def look_up(self, text: str | None = None, remember: bool = True) -> None:
        query = ' '.join(str(text if text is not None else self.query.get()).split())
        if not query:
            return
        self.query.set(query)
        chain = '' if self.chain.get() == 'auto' or ' block ' in f' {query} ' else self.chain.get()
        if remember:
            self.history = self.history[:self.position + 1] + [query]
            self.position = len(self.history) - 1
        self._update_steps()
        self.summary.configure(text=f'Looking up {query}…', fg=self.p.MUTED)
        self._pending.discard('lookup')
        self._submit('lookup', lambda: self.web3.lookup(query, chain))

    def step(self, direction: int) -> None:
        target = self.position + direction
        if 0 <= target < len(self.history):
            self.position = target
            self.look_up(self.history[target], remember=False)

    def _update_steps(self) -> None:
        self.back_button.state(['!disabled' if self.position > 0 else 'disabled'])
        self.forward_button.state(['!disabled' if self.position < len(self.history) - 1 else 'disabled'])

    def failed_lookup(self, error: str) -> None:
        self.summary.configure(text='Not found: ' + error.split(': ', 1)[-1][:300], fg=DOWN)
        self.rows.delete(*self.rows.get_children())

    def show_lookup(self, value: dict) -> None:
        from app.web3_chat import amount
        self.lookup_data = value
        unit = value.get('unit', '')
        lines = [value['title'] + (f" · {value['ens']}" if value.get('ens') else '') + f"   ({value['chain']}, {value['source']})"]
        rows = self.rows
        rows.delete(*rows.get_children())
        if value['kind'] == 'address':
            lines.append(value['address'] + (f"  ·  {value['name']}" if value.get('name') else '')
                         + (f"  ·  ENS {value['ens_name']}" if value.get('ens_name') else ''))
            lines.append(f"Balance {amount(value['balance'], unit)}" + (f"  ·  {amount(value['pending'], unit)} pending" if value.get('pending') else '')
                         + (f"  ·  {value['transaction_count']:,} transactions" if value.get('transaction_count') else '')
                         + (f"  ·  token {value['token']['name']} ({value['token']['symbol']})" if value.get('token') else ''))
            for item in value.get('transactions', []):
                change = item.get('change') or 0
                rows.insert('', 'end', iid=f"tx|{len(rows.get_children())}|{item['hash']}", text=item['hash'], tags=('in' if change > 0 else 'out' if change < 0 else '',),
                            values=(str(item.get('at') or 'pending')[:16].replace('T', ' '), f"{change:+,.8f} {unit}" if change else f'0 {unit} (a call)',
                                    ' · '.join(str(part) for part in (item.get('method'), short(item.get('counterparty'), 8) if item.get('counterparty') else '',
                                                                       'confirmed' if item.get('confirmed') else 'pending') if part)))
            if value.get('tokens'):
                lines.append(f"Tokens Blockscout prices at about {dollars(value['tokens_usd'])}"
                             + (f" ({value['tokens_hidden']:,} unpriced, flagged or airdropped tokens hidden)" if value.get('tokens_hidden') else ''))
            for index, item in enumerate(value.get('tokens') or []):
                rows.insert('', index, iid=f"holding|{index}|{item['address']}", text=f"◆ {item['symbol']} · {item['name']}"[:60], tags=('token',),
                            values=('token held', f"{amount(item['amount'])} {item['symbol']}", f"≈ {dollars(item['usd'])} (Blockscout price)"))
        elif value['kind'] == 'transaction':
            state = ('confirmed' + (f", {value['confirmations']:,} confirmations" if value.get('confirmations') else '')
                     if value.get('confirmed') else (value.get('status') or 'waiting in the mempool'))
            lines.append(f"{state}  ·  block {value.get('block') or '—'}  ·  {str(value.get('at') or '')[:16].replace('T', ' ')} UTC")
            lines.append(f"Value {amount(value.get('value'), unit)}  ·  fee {amount(value.get('fee'), unit)}"
                         + (f" ({value['fee_rate_sat_vb']} sat/vB)" if value.get('fee_rate_sat_vb') else '')
                         + (f"  ·  method {value['method']}" if value.get('method') else ''))
            for side, items in (('from', value.get('inputs') or []), ('to', value.get('outputs') or [])):
                for index, item in enumerate(items):
                    rows.insert('', 'end', iid=f'{side}|{index}|{item["address"]}', text=str(item['address']), tags=('out' if side == 'from' else 'in',),
                                values=('', amount(item['value'], unit), 'input' if side == 'from' else 'output'))
            for party in ('from', 'to'):
                if value.get(party):
                    rows.insert('', 'end', iid=f'{party}|party|{value[party]}', text=value[party], values=('', '', party))
            for index, transfer in enumerate(value.get('token_transfers') or []):
                rows.insert('', 'end', iid=f'token|{index}|{transfer["to"]}', text=str(transfer['to']),
                            values=('', f"{amount(transfer['amount'])} {transfer['token'] or ''}", f"token from {short(transfer['from'], 8)}"))
        else:
            lines.append(f"{str(value.get('at') or '')[:16].replace('T', ' ')} UTC ({ago(value.get('at'))})  ·  "
                         f"{value.get('transaction_count') or 0:,} transactions"
                         + (f"  ·  mined by {value['miner']}" if value.get('miner') else '')
                         + (f"  ·  fees {amount(value['fees'], unit)}" if value.get('fees') is not None else '')
                         + (f"  ·  {value['gas_used_pct']}% gas used" if value.get('gas_used_pct') is not None else ''))
            lines.append(f"Hash {value.get('hash')}")
        self.summary.configure(text='\n'.join(lines), fg=self.p.WHITE)
        watchable = value['kind'] == 'address' and getattr(self.web3, 'path', None) is not None
        self.watch_button.state(['!disabled' if watchable else 'disabled'])

    def selected_target(self) -> str | None:
        selection = self.rows.selection()
        if not selection:
            return None
        parts = selection[0].split('|', 2)
        return parts[2] if len(parts) == 3 and parts[2] not in ('None', '') else None

    def follow(self) -> None:
        target = self.selected_target()
        if target and target not in ('coinbase', '?') and not target.startswith(('v0_', 'v1_', 'op_return')):
            self.look_up(target)

    def copy_selected(self) -> None:
        target = self.selected_target()
        if target:
            self.clipboard_clear()
            self.clipboard_append(target)
            self.footer.configure(text=f'Copied {short(target, 12)}', fg=self.p.GOLD_SOFT)

    # ------------------------------------------------------------------ DeFi
    def show_defi(self, value: dict) -> None:
        self.defi_data = value
        self.defi_note.configure(text=f"{dollars(value['total_tvl_usd'])} locked in DeFi across {value['chain_count']} chains"
                                      + (f" · {dollars(value.get('stablecoin_supply_usd'))} of stablecoins in circulation" if value.get('stablecoin_supply_usd') else '')
                                      + ' · DefiLlama estimates · hover a bar for its figure')
        self.stables.delete(*self.stables.get_children())
        for item in value.get('stablecoins') or []:
            self.stables.insert('', 'end', text=f"{item['symbol']} · {item['name']}",
                                values=(dollars(item['supply_usd']), item.get('mechanism') or '', item.get('chains') or ''))
        self.dexes.delete(*self.dexes.get_children())
        dex = value.get('dex')
        if dex:
            self.dex_note.configure(text=f"{dollars(dex['volume_24h_usd'])} traded in 24 h · {dollars(dex['volume_7d_usd'])} in 7 days"
                                         + (f" · {dex['change_1d_pct']:+.1f}% on the day before" if dex.get('change_1d_pct') is not None else ''))
            for item in dex['top']:
                change = item.get('change_1d_pct')
                self.dexes.insert('', 'end', text=item['name'], tags=('up',) if (change or 0) > 0 else ('down',) if (change or 0) < 0 else (),
                                  values=(dollars(item['volume_24h_usd']), '—' if change is None else f'{change:+.1f}%', item['chains']))
        self.draw_tvl()

    def draw_tvl(self) -> None:
        canvas, p = self.tvl, self.p
        canvas.delete('all')
        width, height = max(300, canvas.winfo_width()), max(200, canvas.winfo_height())
        chains = (self.defi_data or {}).get('chains') or []
        self._tvl_bars = []
        if not chains:
            canvas.create_text(width / 2, height / 2, text='Reading DeFi totals…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        row = min(28, (height - 12) / len(chains))
        largest = chains[0]['tvl_usd'] or 1
        for index, item in enumerate(chains):
            y = 8 + index * row
            bar = (width - 260) * item['tvl_usd'] / largest
            colour = ETH_COLOUR if item['name'] in ('Ethereum', 'Optimism', 'OP Mainnet', 'Base', 'Arbitrum', 'zkSync Era', 'Linea',
                                                    'Scroll', 'Polygon') else p.GOLD
            canvas.create_text(10, y + row / 2, anchor='w', text=item['name'][:16], fill=p.WHITE, font=('Segoe UI', 9))
            canvas.create_rectangle(130, y + 4, 130 + bar, y + row - 4, fill=blend(colour, p.PANEL, .25), outline='')
            canvas.create_text(136 + bar, y + row / 2, anchor='w', text=f"{dollars(item['tvl_usd'])} · {item['share_pct']}%", fill=p.MUTED,
                               font=('Segoe UI', 8))
            self._tvl_bars.append((y, y + row, item))

    def tvl_hover(self, event: Any) -> None:
        self.tvl.delete('hover')
        item = next((item for top, bottom, item in getattr(self, '_tvl_bars', []) if top <= event.y <= bottom), None)
        if not item:
            return
        text = (f"{item['name']}: {dollars(item['tvl_usd'])} locked, {item['share_pct']}% of all DeFi"
                + (f" · native token {item['token']}" if item.get('token') else ''))
        label = self.tvl.create_text(event.x + 12, event.y + 12, text=text, anchor='nw', fill=self.p.WHITE, font=('Segoe UI', 9), tags='hover')
        box = self.tvl.bbox(label)
        if box[2] > self.tvl.winfo_width():
            self.tvl.move(label, -(box[2] - self.tvl.winfo_width()) - 16, 0)
            box = self.tvl.bbox(label)
        backing = self.tvl.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2, fill=self.p.PANEL, outline=self.p.LINE, tags='hover')
        self.tvl.tag_lower(backing, label)

    # ------------------------------------------------------------------ agentic web
    def inspect(self, site: str | None = None) -> None:
        site = (site if site is not None else self.site.get()).strip()
        if not site:
            return
        self.site.set(site)
        self.verdict.configure(text=f'Reading {site}’s public files (robots.txt honoured)…', fg=self.p.MUTED)
        self._pending.discard('inspect')
        web3 = self.web3

        def check():
            value = self.agent_web.inspect(site)
            history = web3.remember_site(value) if hasattr(web3, 'remember_site') else []
            return {**value, 'history': history}
        self._submit('inspect', check)

    def failed_inspect(self, error: str) -> None:
        self.verdict.configure(text='Could not check it: ' + error.split(': ', 1)[-1][:240], fg=DOWN)

    def show_inspect(self, value: dict) -> None:
        self.inspect_data = value
        if value.get('history') is not None:
            self.show_history(value['history'])
        p = self.p
        self.draw_ring(value['score'], value['out_of'])
        self.verdict.configure(text=f"{value['site']}" + (f" · {value['title']}" if value.get('title') else '')
                               + f"\n{value['score']} of {value['out_of']} agent-web surfaces published.\nAI crawlers: {value['ai_position']}."
                               + (f"\n{value['policy']['summary']}" if value.get('policy') else '')
                               + (f"\nContent signals: {', '.join(f'{key}={item}' for key, item in value['robots']['content_signals'].items())}"
                                  if (value.get('robots') or {}).get('content_signals') else ''), fg=p.WHITE)
        self.checks.delete(*self.checks.get_children())
        for key, check in value['checks'].items():
            detail = value['details'].get(key) or {}
            found = ' · '.join(str(part) for part in (detail.get('title') or detail.get('name'),
                                                       f"{detail['links']} links" if detail.get('links') else '',
                                                       f"{detail['skills']} skills" if detail.get('skills') else '',
                                                       f"{detail['tools']} tools" if detail.get('tools') else '', check.get('note')) if part)
            self.checks.insert('', 'end', iid=key, text=('✓  ' if check['present'] else '·  ') + check['label'],
                               values=(found or ('published' if check['present'] else 'not published'),),
                               tags=('yes' if check['present'] else 'no',))
        self.crawlers.delete(*self.crawlers.get_children())
        for item in (value.get('robots') or {}).get('agents') or []:
            access = ('allowed' if item['allowed'] else 'blocked') + (' (named)' if item['named'] else '')
            self.crawlers.insert('', 'end', text=item['agent'], values=(item['owner'], item['purpose'], access),
                                 tags=('allowed' if item['allowed'] else 'blocked',))
        advice = value.get('recommendations') or []
        self.show_preview(value['definition'] + ('\n\n' + value['policy']['summary'] if value.get('policy') else '')
                          + ('\n\nWhat this site could add for agents:\n' + '\n'.join(f'• {item}' for item in advice) if advice else
                             '\n\nIt already publishes every surface checked.'))

    def show_history(self, sites: list) -> None:
        self.site_history = sites
        tree = self.history_tree
        tree.delete(*tree.get_children())
        for entry in sites:
            tree.insert('', 'end', iid=entry['site'], text=entry['site'].replace('https://', ''),
                        values=(f"{entry['score']}/{entry['out_of']}", entry['ai_position'], str(entry['at'])[:16].replace('T', ' ')))

    def history_selected(self) -> None:
        selection = self.history_tree.selection()
        if selection and (not self.inspect_data or self.inspect_data['site'] != selection[0]):
            self.inspect(selection[0].replace('https://', ''))

    def draw_ring(self, score: int, out_of: int) -> None:
        canvas, p = self.ring, self.p
        canvas.delete('all')
        canvas.create_oval(15, 15, 115, 115, outline=blend(p.LINE, p.BLACK, .3), width=12)
        if score:
            colour = UP if score >= out_of * .6 else p.GOLD if score >= out_of * .3 else DOWN
            canvas.create_arc(15, 15, 115, 115, start=90, extent=-359.9 * score / out_of, style='arc', outline=colour, width=12)
        canvas.create_text(65, 58, text=f'{score}/{out_of}', fill=p.WHITE, font=('Segoe UI Semibold', 18))
        canvas.create_text(65, 82, text='agent-ready', fill=p.MUTED, font=('Segoe UI', 8))

    def check_selected(self) -> None:
        selection = self.checks.selection()
        if not selection or not self.inspect_data:
            return
        key = selection[0]
        check = self.inspect_data['checks'][key]
        text = check['meaning'] + (f"\nAddress: {self.inspect_data['site']}{check['path']}" if check.get('path') else '')
        preview = self.inspect_data['previews'].get(key)
        if key in ('robots', 'ai_rules') and self.inspect_data.get('robots'):
            robots = self.inspect_data['robots']
            preview = (f"AI agents named in robots.txt: {robots['named_ai_rules']}\nBlocked: {', '.join(robots['blocked']) or 'none'}\n"
                       f"Sitemaps: {', '.join(robots['sitemaps']) or 'none listed'}")
        self.show_preview(text + ('\n\n' + preview if preview else '\n\nNot published.' if not check['present'] else ''))

    def show_preview(self, text: str) -> None:
        self.preview.configure(state='normal')
        self.preview.delete('1.0', 'end')
        self.preview.insert('1.0', text)
        self.preview.configure(state='disabled')

    # ------------------------------------------------------------------ watched addresses
    def _build_watched(self, parent: Any) -> tk.Frame:
        p = self.p
        frame = tk.Frame(parent, bg=p.BLACK)
        tk.Label(frame, text='Addresses you follow, kept on this computer. Balances are read again every two minutes while this tab is '
                             'open; a change since the last check is marked. Watching never signs or sends anything.',
                 bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left', wraplength=1300).pack(fill='x', pady=(10, 4))
        row = tk.Frame(frame, bg=p.BLACK)
        row.pack(fill='x', pady=(0, 6))
        self.watch_query = StringVar(self, '')
        self.watch_label = StringVar(self, '')
        tk.Label(row, text='Address or ENS', bg=p.BLACK, fg=p.WHITE).pack(side='left')
        entry = ttk.Entry(row, textvariable=self.watch_query, width=52)
        entry.pack(side='left', padx=6)
        entry.bind('<Return>', lambda _event: self.add_watch())
        tk.Label(row, text='Label', bg=p.BLACK, fg=p.WHITE).pack(side='left', padx=(8, 0))
        ttk.Entry(row, textvariable=self.watch_label, width=20).pack(side='left', padx=6)
        ttk.Button(row, text='Watch', style='HayatAccent.TButton', command=self.add_watch).pack(side='left')
        ttk.Button(row, text='Check now', style='Compact.Hayat.TButton', command=self.check_watch).pack(side='right')
        self.watched = ttk.Treeview(frame, columns=('chain', 'balance', 'change', 'checked'), selectmode='browse')
        for key, label, width, anchor in (('#0', 'Label · address', 420, 'w'), ('chain', 'Chain', 90, 'w'), ('balance', 'Balance', 180, 'e'),
                                          ('change', 'Last change', 180, 'e'), ('checked', 'Checked (UTC)', 150, 'w')):
            self.watched.heading(key, text=label)
            self.watched.column(key, width=width, anchor=anchor, stretch=key == '#0')
        self.watched.tag_configure('up', foreground=UP)
        self.watched.tag_configure('down', foreground=DOWN)
        self.watched.tag_configure('error', foreground=p.MUTED)
        self.watched.pack(fill='both', expand=True)
        self.watched.bind('<Double-1>', lambda _event: self.open_watched())
        actions = tk.Frame(frame, bg=p.BLACK)
        actions.pack(fill='x', pady=(6, 0))
        ttk.Button(actions, text='Open in explorer', style='Compact.Hayat.TButton', command=self.open_watched).pack(side='left')
        ttk.Button(actions, text='Stop watching', style='Compact.Hayat.TButton', command=self.remove_watch).pack(side='left', padx=6)
        self.watch_note = tk.Label(actions, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9))
        self.watch_note.pack(side='left', padx=8)
        return frame

    def _watch_job(self, action: str, query: str = '', label: str = '') -> None:
        if getattr(self.web3, 'path', None) is None:
            self.watch_note.configure(text='Watching needs HAYAT\'s data folder; this session has none.', fg=DOWN)
            return
        self._pending.discard('watch')
        self._submit('watch', lambda: self.web3.watchlist(action, query, label))
        self.watch_note.configure(text={'add': 'Adding…', 'remove': 'Removing…', 'check': 'Reading balances…'}.get(action, ''), fg=self.p.MUTED)

    def add_watch(self) -> None:
        if self.watch_query.get().strip():
            self._watch_job('add', self.watch_query.get().strip(), self.watch_label.get().strip())

    def watch_current(self) -> None:
        if self.lookup_data and self.lookup_data['kind'] == 'address':
            self._watch_job('add', self.lookup_data['address'], self.lookup_data.get('ens') or '')
            self.footer.configure(text=f"Watching {short(self.lookup_data['address'], 10)} (see ★ Watched addresses)", fg=self.p.GOLD_SOFT)

    def check_watch(self) -> None:
        self._watch_job('check')

    def remove_watch(self) -> None:
        selection = self.watched.selection()
        if selection:
            self._watch_job('remove', selection[0])

    def open_watched(self) -> None:
        selection = self.watched.selection()
        if selection:
            self.tabs.select(1)
            entry = next((item for item in (self.watch_data or {}).get('addresses', []) if item['address'] == selection[0]), None)
            if entry and entry['chain'] != 'bitcoin':
                self.chain.set(entry['chain'])
            self.look_up(selection[0])

    def failed_watch(self, error: str) -> None:
        self.watch_note.configure(text='Could not do that: ' + error.split(': ', 1)[-1][:200], fg=DOWN)

    def show_watch(self, value: dict) -> None:
        from app.web3_chat import amount
        self.watch_data = value
        tree = self.watched
        tree.delete(*tree.get_children())
        changed = {item['address']: item for item in value.get('changed') or []}
        for entry in value['addresses']:
            unit = entry.get('unit', '')
            previous = entry.get('previous_balance')
            change = (entry['balance'] - previous) if previous is not None and entry.get('balance') is not None else None
            tag = 'error' if entry.get('error') else 'up' if (change or 0) > 0 else 'down' if (change or 0) < 0 else ''
            tree.insert('', 'end', iid=entry['address'], tags=(tag,) if tag else (),
                        text=(entry['label'] + ' · ' if entry.get('label') else '') + entry['address']
                        + ('  ● changed' if entry['address'] in changed else ''),
                        values=(entry['chain'], amount(entry.get('balance'), unit),
                                f'{change:+,.8f} {unit}' if change else ('—' if not entry.get('error') else entry['error']),
                                str(entry.get('checked_at') or '')[:16].replace('T', ' ')))
        if changed:
            self.footer.configure(text=f"{len(changed)} watched address{'es' if len(changed) != 1 else ''} changed balance", fg=self.p.GOLD)
            self.bell()
        verb = {'add': 'Added', 'remove': 'Removed', 'check': 'Checked', 'list': 'Watching'}[value['action']]
        self.watch_note.configure(text=f"{verb} · {len(value['addresses'])} watched"
                                       + (f" · {len(changed)} changed" if value['action'] == 'check' else ''), fg=self.p.GOLD_SOFT)
        if value['action'] == 'add':
            self.watch_query.set('')
            self.watch_label.set('')

    # ------------------------------------------------------------------ closing
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.after_cancel(self._tick_id)
        except (tk.TclError, AttributeError):
            pass
        self.destroy()
