"""Trade Center "3D" view: markets, odds and links in a scene you can turn, move and zoom (app/gui_space3d.py).

* **Markets:** coins and shares placed by their return over the span (left–right), how much they swing
  (height) and how closely they move with Bitcoin or the S&P 500 (depth); sized by traded value and
  coloured by today's move. Click one for its numbers, double-click to open it in the Trade Center.
* **Odds surface:** for the market on screen, every period from an hour (front) to a year (back) and
  every move (left–right): the height is the share of past periods that ended at or above that move.
  The gold dots on the "now" line are the measured chance of ending higher.
* **Links:** the market on screen in the centre and the markets that move with it around it — nearer
  the tighter the link, higher the more it moves per 1 % of theirs; green with, red against, dashed
  when the link has changed over the year, ringed when one has led it from one day to the next.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from app.gui_depth import blend
from app.gui_resources import StringVar
from app.gui_space3d import OrbitScene

UP, DOWN, NEUTRAL = '#4ADE80', '#F05D5E', '#8A96A0'
MODES = (('space', 'Markets'), ('odds', 'Odds surface'), ('links', 'Links'))
SPANS = (('1w', '1 week'), ('1m', '1 month'), ('3m', '3 months'))
DEPTHS = (('corr_btc', 'Bitcoin'), ('corr_spy', 'S&P 500'))
IDLE = ('Hover a sphere or a patch to read it, click for its numbers, double-click to open. Drag turns · Shift+drag or right-drag '
        'moves · the wheel zooms at the pointer · 1–4 views · Home resets · Tab steps through · Space spins.')


def move_colour(change: float | None) -> str:
    """Grey for no move, greener or redder with its size (full colour at 4 %)."""
    if change is None:
        return NEUTRAL
    return blend(NEUTRAL, UP if change >= 0 else DOWN, .35 + .65 * min(1.0, abs(change) / 4))


def nice(value: float) -> float:
    """A round axis end at or above `value`: 1, 1.5, 2, 2.5, 3, 4, 5, 6 or 8 times a power of ten."""
    if value <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(value))
    return next(step * power for step in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if step * power >= value * .9999)


def scale_end(values: list[float]) -> float:
    """An axis end that fits nine markets in ten: one outlier (a coin up 33 % in a month) must not squeeze the rest
    into a corner. Markets beyond it are pinned to the edge and marked."""
    values = sorted(abs(value) for value in values)
    if not values:
        return 1.0
    return nice(values[min(len(values) - 1, int(.9 * len(values)))])


def _pin(value: float) -> tuple[float, bool]:
    """(position within the box, beyond the scale?)"""
    return max(-1.06, min(1.06, value)), abs(value) > 1.0001


def space_scene(value: dict[str, Any], depth: str, current: str | None) -> dict[str, Any]:
    """Points and axes for the market space."""
    rows = [row for row in value['points'] if row['volatility_pct'] is not None]
    reach = scale_end([row['return_pct'] for row in rows])
    top = scale_end([row['volatility_pct'] for row in rows])
    volumes = [math.log10(1 + row['volume']) for row in rows]
    low, high = (min(volumes), max(volumes)) if volumes else (0.0, 1.0)
    label = dict(DEPTHS)[depth]
    points = []
    for row, volume in zip(rows, volumes):
        link = row.get(depth)
        x, wide = _pin(row['return_pct'] / reach)
        y, high = _pin(-1 + 2 * row['volatility_pct'] / top)
        points.append({'id': row['symbol'], 'asset': row['asset'], 'label': row['symbol'] + (' ›' if wide or high else ''),
                       'x': x, 'y': y,
                       'z': -1.0 if link is None else max(-1.0, min(1.0, link)),
                       'size': .45 + 1.1 * ((volume - low) / (high - low) if high > low else .5),
                       # Thinly traded markets fade: their stale prices understate the swing and the links.
                       'colour': blend(move_colour(row['change_today_pct']), NEUTRAL, .65 if row['thin'] else 0),
                       'current': row['symbol'] == current,
                       'tip': (f"{row['symbol']} · {'coin' if row['asset'] == 'crypto' else 'share or fund'}\n"
                               f"{value['span']} return {row['return_pct']:+.1f}% · swings {row['volatility_pct']:.0f}% a year\n"
                               f"moves with Bitcoin {_corr(row['corr_btc'])} · S&P 500 {_corr(row['corr_spy'])}\n"
                               f"today {row['change_today_pct']:+.2f}%" + (' · thin trading' if row['thin'] else '')
                               + ('\nbeyond the scale: pinned to the edge (›)' if wide or high else '')
                               + '\nclick: numbers · double-click: open')})
    axes = {'x': (f"return over {value['span']}", [(-1, f"{-reach:g}%"), (0, '0'), (1, f"+{reach:g}%")]),
            'y': ('swing (a year)', [(-1, '0%'), (0, f"{top / 2:g}%"), (1, f"{top:g}%")]),
            'z': (f"moves with {label}", [(-1, '−1'), (0, '0'), (1, '+1')])}
    segments = [{'a': (0, -1, -1), 'b': (0, -1, 1), 'colour': '#6B7785', 'width': 1, 'dash': (4, 3)}]  # zero return
    note = (f"{len(points)} markets · left–right: return over {value['span']} · height: swing · depth: moves with {label} · "
            "size: traded value · colour: today · faded: thinly traded · ›: beyond the scale")
    return {'points': points, 'segments': segments, 'axes': axes, 'note': note}


def _corr(value: float | None) -> str:
    return '—' if value is None else f"{value:+.2f}"


def odds_scene(surface: dict[str, Any], palette: Any) -> dict[str, Any]:
    """Faces, the 'now' line and axes for the odds surface."""
    rows, moves = surface['rows'], surface['moves_pct']
    count, columns = len(rows), len(moves)
    z = (lambda index: -1 + 2 * index / (count - 1)) if count > 1 else (lambda _index: 0.0)
    x = lambda column: -1 + 2 * column / (columns - 1)
    y = lambda share: -1 + 2 * share / 100
    faces, points, segments = [], [], []
    for index, (row, after) in enumerate(zip(rows, rows[1:])):
        for column in range(columns - 1):
            corners = [(x(column), y(row['shares'][column]), z(index)), (x(column + 1), y(row['shares'][column + 1]), z(index)),
                       (x(column + 1), y(after['shares'][column + 1]), z(index + 1)), (x(column), y(after['shares'][column]), z(index + 1))]
            share = sum(item[1] for item in corners) / 4 * 50 + 50
            measured = row['measured'][column] and after['measured'][column]
            colour = blend(blend(palette.PANEL, palette.BLACK, .5), palette.GOLD, .18 + .62 * share / 100)
            faces.append({'id': f"{row['key']}:{column}", 'corners': corners, 'colour': colour if measured else blend(colour, NEUTRAL, .5),
                          'tip': f"{row['label']} → {after['label']}, ending {moves[column]:+.1f}% or higher:\n"
                                 f"{row['shares'][column]:.0f}% → {after['shares'][column]:.0f}% of past periods"
                                 + ('' if measured else '\n(beyond the measured 5–95% range: faded, not measured)')})
    if count == 1:  # one period: a ribbon of lines rather than a surface
        row = rows[0]
        for column in range(columns - 1):
            segments.append({'a': (x(column), y(row['shares'][column]), 0), 'b': (x(column + 1), y(row['shares'][column + 1]), 0),
                             'colour': palette.GOLD, 'width': 2})
    middle = columns // 2  # the axis is symmetric, so the middle column is "no change"
    for index, row in enumerate(rows):
        points.append({'id': row['key'], 'label': row['key'], 'x': x(middle), 'y': y(row['chance_higher_pct']), 'z': z(index), 'size': .35,
                       'colour': UP if row['chance_higher_pct'] > 50 else DOWN if row['chance_higher_pct'] < 50 else NEUTRAL,
                       'tip': f"{row['label']}: ended higher in {row['chance_higher_pct']:.0f}% of past periods\n"
                              f"middle move {row['middle_pct']:+.1f}%"})
    for index, (row, after) in enumerate(zip(rows, rows[1:])):
        segments.append({'a': (x(middle), y(row['chance_higher_pct']), z(index)), 'b': (x(middle), y(after['chance_higher_pct']), z(index + 1)),
                         'colour': palette.WHITE, 'width': 2})
    ticks = [(-1 + 2 * column / (columns - 1), f"{moves[column]:+.0f}%" if abs(moves[column]) >= 10 else f"{moves[column]:+.1f}%")
             for column in (0, columns // 4, middle, columns - 1 - columns // 4, columns - 1)]
    axes = {'x': ('ending move', ticks), 'y': ('share at or above', [(-1, '0%'), (0, '50%'), (1, '100%')]),
            'z': ('period', [(z(index), row['key']) for index, row in enumerate(rows)])}
    note = (f"{surface['symbol']} · height: share of past periods ending at or above each move · front: shortest period · "
            "white line: chance of ending higher")
    return {'points': points, 'faces': faces, 'segments': segments, 'axes': axes, 'note': note}


def links_scene(web: dict[str, Any], palette: Any) -> dict[str, Any]:
    """The market in the centre, linked markets around it."""
    points = [{'id': web['symbol'], 'label': web['symbol'], 'x': 0.0, 'y': 0.0, 'z': 0.0, 'size': 1.6, 'colour': palette.GOLD,
               'tip': _centre_tip(web)}]
    segments = []
    for node in web['nodes']:
        colour = UP if node['corr'] > 0 else DOWN
        segments.append({'a': (0.0, 0.0, 0.0), 'b': (node['x'], node['y'], node['z']), 'colour': blend(NEUTRAL, colour, .3 + .7 * abs(node['corr'])),
                         'width': 1 + 4 * abs(node['corr']), **({} if node['steady'] else {'dash': (5, 3)})})
        points.append({'id': node['symbol'], 'asset': node['asset'], 'label': node['label'] + (' →' if node['lead'] else ''),
                       'x': node['x'], 'y': node['y'], 'z': node['z'], 'size': .5 + .8 * abs(node['corr']),
                       'colour': move_colour(node['today_pct']),
                       'tip': node['text'] + (f"\n{node['label']} today {node['today_pct']:+.2f}%" if node['today_pct'] is not None else '')
                              + '\ndouble-click: open it'})
    axes = {'y': ('per 1% of theirs', [(-1, '−2%'), (0, '0'), (1, '+2%')])}
    note = f"{web['symbol']} in the centre · nearer: tighter link · higher: moves more per 1% of theirs · dashed: link changed · →: leads"
    return {'points': points, 'segments': segments, 'axes': axes, 'note': note}


def _centre_tip(web: dict[str, Any]) -> str:
    model = web.get('model') or {}
    lines = [web['symbol']]
    if model:
        lines.append(f"these markets explain about {model['r2_pct']:.0f}% of its daily swings ({model['days']} days)")
        if model.get('today'):
            split = model['today']
            lines.append(f"today {split['actual_pct']:+.2f}%: {split['with_markets_pct']:+.2f}% with the markets, "
                         f"{split['own_pct']:+.2f}% its own")
    return '\n'.join(lines)


class Space3DPanel:
    def __init__(self, parent: tk.Misc, palette: Any, open_market: Callable[[str, str], None],
                 on_mode: Callable[[], None] | None = None) -> None:
        p = self.palette = palette
        self.open_market, self.on_mode = open_market, on_mode
        self.mode, self.span, self.depth = StringVar(parent, 'space'), StringVar(parent, '1m'), StringVar(parent, 'corr_btc')
        self.current: str | None = None
        self.space_data: dict[str, Any] | None = None
        self.periods_data: dict[str, Any] | None = None
        self.links_data: dict[str, Any] | None = None
        self.frame = tk.Frame(parent, bg=p.PANEL)
        bar = tk.Frame(self.frame, bg=p.PANEL)
        bar.pack(fill='x', pady=(8, 0))
        for value, label in MODES:
            ttk.Radiobutton(bar, text=label, value=value, variable=self.mode, style='Segment.Toolbutton',
                            command=self._mode_changed).pack(side='left', padx=(0, 4))
        self.views = tk.Frame(bar, bg=p.PANEL)
        self.views.pack(side='right')
        for key, label, _yaw, _pitch in OrbitScene.PRESETS:
            ttk.Button(self.views, text=label, style='Compact.Hayat.TButton', width=len(label) + 1,
                       command=lambda key=key: self.scene.view(key)).pack(side='left', padx=(2, 0))
        self.spin_button = ttk.Button(self.views, text='Spin', style='Compact.Hayat.TButton', width=5,
                                      command=lambda: self.scene.set_spin(not self.scene.spinning))
        self.spin_button.pack(side='left', padx=(6, 0))
        self.options = tk.Frame(self.frame, bg=p.PANEL)
        self.options.pack(fill='x', pady=(4, 0))
        tk.Label(self.options, text='Over', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left', padx=(0, 4))
        for value, label in SPANS:
            ttk.Radiobutton(self.options, text=label, value=value, variable=self.span, style='Segment.Toolbutton',
                            command=self._options_changed).pack(side='left', padx=(0, 4))
        tk.Label(self.options, text='Depth: moves with', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left', padx=(10, 4))
        for value, label in DEPTHS:
            ttk.Radiobutton(self.options, text=label, value=value, variable=self.depth, style='Segment.Toolbutton',
                            command=self._redraw_space).pack(side='left', padx=(0, 4))
        self.scene = OrbitScene(self.frame, p, on_select=self._selected, on_open=self._open, empty='Placing markets in 3D…', hint=False)
        self.scene.pack(fill='both', expand=True, pady=(6, 0))
        self.scene.bind('<<SpinChanged>>', lambda _event: self.spin_button.configure(text='Stop' if self.scene.spinning else 'Spin'))
        self.info = tk.Label(self.frame, text=IDLE, bg=p.PANEL, fg=p.MUTED,
                             font=('Segoe UI', 8), anchor='w', justify='left')
        self.info.pack(fill='x', pady=(4, 0))
        self.info.bind('<Configure>', lambda event: self.info.configure(wraplength=max(200, event.width - 8)))

    # ------------------------------------------------------------------ what the Trade Center should fetch
    def wanted(self) -> str:
        """'space', 'periods' or 'influences': the data the current mode draws."""
        return {'space': 'space', 'odds': 'periods', 'links': 'influences'}[self.mode.get()]

    def _mode_changed(self) -> None:
        if self.mode.get() == 'space':
            self.options.pack(fill='x', pady=(4, 0), before=self.scene)
        else:
            self.options.pack_forget()
        self.redraw(keep_view=False)
        if self.on_mode:
            self.on_mode()

    def _options_changed(self) -> None:
        self.space_data = None
        self.redraw()
        if self.on_mode:
            self.on_mode()

    # ------------------------------------------------------------------ data in
    def set_current(self, symbol: str) -> None:
        if symbol != self.current:
            self.current = symbol
            self.periods_data = self.links_data = None
            self.scene.selected = None  # the ring moves to the market now on screen
            self.redraw()

    def show_space(self, value: dict[str, Any] | None) -> None:
        if value and value['span'] == self.span.get():
            self.space_data = value
            if self.mode.get() == 'space':
                self.redraw()

    def show_periods(self, value: dict[str, Any] | None) -> None:
        if value and value['symbol'] == self.current:
            self.periods_data = value
            if self.mode.get() == 'odds':
                self.redraw()

    def show_links(self, value: dict[str, Any] | None) -> None:
        if value and value['symbol'] == self.current:
            self.links_data = value
            if self.mode.get() == 'links':
                self.redraw()

    def failed(self, message: str) -> None:
        self.scene.clear(message)

    # ------------------------------------------------------------------ drawing
    def _redraw_space(self) -> None:
        if self.mode.get() == 'space':
            self.redraw()

    def redraw(self, keep_view: bool = True) -> None:
        mode = self.mode.get()
        if mode == 'space':
            if not self.space_data:
                return self.scene.clear('Placing markets in 3D… (reading a month of prices for each)')
            scene = space_scene(self.space_data, self.depth.get(), self.current)
        elif mode == 'odds':
            if not self.periods_data:
                return self.scene.clear(f"Measuring every period for {self.current or 'this market'}…")
            from app.market_space import odds_surface
            try:
                scene = odds_scene(odds_surface(self.periods_data), self.palette)
            except ValueError as exc:
                return self.scene.clear(str(exc))
        else:
            if not self.links_data:
                return self.scene.clear(f"Measuring what moves {self.current or 'this market'}…")
            from app.market_space import link_web
            scene = links_scene(link_web(self.links_data), self.palette)
        self.scene.set_scene(**scene, keep_view=keep_view)
        if self.current and any(point['id'] == self.current for point in scene['points']) and self.scene.selected is None:
            self.scene.select(self.current, notify=False)
        return None

    # ------------------------------------------------------------------ picking
    def _point(self, key: str | None) -> dict[str, Any] | None:
        return next((point for point in self.scene.points if point['id'] == key), None)

    def _selected(self, key: str | None) -> None:
        point = self._point(key)
        self.info.configure(text=point['tip'].replace('\n', ' · ') if point else IDLE)

    def _open(self, key: str) -> None:
        point = self._point(key)
        if point and point.get('asset') and self.mode.get() != 'odds':
            self.open_market(point['id'], point['asset'])
