"""HAYAT Command Center: everything live on one screen, each tile opening its full window.

* Markets: six markets streaming trade by trade, each with a 24-hour sparkline whose last point
  moves with the live price.
* World pulse: the last 24 hours of reported events by severity and kind, and the most severe.
* World trade: a map of the shipping chokepoints, coloured by the reports near each; alerts pulse.
* Odds board: for each market, how often it rose a week later in situations like now against
  usually, with the honest verdict (app/market_scenarios.py).
* Web3: Bitcoin fees, mempool, hashrate and difficulty; Ethereum and Layer 2 gas; DEX volume.
* Yours: connected accounts, actions waiting for your confirmation, price alerts and watched
  addresses.
* Calendar: the next scheduled events that move markets (Fed decisions, the jobs report,
  earnings), counting down, with how much markets moved on past ones.
* News & rumours: business and crypto headlines that name markets; rumours marked unconfirmed.

Read-only: nothing here trades, sends or signs. Requests run one at a time in the background,
each on its own cadence; the clock, the live prices and the pulses update several times a second.
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
import tkinter as tk
from datetime import UTC, datetime
from pathlib import Path
from tkinter import ttk
from typing import Any

from app.gui_intelligence_parts import blend
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY

UP, DOWN, AMBER, CYAN = '#4ADE80', '#F05D5E', '#E8C46A', '#38BDF8'
# SPY and GLD are funds: their prices are a share of the fund, not the index level or an ounce of gold,
# so the label names the fund ("S&P 500 $762" read as the index, which stands near ten times that).
MARKETS = (('BTC', 'crypto', 'Bitcoin'), ('ETH', 'crypto', 'Ether'), ('SOL', 'crypto', 'Solana'),
           ('SPY', 'stock', 'S&P 500 fund (SPY)'), ('NVDA', 'stock', 'Nvidia'), ('GLD', 'stock', 'Gold fund (GLD)'))
ODDS = (('BTC', 'crypto'), ('ETH', 'crypto'), ('SPY', 'stock'))
REFRESH = {'spark': 300, 'world': 120, 'trade': 300, 'odds': 600, 'web3': 60, 'mine': 20, 'calendar': 1800, 'news': 300}
STATUS_COLOURS = {'alert': DOWN, 'watch': AMBER, 'calm': UP}
LAND = Path(__file__).resolve().parent / 'assets' / 'world-land.json'


def money(value: Any) -> str:
    from app.markets import money as format_money
    return format_money(value)


def dollars(value: Any) -> str:
    from app.web3_chat import dollars as format_dollars
    return format_dollars(value)


class Tile(tk.Frame):
    """A titled card with a canvas body; a click anywhere opens its full window."""

    def __init__(self, parent: Any, palette: Any, title: str, hint: str, opener: Any = None, height: int = 300) -> None:
        super().__init__(parent, bg=palette.PANEL, padx=12, pady=10, highlightthickness=1,
                         highlightbackground=blend(palette.LINE, palette.PANEL, .4))
        self.p, self.opener = palette, opener
        head = tk.Frame(self, bg=palette.PANEL)
        head.pack(fill='x')
        self.title = tk.Label(head, text=title.upper(), bg=palette.PANEL, fg=CYAN, font=('Segoe UI Semibold', 9), anchor='w')
        self.title.pack(side='left')
        self.stamp = tk.Label(head, text='', bg=palette.PANEL, fg=palette.MUTED, font=('Segoe UI', 8))
        self.stamp.pack(side='right')
        self.hint = tk.Label(self, text=hint, bg=palette.PANEL, fg=palette.MUTED, font=('Segoe UI', 8), anchor='w')
        self.hint.pack(fill='x')
        self.canvas = tk.Canvas(self, bg=palette.PANEL, highlightthickness=0, height=height)
        self.canvas.pack(fill='both', expand=True, pady=(6, 0))
        if opener is not None:
            for widget in (self, head, self.title, self.hint):
                widget.configure(cursor='hand2')
                widget.bind('<Button-1>', lambda _event: opener())
            self.bind('<Enter>', lambda _event: self.configure(highlightbackground=CYAN))
            self.bind('<Leave>', lambda _event: self.configure(highlightbackground=blend(palette.LINE, palette.PANEL, .4)))

    def updated(self, text: str = '') -> None:
        self.stamp.configure(text=text or 'updated ' + datetime.now().strftime('%H:%M:%S'))


class CommandCenter(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p, self.runtime = gui, type(gui), gui.runtime
        p = self.p
        self._closed = False
        self._jobs: queue.Queue = queue.Queue()
        self._results: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._due = {key: 0.0 for key in REFRESH}
        self.data: dict[str, Any] = {}
        self.sparks: dict[str, list[float]] = {}
        self.last_price: dict[str, float] = {}
        self.flash: dict[str, tuple[str, float]] = {}
        self._phase = 0.0
        self._land: list | None = None
        self.title('HAYAT · Command Center')
        self.configure(bg=p.BLACK)
        self.minsize(1100, 700)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        self.bind('<F5>', lambda _event: self.refresh())
        self._build()
        stream = getattr(getattr(self.runtime, 'markets', None), 'live', None)
        if stream is not None:
            stream.watch(self.stream_names())
        place_over(self, gui.root, (1500, 920))
        self.deiconify()
        self._worker = threading.Thread(target=self._work, name='hayat-command-center', daemon=True)
        self._worker.start()
        self._tick_id = self.after(60, self._tick)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        p = self.p
        header = tk.Frame(self, bg=p.BLACK, padx=18, pady=10)
        header.pack(fill='x')
        self.pulse = tk.Canvas(header, width=26, height=26, bg=p.BLACK, highlightthickness=0)
        self.pulse.pack(side='left', padx=(0, 10))
        words = tk.Frame(header, bg=p.BLACK)
        words.pack(side='left', fill='x', expand=True)
        tk.Label(words, text='HAYAT Command Center', bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 20), anchor='w').pack(fill='x')
        self.subtitle = tk.Label(words, text='Markets, the world, trade routes, odds, chains and your accounts, live · click any tile to open it',
                                 bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w')
        self.subtitle.pack(fill='x')
        right = tk.Frame(header, bg=p.BLACK)
        right.pack(side='right')
        self.clock = tk.Label(right, text='', bg=p.BLACK, fg=p.WHITE, font=('Consolas', 15, 'bold'), anchor='e')
        self.clock.pack(anchor='e')
        self.clock_utc = tk.Label(right, text='', bg=p.BLACK, fg=p.MUTED, font=('Consolas', 9), anchor='e')
        self.clock_utc.pack(anchor='e')
        ttk.Button(header, text='Refresh all (F5)', style='HayatAccent.TButton', command=self.refresh).pack(side='right', padx=14)
        grid = tk.Frame(self, bg=p.BLACK)
        grid.pack(fill='both', expand=True, padx=14, pady=(0, 6))
        for column in range(4):
            grid.columnconfigure(column, weight=1, uniform='tiles')
        for row in range(2):
            grid.rowconfigure(row, weight=1, uniform='rows')
        opener = lambda name: (lambda: getattr(self.gui, name)()) if callable(getattr(self.gui, name, None)) else None
        self.tiles = {
            'markets': Tile(grid, p, 'Markets · live', 'Streaming trade by trade · 24-hour sparklines', opener('_open_markets')),
            'world': Tile(grid, p, 'World pulse', 'Events reported in the last 24 hours', opener('_open_intelligence')),
            'trade': Tile(grid, p, 'World trade', 'Shipping chokepoints · reports nearby (not ship traffic)', opener('_open_atlas')),
            'odds': Tile(grid, p, 'Odds board', 'Higher a week later in situations like now · vs usually', opener('_open_markets')),
            'web3': Tile(grid, p, 'Chains & DeFi', 'Bitcoin, Ethereum and Layer 2s · DEX volume', opener('_open_web3')),
            'mine': Tile(grid, p, 'Yours', 'Accounts, confirmations, alerts and watched addresses', opener('_open_accounts')),
            'calendar': Tile(grid, p, 'Calendar', 'Scheduled events that move markets · past reactions', opener('_open_markets')),
            'news': Tile(grid, p, 'News & rumours', 'Headlines naming markets · rumours unconfirmed', opener('_open_markets')),
        }
        for index, key in enumerate(('markets', 'world', 'trade', 'calendar', 'odds', 'web3', 'news', 'mine')):
            self.tiles[key].grid(row=index // 4, column=index % 4, sticky='nsew', padx=4, pady=4)
            self.tiles[key].canvas.bind('<Configure>', lambda _event, key=key: self.draw(key))
        self.tiles['trade'].canvas.bind('<Motion>', self.trade_hover)
        self.tiles['trade'].canvas.bind('<Leave>', lambda _event: self.tiles['trade'].canvas.delete('hover'))
        self.tiles['trade'].canvas.bind('<Button-1>', lambda _event: self.tiles['trade'].opener and self.tiles['trade'].opener())
        self.tiles['markets'].canvas.bind('<Button-1>', lambda _event: self.tiles['markets'].opener and self.tiles['markets'].opener())
        self.footer = tk.Label(self, text='Read-only overview: nothing here trades, sends or signs. Sources: Kraken, HAYAT\'s public '
                                          'feeds, mempool.space, Blockscout, DefiLlama, your own accounts.',
                               bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8), anchor='w', padx=14, pady=4)
        self.footer.pack(side='bottom', fill='x')

    @staticmethod
    def stream_names() -> list[str]:
        from app.markets_live import ws_symbol
        return [ws_symbol(symbol, asset) for symbol, asset, _label in MARKETS]

    # ------------------------------------------------------------------ background work
    def refresh(self) -> None:
        for key in REFRESH:
            self._due[key] = 0.0

    def _work(self) -> None:
        while not self._closed:
            try:
                kind, call = self._jobs.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                self._results.put((kind, call(), None))
            except Exception as exc:  # shown on the tile, never raised into Tk
                self._results.put((kind, None, f'{type(exc).__name__}: {exc}'))

    def _calls(self) -> dict[str, Any]:
        runtime = self.runtime
        markets, intelligence, web3 = getattr(runtime, 'markets', None), getattr(runtime, 'intelligence', None), getattr(runtime, 'web3', None)
        calls: dict[str, Any] = {}
        if markets is not None:
            def spark():
                result = {}
                for symbol, asset, _label in MARKETS:
                    try:
                        result[symbol] = [candle['close'] for candle in markets.candles(symbol, 60, asset)[2][-24:]]
                    except Exception:  # one market missing leaves the others
                        result[symbol] = []
                return result
            calls['spark'] = spark

            def odds():
                from app.market_events import events_and_markets
                from app.market_scenarios import scenarios
                try:
                    events = events_and_markets(intelligence, markets) if intelligence is not None else None
                except Exception:
                    events = None
                rows = []
                for symbol, asset in ODDS:
                    try:
                        value = scenarios(markets, symbol, asset, events)
                        rows.append({'symbol': symbol, 'week': value['next_week'], 'situation': value['situation'], 'live': value['live']})
                    except Exception as exc:
                        rows.append({'symbol': symbol, 'error': str(exc)[:80]})
                return rows
            calls['odds'] = odds
            from app.market_calendar import MarketCalendar
            from app.market_news import MarketNews
            if getattr(markets, 'fetcher', None) is not None:
                calendar = getattr(markets, 'calendar', None) or MarketCalendar(markets.fetcher, markets)
                news = getattr(markets, 'news', None) or MarketNews(markets.fetcher, markets)
                markets.calendar, markets.news = calendar, news
                calls['calendar'] = lambda: calendar.upcoming(60)
                calls['news'] = lambda: news.news('', 48, False, 12)
        if intelligence is not None:
            def world():
                # Totals come from the store's own counts over every matching event: one capped list of 1,000 rows was
                # all road traffic and showed nothing else.
                every = intelligence.dashboard(since_hours=24, limit=1)
                kinds = {kind: count for kind, count in every['aggregates']['counts_by_category'].items() if kind != 'cameras'}
                cameras = every['aggregates']['counts_by_category'].get('cameras', 0)
                high_rows = [row for row in intelligence.dashboard(since_hours=24, min_severity='high', limit=1000)['events']
                             if row.get('category') != 'cameras']
                medium_up = intelligence.dashboard(since_hours=24, min_severity='medium', limit=1)['total_matching']
                total = every['total_matching'] - cameras
                high = len(high_rows)
                severity = {'high': high, 'medium': max(0, medium_up - high), 'low': max(0, total - medium_up)}
                newest = sorted(high_rows, key=lambda row: row.get('observed_at') or '', reverse=True)
                # The world first: road closures are many and local, so they come after other kinds.
                top = [row for row in newest if row.get('category') != 'traffic'] + [row for row in newest if row.get('category') == 'traffic']
                return {'total': total, 'severity': severity, 'kinds': sorted(kinds.items(), key=lambda item: -item[1])[:6],
                        'top': [{'title': row.get('title', ''), 'category': row.get('category', '')} for row in top[:5]]}
            calls['world'] = world

            def trade():
                from app.trade_routes import chokepoints
                return chokepoints(intelligence, markets)
            calls['trade'] = trade
        if web3 is not None:
            def chains():
                value = {'networks': web3.networks()}
                try:
                    value['dex'] = web3.defi(5).get('dex')
                except Exception:
                    value['dex'] = None
                return value
            calls['web3'] = chains
        calls['mine'] = self.mine
        return calls

    def mine(self) -> dict[str, Any]:
        runtime = self.runtime
        value: dict[str, Any] = {}
        accounts = getattr(runtime, 'accounts', None)
        if accounts is not None:
            value['accounts'] = [{'name': row['name'], 'connected': row['connected'], 'as': row['connected_as']}
                                 for row in accounts.status()['accounts']]
        gate = getattr(runtime, 'confirmations', None)
        value['pending'] = len(gate.pending()) if gate is not None else None
        markets = getattr(runtime, 'markets', None)
        if markets is not None and getattr(markets, 'path', None):
            alerts = markets.alerts('list')['alerts']
            value['alerts'] = {'waiting': sum(1 for row in alerts if not row['triggered_at']), 'reached': sum(1 for row in alerts if row['triggered_at'])}
        web3 = getattr(runtime, 'web3', None)
        if web3 is not None and getattr(web3, 'path', None):
            value['watched'] = len(web3.watchlist('list')['addresses'])
        email = getattr(runtime, 'email', None)
        if email is not None:
            try:
                value['email'] = email.status()['ready']
            except Exception:
                value['email'] = None
        return value

    def _schedule(self) -> None:
        now = time.monotonic()
        for kind, call in self._calls().items():
            if kind not in self._pending and now >= self._due[kind]:
                self._pending.add(kind)
                self._due[kind] = now + REFRESH[kind]
                self._jobs.put((kind, call))

    def _tick(self) -> None:
        if self._closed:
            return
        self._schedule()
        while not self._results.empty():
            kind, value, error = self._results.get_nowait()
            self._pending.discard(kind)
            tile = self.tiles['markets' if kind == 'spark' else kind]
            if error:
                tile.updated('unavailable: ' + error.split(': ', 1)[-1][:60])
                continue
            if kind == 'spark':
                self.sparks = value
            else:
                self.data[kind] = value
            tile.updated()
            self.draw('markets' if kind == 'spark' else kind)
        self._phase += .06
        now = datetime.now()
        self.clock.configure(text=now.strftime('%H:%M:%S'))
        self.clock_utc.configure(text=datetime.now(UTC).strftime('%a %d %b · %H:%M:%S UTC'))
        self.draw_pulse()
        self.live_markets()
        if int(self._phase * 10) % 2 == 0:
            self.animate_trade()
        self._tick_id = self.after(100, self._tick)

    # ------------------------------------------------------------------ drawing
    def draw(self, key: str) -> None:
        getattr(self, 'draw_' + key)()

    def draw_pulse(self) -> None:
        canvas = self.pulse
        canvas.delete('all')
        stream = getattr(getattr(self.runtime, 'markets', None), 'live', None)
        live = stream is not None and getattr(stream, 'status', '') == 'live'
        colour = UP if live else AMBER
        grow = (math.sin(self._phase * 2) + 1) / 2
        canvas.create_oval(13 - 6 - 6 * grow, 13 - 6 - 6 * grow, 13 + 6 + 6 * grow, 13 + 6 + 6 * grow, outline=blend(colour, self.p.BLACK, .3 + .5 * grow))
        canvas.create_oval(7, 7, 19, 19, fill=colour, outline='')

    def live_markets(self) -> None:
        stream = getattr(getattr(self.runtime, 'markets', None), 'live', None)
        if stream is None:
            return
        snapshot = stream.snapshot(self.stream_names())
        prices = snapshot.get('prices') or {}
        changed = False
        for (symbol, _asset, _label), name in zip(MARKETS, self.stream_names()):
            item = prices.get(name) or {}
            if item.get('price') is None:
                continue
            price = float(item['price'])
            before = self.last_price.get(symbol)
            if before is not None and price != before:
                self.flash[symbol] = (UP if price > before else DOWN, time.monotonic())
            if price != before:
                self.last_price[symbol] = price
                self.data.setdefault('change', {})[symbol] = item.get('change_pct')
                changed = True
        delay = snapshot.get('delay_ms_median')
        if delay is not None:
            self.tiles['markets'].updated(f'● live · {delay} ms')
        if changed or any(time.monotonic() - moment < .8 for _colour, moment in self.flash.values()):
            self.draw_markets()

    def draw_markets(self) -> None:
        canvas, p = self.tiles['markets'].canvas, self.p
        canvas.delete('all')
        width, height = max(300, canvas.winfo_width()), max(200, canvas.winfo_height())
        row = height / len(MARKETS)
        for index, (symbol, _asset, label) in enumerate(MARKETS):
            top = index * row
            price = self.last_price.get(symbol) or (self.sparks.get(symbol) or [None])[-1]
            change = (self.data.get('change') or {}).get(symbol)
            colour, moment = self.flash.get(symbol, (p.WHITE, 0))
            fresh = time.monotonic() - moment < .8
            canvas.create_text(4, top + row / 2 - 8, text=label, anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
            canvas.create_text(4, top + row / 2 + 8, text=money(price) if price else '…', anchor='w',
                               fill=colour if fresh else p.WHITE, font=('Segoe UI Semibold', 12))
            if change is not None:
                canvas.create_text(width * .42, top + row / 2, text=f'{change:+.2f}%', anchor='w',
                                   fill=UP if change > 0 else DOWN if change < 0 else p.MUTED, font=('Segoe UI Semibold', 9))
            series = list(self.sparks.get(symbol) or [])
            if price and series:
                series[-1] = price
            if len(series) > 1:
                left, right = width * .58, width - 6
                low, high = min(series), max(series)
                span = (high - low) or 1
                points = []
                for position, value in enumerate(series):
                    points += [left + (right - left) * position / (len(series) - 1), top + row - 8 - (row - 16) * (value - low) / span]
                trend = UP if series[-1] >= series[0] else DOWN
                canvas.create_polygon(left, top + row - 8, *points, right, top + row - 8, fill=blend(trend, p.PANEL, .85), outline='')
                canvas.create_line(*points, fill=trend, width=1.6, smooth=True)
                canvas.create_oval(points[-2] - 3, points[-1] - 3, points[-2] + 3, points[-1] + 3, fill=trend, outline='')
            if index:
                canvas.create_line(0, top, width, top, fill=blend(p.LINE, p.PANEL, .6))

    def draw_world(self) -> None:
        canvas, p = self.tiles['world'].canvas, self.p
        canvas.delete('all')
        width, height = max(300, canvas.winfo_width()), max(200, canvas.winfo_height())
        value = self.data.get('world')
        if not value:
            canvas.create_text(width / 2, height / 2, text='Reading the world feeds…' if getattr(self.runtime, 'intelligence', None)
                               else 'Global intelligence is not running in this session.', fill=p.MUTED, font=('Segoe UI', 9))
            return
        for index, (label, key, colour) in enumerate((('High', 'high', DOWN), ('Medium', 'medium', AMBER), ('Other', 'low', p.MUTED))):
            x = 8 + index * (width - 16) / 3
            canvas.create_text(x, 16, text=f"{value['severity'][key]:,}", anchor='w', fill=colour, font=(DISPLAY, 20))
            canvas.create_text(x, 40, text=label, anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
        largest = max([count for _kind, count in value['kinds']] or [1])
        for index, (kind, count) in enumerate(value['kinds']):
            y = 64 + index * 16
            bar = (width * .55) * count / largest
            canvas.create_rectangle(110, y - 5, 110 + bar, y + 5, fill=blend(CYAN, p.PANEL, .45), outline='')
            canvas.create_text(4, y, text=kind.replace('_', ' ')[:16], anchor='w', fill=p.WHITE, font=('Segoe UI', 8))
            canvas.create_text(114 + bar, y, text=f'{count:,}', anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
        y = 72 + len(value['kinds']) * 16
        canvas.create_text(4, y, text='Most severe, newest first', anchor='w', fill=CYAN, font=('Segoe UI Semibold', 8))
        for index, item in enumerate(value['top']):
            if y + 18 + index * 18 > height - 6:
                break
            line = y + 18 + index * 18
            tag = canvas.create_text(4, line, text=f"▲ {(item['category'] or 'event').replace('_', ' ')}", anchor='w', fill=DOWN, font=('Segoe UI Semibold', 8))
            start = canvas.bbox(tag)[2] + 6
            canvas.create_text(start, line, text=item['title'][:int((width - start) / 6)], anchor='w', fill=p.WHITE, font=('Segoe UI', 8))
        if not value['top']:
            canvas.create_text(4, y + 18, text='No high-severity reports in the last 24 hours.', anchor='w', fill=p.MUTED, font=('Segoe UI', 8))

    def land(self) -> list:
        if self._land is None:
            try:
                self._land = json.loads(LAND.read_text(encoding='utf-8'))['polygons']
            except (OSError, ValueError, KeyError):
                self._land = []
        return self._land

    def draw_trade(self) -> None:
        canvas, p = self.tiles['trade'].canvas, self.p
        canvas.delete('all')
        width, height = max(300, canvas.winfo_width()), max(200, canvas.winfo_height())
        map_height = height - 40
        project = lambda lon, lat: (width * (lon + 180) / 360, map_height * (82 - lat) / 140)
        for ring in self.land():
            if len(ring) < 4:
                continue
            points = [value for lon, lat in ring[::2] for value in project(lon, lat)]
            if len(points) >= 6:
                canvas.create_polygon(*points, fill=blend(p.LINE, p.PANEL, .55), outline='')
        for lat in (-30, 0, 30, 60):
            y = project(0, lat)[1]
            canvas.create_line(0, y, width, y, fill=blend(p.LINE, p.PANEL, .82), dash=(2, 6))
        value = self.data.get('trade')
        self._trade_points = []
        if not value:
            canvas.create_text(width / 2, map_height / 2, text='Reading the reports near each chokepoint…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        counts = {'alert': 0, 'watch': 0, 'calm': 0}
        for row in value['chokepoints']:
            x, y = project(row['longitude'], row['latitude'])
            colour = STATUS_COLOURS[row['status']]
            counts[row['status']] += 1
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=colour, outline=p.BLACK, tags=('dot',))
            if row['status'] == 'alert':
                canvas.create_oval(x - 8, y - 8, x + 8, y + 8, outline=colour, width=2, tags=('ring', f"ring:{x}:{y}"))
            self._trade_points.append((x, y, row))
        canvas.create_text(4, height - 22, text=f"▲ {counts['alert']} alert   ● {counts['watch']} reports nearby   ○ {counts['calm']} calm",
                           anchor='w', fill=p.WHITE, font=('Segoe UI', 8))
        canvas.create_text(4, height - 8, text='Hover a dot · click to open the atlas', anchor='w', fill=p.MUTED, font=('Segoe UI', 8))

    def animate_trade(self) -> None:
        canvas = self.tiles['trade'].canvas
        grow = 8 + 6 * (math.sin(self._phase * 3) + 1) / 2
        for item in canvas.find_withtag('ring'):
            tag = next(name for name in canvas.gettags(item) if name.startswith('ring:'))
            _name, x, y = tag.split(':')
            x, y = float(x), float(y)
            canvas.coords(item, x - grow, y - grow, x + grow, y + grow)

    def trade_hover(self, event: Any) -> None:
        canvas, p = self.tiles['trade'].canvas, self.p
        canvas.delete('hover')
        point = next((row for x, y, row in getattr(self, '_trade_points', []) if abs(x - event.x) <= 8 and abs(y - event.y) <= 8), None)
        if not point:
            return
        text = f"{point['name']} · {point['event_count']} report{'s' if point['event_count'] != 1 else ''} nearby" + (
            f"\n{point['events'][0]['title'][:60]}" if point['events'] else '') + '\nLinked: ' + ', '.join(
            item['symbol'] + (f" {item['change_today_pct']:+.1f}%" if item.get('change_today_pct') is not None else '') for item in point['markets'][:4])
        label = canvas.create_text(min(event.x + 12, canvas.winfo_width() - 230), event.y + 10, text=text, anchor='nw', fill=p.WHITE,
                                   font=('Segoe UI', 8), tags='hover')
        box = canvas.bbox(label)
        backing = canvas.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2, fill=p.BLACK, outline=CYAN, tags='hover')
        canvas.tag_lower(backing, label)

    def draw_odds(self) -> None:
        canvas, p = self.tiles['odds'].canvas, self.p
        canvas.delete('all')
        width, height = max(300, canvas.winfo_width()), max(200, canvas.winfo_height())
        rows = self.data.get('odds')
        if not rows:
            canvas.create_text(width / 2, height / 2, text='Measuring odds from two years of daily prices…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        block = (height - 20) / len(rows)
        left, right = 70, width - 50
        x = lambda share: left + (right - left) * share / 100
        for index, row in enumerate(rows):
            top = 4 + index * block
            canvas.create_text(4, top + 12, text=row['symbol'], anchor='w', fill=p.WHITE, font=('Segoe UI Semibold', 12))
            if row.get('error'):
                canvas.create_text(left, top + 12, text=row['error'], anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
                continue
            week = row['week']
            canvas.create_line(x(50), top + 4, x(50), top + 44, fill=p.MUTED, dash=(2, 3))
            for line, (name, item, colour) in enumerate((('now', week['now'], AMBER), ('usual', week['usual'], CYAN))):
                y = top + 12 + line * 16
                if item['up_pct'] is None:
                    continue
                canvas.create_rectangle(left, y - 5, x(item['up_pct']), y + 5, fill=blend(colour, p.PANEL, .35), outline='')
                low, high = item['interval_pct']
                canvas.create_line(x(low), y, x(high), y, fill=colour)
                canvas.create_text(right + 4, y, text=f"{item['up_pct']:.0f}%", anchor='w', fill=p.WHITE, font=('Segoe UI', 8))
                canvas.create_text(left - 4, y, text=name, anchor='e', fill=p.MUTED, font=('Segoe UI', 7))
            verdict = week['verdict']
            short = ('more often than usual' if 'more often' in verdict else 'less often than usual' if 'less often' in verdict
                     else 'too few cases' if 'too few' in verdict else 'within the noise')
            colour = UP if 'more often' in verdict else DOWN if 'less often' in verdict else p.MUTED
            situation = row['situation']
            line = (f"{short} · {week['now']['samples']} cases · trend {situation['trend']}, volatility {situation['volatility']}"
                    + (' · live' if row['live'] else ''))
            canvas.create_text(left, top + 46, text=line[:max(20, int((width - left) / 5.6))],
                               anchor='w', fill=colour, font=('Segoe UI', 8))
        canvas.create_text(4, height - 6, text='Shares of past cases, not forecasts.', anchor='w', fill=p.MUTED, font=('Segoe UI', 7))

    def draw_web3(self) -> None:
        canvas, p = self.tiles['web3'].canvas, self.p
        canvas.delete('all')
        width, height = max(300, canvas.winfo_width()), max(200, canvas.winfo_height())
        value = self.data.get('web3')
        if not value:
            canvas.create_text(width / 2, height / 2, text='Reading the chains…' if getattr(self.runtime, 'web3', None)
                               else 'Web3 is not available in this session.', fill=p.MUTED, font=('Segoe UI', 9))
            return
        y = 10
        for network in value['networks']['networks']:
            colour = '#F7931A' if network['key'] == 'bitcoin' else '#8B9CFF'
            canvas.create_text(4, y, text=network['name'], anchor='w', fill=colour, font=('Segoe UI Semibold', 10))
            if network['key'] == 'bitcoin':
                big = f"block {network['block_height']:,}"
                small = (f"{network['fee_fast_sat_vb']} sat/vB fastest · {network['mempool_transactions']:,} waiting"
                         + (f" · {network['hashrate_eh_s']:,} EH/s" if network.get('hashrate_eh_s') else ''))
            else:
                big = f"{network['gas_average_gwei']} gwei"
                small = f"a block every {network['block_time_seconds']} s" + (
                    f" · {network['utilization_pct']:.0f}% full" if network.get('utilization_pct') is not None else '')
            canvas.create_text(width * .36, y, text=big, anchor='w', fill=p.WHITE, font=('Segoe UI Semibold', 10))
            canvas.create_text(width * .36, y + 15, text=small, anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
            load = network.get('utilization_pct')
            if load is not None:
                canvas.create_rectangle(4, y + 12, 4 + (width * .3) * min(100, load) / 100, y + 17, fill=blend(colour, p.PANEL, .3), outline='')
            y += 38
        dex = value.get('dex')
        if dex:
            canvas.create_text(4, y, text='DEX volume, 24 h', anchor='w', fill=CYAN, font=('Segoe UI Semibold', 9))
            canvas.create_text(width * .36, y, text=dollars(dex['volume_24h_usd']), anchor='w', fill=p.WHITE, font=('Segoe UI Semibold', 10))
            if dex.get('change_1d_pct') is not None:
                canvas.create_text(width * .62, y, text=f"{dex['change_1d_pct']:+.1f}% on the day", anchor='w',
                                   fill=UP if dex['change_1d_pct'] > 0 else DOWN, font=('Segoe UI', 8))
            canvas.create_text(4, y + 16, text='Largest: ' + ', '.join(item['name'] for item in dex['top'][:3]), anchor='w',
                               fill=p.MUTED, font=('Segoe UI', 8))

    def draw_mine(self) -> None:
        canvas, p = self.tiles['mine'].canvas, self.p
        canvas.delete('all')
        width = max(300, canvas.winfo_width())
        value = self.data.get('mine')
        self._mine_spots = []
        if value is None:
            return
        y = 10
        canvas.create_text(4, y, text='Accounts', anchor='w', fill=CYAN, font=('Segoe UI Semibold', 9))
        x, y = 4, y + 20
        for account in value.get('accounts') or []:
            colour = UP if account['connected'] else p.MUTED
            text = ('● ' if account['connected'] else '○ ') + account['name']
            item = canvas.create_text(x + 8, y, text=text, anchor='w', fill=colour, font=('Segoe UI', 9))
            box = canvas.bbox(item)
            canvas.create_rectangle(box[0] - 6, box[1] - 3, box[2] + 6, box[3] + 3, outline=blend(colour, p.PANEL, .4))
            x = box[2] + 16
            if x > width - 90:
                x, y = 4, y + 26
        y += 34
        rows = []
        if value.get('pending') is not None:
            rows.append(('Waiting for your confirmation', f"{value['pending']}", AMBER if value['pending'] else p.MUTED, '_show_confirmation'))
        if value.get('alerts') is not None:
            rows.append(('Price alerts', f"{value['alerts']['waiting']} waiting · {value['alerts']['reached']} reached", p.WHITE, '_open_markets'))
        if value.get('watched') is not None:
            rows.append(('Watched addresses', f"{value['watched']}", p.WHITE, '_open_web3'))
        if value.get('email') is not None:
            rows.append(('Email sending', 'ready' if value['email'] else 'not set up', UP if value['email'] else p.MUTED, '_open_business'))
        for label, reading, colour, method in rows:
            canvas.create_text(4, y, text=label, anchor='w', fill=p.MUTED, font=('Segoe UI', 9))
            canvas.create_text(width - 8, y, text=reading, anchor='e', fill=colour, font=('Segoe UI Semibold', 10))
            self._mine_spots.append((y, method))
            y += 24
        if not hasattr(self, '_mine_bound'):
            self._mine_bound = True
            canvas.bind('<Button-1>', self.mine_click)

    def mine_click(self, event: Any) -> None:
        for y, method in getattr(self, '_mine_spots', []):
            if abs(event.y - y) <= 11:
                opener = getattr(self.gui, method, None)
                if callable(opener):
                    opener()
                return
        if self.tiles['mine'].opener:
            self.tiles['mine'].opener()

    def draw_calendar(self) -> None:
        canvas, p = self.tiles['calendar'].canvas, self.p
        canvas.delete('all')
        width, height = max(260, canvas.winfo_width()), max(200, canvas.winfo_height())
        value = self.data.get('calendar')
        if not value:
            canvas.create_text(width / 2, height / 2, text='Reading the Fed calendar…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        events = value['events']
        if not events:
            canvas.create_text(width / 2, height / 2, text='Nothing scheduled in the next 60 days.', fill=p.MUTED, font=('Segoe UI', 9))
            return
        first = events[0]
        days = first['days_until']
        canvas.create_text(4, 18, text='today' if days == 0 else 'tomorrow' if days == 1 else f'{days} days', anchor='w',
                           fill=AMBER if days <= 3 else p.WHITE, font=(DISPLAY, 22))
        canvas.create_text(4, 44, text=first['title'][:40], anchor='w', fill=p.WHITE, font=('Segoe UI Semibold', 9))
        canvas.create_text(4, 60, text=f"{first['date']} · {first.get('time', '')}", anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
        x = 4
        for row in (first.get('effects') or [])[:4]:
            if not row.get('ratio'):
                continue
            colour = AMBER if row['ratio'] >= 1.2 else p.MUTED
            item = canvas.create_text(x + 6, 82, text=f"{row['symbol']} {row['ratio']}×", anchor='w', fill=colour, font=('Segoe UI', 8))
            box = canvas.bbox(item)
            canvas.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2, outline=blend(colour, p.PANEL, .4))
            x = box[2] + 12
        canvas.create_text(4, 100, text='past moves vs usual days', anchor='w', fill=p.MUTED, font=('Segoe UI', 7))
        y = 124
        for item in events[1:7]:
            if y > height - 24:
                break
            when = 'tomorrow' if item['days_until'] == 1 else f"in {item['days_until']} d"
            canvas.create_text(4, y, text=when, anchor='w', fill=AMBER if item['days_until'] <= 3 else CYAN, font=('Segoe UI Semibold', 8))
            canvas.create_text(64, y, text=item['title'].replace(' (nonfarm payrolls)', '')[:int((width - 70) / 6)], anchor='w', fill=p.WHITE,
                               font=('Segoe UI', 8))
            y += 20
        if value.get('gaps'):
            canvas.create_text(4, height - 8, text=value['gaps'][-1][:int(width / 5.5)], anchor='w', fill=p.MUTED, font=('Segoe UI', 7))

    def draw_news(self) -> None:
        canvas, p = self.tiles['news'].canvas, self.p
        canvas.delete('all')
        width, height = max(260, canvas.winfo_width()), max(200, canvas.winfo_height())
        value = self.data.get('news')
        if not value:
            canvas.create_text(width / 2, height / 2, text='Reading business and crypto feeds…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        y = 8
        chars = int((width - 8) / 5.8)
        for item in value['items']:
            if y > height - 30:
                break
            if item['rumour']:
                tag = canvas.create_text(4, y, text='UNCONFIRMED', anchor='w', fill=p.BLACK, font=('Segoe UI Semibold', 7))
                box = canvas.bbox(tag)
                back = canvas.create_rectangle(box[0] - 3, box[1] - 1, box[2] + 3, box[3] + 1, fill=AMBER, outline='')
                canvas.tag_lower(back, tag)
                start = box[2] + 8
            else:
                start = 4
            canvas.create_text(start, y, text=item['title'][:max(20, chars - (start // 6))], anchor='w', fill=p.WHITE, font=('Segoe UI', 8))
            moves = [move for move in item.get('moves') or [] if move['change_pct'] is not None and move['symbol'] != 'SPY']
            detail = f"{item['outlet']} · {item['published'][11:16]} UTC" + (
                ' · ' + ', '.join(f"{move['symbol']} {move['change_pct']:+.1f}%" for move in moves[:3]) if moves else
                ' · ' + ', '.join(item['markets'][:3]))
            canvas.create_text(4, y + 14, text=detail[:chars], anchor='w', fill=p.MUTED, font=('Segoe UI', 7))
            y += 34
        canvas.create_text(4, height - 8, text=f"{value['rumours']} unconfirmed of {len(value['items'])} · moves since publication",
                           anchor='w', fill=p.MUTED, font=('Segoe UI', 7))

    # ------------------------------------------------------------------ closing
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        stream = getattr(getattr(self.runtime, 'markets', None), 'live', None)
        if stream is not None:
            stream.unwatch(self.stream_names())
        try:
            self.after_cancel(self._tick_id)
        except (tk.TclError, AttributeError):
            pass
        self.destroy()
