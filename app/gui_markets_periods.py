"""Trade Center "Periods" tab: what this market did over every period from an hour to a year (app/market_periods.py).

* A cone: for each period, the 9-in-10 range (thin line), the middle half (bar) and the middle move (tick),
  on a square-root scale so an hour and a year fit side by side; the share higher sits above each.
* A table with the same numbers and how often each range held in its walk-forward test.
* Choosing a period (row or cone) shows its possibilities, levels touched on the way, the odds after a move like
  the last one, and scheduled events inside it.
* "Compare my watchlist" ranks the watchlist (or a standard set) over the chosen period.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from app.gui_intelligence_parts import blend
from app.market_periods import MIN_TESTS

UP, DOWN = '#4ADE80', '#F05D5E'


def move_text(low: float, high: float) -> str:
    """'-0.8…+0.9%', or whole numbers once a side reaches 10 %, so the narrow table keeps every column."""
    return f"{low:+.1f}…{high:+.1f}%" if max(abs(low), abs(high)) < 10 else f"{low:+.0f}…{high:+.0f}%"


class PeriodsPanel:
    def __init__(self, parent, palette, submit: Callable[[str, Callable[[], Any]], None], markets: Any, model: Any = None) -> None:
        p = self.palette = palette
        self.submit, self.markets, self.model = submit, markets, model
        self.data: dict[str, Any] | None = None
        self.selected = '1m'
        self.comparing = False  # a comparison on show stays until another period is chosen
        self.frame = tk.Frame(parent, bg=p.BLACK)
        tk.Label(self.frame, text='Possibilities by time period', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11),
                 anchor='w').pack(fill='x')
        self.head = tk.Label(self.frame, text='Reading hourly, daily and weekly prices…', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8),
                             anchor='w', justify='left', wraplength=300)
        self.head.pack(fill='x')
        self.cone = tk.Canvas(self.frame, bg=p.PANEL, highlightthickness=0, height=118)
        self.cone.pack(fill='x', pady=(4, 4))
        self.cone.bind('<Configure>', lambda _event: self.draw())
        self.cone.bind('<Button-1>', self.cone_click)
        # Headings only (no tree indent): the side panel is about 330 px wide on a 1536 px screen.
        self.tree = ttk.Treeview(self.frame, columns=('period', 'higher', 'middle', 'wide', 'held'), height=8, selectmode='browse',
                                 style='Markets.Treeview', show='headings')
        for key, label, width in (('period', 'Period', 60), ('higher', 'Higher', 46), ('middle', 'Middle half', 76),
                                  ('wide', '9 in 10', 84), ('held', 'Held', 50)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=40, stretch=key in ('middle', 'wide'), anchor='w' if key == 'period' else 'e')
        self.tree.tag_configure('missing', foreground=p.MUTED)
        self.tree.tag_configure('narrow', foreground='#E8C46A')
        self.tree.pack(fill='x')
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.row_selected())
        row = tk.Frame(self.frame, bg=p.BLACK)
        row.pack(fill='x', pady=(4, 0))
        self.compare_button = ttk.Button(row, text='Compare my watchlist', style='Compact.Hayat.TButton', command=self.compare)
        self.compare_button.pack(side='left')
        tk.Label(row, text='over the chosen period', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8)).pack(side='left', padx=6)
        self.explain_button = ttk.Button(row, text='Explain with HAYAT', style='Compact.Hayat.TButton', command=self.explain)
        self.explain_button.pack(side='right')
        # A read-only text box rather than a label: the details scroll instead of being cut on a short screen.
        self.note = tk.Text(self.frame, bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 8), wrap='word', height=6, relief='flat',
                            padx=8, pady=6, highlightthickness=0, cursor='arrow')
        self.note.pack(fill='both', expand=True, pady=(4, 0))
        self.note.configure(state='disabled')
        self.head.bind('<Configure>', lambda event: self.head.configure(wraplength=max(200, event.width - 4)))

    def say(self, text: str) -> None:
        self.note.configure(state='normal')
        self.note.delete('1.0', 'end')
        self.note.insert('1.0', text)
        self.note.configure(state='disabled')

    def said(self) -> str:
        return self.note.get('1.0', 'end').strip()

    # ------------------------------------------------------------------ data
    def show(self, value: dict[str, Any] | None) -> None:
        self.data = value
        tree = self.tree
        tree.delete(*tree.get_children())
        if value is None:
            self.head.configure(text='Reading hourly, daily and weekly prices…')
            self.say('')
            self.draw()
            return
        from app.markets import money
        self.head.configure(text=f"{value['symbol']} now {money(value['price'])} · " + ('● live price' if value['live'] else 'last close')
                                 + f" · updated {value['computed_at'][11:16]} UTC · longest measured: {value['longest_measured']}")
        for row in value['periods']:
            if not row['available']:
                tree.insert('', 'end', iid=row['key'], values=(row['label'], '—', 'too little', 'history', ''), tags=('missing',))
                continue
            levels, record = row['range'], row['track_record']
            held = f"{record['inside_wide_pct']:.0f}%" if record.get('tested', 0) >= MIN_TESTS else 'few'
            narrow = record.get('tested', 0) >= MIN_TESTS and 'too narrow' in record['verdict']
            tree.insert('', 'end', iid=row['key'],
                        values=(row['label'], f"{row['chance_higher']['up_pct']:.0f}%", move_text(levels['p25']['move_pct'], levels['p75']['move_pct']),
                                move_text(levels['p05']['move_pct'], levels['p95']['move_pct']), held),
                        tags=('narrow',) if narrow else ())
        if self.selected in tree.get_children():
            tree.selection_set(self.selected)
        if not self.comparing:
            self.row_selected(force=True)
        self.draw()

    def row(self, key: str) -> dict[str, Any] | None:
        return next((row for row in (self.data or {}).get('periods', []) if row['key'] == key), None)

    def row_selected(self, force: bool = False) -> None:
        selection = self.tree.selection()
        changed = bool(selection) and selection[0] != self.selected
        if selection:
            self.selected = selection[0]
        if self.comparing and not changed and not force:
            return  # the refresh re-selecting the same period leaves the comparison on show
        self.comparing = False
        row = self.row(self.selected)
        if not row:
            return
        from app.market_periods import detail_lines
        chance = row.get('chance_higher')
        head = (f"{row['label']}: higher in {chance['up_pct']}% of past periods ({chance['interval_pct'][0]}–{chance['interval_pct'][1]}%, "
                f"{chance['separate_periods']} separate)." if chance else '')
        self.say('\n'.join(([head] if head else []) + detail_lines(row)))
        self.draw()

    def cone_click(self, event) -> None:
        keys = [row['key'] for row in (self.data or {}).get('periods', [])]
        if not keys:
            return
        column = int(event.x / max(1, self.cone.winfo_width()) * len(keys))
        key = keys[max(0, min(column, len(keys) - 1))]
        self.tree.selection_set(key)
        self.row_selected(force=True)

    def compare(self) -> None:
        from app.market_periods import LABELS, compare
        markets, period = self.markets, self.selected
        self.say(f'Comparing your watchlist over {LABELS[period]}…')
        self.submit('periods_compare', lambda: compare(markets, None, period))

    def explain(self) -> None:
        """A plain summary of every period by HAYAT's local model, from these numbers only."""
        if not self.data:
            return
        if self.model is None:
            self.say("HAYAT's local model is not available in this session; the numbers above stand on their own.")
            return
        from app.market_periods import explain
        model, value = self.model, self.data
        self.comparing = True  # keep the summary on show through the next refresh
        self.say('HAYAT is reading the numbers…')
        self.submit('periods_explain', lambda: {'symbol': value['symbol'], 'text': explain(model, value)})

    def show_explain(self, value: dict[str, Any]) -> None:
        if self.data and value['symbol'] == self.data['symbol']:
            self.comparing = True
            self.say('HAYAT: ' + value['text'] + '\n(From the numbers above; not advice.)')

    def show_compare(self, value: dict[str, Any]) -> None:
        from app.market_periods import report_lines
        self.comparing = True
        self.say('\n'.join(report_lines(value)))

    # ------------------------------------------------------------------ cone
    def draw(self) -> None:
        canvas, p = self.cone, self.palette
        canvas.delete('all')
        width, height = max(220, canvas.winfo_width()), max(100, canvas.winfo_height())
        value = self.data
        if not value:
            canvas.create_text(width / 2, height / 2, text='Measuring every period…', fill=p.MUTED, font=('Segoe UI', 9))
            return
        rows = value['periods']
        squash = lambda move: math.copysign(math.sqrt(abs(move)), move)  # an hour and a year on one scale
        logs = [abs(math.log(1 + row['range'][key]['move_pct'] / 100)) for row in rows if row['available'] for key in ('p05', 'p95')]
        reach = squash(max(logs or [.01]))
        top, bottom = 18, height - 16
        middle = (top + bottom) / 2
        y = lambda move_pct: middle - squash(math.log(1 + move_pct / 100)) / reach * (bottom - top) / 2
        canvas.create_line(0, middle, width, middle, fill=p.MUTED, dash=(2, 3))
        canvas.create_text(2, middle - 2, text='now', fill=p.MUTED, font=('Segoe UI', 7), anchor='sw')
        step = width / len(rows)
        for index, row in enumerate(rows):
            x = step * (index + .5)
            if row['key'] == self.selected:
                canvas.create_rectangle(step * index + 1, 1, step * (index + 1) - 1, height - 1, outline=p.GOLD_SOFT, fill='')
            canvas.create_text(x, height - 3, text=row['key'], fill=p.WHITE if row['available'] else p.MUTED, font=('Segoe UI', 7),
                               anchor='s')
            if not row['available']:
                canvas.create_text(x, middle, text='—', fill=p.MUTED, font=('Segoe UI', 8))
                continue
            levels = row['range']
            canvas.create_line(x, y(levels['p05']['move_pct']), x, y(levels['p95']['move_pct']), fill=p.MUTED)
            canvas.create_rectangle(x - step * .22, y(levels['p75']['move_pct']), x + step * .22, y(levels['p25']['move_pct']),
                                    fill=blend(p.GOLD, p.PANEL, .45), outline='')
            canvas.create_line(x - step * .3, y(levels['p50']['move_pct']), x + step * .3, y(levels['p50']['move_pct']), fill=p.WHITE, width=2)
            chance = row['chance_higher']
            # Green or red only when the whole interval clears 50 %, and never for a thin market whose unchanged
            # periods pull "higher" below half without any fall.
            low, high = chance['interval_pct']
            colour = p.MUTED if row['flat_pct'] >= 10 else UP if low > 50 else DOWN if high < 50 else p.MUTED
            canvas.create_text(x, 3, text=f"{chance['up_pct']:.0f}%", anchor='n', font=('Segoe UI', 7), fill=colour)
