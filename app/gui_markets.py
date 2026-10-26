"""Trade Center: crypto and stocks live, charts with expected ranges, world events by industry, chain state. Read-only.

The window shows markets and never trades. There is no order button, no wallet
and no exchange login; the banner says so. Data comes from app/markets.py on a
worker thread, one request at a time, so the window never freezes and the
public APIs are not hammered.
"""
from __future__ import annotations

import math
import queue
import threading
import time
import tkinter as tk
from datetime import UTC, datetime
from tkinter import ttk

from app.gui_intelligence_parts import FlowRow, blend
from app.gui_resources import BooleanVar, StringVar
from app.gui_windows import MARGIN, place_over, work_area
from app.markets import QUOTES, RANGES, money, signed_pct
from app.gui_fonts import DISPLAY

UP, DOWN = '#4ADE80', '#F05D5E'
AMBER_TC = '#E8C46A'
SOURCES = ('Sources: Kraken WebSocket (live trades) and public API (crypto, tokenized stocks, charts, books) · Coinbase '
           '(cross-check) · Finnhub (exchange prices, with your key) · mempool.space · Blockscout · HAYAT world events · no account')
VIEWS = (('all', 'All markets'), ('watch', '★ Watchlist'), ('gainers', 'Gainers'), ('losers', 'Losers'))
#: Seconds between refreshes: the coin on screen, the market list, the chart, the chain, the outlook and events.
#: The price itself streams live (app/markets_live.py); these are the slower panels around it.
REFRESH = {'coin': 10, 'list': 60, 'chart': 60, 'chain': 60, 'outlook': 300, 'events': 180, 'heatmap': 60, 'compare': 60,
           'scenarios': 60, 'calendar': 1800, 'news': 300, 'periods': 120, 'signals': 600, 'influences': 300, 'space': 300}
LEANING = {'up': ('▲ leans up', '#4ADE80'), 'down': ('▼ leans down', '#F05D5E'), 'risk': ('◆ adds risk', '#E8C46A'),
           'neutral': ('· neutral', '#9AA4AE')}
ASSETS = (('crypto', 'Crypto'), ('stock', 'Stocks'))
#: Chart overlays: (key, label, colour). Moving averages over the candles on screen; the S&P 500 rescaled to start level.
OVERLAYS = (('ma20', 'MA 20', '#60A5FA'), ('ma50', 'MA 50', '#C084FC'), ('compare', 'vs S&P 500', '#CBD5E1'))
#: Keys that work anywhere in the window except while typing in a box.
SHORTCUTS = 'Ctrl+F search · 1–5 range · H heatmap · D 3D · S signals · W watch · A alerts'
#: The centre views: (value, button label).
CENTER_VIEWS = (('chart', 'Chart'), ('heatmap', 'Heatmap'), ('space', '3D'), ('signals', 'Signals'))


def amount(value):
    """0.00004512 rather than 4.512e-05."""
    return f"{value:.8f}".rstrip("0").rstrip(".") if value < 1 else f"{value:,.4f}".rstrip("0").rstrip(".")


def compact(value):
    """1,234,567 → 1.23 M."""
    if value is None:
        return '—'
    for size, unit in ((1e12, ' T'), (1e9, ' B'), (1e6, ' M'), (1e3, ' K')):
        if abs(value) >= size:
            return f'{value / size:.2f}{unit}'
    return f'{value:,.0f}'


class TradeCenter(tk.Toplevel):
    def __init__(self, gui):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.markets, self.palette = gui, gui.runtime.markets, type(gui)
        p = self.palette
        self._closed = False
        self._jobs: queue.Queue = queue.Queue()
        self._results: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._pending_context = {}
        self._job_serial = 0
        self._due = {key: 0.0 for key in REFRESH}
        self.rows: dict[str, dict] = {}
        self.symbol, self.quote_view, self.chart_data, self.book_data = 'BTC', None, None, None
        self.asset, self.selected_asset = StringVar(self, 'crypto'), 'crypto'
        self.outlook_data, self.events_data, self._live_price, self._streaming = None, None, None, None
        self.scenario_data = None
        self.calendar_data, self.news_data = None, None
        self.rumours_only = BooleanVar(self, False)
        self.this_market = BooleanVar(self, False)
        self.center_view = StringVar(self, 'chart')
        self.overlay = {key: BooleanVar(self, key == 'ma20') for key, _label, _colour in OVERLAYS}
        self.compare_data = None
        self._event_marks: list = []
        self._ticks = 0
        self._live_drawn = 0.0
        self._hover_at = None
        self.watchlist: list[str] = []
        self.query = StringVar(self, '')
        self.quote = StringVar(self, 'USD')
        self.view = StringVar(self, 'all')
        self.span = StringVar(self, '1D')
        self.layout = StringVar(self, 'Auto')
        self.status = StringVar(self, 'Loading markets…')
        self.title('HAYAT · Trade Center')
        self.configure(bg=p.BLACK)
        area = work_area(gui.root)
        self.minsize(min(840, area[2] - area[0] - 2 * MARGIN), min(520, area[3] - area[1] - 2 * MARGIN - 40))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        self._build()
        self.layout.trace_add('write', lambda *_: self._layout_panes())
        self.bind('<Configure>', self._layout_panes, add='+')
        for variable in (self.query, self.quote, self.view, self.asset):
            variable.trace_add('write', lambda *_: self._request('list', force=True))
        self.span.trace_add('write', lambda *_: (self._request('chart', force=True), self._request('compare', force=True)))
        self.bind('<Key>', self.shortcut)
        self.bind('<Control-f>', lambda _event: (self.search.focus_set(), self.search.select_range(0, 'end')))
        place_over(self, gui.root, (1280, 820))
        self.deiconify()
        from app.gui_icons import apply_icons
        apply_icons(self, p)
        self._worker = threading.Thread(target=self._work, name='hayat-trade-center', daemon=True)
        self._worker.start()
        self._tick_id = self.after(50, self._tick)

    # ------------------------------------------------------------------ layout
    def _build(self):
        p = self.palette
        header = self.header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill='x')
        words = tk.Frame(header, bg=p.BLACK)
        words.pack(side='left', fill='x', expand=True)
        self.header_title = tk.Label(words, text='Trade Center', bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 19), anchor='w')
        self.header_title.pack(fill='x')
        self.header_subtitle = tk.Label(words, text='Crypto and stocks, streamed live · charts with expected ranges · world events by industry · chain state',
                 bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w')
        self.header_subtitle.pack(fill='x')
        self.header_shortcuts = tk.Label(words, text='Keys: ' + SHORTCUTS, bg=p.BLACK, fg=blend(p.MUTED, p.BLACK, .35), font=('Segoe UI', 8), anchor='w')
        self.header_shortcuts.pack(fill='x')
        controls = tk.Frame(header, bg=p.BLACK)
        controls.pack(side='right', before=words)
        tk.Label(controls, textvariable=self.status, bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9)).pack(side='left', padx=10)
        ttk.Button(controls, text='Refresh', style='HayatAccent.TButton', command=self.refresh_all).pack(side='left')
        self.layout_choice = ttk.Combobox(controls, textvariable=self.layout, state='readonly', width=17,
                                          values=('Auto', 'Chart focus', 'Markets + chart', 'Chart + details'))
        self.layout_choice.pack(side='left', padx=(8, 0))
        # The rule, where it cannot be missed.
        banner = tk.Label(self, text='Read-only. HAYAT shows markets; it never buys, sells, swaps or sends crypto, and has no wallet, '
                                     'exchange account or keys. Use your own exchange app to trade.',
                          bg=blend(p.GOLD, p.BLACK, .85), fg=p.GOLD_SOFT, font=('Segoe UI', 9), anchor='w', padx=16, pady=5)
        banner.pack(fill='x', padx=16)
        banner.bind('<Configure>', lambda event: banner.configure(wraplength=max(300, event.width - 32)))
        from app.gui_markets_views import TickerStrip
        self.ticker = TickerStrip(self, p, lambda symbol, asset: self.select(symbol, asset))
        self.ticker.pack(fill='x', padx=16, pady=(6, 0))
        stream = getattr(self.markets, 'live', None)
        if stream is not None:
            stream.watch(TickerStrip.stream_names())
        self.footer = tk.Label(self, text=SOURCES, bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', padx=14, pady=5)
        self.footer.pack(side='bottom', fill='x')
        self.footer.bind('<Configure>', lambda event: self.footer.configure(wraplength=max(300, event.width - 28)))
        body = tk.PanedWindow(self, orient='horizontal', bg=p.BLACK, sashwidth=6, bd=0)
        body.pack(fill='both', expand=True, padx=16, pady=8)
        self.body = body
        self.list_pane, self.coin_pane, self.side_pane = self._build_list(body), self._build_coin(body), self._build_side(body)
        body.add(self.list_pane, minsize=300, width=350, stretch='never')
        body.add(self.coin_pane, minsize=400, stretch='always')
        body.add(self.side_pane, minsize=300, width=330, stretch='never')

    def _layout_panes(self, event=None):
        if event is not None and event.widget is not self:
            return
        mode, width = self.layout.get(), self.winfo_width()
        compact = self.winfo_height() < 650
        if compact != getattr(self, '_compact_window', None):
            self._compact_window = compact
            self.header.configure(pady=5 if compact else 10)
            self.header_title.configure(font=('Segoe UI Semibold', 14 if compact else 19))
            self.price.configure(font=('Segoe UI Semibold', 20 if compact else 26))
            if compact:
                self.header_subtitle.pack_forget()
                self.header_shortcuts.pack_forget()
                self.ticker.pack_forget()
            else:
                self.header_subtitle.pack(fill='x')
                self.header_shortcuts.pack(fill='x')
                self.ticker.pack(fill='x', padx=16, pady=(6, 0), before=self.body)
        show_list = mode == 'Markets + chart' or mode == 'Auto' and width >= 950
        show_side = mode == 'Chart + details' or mode == 'Auto' and width >= 1200 and self.center_view.get() == 'chart'
        signature = (show_list, show_side)
        if signature != getattr(self, '_pane_layout', None):
            self._pane_layout = signature
            self.body.paneconfigure(self.list_pane, hide=not show_list)
            self.body.paneconfigure(self.side_pane, hide=not show_side)

    def _build_list(self, parent):
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        kinds = tk.Frame(frame, bg=p.BLACK)
        kinds.pack(fill='x', pady=(0, 6))
        for value, label in ASSETS:
            ttk.Radiobutton(kinds, text=label, value=value, variable=self.asset, style='Segment.Toolbutton').pack(side='left', padx=(0, 4))
        top = tk.Frame(frame, bg=p.BLACK)
        top.pack(fill='x')
        self.search = ttk.Entry(top, textvariable=self.query)
        self.search.pack(side='left', fill='x', expand=True)
        self.quote_box = ttk.Combobox(top, textvariable=self.quote, values=list(QUOTES), width=6, state='readonly')
        self.quote_box.pack(side='left', padx=(6, 0))
        # Tokenized stocks trade in US dollars only.
        self.asset.trace_add('write', lambda *_: self.quote_box.configure(state='disabled' if self.asset.get() == 'stock' else 'readonly'))
        views = FlowRow(frame, p.BLACK, gap=4, height=34)
        views.pack(fill='x', pady=6)
        for value, label in VIEWS:
            views.add(ttk.Radiobutton(views, text=label, value=value, variable=self.view, style='Segment.Toolbutton'))
        self.count = tk.Label(frame, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8), anchor='w')
        self.count.pack(fill='x')
        table = tk.Frame(frame, bg=p.BLACK)
        table.pack(fill='both', expand=True, pady=(4, 0))
        style = ttk.Style(self)
        style.configure('Markets.Treeview', rowheight=26)
        self.tree = ttk.Treeview(table, columns=('price', 'change', 'volume'), selectmode='browse', style='Markets.Treeview')
        for key, label, width, anchor in (('#0', 'Coin', 150, 'w'), ('price', 'Price', 105, 'e'), ('change', 'Today', 70, 'e'),
                                          ('volume', '24 h vol.', 85, 'e')):  # '24 h volume' was cut to '24 h volum'
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60, anchor=anchor, stretch=key == '#0')
        self.tree.tag_configure('up', foreground=UP)
        self.tree.tag_configure('down', foreground=DOWN)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.pack(side='bottom', fill='x')
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.select(self.tree.selection()[0] if self.tree.selection() else None))
        return frame

    def _build_coin(self, parent):
        p = self.palette
        frame = tk.Frame(parent, bg=p.PANEL, padx=14, pady=10)
        head = tk.Frame(frame, bg=p.PANEL)
        head.pack(fill='x')
        self.coin_title = tk.Label(head, text='BTC', bg=p.PANEL, fg=p.WHITE, font=(DISPLAY, 16), anchor='w')
        self.coin_title.pack(side='left')
        self.watch_button = ttk.Button(head, text='☆ Watch', style='Compact.Hayat.TButton', command=self.toggle_watch)
        self.watch_button.pack(side='right')
        ttk.Button(head, text='Alerts', style='Compact.Hayat.TButton', command=self.alerts_dialog).pack(side='right', padx=(4, 0))
        ttk.Button(head, text='Price alert…', style='Compact.Hayat.TButton', command=self.alert_dialog).pack(side='right', padx=4)
        price_row = tk.Frame(frame, bg=p.PANEL)
        price_row.pack(fill='x', pady=(4, 0))
        self.price = tk.Label(price_row, text='—', bg=p.PANEL, fg=p.WHITE, font=(DISPLAY, 26), anchor='w')
        self.price.pack(side='left')
        self.change = tk.Label(price_row, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI Semibold', 13), anchor='w')
        self.change.pack(side='left', padx=12, pady=(8, 0))
        # Lit while trades stream in; shows how late they arrive (measured, never assumed zero).
        self.live_badge = tk.Label(price_row, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI Semibold', 9))
        self.live_badge.pack(side='right', pady=(8, 0))
        self.facts = tk.Label(frame, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left')
        self.facts.pack(fill='x', pady=(2, 6))
        spans = tk.Frame(frame, bg=p.PANEL)
        spans.pack(fill='x')
        for value in RANGES:
            ttk.Radiobutton(spans, text=value, value=value, variable=self.span, style='Small.Segment.Toolbutton').pack(side='left', padx=(0, 3))
        tk.Frame(spans, bg=p.PANEL, width=14).pack(side='left')
        for value, label in CENTER_VIEWS:
            ttk.Radiobutton(spans, text=label, value=value, variable=self.center_view, style='Small.Segment.Toolbutton',
                            command=self.switch_center).pack(side='left', padx=(0, 3))
        self.chart_note = tk.Label(frame, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w', justify='left')
        self.chart_note.pack(fill='x', pady=(4, 0))
        self.chart_note.bind('<Configure>', lambda event: self.chart_note.configure(wraplength=max(200, event.width - 4)))
        # Overlays on the chart, each its own colour; the legend says what the compare line shows.
        self.overlay_row = tk.Frame(frame, bg=p.PANEL)
        self.overlay_row.pack(fill='x', pady=(6, 0))
        toggles = tk.Frame(self.overlay_row, bg=p.PANEL)
        toggles.pack(fill='x')
        tk.Label(toggles, text='Overlays', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left', padx=(0, 6))
        for key, label, colour in OVERLAYS:
            tk.Checkbutton(toggles, text=label, variable=self.overlay[key], bg=p.PANEL, fg=colour, selectcolor=p.BLACK,
                           activebackground=p.PANEL, activeforeground=colour, font=('Segoe UI Semibold', 8), bd=0, highlightthickness=0,
                           command=lambda key=key: self.overlay_changed(key)).pack(side='left', padx=(0, 8))
        # One line under the toggles, each entry in its line's colour.
        self.legend = tk.Frame(self.overlay_row, bg=p.PANEL, height=16)
        self.legend.pack(fill='x')
        self.legend_text = ''
        self.chart = tk.Canvas(frame, bg=blend(p.PANEL, p.BLACK, .5), highlightthickness=0, height=360)
        self.chart.pack(fill='both', expand=True, pady=(8, 0))
        from app.gui_markets_views import Heatmap
        self.heatmap = Heatmap(frame, p, lambda symbol, asset: self.select(symbol, asset))
        from app.gui_markets_3d import Space3DPanel
        from app.gui_markets_signals import SignalsPanel
        self.space_panel = Space3DPanel(frame, p, lambda symbol, asset: self.select(symbol, asset),
                                        on_mode=lambda: self._request(self.space_panel.wanted(), force=True))
        self.space_panel.set_current(self.symbol)
        model = getattr(getattr(getattr(self.gui, 'runtime', None), 'intelligence', None), 'model', None)
        self.signals_panel = SignalsPanel(frame, p, self._submit, model)
        self.chart.bind('<Configure>', lambda _event: self.draw_chart())
        self.chart.bind('<Motion>', self.chart_hover)
        self.chart.bind('<Leave>', lambda _event: (self.chart.delete('hover'), setattr(self, '_hover_at', None)))
        return frame

    def _build_side(self, parent):
        p = self.palette
        holder = tk.Frame(parent, bg=p.BLACK)
        self.side_tabs = ttk.Notebook(holder, style='Side.TNotebook')
        self.side_tabs.pack(fill='both', expand=True)
        frame = tk.Frame(self.side_tabs, bg=p.BLACK)
        self.side_tabs.add(frame, text='Book')
        self.side_tabs.add(self._build_outlook(self.side_tabs), text='Outlook')
        self.side_tabs.add(self._build_events(self.side_tabs), text='Events')
        self.side_tabs.add(self._build_scenarios(self.side_tabs), text='Scenarios')
        self.side_tabs.add(self._build_calendar(self.side_tabs), text='Calendar')
        from app.gui_markets_periods import PeriodsPanel
        model = getattr(getattr(getattr(self.gui, 'runtime', None), 'intelligence', None), 'model', None)
        self.periods_panel = PeriodsPanel(self.side_tabs, p, self._submit, self.markets, model)
        self.side_tabs.add(self.periods_panel.frame, text='Periods')
        self.side_tabs.bind('<<NotebookTabChanged>>', lambda _event: [self._request(kind, force=True) for kind in ('scenarios', 'news', 'periods')])
        tk.Label(frame, text='Order book', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x')
        self.book = tk.Canvas(frame, bg=p.PANEL, highlightthickness=0, height=250)
        self.book.pack(fill='x', pady=(4, 8))
        self.book.bind('<Configure>', lambda _event: self.draw_book())
        tk.Label(frame, text='Recent trades', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x')
        self.trades = ttk.Treeview(frame, columns=('price', 'amount'), height=6, selectmode='none', style='Markets.Treeview')
        for key, label, width, anchor in (('#0', 'Time (UTC)', 80, 'w'), ('price', 'Price', 110, 'e'), ('amount', 'Amount', 90, 'e')):
            self.trades.heading(key, text=label)
            self.trades.column(key, width=width, anchor=anchor)
        self.trades.tag_configure('buy', foreground=UP)
        self.trades.tag_configure('sell', foreground=DOWN)
        self.trades.pack(fill='x', pady=(4, 8))
        tk.Label(frame, text='Web3 · network state', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x')
        self.chain = tk.Label(frame, text='Loading…', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9), anchor='nw', justify='left',
                              padx=10, pady=8)
        self.chain.pack(fill='both', expand=True, pady=(4, 0))
        if callable(getattr(self.gui, '_open_web3', None)):
            # The full explorer: addresses, transactions, blocks, Layer 2s, DeFi and the agent-web check.
            link = tk.Label(frame, text='Open the Web3 explorer ↗ (addresses, blocks, Layer 2s, DeFi)', bg=p.BLACK, fg=p.GOLD_SOFT,
                            font=('Segoe UI', 8, 'underline'), cursor='hand2', anchor='w')
            link.pack(fill='x', pady=(4, 0))
            link.bind('<Button-1>', lambda _event: self.gui._open_web3())
        self.chain.bind('<Configure>', lambda event: self.chain.configure(wraplength=max(200, event.width - 20)))
        return holder

    def _build_outlook(self, parent):
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        tk.Label(frame, text='What it may do next', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x')
        tk.Label(frame, text='Statistics from recent volatility, with HAYAT\'s measured track record. Not a prediction or advice.',
                 bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8), anchor='w', justify='left', wraplength=300).pack(fill='x')
        from app.gui_theme import DarkScrolledText
        self.outlook_text = DarkScrolledText(frame, bg=p.PANEL, fg=p.WHITE, font=('Segoe UI', 9), wrap='word',
                                            padx=10, pady=8, height=8, state='disabled')
        self.outlook_text.pack(fill='both', expand=True, pady=(6, 0))
        self._set_outlook_text('Loading…')
        return frame

    def _set_outlook_text(self, text):
        if self.outlook_text.get('1.0', 'end-1c') == text:
            return
        position = self.outlook_text.yview()[0]
        self.outlook_text._smooth_scroller.cancel()
        self.outlook_text.configure(state='normal')
        self.outlook_text.delete('1.0', 'end')
        self.outlook_text.insert('end', text)
        self.outlook_text.configure(state='disabled')
        self.outlook_text.yview_moveto(position)

    def _build_scenarios(self, parent):
        """How often this market rose in situations like now, live; scenario prices; what might matter."""
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        tk.Label(frame, text='Odds from its own past, live', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x')
        self.scenario_head = tk.Label(frame, text='Reading up to two years of daily prices…', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8),
                                      anchor='w', justify='left', wraplength=300)
        self.scenario_head.pack(fill='x')
        self.odds = tk.Canvas(frame, bg=p.PANEL, highlightthickness=0, height=132)
        self.odds.pack(fill='x', pady=(4, 4))
        self.odds.bind('<Configure>', lambda _event: self.draw_odds())
        tk.Label(frame, text='Coming week · weak / middle / strong (● live price)', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8),
                 anchor='w').pack(fill='x')
        self.paths = tk.Canvas(frame, bg=p.PANEL, highlightthickness=0, height=56)
        self.paths.pack(fill='x', pady=(2, 4))
        self.paths.bind('<Configure>', lambda _event: self.draw_paths())
        self.factor_tree = ttk.Treeview(frame, columns=('reading',), height=6, selectmode='browse', style='Markets.Treeview')
        self.factor_tree.heading('#0', text='Might matter')
        self.factor_tree.heading('reading', text='Reading')
        self.factor_tree.column('#0', width=130, stretch=False)
        self.factor_tree.column('reading', width=260)
        for key, (_text, colour) in LEANING.items():
            self.factor_tree.tag_configure(key, foreground=colour)
        self.factor_tree.pack(fill='both', expand=True)
        self.factor_tree.bind('<<TreeviewSelect>>', lambda _event: self.factor_selected())
        self.scenario_note = tk.Label(frame, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8), anchor='nw', justify='left',
                                      wraplength=300, padx=8, pady=6)
        self.scenario_note.pack(fill='x', pady=(4, 0))
        row = tk.Frame(frame, bg=p.BLACK)
        row.pack(fill='x', pady=(4, 0))
        self.explain_button = ttk.Button(row, text='Explain with HAYAT', style='Compact.Hayat.TButton', command=self.explain_scenarios)
        self.explain_button.pack(side='left')
        tk.Label(row, text='uses only these numbers', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left', padx=6)
        self.scenario_note.bind('<Configure>', lambda event: self.scenario_note.configure(wraplength=max(200, event.width - 16)))
        self.scenario_head.bind('<Configure>', lambda event: self.scenario_head.configure(wraplength=max(200, event.width - 4)))
        return frame

    def _build_calendar(self, parent):
        """Scheduled events with countdowns and past reactions; headlines naming markets, rumours marked unconfirmed."""
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        tk.Label(frame, text='Coming up', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x')
        self.calendar_tree = ttk.Treeview(frame, columns=('when', 'past'), height=5, selectmode='browse', style='Markets.Treeview')
        for key, label, width in (('#0', 'Event', 150), ('when', 'When', 70), ('past', 'Past moves', 110)):
            self.calendar_tree.heading(key, text=label)
            self.calendar_tree.column(key, width=width)
        self.calendar_tree.tag_configure('soon', foreground=AMBER_TC)
        self.calendar_tree.pack(fill='x', pady=(2, 4))
        self.calendar_tree.bind('<<TreeviewSelect>>', lambda _event: self.calendar_selected())
        head = tk.Frame(frame, bg=p.BLACK)
        head.pack(fill='x', pady=(4, 0))
        tk.Label(head, text='News & rumours', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11)).pack(side='left')
        for text, variable in (('Rumours only', self.rumours_only), ('This market', self.this_market)):
            tk.Checkbutton(head, text=text, variable=variable, bg=p.BLACK, fg=p.MUTED, selectcolor=p.BLACK, activebackground=p.BLACK,
                           font=('Segoe UI', 8), bd=0, highlightthickness=0,
                           command=lambda: self._request('news', force=True)).pack(side='right', padx=(4, 0))
        self.news_tree = ttk.Treeview(frame, columns=('since',), height=9, selectmode='browse', style='Markets.Treeview')
        self.news_tree.heading('#0', text='Headline')
        self.news_tree.heading('since', text='Moved since')
        self.news_tree.column('#0', width=210)
        self.news_tree.column('since', width=110)
        self.news_tree.tag_configure('rumour', foreground=AMBER_TC)
        self.news_tree.pack(fill='both', expand=True, pady=(2, 4))
        self.news_tree.bind('<<TreeviewSelect>>', lambda _event: self.news_selected())
        self.news_tree.bind('<Double-1>', lambda _event: self.open_news())
        self.calendar_note = tk.Label(frame, text='Reading the Fed calendar and business feeds…', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8),
                                      anchor='nw', justify='left', wraplength=300, padx=8, pady=6)
        self.calendar_note.pack(fill='x')
        self.calendar_note.bind('<Configure>', lambda event: self.calendar_note.configure(wraplength=max(200, event.width - 16)))
        return frame

    def show_calendar(self, value):
        self.calendar_data = value
        tree = self.calendar_tree
        tree.delete(*tree.get_children())
        for index, item in enumerate(value['events'][:12]):
            effects = item.get('effects') or []
            past = ' · '.join(f"{row['symbol']} {row['ratio']}×" for row in effects[:2] if row.get('ratio'))
            when = 'today' if item['days_until'] == 0 else 'tomorrow' if item['days_until'] == 1 else f"{item['days_until']} days"
            tree.insert('', 'end', iid=str(index), text=item['title'].replace(' (nonfarm payrolls)', '').replace(' (FOMC)', ''),
                        values=(when, past or '—'), tags=('soon',) if item['days_until'] <= 3 else ())
        if not self.news_data:
            self.calendar_note.configure(text=value['basis'] + ''.join(' ' + gap for gap in value['gaps']))

    def calendar_selected(self):
        selection = self.calendar_tree.selection()
        if selection and self.calendar_data:
            item = self.calendar_data['events'][int(selection[0])]
            effects = '; '.join(f"{row['symbol']} moved {row['event_move_pct']}% over two days vs {row['usual_move_pct']}% usually "
                                f"({row['ratio']}×, up {row['up_share_pct']}% of {row['events']} times)" for row in item.get('effects') or [])
            self.calendar_note.configure(text=f"{item['title']} · {item['date']} {item.get('time', '')} · {item['basis']}."
                                              + (f" On past ones: {effects}." if effects else '') + ' Bigger past moves mean bigger swings '
                                              'were common, not which way the next one goes.')

    def show_news(self, value):
        self.news_data = value
        tree = self.news_tree
        tree.delete(*tree.get_children())
        for index, item in enumerate(value['items']):
            moves = [move for move in item.get('moves') or [] if move['change_pct'] is not None and move['symbol'] != 'SPY']
            since = ', '.join(f"{move['symbol']} {move['change_pct']:+.1f}%" for move in moves[:2]) or ', '.join(item['markets'][:3])
            tree.insert('', 'end', iid=str(index), text=('UNCONFIRMED · ' if item['rumour'] else '') + item['title'],
                        values=(since,), tags=('rumour',) if item['rumour'] else ())
        if not value['items']:
            self.calendar_note.configure(text='No headlines naming ' + (value['symbol'] or 'markets') + (' with rumour wording' if value['rumours_only']
                                         else '') + f" in the last {value['hours']:g} hours.")
        else:
            self.calendar_note.configure(text=f"{len(value['items'])} headlines · {value['rumours']} unconfirmed · from "
                                              + ', '.join(value['outlets'][:4]) + '… Select one for details; double-click to open it.')

    def news_selected(self):
        selection = self.news_tree.selection()
        if selection and self.news_data:
            item = self.news_data['items'][int(selection[0])]
            moves = ', '.join(f"{move['symbol']} {move['change_pct']:+.2f}%" for move in item.get('moves') or [] if move['change_pct'] is not None)
            self.calendar_note.configure(text=('UNCONFIRMED: rumour wording, a report that something may happen. ' if item['rumour'] else '')
                                         + f"{item['title']} · {item['outlet']} · {item['published'][:16].replace('T', ' ')} UTC. "
                                         + (item['summary'][:220] + ' ' if item['summary'] else '')
                                         + (f"Since then: {moves} (the S&P 500 is the yardstick; nearness in time is not proof of cause)."
                                            if moves else ''))

    def open_news(self):
        selection = self.news_tree.selection()
        if selection and self.news_data:
            import webbrowser
            link = self.news_data['items'][int(selection[0])].get('link')
            if link:
                webbrowser.open(link)

    def _build_events(self, parent):
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        tk.Label(frame, text='Events → industries', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x')
        self.events_note = tk.Label(frame, text='Reading world events…', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8), anchor='w',
                                    justify='left', wraplength=300)
        self.events_note.pack(fill='x')
        self.events_tree = ttk.Treeview(frame, columns=('move',), height=12, selectmode='browse', style='Markets.Treeview')
        self.events_tree.heading('#0', text='Industry · event · market')
        self.events_tree.heading('move', text='Today')
        self.events_tree.column('#0', width=230)
        self.events_tree.column('move', width=70, anchor='e')
        self.events_tree.tag_configure('up', foreground=UP)
        self.events_tree.tag_configure('down', foreground=DOWN)
        self.events_tree.pack(fill='both', expand=True, pady=(4, 4))
        self.events_tree.bind('<<TreeviewSelect>>', lambda _event: self.event_selected())
        self.events_why = tk.Label(frame, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8), anchor='nw', justify='left',
                                   padx=8, pady=6, wraplength=300)
        self.events_why.pack(fill='x')
        return frame

    # ------------------------------------------------------------------ data
    def _submit(self, kind, call):
        """Run one job now, off the UI thread; its answer comes back to show_<kind>."""
        self._pending.add(kind)
        self._job_serial += 1
        context = self._job_context(kind)
        self._pending_context[kind] = (self._job_serial, context)
        self._jobs.put((kind, self._job_serial, context, call))

    def _job_context(self, kind):
        if kind in {'chain', 'events', 'heatmap', 'calendar', 'alerts'}:
            return ()
        if kind == 'list':
            return self.asset.get(), self.query.get(), self.quote.get(), self.view.get()
        selected = (self.symbol, self.selected_asset)
        if kind in {'chart', 'compare', 'coin'}:
            return (*selected, self.quote.get(), self.span.get() if kind != 'coin' else '')
        if kind == 'news':
            return (*selected, self.rumours_only.get(), self.this_market.get())
        if kind == 'space':
            return self.space_panel.span.get(),
        return selected

    def _request(self, kind, force=False):
        if force:
            self._due[kind] = 0.0

    def refresh_all(self):
        for kind in REFRESH:
            self._due[kind] = 0.0

    def _work(self):
        """One request at a time, off the UI thread."""
        while not self._closed:
            try:
                kind, serial, context, call = self._jobs.get(timeout=.2)
            except queue.Empty:
                continue
            if self._pending_context.get(kind) != (serial, context):
                continue  # skip superseded queued work without issuing another request
            try:
                self._results.put((kind, serial, context, call(), None))
            except Exception as exc:  # shown in the window, never raised into Tk
                self._results.put((kind, serial, context, None, f'{type(exc).__name__}: {exc}'))

    def _schedule(self):
        now = time.monotonic()
        # Tk variables are read here, on the UI thread; the worker only gets plain values.
        markets, symbol, quote = self.markets, self.symbol, self.quote.get()
        query, span, listing, asset = self.query.get(), self.span.get(), self.asset.get(), self.selected_asset
        quote = 'USD' if asset == 'stock' else quote
        sort = {'gainers': 'change', 'losers': 'losers'}.get(self.view.get(), 'volume')
        intelligence = getattr(getattr(self.gui, 'runtime', None), 'intelligence', None)

        def outlook():
            from app.market_outlook import outlook as compute
            return compute(markets, symbol, asset)

        live_price, events_now = self._live_price, self.events_data

        def scenarios():
            from app.market_scenarios import scenarios as compute
            return compute(markets, symbol, asset, events_now, live_price)

        def events():
            from app.market_events import events_and_markets
            return events_and_markets(intelligence, markets) if intelligence is not None else None
        watching = self.view.get() == 'watch'

        def listing_rows():
            watchlist = markets.watchlist('list')['symbols'] if markets.path else []
            if watching:
                # The watchlist holds coins and shares together, whichever list is chosen.
                coins = markets.overview(quote, query, 'volume', limit=1000)
                stocks = markets.stocks(query, 'volume', limit=1000)['rows']
                rows = [{**row, 'asset': 'crypto'} for row in coins['rows']] + stocks
                return {**coins, 'rows': [row for row in rows if row['symbol'] in watchlist]}, watchlist
            return (markets.stocks(query, sort if sort != 'price' else 'volume', limit=1000) if listing == 'stock'
                    else markets.overview(quote, query, sort, limit=1000)), watchlist

        jobs = {'list': listing_rows,
                'coin': lambda: (markets.quote(symbol, quote, asset), markets.book(symbol, 15, quote, asset), markets.trades(symbol, 25, quote, asset)),
                'chart': lambda: markets.chart(symbol, span, quote, asset),
                'chain': markets.chain, 'outlook': outlook, 'events': events}
        if self.center_view.get() == 'heatmap':
            def heatmap():
                from app.gui_markets_views import heat_groups
                stocks = {row['symbol']: row for row in markets.stocks(limit=1000)['rows']}
                crypto = {row['symbol']: row for row in markets.overview('USD', limit=1000)['rows']}
                return heat_groups(stocks, crypto)
            jobs['heatmap'] = heatmap
        center = self.center_view.get()

        def signals():
            from app.market_signals import signals as compute
            return compute(markets, symbol, asset, live_price)

        def influences():
            from app.market_influences import influences as compute
            return compute(markets, symbol, asset, events_now, live_price)
        if center == 'signals':
            jobs['signals'], jobs['influences'] = signals, influences
        if center == 'space':
            wanted = self.space_panel.wanted()
            if wanted == 'space':
                span_3d = self.space_panel.span.get()

                def space():
                    from app.market_space import market_space
                    return market_space(markets, None, span_3d)
                jobs['space'] = space
            elif wanted == 'influences':
                jobs['influences'] = influences
        if self.side_tabs.index('current') == 3:
            jobs['scenarios'] = scenarios
        if self.side_tabs.index('current') == 5 or center == 'space' and self.space_panel.wanted() == 'periods':
            def periods():
                from app.market_periods import periods as compute
                return compute(markets, symbol, asset, 'all', live_price)
            jobs['periods'] = periods
        if self.side_tabs.index('current') == 4 and getattr(markets, 'calendar', None) is not None:
            rumours, mine = self.rumours_only.get(), self.this_market.get()
            jobs['calendar'] = lambda: markets.calendar.upcoming(60)
            jobs['news'] = lambda: markets.news.news(symbol if mine else '', 72, rumours, 30)
        if self.overlay['compare'].get() and symbol != 'SPY':
            jobs['compare'] = lambda: markets.chart('SPY', span, 'USD', 'stock')
        for kind, call in jobs.items():
            current = self._pending_context.get(kind)
            changed = current is not None and current[1] != self._job_context(kind)
            if changed or kind not in self._pending and now >= self._due[kind]:
                self._due[kind] = now + REFRESH[kind]
                self._submit(kind, call)

    def _tick(self):
        if self._closed:
            return
        self._schedule()
        while not self._results.empty():
            kind, serial, context, value, error = self._results.get_nowait()
            if self._pending_context.get(kind) != (serial, context):
                continue
            self._pending_context.pop(kind, None)
            self._pending.discard(kind)
            if context != self._job_context(kind):
                self._due[kind] = 0
                continue
            if error:
                if kind == 'outlook':
                    self.outlook_data = None
                    self._set_outlook_text('Outlook unavailable: ' + error.split(': ', 1)[-1][:300])
                    self.draw_chart()
                    continue
                if kind in ('calendar', 'news'):
                    self.calendar_note.configure(text=f"{'Calendar' if kind == 'calendar' else 'News'} unavailable: "
                                                      + error.split(': ', 1)[-1][:200])
                    continue
                if kind in ('periods', 'periods_compare', 'periods_explain'):
                    self.periods_panel.say(error.split(': ', 1)[-1][:300])
                    if kind == 'periods':
                        self.periods_panel.head.configure(text='No periods for this market right now.')
                    continue
                if kind in ('signals', 'influences', 'signals_explain', 'space'):
                    message = error.split(': ', 1)[-1][:300]
                    if kind == 'space' or kind == 'influences' and self.space_panel.mode.get() == 'links':
                        self.space_panel.failed(message)
                    if kind != 'space':
                        self.signals_panel.say(message)
                    if kind == 'signals':
                        self.signals_panel.head.configure(text='No signal lab for this market right now.')
                    continue
                if kind in ('scenarios', 'explain'):
                    self.scenario_note.configure(text=error.split(': ', 1)[-1][:300])
                    if kind == 'scenarios':
                        self.scenario_head.configure(text='No scenarios for this market right now.')
                    continue
                self.footer.configure(text=f'{kind}: {error[:200]} · retrying at the next refresh', fg=DOWN)
                continue
            getattr(self, 'show_' + kind)(value)
        self.show_live()
        self._ticks += 1
        stream = getattr(self.markets, 'live', None)
        if stream is not None and self._ticks % 3 == 0:
            from app.gui_markets_views import TickerStrip
            self.ticker.update_from(stream.snapshot(TickerStrip.stream_names()))
        if stream is not None and self._ticks % 25 == 0 and self.markets.path:
            self._announce(self.markets.check_alerts(self.live_prices(stream)))
        self._tick_id = self.after(200, self._tick)

    def live_prices(self, stream):
        """{symbol: last streamed price} for the ticker markets and the one on screen (USD), for price alerts."""
        from app.gui_markets_views import TICKER
        from app.markets_live import ws_symbol
        followed = {(symbol, asset) for symbol, asset, _label in TICKER} | {(self.symbol, self.selected_asset)}
        names = {ws_symbol(symbol, asset): symbol for symbol, asset in followed}
        prices = stream.snapshot(list(names))['prices']
        return {names[name]: float(item['price']) for name, item in prices.items()
                if name in names and item and item.get('price') is not None}

    # ------------------------------------------------------------------ keys
    def shortcut(self, event):
        """1–5 chart range, H heatmap, D 3D, S signals (again: back to the chart), W watch, A alerts; never while typing."""
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)) or event.state & 0x4:
            return None
        key = (event.char or '').lower()
        ranges = list(RANGES)
        if key.isdigit() and 1 <= int(key) <= len(ranges):
            self.span.set(ranges[int(key) - 1])
        elif key in ('h', 'd', 's'):
            view = {'h': 'heatmap', 'd': 'space', 's': 'signals'}[key]
            self.center_view.set('chart' if self.center_view.get() == view else view)
            self.switch_center()
        elif key == 'w':
            self.toggle_watch()
        elif key == 'a':
            self.alerts_dialog()
        else:
            return None
        return 'break'

    # ------------------------------------------------------------------ chart or heatmap
    def switch_center(self):
        view = self.center_view.get()
        for widget in (self.chart, self.overlay_row, self.heatmap, self.space_panel.frame, self.signals_panel.frame):
            widget.pack_forget()
        if view in ('space', 'signals'):
            self.chart_note.pack_forget()  # the chart's or heatmap's note says nothing about these views
        else:
            self.chart_note.pack(fill='x', pady=(4, 0))
        if view == 'heatmap':
            self.heatmap.pack(fill='both', expand=True, pady=(8, 0))
            self._request('heatmap', force=True)
        elif view == 'space':
            self.space_panel.frame.pack(fill='both', expand=True)
            self._request(self.space_panel.wanted(), force=True)
            self.space_panel.scene.focus_set()
        elif view == 'signals':
            self.signals_panel.frame.pack(fill='both', expand=True)
            self._request('signals', force=True)
            self._request('influences', force=True)
        else:
            self.overlay_row.pack(fill='x', pady=(6, 0))
            self.chart.pack(fill='both', expand=True, pady=(8, 0))
            self.draw_chart()
        self._layout_panes()

    def overlay_changed(self, key):
        if key == 'compare':
            self.compare_data = None
            self._request('compare', force=True)
        self.draw_chart()

    def show_compare(self, value):
        if value and value['range'] == self.span.get():
            self.compare_data = value
            self.draw_chart()

    def show_heatmap(self, groups):
        from app.gui_markets_views import events_for
        flagged = {tile['symbol']: len(events_for(self.events_data, tile['symbol'])) for group in groups for tile in group['tiles']}
        self.heatmap.show(groups, {symbol: count for symbol, count in flagged.items() if count})
        moved = [tile for group in groups for tile in group['tiles'] if tile['change'] is not None]
        if moved:
            rising = sum(tile['change'] > 0 for tile in moved)
            self.chart_note.configure(text=f"{rising} of {len(moved)} rising today · sized by volume · ◆ world events", fg=self.palette.MUTED)

    # ------------------------------------------------------------------ live
    def _stream(self):
        """Follow the market on screen live; stop following the one before."""
        stream = getattr(self.markets, 'live', None)
        if stream is None:
            return
        from app.markets_live import ws_symbol
        wanted = ws_symbol(self.symbol, self.selected_asset)
        if wanted != self._streaming:
            if self._streaming:
                stream.unwatch([self._streaming])
            stream.watch([wanted])
            self._streaming, self._live_price = wanted, None

    def show_live(self):
        stream = getattr(self.markets, 'live', None)
        if stream is None or not self._streaming:
            return
        value = stream.snapshot([self._streaming])
        item = value['prices'].get(self._streaming)
        p = self.palette
        if not item or item.get('price') is None:
            self.live_badge.configure(text={'live': '○ waiting for a trade', 'reconnecting': '○ reconnecting…'}.get(value['status'], '○ connecting…'),
                                      fg=p.MUTED)
            return
        price = float(item['price'])
        if price != self._live_price:
            colour = UP if self._live_price is not None and price > self._live_price else DOWN if self._live_price is not None else p.WHITE
            self.price.configure(text=money(price, 'USD'), fg=colour)
            self.after(600, lambda: self.price.winfo_exists() and self.price.configure(fg=p.WHITE))
            self._live_price = price
            self.live_candle(price)
            if self.scenario_data and self.scenario_data['symbol'] == self.symbol:
                self.draw_paths()
        delay = value.get('delay_ms_median')
        # The stream's own trade time, else the quote's (a thin share sends ticker updates but may not trade for hours).
        same = (self.quote_view or {}).get('symbol') == self.symbol
        last_trade = item.get('last_trade_at') or ((self.quote_view or {}).get('last_trade_at') if same else '') or ''
        try:
            quiet = (datetime.now(UTC) - datetime.fromisoformat(last_trade)).total_seconds() > 600 if last_trade else item['age_seconds'] > 120
        except ValueError:
            quiet = False
        if quiet:
            # Connected, but this market has not traded lately: say when it last did, not "LIVE".
            self.live_badge.configure(text='● streaming · last trade ' + (last_trade[11:16] + ' UTC' if last_trade else 'not seen yet'), fg=p.GOLD)
        else:
            self.live_badge.configure(text='● LIVE' + (f' · {delay} ms' if delay is not None else ''), fg=UP)

    def live_candle(self, price):
        """Move the chart's current candle with each streamed trade (redrawn at most once a second)."""
        data = self.chart_data
        if not data or data['symbol'] != self.symbol or data.get('quote') != 'USD' or not data['candles']:
            return
        last = data['candles'][-1]
        if time.time() >= last['time'] + data['interval_minutes'] * 60:
            return  # the period on screen has ended; the next refresh brings the new candle
        last['close'], last['high'], last['low'] = price, max(last['high'], price), min(last['low'], price)
        data['live'] = True
        if time.monotonic() - self._live_drawn >= 1.0 and self.center_view.get() == 'chart':
            self._live_drawn = time.monotonic()
            self.draw_chart()
            if self._hover_at:
                self.chart_hover(type('Event', (), {'x': self._hover_at[0], 'y': self._hover_at[1]})())

    # ------------------------------------------------------------------ list
    def show_list(self, value):
        overview, self.watchlist = value
        rows = overview['rows']
        stocks = bool(overview.get('stocks_view'))
        if self.view.get() == 'watch':
            rows = [row for row in rows if row['symbol'] in self.watchlist]
            if not rows:
                self.tree.delete(*self.tree.get_children())
                self.rows = {}
                self.count.configure(text='Your watchlist is empty: pick a coin or share and press ☆ Watch (or W).')
                return
        selected = self.tree.selection()
        wanted = {row['symbol'] for row in rows}
        for iid in self.tree.get_children():
            if iid not in wanted:
                self.tree.delete(iid)
        self.rows = {}
        for row in rows:
            key = row['symbol']
            if key in self.rows:
                continue
            self.rows[key] = row
            star = '★ ' if key in self.watchlist else ''
            label = f"{star}{key}" + (f" · {row['name']}" if row.get('name') else '') + (' · thin' if row.get('thin') else '')
            change = row['change_today_pct']
            values = (money(row['price'], row.get('quote') or overview['quote']), signed_pct(change), compact(row['volume_24h_quote']))
            tags = ('up',) if (change or 0) > 0 else ('down',) if (change or 0) < 0 else ()
            if self.tree.exists(key):
                self.tree.item(key, text=label, values=values, tags=tags)
                if self.tree.index(key) != len(self.rows) - 1:
                    self.tree.move(key, '', len(self.rows) - 1)
            else:
                self.tree.insert('', 'end', iid=key, text=label, values=values, tags=tags)
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected)
        shown = 'watchlist' if self.view.get() == 'watch' else f"{overview['total']:,} {overview['quote']} markets"
        self.count.configure(text=(f"{len(self.rows):,} shown · tokenized stocks and funds on Kraken · they track the real share · "
                                   "'thin' = under 5 trades in 24 h") if stocks else
                             f"{len(self.rows):,} shown · {shown} · {overview['assets_listed']:,} assets on Kraken · change since 00:00 UTC")
        self.status.set('Kraken · updated ' + datetime.now().strftime('%H:%M:%S'))
        self._update_watch_button()
        if self.markets.path:
            reached = self.markets.check_alerts({row['symbol']: row['price'] for row in rows})
            self._announce(reached)

    def select(self, symbol, asset=None):
        asset = asset or (self.rows.get(symbol) or {}).get('asset') or self.asset.get()
        if not symbol or symbol == self.symbol and asset == self.selected_asset:
            return
        self.symbol, self.quote_view, self.chart_data, self.book_data = symbol, None, None, None
        self.selected_asset, self.outlook_data, self.scenario_data = asset, None, None
        self._set_outlook_text('Loading…')
        self._stream()
        self.coin_title.configure(text=symbol)
        self.price.configure(text='…')
        self.change.configure(text='')
        self.facts.configure(text='')
        self.trades.delete(*self.trades.get_children())
        self.draw_chart()
        self.draw_book()
        self._update_watch_button()
        self._request('coin', force=True)
        self._request('chart', force=True)
        self._request('outlook', force=True)
        self._request('scenarios', force=True)
        self.show_scenarios(None)
        self._request('periods', force=True)
        self.periods_panel.show(None)
        self.signals_panel.reset()
        self.space_panel.set_current(symbol)
        for kind in ('signals', 'influences'):
            self._request(kind, force=True)

    # ------------------------------------------------------------------ coin
    def show_coin(self, value):
        quote_view, book, trades = value
        if quote_view['symbol'] != self.symbol:
            return  # an answer for a coin no longer on screen
        self._stream()
        self.quote_view, self.book_data = quote_view, book
        q = quote_view['quote']
        name = f"{quote_view['symbol']} · {quote_view['name']}" if quote_view.get('name') else quote_view['symbol']
        self.coin_title.configure(text=name)
        self.price.configure(text=money(quote_view['price'], q))
        change = quote_view['change_today_pct']
        self.change.configure(text=f"{signed_pct(change)} today (UTC)", fg=UP if (change or 0) > 0 else DOWN if (change or 0) < 0 else self.palette.MUTED)
        bid, ask = quote_view['bid'], quote_view['ask']
        # A thin tokenized share can publish a stray resting order (an ask of 0): no quote is better than a wrong one.
        sensible = bid and ask and ask >= bid and ask <= quote_view['price'] * 1.2 and bid >= quote_view['price'] * .8
        lines = [f"24 h  {money(quote_view['low_24h'], q)} – {money(quote_view['high_24h'], q)}   ·   volume {money(quote_view['volume_24h_quote'], q)}"
                 f"   ·   {quote_view['trades_24h']:,} trades   ·   "
                 + (f"bid {money(bid, q)} / ask {money(ask, q)}" if sensible else "no live bid/ask")]
        if quote_view.get('asset') == 'stock':
            exchange = quote_view.get('exchange')
            lines.append((f"Exchange price {money(exchange['price'])} ({exchange['source']}) · token {exchange['difference_pct']:+.3f}% vs exchange"
                          if exchange else 'Tokenized share on Kraken; add a free Finnhub key (Studios → Keys) for the exchange price.')
                         + (f" · last traded {quote_view['last_trade_at'][11:16]} UTC" if quote_view.get('last_trade_at') else '')
                         + (' · thin market' if quote_view.get('thin') else ''))
        else:
            check = quote_view.get('cross_check')
            lines.append(f"Coinbase {money(check['price'], 'USD')} ({check['difference_pct']:+.3f}% vs Kraken) · {signed_pct(check['change_24h_pct'])} over 24 h"
                         if check else 'Not traded on Coinbase: the Kraken price stands alone.')
        self.facts.configure(text='\n'.join(lines))
        self.draw_book()
        self.trades.delete(*self.trades.get_children())
        for index, trade in enumerate(trades['trades'][:25]):
            self.trades.insert('', 'end', iid=str(index), text=trade['at'][11:19], tags=(trade['side'],),
                               values=(money(trade['price'], q), amount(trade['amount'])))
        self._announce(quote_view.get('triggered_alerts') or [])

    def show_chart(self, value):
        if value['symbol'] == self.symbol:
            self.chart_data = value
            self.draw_chart()

    def draw_chart(self):
        canvas, p = self.chart, self.palette
        canvas.delete('all')
        width, height = max(200, canvas.winfo_width()), max(150, canvas.winfo_height())
        data = self.chart_data
        if not data:
            canvas.create_text(width / 2, height / 2, text='Loading chart…', fill=p.MUTED, font=('Segoe UI', 10))
            self.chart_note.configure(text='')
            return
        candles = data['candles']
        change = data['change_pct']
        # The next day's expected range, drawn to the right of the last candle (1D and 1W charts).
        cone = self.outlook_data if (self.outlook_data and self.outlook_data['symbol'] == data['symbol']
                                     and data['quote'] == 'USD' and not self.outlook_data.get('data_quality', {}).get('stale')
                                     and data['range'] in ('1D', '1W')) else None
        self.chart_note.configure(text=f"{signed_pct(change)} over {data['range']} · {data['interval_minutes']}-min candles · Kraken",
                                  fg=UP if (change or 0) >= 0 else DOWN)
        left, right, top, bottom = 10, width - 78, 12, height - 26
        volume_top = bottom - (bottom - top) * .18
        low, high = min(item['low'] for item in candles), max(item['high'] for item in candles)
        compare = self.compare_line(candles)
        if compare:
            low, high = min(low, *(price for _moment, price in compare)), max(high, *(price for _moment, price in compare))
        if cone:
            wide = cone['ranges']['next_day']['wide_95']
            low, high = min(low, wide[0]), max(high, wide[1])
            future = (right - left) * .14
            right_candles = right - future
        else:
            right_candles = right
        pad = (high - low) * .06 or high * .01 or 1
        low, high = low - pad, high + pad
        y = lambda price: top + (high - price) / (high - low) * (volume_top - 8 - top)
        step = (right_candles - left) / max(1, len(candles))
        grid = blend(p.LINE, p.BLACK, .3)
        for index in range(5):
            price = low + (high - low) * index / 4
            canvas.create_line(left, y(price), right, y(price), fill=grid)
            canvas.create_text(right + 6, y(price), text=money(price, data['quote']), anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
        largest = max((item['volume'] for item in candles), default=1) or 1
        body = max(1.0, step * .62)
        for index, item in enumerate(candles):
            x = left + step * (index + .5)
            colour = UP if item['close'] >= item['open'] else DOWN
            canvas.create_rectangle(x - body / 2, bottom - (bottom - volume_top) * item['volume'] / largest, x + body / 2, bottom,
                                    fill=blend(colour, p.BLACK, .6), outline='')
            canvas.create_line(x, y(item['high']), x, y(item['low']), fill=colour)
            top_body, bottom_body = sorted((y(item['open']), y(item['close'])))
            canvas.create_rectangle(x - body / 2, top_body, x + body / 2, max(bottom_body, top_body + 1), fill=colour, outline=colour)
        last = candles[-1]['close']
        legend = []
        for key, label, colour in OVERLAYS:
            if not self.overlay[key].get():
                continue
            if key == 'compare':
                if compare:
                    start, end = candles[0]['time'], candles[-1]['time']
                    points = [(left + step * .5 + (moment - start) / max(1, end - start) * step * (len(candles) - 1), y(price))
                              for moment, price in compare if start <= moment <= end]
                    if len(points) > 1:
                        canvas.create_line(*[value for point in points for value in point], fill=colour, width=1.4, dash=(5, 3))
                    legend.append((f"S&P 500 {signed_pct((compare[-1][1] / compare[0][1] - 1) * 100)} vs {data['symbol']} "
                                   f"{signed_pct(change)}", colour))
                elif self.symbol == 'SPY':
                    legend.append(('this is the S&P 500', p.MUTED))
                else:
                    legend.append(('S&P 500 loading…', p.MUTED))
                continue
            period = 20 if key == 'ma20' else 50
            averages = moving_average([item['close'] for item in candles], period)
            points = [(left + step * (index + .5), y(value)) for index, value in enumerate(averages) if value is not None]
            if len(points) > 1:
                canvas.create_line(*[value for point in points for value in point], fill=colour, width=1.4, smooth=True)
                legend.append((f"{label} {money(averages[-1], data['quote'])}", colour))
            else:
                legend.append((f"{label}: needs {period} candles", p.MUTED))
        if data.get('live'):
            legend.append(('● last candle live', UP))
        self.show_legend(legend)
        if cone:
            for key, shade in (('wide_95', .86), ('likely_68', .7)):
                lower, upper = cone['ranges']['next_day'][key]
                canvas.create_polygon(right_candles, y(cone['price']), right, y(upper), right, y(lower),
                                      fill=blend(p.GOLD, p.BLACK, shade), outline='', tags=('outlook-cone',))
            canvas.create_text(right_candles + 4, top + 2, text='next 24 h', anchor='nw', fill=p.MUTED, font=('Segoe UI', 8))
        canvas.create_line(left, y(last), right, y(last), fill=p.GOLD, dash=(3, 3))
        canvas.create_rectangle(right + 2, y(last) - 9, width - 2, y(last) + 9, fill=p.GOLD, outline='')
        canvas.create_text(right + 6, y(last), text=money(last, data['quote']), anchor='w', fill=p.BLACK, font=('Segoe UI Semibold', 8))
        for index in range(0, len(candles), max(1, len(candles) // 5)):
            stamp = datetime.fromtimestamp(candles[index]['time'], UTC)
            label = stamp.strftime('%H:%M' if data['range'] == '1D' else '%d %b')
            canvas.create_text(left + step * (index + .5), height - 12, text=label, fill=p.MUTED, font=('Segoe UI', 8))
        self._chart_geometry = (left, step, y, candles, data['quote'])
        # World events that bear on this market (Trade Center rules), marked where they were reported.
        from app.gui_markets_views import event_time, events_for
        self._event_marks = []
        start, end = candles[0]['time'], candles[-1]['time'] + (candles[-1]['time'] - candles[-2]['time'] if len(candles) > 1 else 0)
        for event in events_for(self.events_data, data['symbol']):
            moment = event_time(event)
            if moment is None or not start <= moment <= end:
                continue
            x = left + (moment - start) / max(1, end - start) * (right_candles - left)
            colour = DOWN if event.get('severity') == 'high' else p.GOLD
            canvas.create_line(x, top + 14, x, volume_top, fill=blend(colour, p.BLACK, .55), dash=(2, 4))
            canvas.create_polygon(x, top + 4, x + 6, top + 11, x, top + 18, x - 6, top + 11, fill=colour, outline=p.BLACK)
            self._event_marks.append((x, event))

    def show_legend(self, entries):
        """Rebuilt only when its words change (a live redraw every second keeps the same widgets)."""
        text = ' · '.join(words for words, _colour in entries)
        if text == self.legend_text:
            return
        self.legend_text = text
        for child in self.legend.winfo_children():
            child.destroy()
        for words, colour in entries:
            tk.Label(self.legend, text=words, bg=self.palette.PANEL, fg=colour, font=('Segoe UI', 8)).pack(side='left', padx=(0, 12))

    def compare_line(self, candles):
        """[(time, price)] of the S&P 500 rescaled to start where this market starts, for the compare overlay."""
        other = self.compare_data
        if not (self.overlay['compare'].get() and other and other['candles'] and candles and self.symbol != 'SPY'
                and other['range'] == (self.chart_data or {}).get('range')):
            return []
        base = next((item['close'] for item in other['candles'] if item['time'] >= candles[0]['time']), None)
        if not base:
            return []
        return [(item['time'], candles[0]['close'] * item['close'] / base) for item in other['candles'] if item['time'] >= candles[0]['time']]

    def chart_hover(self, event):
        canvas = self.chart
        canvas.delete('hover')
        self._hover_at = (event.x, event.y)
        geometry = getattr(self, '_chart_geometry', None)
        if not geometry or not self.chart_data:
            return
        mark = next((item for x, item in self._event_marks if abs(x - event.x) <= 7 and event.y <= 40), None)
        if mark:
            text = f"{mark['title'][:90]} · {str(mark.get('observed_at'))[:16]} UTC · {mark['sector']} (nearness in time, not proof of cause)"
            label = canvas.create_text(12, 22, text=text, anchor='nw', fill=self.palette.WHITE, font=('Segoe UI', 8), tags='hover')
            box = canvas.bbox(label)
            backing = canvas.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2, fill=self.palette.PANEL,
                                              outline=self.palette.GOLD, tags='hover')
            canvas.tag_lower(backing, label)
            return
        left, step, _y, candles, quote = geometry
        index = int((event.x - left) // step)
        if not 0 <= index < len(candles):
            return
        item = candles[index]
        x = left + step * (index + .5)
        canvas.create_line(x, 0, x, canvas.winfo_height(), fill=self.palette.MUTED, dash=(2, 3), tags='hover')
        stamp = datetime.fromtimestamp(item['time'], UTC).strftime('%d %b %H:%M UTC')
        text = (f"{stamp}  O {money(item['open'], quote)}  H {money(item['high'], quote)}  L {money(item['low'], quote)}  "
                f"C {money(item['close'], quote)}  Vol {compact(item['volume'])}")
        label = canvas.create_text(12, 4, text=text, anchor='nw', fill=self.palette.WHITE, font=('Segoe UI', 8), tags='hover')
        box = canvas.bbox(label)
        backing = canvas.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2, fill=self.palette.PANEL, outline='', tags='hover')
        canvas.tag_lower(backing, label)

    def draw_book(self):
        canvas, p = self.book, self.palette
        canvas.delete('all')
        width, height = max(200, canvas.winfo_width()), max(120, canvas.winfo_height())
        book = self.book_data
        if not book or not book['bids'] or not book['asks']:
            canvas.create_text(width / 2, height / 2, text='Loading order book…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        rows = max(4, min(15, int((height - 30) / 2 / 14)))
        asks, bids = book['asks'][:rows], book['bids'][:rows]
        total = max(sum(item['amount'] for item in asks), sum(item['amount'] for item in bids)) or 1
        middle = height / 2
        q = book['quote']
        for side, items, colour, direction in (('ask', asks, DOWN, -1), ('bid', bids, UP, 1)):
            running = 0.0
            for index, item in enumerate(items):
                running += item['amount']
                y = middle + direction * (14 + index * 14)
                canvas.create_rectangle(width - (width - 8) * running / total, y - 6, width - 4, y + 6, fill=blend(colour, p.PANEL, .78), outline='')
                canvas.create_text(10, y, text=money(item['price'], q), anchor='w', fill=colour, font=('Segoe UI', 8))
                canvas.create_text(width - 10, y, text=amount(item['amount']), anchor='e', fill=p.WHITE, font=('Segoe UI', 8))
        canvas.create_text(width / 2, middle, text=f"spread {money(book['spread'], q)} ({book['spread_pct']}%)", fill=p.MUTED, font=('Segoe UI', 8))

    # ------------------------------------------------------------------ outlook and events
    def show_outlook(self, value):
        if value['symbol'] != self.symbol:
            return
        self.outlook_data = value
        day, hour, week = value['ranges']['next_day'], value['ranges']['next_hour'], value['ranges']['next_week']
        record, trend = value['track_record'], value['trend']
        lines = [f"Next hour   {money(hour['likely_68'][0])} – {money(hour['likely_68'][1])}",
                 f"Next 24 h   {money(day['likely_68'][0])} – {money(day['likely_68'][1])}  (68 %)",
                 f"            {money(day['wide_95'][0])} – {money(day['wide_95'][1])}  (95 %)",
                 f"Next week   {money(week['likely_68'][0])} – {money(week['likely_68'][1])}  (68 %)", '',
                 f"Trend {trend['direction']} · {signed_pct(trend['change_24h_pct'])} 24 h · RSI {trend['rsi_14h']}",
                 f"Daily volatility {value['daily_volatility_pct']} %", '']
        if record.get('tested'):
            lines += [f"Volatility baseline ({record['tested']} non-overlapping past tests):",
                      f"  68 % range held {record['band_coverage_pct']} % of the time",
                      f"  coverage uncertainty (95%): {record['band_coverage_interval_pct'][0]}–{record['band_coverage_interval_pct'][1]}%",
                      f"  momentum right {record['momentum_right_pct']} %; majority baseline {record.get('baseline_right_pct')} %",
                      '', record['verdict']]
        for label, item in (('Hour', hour), ('Day', day), ('Week', week)):
            check = item.get('validation', {})
            if check:
                coverage = ' / '.join(f'{check[key]}%' if check[key] is not None else 'not measured'
                                      for key in ('coverage_68_pct', 'coverage_95_pct'))
                lines += ['', f"{label}: {item['method']} · {check['tested']} non-overlapping later tests",
                          f"Observed 68% / 95% coverage: {coverage}", check['note']]
        quality = value.get('data_quality', {})
        if quality:
            lines += ['', 'Anchor: last completed hourly close.',
                      f"{quality['contiguous_hours']} contiguous hours; {quality['forming_bars_excluded']} forming candles excluded."]
            if quality['stale']:
                lines += ['STALE HISTORY: this is not a current-market outlook.']
            if quality.get('gaps') or quality.get('invalid_bars') or quality.get('duplicates'):
                lines += [f"History cleanup: {quality['gaps']} gaps, {quality['invalid_bars']} invalid bars, "
                          f"{quality['duplicates']} duplicate bars. Only the latest continuous run is used."]
        if value.get('thin'):
            lines += ['', 'Few trades in this market: ranges are understated.']
        self._set_outlook_text('\n'.join(lines))
        self.draw_chart()

    def show_periods(self, value):
        if value['symbol'] == self.symbol:
            self.periods_panel.show(value)
            self.space_panel.show_periods(value)

    def show_signals(self, value):
        if value['symbol'] == self.symbol:
            self.signals_panel.show_signals(value)

    def show_influences(self, value):
        if value['symbol'] == self.symbol:
            self.signals_panel.show_influences(value)
            self.space_panel.show_links(value)

    def show_signals_explain(self, value):
        self.signals_panel.show_explain(value)

    def show_space(self, value):
        self.space_panel.show_space(value)

    def show_periods_compare(self, value):
        self.periods_panel.show_compare(value)

    def show_periods_explain(self, value):
        self.periods_panel.show_explain(value)

    def show_scenarios(self, value):
        if value is not None and value['symbol'] != self.symbol:
            return
        self.scenario_data = value
        tree = self.factor_tree
        tree.delete(*tree.get_children())
        if value is None:
            self.scenario_head.configure(text='Reading up to two years of daily prices…')
            self.scenario_note.configure(text='')
            self.draw_odds()
            self.draw_paths()
            return
        situation = value['situation']
        self.scenario_head.configure(text=f"Now: trend {situation['trend']} · volatility {situation['volatility']} · RSI {situation['rsi']} · "
                                          f"{value['history_days']} days · " + ('● live price' if value['live'] else 'last close')
                                          + f" · updated {value['computed_at'][11:16]} UTC")
        for index, item in enumerate(value['factors']):
            tree.insert('', 'end', iid=str(index), text=item['factor'], values=(f"{item['reading']}  {LEANING[item['leaning']][0]}",),
                        tags=(item['leaning'],))
        big = value['big_move']
        self.scenario_note.configure(text=value['summary'] + (f" A move over {big['threshold_pct']}% tomorrow: {big['now_pct']}% of similar "
                                                              f"days, {big['usual_pct']}% usually." if big.get('now_pct') is not None else '')
                                     + ' Shares of past cases, not a forecast or advice.')
        self.draw_odds()
        self.draw_paths()

    def draw_odds(self):
        """Two bars per horizon: similar days (with their 95 % range) and all days, against the 50 % line."""
        canvas, p = self.odds, self.palette
        canvas.delete('all')
        width = max(220, canvas.winfo_width())
        value = self.scenario_data
        if not value:
            canvas.create_text(width / 2, 66, text='Computing the odds…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        left, right = 92, width - 40
        x = lambda share: left + (right - left) * share / 100
        canvas.create_line(x(50), 6, x(50), 126, fill=p.MUTED, dash=(2, 3))
        canvas.create_text(x(50) + 3, 2, text='50 %', fill=p.MUTED, font=('Segoe UI', 7), anchor='nw')
        rows = (('Next day', value['next_day']), ('Next week', value['next_week']))
        for block, (label, data) in enumerate(rows):
            top = 8 + block * 60
            canvas.create_text(6, top + 6, text=label, anchor='w', fill=p.WHITE, font=('Segoe UI Semibold', 9))
            for line, (name, item, colour) in enumerate((('like now', data['now'], p.GOLD), ('usually', data['usual'], '#60A5FA'))):
                y = top + 20 + line * 16
                canvas.create_text(10, y, text=name, anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
                if item['up_pct'] is None:
                    continue
                canvas.create_rectangle(left, y - 5, x(item['up_pct']), y + 5, fill=blend(colour, p.PANEL, .35), outline='')
                low, high = item['interval_pct']
                canvas.create_line(x(low), y, x(high), y, fill=colour, width=1)
                canvas.create_line(x(low), y - 4, x(low), y + 4, fill=colour)
                canvas.create_line(x(high), y - 4, x(high), y + 4, fill=colour)
                canvas.create_text(right + 4, y, text=f"{item['up_pct']:.0f}%", anchor='w', fill=p.WHITE, font=('Segoe UI', 8))
            short = ('more often than usual' if 'more often' in data['verdict'] else 'less often than usual' if 'less often' in data['verdict']
                     else 'too few to tell' if 'too few' in data['verdict'] else 'within the noise')
            canvas.create_text(left, top + 50, text=f"{data['now']['samples']} similar cases · {short}", anchor='w',
                               fill=UP if 'more often' in data['verdict'] else DOWN if 'less often' in data['verdict'] else p.MUTED,
                               font=('Segoe UI', 7))

    def draw_paths(self):
        """Weak, middle and strong week-ahead prices, with the live price moving between them."""
        canvas, p = self.paths, self.palette
        canvas.delete('all')
        width = max(220, canvas.winfo_width())
        value = self.scenario_data
        if not value:
            return
        paths = value['paths']
        live = self._live_price if self._live_price and self.scenario_data['symbol'] == self.symbol else value['price']
        low, high = min(paths['weak']['price'], live), max(paths['strong']['price'], live)
        span = (high - low) or 1
        x = lambda price: 44 + (width - 88) * (price - low) / span
        canvas.create_rectangle(x(paths['weak']['price']), 18, x(paths['strong']['price']), 30, fill=blend(p.GOLD, p.PANEL, .75), outline='')
        for key, colour in (('weak', DOWN), ('middle', p.WHITE), ('strong', UP)):
            position = x(paths[key]['price'])
            canvas.create_line(position, 14, position, 34, fill=colour, width=2)
            canvas.create_text(position, 44, text=f"{money(paths[key]['price'])}", fill=colour, font=('Segoe UI', 7))
        canvas.create_oval(x(live) - 5, 19, x(live) + 5, 29, fill=p.GOLD, outline=p.BLACK)
        canvas.create_text(x(live), 8, text=money(live), fill=p.GOLD, font=('Segoe UI Semibold', 7))

    def factor_selected(self):
        selection = self.factor_tree.selection()
        if selection and self.scenario_data:
            item = self.scenario_data['factors'][int(selection[0])]
            self.scenario_note.configure(text=f"{item['factor']}: {item['reading']}. {item['note']}")

    def explain_scenarios(self):
        model = getattr(getattr(getattr(self.gui, 'runtime', None), 'intelligence', None), 'model', None)
        if not self.scenario_data:
            return
        if model is None:
            self.scenario_note.configure(text="HAYAT's local model is not available in this session; the numbers above stand on their own.")
            return
        value = self.scenario_data
        self.scenario_note.configure(text='HAYAT is reading the numbers…')

        def explain():
            from app.market_scenarios import explain as summarise
            return {'symbol': value['symbol'], 'text': summarise(model, value)}
        self._submit('explain', explain)

    def show_explain(self, value):
        if value['symbol'] == self.symbol:
            self.scenario_note.configure(text='HAYAT: ' + value['text'] + '\n(From the numbers above; not advice.)')

    def show_events(self, value):
        self.events_data = value
        self.draw_chart()  # its event markers
        if self.center_view.get() == 'heatmap' and self.heatmap.groups:
            self.show_heatmap(self.heatmap.groups)  # its ◆ flags
        tree = self.events_tree
        tree.delete(*tree.get_children())
        if value is None:
            self.events_note.configure(text='World events are not available in this session.')
            return
        benchmark = value.get('benchmark')
        self.events_note.configure(text=f"{len(value['sectors'])} industries touched by events of the last {value['hours']:g} h"
                                        + (f" · S&P 500 {signed_pct(benchmark['change_today_pct'])} today" if benchmark else '')
                                        + ' · a move after an event does not show the event caused it')
        for index, entry in enumerate(value['sectors']):
            sector = tree.insert('', 'end', iid=f's{index}', text=f"{entry['sector']} ({len(entry['events'])})", open=index < 3)
            for number, event in enumerate(entry['events'][:3]):
                tree.insert(sector, 'end', iid=f's{index}e{number}', text='• ' + event['title'][:60])
            for item in entry['markets']:
                change = item['change_today_pct']
                tree.insert(sector, 'end', iid=f"s{index}m{item['symbol']}", text=f"{item['symbol']}" + (' · thin' if item.get('thin') else ''),
                            values=(signed_pct(change),), tags=('up',) if (change or 0) > 0 else ('down',) if (change or 0) < 0 else ())

    def event_selected(self):
        selection = self.events_tree.selection()
        if not selection or not self.events_data:
            return
        iid = selection[0]
        entry = self.events_data['sectors'][int(iid[1:].split('e')[0].split('m')[0])]
        self.events_why.configure(text=entry['why'])
        if 'm' in iid[1:]:
            symbol = iid.split('m', 1)[1]
            self.select(symbol, 'crypto' if symbol in ('BTC', 'ETH', 'SOL') else 'stock')

    # ------------------------------------------------------------------ web3
    def show_chain(self, value):
        lines = []
        bitcoin, ethereum = value.get('bitcoin'), value.get('ethereum')
        if bitcoin:
            lines += ['Bitcoin (mempool.space)', f"  Block {bitcoin['block_height']:,}",
                      f"  Fees: {bitcoin['fee_fast_sat_vb']} fastest · {bitcoin['fee_30min_sat_vb']} in 30 min · "
                      f"{bitcoin['fee_1h_sat_vb']} in 1 h (sat/vB)", f"  {bitcoin['mempool_transactions']:,} transactions waiting"]
        if ethereum:
            lines += ['', 'Ethereum (Blockscout)',
                      f"  Gas: {ethereum['gas_slow_gwei']} / {ethereum['gas_average_gwei']} / {ethereum['gas_fast_gwei']} gwei (slow/avg/fast)",
                      f"  Block every {ethereum['block_time_seconds']} s · "
                      + (f"{ethereum['network_utilization_pct']:.0f}% of capacity used" if ethereum.get('network_utilization_pct') is not None else ''),
                      f"  {int(ethereum['transactions_today'] or 0):,} transactions today" if ethereum.get('transactions_today') else '']
        lines += ['', *('Unavailable: ' + gap for gap in value.get('gaps') or []), 'Public data only: no wallet is connected.']
        self.chain.configure(text='\n'.join(line for line in lines if line is not None))

    # ------------------------------------------------------------------ watchlist and alerts
    def _update_watch_button(self):
        watching = self.symbol in self.watchlist
        self.watch_button.configure(text='★ Watching' if watching else '☆ Watch')

    def toggle_watch(self):
        if not self.markets.path:
            return
        action = 'remove' if self.symbol in self.watchlist else 'add'
        try:
            self.watchlist = self.markets.watchlist(action, self.symbol)['symbols']
        except Exception as exc:  # shown, not raised
            self.footer.configure(text=str(exc), fg=DOWN)
            return
        self._update_watch_button()
        self._request('list', force=True)

    def alert_dialog(self):
        p = self.palette
        dialog = tk.Toplevel(self)
        dialog.title('Price alert · ' + self.symbol)
        dialog.configure(bg=p.PANEL, padx=16, pady=14)
        dialog.transient(self)
        current = (self.quote_view or {}).get('price')
        tk.Label(dialog, text=f"Tell me when {self.symbol} reaches", bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 11)).pack(anchor='w')
        target = StringVar(dialog, f"{current:.6g}" if current else '')
        entry = ttk.Entry(dialog, textvariable=target, width=20)
        entry.pack(anchor='w', pady=6)
        entry.focus_set()
        tk.Label(dialog, text=f"Now {money(current)} · above or below is worked out from the current price.\n"
                              'Checked whenever HAYAT reads prices; nothing is sent and nothing is traded.',
                 bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), justify='left').pack(anchor='w')
        message = tk.Label(dialog, text='', bg=p.PANEL, fg=DOWN, font=('Segoe UI', 9))
        message.pack(anchor='w')

        def save():
            try:
                value = float(target.get().replace(',', '').replace('$', ''))
                created = self.markets.alerts('add', self.symbol, price=value)['created']
            except Exception as exc:  # stays in the dialog
                message.configure(text=str(exc)[:160])
                return
            self.footer.configure(text=f"Price alert #{created['id']} set: {created['symbol']} {created['direction']} {money(created['price'])}",
                                  fg=self.palette.GOLD_SOFT)
            dialog.destroy()
            self.fill_alerts()
        ttk.Button(dialog, text='Set alert', style='HayatAccent.TButton', command=save).pack(anchor='e', pady=(8, 0))
        dialog.bind('<Return>', lambda _event: save())
        place_over(dialog, self, (420, 220))
        return dialog

    def alerts_dialog(self):
        """Every price alert, waiting or reached: delete one, check now, or double-click to open its market."""
        window = getattr(self, '_alerts_window', None)
        if window is not None and window.winfo_exists():
            window.lift()
            return window
        p = self.palette
        dialog = tk.Toplevel(self)
        self._alerts_window = dialog
        dialog.title('HAYAT · Price alerts')
        dialog.configure(bg=p.PANEL, padx=14, pady=12)
        dialog.transient(self)
        tk.Label(dialog, text='Price alerts', bg=p.PANEL, fg=p.WHITE, font=('Segoe UI Semibold', 12)).pack(anchor='w')
        tk.Label(dialog, text='Checked every two minutes while HAYAT runs, and live for the markets streaming in this window. '
                              'A reached alert is reported in the conversation and here; nothing is ever bought or sold.',
                 bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), justify='left', wraplength=520).pack(anchor='w', pady=(2, 8))
        tree = ttk.Treeview(dialog, columns=('when', 'target', 'state'), height=7, selectmode='browse', style='Markets.Treeview')
        for key, label, width, anchor in (('#0', 'Alert', 120, 'w'), ('when', 'Goes', 70, 'w'), ('target', 'Price', 110, 'e'),
                                          ('state', 'State', 230, 'w')):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor=anchor)
        tree.tag_configure('reached', foreground=p.GOLD)
        tree.pack(fill='both', expand=True)
        tree.bind('<Double-1>', lambda _event: tree.selection() and self.open_symbol(tree.item(tree.selection()[0], 'text').split()[1]))
        self.alerts_tree = tree
        row = tk.Frame(dialog, bg=p.PANEL)
        row.pack(fill='x', pady=(8, 0))
        ttk.Button(row, text='New alert…', style='Compact.Hayat.TButton', command=self.alert_dialog).pack(side='left')
        ttk.Button(row, text='Delete', style='Compact.Hayat.TButton', command=self.delete_alert).pack(side='left', padx=6)
        ttk.Button(row, text='Check now', style='HayatAccent.TButton', command=self.check_alerts_now).pack(side='right')
        self.alerts_message = tk.Label(dialog, text='', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w')
        self.alerts_message.pack(fill='x', pady=(6, 0))
        self.fill_alerts()
        place_over(dialog, self, (640, 470))
        return dialog

    def open_symbol(self, symbol):
        """Open a market by ticker alone: a listed row says its kind; otherwise a known share name means a stock."""
        from app.markets import STOCK_NAMES
        asset = (self.rows.get(symbol) or {}).get('asset') or ('stock' if symbol in STOCK_NAMES else 'crypto')
        self.select(symbol, asset)

    def fill_alerts(self):
        tree = getattr(self, 'alerts_tree', None)
        if tree is None or not tree.winfo_exists():
            return
        rows = self.markets.alerts('list')['alerts'] if self.markets.path else []
        tree.delete(*tree.get_children())
        for row in rows:
            reached = bool(row['triggered_at'])
            state = (f"reached {row['triggered_at'][5:16].replace('T', ' ')} UTC at {money(row['triggered_price'])}" if reached
                     else 'waiting')
            tree.insert('', 'end', iid=str(row['id']), text=f"#{row['id']} {row['symbol']}", tags=('reached',) if reached else (),
                        values=(row['direction'], money(row['price']), state))
        waiting = sum(1 for row in rows if not row['triggered_at'])
        self.alerts_message.configure(text=f"{len(rows)} alerts · {waiting} waiting" if rows else
                                      'No alerts yet: choose a market and press New alert….', fg=self.palette.MUTED)

    def delete_alert(self):
        selection = self.alerts_tree.selection()
        if not selection:
            self.alerts_message.configure(text='Choose an alert to delete.', fg=DOWN)
            return
        self.markets.alerts('delete', alert_id=int(selection[0]))
        self.fill_alerts()

    def check_alerts_now(self):
        if self.markets.path and 'alerts' not in self._pending:
            self._submit('alerts', lambda: self.markets.alerts('check'))
            self.alerts_message.configure(text='Checking the prices…', fg=self.palette.MUTED)

    def show_alerts(self, value):
        self._announce(value['triggered'])
        self.fill_alerts()
        if getattr(self, 'alerts_message', None) is not None and self.alerts_message.winfo_exists():
            reached = len(value['triggered'])
            self.alerts_message.configure(text=f"Checked {datetime.now().strftime('%H:%M:%S')}: "
                                               + (f"{reached} reached" if reached else 'none reached yet'), fg=self.palette.GOLD_SOFT)

    def _announce(self, reached):
        for alert in reached:
            self.footer.configure(text=f"Price alert #{alert['id']} reached: {alert['symbol']} {alert['direction']} "
                                       f"{money(alert['price'])} (now {money(alert['triggered_price'])})", fg=self.palette.GOLD)
            self.bell()
        if reached:
            self.fill_alerts()

    @classmethod
    def _all(cls, widget):
        """Every widget in the window (the tests check that none of them offers to trade)."""
        for child in widget.winfo_children():
            yield child
            yield from cls._all(child)

    def close(self):
        if self._closed:
            return
        self._closed = True
        stream = getattr(self.markets, 'live', None)
        if stream is not None:
            from app.gui_markets_views import TickerStrip
            stream.unwatch(([self._streaming] if self._streaming else []) + TickerStrip.stream_names())
        try:
            self.after_cancel(self._tick_id)
        except (tk.TclError, AttributeError):
            pass
        self.destroy()


def moving_average(values, period):
    """Simple moving average, None until `period` values exist."""
    result, running = [], 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        result.append(running / period if index >= period - 1 else None)
    return result


def round_number(value):
    """Kept for callers that format chart axes; finite or zero."""
    return value if isinstance(value, (int, float)) and math.isfinite(value) else 0
